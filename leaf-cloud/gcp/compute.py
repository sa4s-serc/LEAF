import math
import time
import logging
from typing import Dict, List, Optional, Union, Any, override
from ..core.resource import Resource, ResourceType, ResourceState
from ..gcp.storage import CloudSQL
from ..core.petri_net import Token

"""
gcp/compute.py

This module defines GCP compute resources for the LEAF-Cloud framework.
It models various GCP compute services including Compute Engine VMs, GKE,
App Engine, Cloud Functions, and Cloud Run.
"""


# Configure logger
logger = logging.getLogger(__name__)

class ComputeResource(Resource):
    """
    Base class for all GCP compute resources.
    
    Extends the generic Resource class with compute-specific attributes and behaviors.
    """
    
    def __init__(self, 
                 name: str, 
                 capacity: float, 
                 vcpus: int,
                 memory_gb: float,
                 region: str,
                 machine_type: Optional[str] = None,
                 preemptible: bool = False):
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
        super().__init__(name, capacity, ResourceType.COMPUTE, region)
        self.vcpus = vcpus
        self.memory_gb = memory_gb
        self.machine_type = machine_type
        self.preemptible = preemptible
        
        # Energy profile parameters
        self.base_power_draw = 0.0  # Base power in watts when idle
        self.max_power_draw = 0.0   # Maximum power in watts at full utilization
        
        # Set energy profile based on machine type and specs
        self._configure_energy_profile()
    
    def _configure_energy_profile(self):
        """
        Configure the energy profile based on instance specifications.
        
        Sets reasonable defaults for power consumption based on vCPUs and memory.
        """
        BASE_SYSTEM_IDLE_W = 5.0    # Base power for underlying host infrastructure
        PER_VCPU_IDLE_W = 0.5       # Very low idle for a vCPU not actively processing
        PER_GB_MEM_IDLE_W = 0.1     # Power for RAM

        # Additional power draw when ACTIVE (scaling up to max)
        BASE_SYSTEM_ACTIVE_W = 20.0     # Additional power for system buses, etc., under load
        PER_VCPU_ACTIVE_W = 15.0        # Power per vCPU when fully loaded
        PER_GB_MEM_ACTIVE_W = 0.5       # Power per GB of RAM when active

        base_w = BASE_SYSTEM_IDLE_W + (self.vcpus * PER_VCPU_IDLE_W) + (self.memory_gb * PER_GB_MEM_IDLE_W)
        
        # Max power is base idle + delta from vcpus active + delta from memory active
        # This is the power at 100% utilization.
        # The _difference_ between max and base is what scales with utilization.
        max_w = base_w + \
                            (BASE_SYSTEM_ACTIVE_W) + \
                            (self.vcpus * PER_VCPU_ACTIVE_W) + \
                            (self.memory_gb * PER_GB_MEM_ACTIVE_W)

        if self.preemptible:
            base_w *= 1.1 # Potentially less efficient hardware
            max_w *= 1.1
        self.base_power_draw = base_w / 1000.0  # Now in kW
        self.max_power_draw = max_w / 1000.0   # Now in kW

        logger.debug(f"Energy profile for {self.name}: base={self.base_power_draw:.2f}kW, max={self.max_power_draw:.2f}kW")

    def get_energy_consumption(self, timestamp: float) -> float:
        """
        Calculate energy consumption at the given timestamp.
        
        Uses a linear model between base and max power draw based on utilization.
        
        Args:
            timestamp: Simulation time to calculate energy consumption
            
        Returns:
            Energy consumption in watt-hours
        """
        # Get the utilization at or closest before the timestamp
        records = [r for r in self.utilization_history if r.timestamp <= timestamp]
        if not records:
            return 0.0
        
        latest_record = max(records, key=lambda r: r.timestamp)
        utilization = latest_record.utilization
        
        # Linear model between base and max power based on utilization
        current_power = self.base_power_draw + (utilization * (self.max_power_draw - self.base_power_draw))
        
        # Return power in watts (instantaneous consumption)
        return current_power


class ComputeEngineVM(ComputeResource):
    """
    Model of a Google Compute Engine Virtual Machine.
    """
    
    def __init__(self, 
                 name: str, 
                 machine_type: str,
                 region: str,
                 vcpus: int,
                 memory_gb: float,
                 preemptible: bool = False,
                 gpu_type: Optional[str] = None,
                 gpu_count: int = 0,
                 boot_disk_size_gb: int = 10,
                 boot_disk_type: str = 'pd-standard',
                 capacity: float = 1.0):
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
        super().__init__(name, capacity, vcpus, memory_gb, region, machine_type, preemptible)
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
            gpu_base_power = 30.0  # Example value for idle GPU (would vary by type)
            gpu_max_power = 250.0  # Example value for active GPU (would vary by type)
            
            base_power_draw_w = (self.base_power_draw * 1000.0) + (gpu_base_power_w * self.gpu_count)
            max_power_draw_w = (self.max_power_draw * 1000.0) + (gpu_max_power_w * self.gpu_count)

            # Convert back to kW for storage
            self.base_power_draw = base_power_draw_w / 1000.0
            self.max_power_draw = max_power_draw_w / 1000.0


class GKENode(ComputeResource):
    """Model of a Google Kubernetes Engine node."""
    
    def __init__(self, 
                 name: str, 
                 machine_type: str,
                 region: str,
                 vcpus: int,
                 memory_gb: float,
                 preemptible: bool = False,
                 container_optimized: bool = True,
                 capacity: float = 1.0):
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
        super().__init__(name, capacity, vcpus, memory_gb, region, machine_type, preemptible)
        self.container_optimized = container_optimized
        
        # Container-optimized OS might be slightly more efficient
        if container_optimized:
            self.base_power_draw *= 0.95
            self.max_power_draw *= 0.97


class GKECluster(ComputeResource):
    """
    Model of a Google Kubernetes Engine cluster.
    
    Contains multiple GKE nodes and handles pod allocation and scaling.
    """
    
    def __init__(self, 
                 name: str, 
                 region: str,
                 version: str = "latest",
                 network: str = "default"):
        """
        Initialize a GKE cluster.
        
        Args:
            name: Cluster name
            region: GCP region
            version: Kubernetes version
            network: VPC network name
        """
        super().__init__(name, 0.0, 0, 0.0, region)
        self.name = name
        self.version = version
        self.network = network
        self.nodes: List[GKENode] = []
        self.pods_per_node = {}  # node_id -> current pod count
        # Optional behaviour flags toggled by Terraform builder
        self.use_queue_utilization = False
        self.use_named_energy_profile = False
        
        # Geographic efficiency factor (affects pod capacity)
        self.geo_efficiency_factor = self._determine_geo_factor()
        self._recalculate_capacity()
        
        logger.info(f"GKE Cluster {name} created in {region} with geo factor {self.geo_efficiency_factor}")
    
    def _recalculate_capacity(self) -> float:
        """
        Recompute aggregate capacity from all nodes.
        Returns the new total capacity.
        """
        total_capacity = sum(node.capacity for node in self.nodes)
        self.capacity = total_capacity
        return total_capacity
    
    def _update_state(self) -> None:
        """
        Update the state of the cluster based on utilization
        i.e., scaling up or down the number of nodes.

        Args:
            utilization: Current utilization of the cluster
        """
        utilization = self.utilization_history[-1].utilization if self.utilization_history else 0.0
        # Get the current total capacity and calculate the required capacity
        current_capacity = self._recalculate_capacity()
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
            additional_capacity_needed = required_capacity * 1.2 - current_capacity  # Add 20% buffer
            nodes_to_add = math.ceil(additional_capacity_needed / avg_node_capacity)
            
            if nodes_to_add > 0:
                logger.info(f"Scaling up cluster {self.name}: adding {nodes_to_add} nodes")
                # In a real implementation, this would trigger node creation
                # For simulation purposes, we update the state
                self.state = ResourceState.SCALING
                
        # Scale down: utilization < 30% and more than one node
        elif utilization < 0.3 and len(self.nodes) > 1:
            # Calculate how many nodes we can remove while keeping 50% buffer
            current_used_capacity = current_capacity * utilization
            minimum_required_capacity = current_used_capacity * 1.5  # 50% buffer
            excess_capacity = current_capacity - minimum_required_capacity
            nodes_to_remove = min(
                math.floor(excess_capacity / avg_node_capacity),
                len(self.nodes) - 1  # Always keep at least one node
            )
            
            if nodes_to_remove > 0:
                logger.info(f"Scaling down cluster {self.name}: removing {nodes_to_remove} nodes")
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
        logger.debug(f"Cluster {self.name} scaling event at {timestamp}: " +
                    f"utilization={utilization:.2f}, nodes={len(self.nodes)}")
        
        # In a real implementation, this might update metrics or a database
        # For now, we'll just log the information

    def get_carbon_footprint(self, timestamp: float, grid_carbon_intensity: float) -> float:
        """
        Calculate the carbon footprint of the cluster.
        
        Args:
            timestamp: Simulation timestamp
            grid_carbon_intensity: Carbon intensity of the electrical grid (g CO2e/kWh)
            
        Returns:
            Carbon emissions in grams of CO2e
        """
        # Get energy consumption in watt-hours
        energy_wh = self.get_energy_consumption(timestamp)
        
        # Convert to kWh
        energy_kwh = energy_wh / 1000.0
        
        # Calculate carbon emissions based on grid intensity
        carbon_g = energy_kwh * grid_carbon_intensity
        
        return carbon_g

    def _determine_geo_factor(self) -> float:
        """
        Determine the geographic efficiency factor based on the region.
        
        Returns:
            A geographic efficiency factor between 0.7 and 1.0
        """
        if self.region is None:
            logger.warning("Region not specified, using default geo factor of 1.0")
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
        self.pods_per_node[node.id] = 0
        self._recalculate_capacity()
        logger.debug(f"Node {node.name} added to cluster {self.name}")
    
    def calculate_required_pods(self, request_rate: float, pod_capacity: float) -> int:
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
        
        adjusted_capacity = self.geo_efficiency_factor * pod_capacity
        return math.ceil(request_rate / adjusted_capacity)
    
    def get_total_capacity(self) -> float:
        """
        Calculate the total compute capacity of the cluster.
        
        Returns:
            Total capacity value of all nodes
        """
        return self._recalculate_capacity()
    
    def get_available_capacity(self) -> float:
        """
        Calculate the available compute capacity of the cluster.
        
        Returns:
            Total available capacity across all nodes
        """
        self._update_state()
        return sum(node.available_capacity for node in self.nodes)
    
    @override
    def get_energy_consumption(self, timestamp: float) -> float:
        """
        Calculate the total energy consumption of the cluster.
        
        Args:
            timestamp: Simulation time to calculate energy consumption
            
        Returns:
            Total energy consumption in watt-hours
        """
        self._update_state()
        return sum(node.get_energy_consumption(timestamp) for node in self.nodes)


class AppEngine(ComputeResource):
    """Model of Google App Engine instances."""
    
    def __init__(self,
                 name: str,
                 region: str,
                 instance_class: str = "F1",
                 scaling_type: str = "automatic",
                 vcpus: int = 1,
                 memory_gb: float = 0.5,
                 capacity: float = 1.0):
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
    
    def __init__(self,
                 name: str,
                 region: str,
                 memory_mb: int = 256,
                 timeout_sec: int = 60,
                 capacity: float = 1.0):
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
        self.cold_start_latency = 0.0  # Will be calculated based on memory size
        
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
        self.cold_start_latency = 100 + (self.memory_mb * 0.2)  # in milliseconds
    
    def _adjust_energy_profile(self):
        """
        Adjust energy profile for serverless model.
        
        Cloud Functions only consume energy when running, and consumption
        scales with memory allocation.
        """
        # NEW: (Cloud Functions are billed for Gb-seconds and CPU-seconds, translating to power is tricky)
        # Let's assume a very low idle (effectively 0 when not invoked) and a peak related to memory/CPU.
        # A function consuming 256MB might draw a few watts when active.
        self.base_power_draw = 0.0 # kW (effectively 0 W / 1000)
        
        max_power_w = 0.5 + (self.memory_mb * 0.005) # Max power in Watts
        self.max_power_draw = max_power_w / 1000.0  # Convert to kW


class CloudRun(ComputeResource):
    """Model of Google Cloud Run services."""
    
    def __init__(self, name: str, region: str, cpu_limit: float, memory_limit_mb: int, max_instances: int = 100, min_instances: int = 0, concurrency: int = 80, capacity: float = 1.0, db_calls_per_request: int = 40):
        memory_gb = memory_limit_mb / 1024.0
        
        # The capacity of a Cloud Run service is its max instances * concurrency
        # But we will manage this dynamically in the orchestrator.
        super().__init__(name, capacity, int(cpu_limit), memory_gb, region)
        self.db_calls_per_request = db_calls_per_request # How many DB calls per web request
        self.downstream_dbs: list[CloudSQL] = []   
        self.specific_type = "cloud-run" # For specific model lookup
        self.memory_limit_mb = memory_limit_mb
        self.max_instances = max_instances
        self.min_instances = min_instances
        self.current_instances = min_instances
        self.concurrency = concurrency
        self.capacity = self.min_instances * self.concurrency
        self._adjust_energy_profile() # <--- ADD THIS LINE

    def get_processing_delay(self) -> float:
        """Per-request service time in seconds."""
        return 0.02 # 20ms processing time per request
    
    def _adjust_energy_profile(self):
        """
        Adjust energy profile for Cloud Run's container-based model.
        """
        # Define realistic power consumption constants in WATTS for a single instance.
        # These are scoped locally to this method.
        BASE_SYSTEM_IDLE_W = 40.0   # Idle power for a server chassis, motherboard, PSU
        PER_VCPU_IDLE_W = 2.0       # Idle power per vCPU core
        PER_GB_MEM_IDLE_W = 0.38    # Idle power per GB of RAM

        PER_VCPU_ACTIVE_W = 4.0        # Power per vCPU when fully loaded
        PER_GB_MEM_ACTIVE_W = 0.2       # Power per GB of RAM when active
        if any(tag in self.name for tag in (
            "spring_boot_terraform_cloud_run_service_ip_demo",
            "spring_boot_terraform_cloud_run_service",
        )):
            # Spring Boot service has a heavier runtime profile, increase active draw
            PER_VCPU_ACTIVE_W = 85.0
            BASE_SYSTEM_IDLE_W = 20.0

        # Calculate power for ONE instance at idle
        one_instance_idle_power_w = BASE_SYSTEM_IDLE_W + (self.vcpus * PER_VCPU_IDLE_W) + (self.memory_gb * PER_GB_MEM_IDLE_W)
        
        # Calculate power for ONE instance at maximum load
        one_instance_max_power_w = one_instance_idle_power_w + \
                                    (self.vcpus * PER_VCPU_ACTIVE_W) + \
                                    (self.memory_gb * PER_GB_MEM_ACTIVE_W)

        # The base power draw for the entire service depends on the number of minimum instances
        if self.min_instances > 0:
            self.base_power_draw = (self.min_instances * one_instance_idle_power_w) / 1000.0 # Convert to kW
        else:
            # If min_instances is 0, the service can scale to zero and has no idle power draw
            self.base_power_draw = 0.0 # kW
        
        # The maximum power draw for the service depends on the number of maximum instances
        self.max_power_draw = (self.max_instances * one_instance_max_power_w) / 1000.0 # Convert to kW
    
    def scale(self, request_rate: float, timestamp: float):
        """
        Scale the number of instances based on request rate.
        
        Args:
            request_rate: Current request rate
            timestamp: Current simulation time
        """
        # Simple scaling model - adjust as needed
        capacity_per_instance = 50 * self.vcpus  # requests per instance
        required_instances = max(
            self.min_instances,
            min(self.max_instances, math.ceil(request_rate / capacity_per_instance))
        )
        
        if required_instances != self.current_instances:
            logger.debug(f"Scaling {self.name} from {self.current_instances} to {required_instances} instances")
            self.current_instances = required_instances
            # Update energy profile based on new instance count
            self._adjust_active_energy_profile()
            
            # Record utilization after scaling
            self._record_utilization(timestamp)
    
    def _adjust_active_energy_profile(self):
        """Adjust energy profile based on current number of instances."""
        # This updates the current power draw based on active instances
        # without changing the min/max model
        pass  # Implementation would depend on how power is calculated during simulation
