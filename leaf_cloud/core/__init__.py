"""
LEAF-Cloud Core Package

This package provides the core components for cloud infrastructure modeling
and simulation using Colored Petri Nets.

Main components:
- Petri Net simulation engine for infrastructure modeling
- Resource abstractions for cloud resources and their states
- Workload generators for simulating realistic infrastructure usage patterns
"""

__version__ = "0.1.0"

# Import core components from the Petri Net module
from .petri_net import (
    PetriNet,
    Place,
    Transition,
    Arc,
    Token,
    TokenColor,
    Event,
)

# Import resource management components
from .resource import (
    Resource,
    ResourcePool,
    ResourceState,
    ResourceType,
    UtilizationRecord,
)

# Import workload modeling components
from .workload import (
    Workload,
    SteadyWorkload,
    BurstWorkload,
    CyclicalWorkload,
    RandomWorkload,
    CustomWorkload,
    WorkloadMix,
    WorkloadStatistics,
    WorkloadPatternType,
)

# Define what gets exported with "from core import *"
__all__ = [
    # Petri Net components
    "PetriNet",
    "Place",
    "Transition",
    "Arc",
    "Token",
    "TokenColor",
    "Event",
    # Resource components
    "Resource",
    "ResourcePool",
    "ResourceState",
    "ResourceType",
    "UtilizationRecord",
    # Workload components
    "Workload",
    "SteadyWorkload",
    "BurstWorkload",
    "CyclicalWorkload",
    "RandomWorkload",
    "CustomWorkload",
    "WorkloadMix",
    "WorkloadStatistics",
    "WorkloadPatternType",
]
