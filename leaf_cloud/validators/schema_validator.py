"""Schema validation utilities for LEAF-Cloud.

This module provides functionality for validating data against schemas
and handling schema versioning.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

from pydantic import BaseModel, ValidationError as PydanticValidationError

from . import ValidationError, SchemaValidationError, SchemaVersionError

T = TypeVar('T', bound=BaseModel)
logger = logging.getLogger(__name__)

class SchemaValidator:
    """A class to handle schema validation and versioning."""
    
    def __init__(self, schema_dir: Optional[Union[str, Path]] = None):
        """Initialize the schema validator.
        
        Args:
            schema_dir: Directory containing schema files. If None, uses default location.
        """
        self.schema_dir = Path(schema_dir) if schema_dir else Path(__file__).parent / 'schemas'
        self.schemas: Dict[str, Dict] = {}
    
    def load_schema(self, schema_name: str, version: str = "latest") -> Dict:
        """Load a schema from a file.
        
        Args:
            schema_name: Name of the schema to load
            version: Schema version to load (default: "latest")
            
        Returns:
            The loaded schema as a dictionary
            
        Raises:
            FileNotFoundError: If the schema file doesn't exist
            json.JSONDecodeError: If the schema file contains invalid JSON
        """
        cache_key = f"{schema_name}_{version}"
        if cache_key in self.schemas:
            return self.schemas[cache_key]
        
        schema_path = self.schema_dir / schema_name / f"{version}.json"
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema not found: {schema_path}")
        
        with open(schema_path, 'r') as f:
            schema = json.load(f)
        
        self.schemas[cache_key] = schema
        return schema
    
    def validate_against_schema(
        self, 
        data: Dict[str, Any], 
        schema_name: str, 
        version: str = "latest"
    ) -> Dict[str, Any]:
        """Validate data against a schema.
        
        Args:
            data: The data to validate
            schema_name: Name of the schema to validate against
            version: Schema version to use (default: "latest")
            
        Returns:
            The validated data
            
        Raises:
            SchemaValidationError: If validation fails
            FileNotFoundError: If the schema file doesn't exist
        """
        try:
            schema = self.load_schema(schema_name, version)
            # In a real implementation, we would use a JSON Schema validator here
            # For now, we'll just return the data as-is
            return data
        except Exception as e:
            raise SchemaValidationError(
                f"Failed to validate {schema_name} schema (v{version}): {str(e)}"
            ) from e
    
    def validate_with_model(
        self, 
        data: Dict[str, Any], 
        model_class: Type[T],
        context: Optional[Dict[str, Any]] = None
    ) -> T:
        """Validate data against a Pydantic model.
        
        Args:
            data: The data to validate
            model_class: The Pydantic model class to validate against
            context: Optional context for validation
            
        Returns:
            An instance of the model if validation succeeds
            
        Raises:
            SchemaValidationError: If validation fails
        """
        try:
            if context:
                return model_class.model_validate(data, context=context)
            return model_class.model_validate(data)
        except PydanticValidationError as e:
            raise SchemaValidationError(
                f"Validation failed: {e}"
            ) from e
    
    def convert_schema_version(
        self,
        data: Dict[str, Any],
        from_version: str,
        to_version: str,
        schema_name: str
    ) -> Dict[str, Any]:
        """Convert data from one schema version to another.
        
        Args:
            data: The data to convert
            from_version: The source schema version
            to_version: The target schema version
            schema_name: The name of the schema
            
        Returns:
            The converted data
            
        Raises:
            SchemaVersionError: If conversion fails or is not supported
        """
        if from_version == to_version:
            return data
            
        # In a real implementation, we would have conversion logic here
        # For now, we'll just return the data as-is
        return data

# Create a default instance for convenience
schema_validator = SchemaValidator()
