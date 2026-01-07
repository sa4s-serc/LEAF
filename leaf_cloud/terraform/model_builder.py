from __future__ import annotations

# Standard library imports
import json
import logging
import os
import shutil
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Set
import subprocess
import tempfile
import time
from textwrap import dedent
import sys

# Third-party imports
import hcl2
import yaml
from typing_extensions import TypeAlias
import re

# Local application imports
from leaf_cloud.core.petri_net import (
    Arc,
    PetriNet,
    Place,
    Token,
    TokenColor,
    Transition,
)
from leaf_cloud.core.resource import Resource, ResourcePool, ResourceType

# GCP specific imports
from ..gcp.compute import (
    ComputeResource,
    ComputeEngineVM,
    GKECluster,
    GKENode,
    CloudFunction,
    CloudRun,
    AppEngine,
)
from ..gcp.network import (
    VPC,
    LoadBalancer,
    DNS,
    Interconnect,
    VPN,
)
from ..gcp.security import SecurityResource
from ..gcp.security import (
    SecurityResource as BaseSecurityResource,
    SecurityResourceType,
    IAM,
    CloudKMS,
    SecretManager,
    SecurityCommandCenter,
    CloudArmor,
)
from ..gcp.storage import (
    Storage as BaseStorageResource,
    CloudStorage,
    PersistentDisk,
    CloudSQL,
    Bigtable,
    Firestore,
    StorageClass as GCPStorageClass,
    DiskType,
    DatabaseTier,
)
from ..gcp.network import (
    NetworkResource as BaseNetworkResource,
    VPC,
    NetworkResource,
    LoadBalancer,
    DNS,
    VPN,
    Interconnect,
    CDN,
    APIGateway,
    NetworkResourceType,
    LoadBalancerType,
)
from ..gcp.generic_component import GenericGCPComponent, GCPServiceCategory
from .filters import is_non_operational
from ..utils.helpers import normalize_block
from ..utils.capacity_mappings import get_capacity_from_config
from ..config import LEAFCloudConfig
from .parser import TerraformParser

# Type aliases
ResourceId: TypeAlias = str
PlaceId: TypeAlias = str
TransitionId: TypeAlias = str

logger = logging.getLogger(__name__)
# logging.basicConfig(level=logging.INFO)


def _is_resource_of_category(
    resource_obj: Resource, category_class: Type[Resource]
) -> bool:
    return isinstance(resource_obj, category_class)


class ModelBuilder:
    """Builds LEAF-Cloud models from Terraform configurations by translating resources
    into Petri net elements and establishing their relationships.
    """

    RESOURCE_CLASS_MAP: Dict[str, Type[Resource]] = {
        "Resource": Resource,
        "ComputeEngineVM": ComputeEngineVM,
        "GKECluster": GKECluster,
        "GKENode": GKENode,
        "CloudFunction": CloudFunction,
        "CloudRun": CloudRun,
        "AppEngine": AppEngine,
        "CloudStorage": CloudStorage,
        "PersistentDisk": PersistentDisk,
        "CloudSQL": CloudSQL,
        "Bigtable": Bigtable,
        "Firestore": Firestore,
        "VPC": VPC,
        "NetworkResource": NetworkResource,
        "LoadBalancer": LoadBalancer,
        "DNS": DNS,
        "VPN": VPN,
        "Interconnect": Interconnect,
        "CDN": CDN,
        "APIGateway": APIGateway,
        "SecurityResource": BaseSecurityResource,
        "GenericGCPComponent": GenericGCPComponent,
    }
    

    # ---- Nonoperational filters (single source of truth) ----
    def _is_non_operational(self, res_type: str) -> bool:
        # Delegate to centralized filter for single source of truth
        return is_non_operational(res_type)


    def __init__(
        self,
        terraform_path: str,
        config: LEAFCloudConfig,
        tuned_params: Optional[Dict[str, float]] = None,
        pod_resource_requirements: Optional[Dict[str, float]] = None,
        cluster_autoscaler_requirements: Optional[Dict[str, Dict[str, Any]]] = None,
        parsed_resources: Optional[Dict[str, Any]] = None,
        parser: Optional[TerraformParser] = None,
    ):
        """
        Initialize the ModelBuilder with a Terraform directory and configuration object.

        Args:
            terraform_path: Path to Terraform directory or plan JSON file.
            config: The LEAFCloudConfig object.
        """
        self.terraform_path = terraform_path
        self.config = config
        self.tuned_params = tuned_params or {}
        self.pod_resource_requirements = pod_resource_requirements or {}
        self.cluster_autoscaler_requirements = cluster_autoscaler_requirements or {}
        # Optional pre-parsed resources / parser from LEAFCloud.load_terraform
        self._external_parser = parser

        # Get the workload rate from the new workload config structure
        self.workload_rate = 100.0  # Default value
        if hasattr(config.simulation, 'workload') and config.simulation.workload is not None:
            if hasattr(config.simulation.workload, 'params') and 'rate' in config.simulation.workload.params:
                self.workload_rate = config.simulation.workload.params['rate']
            elif hasattr(config.simulation.workload, 'rate'):
                self.workload_rate = config.simulation.workload.rate
        self.env_config = self._load_env_config(
            config.env_config_path or "env.yaml"
        )

        # Initialize required attributes with proper types
        self.tf_resources: Dict[str, Dict[str, Any]] = parsed_resources or {}
        self.resource_mapping: Dict[str, Resource] = {}
        self.resource_pools: Dict[ResourceType, ResourcePool] = {
            rt: ResourcePool(rt.name, rt)
            for rt in ResourceType.__members__.values()
        }
        self.petri_net: PetriNet = PetriNet(name="LeafCloudModel")
        # Pending node pools to resolve after all resources are mapped
        # Each entry: { 'cluster_name': str, 'machine_type': str, 'node_count': int, 'region': str, 'pool_name': str }
        self._pending_node_pools: List[Dict[str, Any]] = []

        logger.debug(
            f"ModelBuilder initialized with Terraform path: {terraform_path}"
        )

    def _load_env_config(self, config_path: str) -> Dict[str, Any]:
        """Load environment configuration from YAML file."""
        default_config = {
            "region": "us-central1",
            "project": "default-project",
            "zone": "us-central1-a",
        }

        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    config = yaml.safe_load(f)
                if config is None:
                    return default_config
                return config
            except Exception as e:
                logger.error(
                    f"Failed to load config from {config_path}: {str(e)}"
                )
                return default_config
        else:
            logger.warning(
                f"Environment configuration file {config_path} not found. Using defaults."
            )
            return default_config

    def _get_dict_value(
        self, data: Dict[str, Any], key_path: Any, default: Any = None
    ) -> Any:
        """Safely get a nested value from a dictionary using dot notation.
        
        key_path can be provided either as a dot-separated string (e.g. "settings.tier")
        or as an iterable (list/tuple) of path segments (e.g. ["settings", "tier"]).
        """
        if not data or key_path in (None, ""):
            return default

        # Support both string and list/tuple style paths
        if isinstance(key_path, (list, tuple)):
            keys = list(key_path)
        else:
            keys = str(key_path).split(".")
        result = data
        for key in keys:
            if isinstance(result, dict) and key in result:
                result = result[key]
            elif (
                isinstance(result, list)
                and key.isdigit()
                and int(key) < len(result)
            ):
                # Handle list indexing if key is a digit and within list bounds
                result = result[int(key)]
            else:
                return default
        return result

    def _safe_cast(
        self,
        value: Any,
        target_type: Callable[[Any], Any],
        default: Any = None,
    ) -> Any:
        """Safely cast a value to a target type."""
        if value is None:
            return default
        try:
            return target_type(value)
        except (ValueError, TypeError, AttributeError):
            logger.warning(
                f"Failed to cast '{value}' to {target_type.__name__ if hasattr(target_type, '__name__') else str(target_type)}. Returning default: {default}"
            )
            return default

    # Returns MB
    def _parse_memory_limit(self, memory_str: Optional[str]) -> int:
        """Parse a memory limit string (e.g., '1Gi', '512Mi') into megabytes (MB)."""
        if not memory_str:
            return 0

        val_str = str(memory_str).lower().strip()
        num_part = ""
        unit_part = ""

        # Separate numeric part from unit part
        for char_idx, char_val in enumerate(val_str):
            if char_val.isdigit() or char_val == ".":
                num_part += char_val
            else:
                unit_part = val_str[char_idx:]
                break

        if not num_part:
            logger.warning(
                f"No numeric part found in memory string '{memory_str}'."
            )
            return 0

        try:
            num = float(num_part)
        except ValueError:
            logger.warning(
                f"Invalid numeric value '{num_part}' in memory string '{memory_str}'."
            )
            return 0

        unit_part = unit_part.lower().replace("i", "").replace("b", "").strip()

        if unit_part == "g" or unit_part == "gb":  # Gigabytes or Gibibytes
            return int(num * 1024)
        elif unit_part == "m" or unit_part == "mb":  # Megabytes or Mebibytes
            return int(num)
        elif unit_part == "k" or unit_part == "kb":  # Kilobytes or Kibibytes
            return int(num / 1024)
        elif not unit_part:  # No unit
            logger.warning(
                f"Memory string '{memory_str}' has no units, assuming MB."
            )
            return int(num)
        else:  # Unrecognized unit
            logger.warning(
                f"Unrecognized memory unit '{unit_part}' in '{memory_str}'. Could not parse."
            )
            return 0

    def _parse_cloudrun_terraform_config(self, res_config: Dict[str, Any], res_name: str) -> Dict[str, Any]:
        """Parse CloudRun Terraform configuration with enhanced error handling and defaults.
        
        This method extracts CloudRun parameters from Terraform configuration with comprehensive
        fallback logic and validation to ensure proper min_instances, max_instances, and 
        concurrency values as per requirements 2.2, 5.3, and 6.4.
        
        Args:
            res_config: The Terraform resource configuration dictionary
            res_name: The resource name for logging purposes
            
        Returns:
            Dict containing parsed CloudRun configuration parameters
            
        Raises:
            Exception: If configuration is severely malformed and cannot be parsed
        """
        logger.debug(f"Parsing CloudRun configuration for {res_name}")
        
        # Initialize result with sensible defaults (requirement 5.3)
        config = {
            'cpu_limit': 1.0,
            'memory_limit_mb': 512,
            'min_instances': 0,
            'max_instances': 100,
            'concurrency': 80  # Default as per requirement 6.4
        }
        
        try:
            # Extract template configuration with multiple fallback paths
            raw_template = self._get_dict_value(res_config, 'template')
            if isinstance(raw_template, list):
                template = raw_template[0] if raw_template else {}
            elif isinstance(raw_template, dict):
                template = raw_template
            else:
                template = {}
                logger.warning(f"No template found in CloudRun config for {res_name}, using defaults")

            # Extract spec block from template
            raw_spec = self._get_dict_value(template, 'spec')
            if isinstance(raw_spec, list):
                spec = raw_spec[0] if raw_spec else {}
            elif isinstance(raw_spec, dict):
                spec = raw_spec
            else:
                spec = {}
                logger.debug(f"No spec found in CloudRun template for {res_name}")

            # Extract container configuration
            raw_containers = self._get_dict_value(spec, 'containers')
            if isinstance(raw_containers, list):
                container_config = raw_containers[0] if raw_containers else {}
            elif isinstance(raw_containers, dict):
                container_config = raw_containers
            else:
                container_config = {}
                logger.debug(f"No containers found in CloudRun spec for {res_name}")
            
            # Parse resource limits with multiple fallback paths
            limits = self._get_dict_value(
                container_config, 
                'resources.0.limits', 
                self._get_dict_value(container_config, 'resources.limits', {})
            )
            
            # CPU limit parsing with validation
            cpu_raw = self._get_dict_value(limits, 'cpu', '1')
            config['cpu_limit'] = self._safe_cast(cpu_raw, float, 1.0)
            if config['cpu_limit'] <= 0:
                logger.warning(f"Invalid CPU limit {cpu_raw} for CloudRun {res_name}, using 1.0")
                config['cpu_limit'] = 1.0
            
            # Memory limit parsing with validation
            memory_limit_str = self._get_dict_value(limits, 'memory', '512Mi')
            config['memory_limit_mb'] = self._parse_memory_limit(memory_limit_str)
            if config['memory_limit_mb'] <= 0:
                logger.warning(f"Invalid memory limit {memory_limit_str} for CloudRun {res_name}, using 512MB")
                config['memory_limit_mb'] = 512

            # Extract annotations for autoscaling configuration with multiple fallback paths
            metadata = self._get_dict_value(template, 'metadata')
            if isinstance(metadata, list):
                metadata = metadata[0] if metadata else {}
            elif not isinstance(metadata, dict):
                metadata = {}
            
            annotations = self._get_dict_value(metadata, 'annotations', {})
            
            # Parse min_instances with validation (requirement 2.2)
            min_instances_raw = (
                annotations.get('autoscaling.knative.dev/minScale') or
                annotations.get('run.googleapis.com/minScale') or
                '0'
            )
            config['min_instances'] = self._safe_cast(min_instances_raw, int, 0)
            if config['min_instances'] < 0:
                logger.warning(f"Invalid min_instances {min_instances_raw} for CloudRun {res_name}, using 0")
                config['min_instances'] = 0
            
            # Parse max_instances with validation (requirement 2.2)
            max_instances_raw = (
                annotations.get('autoscaling.knative.dev/maxScale') or
                annotations.get('run.googleapis.com/maxScale') or
                '100'
            )
            config['max_instances'] = self._safe_cast(max_instances_raw, int, 100)
            if config['max_instances'] <= 0:
                logger.warning(f"Invalid max_instances {max_instances_raw} for CloudRun {res_name}, using 100")
                config['max_instances'] = 100
            
            # Ensure min <= max
            if config['min_instances'] > config['max_instances']:
                logger.warning(
                    f"min_instances ({config['min_instances']}) > max_instances ({config['max_instances']}) "
                    f"for CloudRun {res_name}, setting min to max"
                )
                config['min_instances'] = config['max_instances']
            
            # Parse concurrency with multiple sources (requirement 6.4)
            # Check spec.container_concurrency first
            concurrency_raw = spec.get('container_concurrency')
            if concurrency_raw is not None:
                config['concurrency'] = self._safe_cast(concurrency_raw, int, 80)
            else:
                # Check annotations for concurrency
                concurrency_raw = (
                    annotations.get('run.googleapis.com/concurrency') or
                    annotations.get('autoscaling.knative.dev/concurrency') or
                    '80'
                )
                config['concurrency'] = self._safe_cast(concurrency_raw, int, 80)
            
            # Validate concurrency
            if config['concurrency'] <= 0:
                logger.warning(f"Invalid concurrency {concurrency_raw} for CloudRun {res_name}, using default 80")
                config['concurrency'] = 80
            
            # Log successful parsing
            logger.info(
                f"Parsed CloudRun {res_name}: CPU={config['cpu_limit']}, "
                f"Memory={config['memory_limit_mb']}MB, "
                f"Instances={config['min_instances']}-{config['max_instances']}, "
                f"Concurrency={config['concurrency']}"
            )
            
            return config
            
        except Exception as e:
            logger.error(f"Error parsing CloudRun configuration for {res_name}: {str(e)}")
            logger.info(f"Using default configuration for CloudRun {res_name}")
            # Return defaults on any parsing error (requirement 5.3)
            return config
    def _get_resource_class(
        self, res_type: str
    ) -> Tuple[Type[Resource] | None, ResourceType | None]:
        """Return (concrete‑class, high‑level‑enum) suited for *res_type*."""
        if self._is_non_operational(res_type):
            return None, None

        class_name = self._determine_resource_type(res_type)
        leaf_cls   = self.RESOURCE_CLASS_MAP.get(class_name, GenericGCPComponent)

        if   issubclass(leaf_cls, ComputeResource):        rtype = ResourceType.COMPUTE
        elif issubclass(leaf_cls, BaseStorageResource):    rtype = ResourceType.STORAGE
        elif issubclass(leaf_cls, BaseNetworkResource):    rtype = ResourceType.NETWORK
        elif issubclass(leaf_cls, BaseSecurityResource):   rtype = ResourceType.SECURITY
        else:                                              rtype = ResourceType.GENERIC
        return leaf_cls, rtype

    def _get_base_resource_config(
        self,
        res_type: str,
        res_name: str,
        res_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Common fields every LEAF‑Cloud resource needs (name/region/labels…)."""
        region = self._get_dict_value(res_config, "region")
        zone   = self._get_dict_value(res_config, "zone")

        # If region is missing but zone is available (e.g., us-east1-a), derive it
        if not region and isinstance(zone, str) and "-" in zone:
            parts = zone.split("-")
            if len(parts) >= 3:
                region = "-".join(parts[:-1])

        # Try to infer region from subnet URI if still missing
        if not region:
            subnet = self._get_dict_value(res_config, "subnetwork")
            if isinstance(subnet, str) and "/regions/" in subnet:
                try:
                    region = subnet.split("/regions/")[1].split("/")[0]
                except Exception:
                    region = None

        # Fall back to env config defaults
        region = region or self.env_config.get("region", "us-central1")
        zone   = zone   or self.env_config.get("zone",   f"{region}-a")
        return {
            "name":     res_name,
            "provider": "gcp",
            "region":   region,
            "zone":     zone,
            **self._get_common_fields(res_config),
        }

    def parse_terraform_files(self) -> Dict[str, Any]:
        """
        Parse Terraform configuration using the centralized TerraformParser.
        
        Returns:
            Dictionary containing parsed Terraform resources
        """
        # Reuse pre-parsed resources if provided (e.g., via LEAFCloud.load_terraform)
        if self.tf_resources:
            logger.info(
                "Reusing %d pre-parsed Terraform resources", len(self.tf_resources)
            )
            return self.tf_resources

        parser = self._external_parser or TerraformParser(self.terraform_path)
        parsed = parser.parse_all()
        # Use resources parsed by the TerraformParser (already filtered for non-operational types)
        self.tf_resources = parsed.get("resources", {})
        logger.info(
            "Parsed %d resources using TerraformParser", len(self.tf_resources)
        )
        return self.tf_resources

    def _parse_terraform_directory(self) -> Dict[str, Any]:
        """
        Parse all Terraform files in the specified directory recursively.
        Skips hidden directories (starting with '.').

        Returns:
            Dictionary containing parsed Terraform resources
        """
        all_resources = {}

        for root, dirs, files in os.walk(self.terraform_path):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for file in files:
                if file.endswith(".tf"):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, "r") as tf_file:
                            parsed = hcl2.load(tf_file)

                            # Normalize blocks that might be returned as lists
                            if "resource" in parsed:
                                parsed["resource"] = normalize_block(
                                    parsed["resource"]
                                )
                            if "variable" in parsed:
                                parsed["variable"] = normalize_block(
                                    parsed["variable"]
                                )
                            if "output" in parsed:
                                parsed["output"] = normalize_block(
                                    parsed["output"]
                                )

                            # Process resources if available
                            if "resource" in parsed:
                                for res_type, resources in parsed[
                                    "resource"
                                ].items():
                                    for (
                                        res_name,
                                        res_config,
                                    ) in resources.items():
                                        if self._is_non_operational(res_type):
                                            logger.debug("Skip non-op %s.%s", res_type, res_name)
                                            continue
                                        res_id = f"{res_type}.{res_name}"
                                        all_resources[res_id] = {
                                            "type": res_type,
                                            "name": res_name,
                                            "config": res_config,
                                        }
                    except Exception as e:
                        logger.error(
                            f"Error parsing Terraform file {file_path}: {e}"
                        )

        self.tf_resources = all_resources
        logger.info(
            f"Parsed {len(all_resources)} resources from Terraform files"
        )
        return all_resources

    def _parse_terraform_plan_json(self) -> Dict[str, Any]:
        """
        Parse a Terraform plan JSON file.

        Returns:
            Dictionary containing parsed Terraform resources
        """
        all_resources = {}

        try:
            with open(self.terraform_path, "r") as json_file:
                plan_data = json.load(json_file)

                # Extract resources from the plan
                if (
                    "planned_values" in plan_data
                    and "root_module" in plan_data["planned_values"]
                ):
                    root_module = plan_data["planned_values"]["root_module"]

                    # Process resources in the root module
                    if "resources" in root_module:
                        for resource in root_module["resources"]:
                            res_type = resource.get("type", "")
                            res_name = resource.get("name", "")
                            
                            if self._is_non_operational(res_type):
                                logger.debug("Skip non-op %s.%s (plan)", res_type, res_name)
                                continue
                            res_id = f"{res_type}.{res_name}"
                            # Extract values as the config
                            res_config = resource.get("values", {})

                            all_resources[res_id] = {
                                "type": res_type,
                                "name": res_name,
                                "config": res_config,
                            }

                    # Process resources in child modules recursively
                    if "child_modules" in root_module:
                        self._extract_module_resources(
                            root_module["child_modules"], all_resources
                        )

                logger.info(
                    f"Successfully parsed Terraform plan JSON: {self.terraform_path}"
                )
        except Exception as e:
            logger.error(
                f"Error parsing Terraform plan JSON {self.terraform_path}: {e}"
            )

        self.tf_resources = all_resources
        logger.info(
            f"Parsed {len(all_resources)} resources from Terraform plan JSON"
        )
        return all_resources

    def _extract_module_resources(
        self, modules: List[Dict[str, Any]], all_resources: Dict[str, Any]
    ) -> None:
        """
        Extract resources from child modules recursively.

        Args:
            modules: List of module configurations
            all_resources: Dictionary to update with extracted resources
        """
        for module in modules:
            if "resources" in module:
                for resource in module["resources"]:
                    res_type = resource.get("type", "")
                    res_name = resource.get("name", "")
                    if self._is_non_operational(res_type):
                        logger.debug("Skip nonop %s.%s (child module)", res_type, res_name)
                        continue
                    res_id = f"{res_type}.{res_name}"

                    # Extract values as the config
                    res_config = resource.get("values", {})

                    # Add module address to the resource ID for uniqueness
                    if "address" in module:
                        module_address = module["address"]
                        res_id = f"{module_address}.{res_id}"

                    all_resources[res_id] = {
                        "type": res_type,
                        "name": res_name,
                        "config": res_config,
                    }

            # Process nested child modules recursively
            if "child_modules" in module:
                self._extract_module_resources(
                    module["child_modules"], all_resources
                )

    def build_model(self, workload_rate: Optional[float] = None) -> PetriNet:
        """
        Builds a Petri net model from the Terraform configuration.

        Args:
            workload_rate: Optional rate for steady workload generation

        Returns:
            PetriNet: The constructed Petri net model

        Raises:
            ValueError: If no Terraform resources are found to build the model from
        """
        try:
            if not self.petri_net:
                self.petri_net = PetriNet(name="LeafCloudModel")

            if not self.tf_resources:
                self.parse_terraform_files()

            if not self.tf_resources:
                logger.error(
                    "No Terraform resources found to build model from"
                )
                raise ValueError(
                    "No Terraform resources found to build model from"
                )

            # Create source and sink places if they don't exist
            if PetriNet.SOURCE_PLACE_ID not in self.petri_net.places:
                source_place = Place(
                    id=PetriNet.SOURCE_PLACE_ID, name="Source"
                )
                self.petri_net.add_place(source_place)

            if PetriNet.SINK_PLACE_ID not in self.petri_net.places:
                sink_place = Place(id=PetriNet.SINK_PLACE_ID, name="Sink")
                self.petri_net.add_place(sink_place)

            # Process Terraform resources if not already processed
            if not self.resource_mapping:
                self._process_terraform_resources()

            # Prefer building from Terraform Graphviz DOT if available
            dot_path = self._find_dot_file()
            if dot_path:
                try:
                    nodes, edges = self._parse_dot_graph(dot_path)
                    print(f"Topology source: DOT ({len(nodes)} nodes, {len(edges)} edges) -> {os.path.basename(dot_path)}")

                    # Interactive UI editing step. In CLI, launch a small Streamlit app;
                    # if already inside a Streamlit session, use the in-process editor.
                    try:
                        edited_edges = self._maybe_edit_topology_via_ui(nodes, edges)
                    except Exception:
                        # If UI fails for any reason, silently fall back to original edges
                        edited_edges = edges

                    self._build_topology_from_dot(nodes, edited_edges)
                except Exception as e:
                    print(f"Topology source: layered (DOT parsing failed: {e})")
                    # Fallback to existing layered builder on any DOT error
                    topology_builder = NetworkTopologyBuilder(self.petri_net)
                    resources = list(self.resource_mapping.values())
                    topology_builder.build(resources)
            else:
                print("Topology source: layered (no DOT file found)")
                # Fallback to existing layered builder if no DOT present
                topology_builder = NetworkTopologyBuilder(self.petri_net)
                resources = list(self.resource_mapping.values())
                topology_builder.build(resources)


            if not self.petri_net:
                logger.error("Failed to build Petri net model")
                raise ValueError("Failed to build Petri net model")

            logger.info(
                f"Petri net model built with {len(self.petri_net.places)} places and {len(self.petri_net.transitions)} transitions."
            )
            return self.petri_net

        except Exception as e:
            logger.error(f"Error building Petri net model: {str(e)}")
            # Re-raise the exception with more context
            raise ValueError(
                f"Failed to build Petri net model: {str(e)}"
            ) from e

    def _find_dot_file(self) -> Optional[str]:
        """Locate a Terraform Graphviz DOT file near the terraform path.

        Search order:
        - If terraform_path ends with .dot and exists, use it
        - If terraform_path is a directory, look for 'graph.dot' in root
        - Otherwise, recursively search for the first '*.dot' (prefer 'graph.dot')
        """
        try:
            if os.path.isfile(self.terraform_path) and self.terraform_path.lower().endswith(".dot"):
                return self.terraform_path

            if os.path.isdir(self.terraform_path):
                candidate = os.path.join(self.terraform_path, "graph.dot")
                if os.path.exists(candidate):
                    return candidate

                preferred: Optional[str] = None
                fallback: Optional[str] = None
                for root, _dirs, files in os.walk(self.terraform_path):
                    for fname in files:
                        if not fname.lower().endswith(".dot"):
                            continue
                        fpath = os.path.join(root, fname)
                        if fname == "graph.dot" and preferred is None:
                            preferred = fpath
                        if fallback is None:
                            fallback = fpath
                    # Short-circuit if both found early
                    if preferred and fallback:
                        break
                return preferred or fallback
        except Exception:
            return None
        return None

    def _parse_dot_graph(self, dot_path: str) -> Tuple[Set[str], List[Tuple[str, str]]]:
        """Parse a Graphviz DOT file produced by `terraform graph`.

        Returns a set of node ids (e.g., 'google_compute_router_nat.nat1') and a list
        of directed edges (src, dst) exactly as expressed in the DOT (A -> B).
        """
        nodes: Set[str] = set()
        edges: List[Tuple[str, str]] = []

        # Regex patterns for nodes and edges with quoted identifiers
        edge_re = re.compile(r"\s*\"([^\"]+)\"\s*->\s*\"([^\"]+)\"")
        node_re = re.compile(r"\s*\"([^\"]+)\"\s*\[")

        # Read with UTF-8 first; fall back to common encodings (handles UTF-16 BOM cases on Windows)
        lines: List[str] = []
        tried_encodings = []
        for enc in ("utf-8", "utf-8-sig", "utf-16", "utf-16le", "utf-16be", "cp1252", "latin-1"):
            try:
                with open(dot_path, "r", encoding=enc) as f:
                    lines = f.readlines()
                if enc != "utf-8":
                    print(f"DOT file encoding fallback: {enc}")
                break
            except UnicodeDecodeError as e:
                tried_encodings.append(f"{enc}: {e}")
                continue
        else:
            raise UnicodeDecodeError("dot-decode", b"", 0, 1, f"Unable to decode DOT using encodings: {', '.join(tried_encodings)}")

        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("digraph") or line in ("{", "}"):
                continue
            m_edge = edge_re.match(line)
            if m_edge:
                src, dst = m_edge.group(1), m_edge.group(2)
                edges.append((src, dst))
                nodes.add(src)
                nodes.add(dst)
                continue
            m_node = node_re.match(line)
            if m_node:
                nodes.add(m_node.group(1))

        return nodes, edges

    def _display_and_edit_topology_graph(self, dot_nodes: Set[str], dot_edges: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """Display and interactively edit the DOT dependency graph using Streamlit.

        - Filters out non-operational resources using `_is_non_operational`.
        - Renders a directed graph via streamlit-agraph.
        - Allows users to add/remove edges and persists them via `st.session_state`.
        - If Streamlit or streamlit-agraph are unavailable, returns the filtered edges unchanged.

        Args:
            dot_nodes: Nodes discovered in the DOT graph (simple ids 'type.name').
            dot_edges: Directed edges from the DOT graph as (src, dst) tuples.

        Returns:
            List[Tuple[str, str]]: The final, possibly user-modified list of edges.
        """
        # Local import to avoid hard dependency when running outside Streamlit
        try:
            import streamlit as st  # type: ignore
        except Exception:
            # Best-effort graceful fallback: filter and return
            included_nodes: Set[str] = set()
            for n in dot_nodes:
                if '.' not in n:
                    continue
                rtype = n.rsplit('.', 1)[0]
                if self._is_non_operational(rtype):
                    continue
                included_nodes.add(n)
            filtered_edges: List[Tuple[str, str]] = [
                (s, d) for (s, d) in dot_edges if s in included_nodes and d in included_nodes
            ]
            return filtered_edges

        # Detect if we're running under a real Streamlit context; if not, skip UI
        ctx = None
        try:
            from streamlit.runtime.scriptrunner import get_script_run_ctx  # type: ignore
            ctx = get_script_run_ctx()
        except Exception:
            ctx = None

        if ctx is None:
            included_nodes: Set[str] = set()
            for n in dot_nodes:
                if '.' not in n:
                    continue
                rtype = n.rsplit('.', 1)[0]
                if self._is_non_operational(rtype):
                    continue
                included_nodes.add(n)
            filtered_edges: List[Tuple[str, str]] = [
                (s, d) for (s, d) in dot_edges if s in included_nodes and d in included_nodes
            ]
            return filtered_edges

        # Now it's safe to import and use streamlit-agraph UI parts
        try:
            from streamlit_agraph import agraph, Node as ANode, Edge as AEdge, Config as AConfig  # type: ignore
        except Exception:
            # If agraph is missing, skip UI and return filtered edges
            included_nodes: Set[str] = set()
            for n in dot_nodes:
                if '.' not in n:
                    continue
                rtype = n.rsplit('.', 1)[0]
                if self._is_non_operational(rtype):
                    continue
                included_nodes.add(n)
            filtered_edges: List[Tuple[str, str]] = [
                (s, d) for (s, d) in dot_edges if s in included_nodes and d in included_nodes
            ]
            return filtered_edges

        # Compute filtered, operational-only view for editing
        included_nodes: Set[str] = set()
        for n in dot_nodes:
            if '.' not in n:
                continue
            rtype = n.rsplit('.', 1)[0]
            if self._is_non_operational(rtype):
                continue
            included_nodes.add(n)

        filtered_edges: List[Tuple[str, str]] = [
            (s, d) for (s, d) in dot_edges if s in included_nodes and d in included_nodes
        ]

        # Initialize session state with filtered edges if not present
        if 'topology_edges' not in st.session_state or not isinstance(st.session_state['topology_edges'], list):
            st.session_state['topology_edges'] = [(s, d) for (s, d) in filtered_edges]

        # Build AGraph nodes and edges from session state
        vis_nodes = [ANode(id=n, label=n) for n in sorted(included_nodes)]

        current_edges: List[Tuple[str, str]] = [tuple(e) for e in st.session_state['topology_edges']]
        vis_edges = [AEdge(source=s, target=d, color="#1e88e5", width=2) for (s, d) in current_edges]

        # Create agraph configuration; attempt to enable manipulation if supported
        config_kwargs = dict(
            width=900,
            height=600,
            directed=True,
            nodeHighlightBehavior=True,
            highlightColor="#F7A7A6",
            physics=True,
        )
        # Attempt to enable manipulation for edges only (disable node add)
        config = None
        try:
            add_edge_config = {
                "color": "#1e88e5",  # The same blue color
                "width": 2
            }

            config = AConfig(**{**config_kwargs, "manipulation": {
                "enabled": True,
                "addNode": False,
                "addEdge": add_edge_config,
                "editNode": False,
                "editEdge": False,
                "deleteNode": False,
                "deleteEdge": False,
            }})  # type: ignore[arg-type]
        except Exception:
            try:
                config = AConfig(**{**config_kwargs, "manipulation": True})  # type: ignore[arg-type]
            except Exception:
                config = AConfig(**config_kwargs)  # type: ignore[arg-type]

        st.subheader("Terraform Topology Editor")
        st.caption("Drag to connect nodes (if supported), or use the controls below to add/remove edges. Changes persist in session.")

        # Render the graph; some versions of streamlit-agraph return selected node/edge info
        event = agraph(nodes=vis_nodes, edges=vis_edges, config=config)

        # Add a clear header in your Streamlit app to find the output easily
        st.sidebar.subheader("Component Event Log")
        st.sidebar.write("Last user interaction with the graph returned this event object:")
        
        # Use st.json to pretty-print the event object in the sidebar
        st.sidebar.json(event, expanded=True)
        
        # Try to capture a drag-added edge from the event payload
        try:
            def _extract_edges(obj) -> List[Tuple[str, str]]:
                # Normalize to dict if possible
                def _to_dict(x):
                    if isinstance(x, dict):
                        return x
                    if hasattr(x, '__dict__'):
                        return vars(x)
                    if isinstance(x, str):
                        try:
                            return json.loads(x)
                        except Exception:
                            return None
                    return None

                d = _to_dict(obj)
                edges_found: List[Tuple[str, str]] = []
                if not d:
                    return edges_found

                # Direct keys
                src = d.get('from') or d.get('source')
                dst = d.get('to') or d.get('target')
                if src and dst:
                    edges_found.append((str(src), str(dst)))

                # Nested in 'edge' / 'data'
                for k in ('edge', 'data', 'payload', 'newEdge'):
                    if isinstance(d.get(k), (dict,)):
                        e = d[k]
                        s = e.get('from') or e.get('source')
                        t = e.get('to') or e.get('target')
                        if s and t:
                            edges_found.append((str(s), str(t)))

                # Arrays of edges
                for k in ('edges', 'addedEdges', 'newEdges'):
                    arr = d.get(k)
                    if isinstance(arr, (list, tuple)):
                        for e in arr:
                            if isinstance(e, (list, tuple)) and len(e) == 2:
                                edges_found.append((str(e[0]), str(e[1])))
                            elif isinstance(e, dict):
                                s = e.get('from') or e.get('source')
                                t = e.get('to') or e.get('target')
                                if s and t:
                                    edges_found.append((str(s), str(t)))
                return edges_found

            new_edges = _extract_edges(event)
            for (new_src, new_dst) in new_edges:
                if new_src in included_nodes and new_dst in included_nodes and new_src != new_dst:
                    if (new_src, new_dst) not in current_edges:
                        current_edges.append((new_src, new_dst))
                        st.session_state['topology_edges'] = list(current_edges)
        except Exception:
            pass

        # Manual controls as a fallback or complementary UX
        with st.expander("Edit edges"):
            cols = st.columns(3)
            with cols[0]:
                add_src = st.selectbox("From", options=sorted(included_nodes), key="edge_add_src")
            with cols[1]:
                add_dst = st.selectbox("To", options=sorted(included_nodes), key="edge_add_dst")
            with cols[2]:
                if st.button("Add edge"):
                    if add_src != add_dst and (add_src, add_dst) not in current_edges:
                        current_edges.append((add_src, add_dst))
                        st.session_state['topology_edges'] = list(current_edges)

            st.write("Current edges (click to remove):")
            # Display removable edges in a simple form
            remove_indices: List[int] = []
            for idx, (s, d) in enumerate(current_edges):
                if st.button(f"Remove {s} -> {d}", key=f"rm_{idx}"):
                    remove_indices.append(idx)
            if remove_indices:
                # Remove clicked edges
                st.session_state['topology_edges'] = [
                    e for i, e in enumerate(current_edges) if i not in remove_indices
                ]

        # Confirm apply button
        st.success("Topology edits are stored in session state. Click 'Apply topology' to proceed.")
        if st.button("Apply topology"):
            try:
                if hasattr(st, "toast"):
                    st.toast("Topology applied.")
            except Exception:
                pass

        # Always return the latest in-session edges
        latest_edges: List[Tuple[str, str]] = [tuple(e) for e in st.session_state['topology_edges']]
        return latest_edges

    def _maybe_edit_topology_via_ui(self, dot_nodes: Set[str], dot_edges: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """In CLI context, launch a minimal Streamlit app to edit topology.

        Behavior:
        - If already inside a Streamlit ScriptRunContext, use the in-process editor.
        - Else, spawn `streamlit run` on a small ephemeral app that reads nodes/edges
          from a temp JSON and writes the edited edges back to a temp JSON.
        - If anything fails or times out, return the original filtered edges.
        """
        # First, attempt to detect in-process Streamlit context
        try:
            from streamlit.runtime.scriptrunner import get_script_run_ctx  # type: ignore
            if get_script_run_ctx() is not None:
                return self._display_and_edit_topology_graph(dot_nodes, dot_edges)
        except Exception:
            pass

        # Compute filtered nodes/edges identical to the UI method
        included_nodes: Set[str] = set()
        for n in dot_nodes:
            if '.' not in n:
                continue
            rtype = n.rsplit('.', 1)[0]
            if self._is_non_operational(rtype):
                continue
            included_nodes.add(n)
        filtered_edges: List[Tuple[str, str]] = [
            (s, d) for (s, d) in dot_edges if s in included_nodes and d in included_nodes
        ]

        # Prepare a tiny Streamlit app source that uses streamlit-agraph
        app_source = dedent(
            """
            import json
            import sys
            import streamlit as st
            try:
                from streamlit_agraph import agraph, Node as ANode, Edge as AEdge, Config as AConfig
            except Exception:
                st.error("streamlit-agraph is required for topology editing.")
                st.stop()

            st.set_page_config(page_title="Terraform Topology Editor", layout="wide")
            st.title("Terraform Topology Editor")

            in_path = st.query_params.get("in", None) or (sys.argv[1] if len(sys.argv) > 1 else None)
            out_path = st.query_params.get("out", None) or (sys.argv[2] if len(sys.argv) > 2 else None)
            if not in_path or not out_path:
                st.error("Missing IO paths.")
                st.stop()

            with open(in_path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            nodes = payload.get('nodes', [])
            edges = payload.get('edges', [])

            if 'topology_edges' not in st.session_state:
                st.session_state['topology_edges'] = [(e[0], e[1]) for e in edges]

            vis_nodes = [ANode(id=n, label=n) for n in sorted(nodes)]
            vis_edges = [AEdge(source=s, target=d) for (s, d) in st.session_state['topology_edges']]

            try:
                
                config = AConfig(width=1200, height=700, directed=True, nodeHighlightBehavior=True, highlightColor="#F7A7A6", physics=True, manipulation={
                    "enabled": True,
                    "addNode": False,
                    "addEdge": True,
                    "editNode": False,
                    "editEdge": False,
                    "deleteNode": False,
                    "deleteEdge": False,
                })
            except Exception:
                try:
                    config = AConfig(width=1200, height=700, directed=True, nodeHighlightBehavior=True, highlightColor="#F7A7A6", physics=True, manipulation=True)
                except Exception:
                    config = AConfig(width=1200, height=700, directed=True, nodeHighlightBehavior=True, highlightColor="#F7A7A6", physics=True)

            event = agraph(nodes=vis_nodes, edges=vis_edges, config=config)

            # Capture newly dragged edges if provided by the component
            try:
                def _extract_edges(obj):
                    def _to_dict(x):
                        if isinstance(x, dict):
                            return x
                        if hasattr(x, '__dict__'):
                            return vars(x)
                        if isinstance(x, str):
                            try:
                                return json.loads(x)
                            except Exception:
                                return None
                        return None

                    d = _to_dict(obj)
                    edges_found = []
                    if not d:
                        return edges_found

                    src = d.get('from') or d.get('source')
                    dst = d.get('to') or d.get('target')
                    if src and dst:
                        edges_found.append((str(src), str(dst)))

                    for k in ('edge', 'data', 'payload', 'newEdge'):
                        if isinstance(d.get(k), dict):
                            e = d[k]
                            s = e.get('from') or e.get('source')
                            t = e.get('to') or e.get('target')
                            if s and t:
                                edges_found.append((str(s), str(t)))

                    for k in ('edges', 'addedEdges', 'newEdges'):
                        arr = d.get(k)
                        if isinstance(arr, (list, tuple)):
                            for e in arr:
                                if isinstance(e, (list, tuple)) and len(e) == 2:
                                    edges_found.append((str(e[0]), str(e[1])))
                                elif isinstance(e, dict):
                                    s = e.get('from') or e.get('source')
                                    t = e.get('to') or e.get('target')
                                    if s and t:
                                        edges_found.append((str(s), str(t)))
                    return edges_found

                for (new_src, new_dst) in _extract_edges(event):
                    if (new_src, new_dst) not in st.session_state['topology_edges'] and new_src != new_dst:
                        st.session_state['topology_edges'].append((new_src, new_dst))
            except Exception:
                pass

            with st.expander("Edit edges"):
                c1, c2, c3 = st.columns(3)
                with c1:
                    add_src = st.selectbox("From", options=sorted(nodes), key="edge_add_src")
                with c2:
                    add_dst = st.selectbox("To", options=sorted(nodes), key="edge_add_dst")
                with c3:
                    if st.button("Add edge"):
                        if add_src != add_dst and (add_src, add_dst) not in st.session_state['topology_edges']:
                            st.session_state['topology_edges'].append((add_src, add_dst))

                st.write("Current edges (click to remove):")
                for idx, (s, d) in enumerate(list(st.session_state['topology_edges'])):
                    if st.button(f"Remove {s} -> {d}", key=f"rm_{idx}"):
                        st.session_state['topology_edges'].pop(idx)

            if st.button("Apply topology"):
                with open(out_path, 'w', encoding='utf-8') as f:
                    json.dump({'edges': st.session_state['topology_edges']}, f)
                st.success("Saved. You may close this tab.")
            """
        )

        # Write temp files and spawn streamlit
        tmpdir = tempfile.mkdtemp()
        proc: Optional[subprocess.Popen] = None
        try:
            in_path = os.path.join(tmpdir, "graph_in.json")
            out_path = os.path.join(tmpdir, "graph_out.json")
            app_path = os.path.join(tmpdir, "app.py")

            with open(in_path, 'w', encoding='utf-8') as f:
                json.dump({'nodes': sorted(included_nodes), 'edges': filtered_edges}, f)
            with open(app_path, 'w', encoding='utf-8') as f:
                f.write(app_source)

            # Launch streamlit app; let the user interact and click Apply to produce out file
            try:
                proc = subprocess.Popen([
                    sys.executable, "-m", "streamlit", "run", app_path, in_path, out_path
                ])
            except Exception:
                # On failure to launch, return filtered edges
                return filtered_edges

            # Wait for the output file to appear; print instructions once
            print("Opening Streamlit editor... A browser window should appear.")
            print("After editing, click 'Apply topology' and return here.")

            try:
                start = time.time()
                # Wait up to 1 hour for user to apply
                while True:
                    if os.path.exists(out_path):
                        break
                    if proc.poll() is not None:
                        # App exited without producing output; fall back
                        return filtered_edges
                    time.sleep(1.0)
                    # Optional timeout safeguard
                    if time.time() - start > 3600:
                        print("Editor timeout reached; using original edges.")
                        return filtered_edges

                with open(out_path, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
                new_edges = payload.get('edges') or filtered_edges
                # Normalize to tuples of str
                normalized: List[Tuple[str, str]] = []
                for e in new_edges:
                    if isinstance(e, (list, tuple)) and len(e) == 2:
                        normalized.append((str(e[0]), str(e[1])))
                return normalized if normalized else filtered_edges
            finally:
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    except Exception:
                        pass
        finally:
            try:
                shutil.rmtree(tmpdir)
            except Exception:
                pass

    def _build_topology_from_dot(self, dot_nodes: Set[str], dot_edges: List[Tuple[str, str]]) -> None:
        """Construct a Petri net topology that mirrors the DOT dependency graph.

        - Filters out non-operational resources
        - Maps simple ids from DOT (type.name) to full TF ids parsed earlier
        - Creates per-resource in/out queues and a processing transition
        - For each DOT edge A -> B, creates a zero-delay routing transition from
          out_A to in_B
        - Connects global Source to all nodes with no incoming edges, and all nodes
          with no outgoing edges to global Sink
        """
        # Map simple id 'type.name' to full resource id
        simple_to_full: Dict[str, str] = {}
        for rid in self.tf_resources.keys():
            # child module ids look like 'module.x.module.y.type.name'; take last two segments
            parts = rid.split('.')
            if len(parts) >= 2:
                simple = '.'.join(parts[-2:])
                # Prefer first match; assume uniqueness for most graphs
                if simple not in simple_to_full:
                    simple_to_full[simple] = rid

        # Filter nodes to those we know and that are operational
        included_simples: Set[str] = set()
        for n in dot_nodes:
            if '.' not in n:
                continue
            rtype = n.rsplit('.', 1)[0]
            if self._is_non_operational(rtype):
                continue
            if n in simple_to_full:
                included_simples.add(n)

        if not included_simples:
            print("Topology source: layered (no DOT nodes matched resources)")
            # If nothing matches, fallback to layered builder
            topology_builder = NetworkTopologyBuilder(self.petri_net)
            resources = list(self.resource_mapping.values())
            topology_builder.build(resources)
            return

        # Ensure resources are mapped and added to model
        for simple in included_simples:
            full_id = simple_to_full[simple]
            res_data = self.tf_resources.get(full_id)
            if not res_data:
                continue
            res_type = res_data.get("type")
            res_name = res_data.get("name")
            res_config = res_data.get("config", {})
            if self._is_non_operational(res_type or ""):
                continue
            if full_id not in self.resource_mapping:
                resource = self._map_resource(res_type, res_name, res_config)
                if resource is not None:
                    self.resource_mapping[full_id] = resource

        # Build per-resource process transitions
        topo = NetworkTopologyBuilder(self.petri_net)
        in_place_by_simple: Dict[str, str] = {}
        out_place_by_simple: Dict[str, str] = {}

        for simple in included_simples:
            full_id = simple_to_full[simple]
            res = self.resource_mapping.get(full_id)
            if not res:
                continue
            in_pid = f"dot_in_{res.name}"
            out_pid = f"dot_out_{res.name}"
            topo.ensure_place(in_pid, f"In_{res.name}")
            topo.ensure_place(out_pid, f"Out_{res.name}")
            slot_pid = f"ResourceState_{res.name}"
            topo.add_process(res, in_pid, out_pid, slot_pid, default_delay=0.2)
            in_place_by_simple[simple] = in_pid
            out_place_by_simple[simple] = out_pid

        # Build routing edges per DOT
        def add_route(src_place: str, dst_place: str, tid: str) -> None:
            topo.ensure_transition(
                tid,
                name=tid,
                delay=0.0,
                action=lambda t, _i=src_place, _o=dst_place: { _o: t.get(_i, []).copy() },
            )
            topo.ensure_arc(f"Arc_In_{src_place}_To_{tid}", src_place, tid, "input")
            topo.ensure_arc(f"Arc_Out_{tid}_To_{dst_place}", dst_place, tid, "output")

        for src_simple, dst_simple in dot_edges:
            if src_simple not in included_simples or dst_simple not in included_simples:
                continue
            src_place = out_place_by_simple.get(src_simple)
            dst_place = in_place_by_simple.get(dst_simple)
            if not src_place or not dst_place:
                continue
            tid = f"Route_{src_place}_to_{dst_place}"
            add_route(src_place, dst_place, tid)

        # Connect global source/sink
        # Compute in-degree and out-degree among included nodes
        indeg: Dict[str, int] = {s: 0 for s in included_simples}
        outdeg: Dict[str, int] = {s: 0 for s in included_simples}
        for s, d in dot_edges:
            if s in included_simples and d in included_simples:
                outdeg[s] += 1
                indeg[d] += 1

        source_pid = PetriNet.SOURCE_PLACE_ID
        sink_pid = PetriNet.SINK_PLACE_ID
        topo.ensure_place(source_pid, "Global Source")
        topo.ensure_place(sink_pid, "Global Sink")
        
        print(f"Building DOT-based topology: {len(included_simples)} resources, {len([e for e in dot_edges if e[0] in included_simples and e[1] in included_simples])} internal edges")

        # Nodes with no incoming edges: treat as entry points
        for simple in included_simples:
            if indeg.get(simple, 0) == 0:
                dst_place = in_place_by_simple.get(simple)
                if not dst_place:
                    continue
                tid = f"Route_Source_to_{dst_place}"
                add_route(source_pid, dst_place, tid)

        # Nodes with no outgoing edges: treat as exit points
        for simple in included_simples:
            if outdeg.get(simple, 0) == 0:
                src_place = out_place_by_simple.get(simple)
                if not src_place:
                    continue
                tid = f"Route_{src_place}_to_Sink"
                add_route(src_place, sink_pid, tid)

    def _init_resource(
        self, res_class: Type[Resource], base_config: Dict[str, Any], **kwargs
    ) -> Optional[Resource]:
        """
        Safely initialize a resource class with given configuration.

        Args:
            res_class: The resource class to instantiate
            base_config: Base configuration dictionary
            **kwargs: Additional configuration parameters

        Returns:
            Optional[Resource]: Initialized resource or None if initialization fails
        """
        try:
            init_args = {**base_config, **kwargs}
            # Only include arguments that exist in the class's __init__ method
            valid_args = {}
            import inspect

            sig = inspect.signature(res_class.__init__)
            param_names = set(sig.parameters.keys()) - {"self"}
            for key in param_names:
                if key in init_args:
                    valid_args[key] = init_args[key]
            instance = res_class(**valid_args)
            # Ensure identity exists even if subclass forgot to call base __init__
            if not hasattr(instance, "id") or not getattr(instance, "id", None):
                # Use a stable fallback based on TF type/name if available
                tf_name = init_args.get("name") or res_class.__name__
                tf_region = init_args.get("region")
                instance.id = f"{tf_name}-{tf_region}" if tf_region else str(tf_name)
            return instance
        except Exception as e:
            logger.error(
                f"Failed to initialize resource {res_class.__name__} with args {kwargs}: {str(e)}"
            )
            return None

    def _determine_resource_type(self, res_type: str) -> Optional[str]:
        """Map Terraform resource type to LEAF-Cloud resource type.
        
        Args:
            res_type: The Terraform resource type (e.g., 'google_compute_instance')
            
        Returns:
            str: The corresponding LEAF-Cloud resource type, or None if no mapping exists
        """
        # Define resource type mappings with more specific types first
        resource_type_map = {
            # Compute Resources
            "google_compute_instance": "ComputeEngineVM",
            "google_compute_instance_template": "ComputeEngineVM",
            "google_compute_instance_group_manager": "ComputeEngineVM",
            "google_compute_region_instance_group_manager": "ComputeEngineVM",
            "google_container_cluster": "GKECluster",
            "google_container_node_pool": "GKENode",
            "google_cloudfunctions_function": "CloudFunction",
            "google_cloud_run_service": "CloudRun",
            "google_cloud_run_v2_service": "CloudRun",  # Cloud Run V2
            "google_app_engine_application": "AppEngine",
            "google_app_engine_standard_app_version": "AppEngine",
            "google_app_engine_flexible_app_version": "AppEngine",
            
            # Storage Resources
            "google_storage_bucket": "CloudStorage",
            "google_compute_disk": "PersistentDisk",
            "google_compute_region_disk": "PersistentDisk",
            "google_filestore_instance": "Filestore",
            "google_cloud_storage_bucket": "CloudStorage",  # Alternative naming
            
            # Database Resources
            "google_sql_database_instance": "CloudSQL",
            "google_sql_database": "CloudSQL",
            "google_bigtable_instance": "Bigtable",
            "google_bigtable_table": "Bigtable",
            "google_spanner_instance": "Spanner",
            "google_spanner_database": "Spanner",
            "google_firestore_database": "Firestore",
            "google_redis_instance": "Memorystore",
            "google_datastore_index": "Datastore",
            "google_firestore_index": "Firestore",
            
            # Networking Resources
            "google_compute_network": "VPC",
            "google_compute_subnetwork": "VPC",
            "google_compute_firewall": "NetworkResource",
            "google_compute_router": "NetworkResource",
            "google_compute_router_nat": "NetworkResource",
            "google_vpc_access_connector": "NetworkResource",
            "google_compute_forwarding_rule": "LoadBalancer",
            "google_compute_url_map": "LoadBalancer",
            "google_compute_target_http_proxy": "LoadBalancer",
            "google_compute_target_https_proxy": "LoadBalancer",
            "google_compute_ssl_certificate": "LoadBalancer",
            "google_compute_ssl_policy": "LoadBalancer",
            "google_dns_managed_zone": "DNS",
            "google_dns_record_set": "DNS",
            "google_compute_vpn_tunnel": "VPN",
            "google_compute_vpn_gateway": "VPN",
            "google_compute_ha_vpn_gateway": "VPN",
            "google_compute_interconnect_attachment": "Interconnect",
            "google_compute_network_endpoint_group": "LoadBalancer",
            "google_compute_global_forwarding_rule": "LoadBalancer",
            "google_compute_backend_service": "LoadBalancer",
            "google_compute_backend_bucket": "CDN",
            "google_network_services_edge_cache_service": "CDN",
            "google_network_services_edge_cache_origin": "CDN",
            
            # API Gateway & Service Mesh
            "google_api_gateway_api": "APIGateway",
            "google_api_gateway_api_config": "APIGateway",
            "google_api_gateway_gateway": "APIGateway",
            "google_network_services_gateway": "APIGateway",
            "google_network_services_http_route": "APIGateway",
            "google_network_services_tcp_route": "APIGateway",
            "google_network_services_tls_route": "APIGateway",
            "google_network_services_grpc_route": "APIGateway",

            "google_kms_key_ring": "CloudKMS",
            "google_kms_crypto_key": "CloudKMS",
            "google_kms_secret_ciphertext": "CloudKMS",
            "google_secret_manager_secret": "SecretManager",
            "google_secret_manager_secret_version": "SecretManager",
            "google_scc_source": "SecurityCommandCenter",
            "google_compute_security_policy": "CloudArmor",
            
            # Serverless & Containers
            "google_cloudbuild_trigger": "CloudBuild",
            "google_cloudbuild_worker_pool": "CloudBuild",
            "google_cloudfunctions2_function": "CloudFunction",
            
            # Data & Analytics
            "google_bigquery_dataset": "BigQuery",
            "google_bigquery_table": "BigQuery",
            "google_bigquery_job": "BigQuery",
            "google_dataflow_job": "Dataflow",
            "google_dataproc_cluster": "Dataproc",
            "google_dataproc_job": "Dataproc",
            "google_pubsub_topic": "PubSub",
            "google_pubsub_subscription": "PubSub",
            "google_data_fusion_instance": "DataFusion",
            "google_data_catalog_entry": "DataCatalog",
            
            # AI & ML
            "google_ml_engine_model": "AIPlatform",
            "google_vertex_ai_dataset": "VertexAI",
            "google_vertex_ai_model": "VertexAI",
            "google_vertex_ai_endpoint": "VertexAI",
            "google_dialogflow_agent": "Dialogflow",
            
            # More specific mappings can be added here
        }

        # Try exact match first
        if res_type in resource_type_map:
            return resource_type_map[res_type]

        # Do not use broad vendor-prefix fallback. If there is no explicit mapping,
        # return None so the caller can default to GenericGCPComponent.
        return None  # Default or unmapped

    def export_tf_resources(self, output_path: str) -> None:
        """
        Export the parsed Terraform resources to a JSON file.

        Args:
            output_path: The path to the file where resources should be saved.
        """
        try:
            with open(output_path, "w") as f:
                json.dump(self.tf_resources, f, indent=4)
            logger.info(f"Terraform resources exported to {output_path}")
        except IOError as e:
            logger.error(
                f"Error exporting Terraform resources to {output_path}: {e}"
            )
        except TypeError as e:
            logger.error(f"Error serializing Terraform resources to JSON: {e}")



    def _process_terraform_resources(self) -> None:
        """
        Process Terraform resources and map them to Petri net elements with comprehensive error handling.
        """
        try:
            if not self.tf_resources:
                logger.info("No Terraform resources loaded, attempting to parse files")
                self.parse_terraform_files()
                
            if not self.tf_resources:
                logger.warning("No Terraform resources found after parsing")
                raise ValueError("No Terraform resources found to build model from")

            logger.info(f"Processing {len(self.tf_resources)} Terraform resources")
            
            # Track processing statistics
            processed_count = 0
            skipped_count = 0
            failed_count = 0

            for res_id, resource in self.tf_resources.items():
                try:
                    # Validate resource structure
                    if not isinstance(resource, dict):
                        logger.warning(f"Invalid resource structure for {res_id}: expected dict, got {type(resource)}")
                        failed_count += 1
                        continue
                        
                    res_type = resource.get("type")
                    res_name = resource.get("name")
                    res_config = resource.get("config", {})
                    
                    if not res_type or not res_name:
                        logger.warning(f"Resource {res_id} missing required fields: type={res_type}, name={res_name}")
                        failed_count += 1
                        continue

                    # Check if resource is operational
                    if self._is_non_operational(res_type):
                        logger.debug("Filtered non-operational resource %s.%s", res_type, res_name)
                        skipped_count += 1
                        continue
                        
                    # Attempt to map the resource
                    logger.debug(f"Attempting to map resource {res_id} of type {res_type}")
                    leaf_resource = self._map_resource(res_type, res_name, res_config)

                    if leaf_resource:
                        try:
                            # Add to resource mapping
                            self.resource_mapping[res_id] = leaf_resource
                            
                            # Add to appropriate resource pool
                            if hasattr(leaf_resource, 'resource_type') and leaf_resource.resource_type in self.resource_pools:
                                self.resource_pools[leaf_resource.resource_type].add_resource(leaf_resource)
                            else:
                                logger.debug(f"Resource {res_id} has unknown resource_type: {getattr(leaf_resource, 'resource_type', 'None')}")
                            
                            processed_count += 1
                            logger.debug(f"Mapped Terraform resource {res_id} to LEAF-Cloud resource {leaf_resource.name}")
                            
                        except Exception as e:
                            logger.error(f"Failed to add resource {res_id} to pools: {e}")
                            # Remove from mapping if pool addition failed
                            if res_id in self.resource_mapping:
                                del self.resource_mapping[res_id]
                            failed_count += 1
                    else:
                        logger.warning(f"Resource {res_id} was not mapped (unsupported type or mapping failed)")
                        skipped_count += 1
                        
                except Exception as e:
                    logger.error(f"Error processing resource {res_id}: {e}")
                    failed_count += 1
                    continue

            # Log processing summary
            total_resources = len(self.tf_resources)

            # Resolve any pending GKE node pools now that clusters are mapped
            try:
                if self._pending_node_pools:
                    logger.info("Resolving %d pending GKE node pools", len(self._pending_node_pools))
                for pool in self._pending_node_pools:
                    cluster_name = pool.get("cluster_name")
                    region = pool.get("region")
                    machine_type = pool.get("machine_type") or "n1-standard-1"
                    node_count = int(pool.get("node_count") or 0)
                    pool_name = pool.get("pool_name") or "node-pool"
                    min_node_count = pool.get("min_node_count")
                    max_node_count = pool.get("max_node_count")

                    # Find the Terraform resource id for the matching cluster by its configured name
                    cluster_rid = None
                    for rid, r in self.tf_resources.items():
                        if r.get("type") == "google_container_cluster":
                            cfg = r.get("config", {}) or {}
                            if str(cfg.get("name") or "").strip() == str(cluster_name or "").strip():
                                cluster_rid = rid
                                break
                    if not cluster_rid:
                        logger.warning("Could not find cluster for node pool '%s' (cluster='%s')", pool_name, cluster_name)
                        continue

                    cluster_obj = self.resource_mapping.get(cluster_rid)
                    if not cluster_obj:
                        logger.warning("Cluster '%s' not yet mapped; skipping node pool '%s'", cluster_name, pool_name)
                        continue

                    if hasattr(cluster_obj, "set_autoscaling_bounds"):
                        cluster_obj.set_autoscaling_bounds(min_node_count, max_node_count)

                    # Infer vCPU and memory from machine type
                    try:
                        cpu_count, mem_gb, gpu_type, gpu_count = self._infer_machine_specs(machine_type)
                    except Exception:
                        cpu_count, mem_gb, gpu_type, gpu_count = 2, 4.0, None, 0

                    # Attach nodes to the cluster
                    try:
                        from ..gcp.compute import GKENode
                        added = 0
                        existing_nodes = len(cluster_obj.nodes)
                        target_initial_nodes = node_count
                        if isinstance(min_node_count, int):
                            target_initial_nodes = max(target_initial_nodes, min_node_count)
                        if isinstance(max_node_count, int):
                            target_initial_nodes = min(target_initial_nodes, max_node_count)
                        target_initial_nodes = max(target_initial_nodes, getattr(cluster_obj, "min_nodes", 0))
                        nodes_to_add = max(0, target_initial_nodes - existing_nodes)
                        start_index = existing_nodes
                        new_nodes: List[Tuple[int, GKENode]] = []
                        for i in range(nodes_to_add):
                            node_index = start_index + i
                            node = GKENode(
                                name=f"{cluster_obj.name}-node-{node_index}",
                                machine_type=machine_type,
                                region=region or getattr(cluster_obj, "region", None) or self.env_config.get("region", "us-central1"),
                                vcpus=int(cpu_count),
                                memory_gb=float(mem_gb),
                            )
                            try:
                                node.attributes.setdefault("cluster_resource_id", cluster_rid)
                                if pool_name:
                                    node.attributes.setdefault("node_pool", pool_name)
                            except Exception:
                                pass
                            cluster_obj.add_node(node)
                            new_nodes.append((node_index, node))
                            added += 1
                        if added > 0:
                            logger.info(
                                "Attached %d node(s) (type=%s) to GKE cluster '%s' from pool '%s'",
                                added,
                                machine_type,
                                cluster_obj.name,
                                pool_name,
                            )
                        # Ensure nodes (new or existing) are discoverable in resource mapping
                        try:
                            self.register_cluster_nodes(
                                cluster_rid=cluster_rid,
                                cluster_obj=cluster_obj,
                                pool_name=pool_name,
                                newly_added=new_nodes,
                            )
                        except Exception as reg_err:
                            logger.error(
                                "Failed to register node(s) for pool '%s' on cluster '%s': %s",
                                pool_name,
                                cluster_name,
                                reg_err,
                            )
                    except Exception as e:
                        logger.error("Failed attaching nodes for pool '%s' to cluster '%s': %s", pool_name, cluster_name, e)
                        continue
                # All pending pools have been processed; prevent duplicate work on subsequent passes
                self._pending_node_pools = []
            except Exception as e:
                logger.error("Error resolving pending node pools: %s", e)
            logger.info(
                f"Resource processing completed: {processed_count} processed, "
                f"{skipped_count} skipped, {failed_count} failed out of {total_resources} total"
            )
            
            if processed_count == 0 and total_resources > 0:
                logger.warning("No resources were successfully processed - this may indicate a configuration issue")
                
        except Exception as e:
            logger.error(f"Critical error in resource processing: {e}")
            # Ensure resource_mapping exists even if processing fails
            if not hasattr(self, 'resource_mapping'):
                self.resource_mapping = {}
            raise

    def _create_token_transfer_action(
        self, from_place_id: PlaceId, to_place_id: PlaceId
    ) -> Callable[[Dict[PlaceId, List[Token]]], Dict[PlaceId, List[Token]]]:
        def transfer_action(
            tokens: Dict[PlaceId, List[Token]],
        ) -> Dict[PlaceId, List[Token]]:
            output_tokens: Dict[PlaceId, List[Token]] = {to_place_id: []}
            if from_place_id in tokens:
                # Move all tokens from the 'from' place to the 'to' place.
                # In a typical Petri net, a transition consumes specific tokens based on arc weights.
                # This action implies consuming all available from the specified input place that enabled the transition.
                for token in tokens[from_place_id]:
                    output_tokens[to_place_id].append(token)
            return output_tokens

        return transfer_action

    def operational_resources(self) -> Dict[str, Dict[str, Any]]:
        """tf_resources minus nonoperational ones."""
        return {
            rid: r for rid, r in self.tf_resources.items()
            if not self._is_non_operational(r["type"])
        }

    def _create_compute_resource(
        self, 
        res_type: str, 
        res_name: str, 
        res_config: Dict[str, Any], 
        base_config: Dict[str, Any]
    ) -> Optional[Resource]:
        """Create a compute resource from the given configuration.
        
        Args:
            res_type: Terraform resource type
            res_name: Resource name
            res_config: Resource configuration dictionary
            base_config: Base configuration dictionary with common settings
            
        Returns:
            The created compute resource, or None if creation fails
        """
        try:
            # Handle different compute resource types
            if res_type == "google_compute_instance":
                # Extract instance configuration
                machine_type = self._get_dict_value(res_config, "machine_type", "n1-standard-1")

                # Infer vCPU/memory and GPUs from the machine type (consistent with templates/MIGs)
                cpu_count, memory_gb, gpu_type, gpu_count = self._infer_machine_specs(machine_type)

                # Create the compute instance with inferred specs
                return self._init_resource(
                    ComputeEngineVM,
                    base_config,
                    machine_type=machine_type,
                    vcpus=cpu_count,
                    memory_gb=memory_gb,
                    gpu_type=gpu_type,
                    gpu_count=gpu_count,
                )

            elif res_type == "google_compute_instance_template":
                # Instance templates define a single VM shape; treat as one VM
                machine_type = self._get_dict_value(res_config, "machine_type", None)
                if not machine_type:
                    # Some templates may nest properties
                    machine_type = self._get_dict_value(res_config, ["properties", "machine_type"], "n1-standard-1")

                # Heuristic parsing of machine type to vCPU and memory
                cpu_count, memory_gb, gpu_type, gpu_count = self._infer_machine_specs(machine_type)

                return self._init_resource(
                    ComputeEngineVM,
                    base_config,
                    machine_type=machine_type,
                    vcpus=cpu_count,
                    memory_gb=memory_gb,
                    gpu_type=gpu_type,
                    gpu_count=gpu_count,
                )

            elif res_type in (
                "google_compute_instance_group_manager",
                "google_compute_region_instance_group_manager",
            ):
                # MIG/RIGM: derive per-instance shape from referenced template
                target_size = self._safe_cast(
                    self._get_dict_value(res_config, "target_size", 1), int, 1
                )

                # Try direct field
                template_ref = self._get_dict_value(res_config, "instance_template")
                if not template_ref:
                    # Some configs use versions[0].instance_template
                    versions = self._get_dict_value(res_config, "version") or []
                    if isinstance(versions, list) and versions:
                        template_ref = self._get_dict_value(versions[0], "instance_template")

                machine_type = None
                gpu_type = None
                gpu_count = 0
                if template_ref:
                    # Extract the template name from self_link or terraform ref
                    # Accept formats like:
                    #   google_compute_instance_template.web
                    #   projects/.../global/instanceTemplates/web
                    tmpl_name = str(template_ref).split("/")[-1]
                    if "." in template_ref and not "/" in template_ref:
                        # terraform style: google_compute_instance_template.NAME
                        try:
                            tmpl_name = template_ref.split(".", 1)[1]
                        except Exception:
                            pass

                    # Find matching template resource in tf_resources (module-aware)
                    template_entry = None
                    for rid, r in self.tf_resources.items():
                        if r.get("type") == "google_compute_instance_template":
                            rname = r.get("name")
                            if rname == tmpl_name or rid.endswith(f"google_compute_instance_template.{tmpl_name}"):
                                template_entry = r
                                break

                    if template_entry:
                        tcfg = template_entry.get("config", {})
                        machine_type = self._get_dict_value(tcfg, "machine_type") or \
                                       self._get_dict_value(tcfg, ["properties", "machine_type"]) or "n1-standard-1"
                        # Optional GPU hints (not always present in template; we infer by type if absent)
                        # No-op if not provided

                # Infer specs from machine type (with GPU inference for A2 shapes)
                cpu_per_instance, mem_per_instance_gb, gpu_type, gpu_count_inferred = self._infer_machine_specs(machine_type or "n1-standard-1")
                if gpu_count == 0:
                    gpu_count = gpu_count_inferred

                # Model the whole group as a single logical compute resource by scaling specs
                total_vcpus = max(1, target_size) * cpu_per_instance
                total_mem_gb = max(1, target_size) * mem_per_instance_gb

                name_for_group = f"{res_name}-group"
                group_base = {**base_config, "name": name_for_group}

                return self._init_resource(
                    ComputeEngineVM,
                    group_base,
                    machine_type=(machine_type or "n1-standard-1"),
                    vcpus=total_vcpus,
                    memory_gb=total_mem_gb,
                    gpu_type=gpu_type,
                    gpu_count=gpu_count * max(1, target_size),
                )
                
            elif res_type == "google_container_cluster":
                # Handle GKE cluster
                node_count = self._get_dict_value(
                    res_config, 
                    ["node_pool", "initial_node_count"], 
                    default=3
                )
                cluster_name = base_config.get("name")
                autoscaler_cfg = None
                if isinstance(self.cluster_autoscaler_requirements, dict):
                    if cluster_name and cluster_name in self.cluster_autoscaler_requirements:
                        autoscaler_cfg = self.cluster_autoscaler_requirements.get(cluster_name)
                    elif "*" in self.cluster_autoscaler_requirements:
                        autoscaler_cfg = self.cluster_autoscaler_requirements.get("*")
                
                return self._init_resource(
                    GKECluster,
                    base_config,
                    node_count=node_count,
                    default_pod_cpu=self.pod_resource_requirements.get("cpu"),
                    default_pod_memory_gb=self.pod_resource_requirements.get("memory_gb"),
                    min_pods=(autoscaler_cfg or {}).get("min_pods") if autoscaler_cfg else None,
                    max_pods=(autoscaler_cfg or {}).get("max_pods") if autoscaler_cfg else None,
                )

            elif res_type == "google_container_node_pool":
                # Handle GKE node pool by deferring node attachment until clusters are mapped
                # Extract cluster name and node specs
                cluster_name = self._get_dict_value(res_config, "cluster")
                # node_count may be set via node_count or initial_node_count
                node_count = self._safe_cast(
                    self._get_dict_value(res_config, "node_count", self._get_dict_value(res_config, "initial_node_count", 1)),
                    int,
                    1,
                )
                # node_config is a block; parser typically yields a list of dicts
                machine_type = None
                try:
                    node_cfgs = res_config.get("node_config") or []
                    if isinstance(node_cfgs, list) and node_cfgs:
                        machine_type = (node_cfgs[0] or {}).get("machine_type")
                except Exception:
                    machine_type = None

                min_node_count = None
                max_node_count = None
                autoscaling_blocks = res_config.get("autoscaling") or []
                if isinstance(autoscaling_blocks, list) and autoscaling_blocks:
                    autoscaling_config = autoscaling_blocks[0] or {}
                    min_node_count = self._safe_cast(
                        autoscaling_config.get("min_node_count"),
                        int,
                        None,
                    )
                    max_node_count = self._safe_cast(
                        autoscaling_config.get("max_node_count"),
                        int,
                        None,
                    )

                pool_record = {
                    "pool_name": base_config.get("name"),
                    "cluster_name": cluster_name,
                    "node_count": node_count,
                    "machine_type": machine_type or "n1-standard-1",
                    "region": base_config.get("region") or self.env_config.get("region", "us-central1"),
                    "min_node_count": min_node_count,
                    "max_node_count": max_node_count,
                }
                self._pending_node_pools.append(pool_record)
                logger.debug(
                    "Queued node pool '%s' for cluster '%s': %s nodes of %s",
                    pool_record["pool_name"], cluster_name, node_count, pool_record["machine_type"],
                )
                # Return None so we don't create a standalone compute resource for pools
                return None
                
            elif res_type == "google_cloudfunctions_function":
                # Handle Cloud Function
                memory_mb = self._parse_memory_limit(
                    self._get_dict_value(res_config, "available_memory_mb", "256M")
                )
                
                return self._init_resource(
                    CloudFunction,
                    base_config,
                    memory_mb=memory_mb,
                )
                
            elif res_type in ("google_cloud_run_service", "google_cloud_run_v2_service"):
                # Handle Cloud Run service with enhanced Terraform configuration parsing
                try:
                    cloudrun_config = self._parse_cloudrun_terraform_config(res_config, res_name)

                    # NEW: Create a dictionary of power profile parameters from the tuner
                    power_params = {
                        "run_base_idle_w": self.tuned_params.get("run_base_idle_w"),
                        "run_vcpu_active_w": self.tuned_params.get("run_vcpu_active_w"),
                        "run_base_active_w": self.tuned_params.get("run_base_active_w"),
                    }
                    power_params = {k: v for k, v in power_params.items() if v is not None}
                    
                    return self._init_resource(
                        CloudRun,
                        base_config,
                        cpu_limit=cloudrun_config['cpu_limit'],
                        memory_limit_mb=cloudrun_config['memory_limit_mb'],
                        max_instances=cloudrun_config['max_instances'],
                        min_instances=cloudrun_config['min_instances'],
                        concurrency=cloudrun_config['concurrency'],
                        power_profile_params=power_params,  # NEW: Pass the tuned parameters
                        requests_per_vcpu=self.tuned_params.get("run_requests_per_vcpu"),
                        request_processing_time=self.tuned_params.get("run_request_processing_s"),
                    )
                    
                except Exception as e:
                    logger.error(
                        f"Failed to parse CloudRun configuration for {res_name}: {str(e)}"
                    )
                    # Return CloudRun with sensible defaults as fallback
                    return self._init_resource(
                        CloudRun,
                        base_config,
                        cpu_limit=1.0,
                        memory_limit_mb=512,
                        max_instances=100,
                        min_instances=0,
                        concurrency=80
                    )
                
            elif res_type == "google_app_engine_application":
                # Handle App Engine
                return self._init_resource(
                    AppEngine,
                    base_config,
                )
                
        except Exception as e:
            logger.error(f"Error creating compute resource {res_type}.{res_name}: {e}")
            return None

    def _infer_machine_specs(self, machine_type: Optional[str]) -> Tuple[int, float, Optional[str], int]:
        """Infer vCPU, memory (GB), and GPU attachment from a GCE machine type string.

        Returns: (vcpus, memory_gb, gpu_type, gpu_count)
        """
        mt = (machine_type or "n1-standard-1").lower()

        # Defaults
        vcpus = 1
        mem_gb = 3.75
        gpu_type: Optional[str] = None
        gpu_count = 0

        try:
            # Special fixed shapes
            if mt == "e2-medium":
                return 2, 4.0, None, 0

            # A2 highgpu family (approximate typical A100 40GB profile)
            # a2-highgpu-1g, a2-highgpu-2g, ...
            if mt.startswith("a2-highgpu-"):
                m = re.search(r"a2-highgpu-(\d+)g", mt)
                g = int(m.group(1)) if m else 1
                # Per GCP docs, a2-highgpu-1g has 12 vCPU and ~85 GB RAM
                # Scale roughly linearly by GPU count for other shapes
                return 12 * g, 85.0 * g, "nvidia-a100", g

            # Standard families
            m = re.search(r"^(n1|n2|e2|c2|c2d|c3)-(standard|highmem|highcpu)-(\d+)$", mt)
            if m:
                family, flavor, count_s = m.group(1), m.group(2), m.group(3)
                count = int(count_s)
                vcpus = count
                # Memory per vCPU heuristics (GB)
                if family == "n1":
                    per = 3.75 if flavor == "standard" else (6.5 if flavor == "highmem" else 0.9)
                else:
                    # e2/c2/c2d/c3 standard ≈4GB, highmem ≈8GB, highcpu ≈1GB
                    per = 4.0 if flavor == "standard" else (8.0 if flavor == "highmem" else 1.0)
                mem_gb = per * vcpus
                return vcpus, mem_gb, None, 0

            # Fallback known small shapes
            if mt.startswith("e2-"):
                # e2-small (2 vCPU, 2GB), e2-micro (2 vCPU, 1GB), e2-medium (2 vCPU, 4GB handled above)
                if mt == "e2-small":
                    return 2, 2.0, None, 0
                if mt == "e2-micro":
                    return 2, 1.0, None, 0

        except Exception:
            pass

        return vcpus, mem_gb, gpu_type, gpu_count

    def _add_resource_to_model(
        self,
        resource: Resource,
        tf_resource_type: str,
        res_config: Dict[str, Any],
    ) -> None:
        """Add a resource to the model with appropriate capacity configuration."""
        try:
            capacity = get_capacity_from_config(
            tf_resource_type, res_config, resource.resource_type
        )
            if capacity and hasattr(resource, "set_capacity"):
                resource.set_capacity(capacity)

            state_place_id = f"ResourceState_{resource.name}"
            if state_place_id not in self.petri_net.places:
                capacity_limit = getattr(resource, "capacity", None)
                state_place = Place(
                    id=state_place_id,
                    name=f"State_{resource.name}",
                    capacity=capacity_limit,
                )
                self.petri_net.add_place(state_place)

                place_obj = self.petri_net.places.get(state_place_id)

                initial_token = Token(
                    color=TokenColor.RESOURCE,
                    attributes={
                        "resource_id": resource.id,
                        "resource_name": resource.name,
                    },
                    token_id=f"resource_token_{resource.name}"
                )
                self.petri_net.add_token(state_place_id, initial_token)

                # Synchronize slot tokens with compute concurrency/capacity
                # For compute resources, concurrency should equal available slots.
                try:
                    if isinstance(resource, ComputeResource):
                        desired_slots: Optional[int] = None

                        # Prefer dynamic properties when available (e.g., Cloud Run)
                        instances = getattr(resource, "current_instances", None)
                        concurrency = getattr(resource, "concurrency", None)
                        try:
                            if instances is not None and concurrency is not None:
                                desired_slots = int(max(1, int(instances)) * max(1, int(concurrency)))
                        except Exception:
                            desired_slots = None

                        # Fallback to capacity if concurrency not explicitly modeled
                        if desired_slots is None:
                            try:
                                cap_val = getattr(resource, "capacity", None)
                                if cap_val is not None:
                                    desired_slots = int(max(1, int(float(cap_val))))
                            except Exception:
                                desired_slots = None

                        # Add additional tokens up to desired_slots (one already added above)
                        if desired_slots and desired_slots > 1:
                            if place_obj is not None:
                                try:
                                    existing = place_obj.token_count
                                except Exception:
                                    existing = len(place_obj.tokens)

                                to_add = max(0, desired_slots - existing)
                                if to_add > 0:
                                    for _ in range(to_add):
                                        try:
                                            place_obj.add_token(Token())
                                        except Exception:
                                            # Respect place capacity or any runtime constraint
                                            break
                except Exception:
                    # Best effort; continue even if slot setup fails
                    pass

                if place_obj is not None:
                    try:
                        slot_capacity = float(place_obj.token_count)
                    except Exception:
                        slot_capacity = float(len(place_obj.tokens))
                    try:
                        setattr(resource, "_petri_slot_capacity", slot_capacity)
                    except Exception:
                        try:
                            resource.attributes.setdefault("petri_slot_capacity", slot_capacity)
                        except Exception:
                            pass

        except Exception as e:
            logger.warning(
                f"Failed to add resource {resource.name} to model: {e}"
            )

    def register_cluster_nodes(
        self,
        cluster_rid: str,
        cluster_obj: GKECluster,
        pool_name: Optional[str] = None,
        newly_added: Optional[List[Tuple[int, GKENode]]] = None,
    ) -> Tuple[List[str], List[str]]:
        """Expose GKE cluster nodes in resource_mapping and resource pools."""
        if not cluster_obj or not hasattr(cluster_obj, "nodes"):
            return [], []

        # Track indices explicitly flagged as new so we can annotate them without
        # mutating nodes from other pools.
        newly_added_indices = (
            {idx for idx, _node in newly_added} if newly_added else set()
        )

        # Sanitize pool name for annotations.
        sanitized_pool = None
        if pool_name:
            try:
                sanitized_pool = re.sub(r"[^0-9A-Za-z._-]+", "-", pool_name).strip("-") or None
            except Exception:
                sanitized_pool = pool_name

        compute_pool = self.resource_pools.get(ResourceType.COMPUTE)

        registered_ids: List[str] = []
        active_ids: Set[str] = set()

        for idx, node in enumerate(cluster_obj.nodes):
            try:
                node.attributes.setdefault("cluster_resource_id", cluster_rid)
                if idx in newly_added_indices and sanitized_pool:
                    node.attributes["node_pool"] = sanitized_pool
            except Exception:
                pass

            node_res_id = f"{cluster_rid}.node[{idx}]"
            active_ids.add(node_res_id)
            if node_res_id not in self.resource_mapping:
                registered_ids.append(node_res_id)
            self.resource_mapping[node_res_id] = node

            state_place_id = f"ResourceState_{node.name}"
            if state_place_id not in self.petri_net.places:
                try:
                    self._add_resource_to_model(
                        node,
                        tf_resource_type="google_container_node_pool",
                        res_config={},
                    )
                except Exception as add_err:
                    logger.debug(
                        "Failed to add GKE node %s to Petri net model: %s",
                        node_res_id,
                        add_err,
                    )

            if compute_pool:
                try:
                    if node.id not in compute_pool.resources:
                        compute_pool.add_resource(node)
                except ValueError:
                    # Already registered
                    pass
                except Exception as pool_err:
                    logger.debug(
                        "Failed to add node %s to compute pool: %s",
                        node_res_id,
                        pool_err,
                    )

        # Remove stale node entries that no longer exist on the cluster
        removed_ids: List[str] = []
        prefix = f"{cluster_rid}.node["
        stale_ids = [
            res_id
            for res_id in list(self.resource_mapping.keys())
            if res_id.startswith(prefix) and res_id not in active_ids
        ]
        for res_id in stale_ids:
            node_obj = self.resource_mapping.pop(res_id, None)
            removed_ids.append(res_id)
            if compute_pool and isinstance(node_obj, Resource):
                try:
                    compute_pool.remove_resource(node_obj.id)
                except Exception:
                    pass

        return registered_ids, removed_ids

    def _map_resource(
        self, res_type: str, res_name: str, res_config: Dict[str, Any]
    ) -> Optional[Resource]:
        """Map a Terraform resource to a LEAF‑Cloud resource."""
        if self._is_non_operational(res_type):
            logger.debug(
                f"Skipping IAM resource {res_type}.{res_name} - no operational energy"
            )
            return None

        try:
            base_config = self._get_base_resource_config(res_type, res_name, res_config)
            leaf_res_class, leaf_resource_type_enum = self._get_resource_class(res_type)

            # skip filtered‑out resources
            if leaf_res_class is None and leaf_resource_type_enum is None:
                logger.warning(f"Resource {res_type}.{res_name} filtered out - no resource class mapping found")
                return None

            if leaf_res_class is None:
                logger.info(
                    f"No specific class found for {res_type}, using GenericGCPComponent"
                )
                leaf_res_class = GenericGCPComponent
                leaf_resource_type_enum = ResourceType.GENERIC

            resource = None
            if leaf_resource_type_enum == ResourceType.COMPUTE:
                resource = self._create_compute_resource(
                    res_type, res_name, res_config, base_config
                )
            elif leaf_resource_type_enum == ResourceType.STORAGE:
                resource = self._create_storage_resource(
                    res_type, res_name, res_config, base_config
                )
            elif leaf_resource_type_enum == ResourceType.NETWORK:
                resource = self._create_network_resource(
                    res_type, res_name, res_config, base_config
                )
            elif leaf_resource_type_enum == ResourceType.SECURITY:
                resource = self._create_security_resource(
                    res_type, res_name, res_config, base_config
                )
            else:
                resource = self._init_resource(leaf_res_class, base_config)

            if resource is not None:
                self._add_resource_to_model(resource, res_type, res_config)

            return resource

        except Exception as e:
            logger.error(
                f"Error mapping resource {res_type}.{res_name}: {str(e)}"
            )
            logger.debug(f"Resource config: {res_config}", exc_info=True)
            return None

    def _get_common_fields(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Extract common fields that might be present in any resource config.
        
        Args:
            config: Resource configuration dictionary
            
        Returns:
            Dictionary with common fields
        """
        common_fields = {}
        
        # Common fields that might be present
        common_field_names = [
            "labels", "tags", "metadata", "annotations",
            "project", "project_id", "depends_on", "lifecycle"
        ]
        
        for field_name in common_field_names:
            if field_name in config:
                common_fields[field_name] = config[field_name]
        
        # Handle timeouts if present
        if "timeouts" in config:
            common_fields["timeouts"] = config["timeouts"]
        
        return common_fields

    def _create_storage_resource(
        self,
        res_type: str,
        res_name: str,
        res_config: Dict[str, Any],
        base_config: Dict[str, Any]
    ) -> Optional[Resource]:
        """Create a storage resource from the given configuration.
        
        Args:
            res_type: Terraform resource type
            res_name: Resource name
            res_config: Resource configuration dictionary
            base_config: Base configuration dictionary with common settings
            
        Returns:
            The created storage resource, or None if creation fails
        """
        try:
            if res_type == "google_storage_bucket":
                # Handle Cloud Storage bucket
                location = self._get_dict_value(res_config, "location", "US")
                storage_class_str = self._get_dict_value(
                    res_config, 
                    "storage_class", 
                    "STANDARD"
                ).upper()
                
                # Convert string to StorageClass enum
                try:
                    storage_class = GCPStorageClass(storage_class_str)
                except ValueError:
                    logger.warning(f"Invalid storage class '{storage_class_str}', using STANDARD")
                    storage_class = GCPStorageClass.STANDARD
                
                # Estimate size if not provided
                size_gb = self._get_dict_value(
                    res_config, 
                    "size_gb", 
                    default=100  # Default size in GB
                )
                
                # Convert location to region if needed
                region = location if location.startswith(('us-', 'europe-', 'asia-')) else base_config.get("region", "us-central1")
                
                return self._init_resource(
                    CloudStorage,
                    base_config,
                    capacity_gb=size_gb,
                    region=region,
                    storage_class=storage_class,
                    # Add other relevant parameters
                )
                
            elif res_type == "google_sql_database_instance":
                # Handle Cloud SQL instance
                # --- NEW (V2): Robust Settings Block Normalization ---
                # Handles cases where the HCL parser returns 'settings' as either
                # a direct dictionary or a list containing one dictionary.

                power_params = {
                    "sql_base_w": self.tuned_params.get("sql_base_w"),
                    "sql_vcpu_w": self.tuned_params.get("sql_vcpu_w"),
                    "sql_mem_gb_w": self.tuned_params.get("sql_mem_gb_w"),
                }
                power_params = {k: v for k, v in power_params.items() if v is not None}

                # 1. Safely get the settings block, defaulting to an empty list.
                settings_block = self._get_dict_value(res_config, "settings", [])

                # 2. Normalize the settings block to always be a dictionary.
                settings_dict = {}
                if isinstance(settings_block, list) and settings_block:
                    settings_dict = settings_block[0]  # Take the first item if it's a list
                elif isinstance(settings_block, dict):
                    settings_dict = settings_block   # Use it directly if it's a dict

                # 3. Now, safely extract values from the normalized dictionary.
                tier = self._get_dict_value(settings_dict, "tier", "db-standard-1") # Use a better default
                disk_size_gb = self._get_dict_value(settings_dict, "disk_size", 10)
                
                # Extract availability_type for HA detection in storage.py
                ip_config = self._get_dict_value(settings_dict, "ip_configuration", [{}])[0]
                availability_type = self._get_dict_value(settings_dict, "availability_type", "ZONAL")
                high_availability = availability_type == "REGIONAL"

                # 4. Pass the correct tier string to the CloudSQL object constructor.
                return self._init_resource(
                    CloudSQL,
                    base_config,
                    capacity_gb=disk_size_gb,
                    instance_type=tier, # This will now be "db-custom-4-8192"
                    high_availability=high_availability,
                    power_profile_params=power_params
                )
                
            elif res_type == "google_bigquery_dataset":
                # Handle BigQuery dataset - use GenericGCPComponent since BigQuery class doesn't exist
                location = self._get_dict_value(
                    res_config, 
                    "location", 
                    "US"
                )
                
                # Estimate default size (BigQuery is serverless, so this is just a placeholder)
                size_gb = self._get_dict_value(
                    res_config, 
                    "size_gb", 
                    default=10
                )
                
                return self._init_resource(
                    GenericGCPComponent,
                    base_config,
                    location=location,
                    size_gb=size_gb,
                    # Add other relevant parameters
                )
                
            elif res_type == "google_firestore_database":
                # Handle Firestore database
                location = self._get_dict_value(
                    res_config, 
                    "location", 
                    "nam5"  # Default Firestore location
                )
                
                # Estimate default size (Firestore is serverless)
                size_gb = self._get_dict_value(
                    res_config, 
                    "size_gb", 
                    default=1
                )
                
                return self._init_resource(
                    Firestore,
                    base_config,
                    location=location,
                    size_gb=size_gb,
                    # Add other relevant parameters
                )
                
        except Exception as e:
            logger.error(f"Error creating storage resource {res_type}.{res_name}: {e}")
            return None

    def _create_network_resource(
        self,
        res_type: str,
        res_name: str,
        res_config: Dict[str, Any],
        base_config: Dict[str, Any]
    ) -> Optional[Resource]:
        """Create a network resource from the given configuration.
        
        Args:
            res_type: Terraform resource type
            res_name: Resource name
            res_config: Resource configuration dictionary
            base_config: Base configuration dictionary with common settings
            
        Returns:
            The created network resource, or None if creation fails
        """
        try:
            if res_type == "google_compute_network":
                # Handle VPC Network
                auto_create_subnetworks = self._get_dict_value(
                    res_config,
                    "auto_create_subnetworks",
                    False
                )
                
                return self._init_resource(
                    VPC,
                    base_config,
                    auto_create_subnetworks=auto_create_subnetworks,
                    # Add other relevant parameters
                )
                
            elif res_type == "google_compute_subnetwork":
                # Handle VPC Subnetwork
                ip_cidr_range = self._get_dict_value(
                    res_config,
                    "ip_cidr_range",
                    "10.0.0.0/24"  # Default CIDR range
                )
                
                region = self._get_dict_value(
                    res_config,
                    "region",
                    "us-central1"  # Default region
                )
                
                private_ip_google_access = self._get_dict_value(
                    res_config,
                    "private_ip_google_access",
                    False
                )
                
                return self._init_resource(
                    NetworkResource,
                    base_config,
                    ip_cidr_range=ip_cidr_range,
                    region=region,
                    private_ip_google_access=private_ip_google_access,
                    # Add other relevant parameters
                )
                
            elif res_type == "google_compute_router":
                # Handle Cloud Router
                region = self._get_dict_value(
                    res_config,
                    "region",
                    "us-central1"  # Default region
                )
                
                return self._init_resource(
                    NetworkResource,
                    base_config,
                    region=region,
                    # Add other relevant parameters
                )
                
            elif res_type == "google_compute_router_nat":
                # Handle Cloud NAT
                nat_ip_allocate_option = self._get_dict_value(
                    res_config,
                    "nat_ip_allocate_option",
                    "AUTO_ONLY"
                )
                
                source_subnetwork_ip_ranges_to_nat = self._get_dict_value(
                    res_config,
                    "source_subnetwork_ip_ranges_to_nat",
                    "ALL_SUBNETWORKS_ALL_IP_RANGES"
                )
                
                return self._init_resource(
                    NetworkResource,
                    base_config,
                    nat_ip_allocate_option=nat_ip_allocate_option,
                    source_subnetwork_ip_ranges_to_nat=source_subnetwork_ip_ranges_to_nat,
                    # Add other relevant parameters
                )
                
            elif res_type == "google_compute_firewall":
                # Handle VPC Firewall Rule
                network = self._get_dict_value(
                    res_config,
                    "network",
                    "default"
                )
                
                source_ranges = self._get_dict_value(
                    res_config,
                    "source_ranges",
                    ["0.0.0.0/0"]  # Default to open to all
                )
                
                allows = self._get_dict_value(
                    res_config,
                    "allow",
                    [{"protocol": "tcp", "ports": ["0-65535"]}]  # Default allow all TCP
                )
                
                return self._init_resource(
                    NetworkResource,
                    base_config,
                    network=network,
                    source_ranges=source_ranges,
                    allows=allows,
                    # Add other relevant parameters
                )
                
            elif res_type == "google_vpc_access_connector":
                # Handle VPC Access Connector
                ip_cidr_range = self._get_dict_value(
                    res_config,
                    "ip_cidr_range",
                    "10.8.0.0/28"  # Default CIDR range for VPC connector
                )
                
                min_throughput = self._get_dict_value(
                    res_config,
                    "min_throughput",
                    200  # Default minimum throughput in Mbps
                )
                
                max_throughput = self._get_dict_value(
                    res_config,
                    "max_throughput",
                    300  # Default maximum throughput in Mbps
                )
                
                return self._init_resource(
                    NetworkResource,
                    base_config,
                    ip_cidr_range=ip_cidr_range,
                    min_throughput=min_throughput,
                    max_throughput=max_throughput,
                    # Add other relevant parameters
                )
                
            # Note: Static address resources (e.g., google_compute_global_address) are filtered out
            # by centralized filters as non-operational and are intentionally not modeled here.
                
        except Exception as e:
            logger.error(f"Error creating network resource {res_type}.{res_name}: {e}")
            return None

    def _create_security_resource(
        self,
        res_type: str,
        res_name: str,
        res_config: Dict[str, Any],
        base_config: Dict[str, Any]
    ) -> Optional[Resource]:
        """Create a security resource from the given configuration.
        
        Args:
            res_type: Terraform resource type
            res_name: Resource name
            res_config: Resource configuration dictionary
            base_config: Base configuration dictionary with common settings
            
        Returns:
            The created security resource, or None if creation fails
        """
        try:
            if res_type == "google_kms_key_ring":
                # Handle KMS Key Ring
                location = self._get_dict_value(
                    res_config,
                    "location",
                    "global"
                )
                
                return self._init_resource(
                    CloudKMS,
                    base_config,
                    location=location,
                    # Add other relevant parameters
                )
                
            elif res_type == "google_kms_crypto_key":
                # Handle KMS Crypto Key
                key_ring = self._get_dict_value(
                    res_config,
                    "key_ring",
                    None
                )
                
                purpose = self._get_dict_value(
                    res_config,
                    "purpose",
                    "ENCRYPT_DECRYPT"
                )
                
                rotation_period = self._get_dict_value(
                    res_config,
                    "rotation_period",
                    "7776000s"  # 90 days in seconds
                )
                
                return self._init_resource(
                    CloudKMS,
                    base_config,
                    key_ring=key_ring,
                    purpose=purpose,
                    rotation_period=rotation_period,
                    # Add other relevant parameters
                )
                
            elif res_type == "google_secret_manager_secret":
                # Handle Secret Manager Secret
                replication = self._get_dict_value(
                    res_config,
                    "replication",
                    {"automatic": True}
                )
                
                return self._init_resource(
                    SecretManager,
                    base_config,
                    replication=replication,
                    # Add other relevant parameters
                )
                
            elif res_type == "google_cloud_asset_organization_feed":
                # Handle Cloud Asset Feed
                feed_id = self._get_dict_value(
                    res_config,
                    "feed_id",
                    f"feed-{res_name}"
                )
                
                asset_types = self._get_dict_value(
                    res_config,
                    "asset_types",
                    ["compute.googleapis.com/*"]
                )
                
                return self._init_resource(
                    BaseSecurityResource,
                    base_config,
                    feed_id=feed_id,
                    asset_types=asset_types,
                    # Add other relevant parameters
                )
                
            elif res_type == "google_organization_iam_audit_config":
                # Handle Organization IAM Audit Config
                service = self._get_dict_value(
                    res_config,
                    "service",
                    "allServices"
                )
                
                audit_log_configs = self._get_dict_value(
                    res_config,
                    "audit_log_config",
                    [{"log_type": "ADMIN_READ"}, {"log_type": "DATA_WRITE"}, {"log_type": "DATA_READ"}]
                )
                
                return self._init_resource(
                    BaseSecurityResource,
                    base_config,
                    service=service,
                    audit_log_configs=audit_log_configs,
                    # Add other relevant parameters
                )
                
        except Exception as e:
            logger.error(f"Error creating security resource {res_type}.{res_name}: {e}")
            return None

class NetworkTopologyBuilder:
    """Helper class for building network topology in a Petri net model.
    
    This class encapsulates the logic for creating a layered, sequential DAG-style
    Petri net that routes workload tokens through each resource category before 
    reaching the sink.
    """
    
    def __init__(self, petri_net: PetriNet):
        """Initialize the NetworkTopologyBuilder with a Petri net.
        
        Args:
            petri_net: The Petri net to build topology in
        """
        self.petri_net = petri_net
        self.logger = logger
    
    def ensure_place(self, pid: str, name: str, capacity: int | None = None) -> None:
        """Ensure a place exists in the Petri net.
        
        Args:
            pid: The place ID
            name: The place name
            capacity: Optional capacity for the place
        """
        if pid not in self.petri_net.places:
            self.petri_net.add_place(
                Place(id=pid, name=name, capacity=capacity)
            )
    
    def ensure_transition(self, tid: str, name: str, delay: float, action, resource_id: str = None) -> None:
        """Ensure a transition exists in the Petri net.
        
        Args:
            tid: The transition ID
            name: The transition name
            delay: The transition delay
            action: The action to perform when the transition fires
            resource_id: Optional ID of the resource this transition processes
        """
        if tid not in self.petri_net.transitions:
            self.petri_net.add_transition(
                Transition(id=tid, name=name, delay=delay, action=action, resource_id=resource_id)
            )
    
    def ensure_arc(self, aid: str, place_id: str, transition_id: str, direction: str) -> None:
        """Ensure an arc exists in the Petri net.
        
        Args:
            aid: The arc ID
            place_id: The place ID
            transition_id: The transition ID
            direction: The arc direction ("input" or "output")
        """
        if aid not in self.petri_net.arcs:
            self.petri_net.add_arc(
                Arc(
                    id=aid,
                    place_id=place_id,
                    transition_id=transition_id,
                    direction=direction,
                )
            )
    
    def add_process(
        self,
        resource: Resource,
        from_place: str,
        to_place: str,
        slot_place: str,
        default_delay: float,
    ) -> None:
        """Add a process transition to the Petri net.
        
        Creates Process_<resource.name> transition with four arcs:
        slot_place  
        from_place   Transition_Process_X  to_place
                     slot_place  (return the slot token)
                    
        Args:
            resource: The resource to create a process for
            from_place: Input place ID
            to_place: Output place ID
            slot_place: Slot place ID for resource state
            default_delay: Default processing delay
        """
        t_id = f"Transition_Process_{resource.name}"
        delay = getattr(
            resource, "get_processing_delay", lambda: default_delay
        )()

        def process_action(
            tokens,
            _src=from_place,
            _dst=to_place,
            _slot=slot_place,
            _res_name=resource.name,
        ):
            out: dict[str, list[Token]] = {
                _dst: tokens.get(_src, []).copy()
            }
            out[_slot] = tokens.get(_slot, []).copy()
            return out

        self.ensure_transition(
            t_id,
            name=f"Process_{resource.name}",
            delay=delay,
            action=process_action,
            resource_id=resource.id,
        )
        self.ensure_arc(
            f"Arc_In_{slot_place}_To_{t_id}", slot_place, t_id, "input"
        )
        self.ensure_arc(
            f"Arc_In_{from_place}_To_{t_id}", from_place, t_id, "input"
        )
        self.ensure_arc(
            f"Arc_Out_{t_id}_To_{to_place}", to_place, t_id, "output"
        )
        self.ensure_arc(
            f"Arc_Out_{t_id}_To_{slot_place}", slot_place, t_id, "output"
        )
    
    def build_layer(
        self,
        resources: List[Resource],
        layer_prefix: str,
        input_queue: str,
        output_queue: str,
        default_delay: float,
    ) -> None:
        """Build a layer of resources in the Petri net.
        
        Args:
            resources: List of resources in this layer
            layer_prefix: Prefix for layer-specific IDs
            input_queue: Input queue place ID
            output_queue: Output queue place ID
            default_delay: Default processing delay
        """
        if not resources:
            return
            
        self.logger.info(
            f"Building {layer_prefix.capitalize()} Layer with {len(resources)} resources from '{input_queue}' to '{output_queue}'."
        )
        
        self.ensure_place(
            output_queue, f"{layer_prefix.capitalize()} Processed Queue"
        )
        
        for res in resources:
            place_in = f"in_{layer_prefix}_{res.name}"
            self.ensure_place(place_in, f"In_{res.name}")
            router_tid = f"Router_To_{res.name}"
            
            self.ensure_transition(
                router_tid,
                f"Route_To_{res.name}",
                0.0,
                lambda t, _i=input_queue, _o=place_in: {
                    _o: t.get(_i, []).copy()
                },
            )
            
            self.ensure_arc(
                f"Arc_In_{input_queue}_To_{router_tid}",
                input_queue,
                router_tid,
                "input",
            )
            
            self.ensure_arc(
                f"Arc_Out_{place_in}_From_{router_tid}",
                place_in,
                router_tid,
                "output",
            )
            
            slot_place = f"ResourceState_{res.name}"
            self.add_process(
                res, place_in, output_queue, slot_place, default_delay
            )

    def build(self, resources: List[Resource]) -> None:
        """Build the complete network topology.
        
        Constructs a layered, sequential DAG with the following layers:
        1. Source  [Load Balancers]  [Firewalls]  [Routers]  [Compute]  [Storage]  [Databases]  Sink
        
        Args:
            resources: List of all resources to include in the topology
            
        Raises:
            RuntimeError: If there's an error building the topology
        """
        try:
            self.logger.info("Building a sequential DAG network topology...")
            
            # Create source and sink places
            source_id = PetriNet.SOURCE_PLACE_ID
            sink_id = PetriNet.SINK_PLACE_ID
            self.ensure_place(source_id, "Global Source")
            self.ensure_place(sink_id, "Global Sink")
            
            # Categorize resources
            self.logger.debug("Categorizing resources...")
            
            # Database resources (CloudSQL, Bigtable, Firestore)
            database_resources = [
                r for r in resources
                if isinstance(r, (CloudSQL, Bigtable, Firestore))
            ]
            database_ids = {r.id for r in database_resources}
            
            # Network resources (excluding those that are also databases)
            network_resources = [
                r for r in resources
                if isinstance(r, BaseNetworkResource) and r.id not in database_ids
            ]
            
            # Security resources (excluding those that are also databases)
            security_resources = [
                r for r in resources
                if isinstance(r, BaseSecurityResource) and r.id not in database_ids
            ]
            
            # Compute resources (excluding those that are also databases)
            compute_resources = [
                r for r in resources
                if isinstance(r, ComputeResource) and r.id not in database_ids
            ]
            
            # Storage resources (excluding those that are also databases)
            storage_resources = [
                r for r in resources
                if isinstance(r, BaseStorageResource) and r.id not in database_ids
            ]
            
            # Log resource counts for debugging
            self.logger.debug(f"Categorized resources: {len(database_resources)} databases, "
                           f"{len(network_resources)} network, {len(security_resources)} security, "
                           f"{len(compute_resources)} compute, {len(storage_resources)} storage")
            
            # --- Build the layered topology ---
            
            # Layer 1: Load Balancers (if any)
            load_balancers = [r for r in network_resources 
                            if isinstance(r, LoadBalancer)]
            if load_balancers:
                self.build_layer(
                    resources=load_balancers,
                    layer_prefix="load_balancer",
                    input_queue=source_id,
                    output_queue="lb_processed",
                    default_delay=0.1  # Low delay for load balancers
                )
                prev_layer = "lb_processed"
            else:
                prev_layer = source_id
            
            # Layer 2: Firewalls
            if security_resources:
                self.build_layer(
                    resources=security_resources,
                    layer_prefix="firewall",
                    input_queue=prev_layer,
                    output_queue="firewall_processed",
                    default_delay=0.05  # Very low delay for firewalls
                )
                prev_layer = "firewall_processed"
            
            # Layer 3: Network Routers (generic network resources that aren't load balancers)
            routers = [r for r in network_resources 
                      if r not in load_balancers and 
                      getattr(r, 'name', '').lower().find('router') != -1]
            if routers:
                self.build_layer(
                    resources=routers,
                    layer_prefix="router",
                    input_queue=prev_layer,
                    output_queue="router_processed",
                    default_delay=0.1  # Low delay for routers
                )
                prev_layer = "router_processed"
            
            # Layer 4: Compute Resources
            if compute_resources:
                self.build_layer(
                    resources=compute_resources,
                    layer_prefix="compute",
                    input_queue=prev_layer,
                    output_queue="compute_processed",
                    # Default to a shorter per-request processing delay (50ms) so
                    # capacity units map to a more realistic throughput baseline.
                    default_delay=0.05
                )
                prev_layer = "compute_processed"
            
            # Layer 5: Storage Resources
            if storage_resources:
                self.build_layer(
                    resources=storage_resources,
                    layer_prefix="storage",
                    input_queue=prev_layer,
                    output_queue="storage_processed",
                    default_delay=0.5  # Medium delay for storage
                )
                prev_layer = "storage_processed"
            
            # Layer 6: Database Resources
            if database_resources:
                self.build_layer(
                    resources=database_resources,
                    layer_prefix="database",
                    input_queue=prev_layer,
                    output_queue="database_processed",
                    default_delay=2.0  # Highest delay for databases
                )
                prev_layer = "database_processed"
            
            # Connect the last layer to the sink
            if prev_layer != sink_id:
                # Add a transition from the last layer to the sink
                final_transition = "Final_To_Sink"
                self.ensure_transition(
                    final_transition,
                    "Final_To_Sink",
                    0.0,  # No delay for final transition
                    lambda t, _i=prev_layer, _o=sink_id: {
                        _o: t.get(_i, []).copy()
                    },
                )
                self.ensure_arc(
                    f"Arc_In_{prev_layer}_To_{final_transition}",
                    prev_layer,
                    final_transition,
                    "input",
                )
                self.ensure_arc(
                    f"Arc_Out_{final_transition}_To_{sink_id}",
                    sink_id,
                    final_transition,
                    "output",
                )
            
            self.logger.info("Successfully built network topology")
            
        except Exception as e:
            error_msg = f"Failed to build network topology: {str(e)}"
            self.logger.error(error_msg)
            self.logger.debug("Network topology build error:", exc_info=True)
            raise RuntimeError(error_msg) from e
