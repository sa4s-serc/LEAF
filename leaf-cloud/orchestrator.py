import concurrent.futures
import heapq
import numpy as np
from typing import TypedDict, Dict, Any, List, Optional, Union, Literal, Set, Iterator # Added Set, Iterator
from dataclasses import dataclass, asdict 
from datetime import datetime 

# Import core framework components
from .core.workload import (
    Workload, SteadyWorkload, BurstWorkload, CyclicalWorkload,
    RandomWorkload, CustomWorkload, WorkloadMix, WorkloadStatistics
)

# Import model-specific components
from .core.petri_net import PetriNet, Token, TokenColor, Place, Transition # Added Place, Transition
from .terraform.model_builder import ModelBuilder, Resource # Added Resource
from .config import LEAFCloudConfig
from .exceptions import (
    LeafCloudError, ConfigError, TerraformParseError, ModelBuildError, SimulationError, OrchestratorError
)

import logging
import itertools
import sys
import os
import time

import json
import yaml


import pandas as pd
import threading
from enum import Enum
from typing import Optional, List, Dict, Any, Callable, Union, Tuple, cast 

# Import terraform-related components
from .terraform.parser import TerraformParser
from .models.constraints import ConstraintManager, Constraint, ConstraintSeverity, ConstraintType, ConstraintViolation
from .models import load_latency_model, load_energy_model, load_carbon_model, load_scaling_model, LatencyModel, EnergyModel, CarbonModel, ScalingModel

# Import visualization components
from .visualization.plotter import plot_time_series

"""
orchestrator.py

This module serves as the central orchestrator for the LEAF-Cloud framework.
It coordinates the entire simulation lifecycle, from loading models to generating results,
and handles the scheduling and synchronization of concurrent simulations with
built-in error handling and recovery mechanisms.
"""

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class SimulationState(Enum):
    INITIALIZING = 'initializing'
    PARSING = 'parsing'
    BUILDING = 'building'
    READY = 'ready'
    RUNNING = 'running'
    PAUSED = 'paused'
    COMPLETED = 'completed'
    FAILED = 'failed'

# TypedDicts for structured logging 
class TokenCreationLog(TypedDict):
    time: float
    event: Literal["token_creation"]
    token_id: str
    place: str
    attributes: Dict[str, Any]

class TokenDistributionLog(TypedDict):
    time: float
    event: Literal["token_distribution"]
    distribution: Dict[str, int]

class TransitionFiredLog(TypedDict):
    time: float
    event: Literal["transition_fired"]
    transition: str
    input_places: Dict[str, int]
    output_places: Dict[str, int]
    resources: Optional[List[Dict[str, Any]]] # For retrospective update
    delay: Optional[float] # For retrospective update

class TokenCompletedLog(TypedDict):
    time: float
    event: Literal["token_completed"]
    token_id: str
    place: str

TokenFlowLogEntry = Union[
    TokenCreationLog,
    TokenDistributionLog,
    TransitionFiredLog,
    TokenCompletedLog
]


class Orchestrator:
    """
    The main orchestrator class for the LEAF-Cloud framework.

    Coordinates the simulation lifecycle, manages execution flow,
    handles scheduling and synchronization of concurrent simulations,
    and implements error handling and recovery mechanisms.
    """

    def __init__(self, config: Optional[LEAFCloudConfig] = None, config_path: Optional[str] = None) -> None:
        """
        Initialize the orchestrator with optional configuration.

        Args:
            config_path: Path to a YAML/JSON configuration file
        """
        # Set up configuration
        if config:
            self.config: LEAFCloudConfig = config
        elif config_path:
            self.config = LEAFCloudConfig.load(config_path=config_path)
        else:
            self.config = LEAFCloudConfig.load()

        # Initialize components
        self.parser: Optional[TerraformParser] = None
        self.model_builder: Optional[ModelBuilder] = None
        self.petri_net: Optional[PetriNet] = None
        self.workloads: List[Union[Workload, WorkloadMix]] = []
        self.constraint_manager: ConstraintManager = ConstraintManager()

        # Metric models - These should be assigned by the creator (e.g., LEAFCloud)
        self.latency_model: Optional[LatencyModel] = None
        self.energy_model: Optional[EnergyModel] = None
        self.carbon_model: Optional[CarbonModel] = None
        self.scaling_model: Optional[ScalingModel] = None

        # Simulation state
        self.state: SimulationState = SimulationState.INITIALIZING
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.current_time: float = 0.0
        self.results: Dict[str, Any] = {}

        # Concurrency control
        self.lock: threading.RLock = threading.RLock()
        self._stop_requested: bool = False

        # Error handling
        self.errors: List[Dict[str, Any]] = []
        self.retry_count: int = 0
        self._event_seq_counter: Iterator[int] = itertools.count()

        logger.info("Orchestrator initialized")

    def load_config(self, config_path: str) -> None:
        """
        Load configuration from a file.

        Args:
            config_path: Path to configuration file
        """
        self.config = LEAFCloudConfig.load(config_path=config_path)
        logger.info(f"Configuration loaded from {config_path} using LEAFCloudConfig")

    def parse_terraform(self, terraform_path: Optional[str] = None, var_files: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Parse Terraform files or plan JSON to extract resource definitions.

        Args:
            terraform_path: Path to Terraform directory or plan JSON file
            var_files: List of variable files to use (only applicable for directory parsing)

        Returns:
            Dictionary containing parsed resources
        """
        self.state = SimulationState.PARSING

        current_terraform_path: Optional[str] = terraform_path or self.config.terraform_dir
        current_var_files: Optional[List[str]] = var_files or self.config.var_files # Type for var_files

        if not current_terraform_path:
            logger.error("Terraform path not provided and not found in configuration.")
            self.state = SimulationState.FAILED
            error_msg: str = "Terraform path is required for parsing and not found in config."
            # logger.error(error_msg) # Already logged above
            # self.state = SimulationState.FAILED # Already set
            self.errors.append({"timestamp": time.time(), "stage": "parsing", "error": error_msg})
            raise ConfigError(error_msg)

        self.config.terraform_dir = current_terraform_path

        logger.info(f"Parsing Terraform from {current_terraform_path}")
        terraform_data: Dict[str, Any]
        try:
            if os.path.isfile(current_terraform_path) and current_terraform_path.endswith('.json'):
                model_builder = ModelBuilder(current_terraform_path, self.config.env_config_path)
                parsed_tf_data: Dict[str, Any] = model_builder.parse_terraform_files() # Assuming this returns Dict
                terraform_data = {"resources": parsed_tf_data}
                return terraform_data
            else:
                self.parser = TerraformParser(current_terraform_path, current_var_files)
                terraform_data = self.parser.parse_all()
                logger.info(f"Parsed {len(terraform_data.get('resources', {}))} resources from Terraform files")
                return terraform_data
        except FileNotFoundError as e:
            logger.error(f"Terraform file/directory not found: {current_terraform_path} - {str(e)}")
            self.state = SimulationState.FAILED
            err_msg: str = f"Terraform input not found: {current_terraform_path}"
            self.errors.append({"timestamp": time.time(), "stage": "parsing", "error": err_msg, "details": str(e)})
            raise TerraformParseError(err_msg, original_exception=e)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding Terraform JSON plan: {current_terraform_path} - {str(e)}")
            self.state = SimulationState.FAILED
            err_msg = f"Invalid Terraform JSON plan: {current_terraform_path}"
            self.errors.append({"timestamp": time.time(), "stage": "parsing", "error": err_msg, "details": str(e)})
            raise TerraformParseError(err_msg, original_exception=e)
        except LeafCloudError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error parsing Terraform: {str(e)}", exc_info=True)
            self.state = SimulationState.FAILED
            err_msg = f"An unexpected error occurred during Terraform parsing."
            self.errors.append({"timestamp": time.time(), "stage": "parsing", "error": err_msg, "details": str(e)})
            raise TerraformParseError(err_msg, original_exception=e)

    def build_model(self, terraform_data: Optional[Dict[str, Any]] = None, workload_rate: Optional[float] = None) -> Optional[Tuple[PetriNet, ModelBuilder]]:
        """
        Build a Petri net model from Terraform data.

        Args:
            terraform_data: Dictionary containing Terraform resources
            workload_rate: Base workload rate for the model

        Returns:
            Constructed PetriNet model and the ModelBuilder instance, or None on failure
        """
        self.state = SimulationState.BUILDING
        current_terraform_data: Optional[Dict[str, Any]] = terraform_data
        current_workload_rate: float # Will be assigned

        try:
            if current_terraform_data is None and self.parser:
                current_terraform_data = self.parser.parse_all()

            if current_terraform_data is None:
                error_msg: str = "No Terraform data available for model building. Ensure parsing was successful."
                logger.error(error_msg)
                self.state = SimulationState.FAILED
                self.errors.append({"timestamp": time.time(), "stage": "building", "error": error_msg})
                raise ModelBuildError(error_msg)

            current_workload_rate = workload_rate or self.config.workload_params.get("rate", 100.0)

            if current_terraform_data is None or not current_terraform_data.get("resources"):
                logger.error("Terraform data is None or contains no resources, cannot build model.")
                self.state = SimulationState.FAILED
                self.errors.append({"stage": "building", "error": "Terraform data is None or contains no resources"})
                return None

            terraform_dir: Optional[str] = self.config.terraform_dir
            env_config_path: Optional[str] = self.config.env_config_path

            if not terraform_dir:
                logger.error("Terraform path not configured. Cannot initialize ModelBuilder.")
                self.state = SimulationState.FAILED
                self.errors.append({"stage": "building", "error": "Terraform path not configured for ModelBuilder."})
                return None

            self.model_builder = ModelBuilder(
                terraform_path=terraform_dir,
                env_config_path=env_config_path,
                workload_rate=current_workload_rate,     # <── new
            )

            self.petri_net = self.model_builder.build_model(current_workload_rate)

            self.petri_net.visualize()  # DEBUG

            if self.petri_net:
                logger.info(f"Model built with {len(self.petri_net.places)} places and {len(self.petri_net.transitions)} transitions")
            else:
                logger.warning("Failed to build Petri net model")
            self.state = SimulationState.READY

            if self.petri_net is None or self.model_builder is None:
                return None
            return self.petri_net, self.model_builder
        except LeafCloudError:
            raise
        except Exception as e:
            err_msg: str = f"Error building simulation model: {str(e)}"
            logger.error(err_msg, exc_info=True)
            self.state = SimulationState.FAILED
            self.errors.append({"timestamp": time.time(), "stage": "building", "error": err_msg, "details": str(e)})
            raise ModelBuildError(err_msg, original_exception=e)

    def configure_workload(self, workload_config: Optional[Dict[str, Any]] = None) -> List[Union[Workload, WorkloadMix]]:
        """
        Configure workload pattern(s) for the simulation.

        Args:
            workload_config: Dictionary containing workload configuration

        Returns:
            List of configured Workload objects
        """
        current_workload_config: Dict[str, Any]
        if not hasattr(self, 'workloads'): # Should always be true due to __init__
            self.workloads = []

        try:
            if workload_config is None:
                if hasattr(self, 'config') and self.config is not None:
                    current_workload_config = {
                        "type": getattr(self.config, 'workload_type', 'steady'),
                        "params": getattr(self.config, 'workload_params', {"rate": 100.0})
                    }
                else:
                    current_workload_config = {"type": "steady", "params": {"rate": 100.0}}
            else:
                current_workload_config = workload_config

            workload_type: Any = current_workload_config.get("type", "steady") # Keep Any for now, then check isinstance
            params: Any = current_workload_config.get("params", {}) # Keep Any for now

            if not isinstance(workload_type, str):
                raise ValueError("Workload type must be a string")
            if not isinstance(params, dict):
                raise ValueError("Workload parameters must be a dictionary")

            logger.info(f"Configuring {workload_type} workload")

            if workload_type == "steady":
                duration: float = current_workload_config.get("duration", 100.0) # This was from params before, but seems to be outer config
                rate: float = params.get("rate", 100.0)
                self.workloads = [SteadyWorkload(rate=rate, name="steady_workload", duration=duration)]

            elif workload_type == "burst":
                base_rate: float = params.get("base_rate", 50.0)
                burst_rate: float = params.get("burst_rate", 500.0)
                burst_duration: float = params.get("burst_duration", 60.0)
                burst_interval: float = params.get("burst_interval", 300.0)
                self.workloads = [BurstWorkload(
                    base_rate=base_rate, burst_rate=burst_rate, burst_duration=burst_duration,
                    burst_interval=burst_interval, name="burst_workload"
                )]

            elif workload_type == "cyclical":
                base_rate: float = params.get("base_rate", 100.0)
                amplitude: float = params.get("amplitude", 50.0)
                period: float = params.get("period", 3600.0)
                phase_shift: float = params.get("phase_shift", 0.0)
                self.workloads = [CyclicalWorkload(
                    base_rate=base_rate, amplitude=amplitude, period=period,
                    phase_shift=phase_shift, name="cyclical_workload"
                )]

            elif workload_type == "random":
                base_rate: float = params.get("base_rate", 100.0)
                min_rate: float = params.get("min_rate", 50.0)
                max_rate: float = params.get("max_rate", 150.0)
                change_interval: float = params.get("change_interval", 60.0)
                self.workloads = [RandomWorkload(
                    base_rate=base_rate, min_rate=min_rate, max_rate=max_rate,
                    change_interval=change_interval, name="random_workload"
                )]

            elif workload_type == "custom":
                base_rate: float = params.get("base_rate", 100.0)
                scale_factor: float = params.get("scale_factor", 1.0)
                pattern: Optional[List[Any]] = params.get("pattern")
                rate_function: Optional[Callable[[float], Any]] = params.get("rate_function")

                if callable(rate_function):
                    def float_rate_function(t: float, rf: Callable[[float], Any] = rate_function) -> float:
                        result: Any = rf(t)
                        return float(result) if result is not None else 0.0
                    self.workloads = [CustomWorkload(
                        rate_function=float_rate_function, base_rate=base_rate, name="custom_workload"
                    )]
                elif pattern is not None and isinstance(pattern, list) and len(pattern) > 0:
                    def _get_time_rate_from_pattern_item(item: Any, expected_format: str, default_rate: float) -> Optional[Tuple[float, float]]:
                        if expected_format == "tuple":
                            if isinstance(item, (list, tuple)) and len(item) >= 2 and \
                               isinstance(item[0], (int, float)) and isinstance(item[1], (int, float)):
                                return float(item[0]), float(item[1])
                        elif expected_format == "dict":
                            if isinstance(item, dict) and 'time' in item and 'rate' in item and \
                               isinstance(item.get('time'), (int, float)) and isinstance(item.get('rate'), (int, float)):
                                return float(item['time']), float(item['rate'])
                        return None

                    def pattern_rate_function(t: float, current_pattern: List[Any] = pattern) -> float:
                        if not current_pattern:
                            return base_rate

                        first_item_data: Optional[Tuple[float, float]] = _get_time_rate_from_pattern_item(current_pattern[0], "tuple", base_rate)
                        item_format: str = "tuple"
                        if first_item_data is None:
                            first_item_data = _get_time_rate_from_pattern_item(current_pattern[0], "dict", base_rate)
                            item_format = "dict"
                            if first_item_data is None:
                                logger.warning(f"Unrecognized pattern format for first item: {current_pattern[0]}. Falling back to base_rate.")
                                return base_rate

                        if len(current_pattern) == 1:
                             _, single_rate = first_item_data # first_item_data is not None here
                             return single_rate * scale_factor

                        parsed_pattern: List[Tuple[float, float]] = []
                        for item_idx, item_val in enumerate(current_pattern):
                            tr_data: Optional[Tuple[float, float]] = _get_time_rate_from_pattern_item(item_val, item_format, base_rate)
                            if tr_data is None:
                                logger.warning(f"Skipping invalid pattern item at index {item_idx} (value: {item_val}) for expected format '{item_format}'.")
                                continue
                            parsed_pattern.append(tr_data)

                        if not parsed_pattern:
                            logger.warning("No valid pattern data points after parsing. Falling back to base_rate.")
                            return base_rate

                        if len(parsed_pattern) == 1:
                             logger.info(f"Only one valid data point in pattern after filtering. Using its rate: {parsed_pattern[0][1]}.")
                             return parsed_pattern[0][1] * scale_factor

                        parsed_pattern.sort(key=lambda x_sort: x_sort[0]) # x renamed to x_sort

                        if t < parsed_pattern[0][0]:
                            return parsed_pattern[0][1] * scale_factor

                        if t >= parsed_pattern[-1][0]:
                            return parsed_pattern[-1][1] * scale_factor

                        for i in range(len(parsed_pattern) - 1):
                            t1, r1 = parsed_pattern[i]
                            t2, r2 = parsed_pattern[i+1]

                            if t1 <= t < t2:
                                if t2 == t1: # Avoid division by zero if t1 == t2
                                    return r1 * scale_factor
                                ratio: float = (t - t1) / (t2 - t1)
                                rate_val: float = r1 + ratio * (r2 - r1) # rate renamed to rate_val
                                return rate_val * scale_factor

                        logger.warning(f"Pattern interpolation logic did not resolve a rate for time {t} with pattern {parsed_pattern}. Falling back to base_rate.")
                        return base_rate

                    self.workloads = [CustomWorkload(
                        rate_function=pattern_rate_function, base_rate=base_rate, name="pattern_custom_workload"
                    )]
                    logger.info(f"Created custom workload from pattern with {len(pattern)} points")
                else:
                    def constant_rate_function(t: float) -> float:
                        return float(base_rate)
                    logger.warning("No valid rate_function or pattern provided, using constant rate function")
                    self.workloads = [CustomWorkload(
                        rate_function=constant_rate_function, base_rate=base_rate, name="constant_workload"
                    )]

            elif workload_type == "mix":
                workload_configs_list: Any = params.get("workloads", []) # workload_configs renamed
                inner_workloads: List[Union[Workload, WorkloadMix]] = [] # workloads renamed to inner_workloads

                if not isinstance(workload_configs_list, list):
                    logger.warning(f"'workloads' parameter is not a list, skipping workload mix. Got: {type(workload_configs_list)}")
                    workload_configs_list = []
                for wl_config in workload_configs_list:
                    if isinstance(wl_config, dict):
                        result: List[Union[Workload, WorkloadMix]] = self.configure_workload({ # Recursive call
                            "type": wl_config.get("type", "steady"),
                            "params": wl_config.get("params", {})
                        })
                        if result and len(result) > 0: # result is List[Workload], so result[0] is Workload
                             inner_workloads.append(result[0]) # Appending Workload or WorkloadMix

                typed_workloads: List[Workload] = cast(List[Workload], inner_workloads) # Assuming inner_workloads are compatible
                self.workloads = [WorkloadMix(typed_workloads, name="workload_mix")] if typed_workloads else [SteadyWorkload(rate=100.0, name="default_workload")]

            else:
                logger.warning(f"Unknown workload type '{workload_type}', using SteadyWorkload instead")
                self.workloads = [SteadyWorkload(rate=100.0, name="default_workload")]

            logger.info(f"Configured {len(self.workloads) if self.workloads is not None else 0} workload(s)")
            return self.workloads
        except Exception as e:
            logger.error(f"Error configuring workload: {str(e)}")
            self.workloads = [SteadyWorkload(rate=100.0, name="fallback_workload")]
            if hasattr(self, 'errors'):
                self.errors.append({"stage": "workload_configuration", "error": str(e)})
            return self.workloads

    def setup_constraints(self, constraint_config: Optional[Dict[str, Any]] = None) -> ConstraintManager:
        """
        Set up constraints for the simulation.

        Args:
            constraint_config: Dictionary containing constraint configuration

        Returns:
            Configured ConstraintManager
        """
        current_constraint_config: Optional[Dict[str, Any]] = constraint_config
        try:
            if current_constraint_config is None and self.config.constraint_config_path:
                with open(self.config.constraint_config_path, 'r') as f:
                    if self.config.constraint_config_path.endswith('.json'):
                        current_constraint_config = json.load(f)
                    else:
                        current_constraint_config = yaml.safe_load(f)

            if not hasattr(self, 'constraint_manager') or self.constraint_manager is None: # Should be true due to __init__
                self.constraint_manager = ConstraintManager()

            if current_constraint_config and 'constraints' in current_constraint_config:
                for constraint_def in current_constraint_config['constraints']:
                    constraint = Constraint(
                        name=constraint_def.get('name', 'unnamed_constraint'),
                        description=constraint_def.get('description', 'No description provided'),
                        constraint_type=getattr(ConstraintType, constraint_def.get('constraint_type', 'RESOURCE_LIMIT')),
                        resource_type=constraint_def.get('resource_type', '*'),
                        severity=getattr(ConstraintSeverity, constraint_def.get('severity', 'WARNING')),
                        limit_value=constraint_def.get('threshold'), # Can be None
                        region=constraint_def.get('region'),
                        zone=constraint_def.get('zone'),
                        project=constraint_def.get('project'),
                        service=constraint_def.get('service')
                    )
                    self.constraint_manager.add_constraint(constraint)

            logger.info(f"Set up {len(self.constraint_manager.constraints)} constraints")
            return self.constraint_manager

        except Exception as e:
            logger.error(f"Error setting up constraints: {str(e)}")
            self.errors.append({"stage": "constraint_setup", "error": str(e)})
            # Ensure constraint_manager is initialized if error occurs before that.
            if not hasattr(self, 'constraint_manager') or self.constraint_manager is None:
                 self.constraint_manager = ConstraintManager()
            return self.constraint_manager

    def setup_metrics(self) -> Dict[str, Union[LatencyModel, EnergyModel, CarbonModel, ScalingModel, Any]]:
        """
        Verifies that metric collection models are configured and available.
        The models should be assigned externally by the `LEAFCloud` class.

        Returns:
            Dictionary of configured metrics models
        """
        try:
            metrics: Dict[str, Union[LatencyModel, EnergyModel, CarbonModel, ScalingModel]] = {}

            if self.config.collect_latency and self.latency_model:
                metrics['latency'] = self.latency_model
            elif self.config.collect_latency:
                logger.warning("Latency collection is enabled, but latency_model is not configured.")

            if self.config.collect_energy and self.energy_model:
                metrics['energy'] = self.energy_model
            elif self.config.collect_energy:
                logger.warning("Energy collection is enabled, but energy_model is not configured.")
            
            if self.config.collect_carbon and self.carbon_model:
                metrics['carbon'] = self.carbon_model
            elif self.config.collect_carbon:
                logger.warning("Carbon collection is enabled, but carbon_model is not configured.")

            if self.config.collect_scaling and self.scaling_model:
                metrics['scaling'] = self.scaling_model
            elif self.config.collect_scaling:
                logger.warning("Scaling collection is enabled, but scaling_model is not configured.")

            logger.info(f"Verified {len(metrics)} metric collection models are configured.")
            return metrics

        except Exception as e:
            logger.error(f"Error verifying metrics setup: {str(e)}")
            self.errors.append({"stage": "metrics_setup", "error": str(e)})
            return {}

    def _display_simulation_progress(self, current_sim_time: float, total_duration: float, completed_tokens: int, pending_events: int, errors_count: int, events_per_sec: float, attempt_num: Optional[int] = None, final_status: Optional[str] = None) -> None:
        """Helper method to display simulation progress in the console."""
        CLEAR_LINE: str = '\033[2K'
        attempt_str: str = f"Attempt: {attempt_num} | " if attempt_num is not None else ""
        progress_bar_length: int = 30
        status_line: str

        if total_duration > 0:
            progress_percent: float = (current_sim_time / total_duration) * 100
            filled_length: int = int(progress_bar_length * current_sim_time // total_duration)
            bar: str = '█' * filled_length + '-' * (progress_bar_length - filled_length)

            if final_status:
                if final_status == SimulationState.COMPLETED.value:
                    progress_percent = 100.0
                    bar = '█' * progress_bar_length
                status_line = (
                    f"\r{CLEAR_LINE}Status: {final_status.upper()} | Sim Time: {current_sim_time:8.2f}/{total_duration:.2f}s [{bar}] {progress_percent:6.2f}% | "
                    f"{attempt_str}Tokens: {completed_tokens:<7} | Errors: {errors_count:<3}   "
                )
            else:
                status_line = (
                    f"\r{CLEAR_LINE}Sim Time: {current_sim_time:8.2f}/{total_duration:.2f}s [{bar}] {progress_percent:6.2f}% | "
                    f"{attempt_str}Tokens: {completed_tokens:<7} | Pending Events: {pending_events:<7} | "
                    f"Errors: {errors_count:<3} | EPS: {events_per_sec:<7.1f}   "
                )
        else:
            if final_status:
                status_line = (
                    f"\r{CLEAR_LINE}Status: {final_status.upper()} | Sim Time: {current_sim_time:8.2f}s | "
                    f"{attempt_str}Tokens: {completed_tokens:<7} | Errors: {errors_count:<3}   "
                )
            else:
                status_line = (
                    f"\r{CLEAR_LINE}Sim Time: {current_sim_time:8.2f}s | "
                    f"{attempt_str}Tokens: {completed_tokens:<7} | Pending Events: {pending_events:<7} | "
                    f"Errors: {errors_count:<3} | EPS: {events_per_sec:<7.1f}   "
                )

        sys.stdout.write(status_line)
        sys.stdout.flush()

    def run_simulation(self, duration: Optional[float] = None) -> Dict[str, Any]: # noqa: C901 (suppress complexity for this existing large method)
        """
        Run a single simulation with the current configuration.
        Optimized for performance with heavier workloads and longer durations.

        Args:
            duration: Duration of the simulation in seconds

        Returns:
            Dictionary containing simulation results
        """
        sim_duration: float = duration or self.config.duration
        self.setup_metrics()

        if not self.petri_net:
            raise ValueError("No Petri net model available. Call build_model() first.")

        if not self.workloads:
            self.configure_workload({
                "type":  self.config.workload_type or "steady",
                "params": self.config.workload_params or {"rate": 100.0},
                "duration": sim_duration
            })

        logger.info(f"Starting simulation for duration {sim_duration} seconds")
        self.state = SimulationState.RUNNING
        self.start_time = time.time()
        self.current_time = 0.0
        self._stop_requested = False

        sampling_interval: float = self.config.time_step * 10
        if sim_duration > 3600:
            sampling_interval = max(sampling_interval, sim_duration / 1000)

        log_detailed: bool = sim_duration < 3600

        # Initialize token_flow_log for the try block, used in except/finally
        token_flow_log: List[TokenFlowLogEntry] = []
        event_queue : List[Tuple[float, int, str, Any]] = []
        completed_token_count: int = 0 # Ensure initialized for finally
        processed_events: int = 0 # Ensure initialized for finally

        try:
            event_seq: int = 0
            statistics: Dict[str, Any] = {}

            logger.info("Generating workload events...")
            batch_size: int = 10000
            for workload in self.workloads:
                events_batch: List[Tuple[float, int, str, Any]] = []
                # Assuming workload.generate_events yields Tuple[float, Dict[str, Any]]
                for event_time_val, token_attrs in workload.generate_events(self.current_time):
                    if event_time_val <= sim_duration:
                        events_batch.append((event_time_val, event_seq, "token_creation", token_attrs))
                        event_seq += 1
                        if len(events_batch) >= batch_size:
                            for event in events_batch:
                                heapq.heappush(event_queue, event)
                            events_batch = []
                for event in events_batch: # Remaining events
                    heapq.heappush(event_queue, event)

            logger.info(f"Generated {event_seq} initial events")

            resource_stats: Dict[str, Dict[str, List[Any]]] = {}
            if self.model_builder and self.model_builder.resource_mapping:
                resource_stats = {
                    r_id: {"utilization": [], "state_changes": [], "energy_consumption": [], "carbon_footprint": [], "latency": []} # Ensure latency list
                    for r_id in self.model_builder.resource_mapping
                }
                for r_id, resource_obj in self.model_builder.resource_mapping.items(): # resource renamed to resource_obj
                    if hasattr(resource_obj, 'utilization_ratio') and hasattr(resource_obj, 'state') and hasattr(resource_obj.state, 'value'):
                        resource_stats[r_id]["utilization"].append({"time": 0.0, "value": resource_obj.utilization_ratio})
                        resource_stats[r_id]["state_changes"].append({"time": 0.0, "state": resource_obj.state.value})

            active_token_count: int = 0
            # completed_token_count already initialized
            active_tokens: Optional[Set[str]] = set() if log_detailed else None
            completed_tokens: Optional[Set[str]] = set() if log_detailed else None

            place_token_counts: Dict[str, List[Dict[str, Any]]] = {}
            transition_throughput: Dict[str, List[Any]] = {} # Define Any for now

            if self.petri_net: # self.petri_net is already checked not None
                if self.petri_net.places:
                    place_token_counts = {place.id: [] for place_id, place in self.petri_net.places.items() if hasattr(place, 'id')}
                if self.petri_net.transitions:
                    transition_throughput = {t.id: [] for t_id, t in self.petri_net.transitions.items() if hasattr(t, 'id')}

            max_flow_log_entries: int = 10000 if sim_duration > 3600 else 100000
            # token_flow_log already initialized

            place_to_resource: Dict[str, Resource] = {} # Assuming Resource type from ModelBuilder
            if self.model_builder and self.model_builder.resource_mapping:
                place_to_resource = {
                    res.name: res for res_id, res in self.model_builder.resource_mapping.items()
                    if hasattr(res, 'name')
                }

            # Initialize state for utilization recording and token distribution
            self._last_distribution_log_time: float = -1.0  # Initialize for _log_token_distribution_at_time
            self.last_utilization_update_time: float = 0.0  # Track last utilization recording time
            
            # Create a local reference to self for nested functions
            orchestrator_self = self
            
            def log_token_distribution() -> None:
                if orchestrator_self.petri_net is None:
                    raise ValueError("Petri net is not initialized")
                current_time = orchestrator_self.current_time
                _log_token_distribution_at_time(current_time, token_flow_log, max_flow_log_entries)
                
            def _record_utilization_at_interval(current_target_sim_time: float, 
                                          sampling_interval: float, 
                                          resource_stats: Dict,
                                          token_flow_log: List, 
                                          max_flow_log_entries: int,
                                          log_distribution: bool = False) -> None:
                """
                Records utilization at regular intervals from last_utilization_update_time + sampling_interval
                up to current_target_sim_time.
                Updates last_utilization_update_time internally.
                """
                nonlocal orchestrator_self
                next_sample = orchestrator_self.last_utilization_update_time + sampling_interval
                
                while next_sample <= current_target_sim_time:
                    sample_time = round(next_sample, 5)  # Mitigate float precision issues
                    
                    # Record utilization for all resources at this sample time
                    for r_id, resource_obj in orchestrator_self.model_builder.resource_mapping.items():
                        if hasattr(resource_obj, 'utilization_ratio'):
                            resource_stats[r_id]["utilization"].append({
                                "time": sample_time,
                                "value": 0.0
                            })
                    
                    # Log token distribution if requested
                    if log_distribution:
                        _log_token_distribution_at_time(sample_time, token_flow_log, max_flow_log_entries)
                    
                    # Update state
                    orchestrator_self.last_utilization_update_time = sample_time
                    next_sample += sampling_interval
            
            def _log_token_distribution_at_time(sample_time: float, 
                                            token_flow_log: List, 
                                            max_flow_log_entries: int) -> None:
                """
                Log token distribution at a specific simulation time.
                
                Args:
                    sample_time: The simulation time for this log entry
                    token_flow_log: List to store token flow logs
                    max_flow_log_entries: Maximum number of log entries to keep
                """
                nonlocal orchestrator_self, log_detailed
                
                # Simple throttle: only log for new times
                if sample_time <= orchestrator_self._last_distribution_log_time:
                    return
                    
                if not orchestrator_self.petri_net or not orchestrator_self.petri_net.places:
                    return
                
                # Check log size before adding new entry
                if len(token_flow_log) >= max_flow_log_entries:
                    token_flow_log.pop(0)
                
                # Create distribution snapshot
                distribution: Dict[str, int] = {
                    place.name: len(place.tokens) 
                    for place_id, place in orchestrator_self.petri_net.places.items() 
                    if hasattr(place, 'name') and hasattr(place, 'tokens')
                }
                
                # Create and store log entry
                log_entry: TokenDistributionLog = {
                    "time": sample_time,
                    "event": "token_distribution",
                    "distribution": distribution
                }
                token_flow_log.append(log_entry)
                orchestrator_self._last_distribution_log_time = sample_time
                
                if log_detailed:
                    logger.debug(f"Time {sample_time:.2f}: Token distribution - {distribution}")

            last_stats_update_time: float = -sampling_interval
            # batch_events: List[Any] = [] # Declared but not used like this
            # current_batch_time: Optional[float] = None # Declared but not used

            logger.info("Starting main simulation loop...")
            # processed_events initialized
            last_display_update_time: float = time.time()
            display_update_interval: float = 0.5
            events_per_sec_calc: float = 0.0

            # Record initial utilization at time 0 using the helper
            _record_utilization_at_interval(
                current_target_sim_time=0.0,
                sampling_interval=sampling_interval,
                resource_stats=resource_stats,
                token_flow_log=token_flow_log,
                max_flow_log_entries=max_flow_log_entries,
                log_distribution=True
            )
            
            while event_queue and not self._stop_requested:
                next_event_time = float(event_queue[0][0]) if event_queue[0][0] is not None else 0.0
                
                # Record utilization at regular intervals up to the next event time or simulation end
                target_time = min(next_event_time, sim_duration)
                if self.last_utilization_update_time < target_time:
                    _record_utilization_at_interval(
                        current_target_sim_time=target_time,
                        sampling_interval=sampling_interval,
                        resource_stats=resource_stats,
                        token_flow_log=token_flow_log,
                        max_flow_log_entries=max_flow_log_entries,
                        log_distribution=True
                    )
                
                # If we've reached or passed the simulation duration, we're done
                if next_event_time > sim_duration:
                    # Record final utilization up to the end of simulation
                    if self.last_utilization_update_time < sim_duration:
                        _record_utilization_at_interval(
                            current_target_sim_time=sim_duration,
                            sampling_interval=sampling_interval,
                            resource_stats=resource_stats,
                            token_flow_log=token_flow_log,
                            max_flow_log_entries=max_flow_log_entries,
                            log_distribution=True
                        )
                    break
                    
                self.current_time = next_event_time

                # Process all events at the current time
                events_at_current_time : List[Tuple[float, int, str, Any]] = []
                while event_queue and event_queue[0][0] == self.current_time:
                    events_at_current_time.append(heapq.heappop(event_queue))

                made_state_change_this_timestep: bool = False

                for event_time, _, event_type, event_data in events_at_current_time:
                    processed_events += 1
                    current_wall_time: float = time.time()
                    if self.start_time and current_wall_time - last_display_update_time >= display_update_interval:
                        time_diff_wall: float = current_wall_time - self.start_time
                        if time_diff_wall > 0:
                            events_per_sec_calc = processed_events / time_diff_wall
                        self._display_simulation_progress(
                            current_sim_time=self.current_time, total_duration=sim_duration,
                            completed_tokens=completed_token_count, pending_events=len(event_queue),
                            errors_count=len(self.errors), events_per_sec=events_per_sec_calc
                        )
                        last_display_update_time = current_wall_time

                    if event_type == "token_creation":
                        token_id: str = event_data.get("id", f"token_{active_token_count}")
                        token_color_str: str = event_data.get("type", "REQUEST").upper()
                        token_color: TokenColor
                        try:
                            token_color = getattr(TokenColor, token_color_str)
                        except (AttributeError, KeyError):
                            token_color = TokenColor.REQUEST
                        
                        token: Token = Token(token_id, token_color, attributes=event_data)
                        entry_place: Optional[Place] = None
                        if self.petri_net:
                            entry_place = self.petri_net.get_place_by_name("Source")
                            if entry_place:
                                entry_place.add_token(token)
                            else:
                                logger.warning("Source place not found in Petri net")
                        else: # Should not happen
                            logger.warning("Petri net is not initialized")
                        
                        active_token_count += 1
                        if active_tokens is not None:
                            active_tokens.add(token_id)

                        if log_detailed:
                            place_name_log: str = entry_place.name if entry_place and entry_place.name else "unknown"
                            logger.debug(f"Time {event_time:.2f}: Created token {token_id} at {place_name_log}")
                            if len(token_flow_log) < max_flow_log_entries:
                                creation_log: TokenCreationLog = {
                                    "time": float(event_time),
                                    "event": "token_creation",
                                    "token_id": str(token_id),
                                    "place": place_name_log,
                                    "attributes": dict(event_data) if event_data else {}
                                }
                                token_flow_log.append(creation_log)
                        made_state_change_this_timestep = True

                    elif event_type == "process_transitions":
                        enabled_transitions: List[Tuple[str, Dict[str, List[Token]]]] = []
                        if self.petri_net: # Already checked not None
                            enabled_transitions = self.petri_net.find_enabled_transitions() or []

                        for transition_id_proc, place_tok_mapping_proc in enabled_transitions: # Renamed vars
                            if self.petri_net and transition_id_proc: # PetriNet not None, transition_id_proc is str
                                transition_obj: Optional[Transition] = self.petri_net.get_transition_by_id(transition_id_proc)
                                if transition_obj and place_tok_mapping_proc:
                                    if log_detailed and self.petri_net.places:
                                        try:
                                            input_places_log: Dict[str, int] = {
                                                self.petri_net.places[p_id].name: len(toks)
                                                for p_id, toks in place_tok_mapping_proc.items()
                                                if p_id in self.petri_net.places and self.petri_net.places[p_id]
                                            }
                                            token_count_log: int = sum(len(toks) for toks in place_tok_mapping_proc.values())
                                            if transition_obj.name:
                                                logger.debug(f"Time {event_time:.2f}: Transition {transition_obj.name} enabled with {token_count_log} tokens from {input_places_log}")
                                        except (AttributeError, TypeError, KeyError) as e_log:
                                            logger.debug(f"Time {event_time:.2f}: Error logging transition details: {str(e_log)}")

                                    delay_val: float = 0.0 # delay renamed
                                    if hasattr(transition_obj, 'get_delay'): # Check if Transition has get_delay
                                        delay_val = transition_obj.get_delay(tokens=place_tok_mapping_proc)
                                    
                                    transition_complete_time: float = event_time + delay_val
                                    if transition_complete_time <= sim_duration:
                                        heapq.heappush(event_queue, (transition_complete_time, next(self._event_seq_counter), "transition_complete", (transition_id_proc, place_tok_mapping_proc)))
                    
                    elif event_type == "transition_complete":
                        if not (isinstance(event_data, tuple) and len(event_data) == 2):
                            logger.error(f"Time {event_time:.2f}: Malformed event_data for transition_complete: {event_data}")
                            continue
                        
                        transition_id_comp: str
                        place_tok_mapping_comp: Dict[str, List[Token]]
                        transition_id_comp, place_tok_mapping_comp = event_data

                        transition_comp: Optional[Transition] = None
                        if self.petri_net and transition_id_comp:
                            transition_comp = self.petri_net.get_transition_by_id(transition_id_comp)

                        if transition_comp and place_tok_mapping_comp and self.petri_net: # PetriNet checked
                            output_places_map: Dict[str, List[Token]] = self.petri_net.get_output_tokens(transition_id_comp) or {}
                            
                            snapshots: Dict[str, Tuple[Any, List[Any], List[Any], Any]] = {} # Define tuple structure
                            place_ids_for_snapshot: Set[str] = set()
                            if place_tok_mapping_comp: place_ids_for_snapshot.update(place_tok_mapping_comp.keys())
                            if output_places_map: place_ids_for_snapshot.update(output_places_map.keys())

                            if self.petri_net.places:
                                for pid_snap in place_ids_for_snapshot:
                                    place_obj_snap: Optional[Place] = self.petri_net.places.get(pid_snap)
                                    if place_obj_snap and place_obj_snap.name:
                                        res_snap: Optional[Resource] = place_to_resource.get(place_obj_snap.name)
                                        if res_snap: # Assuming Resource has these attributes
                                            # resource_stats[pid_snap] = ... # Keyed by resource ID, not place ID
                                            snapshots[place_obj_snap.name] = (
                                                res_snap.allocated_capacity,
                                                res_snap.allocation_history.copy(),
                                                res_snap.utilization_history.copy(),
                                                res_snap.state
                                            )
                            
                            fire_success: bool = False
                            try:
                                fire_success = self.petri_net.fire_transition(transition_id_comp, place_tok_mapping_comp)
                                if fire_success:
                                    if self.petri_net.places: # Deallocate inputs
                                        for pl_id_in, toks_consumed in place_tok_mapping_comp.items():
                                            pl_obj_in: Optional[Place] = self.petri_net.places.get(pl_id_in)
                                            if pl_obj_in and pl_obj_in.name:
                                                res_in: Optional[Resource] = place_to_resource.get(pl_obj_in.name)
                                                if res_in:
                                                    for tok_item in toks_consumed:
                                                        if hasattr(tok_item, 'id'):
                                                            res_in.deallocate(tok_item.id, float(event_time))
                                    if output_places_map and self.petri_net.places: # Allocate outputs
                                        for pl_id_out, new_toks in output_places_map.items():
                                            pl_obj_out: Optional[Place] = self.petri_net.places.get(pl_id_out)
                                            if pl_obj_out and pl_obj_out.name:
                                                res_out: Optional[Resource] = place_to_resource.get(pl_obj_out.name)
                                                if res_out:
                                                    for tok_item in new_toks:
                                                        if hasattr(tok_item, 'id'):
                                                            res_out.allocate(1.0, tok_item.id, float(event_time))
                                else: # Benign failure
                                    if transition_comp.name:
                                        logger.debug(f"Time {event_time:.2f}: Transition {transition_comp.name} was enabled but did not fire (likely tokens unavailable).")
                            except Exception as err_fire:
                                if snapshots:
                                    for place_name_key, (alloc, all_hist, util_hist, state_val) in snapshots.items():
                                        res_rb: Optional[Resource] = place_to_resource.get(place_name_key)
                                        if res_rb:
                                            res_rb.allocated_capacity = alloc
                                            res_rb.allocation_history = all_hist
                                            res_rb.utilization_history = util_hist
                                            # res_rb._state = state_val # Assuming Resource has _state
                                logger.error(f"Atomic transition update failed for {transition_comp.name if transition_comp.name else transition_id_comp}, rolled back: {err_fire}")
                                self.errors.append({"stage": "simulation_transition_fire", "error": str(err_fire), "time": self.current_time, "transition_id": str(transition_id_comp)})
                            
                            if fire_success:
                                made_state_change_this_timestep = True
                                if log_detailed and self.petri_net.places and output_places_map:
                                    output_place_names_log: Dict[str, int] = {
                                        self.petri_net.places[p_id].name: len(toks)
                                        for p_id, toks in output_places_map.items()
                                        if p_id in self.petri_net.places and self.petri_net.places[p_id]
                                    }
                                    if transition_comp.name:
                                        logger.debug(f"Time {event_time:.2f}: Transition {transition_comp.name} produced tokens in {output_place_names_log}")
                                    
                                    if len(token_flow_log) < max_flow_log_entries and self.petri_net.places:
                                        input_place_names_log: Dict[str, int] = {
                                            self.petri_net.places[p_id].name: len(toks)
                                            for p_id, toks in place_tok_mapping_comp.items()
                                            if p_id in self.petri_net.places and self.petri_net.places[p_id]
                                        }
                                        fired_log: TransitionFiredLog = {
                                            "time": float(event_time),
                                            "event": "transition_fired",
                                            "transition": str(transition_comp.name) if transition_comp.name else "unknown",
                                            "input_places": input_place_names_log,
                                            "output_places": output_place_names_log,
                                            "resources": None, # Will be updated later
                                            "delay": None      # Will be updated later
                                        }
                                        token_flow_log.append(fired_log)
                            else:
                                logger.debug(f"Transition {transition_comp.name} was enabled but did not fire (likely tokens unavailable or consumed by competitor).")

                            exit_place: Optional[Place] = self.petri_net.get_place_by_name("Global Sink") if self.petri_net else None
                            if fire_success and exit_place: # Process exit only if transition fired
                                completed_this_batch: int = 0
                                for token_exit in list(exit_place.tokens): # token renamed to token_exit
                                    completed_token_count += 1
                                    completed_this_batch +=1
                                    if active_tokens is not None: active_tokens.discard(token_exit.id)
                                    if completed_tokens is not None: completed_tokens.add(token_exit.id)
                                    if log_detailed:
                                        logger.debug(f"Time {event_time:.2f}: Token {token_exit.id} completed at {exit_place.name}")
                                        if len(token_flow_log) < max_flow_log_entries:
                                            comp_log: TokenCompletedLog = {
                                                "time": float(event_time),
                                                "event": "token_completed",
                                                "token_id": str(token_exit.id),
                                                "place": str(exit_place.name)
                                            }
                                            token_flow_log.append(comp_log)
                                    exit_place.remove_token(token_exit)
                                if completed_this_batch > 0 and not log_detailed:
                                    logger.debug(f"Time {event_time:.2f}: {completed_this_batch} tokens completed")
                
                if made_state_change_this_timestep:
                    heapq.heappush(event_queue, (self.current_time, next(self._event_seq_counter), "process_transitions", None))

                if self.current_time - last_stats_update_time >= sampling_interval:
                    if self.model_builder and self.model_builder.resource_mapping:
                        for r_id_stats, resource_obj_stats in self.model_builder.resource_mapping.items():
                            
                            # START OF NEW/MODIFIED LOGIC
                            # 1) Find correct input queue to measure live requests
                            if self.petri_net:
                                place_for_util = (
                                    self.petri_net.places.get(f"compute_in_{resource_obj_stats.name}")
                                    or self.petri_net.places.get(f"storage_in_{resource_obj_stats.name}")
                                    or self.petri_net.places.get(f"In_{resource_obj_stats.name}")
                                )
                            else:
                                place_for_util = None
                            live_requests = len(place_for_util.tokens) if place_for_util else 0
                            
                            # 2) Dynamic Autoscaling for scalable resources
                            workload_rate = 0.0
                            if self.workloads:
                                primary_workload = self.workloads[0]
                                if hasattr(primary_workload, 'get_rate_at_time') and callable(primary_workload.get_rate_at_time):
                                    workload_rate = primary_workload.get_rate_at_time(self.current_time)
                                elif hasattr(primary_workload, 'base_rate'):
                                    workload_rate = primary_workload.base_rate

                            if hasattr(resource_obj_stats, 'concurrency') and self.scaling_model:
                                # Use the configured workload rate for a stable scaling decision.

                                svc = resource_obj_stats.name   # e.g. "cr-aula-spring" or "spring_boot_terraform_cloud_run_service"

                                if svc == "spring_boot_terraform_cloud_run_service" or svc == "cr-aula-spring":
                                    # Demo repo service (#1) → 1 pod = 40 rps
                                    scaler = ScalingModel(
                                        request_capacity=200,
                                        min_pods=resource_obj_stats.min_instances,
                                        max_pods=resource_obj_stats.max_instances
                                    )
                                else:
                                    # Everything else falls back to whatever global self.scaling_model is
                                    scaler = self.scaling_model

                                desired_replicas = scaler.calculate_required_pods(
                                    request_rate=workload_rate,
                                    region=resource_obj_stats.region
                                )

                                min_rep = getattr(resource_obj_stats, 'min_instances', 0)
                                max_rep = getattr(resource_obj_stats, 'max_instances', 100)
                                new_instance_count = max(min_rep, min(desired_replicas, max_rep))

                                # Only apply if the count actually changes
                                if new_instance_count != resource_obj_stats.current_instances:
                                    logger.debug(
                                        f"Time {event_time:.2f}: Scaling {svc} from "
                                        f"{resource_obj_stats.current_instances} → {new_instance_count} "
                                        f" (workload {workload_rate:.2f} rps)"
                                    )
                                    resource_obj_stats.current_instances = new_instance_count
                                
                                # 3) Synchronize Petri Net with new capacity
                                if self.petri_net:
                                    slot_place_name = f"ResourceState_{resource_obj_stats.name}"
                                    slot_place = self.petri_net.places.get(slot_place_name)
                                    if slot_place:
                                        concurrency = getattr(resource_obj_stats, "concurrency", 1)
                                        desired_tokens = new_instance_count * concurrency
                                        current_tokens = len(slot_place.tokens)
                                        diff = desired_tokens - current_tokens
                                        if diff > 0:
                                            for _ in range(diff): slot_place.add_token(Token())
                                        elif diff < 0:
                                            for _ in range(abs(diff)):
                                                if slot_place.tokens: slot_place.remove_token()

                            # 4) Update resource capacity and utilization for this timeslice
                            # 4) Update resource capacity and utilization for this timeslice
                            active_instances = getattr(resource_obj_stats, 'current_instances', 1)
                            
                            # --- START OF THE NEW, CORRECTED LOGIC ---
                            # Check if the resource is a CloudRun instance for special handling
                            if 'CloudRun' in str(type(resource_obj_stats)):
                                # For Cloud Run, utilization is the ratio of active to max instances.
                                # This correctly models its load, as it scales to meet demand.
                                per_pod_rps = scaler.request_capacity
                                if active_instances:
                                    utilization = min(
                                        1.0,
                                        float(workload_rate) / (active_instances * per_pod_rps)
                                    )
                                else:
                                    utilization = 0.0

                                # Total “capacity” = number_of_instances * per-instance_capacity
                                total_capacity = active_instances * per_pod_rps

                                resource_obj_stats.capacity = total_capacity
                                resource_obj_stats.allocated_capacity = utilization * total_capacity
                            elif 'GKECluster' in str(type(resource_obj_stats)):
                                # Approximate cluster utilization directly from workload pressure.
                                total_capacity = getattr(resource_obj_stats, "capacity", 0.0)
                                if (not total_capacity or total_capacity <= 0.0) and hasattr(resource_obj_stats, "get_total_capacity"):
                                    try:
                                        total_capacity = float(resource_obj_stats.get_total_capacity())
                                    except Exception:
                                        total_capacity = 0.0
                                if total_capacity <= 0.0:
                                    node_count = len(getattr(resource_obj_stats, "nodes", []))
                                    total_capacity = float(node_count if node_count > 0 else 1.0)

                                utilization = 0.0
                                if total_capacity > 0.0:
                                    utilization = min(1.0, float(workload_rate) / total_capacity)
                                resource_obj_stats.capacity = total_capacity
                                resource_obj_stats.allocated_capacity = utilization * total_capacity
                            else:
                                # Original logic for all other non-autoscaling resource types
                                if self.petri_net:
                                    place_for_util = (
                                        self.petri_net.places.get(f"compute_in_{resource_obj_stats.name}")
                                        or self.petri_net.places.get(f"storage_in_{resource_obj_stats.name}")
                                        or self.petri_net.places.get(f"In_{resource_obj_stats.name}")
                                    )
                                else:
                                    place_for_util = None
                                live_requests = len(place_for_util.tokens) if place_for_util else 0
                                resource_obj_stats.allocated_capacity = float(live_requests)

                            # --- END OF THE NEW, CORRECTED LOGIC ---

                            # The rest of the loop now uses the correctly calculated utilization
                            if r_id_stats in resource_stats:
                                resource_stats[r_id_stats]["utilization"].append({"time": float(event_time), "value": resource_obj_stats.utilization_ratio})
                            # 5) Energy & Carbon Calculation
                            if self.energy_model and hasattr(resource_obj_stats, 'resource_type'):
                                try:
                                    identifier = resource_obj_stats.name       # e.g. "cr-aula-spring"
                                    capacity_for_energy = float(active_instances if active_instances else 1.0)
                                    if getattr(resource_obj_stats, 'use_named_energy_profile', False):
                                        capacity_for_energy = max(
                                            1.0,
                                            float(getattr(resource_obj_stats, 'capacity', active_instances))
                                        )
                                    power_kw = self.energy_model.calculate_resource_energy(
                                        utilization=resource_obj_stats.utilization_ratio,
                                        resource_type=identifier,             # ← now the lookup uses the exact name
                                        capacity=capacity_for_energy
                                    )
                                    energy_kwh = power_kw * (sampling_interval / 3600.0)
                                    resource_stats[r_id_stats]["energy_consumption"].append({"time": float(event_time), "value": energy_kwh})
                                    
                                    if self.carbon_model and hasattr(resource_obj_stats, 'region'):
                                        carbon_kg = self.carbon_model.calculate_resource_carbon(energy_consumption=energy_kwh, region=resource_obj_stats.region)
                                        resource_stats[r_id_stats]["carbon_footprint"].append({"time": float(event_time), "value": carbon_kg})
                                except Exception as e_metrics:
                                    logger.error(f"Error calculating energy/carbon for {r_id_stats}: {e_metrics}", exc_info=False)
                            # END OF NEW/MODIFIED LOGIC
                            
                            # The original logic for state changes and latency can remain here...
                            last_stats_update_time = self.current_time
                            if r_id_stats in resource_stats and hasattr(resource_obj_stats, 'state') and resource_obj_stats.state: # State changes
                                if not resource_stats[r_id_stats]["state_changes"] or \
                                   resource_stats[r_id_stats]["state_changes"][-1]["state"] != resource_obj_stats.state.value:
                                    resource_stats[r_id_stats]["state_changes"].append({"time": float(event_time), "state": resource_obj_stats.state.value})
                            
                            if self.latency_model and hasattr(resource_obj_stats, 'resource_type') and resource_obj_stats.resource_type:
                                try:
                                    latency_ms_val: float = self.latency_model.calculate_transition_delay(
                                        resource_type=resource_obj_stats.resource_type.value,
                                        utilization=resource_obj_stats.utilization_ratio if hasattr(resource_obj_stats, 'utilization_ratio') else 0.0,
                                        source_region=getattr(resource_obj_stats, 'region', None),
                                        dest_region=getattr(resource_obj_stats, 'region', None)
                                    )
                                    resource_stats[r_id_stats].setdefault("latency", []).append({"time": float(event_time), "value": latency_ms_val})
                                except Exception as e_lat:
                                    logger.error(f"Error calculating latency for {r_id_stats}: {e_lat}", exc_info=True)
                                    resource_stats[r_id_stats].setdefault("latency", []).append({"time": float(event_time), "value": -1.0})
                    
                    if self.config.enforce_constraints and self.model_builder and self.model_builder.resource_mapping:
                        violations: List[ConstraintViolation] = [] # Assuming this type
                        for r_id_con, res_con in self.model_builder.resource_mapping.items():
                            if res_con and self.constraint_manager:
                                res_violations: List[ConstraintViolation] = self.constraint_manager.validate_resource(res_con)
                                if res_violations: violations.extend(res_violations)
                        for violation in violations:
                            logger.warning(f"Constraint violation: {str(violation)}") # Or violation.message

                    last_stats_update_time = self.current_time

                if self.config.warmup_period is not None and isinstance(event_time, float) and event_time >= self.config.warmup_period:
                    if self.latency_model and self.petri_net and self.petri_net.transitions:
                        for trans_id_lat, trans_obj_lat in self.petri_net.transitions.items():
                            connected_resources_lat: List[Dict[str, Any]] = []
                            # Simplified logic for finding connected resources (assuming Resource object has needed attrs)
                            # This part is complex and depends heavily on PetriNet structure and Resource access
                            # Input arcs
                            if self.petri_net.input_arcs and trans_id_lat in self.petri_net.input_arcs:
                                for arc in self.petri_net.input_arcs[trans_id_lat]:
                                    if self.petri_net.places and arc.place_id in self.petri_net.places:
                                        place_arc = self.petri_net.places[arc.place_id]
                                        if place_arc.name in place_to_resource:
                                            res_arc = place_to_resource[place_arc.name]
                                            connected_resources_lat.append({
                                                'resource_id': res_arc.name if hasattr(res_arc, 'name') else 'unknown',
                                                'resource_type': res_arc.resource_type.value if hasattr(res_arc, 'resource_type') and res_arc.resource_type else 'unknown',
                                                'utilization': res_arc.utilization_ratio if hasattr(res_arc, 'utilization_ratio') else 0.0
                                            })
                            # Output arcs (similar logic)
                            if self.petri_net.output_arcs and trans_id_lat in self.petri_net.output_arcs:
                                 for arc in self.petri_net.output_arcs[trans_id_lat]:
                                    if self.petri_net.places and arc.place_id in self.petri_net.places:
                                        place_arc = self.petri_net.places[arc.place_id]
                                        if place_arc.name in place_to_resource:
                                            res_arc = place_to_resource[place_arc.name]
                                            connected_resources_lat.append({
                                                'resource_id': res_arc.name if hasattr(res_arc, 'name') else 'unknown',
                                                'resource_type': res_arc.resource_type.value if hasattr(res_arc, 'resource_type') and res_arc.resource_type else 'unknown',
                                                'utilization': res_arc.utilization_ratio if hasattr(res_arc, 'utilization_ratio') else 0.0
                                            })

                            if connected_resources_lat:
                                for flow_event_entry in reversed(token_flow_log): # flow_event renamed
                                    if flow_event_entry['event'] == 'transition_fired' and \
                                       isinstance(flow_event_entry, dict) and \
                                       flow_event_entry.get('transition') == trans_obj_lat.name : # Check it's TransitionFiredLog
                                        
                                        # This cast is to inform type checker after check, or use TypeGuard
                                        # For simplicity, we assume if event is "transition_fired", it's TransitionFiredLog
                                        # This can be made safer with isinstance checks against the TypedDicts if needed
                                        # For now, directly assign, relying on the event type string.
                                        
                                        # This part requires careful handling of TypedDict structure
                                        # The key 'resources' might not exist if the dict is not TransitionFiredLog
                                        # We ensure it's the correct type of dict before assigning
                                        # However, TypedDicts don't offer isinstance checks easily.
                                        # So, direct modification is risky if the list contains other dicts by mistake.
                                        # The logic assumes this is the correct entry.
                                        cast(TransitionFiredLog, flow_event_entry)['resources'] = connected_resources_lat
                                        cast(TransitionFiredLog, flow_event_entry)['delay'] = trans_obj_lat.delay if hasattr(trans_obj_lat, 'delay') else 0.0
                                        break
                
                # Log token distribution at regular intervals
                log_token_distribution()
                
            # End of main simulation loop

            statistics["token_flow"] = {
                "place_token_counts": place_token_counts,
                "transition_throughput": transition_throughput,
                "token_flow_log": token_flow_log # Already List[TokenFlowLogEntry]
            }

            plots_dir: str = os.path.join(self.config.output_dir, 'plots')
            os.makedirs(plots_dir, exist_ok=True)

            for place_id_plot, counts_plot in place_token_counts.items(): # place_id, counts
                if counts_plot: # Ensure not empty
                    times_plot: List[float] = [rec['time'] for rec in counts_plot]
                    values_plot: List[int] = [rec['count'] for rec in counts_plot]
                    place_name_plot: str = counts_plot[0]['place_name'] if counts_plot else place_id_plot
                    plot_time_series(times_plot, values_plot, f"Time vs Tokens for {place_name_plot}", "Time (s)", "Token Count",
                                     os.path.join(plots_dir, f"time_vs_tokens_{place_name_plot}.png"))

            for r_id_plot, stats_plot in resource_stats.items(): # r_id, stats
                if stats_plot.get("utilization"):
                    times_plot: List[float] = [rec['time'] for rec in stats_plot['utilization']]
                    values_plot: List[float] = [rec['value'] for rec in stats_plot['utilization']]
                    plot_time_series(times_plot, values_plot, f"Time vs Utilization for Resource {r_id_plot}", "Time (s)", "Utilization Ratio",
                                     os.path.join(plots_dir, f"time_vs_utilization_{r_id_plot}.png"))

            self.results["resource_latency_metrics"] = {}
            if self.model_builder and self.model_builder.resource_mapping:
                for r_id_lat_agg, stats_lat_agg in resource_stats.items():
                    res_name_lat_agg: str = self.model_builder.resource_mapping[r_id_lat_agg].name if r_id_lat_agg in self.model_builder.resource_mapping else r_id_lat_agg
                    latency_values_agg: List[float] = [e['value'] for e in stats_lat_agg.get("latency", []) if e['value'] is not None and e['value'] >=0]
                    if latency_values_agg:
                        self.results["resource_latency_metrics"][res_name_lat_agg] = {
                            "avg_latency_ms_per_sample": float(np.mean(latency_values_agg)),
                            "max_latency_ms_per_sample": float(np.max(latency_values_agg)),
                            "min_latency_ms_per_sample": float(np.min(latency_values_agg)),
                            "num_latency_samples": len(latency_values_agg)
                        }
                    else: # Default if no latency values
                        self.results["resource_latency_metrics"][res_name_lat_agg] = {"avg_latency_ms_per_sample":0,"max_latency_ms_per_sample":0,"min_latency_ms_per_sample":0,"num_latency_samples":0}


            self.end_time = time.time()
            execution_time: float = (self.end_time - self.start_time) if self.start_time and self.end_time else 0.0

            workload_stats_final: Dict[str, WorkloadStatistics] = {} # workload_stats renamed
            for i, workload_obj in enumerate(self.workloads): # workload renamed
                workload_stats_final[f"workload_{i}"] = workload_obj.get_statistics()

            resource_utilization_final: Dict[str, Dict[str, float]] = {} # resource_utilization renamed
            for r_id_util, stats_util in resource_stats.items():
                if stats_util.get("utilization"):
                    util_values: List[float] = [u["value"] for u in stats_util["utilization"]]
                    if util_values: # Avoid division by zero if empty
                        resource_utilization_final[r_id_util] = {
                            "avg": sum(util_values) / len(util_values), "max": max(util_values), "min": min(util_values)
                        }

            resource_energy_final: Dict[str, Dict[str, float]] = {} # resource_energy_consumption renamed
            interval_duration_h: float = (sampling_interval / 3600.0) if sampling_interval > 0 else 0
            total_duration_h: float = (sim_duration / 3600.0) if sim_duration > 0 else 0
            for r_id_en, stats_en in resource_stats.items():
                if stats_en.get("energy_consumption"):
                    energy_kwh_vals: List[float] = [e["value"] for e in stats_en["energy_consumption"]]
                    total_kwh: float = sum(energy_kwh_vals)
                    avg_power_kw: float = (total_kwh / total_duration_h) if total_duration_h > 0 else 0
                    power_kw_vals_int: List[float] = [(e_kwh / interval_duration_h) for e_kwh in energy_kwh_vals] if interval_duration_h > 0 else []
                    resource_energy_final[r_id_en] = {
                        "total_kwh": total_kwh, "avg_power_kw": avg_power_kw,
                        "max_power_kw_interval": max(power_kw_vals_int) if power_kw_vals_int else 0,
                        "min_power_kw_interval": min(power_kw_vals_int) if power_kw_vals_int else 0
                    }
            
            resource_carbon_final: Dict[str, Dict[str, float]] = {} # resource_carbon_footprint renamed
            for r_id_carb, stats_carb in resource_stats.items():
                 if stats_carb.get("carbon_footprint"):
                    carb_kg_vals: List[float] = [c["value"] for c in stats_carb["carbon_footprint"]]
                    total_kg_co2e: float = sum(carb_kg_vals)
                    avg_emission_rate: float = (total_kg_co2e / total_duration_h) if total_duration_h > 0 else 0
                    emission_rate_vals_int: List[float] = [(c_kg / interval_duration_h) for c_kg in carb_kg_vals] if interval_duration_h > 0 else []
                    resource_carbon_final[r_id_carb] = {
                        "total_kg_co2e": total_kg_co2e, "avg_emission_rate_kg_co2e_per_hour": avg_emission_rate,
                        "max_emission_rate_kg_co2e_per_hour_interval": max(emission_rate_vals_int) if emission_rate_vals_int else 0,
                        "min_emission_rate_kg_co2e_per_hour_interval": min(emission_rate_vals_int) if emission_rate_vals_int else 0
                    }
            actual_active_tokens_in_petri_net = 0
            if self.petri_net and self.petri_net.places:
                for place in self.petri_net.places.values():
                    actual_active_tokens_in_petri_net += len(place.tokens)
                            
            # simulation_info population
            self.results = {
                "simulation_info": {
                    "duration": sim_duration, 
                    "simulation_time_reached": self.current_time, 
                    "execution_time": execution_time,
                    "total_tokens": completed_token_count + actual_active_tokens_in_petri_net, # Total ever in system that weren't just ephemeral workload events
                    "completed_tokens": completed_token_count,
                    "active_tokens": actual_active_tokens_in_petri_net # Actual tokens remaining
                },
                "workload_stats": workload_stats_final,
                "resource_utilization": resource_utilization_final,
                "resource_utilization_history": {},
                "resource_energy_consumption": resource_energy_final,
                "resource_carbon_footprint": resource_carbon_final,
                "resource_carbon_history": {},
                "resource_states": {r_id_state: stats_state["state_changes"] for r_id_state, stats_state in resource_stats.items() if "state_changes" in stats_state},
                "errors": self.errors,
                "token_flow": statistics.get("token_flow"),
                "resource_info": {
                    r_id_info: {
                        "type": res_info.resource_type.value if hasattr(res_info, 'resource_type') and res_info.resource_type else None,
                        "capacity": res_info.capacity if hasattr(res_info, 'capacity') else None,
                        "region": res_info.region if hasattr(res_info, 'region') else None
                    }
                    for r_id_info, res_info in (self.model_builder.resource_mapping.items() if self.model_builder and self.model_builder.resource_mapping else {})
                }
            }
            # Build resource utilization history from recorded data
            # First get history from resource_stats which contains the raw data from fixed intervals
            for r_id, stats in resource_stats.items():
                if 'utilization' in stats and stats['utilization']:
                    self.results['resource_utilization_history'][r_id] = [
                        {'time': rec['time'], 'utilization': rec['value']} 
                        for rec in stats['utilization']
                    ]
            
            # Fall back to resource mapping if no history in resource_stats
            if not self.results['resource_utilization_history'] and self.model_builder and self.model_builder.resource_mapping:
                for r_id, res in self.model_builder.resource_mapping.items():
                    if hasattr(res, 'utilization_history') and res.utilization_history:
                        self.results['resource_utilization_history'][r_id] = [
                            {'time': rec.timestamp, 'utilization': rec.utilization}
                            for rec in res.utilization_history
                            if hasattr(rec, 'timestamp') and hasattr(rec, 'utilization')
                        ]
            
            # Ensure we have carbon history if available
            if self.model_builder and self.model_builder.resource_mapping:
                for r_id, resource in self.model_builder.resource_mapping.items():
                    if hasattr(resource, 'carbon_history') and resource.carbon_history:
                        self.results['resource_carbon_history'][r_id] = [
                            {'time': rec.timestamp, 'carbon': rec.carbon}
                            for rec in resource.carbon_history
                            if hasattr(rec, 'timestamp') and hasattr(rec, 'carbon')
                        ]

            if self.latency_model: self.results["latency_metrics"] = self.latency_model.calculate(self.results)
            if self.energy_model: self.results["energy_metrics"] = self.energy_model.calculate(self.results)
            if self.carbon_model:
                energy_data_carb: Dict[str, Any] = self.results.get("energy_metrics", {})
                self.results["carbon_metrics"] = self.carbon_model.calculate(self.results, energy_data_carb)
            if self.scaling_model: self.results["scaling_metrics"] = self.scaling_model.calculate(self.results)

            final_completed_count: int = len(completed_tokens) if completed_tokens is not None else completed_token_count
            logger.info(f"Simulation completed: {final_completed_count} tokens processed in {execution_time:.2f} seconds")
            self.state = SimulationState.COMPLETED

            final_eps: float = 0.0
            if self.start_time:
                current_wall_time_final: float = time.time()
                time_diff_final: float = current_wall_time_final - self.start_time
                if time_diff_final > 0: final_eps = processed_events / time_diff_final
            self._display_simulation_progress(
                current_sim_time=self.current_time, total_duration=sim_duration, completed_tokens=completed_token_count,
                pending_events=len(event_queue), errors_count=len(self.errors), events_per_sec=final_eps,
                final_status=self.state.value # Display final state
            )
            return self.results

        except Exception as e_sim:
            logger.error(f"Error during simulation: {str(e_sim)}", exc_info=True) # Add exc_info
            logger.debug(f"Token Flow Log (at error): {token_flow_log}") # Log state of token_flow_log
            self.state = SimulationState.FAILED
            self.errors.append({"stage": "simulation", "error": str(e_sim), "time": self.current_time})
            # self._handle_error(e_sim) # Method not defined
            return {"error": str(e_sim), "partial_results": self.results, "token_flow_at_error": token_flow_log}
        finally:
            final_eps_exit: float = 0.0
            if not hasattr(self, 'results') or self.results is None: # Should be initialized
                 self.results = {"status": "incomplete", "errors": self.errors if hasattr(self, 'errors') else []}

            # Use local variables if they exist, or orchestrator state if not (e.g. if error before loop)
            processed_events_val: int = processed_events if 'processed_events' in locals() else 0
            completed_token_count_val: int = completed_token_count if 'completed_token_count' in locals() else 0
            event_queue_val: List[Any] = event_queue if 'event_queue' in locals() else [] # event_queue is local
            duration_val: float = sim_duration if 'sim_duration' in locals() else (duration or getattr(self.config, "duration", 0.0))


            if hasattr(self, 'start_time') and self.start_time is not None:
                current_wall_time_exit: float = time.time()
                time_diff_exit: float = current_wall_time_exit - self.start_time
                if time_diff_exit > 0: final_eps_exit = processed_events_val / time_diff_exit
            
            current_sim_time_final: float = self.current_time if hasattr(self, 'current_time') else 0.0
            errors_count_final: int = len(self.errors) if hasattr(self, 'errors') else 0

            if hasattr(self, '_display_simulation_progress'):
                self._display_simulation_progress(
                    current_sim_time=current_sim_time_final, total_duration=duration_val,
                    completed_tokens=completed_token_count_val, pending_events=len(event_queue_val),
                    errors_count=errors_count_final, events_per_sec=final_eps_exit,
                    final_status=self.state.value if hasattr(self, 'state') else SimulationState.FAILED.value
                )
            sys.stdout.write("\n")
            sys.stdout.flush()

    def run_concurrent_simulations(self, configurations: List[Dict[str, Any]],
                                   max_workers: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Run multiple simulations concurrently with different configurations.
        """
        num_max_workers: int = max_workers or self.config.max_workers # max_workers renamed

        logger.info(f"Running {len(configurations)} concurrent simulations with {num_max_workers} workers")
        results_list: List[Dict[str, Any]] = [] # results renamed

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_max_workers) as executor:
                futures_list: List[concurrent.futures.Future[Dict[str, Any]]] = [] # futures renamed, added Future return type
                for i, config_item in enumerate(configurations): # config renamed
                    sim_config_obj: LEAFCloudConfig = LEAFCloudConfig(**config_item) # sim_config renamed
                    future_obj: concurrent.futures.Future[Dict[str, Any]] = executor.submit(self._run_single_simulation, sim_config_obj, i) # future renamed
                    futures_list.append(future_obj)

                for completed_future in concurrent.futures.as_completed(futures_list): # future renamed
                    try:
                        result_item: Dict[str, Any] = completed_future.result() # result renamed
                        results_list.append(result_item)
                    except Exception as e_future:
                        logger.error(f"Error in concurrent simulation future: {str(e_future)}")
                        results_list.append({"error": str(e_future)}) # Add error placeholder

        except Exception as e_concurrent:
            logger.error(f"Error orchestrating concurrent simulations: {str(e_concurrent)}")
            self.errors.append({"stage": "concurrent_execution", "error": str(e_concurrent)})

        logger.info(f"Completed {len(results_list)} concurrent simulations")
        return results_list

    def _run_single_simulation(self, config_obj: LEAFCloudConfig, sim_id: int) -> Dict[str, Any]: # config renamed
        """
        Run a single simulation with the given configuration.
        """
        sim_orchestrator = Orchestrator() # New instance
        sim_orchestrator.config = config_obj
        terraform_data_single: Optional[Dict[str, Any]] = None # terraform_data renamed

        try:
            if config_obj.terraform_dir:
                terraform_data_single = sim_orchestrator.parse_terraform() # Use instance's method

            # Pass terraform_data_single to build_model
            sim_orchestrator.build_model(terraform_data_single if terraform_data_single else None)


            workload_config_single: Dict[str, Any] = { # workload_config renamed
                "type": config_obj.workload_type,
                "params": config_obj.workload_params
            }
            sim_orchestrator.configure_workload(workload_config_single)

            if config_obj.constraint_config_path:
                sim_orchestrator.setup_constraints() # Uses its own config

            sim_orchestrator.setup_metrics() # Uses its own config

            result_single: Dict[str, Any] = sim_orchestrator.run_simulation() # result renamed

            result_single["simulation_id"] = sim_id
            result_single["config"] = config_obj.to_dict() # Use config_obj

            return result_single

        except Exception as e_single:
            logger.error(f"Error in simulation {sim_id}: {str(e_single)}", exc_info=True) # Add exc_info
            return {
                "simulation_id": sim_id,
                "error": str(e_single),
                "config": config_obj.to_dict(), # Use config_obj
                "status": "failed"
            }

    def _should_retry_operation(self, error: Exception, current_retry_attempt: int, max_retries: int, retry_delay: float, operation_name: str) -> bool:
        """
        Determines if an operation should be retried.
        """
        if current_retry_attempt < max_retries:
            logger.info(
                f"Retrying {operation_name} (attempt {current_retry_attempt + 1}/{max_retries}) "
                f"after error: {str(error)}. Waiting {retry_delay:.2f}s."
            )
            time.sleep(retry_delay)
            return True
        else:
            logger.error(
                f"Max retries ({max_retries}) reached for {operation_name} "
                f"after error: {str(error)}. No more retries."
            )
            return False

    def stop_simulation(self) -> None:
        """Request simulation to stop at the next event processing"""
        logger.info("Stopping simulation...")
        self._stop_requested = True

    def export_results(self, output_path: Optional[str] = None, file_format: str = 'json') -> str:
        """
        Export simulation results to file.
        """
        export_output_path: str # output_path renamed for clarity
        if not output_path:
            timestamp_str: str = datetime.now().strftime("%Y%m%d_%H%M%S") # timestamp renamed
            output_dir_path: str = self.config.output_dir # output_dir renamed
            os.makedirs(output_dir_path, exist_ok=True)
            export_output_path = os.path.join(output_dir_path, f"{timestamp_str}_results.{file_format}") # Use file_format in name
        else:
            export_output_path = output_path
        
        # Ensure the directory for the output_path exists if specified directly
        os.makedirs(os.path.dirname(export_output_path), exist_ok=True)


        if file_format == 'json':
            with open(export_output_path, 'w') as f_json: # f renamed
                json.dump(self.results, f_json, indent=2)
        elif file_format == 'yaml':
            with open(export_output_path, 'w') as f_yaml: # f renamed
                yaml.dump(self.results, f_yaml, default_flow_style=False)
        elif file_format == 'csv':
            # Flattening results for CSV is complex and depends on structure.
            # This basic version will likely produce a non-ideal CSV.
            try:
                df: pd.DataFrame = pd.DataFrame.from_dict(self.results, orient='index').transpose() # Basic attempt
                # More robust would be to select specific parts of results or use json_normalize
                # For example, if results has a list of records: pd.DataFrame(self.results['some_list_key'])
                df.to_csv(export_output_path, index=False)
            except Exception as e_csv:
                logger.error(f"Could not convert results to CSV directly: {e_csv}. Exporting as JSON instead.")
                # Fallback to JSON
                export_output_path_fallback = export_output_path.replace(".csv", "_fallback.json")
                with open(export_output_path_fallback, 'w') as f_json_fallback:
                     json.dump(self.results, f_json_fallback, indent=2)
                logger.info(f"Simulation results (fallback) exported to {export_output_path_fallback}")
                return export_output_path_fallback

        else:
            raise ValueError(f"Unsupported file format: {file_format}. Supported formats are: json, yaml, csv")

        logger.info(f"Simulation results exported to {export_output_path}")
        return export_output_path

    def resume_simulation(self) -> Dict[str, Any]:   
        """Resume a paused simulation"""
        if self.state == SimulationState.PAUSED:
            logger.info("Resuming simulation...")
            self._stop_requested = False # Allow simulation to run again
            # Note: Resuming a DES like this is non-trivial.
            # run_simulation currently re-initializes many things.
            # Proper resume requires saving/restoring the full simulation state (event queue, current time, etc.)
            # This current implementation will likely restart a new simulation for the remaining duration.
            remaining_duration: float = self.config.duration - self.current_time
            if remaining_duration <= 0:
                logger.warning("Cannot resume: remaining duration is zero or negative.")
                return self.results # Or an empty dict indicating no further run
            return self.run_simulation(duration=remaining_duration)
        else:
            logger.warning(f"Cannot resume: simulation is in {self.state.value} state, not PAUSED")
            return self.results
