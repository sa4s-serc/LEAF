"""
Schema definitions and versioning for LEAF-Cloud.

This module contains schema-related constants, types, and utilities for versioning
and validating different data structures used throughout the LEAF-Cloud system.
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional, Union, Type, TypeVar

from pydantic import BaseModel, ValidationError


class SchemaValidationError(Exception):
    """Raised when schema validation fails."""
    pass


class SchemaVersionError(Exception):
    """Raised when there's a version compatibility issue."""
    pass


# Type variable for Pydantic models
T = TypeVar("T", bound=BaseModel)


def load_and_validate_result(
    file_path: Union[str, Path],
    schema_type: str,
    target_version: Optional[str] = None,
) -> Any:  # Returns Union[RawSimulationResult, ProcessedSimulationResult] but can't reference here due to circular imports
    """Load and validate a result file.

    Args:
        file_path: Path to the result file.
        schema_type: The type of schema ('raw' or 'processed').
        target_version: The target schema version. If None, uses the current version.

    Returns:
        The validated result object (RawSimulationResult or ProcessedSimulationResult).

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        SchemaValidationError: If validation fails.
        SchemaVersionError: If version conversion fails.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Result file not found: {file_path}")

    try:
        with open(file_path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {file_path}: {str(e)}") from e

    # Import here to avoid circular imports
    from ..utils.schemas.results import RawSimulationResult, ProcessedSimulationResult
    
    if schema_type == "raw":
        return validate_and_convert(
            data, RawSimulationResult, "raw", target_version
        )
    elif schema_type == "processed":
        return validate_and_convert(
            data, ProcessedSimulationResult, "processed", target_version
        )
    else:
        raise ValueError(f"Unknown schema type: {schema_type}")


def validate_and_convert(
    data: Dict[str, Any],
    model_class: Type[T],
    schema_type: str,
    target_version: Optional[str] = None,
) -> T:
    """Validate data against a schema and convert to the target version if needed.

    Args:
        data: The data to validate and convert.
        model_class: The Pydantic model class to validate against.
        schema_type: The type of schema ('raw' or 'processed').
        target_version: The target schema version. If None, uses the current version.

    Returns:
        The validated and converted data as an instance of model_class.

    Raises:
        SchemaValidationError: If validation fails.
        SchemaVersionError: If version conversion fails.
    """
    # Get the current version from the data, default to current version if not present
    version = data.get("metadata", {}).get("schema_version")
    if not version:
        # If no schema version is present, assume it's the current version
        version = (
            CURRENT_RAW_SCHEMA_VERSION
            if schema_type == "raw"
            else CURRENT_PROCESSED_SCHEMA_VERSION
        )
        # Add the schema version to the data for consistency
        if "metadata" not in data:
            data["metadata"] = {}
        data["metadata"]["schema_version"] = version

    # If no target version is specified, use the current version
    if target_version is None:
        target_version = (
            CURRENT_RAW_SCHEMA_VERSION
            if schema_type == "raw"
            else CURRENT_PROCESSED_SCHEMA_VERSION
        )

    # Convert the data to the target version if needed
    if version != target_version:
        data = convert_schema(data, version, target_version, schema_type)

    # Validate against the model class
    try:
        return model_class.parse_obj(data)
    except ValidationError as e:
        raise SchemaValidationError(f"Validation failed: {str(e)}") from e


def convert_schema(
    data: Dict[str, Any], from_version: str, to_version: str, schema_type: str
) -> Dict[str, Any]:
    """Convert data between different schema versions.

    Args:
        data: The data to convert.
        from_version: The source schema version.
        to_version: The target schema version.
        schema_type: The type of schema ('raw' or 'processed').

    Returns:
        The converted data.

    Raises:
        SchemaVersionError: If conversion between the versions is not supported.
    """
    # If versions are the same, no conversion needed
    if from_version == to_version:
        return data

    # Check if conversion is supported
    if schema_type not in SCHEMA_COMPATIBILITY:
        raise SchemaVersionError(f"Unsupported schema type: {schema_type}")

    if from_version not in SCHEMA_COMPATIBILITY[schema_type]:
        raise SchemaVersionError(
            f"Unsupported source version {from_version} for schema type {schema_type}"
        )

    # For now, we only support conversion to the current version
    if to_version != SCHEMA_COMPATIBILITY[schema_type][from_version]:
        raise SchemaVersionError(
            f"Conversion from {from_version} to {to_version} is not supported"
        )

    # Add conversion logic here when needed
    # For now, we just update the version number
    if "metadata" not in data:
        data["metadata"] = {}
    data["metadata"]["schema_version"] = to_version

    return data

# Re-export schema versions for backward compatibility
CURRENT_RAW_SCHEMA_VERSION = "1.0.0"
CURRENT_PROCESSED_SCHEMA_VERSION = "1.0.0"

# Schema validation schemas
RAW_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Raw Simulation Result",
    "description": "Schema for raw simulation results",
    "type": "object",
    "properties": {
        "metadata": {
            "type": "object",
            "properties": {
                "schema_version": {"type": "string"},
                "simulation_id": {"type": "string"},
                "timestamp": {"type": "string", "format": "date-time"},
            },
            "required": ["schema_version"],
        },
        # Add more schema definitions as needed
    },
    "required": ["metadata"],
}

PROCESSED_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Processed Simulation Result",
    "description": "Schema for processed simulation results",
    "type": "object",
    "properties": {
        "metadata": {
            "type": "object",
            "properties": {
                "schema_version": {"type": "string"},
                "simulation_id": {"type": "string"},
                "timestamp": {"type": "string", "format": "date-time"},
                "raw_schema_version": {"type": "string"},
            },
            "required": ["schema_version", "raw_schema_version"],
        },
        # Add more schema definitions as needed
    },
    "required": ["metadata"],
}

def validate_schema(data: dict, schema: dict) -> bool:
    """
    Validate data against a JSON schema.
    
    Args:
        data: The data to validate
        schema: The JSON schema to validate against
        
    Returns:
        bool: True if data is valid, False otherwise
    """
    try:
        import jsonschema
        jsonschema.validate(instance=data, schema=schema)
        return True
    except Exception as e:
        import logging
        logging.error(f"Schema validation failed: {str(e)}")
        return False
