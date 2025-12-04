"""LEAF-Cloud Orchestrator Module.

This module provides the main orchestration components for the LEAF-Cloud framework,
including the core Orchestrator class that coordinates the simulation lifecycle,
model building, event processing, and metrics collection.

The module follows a modular design with clear separation of concerns:
- Core orchestration logic (core.py)
- Event handling (handlers/)
- Data models (models.py)
- Reporting (reporting.py)
- Metrics collection (metrics.py)

Example:
    ```python
    from leaf_cloud.orchestrator import Orchestrator
    from leaf_cloud.config import LEAFCloudConfig

    # Create a configuration
    config = LEAFCloudConfig()
    
    # Initialize the orchestrator
    orchestrator = Orchestrator(config)
    
    # Run a simulation
    result = orchestrator.run_simulation()
    ```
"""
from __future__ import annotations

import importlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Map of attribute names to their module paths for lazy loading
_MODULE_MAP = {
    # Core
    "Orchestrator": (".core", "Orchestrator"),

    # Models
    "ProgressState": (".models", "ProgressState"),
    "SimulationState": (".models", "SimulationState"),
    "Token": (".models", "Token"),
    "TokenColor": (".models", "TokenColor"),
    "TokenCompletedLog": (".models", "TokenCompletedLog"),
    "TokenCreationLog": (".models", "TokenCreationLog"),
    "TokenDistributionLog": (".models", "TokenDistributionLog"),
    "TokenFlowLogEntry": (".models", "TokenFlowLogEntry"),
    "TransitionFiredLog": (".models", "TransitionFiredLog"),

    # Handlers
    "BaseEventHandler": (".handlers", "BaseEventHandler"),
    "TokenCreationHandler": (".handlers", "TokenCreationHandler"),
    "TokenDistributionHandler": (".handlers", "TokenDistributionHandler"),
    "TokenCompletionHandler": (".handlers", "TokenCompletionHandler"),
    "TransitionCompletionHandler": (".handlers", "TransitionCompletionHandler"),

    # Reporting
    "ReportGenerator": (".reporting", "ReportGenerator"),

    # Metrics
    "MetricsCollector": (".metrics", "MetricsCollector"),

    # Exceptions
    "OrchestratorError": (".exceptions", "OrchestratorError"),
    "SimulationError": (".exceptions", "SimulationError"),
    "ConfigurationError": (".exceptions", "ConfigurationError"),
    "ResourceError": (".exceptions", "ResourceError"),
    "StateTransitionError": (".exceptions", "StateTransitionError"),
    "ValidationError": (".exceptions", "ValidationError"),
    "ExportError": (".exceptions", "ExportError"),
    "ModelBuildError": (".exceptions", "ModelBuildError"),
    "WorkloadError": (".exceptions", "WorkloadError"),

    # Constants
    "DEFAULT_EVENT_HANDLERS": (".constants", "DEFAULT_EVENT_HANDLERS"),

    # Utils
    "SimulationResult": ("..utils.results", "SimulationResult"),
}

__all__ = list(_MODULE_MAP.keys())

def __getattr__(name: str) -> Any:
    """Lazy import of orchestrator components to improve startup performance."""
    if name in _MODULE_MAP:
        module_path, attr_name = _MODULE_MAP[name]
        module = importlib.import_module(module_path, package=__name__)
        attr = getattr(module, attr_name)
        globals()[name] = attr  # Cache for future access
        return attr

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
