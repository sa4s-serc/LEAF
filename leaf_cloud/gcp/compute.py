import logging
import math
import time
from typing import List, Optional, Dict, Tuple

from typing_extensions import override

from leaf_cloud.core.resource import Resource, ResourceType, ResourceState, UtilizationRecord
from ..utils.power_utils import PowerMixin, PowerProfile, build_power_profile_from_machine

"""
gcp/compute.py

This module defines GCP compute resources for the LEAF-Cloud framework.
It models various GCP compute services including Compute Engine VMs, GKE,
App Engine, Cloud Functions, and Cloud Run.
"""

# Configure logger
logger = logging.getLogger(__name__)

# Create a dedicated logger for autoscaling events that's always visible
autoscaling_logger = logging.getLogger("leaf_cloud.autoscaling")
# Reduce verbosity by default: emit warnings and above unless user elevates
autoscaling_logger.setLevel(logging.WARNING)

# Configure autoscaling logger to be always visible
def _configure_autoscaling_logger():
    """Configure the autoscaling logger for optimal visibility and formatting."""
    # Set level to INFO to ensure autoscaling messages are always shown
    autoscaling_logger.setLevel(logging.INFO)
    
    # Create a custom formatter for autoscaling messages
    formatter = logging.Formatter(
        '%(asctime)s [AUTOSCALING] %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Only add handler if it doesn't already exist
    if not autoscaling_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler.setLevel(logging.INFO)
        autoscaling_logger.addHandler(handler)
        
        # Prevent propagation to avoid duplicate messages
        autoscaling_logger.propagate = False

# Note: Do not auto-configure handlers for library code to avoid interfering
# with host application logging configuration. Applications may call
# _configure_autoscaling_logger() explicitly if desired.


class AutoscalingLogThrottler:
    """Utility class to throttle autoscaling log messages and prevent spam."""
    
    def __init__(self):
        self._last_log_times = {}  # message_key -> timestamp
        self._log_counts = {}      # message_key -> count
    
    def should_log(self, message_key: str, current_time: float, min_interval: float = 10.0) -> bool:
        """
        Determine if a message should be logged based on throttling rules.
        
        Args:
            message_key: Unique key for the message type
            current_time: Current timestamp
            min_interval: Minimum interval between logs in seconds
            
        Returns:
            True if the message should be logged
        """
        last_time = self._last_log_times.get(message_key, 0)
        time_since_last = current_time - last_time
        
        if time_since_last >= min_interval:
            self._last_log_times[message_key] = current_time
            # Reset count when we log
            count = self._log_counts.get(message_key, 0)
            self._log_counts[message_key] = 0
            return True
        else:
            # Increment suppressed count
            self._log_counts[message_key] = self._log_counts.get(message_key, 0) + 1
            return False
    
    def get_suppressed_count(self, message_key: str) -> int:
        """Get the number of suppressed messages for a key."""
        return self._log_counts.get(message_key, 0)

# Global throttler instance
_log_throttler = AutoscalingLogThrottler()

class ComputeResource(Resource, PowerMixin):
    """
    Base class for all GCP compute resources.

    Extends the generic Resource class with compute-specific attributes and behaviors.
    Uses PowerMixin for standardized power calculations.
    """

    def __init__(
        self,
        name: str,
        capacity: float,
        vcpus: int,
        memory_gb: float,
        region: str,
        machine_type: Optional[str] = None,
        preemptible: bool = False,
    ):
        """
        Initialize a compute resource.

        Args:
            name: Unique name for the resource
            capacity: Maximum capacity (normalized)
            vcpus: Number of virtual CPUs
            memory_gb: Memory size in GB
            region: GCP region where the resource is deployed
            machine_type: GCP machine type (e.g., 'n1-standard-2')
            preemptible: Whether the resource is preemptible/spot
        """
        super().__init__(
            name=name,
            resource_type=ResourceType.COMPUTE,
            region=region,
        )
        # Ensure capacity reflects the compute-specific capacity provided
        self.capacity = capacity
        self.vcpus = vcpus
        self.memory_gb = memory_gb
        self.machine_type = machine_type
        self.preemptible = preemptible

        # Initialize power profile
        self._power_profile = self._create_power_profile()

    @property
    def available_capacity(self) -> float:
        """Calculate available capacity (total - allocated)."""
        return max(0.0, self.capacity - self.allocated_capacity)
    
    @property
    def utilization_ratio(self) -> float:
        """Calculate utilization ratio (allocated / total capacity)."""
        if self.capacity <= 0:
            return 0.0
        return min(1.0, self.allocated_capacity / self.capacity)

    def _create_power_profile(self) -> PowerProfile:
        """
        Create a power profile for this compute resource.
        
        Returns:
            PowerProfile: Configured power profile for this resource
        """
        return build_power_profile_from_machine(
            vcpus=self.vcpus,
            memory_gb=self.memory_gb,
            machine_type=self.machine_type,
            preemptible=self.preemptible,
        )

    def get_utilization(self, timestamp: float) -> float:
        """
        Get the current utilization at the given timestamp.
        
        Args:
            timestamp: Timestamp to get utilization for
            
        Returns:
            Utilization ratio (0.0 to 1.0)
        """
        # Fast path for empty or trivially resolved cases
        history = self.utilization_history
        if not history:
            return 0.0
        # If the last record is at or before timestamp, return it
        if history[-1].timestamp <= timestamp:
            return history[-1].utilization
        # If timestamp precedes the first record, assume idle
        if history[0].timestamp > timestamp:
            return 0.0

        # Binary search for the rightmost record with timestamp <= query
        lo, hi = 0, len(history) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if history[mid].timestamp <= timestamp:
                lo = mid + 1
            else:
                hi = mid - 1
        # hi is now the index of the latest record not after the timestamp
        return history[hi].utilization
    
    def get_power_consumption(self, timestamp: float) -> float:
        """
        Get the current power consumption at the given timestamp.
        
        Args:
            timestamp: Timestamp to get power consumption for
            
        Returns:
            Power consumption in kilowatts (kW)
        """
        utilization = self.get_utilization(timestamp)
        return super().get_power_consumption(utilization)

    # Temporary backward-compatibility wrapper (returns kW even though old name implies energy)


class ComputeEngineVM(ComputeResource):
    """
    Model of a Google Compute Engine Virtual Machine.
    """

    def __init__(
        self,
        name: str,
        machine_type: str,
        region: str,
        vcpus: int,
        memory_gb: float,
        preemptible: bool = False,
        gpu_type: Optional[str] = None,
        gpu_count: int = 0,
        boot_disk_size_gb: int = 10,
        boot_disk_type: str = "pd-standard",
        capacity: float = 1.0,
    ):
        """
        Initialize a Compute Engine VM resource.

        Args:
            name: VM name
            machine_type: GCP machine type
            region: GCP region
            vcpus: Number of virtual CPUs
            memory_gb: Memory size in GB
            preemptible: Whether the VM is preemptible
            gpu_type: Type of attached GPU (if any)
            gpu_count: Number of attached GPUs
            boot_disk_size_gb: Size of boot disk in GB
            boot_disk_type: Type of boot disk
            capacity: Normalized capacity
        """
        super().__init__(
            name, capacity, vcpus, memory_gb, region, machine_type, preemptible
        )
        self.gpu_type = gpu_type
        self.gpu_count = gpu_count
        self.boot_disk_size_gb = boot_disk_size_gb
        self.boot_disk_type = boot_disk_type

        # Adjust energy profile if GPUs are present
        if gpu_count > 0:
            self._add_gpu_energy_consumption()

    def _add_gpu_energy_consumption(self):
        """Add GPU energy consumption to the baseline profile."""
        # Simple model addition for GPU power consumption
        # Real-world values would be more complex and GPU-type specific
        if self.gpu_type and self.gpu_count > 0:
            # Add GPU base and max consumption
            # Example value for idle GPU (would vary by type)
            gpu_base_power = 30.0
            # Example value for active GPU (would vary by type)
            gpu_max_power = 250.0
            # Adjust the underlying power profile rather than ad-hoc attributes
            try:
                profile = self.power_profile
                idle_kw = float(profile.idle_power_kw)
                max_kw = float(profile.max_power_kw)
            except Exception:
                # Fallback if profile is not yet initialized
                profile = self._create_power_profile()
                self._power_profile = profile
                idle_kw = float(profile.idle_power_kw)
                max_kw = float(profile.max_power_kw)

            added_idle_kw = (gpu_base_power * self.gpu_count) / 1000.0
            added_max_kw = (gpu_max_power * self.gpu_count) / 1000.0

            new_idle_kw = idle_kw + added_idle_kw
            new_max_kw = max_kw + added_max_kw

            # Replace power profile with updated GPU-inclusive profile
            from ..utils.power_utils import PowerProfile
            self._power_profile = PowerProfile(idle_power_kw=new_idle_kw, max_power_kw=new_max_kw)


class GKENode(ComputeResource):
    """Model of a Google Kubernetes Engine node."""

    def __init__(
        self,
        name: str,
        machine_type: str,
        region: str,
        vcpus: int,
        memory_gb: float,
        preemptible: bool = False,
        container_optimized: bool = True,
        capacity: float = 1.0,
    ):
        """
        Initialize a GKE node resource.

        Args:
            name: Node name
            machine_type: GCP machine type
            region: GCP region
            vcpus: Number of virtual CPUs
            memory_gb: Memory size in GB
            preemptible: Whether the node is preemptible
            container_optimized: Whether using container-optimized OS
            capacity: Normalized capacity
        """
        self.container_optimized = container_optimized
        super().__init__(
            name, capacity, vcpus, memory_gb, region, machine_type, preemptible
        )
        self.cluster: Optional["GKECluster"] = None

    @property
    def utilization_ratio(self) -> float:
        cluster = getattr(self, "cluster", None)
        if cluster and getattr(cluster, "pods_per_node", None):
            pod_count = cluster.pods_per_node.get(self.id)
            limit = getattr(cluster, "_last_pods_per_node_limit", None)
            if pod_count is not None and isinstance(limit, (int, float)) and limit > 0:
                return min(1.0, max(0.0, float(pod_count) / float(limit)))
        return super().utilization_ratio

    def _create_power_profile(self) -> PowerProfile:
        """
        Create a power profile for this GKE node.

        Returns:
            PowerProfile: Configured power profile for this node
        """
        # Start with the standard compute power profile
        profile = super()._create_power_profile()

        # Apply container-optimized OS efficiency if enabled
        if self.container_optimized:
            # Container-optimized OS is slightly more efficient
            idle_power_kw = profile.idle_power_kw * 0.95
            max_power_kw = profile.max_power_kw * 0.97
            return PowerProfile(idle_power_kw=idle_power_kw, max_power_kw=max_power_kw)
        return profile


class GKECluster(ComputeResource):
    """
    Model of a Google Kubernetes Engine cluster.

    Contains multiple GKE nodes and handles pod allocation and scaling.
    """

    def __init__(
        self,
        name: str,
        region: str,
        version: str = "latest",
        network: str = "default",
        default_pod_cpu: Optional[float] = None,
        default_pod_memory_gb: Optional[float] = None,
        node_cpu_overhead: float = 0.2,
        node_memory_overhead_gb: float = 0.5,
        min_pods: Optional[int] = None,
        max_pods: Optional[int] = None,
    ):
        """
        Initialize a GKE cluster.

        Args:
            name: Cluster name
            region: GCP region
            version: Kubernetes version
            network: VPC network name
        """
        self._zero_power_profile = PowerProfile(idle_power_kw=0.0, max_power_kw=0.0)
        super().__init__(name, 1.0, 0, 0.0, region)
        self.name = name
        # Tag for energy model classification when generic functions are used
        self.specific_type = "gke-cluster"
        self.version = version
        self.network = network
        self.nodes: List[GKENode] = []
        self.pods_per_node = {}  # node_id -> current pod count
        self.min_nodes: int = 1
        self.max_nodes: Optional[int] = None
        self.default_pod_cpu_request = (
            float(default_pod_cpu)
            if isinstance(default_pod_cpu, (int, float)) and float(default_pod_cpu) > 0.0
            else 0.5
        )
        self.default_pod_memory_request_gb = (
            float(default_pod_memory_gb)
            if isinstance(default_pod_memory_gb, (int, float)) and float(default_pod_memory_gb) > 0.0
            else 1.0
        )
        self.node_cpu_overhead = max(0.0, float(node_cpu_overhead))
        self.node_memory_overhead_gb = max(0.0, float(node_memory_overhead_gb))
        self._seed_node_vcpus: Optional[float] = None
        self._seed_node_memory_gb: Optional[float] = None
        self.min_pods = min_pods if isinstance(min_pods, int) and min_pods > 0 else None
        self.max_pods = max_pods if isinstance(max_pods, int) and max_pods > 0 else None
        self._last_pods_per_node_limit: int = 10

        # Geographic efficiency factor (affects pod capacity)
        self.geo_efficiency_factor = self._determine_geo_factor()

        logger.info(
            "GKE Cluster %s created in %s with geo factor %s",
            name,
            region,
            self.geo_efficiency_factor,
        )

    def _create_power_profile(self) -> PowerProfile:
        """Clusters are logical orchestrators; physical power is modeled on nodes."""
        return self._zero_power_profile

    def set_autoscaling_bounds(
        self,
        min_nodes: Optional[int],
        max_nodes: Optional[int],
    ) -> None:
        """Configure autoscaling limits for the cluster."""
        def _coerce(value: Optional[int]) -> Optional[int]:
            if value is None:
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                logger.warning(
                    "Ignoring invalid autoscaling bound %s for cluster %s",
                    value,
                    self.name,
                )
                return None

        min_value = _coerce(min_nodes)
        max_value = _coerce(max_nodes)

        if min_value is not None:
            self.min_nodes = max(0, min_value)

        if max_value is not None:
            max_candidate = max(0, max_value)
            if max_candidate < self.min_nodes:
                logger.debug(
                    "Adjusting max_nodes for cluster %s to match min_nodes (%d)",
                    self.name,
                    self.min_nodes,
                )
                max_candidate = self.min_nodes
            self.max_nodes = max_candidate

        if self.max_nodes is not None and self.max_nodes < self.min_nodes:
            self.max_nodes = self.min_nodes

    def _update_state(self) -> None:
        """
        Update the state of the cluster based on utilization
        i.e., scaling up or down the number of nodes.

        Args:
            utilization: Current utilization of the cluster
        """
        utilization = 0.0
        if self.utilization_history:
            utilization = self.utilization_history[-1].utilization
        # Get the current total capacity and calculate the required capacity
        current_capacity = sum(node.capacity for node in self.nodes)
        required_capacity = current_capacity * utilization

        # If no nodes, nothing to update
        if not self.nodes:
            logger.warning(f"Cluster {self.name} has no nodes to scale")
            return

        # Calculate average node capacity for scaling decisions
        avg_node_capacity = current_capacity / len(self.nodes)

        # Scale up: utilization > 80%
        if utilization > 0.8:
            # Calculate how many new nodes we need
            additional_capacity_needed = (
                required_capacity * 1.2 - current_capacity
            )  # Add 20% buffer
            nodes_to_add = math.ceil(
                additional_capacity_needed / avg_node_capacity
            )

            if nodes_to_add > 0:
                logger.info(
                    f"Scaling up cluster {self.name}: adding {nodes_to_add} nodes"
                )
                # In a real implementation, this would trigger node creation
                # For simulation purposes, we update the state
                self.state = ResourceState.SCALING

        # Scale down: utilization < 30% and more than one node
        elif utilization < 0.3 and len(self.nodes) > 1:
            # Calculate how many nodes we can remove while keeping 50% buffer
            current_used_capacity = current_capacity * utilization
            minimum_required_capacity = (
                current_used_capacity * 1.5
            )  # 50% buffer
            excess_capacity = current_capacity - minimum_required_capacity
            nodes_to_remove = min(
                math.floor(excess_capacity / avg_node_capacity),
                len(self.nodes) - 1,  # Always keep at least one node
            )

            if nodes_to_remove > 0:
                logger.info(
                    f"Scaling down cluster {self.name}: removing {nodes_to_remove} nodes"
                )
                # In a real implementation, this would trigger node removal
                # For simulation purposes, we update the state
                self.state = ResourceState.SCALING

        # Normal operation: 30% <= utilization <= 80%
        else:
            self.state = ResourceState.ALLOCATED

        # Record scaling events
        self._record_scaling_event(utilization)

    def _record_scaling_event(self, utilization: float) -> None:
        """
        Record cluster scaling events for analysis.

        Args:
            utilization: Current utilization that triggered scaling
        """
        # Log the scaling event
        timestamp = time.time()
        logger.debug(
            f"Cluster {self.name} scaling event at {timestamp}: "
            + f"utilization={utilization:.2f}, nodes={len(self.nodes)}"
        )

        # In a real implementation, this might update metrics or a database
        # For now, we'll just log the information

    def get_carbon_footprint(
        self, timestamp: float, grid_carbon_intensity: float
    ) -> float:
        """
        Calculate the instantaneous carbon emission rate of the cluster.

        Args:
            timestamp: Simulation timestamp
            grid_carbon_intensity: Carbon intensity of the electrical grid (gCO2/kWh)

        Returns:
            Carbon emissions rate in grams of CO2e per hour (gCO2/h)
        """
        # Instantaneous power draw in kW
        power_kw = self.get_power_consumption(timestamp)

        # Carbon rate (gCO2/h) = Power (kW) * carbon intensity (gCO2/kWh)
        return power_kw * grid_carbon_intensity

    def _determine_geo_factor(self) -> float:
        """
        Determine the geographic efficiency factor based on the region.

        Returns:
            A geographic efficiency factor between 0.7 and 1.0
        """
        if self.region is None:
            logger.warning(
                "Region not specified, using default geo factor of 1.0"
            )
            return 1.0
        # This would ideally use a more sophisticated model based on real data
        # The simplification here assigns factors based on region names
        if self.region.startswith("us-"):
            return 1.0
        elif self.region.startswith("europe-"):
            return 0.95
        elif self.region.startswith("asia-"):
            return 0.9
        else:
            return 0.85

    def add_node(self, node: GKENode) -> None:
        """
        Add a node to the cluster.

        Args:
            node: GKE node to add
        """
        self.nodes.append(node)
        try:
            node.cluster = self
        except Exception:
            pass
        self.pods_per_node[node.id] = 0
        self.capacity = self.get_total_capacity()
        try:
            if self._seed_node_vcpus is None and hasattr(node, "vcpus"):
                self._seed_node_vcpus = float(node.vcpus)
            if self._seed_node_memory_gb is None and hasattr(node, "memory_gb"):
                self._seed_node_memory_gb = float(node.memory_gb)
        except Exception:
            pass
        logger.debug(f"Node {node.name} added to cluster {self.name}")

    def remove_node(self) -> Optional[GKENode]:
        """Removes a node from the cluster if possible."""
        min_required = self.min_nodes if isinstance(self.min_nodes, int) else 0
        if len(self.nodes) > max(min_required, 0):  # Ensure we don't drop below the floor
            removed_node = self.nodes.pop()
            if removed_node.id in self.pods_per_node:
                del self.pods_per_node[removed_node.id]
            self.capacity = self.get_total_capacity()
            logger.debug(
                f"Node {removed_node.name} removed from cluster {self.name}. "
                f"New capacity: {self.capacity}"
            )
            return removed_node
        return None

    def calculate_required_pods(
        self, request_rate: float, pod_capacity: float
    ) -> int:
        """
        Calculate the required number of pods using the dynamic scaling equation.

        Args:
            request_rate: Incoming request rate (λ)
            pod_capacity: Processing capacity of a single pod (Rpod)

        Returns:
            Required number of pods
        """
        # Dynamic scaling model (equation 7):
        # Npods = ⌈λ / (g · Rpod)⌉
        # where:
        # λ is the incoming request rate
        # Rpod is the processing capacity of a single pod
        # g is a geolocation factor
        if pod_capacity <= 0:
            return 0
        adjusted_capacity = self.geo_efficiency_factor * pod_capacity
        if adjusted_capacity <= 0:
            return 0
        return math.ceil(request_rate / adjusted_capacity)

    def _pods_supported_per_node(
        self,
        pod_cpu: Optional[float],
        pod_mem_gb: Optional[float],
    ) -> Optional[int]:
        if (
            pod_cpu is None
            or pod_mem_gb is None
            or pod_cpu <= 0.0
            or pod_mem_gb <= 0.0
        ):
            return None

        node_ref: Optional[GKENode] = self.nodes[0] if self.nodes else None
        vcpus = float(getattr(node_ref, "vcpus", 0.0)) if node_ref else (
            float(self._seed_node_vcpus) if self._seed_node_vcpus is not None else 0.0
        )
        mem_gb = float(getattr(node_ref, "memory_gb", 0.0)) if node_ref else (
            float(self._seed_node_memory_gb) if self._seed_node_memory_gb is not None else 0.0
        )
        if vcpus <= 0.0 or mem_gb <= 0.0:
            return None

        usable_cpu = max(0.0, vcpus - self.node_cpu_overhead)
        usable_mem = max(0.0, mem_gb - self.node_memory_overhead_gb)
        if usable_cpu <= 0.0 or usable_mem <= 0.0:
            return None

        pods_cpu = usable_cpu / pod_cpu
        pods_mem = usable_mem / pod_mem_gb
        pods = math.floor(min(pods_cpu, pods_mem))
        return pods if pods > 0 else 1

    def get_node_capacity_limits(self) -> Tuple[float, float]:
        """Return per-node usable CPU cores and memory (GiB) for scheduling checks."""
        node_ref: Optional[GKENode] = self.nodes[0] if self.nodes else None
        vcpus = float(getattr(node_ref, "vcpus", 0.0) or 0.0)
        mem_gb = float(getattr(node_ref, "memory_gb", 0.0) or 0.0)
        if vcpus <= 0.0 and self._seed_node_vcpus is not None:
            vcpus = float(self._seed_node_vcpus)
        if mem_gb <= 0.0 and self._seed_node_memory_gb is not None:
            mem_gb = float(self._seed_node_memory_gb)
        usable_cpu = max(0.0, vcpus - self.node_cpu_overhead)
        usable_mem = max(0.0, mem_gb - self.node_memory_overhead_gb)
        return usable_cpu, usable_mem

    def scale(
        self,
        request_rate: float,
        pod_capacity: float,
        timestamp: float,
        pods_override: Optional[float] = None,
    ):
        """
        Scale the number of nodes in the cluster based on request rate or an explicit pod target.
        """
        if pods_override is not None:
            try:
                required_pods = max(0, int(math.ceil(float(pods_override))))
            except (TypeError, ValueError):
                required_pods = 0
        else:
            required_pods = self.calculate_required_pods(
                request_rate, pod_capacity
            )
        if isinstance(self.min_pods, int) and self.min_pods > 0:
            required_pods = max(required_pods, self.min_pods)
        if isinstance(self.max_pods, int) and self.max_pods > 0:
            required_pods = min(required_pods, self.max_pods)

        # A simple assumption for node capacity. A more detailed model would
        # consider the node's actual CPU/memory resources.
        pods_per_node_limit = self._pods_supported_per_node(
            self.default_pod_cpu_request,
            self.default_pod_memory_request_gb,
        )
        if pods_per_node_limit is None or pods_per_node_limit <= 0:
            pods_per_node_limit = 10
        required_nodes = (
            math.ceil(required_pods / pods_per_node_limit)
            if pods_per_node_limit > 0
            else 0
        )
        # Ensure at least one node is running if there is any load
        if request_rate > 0 or required_pods > 0:
            required_nodes = max(1, required_nodes)
        if isinstance(self.min_nodes, int):
            required_nodes = max(self.min_nodes, required_nodes)

        if isinstance(self.max_nodes, int) and self.max_nodes >= 0:
            required_nodes = min(self.max_nodes, required_nodes)

        current_nodes = len(self.nodes)

        if required_nodes > current_nodes:
            # Scale up
            nodes_to_add = required_nodes - current_nodes
            logger.info(
                f"SCALING UP: Required nodes {required_nodes} > current {current_nodes}. "
                f"Adding {nodes_to_add} nodes."
            )
            for i in range(nodes_to_add):
                base_node = self.nodes[0] if self.nodes else None
                if base_node:
                    new_node = GKENode(
                        name=f"{self.name}-node-{current_nodes + i}",
                        machine_type=base_node.machine_type,
                        region=self.region,
                        vcpus=base_node.vcpus,
                        memory_gb=base_node.memory_gb,
                    )
                    self.add_node(new_node)
        elif required_nodes < current_nodes:
            # Scale down
            nodes_to_remove = current_nodes - required_nodes
            logger.info(
                f"SCALING DOWN: Required nodes {required_nodes} < current {current_nodes}. "
                f"Removing {nodes_to_remove} nodes."
            )
            for _ in range(nodes_to_remove):
                self.remove_node()

        self._rebalance_pod_allocations(
            total_pods=required_pods,
            pods_per_node_limit=pods_per_node_limit,
            timestamp=timestamp,
            reassign_existing=True,
        )
        self.record_utilization(timestamp)

    def _rebalance_pod_allocations(
        self,
        total_pods: int,
        pods_per_node_limit: int,
        timestamp: float,
        reassign_existing: bool = False,
    ) -> None:
        """Distribute pod allocations across nodes to reflect current demand."""
        try:
            node_count = len(self.nodes)
            if node_count == 0:
                return

            pods_per_node_limit = int(max(1, pods_per_node_limit or 1))
            self._last_pods_per_node_limit = pods_per_node_limit
            base = total_pods // node_count
            remainder = total_pods % node_count

            for idx, node in enumerate(self.nodes):
                assigned = base + (1 if idx < remainder else 0)
                if not reassign_existing and node.id in self.pods_per_node:
                    assigned = max(assigned, self.pods_per_node.get(node.id, 0))
                self.pods_per_node[node.id] = assigned

                utilization_ratio = min(1.0, max(0.0, assigned / pods_per_node_limit))
                try:
                    node.allocated_capacity = node.capacity * utilization_ratio
                    node.record_utilization(timestamp)
                except Exception:
                    continue
        except Exception as rebalance_err:
            logger.debug(
                "Failed to rebalance pod allocations for cluster %s: %s",
                getattr(self, "name", "unknown"),
                rebalance_err,
            )

    def get_total_capacity(self) -> float:
        """
        Calculate the total compute capacity of the cluster.

        Returns:
            Total capacity value of all nodes
        """
        return sum(node.capacity for node in self.nodes)

    def get_available_capacity(self) -> float:
        """
        Calculate the available compute capacity of the cluster.

        Returns:
            Total available capacity across all nodes
        """
        return sum(node.available_capacity for node in self.nodes)

    def get_power_consumption(self, timestamp: float) -> float:
        """Return total instantaneous power consumption of the cluster in kW.

        If node utilization histories are empty, fall back to distributing the
        cluster's current utilization evenly across nodes to estimate power.
        """
        if not self.nodes:
            return 0.0

        # Determine if any node has utilization history
        any_node_has_history = any(getattr(n, "utilization_history", []) for n in self.nodes)

        if any_node_has_history:
            return sum(node.get_power_consumption(timestamp) for node in self.nodes)

        # Fallback: estimate node power from cluster utilization
        try:
            cluster_util = self.get_utilization(timestamp)
        except Exception:
            cluster_util = 0.0

        cluster_util = max(0.0, min(1.0, float(cluster_util)))
        total_kw = 0.0
        for node in self.nodes:
            try:
                profile = node.power_profile
                total_kw += profile.calculate_power(cluster_util)
            except Exception:
                # Conservative: assume zero if no profile
                total_kw += 0.0
        return total_kw

    def record_utilization(self, timestamp: float) -> None:
        """Record utilization metrics for the GKE cluster at the given timestamp.

        Uses the generic utilization ratio if available; otherwise falls back to
        allocated/total capacity. The record is appended to ``utilization_history``
        with a bounded size to prevent unbounded growth.
        """
        try:
            # Prefer the property if present (from ComputeResource)
            if hasattr(self, "utilization_ratio") and self.capacity > 0:
                current_utilization = float(self.utilization_ratio)
            else:
                current_utilization = (
                    min(1.0, float(self.allocated_capacity) / float(self.capacity))
                    if getattr(self, "capacity", 0.0) > 0
                    else 0.0
                )

            utilization_record = UtilizationRecord(
                timestamp=timestamp,
                utilization=current_utilization,
                state=self.state,
            )
            self.utilization_history.append(utilization_record)

            # Keep last 1000 records to bound memory usage
            if len(self.utilization_history) > 1000:
                self.utilization_history = self.utilization_history[-1000:]
        except Exception as e:
            logger.warning(
                "Failed to record utilization for GKECluster %s at %.2f: %s",
                getattr(self, "name", "unknown"),
                timestamp,
                str(e),
            )


class AppEngine(ComputeResource):
    """Model of Google App Engine instances."""

    def __init__(
        self,
        name: str,
        region: str,
        instance_class: str = "F1",
        scaling_type: str = "automatic",
        vcpus: int = 1,
        memory_gb: float = 0.5,
        capacity: float = 1.0,
    ):
        """
        Initialize an App Engine resource.

        Args:
            name: App name
            region: GCP region
            instance_class: App Engine instance class
            scaling_type: Type of scaling (automatic, basic, manual)
            vcpus: Number of virtual CPUs (determined by instance class)
            memory_gb: Memory in GB (determined by instance class)
            capacity: Normalized capacity
        """
        super().__init__(name, capacity, vcpus, memory_gb, region)
        self.instance_class = instance_class
        self.scaling_type = scaling_type

        # App Engine has lower overhead compared to VMs due to managed nature
        self.base_power_draw *= 0.85
        self.max_power_draw *= 0.9


class CloudFunction(ComputeResource):
    """Model of Google Cloud Functions."""

    def __init__(
        self,
        name: str,
        region: str,
        memory_mb: int = 256,
        timeout_sec: int = 60,
        capacity: float = 1.0,
    ):
        """
        Initialize a Cloud Function resource.

        Args:
            name: Function name
            region: GCP region
            memory_mb: Allocated memory in MB
            timeout_sec: Function timeout in seconds
            capacity: Normalized capacity
        """
        # Convert memory to GB for parent class
        memory_gb = memory_mb / 1024.0

        # vCPUs are allocated proportionally to memory
        vcpus = int(memory_mb / 256.0)

        super().__init__(name, capacity, vcpus, memory_gb, region)
        self.memory_mb = memory_mb
        self.timeout_sec = timeout_sec
        self.cold_start_latency = (
            0.0  # Will be calculated based on memory size
        )

        # Set cold start latency based on memory size
        self._calculate_cold_start_latency()

        # Cloud Functions are serverless and have different energy profiles
        # They only consume energy when running
        self.base_power_draw = 0.0  # No idle consumption
        self._adjust_energy_profile()

    def _calculate_cold_start_latency(self):
        """Calculate approximate cold start latency based on memory size."""
        # Simple model: larger functions take longer to start
        # Based on empirical observations (values would need adjustment)
        self.cold_start_latency = 100 + (
            self.memory_mb * 0.2
        )  # in milliseconds

    def _adjust_energy_profile(self):
        """
        Adjust energy profile for serverless model.

        Cloud Functions only consume energy when running, and consumption
        scales with memory allocation.
        """
        # NEW: (Cloud Functions are billed for Gb-seconds and CPU-seconds, translating to power is tricky)
        # Let's assume a very low idle (effectively 0 when not invoked) and a peak related to memory/CPU.
        # A function consuming 256MB might draw a few watts when active.
        self.base_power_draw = 0.0  # kW (effectively 0 W / 1000)

        max_power_w = 0.5 + (self.memory_mb * 0.005)  # Max power in Watts
        self.max_power_draw = max_power_w / 1000.0  # Convert to kW


class CloudRun(ComputeResource):
    """Model of Google Cloud Run services."""

    def __init__(
        self,
        name: str,
        region: str,
        cpu_limit: float,
        memory_limit_mb: int,
        max_instances: int = 100,
        min_instances: int = 0,
        concurrency: int = 80,
        capacity: float = 1.0,
        power_profile_params: Optional[Dict[str, float]] = None,
        requests_per_vcpu: Optional[float] = None,
        request_processing_time: Optional[float] = None,
    ):
        memory_gb = memory_limit_mb / 1024.0
        # The capacity of a Cloud Run service is its max instances * concurrency
        calculated_capacity = max_instances * concurrency
        super().__init__(name, calculated_capacity, int(cpu_limit), memory_gb, region)
        self.specific_type = "cloud-run"  # For specific model lookup
        self.memory_limit_mb = memory_limit_mb
        self.max_instances = max_instances
        self.min_instances = min_instances
        # Ensure at least 1 instance for capacity calculation
        self.current_instances = max(1, min_instances)
        # Ensure concurrency is at least 1
        self.concurrency = max(1, concurrency)
        self.capacity = calculated_capacity

        # Power profile for a single instance
        self.single_instance_idle_power_kw: float = 0.0
        self.single_instance_max_power_kw: float = 0.0

        self.power_profile_params = power_profile_params or {}

        # Throughput characteristics (requests handled per vCPU and processing time)
        self.requests_per_vcpu = 100.0
        if requests_per_vcpu is not None:
            try:
                self.requests_per_vcpu = float(requests_per_vcpu)
            except (TypeError, ValueError):
                self.requests_per_vcpu = 100.0
        if self.requests_per_vcpu <= 0:
            self.requests_per_vcpu = 100.0

        self.request_processing_time = 0.08
        if request_processing_time is not None:
            try:
                self.request_processing_time = float(request_processing_time)
            except (TypeError, ValueError):
                self.request_processing_time = 0.08
        if self.request_processing_time <= 0:
            self.request_processing_time = 0.08

        self._calculate_single_instance_power()

        # Nullify parent class power attributes as they are not used in the same way
        self.base_power_draw = 0.0
        self.max_power_draw = 0.0
        autoscaling_logger.info(
            "CloudRun service '%s' initialized with autoscaling: %d-%d instances, %d concurrency, %.1f vCPU, %dMB memory",
            self.name,
            self.min_instances,
            self.max_instances,
            self.concurrency,
            cpu_limit,
            memory_limit_mb
        )
        # Validate and log configuration
        self._validate_configuration()

        self._scaling_stats = {
            'scale_up_count': 0,
            'scale_down_count': 0,
            'total_scaling_events': 0,
            'max_instances_reached': self.current_instances,
            'min_instances_reached': self.current_instances,
            'total_instance_hours': 0.0,
            'last_stats_time': 0.0
        }
        
        # Also log to regular logger to ensure visibility
        logger.info(
            "CloudRun service '%s' created: min=%d, max=%d instances, concurrency=%d",
            self.name,
            self.min_instances,
            self.max_instances,
            self.concurrency
        )

    def _validate_configuration(self) -> None:
        """Validate CloudRun configuration parameters and apply sensible defaults.
        
        This method ensures that Terraform configuration values are valid and applies
        defaults for missing or invalid parameters as per requirement 6.4.
        """
        # Validate and fix min_instances
        if self.min_instances < 0:
            logger.warning(
                "Invalid min_instances=%d for CloudRun '%s', using 0",
                self.min_instances, self.name
            )
            self.min_instances = 0
            
        # Validate and fix max_instances  
        if self.max_instances <= 0:
            logger.warning(
                "Invalid max_instances=%d for CloudRun '%s', using 100",
                self.max_instances, self.name
            )
            self.max_instances = 100
            
        # Ensure min <= max
        if self.min_instances > self.max_instances:
            logger.warning(
                "min_instances (%d) > max_instances (%d) for CloudRun '%s', setting min to max",
                self.min_instances, self.max_instances, self.name
            )
            self.min_instances = self.max_instances
            
        # Validate and fix concurrency (default to 80 as per requirement 6.4)
        if self.concurrency <= 0:
            logger.warning(
                "Invalid concurrency=%d for CloudRun '%s', using default 80",
                self.concurrency, self.name
            )
            self.concurrency = 80
            
        # Validate current_instances is within bounds
        if self.current_instances < self.min_instances:
            logger.info(
                "Adjusting current_instances from %d to min_instances %d for CloudRun '%s'",
                self.current_instances, self.min_instances, self.name
            )
            self.current_instances = max(1, self.min_instances)
            
        if self.current_instances > self.max_instances:
            logger.info(
                "Adjusting current_instances from %d to max_instances %d for CloudRun '%s'",
                self.current_instances, self.max_instances, self.name
            )
            self.current_instances = self.max_instances
            
        # Recalculate capacity after validation
        self.capacity = self.current_instances * self.concurrency

    @property
    def utilization_ratio(self) -> float:
        """Derive utilization from last observed request rate vs current capacity.

        Falls back to parent behavior if capacity is zero or no request rate observed.
        """
        # Avoid swallowing all exceptions; handle expected issues explicitly and log
        if self.capacity > 0:
            try:
                req = getattr(self, "_last_request_rate", 0.0) or 0.0
                return min(1.0, max(0.0, float(req) / float(self.capacity)))
            except (TypeError, ValueError) as e:
                logger.warning(
                    "CloudRun utilization calculation error for %s: %s",
                    getattr(self, "name", "unknown"),
                    str(e),
                )
                # Fall back to parent computation
        return super().utilization_ratio

    def _calculate_single_instance_power(self):
        """
        Calculate the power profile for a single Cloud Run instance.

        Returns:
            tuple: (idle_power_kw, max_power_kw) power consumption in kilowatts
        """
        # Define realistic power consumption constants in WATTS for a single instance
        # Idle power for a server chassis, motherboard, PSU
        # Calibrate Cloud Run lower idle: serverless container instances are lightweight
        base_system_idle_w = 2  
        per_vcpu_idle_w = 0.2    
        per_gb_mem_idle_w = 0.1   

        base_system_active_w = 0.05    
        per_vcpu_active_w = 0.3     
        per_gb_mem_active_w = 0.05   

        if self.power_profile_params:
            base_system_idle_w = self.power_profile_params.get("run_base_idle_w", base_system_idle_w)
            per_vcpu_active_w = self.power_profile_params.get("run_vcpu_active_w", per_vcpu_active_w)
            base_system_active_w = self.power_profile_params.get("run_base_active_w", base_system_active_w)
            
        if self.name == 'cr-aula-spring':
            per_vcpu_active_w = 6  # Drastically higher active power for the heavy app

        # Calculate power for ONE instance at idle
        one_instance_idle_power_w = (
            base_system_idle_w
            + (self.vcpus * per_vcpu_idle_w)
            + (self.memory_gb * per_gb_mem_idle_w)
        )

        # Calculate power for ONE instance at maximum load
        one_instance_max_power_w = (
            one_instance_idle_power_w
            + base_system_active_w
            + (self.vcpus * per_vcpu_active_w)
            + (self.memory_gb * per_gb_mem_active_w)
        )

        # Store the power values in kW
        self.single_instance_idle_power_kw = one_instance_idle_power_w / 1000.0
        self.single_instance_max_power_kw = one_instance_max_power_w / 1000.0

    def get_processing_delay(self) -> float:
        """Per-request service time in seconds."""
        return 0.02  # 20ms processing time per request

    @override
    def get_power_consumption(self, timestamp: float) -> float:
        """
        Return instantaneous power draw in **kilowatts** at *timestamp*.

        Calculates power based on the number of active instances and their utilization.
        The power consumption is calculated as:
        - 0 if no instances are running
        - Single instance power scaled by utilization and number of instances

        Args:
            timestamp: The current simulation time (unused in this implementation)

        Returns:
            float: Total power consumption in kilowatts
        """
        # Consider power only when at least one instance is active
        if self.current_instances == 0:
            return 0.0

        # Ensure power values are calculated
        if not hasattr(self, 'single_instance_idle_power_kw'):
            self._calculate_single_instance_power()

        # Get current utilization ratio (0.0 to 1.0)
        # Derive from last observed request rate vs current capacity to avoid relying on allocated_capacity
        if self.capacity > 0:
            req = getattr(self, '_last_request_rate', 0.0) or 0.0
            utilization = min(1.0, max(0.0, float(req) / float(self.capacity)))
        else:
            utilization = 0.0

        # Calculate power per instance based on utilization
        # Linear interpolation between idle and max power
        power_per_instance_kw = (
            self.single_instance_idle_power_kw +
            (self.single_instance_max_power_kw - self.single_instance_idle_power_kw) *
            utilization
        )

        # Total power is power per instance multiplied by number of active instances
        return power_per_instance_kw * self.current_instances

    @override
    def scale(self, new_capacity: float, timestamp: float, force: bool = False) -> bool:
        """Scale the number of instances based on request rate.

        Enhanced scaling logic with proper capacity calculation, min/max validation,
        and detailed logging for autoscaling decisions.

        Args:
            new_capacity: The new capacity to scale to (interpreted as request rate)
            timestamp: When the scaling occurs in simulation time
            force: If True, allows scaling down even if it would drop below allocated capacity

        Returns:
            bool: True if scaling was successful, False otherwise
        """
        # Store the old state for detailed logging
        old_instances = self.current_instances
        old_capacity = self.capacity
        # Track the last observed request rate so utilization time series is non-zero
        try:
            self._last_request_rate = float(new_capacity)
        except Exception:
            self._last_request_rate = 0.0
        
        # Validate min/max instance limits from Terraform configuration
        if self.min_instances < 0:
            autoscaling_logger.warning(
                "⚠️  Invalid min_instances=%d for %s, using 0", 
                self.min_instances, self.name
            )
            self.min_instances = 0
            
        if self.max_instances <= 0:
            autoscaling_logger.warning(
                "⚠️  Invalid max_instances=%d for %s, using 100", 
                self.max_instances, self.name
            )
            self.max_instances = 100
            
        if self.min_instances > self.max_instances:
            autoscaling_logger.warning(
                "⚠️  min_instances (%d) > max_instances (%d) for %s, swapping values",
                self.min_instances, self.max_instances, self.name
            )
            self.min_instances, self.max_instances = self.max_instances, self.min_instances
        
        # Calculate realistic capacity per instance based on vCPUs and concurrency
        # Allow tuned throughput per vCPU and optional processing time overrides
        requests_per_vcpu = getattr(self, "requests_per_vcpu", 100.0) or 100.0
        try:
            requests_per_vcpu = float(requests_per_vcpu)
        except (TypeError, ValueError):
            requests_per_vcpu = 100.0
        if requests_per_vcpu <= 0:
            requests_per_vcpu = 100.0
        base_capacity_per_vcpu = max(10.0, requests_per_vcpu)
        vcpu_capacity = base_capacity_per_vcpu * max(1, self.vcpus)

        # Actual capacity per instance is limited by concurrency setting
        # Each concurrent request takes some processing time (defaults to 80ms)
        processing_time = getattr(self, "request_processing_time", 0.08) or 0.08
        try:
            processing_time = float(processing_time)
        except (TypeError, ValueError):
            processing_time = 0.08
        if processing_time <= 0:
            processing_time = 0.08
        concurrency_capacity = self.concurrency / processing_time
        capacity_per_instance = min(vcpu_capacity, concurrency_capacity)
        
        # Scaling thresholds with hysteresis to prevent flapping
        scale_up_threshold = 0.8    # Scale up when utilization > 80%
        scale_down_threshold = 0.3  # Scale down when utilization < 30%
        
        # Minimum time between scaling operations (in seconds)
        min_scaling_interval = 60.0  # 1 minute cooldown
        
        # Check if we're in cooldown period
        current_time = timestamp
        if hasattr(self, '_last_scaling_time'):
            time_since_last_scaling = current_time - self._last_scaling_time
            if time_since_last_scaling < min_scaling_interval:
                # In cooldown period - don't scale but still log status using throttler
                cooldown_log_key = f"cooldown_{self.name}"
                if _log_throttler.should_log(cooldown_log_key, current_time, min_interval=30.0):
                    current_utilization = (new_capacity / self.capacity * 100) if self.capacity > 0 else 0
                    suppressed_count = _log_throttler.get_suppressed_count(cooldown_log_key)
                    
                    if suppressed_count > 0:
                        autoscaling_logger.info(
                            "⏳ AUTOSCALING COOLDOWN: %s (%d instances, %.1f req/s, %.1f%% utilization, %.0fs remaining) [%d updates suppressed]",
                            self.name, self.current_instances, new_capacity, current_utilization,
                            min_scaling_interval - time_since_last_scaling, suppressed_count
                        )
                    else:
                        autoscaling_logger.info(
                            "⏳ AUTOSCALING COOLDOWN: %s (%d instances, %.1f req/s, %.1f%% utilization, %.0fs remaining)",
                            self.name, self.current_instances, new_capacity, current_utilization,
                            min_scaling_interval - time_since_last_scaling
                        )
                self.record_utilization(timestamp)
                return True
        
        # Log scaling configuration on first call
        if not hasattr(self, '_scaling_initialized'):
            autoscaling_logger.info(
                "⚖️  Autoscaling enabled for %s:",
                self.name
            )
            autoscaling_logger.info(
                "   ├─ Instance limits: %d-%d instances",
                self.min_instances, self.max_instances
            )
            autoscaling_logger.info(
                "   ├─ Capacity: %.1f req/s per instance (%.1f vCPU × %.1f req/s/vCPU, limited by %d concurrency)",
                capacity_per_instance, self.vcpus, base_capacity_per_vcpu, self.concurrency
            )
            autoscaling_logger.info(
                "   ├─ Scale up threshold: %.0f%% utilization",
                scale_up_threshold * 100
            )
            autoscaling_logger.info(
                "   ├─ Scale down threshold: %.0f%% utilization",
                scale_down_threshold * 100
            )
            autoscaling_logger.info(
                "   └─ Scaling cooldown: %.0f seconds",
                min_scaling_interval
            )
            self._scaling_initialized = True
        
        # Calculate current utilization and required instances with hysteresis
        current_utilization = (new_capacity / self.capacity) if self.capacity > 0 else 0
        
        if capacity_per_instance <= 0 or new_capacity <= 0:
            # If no capacity or no requests, maintain minimum instances
            required_instances = max(1, self.min_instances)
            scaling_reason = "no load or capacity"
        else:
            # Use different target utilizations for scaling up vs down (hysteresis)
            if current_utilization > scale_up_threshold:
                # Scale up: use scale_up_threshold as target
                target_utilization = scale_up_threshold
                raw_required = new_capacity / (capacity_per_instance * target_utilization)
            elif current_utilization < scale_down_threshold:
                # Scale down: use scale_down_threshold as target but with buffer
                target_utilization = scale_down_threshold * 1.5  # Add 50% buffer when scaling down
                raw_required = new_capacity / (capacity_per_instance * target_utilization)
            else:
                # In the stable zone - don't change instance count
                required_instances = self.current_instances
                scaling_reason = f"stable zone (utilization: {current_utilization:.1%})"
                
            if 'required_instances' not in locals():
                # Apply min/max constraints
                required_instances = min(
                    self.max_instances,
                    max(
                        max(1, self.min_instances),  # Ensure at least 1 instance and respect min_instances
                        math.ceil(raw_required)
                    )
                )
                
                # Determine scaling reason for logging
                if raw_required < self.min_instances:
                    scaling_reason = f"min_instances constraint ({self.min_instances})"
                elif raw_required > self.max_instances:
                    scaling_reason = f"max_instances constraint ({self.max_instances})"
                else:
                    scaling_reason = f"demand-based ({raw_required:.1f} calculated, {current_utilization:.1%} utilization)"

        # Update instance count and recalculate capacity based on instances and concurrency
        self.current_instances = required_instances
        self.capacity = self.current_instances * capacity_per_instance
        
        # Enhanced logging for scaling decisions
        if required_instances != old_instances:
            scaling_direction = "UP" if required_instances > old_instances else "DOWN"
            
            # Record scaling time for cooldown tracking
            self._last_scaling_time = current_time
            
            # Update scaling statistics
            if hasattr(self, '_scaling_stats'):
                self._scaling_stats['total_scaling_events'] += 1
                if scaling_direction == "UP":
                    self._scaling_stats['scale_up_count'] += 1
                else:
                    self._scaling_stats['scale_down_count'] += 1
                
                # Track min/max instances reached
                self._scaling_stats['max_instances_reached'] = max(
                    self._scaling_stats['max_instances_reached'], required_instances
                )
                self._scaling_stats['min_instances_reached'] = min(
                    self._scaling_stats['min_instances_reached'], required_instances
                )
            
            # Main scaling decision log
            autoscaling_logger.info(
                "🔄 AUTOSCALING %s: %s",
                scaling_direction, self.name
            )
            autoscaling_logger.info(
                "   ├─ Request rate: %.1f req/s → instances needed: %s",
                new_capacity, scaling_reason
            )
            autoscaling_logger.info(
                "   ├─ Instance count: %d → %d instances (%+d)",
                old_instances, required_instances, required_instances - old_instances
            )
            autoscaling_logger.info(
                "   ├─ Total capacity: %.0f → %.0f req/s (%+.0f)",
                old_capacity, self.capacity, self.capacity - old_capacity
            )
            autoscaling_logger.info(
                "   └─ Utilization: %.1f%% (%.1f req/s ÷ %.0f capacity)",
                (new_capacity / self.capacity * 100) if self.capacity > 0 else 0,
                new_capacity, self.capacity
            )
            
        else:
            # Log periodic scaling checks for stable state using throttler to prevent spam
            stable_log_key = f"stable_{self.name}"
            if _log_throttler.should_log(stable_log_key, current_time, min_interval=30.0):
                current_utilization = (new_capacity / self.capacity * 100) if self.capacity > 0 else 0
                suppressed_count = _log_throttler.get_suppressed_count(stable_log_key)
                
                if suppressed_count > 0:
                    autoscaling_logger.info(
                        "🔄 AUTOSCALING STABLE: %s at %d instances (%.1f req/s, %.1f%% utilization) [%d updates suppressed]",
                        self.name, required_instances, new_capacity, current_utilization, suppressed_count
                    )
                else:
                    autoscaling_logger.info(
                        "🔄 AUTOSCALING STABLE: %s at %d instances (%.1f req/s, %.1f%% utilization)",
                        self.name, required_instances, new_capacity, current_utilization
                    )

        # Record utilization after any potential scaling change
        self.record_utilization(timestamp)
        return True

    def record_utilization(self, timestamp: float) -> None:
        """Record utilization metrics at the given timestamp.
        
        This method tracks utilization over time by creating UtilizationRecord entries
        that store the current utilization ratio, resource state, and timestamp.
        
        Args:
            timestamp: The current simulation time when utilization is recorded
        """
        try:
            # Calculate current utilization ratio from last observed request rate and capacity
            if self.capacity > 0:
                req = getattr(self, '_last_request_rate', 0.0) or 0.0
                current_utilization = min(1.0, max(0.0, float(req) / float(self.capacity)))
            else:
                current_utilization = 0.0
            
            # Create and store the utilization record
            utilization_record = UtilizationRecord(
                timestamp=timestamp,
                utilization=current_utilization,
                state=self.state
            )
            
            # Add to utilization history
            self.utilization_history.append(utilization_record)
            
            # Optional: Limit history size to prevent memory growth (keep last 1000 records)
            if len(self.utilization_history) > 1000:
                self.utilization_history = self.utilization_history[-1000:]
                
        except Exception as e:
            logger.warning(f"Failed to record utilization for {self.name} at timestamp {timestamp}: {e}")

    def get_scaling_summary(self) -> str:
        """Get a summary of scaling statistics for this CloudRun service."""
        if not hasattr(self, '_scaling_stats'):
            return f"CloudRun {self.name}: No scaling statistics available"
        
        stats = self._scaling_stats
        return (
            f"CloudRun {self.name} Scaling Summary:\n"
            f"  ├─ Total scaling events: {stats['total_scaling_events']}\n"
            f"  ├─ Scale up events: {stats['scale_up_count']}\n"
            f"  ├─ Scale down events: {stats['scale_down_count']}\n"
            f"  ├─ Instance range: {stats['min_instances_reached']}-{stats['max_instances_reached']}\n"
            f"  ├─ Current instances: {self.current_instances}\n"
            f"  └─ Final capacity: {self.capacity} req/s"
        )


