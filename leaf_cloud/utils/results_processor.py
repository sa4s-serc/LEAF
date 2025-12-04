"""results_processor.py

A helper class that encapsulates the simulation-metrics post-processing logic.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ..orchestrator import Orchestrator

from ..utils.schemas.results import (
    RawSimulationResult,
    ProcessedSimulationResult,
    AnalysisMetadata,
    MetricStatistics,
    ProcessedResourceAnalysis,
    TimeSeriesDataPoint,
    RawResourceMetrics,
)

logger = logging.getLogger(__name__)

# Constants for calculations
SECONDS_PER_HOUR = 3600
WATTS_PER_KILOWATT = 1000


class ResultsProcessor:
    """
    Processes raw simulation results into analyzed metrics and insights.

    This class handles the transformation of raw time-series data into
    aggregated statistics, efficiency metrics, and cost analysis.
    """

    def __init__(self, orchestrator: "Orchestrator") -> None:
        """Initialize the ResultsProcessor with an orchestrator reference.

        Args:
            orchestrator: The orchestrator instance that manages the simulation.
        """
        self.orchestrator = orchestrator
        self._resource_cache = {}

    def process(
        self, raw_results: RawSimulationResult
    ) -> ProcessedSimulationResult:
        """Process raw simulation results to produce an analyzed summary.

        This is the main entry point that orchestrates the entire processing
        pipeline from raw data to analyzed results.

        Args:
            raw_results: The raw simulation results to process.

        Returns:
            ProcessedSimulationResult containing the analyzed metrics.

        Raises:
            ValueError: If the input data is invalid or processing fails.
        """
        try:
            # Get resource metadata mapping
            resource_mapping = self._get_resource_mapping()

            # Run metric models to get derived metrics
            model_outputs = self._run_metric_models(
                raw_results, resource_mapping
            )

            # Process per-resource metrics
            per_resource_analysis = self._calculate_per_resource_analysis(
                raw_results.resource_metrics, model_outputs
            )

            # Process token metrics
            token_analysis = self._analyze_token_metrics(
                raw_results.token_traces
            )

            # Calculate system-wide metrics
            summary_metrics = self._calculate_summary_metrics(
                per_resource_analysis,
                token_analysis,
                raw_results.metadata.duration_seconds,
            )

            # Group analysis by various dimensions
            per_resource_type_analysis = self._group_analysis_by_attribute(
                per_resource_analysis, resource_mapping, "type"
            )
            per_region_analysis = self._group_analysis_by_attribute(
                per_resource_analysis, resource_mapping, "region"
            )
            per_path_analysis = self._analyze_by_processing_path(
                raw_results.token_traces, resource_mapping
            )

            # Calculate efficiency metrics
            efficiency_metrics = self._calculate_efficiency_metrics(
                summary_metrics, per_resource_analysis, token_analysis
            )

            # Calculate cost analysis
            cost_analysis = self._calculate_cost_analysis(
                per_resource_analysis, resource_mapping
            )

            # Generate distributions
            distributions = self._generate_distributions(
                raw_results, per_resource_analysis
            )

            # Create analysis metadata
            analysis_metadata = AnalysisMetadata(
                **raw_results.metadata.model_dump(),
                analysis_timestamp=time.time(),
                raw_simulation_id=raw_results.metadata.simulation_id,
            )

            return ProcessedSimulationResult(
                metadata=analysis_metadata,
                summary_metrics=summary_metrics,
                per_resource_analysis=per_resource_analysis,
                per_resource_type_analysis=per_resource_type_analysis,
                per_region_analysis=per_region_analysis,
                per_path_analysis=per_path_analysis,
                token_analysis=token_analysis,
                efficiency_metrics=efficiency_metrics,
                cost_analysis=cost_analysis,
                distributions=distributions,
            )

        except Exception as e:
            logger.error(f"Error processing simulation results: {str(e)}")
            raise ValueError(
                f"Failed to process simulation results: {str(e)}"
            ) from e

    def _run_metric_models(
        self,
        raw_results: RawSimulationResult,
        resource_mapping: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run metric models to derive additional metrics from raw data.

        Args:
            raw_results: The raw simulation results.
            resource_mapping: Mapping of resource IDs to resource metadata.

        Returns:
            Dictionary containing derived metrics and model outputs.
        """
        model_outputs = {
            "energy_models": {},
            "carbon_models": {},
            "cost_models": {},
        }

        # Process each resource's metrics through the appropriate models
        for res_id, metrics in raw_results.resource_metrics.items():
            # Get resource metadata
            resource_meta = resource_mapping.get(res_id, None)

            # Calculate energy consumption (simplified example)
            if metrics.power_draw_w:
                energy_wh = self._calculate_energy_consumption(
                    metrics.power_draw_w, raw_results.metadata.duration_seconds
                )
                # Create MetricStatistics for energy metrics using the _calculate_statistics helper
                energy_stats = self._calculate_statistics(metrics.power_draw_w)

                # For energy_wh, we need to create a single value MetricStatistics
                energy_wh_stats = self._create_single_value_metric(energy_wh)
                model_outputs["energy_models"][res_id] = {
                    "energy_wh": energy_wh_stats,
                    "avg_power_w": energy_stats,
                }

            # Calculate carbon emissions (simplified example)
            if res_id in model_outputs["energy_models"]:
                region = self._get_resource_attr(
                    resource_meta, 'region', 'global')
                # Get the energy value directly instead of accessing .mean
                energy_wh = model_outputs["energy_models"][res_id]["energy_wh"]
                if hasattr(energy_wh, 'mean'):
                    energy_value = energy_wh.mean  # It's a MetricStatistics object
                else:
                    energy_value = energy_wh  # It's already a scalar

                carbon_g = self._calculate_carbon_emissions(
                    energy_value,
                    region,
                )
                # Create carbon stats using the helper
                carbon_stats = self._create_single_value_metric(carbon_g)

                # Create carbon intensity stats using the helper
                carbon_intensity = self._get_carbon_intensity(region)
                carbon_intensity_stats = self._create_single_value_metric(
                    carbon_intensity)
                model_outputs["carbon_models"][res_id] = {
                    "carbon_g_co2e": carbon_stats,
                    "carbon_intensity_g_per_kwh": carbon_intensity_stats,
                }

            # Calculate resource costs (simplified example)
            cost_usd = self._calculate_resource_cost(
                resource_meta, raw_results.metadata.duration_seconds
            )
            if cost_usd is not None:
                # Calculate cost per hour, handling zero duration case
                duration_hours = (
                    raw_results.metadata.duration_seconds / SECONDS_PER_HOUR
                    if raw_results.metadata.duration_seconds > 0
                    else 0.0
                )
                cost_per_hour = cost_usd / duration_hours if duration_hours > 0 else 0.0

                # Create cost stats using the helper
                cost_stats = self._create_single_value_metric(cost_usd)
                cost_per_hour_stats = self._create_single_value_metric(
                    cost_per_hour)

                model_outputs["cost_models"][res_id] = {
                    "cost_usd": cost_stats,
                    "cost_per_hour": cost_per_hour_stats,
                }

        return model_outputs

    def _calculate_energy_consumption(
        self, power_series: List[TimeSeriesDataPoint], duration_seconds: float
    ) -> float:
        """Calculate total energy consumption in watt-hours from power time series."""
        if not power_series:
            return 0.0

        # Sort by timestamp to ensure correct integration
        sorted_series = sorted(power_series, key=lambda x: x.timestamp)

        # Calculate energy using trapezoidal integration
        total_joules = 0.0
        for i in range(1, len(sorted_series)):
            dt = sorted_series[i].timestamp - sorted_series[i - 1].timestamp
            avg_power = (
                sorted_series[i - 1].value + sorted_series[i].value
            ) / 2
            total_joules += avg_power * dt

        # Convert to watt-hours
        return total_joules / SECONDS_PER_HOUR

    def _calculate_carbon_emissions(
        self, energy_wh: float, region: str
    ) -> float:
        """Calculate carbon emissions in grams of CO2e for given energy consumption."""
        carbon_intensity = self._get_carbon_intensity(region)
        return energy_wh * carbon_intensity / 1000  # Convert to grams

    def _get_carbon_intensity(self, region: str) -> float:
        """Get carbon intensity for a region in gCO2e/kWh."""
        # This is a simplified example - in practice, use actual grid carbon intensity data
        carbon_intensities = {
            "us-west": 250,  # gCO2e/kWh
            "us-east": 350,
            "europe": 300,
            "asia": 500,
        }
        # Default to global average
        return carbon_intensities.get(region.lower(), 400)

    def _calculate_resource_cost(
        self, resource_meta: Any, duration_seconds: float
    ) -> Optional[float]:
        """Calculate the cost of a resource for the simulation duration.

        Args:
            resource_meta: Either a dictionary or an object containing resource metadata
            duration_seconds: Duration of the simulation in seconds

        Returns:
            The calculated cost in USD or None if cost cannot be determined
        """
        if not resource_meta:
            return None

        # Get hourly rate from metadata or use defaults
        hourly_rate = self._get_resource_attr(resource_meta, 'hourly_rate')

        # If no hourly rate, use defaults based on resource type
        if hourly_rate is None:
            # Default hourly rates by resource type (in USD)
            default_rates = {
                "gpu": 0.90,
                "tpu": 1.50,
                "cpu": 0.10,
            }

            # Get resource type, defaulting to 'cpu'
            resource_type = str(self._get_resource_attr(
                resource_meta, 'type', 'cpu')).lower()
            hourly_rate = default_rates.get(resource_type, 0.05)

        # Calculate cost for the simulation duration
        if duration_seconds <= 0:
            return 0.0

        try:
            hours = float(duration_seconds) / SECONDS_PER_HOUR
            cost = float(hourly_rate) * hours
            # Round to avoid floating point precision issues
            return round(cost, 6)
        except (TypeError, ValueError) as e:
            logger.warning(f"Error calculating resource cost: {e}")
            return None

    def _create_single_value_metric(self, value: float) -> MetricStatistics:
        """Create a MetricStatistics object for a single value.

        Args:
            value: The value to use for all statistics.

        Returns:
            A MetricStatistics object with all fields set to the given value.
        """
        return MetricStatistics(
            mean=float(value),
            std=0.0,
            min=float(value),
            max=float(value),
            p25=float(value),
            p50=float(value),
            p75=float(value),
            p95=float(value),
            p99=float(value),
            count=1,
        )

    def _calculate_statistics(
        self, time_series: List[TimeSeriesDataPoint]
    ) -> MetricStatistics:
        """Calculate detailed statistics for a time series.

        Args:
            time_series: List of time series data points.

        Returns:
            MetricStatistics object containing statistical measures.
        """
        if not time_series:
            return MetricStatistics(
                mean=0,
                std=0,
                min=0,
                max=0,
                p25=0,
                p50=0,
                p75=0,
                p95=0,
                p99=0,
                count=0,
            )

        values = [dp.value for dp in time_series]
        arr = np.array(values)

        # Handle case with only one data point
        if len(arr) == 1:
            val = float(arr[0])
            return MetricStatistics(
                mean=val,
                std=0,
                min=val,
                max=val,
                p25=val,
                p50=val,
                p75=val,
                p95=val,
                p99=val,
                count=1,
            )

        return MetricStatistics(
            mean=float(np.mean(arr)),
            std=float(np.std(arr)),
            min=float(np.min(arr)),
            max=float(np.max(arr)),
            p25=float(np.percentile(arr, 25)),
            p50=float(np.percentile(arr, 50)),
            p75=float(np.percentile(arr, 75)),
            p95=float(np.percentile(arr, 95)),
            p99=float(np.percentile(arr, 99)),
            count=len(arr),
        )

    def _calculate_rate_metrics(
        self, time_series: List[TimeSeriesDataPoint]
    ) -> MetricStatistics:
        """Calculate rate metrics (e.g., tokens/second) from cumulative counters.

        Args:
            time_series: List of time series data points with cumulative values.

        Returns:
            MetricStatistics object containing rate statistics.
        """
        if len(time_series) < 2:
            return MetricStatistics(
                mean=0,
                std=0,
                min=0,
                max=0,
                p25=0,
                p50=0,
                p75=0,
                p95=0,
                p99=0,
                count=0,
            )

        # Sort by timestamp
        sorted_series = sorted(time_series, key=lambda x: x.timestamp)

        # Calculate rates between consecutive points
        rates = []
        for i in range(1, len(sorted_series)):
            dt = sorted_series[i].timestamp - sorted_series[i - 1].timestamp
            if dt > 0:  # Avoid division by zero
                dv = sorted_series[i].value - sorted_series[i - 1].value
                rates.append(dv / dt)

        if not rates:
            return MetricStatistics(
                mean=0,
                std=0,
                min=0,
                max=0,
                p25=0,
                p50=0,
                p75=0,
                p95=0,
                p99=0,
                count=0,
            )

        arr = np.array(rates)
        return MetricStatistics(
            mean=float(np.mean(arr)),
            std=float(np.std(arr)),
            min=float(np.min(arr)),
            max=float(np.max(arr)),
            p25=float(np.percentile(arr, 25)),
            p50=float(np.percentile(arr, 50)),
            p75=float(np.percentile(arr, 75)),
            p95=float(np.percentile(arr, 95)),
            p99=float(np.percentile(arr, 99)),
            count=len(arr),
        )

    def _calculate_per_resource_analysis(
        self,
        resource_metrics: Dict[str, RawResourceMetrics],
        model_outputs: Dict[str, Any],
    ) -> Dict[str, ProcessedResourceAnalysis]:
        """Calculate processed analysis for each resource.

        Args:
            resource_metrics: Dictionary of raw resource metrics.
            model_outputs: Outputs from metric models.

        Returns:
            Dictionary mapping resource IDs to their processed analysis.
        """
        analysis = {}

        for res_id, metrics in resource_metrics.items():
            # Get model outputs for this resource
            energy_model = model_outputs.get("energy_models", {}).get(
                res_id, {}
            )
            carbon_model = model_outputs.get("carbon_models", {}).get(
                res_id, {}
            )
            cost_model = model_outputs.get("cost_models", {}).get(res_id, {})

            # Calculate utilization statistics
            utilization_stats = self._calculate_statistics(metrics.utilization)

            # Calculate memory metrics if available
            memory_stats = self._calculate_statistics(
                metrics.memory_usage_bytes
            )

            # Calculate network metrics
            net_in_stats = self._calculate_statistics(metrics.network_in_bps)
            net_out_stats = self._calculate_statistics(metrics.network_out_bps)

            # Calculate disk metrics
            disk_read_stats = self._calculate_statistics(metrics.disk_read_bps)
            disk_write_stats = self._calculate_statistics(
                metrics.disk_write_bps
            )

            # Calculate token processing rate if tokens are being tracked
            token_rate_stats = self._calculate_rate_metrics(
                metrics.tokens_processed
            )

            # Get energy and carbon metrics from models
            energy_wh = energy_model.get("energy_wh", 0)
            carbon_g = carbon_model.get("carbon_g_co2e", 0)

            # Calculate efficiency metrics if we have tokens processed
            efficiency_metrics = {}
            if metrics.tokens_processed and energy_wh > 0:
                total_tokens = (
                    metrics.tokens_processed[-1].value
                    if metrics.tokens_processed
                    else 0
                )
                if total_tokens > 0:
                    efficiency_metrics = {
                        # Convert to tokens/kWh
                        "efficiency_tokens_per_kwh": (total_tokens / energy_wh)
                        * 1000,
                        "carbon_intensity_g_per_token": (
                            carbon_g / total_tokens if total_tokens > 0 else 0
                        ),
                        "cost_per_token_usd": (
                            cost_model.get("cost_usd").mean / total_tokens
                            if total_tokens > 0 and cost_model.get("cost_usd") is not None
                            else 0
                        ),
                    }

            # Create the processed analysis
            analysis[res_id] = ProcessedResourceAnalysis(
                utilization=utilization_stats,
                energy_wh=MetricStatistics(
                    mean=energy_wh,
                    std=0,
                    min=energy_wh,
                    max=energy_wh,
                    p25=energy_wh,
                    p50=energy_wh,
                    p75=energy_wh,
                    p95=energy_wh,
                    p99=energy_wh,
                    count=1,
                ),
                carbon_g_co2e=MetricStatistics(
                    mean=carbon_g,
                    std=0,
                    min=carbon_g,
                    max=carbon_g,
                    p25=carbon_g,
                    p50=carbon_g,
                    p75=carbon_g,
                    p95=carbon_g,
                    p99=carbon_g,
                    count=1,
                ),
                throughput_tokens_per_sec=token_rate_stats,
                request_latency_ms=self._calculate_statistics(
                    metrics.request_latency_ms
                ),
                memory_utilization=memory_stats,
                network_utilization=MetricStatistics(
                    mean=(net_in_stats.mean + net_out_stats.mean) / 2,
                    std=0,  # Simplified
                    min=min(net_in_stats.min, net_out_stats.min),
                    max=max(net_in_stats.max, net_out_stats.max),
                    p25=(net_in_stats.p25 + net_out_stats.p25) / 2,
                    p50=(net_in_stats.p50 + net_out_stats.p50) / 2,
                    p75=(net_in_stats.p75 + net_out_stats.p75) / 2,
                    p95=(net_in_stats.p95 + net_out_stats.p95) / 2,
                    p99=(net_in_stats.p99 + net_out_stats.p99) / 2,
                    count=min(net_in_stats.count, net_out_stats.count),
                ),
                disk_utilization=MetricStatistics(
                    mean=(disk_read_stats.mean + disk_write_stats.mean) / 2,
                    std=0,  # Simplified
                    min=min(disk_read_stats.min, disk_write_stats.min),
                    max=max(disk_read_stats.max, disk_write_stats.max),
                    p25=(disk_read_stats.p25 + disk_write_stats.p25) / 2,
                    p50=(disk_read_stats.p50 + disk_write_stats.p50) / 2,
                    p75=(disk_read_stats.p75 + disk_write_stats.p75) / 2,
                    p95=(disk_read_stats.p95 + disk_write_stats.p95) / 2,
                    p99=(disk_read_stats.p99 + disk_write_stats.p99) / 2,
                    count=min(disk_read_stats.count, disk_write_stats.count),
                ),
                cost_usd=(
                    MetricStatistics(
                        mean=float(cost_model["cost_usd"].mean),
                        std=float(cost_model["cost_usd"].std),
                        min=float(cost_model["cost_usd"].min),
                        max=float(cost_model["cost_usd"].max),
                        p25=float(cost_model["cost_usd"].p25),
                        p50=float(cost_model["cost_usd"].p50),
                        p75=float(cost_model["cost_usd"].p75),
                        p95=float(cost_model["cost_usd"].p95),
                        p99=float(cost_model["cost_usd"].p99),
                        count=int(cost_model["cost_usd"].count),
                    )
                    if cost_model.get("cost_usd") is not None
                    else None
                ),
                **efficiency_metrics,
            )

        return analysis

    def _analyze_token_metrics(
        self, token_traces: List[Any]
    ) -> Dict[str, MetricStatistics]:
        """Analyze token processing metrics from token traces.

        Args:
            token_traces: List of token trace events.

        Returns:
            Dictionary of token processing metrics.
        """
        if not token_traces:
            return {}

        # Extract end-to-end latencies
        e2e_latencies = []
        queue_times = []
        processing_times = []

        for trace in token_traces:
            if not isinstance(trace, dict):
                trace = trace.dict()

            # Calculate end-to-end latency
            if "generated_at" in trace and "completed_at" in trace:
                e2e_latencies.append(
                    trace["completed_at"] - trace["generated_at"]
                )

            # Calculate queue time (time between generation and processing start)
            if "generated_at" in trace and "processing_started_at" in trace:
                queue_times.append(
                    trace["processing_started_at"] - trace["generated_at"]
                )

            # Calculate processing time (time between processing start and completion)
            if "processing_started_at" in trace and "completed_at" in trace:
                processing_times.append(
                    trace["completed_at"] - trace["processing_started_at"]
                )

        # Convert to milliseconds
        def to_ms(values):
            return [v * 1000 for v in values] if values else [0]

        return {
            "end_to_end_latency_ms": (
                self._calculate_statistics(
                    [
                        TimeSeriesDataPoint(timestamp=0, value=v)
                        for v in to_ms(e2e_latencies)
                    ]
                )
                if e2e_latencies
                else MetricStatistics(
                    mean=0,
                    std=0,
                    min=0,
                    max=0,
                    p25=0,
                    p50=0,
                    p75=0,
                    p95=0,
                    p99=0,
                    count=0,
                )
            ),
            "queue_time_ms": (
                self._calculate_statistics(
                    [
                        TimeSeriesDataPoint(timestamp=0, value=v)
                        for v in to_ms(queue_times)
                    ]
                )
                if queue_times
                else MetricStatistics(
                    mean=0,
                    std=0,
                    min=0,
                    max=0,
                    p25=0,
                    p50=0,
                    p75=0,
                    p95=0,
                    p99=0,
                    count=0,
                )
            ),
            "processing_time_ms": (
                self._calculate_statistics(
                    [
                        TimeSeriesDataPoint(timestamp=0, value=v)
                        for v in to_ms(processing_times)
                    ]
                )
                if processing_times
                else MetricStatistics(
                    mean=0,
                    std=0,
                    min=0,
                    max=0,
                    p25=0,
                    p50=0,
                    p75=0,
                    p95=0,
                    p99=0,
                    count=0,
                )
            ),
        }

    def _get_resource_metrics(self) -> Dict[str, Any]:
        """Get metrics for all resources.

        This method returns a dictionary of resource metrics that can be used for
        summary calculations. The metrics include token processing information.

        Returns:
            Dictionary mapping resource IDs to their metrics
        """
        resource_metrics = {}

        # Get the orchestrator's resource tracker if available
        if hasattr(self, 'orchestrator') and hasattr(self.orchestrator, 'resource_tracker'):
            resource_tracker = self.orchestrator.resource_tracker
            # Get metrics for all resources from the tracker
            for res_id, resource in resource_tracker.resources.items():
                metrics = {
                    'tokens_processed': getattr(resource, 'tokens_processed', []),
                    # Add other metrics as needed
                }
                resource_metrics[res_id] = type(
                    'ResourceMetrics', (), metrics)()

        return resource_metrics

    def _calculate_summary_metrics(
        self,
        per_resource_analysis: Dict[str, ProcessedResourceAnalysis],
        token_analysis: Dict[str, MetricStatistics],
        duration_seconds: float,
    ) -> Dict[str, float]:
        """Calculate summary metrics for the entire simulation.

        Args:
            per_resource_analysis: Processed analysis for each resource.
            token_analysis: Token processing metrics.
            duration_seconds: Total simulation duration in seconds.

        Returns:
            Dictionary of summary metrics.
        """
        if not per_resource_analysis:
            return {}

        # Calculate totals across all resources
        total_energy_wh = sum(
            analysis.energy_wh.mean
            for analysis in per_resource_analysis.values()
        )

        total_carbon_g = sum(
            analysis.carbon_g_co2e.mean
            for analysis in per_resource_analysis.values()
        )

        total_cost_usd = sum(
            analysis.cost_usd.mean if analysis.cost_usd else 0.0
            for analysis in per_resource_analysis.values()
        )

        # Calculate average utilization (weighted by resource capacity if available)
        avg_utilization = (
            np.mean(
                [
                    analysis.utilization.mean
                    for analysis in per_resource_analysis.values()
                    if analysis.utilization and analysis.utilization.count > 0
                ]
            )
            if per_resource_analysis
            else 0
        )

        # Get token metrics
        total_tokens = sum(
            (
                metrics.tokens_processed[-1].value
                if metrics.tokens_processed
                else 0
            )
            for metrics in self._get_resource_metrics().values()
            if hasattr(metrics, "tokens_processed")
            and metrics.tokens_processed
        )

        avg_tokens_per_sec = (
            token_analysis.get("throughput_tokens_per_sec", {}).get("mean", 0)
            if isinstance(token_analysis, dict)
            else 0
        )

        return {
            "total_tokens_processed": total_tokens,
            "avg_tokens_per_second": avg_tokens_per_sec,
            "total_energy_wh": total_energy_wh,
            "total_carbon_g_co2e": total_carbon_g,
            "total_cost_usd": total_cost_usd,
            "avg_utilization_percent": avg_utilization,
            "total_simulation_time_seconds": duration_seconds,
            "avg_end_to_end_latency_ms": (
                token_analysis.get("end_to_end_latency_ms").mean if token_analysis.get(
                    "end_to_end_latency_ms") else 0
            ),
            "avg_queue_time_ms": (
                token_analysis.get("queue_time_ms").mean if token_analysis.get(
                    "queue_time_ms") else 0
            ),
            "avg_processing_time_ms": (
                token_analysis.get("processing_time_ms").mean if token_analysis.get(
                    "processing_time_ms") else 0
            ),
        }

    def _group_analysis_by_attribute(
        self,
        per_resource_analysis: Dict[str, ProcessedResourceAnalysis],
        resource_mapping: Dict[str, Any],
        attribute: str,
    ) -> Dict[str, Dict[str, MetricStatistics]]:
        """Group resource analysis by a given attribute (e.g., 'type' or 'region').

        Args:
            per_resource_analysis: Processed analysis for each resource.
            resource_mapping: Mapping of resource IDs to resource metadata.
            attribute: Attribute to group by (e.g., 'type', 'region').

        Returns:
            Nested dictionary mapping attribute values to metric statistics.
        """
        if not per_resource_analysis:
            return {}

        grouped_metrics = defaultdict(lambda: defaultdict(list))

        for res_id, analysis in per_resource_analysis.items():
            # Get the attribute value from resource metadata
            resource_meta = resource_mapping.get(res_id, None)
            attr_value = self._get_resource_attr(
                resource_meta, attribute, "unknown")

            # Skip if attribute is not available
            if not attr_value:
                continue

            # Add all metrics to the appropriate group
            for field_name, field_value in analysis.dict().items():
                if isinstance(field_value, dict) and "mean" in field_value:
                    grouped_metrics[attr_value][field_name].append(field_value)

        # Aggregate statistics for each group and metric
        result = {}
        for attr_value, metrics in grouped_metrics.items():
            result[attr_value] = {}
            for metric_name, stats_list in metrics.items():
                # Simple average aggregation for now
                # In a real implementation, we might want to weight by resource capacity
                means = [s["mean"] for s in stats_list if s["count"] > 0]
                counts = [s["count"] for s in stats_list if s["count"] > 0]

                if not means:
                    continue

                total_count = sum(counts)
                if total_count == 0:
                    continue

                # Weighted average of means
                weighted_mean = (
                    sum(m * c for m, c in zip(means, counts)) / total_count
                )

                # For other statistics, we'd need the raw data to be accurate
                # This is a simplified approach
                result[attr_value][metric_name] = MetricStatistics(
                    mean=weighted_mean,
                    std=np.std(means) if len(means) > 1 else 0,
                    min=min(means),
                    max=max(means),
                    p25=(
                        np.percentile(means, 25)
                        if len(means) > 1
                        else weighted_mean
                    ),
                    p50=(
                        np.percentile(means, 50)
                        if len(means) > 1
                        else weighted_mean
                    ),
                    p75=(
                        np.percentile(means, 75)
                        if len(means) > 1
                        else weighted_mean
                    ),
                    p95=(
                        np.percentile(means, 95)
                        if len(means) > 1
                        else weighted_mean
                    ),
                    p99=(
                        np.percentile(means, 99)
                        if len(means) > 1
                        else weighted_mean
                    ),
                    count=total_count,
                )

        return dict(result)

    def _analyze_by_processing_path(
        self, 
        token_traces: List[Any], 
        resource_mapping: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        """Analyze token processing paths through the system.
        
        This method analyzes the paths tokens take through the system and calculates
        statistics for each unique path. A path is defined as the sequence of
        resources a token passes through.
        
        Args:
            token_traces: List of token trace events from the simulation
            resource_mapping: Mapping of resource IDs to resource metadata
            
        Returns:
            Dictionary mapping path signatures to path statistics with the following structure:
            {
                "resource1 → resource2 → resource3": {
                    "count": int,  # Number of tokens that took this path
                    "duration": MetricStatistics,  # Statistics about path duration
                    "resources": List[str]  # List of unique resources in path
                },
                ...
            }
        """
        from typing import Dict, Set, TypedDict
        
        # Define local type for better type checking
        class PathStats(TypedDict):
            count: int
            durations: List[float]
            resources: Set[str]
        
        logger.debug("Starting analysis of token processing paths")
        
        if not token_traces:
            logger.debug("No token traces provided for path analysis")
            return {}
            
        # Validate resource_mapping
        if not isinstance(resource_mapping, dict):
            logger.warning("Invalid resource_mapping type: %s, expected dict", type(resource_mapping).__name__)
            resource_mapping = {}
        
        # Group token traces by token ID
        traces_by_token: Dict[str, List[Any]] = {}
        for trace in token_traces:
            try:
                if not hasattr(trace, 'details') or not isinstance(trace.details, dict):
                    logger.debug("Skipping trace with missing or invalid details")
                    continue
                    
                token_id = trace.details.get('token_id', 'unknown')
                if not isinstance(token_id, str):
                    token_id = str(token_id)
                    
                if token_id not in traces_by_token:
                    traces_by_token[token_id] = []
                traces_by_token[token_id].append(trace)
                
            except Exception as e:
                logger.warning("Error processing trace: %s", str(e), exc_info=True)
                continue
        
        logger.debug("Grouped %d tokens into %d unique token IDs", 
                    len(token_traces), len(traces_by_token))
        
        # Analyze each token's path
        path_stats: Dict[str, PathStats] = {}
        for token_id, traces in traces_by_token.items():
            try:
                # Sort traces by timestamp
                try:
                    traces.sort(key=lambda x: getattr(x, 'timestamp', 0))
                except Exception as e:
                    logger.warning("Error sorting traces for token %s: %s", 
                                 token_id, str(e))
                    continue
                
                # Extract path as sequence of resource types
                path: List[str] = []
                path_resources: Set[str] = set()
                
                for trace in traces:
                    try:
                        if not hasattr(trace, 'details') or not isinstance(trace.details, dict):
                            continue
                            
                        resource_id = trace.details.get('resource_id')
                        if not resource_id:
                            continue
                            
                        # Get resource type from mapping, or use resource_id if not found
                        resource_info = resource_mapping.get(str(resource_id), {})
                        res_type = self._get_resource_type(resource_info, resource_id)
                        
                        if res_type not in path_resources:  # Only count each resource type once per token
                            path.append(res_type)
                            path_resources.add(res_type)
                            
                    except Exception as e:
                        logger.warning("Error processing trace for token %s: %s", 
                                     token_id, str(e), exc_info=True)
                
                # Skip if no valid path
                if not path:
                    logger.debug("No valid path found for token %s", token_id)
                    continue
                    
                # Create path signature
                path_signature = ' → '.join(path)
                
                # Calculate path metrics
                try:
                    start_time = float(getattr(traces[0], 'timestamp', 0))
                    end_time = float(getattr(traces[-1], 'timestamp', 0))
                    duration = max(0.0, end_time - start_time) if end_time > start_time else 0.0
                except (TypeError, ValueError) as e:
                    logger.warning("Error calculating duration for token %s: %s", 
                                 token_id, str(e))
                    duration = 0.0
                
                # Initialize path stats if not exists
                if path_signature not in path_stats:
                    path_stats[path_signature] = {
                        'count': 0,
                        'durations': [],
                        'resources': set(path)
                    }
                
                # Update path stats
                path_stats[path_signature]['count'] += 1
                path_stats[path_signature]['durations'].append(duration)
                
            except Exception as e:
                logger.error("Unexpected error analyzing token %s: %s", 
                            token_id, str(e), exc_info=True)
                continue
        
        # Convert to MetricStatistics for each path
        result: Dict[str, Dict[str, Any]] = {}
        for path, stats in path_stats.items():
            try:
                durations = stats['durations']
                result[path] = {
                    'count': stats['count'],
                    'duration': self._create_metric_statistics(durations) if durations else None,
                    'resources': sorted(list(stats['resources']))  # Convert to sorted list for consistency
                }
            except Exception as e:
                logger.error("Error creating metrics for path %s: %s", 
                            path, str(e), exc_info=True)
                continue
        
        logger.debug("Completed path analysis. Found %d unique paths.", len(result))
        return result

    def _generate_distributions(
        self,
        raw_results: RawSimulationResult,
        per_resource_analysis: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate distribution data for various metrics.
        
        This method creates distribution data for key metrics that can be used
        for visualization and statistical analysis. The distributions include
        histograms and probability density functions for metrics like:
        - Resource utilization
        - Processing times
        - Queue lengths
        - Token processing rates
        
        Args:
            raw_results: The raw simulation results.
            per_resource_analysis: Dictionary of per-resource analysis.
            
        Returns:
            Dictionary containing distribution data with the following structure:
            {
                "resource_utilization": {
                    "bins": List[float],  # Bin edges
                    "counts": List[int],  # Count in each bin
                    "density": List[float]  # Normalized density
                },
                "processing_times": {
                    "bins": List[float],
                    "counts": List[int],
                    "density": List[float]
                },
                # Additional distributions...
            }
        """
        distributions = {}
        
        try:
            # Generate distribution for resource utilization
            util_values = [
                float(analysis.utilization.mean) 
                for analysis in per_resource_analysis.values()
                if hasattr(analysis, 'utilization') and hasattr(analysis.utilization, 'mean')
            ]
            
            if util_values:
                hist, bin_edges = np.histogram(util_values, bins=20, range=(0, 100), density=True)
                distributions["resource_utilization"] = {
                    "bins": bin_edges.tolist(),
                    "counts": hist.tolist(),
                    "density": (hist / hist.sum()).tolist() if hist.sum() > 0 else [0] * len(hist)
                }
            
            # Generate distribution for processing times
            proc_times = []
            for analysis in per_resource_analysis.values():
                if hasattr(analysis, 'processing_time_ms') and hasattr(analysis.processing_time_ms, 'mean'):
                    proc_times.append(analysis.processing_time_ms.mean)
            
            if proc_times:
                hist, bin_edges = np.histogram(proc_times, bins=20, density=True)
                distributions["processing_times_ms"] = {
                    "bins": bin_edges.tolist(),
                    "counts": hist.tolist(),
                    "density": (hist / hist.sum()).tolist() if hist.sum() > 0 else [0] * len(hist)
                }
            
            # Add token processing rate distribution if available
            if hasattr(raw_results, 'token_metrics') and hasattr(raw_results.token_metrics, 'processing_rate_tokens_per_sec'):
                rates = [float(x) for x in raw_results.token_metrics.processing_rate_tokens_per_sec if x > 0]
                if rates:
                    hist, bin_edges = np.histogram(rates, bins=20, density=True)
                    distributions["token_processing_rates"] = {
                        "bins": bin_edges.tolist(),
                        "counts": hist.tolist(),
                        "density": (hist / hist.sum()).tolist() if hist.sum() > 0 else [0] * len(hist)
                    }
            
            logger.debug(f"Generated distributions for {len(distributions)} metrics")
            
        except Exception as e:
            logger.error(f"Error generating distributions: {str(e)}", exc_info=True)
            return {}
        
        return distributions

    def _calculate_cost_analysis(
        self,
        per_resource_analysis: Dict[str, Any],
        resource_mapping: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Calculate detailed cost analysis from simulation results.

        This method aggregates cost metrics across resources and provides
        breakdowns by resource type and region.

        Args:
            per_resource_analysis: Dictionary of per-resource analysis results.
            resource_mapping: Mapping of resource IDs to resource metadata.

        Returns:
            Dictionary containing cost analysis with the following structure:
            {
                "total_cost_usd": float,  # Total cost across all resources
                "cost_by_resource_type": Dict[str, float],  # Cost by resource type
                "cost_by_region": Dict[str, float],  # Cost by region
                "cost_components": {  # Cost breakdown by component
                    "compute": float,
                    "network": float,
                    "storage": float
                }
            }
        """
        cost_analysis = {
            "total_cost_usd": 0.0,
            "cost_by_resource_type": {},
            "cost_by_region": {},
            "cost_components": {
                "compute": 0.0,
                "network": 0.0,
                "storage": 0.0
            }
        }

        try:
            # Aggregate costs across all resources
            for res_id, analysis in per_resource_analysis.items():
                if not hasattr(analysis, "cost_usd") or not analysis.cost_usd:
                    continue

                # Get resource metadata
                resource_meta = resource_mapping.get(res_id, {})
                resource_type = self._get_resource_attr(resource_meta, "type", "unknown")
                region = self._get_resource_attr(resource_meta, "region", "unknown")
                
                # Get cost from analysis (using mean of the cost distribution)
                cost = analysis.cost_usd.mean if hasattr(analysis.cost_usd, "mean") else 0.0
                if cost <= 0:
                    continue

                # Update total cost
                cost_analysis["total_cost_usd"] += cost

                # Update cost by resource type
                if resource_type not in cost_analysis["cost_by_resource_type"]:
                    cost_analysis["cost_by_resource_type"][resource_type] = 0.0
                cost_analysis["cost_by_resource_type"][resource_type] += cost

                # Update cost by region
                if region not in cost_analysis["cost_by_region"]:
                    cost_analysis["cost_by_region"][region] = 0.0
                cost_analysis["cost_by_region"][region] += cost

                # Update cost components (simplified breakdown)
                # This is a simplified example - adjust based on your actual cost model
                if "gpu" in resource_type.lower():
                    cost_analysis["cost_components"]["compute"] += cost * 0.9  # 90% compute
                    cost_analysis["cost_components"]["network"] += cost * 0.05  # 5% network
                    cost_analysis["cost_components"]["storage"] += cost * 0.05  # 5% storage
                else:
                    # Default split for other resource types
                    cost_analysis["cost_components"]["compute"] += cost * 0.8
                    cost_analysis["cost_components"]["network"] += cost * 0.15
                    cost_analysis["cost_components"]["storage"] += cost * 0.05

            logger.info(
                f"Completed cost analysis. Total cost: ${cost_analysis['total_cost_usd']:.2f}"
            )
            logger.debug(f"Cost by type: {cost_analysis['cost_by_resource_type']}")
            logger.debug(f"Cost by region: {cost_analysis['cost_by_region']}")

        except Exception as e:
            logger.error(f"Error in cost analysis: {str(e)}", exc_info=True)
            # Return empty cost analysis in case of error
            return {
                "total_cost_usd": 0.0,
                "cost_by_resource_type": {},
                "cost_by_region": {},
                "cost_components": {
                    "compute": 0.0,
                    "network": 0.0,
                    "storage": 0.0
                }
            }

        return cost_analysis

    def _calculate_efficiency_metrics(
        self,
        summary_metrics: Dict[str, float],
        per_resource_analysis: Dict[str, Any],
        token_analysis: Dict[str, Any],
    ) -> Dict[str, float]:
        """Calculate system-wide efficiency metrics from simulation results.

        This method computes various efficiency metrics like tokens per kWh, carbon intensity,
        and cost per token based on the aggregated simulation results.

        Args:
            summary_metrics: Dictionary of aggregated summary metrics.
            per_resource_analysis: Dictionary of per-resource analysis results.
            token_analysis: Dictionary of token processing metrics.

        Returns:
            Dictionary containing system-wide efficiency metrics with the following keys:
            - tokens_per_kwh: Average number of tokens processed per kilowatt-hour
            - carbon_intensity_g_per_token: Average carbon emissions in grams per token
            - cost_per_token_usd: Average cost in USD per token processed
            - energy_per_token_wh: Average energy consumption in watt-hours per token
            - resource_utilization_avg_pct: Average resource utilization percentage

        Example:
            {
                "tokens_per_kwh": 200000.0,
                "carbon_intensity_g_per_token": 0.0025,
                "cost_per_token_usd": 0.000025,
                "energy_per_token_wh": 0.005,
                "resource_utilization_avg_pct": 75.5
            }
        """
        metrics = {}
        
        try:
            total_tokens = summary_metrics.get("total_tokens_processed", 0)
            total_energy_wh = summary_metrics.get("total_energy_wh", 0)
            total_carbon_g_co2e = summary_metrics.get("total_carbon_g_co2e", 0)
            total_cost_usd = summary_metrics.get("total_cost_usd", 0)
            
            # Calculate tokens per kWh (convert Wh to kWh by dividing by 1000)
            if total_energy_wh > 0:
                metrics["tokens_per_kwh"] = total_tokens / (total_energy_wh / 1000)
                logger.debug(f"Calculated {metrics['tokens_per_kwh']:.2f} tokens per kWh")
            else:
                logger.warning("Total energy is zero, cannot calculate tokens per kWh")
                metrics["tokens_per_kwh"] = 0.0
            
            # Calculate carbon intensity (g CO2e per token)
            if total_tokens > 0:
                metrics["carbon_intensity_g_per_token"] = total_carbon_g_co2e / total_tokens
                logger.debug(
                    f"Calculated carbon intensity: {metrics['carbon_intensity_g_per_token']:.6f} g CO2e/token"
                )
            else:
                logger.warning("No tokens processed, cannot calculate carbon intensity")
                metrics["carbon_intensity_g_per_token"] = 0.0
            
            # Calculate cost per token
            if total_tokens > 0:
                metrics["cost_per_token_usd"] = total_cost_usd / total_tokens
                metrics["energy_per_token_wh"] = total_energy_wh / total_tokens if total_energy_wh > 0 else 0.0
                logger.debug(
                    f"Calculated cost per token: ${metrics['cost_per_token_usd']:.8f}"
                )
            else:
                logger.warning("No tokens processed, cannot calculate cost per token")
                metrics["cost_per_token_usd"] = 0.0
                metrics["energy_per_token_wh"] = 0.0
            
            # Calculate average resource utilization
            if per_resource_analysis:
                total_util = 0.0
                count = 0
                for res_id, analysis in per_resource_analysis.items():
                    if hasattr(analysis, "utilization") and hasattr(analysis.utilization, "mean"):
                        total_util += analysis.utilization.mean
                        count += 1
                        logger.debug(f"Added utilization for {res_id}: {analysis.utilization.mean:.2f}%")
                
                if count > 0:
                    avg_util = total_util / count
                    metrics["resource_utilization_avg_pct"] = avg_util
                    logger.info(f"Calculated average resource utilization: {avg_util:.2f}%")
                else:
                    logger.warning("No resource utilization data available")
                    metrics["resource_utilization_avg_pct"] = 0.0
            else:
                logger.warning("No per-resource analysis data available")
                metrics["resource_utilization_avg_pct"] = 0.0
            
            logger.info("Efficiency metrics calculation completed successfully")
            
        except Exception as e:
            logger.error(f"Error calculating efficiency metrics: {str(e)}", exc_info=True)
            # Return default values in case of error
            metrics.update({
                "tokens_per_kwh": 0.0,
                "carbon_intensity_g_per_token": 0.0,
                "cost_per_token_usd": 0.0,
                "energy_per_token_wh": 0.0,
                "resource_utilization_avg_pct": 0.0
            })
        
        return metrics

    def _get_resource_attr(self, resource: Any, attr: str, default: Any = None) -> Any:
        """Safely get an attribute from a resource, whether it's a dict or object.

        Args:
            resource: The resource object or dict
            attr: The attribute name to get
            default: Default value if attribute doesn't exist

        Returns:
            The attribute value or default
        """
        if resource is None:
            return default

        # Handle dictionary access
        if hasattr(resource, 'get') and callable(getattr(resource, 'get')):
            return resource.get(attr, default)

        # Handle object attribute access
        if hasattr(resource, attr):
            value = getattr(resource, attr)
            if value is not None:
                return value

        # Handle nested config object
        if hasattr(resource, 'config'):
            config = getattr(resource, 'config')
            if hasattr(config, 'get') and callable(getattr(config, 'get')):
                return config.get(attr, default)

        return default

    def _get_resource_mapping(self) -> Dict[str, Any]:
        """Return a mapping from resource names to resource objects.

        Preserves the original resource objects while ensuring safe attribute access.
        """
        orchestrator = self.orchestrator
        if not hasattr(orchestrator, "model_builder"):
            logger.warning("No model builder available for resource mapping")
            return {}

        # Return the original resource mapping with objects
        return getattr(orchestrator.model_builder, "resource_mapping", {})
