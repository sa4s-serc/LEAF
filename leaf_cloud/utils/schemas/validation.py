"""Validation and versioning utilities for simulation result schemas.

This module provides functions to validate simulation results against their schemas
and handle version compatibility between different schema versions.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Type, TypeVar, Union

from pydantic import ValidationError, BaseModel, Field

from .results import (
    RawSimulationResult,
    ProcessedSimulationResult,
)

logger = logging.getLogger(__name__)


# Type variable for Pydantic models
T = TypeVar("T", bound=BaseModel)

# Schema version constants
CURRENT_RAW_SCHEMA_VERSION = "1.0.0"
CURRENT_PROCESSED_SCHEMA_VERSION = "1.0.0"

# Schema version compatibility mapping
# Maps schema versions to their compatible model versions
SCHEMA_COMPATIBILITY = {
    "raw": {
        "1.0.0": CURRENT_RAW_SCHEMA_VERSION,
    },
    "processed": {
        "1.0.0": CURRENT_PROCESSED_SCHEMA_VERSION,
    },
}


class SchemaValidationError(Exception):
    """Raised when schema validation fails."""

    pass


class SchemaVersionError(Exception):
    """Raised when there's a version compatibility issue."""

    pass


def get_schema_version(data: Dict[str, Any], schema_type: str) -> str:
    """Extract the schema version from a result dictionary.

    Args:
        data: The simulation result data as a dictionary.
        schema_type: The type of schema ('raw' or 'processed').

    Returns:
        The schema version string.

    Raises:
        SchemaVersionError: If the version cannot be determined.
    """
    version_field = f"schema_version"
    if (
        schema_type == "raw"
        and "metadata" in data
        and "schema_version" in data
    ):
        # Old format where schema_version was at the root
        version = data.get("schema_version")
    elif (
        schema_type == "processed"
        and "metadata" in data
        and "schema_version" in data["metadata"]
    ):
        # New format where schema_version is in metadata
        version = data["metadata"].get("schema_version")
    elif (
        schema_type == "raw"
        and "metadata" in data
        and "simulation_id" in data["metadata"]
    ):
        # Very old format without explicit version, assume 1.0.0
        version = "1.0.0"
    else:
        raise SchemaVersionError(
            f"Could not determine {schema_type} schema version from data. "
            f"Missing required version field."
        )

    if not version:
        raise SchemaVersionError(f"Empty {schema_type} schema version")

    return str(version)


def validate_schema_version(version: str, schema_type: str) -> None:
    """Validate that a schema version is supported.

    Args:
        version: The schema version to validate.
        schema_type: The type of schema ('raw' or 'processed').

    Raises:
        SchemaVersionError: If the version is not supported.
    """
    if schema_type not in SCHEMA_COMPATIBILITY:
        raise ValueError(f"Unknown schema type: {schema_type}")

    if version not in SCHEMA_COMPATIBILITY[schema_type]:
        raise SchemaVersionError(
            f"Unsupported {schema_type} schema version: {version}. "
            f"Supported versions: {', '.join(SCHEMA_COMPATIBILITY[schema_type].keys())}"
        )


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
    if from_version == to_version:
        return data

    # For now, we only support conversion to the current version
    if to_version != SCHEMA_COMPATIBILITY[schema_type][from_version]:
        raise SchemaVersionError(
            f"Direct conversion from {from_version} to {to_version} is not supported. "
            f"Must convert to {SCHEMA_COMPATIBILITY[schema_type][from_version]} first."
        )

    # Create a copy to avoid modifying the original
    converted = data.copy()

    # Apply version-specific conversion logic here
    if (
        schema_type == "raw"
        and from_version == "1.0.0"
        and to_version == "1.0.0"
    ):
        # No conversion needed between same versions
        pass

    # Ensure the version field is updated
    if schema_type == "raw":
        converted["schema_version"] = to_version
    elif "metadata" in converted:
        converted["metadata"]["schema_version"] = to_version

    return converted


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
    if target_version is None:
        target_version = (
            CURRENT_RAW_SCHEMA_VERSION
            if schema_type == "raw"
            else CURRENT_PROCESSED_SCHEMA_VERSION
        )

    try:
        # First, try to parse with the target model directly
        return model_class.model_validate(data)
    except ValidationError as e:
        logger.debug(
            "Initial validation failed, attempting version conversion",
            exc_info=True,
        )

        try:
            # Get the source version
            source_version = get_schema_version(data, schema_type)
            validate_schema_version(source_version, schema_type)

            # Convert to target version
            converted_data = convert_schema(
                data, source_version, target_version, schema_type
            )

            # Try validation again with converted data
            return model_class.model_validate(converted_data)

        except (SchemaVersionError, ValidationError) as conv_e:
            # If conversion or validation still fails, raise with combined error info
            raise SchemaValidationError(
                f"Failed to validate {schema_type} data. "
                f"Original error: {str(e)}\nConversion error: {str(conv_e)}"
            ) from conv_e


def load_and_validate_result(
    file_path: Union[str, Path],
    schema_type: str,
    target_version: Optional[str] = None,
) -> Union[RawSimulationResult, ProcessedSimulationResult]:
    """Load and validate a result file.

    Args:
        file_path: Path to the result file.
        schema_type: The type of schema ('raw' or 'processed').
        target_version: The target schema version. If None, uses the current version.

    Returns:
        The validated result object.

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


def ensure_compatible_version(
    data: Dict[str, Any],
    schema_type: str,
    min_version: Optional[str] = None,
    max_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Ensure the data is compatible with the specified version requirements.

    Args:
        data: The data to check.
        schema_type: The type of schema ('raw' or 'processed').
        min_version: The minimum required version (inclusive).
        max_version: The maximum allowed version (inclusive).

    Returns:
        The data, possibly converted to a compatible version.

    Raises:
        SchemaVersionError: If the data cannot be made compatible.
    """
    version = get_schema_version(data, schema_type)
    validate_schema_version(version, schema_type)

    # Convert to the latest compatible version if needed
    target_version = SCHEMA_COMPATIBILITY[schema_type][version]

    # Check version constraints
    if min_version and version < min_version:
        raise SchemaVersionError(
            f"Schema version {version} is older than the minimum required version {min_version}"
        )

    if max_version and version > max_version:
        raise SchemaVersionError(
            f"Schema version {version} is newer than the maximum allowed version {max_version}"
        )

    # Convert to the target version if needed
    if version != target_version:
        data = convert_schema(data, version, target_version, schema_type)

    return data
