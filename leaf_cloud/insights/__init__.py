"""
__init__.py

This file makes the `insights` directory a package, allowing us to expose
selected parts of the `insights` module to users of the `leaf-cloud` package.
"""

from .engine import InsightsEngine
from .extractor import InsightsExtractor
from .models import (
    InsightsReport,
    AnyInsight,
    BottleneckInsight,
    InsightSeverity,
    RecommendationInsight,
    AnomalyInsight,
)