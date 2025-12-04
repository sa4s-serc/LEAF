import logging
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
import warnings
from leaf_cloud.core.resource import Resource, ResourceType, ResourceState


"""
gcp/generic_component.py

This module defines a generic GCP component class that can be used
to approximate any Google Cloud Platform component that isn't explicitly
modeled elsewhere in the framework. It extends the core Resource class
with GCP-specific attributes and behaviors.
"""

# Configure logger
logger = logging.getLogger(__name__)


class GCPServiceCategory(Enum):
    """Enumeration of high-level GCP service categories"""

    COMPUTE = "compute"  # Compute Engine, GKE, Cloud Functions, etc.
    STORAGE = "storage"  # Cloud Storage, Cloud SQL, Firestore, etc.
    NETWORK = "network"  # VPC, Load Balancing, Cloud CDN, etc.
    SECURITY = "security"  # Cloud IAM, Security Command Center, etc.
    ANALYTICS = "analytics"  # BigQuery, Dataflow, Pub/Sub, etc.
    DATABASE = "database"  # Cloud SQL, Spanner, Bigtable, etc.
    SERVERLESS = "serverless"  # Cloud Functions, Cloud Run, etc.
    AIML = "aiml"  # Vertex AI, AutoML, etc.
    OTHER = "other"  # Any service that doesn't fit above categories


class GenericGCPComponent(Resource):
    """
    Generic Google Cloud Platform component class.

    This class extends the core Resource class with GCP-specific
    attributes and behaviors, providing a way to model any GCP service
    that isn't explicitly implemented elsewhere.
    """

    def __init__(
        self,
        name: str,
        capacity: float = 1.0,
        resource_type: ResourceType = ResourceType.GENERIC,
        region: str = "us-central1",
        zone: Optional[str] = None,
        service_category: GCPServiceCategory = GCPServiceCategory.OTHER,
        service_name: str = "generic-service",
        base_power_kw: float = 0.001,  # Base power in kW when idle
        # Power in kW when at 100% utilization
        dynamic_power_kw: Optional[float] = None,
        carbon_factor: float = 1.0,
        attributes: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize a new GCP component.

        Args:
            name: Unique name for the component
            capacity: Maximum capacity of the resource (normalized to 1.0)
            resource_type: The type of resource (compute, storage, etc.)
            region: GCP region where the resource is deployed
            zone: Specific zone within the region (if applicable)
            service_category: The category of GCP service
            service_name: The specific GCP service name
            base_power_kw: Base power consumption in kW when idle
            dynamic_power_kw: Additional power in kW when at 100% utilization. If None, defaults to 4 * base_power_kw.
            carbon_factor: Regional carbon intensity factor
            attributes: Additional service-specific attributes
        """
        super().__init__(
            name=name,
            resource_type=resource_type,
            region=region,
        )
        self.capacity = capacity
        self.zone = zone
        self.service_category = service_category
        self.service_name = service_name
        self.base_power_kw = base_power_kw
        # If dynamic_power_kw is not provided, default to 4x base_power_kw for backward compatibility
        self.dynamic_power_kw = (
            dynamic_power_kw
            if dynamic_power_kw is not None
            else base_power_kw * 4
        )
        self.carbon_factor = carbon_factor
        self.attributes = attributes or {}

        # Petri net integration attributes
        self.input_places: List[str] = []
        self.output_places: List[str] = []
        self.transitions: List[str] = []

        # Monitoring attributes
        self.last_monitored_time: Optional[float] = None
        self.monitoring_history: List[Dict] = []

        # Performance metrics
        # (timestamp, latency)
        self.latency_history: List[Tuple[float, float]] = []
        self.request_count = 0
        self.error_count = 0

        logger.info(
            f"GCP Component {self.name} ({self.service_name}) created in region {self.region}"
        )

    def get_power_consumption(self, timestamp: float) -> float:
        """
        Calculate power consumption at the given timestamp.

        For generic GCP components, power consumption is modeled as:
        P(kW) = base_power_kw + (utilization * dynamic_power_kw)

        Args:
            timestamp: Simulation time to calculate power consumption.

        Returns:
            Power consumption in kW for the resource.
        """
        utilization = self._get_utilization_at_time(timestamp)
        power = self.base_power_kw + (utilization * self.dynamic_power_kw)

        # Ensure power consumption doesn't exceed a reasonable maximum
        # (e.g., if base is 0, power would be 0, which is not realistic for an active service)
        return max(self.base_power_kw if utilization > 0 else 0, power)

    def _get_utilization_at_time(self, timestamp: float) -> float:
        """
        Get the resource utilization at a specific timestamp.

        Args:
            timestamp: Time at which to retrieve utilization

        Returns:
            Utilization ratio (0.0 to 1.0) at the specified time
        """
        history = self.utilization_history
        if not history:
            return 0.0
        # If last record is not after timestamp, return it
        if history[-1].timestamp <= timestamp:
            return history[-1].utilization
        # If timestamp is before first record, assume idle
        if history[0].timestamp > timestamp:
            return 0.0

        # Binary search for rightmost record with timestamp <= query
        lo, hi = 0, len(history) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if history[mid].timestamp <= timestamp:
                lo = mid + 1
            else:
                hi = mid - 1
        return history[hi].utilization

    def get_carbon_footprint(self, timestamp: float) -> float:
        """
        Calculate the carbon emission rate at the given timestamp.

        Carbon Rate = Power consumption (kW) * Regional carbon factor (gCO2/kWh)

        Args:
            timestamp: Simulation time to calculate carbon footprint.

        Returns:
            Carbon emissions rate (e.g., gCO2/hour) for the resource.
        """
        power_kw = self.get_power_consumption(timestamp)
        return power_kw * self.carbon_factor

    def register_petri_elements(
        self,
        input_places: List[str],
        output_places: List[str],
        transitions: List[str],
    ) -> None:
        """
        Register the Petri net elements associated with this component.

        Args:
            input_places: Names of input places
            output_places: Names of output places
            transitions: Names of transitions
        """
        self.input_places = input_places
        self.output_places = output_places
        self.transitions = transitions
        logger.debug(
            f"Registered Petri net elements for {self.name}: {len(input_places)} inputs, "
            f"{len(output_places)} outputs, {len(transitions)} transitions"
        )

    def record_utilization(self, timestamp: float) -> None:
        """Record utilization and cap history to prevent unbounded growth."""
        super().record_utilization(timestamp)
        if len(self.utilization_history) > 1000:
            self.utilization_history = self.utilization_history[-1000:]

    def record_latency(self, timestamp: float, latency_value: float) -> None:
        """
        Record a latency measurement.

        Args:
            timestamp: Simulation time when latency was measured
            latency_value: The measured latency value
        """
        self.latency_history.append((timestamp, latency_value))

    def record_request(self, is_error: bool = False) -> None:
        """
        Record a request processed by this component.

        Args:
            is_error: Whether the request resulted in an error
        """
        self.request_count += 1
        if is_error:
            self.error_count += 1

    def get_average_latency(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> float:
        """
        Calculate average latency over a time period.

        Args:
            start_time: Start of time period (default: beginning of simulation)
            end_time: End of time period (default: latest record)

        Returns:
            Average latency value
        """
        if not self.latency_history:
            return 0.0

        # Filter records by time range if specified
        records = self.latency_history
        if start_time is not None:
            records = [(t, l) for t, l in records if t >= start_time]
        if end_time is not None:
            records = [(t, l) for t, l in records if t <= end_time]

        if not records:
            return 0.0

        # Calculate average latency
        total_latency = sum(latency for _, latency in records)
        return total_latency / len(records)

    def get_error_rate(self) -> float:
        """
        Calculate the error rate of this component.

        Returns:
            Error rate as a percentage
        """
        if self.request_count == 0:
            return 0.0
        return (self.error_count / self.request_count) * 100.0

    def monitor(self, timestamp: float) -> Dict[str, Any]:
        """
        Collect monitoring data for this component.

        Args:
            timestamp: Simulation time when monitoring is performed

        Returns:
            Dictionary of monitoring data
        """
        self.last_monitored_time = timestamp

        monitoring_data = {
            "timestamp": timestamp,
            "name": self.name,
            "service": self.service_name,
            "region": self.region,
            "state": self.state.value,
            "utilization": self.utilization_ratio,
            "power_consumption_kw": self.get_power_consumption(timestamp),
            "carbon_footprint": self.get_carbon_footprint(timestamp),
            "request_count": self.request_count,
            "error_rate": self.get_error_rate(),
            "average_latency": self.get_average_latency(),
        }

        self.monitoring_history.append(monitoring_data)
        return monitoring_data

    def get_monitoring_report(self) -> Dict[str, Any]:
        """
        Generate a comprehensive monitoring report for this component.

        Returns:
            Dictionary containing monitoring report data
        """
        if not self.monitoring_history:
            return {
                "name": self.name,
                "status": "No monitoring data available",
            }

        # Calculate aggregated metrics
        avg_utilization = sum(
            m["utilization"] for m in self.monitoring_history
        ) / len(self.monitoring_history)
        avg_power = sum(
            m["power_consumption_kw"] for m in self.monitoring_history
        ) / len(self.monitoring_history)
        avg_carbon = sum(
            m["carbon_footprint"] for m in self.monitoring_history
        ) / len(self.monitoring_history)

        return {
            "name": self.name,
            "service": self.service_name,
            "category": self.service_category.value,
            "region": self.region,
            "zone": self.zone,
            "current_state": self.state.value,
            "capacity": self.capacity,
            "allocated_capacity": self.allocated_capacity,
            "average_utilization": avg_utilization,
            "average_power_consumption_kw": avg_power,
            "average_carbon_footprint": avg_carbon,
            "total_requests": self.request_count,
            "error_rate": self.get_error_rate(),
            "average_latency": self.get_average_latency(),
            "monitoring_records": len(self.monitoring_history),
            "creation_time": self.creation_time.isoformat(),
        }

    def __str__(self) -> str:
        return f"GCP::{self.service_name} ({self.name}): {self.allocated_capacity}/{self.capacity} [{self.state.value}]"

    def __repr__(self) -> str:
        return (
            f"GenericGCPComponent(id={self.id}, name={self.name}, "
            f"service={self.service_name}, region={self.region}, "
            f"capacity={self.capacity}, allocated={self.allocated_capacity}, "
            f"state={self.state.value})"
        )
