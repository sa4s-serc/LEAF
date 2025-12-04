"""Custom exceptions for the LEAF-Cloud framework.

This module defines a hierarchy of exceptions specific to the LEAF-Cloud framework,
providing more detailed error information and better error handling capabilities.
"""

from typing import Optional, Type, TypeVar, Any, Dict

# Type variable for exception chaining
_ExceptionType = TypeVar('_ExceptionType', bound=Exception)


class LeafCloudError(Exception):
    """Base class for all LEAF-Cloud specific errors.
    
    This exception serves as the base class for all custom exceptions in the
    LEAF-Cloud framework. It extends the standard Exception class with support
    for chaining exceptions and providing more detailed error messages.
    
    Args:
        message: A human-readable error message describing the issue.
        original_exception: The original exception that caused this error, if any.
        details: Additional details about the error as a dictionary.
    """
    
    def __init__(
        self, 
        message: str, 
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Initialize the exception with a message and optional original exception."""
        super().__init__(message)
        self.message = message
        self.original_exception = original_exception
        self.details = details or {}
        
        # Store the original exception's traceback if available
        if original_exception is not None:
            self.__cause__ = original_exception
    
    def __str__(self) -> str:
        """Return a string representation of the exception.
        
        Returns:
            A string containing the error message and optionally the original exception.
        """
        if self.original_exception:
            return f"{self.message} (caused by: {self.original_exception})"
        return self.message
    
    def with_details(self, **kwargs: Any) -> 'LeafCloudError':
        """Add additional details to the exception.
        
        Args:
            **kwargs: Key-value pairs to add as details.
            
        Returns:
            The exception instance (for method chaining).
        """
        self.details.update(kwargs)
        return self
    
    @classmethod
    def from_exception(
        cls: Type['_ExceptionType'], 
        exc: Exception, 
        message: Optional[str] = None
    ) -> '_ExceptionType':
        """Create a new exception from an existing one.
        
        Args:
            exc: The original exception to wrap.
            message: Optional custom message. If not provided, uses str(exc).
            
        Returns:
            A new instance of the exception class.
        """
        return cls(message or str(exc), original_exception=exc)


class ConfigError(LeafCloudError):
    """Error related to configuration loading or validation.
    
    This exception is raised when there is an issue with loading, parsing,
    or validating configuration data for the LEAF-Cloud framework.
    """
    pass


class TerraformParseError(LeafCloudError):
    """Error during Terraform parsing.
    
    This exception is raised when there is an issue parsing Terraform
    configuration files or state data.
    """
    pass


class ModelBuildError(LeafCloudError):
    """Error during simulation model construction.
    
    This exception is raised when there is an issue building the simulation
    model from the provided configuration and resources.
    """
    pass


class SimulationError(LeafCloudError):
    """Error during a simulation run.
    
    This exception is raised when an error occurs during the execution
    of a simulation.
    """
    pass


class WorkloadError(LeafCloudError):
    """Error related to workload definition or processing.
    
    This exception is raised when there is an issue with defining or
    processing workload data for the simulation.
    """
    pass


class MetricsError(LeafCloudError):
    """Error occurring in a metric calculation model.
    
    This exception is raised when there is an issue calculating or
    processing metrics during or after a simulation.
    """
    pass


class ExportError(LeafCloudError):
    """Error during data or results export.
    
    This exception is raised when there is an issue exporting simulation
    results or other data to various output formats.
    """
    pass


class DiagramError(LeafCloudError):
    """Error during diagram generation.
    
    This exception is raised when there is an issue generating diagrams
    or visualizations from the simulation data.
    """
    pass


class FileOperationError(LeafCloudError):
    """Error related to file operations (read, write, access).
    
    This exception is raised when there is an issue performing file I/O
    operations, such as reading or writing configuration files, logs, or
    simulation results.
    """
    pass


class ExternalToolError(LeafCloudError):
    """Error when interacting with an external tool (e.g., Terraform CLI).
    
    This exception is raised when there is an issue executing or communicating
    with an external tool or command-line utility.
    """
    pass


class OrchestratorError(LeafCloudError):
    """Error specific to the Orchestrator's operations.
    
    This exception is raised when there is an issue with the orchestration
    of the simulation workflow or resource management.
    """
    pass


class ResourceError(LeafCloudError):
    """Error related to resource modeling, creation, or access.
    
    This exception is raised when there is an issue with cloud resource
    modeling, provisioning, or management.
    """
    pass


class ValidationError(LeafCloudError):
    """Error related to data validation.
    
    This exception is raised when there is an issue with validating
    input data, configuration, or other values against expected formats
    or constraints.
    """
    pass


class ModelExportError(LeafCloudError):
    """Error during model export operations.
    
    This exception is raised when there is an issue exporting simulation
    models to various formats or representations.
    """
    pass
