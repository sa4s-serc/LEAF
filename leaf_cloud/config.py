"""Configuration management for the LEAF-Cloud framework.

This module provides a comprehensive configuration system for the LEAF-Cloud framework,
handling loading, validation, and access to all configuration parameters. It supports
multiple configuration sources including YAML/JSON files and environment variables.
"""
import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Type,
    Union,
    get_type_hints,
)

import yaml

from .exceptions import ConfigError
# Import validators
from .validators import (
    SchemaValidationError,
    SchemaVersionError,
    ValidationError,
    validate_in_range,
    validate_positive_number,
)
from .validators.config_validator import ConfigValidator

# Type aliases for better code readability
ConfigDict = Dict[str, Any]
PathLike = Union[str, os.PathLike]

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class WorkloadConfig:
    """Configuration for workload generation.

    Attributes:
        type: Type of workload to simulate. One of ["steady", "burst", "cyclical", "random", "custom"].
              Defaults to "steady".
        params: Dictionary of workload-specific parameters. For "steady" workload, this should
               contain at least {"rate": float}.
        enabled: Whether workload generation is enabled. Defaults to True.
        warmup_period: Time in seconds before starting workload generation. Defaults to 0.0.
        cooldown_period: Time in seconds after stopping workload generation. Defaults to 0.0.
        jitter: Standard deviation of random jitter to add to inter-arrival times. Defaults to 0.0.
    """
    type: str = "steady"
    params: Dict[str, Any] = field(default_factory=lambda: {"rate": 100.0})
    enabled: bool = True
    warmup_period: float = 0.0
    cooldown_period: float = 0.0
    jitter: float = 0.0

    def _update_from_dict(self, data: Dict[str, Any]) -> None:
        """Update configuration from a dictionary."""
        if not isinstance(data, dict):
            logger.warning(f"Expected dictionary for workload config, got {type(data).__name__}")
            return

        for key, value in data.items():
            if hasattr(self, key):
                try:
                    if key == 'params' and isinstance(value, dict):
                        self.params.update(value)
                    else:
                        setattr(self, key, value)
                except (AttributeError, TypeError, ValueError) as e:
                    logger.warning(f"Could not set workload.{key}={value!r}: {e}")
            else:
                logger.warning(f"Unknown workload configuration key: {key}")

    def _validate(self) -> None:
        """Validate workload configuration."""
        valid_types = ["steady", "burst", "cyclical", "random", "custom"]
        if self.type not in valid_types:
            raise ValueError(f"workload.type must be one of {valid_types}, got {self.type}")
        
        if not isinstance(self.params, dict):
            raise ValueError("workload.params must be a dictionary")
        
        if self.type == "steady" and "rate" not in self.params:
            raise ValueError("workload.params must contain 'rate' for 'steady' workload type")


@dataclass
class SimulationConfig:
    """Configuration for simulation parameters.

    Attributes:
        duration: Total simulation time in seconds. Defaults to 3600.0 (1 hour).
        time_step: Time between simulation steps in seconds. Defaults to 1.0.
        iterations: Number of simulation iterations to run. Defaults to 1.
        warmup_period: Time in seconds before starting metric collection. Defaults to 60.0.
        workload: Workload configuration. Defaults to a steady workload with rate=100.0.
        max_workers: Maximum number of worker threads for parallel execution. Defaults to 4.
    """
    duration: float = 3600.0
    time_step: float = 1.0
    iterations: int = 1
    warmup_period: float = 60.0
    workload: WorkloadConfig = field(default_factory=WorkloadConfig)
    max_workers: int = 4

    def _update_from_dict(self, data: Dict[str, Any]) -> None:
        """Update configuration from a dictionary.

        Args:
            data: Dictionary containing configuration key-value pairs.

        Note:
            Unknown keys will generate a warning but won't raise an exception.
            Type conversion is handled automatically where possible.
        """
        if not isinstance(data, dict):
            logger.warning(
                f"Expected dictionary for simulation config, got {type(data).__name__}"
            )
            return

        # Handle workload configuration specially
        if 'workload' in data and hasattr(self, 'workload'):
            if isinstance(data['workload'], dict):
                self.workload._update_from_dict(data['workload'])
                # Remove from data to avoid processing again
                data = {k: v for k, v in data.items() if k != 'workload'}
            elif data['workload'] is not None:
                logger.warning(
                    f"Expected dictionary for workload config, got {type(data['workload']).__name__}"
                )

        # Process remaining keys
        for key, value in data.items():
            if key == 'workload':
                continue  # Already processed
                
            if hasattr(self, key):
                try:
                    current_value = getattr(self, key)
                    if isinstance(current_value, dict) and isinstance(value, dict):
                        current_value.update(value)
                    else:
                        setattr(self, key, value)
                except (AttributeError, TypeError, ValueError) as e:
                    logger.warning(
                        f"Could not set attribute {key}={value!r} on {self.__class__.__name__}: {e}"
                    )
            else:
                logger.warning(
                    f"Unknown configuration key in {self.__class__.__name__}: {key}"
                )

    def _validate(self) -> None:
        """Validate simulation configuration.
        
        Raises:
            ValueError: If any validation checks fail.
        """
        # Validate duration
        if not isinstance(self.duration, (int, float)) or self.duration <= 0:
            raise ValueError(f"duration must be positive, got {self.duration}")
            
        # Validate time_step
        if not isinstance(self.time_step, (int, float)) or self.time_step <= 0:
            raise ValueError(f"time_step must be a positive number, got {self.time_step}")
            
        # Validate iterations
        if not isinstance(self.iterations, int) or self.iterations < 1:
            raise ValueError(f"iterations must be a positive integer, got {self.iterations}")
            
        # Validate warmup_period
        if not isinstance(self.warmup_period, (int, float)) or self.warmup_period < 0:
            raise ValueError(f"warmup_period must be a non-negative number, got {self.warmup_period}")
            
        # Validate max_workers
        if not isinstance(self.max_workers, int) or self.max_workers < 1:
            raise ValueError(f"max_workers must be a positive integer, got {self.max_workers}")
            
        # Validate workload configuration
        if hasattr(self, 'workload') and self.workload is not None:
            self.workload._validate()
        else:
            # For backward compatibility, create a default workload config
            self.workload = WorkloadConfig()


@dataclass
class MetricsConfig:
    """Configuration for metrics collection and reporting.
    
    Attributes:
        enabled: Whether metrics collection is enabled. Defaults to True.
        collection_interval: Interval in seconds between metrics collection. Defaults to 1.0.
        output_file: Path to output metrics file. Defaults to 'metrics.json'.
        include_timestamps: Whether to include timestamps in metrics. Defaults to True.
        track_resources: Whether to track resource-level metrics. Defaults to True.
        track_aggregates: Whether to track aggregate metrics. Defaults to True.
        track_utilization: Whether to track resource utilization. Defaults to True.
        track_throughput: Whether to track request throughput. Defaults to True.
        track_latency: Whether to track request latency. Defaults to True.
    """
    enabled: bool = True
    collection_interval: float = 1.0
    output_file: str = 'metrics.json'
    include_timestamps: bool = True
    track_resources: bool = True
    track_aggregates: bool = True
    track_utilization: bool = True
    track_throughput: bool = True
    track_latency: bool = True

    def _update_from_dict(self, data: Dict[str, Any]) -> None:
        """Update configuration from a dictionary."""
        if not isinstance(data, dict):
            logger.warning(f"Expected dictionary for metrics config, got {type(data).__name__}")
            return
        
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                logger.warning(f"Unknown metrics config key: {key}")


class LEAFCloudConfig:
    """Central configuration class for the LEAF-Cloud framework.

    This class handles loading, validation, and access to all configuration parameters
    used throughout the LEAF-Cloud framework. It supports multiple configuration
    sources including YAML/JSON files and environment variables, with proper precedence
    and type conversion.

    Configuration can be loaded using the class methods:
    - `from_file()`: Load from a specific YAML/JSON file
    - `load()`: Load with multiple sources (defaults, env.yaml, environment variables)

    The configuration is validated on load and can be saved back to a file.

    Attributes:
        version: Configuration schema version. Defaults to '1.0'.
        base_dir: Base directory for relative paths. Defaults to current working directory.
        terraform_dir: Directory containing Terraform configuration files. Defaults to 'terraform'.
        var_files: List of variable files to load. Defaults to ['variables.tf'].
        output_dir: Directory for output files. Defaults to 'results'.
        env_config_path: Path to environment configuration file. Defaults to 'env.yaml'.
        collect_latency: Whether to collect latency metrics. Defaults to True.
        collect_energy: Whether to collect energy metrics. Defaults to True.
        collect_carbon: Whether to collect carbon metrics. Defaults to True.
        collect_scaling: Whether to collect scaling metrics. Defaults to True.
        enforce_constraints: Whether to enforce configuration constraints. Defaults to True.
        constraint_config_path: Path to constraint configuration file. Defaults to 'constraints.yaml'.
        recovery_strategy: Strategy for handling failures. One of ['retry', 'ignore', 'abort']. Defaults to 'retry'.
        max_retries: Maximum number of retry attempts. Defaults to 3.
        retry_delay: Delay between retry attempts in seconds. Defaults to 5.0.
        gcp_region: Default GCP region. Defaults to 'us-central1'.
        gcp_project_id: GCP project ID. Defaults to None.
        simulation: Simulation configuration.
        logging: Logging configuration.
        metrics: Metrics collection configuration.
        models: Model configurations (energy, carbon, latency, scaling).
    """

    # --------------------------------------------------------------------------
    # Initialization and Core Methods
    # --------------------------------------------------------------------------
    def __init__(self, **kwargs):
        """Initialize LEAFCloudConfig with default values.

        Args:
            **kwargs: Configuration overrides. Keys should match attribute names.
        """
        # Set default values
        self.version = "1.0"
        self.base_dir = str(Path.cwd())
        self.terraform_dir = "terraform"
        self.var_files = []
        self.output_dir = str(Path("results").resolve())
        self.env_config_path = None

        # Feature flags
        self.collect_latency = True
        self.collect_energy = True
        self.collect_carbon = True
        self.collect_scaling = True
        self.enforce_constraints = True
        self.constraint_config_path = None

        # Recovery settings
        self.recovery_strategy = "retry"  # Options: "retry", "fail", "continue"
        self.max_retries = 3
        self.retry_delay = 5.0  # seconds

        # GCP specific settings
        self.gcp_region = "us-central1"
        self.gcp_project_id = None

        # Initialize nested configs
        self.simulation = SimulationConfig()
        self.logging = self.LoggingConfig()
        self.metrics = MetricsConfig()
        self.models = self.ModelConfigs()
        self.latency = self.LatencyModelConfig()
        self.energy = self.EnergyModelConfig()
        self.carbon = self.CarbonModelConfig()
        self.scaling = self.ScalingModelConfig()

        # Apply any kwargs overrides
        if kwargs:
            self._update_from_dict(kwargs)

    def __post_init__(self):
        """Initialize nested configurations if not provided."""
        if not hasattr(self, "simulation") or self.simulation is None:
            self.simulation = SimulationConfig()
        if not hasattr(self, "logging") or self.logging is None:
            self.logging = self.LoggingConfig()
        if not hasattr(self, "metrics") or self.metrics is None:
            self.metrics = MetricsConfig()
        if not hasattr(self, "models") or self.models is None:
            self.models = self.ModelConfigs()

    # --------------------------------------------------------------------------
    # Classmethods for Loading Configuration
    # --------------------------------------------------------------------------
    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "LEAFCloudConfig":
        """Create a LEAFCloudConfig instance from a dictionary."""
        config = cls()
        config.update(config_dict)
        return config

    @classmethod
    def from_file(cls, config_path: Union[str, os.PathLike]) -> "LEAFCloudConfig":
        """
        Load configuration from a YAML/JSON file.

        Args:
            config_path: Path to the configuration file (str or Path-like)

        Returns:
            Loaded LEAFCloudConfig object

        Raises:
            FileNotFoundError: If the config file doesn't exist
            ValueError: If there's an error parsing the config file
        """
        config_path = Path(config_path).expanduser().resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                if config_path.suffix.lower() in ('.yaml', '.yml'):
                    config_data = yaml.safe_load(f)
                elif config_path.suffix.lower() == '.json':
                    config_data = json.load(f)
                else:
                    raise ValueError(
                        f"Unsupported config file format: {config_path.suffix}. "
                        "Supported formats: .yaml, .yml, .json"
                    )

            if not isinstance(config_data, dict):
                raise ValueError("Configuration file must contain a dictionary/mapping")

            return cls.from_dict(config_data)

        except (yaml.YAMLError, json.JSONDecodeError) as e:
            raise ValueError(f"Error parsing configuration file {config_path}: {e}") from e
            
    def load_from_file(self, config_path: Union[str, os.PathLike]) -> None:
        """
        Load configuration from a YAML/JSON file into this instance.
        
        This is an instance method that updates the current instance with values
        from the specified configuration file.
        
        Args:
            config_path: Path to the configuration file (str or Path-like)
            
        Raises:
            FileNotFoundError: If the config file doesn't exist
            ValueError: If there's an error parsing the config file
        """
        config_path = Path(config_path).expanduser().resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
            
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                if config_path.suffix.lower() in ('.yaml', '.yml'):
                    config_data = yaml.safe_load(f)
                elif config_path.suffix.lower() == '.json':
                    config_data = json.load(f)
                else:
                    raise ValueError(
                        f"Unsupported config file format: {config_path.suffix}. "
                        "Supported formats: .yaml, .yml, .json"
                    )
                    
            if not isinstance(config_data, dict):
                raise ValueError("Configuration file must contain a dictionary/mapping")
                
            self._update_from_dict(config_data)
            
        except (yaml.YAMLError, json.JSONDecodeError) as e:
            raise ValueError(f"Error parsing configuration file {config_path}: {e}") from e

            # Ensure required attributes are set
            if not hasattr(config, "base_dir") or not config.base_dir:
                config.base_dir = str(Path.cwd())

            # Initialize any missing nested configs
            if hasattr(config, "__post_init__"):
                config.__post_init__()

            # Ensure the config is valid before returning
            if hasattr(config, "validate"):
                config.validate()

            logger.info(f"Successfully loaded configuration from {config_path}")
            return config

        except (yaml.YAMLError, json.JSONDecodeError) as e:
            logger.error(f"Error parsing configuration file {config_path}: {e}")
            raise ValueError(f"Error parsing configuration file {config_path}: {e}")
        except Exception as e:
            logger.error(f"Error loading configuration from {config_path}: {e}")
            raise

    @classmethod
    def load(
        cls,
        config_path: Optional[str] = None,
        env_yaml_path: Optional[str] = "env.yaml",
        env_prefix: str = "LEAF_",
    ) -> "LEAFCloudConfig":
        """
        Load configuration with precedence:
        1. Default values (lowest priority)
        2. env.yaml file (if exists)
        3. Configuration file (if provided)
        4. Environment variables (highest priority)

        Args:
            config_path: Optional path to a specific config file.
            env_yaml_path: Path to env.yaml file (default: "env.yaml").
            env_prefix: Prefix for environment variables (default: LEAF_).

        Returns:
            Configured LEAFCloudConfig object.
        """
        # Start with default values
        config = cls()
        logger.debug(f"Default config values: gcp_region={config.gcp_region}")
        
        # First, load from env.yaml if it exists
        env_config = {}
        if env_yaml_path and os.path.exists(env_yaml_path):
            logger.info(f"Attempting to load configuration from {env_yaml_path}")
            try:
                with open(env_yaml_path, "r") as f:
                    if env_yaml_path.endswith(".json"):
                        env_config = json.load(f) or {}
                    else:
                        env_config = yaml.safe_load(f) or {}
                
                if env_config:
                    logger.info(
                        f"Configuration successfully loaded from {env_yaml_path}: {env_config}"
                    )
                else:
                    logger.info(
                        f"{env_yaml_path} is empty, skipping environment config."
                    )
            except (json.JSONDecodeError, yaml.YAMLError) as e:
                logger.error(
                    f"Error parsing configuration file {env_yaml_path}: {str(e)}"
                )
            except Exception as e:
                logger.error(
                    f"Error loading configuration from {env_yaml_path}: {str(e)}"
                )
        
        # Then load from main config file if provided
        file_config = {}
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    file_config = yaml.safe_load(f) or {}
                if file_config:
                    logger.info(
                        f"Configuration loaded from specific file {config_path}: {file_config}"
                    )
            except (yaml.YAMLError, IOError) as e:
                logger.error(f"Error loading specific config file {config_path}: {e}")
        
        # Merge configs with proper precedence:
        # 1. Start with defaults (already in config)
        logger.debug(f"Config before any overrides: gcp_region={config.gcp_region}")
        
        # 2. Apply main config file overrides (lower precedence)
        if file_config:
            logger.debug(f"Applying main config file overrides: {file_config}")
            config._update_from_dict(file_config)
            logger.debug(f"After main config overrides: gcp_region={config.gcp_region}")
            
        # 3. Apply env.yaml overrides (higher precedence than main config)
        if env_config:
            logger.debug(f"Applying env.yaml overrides: {env_config}")
            config._update_from_dict(env_config)
            logger.debug(f"After env.yaml overrides: gcp_region={config.gcp_region}")
            
        # 4. Finally, apply environment variable overrides (highest precedence)
        logger.info(f"Applying environment variable overrides with prefix {env_prefix}")
        config.update_from_env(prefix=env_prefix)
        logger.debug(f"After env var overrides: gcp_region={config.gcp_region}")
        
        # Log the final state of all config values
        logger.debug(f"Final config values: {config.to_dict()}")

        # Initialize and validate the final config
        config.__post_init__()
        config.validate()
        return config

    # --------------------------------------------------------------------------
    # Public Methods for Managing Configuration
    # --------------------------------------------------------------------------
    def update(self, config_dict: Dict[str, Any]) -> None:
        """Update configuration from a dictionary."""
        if not isinstance(config_dict, dict):
            raise ValueError("Configuration must be a dictionary")

        for key, value in config_dict.items():
            if hasattr(self, key):
                attr = getattr(self, key)
                if is_dataclass(attr) and isinstance(value, dict):
                    # Update nested dataclass
                    attr._update_from_dict(value)
                else:
                    # Update simple attribute
                    setattr(self, key, value)
            elif key == "version":
                # Handle version separately
                if value != self.version:
                    logger.warning(
                        f"Configuration version mismatch: expected {self.version}, got {value}"
                    )
            else:
                logger.warning(f"Unknown configuration key: {key}")

    def update_from_env(self, prefix: str = "LEAF_") -> None:
        """Update configuration from environment variables."""
        for key, value in os.environ.items():
            if key.startswith(prefix):
                # Convert LEAF_SIMULATION_DURATION to simulation.duration
                parts = key[len(prefix) :].lower().split("__")
                self._set_nested_value(parts, value)

    def validate(self) -> None:
        """Validate the current configuration.
        
        Raises:
            ConfigError: If any validation checks fail.
        """
        errors = []
        
        # Validate version
        if not isinstance(self.version, str) or not self.version:
            errors.append("Version must be a non-empty string")
            
        # Validate paths
        self._validate_paths(errors)
        
        # Validate feature flags
        for flag in ["collect_latency", "collect_energy", "collect_carbon", "collect_scaling", "enforce_constraints"]:
            if not isinstance(getattr(self, flag), bool):
                errors.append(f"{flag} must be a boolean value")
                
        # Validate recovery settings
        self._validate_recovery_strategy(errors)
        
        # Validate GCP settings
        if not isinstance(self.gcp_region, str) or not self.gcp_region:
            errors.append("GCP region must be a non-empty string")
            
        # Validate nested configurations
        self.simulation._validate()
        self.logging._validate()
        self._validate_model_configs(errors)
        
        # If any errors were found, raise them
        if errors:
            raise ConfigError("\n".join([f"- {error}" for error in errors]))
    
    def _validate_paths(self, errors: List[str]) -> None:
        """Validate file and directory paths in the configuration."""
        # Check if base directory exists and is a directory
        if not os.path.isdir(self.base_dir):
            errors.append(f"Base directory does not exist: {self.base_dir}")
            
        # Check if terraform directory exists and is a directory
        terraform_path = os.path.join(self.base_dir, self.terraform_dir)
        if not os.path.isdir(terraform_path):
            errors.append(f"Terraform directory does not exist: {terraform_path}")
            
        # Check if output directory is writable
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            test_file = os.path.join(self.output_dir, ".writable_test")
            with open(test_file, 'w') as f:
                f.write("test")
            os.remove(test_file)
        except (OSError, IOError) as e:
            errors.append(f"Output directory is not writable: {self.output_dir} - {str(e)}")
    
    def _validate_recovery_strategy(self, errors: List[str]) -> None:
        """Validate recovery strategy configuration."""
        valid_strategies = ["retry", "ignore", "abort"]
        if self.recovery_strategy not in valid_strategies:
            errors.append(
                f"Invalid recovery_strategy: {self.recovery_strategy}. "
                f"Must be one of: {', '.join(valid_strategies)}"
            )
            
        if not isinstance(self.max_retries, int) or self.max_retries < 0:
            errors.append(f"max_retries must be a non-negative integer, got {self.max_retries}")
            
        if not isinstance(self.retry_delay, (int, float)) or self.retry_delay < 0:
            errors.append(f"retry_delay must be a non-negative number, got {self.retry_delay}")
    
    def _validate_model_configs(self, errors: List[str]) -> None:
        """Validate all model configurations."""
        # Validate energy model
        self._validate_energy_config(errors)
        
        # Validate carbon model
        self._validate_carbon_config(errors)
        
        # Validate latency model
        self._validate_latency_config(errors)
        
        # Validate scaling model
        self._validate_scaling_config(errors)
    
    def _validate_energy_config(self, errors: List[str]) -> None:
        """Validate energy model configuration."""
        energy = self.models.energy
        
        # Validate weights
        for component, weight in energy.weights.items():
            if not isinstance(weight, (int, float)) or weight < 0:
                errors.append(f"Energy weight for {component} must be a non-negative number, got {weight}")
        
        # Validate function parameters
        for func_name, func_params in energy.functions.items():
            for param_name, param_value in func_params.items():
                if not isinstance(param_value, (int, float)) or param_value < 0:
                    errors.append(
                        f"Energy function {func_name}.{param_name} must be a non-negative number, "
                        f"got {param_value}"
                    )
    
    def _validate_carbon_config(self, errors: List[str]) -> None:
        """Validate carbon model configuration."""
        carbon = self.models.carbon
        
        # Validate region factors
        for region, factor in carbon.regions.items():
            if not isinstance(factor, (int, float)) or factor < 0:
                errors.append(f"Carbon factor for region {region} must be a non-negative number, got {factor}")
        
        # Validate default factor
        if not isinstance(carbon.default_factor, (int, float)) or carbon.default_factor < 0:
            errors.append(f"Default carbon factor must be a non-negative number, got {carbon.default_factor}")
    
    def _validate_latency_config(self, errors: List[str]) -> None:
        """Validate latency model configuration."""
        try:
            # Use the LatencyModelConfig's own validation method
            if hasattr(self, 'latency') and self.latency is not None:
                self.latency._validate()
            elif hasattr(self, 'models') and hasattr(self.models, 'latency') and self.models.latency is not None:
                self.models.latency._validate()
        except ValueError as e:
            errors.append(f"Latency configuration error: {str(e)}")
    
    def _validate_scaling_config(self, errors: List[str]) -> None:
        """Validate scaling model configuration."""
        scaling = self.models.scaling
        
        # Validate thresholds
        for threshold in ["cpu_threshold", "memory_threshold"]:
            value = getattr(scaling, threshold)
            if not isinstance(value, (int, float)) or not (0 <= value <= 1):
                errors.append(f"{threshold} must be a number between 0 and 1, got {value}")
        
        # Validate pod counts
        if not isinstance(scaling.min_pods, int) or scaling.min_pods < 0:
            errors.append(f"min_pods must be a non-negative integer, got {scaling.min_pods}")
            
        if not isinstance(scaling.max_pods, int) or scaling.max_pods <= 0:
            errors.append(f"max_pods must be a positive integer, got {scaling.max_pods}")
            
        if scaling.min_pods > scaling.max_pods:
            errors.append(f"min_pods ({scaling.min_pods}) cannot be greater than max_pods ({scaling.max_pods})")
        
        # Validate other numeric parameters
        for param in ["scaling_buffer", "cooldown_period"]:
            value = getattr(scaling, param)
            if not isinstance(value, (int, float)) or value < 0:
                errors.append(f"{param} must be a non-negative number, got {value}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to a dictionary.

        Returns:
            Dict containing all configuration parameters, with nested objects converted to dictionaries.
        """
        result = {}
        for key, value in self.__dict__.items():
            if is_dataclass(value):
                result[key] = asdict(value)
            elif isinstance(value, list):
                result[key] = [
                    asdict(item) if is_dataclass(item) else item for item in value
                ]
            elif isinstance(value, dict):
                result[key] = {
                    k: asdict(v) if is_dataclass(v) else v for k, v in value.items()
                }
            else:
                result[key] = value
        return result

    def save(self, config_path: Union[str, Path], format: str = None) -> str:
        """
        Save the current configuration to a file.

        Args:
            config_path: Path where to save the configuration file.
            format: Output format ('yaml' or 'json'). If None, inferred from file extension.

        Returns:
            str: The path to which the configuration was saved.

        Raises:
            ValueError: If an unsupported format is specified or if there's an error saving the file.
            OSError: If the file cannot be written to the specified path.
        """
        config_path = Path(config_path)

        # Determine format from file extension if not specified
        if format is None:
            if config_path.suffix.lower() == ".json":
                format = "json"
            elif config_path.suffix.lower() in (".yaml", ".yml"):
                format = "yaml"
            else:
                raise ValueError(
                    "Could not determine format from file extension. "
                    "Please specify format='yaml' or format='json'"
                )

        format = format.lower()
        if format not in ("yaml", "json"):
            raise ValueError(f"Unsupported format: {format}. Must be 'yaml' or 'json'")

        config_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            config_dict = self.to_dict()

            with open(config_path, "w", encoding="utf-8") as f:
                if format == "yaml":
                    yaml.dump(
                        config_dict,
                        f,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                        Dumper=yaml.SafeDumper,
                    )
                else:  # json
                    json.dump(
                        config_dict,
                        f,
                        indent=2,
                        ensure_ascii=False,
                        sort_keys=False,
                        default=str,
                    )

            logger.info(f"Configuration saved to {config_path} in {format.upper()} format")
            return str(config_path)

        except (yaml.YAMLError, json.JSONDecodeError) as e:
            raise ValueError(f"Failed to serialize configuration to {format.upper()}: {e}")
        except OSError as e:
            raise OSError(f"Failed to write configuration to {config_path}: {e}")

    def hash(self) -> str:
        """
        Generate a consistent hash for the configuration object.

        Returns:
            str: A hexadecimal string representing the hash of the configuration.
        """

        def serialize(obj: Any) -> Any:
            """Recursively serialize an object to a hashable format."""
            if obj is None or isinstance(obj, (str, int, float, bool)):
                return obj

            if isinstance(obj, (list, tuple, set)):
                return sorted([serialize(item) for item in obj], key=str)

            if isinstance(obj, dict):
                return {str(k): serialize(v) for k, v in sorted(obj.items())}

            if isinstance(obj, (datetime, Path)):
                return str(obj)

            if hasattr(obj, "__dict__") or is_dataclass(obj):
                if hasattr(obj, "to_dict"):
                    return serialize(obj.to_dict())
                d = asdict(obj) if is_dataclass(obj) else vars(obj)
                return serialize(d)

            return str(obj)

        try:
            serialized = serialize(self)
            json_str = json.dumps(
                serialized, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
            return hashlib.sha256(json_str.encode("utf-8")).hexdigest()
        except Exception as e:
            logger.warning(f"Error generating config hash: {e}")
            return hashlib.sha256(str(id(self)).encode("utf-8")).hexdigest()

    def __hash__(self):
        return int(self.hash(), 16)

    # --------------------------------------------------------------------------
    # Helper and Private Methods
    # --------------------------------------------------------------------------
    def _set_nested_value(self, path: List[str], value: Any) -> None:
        """Set a nested configuration value from a path list."""
        current = self
        for part in path[:-1]:
            if hasattr(current, part):
                current = getattr(current, part)
            else:
                logger.warning(f"Unknown configuration section: {'.'.join(path)}")
                return

        attr_name = path[-1]
        if hasattr(current, attr_name):
            current_value = getattr(current, attr_name)
            if current_value is not None:
                value_type = type(current_value)
                try:
                    if value_type == bool:
                        value = value.lower() in ("true", "1", "t", "y", "yes")
                    else:
                        value = value_type(value)
                except (ValueError, TypeError) as e:
                    logger.warning(
                        f"Could not convert {value} to {value_type.__name__} for {'.'.join(path)}: {e}"
                    )
                    return
            setattr(current, attr_name, value)

    def _update_from_dict(self, data: Dict[str, Any]) -> None:
        """Update configuration from a dictionary."""
        if not data:
            return
            
        logger.debug(f"_update_from_dict called with data: {data}")

        for key, value in data.items():
            if value is None:
                continue

            if hasattr(self, key):
                current_value = getattr(self, key)
                try:
                    # Handle nested configuration objects
                    if hasattr(current_value, "_update_from_dict") and isinstance(value, dict):
                        current_value._update_from_dict(value)
                    # Handle dictionaries
                    elif isinstance(current_value, dict) and isinstance(value, dict):
                        # Only update existing keys, don't add new ones
                        for k, v in value.items():
                            if k in current_value:
                                current_value[k] = v
                    # Handle lists
                    elif isinstance(current_value, list) and isinstance(value, list):
                        current_value.extend(v for v in value if v is not None)
                    # Handle all other types (including strings, numbers, etc.)
                    else:
                        if isinstance(value, str) and value.lower() == "none":
                            value = None
                        # Directly set the attribute on self for top-level attributes
                        logger.debug(f"Setting attribute {key} = {value} (current value: {getattr(self, key, 'N/A')})")
                        setattr(self, key, value)
                        logger.debug(f"After setting, {key} = {getattr(self, key, 'N/A')}")
                        
                        # If the attribute has a __post_init__ method, call it
                        attr = getattr(self, key, None)
                        if hasattr(attr, "__post_init__"):
                            logger.debug(f"Calling __post_init__ on {key}")
                            attr.__post_init__()

                except Exception as e:
                    logger.warning(f"Error updating config key '{key}': {e}")
                    logger.debug("Error details:", exc_info=True)
            else:
                logger.warning(f"Unknown configuration key: {key}")

    # --------------------------------------------------------------------------
    # Strict Validation Methods
    # --------------------------------------------------------------------------
    def strict_validation(self) -> bool:
        """Perform comprehensive validation of the configuration.

        Returns:
            True if the configuration is valid.

        Raises:
            ConfigError: If any validation checks fail.
        """
        errors: List[str] = []

        self._validate_simulation_config(errors)
        self._validate_logging_config(errors)
        self._validate_paths(errors)
        self._validate_workload_type(errors)
        self._validate_recovery_strategy(errors)
        self._validate_model_configs(errors)

        if errors:
            error_message = "\n- " + "\n- ".join(errors)
            raise ConfigError(
                f"Configuration validation failed with {len(errors)} error(s):{error_message}"
            )

        logger.info("Configuration validated successfully.")
        return True

    def _validate_simulation_config(self, errors: List[str]) -> None:
        """Validate simulation configuration parameters."""
        if not hasattr(self, "simulation"):
            errors.append("Missing required configuration section: 'simulation'")
            return

        class SimulationConfigValidator(ConfigValidator):
            def validate(self) -> bool:
                try:
                    validate_positive_number(
                        float(self.config.duration), "simulation.duration"
                    )
                    validate_positive_number(
                        int(self.config.iterations), "simulation.iterations", min_val=1
                    )
                    validate_positive_number(
                        float(self.config.time_step), "simulation.time_step"
                    )
                    validate_positive_number(
                        float(self.config.warmup_period),
                        "simulation.warmup_period",
                        min_val=0,
                    )
                except (ValidationError, ValueError) as e:
                    self._add_error(str(e))
                return not self.errors

        validator = SimulationConfigValidator(self.simulation, errors)
        validator.validate()

    def _validate_logging_config(self, errors: List[str]) -> None:
        """Validate logging configuration."""
        if not hasattr(self, "logging"):
            return

        class LoggingConfigValidator(ConfigValidator):
            def validate(self) -> bool:
                valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
                if hasattr(self.config, "level"):
                    if str(self.config.level).upper() not in valid_log_levels:
                        self._add_error(
                            f"logging.level must be one of {sorted(valid_log_levels)}, got {self.config.level!r}"
                        )
                if hasattr(self.config, "file") and self.config.file:
                    try:
                        log_path = Path(self.config.file).resolve()
                        log_path.parent.mkdir(parents=True, exist_ok=True)
                        test_file = log_path.parent / f".test_{os.urandom(8).hex()}"
                        test_file.touch()
                        test_file.unlink()
                    except (OSError, IOError) as e:
                        self._add_error(f"Cannot write to log file {self.config.file}: {e}")
                return not self.errors

        validator = LoggingConfigValidator(self.logging, errors)
        validator.validate()

    def _validate_paths(self, errors: List[str]) -> None:
        """Validate file and directory paths in the configuration."""
        if self.terraform_dir and not os.path.isdir(self.terraform_dir):
            errors.append(
                f"terraform_dir does not exist or is not a directory: {self.terraform_dir}"
            )
        if self.var_files:
            if not isinstance(self.var_files, list):
                errors.append("var_files must be a list of file paths")
            else:
                for i, var_file in enumerate(self.var_files):
                    if not os.path.isfile(var_file):
                        errors.append(f"Variable file at index {i} does not exist: {var_file}")
        if self.output_dir:
            try:
                Path(self.output_dir).mkdir(parents=True, exist_ok=True)
            except (TypeError, OSError) as e:
                errors.append(f"Invalid output_dir: {e}")

    def _validate_workload_type(self, errors: List[str]) -> None:
        """Validate workload type configuration."""
        if hasattr(self, "simulation") and hasattr(self.simulation, "workload_type"):
            valid_types = {"steady", "burst", "cyclical", "random", "custom", "mix"}
            if self.simulation.workload_type not in valid_types:
                errors.append(
                    f"simulation.workload_type must be one of {sorted(valid_types)}, "
                    f"got {self.simulation.workload_type!r}"
                )

    def _validate_recovery_strategy(self, errors: List[str]) -> None:
        """Validate recovery strategy configuration."""
        valid_strategies = {"retry", "ignore", "abort"}
        if self.recovery_strategy not in valid_strategies:
            errors.append(
                f"recovery_strategy must be one of {sorted(valid_strategies)}, "
                f"got {self.recovery_strategy!r}"
            )

    def _validate_model_configs(self, errors: List[str]) -> None:
        """Validate model configurations."""
        if not hasattr(self, "models"):
            return
        model_validations = {
            "energy": self._validate_energy_config,
            "carbon": self._validate_carbon_config,
            "latency": self._validate_latency_config,
            "scaling": self._validate_scaling_config,
        }
        for name, func in model_validations.items():
            if hasattr(self.models, name):
                try:
                    func(errors)
                except Exception as e:
                    errors.append(f"Error validating {name} configuration: {e}")

    def _validate_energy_config(self, errors: List[str]) -> None:
        """Validate energy model configuration."""
        energy = self.models.energy
        if hasattr(energy, "weights"):
            for resource, weight in energy.weights.items():
                if not isinstance(weight, (int, float)) or weight < 0:
                    errors.append(
                        f"energy.weights['{resource}'] must be a non-negative number, got {weight!r}"
                    )

    def _validate_carbon_config(self, errors: List[str]) -> None:
        """Validate carbon model configuration."""
        carbon = self.models.carbon
        if hasattr(carbon, "regions"):
            for region, factor in carbon.regions.items():
                if not isinstance(factor, (int, float)) or factor < 0:
                    errors.append(
                        f"carbon.regions['{region}'] must be a non-negative number, got {factor!r}"
                    )

    def _validate_latency_config(self, errors: List[str]) -> None:
        """Validate latency model configuration."""
        latency = self.models.latency
        if hasattr(latency, "base_latency"):
            for region, latencies in latency.base_latency.items():
                if isinstance(latencies, dict):
                    for service, value in latencies.items():
                        if not isinstance(value, (int, float)) or value < 0:
                            errors.append(
                                f"latency.base_latency['{region}']['{service}'] must be a non-negative number, got {value!r}"
                            )

    def _validate_scaling_config(self, errors: List[str]) -> None:
        """Validate scaling model configuration."""
        scaling = self.models.scaling
        if not (0 < scaling.cpu_threshold <= 1):
            errors.append(
                f"scaling.cpu_threshold must be between 0 and 1, got {scaling.cpu_threshold!r}"
            )
        if not (0 < scaling.memory_threshold <= 1):
            errors.append(
                f"scaling.memory_threshold must be between 0 and 1, got {scaling.memory_threshold!r}"
            )
        if not (isinstance(scaling.min_pods, int) and scaling.min_pods >= 0):
            errors.append(
                f"scaling.min_pods must be a non-negative integer, got {scaling.min_pods!r}"
            )
        if not (isinstance(scaling.max_pods, int) and scaling.max_pods > 0):
            errors.append(
                f"scaling.max_pods must be a positive integer, got {scaling.max_pods!r}"
            )
        if scaling.min_pods > scaling.max_pods:
            errors.append(
                f"scaling.min_pods ({scaling.min_pods}) cannot be greater than "
                f"scaling.max_pods ({scaling.max_pods})"
            )

    # --------------------------------------------------------------------------
    # Nested Configuration Classes
    # --------------------------------------------------------------------------
    @dataclass
    class LoggingConfig:
        """Configuration for logging."""
        log_level: str = "INFO"
        log_file: Optional[str] = None
        log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        file_mode: str = "a"

        def _update_from_dict(self, data: Dict[str, Any]) -> None:
            if not isinstance(data, dict):
                return
            for key, value in data.items():
                if hasattr(self, key):
                    if key == "log_level" and value:
                        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
                        if str(value).upper() not in valid_levels:
                            logger.warning(
                                f"Invalid log level: {value}. Using default: {self.log_level}"
                            )
                            continue
                    setattr(self, key, value)

        def _validate(self) -> None:
            valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
            if self.log_level.upper() not in valid_levels:
                raise ValueError(
                    f"Invalid log_level: {self.log_level}. Must be one of: {', '.join(valid_levels)}"
                )
            if self.file_mode not in ["a", "w"]:
                raise ValueError(
                    f"file_mode must be 'a' (append) or 'w' (overwrite), got {self.file_mode}"
                )

        def setup_logging(self) -> None:
            from ..utils.logging_utils import configure_logging
            configure_logging(
                log_level=self.log_level,
                log_file=self.log_file,
                log_format=self.log_format,
                suppress_third_party=False,
            )
            logger.info(f"Logging configured at level {self.log_level}")
            if self.log_file:
                logger.info(f"Logging to file: {self.log_file}")

    @dataclass
    class EnergyModelConfig:
        """Configuration for energy consumption modeling."""
        weights: Dict[str, float] = field(default_factory=lambda: {
            "compute": 1.0, "storage": 0.7, "network": 0.5, "security": 0.3, "default": 0.5
        })
        functions: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
            # These are fallback curves only; for CloudRun and CloudSQL we use calibrated per-class models.
            "compute": {"idle": 0.01, "coef": 0.05, "exp": 1.1},
            "storage": {"idle": 0.05, "coef": 0.15, "exp": 1.1},
            "network": {"idle": 0.01, "coef": 0.05, "exp": 1.1},
            "default": {"idle": 0.05, "coef": 0.2, "exp": 1.2},
        })
        profiles: Dict[str, Any] = field(default_factory=lambda: {
            # Kubernetes workloads and cluster wrappers are logical resources; power is accounted for via nodes.
            "k8s-workload": {"idle": 0.0, "max": 0.0},
            "gke-cluster": {"idle": 0.0, "max": 0.0},
        })

        def _update_from_dict(self, data: Dict[str, Any]) -> None:
            if not isinstance(data, dict):
                return
            for key, value in data.items():
                if not hasattr(self, key):
                    logger.warning(f"Unknown energy model config key: {key}")
                    continue
                current_value = getattr(self, key)
                if isinstance(current_value, dict) and isinstance(value, dict):
                    current_value.update(value)

    @dataclass
    class CarbonModelConfig:
        """Configuration for carbon emission modeling."""
        regions: Dict[str, float] = field(default_factory=lambda: {
            "us-central1": 0.2152373529, "us-east1": 0.379, "us-west1": 0.076,
            "europe-west1": 0.088, "europe-west4": 0.417, "asia-east1": 0.541,
            "global": 0.450
        })
        default_factor: float = 0.450

        def _update_from_dict(self, data: Dict[str, Any]) -> None:
            if not isinstance(data, dict):
                return
            for key, value in data.items():
                if not hasattr(self, key):
                    logger.warning(f"Unknown carbon model config key: {key}")
                    continue
                current_value = getattr(self, key)
                if key == "regions" and isinstance(current_value, dict) and isinstance(value, dict):
                    current_value.update(value)
                else:
                    setattr(self, key, value)

    @dataclass
    class LatencyModelConfig:
        """Configuration for latency modeling."""
        base_latency: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
            "compute": {"default": 10.0}, "storage": {"default": 20.0},
            "network": {"default": 20.0}, "security": {"default": 15.0},
            "default": 20.0
        })
        stochastic_variation: Dict[str, Any] = field(default_factory=lambda: {
            "enabled": False, "distribution": "uniform",
            "params": {"uniform": {"min": -0.1, "max": 0.1}}
        })
        congestion_params: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
            "compute": {"threshold": 0.75, "factor": 2.5, "exp": 2.0},
            "default": {"threshold": 0.70, "factor": 2.0, "exp": 2.0},
        })
        region_latency_factors: Dict[str, float] = field(default_factory=lambda: {
            "us-central1_us-east1": 1.2, "same-region": 1.0, "different-region": 2.0
        })
        
        # New fields for end-to-end latency
        end_to_end_enabled: bool = False
        user_to_infrastructure_latency: Dict[str, float] = field(default_factory=lambda: {
            # Same region - minimal additional latency
            "us-central1_us-central1": 5.0,
            "us-east1_us-east1": 5.0,
            "europe-west1_europe-west1": 5.0,
            "asia-southeast1_asia-southeast1": 5.0,
            
            # Cross-region latencies (realistic internet latencies)
            "us-central1_us-east1": 45.0,
            "us-east1_us-central1": 45.0,
            "us-central1_europe-west1": 120.0,
            "europe-west1_us-central1": 120.0,
            "us-east1_europe-west1": 85.0,
            "europe-west1_us-east1": 85.0,
            "us-central1_asia-southeast1": 180.0,
            "asia-southeast1_us-central1": 180.0,
            "us-east1_asia-southeast1": 200.0,
            "asia-southeast1_us-east1": 200.0,
            "europe-west1_asia-southeast1": 160.0,
            "asia-southeast1_europe-west1": 160.0,
        })
        user_distribution: Dict[str, float] = field(default_factory=lambda: {
        "asia-southeast1": 1.0,
        })
        default_user_to_infra_latency: float = 50.0

        def _update_from_dict(self, data: Dict[str, Any]) -> None:
            if not isinstance(data, dict):
                return
            for key, value in data.items():
                if not hasattr(self, key):
                    logger.warning(f"Unknown latency model config key: {key}")
                    continue
                current_value = getattr(self, key)
                if isinstance(current_value, dict) and isinstance(value, dict):
                    # Deep merge for nested dictionaries
                    for sub_key, sub_value in value.items():
                        if sub_key in current_value and isinstance(current_value[sub_key], dict) and isinstance(sub_value, dict):
                            current_value[sub_key].update(sub_value)
                        else:
                            current_value[sub_key] = sub_value
                else:
                    setattr(self, key, value)
        
        def _validate(self) -> None:
            """Validate latency model configuration.
            
            Raises:
                ValueError: If any validation checks fail.
            """
            # Validate base latency configuration
            if not isinstance(self.base_latency, dict):
                raise ValueError("base_latency must be a dictionary")
            
            # Validate stochastic variation configuration
            if not isinstance(self.stochastic_variation, dict):
                raise ValueError("stochastic_variation must be a dictionary")
            
            # Validate congestion parameters
            if not isinstance(self.congestion_params, dict):
                raise ValueError("congestion_params must be a dictionary")
            
            # Validate region latency factors
            if not isinstance(self.region_latency_factors, dict):
                raise ValueError("region_latency_factors must be a dictionary")
            
            # Validate end-to-end latency configuration
            if not isinstance(self.end_to_end_enabled, bool):
                raise ValueError("end_to_end_enabled must be a boolean")
            
            if self.end_to_end_enabled:
                self._validate_end_to_end_config()
        
        def _validate_end_to_end_config(self) -> None:
            """Validate end-to-end latency specific configuration."""
            # Validate user-to-infrastructure latency matrix
            if not isinstance(self.user_to_infrastructure_latency, dict):
                raise ValueError("user_to_infrastructure_latency must be a dictionary")
            
            for key, value in self.user_to_infrastructure_latency.items():
                if not isinstance(key, str):
                    raise ValueError(f"user_to_infrastructure_latency keys must be strings, got {type(key)}")
                if not isinstance(value, (int, float)) or value < 0:
                    raise ValueError(f"user_to_infrastructure_latency values must be non-negative numbers, got {value} for key {key}")
                
                # Validate key format (should be "region1_region2")
                if "_" not in key:
                    raise ValueError(f"user_to_infrastructure_latency key must be in format 'user_region_infra_region', got {key}")
            
            # Validate user distribution
            if not isinstance(self.user_distribution, dict):
                raise ValueError("user_distribution must be a dictionary")
            
            if self.user_distribution:
                # Check that all values are valid percentages
                total_percentage = 0.0
                for region, percentage in self.user_distribution.items():
                    if not isinstance(region, str):
                        raise ValueError(f"user_distribution keys must be region names (strings), got {type(region)}")
                    if not isinstance(percentage, (int, float)) or percentage < 0 or percentage > 1:
                        raise ValueError(f"user_distribution values must be between 0 and 1, got {percentage} for region {region}")
                    total_percentage += percentage
                
                # Check that percentages sum to approximately 1.0 (allow small floating point errors)
                if abs(total_percentage - 1.0) > 0.01:
                    raise ValueError(f"user_distribution percentages must sum to 1.0, got {total_percentage}")
            
            # Validate default user-to-infrastructure latency
            if not isinstance(self.default_user_to_infra_latency, (int, float)) or self.default_user_to_infra_latency < 0:
                raise ValueError("default_user_to_infra_latency must be a non-negative number")
        
        def get_user_to_infra_latency(self, user_region: str, infra_region: str) -> float:
            """Get user-to-infrastructure latency for given regions.
            
            Args:
                user_region: User's region
                infra_region: Infrastructure region
                
            Returns:
                Latency in milliseconds
            """
            key = f"{user_region}_{infra_region}"
            return self.user_to_infrastructure_latency.get(key, self.default_user_to_infra_latency)
        
        def get_default_user_distribution(self, available_regions: List[str]) -> Dict[str, float]:
            """Get default equal user distribution for given regions.
            
            Args:
                available_regions: List of available infrastructure regions
                
            Returns:
                Dictionary mapping regions to equal percentages
            """
            if not available_regions:
                return {}
            
            equal_percentage = 1.0 / len(available_regions)
            return {region: equal_percentage for region in available_regions}

    @dataclass
    class ScalingModelConfig:
        """Configuration for scaling behavior."""
        request_capacity: float = 10.0
        strict_hpa_only: bool = True
        requests_per_vcpu: float = 25.0
        cpu_threshold: float = 0.8
        memory_threshold: float = 0.8
        min_pods: int = 1
        max_pods: int = 100
        scaling_buffer: float = 0.2
        cooldown_period: int = 300
        autoscaling_groups: Dict[str, Dict[str, Any]] = field(default_factory=dict)
        region_latency_factors: Dict[str, float] = field(default_factory=dict)
        region_capacity_factors: Dict[str, float] = field(default_factory=dict)
        default_iam_capacity: int = 10000
        default_scc_monitored_assets: int = 100
        default_scc_tier: str = "STANDARD"
        default_armor_protection_tier: str = "CLOUD_ARMOR"

        def _update_from_dict(self, data: Dict[str, Any]) -> None:
            if not isinstance(data, dict):
                return
            for key, value in data.items():
                if not hasattr(self, key):
                    logger.warning(f"Unknown scaling model config key: {key}")
                    continue
                current_value = getattr(self, key)
                if isinstance(current_value, dict) and isinstance(value, dict):
                    current_value.update(value)
                else:
                    try:
                        expected_type = self.__annotations__.get(key)
                        if expected_type in (int, float):
                            setattr(self, key, expected_type(value))
                        else:
                            setattr(self, key, value)
                    except (TypeError, ValueError) as e:
                        logger.warning(f"Invalid value for {key}: {value}. Error: {e}")

    @dataclass
    class ModelConfigs:
        """Container for all model configurations."""
        energy: "LEAFCloudConfig.EnergyModelConfig" = field(default_factory=lambda: LEAFCloudConfig.EnergyModelConfig())
        carbon: "LEAFCloudConfig.CarbonModelConfig" = field(default_factory=lambda: LEAFCloudConfig.CarbonModelConfig())
        latency: "LEAFCloudConfig.LatencyModelConfig" = field(default_factory=lambda: LEAFCloudConfig.LatencyModelConfig())
        scaling: "LEAFCloudConfig.ScalingModelConfig" = field(default_factory=lambda: LEAFCloudConfig.ScalingModelConfig())

        def _update_from_dict(self, data: Dict[str, Any]) -> None:
            if not isinstance(data, dict):
                return
            for model_name, model_data in data.items():
                if hasattr(self, model_name) and isinstance(model_data, dict):
                    model_config = getattr(self, model_name)
                    if hasattr(model_config, "_update_from_dict"):
                        model_config._update_from_dict(model_data)

        def _get_model_names(self) -> List[str]:
            return [name for name, _ in get_type_hints(self.__class__).items() if not name.startswith("_")]


# Create a default configuration instance for easy import
default_config = LEAFCloudConfig()


def load_config(
    config_path: Optional[Union[str, Path]] = None,
    env_yaml_path: Optional[Union[str, Path]] = "env.yaml",
    env_prefix: str = "LEAF_",
) -> LEAFCloudConfig:
    """
    Load configuration from multiple sources with precedence:
    1. Environment variables (with given prefix)
    2. Configuration file (if provided)
    3. Default values

    Args:
        config_path: Path to the main configuration file (YAML/JSON).
        env_yaml_path: Path to environment-specific YAML file (e.g., env.yaml).
        env_prefix: Prefix for environment variables.

    Returns:
        Configured LEAFCloudConfig object.
    """
    return LEAFCloudConfig.load(
        config_path=str(config_path) if config_path else None,
        env_yaml_path=str(env_yaml_path) if env_yaml_path else None,
        env_prefix=env_prefix,
    )


