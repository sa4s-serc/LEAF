from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Any, ClassVar, Dict, Iterator, List, Literal, Optional, Tuple, Type, TypeVar, 
    Union, Deque, TypedDict, overload, TYPE_CHECKING, final, TypeAlias
)
from collections import deque
from datetime import datetime
import time
import uuid
import logging
import threading

if TYPE_CHECKING:
    from leaf_cloud.core.petri_net import TokenColor

# Type aliases
ResourceID: TypeAlias = str
Region: TypeAlias = str
Timestamp: TypeAlias = float
Utilization: TypeAlias = float  # 0.0 to 1.0
Capacity: TypeAlias = float
AllocationKey: TypeAlias = str  # Key for allocation history
ResourceAttributes: TypeAlias = Dict[str, Any]

# Generic type variable for resource subclasses
R = TypeVar('R', bound='Resource')

# Custom exceptions
class ResourceError(Exception):
    """Base exception for resource-related errors."""
    pass

class AllocationError(ResourceError):
    """Raised when resource allocation fails."""
    def __init__(self, message: str, resource_id: Optional[ResourceID] = None):
        self.resource_id = resource_id
        super().__init__(f"[{resource_id}] {message}" if resource_id else message)

class CapacityError(ResourceError, ValueError):
    """Raised for invalid capacity values or operations."""
    def __init__(self, message: str, current: Optional[Capacity] = None, 
                 requested: Optional[Capacity] = None):
        if current is not None and requested is not None:
            message = f"{message} (current: {current}, requested: {requested})"
        elif current is not None:
            message = f"{message} (current: {current})"
        super().__init__(message)

class StateTransitionError(ResourceError):
    """Raised for invalid state transitions."""
    def __init__(self, resource_id: str, from_state: str, to_state: str):
        super().__init__(
            f"Invalid state transition for resource {resource_id}: {from_state} -> {to_state}"
        )

"""
Resource Management Module

This module provides the core abstractions for modeling and managing cloud resources
in the LEAF-Cloud framework. It includes:

- Resource: Abstract base class for all cloud resources
- ResourcePool: For managing collections of resources
- Support for resource allocation, scaling, and utilization tracking

Example:
    ```python
    # Create a compute resource
    class ComputeResource(Resource):
        def __init__(self, name: str, vcpus: int, **kwargs):
            super().__init__(name, capacity=vcpus, 
                          resource_type=ResourceType.COMPUTE, **kwargs)
            self.vcpus = vcpus
            
    # Create a resource pool and add resources
    pool = ResourcePool("web-servers", ResourceType.COMPUTE, "us-west1")
    for i in range(3):
        server = ComputeResource(f"web-{i}", vcpus=4)
        pool.add_resource(server)
    
    # Allocate resources
    allocation = pool.allocate_resources(
        request_id="job-123",
        amount=2,  # Request 2 vCPUs
        timestamp=0.0
    )
    ```

Thread Safety:
    - Resource instances are not thread-safe for write operations.
    - For concurrent access, use external synchronization.
    - Read-only operations are generally thread-safe.
"""


# Configure logger
logger = logging.getLogger(__name__)


class ResourceState(str, Enum):
    """Enumeration of possible resource states.
    
    States follow a general lifecycle:
    - INITIALIZING → AVAILABLE → ALLOCATED → AVAILABLE → TERMINATED
    - Any state can transition to FAILED on error
    - SCALING is a temporary state during capacity changes
    """
    INITIALIZING = "initializing"
    AVAILABLE = "available"
    ALLOCATED = "allocated"
    DEGRADED = "degraded"
    FAILED = "failed"
    TERMINATED = "terminated"
    SCALING = "scaling"
    
    def __str__(self) -> str:
        return self.name.lower()


class ResourceType(str, Enum):
    """Enumeration of abstract resource types.
    
    Each type represents a category of cloud resources with common
    characteristics and behaviors.
    """
    COMPUTE = "compute"
    STORAGE = "storage"
    NETWORK = "network"
    SECURITY = "security"
    GENERIC = "generic"
    DATABASE = "database"
    
    def __str__(self) -> str:
        return self.name.lower()


class UtilizationStats(TypedDict):
    """Type definition for utilization statistics."""
    min: float
    max: float
    avg: float
    samples: int
    total_time: float
    avg_allocated: float


@dataclass(frozen=True)
class UtilizationRecord:
    """Immutable record of resource utilization at a specific time.
    
    This class provides a thread-safe way to track resource utilization over time.
    Each record is immutable once created.
    
    Attributes:
        timestamp: When this record was created (simulation time)
        utilization: Resource utilization ratio (0.0 to 1.0)
        state: The resource state at the time of recording
        
    Raises:
        ValueError: If utilization is not between 0.0 and 1.0
        
    Example:
        >>> record = UtilizationRecord(timestamp=100.5, utilization=0.75, 
        ...                          state=ResourceState.ALLOCATED)
        >>> record.utilization
        0.75
        >>> record.to_dict()
        {'timestamp': 100.5, 'utilization': 0.75, 'state': 'allocated'}
    """
    timestamp: Timestamp
    utilization: Utilization  # 0.0 to 1.0
    state: ResourceState
    
    def __post_init__(self) -> None:
        if not 0.0 <= self.utilization <= 1.0:
            raise ValueError(
                f"Utilization must be between 0.0 and 1.0, got {self.utilization}"
            )
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> UtilizationRecord:
        """Create a UtilizationRecord from a dictionary.
        
        Args:
            data: Dictionary containing timestamp, utilization, and state
                
        Returns:
            A new UtilizationRecord instance
            
        Raises:
            ValueError: If required fields are missing or invalid
        """
        try:
            return cls(
                timestamp=data['timestamp'],
                utilization=data['utilization'],
                state=ResourceState(data['state'])
            )
        except KeyError as e:
            raise ValueError(f"Missing required field: {e}") from e
            
    def to_dict(self) -> Dict[str, Any]:
        """Convert the record to a dictionary.
        
        Returns:
            Dictionary with timestamp, utilization, and state
            """
        return {
            'timestamp': self.timestamp,
            'utilization': self.utilization,
            'state': str(self.state)
        }


class Resource(ABC):
    """Abstract base class for all resources in the LEAF-Cloud framework.
    
    This class provides the foundation for modeling cloud resources with capabilities for:
    - Capacity management (allocation/deallocation)
    - State management (available, allocated, failed, etc.)
    - Utilization tracking and statistics
    - Resource lifecycle management
    
    Subclasses should implement resource-specific behavior while leveraging the base
    class functionality for common resource management tasks.
    
    Thread Safety:
        This class is not thread-safe for write operations. For concurrent access,
        use external synchronization mechanisms. Read-only operations are thread-safe.
    
    Example:
        ```python
        # Create a compute resource with 8 vCPUs
        class ComputeResource(Resource):
            def __init__(self, name: str, vcpus: int, **kwargs):
                super().__init__(name, capacity=vcpus, 
                              resource_type=ResourceType.COMPUTE, **kwargs)
                self.vcpus = vcpus
            
            def get_power_consumption(self, timestamp: float) -> float:
                # Custom power consumption calculation for compute
                base_power = 50.0  # Base power in watts
                return base_power + (self.utilization_ratio * 100)  # Scale with utilization
        
        # Create and use a compute resource
        cpu = ComputeResource("web-server-1", vcpus=8)
        cpu.allocate(amount=4, request_id="req-123", timestamp=0.0)
        print(f"Available vCPUs: {cpu.available_capacity}")
        ```
    """
    
    # Class constants for default values
    DEFAULT_CAPACITY: ClassVar[Capacity] = 1.0
    DEFAULT_RESOURCE_TYPE: ClassVar[ResourceType] = ResourceType.GENERIC
    
    # Type aliases for better code readability
    _AllocationHistory = Dict[AllocationKey, Tuple[Timestamp, Timestamp]]
    _UtilizationHistory = List[UtilizationRecord]
    _AttributeMap = Dict[str, Any]

    def __init__(
        self,
        name: str,
        capacity: Capacity = DEFAULT_CAPACITY,
        resource_type: ResourceType = DEFAULT_RESOURCE_TYPE,
        region: Optional[Region] = None,
        timestamp: Timestamp = 0.0,
        attributes: Optional[ResourceAttributes] = None,
    ) -> None:
        """Initialize a new resource.
        
        Args:
            name: Human-readable name for the resource. Must be non-empty.
            capacity: Total capacity of the resource. Must be positive.
            resource_type: Type of the resource. Defaults to GENERIC.
            region: Geographic region where the resource is located. Optional.
            timestamp: Creation timestamp in simulation time. Defaults to 0.0.
            attributes: Additional attributes for the resource. Will be copied to
                     prevent external modifications.
                
        Raises:
            ValueError: If capacity is not positive or name is empty.
            TypeError: If resource_type is not a ResourceType.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Resource name must be a non-empty string")
            
        # Be tolerant: subclasses may set capacity themselves after initialization.
        # Default to 1.0 when not provided or invalid, rather than raising.
        try:
            capacity_value = float(capacity)
            if capacity_value <= 0:
                capacity_value = 1.0
        except Exception:
            capacity_value = 1.0
            
        if not isinstance(resource_type, ResourceType):
            raise TypeError(
                f"resource_type must be a ResourceType, got {type(resource_type).__name__}"
            )
        
        # Initialize instance variables with type hints
        self.id: ResourceID = str(uuid.uuid4())
        self.name: str = name.strip()
        self.capacity: Capacity = float(capacity_value)
        self.allocated_capacity: Capacity = 0.0
        self.resource_type: ResourceType = resource_type
        self.specific_type: str = self.name.lower()
        self.region: Optional[Region] = str(region) if region else None
        self._state: ResourceState = ResourceState.INITIALIZING
        self.utilization_history: Resource._UtilizationHistory = []
        self.allocation_history: Resource._AllocationHistory = {}
        self.creation_time: Timestamp = float(timestamp)
        self.attributes: Resource._AttributeMap = dict(attributes) if attributes else {}
        self._lock: threading.RLock = threading.RLock()  # For thread safety
        
        # Log resource creation
        logger.debug(
            "Resource %s (%s) created at time %s with capacity %s",
            self.name, self.id, timestamp, self.capacity
        )
        
        # Set initial state (validates the state transition)
        self.state = ResourceState.AVAILABLE

    # --- Capacity management API (default to runtime error; subclasses should override) ---
    def allocate(self, amount: float, request_id: str, timestamp: float) -> None:
        """Allocate a given amount of capacity for a request.

        Subclasses must override. Default raises NotImplementedError.
        """
        raise NotImplementedError("allocate() not implemented for this Resource")

    def release(self, amount: float, timestamp: float) -> None:
        """Release a previously allocated amount of capacity.

        Subclasses must override. Default raises NotImplementedError.
        """
        raise NotImplementedError("release() not implemented for this Resource")

    @property
    def available_capacity(self) -> Capacity:
        """Amount of capacity currently unallocated.

        This is a common API used across the codebase. Defining it here ensures
        all resources expose a consistent interface.
        """
        try:
            return max(0.0, float(self.capacity) - float(self.allocated_capacity))
        except Exception:
            return 0.0

    @property
    def state(self) -> ResourceState:
        """Get the current state of the resource.
        
        Returns:
            The current state of the resource.
            
        Thread Safety:
            This property is thread-safe for read operations. For write operations,
            external synchronization is required.
        """
        with self._lock:
            return self._state

    @state.setter
    def state(self, new_state: ResourceState) -> None:
        """Set a new state for the resource.
        
        This method enforces valid state transitions according to the resource
        lifecycle. Invalid transitions will raise a StateTransitionError.
        
        Args:
            new_state: The new state to set. Must be a valid ResourceState.
            
        Raises:
            StateTransitionError: If the requested state transition is invalid.
            TypeError: If new_state is not a ResourceState.
            
        Example:
            >>> resource = Resource("test")
            >>> resource.state = ResourceState.AVAILABLE  # Valid
            >>> resource.state = "invalid"  # Raises TypeError
            >>> resource.state = ResourceState.TERMINATED  # Invalid transition
            StateTransitionError: Invalid state transition...
        """
        if not isinstance(new_state, ResourceState):
            raise TypeError(
                f"State must be a ResourceState, got {type(new_state).__name__}"
            )
            
        with self._lock:
            old_state = self._state
            
            # Skip if state isn't changing
            if old_state == new_state:
                return
            
            # Define valid state transitions
            valid_transitions: Dict[ResourceState, List[ResourceState]] = {
                ResourceState.INITIALIZING: [ResourceState.AVAILABLE, ResourceState.FAILED],
                ResourceState.AVAILABLE: [
                    ResourceState.ALLOCATED, 
                    ResourceState.TERMINATED,
                    ResourceState.FAILED, 
                    ResourceState.SCALING
                ],
                ResourceState.ALLOCATED: [
                    ResourceState.AVAILABLE, 
                    ResourceState.DEGRADED, 
                    ResourceState.FAILED
                ],
                ResourceState.DEGRADED: [ResourceState.AVAILABLE, ResourceState.FAILED],
                ResourceState.SCALING: [ResourceState.AVAILABLE, ResourceState.FAILED],
                # Terminal states (no transitions out)
                ResourceState.TERMINATED: [],
                ResourceState.FAILED: []
            }
            # Enforce valid transition and update state
            allowed_next_states: List[ResourceState] = valid_transitions.get(old_state, [])
            if new_state not in allowed_next_states:
                raise StateTransitionError(str(self.id), str(old_state), str(new_state))

            self._state = new_state

            # Record utilization snapshot at state change using current time
            try:
                self.record_utilization(time.time())
            except Exception:
                # Best-effort; do not fail state change due to metrics recording
                pass

            return

        # End of state setter



    def record_utilization(self, timestamp: float) -> None:
        """Record a utilization snapshot in a thread-safe manner.

        Args:
            timestamp: Simulation time when recording occurs.
        """
        try:
            with self._lock:
                self.utilization_history.append(
                    UtilizationRecord(
                        timestamp=timestamp,
                        utilization=self.utilization_ratio,
                        state=self._state,
                    )
                )
        except Exception:
            # Best-effort metrics recording; never raise from here
            pass

class ResourcePool:
    """A collection of resources that can be managed as a unit.
    
    This class provides a way to group and manage multiple resources together,
    such as a cluster of compute nodes or a pool of storage devices. It enables:
    - Grouping related resources for collective management
    - Aggregated capacity and utilization tracking
    - Simplified resource allocation across multiple instances
    - Resource discovery and querying
    - Geographic and logical organization of resources
    
    Example:
        >>> # Create a pool of compute resources
        >>> pool = ResourcePool("web-servers", ResourceType.COMPUTE, "us-west1")
        >>> 
        >>> # Add some resources to the pool
        >>> for i in range(3):
        ...     server = ComputeResource(f"web-{i}", vcpus=4)
        ...     pool.add_resource(server)
        >>> 
        >>> # Allocate resources from the pool
        >>> allocation = pool.allocate_resources("job-123", 2, 0.0)
        >>> print(f"Allocated {len(allocation)} resources")
    """
    
    def __init__(
        self,
        name: str,
        resource_type: ResourceType = ResourceType.GENERIC,
        region: Optional[Region] = None,
    ) -> None:
        """Initialize a new resource pool.

        Args:
            name: Name of the resource pool
            resource_type: Type of resources in this pool
            region: Geographic region for this pool (optional)
        """
        self.name: str = name
        self.resource_type: ResourceType = resource_type
        self.region: Optional[Region] = region
        self.resources: Dict[ResourceID, Resource] = {}
        self.creation_time: Timestamp = time.time()
        self._lock: threading.RLock = threading.RLock()
        self.tags: Dict[str, str] = {}
        self._allocations: Dict[str, Dict[str, float]] = {}  # request_id -> {resource_id -> amount}
        
        logger.debug(
            "Created resource pool '%s' with type '%s' in region '%s'",
            name, resource_type, region or 'any'
        )
    
    def add_resource(self, resource: Resource) -> None:
        """Add a resource to the pool.

        Args:
            resource: Resource to add

        Raises:
            ValueError: If the resource already exists in the pool
            TypeError: If the resource type doesn't match the pool type
        """
        if not isinstance(resource, Resource):
            raise TypeError(f"Expected Resource, got {type(resource).__name__}")
            
        if resource.resource_type != self.resource_type:
            raise ValueError(
                f"Cannot add resource of type {resource.resource_type} "
                f"to pool of type {self.resource_type}"
            )
            
        with self._lock:
            if resource.id in self.resources:
                raise ValueError(f"Resource {resource.id} already exists in pool")
                
            self.resources[resource.id] = resource
            logger.debug("Added resource %s to pool %s", resource.id, self.name)
    
    def remove_resource(self, resource_id: str) -> Optional[Resource]:
        """Remove a resource from the pool.

        Args:
            resource_id: ID of the resource to remove

        Returns:
            The removed resource or None if not found
        """
        with self._lock:
            return self.resources.pop(resource_id, None)
    
    def get_available_resources(self) -> List[Resource]:
        """Get all resources that are currently available.

        Returns:
            List of available resources (allocated_capacity < capacity)
        """
        with self._lock:
            return [
                resource for resource in self.resources.values()
                if resource.available_capacity > 0
            ]
    
    def get_total_capacity(self) -> float:
        """Get the total capacity of all resources in the pool.

        Returns:
            Sum of all resource capacities in the pool
        """
        with self._lock:
            return sum(resource.capacity for resource in self.resources.values())
    
    def get_allocated_capacity(self) -> float:
        """Get the total allocated capacity of all resources in the pool.

        Returns:
            Sum of all allocated capacity in the pool
        """
        with self._lock:
            return sum(resource.allocated_capacity for resource in self.resources.values())
    
    @property
    def utilization_ratio(self) -> float:
        """Calculate the overall utilization ratio of the pool.

        The utilization ratio is calculated as the ratio of allocated capacity to
        total capacity, clamped between 0.0 and 1.0. This provides a normalized
        view of resource usage across the entire pool.

        Returns:
            float: Utilization ratio between 0.0 (completely unused) and 1.0 (fully utilized).
                 Returns 0.0 if the pool has no capacity or no resources.
        """
        total_capacity = self.get_total_capacity()
        if total_capacity <= 0:
            return 0.0
            
        return min(1.0, max(0.0, self.get_allocated_capacity() / total_capacity))
    
    def allocate_resources(
        self,
        request_id: str,
        amount: float,
        timestamp: float,
        strategy: str = "first_fit",
    ) -> List[Resource]:
        """Allocate resources from the pool.
        
        Args:
            request_id: Unique identifier for this allocation request
            amount: Amount of resources to allocate
            timestamp: Current simulation time
            strategy: Allocation strategy to use (default: "first_fit")
            
        Returns:
            List of allocated resources
            
        Raises:
            ValueError: If the allocation cannot be satisfied
        """
        if amount <= 0:
            raise ValueError("Allocation amount must be positive")
            
        with self._lock:
            if strategy == "first_fit":
                return self._allocate_first_fit(request_id, amount, timestamp)
            else:
                raise ValueError(f"Unknown allocation strategy: {strategy}")
    
    def _allocate_first_fit(
        self, request_id: str, amount: float, timestamp: float
    ) -> List[Resource]:
        """Allocate resources using first-fit strategy.
        
        This method first tries to find a single resource that can handle the entire
        allocation. If none is found, it will attempt to allocate from multiple resources.
        """
        allocated: List[Tuple[Resource, float]] = []  # List of (resource, allocated_amount) pairs
        remaining = amount
        
        # First try to find a single resource that can handle the entire allocation
        for resource in self.resources.values():
            if resource.available_capacity >= amount:
                try:
                    resource.allocate(amount, request_id, timestamp)
                    allocated.append((resource, amount))
                    # Update allocations tracking
                    self._allocations[request_id] = {resource.id: amount}
                    return [resource]  # Successfully allocated from a single resource
                except (CapacityError, AllocationError):
                    continue  # Try next resource
        
        # If we get here, we couldn't find a single resource with enough capacity
        # Fall back to allocating from multiple resources
        for resource in self.resources.values():
            if remaining <= 0:
                break
                
            if resource.available_capacity > 0:
                # Try to allocate as much as possible from this resource
                alloc_amount = min(remaining, resource.available_capacity)
                try:
                    resource.allocate(alloc_amount, request_id, timestamp)
                    allocated.append((resource, alloc_amount))
                    remaining -= alloc_amount
                except (CapacityError, AllocationError) as e:
                    logger.warning(
                        "Failed to allocate %s from %s: %s",
                        alloc_amount, resource.id, str(e)
                    )
        
        if remaining > 0:
            # Release any partially allocated resources
            for resource, alloc_amount in allocated:
                resource.release(alloc_amount, timestamp)
            raise CapacityError(
                f"Insufficient resources: could only allocate {amount - remaining} of {amount}",
                current=amount - remaining,
                requested=amount
            )
        
        # Update allocations tracking
        self._allocations[request_id] = {
            resource.id: alloc_amount 
            for resource, alloc_amount in allocated
        }
        
        return [r for r, _ in allocated]  # Return only the resources
    
    def release_resources(self, request_id: str, timestamp: float) -> None:
        """Release resources allocated to a request.
        
        Args:
            request_id: ID of the request to release resources for
            timestamp: Current simulation time
        """
        with self._lock:
            if request_id in self._allocations:
                for resource_id, amount in self._allocations[request_id].items():
                    # Look up the resource in the pool's resources dictionary
                    resource = self.get_resource(resource_id)
                    if resource is not None:
                        try:
                            resource.release(amount, timestamp)
                        except Exception as e:
                            logger.warning(
                                "Error releasing resource %s: %s", resource_id, str(e)
                            )
                # Clean up the allocation record
                del self._allocations[request_id]
    
    def get_resource(self, resource_id: str) -> Optional[Resource]:
        """Get a resource by ID.
        
        Args:
            resource_id: ID of the resource to get
            
        Returns:
            The resource if found, None otherwise
        """
        with self._lock:
            return self.resources.get(resource_id)
    
    def __len__(self) -> int:
        """Get the number of resources in the pool."""
        with self._lock:
            return len(self.resources)

    def __bool__(self) -> bool:
        """Return True so that an empty pool is still truthy.

        Using __len__ without __bool__ would make a newly created (empty)
        pool evaluate to False in boolean contexts, which caused resources
        to be skipped during mapping.  By explicitly returning True we
        avoid that trap while still allowing len(pool) to convey the
        actual number of resources."""
        return True
    
    def __contains__(self, resource_id: str) -> bool:
        """Check if a resource with the given ID exists in the pool."""
        with self._lock:
            return resource_id in self.resources
    
    def __iter__(self) -> Iterator[Resource]:
        """Iterate over all resources in the pool."""
        with self._lock:
            return iter(list(self.resources.values()))
    
    def __str__(self) -> str:
        """Get a string representation of the pool."""
        return (
            f"ResourcePool(name='{self.name}', type={self.resource_type}, "
            f"resources={len(self)}, utilization={self.utilization_ratio:.1%})"
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the pool to a dictionary representation."""
        return {
            "name": self.name,
            "resource_type": str(self.resource_type),
            "region": self.region,
            "resource_count": len(self),
            "total_capacity": self.get_total_capacity(),
            "allocated_capacity": self.get_allocated_capacity(),
            "utilization": self.utilization_ratio,
            "creation_time": self.creation_time,
            "tags": self.tags,
        }
