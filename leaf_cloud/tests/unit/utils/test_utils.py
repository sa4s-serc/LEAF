"""Test utilities for LEAF-Cloud unit tests.

This module provides common test utilities and fixtures for unit tests.
"""
import time
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Type, TypeVar, Union

import pytest

from leaf_cloud.core.resource import (
    AllocationError,
    CapacityError,
    Resource,
    ResourcePool,
    ResourceState,
    ResourceType,
    StateTransitionError,
    UtilizationRecord,
)

# Re-export assert_dict_contains from conftest
from leaf_cloud.tests.conftest import assert_dict_contains

# Type variable for resource subclasses
R = TypeVar('R', bound=Resource)

# Custom test resource class that properly extends Resource
class MockResource(Resource):
    """Mock implementation of Resource for testing purposes."""
    
    def __init__(
        self, 
        name: str,
        resource_type: ResourceType = ResourceType.GENERIC,
        region: Optional[str] = None,
        capacity: float = 1.0,
        **kwargs
    ):
        """Initialize a mock resource.
        
        Args:
            name: Name of the resource
            resource_type: Type of the resource
            region: Geographic region
            capacity: Resource capacity
            **kwargs: Additional attributes
        """
        # Initialize the base class first
        self.id = str(uuid.uuid4())
        self.name = name.strip()
        self.resource_type = resource_type
        self.region = region
        self.creation_time = time.time()
        self.attributes = {}
        
        # Initialize thread safety
        self._lock = threading.RLock()
        
        # Set capacity and state
        self.capacity = capacity
        self.allocated_capacity = 0.0
        self._state = ResourceState.AVAILABLE
        
    @property
    def state(self) -> ResourceState:
        """Thread-safe access to resource state."""
        with self._lock:
            return self._state
    
    @state.setter
    def state(self, value: ResourceState) -> None:
        """Thread-safe state update."""
        with self._lock:
            self._state = value
    
    def allocate(self, amount: float, request_id: str, timestamp: float) -> None:
        """Allocate capacity from this resource.
        
        Note: This mock implementation only allows allocation in AVAILABLE state
        to match the test expectations.
        """
        with self._lock:
            # Only allow allocation if resource is AVAILABLE
            if self.state != ResourceState.AVAILABLE:
                raise AllocationError(f"Resource {self.id} is not available")
                
            available = self.capacity - self.allocated_capacity
            if amount > available + 1e-10:  # Allow for floating point imprecision
                raise CapacityError(
                    "Insufficient capacity",
                    current=available,
                    requested=amount
                )
                
            self.allocated_capacity += amount
            self.state = ResourceState.ALLOCATED
    
    @property
    def available_capacity(self) -> float:
        """Get the available capacity of this resource."""
        with self._lock:
            return self.capacity - self.allocated_capacity
    
    def release(self, amount: float, timestamp: float) -> None:
        """Release allocated capacity."""
        with self._lock:
            if amount > self.allocated_capacity:
                raise ValueError("Cannot release more than allocated")
            self.allocated_capacity -= amount
            if self.allocated_capacity <= 0:
                self.state = ResourceState.AVAILABLE

class ResourceTestMixin:
    """Mixin class with common test methods for resource-related tests."""
    
    def assert_resource_state(self, resource, expected_state: Union[str, ResourceState]):
        """Assert that a resource is in the expected state.
        
        Args:
            resource: The resource to check
            expected_state: Expected state as string (e.g., 'AVAILABLE') or ResourceState enum
        """
        if isinstance(expected_state, str):
            expected_state = ResourceState[expected_state.upper()]
            
        assert resource.state == expected_state, \
            f"Expected state '{expected_state.name}', got '{resource.state.name}'"
    
    def assert_capacity(
        self, 
        resource, 
        expected_allocated: float, 
        expected_available: float,
        tolerance: float = 1e-10
    ):
        """Assert resource capacity values with optional floating-point tolerance.
        
        Args:
            resource: The resource to check
            expected_allocated: Expected allocated capacity
            expected_available: Expected available capacity
            tolerance: Allowed difference for floating-point comparison
        """
        assert abs(resource.allocated_capacity - expected_allocated) <= tolerance, \
            f"Expected {expected_allocated} allocated, got {resource.allocated_capacity}"
            
        available = resource.capacity - resource.allocated_capacity
        assert abs(available - expected_available) <= tolerance, \
            f"Expected {expected_available} available, got {available}"

# Common fixtures for resource tests
@pytest.fixture
def timestamp() -> float:
    """Return the current timestamp."""
    return time.time()

@pytest.fixture
def sample_resource_attributes() -> Dict[str, Any]:
    """Return a dictionary of sample resource attributes for testing."""
    return {
        "name": "test-resource",
        "resource_type": ResourceType.COMPUTE,
        "region": "us-west1",
        "capacity": 100.0
    }

@pytest.fixture
def create_resource(sample_resource_attributes: Dict[str, Any]) -> callable:
    """Factory fixture to create a TestResource with custom attributes.
    
    Args:
        sample_resource_attributes: Default attributes to use
        
    Returns:
        A function that creates a TestResource with optional overrides
    """
    def _create_resource(**overrides):
        attrs = {**sample_resource_attributes, **overrides}
        return MockResource(**attrs)
    return _create_resource

@pytest.fixture
def resource(create_resource: callable) -> MockResource:
    """Create a sample test resource for testing."""
    return create_resource()

@pytest.fixture
def sample_resource_pool_attributes() -> Dict[str, Any]:
    """Return a dictionary of sample resource pool attributes for testing."""
    return {
        "name": "test-pool",
        "resource_type": ResourceType.COMPUTE,
        "region": "us-west1"
    }

@pytest.fixture
def create_resource_pool(sample_resource_pool_attributes: Dict[str, Any]) -> callable:
    """Factory fixture to create a ResourcePool with custom attributes.
    
    Args:
        sample_resource_pool_attributes: Default attributes to use
        
    Returns:
        A function that creates a ResourcePool with optional overrides
    """
    def _create_resource_pool(**overrides):
        attrs = {**sample_resource_pool_attributes, **overrides}
        return ResourcePool(**attrs)
    return _create_resource_pool

@pytest.fixture
def resource_pool(create_resource_pool: callable) -> ResourcePool:
    """Create a sample resource pool for testing."""
    return create_resource_pool()

@pytest.fixture
def populated_resource_pool(
    create_resource_pool: callable, 
    create_resource: callable,
) -> ResourcePool:
    """Create a resource pool with sample resources for testing."""
    pool = create_resource_pool()
    
    # Add some resources with different capacities
    for i, capacity in enumerate([100.0, 200.0, 50.0]):
        resource = create_resource(
            name=f"res-{i}",
            capacity=capacity,
            resource_type=pool.resource_type
        )
        pool.add_resource(resource)
    
    return pool
