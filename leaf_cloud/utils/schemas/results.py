"""
This module defines the Pydantic schemas for raw and processed simulation results.

These models provide a structured, validated, and extensible data contract for all
simulation outputs, separating raw data from processed analysis.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, validator

# ----------------------------------------------------------------------------
# Core Data Structures
# ----------------------------------------------------------------------------


class TimeSeriesDataPoint(BaseModel):
    """Represents a single data point in a time series."""

    timestamp: float = Field(
        ..., description="The timestamp of the data point (in seconds)."
    )
    value: float = Field(
        ..., description="The value of the metric at the given timestamp."
    )


class MetricStatistics(BaseModel):
    """Provides detailed statistical analysis for a given metric."""

    mean: float = Field(..., description="Mean (average) value.")
    std: float = Field(..., description="Standard deviation.")
    min: float = Field(..., description="Minimum value.")
    max: float = Field(..., description="Maximum value.")
    p25: float = Field(..., description="25th percentile.")
    p50: float = Field(..., description="50th percentile (median).")
    p75: float = Field(..., description="75th percentile.")
    p95: float = Field(..., description="95th percentile.")
    p99: float = Field(..., description="99th percentile.")
    count: int = Field(..., description="Total number of data points.")


class SimulationMetadata(BaseModel):
    """Consolidated metadata about a simulation run"""
    
    # Core identification
    simulation_id: str = Field(
        ...,
        description="Unique identifier for the simulation run."
    )
    
    # Timing information (from schema/types.py and utils/schemas/results.py)
    start_time: float = Field(
        ...,
        description="Simulation start timestamp (UNIX epoch)."
    )
    end_time: float = Field(
        ...,
        description="Simulation end timestamp (UNIX epoch)."
    )
    duration_seconds: float = Field(
        ...,
        description="Duration of the simulation in seconds."
    )
    timestamp: float = Field(
        default_factory=lambda: datetime.now().timestamp(),
        description="When the simulation was run (UNIX timestamp)."
    )
    
    # Version information (from schema/types.py and utils/schemas/results.py)
    version: str = Field(
        "1.0.0",
        description="Version of the simulation software."
    )
    leaf_cloud_version: str = Field(
        ...,
        description="Version of the LEAF-Cloud framework used."
    )
    
    # Configuration (from simulation/__init__.py)
    config_hash: Optional[str] = Field(
        None,
        description="A hash of the configuration for reproducibility."
    )
    terraform_dir: Optional[str] = Field(
        None,
        description="Path to the Terraform directory used for the simulation."
    )
    config_path: Optional[str] = Field(
        None,
        description="Path to the configuration file used for the simulation."
    )
    
    # Workload and parameters (from simulation/__init__.py)
    workload_config: Optional[Dict[str, Any]] = Field(
        None,
        description="Configuration for the workload generator."
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional simulation parameters."
    )


# ----------------------------------------------------------------------------
# Raw Simulation Result Schema (results_raw.json)
# ----------------------------------------------------------------------------


class RawResourceMetrics(BaseModel):
    """Container for all raw time-series metrics for a single resource.

    All time series data points must be in chronological order and have unique timestamps.
    """

    # Core resource metrics
    utilization: List[TimeSeriesDataPoint] = Field(
        default_factory=list,
        description="Time series of resource utilization (0-100%).",
    )
    power_draw_w: List[TimeSeriesDataPoint] = Field(
        default_factory=list, description="Time series of power draw in watts."
    )

    # Resource-specific metrics
    memory_usage_bytes: List[TimeSeriesDataPoint] = Field(
        default_factory=list,
        description="Time series of memory usage in bytes.",
    )
    network_in_bps: List[TimeSeriesDataPoint] = Field(
        default_factory=list,
        description="Time series of network input bandwidth in bits per second.",
    )
    network_out_bps: List[TimeSeriesDataPoint] = Field(
        default_factory=list,
        description="Time series of network output bandwidth in bits per second.",
    )
    disk_read_bps: List[TimeSeriesDataPoint] = Field(
        default_factory=list,
        description="Time series of disk read bandwidth in bytes per second.",
    )
    disk_write_bps: List[TimeSeriesDataPoint] = Field(
        default_factory=list,
        description="Time series of disk write bandwidth in bytes per second.",
    )
    replica_count: List[TimeSeriesDataPoint] = Field(
        default_factory=list,
        description="Time series of workload replica counts.",
    )

    # Token processing metrics
    tokens_processed: List[TimeSeriesDataPoint] = Field(
        default_factory=list,
        description="Time series of cumulative tokens processed.",
    )
    request_latency_ms: List[TimeSeriesDataPoint] = Field(
        default_factory=list,
        description="Time series of request latencies in milliseconds.",
    )

    # Resource metadata
    resource_type: str = Field(
        ..., description="Type of the resource (e.g., 'gpu', 'cpu', 'tpu')."
    )
    region: str = Field(
        ..., description="Geographic region where the resource is located."
    )

    class Config:
        json_schema_extra = {
            "example": {
                "resource_type": "gpu",
                "region": "us-west1",
                "utilization": [
                    {"timestamp": 0.0, "value": 45.2},
                    {"timestamp": 1.0, "value": 47.8},
                ],
                "power_draw_w": [
                    {"timestamp": 0.0, "value": 250.5},
                    {"timestamp": 1.0, "value": 255.3},
                ],
            }
        }


class TokenTraceEvent(BaseModel):
    """A single event in a token's lifecycle trace."""

    timestamp: float
    event_type: str
    details: Dict[str, Any]


class LatencyMetrics(BaseModel):
    """Metrics related to request and processing latencies."""
    
    end_to_end_ms: MetricStatistics = Field(
        ...,
        description="End-to-end latency statistics in milliseconds."
    )
    processing_ms: MetricStatistics = Field(
        ...,
        description="Processing latency statistics in milliseconds (excluding queue time)."
    )
    queue_time_ms: MetricStatistics = Field(
        ...,
        description="Time spent in queue before processing in milliseconds."
    )
    p90_ms: float = Field(
        ...,
        description="90th percentile latency in milliseconds."
    )
    p99_ms: float = Field(
        ...,
        description="99th percentile latency in milliseconds."
    )
    max_ms: float = Field(
        ...,
        description="Maximum observed latency in milliseconds."
    )


class EndToEndLatencyMetrics(BaseModel):
    """Metrics specifically for end-to-end latency breakdown."""
    
    enabled: bool = Field(
        ...,
        description="Whether end-to-end latency calculation was enabled for this simulation."
    )
    total_end_to_end_ms: MetricStatistics = Field(
        ...,
        description="Total end-to-end latency statistics in milliseconds."
    )
    user_to_infrastructure_ms: MetricStatistics = Field(
        ...,
        description="User-to-infrastructure latency component statistics in milliseconds."
    )
    infrastructure_internal_ms: MetricStatistics = Field(
        ...,
        description="Infrastructure-internal processing latency statistics in milliseconds."
    )
    user_distribution: Optional[Dict[str, float]] = Field(
        None,
        description="User geographic distribution used for calculations (region -> percentage)."
    )
    latency_breakdown_by_user_region: Optional[Dict[str, MetricStatistics]] = Field(
        None,
        description="End-to-end latency breakdown by user region."
    )
    latency_breakdown_by_infrastructure_region: Optional[Dict[str, MetricStatistics]] = Field(
        None,
        description="End-to-end latency breakdown by infrastructure region."
    )


class EnergyMetrics(BaseModel):
    """Metrics related to energy consumption."""
    
    total_energy_wh: float = Field(
        ...,
        description="Total energy consumed in watt-hours."
    )
    avg_power_w: float = Field(
        ...,
        description="Average power draw in watts."
    )
    peak_power_w: float = Field(
        ...,
        description="Peak power draw in watts."
    )
    energy_per_token_wh: float = Field(
        ...,
        description="Energy consumed per token processed in watt-hours."
    )
    energy_by_component: Dict[str, float] = Field(
        default_factory=dict,
        description="Energy consumption by component in watt-hours."
    )


class CarbonMetrics(BaseModel):
    """Metrics related to carbon emissions."""
    
    total_co2e_g: float = Field(
        ...,
        description="Total carbon emissions in grams of CO2 equivalent."
    )
    co2e_per_token_g: float = Field(
        ...,
        description="Carbon emissions per token in grams of CO2 equivalent."
    )
    carbon_intensity_g_per_kwh: float = Field(
        ...,
        description="Carbon intensity in grams of CO2 equivalent per kilowatt-hour."
    )
    renewable_energy_percent: float = Field(
        ...,
        description="Percentage of energy from renewable sources.",
        ge=0,
        le=100
    )
    region: str = Field(
        ...,
        description="Region where the carbon metrics were calculated."
    )


class ScalingMetrics(BaseModel):
    """Metrics related to system scaling and resource utilization."""
    
    avg_utilization_pct: float = Field(
        ...,
        description="Average resource utilization percentage across all resources.",
        ge=0,
        le=100
    )
    peak_utilization_pct: float = Field(
        ...,
        description="Peak resource utilization percentage.",
        ge=0,
        le=100
    )
    underutilized_resources: int = Field(
        ...,
        description="Number of resources with utilization below the target threshold.",
        ge=0
    )
    overutilized_resources: int = Field(
        ...,
        description="Number of resources with utilization above the target threshold.",
        ge=0
    )
    scaling_events: int = Field(
        ...,
        description="Total number of scaling events during the simulation.",
        ge=0
    )
    avg_scaling_duration_s: float = Field(
        ...,
        description="Average duration of scaling events in seconds.",
        ge=0
    )
    resource_efficiency: Dict[str, float] = Field(
        default_factory=dict,
        description="Efficiency metrics by resource type."
    )


class RawSimulationResult(BaseModel):
    """Schema for the raw, unprocessed output of a LEAF-Cloud simulation.

    This represents the complete, unprocessed output of a simulation run,
    containing all time-series data, events, and metadata needed for analysis.
    """

    # Core metadata
    metadata: SimulationMetadata = Field(
        ...,
        description="Metadata about the simulation run, including timing and version info.",
    )

    # Configuration
    config_snapshot: Dict[str, Any] = Field(
        ...,
        description="""
        A complete snapshot of the simulation configuration at the time of the run.
        This should include all parameters needed to reproduce the simulation.
        """,
    )

    # Time-series data
    resource_metrics: Dict[str, RawResourceMetrics] = Field(
        ...,
        description="""
        Raw time-series metrics for each resource in the simulation.
        Keys are resource IDs, values are the corresponding metrics.
        """,
    )

    # Token processing
    token_traces: List[TokenTraceEvent] = Field(
        default_factory=list,
        description="""
        A log of token lifecycle events, including generation, processing, and completion.
        Used for detailed analysis of token flow through the system.
        """,
    )

    # System events
    system_events: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="""
        System-level events that occurred during the simulation.
        Includes scaling events, failures, and other significant occurrences.
        """,
    )

    # Raw model outputs
    raw_model_outputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="""
        Raw outputs from various metric models used during the simulation.
        This provides access to the underlying data used for metrics calculation.
        """,
    )

    # Analysis results (post-simulation)
    analysis_results: Dict[str, Any] = Field(
        default_factory=dict,
        description="""
        Optional, structured outputs from analytical models run after the simulation
        (e.g., latency, energy, scaling). When present, CLI can render richer
        summaries without deriving metrics from raw traces.
        """,
    )

    # Cost data
    cost_data: Optional[Dict[str, Any]] = Field(
        None,
        description="""
        Raw cost estimation data from Infracost CLI.
        Contains the complete JSON response from Infracost breakdown command.
        """,
    )

    # Versioning
    schema_version: str = Field(
        "1.0.0", description="Version of the raw result schema."
    )

    class Config:
        json_schema_extra = {
            "example": {
                "metadata": {
                    "simulation_id": "sim_123",
                    "start_time": 1678901234.0,
                    "end_time": 1678904834.0,
                    "duration_seconds": 3600.0,
                    "leaf_cloud_version": "1.0.0",
                },
                "config_snapshot": {
                    "workload": {
                        "type": "llm_inference",
                        "arrival_rate": 10.0,
                    },
                    "resources": [{"type": "gpu", "count": 4}],
                    "regions": ["us-west1", "us-east1"],
                },
                "resource_metrics": {
                    "gpu-1": {
                        "resource_type": "gpu",
                        "region": "us-west1",
                        "utilization": [
                            {"timestamp": 0.0, "value": 45.2},
                            {"timestamp": 1.0, "value": 47.8},
                        ],
                    }
                },
                "schema_version": "1.0.0",
            }
        }


# ----------------------------------------------------------------------------
# Processed Simulation Result Schema (results_processed.json)
# ----------------------------------------------------------------------------


class AnalysisMetadata(SimulationMetadata):
    """Extends simulation metadata with information about the analysis process."""

    analysis_timestamp: float = Field(
        ..., description="Timestamp when the analysis was performed."
    )
    raw_simulation_id: str = Field(
        ...,
        description="ID of the raw simulation result used for this analysis.",
    )


class ProcessedResourceAnalysis(BaseModel):
    """Container for all processed analysis for a single resource.

    This class provides statistical analysis of time-series metrics for a single resource,
    including performance, efficiency, and cost metrics.
    """

    # Core metrics
    utilization: MetricStatistics = Field(
        ...,
        description="Statistical analysis of resource utilization (0-100%).",
    )

    # Energy and carbon metrics
    energy_wh: MetricStatistics = Field(
        ...,
        description="Statistical analysis of energy consumption in watt-hours.",
    )
    carbon_g_co2e: MetricStatistics = Field(
        ...,
        description="Statistical analysis of carbon emissions in grams of CO2 equivalent.",
    )

    # Performance metrics
    throughput_tokens_per_sec: MetricStatistics = Field(
        ...,
        description="Statistical analysis of token processing throughput in tokens per second.",
    )
    request_latency_ms: MetricStatistics = Field(
        ...,
        description="Statistical analysis of request latencies in milliseconds.",
    )

    # Resource utilization metrics
    memory_utilization: MetricStatistics = Field(
        ...,
        description="Statistical analysis of memory utilization in percentage.",
    )
    network_utilization: MetricStatistics = Field(
        ...,
        description="Statistical analysis of network bandwidth utilization in percentage.",
    )
    disk_utilization: MetricStatistics = Field(
        ...,
        description="Statistical analysis of disk I/O utilization in percentage.",
    )

    # Efficiency metrics
    efficiency_tokens_per_kwh: Optional[float] = Field(
        None,
        description="Average number of tokens processed per kilowatt-hour of energy consumed.",
    )
    carbon_intensity_g_per_token: Optional[float] = Field(
        None,
        description="Average carbon emissions in grams of CO2e per token processed.",
    )

    # Cost metrics
    cost_usd: Optional[MetricStatistics] = Field(
        None,
        description="Statistical analysis of cost in USD for this resource over the simulation period.",
    )
    cost_per_token_usd: Optional[float] = Field(
        None, description="Estimated cost in USD per token processed."
    )

    class Config:
        json_schema_extra = {
            "example": {
                "utilization": {
                    "mean": 75.5,
                    "std": 10.2,
                    "min": 20.0,
                    "max": 95.0,
                    "25%": 65.0,
                    "50%": 75.0,
                    "75%": 85.0,
                    "95%": 92.0,
                    "count": 1000,
                },
                "energy_wh": {
                    "mean": 250.0,
                    "std": 25.0,
                    "min": 100.0,
                    "max": 300.0,
                    "25%": 230.0,
                    "50%": 250.0,
                    "75%": 270.0,
                    "95%": 290.0,
                    "count": 1000,
                },
                "throughput_tokens_per_sec": {
                    "mean": 150.0,
                    "std": 15.0,
                    "min": 50.0,
                    "max": 200.0,
                    "25%": 140.0,
                    "50%": 150.0,
                    "75%": 160.0,
                    "95%": 180.0,
                    "count": 1000,
                },
                "efficiency_tokens_per_kwh": 250000,
                "carbon_intensity_g_per_token": 0.0025,
                "cost_usd": 12.50,
                "cost_per_token_usd": 0.000025,
            }
        }


class ProcessedSimulationResult(BaseModel):
    """Schema for the processed and analyzed simulation results.

    This represents the comprehensive output of the analysis phase, containing both
    aggregated metrics and detailed breakdowns across various dimensions.
    """

    # Core metadata
    metadata: AnalysisMetadata = Field(
        ..., description="Metadata about the simulation and analysis process."
    )

    # Summary metrics (scalars)
    summary_metrics: Dict[str, float] = Field(
        ...,
        description="""
        Aggregated summary metrics for the entire simulation.
        Includes metrics like total tokens, total energy, total cost, etc.
        """,
        example={
            "total_tokens_processed": 1000000,
            "total_energy_wh": 5000.0,
            "total_carbon_g_co2e": 2500.0,
            "total_cost_usd": 25.0,
            "avg_tokens_per_second": 1000.0,
            "avg_latency_ms": 150.0,
            "total_simulation_time_seconds": 3600.0,
        },
    )

    # Detailed analysis
    per_resource_analysis: Dict[str, ProcessedResourceAnalysis] = Field(
        ...,
        description="Detailed analysis for each individual resource, keyed by resource ID.",
    )

    # Aggregated analysis
    per_resource_type_analysis: Dict[str, Dict[str, MetricStatistics]] = Field(
        ...,
        description="""
        Aggregated analysis grouped by resource type.
        First key is resource type (e.g., 'gpu', 'cpu'), second key is metric name.
        """,
    )

    per_region_analysis: Dict[str, Dict[str, MetricStatistics]] = Field(
        ...,
        description="""
        Aggregated analysis grouped by geographic region.
        First key is region name, second key is metric name.
        """,
    )

    per_path_analysis: Dict[str, Dict[str, MetricStatistics]] = Field(
        default_factory=dict,
        description="""
        Aggregated analysis grouped by processing path.
        First key is path identifier, second key is metric name.
        """,
    )

    # Token processing analysis
    token_analysis: Dict[str, MetricStatistics] = Field(
        ...,
        description="""
        Statistical analysis of token processing metrics.
        Includes metrics like end-to-end latency, queue times, etc.
        """,
    )

    # End-to-end latency analysis
    end_to_end_latency: Optional[EndToEndLatencyMetrics] = Field(
        None,
        description="""
        Detailed end-to-end latency analysis including user-to-infrastructure and infrastructure-internal components.
        Only present when end-to-end latency calculation was enabled.
        """,
    )

    # Efficiency metrics
    efficiency_metrics: Dict[str, float] = Field(
        default_factory=dict,
        description="""
        System-wide efficiency metrics.
        Includes metrics like overall tokens per kWh, carbon intensity, etc.
        """,
        example={
            "tokens_per_kwh": 200000.0,
            "carbon_intensity_g_per_token": 0.0025,
            "cost_per_token_usd": 0.000025,
            "energy_per_token_wh": 0.005,
            "resource_utilization_avg_pct": 75.5,
        },
    )

    # Cost analysis
    cost_analysis: Dict[str, Any] = Field(
        default_factory=dict,
        description="""
        Detailed cost breakdown and analysis.
        Includes cost by resource type, region, and component.
        """,
        example={
            "total_cost_usd": 25.0,
            "cost_by_resource_type": {"gpu": 20.0, "cpu": 5.0},
            "cost_by_region": {"us-west1": 15.0, "us-east1": 10.0},
            "cost_components": {
                "compute": 22.0,
                "network": 2.5,
                "storage": 0.5,
            },
        },
    )

    # Distributions and histograms
    distributions: Dict[str, Dict[str, List[float]]] = Field(
        default_factory=dict,
        description="""
        Histograms and distributions for key metrics.
        Keys are metric names, values are dicts with 'bins' and 'counts'.
        """,
        example={
            "latency_ms": {
                "bins": [0, 50, 100, 150, 200, 250, 300],
                "counts": [100, 500, 1000, 800, 300, 100],
            },
            "utilization_pct": {
                "bins": [0, 25, 50, 75, 100],
                "counts": [100, 300, 800, 300],
            },
        },
    )

    # Versioning
    schema_version: str = Field(
        "1.0.0", description="Version of the processed result schema."
    )

    class Config:
        json_schema_extra = {
            "example": {
                "metadata": {
                    "simulation_id": "sim_123",
                    "analysis_timestamp": 1678901234.56789,
                    "raw_simulation_id": "raw_sim_123",
                },
                "summary_metrics": {
                    "total_tokens_processed": 1000000,
                    "total_energy_wh": 5000.0,
                    "total_carbon_g_co2e": 2500.0,
                    "total_cost_usd": 25.0,
                },
                "efficiency_metrics": {
                    "tokens_per_kwh": 200000.0,
                    "carbon_intensity_g_per_token": 0.0025,
                },
            }
        }
