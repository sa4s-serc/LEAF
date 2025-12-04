"""Centralized validation utilities for the LEAF-Cloud project.

This module provides common validation functionality to be used across the codebase,
reducing code duplication and ensuring consistent validation behavior.
"""

from typing import Any, Dict, List, Optional, Type, TypeVar, Union
from pydantic import BaseModel, ValidationError as PydanticValidationError
from pathlib import Path
import logging

# Type variable for generic type hints
T = TypeVar('T', bound=BaseModel)

logger = logging.getLogger(__name__)

class ValidationError(Exception):
    """Base exception for all validation errors in the application."""
    
    def __init__(
        self, 
        message: str, 
        code: Optional[str] = None, 
        field: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        """Initialize ValidationError with enhanced error information.
        
        Args:
            message: Human-readable error message
            code: Error code for programmatic handling
            field: Name of the field that caused the error
            details: Additional error details
        """
        super().__init__(message)
        self.code = code or "validation_error"
        self.field = field
        self.details = details or {}
        
        # Store field in details for backward compatibility
        if field:
            self.details["field"] = field
        if code:
            self.details["code"] = code

class SchemaValidationError(ValidationError):
    """Raised when schema validation fails."""
    pass

class SchemaVersionError(ValidationError):
    """Raised when there's a version compatibility issue."""
    pass

def validate_with_model(data: Dict[str, Any], model_class: Type[T]) -> T:
    """Validate data against a Pydantic model.
    
    Args:
        data: The data to validate
        model_class: The Pydantic model class to validate against
        
    Returns:
        An instance of the model if validation succeeds
        
    Raises:
        SchemaValidationError: If validation fails
    """
    try:
        return model_class.model_validate(data)
    except PydanticValidationError as e:
        raise SchemaValidationError(
            f"Validation failed: {e}"
        ) from e

def validate_file_exists(file_path: Union[str, Path]) -> Path:
    """Validate that a file exists and is accessible.
    
    Args:
        file_path: Path to the file to validate
        
    Returns:
        The resolved Path object if the file exists
        
    Raises:
        FileNotFoundError: If the file doesn't exist
        NotADirectoryError: If the path is a directory
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise NotADirectoryError(f"Path is not a file: {path}")
    return path

def validate_positive_number(value: Union[int, float], name: str) -> None:
    """Validate that a number is positive.
    
    Args:
        value: The value to validate
        name: The name of the parameter for error messages
        
    Raises:
        ValueError: If the value is not positive
    """
    if value <= 0:
        raise ValueError(f"{name} must be a positive number, got {value}")

def validate_in_range(
    value: Union[int, float], 
    name: str, 
    min_val: Optional[float] = None, 
    max_val: Optional[float] = None
) -> None:
    """Validate that a value is within a specified range.
    
    Args:
        value: The value to validate
        name: The name of the parameter for error messages
        min_val: Minimum allowed value (inclusive)
        max_val: Maximum allowed value (inclusive)
        
    Raises:
        ValueError: If the value is outside the specified range
    """
    if min_val is not None and value < min_val:
        raise ValueError(f"{name} must be >= {min_val}, got {value}")
    if max_val is not None and value > max_val:
        raise ValueError(f"{name} must be <= {max_val}, got {value}")
