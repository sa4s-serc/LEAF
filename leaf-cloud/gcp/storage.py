from enum import Enum
from typing import Dict, List, Optional, Union, Tuple, override
import logging
import math
from ..core.resource import Resource, ResourceType, ResourceState

"""
gcp/storage.py

This module defines various storage resources available in GCP.
It models specific storage services like Cloud Storage, Persistent Disks,
Cloud SQL, Bigtable, and Firestore with their respective properties.
"""

logger = logging.getLogger(__name__)


class StorageClass(Enum):
    """Enumeration of Google Cloud Storage classes"""
    STANDARD = 'STANDARD'
    NEARLINE = 'NEARLINE'
    COLDLINE = 'COLDLINE'
    ARCHIVE = 'ARCHIVE'


class DiskType(Enum):
    """Enumeration of GCP Persistent Disk types"""
    STANDARD = 'pd-standard'
    BALANCED = 'pd-balanced'
    SSD = 'pd-ssd'
    EXTREME = 'pd-extreme'


class DatabaseTier(Enum):
    """Enumeration of database performance tiers"""
    BASIC = 'basic'
    STANDARD = 'standard'
    HIGH_AVAILABILITY = 'high-availability'
    ENTERPRISE = 'enterprise'


class Storage(Resource):
    """
    Base class for all GCP storage resources.
    Extends Resource with storage-specific attributes and behavior.
    """
    
    def __init__(self, 
                 name: str, 
                 capacity_gb: float, 
                 region: str,
                 iops: int = 0,
                 throughput_mbps: float = 0):
        """
        Initialize a storage resource.
        
        Args:
            name: Unique name for the storage resource
            capacity_gb: Storage capacity in GB
            region: GCP region where the storage is deployed
            iops: Input/output operations per second capacity
            throughput_mbps: Throughput in MB per second
        """
        super().__init__(name=name, 
                         capacity=capacity_gb, 
                         resource_type=ResourceType.STORAGE,
                         region=region)
        
        self.capacity_gb = capacity_gb
        self.iops = iops
        self.throughput_mbps = throughput_mbps
        self.allocated_iops = 0.0
        self.allocated_throughput = 0.0
        
        # Energy consumption parameters
        self.base_power_watts = 0.0  # Will be set by subclasses
        self.power_per_gb = 0.0      # Will be set by subclasses
        self.power_per_iops = 0.0    # Will be set by subclasses
    
    def can_allocate(self, amount: float, requested_iops: int = 0, requested_throughput: float = 0) -> bool:
        """
        Check if the requested storage resources can be allocated.
        
        Args:
            amount: The amount of storage capacity requested in GB
            requested_iops: The IOPS capacity requested
            requested_throughput: The throughput capacity requested in MB/s
            
        Returns:
            True if the resource can allocate the requested amounts, False otherwise
        """
        basic_allocation = super().can_allocate(amount)
        iops_available = self.iops == 0 or (self.allocated_iops + requested_iops <= self.iops)
        throughput_available = self.throughput_mbps == 0 or (self.allocated_throughput + requested_throughput <= self.throughput_mbps)
        
        return basic_allocation and iops_available and throughput_available
    
    def allocate(self, amount: float, token_id: str, timestamp: float, 
                requested_iops: int = 0, requested_throughput: int = 0) -> bool:
        """
        Allocate the specified amount of storage resources.
        
        Args:
            amount: Amount of storage capacity to allocate in GB
            token_id: ID of the token requesting allocation
            timestamp: Simulation time when allocation occurs
            requested_iops: The IOPS capacity requested
            requested_throughput: The throughput capacity requested in MB/s
            
        Returns:
            True if allocation was successful, False otherwise
        """
        if not self.can_allocate(amount, requested_iops, requested_throughput):
            logger.warning(f"Storage resource {self.name} failed to allocate {amount}GB, "
                          f"{requested_iops} IOPS, {requested_throughput}MB/s for token {token_id}")
            return False
            
        self.allocated_capacity += amount
        self.allocated_iops += requested_iops
        self.allocated_throughput += requested_throughput
        self.allocation_history[token_id] = (timestamp, amount)
        
        # Update state if first allocation
        if self.state == ResourceState.AVAILABLE and self.allocated_capacity > 0:
            self.state = ResourceState.ALLOCATED
            
        # Record utilization
        self._record_utilization(timestamp)
        
        logger.debug(f"Storage resource {self.name} allocated {amount}GB for token {token_id}, "
                    f"utilization now at {self.utilization_ratio:.2f}")
        return True
    
    def deallocate(self, token_id: str, timestamp: float) -> float:
        """
        Deallocate storage resources allocated to a specific token.
        
        Args:
            token_id: ID of the token to deallocate
            timestamp: Simulation time when deallocation occurs
            
        Returns:
            Amount of capacity that was deallocated in GB
        """
        # Get the token's allocation details
        if token_id not in self.allocation_history:
            logger.warning(f"Attempt to deallocate token {token_id} from storage {self.name}, "
                          f"but token was not allocated")
            return 0.0
        
        # For now, we'll assume IOPS and throughput are tracked separately in subclasses
        # or in a more complex allocation_history structure in real implementation
        amount_deallocated = super().deallocate(token_id, timestamp)
        
        return amount_deallocated
    
    def get_energy_consumption(self, timestamp: float) -> float:
        """
        Calculate energy consumption at the given timestamp.
        
        For storage resources, energy consumption is modeled as:
        E = base_power + (power_per_gb * allocated_gb) + (power_per_iops * allocated_iops)
        
        Args:
            timestamp: Simulation time to calculate energy consumption
            
        Returns:
            Energy consumption in Watt-hours for the resource
        """
        # Get the utilization record closest to the timestamp
        utilization = 0.0
        for record in reversed(self.utilization_history):
            if record.timestamp <= timestamp:
                utilization = record.utilization
                break
        
        # Calculate energy consumption
        energy_gb = self.power_per_gb * self.capacity_gb * utilization
        energy_iops = self.power_per_iops * self.allocated_iops
        total_energy = self.base_power_watts + energy_gb + energy_iops
        
        return total_energy


class CloudStorage(Storage):
    """
    Google Cloud Storage bucket implementation.
    """
    
    def __init__(self, 
                 name: str, 
                 capacity_gb: float,
                 region: str,
                 storage_class: StorageClass = StorageClass.STANDARD):
        """
        Initialize a Cloud Storage bucket.
        
        Args:
            name: Bucket name
            capacity_gb: Storage capacity in GB
            region: GCP region where the bucket is deployed
            storage_class: Storage class (STANDARD, NEARLINE, COLDLINE, ARCHIVE)
        """
        super().__init__(name=name, 
                         capacity_gb=capacity_gb, 
                         region=region)
        
        self.storage_class = storage_class
        # Set energy parameters based on storage class
        self._set_energy_parameters()
        
    def _set_energy_parameters(self):
        """Set energy consumption parameters based on storage class"""
        # These values are placeholders and should be calibrated with real-world data
        if self.storage_class == StorageClass.STANDARD:
            self.base_power_watts = 0.1  # Watts (very small for a logical bucket representation)
            self.power_per_gb = 0.0005 # Watts per GB (HDD array, considering efficiency)
            self.power_per_iops = 0.00001 # Watts per IOPS
        elif self.storage_class == StorageClass.NEARLINE:
            self.base_power_watts = 0.08
            self.power_per_gb = 0.0015
            self.power_per_iops = 0.0002
        elif self.storage_class == StorageClass.COLDLINE:
            self.base_power_watts = 0.05
            self.power_per_gb = 0.001
            self.power_per_iops = 0.0003
        elif self.storage_class == StorageClass.ARCHIVE:
            self.base_power_watts = 0.01
            self.power_per_gb = 0.0001
            self.power_per_iops = 0.00002 # Maybe slightly more for retrieval
        self.base_power_watts /= 1000.0
        self.power_per_gb     /= 1000.0
        self.power_per_iops   /= 1000.0
        
    def calculate_latency(self, operation_type: str, data_size_mb: float) -> float:
        """
        Calculate the latency for a storage operation.
        
        Args:
            operation_type: Type of operation (READ, WRITE)
            data_size_mb: Size of data being operated on in MB
            
        Returns:
            Estimated latency in milliseconds
        """
        # Base latency values (in ms) - these should be calibrated with real-world data
        base_latency = {
            "READ": {
                StorageClass.STANDARD: 10,
                StorageClass.NEARLINE: 50,
                StorageClass.COLDLINE: 200,
                StorageClass.ARCHIVE: 1000
            },
            "WRITE": {
                StorageClass.STANDARD: 20,
                StorageClass.NEARLINE: 30,
                StorageClass.COLDLINE: 40,
                StorageClass.ARCHIVE: 60
            }
        }
        
        # Calculate latency based on data size and utilization
        base = base_latency.get(operation_type, {}).get(self.storage_class, 20)
        size_factor = data_size_mb / 10  # Scale factor, assuming 10MB as reference size
        utilization_factor = 1 + self.utilization_ratio * 2  # Higher utilization = higher latency
        
        return base * size_factor * utilization_factor


class PersistentDisk(Storage):
    """
    Google Cloud Persistent Disk implementation.
    """
    
    def __init__(self, 
                 name: str, 
                 capacity_gb: float,
                 region: str,
                 disk_type: DiskType = DiskType.STANDARD,
                 iops: Optional[int] = None,
                 throughput_mbps: Optional[float] = None):
        """
        Initialize a Persistent Disk.
        
        Args:
            name: Disk name
            capacity_gb: Storage capacity in GB
            region: GCP region where the disk is deployed
            disk_type: Type of persistent disk
            iops: IOPS capacity (if not specified, will be set based on type and capacity)
            throughput_mbps: Throughput in MB per second (if not specified, will be set based on type and capacity)
        """
        # Set default IOPS and throughput based on disk type and capacity
        if iops is None or throughput_mbps is None:
            default_iops, default_throughput = self._get_default_performance(disk_type, capacity_gb)
            iops = iops or default_iops
            throughput_mbps = throughput_mbps or default_throughput
            
        super().__init__(name=name, 
                        capacity_gb=capacity_gb, 
                        region=region,
                        iops=iops,
                        throughput_mbps=throughput_mbps)
        
        self.disk_type = disk_type
        # Set energy parameters based on disk type
        self._set_energy_parameters()
    
    def _get_default_performance(self, disk_type: DiskType, capacity_gb: float) -> Tuple[int, float]:
        """
        Calculate default IOPS and throughput based on disk type and capacity.
        These values are based on GCP documentation but are simplified.
        
        Args:
            disk_type: The type of persistent disk
            capacity_gb: The capacity of the disk in GB
            
        Returns:
            Tuple of (IOPS, throughput_mbps)
        """
        if disk_type == DiskType.STANDARD:
            # Standard PD: 0.75 IOPS/GB, max 7,500 IOPS
            iops = min(int(0.75 * capacity_gb), 7500)
            # Standard PD: 0.12 MB/s/GB, max 240 MB/s
            throughput = min(0.12 * capacity_gb, 240)
        elif disk_type == DiskType.BALANCED:
            # Balanced PD: 6 IOPS/GB, max 15,000 IOPS
            iops = min(int(6 * capacity_gb), 15000)
            # Balanced PD: 0.28 MB/s/GB, max 1,200 MB/s
            throughput = min(0.28 * capacity_gb, 1200)
        elif disk_type == DiskType.SSD:
            # SSD PD: 30 IOPS/GB, max 60,000 IOPS
            iops = min(int(30 * capacity_gb), 60000)
            # SSD PD: 0.48 MB/s/GB, max 1,200 MB/s
            throughput = min(0.48 * capacity_gb, 1200)
        elif disk_type == DiskType.EXTREME:
            # Extreme PD: custom IOPS with minimums based on size
            min_iops = max(2500, int(capacity_gb * 2))
            iops = min(60000, min_iops)
            throughput = min(0.48 * capacity_gb, 1200)
        else:
            iops = 1000
            throughput = 100
            
        return iops, throughput
    
    def _set_energy_parameters(self):
        """Set energy consumption parameters based on disk type"""
        # These values are placeholders and should be calibrated with real-world data
        base_w, per_gb_w, per_iops_w = 0, 0, 0
        if self.disk_type == DiskType.STANDARD:
            self.base_power_watts = 1.0  # Idle power for a disk
            self.power_per_gb = 0.002 # Very small incremental per GB after base
            self.power_per_iops = 0.0001
        elif self.disk_type == DiskType.BALANCED:
            self.base_power_watts = 1.0
            self.power_per_gb = 0.0015
            self.power_per_iops = 0.0004
        elif self.disk_type == DiskType.SSD:
            self.base_power_watts = 0.5  # Idle for SSD
            self.power_per_gb = 0.001
            self.power_per_iops = 0.00005
        elif self.disk_type == DiskType.EXTREME:
            self.base_power_watts = 2.0
            self.power_per_gb = 0.0025
            self.power_per_iops = 0.0002
        self.base_power_watts = base_w / 1000.0
        self.power_per_gb = per_gb_w / 1000.0
        self.power_per_iops = per_iops_w / 1000.0
    
    def calculate_latency(self, operation_type: str, data_size_mb: float) -> float:
        """
        Calculate the latency for a disk operation.
        
        Args:
            operation_type: Type of operation (READ, WRITE)
            data_size_mb: Size of data being operated on in MB
            
        Returns:
            Estimated latency in milliseconds
        """
        # Base latency values (in ms)
        base_latency = {
            "READ": {
                DiskType.STANDARD: 15,
                DiskType.BALANCED: 10,
                DiskType.SSD: 5,
                DiskType.EXTREME: 2
            },
            "WRITE": {
                DiskType.STANDARD: 20,
                DiskType.BALANCED: 15,
                DiskType.SSD: 10,
                DiskType.EXTREME: 5
            }
        }
        
        # Calculate effective IOPS capacity based on current utilization
        effective_iops = self.iops * (1 - self.utilization_ratio)
        if effective_iops <= 0:
            effective_iops = 1  # Avoid division by zero
            
        # Basic latency calculation: data_size_in_blocks / effective_iops 
        block_size_kb = 4  # Assuming 4KB block size
        blocks = math.ceil((data_size_mb * 1024) / block_size_kb)
        operation_time_ms = (blocks / effective_iops) * 1000
        
        # Add base latency
        base = base_latency.get(operation_type, {}).get(self.disk_type, 10)
        
        return base + operation_time_ms


class CloudSQL(Storage):
    """
    Google Cloud SQL database implementation.
    """
    
    def __init__(self, 
                 name: str, 
                 capacity_gb: float,
                 region: str,
                 instance_type: str = "db-standard-1",
                 database_tier: DatabaseTier = DatabaseTier.STANDARD,
                 high_availability: bool = False):
        """
        Initialize a Cloud SQL instance.
        
        Args:
            name: Instance name
            capacity_gb: Storage capacity in GB
            region: GCP region where the instance is deployed
            instance_type: Machine type for the instance
            database_tier: Performance tier
            high_availability: Whether HA configuration is enabled
        """
        # Derive IOPS and throughput from the instance type and tier
        iops, throughput = self._calculate_performance(instance_type, database_tier, capacity_gb)
        
        super().__init__(
            name=name,
            capacity_gb=capacity_gb,
            region=region,
            iops=iops,
            throughput_mbps=throughput
        )
        self.resource_type = ResourceType.DATABASE
        
        self.instance_type = instance_type
        self.database_tier = database_tier
        self.high_availability = high_availability
        self.read_ops = 0
        self.write_ops = 0
        
        
        # Set energy parameters
        self._set_energy_parameters()
    def handle_query(self, token, read_mb: float = 0.002, write_mb: float = 0.001):
        """
        Increment IOPS and throughput counters to register utilization.
        This method is called by the Petri net transition action.
        """
        self.read_ops += 1
        self.write_ops += 1

    @property
    @override
    def utilization_ratio(self) -> float:
        """Override default utilization to be based on IOPS instead of storage capacity."""
        if self.iops <= 0:
            return 0.0
        current_iops = self.read_ops + self.write_ops
        # The orchestrator resets the op counts each interval, so this reflects current load.
        return min(1.0, current_iops / self.iops)

    @override
    def _record_utilization(self, timestamp: float) -> None:
        """Override to record utilization and then reset op counters for the next interval."""
        super()._record_utilization(timestamp)
        # Reset counters after recording so utilization reflects per-interval activity
        self.read_ops = 0
        self.write_ops = 0
    
    def _calculate_performance(self, instance_type: str, tier: DatabaseTier, capacity_gb: float) -> Tuple[int, float]:
        """
        Calculate IOPS and throughput based on instance type and tier.
        
        Args:
            instance_type: Machine type for the instance
            tier: Database performance tier
            capacity_gb: Storage capacity in GB
            
        Returns:
            Tuple of (IOPS, throughput_mbps)
        """
        # Extract vCPU count from instance type (e.g., "db-standard-2" has 2 vCPUs)
        try:
            vcpu = int(instance_type.split("-")[-1])
        except (ValueError, IndexError):
            vcpu = 1
            
        # Base performance values
        base_iops = 3000
        base_throughput = 60
        
        # Scale based on vCPUs
        vcpu_factor = vcpu / 2  # Normalized to 2 vCPUs as baseline
        
        # Adjust based on tier
        tier_multipliers = {
            DatabaseTier.BASIC: 0.5,
            DatabaseTier.STANDARD: 1.0,
            DatabaseTier.HIGH_AVAILABILITY: 1.2,
            DatabaseTier.ENTERPRISE: 1.5
        }
        tier_factor = tier_multipliers.get(tier, 1.0)
        
        # Calculate final values
        iops = int(base_iops * vcpu_factor * tier_factor)
        throughput = base_throughput * vcpu_factor * tier_factor
        
        return iops, throughput
    
    def _set_energy_parameters(self):
        """Set energy consumption parameters based on instance configuration"""
        # Base energy parameters
        # NEW: Reuse the granular per-vCPU/GB logic from ComputeResource.
        # Define these once, e.g., in a helper or constants.
        SQL_SYSTEM_IDLE_W = 150.0   # High constant power for the managed service itself
        PER_VCPU_IDLE_W = 10.0      # Power for the underlying vCPUs at idle
        PER_GB_MEM_IDLE_W = 0.5     # Power for RAM
        storage_power_per_gb_w = 0.001
        storage_power_per_iops_w = 0.00001


        vcpu = 1
        memory_gb = 4.0 # Default values
        try:
            # Simple parsing for instance types like 'db-n1-standard-2'
            parts = self.instance_type.split('-')
            if len(parts) > 1 and parts[-1].isdigit():
                vcpu = int(parts[-1])
                memory_gb = vcpu * 4.0 # Estimate memory based on standard ratios
        except Exception:
            logger.warning(f"Could not parse vCPU/memory from CloudSQL instance_type {self.instance_type}, using defaults.")
        
        # Calculate total idle power in Watts
        base_power_kw = (SQL_SYSTEM_IDLE_W
                         + (vcpu * PER_VCPU_IDLE_W)
                         + (memory_gb * PER_GB_MEM_IDLE_W)) / 1000.0
        self.base_power_watts = base_power_kw
        # keep storage ramp per GB, but bump IOPS power to match measured split
        self.power_per_gb   = storage_power_per_gb_w    / 1000.0
        self.power_per_iops = 0.003                     / 1000.0  # 3 mW per IOPS

    def calculate_query_latency(self, query_complexity: str, data_processed_mb: float) -> float:
        """
        Calculate the latency for a database query.
        
        Args:
            query_complexity: Complexity of the query (SIMPLE, MODERATE, COMPLEX)
            data_processed_mb: Amount of data processed by the query
            
        Returns:
            Estimated latency in milliseconds
        """
        # Base latency values by complexity (in ms)
        complexity_base = {
            "SIMPLE": 5,
            "MODERATE": 20,
            "COMPLEX": 100
        }
        
        # Get base latency for this complexity
        base = complexity_base.get(query_complexity, 20)
        
        # Calculate latency factors
        data_factor = 1 + (data_processed_mb / 10)  # Scale with data size
        utilization_factor = 1 + (self.utilization_ratio * 3)  # Higher impact from utilization
        
        # Calculate IOPS factor - how constrained are we?
        iops_capacity_ratio = min(1.0, self.allocated_iops / self.iops) if self.iops > 0 else 0.5
        iops_factor = 1 + iops_capacity_ratio
        
        # Try to get vCPU count to factor in processing power
        try:
            vcpu = int(self.instance_type.split("-")[-1])
        except (ValueError, IndexError):
            vcpu = 1
        
        # More vCPUs = faster processing
        vcpu_factor = max(0.5, 1 / (vcpu / 2))  # Normalized to 2 vCPUs
        
        return base * data_factor * utilization_factor * iops_factor * vcpu_factor


class Bigtable(Storage):
    """
    Google Cloud Bigtable implementation.
    """
    
    def __init__(self, 
                 name: str, 
                 capacity_gb: float,
                 region: str,
                 nodes: int = 1):
        """
        Initialize a Bigtable instance.
        
        Args:
            name: Instance name
            capacity_gb: Storage capacity in GB
            region: GCP region where the instance is deployed
            nodes: Number of nodes in the Bigtable instance
        """
        # Bigtable performance scales with node count
        iops = nodes * 10000  # Simplified: 10k IOPS per node
        throughput = nodes * 200  # Simplified: 200 MB/s per node
        
        super().__init__(name=name, 
                        capacity_gb=capacity_gb, 
                        region=region,
                        iops=iops,
                        throughput_mbps=throughput)
        
        self.nodes = nodes
        # Set energy parameters
        self._set_energy_parameters()
    
    def _set_energy_parameters(self):
        """Set energy consumption parameters based on Bigtable configuration"""
        # Base power for a single Bigtable node
        node_power_watts = 15.0
        
        # Total base power scales linearly with node count
        self.base_power_watts = node_power_watts * self.nodes
        
        # Storage power parameters
        self.power_per_gb = 0.025
        self.power_per_iops = 0.0006
    
    def calculate_operation_latency(self, operation_type: str, rows_count: int, avg_row_size_kb: float) -> float:
        """
        Calculate the latency for a Bigtable operation.
        
        Args:
            operation_type: Type of operation (READ, WRITE, SCAN)
            rows_count: Number of rows affected by the operation
            avg_row_size_kb: Average size of each row in KB
            
        Returns:
            Estimated latency in milliseconds
        """
        # Base latency values by operation type (in ms)
        operation_base = {
            "READ": 2,
            "WRITE": 5,
            "SCAN": 10
        }
        
        # Get base latency for this operation type
        base = operation_base.get(operation_type, 5)
        
        # Total data size in MB
        data_size_mb = (rows_count * avg_row_size_kb) / 1024
        
        # Calculate factors
        data_factor = 1 + (data_size_mb / 5)  # Scale with data size
        utilization_factor = 1 + (self.utilization_ratio * 2)  # Impact from utilization
        
        # Factor in node count - more nodes generally means better performance
        node_factor = max(0.5, 1 / math.sqrt(self.nodes))  # Square root scaling
        
        return base * data_factor * utilization_factor * node_factor


class Firestore(Storage):
    """
    Google Cloud Firestore implementation.
    """
    
    def __init__(self, 
                 name: str, 
                 capacity_gb: float,
                 region: str,
                 mode: str = "NATIVE"):
        """
        Initialize a Firestore database.
        
        Args:
            name: Database name
            capacity_gb: Estimated storage capacity in GB
            region: GCP region where the database is deployed
            mode: Firestore mode (NATIVE or DATASTORE)
        """
        # Firestore doesn't have explicit IOPS/throughput limits like other services
        # but we'll model it with reasonable defaults
        super().__init__(name=name, 
                        capacity_gb=capacity_gb, 
                        region=region,
                        iops=5000,
                        throughput_mbps=50)
        
        self.mode = mode
        # Set energy parameters
        self._set_energy_parameters()
    
    def _set_energy_parameters(self):
        """Set energy consumption parameters for Firestore"""
        # Firestore is a managed service with serverless scaling
        # so we'll use simplified energy model
        self.base_power_watts = 5.0
        self.power_per_gb = 0.04
        self.power_per_iops = 0.001
        
        # Adjust based on mode
        if self.mode == "DATASTORE":
            # Datastore mode might be slightly more efficient
            self.power_per_gb *= 0.9
            self.power_per_iops *= 0.9
    
    def calculate_operation_latency(self, operation_type: str, document_count: int) -> float:
        """
        Calculate the latency for a Firestore operation.
        
        Args:
            operation_type: Type of operation (GET, SET, QUERY)
            document_count: Number of documents involved
            
        Returns:
            Estimated latency in milliseconds
        """
        # Base latency values by operation type (in ms)
        operation_base = {
            "GET": 10,
            "SET": 15,
            "QUERY": 30
        }
        
        # Get base latency for this operation type
        base = operation_base.get(operation_type, 20)
        
        # Calculate factors
        count_factor = 1 + (document_count / 10)  # Scale with document count
        utilization_factor = 1 + (self.utilization_ratio * 1.5)  # Impact from utilization
        
        # Mode factor - Datastore mode might have different performance characteristics
        mode_factor = 0.9 if self.mode == "DATASTORE" else 1.0
        
        return base * count_factor * utilization_factor * mode_factor