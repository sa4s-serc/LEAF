from typing import Dict, List, Optional, Any, Callable, Union
from dataclasses import dataclass, field
import logging
from enum import Enum
from datetime import datetime
from leaf_cloud.core.resource import Resource, ResourceType

"""
models/constraints.py

This module defines resource and operational constraints for the LEAF-Cloud framework.
It provides mechanisms to validate constraints, model service limits and quotas,
and handle constraint violations during simulation.
"""

logger = logging.getLogger(__name__)


class ConstraintSeverity(Enum):
    """Severity levels for constraint violations."""

    INFO = 0  # Informational only, no action needed
    WARNING = 1  # Warning, may need attention but simulation continues
    ERROR = 2  # Error, requires attention, simulation may be affected
    CRITICAL = 3  # Critical, simulation results may be invalid
    FATAL = 4  # Fatal, simulation cannot continue


class ConstraintType(Enum):
    """Types of constraints that can be applied."""

    RESOURCE_LIMIT = 0  # Maximum number of resources (e.g., VM instances)
    QUOTA_LIMIT = 1  # GCP quota limits (e.g., API requests/minute)
    CAPACITY_LIMIT = 2  # Resource capacity (e.g., max CPU utilization)
    DEPENDENCY = 3  # Resource dependency constraints
    ALLOCATION = 4  # Resource allocation constraints
    REGIONAL = 5  # Regional or zonal constraints
    NETWORK = 6  # Network-related constraints
    SECURITY = 7  # Security-related constraints
    OPERATIONAL = 8  # Operational constraints
    CUSTOM = 9  # Custom-defined constraints


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
        logger.debug("\n=== CHECK_VIOLATION ===")
        logger.debug(f"Constraint: {self.name}")
        logger.debug(f"Constraint type: {self.constraint_type}")
        logger.debug(f"Resource: {resource.name if hasattr(resource, 'name') else 'unnamed'}")
        logger.debug(f"Value to check: {value} (type: {type(value).__name__})")
        logger.debug(f"Limit value: {self.limit_value} (type: {type(self.limit_value).__name__ if self.limit_value is not None else 'None'})")
        logger.debug(f"Limit function: {'Yes' if self.limit_function else 'No'}")
        
        # Log all attributes of the resource for debugging
        logger.debug("Resource attributes:")
        for attr in dir(resource):
            if not attr.startswith('_') and not callable(getattr(resource, attr)):
                logger.debug(f"  {attr}: {getattr(resource, attr, 'N/A')} (type: {type(getattr(resource, attr, 'N/A')).__name__})")
        
        # If we have a limit function, use that
        if self.limit_function is not None:
            logger.debug("Using limit function to check violation")
            try:
                result = self.limit_function(resource, value, self)
                logger.debug(f"Limit function returned: {result}")
                return result
            except Exception as e:
                logger.error(f"Error in limit function: {e}", exc_info=True)
                return False
                
        # Otherwise, use the limit value
        if self.limit_value is not None:
            logger.debug("Using limit value to check violation")
            try:
                # Try to convert both to float for numeric comparison
                value_float = float(value) if value is not None else 0.0
                limit_float = float(self.limit_value) if self.limit_value is not None else 0.0
                
                logger.debug(f"Comparing value {value_float} (type: {type(value_float).__name__}) "
                           f"with limit {limit_float} (type: {type(limit_float).__name__})")
                
                # For QUOTA_LIMIT, violation occurs when value > limit
                if self.constraint_type == ConstraintType.QUOTA_LIMIT:
                    if value_float > limit_float:
                        logger.debug(f"VIOLATION DETECTED: {value_float} > {limit_float} (QUOTA_LIMIT)")
                        return True
                    else:
                        logger.debug(f"No violation: {value_float} <= {limit_float} (QUOTA_LIMIT)")
                        return False
                # For other constraint types, use the default comparison
                else:
                    if value_float > limit_float:
                        logger.debug(f"VIOLATION DETECTED: {value_float} > {limit_float} (DEFAULT)")
                        return True
                    else:
                        logger.debug(f"No violation: {value_float} <= {limit_float} (DEFAULT)")
                        return False
            except (ValueError, TypeError) as e:
                logger.error(f"  Error comparing values: {e}", exc_info=True)
                logger.error(f"  Value: {value} (type: {type(value).__name__})")
                logger.error(f"  Limit: {self.limit_value} (type: {type(self.limit_value).__name__})")
                return False
                
        logger.debug("No limit value or function set, returning False")
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Convert the constraint to a dictionary."""
        result = {
            "name": self.name,
            "description": self.description,
            "constraint_type": self.constraint_type.name,
            "resource_type": self.resource_type,
            "severity": self.severity.name,
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
            "handling_action": self.handling_action,
        }


def _resource_type_matches_constraint(resource: Resource, constraint: Constraint) -> bool:
    """Unified resource-type matching logic for constraints.
    
    Supports enums and strings and tolerates dotted or namespaced representations.
    Returns True when the constraint has no resource_type.
    """
    # If constraint has no specific resource type, it applies to all
    if constraint.resource_type is None:
        return True

    # Normalize constraint type to string
    if hasattr(constraint.resource_type, 'value'):
        ctype = str(constraint.resource_type.value)
    else:
        ctype = str(constraint.resource_type)
    # Normalize resource type to string
    if hasattr(resource.resource_type, 'value'):
        rtype = str(resource.resource_type.value)
    else:
        rtype = str(resource.resource_type)

    ctype = ctype.lower().strip()
    rtype = rtype.lower().strip()

    if ctype == rtype:
        return True

    # Allow loose matches to cope with namespaced strings
    return (
        ctype in rtype or rtype in ctype or
        rtype.endswith(f'.{ctype}') or ctype.endswith(f'.{rtype}')
    )


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

    def _handle_info(self, violation: ConstraintViolation, resource: Resource) -> None:
        """Handle INFO level constraint violations by logging them.
        
        Args:
            violation: The constraint violation that occurred
            resource: The resource that violated the constraint
        """
        logger.info(
            "Info: Constraint '%s' violation on resource %s: %s (value: %s, expected: %s)",
            violation.constraint.name,
            resource.id,
            violation.details,
            violation.value,
            violation.expected_value
        )
        violation.handled = True
        violation.handling_action = "logged"
    
    def _handle_warning(self, violation: ConstraintViolation, resource: Resource) -> None:
        """Handle WARNING level constraint violations by logging them as warnings.
        
        Args:
            violation: The constraint violation that occurred
            resource: The resource that violated the constraint
        """
        logger.warning(
            "Warning: Constraint '%s' violation on resource %s: %s (value: %s, expected: %s)",
            violation.constraint.name,
            resource.id,
            violation.details,
            violation.value,
            violation.expected_value
        )
        violation.handled = True
        violation.handling_action = "logged_warning"
    
    def _handle_error(self, violation: ConstraintViolation, resource: Resource) -> None:
        """Handle ERROR level constraint violations by logging them as errors.
        
        Args:
            violation: The constraint violation that occurred
            resource: The resource that violated the constraint
        """
        logger.error(
            "Error: Constraint '%s' violation on resource %s: %s (value: %s, expected: %s)",
            violation.constraint.name,
            resource.id,
            violation.details,
            violation.value,
            violation.expected_value
        )
        violation.handled = True
        violation.handling_action = "logged_error"
    
    def _handle_critical(self, violation: ConstraintViolation, resource: Resource) -> None:
        """Handle CRITICAL level constraint violations by logging them as critical errors.
        
        Args:
            violation: The constraint violation that occurred
            resource: The resource that violated the constraint
        """
        logger.critical(
            "CRITICAL: Constraint '%s' violation on resource %s: %s (value: %s, expected: %s)",
            violation.constraint.name,
            resource.id,
            violation.details,
            violation.value,
            violation.expected_value
        )
        violation.handled = True
        violation.handling_action = "logged_critical"
    
    def _handle_fatal(self, violation: ConstraintViolation, resource: Resource) -> None:
        """Handle FATAL level constraint violations by raising an exception.
        
        Args:
            violation: The constraint violation that occurred
            resource: The resource that violated the constraint
            
        Raises:
            ConstraintFatalException: Always raises this exception for fatal violations
        """
        error_msg = (
            f"FATAL: Constraint '{violation.constraint.name}' violation on resource {resource.id}: "
            f"{violation.details} (value: {violation.value}, expected: {violation.expected_value})"
        )
        logger.critical(error_msg)
        violation.handled = True
        violation.handling_action = "raised_exception"
        raise ConstraintFatalException(
            message=error_msg,
            constraint=violation.constraint,
            resource=resource,
            actual_value=violation.value
        )
    
    def _set_default_handlers(self):
        """Set default handlers for constraint violations based on severity."""
        self.violation_handlers = {
            ConstraintSeverity.INFO: self._handle_info,
            ConstraintSeverity.WARNING: self._handle_warning,
            ConstraintSeverity.ERROR: self._handle_error,
            ConstraintSeverity.CRITICAL: self._handle_critical,
            ConstraintSeverity.FATAL: self._handle_fatal
        }
        
    def get_violations(self, filters: Optional[Union[Dict[str, Any], ConstraintSeverity]] = None) -> List[ConstraintViolation]:
        """
        Get a list of constraint violations, optionally filtered.
        
        Args:
            filters: Optional filter specification, which can be:
                    - A ConstraintSeverity enum value to filter by severity
                    - A dictionary of filters with the following possible keys:
                        - severity: Filter by severity level (ConstraintSeverity enum)
                        - resource_id: Filter by resource ID
                        - constraint_name: Filter by constraint name
                        - handled: Filter by handled status
                
        Returns:
            List of matching ConstraintViolation objects
        """
        if filters is None:
            return self.violations
            
        # Handle case where filters is a ConstraintSeverity
        if isinstance(filters, ConstraintSeverity):
            return [v for v in self.violations if v.get_severity() == filters]
            
        # Handle case where filters is a dictionary
        filtered = self.violations
        
        if 'severity' in filters:
            filtered = [v for v in filtered if v.get_severity() == filters['severity']]
            
        if 'resource_id' in filters:
            filtered = [v for v in filtered if hasattr(v.resource, 'id') and v.resource.id == filters['resource_id']]
            
        if 'constraint_name' in filters:
            filtered = [v for v in filtered if v.constraint.name == filters['constraint_name']]
            
        if 'handled' in filters:
            filtered = [v for v in filtered if v.handled == filters['handled']]
            
        return filtered
        
    def get_violation_summary(self) -> Dict[str, Any]:
        """
        Get a summary of constraint violations by severity.
        
        Returns:
            Dictionary with violation counts by severity and other summary information
        """
        summary = {
            'total': len(self.violations),
            'by_severity': {},
            'by_constraint': {},
            'by_resource': {}
        }
        
        # Count by severity - include all severity levels, even if count is zero
        for severity in ConstraintSeverity:
            count = sum(1 for v in self.violations if v.get_severity() == severity)
            summary['by_severity'][severity.name] = count
        
        # Count by constraint
        for violation in self.violations:
            constraint_name = violation.constraint.name
            summary['by_constraint'][constraint_name] = summary['by_constraint'].get(constraint_name, 0) + 1
        
        # Count by resource
        for violation in self.violations:
            if hasattr(violation.resource, 'id'):
                resource_id = violation.resource.id
                summary['by_resource'][resource_id] = summary['by_resource'].get(resource_id, 0) + 1
        
        return summary

    def add_constraint(self, constraint: Constraint) -> None:
        """
        Add a constraint to the manager.

        Args:
            constraint: The constraint to add
        """
        self.constraints.append(constraint)
        logger.info(
            f"Added constraint: {constraint.name} of type {constraint.constraint_type.name}"
        )

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
        self.constraints = [
            c for c in self.constraints if c.name != constraint_name
        ]

        if len(self.constraints) < initial_count:
            logger.info(f"Removed constraint: {constraint_name}")
            return True

        logger.warning(f"Constraint not found: {constraint_name}")
        return False

    def validate_resource(
        self, resource: Resource, context: Dict[str, Any] = None
    ) -> List[ConstraintViolation]:
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

        # Add resource to context if not already present
        if 'resource' not in context:
            context['resource'] = resource

        violations = []
        
        logger.debug("\n=== VALIDATE_RESOURCE START ===")
        logger.debug(f"Resource ID: {getattr(resource, 'id', 'N/A')}")
        logger.debug(f"Resource name: {getattr(resource, 'name', 'unnamed')}")
        logger.debug(f"Resource type: {resource.resource_type} (type: {type(resource.resource_type).__name__})")
        
        # Log all attributes of the resource
        logger.debug("Resource attributes:")
        for attr in dir(resource):
            if not attr.startswith('_') and not callable(getattr(resource, attr)):
                logger.debug(f"  {attr}: {getattr(resource, attr, 'N/A')} (type: {type(getattr(resource, attr, 'N/A')).__name__})")

        # Log all constraints for debugging
        logger.debug("\nAll constraints in manager:")
        for i, c in enumerate(self.constraints, 1):
            logger.debug(f"  {i}. {c.name}")
            logger.debug(f"     Type: {c.constraint_type}")
            logger.debug(f"     Resource Type: {c.resource_type} (type: {type(c.resource_type).__name__})")
            logger.debug(f"     Limit: {c.limit_value} (type: {type(c.limit_value).__name__ if c.limit_value is not None else 'None'})")
            logger.debug(f"     Attributes: {c.attributes}")

        # Get all constraints that apply to this resource type
        applicable_constraints = []
        for c in self.constraints:
            logger.debug(f"\n=== CHECKING CONSTRAINT: {c.name} ===")
            type_matches = _resource_type_matches_constraint(resource, c)
            logger.debug(f"  Type match: {type_matches}")
            
            # For debugging: Check if the constraint has a metric/quota_metric that matches any attribute
            if hasattr(c, 'attributes') and isinstance(c.attributes, dict):
                metric_key = 'metric' if 'metric' in c.attributes else ('quota_metric' if 'quota_metric' in c.attributes else None)
                if metric_key:
                    metric = c.attributes[metric_key]
                    has_metric = hasattr(resource, metric) or (hasattr(resource, 'attributes') and metric in getattr(resource, 'attributes', {}))
                    logger.debug(f"  Metric '{metric}' (key: {metric_key}) found in resource: {has_metric}")
            
            if type_matches or c.resource_type is None:
                applicable_constraints.append(c)
                logger.debug("  -> APPLIES")
            else:
                logger.debug("  -> DOES NOT APPLY (type mismatch)")

        logger.debug(f"\n=== APPLICABLE CONSTRAINTS ===")
        logger.debug(f"Found {len(applicable_constraints)} applicable constraints for resource type {resource.resource_type}")
        for i, c in enumerate(applicable_constraints, 1):
            logger.debug(f"  {i}. {c.name} (type: {c.constraint_type.name}, limit: {c.limit_value})")
        for i, c in enumerate(applicable_constraints, 1):
            logger.debug(f"  {i}. {c.name} (type: {c.constraint_type}, limit: {c.limit_value})")

        for constraint in applicable_constraints:
            logger.debug(f"\nChecking constraint: {constraint.name}")
            logger.debug(f"  Constraint type: {constraint.constraint_type}")
            logger.debug(f"  Limit value: {constraint.limit_value}")
            logger.debug(f"  Resource type: {constraint.resource_type}")
            
            # Skip constraints that don't match the resource's region/zone/project if specified
            if (constraint.region and constraint.region != getattr(resource, 'region', None)) or \
               (constraint.zone and constraint.zone != getattr(resource, 'zone', None)) or \
               (constraint.project and constraint.project != getattr(resource, 'project', None)) or \
               (constraint.service and constraint.service != getattr(resource, 'service', None)):
                logger.debug("  Skipping due to region/zone/project/service mismatch")
                continue

            # Get the value to validate against
            # Get the value to check against the constraint
            logger.debug("\n=== CONSTRAINT VALIDATION DETAILS ===")
            logger.debug(f"Resource ID: {resource.id}")
            logger.debug(f"Resource name: {getattr(resource, 'name', 'unnamed')}")
            logger.debug(f"Resource type: {resource.resource_type} (type: {type(resource.resource_type).__name__})")
            logger.debug(f"Constraint name: {constraint.name}")
            logger.debug(f"Constraint type: {constraint.constraint_type}")
            logger.debug(f"Constraint resource type: {constraint.resource_type} (type: {type(constraint.resource_type).__name__})")
            logger.debug(f"Constraint limit: {constraint.limit_value} (type: {type(constraint.limit_value).__name__})")
            logger.debug(f"Constraint attributes: {constraint.attributes}")
            logger.debug(f"Context keys: {list(context.keys())}")
            logger.debug(f"Context value: {context.get('value')} (type: {type(context.get('value')).__name__})")
            
            # Get the value to check from the resource
            value = None
            # Get the attribute name from the constraint's attributes (support both 'metric' and legacy 'quota_metric')
            attr_to_check = None
            if constraint.attributes:
                if 'metric' in constraint.attributes:
                    attr_to_check = constraint.attributes.get('metric')
                elif 'quota_metric' in constraint.attributes:
                    attr_to_check = constraint.attributes.get('quota_metric')
            
            logger.debug(f"\n  === VALUE EXTRACTION FOR CONSTRAINT: {constraint.name} ===")
            logger.debug(f"  Constraint name: {constraint.name}")
            logger.debug(f"  Constraint type: {constraint.constraint_type}")
            logger.debug(f"  Constraint resource type: {constraint.resource_type} (type: {type(constraint.resource_type).__name__})")
            logger.debug(f"  Constraint limit: {constraint.limit_value} (type: {type(constraint.limit_value).__name__ if constraint.limit_value is not None else 'None'})")
            logger.debug(f"  Constraint attributes: {constraint.attributes}")
            
            logger.debug(f"\n  Resource info:")
            logger.debug(f"  Resource ID: {getattr(resource, 'id', 'N/A')}")
            logger.debug(f"  Resource name: {getattr(resource, 'name', 'unnamed')}")
            logger.debug(f"  Resource type: {resource.resource_type} (type: {type(resource.resource_type).__name__})")
            logger.debug(f"  Resource type string: {str(resource.resource_type)}")
            
            # Get the value to check from the resource or context
            value = None
            if attr_to_check:
                # First try to get the value from the resource
                if hasattr(resource, 'attributes') and isinstance(resource.attributes, dict) and attr_to_check in resource.attributes:
                    value = resource.attributes[attr_to_check]
                    logger.debug(f"  Found {attr_to_check} in resource attributes: {value} (type: {type(value).__name__})")
                # Then try to get it as a direct attribute
                elif hasattr(resource, attr_to_check):
                    value = getattr(resource, attr_to_check)
                    logger.debug(f"  Found direct attribute {attr_to_check}: {value} (type: {type(value).__name__})")
            
            # If still no value, try to get from context
            if value is None and context and 'value' in context:
                value = context['value']
                logger.debug(f"  Using value from context: {value} (type: {type(value).__name__})")
            
            # If we have a value, check the constraint
            if value is not None:
                logger.debug(f"  Attribute being checked: {attr_to_check}")
                
                # Check if the constraint is violated
                if constraint.check_violation(resource, value):
                    violation = ConstraintViolation(
                        constraint=constraint,
                        resource=resource,
                        value=value,
                        expected_value=constraint.limit_value,
                    )
                    violations.append(violation)
                    logger.warning(f"VIOLATION DETECTED: {value} > {constraint.limit_value}")
                    logger.warning(f"  Constraint: {constraint.name}")
                    logger.warning(f"  Resource: {resource.name if hasattr(resource, 'name') else 'unnamed'}")
                    logger.warning(f"  Value: {value}, Limit: {constraint.limit_value}")
                else:
                    logger.debug(f"No violation: {value} <= {constraint.limit_value}")
        
        return violations


class ConstraintFatalException(Exception):
    """
    Exception raised when a critical constraint violation occurs that should stop the simulation.
    
    This exception is used to indicate that a constraint violation is so severe that
    the simulation cannot continue. It typically represents a violation of a hard
    constraint that cannot be automatically resolved.
    
    Attributes:
        message: Explanation of the error
        constraint: The Constraint that was violated
        resource: The Resource that violated the constraint
        actual_value: The actual value that caused the violation
    """
    
    def __init__(
        self,
        message: str,
        constraint: Optional['Constraint'] = None,
        resource: Optional[Any] = None,
        actual_value: Optional[Any] = None
    ):
        self.message = message
        self.constraint = constraint
        self.resource = resource
        self.actual_value = actual_value
        super().__init__(self.message)
    
    def __str__(self) -> str:
        details = [self.message]
        if self.constraint:
            details.append(f"Constraint: {self.constraint.name}")
        if self.resource:
            details.append(f"Resource: {getattr(self.resource, 'name', str(self.resource))}")
        if self.actual_value is not None:
            details.append(f"Actual value: {self.actual_value}")
        return "; ".join(details)


class ServiceQuotaDefinitions:
    """
    Provides standard quota definitions for various cloud services.
    
    This class contains static methods that return dictionaries of quota definitions
    for different cloud services. Each quota definition includes the default limit
    and other relevant metadata.
    """
    
    @staticmethod
    def get_compute_quotas() -> Dict[str, Dict[str, Any]]:
        """
        Get Compute Engine quota definitions.
        
        Returns:
            Dictionary of Compute Engine quota definitions
        """
        return {
            "cpus_per_region": {
                "default": 24,
                "description": "Number of vCPUs allowed per region",
                "metric": "cpus",
                "unit": "count"
            },
            "instances_per_region": {
                "default": 8,
                "description": "Number of instances allowed per region",
                "metric": "instances",
                "unit": "count"
            },
            "in_use_addresses": {
                "default": 8,
                "description": "Number of static IP addresses that can be used",
                "metric": "ip_addresses",
                "unit": "count"
            },
            "ssd_total_gb": {
                "default": 100,
                "description": "Total SSD disk space (GB) allowed per region",
                "metric": "disk_size",
                "unit": "GB"
            },
            "local_ssd_gb": {
                "default": 3000,
                "description": "Total local SSD disk space (GB) allowed per region",
                "metric": "disk_size",
                "unit": "GB"
            }
        }
    
    @staticmethod
    def get_gke_quotas() -> Dict[str, Dict[str, Any]]:
        """
        Get GKE (Google Kubernetes Engine) quota definitions.
        
        Returns:
            Dictionary of GKE quota definitions
        """
        return {
            "clusters_per_project": {
                "default": 5,
                "description": "Number of GKE clusters allowed per project",
                "metric": "clusters",
                "unit": "count"
            },
            "nodes_per_zone": {
                "default": 100,
                "description": "Number of nodes allowed per zone in a cluster",
                "metric": "nodes",
                "unit": "count"
            },
            "pods_per_node": {
                "default": 110,
                "description": "Maximum number of pods per node",
                "metric": "pods",
                "unit": "count"
            },
            "node_pools_per_cluster": {
                "default": 3,
                "description": "Number of node pools allowed per cluster",
                "metric": "node_pools",
                "unit": "count"
            }
        }
    
    @staticmethod
    def get_storage_quotas() -> Dict[str, Dict[str, Any]]:
        """
        Get Cloud Storage quota definitions.
        
        Returns:
            Dictionary of Cloud Storage quota definitions
        """
        return {
            "buckets_per_project": {
                "default": 100,
                "description": "Number of buckets allowed per project",
                "metric": "buckets",
                "unit": "count"
            },
            "bucket_acls_per_bucket": {
                "default": 100,
                "description": "Number of ACL entries per bucket",
                "metric": "acls",
                "unit": "count"
            },
            "storage_tb": {
                "default": 5,
                "description": "Total storage capacity (TB) per project",
                "metric": "storage_size",
                "unit": "TB"
            },
            "upload_bandwidth_tb": {
                "default": 1.2,
                "description": "Upload bandwidth (TB/day) per project",
                "metric": "bandwidth",
                "unit": "TB/day"
            }
        }
    
    @staticmethod
    def get_network_quotas() -> Dict[str, Dict[str, Any]]:
        """
        Get Networking quota definitions.
        
        Returns:
            Dictionary of Networking quota definitions
        """
        return {
            "firewalls_per_network": {
                "default": 5,
                "description": "Number of firewall rules allowed per VPC network",
                "metric": "firewall_rules",
                "unit": "count"
            },
            "networks_per_project": {
                "default": 5,
                "description": "Number of VPC networks allowed per project",
                "metric": "networks",
                "unit": "count"
            },
            "routes_per_network": {
                "default": 100,
                "description": "Number of routes allowed per VPC network",
                "metric": "routes",
                "unit": "count"
            },
            "subnets_per_network": {
                "default": 20,
                "description": "Number of subnets allowed per VPC network",
                "metric": "subnets",
                "unit": "count"
            },
            "vpn_tunnels_per_region": {
                "default": 3,
                "description": "Number of VPN tunnels allowed per region",
                "metric": "vpn_tunnels",
                "unit": "count"
            }
        }


def create_standard_constraints() -> List[Constraint]:
    """
    Create a list of standard constraints for testing and simulation.
    
    Returns:
        List of standard Constraint objects
    """
    constraints = [
        # QUOTA_LIMIT constraints
        Constraint(
            name="cpu_quota",
            description="CPU quota limit per instance",
            constraint_type=ConstraintType.QUOTA_LIMIT,
            resource_type=ResourceType.COMPUTE,
            severity=ConstraintSeverity.ERROR,
            limit_value=10,  # 10 vCPUs
            attributes={"metric": "cpu", "unit": "count"}
        ),
        Constraint(
            name="memory_quota",
            description="Memory quota limit per instance",
            constraint_type=ConstraintType.QUOTA_LIMIT,
            resource_type=ResourceType.COMPUTE,
            severity=ConstraintSeverity.ERROR,
            limit_value=64,  # 64GB
            attributes={"metric": "memory", "unit": "GB"}
        ),
        Constraint(
            name="disk_quota",
            description="Disk quota limit per instance",
            constraint_type=ConstraintType.QUOTA_LIMIT,
            resource_type=ResourceType.STORAGE,
            severity=ConstraintSeverity.WARNING,
            limit_value=1000,  # 1000GB
            attributes={"metric": "disk_size", "unit": "GB"}
        ),
        
        # RESOURCE_LIMIT constraint
        Constraint(
            name="instance_quota",
            description="Maximum number of instances",
            constraint_type=ConstraintType.RESOURCE_LIMIT,
            resource_type=ResourceType.COMPUTE,
            severity=ConstraintSeverity.ERROR,
            limit_value=20,  # 20 instances
            attributes={"metric": "instance_count"}
        ),
        
        # CAPACITY_LIMIT constraint
        Constraint(
            name="max_capacity_per_zone",
            description="Maximum resource capacity per availability zone",
            constraint_type=ConstraintType.CAPACITY_LIMIT,
            resource_type=ResourceType.COMPUTE,
            severity=ConstraintSeverity.WARNING,
            limit_value=100,  # 100 units of capacity
            attributes={"metric": "capacity_units", "scope": "zone"}
        )
    ]
    
    return constraints


def validate_workload_against_constraints(
    workload_stats: Any,
    resource_map: Dict[str, Resource],
    constraint_manager: ConstraintManager,
) -> Dict[str, List[ConstraintViolation]]:
    """
    Validate a workload against all registered constraints.
    
    Args:
        workload_stats: Workload statistics to validate against
        resource_map: Dictionary mapping resource IDs to Resource objects
        constraint_manager: ConstraintManager containing constraints to validate against
        logger: Optional logger for debug output
        
    Returns:
        Dictionary mapping resource IDs to lists of constraint violations
    """
    
    violations: Dict[str, List[ConstraintViolation]] = {}
    
    logger.debug("\n=== VALIDATE_WORKLOAD_AGAINST_CONSTRAINTS ===")
    logger.debug(f"Input resource_map keys: {list(resource_map.keys())}")
    logger.debug(f"Number of constraints: {len(constraint_manager.constraints) if constraint_manager.constraints else 0}")
    
    # If no resources or no constraints, return empty violations
    if not resource_map:
        logger.debug("No resources to validate")
        return violations
        
    if not constraint_manager.constraints:
        logger.debug("No constraints to validate against")
        return violations
    
    logger.debug(f"\n=== VALIDATION START ===")
    logger.debug(f"Validating {len(resource_map)} resources against {len(constraint_manager.constraints)} constraints")
    
    # Log all constraints for debugging
    logger.debug("\n=== CONSTRAINTS ===")
    for i, constraint in enumerate(constraint_manager.constraints, 1):
        logger.debug(f"Constraint {i}: {constraint.name}")
        logger.debug(f"  Type: {constraint.constraint_type}")
        logger.debug(f"  Resource Type: {constraint.resource_type} (type: {type(constraint.resource_type).__name__})")
        logger.debug(f"  Limit: {constraint.limit_value} (type: {type(constraint.limit_value).__name__ if constraint.limit_value is not None else 'None'})")
        logger.debug(f"  Attributes: {constraint.attributes}")
        logger.debug(f"  Severity: {constraint.severity}")
    
    # Log all resources being validated
    logger.debug("\n=== RESOURCES BEING VALIDATED ===")
    for i, (res_id, resource) in enumerate(resource_map.items(), 1):
        logger.debug(f"Resource {i}: {res_id}")
        logger.debug(f"  Type: {getattr(resource, 'resource_type', 'N/A')} (type: {type(getattr(resource, 'resource_type', 'N/A')).__name__})")
        logger.debug(f"  Attributes: {getattr(resource, 'attributes', {})}")
        logger.debug(f"  All attributes: {[attr for attr in dir(resource) if not attr.startswith('_')]}")
        
        # Log all attribute values
        for attr in dir(resource):
            if not attr.startswith('_'):
                try:
                    val = getattr(resource, attr)
                    logger.debug(f"  {attr}: {val} (type: {type(val).__name__})")
                except Exception as e:
                    logger.debug(f"  {attr}: <error getting value: {str(e)}>")
    
    for resource_id, resource in resource_map.items():
        logger.debug(f"\n=== VALIDATING RESOURCE {resource_id} ===")
        logger.debug(f"Resource name: {getattr(resource, 'name', 'unnamed')}")
        logger.debug(f"Resource type: {resource.resource_type} (type: {type(resource.resource_type).__name__})")
        
        # Convert resource_type to string for consistent comparison if it's an enum
        resource_type_str = str(resource.resource_type.value) if hasattr(resource.resource_type, 'value') else str(resource.resource_type)
        logger.debug(f"Resource type (string): {resource_type_str}")
        
        # Get the peak resource values from workload stats
        peak_resources = getattr(workload_stats, 'get_peak_resources', lambda: {})()
        peak_value = peak_resources.get(resource_id)
        if peak_value is None:
            logger.debug(f"No peak value found for resource {resource_id} in {peak_resources}")
            continue
            
        # Ensure peak_value is a number for comparison
        try:
            peak_value = float(peak_value) if peak_value is not None else 0
        except (ValueError, TypeError) as e:
            logger.warning(f"Could not convert peak value {peak_value} to float for resource {resource_id}: {e}")
            peak_value = 0
        
        logger.debug(f"\n=== PEAK RESOURCE VALUE ===")
        logger.debug(f"Resource ID: {resource_id}")
        logger.debug(f"Peak value: {peak_value} (type: {type(peak_value).__name__})")
        
        # Get the resource value from attributes if quota_metric is specified
        resource_attributes = getattr(resource, 'attributes', {})
        logger.debug(f"Resource attributes: {resource_attributes}")
        
        # Create context with workload stats and peak value
        context = {
            'workload': workload_stats,
            'peak_value': peak_value,
            'resource_id': resource_id,
            'value': peak_value  # Default to peak_value if no quota_metric is found
        }
        
        # Initialize value from peak_value as default
        value_to_check = peak_value
        logger.debug(f"Initial value_to_check (from peak_value): {value_to_check}")
        
        # Check if any constraint has a metric/quota_metric that matches a resource attribute
        for constraint in constraint_manager.constraints:
            logger.debug(f"\n=== CHECKING CONSTRAINT: {constraint.name} ===")
            logger.debug(f"Constraint type: {constraint.constraint_type}")
            logger.debug(f"Constraint attributes: {constraint.attributes}")
            
            metric_key = None
            if isinstance(constraint.attributes, dict):
                metric_key = 'metric' if 'metric' in constraint.attributes else ('quota_metric' if 'quota_metric' in constraint.attributes else None)
            logger.debug(f"Looking for metric key: {metric_key} -> {constraint.attributes.get(metric_key) if metric_key else None}")
            
            if metric_key:
                metric_name = constraint.attributes.get(metric_key)
                # First check resource.attributes dictionary
                if hasattr(resource, 'attributes') and isinstance(resource.attributes, dict):
                    logger.debug(f"Resource has attributes: {resource.attributes}")
                    if metric_name in resource.attributes:
                        value_to_check = resource.attributes[metric_name]
        
        # Log all constraints being checked
        logger.debug("\n=== ALL CONSTRAINTS ===")
        for i, c in enumerate(constraint_manager.constraints, 1):
            logger.debug(f"  Constraint {i}: {c.name}")
            logger.debug(f"    Type: {c.constraint_type}")
            logger.debug(f"    Resource Type: {c.resource_type} (type: {type(c.resource_type).__name__})")
            logger.debug(f"    Limit: {c.limit_value} (type: {type(c.limit_value).__name__ if c.limit_value is not None else 'None'})")
            logger.debug(f"    Attributes: {c.attributes}")
        
        # Find matching constraints for this resource type
        matching_constraints = []
        
        for constraint in constraint_manager.constraints:
            # Get constraint resource type as string, handling enums and direct string values
            logger.debug(f"\n  Checking constraint: {constraint.name}")
            type_matches = _resource_type_matches_constraint(resource, constraint)
            logger.debug(f"    Type match: {type_matches}")
            
            # For debugging: Check if the constraint has a metric/quota_metric that matches any attribute
            if hasattr(constraint, 'attributes') and isinstance(constraint.attributes, dict):
                metric_key_dbg = 'metric' if 'metric' in constraint.attributes else ('quota_metric' if 'quota_metric' in constraint.attributes else None)
                if metric_key_dbg:
                    metric = constraint.attributes[metric_key_dbg]
                    has_metric = hasattr(resource, metric) or (hasattr(resource, 'attributes') and metric in getattr(resource, 'attributes', {}))
                    logger.debug(f"    Metric '{metric}' (key: {metric_key_dbg}) found in resource: {has_metric}")
                    
                    # If the metric is in attributes, log its value
                    if hasattr(resource, 'attributes') and isinstance(resource.attributes, dict) and metric in getattr(resource, 'attributes', {}):
                        metric_value = resource.attributes[metric]
                        logger.debug(f"    Metric value for '{metric}': {metric_value} (type: {type(metric_value).__name__})")
            
            if type_matches or constraint.resource_type is None:
                matching_constraints.append(constraint)
                logger.debug("  -> APPLIES")
            else:
                logger.debug("  -> DOES NOT APPLY (type mismatch)")
        
        logger.debug(f"\n=== MATCHING CONSTRAINTS ===")
        logger.debug(f"Found {len(matching_constraints)} matching constraints for resource {resource_id}")
        for i, c in enumerate(matching_constraints, 1):
            logger.debug(f"  {i}. {c.name} (type: {c.constraint_type.name}, limit: {c.limit_value})")
        
        # Only validate if there are matching constraints
        if matching_constraints:
            logger.debug("\n=== VALIDATING RESOURCE ===")
            logger.debug(f"Context: {context}")
            
            # Log peak value and resource attributes
            logger.debug(f"Peak value: {peak_value} (type: {type(peak_value).__name__})")
            logger.debug(f"Resource attributes: {getattr(resource, 'attributes', {})}")
            
            # Log all attributes of the resource for debugging
            logger.debug("\n=== RESOURCE ATTRIBUTES ===")
            for attr in dir(resource):
                if not attr.startswith('_') and not callable(getattr(resource, attr)):
                    logger.debug(f"  {attr}: {getattr(resource, attr, 'N/A')} (type: {type(getattr(resource, attr, 'N/A')).__name__})")
            
            resource_violations = constraint_manager.validate_resource(resource, context)
            logger.debug(f"\n=== VALIDATION RESULT ===")
            logger.debug(f"Found {len(resource_violations)} violations for resource {resource_id}")
            
            # Only add to violations if there are actual violations
            if resource_violations:
                violations[resource_id] = resource_violations
                # Log the violations
                for i, violation in enumerate(resource_violations, 1):
                    logger.debug(f"\n  Violation {i}:")
                    logger.debug(f"    Constraint: {violation.constraint.name}")
                    logger.debug(f"    Value: {violation.value} (type: {type(violation.value).__name__})")
                    logger.debug(f"    Expected: {violation.expected_value} (type: {type(violation.expected_value).__name__})")
                    logger.debug(f"    Details: {violation.details}")
                    logger.debug(f"    Constraint type: {violation.constraint.constraint_type}")
                    logger.debug(f"    Constraint limit: {violation.constraint.limit_value}")
                    logger.debug(f"    Constraint attributes: {violation.constraint.attributes}")
            else:
                logger.debug("No violations found for this resource")
        else:
            logger.debug(f"\n=== NO MATCHING CONSTRAINTS ===")
            logger.debug(f"No matching constraints found for resource type {resource_type_str}")
            logger.debug(f"Available resource types in constraints: {[str(c.resource_type) for c in constraint_manager.constraints]}")
            logger.debug(f"Resource type being checked: {resource_type_str} (type: {type(resource.resource_type).__name__})")
    
    # Log final violations
    logger.debug("\n=== FINAL VIOLATIONS ===")
    if violations:
        for res_id, violation_list in violations.items():
            logger.debug(f"Resource {res_id} has {len(violation_list)} violations:")
            for i, violation in enumerate(violation_list, 1):
                logger.debug(f"  {i}. {violation.constraint.name}: {violation.value} > {violation.expected_value}")
    else:
        logger.debug("No violations found in any resources")
    
    return violations


def handle_constraint_violations_during_simulation(
    violations: Dict[str, List[ConstraintViolation]],
    resources: Dict[str, Resource],
) -> Dict[str, Any]:
    """
    Handle constraint violations during simulation by taking appropriate actions.
    
    For FATAL severity violations, raises a ConstraintFatalException.
    For other severities, logs the violation and takes appropriate action (scale, throttle,
    or log-only), marking violations as handled with the chosen action.

    Args:
        violations: Dictionary mapping resource IDs to lists of constraint violations
        resources: Dictionary mapping resource IDs to Resource objects
        logger: Optional logger instance for logging messages

    Returns:
        Dictionary containing information about remediation actions taken
        
    Raises:
        ConstraintFatalException: If any violation has ERROR or higher severity
    """
    actions_taken: Dict[str, List[Dict[str, Any]]] = {}
    
    # Use module logger
    log = logger
    
    # Track which resources we've logged warnings for
    warned_resources = set()
    
    # Process all violations in a single pass
    for resource_id, resource_violations in violations.items():
        # Skip if resource doesn't exist (but log a warning if we haven't already)
        if resource_id not in resources:
            if resource_id not in warned_resources:
                log.warning(f"Resource ID {resource_id} not found in resources map")
                warned_resources.add(resource_id)
            continue
            
        resource = resources[resource_id]
        resource_actions = []

        for violation in resource_violations:
            # Only handle unhandled violations
            if not violation.handled:
                # Determine remediation action and apply it for non-fatal first
                action = _determine_remediation_action(violation, resource)
                try:
                    if action.get('type') == 'abort_simulation' or violation.get_severity() == ConstraintSeverity.FATAL:
                        # Log then raise via fatal handler for consistency
                        _ = self if False else None  # placeholder to satisfy lints about unused self in free function context
                        raise ConstraintFatalException(
                            f"Fatal constraint violation for resource {resource_id}: {violation.details}",
                            resource=resource,
                            constraint=violation.constraint,
                            actual_value=violation.value
                        )
                    else:
                        _apply_remediation_action(action, resource)
                        violation.handled = True
                        violation.handling_action = action.get('type')
                        log.warning(
                            f"Constraint violation for resource {resource_id}: {violation.details} -> action: {action.get('type')}"
                        )
                        resource_actions.append({
                            'constraint': violation.constraint.name,
                            'severity': violation.get_severity().name,
                            'details': violation.details,
                            'action': action.get('type')
                        })
                except ConstraintFatalException:
                    # Re-raise after logging
                    log.critical(
                        f"Fatal constraint violation for resource {resource_id}: {violation.details}",
                        extra={
                            'resource_id': resource_id,
                            'constraint': violation.constraint.name if violation.constraint else None,
                            'value': violation.value,
                            'expected_value': getattr(violation, 'expected_value', None)
                        }
                    )
                    violation.handled = True
                    violation.handling_action = 'raised_exception'
                    raise
        
        # Add to actions taken if we did anything
        if resource_actions:
            actions_taken[resource_id] = resource_actions
    
    # Log validation summary
    log.debug("\n=== VALIDATION SUMMARY ===")
    total_violations = sum(len(v) for v in violations.values())
    log.debug(f"Total violations found: {total_violations}")
    
    if total_violations > 0:
        log.debug("Detailed violations:")
        for resource_id, violation_list in violations.items():
            if violation_list:  # Only log resources with violations
                log.debug(f"  Resource {resource_id}:")
                for i, violation in enumerate(violation_list, 1):
                    log.debug(f"    Violation {i}:")
                    log.debug(f"      Constraint: {violation.constraint.name}")
                    log.debug(f"      Value: {violation.value} (type: {type(violation.value).__name__})")
                    log.debug(f"      Expected: {violation.expected_value} (type: {type(violation.expected_value).__name__})")
                    log.debug(f"      Details: {violation.details}")
    else:
        log.debug("No violations found in any resource")
    
    return actions_taken


def _determine_remediation_action(
    violation: ConstraintViolation, resource: Resource
) -> Dict[str, Any]:
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
                "details": f"Scale resource to address {violation.constraint.name}",
            }

    # For quota violations, we can't add more resources
    if constraint_type == ConstraintType.QUOTA_LIMIT:
        if severity in [ConstraintSeverity.ERROR, ConstraintSeverity.CRITICAL]:
            return {
                "type": "throttle_workload",
                "resource_id": resource.id,
                "details": f"Throttle workload to comply with {violation.constraint.name}",
            }

    # For fatal violations, we need to abort
    if severity == ConstraintSeverity.FATAL:
        return {
            "type": "abort_simulation",
            "resource_id": resource.id,
            "details": f"Fatal violation of {violation.constraint.name}",
        }

    # Default action
    return {
        "type": "log_only",
        "resource_id": resource.id,
        "details": f"Log violation of {violation.constraint.name}",
    }


def _apply_remediation_action(
    action: Dict[str, Any], resource: Resource
) -> None:
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
        logger.critical(
            f"Simulation abort required due to constraint violation on {resource.name}"
        )
        raise ConstraintFatalException(
            f"Fatal constraint violation on {resource.name}"
        )

    elif action_type == "log_only":
        # No action needed, just log
        logger.info(f"Logged constraint violation on {resource.name}")
