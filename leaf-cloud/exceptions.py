"""
Custom exceptions for the LEAF-Cloud framework.
"""

class LeafCloudError(Exception):
    """Base class for all LEAF-Cloud specific errors."""
    def __init__(self, message: str, original_exception: Exception = None):
        super().__init__(message)
        self.original_exception = original_exception
        self.message = message

    def __str__(self):
        if self.original_exception:
            return f"{self.message}: {self.original_exception}"
        return self.message

class ConfigError(LeafCloudError):
    """Error related to configuration loading or validation."""
    pass

class TerraformParseError(LeafCloudError):
    """Error during Terraform parsing."""
    pass

class ModelBuildError(LeafCloudError):
    """Error during simulation model construction."""
    pass

class SimulationError(LeafCloudError):
    """Error during a simulation run."""
    pass

class WorkloadError(LeafCloudError):
    """Error related to workload definition or processing."""
    pass

class MetricsError(LeafCloudError):
    """Error occurring in a metric calculation model."""
    pass

class ExportError(LeafCloudError):
    """Error during data or results export."""
    pass

class DiagramError(LeafCloudError):
    """Error during diagram generation."""
    pass

class FileOperationError(LeafCloudError):
    """Error related to file operations (read, write, access)."""
    pass

class ExternalToolError(LeafCloudError):
    """Error when interacting with an external tool (e.g., Terraform CLI)."""
    pass

class OrchestratorError(LeafCloudError):
    """Error specific to the Orchestrator's operations."""
    pass
