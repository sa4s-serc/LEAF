"""Utilities for generating processed results suitable for insights.

This module provides helper functions to create a minimal ProcessedSimulationResult
from raw results so the InsightsEngine can run without requiring an external
processing pipeline.
"""

from __future__ import annotations

from typing import List
from .engine import logger  # reuse insights logger
from ..utils.schemas.results import (
    RawSimulationResult,
    ProcessedSimulationResult,
    AnalysisMetadata,
    ProcessedResourceAnalysis,
    MetricStatistics,
)
import time


def _percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    data = sorted(data)
    k = (len(data) - 1) * p / 100.0
    f = int(k)
    c = k - f
    if f + 1 < len(data):
        return data[f] * (1 - c) + data[f + 1] * c
    return data[f]


def _stats_from_series(values: List[float]) -> MetricStatistics:
    if not values:
        return MetricStatistics(
            mean=0.0, std=0.0, min=0.0, max=0.0,
            p25=0.0, p50=0.0, p75=0.0, p95=0.0, p99=0.0, count=0
        )
    n = len(values)
    mean_val = sum(values) / n
    var = sum((x - mean_val) ** 2 for x in values) / n if n > 1 else 0.0
    return MetricStatistics(
        mean=mean_val,
        std=var ** 0.5,
        min=min(values),
        max=max(values),
        p25=_percentile(values, 25),
        p50=_percentile(values, 50),
        p75=_percentile(values, 75),
        p95=_percentile(values, 95),
        p99=_percentile(values, 99),
        count=n,
    )


def auto_process_raw_results(raw_results: RawSimulationResult) -> ProcessedSimulationResult:
    """Create a lightweight processed view from raw results for insights.

    This mirrors the minimal behavior previously in the CLI insights command, but
    packaged for reuse from other call sites.
    """
    # Build utilization stats per resource from raw utilization time series
    per_resource_analysis = {}
    for resource_id, metrics in raw_results.resource_metrics.items():
        util_values = [pt.value for pt in metrics.utilization] if metrics.utilization else []
        utilization_stats = _stats_from_series(util_values)

        # Placeholder stats for other dimensions; insights focus primarily on utilization
        per_resource_analysis[resource_id] = ProcessedResourceAnalysis(
            utilization=utilization_stats,
            energy_wh=_stats_from_series([0.0]),
            carbon_g_co2e=_stats_from_series([0.0]),
            throughput_tokens_per_sec=_stats_from_series([]),
            request_latency_ms=_stats_from_series([]),
            memory_utilization=_stats_from_series([]),
            network_utilization=_stats_from_series([]),
            disk_utilization=_stats_from_series([]),
            cost_usd=_stats_from_series([0.0]),
        )

    analysis_metadata = AnalysisMetadata(
        **raw_results.metadata.model_dump(),
        analysis_timestamp=time.time(),
        raw_simulation_id=raw_results.metadata.simulation_id,
    )

    # Very light summary placeholders (insights extractor uses per_resource_analysis)
    processed = ProcessedSimulationResult(
        metadata=analysis_metadata,
        summary_metrics={
            "total_energy_wh": 0.0,
            "total_carbon_g_co2e": 0.0,
            "total_cost_usd": 0.0,
            "avg_utilization": 0.0,
            "total_tokens_processed": 0,
            "avg_throughput_tokens_per_sec": 0.0,
            "avg_latency_ms": 0.0,
        },
        per_resource_analysis=per_resource_analysis,
        per_resource_type_analysis={},
        per_region_analysis={},
        per_path_analysis={},
        token_analysis={},
        efficiency_metrics={},
        cost_analysis={},
        distributions={},
    )

    logger.debug("Created lightweight processed results for %d resources", len(per_resource_analysis))
    return processed

