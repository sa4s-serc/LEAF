"""Configuration validation for LEAF-Cloud.

This module provides configuration validation utilities, including validation
for different configuration sections and types.
"""

from typing import Any, Dict, List, Optional, Type, TypeVar, Union
from pathlib import Path
import logging

from ..validators import (
    validate_positive_number,
    validate_in_range,
    ValidationError,
)

logger = logging.getLogger(__name__)

class ConfigValidator:
    """Base class for configuration validators."""
    
    def __init__(self, config: Any, errors: Optional[List[str]] = None):
        """Initialize the validator with a config object.
        
        Args:
            config: The configuration object to validate
            errors: Optional list to collect error messages
        """
        self.config = config
        self.errors = errors if errors is not None else []
    
    def validate(self) -> bool:
        """Run all validation checks.
        
        Returns:
            bool: True if all validations pass, False otherwise
        """
        raise NotImplementedError("Subclasses must implement validate()")
    
    def _add_error(self, message: str) -> None:
        """Add an error message to the errors list.
        
        Args:
            message: The error message to add
        """
        self.errors.append(message)
        logger.error(message)

class LoggingConfigValidator(ConfigValidator):
    """Validator for logging configuration."""
    
    def validate(self) -> bool:
        """Validate logging configuration."""
        if not hasattr(self.config, 'logging'):
            return True  # Logging is optional
            
        logging_config = self.config.logging
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        
        if not hasattr(logging_config, 'log_level'):
            self._add_error("Missing required logging configuration: 'log_level'")
        elif not isinstance(logging_config.log_level, str) or logging_config.log_level.upper() not in valid_log_levels:
            self._add_error(
                f"logging.log_level must be one of {valid_log_levels}, "
                f"got {getattr(logging_config, 'log_level', 'UNSET')!r}"
            )
        
        return len(self.errors) == 0

class SimulationConfigValidator(ConfigValidator):
    """Validator for simulation configuration."""
    
    def validate(self) -> bool:
        """Validate simulation configuration."""
        if not hasattr(self.config, 'simulation'):
            self._add_error("Missing required configuration section: 'simulation'")
            return False
            
        sim_config = self.config.simulation
        
        # Check required fields
        required_fields = ['duration', 'time_step']
        for field in required_fields:
            if not hasattr(sim_config, field):
                self._add_error(f"Missing required simulation configuration: '{field}'")
        
        # Validate duration and time_step if they exist
        if hasattr(sim_config, 'duration') and hasattr(sim_config.duration, '__float__'):
            try:
                validate_positive_number(float(sim_config.duration), "Simulation duration")
            except ValueError as e:
                self._add_error(str(e))
                
        if hasattr(sim_config, 'time_step') and hasattr(sim_config.time_step, '__float__'):
            try:
                validate_positive_number(float(sim_config.time_step), "Simulation time step")
            except ValueError as e:
                self._add_error(str(e))
        
        return len(self.errors) == 0

def validate_config(config: Any) -> List[str]:
    """Validate a configuration object.
    
    Args:
        config: The configuration object to validate
        
    Returns:
        List of error messages, empty if validation passes
    """
    errors = []
    validators = [
        LoggingConfigValidator(config, errors),
        SimulationConfigValidator(config, errors),
    ]
    
    for validator in validators:
        validator.validate()
    
    return errors
