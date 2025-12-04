"""engine.py

This module contains the core logic for the insights generation engine. The
InsightsEngine class takes raw and processed simulation results, runs a series of
analysis functions, and produces a structured InsightsReport.
"""

from __future__ import annotations

import time
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

from ..utils.schemas.results import (
    RawSimulationResult,
    ProcessedSimulationResult,
)
from .models import (
    InsightsReport,
    AnyInsight,
    BottleneckInsight,
    InsightSeverity,
    RecommendationInsight,
    AnomalyInsight,
)
from .extractor import InsightsExtractor

logger = logging.getLogger(__name__)


class InsightsEngine:
    """The engine for generating insights from simulation results.

    This class provides a high-level interface for generating insights from both
    raw and processed simulation results. It leverages the InsightsExtractor for
    detailed analysis and provides additional reporting capabilities.
    """

    def __init__(
        self,
        raw_results: RawSimulationResult,
        processed_results: ProcessedSimulationResult,
        output_dir: Optional[Path] = None,
    ):
        """Initializes the engine with the results to be analyzed.

        Args:
            raw_results: Raw simulation results.
            processed_results: Processed simulation results with metrics and statistics.
            output_dir: Optional directory for saving intermediate analysis results.
        """
        self.raw_results = raw_results
        self.processed_results = processed_results
        self.output_dir = output_dir
        self._resource_metadata = self._extract_resource_metadata()

    def _extract_resource_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Extract metadata for all resources from raw results."""
        metadata = {}
        for res_id, metrics in self.raw_results.resource_metrics.items():
            metadata[res_id] = {
                "type": metrics.resource_type,
                "region": metrics.region,
                "instance_type": (
                    metrics.instance_type
                    if hasattr(metrics, "instance_type")
                    else None
                ),
                "capacity": (
                    metrics.capacity if hasattr(metrics, "capacity") else None
                ),
                "tags": getattr(metrics, "tags", {}),
            }
        return metadata

    def generate_report(self) -> InsightsReport:
        """Generates a comprehensive insights report using the InsightsExtractor.

        Returns:
            InsightsReport containing all detected insights.
        """
        try:
            # Initialize the extractor with processed results
            extractor = InsightsExtractor(
                processed_result=self.processed_results,
                resource_metadata=self._resource_metadata,
            )

            # Generate the insights report
            report = extractor.extract_insights()

            # Add metadata
            report.metadata.update(
                {
                    "analysis_timestamp": time.time(),
                    "raw_simulation_id": self.raw_results.metadata.simulation_id,
                    "processed_analysis_id": getattr(self.processed_results.metadata, 'analysis_id', 'unknown'),
                    "insights_version": "1.0.0",
                    "generator": "leaf_cloud.insights.engine.InsightsEngine",
                }
            )

            # Save the report if output directory is provided
            if self.output_dir:
                self._save_report(report)

            return report

        except Exception as e:
            logger.exception("Failed to generate insights report")
            # Return an empty report with error information
            return InsightsReport(
                metadata={
                    "error": str(e),
                    "analysis_timestamp": time.time(),
                    "raw_simulation_id": (
                        self.raw_results.metadata.simulation_id
                        if hasattr(self.raw_results, "metadata")
                        else "unknown"
                    ),
                    "insights_version": "1.0.0",
                    "generator": "leaf_cloud.insights.engine.InsightsEngine",
                    "status": "error",
                },
                insights=[],
                summary={"error": str(e), "status": "error"},
            )

    def _save_report(self, report: InsightsReport) -> None:
        """Save the insights report to the output directory.

        Args:
            report: The insights report to save.
        """
        if not self.output_dir:
            return

        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)

            # Save the full report as JSON
            report_path = self.output_dir / "insights_report.json"
            with open(report_path, "w") as f:
                f.write(report.model_dump_json(indent=2))

            logger.info(f"Saved insights report to {report_path}")

            # Generate a simplified markdown summary
            self._generate_markdown_summary(report)

        except Exception as e:
            logger.error(f"Failed to save insights report: {e}", exc_info=True)

    def _generate_markdown_summary(self, report: InsightsReport) -> None:
        """Generate a markdown summary of the insights report.

        Args:
            report: The insights report to summarize.
        """
        if not self.output_dir:
            return

        try:
            summary_path = self.output_dir / "insights_summary.md"

            with open(summary_path, "w") as f:
                # Header
                f.write(f"# LEAF Simulation Insights Report\n\n")
                f.write(
                    f"Generated at: {report.metadata.get('analysis_timestamp', 'N/A')}\n"
                )
                f.write(
                    f"Simulation ID: {report.metadata.get('raw_simulation_id', 'N/A')}\n\n"
                )

                # Summary
                f.write("## Summary\n\n")
                f.write(f"- **Total Insights**: {len(report.insights)}")

                # Insights by severity - calculate from insights
                severity_counts = {}
                for insight in report.insights:
                    severity = insight.severity.value
                    severity_counts[severity] = severity_counts.get(severity, 0) + 1
                
                if severity_counts:
                    f.write("\n\n## Insights by Severity\n\n")
                    for severity, count in severity_counts.items():
                        if count > 0:
                            f.write(
                                f"- **{severity.capitalize()}**: {count}\n"
                            )

                # Recommendations section
                recommendations = [
                    i
                    for i in report.insights
                    if isinstance(i, RecommendationInsight)
                ]
                if recommendations:
                    f.write("\n## Recommendations\n\n")
                    for idx, rec in enumerate(recommendations, 1):
                        f.write(f"{idx}. **{rec.description}**\n")
                        f.write(f"   *Recommendation*: {rec.recommended_action}\n")
                        f.write(f"   *Confidence*: {rec.confidence:.0%}\n\n")

                # Bottlenecks section
                bottlenecks = [
                    i
                    for i in report.insights
                    if isinstance(i, BottleneckInsight)
                ]
                if bottlenecks:
                    f.write("\n## Performance Bottlenecks\n\n")
                    for idx, b in enumerate(bottlenecks, 1):
                        f.write(f"{idx}. **{b.description}**\n")
                        if hasattr(b, "recommendation"):
                            f.write(
                                f"   *Recommendation*: {b.recommendation}\n"
                            )
                        f.write(
                            f"   *Severity*: {b.severity.value.capitalize()}\n\n"
                        )

                # Anomalies section
                anomalies = [
                    i for i in report.insights if isinstance(i, AnomalyInsight)
                ]
                if anomalies:
                    f.write("\n## Detected Anomalies\n\n")
                    for idx, a in enumerate(anomalies, 1):
                        f.write(f"{idx}. **{a.description}**\n")
                        f.write(f"   *Metric*: {a.metric}\n")
                        f.write(
                            f"   *Value*: {a.value:.2f} (Baseline: {a.baseline:.2f}, {a.deviation:+.1f}%)\n"
                        )
                        if hasattr(a, "recommendation"):
                            f.write(
                                f"   *Recommendation*: {a.recommendation}\n"
                            )
                        f.write(
                            f"   *Severity*: {a.severity.value.capitalize()}\n\n"
                        )

                # Footer
                f.write("\n---\n")
                f.write("*Generated by LEAF Cloud Simulation Insights*\n")

            logger.info(f"Generated markdown summary at {summary_path}")

        except Exception as e:
            logger.error(
                f"Failed to generate markdown summary: {e}", exc_info=True
            )
