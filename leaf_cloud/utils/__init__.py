import logging

# Import logging utilities from the new logging_utils module
from .logging_utils import configure_logging, get_logger, LoggingContext

from .helpers import (
    # Logging utilities (legacy, will be removed in future versions)
    log_execution_time,
    log_section,
    # Error handling
    safe_execute,
    # Data conversion & formatting
    load_yaml_file,
    load_json_file,
    save_json_file,
    save_csv_file,
    format_timestamp,
    parse_timestamp,
    format_size,
    # Mathematical utilities
    linear_interpolate,
    calculate_statistics,
    calculate_percentiles,
    moving_average,
    energy_to_carbon,
    convert_to_base_unit,
    # Miscellaneous helpers
    get_package_root,
    ensure_directory_exists,
    flatten_dict,
    unflatten_dict,
    dict_merge,
    sanitize_filename,
)

from .analyser import (
    # Analytics utilities
    analyze_carbon,
    analyze_cost,
    analyze_energy,
    analyze_latency,
    analyze_resource_states,
    analyze_resource_utilization,
    analyze_scaling,
    analyze_simulation_info,
    analyze_workload_stats,
    generate_summary_and_recommendations,
    perform_additional_analysis,
)

# Import CLI utilities from consolidated location
from ..cli.cli_utils import (
    print_header,
    print_success,
    print_warning,
    print_error,
    create_progress,
    display_table,
)

# Import visualization utilities
from ..cli.visualization import (
    save_plot,
    create_time_series_plot,
    create_histogram,
)

# Import schemas
# Import SimulationResult from the new module to avoid circular imports
from .results import SimulationResult

from .schemas.results import (
    RawSimulationResult,
    ProcessedSimulationResult,
    TimeSeriesDataPoint,
    MetricStatistics,
    RawResourceMetrics,
    ProcessedResourceAnalysis,
    AnalysisMetadata,
    TokenTraceEvent,
)
from .schemas import (
    validate_and_convert,
    load_and_validate_result,
    ensure_compatible_version,
    SchemaValidationError,
    SchemaVersionError,
)

# Import schema versions from the schema package
from ..schema import (
    CURRENT_RAW_SCHEMA_VERSION,
    CURRENT_PROCESSED_SCHEMA_VERSION,
)

"""
LEAF-Cloud Utilities Package

This package provides common utility functions for the LEAF-Cloud framework,
including logging configuration, error handling, data conversion, formatting tools,
and mathematical utilities for model calculations. Additionally, it includes
analytics utilities for analyzing simulation results and generating reports.

The module consists of several components:
- Logging utilities: Functions for configuring logging and measuring execution time
- Error handling: Custom error classes and a safe execution function
- Data conversion & formatting: Functions for loading and saving data in various formats
- Mathematical utilities: Functions for performing common mathematical operations
- Miscellaneous helpers: Functions for manipulating dictionaries and file paths
- Analytics utilities: Functions for analyzing simulation results and generating reports
"""

# Configure package logger
logger = logging.getLogger(__name__)

# Import main classes from submodules

# Package metadata
__version__ = "0.1.0"
__author__ = "LEAF-Cloud Team"

__all__ = [
    # Logging utilities
    "configure_logging",
    "get_logger",
    "LoggingContext",
    # Legacy logging utilities (will be removed in future versions)
    "log_execution_time",
    "log_section",
    "logger",
    # Error handling
    "safe_execute",
    # Data conversion & formatting
    "load_yaml_file",
    "load_json_file",
    "save_json_file",
    "save_csv_file",
    "format_timestamp",
    "parse_timestamp",
    "format_size",
    # Mathematical utilities
    "linear_interpolate",
    "calculate_statistics",
    "calculate_percentiles",
    "moving_average",
    "energy_to_carbon",
    "convert_to_base_unit",
    # CLI utilities
    "print_header",
    "print_success",
    "print_warning",
    "print_error",
    "create_progress",
    "display_table",
    "save_plot",
    "create_time_series_plot",
    "create_histogram",
    # Schema models and validation
    "SimulationResult",
    "RawSimulationResult",
    "ProcessedSimulationResult",
    "TimeSeriesDataPoint",
    "MetricStatistics",
    "RawResourceMetrics",
    "ProcessedResourceAnalysis",
    "validate_and_convert",
    "load_and_validate_result",
    "ensure_compatible_version",
    "SchemaValidationError",
    "SchemaVersionError",
    "CURRENT_RAW_SCHEMA_VERSION",
    "CURRENT_PROCESSED_SCHEMA_VERSION",
    # Miscellaneous helpers
    "ensure_directory_exists",
    "flatten_dict",
    "unflatten_dict",
    "dict_merge",
    "sanitize_filename",
    "get_package_root",
    # Analytics utilities
    "analyze_carbon",
    "analyze_cost",
    "analyze_energy",
    "analyze_latency",
    "analyze_resource_states",
    "analyze_resource_utilization",
    "analyze_scaling",
    "analyze_simulation_info",
    "analyze_workload_stats",
    "generate_summary_and_recommendations",
    "perform_additional_analysis",
]
