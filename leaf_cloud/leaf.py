"""Main LEAF-Cloud class for managing the simulation lifecycle.

This class provides the main entry point for interacting with the LEAF-Cloud framework.
It handles configuration, model loading, and simulation execution.
"""
from typing import Optional, List, Dict, Any, Union, TYPE_CHECKING
from pathlib import Path
from collections import defaultdict
import logging
import json
import time
import uuid
import math
from datetime import datetime, timezone
from .utils.schemas.results import (
    RawSimulationResult,
    RawResourceMetrics,
    SimulationMetadata,
    TimeSeriesDataPoint,
    TokenTraceEvent,
)
from .terraform import TerraformParser
from .terraform.model_builder import ModelBuilder
from .kubernetes.parser import KubernetesParser
from .kubernetes.model_builder import KubernetesModelBuilder
from .core.petri_net import PetriNet, Place, Transition, Arc, Token
from .utils.machine_specs import get_machine_specs

if TYPE_CHECKING:
    from .kubernetes.parser import K8sResource

logger = logging.getLogger(__name__)


class LEAFCloud:
    DEFAULT_POD_CPU = 0.5
    DEFAULT_POD_MEMORY_GB = 1.0
    """Main class for the LEAF-Cloud framework.
    
    This class provides methods for loading Terraform configurations, building models,
    and running simulations.
    """
    
    def __init__(self, config_path: Optional[str] = None) -> None:
        """Initialize the LEAFCloud instance.
        
        Args:
            config_path: Optional path to a configuration file.
        """
        self.config_path = config_path
        self.terraform_dir: Optional[Path] = None
        self.kubernetes_manifest: Optional[Path] = None
        self.initialized = False
        self.simulation_results = {}
        self.workload_config = {}
        self.simulation_parameters = {}
        self.model = None
        self.resource_mapping: Dict[str, Any] = {}
        self.petri_net: Optional[PetriNet] = None
        self._parser: Optional[TerraformParser] = None
        self._kube_parser: Optional[KubernetesParser] = None
        self._kube_resources: List["K8sResource"] = []
        self._last_model_builder: Optional[ModelBuilder] = None
        self._pod_resource_requirements: Dict[str, float] = {
            "cpu": self.DEFAULT_POD_CPU,
            "memory_gb": self.DEFAULT_POD_MEMORY_GB,
        }
        self._cluster_autoscaler_requirements: Dict[str, Dict[str, Any]] = {}
        self._kube_workload_summary: Dict[str, float] = {}
        self._kube_fallback_config: Dict[str, Any] = {}
        self._synthetic_node_ids: List[str] = []
        
        # Initialize configuration
        try:
            from .config import LEAFCloudConfig
            if config_path:
                self.config = LEAFCloudConfig.from_file(config_path)
            else:
                self.config = LEAFCloudConfig()
        except Exception as e:
            logger.warning(f"Could not load configuration: {e}. Using defaults.")
            # Create a minimal config object with required sections for orchestrator
            class MinimalConfig:
                def __init__(self):
                    self.models = type('obj', (object,), {
                        'latency': type('obj', (object,), {
                            'stochastic_variation': {
                                'enabled': False,
                                'distribution': 'uniform',
                                'params': {
                                    'uniform': {'min': -0.1, 'max': 0.1},
                                    'normal': {'mean': 0.0, 'stddev': 0.05}
                                },
                                'apply_to': ['all']
                            }
                        })()
                    })()
                    
                    # Add required sections for orchestrator
                    self.simulation = type('obj', (object,), {
                        'duration': 3600.0,
                        'time_step': 1.0,
                        'workload_rate': 100.0,
                        'metrics_interval': 1.0,
                        'workload': type('obj', (object,), {
                            'type': 'steady',
                            'params': {'rate': 100.0}
                        })()
                    })()
                    
                    self.logging = type('obj', (object,), {
                        'level': 'INFO',
                        'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
                    })()
                    
                    self.metrics = type('obj', (object,), {
                        'enabled': True,
                        'interval': 1.0
                    })()
                    
                    self.output_dir = Path("output")
                    
                def get(self, key, default=None):
                    """Support dict-like access for config values."""
                    keys = key.split('.')
                    obj = self
                    for k in keys:
                        if hasattr(obj, k):
                            obj = getattr(obj, k)
                        else:
                            return default
                    return obj
                
                def to_dict(self):
                    """Convert config to dictionary format for compatibility."""
                    return {
                        'models': {
                            'latency': {
                                'stochastic_variation': {
                                    'enabled': False,
                                    'distribution': 'uniform',
                                    'params': {
                                        'uniform': {'min': -0.1, 'max': 0.1},
                                        'normal': {'mean': 0.0, 'stddev': 0.05}
                                    },
                                    'apply_to': ['all']
                                }
                            }
                        },
                        'simulation': {
                            'duration': 3600.0,
                            'time_step': 1.0,
                            'workload_rate': 100.0,
                            'metrics_interval': 1.0,
                            'workload': {
                                'type': 'steady',
                                'params': {'rate': 100.0}
                            }
                        },
                        'logging': {
                            'level': 'INFO',
                            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
                        },
                        'metrics': {
                            'enabled': True,
                            'interval': 1.0
                        },
                        'output_dir': str(self.output_dir)
                    }
            
            self.config = MinimalConfig()
        
        logger.debug("Initialized LEAFCloud with config: %s", config_path)
    
    def load_terraform(self, terraform_path: Union[str, Path], var_files: Optional[List[Union[str, Path]]] = None) -> None:
        """Load a Terraform configuration directory or plan.json file with optional variable files.
        
        Args:
            terraform_path: Path to the Terraform directory or plan.json file.
            var_files: Optional list of paths to variable files (.tfvars) to load.
                     These files will be used in addition to any .tfvars files
                     found recursively in the terraform_dir.
            
        Raises:
            FileNotFoundError: If the directory/file or any specified var file is not found.
        """
        terraform_path = Path(terraform_path).resolve()
        if not terraform_path.exists():
            raise FileNotFoundError(f"Terraform path not found: {terraform_path}")
        
        # Convert var_files to Path objects and validate they exist
        var_file_paths = []
        if var_files:
            for vf in var_files:
                vf_path = Path(vf).resolve()
                if not vf_path.exists():
                    raise FileNotFoundError(f"Variable file not found: {vf_path}")
                var_file_paths.append(str(vf_path))
        
        # Store the terraform path and variable files
        self.terraform_dir = terraform_path
        self._parser = TerraformParser(str(terraform_path), var_file_paths)
        
        # Parse the configuration
        self._parsed_config = self._parser.parse_all()
        
        logger.info("Loaded Terraform configuration from %s", terraform_path)
        if var_file_paths:
            logger.info("Using variable files: %s", ", ".join(var_file_paths))
        # Reset Kubernetes context when switching inputs
        self._kube_parser = None
        self._kube_resources: List["K8sResource"] = []

    def load_kubernetes(self, manifest_path: Union[str, Path], kubeconfig: Optional[Union[str, Path]] = None) -> None:
        """Load Kubernetes manifests from a file or directory.
        
        Args:
            manifest_path: Path to a Kubernetes manifest file or directory containing manifests.
            kubeconfig: Optional kubeconfig path for future cluster queries.
        """
        manifest = Path(manifest_path).resolve()
        if not manifest.exists():
            raise FileNotFoundError(f"Kubernetes manifest path not found: {manifest}")

        kubeconfig_path = Path(kubeconfig).resolve() if kubeconfig else None
        self.kubernetes_manifest = manifest
        self._kube_parser = KubernetesParser(manifest, kubeconfig_path)
        self._kube_resources = self._kube_parser.parse()

        logger.info(
            "Loaded %d Kubernetes manifest resources from %s",
            len(self._kube_resources),
            manifest,
        )
        if self._kube_parser.unsupported_kinds:
            unsupported = ", ".join(
                f"{kind} ({count})" for kind, count in sorted(self._kube_parser.unsupported_kinds.items())
            )
            logger.warning("Unsupported Kubernetes kinds encountered: %s", unsupported)

    def configure_kubernetes_fallback(self, fallback_config: Optional[Dict[str, Any]]) -> None:
        """Configure defaults for synthetic nodes when Terraform data is missing."""
        cfg = dict(fallback_config or {})
        if cfg:
            cfg.setdefault("enabled", True)
            cfg.setdefault("machine_type", "e2-standard-2")
            cfg.setdefault("region", "us-central1")
            cfg.setdefault("cpu_headroom", 0.75)
            cfg.setdefault("memory_headroom", 0.8)
        self._kube_fallback_config = cfg
        
    def build_model(self, tuned_params: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Build the intermediate model from the loaded Terraform configuration.
        
        Returns:
            A dictionary representing the built model.
        """
        logger.info("Building infrastructure model")
        
        # Initialize the model structure
        self.model = {
            "version": "1.0",
            "terraform_dir": str(self.terraform_dir) if self.terraform_dir else None,
            "resources": [],
            "kubernetes_resources": [],
            "modules": [],
            "variables": [],
            "outputs": [],
            "config": {},
            "metadata": {
                "generated_by": "LEAF-Cloud",
                "format_version": "1.0"
            }
        }
        
        terraform_available = hasattr(self, "_parser") and self._parser is not None
        pod_requirements = dict(self._pod_resource_requirements)
        kube_builder: Optional[KubernetesModelBuilder] = None

        if terraform_available:
            try:
                resources = getattr(self._parser, 'resources', {})
                logger.debug("Found %d resources in parser", len(resources))
                
                for resource_id, resource_data in resources.items():
                    try:
                        resource_entry = {
                            'id': resource_id,
                            'type': resource_data.get('type', 'unknown'),
                            'name': resource_data.get('name', ''),
                            'config': resource_data.get('config', {}),
                            'file': resource_data.get('file', '')
                        }
                        self.model['resources'].append(resource_entry)
                        logger.debug("Added resource: %s (%s)", resource_id, resource_entry['type'])
                    except Exception as e:
                        logger.error("Error processing resource %s: %s", resource_id, str(e), exc_info=True)
                
                variables = getattr(self._parser, 'variables', {})
                logger.debug("Found %d variables in parser", len(variables))
                
                for var_name, var_data in variables.items():
                    try:
                        var_entry = {
                            'name': var_name,
                            'type': var_data.get('type', 'any'),
                            'default': var_data.get('default'),
                            'description': var_data.get('description', ''),
                            'value': var_data.get('value')
                        }
                        self.model['variables'].append(var_entry)
                        logger.debug("Added variable: %s", var_name)
                    except Exception as e:
                        logger.error("Error processing variable %s: %s", var_name, str(e), exc_info=True)
                
                outputs = getattr(self._parser, 'outputs', {})
                logger.debug("Found %d outputs in parser", len(outputs))
                
                for output_name, output_data in outputs.items():
                    try:
                        output_entry = {
                            'name': output_name,
                            'value': output_data.get('value'),
                            'description': output_data.get('description', ''),
                            'sensitive': output_data.get('sensitive', False)
                        }
                        self.model['outputs'].append(output_entry)
                        logger.debug("Added output: %s", output_name)
                    except Exception as e:
                        logger.error("Error processing output %s: %s", output_name, str(e), exc_info=True)
                
                resources = getattr(self._parser, 'resources', {})
                logger.info("Parser found %d resources", len(resources))
                
                logger.info(
                    "Successfully built model with %d resources, %d variables, and %d outputs",
                    len(self.model['resources']),
                    len(self.model['variables']),
                    len(self.model['outputs']),
                )
            except Exception as e:
                logger.error("Error building model: %s", str(e), exc_info=True)
                raise
        else:
            logger.info("No Terraform configuration loaded; skipping Terraform-specific parsing.")

        if getattr(self, "_kube_resources", None):
            for k8s_res in self._kube_resources:
                k8s_entry = {
                    "id": k8s_res.resource_id,
                    "type": k8s_res.kind,
                    "name": k8s_res.name,
                    "namespace": k8s_res.namespace,
                    "metadata": k8s_res.metadata,
                    "spec": k8s_res.spec,
                    "source": str(k8s_res.source.path),
                    "source_line": k8s_res.source.start_line,
                }
                self.model['kubernetes_resources'].append(k8s_entry)
                self.model['resources'].append({**k8s_entry, "source_type": "kubernetes"})
            kube_builder = KubernetesModelBuilder(self._kube_resources, cluster_name="k8s-cluster")
                      
        # Build detailed resource mapping and Petri net using ModelBuilder
        terraform_mapping: Dict[str, Any] = {}
        terraform_petri_net = None
        kubernetes_mapping: Dict[str, Any] = {}
        kubernetes_petri_net = None
        self._cluster_autoscaler_requirements = {}
        self._kube_workload_summary = {}
        if kube_builder is not None:
            try:
                kubernetes_petri_net = kube_builder.build_model()
                kubernetes_mapping = kube_builder.resource_mapping
                cpu_req, mem_req = kube_builder.derive_average_pod_resources(
                    self.DEFAULT_POD_CPU,
                    self.DEFAULT_POD_MEMORY_GB,
                )
                pod_requirements = {"cpu": cpu_req, "memory_gb": mem_req}
                self._pod_resource_requirements = pod_requirements
                pod_requirements = dict(self._pod_resource_requirements)
                self._cluster_autoscaler_requirements = self._derive_cluster_autoscaler_requirements(kube_builder)
                try:
                    self._kube_workload_summary = kube_builder.summarize_workload_resources()
                except Exception:
                    self._kube_workload_summary = {}
                logger.info(
                    "KubernetesModelBuilder mapped %d operational resources",
                    len(kubernetes_mapping),
                )
            except Exception as exc:
                logger.error("Kubernetes model builder failed: %s", exc, exc_info=True)
                kubernetes_mapping = {}
                kubernetes_petri_net = None
                self._cluster_autoscaler_requirements = {}
                self._kube_workload_summary = {}
        try:
            if self.terraform_dir:
                builder = ModelBuilder(
                    str(self.terraform_dir),
                    self.config,
                    tuned_params=tuned_params,
                    pod_resource_requirements=pod_requirements,
                    cluster_autoscaler_requirements=self._cluster_autoscaler_requirements,
                    parsed_resources=getattr(self, "_parsed_config", {}).get("resources", {}),
                    parser=self._parser,
                )
                _ = builder.build_model()
                terraform_mapping = builder.resource_mapping
                terraform_petri_net = builder.petri_net
                self._last_model_builder = builder
                logger.info("ModelBuilder mapped %d operational resources", len(terraform_mapping))
            else:
                logger.info("terraform_dir is not set; skipping Terraform ModelBuilder phase")
        except Exception as e:
            logger.error("ModelBuilder failed: %s", e, exc_info=True)
            terraform_mapping = {}
            terraform_petri_net = None
            self._last_model_builder = None
            # We don't raise here so that heuristic fallback can still work

        combined_mapping: Dict[str, Any] = {}
        if terraform_mapping:
            combined_mapping.update(terraform_mapping)
        if kubernetes_mapping:
            combined_mapping.update(kubernetes_mapping)

        if terraform_petri_net and kubernetes_petri_net:
            self.petri_net = terraform_petri_net
            self._merge_petri_nets(self.petri_net, kubernetes_petri_net)
        elif terraform_petri_net:
            self.petri_net = terraform_petri_net
        elif kubernetes_petri_net:
            self.petri_net = kubernetes_petri_net
        else:
            self.petri_net = None

        self.resource_mapping = combined_mapping

        return self.model

    def _merge_petri_nets(self, base: Optional[PetriNet], other: Optional[PetriNet]) -> None:
        """Merge Petri net components from `other` into `base`."""
        if base is None or other is None:
            return

        for place in other.places.values():
            if place.id in base.places:
                continue
            cloned_place = Place(id=place.id, name=place.name, capacity=place.capacity)
            for token in place.tokens:
                try:
                    cloned_place.add_token(token)
                except Exception:
                    continue
            base.add_place(cloned_place)

        for transition in other.transitions.values():
            if transition.id in base.transitions:
                continue
            cloned_transition = Transition(
                id=transition.id,
                name=transition.name,
                delay=transition.delay,
                priority=transition.priority,
                guard=getattr(transition, "_guard", None),
                action=getattr(transition, "_action", None),
            )
            base.add_transition(cloned_transition)

        for arc in other.arcs.values():
            if arc.id in base.arcs:
                continue
            cloned_arc = Arc(
                id=arc.id,
                place_id=arc.place_id,
                transition_id=arc.transition_id,
                direction=arc.direction,
                weight=arc.weight,
                guard=getattr(arc, "_guard", None),
            )
            base.add_arc(cloned_arc)

    def _derive_cluster_autoscaler_requirements(
        self, kube_builder: KubernetesModelBuilder
    ) -> Dict[str, Dict[str, Any]]:
        specs = getattr(kube_builder, "workload_autoscalers", {}) or {}
        if not specs:
            return {}

        total_min = 0
        total_max = 0
        max_present = False
        for info in specs.values():
            current = info.get("current_replicas")
            try:
                current = int(current)
            except (TypeError, ValueError):
                current = 1
            current = max(1, current)

            min_rep = info.get("min_replicas")
            max_rep = info.get("max_replicas")
            total_min += int(min_rep) if isinstance(min_rep, int) and min_rep > 0 else current
            if isinstance(max_rep, int) and max_rep > 0:
                total_max += max_rep
                max_present = True
            else:
                total_max += int(min_rep) if isinstance(min_rep, int) and min_rep > 0 else current

        summary = {
            "min_pods": total_min if total_min > 0 else None,
            "max_pods": total_max if max_present else None,
        }
        requirements: Dict[str, Dict[str, Any]] = {}
        cluster_key = getattr(kube_builder, "cluster_name", None)
        if cluster_key:
            requirements[str(cluster_key)] = summary.copy()
        requirements["*"] = summary.copy()
        return requirements

    def _has_gke_nodes(self) -> bool:
        try:
            from .gcp.compute import GKENode  # type: ignore
        except Exception:
            return False
        return any(
            isinstance(res, GKENode) for res in (self.resource_mapping or {}).values()
        )

    def _get_resource_metrics_map(self, simulation_result: Any) -> Optional[Dict[str, Any]]:
        if simulation_result is None:
            return None
        if isinstance(simulation_result, dict):
            return simulation_result.get("resource_metrics")
        return getattr(simulation_result, "resource_metrics", None)

    def _workload_ids(self) -> List[str]:
        try:
            from .kubernetes.model_builder import K8sWorkloadResource  # type: ignore
        except Exception:
            return []
        ids: List[str] = []
        for rid, res in (self.resource_mapping or {}).items():
            if isinstance(res, K8sWorkloadResource):
                ids.append(rid)
        return ids

    def _build_workload_cpu_timeline(
        self,
        workload_ids: List[str],
        metrics_map: Dict[str, Any],
    ) -> Dict[float, float]:
        timeline: Dict[float, float] = defaultdict(float)
        for rid in workload_ids:
            res_metrics = metrics_map.get(rid)
            if res_metrics is None:
                continue
            if hasattr(res_metrics, "utilization"):
                util_series = getattr(res_metrics, "utilization") or []
            elif isinstance(res_metrics, dict):
                util_series = res_metrics.get("utilization") or []
            else:
                util_series = []

            capacity = float(getattr(self.resource_mapping.get(rid, object()), "capacity", 1.0))
            for point in util_series:
                ts = None
                val = None
                if hasattr(point, "timestamp"):
                    ts = getattr(point, "timestamp", None)
                elif isinstance(point, dict):
                    ts = point.get("timestamp")
                    if ts is None:
                        ts = point.get("time")
                if hasattr(point, "value"):
                    val = getattr(point, "value", None)
                elif isinstance(point, dict):
                    val = point.get("value")
                if ts is None or val is None:
                    continue
                try:
                    ts_f = float(ts)
                    val_f = max(0.0, float(val))
                except (TypeError, ValueError):
                    continue
                timeline[ts_f] += capacity * val_f
        return timeline

    def apply_kubernetes_node_fallback(self, simulation_result: Any) -> None:
        """Inject synthetic node metrics when Terraform nodes are unavailable."""
        cfg = self._kube_fallback_config or {}
        if not cfg.get("enabled", False):
            return
        if self._synthetic_node_ids:
            return
        if not self._kube_workload_summary:
            return
        if self._has_gke_nodes():
            return

        metrics_map = self._get_resource_metrics_map(simulation_result)
        if not isinstance(metrics_map, dict):
            return
        workload_ids = self._workload_ids()
        if not workload_ids:
            return

        timeline = self._build_workload_cpu_timeline(workload_ids, metrics_map)
        if not timeline:
            logger.info(
                "Kubernetes fallback requested but workload metrics were empty; skipping synthetic nodes."
            )
            return

        machine_type = str(cfg.get("machine_type") or "e2-standard-2")
        node_region = str(cfg.get("region") or "us-central1")
        cpu_headroom = max(0.05, min(1.0, float(cfg.get("cpu_headroom", 0.75) or 0.75)))
        memory_headroom = max(0.05, min(1.0, float(cfg.get("memory_headroom", 0.8) or 0.8)))
        per_node_vcpus, per_node_mem = get_machine_specs(machine_type)
        per_node_vcpus = max(per_node_vcpus, 0.1)
        per_node_mem = max(per_node_mem, 0.1)

        summary = self._kube_workload_summary
        total_cpu = float(summary.get("total_cpu_requests") or summary.get("total_cpu_limits") or 0.0)
        total_mem = float(
            summary.get("total_memory_requests_gb") or summary.get("total_memory_limits_gb") or 0.0
        )
        cpu_based = math.ceil(total_cpu / (per_node_vcpus * cpu_headroom)) if total_cpu > 0 else 0
        mem_based = math.ceil(total_mem / (per_node_mem * memory_headroom)) if total_mem > 0 else 0
        node_count = max(cpu_based, mem_based, int(cfg.get("min_count") or 1))

        default_nodes = int(cfg.get("default_count") or 0)
        if default_nodes > 0:
            node_count = default_nodes
        max_nodes = cfg.get("max_count")
        if isinstance(max_nodes, int) and max_nodes > 0:
            node_count = min(node_count, max_nodes)
        node_count = max(node_count, 1)

        total_capacity = per_node_vcpus * node_count
        if total_capacity <= 0:
            return

        sorted_points = sorted(timeline.items())
        node_util_series = [
            {"timestamp": ts, "value": min(1.0, max(0.0, usage / total_capacity))}
            for ts, usage in sorted_points
        ]

        from .gcp.compute import GKECluster, GKENode  # type: ignore

        cluster_name = cfg.get("cluster_name") or "synthetic-gke-cluster"
        cluster = GKECluster(
            name=cluster_name,
            region=node_region,
            min_pods=int(summary.get("total_replicas") or 0) or None,
            max_pods=None,
        )
        self.resource_mapping[f"{cluster_name}"] = cluster

        # Pre-calculate the utilization series once for all nodes
        utilization_series = [
            TimeSeriesDataPoint(timestamp=float(point["timestamp"]), value=float(point["value"]))
            for point in node_util_series
        ]

        for idx in range(node_count):
            node_id = f"{cluster_name}.node[{idx}]"
            node = GKENode(
                name=node_id,
                machine_type=machine_type,
                region=node_region,
                vcpus=int(math.ceil(per_node_vcpus)),
                memory_gb=per_node_mem,
                capacity=per_node_vcpus,
            )
            try:
                cluster.add_node(node)
            except Exception:
                pass
            self.resource_mapping[node_id] = node
            metrics_map[node_id] = RawResourceMetrics(
                utilization=utilization_series,
                power_draw_w=[],
                memory_usage_bytes=[],
                network_in_bps=[],
                network_out_bps=[],
                disk_read_bps=[],
                disk_write_bps=[],
                tokens_processed=[],
                request_latency_ms=[],
                replica_count=[],
                resource_type="compute",
                region=node_region,
            )
            self._synthetic_node_ids.append(node_id)

        if isinstance(simulation_result, dict):
            simulation_result["resource_metrics"] = metrics_map
        else:
            try:
                setattr(simulation_result, "resource_metrics", metrics_map)
            except Exception:
                pass

        logger.info(
            "Synthesized %d GKE node(s) of type %s in %s for Kubernetes-only workload estimation.",
            node_count,
            machine_type,
            node_region,
        )
    
    def configure_workload(self, workload_config: Dict[str, Any]) -> None:
        """Configure the workload for the simulation.
        
        Args:
            workload_config: Dictionary containing workload configuration parameters.
            
        Raises:
            ValueError: If workload configuration is invalid
        """
        # Validate workload configuration
        self._validate_workload_config(workload_config)
        
        self.workload_config = workload_config
        logger.info("Configured workload: %s", workload_config)
        logger.info("Workload type: %s", workload_config.get('type'))
        logger.info("Workload params: %s", workload_config.get('params'))
    
    def _validate_workload_config(self, workload_config: Dict[str, Any]) -> None:
        """Validate workload configuration parameters.
        
        Args:
            workload_config: Dictionary containing workload configuration
            
        Raises:
            ValueError: If configuration is invalid
        """
        if not workload_config:
            raise ValueError("Workload configuration cannot be empty")
        
        workload_type = workload_config.get('type')
        if not workload_type:
            raise ValueError("Workload type is required")
        
        params = workload_config.get('params', {})
        
        if workload_type == 'steady':
            rate = params.get('rate')
            if rate is None:
                raise ValueError("Steady workload requires 'rate' parameter")
            if not isinstance(rate, (int, float)) or rate <= 0:
                raise ValueError("Steady workload rate must be a positive number")
        
        elif workload_type == 'burst':
            # Accept both legacy and normalized parameter names and validate presence/positivity
            # Legacy: burst_rate, burst_duration, idle_duration [, base_rate]
            # Normalized: base_rate, peak_rate, burst_duration, burst_interval
            def _is_pos(x):
                return isinstance(x, (int, float)) and x > 0

            # Accept synonyms from CLI mapping: duration -> burst_duration, interval -> burst_interval
            burst_duration = params.get('burst_duration', params.get('duration'))
            burst_interval = params.get('burst_interval', params.get('interval'))
            idle_duration = params.get('idle_duration')
            base_rate = params.get('base_rate')
            peak_rate = params.get('peak_rate', params.get('burst_rate'))

            if not _is_pos(burst_duration):
                raise ValueError("Burst workload requires positive 'burst_duration'")

            # Allow either burst_interval or idle_duration (we can derive one from the other)
            if burst_interval is None and idle_duration is None:
                raise ValueError("Burst workload requires 'burst_interval' or 'idle_duration'")
            if burst_interval is not None and not _is_pos(burst_interval):
                raise ValueError("Burst workload 'burst_interval' must be a positive number")
            if idle_duration is not None and not _is_pos(idle_duration):
                raise ValueError("Burst workload 'idle_duration' must be a positive number")

            # Rates: require either peak_rate or burst_rate; base_rate may be provided or defaulted
            if peak_rate is None or not _is_pos(peak_rate):
                raise ValueError("Burst workload requires positive 'peak_rate' (or 'burst_rate')")
            if base_rate is not None and not _is_pos(base_rate):
                raise ValueError("Burst workload 'base_rate' must be a positive number when provided")
        
        elif workload_type == 'csv':
            csv_file = params.get('csv_file') or params.get('file')
            if not csv_file:
                raise ValueError("CSV workload requires 'csv_file' or 'file' parameter")
            if not isinstance(csv_file, str) or not csv_file.strip():
                raise ValueError("CSV workload file path must be a non-empty string")
        
        elif workload_type not in ['steady', 'burst', 'cyclical', 'random', 'custom', 'csv']:
            raise ValueError(f"Unsupported workload type: {workload_type}")
    
    def _parse_workload_from_csv(self, path: str) -> List[List[Union[int, float]]]:
        """Convert a CSV (timestamp, rps) to the pattern format expected by LEAF-Cloud.
        
        Args:
            path: Path to the CSV file
            
        Returns:
            List of [time_offset, rate] pairs where time_offset is seconds from start
        """
        import csv
        from datetime import datetime
        from typing import List, Union
        
        pattern: List[List[Union[int, float]]] = []
        
        with open(path, newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                # Skip header-like rows that are clearly not "date, value"
                if len(row) < 2 or not row[0] or not row[1].strip().replace(".", "", 1).isdigit():
                    # Example header lines: "TimeSeries ID, …", "project_id …"
                    continue
                try:
                    ts = datetime.strptime(row[0].strip(), "%a %b %d %Y %H:%M:%S GMT%z (%Z)")
                    rps = float(row[1])
                except ValueError:
                    # Ignore lines we fail to parse cleanly
                    continue
                pattern.append([ts, rps])

        if not pattern:
            raise ValueError("No valid workload data could be parsed from the CSV file.")

        # Convert absolute timestamps → seconds-elapsed
        t0 = pattern[0][0]
        pattern = [[int((ts - t0).total_seconds()), rps] for ts, rps in pattern]
        return pattern
    
    def run_simulation(self, mode="heuristic", **kwargs) -> "RawSimulationResult":
        """Run the simulation and return a ``RawSimulationResult`` instance.

        Args:
            mode: Simulation mode - "heuristic" for fast estimation or "detailed" for full Petri net simulation
            **kwargs: Additional simulation parameters. When running in detailed mode,
                set ``allow_fallback=True`` (or ``fallback_on_error=True``) to permit
                automatic fallback to the heuristic model if the orchestrator fails.
                By default, detailed mode raises the error instead of falling back.
            
        This implementation can use either:
        - Heuristic mode: Fast mathematical estimation based on workload parameters
        - Detailed mode: Full Petri net discrete event simulation via orchestrator
        """
        if mode == "detailed" or kwargs.get("use_orchestrator", False):
            return self._run_orchestrator_simulation(**kwargs)
        else:
            return self._run_heuristic_simulation(**kwargs)
    
    def _run_heuristic_simulation(self, **kwargs) -> "RawSimulationResult":
        """Run the heuristic simulation (current implementation).
        
        This generates realistic simulation data based on mathematical estimation
        rather than discrete event simulation.
        """
        logger.info("Starting heuristic simulation with parameters: %s", kwargs)

        # ------------------------------------------------------------------
        # Derive simulation parameters
        # ------------------------------------------------------------------
        duration: float = float(kwargs.get("duration", 3600.0))
        iterations: int = int(kwargs.get("iterations", 1))
        start_time_ts: float = datetime.now(timezone.utc).timestamp()
        simulation_id: str = f"sim_{int(start_time_ts)}"

        # Calculate simulation steps based on duration
        step_duration: float = 1.0  # 1 second per step
        steps: int = int(duration / step_duration)

        # ------------------------------------------------------------------
        # Build SimulationMetadata
        # ------------------------------------------------------------------
        try:
            from . import __version__ as leaf_version  # type: ignore
        except Exception:
            leaf_version = "0.1.0"

        end_time_ts: float = start_time_ts + duration

        metadata = SimulationMetadata(
            simulation_id=simulation_id,
            start_time=start_time_ts,
            end_time=end_time_ts,
            duration_seconds=duration,
            leaf_cloud_version=leaf_version,
            config_hash=None,
        )

        # ------------------------------------------------------------------
        # Capture the configuration snapshot
        # ------------------------------------------------------------------
        config_snapshot = {
            "workload": getattr(self, "workload_config", {}),
            "parameters": kwargs,
            "terraform_dir": str(self.terraform_dir) if self.terraform_dir else None,
        }

        # ------------------------------------------------------------------
        # Generate realistic resource metrics based on parsed Terraform
        # ------------------------------------------------------------------
        resource_metrics = {}
        
        if hasattr(self, 'model') and self.model and 'resources' in self.model:
            import random
            import math
            
            # Ensure different random sequences for each simulation
            random.seed(int(start_time_ts * 1000) % 2147483647)
            
            # Get workload rate for simulation based on workload type
            workload_rate = 100.0  # default rate
            if hasattr(self, 'workload_config') and self.workload_config:
                workload_type = self.workload_config.get('type', 'steady')
                params = self.workload_config.get('params', {})
                
                logger.info(f"Processing workload type: {workload_type} with params: {params}")
                
                if workload_type == 'steady':
                    workload_rate = float(params.get('rate', 100.0))
                elif workload_type == 'burst':
                    # For burst workload, use peak_rate as the primary rate
                    workload_rate = float(params.get('peak_rate', params.get('base_rate', 100.0)))
                elif workload_type == 'cyclical':
                    # For cyclical workload, use base_rate + amplitude as peak rate
                    base_rate = float(params.get('base_rate', 100.0))
                    amplitude = float(params.get('amplitude', 0.0))
                    workload_rate = base_rate + amplitude
                elif workload_type == 'random':
                    # For random workload, use max_rate as the peak rate
                    workload_rate = float(params.get('max_rate', 100.0))
                elif workload_type in ['csv', 'custom']:
                    # For CSV/custom workload, try to get rate or use default
                    workload_rate = float(params.get('rate', params.get('base_rate', 100.0)))
                else:
                    # Fallback for other workload types
                    workload_rate = float(params.get('rate', 100.0))
                
                logger.info(f"Calculated workload rate: {workload_rate} for type: {workload_type}")
            
            logger.debug(f"Generating metrics for {len(self.model['resources'])} resources with workload rate {workload_rate}")
            
            # Cache LatencyModel if end-to-end latency is enabled to avoid recreating it in loops
            latency_model = None
            if kwargs.get('end_to_end_latency', False):
                try:
                    from .models.latency import LatencyModel
                    latency_model = LatencyModel(self.config.models.latency)
                    logger.debug("Created cached LatencyModel for end-to-end calculations")
                except Exception as e:
                    logger.debug("Could not create LatencyModel: %s", str(e))
            
            # Generate time-series data for the simulation duration
            time_points = list(range(0, int(duration), int(step_duration)))
            timestamps = [start_time_ts + t for t in time_points]
            
            def create_time_series(values, timestamps):
                return [TimeSeriesDataPoint(timestamp=t, value=v) for t, v in zip(timestamps, values)]
            
            for resource in self.model['resources']:
                resource_id = resource['id']
                resource_type = resource['type']
                
                # Debug logging for resource processing
                logger.info(f"Processing resource: {resource_id} (type: {resource_type})")
                
                # Generate realistic utilization based on workload and resource type
                utilization_data = []
                power_data = []
                memory_data = []
                network_in_data = []
                network_out_data = []
                disk_read_data = []
                disk_write_data = []
                tokens_processed_data = []
                latency_data = []
                
                # Determine region based on resource configuration with better extraction
                region = "us-central1"  # Default
                if 'config' in resource and resource['config']:
                    if 'zone' in resource['config']:
                        zone = resource['config']['zone']
                        if 'us-central1' in str(zone):
                            region = "us-central1"
                        elif 'us-east1' in str(zone):
                            region = "us-east1"
                        elif 'europe-west1' in str(zone):
                            region = "europe-west1"

                for t in time_points:
                    # Base utilization influenced by workload rate
                    base_util = min(0.9, workload_rate / 100.0)  # Cap at 90%

                    # Add some realistic variation
                    variation = 0.1 * math.sin(t / 60.0) + 0.05 * random.random()
                    utilization = max(0.05, min(0.95, base_util + variation))
                    utilization_data.append(round(utilization, 3))

                    # Power consumption based on utilization (watts)
                    if 'compute' in resource_type or 'instance' in resource_type:
                        base_power = 50  # Base power for compute
                        power = base_power + (utilization * 150)  # Scale with utilization
                    elif 'storage' in resource_type or 'bucket' in resource_type:
                        base_power = 10  # Lower power for storage
                        power = base_power + (utilization * 30)
                    else:
                        base_power = 20  # Default for other resources
                        power = base_power + (utilization * 50)

                    power_data.append(round(power, 2))
                    
                    # Debug logging for first few time points
                    if t < 5:  # Only log first 5 time points to avoid spam
                        logger.debug(f"Resource {resource_id} t={t}: util={utilization:.3f}, power={power:.2f}W")
                    
                    # Memory usage (bytes)
                    if 'compute' in resource_type or 'instance' in resource_type:
                        memory_usage = int(utilization * 2 * 1024 * 1024 * 1024)  # Up to 2GB
                    else:
                        memory_usage = int(utilization * 512 * 1024 * 1024)  # Up to 512MB
                    memory_data.append(memory_usage)
                    
                    # Network I/O (bits per second)
                    network_base = workload_rate * 1000  # Base network activity
                    network_in = int(network_base * utilization * (1 + 0.2 * random.random()))
                    network_out = int(network_base * utilization * 0.8 * (1 + 0.2 * random.random()))
                    network_in_data.append(network_in)
                    network_out_data.append(network_out)
                    
                    # Disk I/O (bytes per second)
                    if 'storage' in resource_type or 'bucket' in resource_type:
                        disk_read = int(workload_rate * 10000 * utilization)
                        disk_write = int(workload_rate * 8000 * utilization)
                    else:
                        disk_read = int(workload_rate * 1000 * utilization)
                        disk_write = int(workload_rate * 800 * utilization)
                    disk_read_data.append(disk_read)
                    disk_write_data.append(disk_write)
                    
                    # Tokens processed (requests handled)
                    tokens = int(workload_rate * utilization * (1 + 0.1 * random.random()))
                    tokens_processed_data.append(tokens)
                    
                    # Request latency (milliseconds) - infrastructure processing time
                    base_latency = 50  # Base latency
                    latency_factor = 1 + (utilization * 2)  # Higher utilization = higher latency
                    infrastructure_latency = base_latency * latency_factor * (1 + 0.3 * random.random())
                    
                    # Calculate end-to-end latency if enabled (using cached model)
                    if latency_model is not None:
                        try:
                            # Assign a user region for this calculation
                            user_region = latency_model.assign_user_region()
                            
                            # Calculate end-to-end latency for this resource's region
                            end_to_end_result = latency_model.calculate_end_to_end_latency(
                                user_region=user_region,
                                infrastructure_region=region,
                                infrastructure_latency=infrastructure_latency
                            )
                            
                            # Use the total end-to-end latency
                            final_latency = end_to_end_result["total_end_to_end_latency"]
                            logger.debug(
                                "Resource %s end-to-end latency: user=%s, infra=%s, user_to_infra=%.2fms, infra_internal=%.2fms, total=%.2fms",
                                resource_id, user_region, region, 
                                end_to_end_result["user_to_infra_latency"],
                                infrastructure_latency, final_latency
                            )
                        except Exception as e:
                            logger.debug("Could not calculate end-to-end latency for resource %s: %s", resource_id, str(e))
                            final_latency = infrastructure_latency
                    else:
                        final_latency = infrastructure_latency
                    
                    latency_data.append(round(final_latency, 2))
                
                # Convert data to TimeSeriesDataPoint format
                # TimeSeriesDataPoint is already imported at module level
                
                # timestamps is already pre-calculated outside the loop
                
                # Create resource metrics
                resource_metrics[resource_id] = RawResourceMetrics(
                    utilization=create_time_series(utilization_data, timestamps),
                    power_draw_w=create_time_series(power_data, timestamps),
                    memory_usage_bytes=create_time_series(memory_data, timestamps),
                    network_in_bps=create_time_series(network_in_data, timestamps),
                    network_out_bps=create_time_series(network_out_data, timestamps),
                    disk_read_bps=create_time_series(disk_read_data, timestamps),
                    disk_write_bps=create_time_series(disk_write_data, timestamps),
                    tokens_processed=create_time_series(tokens_processed_data, timestamps),
                    request_latency_ms=create_time_series(latency_data, timestamps),
                    resource_type=resource_type,
                    region=region,
                )
                
                avg_util = sum(utilization_data)/len(utilization_data)
                avg_power = sum(power_data)/len(power_data)
                logger.info(f"Generated metrics for {resource_id}: type={resource_type}, region={region}, avg_util={avg_util:.2f}, avg_power={avg_power:.1f}W")
        
        else:
            # Fallback: create at least one resource with sample data
            logger.warning("No parsed resources found, creating sample resource metrics")
            from .utils.schemas.results import TimeSeriesDataPoint
            
            # Create sample timestamps
            sample_timestamps = [start_time_ts + i for i in range(5)]
            
            def create_sample_time_series(values, timestamps):
                return [TimeSeriesDataPoint(timestamp=t, value=v) for t, v in zip(timestamps, values)]
            
            resource_metrics["sample-resource"] = RawResourceMetrics(
                utilization=create_sample_time_series([0.3, 0.4, 0.5, 0.4, 0.3], sample_timestamps),
                power_draw_w=create_sample_time_series([75.0, 85.0, 95.0, 85.0, 75.0], sample_timestamps),
                memory_usage_bytes=create_sample_time_series([1073741824, 1207959552, 1342177280, 1207959552, 1073741824], sample_timestamps),
                network_in_bps=create_sample_time_series([1000, 1200, 1500, 1200, 1000], sample_timestamps),
                network_out_bps=create_sample_time_series([800, 960, 1200, 960, 800], sample_timestamps),
                disk_read_bps=create_sample_time_series([5000, 6000, 7500, 6000, 5000], sample_timestamps),
                disk_write_bps=create_sample_time_series([4000, 4800, 6000, 4800, 4000], sample_timestamps),
                tokens_processed=create_sample_time_series([10, 12, 15, 12, 10], sample_timestamps),
                request_latency_ms=create_sample_time_series([45.2, 52.1, 68.5, 52.1, 45.2], sample_timestamps),
                resource_type="compute",
                region="us-central1",
            )

        # ------------------------------------------------------------------
        # Generate some sample token traces and system events
        # ------------------------------------------------------------------
        token_traces = []
        system_events = []
        
        # Generate sample token flow for demonstration
        from .utils.schemas.results import TokenTraceEvent
        
        # Check if end-to-end latency is enabled
        end_to_end_enabled = kwargs.get('end_to_end_latency', False)
        
        for i in range(min(10, int(duration / 10))):  # Sample events every 10 seconds
            token_details = {
                "token_id": f"token_{i}",
                "resource": list(resource_metrics.keys())[0] if resource_metrics else "sample-resource",
                "workload_rate": workload_rate if 'workload_rate' in locals() else 10.0
            }
            
            # Add user region assignment if end-to-end latency is enabled
            if end_to_end_enabled:
                try:
                    from .models.latency import LatencyModel
                    # Create a temporary latency model to assign user regions
                    latency_model = LatencyModel(self.config.models.latency)
                    user_region = latency_model.assign_user_region()
                    if user_region:
                        token_details["user_region"] = user_region
                        logger.debug("Assigned user region '%s' to token %s", user_region, f"token_{i}")
                except Exception as e:
                    logger.debug("Could not assign user region to token: %s", str(e))
            
            token_traces.append(TokenTraceEvent(
                timestamp=start_time_ts + (i * 10),
                event_type="token_created",
                details=token_details
            ))
            
            system_events.append({
                "timestamp": start_time_ts + (i * 10),
                "event_type": "resource_allocation",
                "details": {"resource_count": len(resource_metrics)}
            })

        # ------------------------------------------------------------------
        # Run cost estimation if enabled
        # ------------------------------------------------------------------
        cost_data = None
        if kwargs.get('enable_cost_analysis', False):
            try:
                from .cost import InfracostEstimator
                logger.info("Running cost estimation with Infracost")
                
                usage_file = kwargs.get('infracost_usage_file')
                estimator = InfracostEstimator(
                    terraform_dir=self.terraform_dir,
                    usage_file=usage_file
                )
                
                cost_metrics = estimator.estimate_costs()
                if cost_metrics:
                    # Calculate costs for simulation duration
                    cost_metrics = estimator.calculate_duration_costs(duration)
                    cost_data = cost_metrics.model_dump(mode="python")
                    logger.info(f"Cost estimation completed: ${cost_metrics.total_simulation_cost:.4f} for {duration}s simulation")
                else:
                    logger.warning("Cost estimation failed")
                    
            except ImportError as e:
                logger.warning(f"Cost estimation module not available: {e}")
            except Exception as e:
                logger.error(f"Error during cost estimation: {e}", exc_info=True)

        # ------------------------------------------------------------------
        # Assemble the RawSimulationResult object
        # ------------------------------------------------------------------
        raw_result = RawSimulationResult(
            metadata=metadata,
            config_snapshot=config_snapshot,
            resource_metrics=resource_metrics,
            token_traces=token_traces,
            system_events=system_events,
            raw_model_outputs={
                "total_resources": len(resource_metrics),
                "simulation_steps": steps,
                "workload_rate": workload_rate if 'workload_rate' in locals() else 10.0
            },
            cost_data=cost_data,
        )

        # ------------------------------------------------------------------
        # Persist results if the caller requested an output directory
        # ------------------------------------------------------------------
        output_dir = kwargs.get("output_dir")
        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            outfile = output_path / "raw_simulation_results.json"
            with open(outfile, "w") as fp:
                json.dump(raw_result.model_dump(mode="python"), fp, indent=2)
            logger.info("Saved structured simulation results to %s", outfile)

        return raw_result
    
    def _run_orchestrator_simulation(self, **kwargs) -> "RawSimulationResult":
        """Run the detailed Petri net simulation using the orchestrator.
        
        This uses the full discrete event simulation engine for precise modeling
        of token flow, resource contention, and timing.
        """
        logger.info("Starting orchestrator simulation with parameters: %s", kwargs)
        
        try:
            from .orchestrator.core import Orchestrator
            from .terraform.model_builder import ModelBuilder
            
            # Initialize orchestrator with configuration
            debug = kwargs.get('debug', False)
            # Propagate end-to-end latency configuration before model initialization
            try:
                if kwargs.get('end_to_end_latency') and hasattr(self, 'config') and hasattr(self.config, 'models') and hasattr(self.config.models, 'latency'):
                    self.config.models.latency.end_to_end_enabled = True
                    if kwargs.get('user_distribution'):
                        self.config.models.latency.user_distribution = kwargs.get('user_distribution')
                    logger.info("Enabled end-to-end latency in configuration prior to orchestrator setup")
            except Exception as e:
                logger.debug("Could not apply end-to-end latency configuration: %s", str(e))
            orchestrator = Orchestrator(self.config, debug=debug)
            
            # Set up simulation parameters
            duration = float(kwargs.get("duration", 3600.0))
            
            # Create and configure ModelBuilder with resource mapping
            logger.info("Building resource model from loaded infrastructure configuration")
            try:
                tuned_params = kwargs.get('parameters')
                model_builder = self._create_resource_mapping(tuned_params=tuned_params)
                
                # Validate ModelBuilder was created successfully
                if not model_builder:
                    logger.error("Failed to create ModelBuilder - received None")
                    raise ValueError("ModelBuilder creation failed")
                
                if not hasattr(model_builder, 'resource_mapping'):
                    logger.error("ModelBuilder missing resource_mapping attribute")
                    raise ValueError("Invalid ModelBuilder - missing resource_mapping")
                
                # Pass ModelBuilder to orchestrator for scaling
                orchestrator._model_builder = model_builder
                
                # Log successful integration
                resource_count = len(model_builder.resource_mapping) if model_builder.resource_mapping else 0
                logger.info(f"Successfully integrated ModelBuilder with {resource_count} resources")
                
            except Exception as e:
                logger.error(f"Failed to create or integrate ModelBuilder: {e}")
                logger.error("FALLBACK DISABLED - ModelBuilder integration failed")
                # Don't set empty ModelBuilder - let the error propagate
                raise e
            
            # Build the Petri net model from the parsed Terraform
            logger.info("Building Petri net model from loaded infrastructure configuration")
            try:
                self._build_petri_net_model(orchestrator)
                logger.info("Successfully built Petri net model")
            except Exception as e:
                logger.error(f"Failed to build Petri net model: {e}")
                logger.warning("Simulation will continue with simplified model")
                # Continue simulation even if Petri net building fails
            
            # Configure workload if provided on LEAF instance; otherwise adapt from config
            workload_cfg: dict | None = None
            if hasattr(self, 'workload_config') and self.workload_config:
                workload_cfg = self.workload_config
            else:
                # Try to adapt from self.config.simulation.workload
                try:
                    sim = getattr(self.config, 'simulation', None)
                    wl = getattr(sim, 'workload', None)
                    if wl is not None:
                        # Pydantic-like models: prefer dict() or __dict__
                        if hasattr(wl, 'dict'):
                            workload_cfg = wl.dict()
                        elif hasattr(wl, 'model_dump'):
                            workload_cfg = wl.model_dump()
                        elif isinstance(wl, dict):
                            workload_cfg = wl
                        else:
                            # Minimal adaptation: extract type/rate if present as attributes
                            typ = getattr(wl, 'type', 'steady')
                            rate = getattr(getattr(wl, 'params', None), 'rate', None) or getattr(wl, 'rate', None)
                            params = {'rate': float(rate)} if rate is not None else {}
                            workload_cfg = {'type': typ, 'params': params}
                        # Also set on self for event generation parity
                        if workload_cfg:
                            self.workload_config = workload_cfg
                except Exception as e:
                    logger.debug("Could not adapt workload from config: %s", str(e))

            if workload_cfg:
                try:
                    orchestrator.configure_workload(workload_cfg)
                    logger.info("Successfully configured workload")
                except Exception as e:
                    logger.error(f"Failed to configure workload: {e}")
                    logger.warning("Simulation will continue with default workload settings")
            
            # Advance orchestrator state through required phases
            from .orchestrator.models import SimulationState
            orchestrator.state = SimulationState.PARSING
            orchestrator.state = SimulationState.BUILDING
            orchestrator.state = SimulationState.READY
            
            # Generate initial workload events
            logger.info("Generating initial workload events")
            self._generate_workload_events(orchestrator, duration)
            
            # Run the orchestrator simulation
            logger.info("Running Petri net simulation for %.2f seconds", duration)
            orchestrator_result = orchestrator.run_simulation(max_duration=duration)
            
            # Convert orchestrator result to RawSimulationResult format
            return self._convert_orchestrator_result(orchestrator_result, **kwargs)
            
        except Exception as e:
            logger.error("Orchestrator simulation failed: %s", str(e), exc_info=True)
            # Only fallback if explicitly allowed by caller
            allow_fallback = bool(
                kwargs.get("allow_fallback") or kwargs.get("fallback_on_error")
            )
            if allow_fallback:
                logger.warning(
                    "Falling back to heuristic simulation due to orchestrator error (fallback explicitly allowed)"
                )
                fallback_result = self._run_heuristic_simulation(**kwargs)
                fallback_result.raw_model_outputs["simulation_mode"] = "orchestrator_fallback"
                fallback_result.raw_model_outputs["orchestrator_error"] = str(e)
                return fallback_result
            # Default: propagate the error to prevent silent misleading results
            raise
    
    def _build_petri_net_model(self, orchestrator) -> None:
        """Build a Petri net model from the parsed Terraform configuration.
        
        Args:
            orchestrator: The orchestrator instance to configure
        """
        # Prefer the Petri net constructed by ModelBuilder (has proper Source→...→Sink wiring)
        try:
            model_builder = getattr(orchestrator, "_model_builder", None)
            petri_net = getattr(model_builder, "petri_net", None)

            if petri_net is not None and getattr(petri_net, "places", None) and getattr(petri_net, "transitions", None):
                orchestrator.petri_net = petri_net
                try:
                    orchestrator._refresh_resource_state_index()
                except Exception:
                    pass
                logger.info(
                    "Using ModelBuilder-provided Petri net with %d places and %d transitions",
                    len(petri_net.places),
                    len(petri_net.transitions),
                )
                return

            # Fallback: retain previous minimal Petri net construction if ModelBuilder is unavailable
            if not hasattr(self, 'model') or not self.model:
                logger.warning("No model available for Petri net construction")
                return

            from .core.petri_net import PetriNet, Place, Transition, Arc, Token, TokenColor

            fallback_net = PetriNet("terraform_infrastructure_model")

            request_queue = Place("request_queue")
            processing_place = Place("processing")
            completed_place = Place("completed")
            fallback_net.add_place(request_queue)
            fallback_net.add_place(processing_place)
            fallback_net.add_place(completed_place)

            process_transition = Transition("process_request")
            fallback_net.add_transition(process_transition)
            fallback_net.add_arc(Arc("process_in", request_queue.id, process_transition.id, "input"))
            fallback_net.add_arc(Arc("process_out", processing_place.id, process_transition.id, "output"))

            complete_transition = Transition("complete_request")
            fallback_net.add_transition(complete_transition)
            fallback_net.add_arc(Arc("complete_in", processing_place.id, complete_transition.id, "input"))
            fallback_net.add_arc(Arc("complete_out", completed_place.id, complete_transition.id, "output"))

            resource_places = {}
            if self.model.get('resources'):
                for resource in self.model['resources']:
                    resource_id = resource['id']
                    idle_place = Place(f"{resource_id}_idle")
                    busy_place = Place(f"{resource_id}_busy")
                    fallback_net.add_place(idle_place)
                    fallback_net.add_place(busy_place)
                    idle_place.add_token(Token(color=TokenColor.GENERIC, token_id=f"{resource_id}_initial"))
                    resource_places[resource_id] = {'idle': idle_place, 'busy': busy_place}

                for resource_id, places in resource_places.items():
                    alloc_transition = Transition(f"{resource_id}_allocate")
                    fallback_net.add_transition(alloc_transition)
                    fallback_net.add_arc(Arc(f"{resource_id}_alloc_in", places['idle'].id, alloc_transition.id, "input"))
                    fallback_net.add_arc(Arc(f"{resource_id}_alloc_out", places['busy'].id, alloc_transition.id, "output"))

                    dealloc_transition = Transition(f"{resource_id}_deallocate")
                    fallback_net.add_transition(dealloc_transition)
                    fallback_net.add_arc(Arc(f"{resource_id}_dealloc_in", places['busy'].id, dealloc_transition.id, "input"))
                    fallback_net.add_arc(Arc(f"{resource_id}_dealloc_out", places['idle'].id, dealloc_transition.id, "output"))

            orchestrator.petri_net = fallback_net
            try:
                orchestrator._refresh_resource_state_index()
            except Exception:
                pass
            logger.info(
                "Built fallback Petri net with %d places and %d transitions",
                len(fallback_net.places),
                len(fallback_net.transitions),
            )
        except Exception as e:
            logger.error("Failed to build Petri net model: %s", str(e), exc_info=True)
            raise
    
    def _generate_workload_events(self, orchestrator, duration: float) -> None:
        """Generate initial workload events for the simulation.
        
        Args:
            orchestrator: The orchestrator instance
            duration: Simulation duration in seconds
        """
        if not hasattr(self, 'workload_config') or not self.workload_config:
            logger.warning("No workload configuration available, using default steady workload")
            # Use default workload
            workload_type = 'steady'
            params = {'rate': 20.0}
        else:
            workload_type = self.workload_config.get('type', 'steady')
            params = self.workload_config.get('params', {})
        
        try:
            if workload_type == 'steady':
                rate = float(params.get('rate', 20.0))
                # Batch: schedule one event per second carrying 'count' ~= rate
                current_time = 0.0
                event_count = 0
                while current_time < duration:
                    # Determine count for this second (cap to remaining duration fraction)
                    remaining = duration - current_time
                    # If less than a second remains, proportionally scale count
                    effective_window = 1.0 if remaining >= 1.0 else max(remaining, 0.0)
                    count = max(int(round(rate * effective_window)), 0)
                    if count > 0:
                        orchestrator.schedule_event(
                            timestamp=current_time,
                            event_type="token_creation",
                            event_data={
                                "target_place": "global_source_place",
                                "count": count,
                                "token_type": "REQUEST"
                            }
                        )
                        event_count += 1
                    current_time += 1.0
                logger.info("Scheduled %d batched steady workload events over %.2f seconds (batch size≈%s/s)",
                            event_count, duration, rate)
                           
            elif workload_type == 'burst':
                # Normalize params to support both legacy and normalized keys
                base_rate = float(params.get('base_rate', 10.0))
                peak_rate = float(params.get('peak_rate', params.get('burst_rate', 50.0)))
                # Accept synonym 'duration' for burst_duration
                burst_duration = float(params.get('burst_duration', params.get('duration', 60.0)))
                # Accept synonym 'interval' for burst_interval
                if ('burst_interval' in params) or ('interval' in params):
                    burst_interval = float(params.get('burst_interval', params.get('interval', 300.0)))
                else:
                    idle_duration = float(params.get('idle_duration', 240.0))
                    burst_interval = float(burst_duration + idle_duration)

                # Safety: ensure interval > duration
                if burst_interval <= burst_duration:
                    burst_interval = burst_duration * 2.0

                current_time = 0.0
                event_count = 0

                while current_time < duration:
                    # Determine if we're in a burst period within the cycle
                    cycle_time = current_time % burst_interval
                    rate_now = peak_rate if cycle_time < burst_duration else base_rate

                    remaining = duration - current_time
                    effective_window = 1.0 if remaining >= 1.0 else max(remaining, 0.0)
                    count = max(int(round(rate_now * effective_window)), 0)
                    if count > 0:
                        orchestrator.schedule_event(
                            timestamp=current_time,
                            event_type="token_creation",
                            event_data={
                                "target_place": "global_source_place",
                                "count": count,
                                "token_type": "REQUEST"
                            }
                        )
                        event_count += 1
                    current_time += 1.0
                logger.info(
                    "Scheduled %d batched burst workload events over %.2f seconds (base=%.2f/s, peak=%.2f/s, burst=%.2fs, interval=%.2fs)",
                    event_count, duration, base_rate, peak_rate, burst_duration, burst_interval
                )
            elif workload_type == 'csv':
                # CSV workload - load from file and schedule events based on timestamps
                csv_file = params.get('csv_file') or params.get('file')
                base_rate = params.get('base_rate', 1.0)
                
                if not csv_file:
                    raise ValueError("CSV workload requires 'csv_file' parameter")
                
                try:
                    # Parse CSV file using the old format logic
                    pattern = self._parse_workload_from_csv(csv_file)
                    
                    if not pattern:
                        raise ValueError("No valid workload data could be parsed from the CSV file")
                    
                    logger.info("Parsed CSV pattern: %s", pattern[:5])  # Log first 5 entries for debugging
                    
                    # Generate events based on CSV pattern data
                    event_count = 0
                    
                    for time_offset, rate in pattern:
                        if time_offset >= duration:
                            break
                            
                        if rate > 0:
                            # Batch all events at this timestamp into a single token_creation with count
                            orchestrator.schedule_event(
                                timestamp=float(time_offset),
                                event_type="token_creation",
                                event_data={
                                    "target_place": "global_source_place",
                                    "count": int(round(rate)),
                                    "token_type": "REQUEST"
                                }
                            )
                            event_count += 1
                    
                    logger.info("Scheduled %d CSV workload events over %.2f seconds", 
                               event_count, duration)
                    
                except Exception as e:
                    logger.error("Failed to load CSV workload: %s", str(e))
                    raise ValueError(f"CSV workload error: {str(e)}")
            else:
                logger.warning("Unsupported workload type: %s, using steady workload", workload_type)
                # Fall back to steady workload
                rate = 20.0
                current_time = 0.0
                event_count = 0
                while current_time < duration:
                    remaining = duration - current_time
                    effective_window = 1.0 if remaining >= 1.0 else max(remaining, 0.0)
                    count = max(int(round(rate * effective_window)), 0)
                    if count > 0:
                        orchestrator.schedule_event(
                            timestamp=current_time,
                            event_type="token_creation",
                            event_data={
                                "target_place": "global_source_place",
                                "count": count,
                                "token_type": "REQUEST"
                            }
                        )
                        event_count += 1
                    current_time += 1.0
                    event_count += 1
                logger.info("Scheduled %d fallback steady workload events over %.2f seconds", 
                           event_count, duration)
                
        except Exception as e:
            logger.error("Failed to generate workload events: %s", str(e), exc_info=True)
            raise

    def _create_resource_mapping(self, tuned_params: Optional[Dict[str, float]] = None):
        """Create or reuse a ModelBuilder with a valid resource_mapping.

        Preference order:
        1) Reuse mapping built earlier in build_model() to avoid re-parsing.
        2) Otherwise, build using self.terraform_dir (must be set by load_terraform()).
        """

        try:
            # 1) Reuse existing mapping if already built
            if hasattr(self, "resource_mapping") and getattr(self, "resource_mapping", None):
                existing_mapping = self.resource_mapping
                existing_petri = getattr(self, "petri_net", None)

                if self._last_model_builder:
                    logger.info(
                        "Reusing existing ModelBuilder with %d resources from prior build",
                        len(existing_mapping),
                    )
                    # Ensure the builder references stay aligned with the cached mapping/net
                    self._last_model_builder.resource_mapping = existing_mapping
                    if existing_petri is not None:
                        setattr(self._last_model_builder, "petri_net", existing_petri)
                    if hasattr(self._last_model_builder, "pod_resource_requirements"):
                        self._last_model_builder.pod_resource_requirements = dict(self._pod_resource_requirements)
                    if hasattr(self._last_model_builder, "cluster_autoscaler_requirements"):
                        self._last_model_builder.cluster_autoscaler_requirements = dict(
                            self._cluster_autoscaler_requirements
                        )
                    return self._last_model_builder

                class ExistingModelBuilder:
                    def __init__(self, mapping, petri, pod_req, cluster_req):
                        self.resource_mapping = mapping
                        self.petri_net = petri
                        self.pod_resource_requirements = pod_req
                        self.cluster_autoscaler_requirements = cluster_req
                    def reset(self):
                        return None
                    def register_cluster_nodes(self, *args, **kwargs):
                        logger.warning("ExistingModelBuilder stub cannot register cluster nodes dynamically.")
                        return [], []

                logger.info(
                    "Reusing existing resource_mapping with %d resources from prior build",
                    len(existing_mapping),
                )
                return ExistingModelBuilder(
                    existing_mapping,
                    existing_petri,
                    dict(self._pod_resource_requirements),
                    dict(self._cluster_autoscaler_requirements),
                )

            # 2) Build using the explicitly loaded terraform_dir
            if not self.terraform_dir:
                raise ValueError(
                    "Terraform directory is not set. Call load_terraform() before running simulation."
                )

            terraform_path = str(self.terraform_dir)
            model_builder = ModelBuilder(
                terraform_path,
                self.config,
                tuned_params=tuned_params,
                pod_resource_requirements=self._pod_resource_requirements,
                cluster_autoscaler_requirements=self._cluster_autoscaler_requirements,
            )

            logger.info("Building Petri net model and resource mapping from Terraform at %s", terraform_path)
            model_builder.build_model()

            resource_count = len(model_builder.resource_mapping)
            logger.info(
                "ModelBuilder created %d resources from Terraform configuration",
                resource_count,
            )
            self._last_model_builder = model_builder

            cloudrun_resources = [
                res
                for res in model_builder.resource_mapping.values()
                if hasattr(res, "specific_type") and res.specific_type == "cloud-run"
            ]
            if cloudrun_resources:
                logger.info(
                    "Found %d CloudRun resources for autoscaling:",
                    len(cloudrun_resources),
                )
                for resource in cloudrun_resources:
                    logger.info(
                        "  - %s: %s-%s instances, %s concurrency",
                        resource.name,
                        getattr(resource, "min_instances", "?"),
                        getattr(resource, "max_instances", ""),
                        getattr(resource, "concurrency", ""),
                    )

            return model_builder

        except Exception as e:
            logger.error(f"Failed to create ModelBuilder: {e}")
            logger.error("FALLBACK DISABLED - No resource mapping will be available")

            class EmptyModelBuilder:
                def __init__(self):
                    self.resource_mapping = {}
                def reset(self):
                    return None

            return EmptyModelBuilder()
    
    def _create_fallback_resource_mapping(self):
        """DISABLED: Fallback resource mapping creation when ModelBuilder fails."""
        # FALLBACK DISABLED TO EXPOSE REAL ISSUES
        logger.error("Fallback resource mapping is disabled - this should not be called")
        raise RuntimeError("Fallback resource mapping has been disabled to expose the real ModelBuilder issues")
    
    def _convert_orchestrator_result(self, orchestrator_result, **kwargs) -> "RawSimulationResult":
        """Convert orchestrator SimulationResult to RawSimulationResult format.
        
        Args:
            orchestrator_result: Result from orchestrator simulation
            **kwargs: Original simulation parameters
            
        Returns:
            RawSimulationResult compatible with existing interfaces
        """
        from .utils.schemas.results import TimeSeriesDataPoint, TokenTraceEvent
        
        # Extract timing information from orchestrator result (support datetime/float/str)
        def _to_unix_ts(val: Any) -> float:
            try:
                if val is None:
                    return time.time()
                # from datetime import datetime  # Removed redundant import
                if isinstance(val, (int, float)):
                    return float(val)
                if isinstance(val, datetime):
                    return val.timestamp()
                if isinstance(val, str):
                    try:
                        # ISO8601 string
                        return datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        return time.time()
                # Fallback best-effort
                return float(val)  # may raise
            except Exception:
                return time.time()

        # Extract timing information from orchestrator result
        start_time_ts = _to_unix_ts(getattr(orchestrator_result, 'start_time', None))
        
        # Prefer the orchestrator's simulated duration; otherwise fall back to requested duration
        duration = kwargs.get('duration', 3600.0)
        if hasattr(orchestrator_result, 'raw_results') and isinstance(orchestrator_result.raw_results, dict):
            try:
                duration = float(orchestrator_result.raw_results.get('simulation_duration', duration))
            except Exception:
                pass

        end_time_ts = start_time_ts + float(duration)
        
        # Build metadata
        try:
            from . import __version__ as leaf_version
        except Exception:
            leaf_version = "0.1.0"
        
        metadata = SimulationMetadata(
            simulation_id=f"orch_sim_{int(start_time_ts)}",
            start_time=start_time_ts,
            end_time=end_time_ts,
            duration_seconds=float(duration),
            leaf_cloud_version=leaf_version,
            config_hash=None,
        )
        
        # Convert resource metrics from orchestrator output (use REAL time series)
        resource_metrics: dict[str, RawResourceMetrics] = {}

        # Extract utilization series produced by orchestrator analysis adapter
        utilization_by_resource: Dict[str, list] = {}
        replicas_by_resource: Dict[str, list] = {}
        power_by_resource: Dict[str, list] = {}
        # Prefer analysis_results.raw_results.utilization if present
        if hasattr(orchestrator_result, 'analysis_results') and orchestrator_result.analysis_results:
            try:
                raw_section = orchestrator_result.analysis_results.get('raw_results', {}) or {}
                utilization_by_resource = raw_section.get('utilization', {}) or {}
                replicas_by_resource = raw_section.get('replica_count', {}) or {}
            except Exception:
                utilization_by_resource = {}
        # Fallback to orchestrator_result.raw_results
        if not utilization_by_resource and hasattr(orchestrator_result, 'raw_results') and isinstance(orchestrator_result.raw_results, dict):
            utilization_by_resource = orchestrator_result.raw_results.get('utilization', {}) or orchestrator_result.raw_results.get('resource_utilization', {}) or {}
            power_by_resource = orchestrator_result.raw_results.get('power_watts', {}) or {}
            replicas_by_resource = orchestrator_result.raw_results.get('replica_count', {}) or replicas_by_resource

        if not utilization_by_resource:
            logger.info("Orchestrator result contained no utilization series (analysis_results/raw_results).")

        # Helper to resolve resource metadata (type, region)
        def _resolve_resource_info(res_id: str) -> tuple[str, str]:
            # Prefer rich objects from ModelBuilder mapping if available
            try:
                if hasattr(self, 'resource_mapping') and self.resource_mapping:
                    resource_obj = self.resource_mapping.get(res_id)
                    if resource_obj is not None:
                        rtype = (
                            getattr(resource_obj, 'resource_type', None)
                            or getattr(resource_obj, 'specific_type', None)
                            or type(resource_obj).__name__.lower()
                        )
                        region_val = getattr(resource_obj, 'region', None) or "us-central1"
                        return str(rtype), str(region_val)
            except Exception:
                pass

            # Fallback to parsed Terraform model for metadata
            try:
                if hasattr(self, 'model') and self.model and 'resources' in self.model:
                    for res in self.model['resources']:
                        if res.get('id') == res_id:
                            rtype = res.get('type', 'compute')
                            region_val = 'us-central1'
                            cfg = res.get('config') or {}
                            zone = str(cfg.get('zone', '')).lower()
                            if 'us-east1' in zone:
                                region_val = 'us-east1'
                            elif 'europe-west1' in zone:
                                region_val = 'europe-west1'
                            logger.debug("_convert_orchestrator_result: Using Terraform model metadata for %s (type=%s, region=%s)", res_id, rtype, region_val)
                            return rtype, region_val
            except Exception:
                pass

            logger.info("_convert_orchestrator_result: Falling back to default metadata for %s (type=compute, region=us-central1)", res_id)
            return 'compute', 'us-central1'

        # Attempt to build power model(s) for physical metrics reconstruction
        energy_model = None
        carbon_model = None
        try:
            # Instantiate models only if configured
            if hasattr(self.config, 'models') and hasattr(self.config.models, 'energy'):
                from .models.energy import EnergyModel
                energy_model = EnergyModel(self.config.models.energy)
            if hasattr(self.config, 'models') and hasattr(self.config.models, 'carbon'):
                from .models.carbon import CarbonModel
                carbon_model = CarbonModel(self.config.models.carbon)
        except Exception as e:
            logger.debug("Energy/Carbon models not available for conversion: %s", str(e))

        # Build RawResourceMetrics using orchestrator-provided utilization time series
        for res_id, series in utilization_by_resource.items():
            try:
                rtype, region = _resolve_resource_info(res_id)
                # Convert simulation-time to absolute timestamps
                utilization_ts = [
                    TimeSeriesDataPoint(timestamp=start_time_ts + float(pt.get('time', 0.0)),
                                         value=float(pt.get('value', 0.0)))
                    for pt in series if isinstance(pt, dict)
                ]

                # Populate power time series: use orchestrator-provided raw power if available; else reconstruct
                power_ts = []
                if power_by_resource.get(res_id):
                    logger.debug("_convert_orchestrator_result: Using orchestrator-provided power series for %s", res_id)
                    for p in power_by_resource[res_id]:
                        try:
                            power_ts.append(TimeSeriesDataPoint(timestamp=start_time_ts + float(p.get('time', 0.0)), value=float(p.get('value', 0.0))))
                        except Exception:
                            continue
                elif energy_model is not None and hasattr(self, 'resource_mapping') and self.resource_mapping:
                    resource_obj = self.resource_mapping.get(res_id)
                    if resource_obj is not None and utilization_ts:
                        logger.info("_convert_orchestrator_result: Reconstructing power via energy model for %s", res_id)
                        for pt in utilization_ts:
                            try:
                                sim_t = float(pt.timestamp - start_time_ts)
                                if hasattr(energy_model, 'calculate_power') and callable(getattr(energy_model, 'calculate_power')):
                                    pw = energy_model.calculate_power(resource=resource_obj, utilization=float(pt.value), timestamp=sim_t)
                                else:
                                    # Fallback to kW curve and convert to W
                                    res_type = getattr(resource_obj, 'specific_type', getattr(resource_obj, 'resource_type', type(resource_obj).__name__.lower()))
                                    capacity = float(getattr(resource_obj, 'capacity', 1.0))
                                    pw_kw = energy_model.calculate_resource_energy(utilization=float(pt.value), resource_type=res_type, capacity=capacity)
                                    pw = pw_kw * 1000.0
                                power_ts.append(TimeSeriesDataPoint(timestamp=pt.timestamp, value=float(pw)))
                            except Exception as e:
                                logger.debug("Power calc failed for %s @ %s: %s", res_id, pt.timestamp, str(e))

                replica_ts = [
                    TimeSeriesDataPoint(
                        timestamp=start_time_ts + float(pt.get("time", 0.0)),
                        value=float(pt.get("value", 0.0)),
                    )
                    for pt in replicas_by_resource.get(res_id, [])
                    if isinstance(pt, dict)
                ]

                resource_metrics[res_id] = RawResourceMetrics(
                    utilization=utilization_ts,
                    power_draw_w=power_ts,
                    memory_usage_bytes=[],
                    network_in_bps=[],
                    network_out_bps=[],
                    disk_read_bps=[],
                    disk_write_bps=[],
                    tokens_processed=[],
                    request_latency_ms=[],
                    replica_count=replica_ts,
                    resource_type=rtype,
                    region=region,
                )
            except Exception as e:
                logger.debug("Failed to convert utilization for resource %s: %s", res_id, str(e))
                
        # If nothing was produced, keep a minimal, explicit fallback without synthesizing heuristics
        if not resource_metrics:
            logger.warning("No orchestrator time series available; emitting empty metrics with metadata only")
            # Try to at least enumerate known resources
            if hasattr(self, 'resource_mapping') and self.resource_mapping:
                for res_id in self.resource_mapping.keys():
                    rtype, region = _resolve_resource_info(res_id)
                    resource_metrics[res_id] = RawResourceMetrics(
                        utilization=[],
                        power_draw_w=[],
                        memory_usage_bytes=[],
                        network_in_bps=[],
                        network_out_bps=[],
                        disk_read_bps=[],
                        disk_write_bps=[],
                        tokens_processed=[],
                        request_latency_ms=[],
                        resource_type=rtype,
                        region=region,
                    )
            elif hasattr(self, 'model') and self.model and 'resources' in self.model:
                for res in self.model['resources']:
                    rtype, region = _resolve_resource_info(res.get('id'))
                    resource_metrics[res.get('id')] = RawResourceMetrics(
                        utilization=[],
                        power_draw_w=[],
                        memory_usage_bytes=[],
                        network_in_bps=[],
                        network_out_bps=[],
                        disk_read_bps=[],
                        disk_write_bps=[],
                        tokens_processed=[],
                        request_latency_ms=[],
                        resource_type=rtype,
                        region=region,
            )
        
        # Convert token traces from orchestrator
        token_traces = []
        # Accept both singular and plural attribute names
        raw_token_logs = []
        if hasattr(orchestrator_result, 'token_flow_logs') and getattr(orchestrator_result, 'token_flow_logs'):
            raw_token_logs = getattr(orchestrator_result, 'token_flow_logs')
        elif hasattr(orchestrator_result, 'token_flow_log') and getattr(orchestrator_result, 'token_flow_log'):
            raw_token_logs = getattr(orchestrator_result, 'token_flow_log')

        if raw_token_logs:
            logger.info("_convert_orchestrator_result: found %d token flow entries", len(raw_token_logs))
            for log_entry in raw_token_logs:  # include all entries to allow latency derivation
                try:
                    # Handle different log entry formats
                    if hasattr(log_entry, 'timestamp'):
                        timestamp = log_entry.timestamp
                        event_type = getattr(log_entry, 'event_type', 'token_event')
                        details = getattr(log_entry, 'details', {})
                    elif isinstance(log_entry, dict):
                        timestamp = log_entry.get('timestamp', start_time_ts)
                        event_type = log_entry.get('event_type', 'token_event')
                        details = log_entry.get('details', log_entry)
                    else:
                        # Fallback for unknown format
                        timestamp = start_time_ts
                        event_type = 'token_event'
                        details = {'raw_entry': str(log_entry)}
                    
                    token_traces.append(TokenTraceEvent(
                        timestamp=timestamp,
                        event_type=event_type,
                        details=details
                    ))
                except Exception as e:
                    logger.debug("Error converting token trace entry: %s", str(e))
        else:
            logger.info("_convert_orchestrator_result: no token flow entries present on orchestrator_result")
        
        # Calculate per-resource latency from token traces
        if token_traces:
            logger.info("Calculating per-resource latency from %d token traces", len(token_traces))
            token_histories = {}
            
            # Group events by token
            for trace in token_traces:
                details = trace.details
                token_id = details.get('token_id')
                if not token_id:
                    # Try to find token id in other fields or nested token dict
                    token = details.get('token')
                    if isinstance(token, dict):
                        token_id = token.get('id')
                    elif token:
                        token_id = str(token)
                
                if not token_id:
                    continue
                    
                if token_id not in token_histories:
                    token_histories[token_id] = []
                token_histories[token_id].append(trace)

            # Process histories to find latencies
            for token_id, history in token_histories.items():
                # Sort by timestamp
                history.sort(key=lambda x: x.timestamp)
                
                resource_starts = {}
                
                for event in history:
                    evt_type = event.event_type
                    details = event.details
                    res_id = details.get('resource_id') or details.get('resource') or details.get('place')
                    
                    if not res_id:
                        continue
                        
                    # Normalize resource ID (remove 'ResourceState_' prefix if present)
                    if str(res_id).startswith("ResourceState_"):
                        res_id = str(res_id)[14:]
                    
                    if res_id not in resource_metrics:
                        continue

                    if evt_type in ('token_produced', 'token_arrived', 'place_entered'):
                        resource_starts[res_id] = event.timestamp
                    elif evt_type in ('token_consumed', 'token_departed', 'place_left'):
                        if res_id in resource_starts:
                            start_ts = resource_starts.pop(res_id)
                            duration_ms = (event.timestamp - start_ts) * 1000.0
                            if duration_ms >= 0:
                                resource_metrics[res_id].request_latency_ms.append(
                                    TimeSeriesDataPoint(
                                        timestamp=event.timestamp,
                                        value=duration_ms
                                    )
                                )
        
        # Create system events from orchestrator data
        system_events = [
            {
                "timestamp": start_time_ts,
                "event_type": "orchestrator_simulation_start",
                "details": {
                    "mode": "detailed",
                    "resources": len(resource_metrics),
                    "petri_net_enabled": True
                }
            }
        ]
        
        # Add orchestrator-specific events
        if hasattr(orchestrator_result, 'errors') and orchestrator_result.errors:
            for error in orchestrator_result.errors[:5]:  # Limit to first 5 errors
                system_events.append({
                    "timestamp": error.get('time', start_time_ts),
                    "event_type": "orchestrator_error",
                    "details": {
                        "message": error.get('message', 'Unknown error'),
                        "event_type": error.get('event_type', 'unknown')
                    }
                })
        
        system_events.append({
            "timestamp": end_time_ts,
            "event_type": "orchestrator_simulation_end",
            "details": {
                "duration": duration,
                "token_events": len(token_traces),
                "errors": len(getattr(orchestrator_result, 'errors', []))
            }
        })
        
        # Build config snapshot
        config_snapshot = {
            "workload": getattr(self, "workload_config", {}),
            "parameters": kwargs,
            "terraform_dir": str(self.terraform_dir) if self.terraform_dir else None,
            "simulation_mode": "orchestrator"
        }
        
        # Extract orchestrator-specific metrics (summary only; no synthesized series)
        orchestrator_metrics = {
            "total_resources": len(resource_metrics),
            "simulation_mode": "orchestrator",
            "orchestrator_events": len(token_traces),
        }
        # Include token statistics if present in raw_results
        try:
            if hasattr(orchestrator_result, 'raw_results') and isinstance(orchestrator_result.raw_results, dict):
                token_stats = orchestrator_result.raw_results.get('token_stats', {}) or {}
            orchestrator_metrics.update({
                "tokens_created": token_stats.get('total_created', 0),
                "tokens_completed": token_stats.get('total_completed', 0),
                    "tokens_active": token_stats.get('active', 0),
            })
        except Exception:
            pass
        
        # Capture post-simulation analysis from orchestrator if available
        analysis_results: dict[str, Any] = {}
        try:
            if hasattr(orchestrator_result, 'analysis_results') and isinstance(orchestrator_result.analysis_results, dict):
                analysis_results = dict(orchestrator_result.analysis_results)
            
            # Ensure latency analysis exists and populate resource_latency if needed
            if 'latency' in analysis_results:
                latency_data = analysis_results['latency']
                
                # If resource_latency is missing or empty, try to populate from by_resource
                if not latency_data.get('resource_latency'):
                    by_resource = latency_data.get('by_resource', {})
                    if by_resource:
                        # Extract average latency for each resource
                        resource_latency = {
                            res_id: stats.get('average', 0.0)
                            for res_id, stats in by_resource.items()
                            if isinstance(stats, dict)
                        }
                        latency_data['resource_latency'] = resource_latency
                        logger.debug(
                            "Populated resource_latency from by_resource: %d resources",
                            len(resource_latency)
                        )

        except Exception as e:
            logger.warning("Error updating analysis results with calculated latency: %s", str(e))
            if not analysis_results:
                analysis_results = {}

        # Optionally run cost estimation in orchestrator mode
        cost_data = None
        try:
            if kwargs.get('enable_cost_analysis', False):
                from .cost import InfracostEstimator
                logger.info("Running cost estimation with Infracost for orchestrator result")
                usage_file = kwargs.get('infracost_usage_file')
                estimator = InfracostEstimator(
                    terraform_dir=self.terraform_dir,
                    usage_file=usage_file,
                )
                cost_metrics = estimator.estimate_costs()
                if cost_metrics:
                    cost_metrics = estimator.calculate_duration_costs(float(duration))
                    cost_data = cost_metrics.model_dump(mode="python") if cost_metrics else None
                    if cost_metrics:
                        logger.info(
                            f"Cost estimation completed: ${cost_metrics.total_simulation_cost:.4f} for {duration}s simulation"
                        )
                else:
                    logger.warning("Cost estimation failed (no metrics returned)")
        except ImportError as e:
            logger.warning(f"Cost estimation module not available: {e}")
        except Exception as e:
            logger.error(f"Error during cost estimation (orchestrator): {e}", exc_info=True)

        # Assemble final result
        raw_result = RawSimulationResult(
            metadata=metadata,
            config_snapshot=config_snapshot,
            resource_metrics=resource_metrics,
            token_traces=token_traces,
            system_events=system_events,
            raw_model_outputs=orchestrator_metrics,
            analysis_results=analysis_results,
            cost_data=cost_data,
        )
        
        logger.info("Converted orchestrator result: %d resources, %d token events, %.2f second duration",
                   len(resource_metrics), len(token_traces), duration)
        
        return raw_result
    
    def get_intermediate_model(self) -> Dict[str, Any]:
        """Get the intermediate model representation.
        
        Returns:
            A dictionary containing the intermediate model.
            
        Note:
            This method is used by the intermediate model export functionality.
        """
        if self.model is None:
            self.build_model()
        return self.model

    def get_metrics_summary(self, result: "RawSimulationResult", detailed_latency: bool = False) -> str:
        """
        Generate a summary of the metrics from a simulation result.

        Args:
            result: The simulation result to summarize
            detailed_latency: If True, includes detailed latency information.

        Returns:
            str: Formatted string containing the simulation metrics summary.
        """
        if not result:
            return "No simulation results available."

        # Build the summary header
        summary_lines = [
            "Simulation Results Summary:",
            "=" * 50,
            f"Simulation ID: {result.metadata.simulation_id}",
            f"Start Time: {datetime.fromtimestamp(result.metadata.start_time).isoformat()}",
            f"End Time: {datetime.fromtimestamp(result.metadata.end_time).isoformat()}",
            f"Duration: {result.metadata.duration_seconds:.2f} seconds",
            ""
        ]

        # Calculate aggregated metrics from resource metrics
        if result.resource_metrics:
            total_resources = len(result.resource_metrics)
            summary_lines.append(f"Total Resources: {total_resources}")
            
            # Calculate average utilization across all resources
            total_utilization = 0
            total_power = 0
            total_memory = 0
            total_tokens = 0
            latency_values = []
            
            for resource_id, metrics in result.resource_metrics.items():
                if metrics.utilization:
                    avg_util = sum(point.value for point in metrics.utilization) / len(metrics.utilization)
                    total_utilization += avg_util
                
                if metrics.power_draw_w:
                    avg_power = sum(point.value for point in metrics.power_draw_w) / len(metrics.power_draw_w)
                    total_power += avg_power
                
                if metrics.memory_usage_bytes:
                    avg_memory = sum(point.value for point in metrics.memory_usage_bytes) / len(metrics.memory_usage_bytes)
                    total_memory += avg_memory
                
                if metrics.tokens_processed:
                    total_tokens_resource = sum(point.value for point in metrics.tokens_processed)
                    total_tokens += total_tokens_resource
                
                if metrics.request_latency_ms:
                    latency_values.extend([point.value for point in metrics.request_latency_ms])
            
            # Count completed tokens from token traces/logs
            completed_tokens_count = 0
            try:
                raw_token_events = []
                if hasattr(result, 'token_traces') and result.token_traces:
                    raw_token_events = result.token_traces
                elif hasattr(result, 'token_flow_logs') and result.token_flow_logs:
                    raw_token_events = result.token_flow_logs
                elif hasattr(result, 'token_flow_log') and result.token_flow_log:
                    raw_token_events = result.token_flow_log
                for ev in raw_token_events:
                    if hasattr(ev, 'event_type'):
                        if getattr(ev, 'event_type') == 'token_completed':
                            completed_tokens_count += 1
                    elif isinstance(ev, dict):
                        if ev.get('event_type') == 'token_completed' or ev.get('event') == 'token_completed':
                            completed_tokens_count += 1
            except Exception:
                completed_tokens_count = 0

            # Display aggregated metrics
            summary_lines.extend([
                "Aggregated Metrics:",
                "-" * 20,
                f"  Average Utilization: {(total_utilization / total_resources * 100):.2f}%" if total_resources > 0 else "  Average Utilization: N/A",
                f"  Total Power Draw: {total_power:.2f} W",
                f"  Total Memory Usage: {total_memory / (1024**3):.2f} GB",
                f"  Completed Tokens: {completed_tokens_count}",
                ""
            ])

        # Add energy metrics (prefer orchestrator analysis results if present)
            summary_lines.append("Energy Metrics:")
            summary_lines.append("-" * 15)
            
        energy_analysis = None
        try:
            if isinstance(getattr(result, 'analysis_results', None), dict):
                energy_analysis = result.analysis_results.get('energy')
        except Exception:
            energy_analysis = None

        if isinstance(energy_analysis, dict) and energy_analysis:
            total_kwh = float(energy_analysis.get('total_kwh') or energy_analysis.get('total_energy_kwh') or 0.0)
            summary_lines.append(f"  Total Energy: {total_kwh:.4f} kWh")
            try:
                duration_seconds = float(result.metadata.duration_seconds)
                if duration_seconds > 0:
                    per_hour_kwh = total_kwh * (3600.0 / duration_seconds)
                    summary_lines.append(f"  Per-Hour Energy: {per_hour_kwh:.4f} kWh/h")
            except Exception:
                pass

            resource_energy = energy_analysis.get('resource_energy') or {}
            if isinstance(resource_energy, dict) and resource_energy:
                summary_lines.append("  Resource-wise Energy (kWh):")
                # Sort descending by energy
                for rid, ekwh in sorted(resource_energy.items(), key=lambda x: float(x[1]), reverse=True):
                    try:
                        summary_lines.append(f"    {rid}: {float(ekwh):.4f} kWh")
                    except Exception:
                        continue
            summary_lines.append("")
        else:
            # Fallback: compute energy per resource from power_draw if analysis not available
            if result.resource_metrics:
                total_energy_kwh = 0.0
                energy_by_resource: list[tuple[str, float]] = []
                for resource_id, metrics in result.resource_metrics.items():
                    if metrics.power_draw_w:
                        avg_power = sum(point.value for point in metrics.power_draw_w) / len(metrics.power_draw_w)
                        duration_hours = result.metadata.duration_seconds / 3600
                        energy_kwh = (avg_power * duration_hours) / 1000
                        total_energy_kwh += energy_kwh
                        energy_by_resource.append((resource_id, energy_kwh))
                summary_lines.append(f"  Total Energy: {total_energy_kwh:.4f} kWh")
                try:
                    duration_seconds = float(result.metadata.duration_seconds)
                    if duration_seconds > 0:
                        per_hour_kwh = total_energy_kwh * (3600.0 / duration_seconds)
                        summary_lines.append(f"  Per-Hour Energy: {per_hour_kwh:.4f} kWh/h")
                except Exception:
                    pass
                if energy_by_resource:
                    summary_lines.append("  Resource-wise Energy (kWh):")
                    for rid, ekwh in sorted(energy_by_resource, key=lambda x: x[1], reverse=True):
                        summary_lines.append(f"    {rid}: {ekwh:.4f} kWh")
                summary_lines.append("")

        # Add carbon metrics (calculated from energy consumption)
        if result.resource_metrics:
            summary_lines.append("Carbon Metrics:")
            summary_lines.append("-" * 15)

            # Carbon intensity factors (kg CO2e/kWh) by region (static defaults)
            carbon_factors = {
                "us-central1": 0.2152373529,
                "us-east1": 0.379,
                "europe-west1": 0.088,
                "default": 0.450
            }

            total_carbon_kg = 0
            carbon_by_region = {}
            carbon_by_resource = []  # list of (resource_id, carbon_kg)
            
            for resource_id, metrics in result.resource_metrics.items():
                if metrics.power_draw_w:
                    # Calculate energy consumption
                    avg_power = sum(point.value for point in metrics.power_draw_w) / len(metrics.power_draw_w)
                    duration_hours = result.metadata.duration_seconds / 3600
                    energy_kwh = (avg_power * duration_hours) / 1000

                    # Get carbon factor for region
                    region = metrics.region
                    carbon_factor = carbon_factors.get(region, carbon_factors.get("default", 0.45))
                    carbon_kg = energy_kwh * carbon_factor
                    total_carbon_kg += carbon_kg
                    carbon_by_resource.append((resource_id, carbon_kg))
                    
                    # Group by region
                    if region not in carbon_by_region:
                        carbon_by_region[region] = 0
                    carbon_by_region[region] += carbon_kg
            
            summary_lines.append(f"  Total Carbon: {total_carbon_kg:.6f} kg CO2e")
            try:
                duration_seconds = float(result.metadata.duration_seconds)
                if duration_seconds > 0:
                    per_hour_kg = total_carbon_kg * (3600.0 / duration_seconds)
                    summary_lines.append(f"  Per-Hour Carbon: {per_hour_kg:.6f} kg CO2e/h")
            except Exception:
                pass
            if carbon_by_region:
                summary_lines.append("  By Region:")
                for region, carbon in carbon_by_region.items():
                    summary_lines.append(f"    {region}: {carbon:.6f} kg CO2e")
            # Also show per-resource carbon (top 10 by emissions)
            if carbon_by_resource:
                summary_lines.append("  Resource-wise Carbon (kg CO2e):")
                for rid, ckg in sorted(carbon_by_resource, key=lambda x: x[1], reverse=True)[:10]:
                    try:
                        summary_lines.append(f"    {rid}: {float(ckg):.6f} kg CO2e")
                    except Exception:
                        continue
            summary_lines.append("")

        # Add latency metrics
        if latency_values:
            summary_lines.append("Latency Metrics:")
            summary_lines.append("-" * 15)
            
            latency_values.sort()
            n = len(latency_values)
            
            avg_latency = sum(latency_values) / n
            min_latency = min(latency_values)
            max_latency = max(latency_values)
            
            # Calculate percentiles
            p50 = latency_values[int(n * 0.5)]
            p95 = latency_values[int(n * 0.95)]
            p99 = latency_values[int(n * 0.99)]
            
            summary_lines.extend([
                f"  Average Latency: {avg_latency:.2f} ms",
                f"  Min Latency: {min_latency:.2f} ms",
                f"  Max Latency: {max_latency:.2f} ms",
                f"  P50 Latency: {p50:.2f} ms",
                f"  P95 Latency: {p95:.2f} ms",
                f"  P99 Latency: {p99:.2f} ms",
            ])
            
            if detailed_latency:
                summary_lines.append(f"  Total Latency Samples: {n}")
            
            summary_lines.append("")

        # Add cost metrics if available
        if result.cost_data:
            summary_lines.append("Cost Metrics:")
            summary_lines.append("-" * 12)
            
            cost_data = result.cost_data
            if isinstance(cost_data, dict):
                if 'total_simulation_cost' in cost_data:
                    summary_lines.append(f"  Simulation Cost: ${cost_data['total_simulation_cost']:.4f}")
                if 'total_monthly_cost' in cost_data:
                    summary_lines.append(f"  Monthly Cost: ${cost_data['total_monthly_cost']:.2f}")
                if 'total_hourly_cost' in cost_data:
                    summary_lines.append(f"  Hourly Cost: ${cost_data['total_hourly_cost']:.4f}")
                
                if 'by_resource' in cost_data:
                    summary_lines.append("  By Resource Type:")
                    for resource_type, cost in cost_data['by_resource'].items():
                        summary_lines.append(f"    {resource_type}: ${cost:.4f}")
            
            summary_lines.append("")

        # Add system information
        summary_lines.extend([
            "System Information:",
            "-" * 18,
            f"  Total Token Events: {len(result.token_traces)}",
            f"  Total System Events: {len(result.system_events)}",
            f"  Raw Model Outputs: {len(result.raw_model_outputs)} entries",
            ""
        ])

        return "\n".join(summary_lines)

# For backward compatibility with existing code
LEAF = LEAFCloud
