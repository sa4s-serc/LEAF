"""models.py

Defines the Pydantic models for the insights report. This includes the structure for
individual insights (like recommendations, anomalies, and bottlenecks) and the
overall report that aggregates them.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Dict, Any, Optional, Union

from pydantic import BaseModel, Field


class InsightType(str, Enum):
    """Enumeration for the type of insight."""

    RECOMMENDATION = "recommendation"
    ANOMALY = "anomaly"
    BOTTLENECK = "bottleneck"
    GENERAL = "general"


class InsightSeverity(str, Enum):
    """Enumeration for the severity level of an insight."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class BaseInsight(BaseModel):
    """Base model for a single insight, providing common fields."""

    type: InsightType = Field(..., description="The type of the insight.")
    severity: InsightSeverity = Field(
        ..., description="The severity level of the insight."
    )
    category: str = Field(
        ...,
        description="A category for the insight, e.g., 'performance', 'cost', 'reliability'.",
    )
    description: str = Field(
        ..., description="A human-readable description of the insight."
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="The confidence level of the insight, from 0.0 to 1.0.",
    )
    data_references: Dict[str, Any] = Field(
        {},
        description="References to data in the raw or processed results that support this insight.",
    )


class RecommendationInsight(BaseInsight):
    """An insight that suggests a specific action or optimization."""

    type: InsightType = InsightType.RECOMMENDATION
    recommended_action: str = Field(
        ..., description="The recommended action to take."
    )
    estimated_impact: Optional[str] = Field(
        None,
        description="The estimated impact of implementing the recommendation (e.g., 'cost savings of 10%').",
    )


class AnomalyInsight(BaseInsight):
    """An insight that highlights an unusual or unexpected pattern in the data."""

    type: InsightType = InsightType.ANOMALY
    metric: str = Field(
        ..., description="The metric in which the anomaly was detected."
    )
    observed_value: Any = Field(
        ...,
        description="The observed value or pattern that is considered anomalous.",
    )
    expected_value_range: Optional[List[Any]] = Field(
        None, description="The range of expected values."
    )


class BottleneckInsight(BaseInsight):
    """An insight that identifies a performance bottleneck in the system."""

    type: InsightType = InsightType.BOTTLENECK
    resource_id: str = Field(
        ..., description="The ID of the resource identified as a bottleneck."
    )
    metric: str = Field(
        ...,
        description="The performance metric indicating the bottleneck (e.g., 'utilization', 'latency').",
    )
    utilization_percent: Optional[float] = Field(
        None,
        description="The utilization percentage of the bottlenecked resource.",
    )


# A union type to allow for different kinds of insights in the report
AnyInsight = Union[RecommendationInsight, AnomalyInsight, BottleneckInsight]


class InsightsReport(BaseModel):
    """The main model for the insights report, containing metadata and a list of insights."""

    version: str = Field(
        "1.0.0", description="The version of the insights report schema."
    )
    metadata: Dict[str, Any] = Field(
        ...,
        description="Metadata about the analysis, such as timestamps and source file IDs.",
    )
    insights: List[AnyInsight] = Field(
        ...,
        description="A list of insights generated from the simulation results.",
    )
