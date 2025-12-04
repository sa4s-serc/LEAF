import os
import json
import yaml
import csv
import logging
import time
import numpy as np
import functools
from typing import (
    Dict,
    List,
    Any,
    Optional,
    Union,
    Callable,
    TypeVar,
    Generator,
)
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps
from contextlib import contextmanager
from ..exceptions import ConfigError

"""
utils/helpers.py

This module provides common utility functions for the LEAF-Cloud framework,
including logging configuration, error handling, data conversion, formatting tools,
and mathematical utilities for model calculations.
"""

# Configure root logger
logger = logging.getLogger("leaf_cloud")

# Type variables for generic functions
T = TypeVar("T")
R = TypeVar("R")

# ===== LOGGING UTILITIES =====


# Note: configure_logging has been moved to logging_utils.py
# Please use leaf_cloud.utils.configure_logging instead


def log_execution_time(func: Callable) -> Callable:
    """
    Decorator to log the execution time of a function.

    Args:
        func: Function to be decorated

    Returns:
        Wrapped function with execution time logging
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        logger.debug(
            f"{func.__name__} executed in {end_time - start_time:.2f} seconds"
        )
        return result

    return wrapper


def get_package_root() -> Path:
    """Get the root directory of the leaf_cloud package."""
    # Use __file__ to get the path of the current file (helpers.py)
    # Then, resolve the absolute path and find its parent's parent (utils/ -> leaf_cloud/)
    return Path(__file__).resolve().parent.parent


@contextmanager
def log_section(section_name: str) -> Generator[None, None, None]:
    """
    Context manager to log the start and end of a section.

    Args:
        section_name: Name of the section to log
    """
    logger.info(f"Starting {section_name}")
    start_time = time.time()
    try:
        yield
    finally:
        end_time = time.time()
        logger.info(
            f"Completed {section_name} in {end_time - start_time:.2f} seconds"
        )


# ===== ERROR HANDLING =====


def safe_execute(
    func: Callable[..., T],
    default_value: R,
    error_msg: str = "Operation failed",
    log_level: str = "ERROR",
) -> Union[T, R]:
    """
    Execute a function safely, returning a default value if it fails.

    Args:
        func: Function to execute
        default_value: Value to return if function fails
        error_msg: Message to log if function fails
        log_level: Logging level for error messages

    Returns:
        Function result or default value if it fails
    """
    try:
        return func()
    except Exception as e:
        getattr(logger, log_level.lower())(f"{error_msg}: {str(e)}")
        return default_value


# ===== DATA CONVERSION & FORMATTING =====


def load_yaml_file(file_path: str) -> Dict:
    """
    Load data from YAML file.

    Args:
        file_path: Path to YAML file

    Returns:
        Dictionary with loaded data

    Raises:
        ConfigError: If file doesn't exist or isn't valid YAML
    """
    try:
        with open(file_path, "r") as f:
            data = yaml.safe_load(f)
            if data is None:
                return {}
            return data
    except FileNotFoundError:
        raise ConfigError(f"YAML file not found: {file_path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {file_path}: {str(e)}")


def load_json_file(file_path: str) -> Dict:
    """
    Load data from JSON file.

    Args:
        file_path: Path to JSON file

    Returns:
        Dictionary with loaded data

    Raises:
        ConfigError: If file doesn't exist or isn't valid JSON
    """
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"JSON file not found: {file_path}")
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {file_path}: {str(e)}")


def save_json_file(data: Any, file_path: str, pretty: bool = True) -> None:
    """
    Save data to JSON file.

    Args:
        data: Data to save
        file_path: Path to save JSON file
        pretty: Whether to format with indentation
    """
    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)

    with open(file_path, "w") as f:
        if pretty:
            json.dump(data, f, indent=2)
        else:
            json.dump(data, f)


def save_csv_file(data: List[Dict], file_path: str) -> None:
    """
    Save list of dictionaries to CSV file.

    Args:
        data: List of dictionaries to save
        file_path: Path to save CSV file
    """
    if not data:
        logger.warning(f"No data to save to CSV file: {file_path}")
        return

    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)

    with open(file_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)


def format_timestamp(timestamp: float) -> str:
    """
    Format a UNIX timestamp as ISO format string.

    Args:
        timestamp: UNIX timestamp

    Returns:
        Formatted datetime string (ISO format)
    """
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.isoformat()


def parse_timestamp(timestamp_str: str) -> float:
    """
    Parse ISO format timestamp string to UNIX timestamp.

    Args:
        timestamp_str: ISO format timestamp string

    Returns:
        UNIX timestamp
    """
    dt = datetime.fromisoformat(timestamp_str)
    return dt.timestamp()


def format_size(size_bytes: float) -> str:
    """
    Format bytes to human-readable size.

    Args:
        size_bytes: Size in bytes

    Returns:
        Human-readable size string
    """
    if size_bytes < 1024:
        return f"{size_bytes:.2f} B"

    size_kb = size_bytes / 1024
    if size_kb < 1024:
        return f"{size_kb:.2f} KB"

    size_mb = size_kb / 1024
    if size_mb < 1024:
        return f"{size_mb:.2f} MB"

    size_gb = size_mb / 1024
    return f"{size_gb:.2f} GB"


# ===== MATHEMATICAL UTILITIES =====


def linear_interpolate(
    x: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    """
    Perform linear interpolation between two points.

    Args:
        x: X value to interpolate
        x1: X coordinate of first point
        y1: Y coordinate of first point
        x2: X coordinate of second point
        y2: Y coordinate of second point

    Returns:
        Interpolated Y value
    """
    if x2 == x1:
        return y1  # Avoid division by zero

    return y1 + (x - x1) * (y2 - y1) / (x2 - x1)


def calculate_statistics(values: List[float]) -> Dict[str, float]:
    """
    Calculate basic statistics for a list of values.

    Args:
        values: List of numeric values

    Returns:
        Dictionary with statistics (mean, median, min, max, std_dev, variance)
    """
    if not values:
        return {
            "mean": 0.0,
            "median": 0.0,
            "min": 0.0,
            "max": 0.0,
            "std_dev": 0.0,
            "variance": 0.0,
            "count": 0,
        }

    return {
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "std_dev": float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
        "variance": float(np.var(values, ddof=1)) if len(values) > 1 else 0.0,
        "count": len(values),
    }


def calculate_percentiles(
    values: List[float], percentiles: List[int] = []
) -> Dict[int, float]:
    """
    Calculate percentiles for a list of values.

    Args:
        values: List of numeric values
        percentiles: List of percentiles to calculate (0-100)

    Returns:
        Dictionary mapping percentiles to values
    """
    if not values:
        return {}

    if percentiles is None:
        percentiles = [10, 25, 50, 75, 90, 95, 99]

    return {p: float(np.percentile(values, p)) for p in percentiles}


def moving_average(values: List[float], window_size: int = 5) -> List[float]:
    """
    Calculate moving average for a list of values.

    Args:
        values: List of numeric values
        window_size: Size of moving average window

    Returns:
        List of moving averages
    """
    if not values or window_size <= 0:
        return []

    result = []
    for i in range(len(values)):
        start_idx = max(0, i - window_size + 1)
        window = values[start_idx : i + 1]
        result.append(sum(window) / len(window))

    return result


def energy_to_carbon(energy_kwh: float, carbon_factor: float) -> float:
    """
    Convert energy to carbon emissions.

    Args:
        energy_kwh: Energy in kWh
        carbon_factor: Carbon intensity factor (gCO₂eq/kWh)

    Returns:
        Carbon emissions in kg CO₂eq
    """
    # Convert from g to kg
    return (energy_kwh * carbon_factor) / 1000


def convert_to_base_unit(value: float, unit: str) -> float:
    """
    Convert a value from specified unit to base unit.

    Args:
        value: Value to convert
        unit: Unit string (e.g., 'MB', 'GB', 'TB', 'ms', 's')

    Returns:
        Value in base unit
    """
    # Storage units
    if unit.upper() == "KB":
        return value * 1024
    elif unit.upper() == "MB":
        return value * 1024 * 1024
    elif unit.upper() == "GB":
        return value * 1024 * 1024 * 1024
    elif unit.upper() == "TB":
        return value * 1024 * 1024 * 1024 * 1024

    # Time units
    elif unit.lower() == "ms":
        return value / 1000
    elif unit.lower() == "us" or unit.lower() == "μs":
        return value / 1_000_000
    elif unit.lower() == "ns":
        return value / 1_000_000_000
    elif unit.lower() == "min":
        return value * 60
    elif unit.lower() == "h":
        return value * 3600

    # Default case: assume already in base unit
    return value


# ===== MISCELLANEOUS HELPERS =====


def ensure_directory_exists(directory_path: str) -> None:
    """
    Ensure a directory exists, creating it if necessary.

    Args:
        directory_path: Path to directory
    """
    Path(directory_path).mkdir(parents=True, exist_ok=True)


def flatten_dict(d: Dict, parent_key: str = "", sep: str = ".") -> Dict:
    """
    Flatten a nested dictionary.

    Args:
        d: Dictionary to flatten
        parent_key: Parent key for recursion
        sep: Separator for keys

    Returns:
        Flattened dictionary
    """
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def unflatten_dict(d: Dict, sep: str = ".") -> Dict:
    """
    Unflatten a flattened dictionary.

    Args:
        d: Flattened dictionary
        sep: Separator used in keys

    Returns:
        Nested dictionary
    """
    result = {}
    for key, value in d.items():
        parts = key.split(sep)
        tmp = result
        for part in parts[:-1]:
            if part not in tmp:
                tmp[part] = {}
            tmp = tmp[part]
        tmp[parts[-1]] = value
    return result


def dict_merge(d1: Dict, d2: Dict) -> Dict:
    """
    Recursively merge two dictionaries.

    Args:
        d1: First dictionary
        d2: Second dictionary

    Returns:
        Merged dictionary
    """
    result = d1.copy()
    for k, v in d2.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = dict_merge(result[k], v)
        else:
            result[k] = v
    return result


def normalize_block(block: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """
    Normalize a Terraform configuration block list into a dictionary.

    Args:
        block: List of dictionaries with resource blocks

    Returns:
        Normalized dictionary with block key mapping to block configuration
    """
    if not block:
        return {}

    result = {}
    for item in block:
        for key, value in item.items():
            if key in result:
                if isinstance(value, dict) and isinstance(result[key], dict):
                    result[key].update(value)
                elif isinstance(value, list) and isinstance(result[key], list):
                    result[key].extend(value)
                else:
                    result[key] = value
            else:
                result[key] = value
    return result


def normalize_block(block):
    """
    Normalize a Terraform block by converting a list of dictionaries into a single dictionary.
    If block is already a dictionary, it is returned unchanged.
    """
    if isinstance(block, list):
        normalized = {}
        for item in block:
            if isinstance(item, dict):
                for key, value in item.items():
                    # Merge multiple blocks under the same key if necessary
                    if key in normalized:
                        normalized[key].update(value)
                    else:
                        normalized[key] = value
        return normalized
    return block


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a string for use as a filename.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename
    """
    # Replace invalid filename characters
    invalid_chars = ["<", ">", ":", '"', "/", "\\", "|", "?", "*"]
    for char in invalid_chars:
        filename = filename.replace(char, "_")

    # Limit length to 255 characters
    if len(filename) > 255:
        base, ext = os.path.splitext(filename)
        filename = base[: 255 - len(ext)] + ext

    return filename
