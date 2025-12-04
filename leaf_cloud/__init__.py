"""
LEAF-Cloud: Layered Eco-centric Analytical Framework for Cloud Infrastructure

A framework for modeling, simulating, and analyzing cloud infrastructure using
Terraform configurations, with a focus on eco-centric metrics like energy usage
and carbon emissions.
"""

# Package metadata
__version__ = "0.1.0"

# Import main components for convenient access
from .config import LEAFCloudConfig, load_config

# Lazy import function for orchestrator to avoid circular dependencies
def _get_orchestrator():
    """Lazy import of Orchestrator to avoid circular dependencies."""
    from .orchestrator.core import Orchestrator
    return Orchestrator

def _get_simulation_state():
    """Lazy import of SimulationState to avoid circular dependencies."""
    from .orchestrator.models import SimulationState
    return SimulationState
from .leaf_types import (
    SimulationStatus,
    ResultFormat,
    TokenFlowLogEntry,
    ResourceMetrics,
    SimulationResult,
    SimulationResults,
)

# Public proxy functions for backward compatibility
def Orchestrator(*args, **kwargs):
    """Public proxy for Orchestrator class."""
    return _get_orchestrator()(*args, **kwargs)

def SimulationState():
    """Public proxy for SimulationState enum."""
    return _get_simulation_state()

# Define __all__ to limit what's imported with wildcard imports
__all__ = [
    # Configuration related
    "LEAFCloudConfig",
    "load_config",
    # Core components (proxy functions)
    "Orchestrator",
    "SimulationState",
    # Types
    "SimulationStatus",
    "ResultFormat",
    "TokenFlowLogEntry",
    "ResourceMetrics",
    "SimulationResult",
    "SimulationResults",
    # Package metadata
    "__version__",
]
