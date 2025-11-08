import os
import json
import yaml
import logging
import datetime
from typing import Dict, List, Optional, Any, Tuple, Union, cast
from enum import Enum
from .orchestrator import Orchestrator, SimulationState
from .config import LEAFCloudConfig
from .exceptions import (
    LeafCloudError, ConfigError, TerraformParseError, ModelBuildError, 
    WorkloadError, SimulationError, ExportError, DiagramError, FileOperationError
)

from .core.petri_net import PetriNet, Token, TokenColor, Place, Transition, Arc
from .core.workload import Workload, WorkloadMix

from .models.latency import LatencyModel
from .models.energy import EnergyModel
from .models.carbon import CarbonModel
from .models.scaling import ScalingModel
from .models.aggregation import MetricAggregator
from .terraform.parser import TerraformParser
from .terraform.generator import TerraformGenerator
from .terraform.model_builder import ModelBuilder
from .export.class_diagram import generate_class_diagram, model_builder_to_class_diagram
from .export.deployment_diagram import generate_deployment_diagram, generate_data_flow_diagram
import argparse


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class Layer(Enum):
    """Enum representing the layers in the LEAF-Cloud framework"""
    ABSTRACT = 1    # Abstract GCP Resource Types
    SPECIALIZED = 2 # Specialized GCP Components
    WORKLOAD = 3    # Workload Layer

class LEAFCloud:
    """
    LEAF-Cloud framework main class.
    
    This class serves as the main integration point for all modules in the LEAF-Cloud
    framework and manages the layered modeling structure consisting of:
    - Abstract GCP Resources Layer
    - Specialized GCP Components Layer
    - Workload Layer
    
    It provides a high-level API for model creation, configuration, simulation,
    and results analysis.
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the LEAF-Cloud framework.
        
        Args:
            config_path: Path to configuration file
        """
        # Initialize configuration
        self.config_path = config_path
        self.config = {}
        if config_path:
            self._load_config(config_path)
        
        # Initialize orchestrator FIRST to get the fully resolved config
        self.orchestrator = Orchestrator(config_path=self.config_path)
        
        # Initialize layers with type annotations
        self.layers: Dict[Layer, Union[Dict, List]] = {
            Layer.ABSTRACT: {},    # Abstract resources by type
            Layer.SPECIALIZED: {}, # Specialized components by ID
            Layer.WORKLOAD: []     # Workload definitions
        }
        
        # Track models and metrics
        self.petri_net: Optional[PetriNet] = None
        self.model_builder: Optional[ModelBuilder] = None
        self.terraform_data : Optional[Dict[str, Any]] = None
        self.results: Union[Dict[str, Any], List[Dict[str, Any]]] = {}
        self.visualizations = {}
        
        # Get the correct config path for env-specific settings from the orchestrator's loaded config
        env_config_for_models = self.orchestrator.config.env_config_path
        
        # Initialize metrics models using the correctly resolved config path
        self.metrics = {
            'latency': LatencyModel(config_path=env_config_for_models),
            'energy': EnergyModel(config_path=env_config_for_models),
            'carbon': CarbonModel(config_path=env_config_for_models),
            'scaling': ScalingModel(config_path=env_config_for_models)
        }
        
        # CRITICAL: Assign these created, configured models to the orchestrator instance
        # This ensures the orchestrator uses the same, correctly configured models.
        self.orchestrator.latency_model = self.metrics.get('latency')
        self.orchestrator.energy_model = self.metrics.get('energy')
        self.orchestrator.carbon_model = self.metrics.get('carbon')
        self.orchestrator.scaling_model = self.metrics.get('scaling')
        
        # Initialize metrics aggregator
        self.aggregator = MetricAggregator()
        
        logger.info("LEAF-Cloud framework initialized")
    
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """
        Load configuration from file.
        
        Args:
            config_path: Path to configuration file
            
        Returns:
            Configuration dictionary
        """
        try:
            with open(config_path, 'r') as f:
                if config_path.endswith('.json'):
                    config_data = json.load(f)
                else:
                    config_data = yaml.safe_load(f)
                
                # Ensure config_data is a dictionary
                if config_data is None:
                    config_data = {}
                elif not isinstance(config_data, dict):
                    logger.warning(f"Configuration from {config_path} is not a dictionary, converting to empty dict")
                    config_data = {}
                
                self.config = config_data
            
            logger.info(f"Configuration loaded from {config_path}")
            return self.config
        
        except FileNotFoundError as e:
            logger.error(f"Configuration file not found: {config_path} - {str(e)}")
            self.config = {}
            raise ConfigError(f"Configuration file not found: {config_path}", original_exception=e)
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            logger.error(f"Error decoding configuration file: {config_path} - {str(e)}")
            self.config = {}
            raise ConfigError(f"Error decoding configuration file {config_path}: {e}", original_exception=e)
        except Exception as e: # Catch any other unexpected errors during loading
            logger.error(f"Unexpected error loading configuration from {config_path}: {str(e)}", exc_info=True)
            self.config = {}
            raise ConfigError(f"Unexpected error loading configuration from {config_path}: {e}", original_exception=e)
    
    def load_terraform(self, terraform_path: str, var_files: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Load and parse Terraform configuration from a directory or plan JSON file.
        
        Args:
            terraform_path: Path to Terraform directory or plan JSON file
            var_files: List of Terraform variable files (only used for directory parsing)
            
        Returns:
            Parsed Terraform data
        """
        logger.info(f"Loading Terraform configuration from {terraform_path}")
        try:
            self.terraform_data = self.orchestrator.parse_terraform(terraform_path, var_files)
            # Rebuild layers based on terraform data
            self._populate_layers_from_terraform()
            return self.terraform_data
        except (ConfigError, TerraformParseError, ModelBuildError) as e:
            logger.error(f"Error during Terraform loading or initial model population: {str(e)}")
            raise # Re-raise the specific error for upstream handling
        except LeafCloudError as e: # Catch any other leafcloud specific errors
            logger.error(f"A LEAF-Cloud error occurred during terraform loading: {str(e)}", exc_info=True)
            raise
        except Exception as e: # Catch unexpected errors
            logger.error(f"An unexpected error occurred during terraform loading: {str(e)}", exc_info=True)
            raise TerraformParseError(f"An unexpected error occurred while loading Terraform: {str(e)}", original_exception=e)
    
    def _populate_layers_from_terraform(self) -> None:
        """
        Populate the layered structure from Terraform data.
        """
        if not self.terraform_data:
            logger.warning("No Terraform data available to populate layers")
            return
        
        try:
            # Reset layers
            self.layers = {
                Layer.ABSTRACT: {},
                Layer.SPECIALIZED: {},
                Layer.WORKLOAD: []
            }
            
            # Extract resources from Terraform data
            resources = self.terraform_data.get('resources', {})
            
            # Map resources to appropriate layers
            for resource_id, resource_data in resources.items():
                resource_type = resource_data.get('type', '')
                
                # Categorize resource into abstract layer
                if 'compute' in resource_type:
                    abstract_type = 'compute'
                elif 'storage' in resource_type:
                    abstract_type = 'storage'
                elif 'network' in resource_type:
                    abstract_type = 'network'
                elif 'iam' in resource_type or 'security' in resource_type:
                    abstract_type = 'security'
                else:
                    abstract_type = 'other'
                
                # Cast to Dict to help the type checker understand this is a dictionary
                abstract_layer = cast(Dict[str, List[str]], self.layers[Layer.ABSTRACT])
                if abstract_type not in abstract_layer:
                    abstract_layer[abstract_type] = []
                
                # Use the same cast for consistency
                abstract_layer = cast(Dict[str, List[str]], self.layers[Layer.ABSTRACT])
                abstract_layer[abstract_type].append(resource_id)
                
                # Add to specialized layer with proper type casting
                specialized_layer = cast(Dict[str, Dict], self.layers[Layer.SPECIALIZED])
                specialized_layer[resource_id] = resource_data
            
            logger.info(f"Populated layers with {len(self.layers[Layer.SPECIALIZED])} resources")
            
        except KeyError as e:
            logger.error(f"Error populating layers due to missing key: {str(e)}", exc_info=True)
            raise ModelBuildError(f"Failed to populate layers from Terraform data: Missing expected key {str(e)}", original_exception=e)
        except Exception as e:
            logger.error(f"Error populating layers: {str(e)}", exc_info=True)
            raise ModelBuildError(f"An unexpected error occurred while populating layers from Terraform data: {str(e)}", original_exception=e)
    
    def configure_workload(self, workload_config: Optional[Dict[str, Any]] = None) -> List[Union[Workload, WorkloadMix]]:
        """
        Configure workload for simulation.
        
        Args:
            workload_config: Workload configuration dictionary
        
        Returns:
            List of configured workload objects
        """
        logger.info("Configuring workload")
        try:
            workloads = self.orchestrator.configure_workload(workload_config)
            self.layers[Layer.WORKLOAD] = workloads
            return workloads
        except ValueError as e:
            logger.error(f"Invalid workload configuration: {str(e)}", exc_info=True)
            raise WorkloadError(f"Invalid workload configuration: {str(e)}", original_exception=e)
        except LeafCloudError: # Re-raise our own specific errors
            raise
        except Exception as e:
            logger.error(f"An unexpected error occurred during workload configuration: {str(e)}", exc_info=True)
            raise WorkloadError(f"An unexpected error occurred during workload configuration: {str(e)}", original_exception=e)
    
    def build_model(
        self,
        workload_rate: float | None = None
    ) -> tuple[PetriNet, ModelBuilder]:
        """
        Build Petri net model from the current layers.
        
        Returns:
            Built Petri net model
        """
        logger.info("Building Petri net model")
        if not self.terraform_data:
            raise ModelBuildError("No Terraform data available. Call load_terraform() first.")
        
        try:
            # Pass workload_rate straight through to the orchestrator
            res = self.orchestrator.build_model(
                terraform_data=self.terraform_data,
                workload_rate=workload_rate,
            )
            # If orchestrator.build_model returns None without raising an error (legacy behavior not expected after refactor)
            if res is None: 
                err_msg = "Orchestrator.build_model returned None, indicating a failure to build the model."
                logger.error(err_msg)
                raise ModelBuildError(err_msg)
            self.petri_net, self.model_builder = res
            return self.petri_net, self.model_builder
        except ModelBuildError: # Re-raise ModelBuildError specifically
            raise
        except LeafCloudError as e: # Catch other leaf-cloud specific errors
            logger.error(f"A LEAF-Cloud error occurred during model building: {str(e)}", exc_info=True)
            raise ModelBuildError(f"A LEAF-Cloud system error occurred during model building: {str(e)}", original_exception=e)
        except Exception as e:
            logger.error(f"An unexpected error occurred during model building: {str(e)}", exc_info=True)
            raise ModelBuildError(f"An unexpected error occurred during model building: {str(e)}", original_exception=e)
    
    def run_simulation(self, duration: Optional[float] = None, 
                      iterations: Optional[int] = None) -> Dict[str, Any]:
        """
        Run simulation with current model and configuration.
        
        Args:
            duration: Simulation duration in seconds
            iterations: Number of simulation iterations
        
        Returns:
            Simulation results
        """
        try:
            if not self.petri_net:
                logger.info("No model available, building model first")
                self.build_model() # This call can raise ModelBuildError or other exceptions
            
            if not self.petri_net: # Double check in case build_model failed silently (should not happen)
                 raise SimulationError("Simulation cannot run because the Petri net model is not available after attempting to build it.")

            if iterations is not None:
                self.orchestrator.config.iterations = iterations
            
            logger.info(f"Running simulation for {duration or self.orchestrator.config.duration} seconds")
            self.results = self.orchestrator.run_simulation(duration)

            # logger.info(f"Completed Simulation. Saving raw simulation details...")
            # self.export_results() # This might also need error handling

            # Process results with metrics models
            self._process_results() # This might also need error handling
            
            return self.results
        except (ModelBuildError, SimulationError): # Re-raise our specific errors if they come from build_model or run_simulation
            raise
        except LeafCloudError as e: # Catch other leaf-cloud specific errors
            logger.error(f"A LEAF-Cloud error occurred during simulation: {str(e)}", exc_info=True)
            raise SimulationError(f"A LEAF-Cloud system error occurred during simulation: {str(e)}", original_exception=e)
        except Exception as e:
            logger.error(f"An unexpected error occurred during simulation: {str(e)}", exc_info=True)
            raise SimulationError(f"An unexpected error occurred during simulation: {str(e)}", original_exception=e)
    
    def run_multiple_simulations(self, configurations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Run multiple simulations with different configurations.
        
        Args:
            configurations: List of simulation configurations
            
        Returns:
            List of simulation results
        """
        logger.info(f"Running {len(configurations)} simulations")
        
        # Create SimulationConfig objects from dictionaries
        sim_configs = [LEAFCloudConfig(**config) for config in configurations]
        
        # Convert SimulationConfig objects back to dictionaries for orchestrator
        config_dicts = [config.to_dict() if hasattr(config, 'to_dict') else config.__dict__ for config in sim_configs]
        
        # Convert SimulationConfig objects to dictionaries before passing to run_concurrent_simulations
        # This ensures type compatibility with the method signature
        config_dict_list = []
        for config in config_dicts:
            # Check if the config is already a dictionary
            if isinstance(config, dict):
                config_dict_list.append(config)
            # If it has a to_dict method, use it
            elif hasattr(config, 'to_dict') and callable(config.to_dict):
                config_dict_list.append(config.to_dict())
            # Otherwise, try to convert it to a dictionary using __dict__
            elif hasattr(config, '__dict__'):
                config_dict_list.append(config.__dict__)
            # If all else fails, just use the object as is
            else:
                config_dict_list.append(config)
        
        # Run concurrent simulations
        results = self.orchestrator.run_concurrent_simulations(config_dict_list)
        
        # Aggregate results
        if hasattr(self, 'aggregator') and self.aggregator is not None:
            # Re-initialize aggregator to ensure it's clean for this set of simulations
            # and correctly typed. The MetricAggregator class is already imported.
            self.aggregator = MetricAggregator()

            if isinstance(results, list):
                all_runs_processed = False
                for run_result in results:
                    if isinstance(run_result, dict):
                        self.aggregator.add_simulation_run(run_result)
                        all_runs_processed = True  # Mark that at least one run was added
                    else:
                        logger.warning(f"Item in simulation results list is not a dictionary: {type(run_result)}. Skipping.")
                
                if all_runs_processed and self.aggregator.run_results:  # Ensure some results were actually added
                    summary = self.aggregator.generate_summary()
                    self.results = summary.to_dict()  # Store as dict
                    logger.info(f"Aggregated results from {len(self.aggregator.run_results)} simulation runs.")
                else:
                    logger.warning("No valid simulation runs were added to the aggregator. Storing raw results or original list if empty.")
                    self.results = results  # Fallback to raw results if no runs processed
            elif isinstance(results, dict):  # Handle case where results might be a single dict
                self.aggregator.add_simulation_run(results)
                summary = self.aggregator.generate_summary()
                self.results = summary.to_dict()
                logger.info("Aggregated results from a single simulation run dictionary.")
            else:
                logger.warning(f"Results from orchestrator is not a list or dict ({type(results)}). Using raw results.")
                self.results = results
        else:
            logger.warning("MetricAggregator instance not available. Using raw results.")
            self.results = results
        
        return results
    
    def _process_results(self) -> Dict[str, Any]:
        """
        Process simulation results with metrics models.
        
        Returns:
            Processed metrics
        """
        processed_metrics = {}
        
        if not self.results:
            logger.warning("No results available to process")
            return processed_metrics

        # Ensure results is a dictionary for processing a single simulation run
        if isinstance(self.results, list):
            logger.warning("_process_results currently only handles a single simulation result (dict), not a list. Skipping processing.")
            return processed_metrics
        
        # Cast self.results to Dict for the rest of the method
        current_simulation_results = cast(Dict[str, Any], self.results)
        
        try:
            # Process with each metric model
            self.results.setdefault("sim_duration_seconds", self.orchestrator.config.duration)
            for metric_name, model in self.metrics.items():
                try:
                    if metric_name == 'latency' and hasattr(model, 'calculate'):
                        processed_metrics['latency'] = cast(LatencyModel, model).calculate(current_simulation_results)
                    elif metric_name == 'energy' and hasattr(model, 'calculate'):
                        processed_metrics['energy'] = cast(EnergyModel, model).calculate(current_simulation_results)
                    elif metric_name == 'carbon' and hasattr(model, 'calculate'):
                        if 'energy' not in processed_metrics:
                            if 'energy' in self.metrics and hasattr(self.metrics['energy'], 'calculate'):
                                try:
                                    processed_metrics['energy'] = cast(EnergyModel, self.metrics['energy']).calculate(current_simulation_results)
                                except Exception as e:
                                    logger.warning(f"Failed to compute energy metrics for carbon calculation: {e}")
                            else:
                                logger.warning("Energy model not found or does not have calculate method, cannot calculate carbon.")
                                continue # Skip carbon if energy can't be calculated
                        
                        if 'energy' in processed_metrics:
                            energy_data = processed_metrics['energy']
                            # Revert to keyword argument, assuming CarbonModel.calculate expects it as such
                            processed_metrics['carbon'] = cast(CarbonModel, model).calculate(current_simulation_results, energy_data=energy_data)
                        else:
                            logger.warning("Cannot calculate carbon metrics as energy data is unavailable.")
                    elif metric_name == 'scaling' and hasattr(model, 'calculate'):
                        processed_metrics['scaling'] = cast(ScalingModel, model).calculate(current_simulation_results)
                except Exception as me:
                    logger.warning(f"Failed to compute {metric_name} metrics: {me}")
                    # Attempt to get existing metrics from results, if any, to avoid overwriting
                    # This part might need to be refined based on how 'metrics' is structured within results
                    # For now, we ensure 'metric_name' is a key in processed_metrics if an error occurs
                    if metric_name not in processed_metrics:
                         processed_metrics[metric_name] = {}
            
            # Add processed metrics to the main results dictionary
            if current_simulation_results: # Ensure it's not None if it was an empty dict initially
                if 'utilization' not in processed_metrics:
                    processed_metrics['utilization'] = {}
                processed_metrics['utilization'].update(current_simulation_results.get('resource_utilization', {}))
                current_simulation_results["processed_metrics"] = processed_metrics

            return processed_metrics

        except Exception as e:
            logger.error(f"Error processing results: {str(e)}")
            return {}
    
    def generate_terraform(self, output_dir: str = "terraform_output"):
        """
        Generates Terraform configuration files based on the simulation results.
        """
        current_simulation_data: Optional[Dict[str, Any]] = None
        if isinstance(self.results, list):
            if self.results:  # Ensure the list is not empty
                if isinstance(self.results[0], dict):
                    current_simulation_data = self.results[0]
                else:
                    logger.error(f"First element of self.results list is not a dict: {type(self.results[0])}. Cannot generate Terraform.")
                    return
            else: # Empty list
                logger.error("self.results is an empty list. Cannot generate Terraform.")
                return
        elif isinstance(self.results, dict):
            current_simulation_data = self.results
        else: # Neither a dict nor a list of dicts, or None
            logger.error(f"self.results is not a dict or list of dicts: {type(self.results)}. Cannot generate Terraform.")
            return

        if not current_simulation_data: # Fallback check
            logger.error("No valid simulation results (dict) available to generate Terraform.")
            return

        # Ensure output directory exists
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        try:
            # Pass the actual simulation data to the generator
            generator = TerraformGenerator(simulation_results=current_simulation_data)
            
            # Call the correct method to get the Terraform config string
            terraform_config_str = generator.generate_terraform_config()
            
            output_file_path = os.path.join(output_dir, "main.tf")
            with open(output_file_path, "w") as f:
                f.write(terraform_config_str)
            logger.info(f"Terraform configuration written to {output_file_path}")

        except Exception as e:
            logger.error(f"Error generating Terraform configuration: {e}")
    
    def generate_visualization(self, viz_type: str, output_path: Optional[str] = None) -> str:
        if output_path is None:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = self.orchestrator.config.output_dir
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, f"{timestamp}_{viz_type}.png")
        
        try:
            if viz_type == "class_diagram":
                if self.model_builder is None:
                    logger.error("Model builder not available for class diagram generation.")
                    return ""
                model_builder_to_class_diagram(self.model_builder, output_path)
            
            elif viz_type == "deployment_diagram":
                if self.petri_net is None or self.model_builder is None:
                    logger.error("Petri net or model builder not available for deployment diagram generation.")
                    return ""
                generate_deployment_diagram(self.petri_net, self.model_builder.resource_mapping, output_path)
            
            elif viz_type == "data_flow_diagram":
                if self.petri_net is None or self.model_builder is None:
                    logger.error("Petri net or model builder not available for data flow diagram generation.")
                    return ""
                generate_data_flow_diagram(self.petri_net, self.model_builder.resource_mapping, output_path)
            
            else:
                raise ValueError(f"Unknown visualization type: {viz_type}")
            
            self.visualizations[viz_type] = output_path
            logger.info(f"{viz_type} generated at {output_path}")
            
            return output_path
            
        except Exception as e:
            logger.error(f"Error generating {viz_type}: {str(e)}")
            return ""
    
    def export_results(self, output_path: Optional[str] = None, file_format: str = 'json', results_data: Optional[Dict[str, Any]] = None) -> str:
        """
        Export simulation results to file.
        
        Args:
            output_path: Path to output file
            file_format: Format of the output file (default is 'json')
            results_data: The actual results dictionary to export
            
        Returns:
            Path to the output file
        """
        data_to_export = results_data if results_data is not None else self.results

        # Use a try-except block to safely handle potential attribute errors
        try:
            # Use getattr to safely access the export_results method
            export_results_method = getattr(self.orchestrator, 'export_results', None)
            if self.orchestrator is not None and export_results_method is not None and callable(export_results_method):
                result = export_results_method(output_path, file_format)
                # Ensure we return a string
                return str(result) if result is not None else ""
        except (AttributeError, TypeError) as e:
            logger.warning(f"Error calling export_results on orchestrator: {str(e)}")
            # Continue to fallback implementation
        
        # Fallback implementation if the method doesn't exist or there was an error
        logger.warning("Using fallback implementation for export_results")
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = "./results"
        os.makedirs(output_dir, exist_ok=True)
        
        if not output_path:
            output_path = os.path.join(output_dir, f"{timestamp}_results.{file_format}")
        
        # Export results based on the specified format
        if file_format == 'json':
            with open(output_path, 'w') as f:
                json.dump(data_to_export, f, indent=2)
        elif file_format == 'yaml':
            import yaml
            with open(output_path, 'w') as f:
                yaml.dump(data_to_export, f)
        else:
            logger.warning(f"Unsupported file format: {file_format}, defaulting to JSON")
            with open(output_path, 'w') as f:
                json.dump(data_to_export, f, indent=2)
                
        return str(output_path)  # Ensure we return a string
    
    def get_layer_summary(self) -> Dict[str, Any]:
        """
        Get summary of the layers in the model.
        
        Returns:
            Dictionary with layer summaries
        """
        summary = {}
        
        for layer, content in self.layers.items():
            if layer == Layer.ABSTRACT:
                assert isinstance(content, dict), "Abstract layer content should be a dictionary"
                summary["abstract_layer"] = {
                    "resource_types": list(content.keys()),
                    "counts": {k: len(v) for k, v in content.items()}
                }
            
            elif layer == Layer.SPECIALIZED:
                assert isinstance(content, dict), "Specialized layer content should be a dictionary"
                specialized_summary: Dict[str, Union[int, Dict[str, int]]] = {
                    "total_resources": len(content),
                    "resource_types": {}
                }
                summary["specialized_layer"] = cast(Any, specialized_summary)
                
                # Count resources by type
                type_counts = {}
                for resource_data in content.values():
                    resource_type = resource_data.get('type', 'unknown')
                    type_counts[resource_type] = type_counts.get(resource_type, 0) + 1
                
                specialized_layer_summary = cast(Dict[str, Union[int, Dict[str, int]]], summary["specialized_layer"])
                specialized_layer_summary["resource_types"] = cast(Dict[str, int], type_counts)
            
            elif layer == Layer.WORKLOAD:
                workload_summary = {
                    "total_workloads": len(content),
                    "workload_types": [type(w).__name__ for w in content]
                }
                summary["workload_layer"] = workload_summary
        
        if self.petri_net:
            petri_net_summary = {
                "places": len(self.petri_net.places),
                "transitions": len(self.petri_net.transitions),
                "arcs": len(self.petri_net.arcs)
            }
            summary["petri_net"] = petri_net_summary
        
        return summary
    
    # In leaf-cloud/leaf_cloud.py

    def get_metrics_summary(self) -> Dict[str, Any]:
        """
        Get summary of calculated metrics.
        
        Returns:
            Dictionary with metrics summaries
        """
        container_for_actual_metrics: Optional[Dict[str, Any]] = None

        if isinstance(self.results, list):
            if not self.results:
                logger.warning("Cannot get metrics summary: self.results list is empty.")
                # FIX: Return a default, empty structure
                return {
                    'latency': {'average': 0, 'p95': 0, 'p99': 0},
                    'energy': {'total_kwh': 0, 'average_power_kw': 0},
                    'carbon': {'total_co2eq_kg': 0, 'intensity_gco2eq_kwh': 0},
                    'scaling': {'max_replicas': 0, 'min_replicas': 0, 'autoscaling_events': 0}
                }
            last_run_result_container = self.results[-1]
            if isinstance(last_run_result_container, dict):
                container_for_actual_metrics = last_run_result_container
        elif isinstance(self.results, dict):
            if not self.results:
                 logger.warning("Cannot get metrics summary: self.results dictionary is empty.")
                 # FIX: Return a default, empty structure
                 return {
                    'latency': {'average': 0, 'p95': 0, 'p99': 0},
                    'energy': {'total_kwh': 0, 'average_power_kw': 0},
                    'carbon': {'total_co2eq_kg': 0, 'intensity_gco2eq_kwh': 0},
                    'scaling': {'max_replicas': 0, 'min_replicas': 0, 'autoscaling_events': 0}
                }
            container_for_actual_metrics = self.results
        
        # --- START OF THE FIX ---
        # Initialize summary with default "zero" values to ensure a consistent structure
        summary: Dict[str, Any] = {
            'latency': {'average': 0, 'p95': 0, 'p99': 0},
            'energy': {'total_kwh': 0, 'average_power_kw': 0},
            'carbon': {'total_co2eq_kg': 0, 'intensity_gco2eq_kwh': 0},
            'scaling': {'max_replicas': 0, 'min_replicas': 0, 'autoscaling_events': 0}
        }

        if not container_for_actual_metrics or 'processed_metrics' not in container_for_actual_metrics:
            logger.warning("No 'processed_metrics' found. Returning default summary.")
            return summary

        actual_metrics = container_for_actual_metrics.get('processed_metrics')
        if not isinstance(actual_metrics, dict):
            logger.warning("'processed_metrics' is not a dictionary. Returning default summary.")
            return summary
            
        # Overwrite defaults only if the data exists
        latency_metric_data = actual_metrics.get('latency')
        if isinstance(latency_metric_data, dict):
            summary['latency'] = {
                'average': latency_metric_data.get('average', 0),
                'p95': latency_metric_data.get('percentile_95', 0),
                'p99': latency_metric_data.get('percentile_99', 0)
            }
        
        energy_metric_data = actual_metrics.get('energy')
        if isinstance(energy_metric_data, dict):
            summary['energy'] = {
                'total_kwh': energy_metric_data.get('total_kwh', 0),
                'average_power_kw': energy_metric_data.get('average_power_kw', 0) 
            }
        
        carbon_metric_data = actual_metrics.get('carbon')
        if isinstance(carbon_metric_data, dict):
            summary['carbon'] = {
                'total_co2eq_kg': carbon_metric_data.get('total_co2eq_kg', 0),
                'intensity_gco2eq_kwh': carbon_metric_data.get('intensity_gco2eq_kwh', 0)
            }

        scaling_metric_data = actual_metrics.get('scaling')
        if isinstance(scaling_metric_data, dict):
            summary['scaling'] = {
                'max_replicas': scaling_metric_data.get('max_replicas', 0),
                'min_replicas': scaling_metric_data.get('min_replicas', 0),
                'autoscaling_events': scaling_metric_data.get('autoscaling_events', 0)
            }
        # --- END OF THE FIX ---

        return summary


# Convenience function to create a new LEAF-Cloud instance
def create_framework(config_path: Optional[str] = None) -> LEAFCloud:
    """
    Create a new LEAF-Cloud framework instance.
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        Configured LEAFCloud instance
    """
    return LEAFCloud(config_path)


if __name__ == "__main__":
    # Example usage when run as a script
    
    parser = argparse.ArgumentParser(description="LEAF-Cloud Framework")
    parser.add_argument("--config", help="Path to configuration file")
    parser.add_argument("--terraform", help="Path to Terraform directory")
    args = parser.parse_args()
    
    framework = create_framework(args.config)
    
    if args.terraform:
        framework.load_terraform(args.terraform)
        framework.build_model()
        results = framework.run_simulation()
        print(f"Simulation completed with {len(results)} results")
        print(framework.get_metrics_summary())