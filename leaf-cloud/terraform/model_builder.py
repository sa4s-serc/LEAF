from __future__ import annotations
import os
import json
import logging
import math
import yaml
from enum import Enum
from typing import (
    Optional, Dict, Any, List, Set, Callable, Type, Union,
    TypeVar, Tuple, cast, Iterable
)
from collections import defaultdict
from dataclasses import dataclass


import hcl2
from typing_extensions import TypeAlias

# Import from local modules instead of leaf_cloud package
from ..core.resource import Resource, ResourceType, ResourcePool
from ..core.petri_net import (
    PetriNet, Place, Transition, Arc, Token,
    TokenColor
)
from ..core.workload import (
    Workload, SteadyWorkload,
    CustomWorkload, WorkloadMix
)

# GCP specific imports
from ..gcp.compute import (
    ComputeResource as BaseComputeResource,
    ComputeEngineVM, GKECluster, GKENode, CloudFunction, CloudRun, AppEngine,
)
from ..gcp.storage import (
    Storage as BaseStorageResource,
    CloudStorage, PersistentDisk, CloudSQL, Bigtable, Firestore,
    StorageClass as GCPStorageClass, DiskType, DatabaseTier
)
from ..gcp.network import (
    NetworkResource as BaseNetworkResource,
    VPC, NetworkResource, LoadBalancer, DNS, VPN, Interconnect, CDN, APIGateway,
    NetworkResourceType, LoadBalancerType
)
from ..gcp.security import (
    SecurityResource as BaseSecurityResource,
    SecurityResource, SecurityResourceType, IAM, CloudKMS, SecretManager,
    SecurityCommandCenter, CloudArmor
)
from ..gcp.generic_component import (
    GenericGCPComponent, GCPServiceCategory
)
from ..models.constraints import (
    Constraint, ConstraintType, ConstraintViolation,
    ConstraintSeverity
)
from ..utils.helpers import normalize_block

# Type aliases 
ResourceId: TypeAlias = str
PlaceId: TypeAlias = str
TransitionId: TypeAlias = str

logger = logging.getLogger(__name__)
# logging.basicConfig(level=logging.INFO)



def _is_resource_of_category(resource_obj: Resource, category_class: Type[Resource]) -> bool:
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
        "SecurityResource": SecurityResource,
        "GenericGCPComponent": GenericGCPComponent
    }

    def __init__(self, terraform_path: str, env_config_path: Optional[str] = None, workload_rate: float = 100.0):
        """
        Initialize the ModelBuilder with a Terraform directory or plan JSON file.
        
        Args:
            terraform_path: Path to Terraform directory or plan JSON file
            env_config_path: Optional path to environment configuration file
        """
        self.workload_rate = workload_rate
        self.terraform_path = terraform_path
        self.env_config = self._load_env_config(env_config_path or "env.yaml")
        # Initialize required attributes with proper types
        self.tf_resources: Dict[str, Dict[str, Any]] = {}
        self.resource_mapping: Dict[str, Resource] = {}
        self.resource_pools: Dict[ResourceType, ResourcePool] = {rt: ResourcePool(rt.name, rt) for rt in ResourceType.__members__.values()}
        self.petri_net: PetriNet = PetriNet(name="LeafCloudModel")
        
        logger.info(f"ModelBuilder initialized with Terraform path: {terraform_path}")

    def _load_env_config(self, config_path: str) -> Dict[str, Any]:
        """Load environment configuration from YAML file."""
        default_config = {
            'region': 'us-central1',
            'project': 'default-project',
            'zone': 'us-central1-a'
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = yaml.safe_load(f)
                if config is None:
                    return default_config
                return config
            except Exception as e:
                logger.error(f"Failed to load config from {config_path}: {str(e)}")
                return default_config
        else:
            logger.warning(f"Environment configuration file {config_path} not found. Using defaults.")
            return default_config
            
    def _get_dict_value(self, data: Dict[str, Any], key_path: str, default: Any = None) -> Any:
        """Safely get a nested value from a dictionary using dot notation."""
        if not data or not key_path:
            return default
        keys = key_path.split('.')
        result = data
        for key in keys:
            if isinstance(result, dict) and key in result:
                result = result[key]
            elif isinstance(result, list) and key.isdigit() and int(key) < len(result):
                # Handle list indexing if key is a digit and within list bounds
                result = result[int(key)]
            else:
                return default
        return result

    def _safe_cast(self, value: Any, target_type: Callable[[Any], Any], default: Any = None) -> Any:
        """Safely cast a value to a target type."""
        if value is None:
            return default
        try:
            return target_type(value)
        except (ValueError, TypeError, AttributeError):
            logger.warning(f"Failed to cast '{value}' to {target_type.__name__ if hasattr(target_type, '__name__') else str(target_type)}. Returning default: {default}")
            return default

    def _parse_memory_limit(self, memory_str: Optional[str]) -> int: # Returns MB
        """Parse a memory limit string (e.g., '1Gi', '512Mi') into megabytes (MB)."""
        if not memory_str:
            return 0
        
        val_str = str(memory_str).lower().strip()
        num_part = ""
        unit_part = ""

        # Separate numeric part from unit part
        for char_idx, char_val in enumerate(val_str):
            if char_val.isdigit() or char_val == '.':
                num_part += char_val
            else:
                unit_part = val_str[char_idx:]
                break
        
        if not num_part:
            logger.warning(f"No numeric part found in memory string '{memory_str}'.")
            return 0

        try:
            num = float(num_part)
        except ValueError:
            logger.warning(f"Invalid numeric value '{num_part}' in memory string '{memory_str}'.")
            return 0

        unit_part = unit_part.lower().replace("i", "").replace("b", "").strip()

        if unit_part == 'g' or unit_part == 'gb': # Gigabytes or Gibibytes
            return int(num * 1024)
        elif unit_part == 'm' or unit_part == 'mb': # Megabytes or Mebibytes
            return int(num)
        elif unit_part == 'k' or unit_part == 'kb': # Kilobytes or Kibibytes
            return int(num / 1024)
        elif not unit_part: # No unit
             logger.warning(f"Memory string '{memory_str}' has no units, assuming MB.")
             return int(num) 
        else: # Unrecognized unit
            logger.warning(f"Unrecognized memory unit '{unit_part}' in '{memory_str}'. Could not parse.")
            return 0
            
    def parse_terraform_files(self) -> Dict[str, Any]:
        """
        Parse Terraform files or plan JSON based on the provided path.
        
        Returns:
            Dictionary containing parsed Terraform resources
        """
        # Determine if the path is a directory or a JSON file
        if os.path.isdir(self.terraform_path):
            return self._parse_terraform_directory()
        elif os.path.isfile(self.terraform_path) and self.terraform_path.endswith('.json'):
            return self._parse_terraform_plan_json()
        else:
            raise ValueError(f"Invalid Terraform path: {self.terraform_path}. Must be a directory or a .json file.")
    
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
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for file in files:
                if file.endswith('.tf'):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, 'r') as tf_file:
                            parsed = hcl2.load(tf_file)
                            
                            # Normalize blocks that might be returned as lists
                            if 'resource' in parsed:
                                parsed['resource'] = normalize_block(parsed['resource'])
                            if 'variable' in parsed:
                                parsed['variable'] = normalize_block(parsed['variable'])
                            if 'output' in parsed:
                                parsed['output'] = normalize_block(parsed['output'])
                            
                            # Process resources if available
                            if 'resource' in parsed:
                                for res_type, resources in parsed['resource'].items():
                                    for res_name, res_config in resources.items():
                                        res_id = f"{res_type}.{res_name}"
                                        all_resources[res_id] = {
                                            'type': res_type,
                                            'name': res_name,
                                            'config': res_config
                                        }
                            
                            logger.info(f"Successfully parsed Terraform file: {file_path}")
                    except Exception as e:
                        logger.error(f"Error parsing Terraform file {file_path}: {e}")
        
        self.tf_resources = all_resources
        logger.info(f"Parsed {len(all_resources)} resources from Terraform files")
        return all_resources
    
    def _parse_terraform_plan_json(self) -> Dict[str, Any]:
        """
        Parse a Terraform plan JSON file.
        
        Returns:
            Dictionary containing parsed Terraform resources
        """
        all_resources = {}
        
        try:
            with open(self.terraform_path, 'r') as json_file:
                plan_data = json.load(json_file)
                
                # Extract resources from the plan
                if 'planned_values' in plan_data and 'root_module' in plan_data['planned_values']:
                    root_module = plan_data['planned_values']['root_module']
                    
                    # Process resources in the root module
                    if 'resources' in root_module:
                        for resource in root_module['resources']:
                            res_type = resource.get('type', '')
                            res_name = resource.get('name', '')
                            res_id = f"{res_type}.{res_name}"
                            
                            # Extract values as the config
                            res_config = resource.get('values', {})
                            
                            all_resources[res_id] = {
                                'type': res_type,
                                'name': res_name,
                                'config': res_config
                            }
                    
                    # Process resources in child modules recursively
                    if 'child_modules' in root_module:
                        self._extract_module_resources(root_module['child_modules'], all_resources)
                
                logger.info(f"Successfully parsed Terraform plan JSON: {self.terraform_path}")
        except Exception as e:
            logger.error(f"Error parsing Terraform plan JSON {self.terraform_path}: {e}")
        
        self.tf_resources = all_resources
        logger.info(f"Parsed {len(all_resources)} resources from Terraform plan JSON")
        return all_resources
    
    def _extract_module_resources(self, modules: List[Dict[str, Any]], all_resources: Dict[str, Any]) -> None:
        """
        Extract resources from child modules recursively.
        
        Args:
            modules: List of module configurations
            all_resources: Dictionary to update with extracted resources
        """
        for module in modules:
            if 'resources' in module:
                for resource in module['resources']:
                    res_type = resource.get('type', '')
                    res_name = resource.get('name', '')
                    res_id = f"{res_type}.{res_name}"
                    
                    # Extract values as the config
                    res_config = resource.get('values', {})
                    
                    # Add module address to the resource ID for uniqueness
                    if 'address' in module:
                        module_address = module['address']
                        res_id = f"{module_address}.{res_id}"
                    
                    all_resources[res_id] = {
                        'type': res_type,
                        'name': res_name,
                        'config': res_config
                    }
            
            # Process nested child modules recursively
            if 'child_modules' in module:
                self._extract_module_resources(module['child_modules'], all_resources)

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
                logger.error("No Terraform resources found to build model from")
                raise ValueError("No Terraform resources found to build model from")
            
            # Create source and sink places if they don't exist
            if PetriNet.SOURCE_PLACE_ID not in self.petri_net.places:
                source_place = Place(
                    id=PetriNet.SOURCE_PLACE_ID,
                    name="Source"
                )
                self.petri_net.add_place(source_place)
                
            if PetriNet.SINK_PLACE_ID not in self.petri_net.places:
                sink_place = Place(
                    id=PetriNet.SINK_PLACE_ID,
                    name="Sink"
                )
                self.petri_net.add_place(sink_place)

            # Process Terraform resources if not already processed
            if not self.resource_mapping:
                self._process_terraform_resources()
            
            # Build network topology
            self._build_network_topology()

            # Add workload generation if rate is provided
            if workload_rate is not None and workload_rate > 0:
                self._add_workload_generation(workload_rate)
            
            if not self.petri_net:
                logger.error("Failed to build Petri net model")
                raise ValueError("Failed to build Petri net model")
                
            logger.info(f"Petri net model built with {len(self.petri_net.places)} places and {len(self.petri_net.transitions)} transitions.")
            return self.petri_net
            
        except Exception as e:
            logger.error(f"Error building Petri net model: {str(e)}")
            # Re-raise the exception with more context
            raise ValueError(f"Failed to build Petri net model: {str(e)}") from e

    def _create_token_transfer_action(
            self,
            from_req_id: str,
            to_req_id: str,
            state_place_id: str
        ) -> Callable[[Dict[str, List[Token]]], Dict[str, List[Token]]]:
        """
        Consume 1 token from `from_req_id`  (the “In_<resource>” queue)
                    together with 1 token from `state_place_id`
        After the delay, return the “state token” to state_place_id
                    and pass the request token into `to_req_id`.
        """
        def action(tokens: Dict[str, List[Token]]) -> Dict[str, List[Token]]:
            output: Dict[str, List[Token]] = {}

            # 1. Move request‐tokens onward:
            if from_req_id in tokens and tokens[from_req_id]:
                output[to_req_id] = tokens[from_req_id]  # forward every request‐token

            # 2. Return exactly one slot token to `state_place_id`
            #    (so the resource can handle another request next time):
            output.setdefault(state_place_id, []).append(Token())

            return output

        return action

        
    def _get_compute_capacity(self, machine_type: str) -> int:
        """Get compute capacity based on machine type."""
        # Default capacity if not found
        default_capacity = 100
        
        # Extract capacity from machine type string
        try:
            # Parse format like "n1-standard-2", "e2-medium", etc.
            if "-" in machine_type:
                parts = machine_type.split("-")
                if len(parts) >= 3 and parts[-1].isdigit():
                    return int(parts[-1]) * default_capacity
                elif parts[-1] == "medium":
                    return default_capacity
                elif parts[-1] == "small":
                    return default_capacity // 2
            return default_capacity
        except:
            return default_capacity

    def _init_resource(self, res_class: Type[Resource], base_config: Dict[str, Any], **kwargs) -> Optional[Resource]:
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
            param_names = set(sig.parameters.keys()) - {'self'}
            for key in param_names:
                if key in init_args:
                    valid_args[key] = init_args[key]
            return res_class(**valid_args)
        except Exception as e:
            logger.error(f"Failed to initialize resource {res_class.__name__} with args {kwargs}: {str(e)}")
            return None

    def _determine_resource_type(self, res_type: str) -> Optional[str]:
        """Map Terraform resource type to LEAF-Cloud resource type."""
        resource_type_map = {
            'google_compute_instance': 'ComputeEngineVM',
            'google_container_cluster': 'GKECluster',
            'google_container_node_pool': 'GKENode',
            'google_cloudfunctions_function': 'CloudFunction',
            'google_cloud_run_service': 'CloudRun',
            'google_app_engine_application': 'AppEngine',
            'google_storage_bucket': 'CloudStorage',
            'google_compute_disk': 'PersistentDisk',
            'google_sql_database_instance': 'CloudSQL',
            'google_bigtable_instance': 'Bigtable',
            'google_firestore_database': 'Firestore',
            'google_compute_network': 'VPC',
            'google_compute_firewall': 'NetworkResource',
            'google_compute_router': 'NetworkResource',
            'google_compute_router_nat': 'NetworkResource',
            'google_compute_forwarding_rule': 'LoadBalancer',
            'google_compute_url_map': 'LoadBalancer',
            'google_dns_managed_zone': 'DNS',
            'google_compute_vpn_tunnel': 'VPN',
            'google_compute_interconnect_attachment': 'Interconnect',
            'google_compute_backend_service': 'LoadBalancer', # Could also be CDN if specific fields are present
            'google_api_gateway_api': 'APIGateway',
            'google_service_account': 'SecurityResource', # IAM related
            'google_project_iam': 'IAMPolicy', # More specific IAM
            'google_kms_key_ring': 'CloudKMS',
            'google_secret_manager_secret': 'SecretManager',
            'google_scc_source': 'SecurityCommandCenter',
            'google_compute_security_policy': 'CloudArmor'
            # More specific mappings can be added here
        }
        
        # Try exact match first
        if res_type in resource_type_map:
            return resource_type_map[res_type]
            
        # Try prefix match
        # Example: 'google_compute_instance_template' should map to 'ComputeEngineVM'
        for tf_type, leaf_type in resource_type_map.items():
            if res_type.startswith(tf_type.split('_')[0]): # Match 'google'
                return leaf_type
                
        return None # Default or unmapped

    def export_tf_resources(self, output_path: str) -> None:
        """
        Export the parsed Terraform resources to a JSON file.

        Args:
            output_path: The path to the file where resources should be saved.
        """
        try:
            with open(output_path, 'w') as f:
                json.dump(self.tf_resources, f, indent=4)
            logger.info(f"Terraform resources exported to {output_path}")
        except IOError as e:
            logger.error(f"Error exporting Terraform resources to {output_path}: {e}")
        except TypeError as e:
            logger.error(f"Error serializing Terraform resources to JSON: {e}")

    def _process_terraform_resources(self) -> None:
        """
        Process Terraform resources and map them to Petri net elements.
        """
        if not self.tf_resources:
            self.parse_terraform_files()
        if not self.tf_resources:
            raise ValueError("No Terraform resources found to build model from")
            
        logger.info(f"Processing {len(self.tf_resources)} Terraform resources")
        
        for res_id, resource in self.tf_resources.items():
            res_type = resource['type']
            res_name = resource['name']
            res_config = resource['config']
            
            leaf_resource = self._map_resource(res_type, res_name, res_config)
            
            if leaf_resource:
                self.resource_mapping[res_id] = leaf_resource
                # Add to appropriate resource pool
                self.resource_pools[leaf_resource.resource_type].add_resource(leaf_resource)
                logger.debug(f"Mapped Terraform resource {res_id} to LEAF-Cloud resource")
            else:
                logger.warning(f"Could not map Terraform resource {res_id} to a LEAF-Cloud resource")

    def _create_token_transfer_action(self, from_place_id: PlaceId, to_place_id: PlaceId) -> Callable[[Dict[PlaceId, List[Token]]], Dict[PlaceId, List[Token]]]:
        def transfer_action(tokens: Dict[PlaceId, List[Token]]) -> Dict[PlaceId, List[Token]]:
            output_tokens: Dict[PlaceId, List[Token]] = {to_place_id: []}
            if from_place_id in tokens:
                # Move all tokens from the 'from' place to the 'to' place.
                # In a typical Petri net, a transition consumes specific tokens based on arc weights.
                # This action implies consuming all available from the specified input place that enabled the transition.
                for token in tokens[from_place_id]:
                    output_tokens[to_place_id].append(token)
            return output_tokens
        return transfer_action

    # leaf-cloud/model_builder.py

    def _build_network_topology(self) -> None:
        """
        Build a layered, sequential DAG-style Petri net that routes workload tokens
        through each resource category before reaching the sink.
        This ensures a single, unbroken path for all tokens.
        """
        logger.info("Building a sequential DAG network topology...")

        # --- Helper functions ---
        def ensure_place(pid: str, name: str, capacity: int | None = None) -> None:
            if pid not in self.petri_net.places:
                self.petri_net.add_place(Place(id=pid, name=name, capacity=capacity))

        def ensure_transition(tid: str, name: str, delay: float, action: Callable) -> None:
            if tid not in self.petri_net.transitions:
                self.petri_net.add_transition(Transition(id=tid, name=name, delay=delay, action=action))

        def ensure_arc(aid: str, place_id: str, transition_id: str, direction: str) -> None:
            if aid not in self.petri_net.arcs:
                self.petri_net.add_arc(Arc(id=aid, place_id=place_id, transition_id=transition_id, direction=direction))

        def add_process(resource, from_place: str, to_place: str, slot_place: str, default_delay: float) -> None:
            """
            Creates a standard processing transition for a resource.
            This function is now enhanced to inject specific logic for CloudRun and CloudSQL.
            """
            t_id = f"Transition_Process_{resource.name}"
            delay = getattr(resource, 'get_processing_delay', lambda: default_delay)()

            def action(tokens, _src=from_place, _dst=to_place, _slot=slot_place):
                # The base action is to move the request token forward and return the slot token.
                out: dict[str, list[Token]] = {
                    _dst: tokens.get(_src, []).copy(),
                    _slot: tokens.get(_slot, []).copy()
                }

                # --- INJECTED LOGIC ---
                # If the resource is a CloudRun instance, trigger its downstream databases.
                if isinstance(resource, CloudRun):
                    # For each request token being processed, fire the DB calls.
                    for token in tokens.get(_src, []):
                        for _ in range(resource.db_calls_per_request):
                            for db in resource.downstream_dbs:
                                # This simulates the web app making a query to the database.
                                db.handle_query(token)

                # If the resource is a CloudSQL instance, its own "processing" is handling a query.
                # This is triggered by the logic above.
                elif isinstance(resource, CloudSQL):
                    for token in tokens.get(_src, []):
                        resource.handle_query(token)
                # --- END INJECTED LOGIC ---

                return out

            ensure_transition(t_id, f"Process_{resource.name}", delay, action)
            ensure_arc(f"Arc_In_{slot_place}_To_{t_id}", slot_place, t_id, "input")
            ensure_arc(f"Arc_In_{from_place}_To_{t_id}", from_place, t_id, "input")
            ensure_arc(f"Arc_Out_{t_id}_To_{to_place}", to_place, t_id, "output")
            ensure_arc(f"Arc_Out_{t_id}_To_{slot_place}", slot_place, t_id, "output")

        def build_layer(resources: list, layer_prefix: str, input_queue: str, output_queue: str, default_delay: float):
            if not resources: return
            logger.info(f"Building {layer_prefix.capitalize()} Layer with {len(resources)} resources from '{input_queue}' to '{output_queue}'.")
            ensure_place(output_queue, f"{layer_prefix.capitalize()} Processed Queue")
            for res in resources:
                place_in = f"in_{layer_prefix}_{res.name}"
                ensure_place(place_in, f"In_{res.name}")
                # Router from main layer input queue to specific resource's input
                ensure_transition(f"Router_To_{res.name}", f"Route_To_{res.name}", 0.0, lambda t, _i=input_queue, _o=place_in: {_o: t.get(_i, []).copy()})
                ensure_arc(f"Arc_In_{input_queue}_To_{res.name}", input_queue, f"Router_To_{res.name}", "input")
                ensure_arc(f"Arc_Out_{place_in}_From_{res.name}", place_in, f"Router_To_{res.name}", "output")
                # The actual processing transition for the resource
                slot_place = f"ResourceState_{res.name}"
                add_process(res, place_in, output_queue, slot_place, default_delay)

        # --- START OF MODEL CONSTRUCTION ---

        # 1. Define core places and categorize all resources
        workload_queue_id = "workload_queue"
        sink_id = PetriNet.SINK_PLACE_ID
        ensure_place(workload_queue_id, "Workload Queue")

        all_resources = list(self.resource_mapping.values())

        # 2. Automatically link CloudRun services to CloudSQL databases
        cloud_runs = [r for r in all_resources if isinstance(r, CloudRun)]
        cloud_sqls = [r for r in all_resources if isinstance(r, CloudSQL)]
        for cr in cloud_runs:
            for db in cloud_sqls:
                # This populates the list that the 'add_process' action will use.
                cr.downstream_dbs.append(db)
                logger.info(f"Linking CloudRun '{cr.name}' -> CloudSQL '{db.name}'")

        # 3. Define the layers for the sequential DAG
        layers_data = [
            ([r for r in all_resources if isinstance(r, BaseNetworkResource)], "network", 0.001),
            ([r for r in all_resources if isinstance(r, BaseSecurityResource)], "security", 0.01),
            ([r for r in all_resources if isinstance(r, BaseComputeResource)], "compute", 0.02), # Compute delay is per-request
            ([r for r in all_resources if isinstance(r, GenericGCPComponent)], "generic", 0.005),
            ([r for r in all_resources if isinstance(r, BaseStorageResource)], "storage", 0.002) # Storage delay is per-query
        ]

        # 4. Create ResourceState places & inject initial capacity tokens for all resources
        for res in all_resources:
            state_pid = f"ResourceState_{res.name}"
            ensure_place(state_pid, f"StateOf_{res.name}")
            if not self.petri_net.places[state_pid].tokens:
                if hasattr(res, 'concurrency'):
                    initial_slots = max(1, getattr(res, 'min_instances', 0) * getattr(res, 'concurrency', 80))
                else:
                    initial_slots = int(max(1, getattr(res, "capacity", 1)))
                logger.debug(f"Injecting {initial_slots} initial slot tokens into {state_pid}")
                for _ in range(initial_slots):
                    self.petri_net.add_token(state_pid, Token())

        # 5. Build the layers sequentially
        last_output_queue = workload_queue_id
        for i, (resources, prefix, delay) in enumerate(layers_data):
            if not resources: continue

            # Determine the output queue for this layer.
            # If it's the last layer with resources, its output must be the sink.
            is_last_layer = all(not res_list for res_list, _, _ in layers_data[i+1:])
            output_queue = sink_id if is_last_layer else f"{prefix}_processed_queue"

            build_layer(resources, prefix, last_output_queue, output_queue, delay)
            last_output_queue = output_queue

        # 6. Final connection: if the last processed queue is not the sink, connect it.
        if last_output_queue != sink_id:
            logger.info(f"Connecting final queue '{last_output_queue}' to Global Sink.")
            passthrough_id = f"Transition_Passthrough_{last_output_queue}_To_Sink"
            ensure_transition(passthrough_id, "Passthrough_To_Sink", 0.0, lambda t, _i=last_output_queue: {sink_id: t.get(_i, []).copy()})
            ensure_arc(f"Arc_In_{last_output_queue}_To_Passthrough", last_output_queue, passthrough_id, "input")
            ensure_arc(f"Arc_Out_Passthrough_To_{sink_id}", sink_id, passthrough_id, "output")

        logger.info("Sequential network topology successfully built.")



    def _add_workload_generation(self, workload_rate: float) -> None:
        """
        Add a robust, self-contained workload generation transition to the Petri net.
        """
        logger.info(f"Adding robust workload generation at rate: {workload_rate} req/s")

        # --- START OF REWRITTEN METHOD ---

        control_place_id = "WorkloadGeneratorControl"
        workload_queue_id = "workload_queue"
        generator_transition_id = "WorkloadGenerator"

        # 1. Ensure the control place exists and has exactly one token
        if control_place_id not in self.petri_net.places:
            self.petri_net.add_place(Place(id=control_place_id, name="Workload Generator Control"))
            self.petri_net.add_token(control_place_id, Token(id="control_token"))
        
        # 2. Define the generator's action
        def workload_generator_action(consumed_tokens: Dict[str, List[Token]]) -> Dict[str, List[Token]]:
            # This action creates a new request and returns the control token to its own place,
            # creating a perfect, self-sustaining loop.
            new_request = Token(color=TokenColor.REQUEST, attributes={"source": "generator"})
            control_token = consumed_tokens.get(control_place_id, [])
            
            return {
                workload_queue_id: [new_request],
                control_place_id: control_token
            }

        # 3. Create the generator transition
        if generator_transition_id not in self.petri_net.transitions:
            self.petri_net.add_transition(Transition(
                id=generator_transition_id,
                name="GenerateWorkload",
                delay=1.0 / workload_rate if workload_rate > 0 else float('inf'),
                action=workload_generator_action
            ))

        # 4. Wire the arcs for the self-looping generator
        # Input from control place
        if f"arc_in_{control_place_id}" not in self.petri_net.arcs:
            self.petri_net.add_arc(Arc(id=f"arc_in_{control_place_id}", place_id=control_place_id, transition_id=generator_transition_id, direction="input"))
        # Output back to control place
        if f"arc_out_{control_place_id}" not in self.petri_net.arcs:
            self.petri_net.add_arc(Arc(id=f"arc_out_{control_place_id}", place_id=control_place_id, transition_id=generator_transition_id, direction="output"))
        # Output to the main workload queue
        if f"arc_out_{workload_queue_id}" not in self.petri_net.arcs:
            self.petri_net.add_arc(Arc(id=f"arc_out_{workload_queue_id}", place_id=workload_queue_id, transition_id=generator_transition_id, direction="output"))
        
        # --- END OF REWRITTEN METHOD ---
        logger.info("Workload generation logic has been built/verified.")

    def _map_resource(self, res_type: str, res_name: str, res_config: Dict[str, Any]) -> Optional[Resource]:
        """Map a Terraform resource to a LEAF-Cloud resource.
        
        Maps specific resource types to corresponding LEAF-Cloud classes.
        Falls back to GenericGCPComponent for unmapped resource types.

        Args:
            res_type: Terraform resource type (e.g., "google_compute_instance")
            res_name: Resource name
            res_config: Resource configuration dictionary

        Returns:
            Optional[Resource]: The created LEAF-Cloud resource or None if mapping fails
        """
        try:
            # Get base configuration
            base_config = {
                'name': res_name, # Use passed res_name
                'region': self._get_dict_value(res_config, 'region', default=self.env_config.get('region', 'us-central1'))
            }
            # Extract region from resource config or use default
            region = res_config.get('region', res_config.get('location', self.env_config.get('region', 'us-central1')))
            zone = res_config.get('zone')
        except Exception as e:
            logger.error(f"Error processing resource {res_name}: {e}") # Use res_name
            return None
        
        # If zone is specified but not region, extract region from zone
        if not region and zone and '-' in zone:
            region = '-'.join(zone.split('-')[:-1])
            
        logger.debug(f"Mapping resource {res_type}.{res_name} in region {region}")
        IGNORED_RESOURCE_TYPES = (
            # IAM and Service Accounts
            "google_project_iam", "google_service_account",
            "_iam_binding", "_iam_member", "_iam_policy",

            # Logical/Config-only SQL resources
            "google_sql_user",

            # Other config-only or control-plane resources
            "google_project_service", "google_service_networking_connection",
            "google_artifact_registry_repository", "null_resource", "google_cloudbuild_trigger",
            "google_clouddeploy_delivery_pipeline", "google_compute_address", "google_compute_global_address",
            "google_compute_router", "google_compute_router_nat", "google_compute_firewall",
            "google_compute_subnetwork", "google_vpc_access_connector",
        )
        if any(ignored in res_type for ignored in IGNORED_RESOURCE_TYPES):
            logger.debug(f"Skipping IAM resource {res_name} – no operational energy")
            return None
        # ====== COMPUTE RESOURCES ======
        if res_type == 'google_compute_instance':
            machine_type = self._get_dict_value(res_config, 'machine_type', 'n1-standard-1')
            preemptible = self._get_dict_value(res_config, 'scheduling.preemptible', False)
            
            # Extract machine specs
            vcpus = 1
            memory_gb = 4.0
            if '-' in machine_type:
                parts = machine_type.split('-')
                if len(parts) >= 3 and parts[-1].isdigit():
                    vcpus = int(parts[-1])
                    memory_gb = vcpus * 4.0  # Estimate: 4GB per vCPU
            
            # Check for GPUs
            gpu_type = None
            gpu_count = 0
            guest_accelerators = self._get_dict_value(res_config, 'guest_accelerator', [])
            if isinstance(guest_accelerators, list):
                for g in guest_accelerators:
                    if g.get('type'):
                        gpu_type = g.get('type')
                        gpu_count = self._safe_cast(g.get('count', 1), int, 1)
                        break
                    
            # Extract boot disk info
            boot_disk_size_gb = 10
            boot_disk_type = 'pd-standard'
            boot_disk_params = self._get_dict_value(res_config, 'boot_disk.initialize_params', {})
            boot_disk_size_gb = self._safe_cast(boot_disk_params.get('size', 10), int, 10)
            boot_disk_type = boot_disk_params.get('type', 'pd-standard')
            
            return ComputeEngineVM(
                name=res_name,
                machine_type=machine_type,
                region=region,
                vcpus=vcpus,
                memory_gb=memory_gb,
                preemptible=preemptible,
                gpu_type=gpu_type if gpu_type else '',
                gpu_count=gpu_count,
                boot_disk_size_gb=boot_disk_size_gb,
                boot_disk_type=boot_disk_type,
                capacity=self._get_compute_capacity(machine_type)
            )
            
        elif res_type == 'google_container_cluster':
            initial_node_count = self._safe_cast(self._get_dict_value(res_config, 'initial_node_count', 3), int, 3)
            node_version = self._get_dict_value(res_config, 'node_version', 'latest')
            network = self._get_dict_value(res_config, 'network', 'default')
            
            cluster = GKECluster(
                name=res_name,
                region=region,
                version=node_version,
                network=network
            )

            normalized_path = os.path.normpath(self.terraform_path)
            if "microservices-demo" in normalized_path:
                cluster.use_queue_utilization = True
                cluster.use_named_energy_profile = True
            
            # Create default nodes based on initial node count
            for i in range(initial_node_count):
                node_config = self._get_dict_value(res_config, 'node_config', {})
                machine_type = self._get_dict_value(node_config, 'machine_type', 'e2-medium')
                node = GKENode(
                    name=f"{res_name}-node-{i}",
                    machine_type=machine_type,
                    region=region,
                    vcpus=2,  # Assuming e2-medium default
                    memory_gb=4.0,
                    preemptible=self._get_dict_value(node_config, 'preemptible', False),
                    container_optimized=True,
                    capacity=self._get_compute_capacity(machine_type)
                )
                cluster.add_node(node)
                
            return cluster
            
        elif res_type == 'google_container_node_pool':
            node_count = self._safe_cast(self._get_dict_value(res_config, 'node_count', 1), int, 1)
            node_config = self._get_dict_value(res_config, 'node_config', {})
            machine_type = self._get_dict_value(node_config, 'machine_type', 'e2-medium')
            preemptible = self._get_dict_value(node_config, 'preemptible', False)
            
            return GKENode( # Should ideally be part of a GKECluster, or a pool that can be added to one.
                name=res_name,
                machine_type=machine_type,
                region=region,
                vcpus=2,  # Estimate for e2-medium
                memory_gb=4.0,
                preemptible=preemptible,
                capacity=self._get_compute_capacity(machine_type)
            )
            
        elif res_type == 'google_cloudfunctions_function':
            memory_mb = self._safe_cast(self._get_dict_value(res_config, 'available_memory_mb', 256), int, 256)
            timeout_sec = self._safe_cast(self._get_dict_value(res_config, 'timeout_seconds', self._get_dict_value(res_config, 'timeout_sec', 60)), int, 60) # Handle both keys
            
            return CloudFunction(
                name=res_name,
                region=region,
                memory_mb=memory_mb,
                timeout_sec=timeout_sec
            )
            
        elif res_type == 'google_cloud_run_service':
            template = self._get_dict_value(res_config, 'template', [{}])[0]
            spec = self._get_dict_value(template, 'spec', [{}])[0]
            containers = self._get_dict_value(spec, 'containers', [{}])
            container_config = containers[0] if containers else {}
            
            limits = self._get_dict_value(container_config, 'resources.0.limits', self._get_dict_value(container_config, 'resources.limits', {}))
            cpu_limit = self._safe_cast(self._get_dict_value(limits, 'cpu', '1'), float, 1.0)
            memory_limit_str = self._get_dict_value(limits, 'memory', '512Mi')
            memory_limit_mb = self._parse_memory_limit(memory_limit_str)

            annotations = self._get_dict_value(template, 'metadata.0.annotations', self._get_dict_value(template, 'metadata.annotations', {}))
            min_instances = self._safe_cast(self._get_dict_value(annotations, 'autoscaling.knative.dev/minScale', '0'), int, 0)
            max_instances = self._safe_cast(self._get_dict_value(annotations, 'autoscaling.knative.dev/maxScale', '100'), int, 100)
            
            # Concurrency can be in spec or annotations depending on API version
            concurrency = self._safe_cast(self._get_dict_value(spec, 'container_concurrency', '80'), int, 80)
            if 'run.googleapis.com/concurrency' in annotations:
                 concurrency = self._safe_cast(annotations['run.googleapis.com/concurrency'], int, 80)
            
            return CloudRun(
                name=res_name,
                region=region,
                cpu_limit=cpu_limit,
                memory_limit_mb=memory_limit_mb,
                max_instances=max_instances,
                min_instances=min_instances,
                concurrency=concurrency,
                capacity=max_instances * concurrency
            )
            
        elif res_type == 'google_app_engine_application' or res_type == 'google_app_engine_standard_app_version':
            instance_class = self._get_dict_value(res_config, 'instance_class', 'F1') # For standard_app_version
            if res_type == 'google_app_engine_application': # app_engine_application might not have instance_class directly
                 # It might be in 'iap_config' or implied by service level
                 pass


            scaling_config = self._get_dict_value(res_config, 'automatic_scaling', 
                                                self._get_dict_value(res_config, 'basic_scaling', 
                                                                  self._get_dict_value(res_config, 'manual_scaling', {})))
            
            scaling_type = "automatic" # Default
            if 'automatic_scaling' in res_config: scaling_type = "automatic"
            elif 'basic_scaling' in res_config: scaling_type = "basic"
            elif 'manual_scaling' in res_config: scaling_type = "manual"
                
            return AppEngine(
                name=res_name,
                region=region, # App Engine region is often derived from 'location_id' for application resource
                instance_class=instance_class,
                scaling_type=scaling_type
            )
        
        # ====== STORAGE RESOURCES ======
        elif res_type == 'google_storage_bucket':
            storage_class_str = self._get_dict_value(res_config, 'storage_class', 'STANDARD')
            try:
                member_name = storage_class_str.upper()
                storage_class_enum = getattr(GCPStorageClass, member_name)
            except AttributeError:
                logger.warning(f"Invalid storage class '{storage_class_str}' for {res_name}. Defaulting to STANDARD.")
                storage_class_enum = GCPStorageClass.STANDARD
                
            return CloudStorage(
                name=res_name,
                capacity_gb=1000.0,  # Default size, TF does not specify capacity for buckets
                region=region, # Buckets have a location, which maps to region
                storage_class=storage_class_enum
            )
            
        elif res_type == 'google_compute_disk':
            size_gb = self._safe_cast(self._get_dict_value(res_config, 'size', 10), int, 10)
            disk_type_str = self._get_dict_value(res_config, 'type', 'pd-standard')
            throughput_iops = 1000
            try:
                disk_type_enum = DiskType(disk_type_str)
            except ValueError:
                logger.warning(f"Invalid disk type '{disk_type_str}' for {res_name}. Defaulting to STANDARD.")
                disk_type_enum = DiskType.STANDARD
                
            return PersistentDisk(
                name=res_name,
                capacity_gb=size_gb,
                region=region, # Persistent disks are zonal, region should be derived from zone
                disk_type=disk_type_enum,
                capacity=throughput_iops
            )
            
        elif res_type == 'google_sql_database_instance':
            settings = self._get_dict_value(res_config, 'settings', {})
            tier = self._get_dict_value(settings, 'tier', 'db-f1-micro')
            database_version = self._get_dict_value(res_config, 'database_version', 'MYSQL_5_7') # Or POSTGRES_13 etc.
            
            high_availability = self._get_dict_value(settings, 'availability_type', 'ZONAL') == 'REGIONAL'
            
            tier_enum = DatabaseTier.STANDARD
            if 'micro' in tier.lower() or 'small' in tier.lower():
                tier_enum = DatabaseTier.BASIC
            elif high_availability:
                tier_enum = DatabaseTier.HIGH_AVAILABILITY
            size_gb = self._safe_cast(self._get_dict_value(settings, 'disk_size', 10), float, 10.0)
            return CloudSQL(
                name=res_name,
                capacity_gb=size_gb,
                region=region,
                instance_type=tier,
                database_tier=tier_enum,
                high_availability=high_availability
            )
            
        elif res_type.startswith('google_bigtable'): # e.g. google_bigtable_instance, google_bigtable_table
            # For instance
            num_nodes = 1
            clusters = self._get_dict_value(res_config, 'cluster', []) # This is a list for instance
            if isinstance(clusters, list) and clusters:
                 # Assuming single cluster for simplicity or take the first one
                num_nodes = self._safe_cast(self._get_dict_value(clusters[0], 'num_nodes', 1), int, 1)

            return Bigtable( # This might represent an instance or a table. Clarify modeling.
                name=res_name,
                capacity_gb=1000.0,  # Default size, Bigtable pricing is complex
                region=region, # Bigtable instances have clusters in zones.
                nodes=num_nodes
            )
            
        elif res_type.startswith('google_firestore'): # e.g. google_firestore_database, google_firestore_document
            mode = self._get_dict_value(res_config, 'type', 'FIRESTORE_NATIVE') # For google_firestore_database, type can be NATIVE or DATASTORE_MODE
            # app_engine_integration_mode is for older datastore resources
            
            return Firestore(
                name=res_name,
                capacity_gb=100.0,  # Default size, Firestore pricing is usage-based
                region=region, # Firestore database has a location_id
                mode=mode
            )
        
        # ====== NETWORK RESOURCES ======
        elif res_type == 'google_compute_network':
            subnet_ranges = [] # Subnets are separate resources usually
            auto_create = self._get_dict_value(res_config, 'auto_create_subnetworks', False)
            
            return VPC(
                name=res_name,
                region=region, # VPC is global but subnets are regional
                subnet_ranges=subnet_ranges, # This would be populated by related subnet resources
                auto_create_subnets=auto_create
            )
            
        elif res_type == 'google_compute_firewall':
            return NetworkResource(
                name=res_name,
                capacity=1.0, # Capacity for firewall is conceptual
                network_type=NetworkResourceType.FIREWALL,
                region=region, # Firewalls are associated with a VPC (global) but rules can be regional
                bandwidth_mbps=1000.0 # Conceptual
            )
            
        elif res_type.startswith('google_compute_router'): # e.g. google_compute_router, google_compute_router_interface, google_compute_router_peer
            return NetworkResource(
                name=res_name,
                capacity=1.0, # Conceptual
                network_type=NetworkResourceType.ROUTER,
                region=region # Routers are regional
            )
            
        elif res_type == 'google_compute_router_nat':
            return NetworkResource(
                name=res_name,
                capacity=1.0, # Conceptual
                network_type=NetworkResourceType.NAT,
                region=region # Cloud NAT is regional
            )
            
        elif res_type.startswith('google_compute_forwarding_rule') or res_type.startswith('google_compute_target_') or res_type.startswith('google_compute_url_map') or res_type.startswith('google_compute_backend_service'):
            # This is a simplification. These components together form a Load Balancer.
            # For LoadBalancer, the type (HTTP, TCP, etc.) and scope (Global, Regional) are important.
            lb_type = LoadBalancerType.HTTP # Default
            load_balancing_scheme = self._get_dict_value(res_config, 'load_balancing_scheme', 'EXTERNAL') # Common in forwarding rules and backend services
            
            if 'EXTERNAL' in load_balancing_scheme: lb_type = LoadBalancerType.EXTERNAL
            elif 'INTERNAL' in load_balancing_scheme: lb_type = LoadBalancerType.INTERNAL
            # Further checks for TCP/UDP based on IP protocol in forwarding rule, or protocol in backend service
            
            # Check for CDN for backend service
            if res_type.startswith('google_compute_backend_service'):
                if self._get_dict_value(res_config, 'enable_cdn', False):
                     return CDN(
                        name=res_name,
                        region=region, # CDN can be global, region might be for control plane
                    )

            return LoadBalancer(
                name=res_name,
                region=region, # Can be global or regional
                lb_type=lb_type
            )
            
        elif res_type.startswith('google_dns'): # e.g. google_dns_managed_zone, google_dns_record_set
            return DNS(
                name=res_name,
                region="global" # DNS zones are global, but can have regional policies
            )
            
        elif res_type.startswith('google_compute_vpn'): # e.g. google_compute_vpn_tunnel, google_compute_ha_vpn_gateway
            vpn_type = 'classic' # Default
            if res_type == 'google_compute_ha_vpn_gateway': vpn_type = 'ha'
            
            return VPN(
                name=res_name,
                region=region, # VPN Gateways are regional
                vpn_type=vpn_type
            )
            
        elif res_type.startswith('google_compute_interconnect'): # e.g. google_compute_interconnect_attachment
            interconnect_type = 'partner' # or 'dedicated' based on 'type' field in attachment or interconnect resource
            if 'type' in res_config:
                interconnect_type = self._get_dict_value(res_config, 'type', 'partner').lower()

            return Interconnect(
                name=res_name,
                region=region, # Interconnect attachments are regional
                bandwidth_mbps=self._safe_cast(self._get_dict_value(res_config, 'bandwidth', 'BPS_10G'), str, 'BPS_10G'), # Map this to actual Mbps
                interconnect_type=interconnect_type
            )
        
        # Note: google_compute_backend_service handled with LoadBalancer/CDN above

        elif res_type.startswith('google_api_gateway'): # e.g. google_api_gateway_api, google_api_gateway_config
            # Simplified mapping, actual gateway would involve multiple resources
            return APIGateway(
                name=res_name, # This might be the API name or config name
                region=region, # API Gateway is regional
                bandwidth_mbps=1000.0, # Conceptual default
                max_requests_per_second=1000.0, # Conceptual default
            )
            
        # ====== SECURITY RESOURCES ======
        elif res_type == 'google_service_account':
            return SecurityResource( # This is a generic security resource, might need a specific IAMUser/ServiceAccount class
                name=res_name,
                security_type=SecurityResourceType.IAM,
                region="global"
            )
            
        elif res_type.startswith('google_project_iam') or res_type.endswith('iam_member') or res_type.endswith('iam_binding') or res_type.endswith('iam_policy'):
            # These define IAM policies/bindings, not distinct resources in the same way as a VM
            iam = IAM(
        name=res_name,
        region="global"
    )
            # now force the capacity on the instance, instead of in the constructor
            iam.capacity = 10_000
            return iam
            
        elif res_type.startswith('google_kms'): # e.g. google_kms_key_ring, google_kms_crypto_key
            key_ring_name = res_name
            key_count = 0 # This would need to count crypto keys associated with this key ring if this is a key_ring resource
            if res_type == 'google_kms_crypto_key':
                key_count = 1 # If it's a crypto key itself
            
            protection_level = self._get_dict_value(res_config, 'protection_level', 'SOFTWARE') # For crypto_key
            if res_type == 'google_kms_key_ring': # KeyRing does not have protection_level
                 protection_level = 'SOFTWARE' # Default for associated keys

            return CloudKMS(
                name=res_name, # KeyRing name or CryptoKey name
                region=region, # KMS resources are regional or global
                key_count=key_count, # Number of keys in a keyring, or 1 if it's a key
                protection_level=protection_level.upper()
            )
            
        elif res_type.startswith('google_secret_manager'): # e.g. google_secret_manager_secret, google_secret_manager_secret_version
            return SecretManager(
                name=res_name, # Secret name
                region=region # Secrets can be regional or global based on replication policy
            )
            
        elif res_type.startswith('google_scc') or res_type.startswith('google_securitycenter'): # e.g. google_scc_source, google_securitycenter_source
            return SecurityCommandCenter( # Represents SCC setup or a specific finding/source
                name=res_name,
                region="global", # SCC is generally a global view
                monitored_assets_count=100, # Conceptual
                tier='STANDARD' # or PREMIUM
            )
            
        elif res_type == 'google_compute_security_policy':
            rules = self._get_dict_value(res_config, 'rule', [])
            rule_count = len(rules) if isinstance(rules, list) else 0
                
            return CloudArmor(
                name=res_name,
                region="global", # Cloud Armor policies can be global or regional
                rule_count=rule_count,
                protection_tier=self._get_dict_value(res_config, 'type', 'CLOUD_ARMOR').upper() # Type might be CLOUD_ARMOR or CLOUD_ARMOR_EDGE
            )
        else:
            # For any unhandled resource types, create a generic component
            logger.warning(f"Creating generic component for unhandled resource type: {res_type}")
            # Try to determine a service category if possible based on res_type prefix
            service_category = GCPServiceCategory.OTHER
            if "compute" in res_type: service_category = GCPServiceCategory.COMPUTE
            elif "storage" in res_type: service_category = GCPServiceCategory.STORAGE
            elif "network" in res_type or "vpc" in res_type or "dns" in res_type: service_category = GCPServiceCategory.NETWORK
            elif "sql" in res_type or "database" in res_type or "bigtable" in res_type or "firestore" in res_type: service_category = GCPServiceCategory.DATABASE
            elif "iam" in res_type or "kms" in res_type or "security" in res_type: service_category = GCPServiceCategory.SECURITY

            return GenericGCPComponent(
                name=res_name,
                resource_type=ResourceType.GENERIC, # Map to a generic type
                region=region,
                service_category=service_category,
                attributes={'terraform_type': res_type}
            )
