"""Unit tests for the constraints module."""

import sys
import unittest
import logging
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, List, Any, Optional, Set, Callable, Union

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from datetime import datetime

# Import the classes and functions to test
from leaf_cloud.models.constraints import (
    Constraint,
    ConstraintManager,
    ConstraintType,
    ConstraintViolation,
    ConstraintSeverity,
    ServiceQuotaDefinitions,
    create_standard_constraints,
    validate_workload_against_constraints,
    handle_constraint_violations_during_simulation,
    ConstraintFatalException,
)
from leaf_cloud.core.resource import Resource, ResourceType, ResourceState

# Get all constraint types from the ConstraintType enum
CONSTRAINT_TYPES = {t.name: t for t in ConstraintType}

# Define test-specific constraint types
TEST_CONSTRAINT_TYPE = {
    'QUOTA': next((t for t in ConstraintType if 'QUOTA' in t.name), ConstraintType.QUOTA_LIMIT),
    'LIMIT': next((t for t in ConstraintType if 'LIMIT' in t.name and 'QUOTA' not in t.name), ConstraintType.CAPACITY_LIMIT),
    'QUOTA_LIMIT': ConstraintType.QUOTA_LIMIT,
    'CAPACITY_LIMIT': ConstraintType.CAPACITY_LIMIT
}
from leaf_cloud.core.resource import Resource, ResourceState
from leaf_cloud.core.workload import WorkloadStatistics

# Test data
@dataclass
class MockResource(Resource):
    """Mock resource for testing constraints.
    
    This class extends the base Resource class to provide a mock implementation
    for testing constraint validation.
    """
    def __init__(self, name: str, resource_type: Union[str, ResourceType], **kwargs):
        # Convert string resource_type to ResourceType if needed
        if isinstance(resource_type, str):
            resource_type_enum = getattr(ResourceType, resource_type.upper(), ResourceType.GENERIC)
        else:
            resource_type_enum = resource_type
            
        # Allow explicit override of resource_type via kwargs
        if 'resource_type_enum' in kwargs:
            resource_type_enum = kwargs.pop('resource_type_enum')
        
        # Store the original resource type for debugging
        self._original_resource_type = resource_type_enum
        
        # Initialize the base Resource class with required parameters
        # Pass the resource_type_enum directly to the base class
        super().__init__(
            name=name,
            resource_type=resource_type_enum,  # Pass the enum directly
            region=kwargs.get('region')
        )
        
        # Initialize required attributes from Resource base class
        self._state = kwargs.get('state', ResourceState.AVAILABLE)  # Default to AVAILABLE state
        self._lock = kwargs.get('_lock')
        if self._lock is None:
            import threading
            self._lock = threading.RLock()
        
        # Set additional attributes directly on the instance
        self.zone = kwargs.get('zone')
        self.project = kwargs.get('project')
        self.service = kwargs.get('service')
        self.id = kwargs.get('id', f"resource-{name}")  # Add id attribute with default value
        
        # Set capacity if provided
        if 'capacity' in kwargs:
            self.capacity = kwargs['capacity']
        else:
            self.capacity = 1.0  # Default capacity
        
        # Set any additional attributes from kwargs directly on the instance
        for key, value in kwargs.items():
            if not hasattr(self, key) and key not in ['region', 'state', 'capacity', 'id', 'resource_type_enum', '_lock']:
                setattr(self, key, value)
    
    # Property to handle state getter/setter to match Resource class behavior
    @property
    def state(self):
        return self._state
        
    @state.setter
    def state(self, value):
        self._state = value
    
    def __str__(self):
        return f"MockResource(name={self.name}, type={self.resource_type})"


class TestConstraint(unittest.TestCase):
    """Test cases for the Constraint class."""
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.constraint = Constraint(
            name="test_constraint",
            description="Test constraint",
            constraint_type=TEST_CONSTRAINT_TYPE['QUOTA'],
            resource_type="compute.v1.instance",
            severity=ConstraintSeverity.ERROR,
            limit_value=10,
            region="us-central1",
            project="test-project",
        )
    
    def test_constraint_initialization(self):
        """Test that the constraint initializes correctly."""
        self.assertEqual(self.constraint.name, "test_constraint")
        self.assertEqual(self.constraint.description, "Test constraint")
        # Check that the constraint type is one of the valid enum values
        self.assertIn(self.constraint.constraint_type, ConstraintType)
        self.assertEqual(self.constraint.resource_type, "compute.v1.instance")
        self.assertEqual(self.constraint.severity, ConstraintSeverity.ERROR)
        self.assertEqual(self.constraint.limit_value, 10)
        self.assertEqual(self.constraint.region, "us-central1")
        self.assertEqual(self.constraint.project, "test-project")
    
    def test_check_violation_with_limit_value(self):
        """Test check_violation with a simple limit value."""
        resource = MockResource("test-resource", "compute.v1.instance")
        
        # Value under limit - no violation
        self.assertFalse(self.constraint.check_violation(resource, 5))
        
        # Value at limit - no violation
        self.assertFalse(self.constraint.check_violation(resource, 10))
        
        # Value over limit - violation
        self.assertTrue(self.constraint.check_violation(resource, 15))
    
    def test_check_violation_with_limit_function(self):
        """Test check_violation with a limit function."""
        def limit_func(resource, value, constraint):
            return value > 20
            
        constraint = Constraint(
            name="function_constraint",
            description="Test function constraint",
            constraint_type=TEST_CONSTRAINT_TYPE['QUOTA_LIMIT'],
            resource_type="compute.v1.instance",
            limit_function=limit_func
        )
        
        resource = MockResource("test-resource", "compute.v1.instance")
        
        # Value under limit - no violation
        self.assertFalse(constraint.check_violation(resource, 15))
        
        # Value at limit - no violation
        self.assertFalse(constraint.check_violation(resource, 20))
        
        # Value over limit - violation
        self.assertTrue(constraint.check_violation(resource, 25))
    
    def test_to_dict(self):
        """Test the to_dict method."""
        result = self.constraint.to_dict()
        self.assertEqual(result["name"], "test_constraint")
        self.assertEqual(result["description"], "Test constraint")
        # Check that constraint_type is a string and is one of the expected values
        self.assertIsInstance(result["constraint_type"], str)
        self.assertIn(result["constraint_type"], [t.name for t in ConstraintType])
        self.assertEqual(result["resource_type"], "compute.v1.instance")
        self.assertEqual(result["severity"], "ERROR")
        self.assertEqual(result["limit_value"], 10)
        self.assertEqual(result["region"], "us-central1")
        self.assertEqual(result["project"], "test-project")


class TestConstraintViolation(unittest.TestCase):
    """Test cases for the ConstraintViolation class."""
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.constraint = Constraint(
            name="test_constraint",
            description="Test constraint",
            constraint_type=TEST_CONSTRAINT_TYPE['QUOTA'],
            resource_type="compute.v1.instance",
        )
        
        self.resource = MockResource("test-resource", "compute.v1.instance")
        
        self.violation = ConstraintViolation(
            constraint=self.constraint,
            resource=self.resource,
            value=15,
            expected_value=10,
            details="Value 15 exceeds limit of 10",
        )
    
    def test_violation_initialization(self):
        """Test that the violation initializes correctly."""
        self.assertEqual(self.violation.constraint, self.constraint)
        self.assertEqual(self.violation.resource, self.resource)
        self.assertEqual(self.violation.value, 15)
        self.assertEqual(self.violation.expected_value, 10)
        self.assertEqual(self.violation.details, "Value 15 exceeds limit of 10")
        self.assertFalse(self.violation.handled)
        self.assertIsNone(self.violation.handling_action)
    
    def test_get_severity(self):
        """Test the get_severity method."""
        self.assertEqual(self.violation.get_severity(), ConstraintSeverity.ERROR)
        
        # Test with a different severity
        constraint = Constraint(
            name="warning_constraint",
            description="Test warning constraint",
            constraint_type=TEST_CONSTRAINT_TYPE['QUOTA'],
            resource_type="compute.v1.instance",
            severity=ConstraintSeverity.WARNING,
        )
        
        violation = ConstraintViolation(
            constraint=constraint,
            resource=self.resource,
            value=15,
            expected_value=10,
        )
        
        self.assertEqual(violation.get_severity(), ConstraintSeverity.WARNING)
    
    def test_to_dict(self):
        """Test the to_dict method."""
        result = self.violation.to_dict()
        self.assertIsInstance(result, dict)
        self.assertEqual(result["constraint"]["name"], "test_constraint")
        self.assertEqual(result["resource_id"], "resource-test-resource")
        self.assertEqual(result["resource_name"], "test-resource")
        self.assertEqual(result["value"], 15)
        self.assertEqual(result["expected_value"], 10)
        self.assertEqual(result["details"], "Value 15 exceeds limit of 10")
        self.assertFalse(result["handled"])
        self.assertIsNone(result["handling_action"])
        self.assertIn("timestamp", result)


class TestConstraintManager(unittest.TestCase):
    """Test cases for the ConstraintManager class."""
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.manager = ConstraintManager()
        
        # Create test resource with matching resource type
        # Use the same format (enum) for both resource and constraints
        self.resource = MockResource(
            name="test-instance",
            resource_type=ResourceType.COMPUTE,
            attributes={"cpus": 4, "memory_gb": 16},
            # Explicitly set the resource_type on the instance to ensure it's set
            resource_type_enum=ResourceType.COMPUTE
        )
        
        # Create test constraints with matching resource type
        self.constraint1 = Constraint(
            name="cpu_quota",
            description="CPU quota limit",
            constraint_type=ConstraintType.QUOTA_LIMIT,
            limit_value=8,
            resource_type=ResourceType.COMPUTE,
            severity=ConstraintSeverity.ERROR,
        )
        
        self.constraint2 = Constraint(
            name="memory_quota",
            description="Memory quota limit",
            constraint_type=ConstraintType.QUOTA_LIMIT,
            limit_value=32,
            resource_type=ResourceType.COMPUTE,
            severity=ConstraintSeverity.ERROR,
        )
    
    def test_add_constraint(self):
        """Test adding a single constraint."""
        self.manager.add_constraint(self.constraint1)
        self.assertEqual(len(self.manager.constraints), 1)
        self.assertEqual(self.manager.constraints[0].name, "cpu_quota")
    
    def test_add_constraints(self):
        """Test adding multiple constraints."""
        self.manager.add_constraints([self.constraint1, self.constraint2])
        self.assertEqual(len(self.manager.constraints), 2)
        self.assertEqual(self.manager.constraints[0].name, "cpu_quota")
        self.assertEqual(self.manager.constraints[1].name, "memory_quota")
    
    def test_remove_constraint(self):
        """Test removing a constraint."""
        self.manager.add_constraints([self.constraint1, self.constraint2])
        
        # Remove an existing constraint
        result = self.manager.remove_constraint("cpu_quota")
        self.assertTrue(result)
        self.assertEqual(len(self.manager.constraints), 1)
        self.assertEqual(self.manager.constraints[0].name, "memory_quota")
        
        # Try to remove a non-existent constraint
        result = self.manager.remove_constraint("non_existent")
        self.assertFalse(result)
    
    def test_validate_resource(self):
        """Test validating a resource against constraints."""
        # Debug print the initial state
        print("\n=== Test Setup ===")
        print(f"Resource type: {type(self.resource).__name__}")
        print(f"Resource name: {self.resource.name}")
        print(f"Resource ID: {self.resource.id}")
        print(f"Resource type (raw): {self.resource.resource_type}")
        print(f"Resource type (str): {str(self.resource.resource_type)}")
        print(f"Resource type (repr): {repr(self.resource.resource_type)}")
        print(f"Resource type (class): {self.resource.resource_type.__class__.__name__}")
        print(f"Resource attributes: {getattr(self.resource, 'attributes', 'No attributes')}")
        
        # Add constraints
        print("\n=== Adding Constraints ===")
        print(f"Constraint 1: {self.constraint1.name}, limit_value={self.constraint1.limit_value}, type={self.constraint1.constraint_type}")
        print(f"  - Resource type: {self.constraint1.resource_type} (type: {type(self.constraint1.resource_type).__name__})")
        print(f"Constraint 2: {self.constraint2.name}, limit_value={self.constraint2.limit_value}, type={self.constraint2.constraint_type}")
        print(f"  - Resource type: {self.constraint2.resource_type} (type: {type(self.constraint2.resource_type).__name__})")
        self.manager.add_constraints([self.constraint1, self.constraint2])
        
        # Test 1: No violations (value=4, limit=8)
        print("\n=== Test 1: No violations (value=4, limit=8) ===")
        context = {"value": 4}  # Below both limits
        print(f"Context: {context}")
        print(f"Constraints: {[c.name for c in self.manager.constraints]}")
        
        # Add detailed debug logging
        print("\n=== Debug: Validating resource with context:", context)
        for constraint in self.manager.constraints:
            print(f"\nChecking constraint: {constraint.name}")
            print(f"  - Constraint type: {constraint.constraint_type}")
            print(f"  - Limit value: {constraint.limit_value}")
            print(f"  - Resource type: {self.resource.resource_type}")
            print(f"  - Constraint resource type: {constraint.resource_type}")
            
            # Simulate the validation logic
            if constraint.resource_type and constraint.resource_type != self.resource.resource_type:
                print("  - SKIPPING: Resource type mismatch")
                continue
                
            # Simulate value retrieval
            value = context.get("value")
            print(f"  - Retrieved value from context: {value}")
            
            # Simulate the comparison
            if value is not None and constraint.limit_value is not None:
                try:
                    value_float = float(value)
                    limit_float = float(constraint.limit_value)
                    is_violated = value_float > limit_float
                    print(f"  - Numeric comparison: {value_float} > {limit_float} = {is_violated}")
                except (ValueError, TypeError) as e:
                    print(f"  - Could not compare values numerically: {e}")
                    is_violated = value > constraint.limit_value
                    print(f"  - Direct comparison: {value} > {constraint.limit_value} = {is_violated}")
        
        # Run the actual validation
        violations = self.manager.validate_resource(self.resource, context)
        print(f"\nFound {len(violations)} violations (expected 0)")
        self.assertEqual(len(violations), 0)
        
        # Test 2: One violation (value=16, limit=8)
        print("\n=== Test 2: One violation (value=16, limit=8) ===")
        context = {"value": 16}
        print(f"Context: {context}")
        print(f"Constraints: {[c.name for c in self.manager.constraints]}")
        
        # Add detailed debug logging
        print("\n=== Debug: Validating resource with context:", context)
        for constraint in self.manager.constraints:
            print(f"\nChecking constraint: {constraint.name}")
            print(f"  - Constraint type: {constraint.constraint_type}")
            print(f"  - Limit value: {constraint.limit_value}")
            print(f"  - Resource type: {self.resource.resource_type}")
            print(f"  - Constraint resource type: {constraint.resource_type}")
            
            # Simulate the validation logic
            if constraint.resource_type and constraint.resource_type != self.resource.resource_type:
                print("  - SKIPPING: Resource type mismatch")
                continue
                
            # Simulate value retrieval
            value = context.get("value")
            print(f"  - Retrieved value from context: {value}")
            
            # Simulate the comparison
            if value is not None and constraint.limit_value is not None:
                try:
                    value_float = float(value)
                    limit_float = float(constraint.limit_value)
                    is_violated = value_float > limit_float
                    print(f"  - Numeric comparison: {value_float} > {limit_float} = {is_violated}")
                except (ValueError, TypeError) as e:
                    print(f"  - Could not compare values numerically: {e}")
                    is_violated = value > constraint.limit_value
                    print(f"  - Direct comparison: {value} > {constraint.limit_value} = {is_violated}")
        
        # Run the actual validation
        violations = self.manager.validate_resource(self.resource, context)
        print(f"\nFound {len(violations)} violations (expected 1)")
        if violations:
            print("Violations:")
            for v in violations:
                print(f"  - {v.constraint.name}: {v.value} > {v.expected_value}")
        else:
            print("No violations found. This is unexpected.")
            print("Possible reasons:")
            print("  1. The constraint check is not working as expected")
            print("  2. The value is not being passed correctly to the constraint check")
            print("  3. The constraint limit is not being set correctly")
        
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].constraint.name, "cpu_quota")
        
        print("\n=== Testing with multiple violations (value=40, limits=8,32) ===")
        context = {"value": 40}  # Exceeds both cpu_quota (8) and memory_quota (32)
        print(f"Context: {context}")
        violations = self.manager.validate_resource(self.resource, context)
        print(f"Found {len(violations)} violations (expected 2)")
        if violations:
            print("Violations:")
            for v in violations:
                print(f"  - {v.constraint.name}: {v.value} > {v.expected_value}")
        self.assertEqual(len(violations), 2)
    
    def test_get_violations(self):
        """Test getting violations with filters."""
        # Add constraints and create some violations
        self.manager.add_constraints([self.constraint1, self.constraint2])
        
        # Create test violations
        resource1 = MockResource("instance-1", "compute.v1.instance", attributes={"cpus": 16})
        resource2 = MockResource("instance-2", "compute.v1.instance", attributes={"memory_gb": 64})
        
        violation1 = ConstraintViolation(
            constraint=self.constraint1,
            resource=resource1,
            value=16,
            expected_value=8,
            details="CPU quota exceeded",
        )
        
        violation2 = ConstraintViolation(
            constraint=self.constraint2,
            resource=resource2,
            value=64,
            expected_value=32,
            details="Memory quota exceeded",
        )
        
        self.manager.violations = [violation1, violation2]
        
        # Get all violations
        all_violations = self.manager.get_violations()
        self.assertEqual(len(all_violations), 2)
        
        # Filter by severity
        error_violations = self.manager.get_violations(ConstraintSeverity.ERROR)
        self.assertEqual(len(error_violations), 2)
        
        # Test with a different severity (should return empty list)
        warning_violations = self.manager.get_violations(ConstraintSeverity.WARNING)
        self.assertEqual(len(warning_violations), 0)
    
    def test_violation_summary(self):
        """Test getting a summary of violations."""
        # Add some test violations with different severities
        error_constraint = Constraint(
            name="error_constraint",
            description="Error constraint",
            constraint_type=TEST_CONSTRAINT_TYPE['QUOTA_LIMIT'],
            resource_type="compute.v1.instance",
            severity=ConstraintSeverity.ERROR,
        )
        
        warning_constraint = Constraint(
            name="warning_constraint",
            description="Warning constraint",
            constraint_type=TEST_CONSTRAINT_TYPE['CAPACITY_LIMIT'],
            resource_type="compute.v1.instance",
            severity=ConstraintSeverity.WARNING,
        )
        
        self.manager.violations = [
            ConstraintViolation(
                constraint=error_constraint,
                resource=self.resource,
                value=10,
                expected_value=5,
            ),
            ConstraintViolation(
                constraint=error_constraint,
                resource=self.resource,
                value=12,
                expected_value=5,
            ),
            ConstraintViolation(
                constraint=warning_constraint,
                resource=self.resource,
                value=8,
                expected_value=5,
            ),
        ]
        
        # Get and verify the summary
        summary = self.manager.get_violation_summary()
        # Check the counts in the by_severity dictionary
        self.assertEqual(summary['by_severity'].get(ConstraintSeverity.ERROR.name, 0), 2)
        self.assertEqual(summary['by_severity'].get(ConstraintSeverity.WARNING.name, 0), 1)


class TestServiceQuotaDefinitions(unittest.TestCase):
    """Test cases for the ServiceQuotaDefinitions class."""
    
    def test_get_compute_quotas(self):
        """Test getting Compute Engine quota definitions."""
        quotas = ServiceQuotaDefinitions.get_compute_quotas()
        self.assertIsInstance(quotas, dict)
        self.assertGreater(len(quotas), 0)
        
        # Check a few common quotas
        self.assertIn("cpus_per_region", quotas)
        self.assertIn("instances_per_region", quotas)
        self.assertIn("in_use_addresses", quotas)
    
    def test_get_gke_quotas(self):
        """Test getting GKE quota definitions."""
        quotas = ServiceQuotaDefinitions.get_gke_quotas()
        self.assertIsInstance(quotas, dict)
        self.assertGreater(len(quotas), 0)
        
        # Check a few common quotas
        self.assertIn("clusters_per_project", quotas)
        self.assertIn("nodes_per_zone", quotas)
    
    def test_get_storage_quotas(self):
        """Test getting Cloud Storage quota definitions."""
        quotas = ServiceQuotaDefinitions.get_storage_quotas()
        self.assertIsInstance(quotas, dict)
        self.assertGreater(len(quotas), 0)
        
        # Check a few common quotas
        self.assertIn("buckets_per_project", quotas)
        self.assertIn("bucket_acls_per_bucket", quotas)
    
    def test_get_network_quotas(self):
        """Test getting Networking quota definitions."""
        quotas = ServiceQuotaDefinitions.get_network_quotas()
        self.assertIsInstance(quotas, dict)
        self.assertGreater(len(quotas), 0)
        
        # Check a few common quotas
        self.assertIn("firewalls_per_network", quotas)
        self.assertIn("networks_per_project", quotas)


class TestConstraintUtils(unittest.TestCase):
    """Test cases for utility functions in the constraints module."""
    
    def test_create_standard_constraints(self):
        """Test creating standard constraints."""
        constraints = create_standard_constraints()
        self.assertIsInstance(constraints, list)
        self.assertGreater(len(constraints), 0)
        
        # Check that we have constraints for different services
        constraint_types = {c.constraint_type for c in constraints}
        self.assertIn(ConstraintType.QUOTA_LIMIT, constraint_types)
        self.assertIn(ConstraintType.CAPACITY_LIMIT, constraint_types)
    
    def test_validate_workload_against_constraints(self):
        """Test validating a workload against constraints."""
        # Ensure sys is imported at the module level
        import sys
        
        # Set up logging
        root_logger = logging.getLogger()
        
        # Remove all existing handlers
        root_logger.handlers = []
        
        # Create a stream handler that outputs to stdout
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        
        # Add the handler to the root logger
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)
        
        # Also enable debug logging for the constraints module
        constraints_logger = logging.getLogger('leaf_cloud.models.constraints')
        constraints_logger.setLevel(logging.DEBUG)
        constraints_logger.addHandler(handler)
        
        # Get logger for this test
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        
        logger.info("Starting test_validate_workload_against_constraints")
        
        # Create test resources with attributes that match the quota_metric in the constraint
        # Use resource_type_enum to preserve the exact string type
        resource1 = MockResource(
            "test-resource-1", 
            "compute.v1.instance",
            id="resource-1",
            cpu=5,  # This is within the limit
            attributes={"cpu": 5, "region": "us-central1"},
            resource_type_enum="compute.v1.instance"  # Preserve the exact string type
        )
        
        resource2 = MockResource(
            "test-resource-2", 
            "compute.v1.instance",
            id="resource-2",
            cpu=11,  # This exceeds the limit
            attributes={"cpu": 11, "region": "us-central1"},
            resource_type_enum="compute.v1.instance"  # Preserve the exact string type
        )
        
        logger.debug(f"Created resource1: id={resource1.id}, cpu={getattr(resource1, 'cpu', 'N/A')}, attributes={getattr(resource1, 'attributes', {})}")
        logger.debug(f"Created resource2: id={resource2.id}, cpu={getattr(resource2, 'cpu', 'N/A')}, attributes={getattr(resource2, 'attributes', {})}")
        
        # Create a resource map for the test
        resource_map = {
            "resource-1": resource1,
            "resource-2": resource2
        }
        
        # Create a constraint that will be violated by the total capacity
        constraint = Constraint(
            name="total_cpu_quota",
            description="Total CPU quota across all instances",
            constraint_type=ConstraintType.QUOTA_LIMIT,
            resource_type="compute.v1.instance",
            limit_value=12,  # Will be exceeded by total of 16 CPUs
            severity=ConstraintSeverity.ERROR
        )
        
        # Create a workload statistics mock with required attributes
        workload_stats = MagicMock()
        peak_resources = {
            "resource-1": 5,  # Using string keys as expected by the implementation
            "resource-2": 11  # Total is 16, which exceeds the quota of 12
        }
        workload_stats.get_peak_resources.return_value = peak_resources
        workload_stats.average_request_rate = 10.5
        workload_stats.workload_name = "test-workload"
        workload_stats.total_tokens = 1000
        workload_stats.peak_request_rate = 20.0
        
        logger.debug(f"Created workload_stats with peak_resources: {peak_resources}")
        
        logger.debug(f"Created resource_map with keys: {list(resource_map.keys())}")
        
        # Create a constraint manager and add the constraint
        constraint_manager = ConstraintManager()
        
        # Create a constraint that will be violated by the resource values
        constraint = Constraint(
            name="cpu_quota",
            description="CPU quota per instance",
            constraint_type=ConstraintType.QUOTA_LIMIT,
            resource_type="compute.v1.instance",
            limit_value=10,  # Will be exceeded by resource-2 which has 11
            severity=ConstraintSeverity.ERROR,
            attributes={"quota_metric": "cpu"}  # This should match the attribute name in the resource
        )
        
        logger.debug("\n=== CREATED CONSTRAINT ===")
        logger.debug(f"Name: {constraint.name}")
        logger.debug(f"Type: {constraint.constraint_type}")
        logger.debug(f"Resource Type: {constraint.resource_type}")
        logger.debug(f"Limit Value: {constraint.limit_value}")
        logger.debug(f"Attributes: {constraint.attributes}")
        
        # Log the constraint details
        logger.debug("\n=== CREATED CONSTRAINT ===")
        logger.debug(f"Name: {constraint.name}")
        logger.debug(f"Type: {constraint.constraint_type}")
        logger.debug(f"Resource Type: {constraint.resource_type}")
        logger.debug(f"Limit Value: {constraint.limit_value}")
        logger.debug(f"Attributes: {constraint.attributes}")
        
        # Add the constraint to the manager
        logger.debug("\n=== ADDING CONSTRAINT TO MANAGER ===")
        constraint_manager.add_constraint(constraint)
        logger.debug(f"Number of constraints in manager: {len(constraint_manager.constraints)}")
        for i, c in enumerate(constraint_manager.constraints, 1):
            logger.debug(f"  Constraint {i}: {c.name} (type: {c.constraint_type})")
        
        # Log the resource map before validation
        logger.debug("\n=== RESOURCE MAP ===")
        for res_id, resource in resource_map.items():
            logger.debug(f"Resource ID: {res_id}")
            logger.debug(f"  Name: {resource.name}")
            logger.debug(f"  Type: {resource.resource_type}")
            logger.debug(f"  Attributes: {getattr(resource, 'attributes', 'No attributes')}")
            if hasattr(resource, 'attributes'):
                for attr_name, attr_value in resource.attributes.items():
                    logger.debug(f"    {attr_name}: {attr_value} (type: {type(attr_value).__name__})")
        
        # Log the workload stats
        logger.debug("\n=== WORKLOAD STATS ===")
        logger.debug(f"Workload name: {getattr(workload_stats, 'workload_name', 'N/A')}")
        logger.debug(f"Average request rate: {getattr(workload_stats, 'average_request_rate', 'N/A')}")
        logger.debug(f"Total tokens: {getattr(workload_stats, 'total_tokens', 'N/A')}")
        logger.debug(f"Peak request rate: {getattr(workload_stats, 'peak_request_rate', 'N/A')}")
        
        # Log peak resources
        logger.debug("\n=== PEAK RESOURCES ===")
        for res_id, value in peak_resources.items():
            logger.debug(f"  {res_id}: {value} (type: {type(value).__name__})")
        
        # Test the validation with detailed debug logging
        logger.debug("\n=== CALLING VALIDATE_WORKLOAD_AGAINST_CONSTRAINTS ===")
        logger.debug("Input parameters:")
        logger.debug(f"  workload_stats: {workload_stats}")
        logger.debug(f"  resource_map keys: {list(resource_map.keys())}")
        logger.debug(f"  constraint_manager constraints: {[c.name for c in constraint_manager.constraints]}")
        
        # Enable debug logging for the constraints module
        import sys
        old_stdout = sys.stdout
        sys.stdout = sys.__stdout__  # Ensure we can see print statements
        
        try:
            violations = validate_workload_against_constraints(
                workload_stats=workload_stats,
                resource_map=resource_map,
                constraint_manager=constraint_manager,
            )
        except Exception as e:
            logger.error(f"Error in validate_workload_against_constraints: {e}", exc_info=True)
            raise
        finally:
            sys.stdout = old_stdout
        
        logger.debug("\n=== VALIDATION COMPLETE ===")
        logger.debug(f"Violations found: {len(violations)}")
        for res_id, violation_list in violations.items():
            logger.debug(f"  Resource {res_id} has {len(violation_list)} violations:")
            for i, violation in enumerate(violation_list, 1):
                logger.debug(f"    Violation {i}:")
                logger.debug(f"      Constraint: {violation.constraint.name}")
                logger.debug(f"      Value: {violation.value}")
                logger.debug(f"      Expected: {violation.expected_value}")
                logger.debug(f"      Details: {violation.details}")
        
        logger.debug(f"\n=== Validation complete ===")
        logger.debug(f"Found violations: {violations}")
        
        # Log detailed information about the resources and constraints
        logger.debug("\n=== Resource Details ===")
        for res_id, resource in resource_map.items():
            logger.debug(f"Resource ID: {res_id}")
            logger.debug(f"  Type: {type(resource).__name__}")
            logger.debug(f"  Attributes: {[attr for attr in dir(resource) if not attr.startswith('_')]}")
            if hasattr(resource, '__dict__'):
                logger.debug(f"  __dict__: {resource.__dict__}")
        
        # Log the constraints being checked
        logger.debug("\n=== Constraints Being Checked ===")
        for c in constraint_manager.constraints:  # Directly access the constraints list
            logger.debug(f"Constraint: {c.name}")
            logger.debug(f"  Type: {c.constraint_type}")
            logger.debug(f"  Resource Type: {c.resource_type}")
            logger.debug(f"  Limit: {c.limit_value}")
            logger.debug(f"  Attributes: {c.attributes}")
            logger.debug(f"  Full constraint: {c}")
        
        # Log the peak resources being used
        logger.debug("\n=== Peak Resources ===")
        for res_id, value in peak_resources.items():
            logger.debug(f"  {res_id}: {value} (type: {type(res_id).__name__})")
        
        logger.debug("\n=== End of Debug Information ===\n")
        
        # Should have one violation for resource-2 which exceeds the limit of 10
        self.assertEqual(len(violations), 1, f"Expected 1 violation, got {len(violations)}. Violations: {violations}")
        self.assertIn("resource-2", violations, f"Expected violation for resource-2, got violations for: {violations.keys()}")
        self.assertEqual(len(violations["resource-2"]), 1, f"Expected 1 violation for resource-2, got {len(violations.get('resource-2', []))}")
        self.assertEqual(violations["resource-2"][0].constraint.name, "cpu_quota", 
                         f"Expected violation for constraint 'cpu_quota', got {violations['resource-2'][0].constraint.name if violations and 'resource-2' in violations and violations['resource-2'] else 'no violation'}")
    
    def test_handle_constraint_violations_during_simulation(self):
        """Test handling constraint violations during simulation."""
        # Create a test resource with an ID and constraint violation
        resource = MockResource(
            "test-resource", "compute.v1.instance",
            capacity=10, allocated_capacity=8, region="us-central1",
            id="resource-1"
        )
        
        constraint = Constraint(
            name="cpu_quota",
            description="CPU quota per instance",
            constraint_type=TEST_CONSTRAINT_TYPE['QUOTA_LIMIT'],
            resource_type="compute.v1.instance",
            limit_value=8,
        )
        
        violation = ConstraintViolation(
            constraint=constraint,
            resource=resource,
            value=8,
            expected_value=8,
            details="Test violation"
        )
        
        # Create a mock logger
        mock_logger = MagicMock()
        
        # Test with a warning severity violation
        warning_constraint = Constraint(
            name="cpu_quota_warning",
            description="CPU quota per instance (warning)",
            constraint_type=TEST_CONSTRAINT_TYPE['QUOTA_LIMIT'],
            resource_type="compute.v1.instance",
            limit_value=8,
            severity=ConstraintSeverity.WARNING
        )

        warning_violation = ConstraintViolation(
            constraint=warning_constraint,
            resource=resource,
            value=8,
            expected_value=8,
            details="Test warning violation"
        )

        # Mock the logger at the module level
        with patch('leaf_cloud.models.constraints.logger', mock_logger):
            result = handle_constraint_violations_during_simulation(
                violations={resource.id: [warning_violation]},
                resources={resource.id: resource}
            )

        # Should log a warning for WARNING severity
        mock_logger.warning.assert_called_once()
        
        # Test with a non-existent resource ID to trigger the warning
        mock_logger.reset_mock()
        non_existent_id = "non-existent-resource"
        with patch('leaf_cloud.models.constraints.logger', mock_logger):
            result = handle_constraint_violations_during_simulation(
                violations={non_existent_id: [violation]},
                resources={resource.id: resource}  # Only include the real resource, not the non-existent one
            )
        
        # Should log a warning about the missing resource
        mock_logger.warning.assert_called_once()
        self.assertIn(non_existent_id, mock_logger.warning.call_args[0][0])
        
        # Test with an error severity violation
        mock_logger.reset_mock()
        
        # Create a new constraint with ERROR severity
        error_constraint = Constraint(
            name="cpu_quota_error",
            description="CPU quota per instance (error)",
            constraint_type=TEST_CONSTRAINT_TYPE['QUOTA_LIMIT'],
            resource_type="compute.v1.instance",
            limit_value=8,
            severity=ConstraintSeverity.ERROR
        )
        
        error_violation = ConstraintViolation(
            constraint=error_constraint,
            resource=resource,
            value=8,
            expected_value=8,
            details="Test error violation"
        )
        
        # Test that ERROR severity raises ConstraintFatalException
        with patch('leaf_cloud.models.constraints.logger', mock_logger):
            with self.assertRaises(ConstraintFatalException):
                handle_constraint_violations_during_simulation(
                    violations={resource.id: [error_violation]},
                    resources={resource.id: resource}
                )
        
        # Should log an error before raising
        mock_logger.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
