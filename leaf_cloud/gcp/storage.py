from enum import Enum
from typing import Dict, List, Optional, Union, Tuple
import logging
import math
import warnings
from leaf_cloud.core.resource import Resource, ResourceType, ResourceState

"""
gcp/storage.py

This module defines various storage resources available in GCP.
It models specific storage services like Cloud Storage, Persistent Disks,
Cloud SQL, Bigtable, and Firestore with their respective properties.
"""

logger = logging.getLogger(__name__)


class StorageClass(Enum):
    """Enumeration of Google Cloud Storage classes"""

    STANDARD = "STANDARD"
    NEARLINE = "NEARLINE"
    COLDLINE = "COLDLINE"
    ARCHIVE = "ARCHIVE"


class DiskType(Enum):
    """Enumeration of GCP Persistent Disk types"""

    STANDARD = "pd-standard"
    BALANCED = "pd-balanced"
    SSD = "pd-ssd"
    EXTREME = "pd-extreme"


class DatabaseTier(Enum):
    """Enumeration of database performance tiers"""

    BASIC = "basic"
    STANDARD = "standard"
    HIGH_AVAILABILITY = "high-availability"
    ENTERPRISE = "enterprise"


class Storage(Resource):
    """
    Base class for all GCP storage resources.
    Extends Resource with storage-specific attributes and behavior.
    """

    def __init__(
        self,
        name: str,
        capacity_gb: float,
        region: str,
        iops: int = 0,
        throughput_mbps: float = 0,
    ):
        """
        Initialize a storage resource.

        Args:
            name: Unique name for the storage resource
            capacity_gb: Storage capacity in GB
            region: GCP region where the storage is deployed
            iops: Input/output operations per second capacity
            throughput_mbps: Throughput in MB per second
        """
        super().__init__(
            name=name,
            resource_type=ResourceType.STORAGE,
            region=region,
        )
        # Set capacities after base init to avoid passing capacity kw to base
        self.capacity_gb = capacity_gb
        self.capacity = float(capacity_gb)
        self.iops = iops
        self.throughput_mbps = throughput_mbps
        self.allocated_iops = 0.0
        self.allocated_throughput = 0.0
        # Track per-request storage allocations to support accurate deallocation of
        # capacity, IOPS and throughput without leaking performance resources
        # Mapping: request_id -> (allocated_gb, allocated_iops, allocated_throughput_mbps)
        self._storage_allocations: Dict[str, Tuple[float, int, float]] = {}

        # Energy consumption parameters
        self.base_power_watts = 0.0  # Will be set by subclasses
        self.power_per_gb = 0.0  # Will be set by subclasses
        self.power_per_iops = 0.0  # Will be set by subclasses

    def can_allocate(
        self,
        amount: float,
        requested_iops: int = 0,
        requested_throughput: float = 0,
    ) -> bool:
        """
        Check if the requested storage resources can be allocated.

        Args:
            amount: The amount of storage capacity requested in GB
            requested_iops: The IOPS capacity requested
            requested_throughput: The throughput capacity requested in MB/s

        Returns:
            True if the resource can allocate the requested amounts, False otherwise
        """
        basic_allocation = self.available_capacity >= amount
        iops_available = self.iops == 0 or (
            self.allocated_iops + requested_iops <= self.iops
        )
        throughput_available = self.throughput_mbps == 0 or (
            self.allocated_throughput + requested_throughput
            <= self.throughput_mbps
        )

        return basic_allocation and iops_available and throughput_available

    def allocate(
        self,
        amount: float,
        request_id: str,
        timestamp: float,
        requested_iops: int = 0,
        requested_throughput: float = 0.0,
        **kwargs: any
    ) -> bool:
        """
        Allocate the specified amount of storage resources.

        Args:
            amount: Amount of storage capacity to allocate in GB
            request_id: ID of the request requesting allocation
            timestamp: Simulation time when allocation occurs
            requested_iops: The IOPS capacity requested
            requested_throughput: The throughput capacity requested in MB/s

        Returns:
            True if allocation was successful, False otherwise
        """
        # Ensure capacity mutations and state changes are atomic
        with self._lock:
            if not self.can_allocate(amount, requested_iops, requested_throughput):
                logger.warning(
                    f"Storage resource {self.name} failed to allocate {amount}GB, "
                    f"{requested_iops} IOPS, {requested_throughput}MB/s for request {request_id}"
                )
                return False

            self.allocated_capacity += amount
            self.allocated_iops += requested_iops
            self.allocated_throughput += requested_throughput
            self.allocation_history[request_id] = (timestamp, amount)
            # Record detailed allocation for correct deallocation later
            self._storage_allocations[request_id] = (
                float(amount), int(requested_iops), float(requested_throughput)
            )

            # Update state if first allocation
            if (
                self.state == ResourceState.AVAILABLE
                and self.allocated_capacity > 0
            ):
                # Use state property to preserve transition validation
                self.state = ResourceState.ALLOCATED

        # Record utilization outside of lock to minimize contention
        self.record_utilization(timestamp)

        logger.debug(
            f"Storage resource {self.name} allocated {amount}GB for token {request_id}, "
            f"utilization now at {self.utilization_ratio:.2f}"
        )
        return True

    def deallocate(
        self,
        request_id: str,
        timestamp: float,
        amount: Optional[float] = None,
    ) -> float:
        """
        Deallocate storage resources allocated to a specific request.

        Args:
            request_id: The ID of the allocation to release
            timestamp: When the release occurs in simulation time
            amount: Optional amount to release. If None, releases the entire allocation

        Returns:
            Amount of capacity that was deallocated in GB
        """
        # Ensure deallocation updates capacity and performance allocations atomically
        with self._lock:
            allocation = self._storage_allocations.get(request_id)
            if allocation is None:
                logger.warning(
                    "Attempt to deallocate request %s from storage %s, but no such allocation exists",
                    request_id,
                    self.name,
                )
                return 0.0

            allocated_gb, allocated_iops, allocated_throughput = allocation

            # Determine how much to release (full if amount is None)
            release_gb = (
                float(allocated_gb)
                if amount is None
                else max(0.0, min(float(amount), float(allocated_gb)))
            )
            if release_gb <= 0.0:
                return 0.0

            proportion = 0.0 if allocated_gb <= 0 else release_gb / float(allocated_gb)
            release_iops = int(round(float(allocated_iops) * proportion))
            release_throughput = float(allocated_throughput) * proportion

            # Apply releases, ensuring we don't go negative due to rounding
            self.allocated_capacity = max(0.0, float(self.allocated_capacity) - release_gb)
            self.allocated_iops = max(0.0, float(self.allocated_iops) - float(release_iops))
            self.allocated_throughput = max(
                0.0, float(self.allocated_throughput) - float(release_throughput)
            )

            # Update or clear the stored allocation
            if release_gb < float(allocated_gb):
                remaining_gb = float(allocated_gb) - release_gb
                remaining_iops = max(0, int(allocated_iops) - release_iops)
                remaining_throughput = max(
                    0.0, float(allocated_throughput) - release_throughput
                )
                self._storage_allocations[request_id] = (
                    remaining_gb,
                    remaining_iops,
                    remaining_throughput,
                )
                self.allocation_history[request_id] = (timestamp, remaining_gb)
            else:
                # Full deallocation for this request
                del self._storage_allocations[request_id]
                self.allocation_history.pop(request_id, None)

            # Update state if no capacity remains allocated
            if self.allocated_capacity <= 0:
                self.allocated_capacity = 0.0
                self.state = ResourceState.AVAILABLE

        # Record utilization outside the lock
        self.record_utilization(timestamp)
        return release_gb

    def get_energy_consumption(self, timestamp: float) -> float:
        """
        Deprecated: use get_power_consumption().

        This method calculates instantaneous power in kW, not energy. It remains
        for backward compatibility but will be removed in a future release.
        """
        
        # Get the utilization record closest to the timestamp
        utilization = 0.0
        for record in reversed(self.utilization_history):
            if record.timestamp <= timestamp:
                utilization = record.utilization
                break

        # Calculate power consumption in kW
        power_from_capacity_kw = self.power_per_gb * self.capacity_gb * utilization
        power_from_iops_kw = self.power_per_iops * self.allocated_iops
        # base_power_watts is stored in kW (despite the name); ensure we don't mix units
        base_power_kw = float(self.base_power_watts)
        total_power_kw = base_power_kw + power_from_capacity_kw + power_from_iops_kw

        return total_power_kw

    def get_power_consumption(self, timestamp: float) -> float:
        """Preferred API; forwards to `get_energy_consumption` (deprecated). Returns instantaneous power in kW."""
        return self.get_energy_consumption(timestamp)


class CloudStorage(Storage):
    """
    Google Cloud Storage bucket implementation.
    """

    def __init__(
        self,
        name: str,
        capacity_gb: float,
        region: str,
        storage_class: StorageClass = StorageClass.STANDARD,
    ):
        """
        Initialize a Cloud Storage bucket.

        Args:
            name: Bucket name
            capacity_gb: Storage capacity in GB
            region: GCP region where the bucket is deployed
            storage_class: Storage class (STANDARD, NEARLINE, COLDLINE, ARCHIVE)
        """
        super().__init__(name=name, capacity_gb=capacity_gb, region=region)

        self.storage_class = storage_class
        # Set energy parameters based on storage class
        self._set_energy_parameters()

    def _set_energy_parameters(self):
        """Set energy consumption parameters based on storage class"""
        # These values are placeholders in Watts and should be calibrated with real-world data
        if self.storage_class == StorageClass.STANDARD:
            # Watts (very small for a logical bucket representation)
            base_w = 0.1
            # Watts per GB (HDD array, considering efficiency)
            per_gb_w = 0.0005
            per_iops_w = 0.00001  # Watts per IOPS
        elif self.storage_class == StorageClass.NEARLINE:
            base_w = 0.08
            per_gb_w = 0.0015
            per_iops_w = 0.0002
        elif self.storage_class == StorageClass.COLDLINE:
            base_w = 0.05
            per_gb_w = 0.001
            per_iops_w = 0.0003
        elif self.storage_class == StorageClass.ARCHIVE:
            base_w = 0.01
            per_gb_w = 0.0001
            per_iops_w = 0.00002  # Maybe slightly more for retrieval
        else:
            base_w, per_gb_w, per_iops_w = 0, 0, 0

        self.base_power_watts = base_w / 1000.0  # Store as kW
        self.power_per_gb = per_gb_w / 1000.0  # Store as kW/GB
        self.power_per_iops = per_iops_w / 1000.0  # Store as kW/IOPS

    def calculate_latency(
        self, operation_type: str, data_size_mb: float
    ) -> float:
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
                StorageClass.ARCHIVE: 1000,
            },
            "WRITE": {
                StorageClass.STANDARD: 20,
                StorageClass.NEARLINE: 30,
                StorageClass.COLDLINE: 40,
                StorageClass.ARCHIVE: 60,
            },
        }

        # Calculate latency based on data size and utilization
        base = base_latency.get(operation_type, {}).get(self.storage_class, 20)
        size_factor = (
            data_size_mb / 10
        )  # Scale factor, assuming 10MB as reference size
        utilization_factor = (
            1 + self.utilization_ratio * 2
        )  # Higher utilization = higher latency

        return base * size_factor * utilization_factor


class PersistentDisk(Storage):
    """
    Google Cloud Persistent Disk implementation.
    """

    def __init__(
        self,
        name: str,
        capacity_gb: float,
        region: str,
        disk_type: DiskType = DiskType.STANDARD,
        iops: Optional[int] = None,
        throughput_mbps: Optional[float] = None,
    ):
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
            default_iops, default_throughput = self._get_default_performance(
                disk_type, capacity_gb
            )
            iops = iops or default_iops
            throughput_mbps = throughput_mbps or default_throughput

        super().__init__(
            name=name,
            capacity_gb=capacity_gb,
            region=region,
            iops=iops,
            throughput_mbps=throughput_mbps,
        )

        self.disk_type = disk_type
        # Set energy parameters based on disk type
        self._set_energy_parameters()

    def _get_default_performance(
        self, disk_type: DiskType, capacity_gb: float
    ) -> Tuple[int, float]:
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
        # These values are placeholders in Watts and should be calibrated with real-world data
        if self.disk_type == DiskType.STANDARD:
            base_w = 1.0  # Idle power for a disk
            per_gb_w = 0.002  # Very small incremental per GB after base
            per_iops_w = 0.0001
        elif self.disk_type == DiskType.BALANCED:
            base_w = 1.0
            per_gb_w = 0.0015
            per_iops_w = 0.0004
        elif self.disk_type == DiskType.SSD:
            base_w = 0.5  # Idle for SSD
            per_gb_w = 0.001
            per_iops_w = 0.00005
        elif self.disk_type == DiskType.EXTREME:
            base_w = 2.0
            per_gb_w = 0.0025
            per_iops_w = 0.0002
        else:
            base_w, per_gb_w, per_iops_w = 0, 0, 0

        self.base_power_watts = base_w / 1000.0  # Store as kW
        self.power_per_gb = per_gb_w / 1000.0  # Store as kW/GB
        self.power_per_iops = per_iops_w / 1000.0  # Store as kW/IOPS

    def calculate_latency(
        self, operation_type: str, data_size_mb: float
    ) -> float:
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
                DiskType.EXTREME: 2,
            },
            "WRITE": {
                DiskType.STANDARD: 20,
                DiskType.BALANCED: 15,
                DiskType.SSD: 10,
                DiskType.EXTREME: 5,
            },
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

    def __init__(
        self,
        name: str,
        capacity_gb: float,
        region: str,
        instance_type: str = "db-standard-1",
        database_tier: DatabaseTier = DatabaseTier.STANDARD,
        high_availability: bool = False,
        vcpus: Optional[int] = None,
        memory_gb: Optional[float] = None,

        power_profile_params: Optional[Dict[str, float]] = None
    ):
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
        # Derive performance from explicit config when provided; otherwise use instance_type heuristics
        iops, throughput = self._calculate_performance(
            instance_type, database_tier, capacity_gb, vcpus
        )

        super().__init__(
            name=name,
            capacity_gb=capacity_gb,
            region=region,
            iops=iops,
            throughput_mbps=throughput,
        )
        self.resource_type = ResourceType.DATABASE

        self.instance_type = instance_type
        self.database_tier = database_tier
        self.high_availability = high_availability
        self.vcpus_explicit: Optional[int] = vcpus
        self.memory_gb_explicit: Optional[float] = memory_gb

        self.power_profile_params = power_profile_params # Store the new params
        # Set energy parameters
        self._set_energy_parameters()

    def get_processing_delay(self) -> float:
        """
        Return an estimated per-request processing time for the database (seconds).

        Uses the request-rate hint (_last_request_rate) provided by the orchestrator
        to approximate queuing effects: higher query rates stretch the delay.
        """
        base_delay = 0.18   # slightly lower baseline ≈180 ms
        per_request_increment = 0.0014  # add ≈1.4 ms per additional rps
        req_rate = 0.0
        try:
            req_rate = float(getattr(self, "_last_request_rate", 0.0) or 0.0)
        except Exception:
            req_rate = 0.0
        delay = base_delay + per_request_increment * req_rate
        # Clamp to a sane range to avoid runaway delays
        return max(0.05, min(delay, 2.0))

    @property
    def utilization_ratio(self) -> float:
        """Estimate CloudSQL utilization from observed request rate.

        Falls back to base behavior (allocated/total capacity) if no request
        rate hint is available. When a request rate hint is present, derive
        utilization using both IOPS and throughput envelopes and choose the
        higher utilization as the effective value.

        Heuristics:
        - Assume each request generates a small number of I/O operations and bytes.
        - iops_per_request: 40 (reads+writes)
        - mb_per_request: 0.25 MB
        """
        try:
            req = float(getattr(self, "_last_request_rate", 0.0) or 0.0)
            if req <= 0:
                return super().utilization_ratio

            iops_capacity = float(getattr(self, "iops", 0) or 0)
            mbps_capacity = float(getattr(self, "throughput_mbps", 0.0) or 0.0)

            iops_per_request = 40.0
            mb_per_request = 0.25

            load_iops = req * iops_per_request
            load_mbps = req * mb_per_request

            util_iops = (load_iops / iops_capacity) if iops_capacity > 0 else 0.0
            util_bw = (load_mbps / mbps_capacity) if mbps_capacity > 0 else 0.0

            return max(0.0, min(1.0, max(util_iops, util_bw)))
        except Exception:
            return super().utilization_ratio

    def _calculate_performance(
        self, instance_type: str, tier: DatabaseTier, capacity_gb: float, vcpus: Optional[int]
    ) -> Tuple[int, float]:
        """
        Calculate IOPS and throughput based on instance type and tier.

        Args:
            instance_type: Machine type for the instance
            tier: Database performance tier
            capacity_gb: Storage capacity in GB

        Returns:
            Tuple of (IOPS, throughput_mbps)
        """
        # Prefer explicit vCPU configuration if provided; fall back to instance_type parsing
        if vcpus is not None and vcpus > 0:
            vcpu = int(vcpus)
        else:
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
            DatabaseTier.ENTERPRISE: 1.5,
        }
        tier_factor = tier_multipliers.get(tier, 1.0)

        # Calculate final values
        iops = int(base_iops * vcpu_factor * tier_factor)
        throughput = base_throughput * vcpu_factor * tier_factor

        return iops, throughput
    
    def _set_energy_parameters(self):
        """Set energy consumption parameters based on instance configuration"""
        # --- NEW (V7): Final, Corrected Parser and Logic ---
        # This version fixes the root cause: it correctly parses the 'self.instance_type'
        # attribute which holds the tier string like "db-custom-4-8192".

        power_profiles = {
            "standard": {
                "base_w": 1.0, "vcpu_w": 0.5, "mem_gb_w": 0.1,
                "storage_gb_w": 0.005, "iops_w": 0.00050
            },
            "enterprise": {
                "base_w": 30.0, "vcpu_w": 15.0, "mem_gb_w": 1.5,
                "storage_gb_w": 0.015, "iops_w": 0.0015
            }
        }

        vcpu = 1 # Default vCPU count
        memory_gb = 4.0 # Default memory
        
        # --- DIRECT AND ROBUST PARSING OF self.instance_type ---
        try:
            # self.instance_type holds the 'tier' string, e.g., "db-custom-4-8192"
            tier_string = self.instance_type
            parts = tier_string.split("-")
            if "custom" in parts and len(parts) >= 4:
                # Handles "db-custom-VCPU-MEMORY"
                vcpu = int(parts[2])
                memory_mb = int(parts[3])
                memory_gb = memory_mb / 1024.0
            elif len(parts) >= 3 and parts[-1].isdigit():
                # Handles "db-standard-VCPU" or other standard types
                vcpu = int(parts[-1])
                memory_gb = vcpu * 4.0 # Fallback memory estimation
        except (ValueError, IndexError, AttributeError):
            logger.warning(f"Could not parse vCPU/memory from instance_type '{getattr(self, 'instance_type', 'N/A')}'. Defaulting to 1 vCPU, 4GB RAM.")
            vcpu = 1
            memory_gb = 4.0

        # Override with explicit values if they were provided during initialization
        if self.vcpus_explicit is not None and self.vcpus_explicit > 0:
            vcpu = int(self.vcpus_explicit)
        if self.memory_gb_explicit is not None and self.memory_gb_explicit > 0:
            memory_gb = float(self.memory_gb_explicit)

        # Now, select the profile based on the CORRECT vCPU count
        selected_profile_key = "standard"
        if (self.database_tier == DatabaseTier.ENTERPRISE or
            self.high_availability or
            vcpu >= 4):
            selected_profile_key = "enterprise"
        
        profile = power_profiles[selected_profile_key]

        if self.power_profile_params:
            profile["base_w"] = self.power_profile_params.get("sql_base_w", profile["base_w"])
            profile["vcpu_w"] = self.power_profile_params.get("sql_vcpu_w", profile["vcpu_w"])
            profile["mem_gb_w"] = self.power_profile_params.get("sql_mem_gb_w", profile["mem_gb_w"])
            
        # Use a more visible INFO log to confirm the fix
        logger.info(f"CloudSQL '{self.name}' (Tier: '{self.instance_type}', Parsed vCPUs: {vcpu})-> Selected '{selected_profile_key}' power profile.")

        base_compute_w = profile["base_w"] + (vcpu * profile["vcpu_w"]) + (memory_gb * profile["mem_gb_w"])
        
        final_base_w = base_compute_w

        storage_power_per_gb_w = profile["storage_gb_w"]
        storage_power_per_iops_w = profile["iops_w"]

        self.base_power_watts = final_base_w / 1000.0
        self.power_per_gb = storage_power_per_gb_w / 1000.0
        self.power_per_iops = storage_power_per_iops_w / 1000.0

  


    def calculate_query_latency(
        self, query_complexity: str, data_processed_mb: float
    ) -> float:
        """
        Calculate the latency for a database query.

        Args:
            query_complexity: Complexity of the query (SIMPLE, MODERATE, COMPLEX)
            data_processed_mb: Amount of data processed by the query

        Returns:
            Estimated latency in milliseconds
        """
        # Base latency values by complexity (in ms)
        complexity_base = {"SIMPLE": 5, "MODERATE": 20, "COMPLEX": 100}

        # Get base latency for this complexity
        base = complexity_base.get(query_complexity, 20)

        # Calculate latency factors
        data_factor = 1 + (data_processed_mb / 10)  # Scale with data size
        # Higher impact from utilization
        utilization_factor = 1 + (self.utilization_ratio * 3)

        # Calculate IOPS factor - how constrained are we?
        iops_capacity_ratio = (
            min(1.0, self.allocated_iops / self.iops) if self.iops > 0 else 0.5
        )
        iops_factor = 1 + iops_capacity_ratio

        # Try to get vCPU count to factor in processing power
        # Prefer explicit vCPU count when available
        if self.vcpus_explicit is not None and self.vcpus_explicit > 0:
            vcpu = int(self.vcpus_explicit)
        else:
            try:
                vcpu = int(self.instance_type.split("-")[-1])
            except (ValueError, IndexError):
                vcpu = 1

        # More vCPUs = faster processing
        vcpu_factor = max(0.5, 1 / (vcpu / 2))  # Normalized to 2 vCPUs

        return (
            base * data_factor * utilization_factor * iops_factor * vcpu_factor
        )


class Bigtable(Storage):
    """
    Google Cloud Bigtable implementation.
    """

    def __init__(
        self, name: str, capacity_gb: float, region: str, nodes: int = 1
    ):
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

        super().__init__(
            name=name,
            capacity_gb=capacity_gb,
            region=region,
            iops=iops,
            throughput_mbps=throughput,
        )

        self.nodes = nodes
        # Set energy parameters
        self._set_energy_parameters()

    def _set_energy_parameters(self):
        """Set energy consumption parameters based on Bigtable configuration"""
        # Base power for a single Bigtable node in Watts
        node_power_watts = 15.0

        # Total base power scales linearly with node count
        total_base_w = node_power_watts * self.nodes

        # Storage power parameters in Watts
        power_per_gb_w = 0.025
        power_per_iops_w = 0.0006

        # Convert to kW and assign
        self.base_power_watts = total_base_w / 1000.0
        self.power_per_gb = power_per_gb_w / 1000.0
        self.power_per_iops = power_per_iops_w / 1000.0

    def calculate_operation_latency(
        self, operation_type: str, rows_count: int, avg_row_size_kb: float
    ) -> float:
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
        operation_base = {"READ": 2, "WRITE": 5, "SCAN": 10}

        # Get base latency for this operation type
        base = operation_base.get(operation_type, 5)

        # Total data size in MB
        data_size_mb = (rows_count * avg_row_size_kb) / 1024

        # Calculate factors
        data_factor = 1 + (data_size_mb / 5)  # Scale with data size
        # Impact from utilization
        utilization_factor = 1 + (self.utilization_ratio * 2)

        # Factor in node count - more nodes generally means better performance
        node_factor = max(
            0.5, 1 / math.sqrt(self.nodes)
        )  # Square root scaling

        return base * data_factor * utilization_factor * node_factor


class Firestore(Storage):
    """
    Google Cloud Firestore implementation.
    """

    def __init__(
        self, name: str, capacity_gb: float, region: str, mode: str = "NATIVE"
    ):
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
        super().__init__(
            name=name,
            capacity_gb=capacity_gb,
            region=region,
            iops=5000,
            throughput_mbps=50,
        )

        self.mode = mode
        # Set energy parameters
        self._set_energy_parameters()

    def _set_energy_parameters(self):
        """Set energy consumption parameters for Firestore"""
        # Firestore is a managed service with serverless scaling, so we'll use a simplified energy model.
        # Values are in Watts.
        base_w = 5.0
        per_gb_w = 0.04
        per_iops_w = 0.001

        # Adjust based on mode
        if self.mode == "DATASTORE":
            # Datastore mode might be slightly more efficient
            per_gb_w *= 0.9
            per_iops_w *= 0.9

        # Convert to kW and assign
        self.base_power_watts = base_w / 1000.0
        self.power_per_gb = per_gb_w / 1000.0
        self.power_per_iops = per_iops_w / 1000.0

    def calculate_operation_latency(
        self, operation_type: str, document_count: int
    ) -> float:
        """
        Calculate the latency for a Firestore operation.

        Args:
            operation_type: Type of operation (GET, SET, QUERY)
            document_count: Number of documents involved

        Returns:
            Estimated latency in milliseconds
        """
        # Base latency values by operation type (in ms)
        operation_base = {"GET": 10, "SET": 15, "QUERY": 30}

        # Get base latency for this operation type
        base = operation_base.get(operation_type, 20)

        # Calculate factors
        count_factor = 1 + (document_count / 10)  # Scale with document count
        # Impact from utilization
        utilization_factor = 1 + (self.utilization_ratio * 1.5)

        # Mode factor - Datastore mode might have different performance characteristics
        mode_factor = 0.9 if self.mode == "DATASTORE" else 1.0

        return base * count_factor * utilization_factor * mode_factor
