from typing import Dict, List, Optional, Tuple, Any, Union, Set, Callable
from dataclasses import dataclass, field
from datetime import datetime
import logging
from enum import Enum
import json
from ..core.resource import Resource, ResourceState
from ..core.workload import WorkloadStatistics

"""
models/constraints.py

This module defines resource and operational constraints for the LEAF-Cloud framework.
It provides mechanisms to validate constraints, model service limits and quotas,
and handle constraint violations during simulation.
"""

logger = logging.getLogger(__name__)

class ConstraintSeverity(Enum):
    """Severity levels for constraint violations."""
    INFO = 0      # Informational only, no action needed
    WARNING = 1   # Warning, may need attention but simulation continues
    ERROR = 2     # Error, requires attention, simulation may be affected
    CRITICAL = 3  # Critical, simulation results may be invalid
    FATAL = 4     # Fatal, simulation cannot continue


class ConstraintType(Enum):
    """Types of constraints that can be applied."""
    RESOURCE_LIMIT = 0       # Maximum number of resources (e.g., VM instances)
    QUOTA_LIMIT = 1          # GCP quota limits (e.g., API requests/minute)
    CAPACITY_LIMIT = 2       # Resource capacity (e.g., max CPU utilization)
    DEPENDENCY = 3           # Resource dependency constraints
    ALLOCATION = 4           # Resource allocation constraints
    REGIONAL = 5             # Regional or zonal constraints
    NETWORK = 6              # Network-related constraints
    SECURITY = 7             # Security-related constraints
    OPERATIONAL = 8          # Operational constraints
    CUSTOM = 9               # Custom-defined constraints


@dataclass
class Constraint:
    """Defines a constraint on a resource or operation."""
    name: str
    description: str
    constraint_type: ConstraintType
    resource_type: str
    severity: ConstraintSeverity = ConstraintSeverity.ERROR
    limit_value: Optional[Any] = None
    limit_function: Optional[Callable] = None
    region: Optional[str] = None
    zone: Optional[str] = None
    project: Optional[str] = None
    service: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    
    def check_violation(self, resource: Resource, value: Any) -> bool:
        """
        Check if the constraint is violated for the given resource and value.
        
        Args:
            resource: The resource to check
            value: The value to check against the constraint
            
        Returns:
            True if the constraint is violated, False otherwise
        """
        if self.limit_function is not None:
            return self.limit_function(resource, value, self)
        
        if self.limit_value is not None:
            if isinstance(value, (int, float)) and isinstance(self.limit_value, (int, float)):
                return value > self.limit_value
            return value == self.limit_value  # For non-numeric types, exact match is required
        
        return False  # Default if no limit is specified
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the constraint to a dictionary."""
        result = {
            "name": self.name,
            "description": self.description,
            "constraint_type": self.constraint_type.name,
            "resource_type": self.resource_type,
            "severity": self.severity.name
        }
        
        if self.limit_value is not None:
            result["limit_value"] = self.limit_value
        
        if self.limit_function is not None:
            result["has_limit_function"] = True
        
        if self.region:
            result["region"] = self.region
            
        if self.zone:
            result["zone"] = self.zone
            
        if self.project:
            result["project"] = self.project
            
        if self.service:
            result["service"] = self.service
            
        if self.attributes:
            result["attributes"] = self.attributes
            
        return result


@dataclass
class ConstraintViolation:
    """Records a violation of a constraint."""
    constraint: Constraint
    resource: Resource
    value: Any
    expected_value: Any
    timestamp: datetime = field(default_factory=datetime.now)
    details: str = ""
    handled: bool = False
    handling_action: Optional[str] = None
    
    def get_severity(self) -> ConstraintSeverity:
        """Get the severity of the constraint violation."""
        return self.constraint.severity
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the constraint violation to a dictionary."""
        return {
            "constraint": self.constraint.to_dict(),
            "resource_id": self.resource.id,
            "resource_name": self.resource.name,
            "resource_type": self.resource.resource_type,
            "value": self.value,
            "expected_value": self.expected_value,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details,
            "severity": self.constraint.severity.name,
            "handled": self.handled,
            "handling_action": self.handling_action
        }


class ConstraintManager:
    """
    Manages constraints and violations during simulation.
    Provides methods for adding constraints, validating resources against constraints,
    and handling violations.
    """
    
    def __init__(self):
        """Initialize the constraint manager."""
        self.constraints: List[Constraint] = []
        self.violations: List[ConstraintViolation] = []
        self.violation_handlers: Dict[ConstraintSeverity, Callable] = {}
        
        # Set default handlers
        self._set_default_handlers()
        
    def _set_default_handlers(self):
        """Set default handlers for constraint violations based on severity."""
        self.violation_handlers[ConstraintSeverity.INFO] = self._handle_info
        self.violation_handlers[ConstraintSeverity.WARNING] = self._handle_warning
        self.violation_handlers[ConstraintSeverity.ERROR] = self._handle_error
        self.violation_handlers[ConstraintSeverity.CRITICAL] = self._handle_critical
        self.violation_handlers[ConstraintSeverity.FATAL] = self._handle_fatal
        
    def add_constraint(self, constraint: Constraint) -> None:
        """
        Add a constraint to the manager.
        
        Args:
            constraint: The constraint to add
        """
        self.constraints.append(constraint)
        logger.info(f"Added constraint: {constraint.name} of type {constraint.constraint_type.name}")
        
    def add_constraints(self, constraints: List[Constraint]) -> None:
        """
        Add multiple constraints to the manager.
        
        Args:
            constraints: List of constraints to add
        """
        for constraint in constraints:
            self.add_constraint(constraint)
            
    def remove_constraint(self, constraint_name: str) -> bool:
        """
        Remove a constraint from the manager by name.
        
        Args:
            constraint_name: Name of the constraint to remove
            
        Returns:
            True if the constraint was removed, False if it wasn't found
        """
        initial_count = len(self.constraints)
        self.constraints = [c for c in self.constraints if c.name != constraint_name]
        
        if len(self.constraints) < initial_count:
            logger.info(f"Removed constraint: {constraint_name}")
            return True
        
        logger.warning(f"Constraint not found: {constraint_name}")
        return False
    
    def validate_resource(self, resource: Resource, context: Dict[str, Any] = None) -> List[ConstraintViolation]:
        """
        Validate a resource against all applicable constraints.
        
        Args:
            resource: The resource to validate
            context: Additional context information for validation
            
        Returns:
            List of constraint violations found
        """
        if context is None:
            context = {}
            
        violations = []
        
        for constraint in self.constraints:
            # Skip constraints that don't apply to this resource type
            if constraint.resource_type != resource.resource_type and constraint.resource_type != "*":
                continue
                
            # Skip constraints for different regions/zones if specified
            if constraint.region and resource.region != constraint.region:
                continue
                
            if constraint.zone and resource.zone != constraint.zone:
                continue
            
            # Get the value to check against the constraint
            value = self._get_resource_value(resource, constraint, context)
            
            # Check if the constraint is violated
            if constraint.check_violation(resource, value):
                violation = ConstraintViolation(
                    constraint=constraint,
                    resource=resource,
                    value=value,
                    expected_value=constraint.limit_value,
                    details=f"Resource {resource.name} violates {constraint.name}"
                )
                
                violations.append(violation)
                self.violations.append(violation)
                
                # Handle the violation
                self._handle_violation(violation)
                
        return violations
    
    def _get_resource_value(self, resource: Resource, constraint: Constraint, context: Dict[str, Any]) -> Any:
        """
        Get the resource value to check against the constraint.
        
        Args:
            resource: The resource to get the value from
            constraint: The constraint being checked
            context: Additional context information
            
        Returns:
            The value to check against the constraint
        """
        # First check constraint attributes for a specific property to check
        if "property" in constraint.attributes:
            prop_name = constraint.attributes["property"]
            if hasattr(resource, prop_name):
                return getattr(resource, prop_name)
        
        # Check common properties based on constraint type
        if constraint.constraint_type == ConstraintType.CAPACITY_LIMIT:
            return resource.current_utilization
        elif constraint.constraint_type == ConstraintType.RESOURCE_LIMIT:
            return 1  # For counting resources
        elif constraint.constraint_type == ConstraintType.QUOTA_LIMIT:
            if "quota_metric" in constraint.attributes:
                quota_metric = constraint.attributes["quota_metric"]
                if quota_metric in context:
                    return context[quota_metric]
                    
        # Default fallback
        return None
    
    def _handle_violation(self, violation: ConstraintViolation) -> None:
        """
        Handle a constraint violation based on its severity.
        
        Args:
            violation: The constraint violation to handle
        """
        severity = violation.get_severity()
        
        if severity in self.violation_handlers:
            self.violation_handlers[severity](violation)
        else:
            # Default handler
            logger.warning(f"Unhandled constraint violation: {violation.constraint.name}")
            
    def _handle_info(self, violation: ConstraintViolation) -> None:
        """Handle INFO severity constraint violations."""
        logger.info(f"Constraint INFO: {violation.details}")
        violation.handled = True
        violation.handling_action = "logged_info"
        
    def _handle_warning(self, violation: ConstraintViolation) -> None:
        """Handle WARNING severity constraint violations."""
        logger.warning(f"Constraint WARNING: {violation.details}")
        violation.handled = True
        violation.handling_action = "logged_warning"
        
    def _handle_error(self, violation: ConstraintViolation) -> None:
        """Handle ERROR severity constraint violations."""
        logger.error(f"Constraint ERROR: {violation.details}")
        violation.handled = True
        violation.handling_action = "logged_error"
        
    def _handle_critical(self, violation: ConstraintViolation) -> None:
        """Handle CRITICAL severity constraint violations."""
        logger.critical(f"Constraint CRITICAL: {violation.details}")
        violation.handled = True
        violation.handling_action = "logged_critical"
        
    def _handle_fatal(self, violation: ConstraintViolation) -> None:
        """Handle FATAL severity constraint violations."""
        error_msg = f"Constraint FATAL: {violation.details}"
        logger.critical(error_msg)
        violation.handled = True
        violation.handling_action = "simulation_halted"
        raise ConstraintFatalException(error_msg)
    
    def set_custom_handler(self, severity: ConstraintSeverity, handler: Callable) -> None:
        """
        Set a custom handler for constraint violations of a specific severity.
        
        Args:
            severity: The severity level to handle
            handler: The handler function, which should take a ConstraintViolation as argument
        """
        self.violation_handlers[severity] = handler
        
    def get_violations(self, severity: Optional[ConstraintSeverity] = None) -> List[ConstraintViolation]:
        """
        Get all violations, optionally filtered by severity.
        
        Args:
            severity: Optional severity filter
            
        Returns:
            List of matching constraint violations
        """
        if severity is None:
            return self.violations
        
        return [v for v in self.violations if v.get_severity() == severity]
    
    def get_violation_summary(self) -> Dict[str, int]:
        """
        Get a summary of violations by severity.
        
        Returns:
            Dictionary mapping severity names to violation counts
        """
        summary = {severity.name: 0 for severity in ConstraintSeverity}
        
        for violation in self.violations:
            severity_name = violation.get_severity().name
            summary[severity_name] += 1
            
        return summary
    
    def export_violations_to_json(self, filepath: str) -> None:
        """
        Export all violations to a JSON file.
        
        Args:
            filepath: Path to the output JSON file
        """
        violations_dict = [v.to_dict() for v in self.violations]
        
        with open(filepath, 'w') as f:
            json.dump(violations_dict, f, indent=2)
            
        logger.info(f"Exported {len(violations_dict)} constraint violations to {filepath}")
    
    def clear_violations(self) -> None:
        """Clear all recorded violations."""
        self.violations = []


class ServiceQuotaDefinitions:
    """
    Defines common GCP service quotas and limits.
    These can be used to create constraints for validation.
    """
    
    @staticmethod
    def get_compute_quotas() -> Dict[str, Any]:
        """Get Compute Engine quota definitions."""
        return {
            "cpus_per_region": 500,
            "in_use_addresses": 8,
            "instances_per_region": 2000,
            "gpus_per_region": 8,
            "ssd_total_gb": 500,
            "pd_standard_total_gb": 10000,
            "local_ssd_total_gb": 6000,
            "network_egress_bandwidth_gbps": 20,
            "api_requests_per_100s": 2000,
        }
    
    @staticmethod
    def get_gke_quotas() -> Dict[str, Any]:
        """Get Google Kubernetes Engine quota definitions."""
        return {
            "clusters_per_project": 100,
            "nodes_per_zone": 1000,
            "pods_per_node": 110,
            "persistent_disks_per_node": 16,
            "apis_per_100s": 1000,
        }
    
    @staticmethod
    def get_storage_quotas() -> Dict[str, Any]:
        """Get Cloud Storage quota definitions."""
        return {
            "buckets_per_project": 1000,
            "bucket_acls_per_bucket": 100,
            "default_object_acls_per_bucket": 100,
            "storage_class_ops_per_60s": 1200,
            "get_bucket_ops_per_60s": 800,
            "object_delete_ops_per_60s": 10000,
            "object_list_ops_per_60s": 5000,
        }
    
    @staticmethod
    def get_network_quotas() -> Dict[str, Any]:
        """Get Networking quota definitions."""
        return {
            "firewalls_per_network": 500,
            "networks_per_project": 15,
            "routes_per_vpc_network": 250,
            "static_routes_per_vpc_network": 100,
            "subnets_per_vpc_network_per_region": 200,
            "forwarding_rules_per_region": 150,
        }


class ConstraintFatalException(Exception):
    """Exception raised when a fatal constraint violation occurs."""
    pass


def create_standard_constraints() -> List[Constraint]:
    """
    Create a standard set of constraints based on GCP service quotas.
    
    Returns:
        List of standard constraints
    """
    constraints = []
    
    # Add Compute Engine constraints
    compute_quotas = ServiceQuotaDefinitions.get_compute_quotas()
    for name, limit in compute_quotas.items():
        constraints.append(
            Constraint(
                name=f"compute_{name}_quota",
                description=f"Compute Engine quota limit for {name}",
                constraint_type=ConstraintType.QUOTA_LIMIT,
                resource_type="compute",
                severity=ConstraintSeverity.ERROR,
                limit_value=limit,
                attributes={"quota_metric": name}
            )
        )
    
    # Add GKE constraints
    gke_quotas = ServiceQuotaDefinitions.get_gke_quotas()
    for name, limit in gke_quotas.items():
        constraints.append(
            Constraint(
                name=f"gke_{name}_quota",
                description=f"GKE quota limit for {name}",
                constraint_type=ConstraintType.QUOTA_LIMIT,
                resource_type="gke",
                severity=ConstraintSeverity.ERROR,
                limit_value=limit,
                attributes={"quota_metric": name}
            )
        )
    
    # Add common capacity constraints
    constraints.append(
        Constraint(
            name="cpu_utilization_limit",
            description="CPU utilization should not exceed 80%",
            constraint_type=ConstraintType.CAPACITY_LIMIT,
            resource_type="compute",
            severity=ConstraintSeverity.WARNING,
            limit_value=0.8,
            attributes={"property": "cpu_utilization"}
        )
    )
    
    constraints.append(
        Constraint(
            name="memory_utilization_limit",
            description="Memory utilization should not exceed 80%",
            constraint_type=ConstraintType.CAPACITY_LIMIT,
            resource_type="compute",
            severity=ConstraintSeverity.WARNING,
            limit_value=0.8,
            attributes={"property": "memory_utilization"}
        )
    )
    
    constraints.append(
        Constraint(
            name="disk_utilization_limit",
            description="Disk utilization should not exceed 90%",
            constraint_type=ConstraintType.CAPACITY_LIMIT,
            resource_type="storage",
            severity=ConstraintSeverity.WARNING,
            limit_value=0.9,
            attributes={"property": "disk_utilization"}
        )
    )
    
    return constraints


def validate_workload_against_constraints(
    workload_stats: WorkloadStatistics,
    resource_map: Dict[str, Resource],
    constraint_manager: ConstraintManager
) -> Dict[str, List[ConstraintViolation]]:
    """
    Validate a workload against constraints for all associated resources.
    
    Args:
        workload_stats: Statistics about the workload
        resource_map: Dictionary mapping resource IDs to Resource objects
        constraint_manager: The constraint manager to use for validation
        
    Returns:
        Dictionary mapping resource IDs to lists of constraint violations
    """
    violations = {}
    
    # Create context with workload statistics for constraint validation
    context = {
        "request_rate": workload_stats.average_request_rate,
        "token_count": workload_stats.total_tokens,
        "peak_rate": workload_stats.peak_request_rate,
        "workload_name": workload_stats.workload_name,
    }
    
    # Validate each resource
    for resource_id, resource in resource_map.items():
        resource_violations = constraint_manager.validate_resource(resource, context)
        if resource_violations:
            violations[resource_id] = resource_violations
            
    return violations


def handle_constraint_violations_during_simulation(
    violations: Dict[str, List[ConstraintViolation]],
    resources: Dict[str, Resource]
) -> Dict[str, Any]:
    """
    Handle constraint violations during simulation by taking appropriate actions.
    
    Args:
        violations: Dictionary mapping resource IDs to lists of constraint violations
        resources: Dictionary mapping resource IDs to Resource objects
        
    Returns:
        Dictionary containing information about remediation actions taken
    """
    actions_taken = {}
    
    for resource_id, resource_violations in violations.items():
        if resource_id not in resources:
            logger.warning(f"Resource ID {resource_id} not found in resources map")
            continue
            
        resource = resources[resource_id]
        resource_actions = []
        
        for violation in resource_violations:
            # Only handle unhandled violations
            if not violation.handled:
                action = _determine_remediation_action(violation, resource)
                if action and action["type"] != "none":
                    _apply_remediation_action(action, resource)
                    violation.handled = True
                    violation.handling_action = action["type"]
                    resource_actions.append(action)
        
        if resource_actions:
            actions_taken[resource_id] = resource_actions
            
    return actions_taken


def _determine_remediation_action(violation: ConstraintViolation, resource: Resource) -> Dict[str, Any]:
    """
    Determine what remediation action to take for a constraint violation.
    
    Args:
        violation: The constraint violation
        resource: The affected resource
        
    Returns:
        Dictionary describing the remediation action
    """
    constraint_type = violation.constraint.constraint_type
    severity = violation.get_severity()
    
    # No action for INFO severity
    if severity == ConstraintSeverity.INFO:
        return {"type": "none"}
    
    # For capacity constraints, try scaling resources
    if constraint_type == ConstraintType.CAPACITY_LIMIT:
        if severity in [ConstraintSeverity.WARNING, ConstraintSeverity.ERROR]:
            return {
                "type": "scale_resource",
                "resource_id": resource.id,
                "details": f"Scale resource to address {violation.constraint.name}"
            }
    
    # For quota violations, we can't add more resources
    if constraint_type == ConstraintType.QUOTA_LIMIT:
        if severity in [ConstraintSeverity.ERROR, ConstraintSeverity.CRITICAL]:
            return {
                "type": "throttle_workload",
                "resource_id": resource.id,
                "details": f"Throttle workload to comply with {violation.constraint.name}"
            }
    
    # For fatal violations, we need to abort
    if severity == ConstraintSeverity.FATAL:
        return {
            "type": "abort_simulation",
            "resource_id": resource.id,
            "details": f"Fatal violation of {violation.constraint.name}"
        }
    
    # Default action
    return {
        "type": "log_only",
        "resource_id": resource.id,
        "details": f"Log violation of {violation.constraint.name}"
    }


def _apply_remediation_action(action: Dict[str, Any], resource: Resource) -> None:
    """
    Apply a remediation action to a resource.
    
    Args:
        action: The remediation action to apply
        resource: The resource to apply the action to
    """
    action_type = action["type"]
    
    if action_type == "scale_resource":
        # Example: Increase capacity by 20%
        if hasattr(resource, "capacity"):
            resource.capacity *= 1.2
            logger.info(f"Increased capacity of {resource.name} by 20%")
    
    elif action_type == "throttle_workload":
        # Example: Set a throttling flag on the resource
        resource.is_throttled = True
        logger.info(f"Enabled throttling for {resource.name}")
    
    elif action_type == "abort_simulation":
        # Let the calling code handle this by raising an exception
        logger.critical(f"Simulation abort required due to constraint violation on {resource.name}")
        raise ConstraintFatalException(f"Fatal constraint violation on {resource.name}")
    
    elif action_type == "log_only":
        # No action needed, just log
        logger.info(f"Logged constraint violation on {resource.name}")