"""
Exception hierarchy for the LEAF-Cloud Orchestrator.

This module defines all custom exceptions used throughout the orchestrator package.
"""

class OrchestratorError(Exception):
    """Base exception for all Orchestrator-related errors."""
    pass


class SimulationError(OrchestratorError):
    """Raised when there's an error during simulation execution."""
    pass


class ConfigurationError(OrchestratorError):
    """Raised when there's an error in the Orchestrator configuration."""
    pass


class ResourceError(OrchestratorError):
    """Raised when there's an error related to resource management."""
    pass


class StateTransitionError(OrchestratorError):
    """Raised when an invalid state transition is attempted."""
    pass


class ValidationError(OrchestratorError):
    """Raised when input validation fails."""
    pass


class ExportError(OrchestratorError):
    """Raised when there's an error exporting simulation results."""
    pass


class ModelBuildError(OrchestratorError):
    """Raised when there's an error building the simulation model."""
    pass


class WorkloadError(OrchestratorError):
    """Raised when there's an error configuring workloads."""
    pass
