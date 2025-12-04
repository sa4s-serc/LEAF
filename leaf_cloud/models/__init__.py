import logging

"""
LEAF-Cloud Framework Models Package

This package contains various models for simulation and analysis of cloud infrastructure:

- Scaling Model: Dynamic pod scaling based on request rates and resource utilization
- Latency Model: Estimation of end-to-end latency across cloud resources
- Energy Model: Calculation of energy consumption based on resource utilization
- Carbon Model: Estimation of carbon footprint based on energy and regional factors
- Constraints: Validation of resource constraints and service limits
- Aggregation: Statistical analysis and reporting of simulation results
"""


# Set up package-level logger
logger = logging.getLogger(__name__)

# Version information
__version__ = "0.1.0"

# Import main components from scaling module
from .scaling import (
    ScalingModel,
)

# Import main components from latency module
from .latency import (
    LatencyModel,
    load_latency_model,
)

# Import main components from energy module
from .energy import (
    EnergyModel,
    load_energy_model,
)

# Import main components from carbon module
from .carbon import (
    CarbonModel,
    load_carbon_model,
)

# Import main components from constraints module
from .constraints import (
    Constraint,
    ConstraintViolation,
    ConstraintManager,
    ConstraintType,
    ConstraintSeverity,
    create_standard_constraints,
)

# Import main components from aggregation module
from .aggregation import (
    MetricAggregator,
    SimulationSummary,
    MetricSummary,
    ResourceMetrics,
    WorkloadMetrics,
    combine_simulation_results,
    generate_report,
)

# Define what's available for import with "from models import *"
__all__ = [
    # Scaling
    "ScalingModel",
    # Latency
    "LatencyModel",
    "load_latency_model",
    # Energy
    "EnergyModel",
    "load_energy_model",
    # Carbon
    "CarbonModel",
    "load_carbon_model",
    # Constraints
    "Constraint",
    "ConstraintViolation",
    "ConstraintManager",
    "ConstraintType",
    "ConstraintSeverity",
    "create_standard_constraints",
    # Aggregation
    "MetricAggregator",
    "SimulationSummary",
    "MetricSummary",
    "ResourceMetrics",
    "WorkloadMetrics",
    "combine_simulation_results",
    "generate_report",
]