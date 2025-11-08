import os
import json
import yaml
import logging
from typing import Dict, List, Optional, Any, Union
from pathlib import Path
from dataclasses import dataclass, field, fields

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ConfigValidationError(Exception):
    """Exception raised for configuration validation errors."""
    pass

@dataclass
class LEAFCloudConfig:
    """
    Central configuration class for the LEAF-Cloud framework.
    Handles loading, validation, and access to all configuration parameters.
    """
    # Base paths and directories
    base_dir: str = field(default_factory=lambda: os.getcwd())
    terraform_dir: Optional[str] = None
    var_files: List[str] = field(default_factory=list)
    output_dir: str = "results"
    env_config_path: Optional[str] = None
    
    # Simulation parameters
    duration: float = 3600.0  # Simulation time in seconds
    time_step: float = 1.0    # Simulation step size
    iterations: int = 1       # Number of iterations
    warmup_period: float = 60.0  # Warmup period before collecting metrics
    
    # Workload configuration
    # workload_params['rate']: workload generation rate (requests per second)
    workload_type: str = "steady"
    workload_params: Dict[str, Any] = field(default_factory=lambda: {"rate": 100.0})  # rate: requests per second
    
    # Metric collection
    collect_latency: bool = True
    collect_energy: bool = True
    collect_carbon: bool = True
    collect_scaling: bool = True
    
    # Constraint handling
    enforce_constraints: bool = True
    constraint_config_path: Optional[str] = None
    
    # Recovery strategy
    recovery_strategy: str = "retry"
    max_retries: int = 3
    retry_delay: float = 5.0
    
    # Concurrency
    max_workers: int = 4
    
    # GCP specific settings
    gcp_region: str = "us-central1"
    gcp_project_id: Optional[str] = None
    
    # Regional carbon factors (kg CO2e per kWh)
    carbon_factors: Dict[str, float] = field(default_factory=lambda: {
        "us-central1": 0.479,
        "us-east1": 0.530,
        "us-east4": 0.338,
        "us-west1": 0.130,
        "us-west2": 0.285,
        "us-west3": 0.240,
        "europe-west1": 0.171,
        "europe-west4": 0.440,
        "asia-east1": 0.622,
        "asia-southeast1": 0.490
    })
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    def __post_init__(self):
        """Set up logging based on configuration"""
        numeric_level = getattr(logging, self.log_level.upper(), None)
        if isinstance(numeric_level, int):
            logging.basicConfig(level=numeric_level, format=self.log_format)
        
        # Ensure paths are absolute
        if self.terraform_dir and not os.path.isabs(self.terraform_dir):
            self.terraform_dir = os.path.join(self.base_dir, self.terraform_dir)
        
        if self.output_dir and not os.path.isabs(self.output_dir):
            self.output_dir = os.path.join(self.base_dir, self.output_dir)
            
        # Create output directory if it doesn't exist
        if self.output_dir:
            os.makedirs(self.output_dir, exist_ok=True)
    
    @classmethod
    def from_file(cls, config_path: str) -> 'LEAFCloudConfig':
        """
        Load configuration from a YAML/JSON file.
        
        Args:
            config_path: Path to the configuration file
            
        Returns:
            Loaded LEAFCloudConfig object
        """
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        try:
            with open(config_path, 'r') as f:
                if config_path.endswith('.json'):
                    config_dict = json.load(f)
                else:
                    config_dict = yaml.safe_load(f)

            if config_dict is None:
                config_dict = {}
            
            known_keys = {f.name for f in fields(cls)}
            filtered_config_dict = {k: v for k, v in config_dict.items() if k in known_keys}
            # --- END OF FIX ---

            # Create config instance with the filtered values
            config = cls(**filtered_config_dict)
            logger.info(f"Configuration loaded from {config_path}")
            return config
            
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            logger.error(f"Error parsing configuration file {config_path}: {str(e)}")
            raise ConfigValidationError(f"Invalid configuration format: {str(e)}")
        except Exception as e:
            logger.error(f"Error loading configuration: {str(e)}")
            raise
    
    @classmethod
    def from_env(cls, env_prefix: str = "LEAF_") -> 'LEAFCloudConfig':
        """
        Load configuration from environment variables.
        
        Args:
            env_prefix: Prefix for environment variables (default: LEAF_)
            
        Returns:
            LEAFCloudConfig object with values from environment variables
        """
        # Start with default configuration
        config = cls()
        
        # Look for environment variables with the specified prefix
        for key, value in os.environ.items():
            if key.startswith(env_prefix):
                config_key = key[len(env_prefix):].lower()
                if hasattr(config, config_key):
                    attr_type = type(getattr(config, config_key))
                    try:
                        # Convert environment variable to the appropriate type
                        if attr_type == bool:
                            setattr(config, config_key, value.lower() in ('true', 'yes', '1'))
                        elif attr_type == int:
                            setattr(config, config_key, int(value))
                        elif attr_type == float:
                            setattr(config, config_key, float(value))
                        elif attr_type == list:
                            setattr(config, config_key, value.split(','))
                        elif attr_type == dict:
                            # For dictionaries, use JSON parsing
                            setattr(config, config_key, json.loads(value))
                        else:
                            setattr(config, config_key, value)
                    except (ValueError, json.JSONDecodeError) as e:
                        logger.warning(f"Could not convert environment variable {key} to {attr_type.__name__}: {str(e)}")
        
        logger.info(f"Configuration loaded from environment variables with prefix {env_prefix}")
        return config
    
    @classmethod
    def load(cls, config_path: Optional[str] = None, env_yaml_path: Optional[str] = "env.yaml", env_prefix: str = "LEAF_") -> 'LEAFCloudConfig':
        """
        Load configuration from multiple sources with precedence:
        1. Environment variables (highest priority)
        2. Configuration file specified in config_path
        3. env.yaml file
        4. Default values (lowest priority)
        
        Args:
            config_path: Path to primary configuration file
            env_yaml_path: Path to env.yaml file (default: "env.yaml")
            env_prefix: Prefix for environment variables (default: LEAF_)
            
        Returns:
            Configured LEAFCloudConfig object
        """
        # Start with default configuration
        config = cls()
        
        # Load from env.yaml if it exists
        if env_yaml_path and os.path.exists(env_yaml_path):
            try:
                env_config = cls.from_file(env_yaml_path)
                # Update config with values from env.yaml
                for key, value in env_config.__dict__.items():
                    setattr(config, key, value)
                logger.info(f"Configuration loaded from {env_yaml_path}")
            except Exception as e:
                logger.warning(f"Error loading {env_yaml_path}: {str(e)}")
        
        # Load from specified config file if provided
        if config_path and os.path.exists(config_path):
            try:
                file_config = cls.from_file(config_path)
                # Update config with values from config file
                for key, value in file_config.__dict__.items():
                    setattr(config, key, value)
                logger.info(f"Configuration loaded from {config_path}")
            except Exception as e:
                logger.warning(f"Error loading {config_path}: {str(e)}")
        
        # Override with environment variables (highest priority)
        for key, value in os.environ.items():
            if key.startswith(env_prefix):
                config_key = key[len(env_prefix):].lower()
                if hasattr(config, config_key):
                    attr_type = type(getattr(config, config_key))
                    try:
                        # Convert environment variable to the appropriate type
                        if attr_type == bool:
                            setattr(config, config_key, value.lower() in ('true', 'yes', '1'))
                        elif attr_type == int:
                            setattr(config, config_key, int(value))
                        elif attr_type == float:
                            setattr(config, config_key, float(value))
                        elif attr_type == list:
                            setattr(config, config_key, value.split(','))
                        elif attr_type == dict:
                            # For dictionaries, use JSON parsing
                            setattr(config, config_key, json.loads(value))
                        else:
                            setattr(config, config_key, value)
                    except (ValueError, json.JSONDecodeError) as e:
                        logger.warning(f"Could not convert environment variable {key} to {attr_type.__name__}: {str(e)}")
        
        # Validate the final configuration
        config.validate()
        
        return config
    
    def validate(self) -> bool:
        """
        Validate the configuration for correctness and completeness.
        
        Returns:
            True if configuration is valid
            
        Raises:
            ConfigValidationError if validation fails
        """
        errors = []
        
        # Basic type validations
        if not isinstance(self.duration, (int, float)) or self.duration <= 0:
            errors.append("duration must be a positive number")
        
        if not isinstance(self.iterations, int) or self.iterations <= 0:
            errors.append("iterations must be a positive integer")
        
        if not isinstance(self.time_step, (int, float)) or self.time_step <= 0:
            errors.append("time_step must be a positive number")
        
        # Path validations
        if self.terraform_dir and not os.path.exists(self.terraform_dir):
            errors.append(f"terraform_dir path does not exist: {self.terraform_dir}")
        
        for var_file in self.var_files:
            if not os.path.exists(var_file):
                errors.append(f"Variable file does not exist: {var_file}")
        
        # Workload validation
        valid_workload_types = ["steady", "burst", "cyclical", "random", "custom", "mix"]
        if self.workload_type not in valid_workload_types:
            errors.append(f"workload_type must be one of {valid_workload_types}")
        
        # Recovery strategy validation
        valid_recovery_strategies = ["retry", "ignore", "abort"]
        if self.recovery_strategy not in valid_recovery_strategies:
            errors.append(f"recovery_strategy must be one of {valid_recovery_strategies}")
        
        # Log validation results
        if errors:
            error_msg = "Configuration validation errors: \n" + "\n".join(errors)
            logger.error(error_msg)
            raise ConfigValidationError(error_msg)
        
        logger.info("Configuration validation successful")
        return True
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert configuration to dictionary.
        
        Returns:
            Dictionary representation of the configuration
        """
        return {k: v for k, v in self.__dict__.items()}
    
    def save(self, output_path: str) -> None:
        """
        Save configuration to a file.
        
        Args:
            output_path: Path where configuration will be saved
        """
        config_dict = self.to_dict()
        
        try:
            with open(output_path, 'w') as f:
                if output_path.endswith('.json'):
                    json.dump(config_dict, f, indent=2)
                else:
                    yaml.dump(config_dict, f)
            logger.info(f"Configuration saved to {output_path}")
        except Exception as e:
            logger.error(f"Error saving configuration to {output_path}: {str(e)}")
            raise

# Create a default configuration instance for easy import
default_config = LEAFCloudConfig()

def load_config(config_path: Optional[str] = None, env_yaml_path: Optional[str] = "env.yaml") -> LEAFCloudConfig:
    """
    Utility function to load configuration from files and environment.
    
    Args:
        config_path: Path to configuration file
        env_yaml_path: Path to env.yaml file
        
    Returns:
        Configured LEAFCloudConfig object
    """
    return LEAFCloudConfig.load(config_path, env_yaml_path)