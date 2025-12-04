"""Insights extraction for simulation results analysis."""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import numpy as np
from datetime import datetime, UTC

from ..utils.schemas.results import (
    ProcessedSimulationResult,
    ProcessedResourceAnalysis,
    MetricStatistics,
)
from .models import (
    InsightSeverity,
    InsightType,
    BaseInsight,
    RecommendationInsight,
    AnomalyInsight,
    BottleneckInsight,
    InsightsReport,
)


@dataclass
class ResourceContext:
    """Contextual information about a resource for insights generation."""

    resource_id: str
    resource_type: str
    region: str
    analysis: ProcessedResourceAnalysis
    metadata: Dict[str, Any]


class InsightsExtractor:
    """Extracts insights from processed simulation results."""

    def __init__(
        self,
        processed_result: ProcessedSimulationResult,
        resource_metadata: Dict[str, Dict[str, Any]],
    ):
        """Initialize the insights extractor.

        Args:
            processed_result: The processed simulation results.
            resource_metadata: Mapping of resource IDs to their metadata.
        """
        self.processed_result = processed_result
        self.resource_metadata = resource_metadata
        self.insights: List[BaseInsight] = []
        self._resource_contexts = self._create_resource_contexts()

    def _create_resource_contexts(self) -> Dict[str, ResourceContext]:
        """Create resource contexts for analysis."""
        contexts = {}
        for (
            res_id,
            analysis,
        ) in self.processed_result.per_resource_analysis.items():
            meta = self.resource_metadata.get(res_id, {})
            contexts[res_id] = ResourceContext(
                resource_id=res_id,
                resource_type=meta.get("type", "unknown"),
                region=meta.get("region", "unknown"),
                analysis=analysis,
                metadata=meta,
            )
        return contexts

    def extract_insights(self) -> "InsightsReport":
        """Extract all insights from the processed results.

        Returns:
            InsightsReport containing all extracted insights.
        """
        self.insights = []

        # Extract different types of insights
        self._extract_bottlenecks()
        self._extract_anomalies()
        self._generate_recommendations()

        # Create the final report
        return InsightsReport(
            insights=self.insights,
            metadata={
                "simulation_id": self.processed_result.metadata.simulation_id,
                "total_resources_analyzed": len(self._resource_contexts),
                "insights_generated": len(self.insights),
                "generated_at": datetime.now(UTC).isoformat(),
            },
        )

    def _extract_bottlenecks(self) -> None:
        """Identify resource bottlenecks in the system."""
        # Find resources with high utilization
        for ctx in self._resource_contexts.values():
            util = ctx.analysis.utilization

            if util.p95 > 90:  # High utilization threshold
                self.insights.append(
                    BottleneckInsight(
                        resource_id=ctx.resource_id,
                        severity=InsightSeverity.CRITICAL,
                        confidence=0.9,
                        category="performance",
                        description=f"High utilization detected on {ctx.resource_type} {ctx.resource_id}",
                        metric="utilization",
                        utilization_percent=util.p95,
                    )
                )

            # Check for memory pressure
            if hasattr(ctx.analysis, "memory_utilization"):
                mem = ctx.analysis.memory_utilization
                if mem.p95 > 85:  # High memory utilization
                    self.insights.append(
                        BottleneckInsight(
                            resource_id=ctx.resource_id,
                            severity=InsightSeverity.WARNING,
                            confidence=0.8,
                            category="performance",
                            description=f"High memory utilization detected on {ctx.resource_type} {ctx.resource_id}",
                            metric="memory_utilization",
                            utilization_percent=mem.p95,
                        )
                    )

    def _extract_anomalies(self) -> None:
        """Detect anomalous behavior in the system."""
        # Check for high latency
        for ctx in self._resource_contexts.values():
            if hasattr(ctx.analysis, "request_latency_ms"):
                latency = ctx.analysis.request_latency_ms

                # Check for high latency compared to historical baseline
                # This is a simplified example - in practice, you'd compare against historical data
                if latency.p95 > 1000:  # 1 second threshold
                    self.insights.append(
                        AnomalyInsight(
                            resource_id=ctx.resource_id,
                            resource_type=ctx.resource_type,
                            severity=InsightSeverity.CRITICAL,
                            confidence=0.85,
                            metric="request_latency_ms",
                            value=latency.p95,
                            baseline=100,  # Example baseline
                            deviation=(latency.p95 - 100)
                            / 100
                            * 100,  # % deviation
                            timestamp=datetime.now(UTC),
                            recommendation="Investigate root cause of high latency",
                        )
                    )

            # Check for high error rates if available
            if hasattr(ctx.analysis, "error_rate"):
                error_rate = ctx.analysis.error_rate.mean
                if error_rate > 0.05:  # 5% error rate threshold
                    self.insights.append(
                        AnomalyInsight(
                            resource_id=ctx.resource_id,
                            resource_type=ctx.resource_type,
                            severity=InsightSeverity.CRITICAL,
                            confidence=0.9,
                            metric="error_rate",
                            value=error_rate,
                            baseline=0.01,  # 1% baseline
                            deviation=(error_rate - 0.01)
                            / 0.01
                            * 100,  # % deviation
                            timestamp=datetime.now(UTC),
                            recommendation="Investigate and fix the root cause of errors",
                        )
                    )

    def _generate_recommendations(self) -> None:
        """Generate optimization recommendations."""
        # Check for underutilized resources
        for ctx in self._resource_contexts.values():
            util = ctx.analysis.utilization

            # Recommend downsizing for underutilized resources
            if util.p95 < 30 and util.max < 50:  # Low utilization
                self.insights.append(
                    RecommendationInsight(
                        resource_id=ctx.resource_id,
                        resource_type=ctx.resource_type,
                        severity=InsightSeverity.INFO,
                        confidence=0.8,
                        category="cost_optimization",
                        description=f"{ctx.resource_type} {ctx.resource_id} is underutilized",
                        recommended_action=f"Consider downsizing {ctx.resource_type} {ctx.resource_id} to a smaller instance type",
                        estimated_impact="30% cost savings estimate",
                    )
                )

            # Check for cost optimization opportunities
            if hasattr(ctx.analysis, "efficiency_tokens_per_kwh"):
                efficiency = ctx.analysis.efficiency_tokens_per_kwh
                if (
                    efficiency and efficiency < 1000
                ):  # Low efficiency threshold
                    self.insights.append(
                        RecommendationInsight(
                            resource_id=ctx.resource_id,
                            resource_type=ctx.resource_type,
                            severity=InsightSeverity.WARNING,
                            confidence=0.7,
                            category="efficiency",
                            description=f"Low processing efficiency for {ctx.resource_type} {ctx.resource_id}",
                            recommended_action="Consider optimizing model or upgrading hardware",
                            estimated_impact="20% efficiency improvement potential",
                        )
                    )

        # Check for regional optimization opportunities
        self._check_regional_optimizations()

    def _check_regional_optimizations(self) -> None:
        """Check for optimization opportunities across regions."""
        # Group resources by region
        regions = {}
        for ctx in self._resource_contexts.values():
            if ctx.region not in regions:
                regions[ctx.region] = []
            regions[ctx.region].append(ctx)

        # Check for regions with high carbon intensity
        for region, resources in regions.items():
            carbon_intensity = np.mean(
                [
                    r.analysis.carbon_g_co2e.mean
                    / (r.analysis.energy_wh.mean + 1e-6)
                    * 1000
                    for r in resources
                    if hasattr(r.analysis, "carbon_g_co2e")
                    and hasattr(r.analysis, "energy_wh")
                ]
            )

            if carbon_intensity > 300:  # gCO2e/kWh threshold
                self.insights.append(
                    RecommendationInsight(
                        resource_id=None,
                        resource_type="region",
                        severity=InsightSeverity.WARNING,
                        confidence=0.8,
                        category="sustainability",
                        description=f"High carbon intensity in region {region}",
                        recommended_action=f"Consider migrating workloads to a lower-carbon region",
                        estimated_impact="40% carbon reduction potential",
                        metadata={
                            "region": region,
                            "carbon_intensity": carbon_intensity,
                        },
                    )
                )

    def _generate_insights_summary(self) -> Dict[str, Any]:
        """Generate a summary of the extracted insights."""
        severity_counts = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "info": 0,
        }

        type_counts = {"bottleneck": 0, "anomaly": 0, "recommendation": 0}

        for insight in self.insights:
            # Count by severity
            if insight.severity == InsightSeverity.CRITICAL:
                severity_counts["critical"] += 1
            elif insight.severity == InsightSeverity.WARNING:
                severity_counts["medium"] += 1
            elif insight.severity == InsightSeverity.INFO:
                severity_counts["info"] += 1
            else:
                severity_counts["info"] += 1

            # Count by type
            if isinstance(insight, BottleneckInsight):
                type_counts["bottleneck"] += 1
            elif isinstance(insight, AnomalyInsight):
                type_counts["anomaly"] += 1
            elif isinstance(insight, RecommendationInsight):
                type_counts["recommendation"] += 1

        return {
            "total_insights": len(self.insights),
            "severity_breakdown": severity_counts,
            "type_breakdown": type_counts,
            "has_critical_issues": severity_counts["critical"] > 0,
            "has_high_priority_issues": severity_counts["high"] > 0
            or severity_counts["critical"] > 0,
            "generated_at": datetime.now(UTC).isoformat(),
        }
