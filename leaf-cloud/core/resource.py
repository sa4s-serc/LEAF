from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, List, Optional, Union, Tuple
from datetime import datetime
import uuid
import logging

"""
core/resource.py

This module defines the abstract resource class hierarchy for the LEAF-Cloud framework.
It provides the base classes for modeling cloud resources, managing their states,
and tracking their utilization throughout simulation.
"""


# Configure logger
logger = logging.getLogger(__name__)


class ResourceState(Enum):
    """Enumeration of possible resource states"""
    INITIALIZING = 'initializing'
    AVAILABLE = 'available'
    ALLOCATED = 'allocated'
    DEGRADED = 'degraded'
    FAILED = 'failed'
    TERMINATED = 'terminated'
    SCALING = 'scaling'  


class ResourceType(Enum):
    """Enumeration of abstract resource types"""
    COMPUTE = 'compute'
    STORAGE = 'storage'
    NETWORK = 'network'
    SECURITY = 'security'
    GENERIC = 'generic'
    DATABASE  = "database"


class UtilizationRecord:
    """Class to store a single utilization record for a resource"""
    def __init__(self, timestamp: float, utilization: float, state: ResourceState):
        self.timestamp = timestamp
        self.utilization = utilization
        self.state = state
    def __repr__(self) -> str:
        return f"UtilizationRecord(timestamp={self.timestamp}, utilization={self.utilization:.2f}, state={self.state.value})"


class Resource(ABC):
    """Abstract base class for all resources in the LEAF-Cloud framework."""
    
    def __init__(self, name: str, capacity: float = 1.0, resource_type: ResourceType = ResourceType.GENERIC, region: Optional[str] = None):
        self.id = str(uuid.uuid4())
        self.name = name
        self.capacity = capacity
        self.allocated_capacity = 0.0
        self.resource_type = resource_type
        self.specific_type = name.lower() # Default specific type
        self.region = region
        self._state = ResourceState.INITIALIZING
        self.utilization_history: List[UtilizationRecord] = []
        self.allocation_history: Dict[str, Tuple[float, float]] = {}
        self.creation_time = datetime.now()
        logger.info(f"Resource {self.name} ({self.id}) created with capacity {self.capacity}")
        self.state = ResourceState.AVAILABLE
    
    @property
    def state(self) -> ResourceState:
        return self._state
    
    @state.setter
    def state(self, new_state: ResourceState) -> None:
        if new_state != self._state:
            logger.info(f"Resource {self.name} state changed: {self._state.value} -> {new_state.value}")
            self._state = new_state
    
    @property
    def available_capacity(self) -> float:
        return max(0.0, self.capacity - self.allocated_capacity)
    
    @property
    def utilization_ratio(self) -> float:
        if self.capacity <= 0: return 1.0
        return self.allocated_capacity / self.capacity
    
    def allocate(self, amount: float, token_id: str, timestamp: float) -> bool:
        self.allocated_capacity += amount
        self.allocation_history[token_id] = (timestamp, amount)
        if self.allocated_capacity > self.capacity:
            logger.warning(f"Resource {self.name} overloaded by {self.allocated_capacity - self.capacity:.2f} units for token {token_id}")
            self.state = ResourceState.DEGRADED
        elif self.state == ResourceState.AVAILABLE and self.allocated_capacity > 0:
            self.state = ResourceState.ALLOCATED
        self._record_utilization(timestamp)
        return True
    
    def deallocate(self, token_id: str, timestamp: float) -> float:
        if token_id not in self.allocation_history: return 0.0
        _, amount = self.allocation_history.pop(token_id)
        self.allocated_capacity = max(0.0, self.allocated_capacity - amount)
        if self.allocated_capacity == 0 and self.state == ResourceState.ALLOCATED:
            self.state = ResourceState.AVAILABLE
        self._record_utilization(timestamp)
        return amount
    
    def _record_utilization(self, timestamp: float) -> None:
        record = UtilizationRecord(timestamp=timestamp, utilization=self.utilization_ratio, state=self.state)
        self.utilization_history.append(record)
    
    def get_average_utilization(self, start_time: Optional[float] = None, end_time: Optional[float] = None) -> float:
        """
        Calculate average utilization over a time period.
        
        Args:
            start_time: Start of time period (default: beginning of simulation)
            end_time: End of time period (default: latest record)
            
        Returns:
            Average utilization as a ratio between 0.0 and 1.0
        """
        if not self.utilization_history:
            return 0.0
            
        # Filter records by time range if specified
        records = self.utilization_history
        if start_time is not None:
            records = [r for r in records if r.timestamp >= start_time]
        if end_time is not None:
            records = [r for r in records if r.timestamp <= end_time]
            
        if not records:
            return 0.0
            
        # Calculate weighted average based on time intervals
        total_utilization = 0.0
        total_time = 0.0
        
        for i in range(len(records) - 1):
            time_interval = records[i+1].timestamp - records[i].timestamp
            total_utilization += records[i].utilization * time_interval
            total_time += time_interval
            
        # Return average or 0 if no valid time intervals
        return total_utilization / total_time if total_time > 0 else 0.0
    
    @abstractmethod
    def get_energy_consumption(self, timestamp: float) -> float:
        """
        Calculate energy consumption at the given timestamp.
        Must be implemented by concrete subclasses.
        
        Args:
            timestamp: Simulation time to calculate energy consumption
            
        Returns:
            Energy consumption value for the resource
        """
        pass
    
    def reset(self) -> None:
        """Reset the resource to its initial state."""
        self.allocated_capacity = 0.0
        self.state = ResourceState.AVAILABLE
        self.utilization_history = []
        self.allocation_history = {}
        logger.info(f"Resource {self.name} reset to initial state")
        
    def __str__(self) -> str:
        return f"{self.name} ({self.resource_type.value}): {self.allocated_capacity}/{self.capacity} [{self.state.value}]"
    
    def __repr__(self) -> str:
        return (f"Resource(id={self.id}, name={self.name}, type={self.resource_type.value}, "
                f"capacity={self.capacity}, allocated={self.allocated_capacity}, "
                f"state={self.state.value})")


class ResourcePool:
    """
    A collection of resources that can be managed as a unit.
    Useful for representing resource groups like clusters or pools.
    """
    
    def __init__(self, name: str, resource_type: ResourceType = ResourceType.GENERIC):
        """
        Initialize a new resource pool.
        
        Args:
            name: Name of the resource pool
            resource_type: Type of resources in this pool
        """
        self.name = name
        self.resource_type = resource_type
        self.resources: Dict[str, Resource] = {}
    
    def add_resource(self, resource: Resource) -> None:
        """
        Add a resource to the pool.
        
        Args:
            resource: Resource to add
        """
        self.resources[resource.id] = resource
        logger.debug(f"Added resource {resource.name} to pool {self.name}")
    
    def remove_resource(self, resource_id: str) -> Optional[Resource]:
        """
        Remove a resource from the pool.
        
        Args:
            resource_id: ID of the resource to remove
            
        Returns:
            The removed resource or None if not found
        """
        if resource_id in self.resources:
            resource = self.resources.pop(resource_id)
            logger.debug(f"Removed resource {resource.name} from pool {self.name}")
            return resource
        return None
    
    def get_available_resources(self) -> List[Resource]:
        """
        Get all resources that are currently available.
        
        Returns:
            List of available resources
        """
        return [r for r in self.resources.values() if r.state == ResourceState.AVAILABLE]
    
    def get_total_capacity(self) -> float:
        """
        Get the total capacity of all resources in the pool.
        
        Returns:
            Total capacity value
        """
        return sum(r.capacity for r in self.resources.values())
    
    def get_allocated_capacity(self) -> float:
        """
        Get the total allocated capacity of all resources in the pool.
        
        Returns:
            Total allocated capacity value
        """
        return sum(r.allocated_capacity for r in self.resources.values())
    
    @property
    def utilization_ratio(self) -> float:
        """
        Calculate the overall utilization ratio of the pool.
        
        Returns:
            Overall utilization as a ratio between 0.0 and 1.0
        """
        total = self.get_total_capacity()
        if total <= 0:
            return 0.0
        return min(1.0, self.get_allocated_capacity() / total)
    
    def __len__(self) -> int:
        return len(self.resources)
    
    def __str__(self) -> str:
        return f"ResourcePool {self.name}: {len(self)} resources, {self.utilization_ratio:.2f} utilization"