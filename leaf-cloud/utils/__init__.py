import logging

from .helpers import (
    # Logging utilities
    configure_logging,
    log_execution_time,
    log_section,
    
    # Error handling
    CloudModelError,
    ConfigurationError,
    ValidationError, 
    ResourceError,
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
    ensure_directory_exists,
    flatten_dict,
    unflatten_dict,
    dict_merge,
    sanitize_filename,
)

from .analyser import (
    # Analytics utilities
    analyze_carbon,
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
    'configure_logging',
    'log_execution_time',
    'log_section',
    'logger',
    
    # Error handling
    'CloudModelError',
    'ConfigurationError',
    'ValidationError', 
    'ResourceError',
    'safe_execute',
    
    # Data conversion & formatting
    'load_yaml_file',
    'load_json_file',
    'save_json_file',
    'save_csv_file',
    'format_timestamp',
    'parse_timestamp',
    'format_size',
    
    # Mathematical utilities
    'linear_interpolate',
    'calculate_statistics',
    'calculate_percentiles',
    'moving_average',
    'energy_to_carbon',
    'convert_to_base_unit',
    
    # Miscellaneous helpers
    'ensure_directory_exists',
    'flatten_dict',
    'unflatten_dict',
    'dict_merge',
    'sanitize_filename'

    # Analytics utilities
    'analyze_carbon',
    'analyze_energy',
    'analyze_latency',
    'analyze_resource_states',
    'analyze_resource_utilization',
    'analyze_scaling',
    'analyze_simulation_info',
    'analyze_workload_stats',
    'generate_summary_and_recommendations',
    'perform_additional_analysis',
]