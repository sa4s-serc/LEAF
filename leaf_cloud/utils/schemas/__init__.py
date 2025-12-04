"""Schemas for simulation results and configurations."""

from .results import (
    RawSimulationResult,
    ProcessedSimulationResult,
    TimeSeriesDataPoint,
    MetricStatistics,
    RawResourceMetrics,
    ProcessedResourceAnalysis,
    TokenTraceEvent,
)

# Version information
CURRENT_RAW_SCHEMA_VERSION = "1.0.0"
CURRENT_PROCESSED_SCHEMA_VERSION = "1.0.0"

# Import validation utilities
from .validation import (
    validate_and_convert,
    load_and_validate_result,
    ensure_compatible_version,
    SchemaValidationError,
    SchemaVersionError,
)
