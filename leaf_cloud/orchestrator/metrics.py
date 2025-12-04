"""
Metrics collection and processing for the LEAF-Cloud Orchestrator.

This module provides functionality for collecting, processing, and reporting
metrics from the LEAF-Cloud simulation, including:
- Resource utilization metrics
- Energy consumption measurements
- Carbon emissions calculations
- System performance statistics
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple, TypeAlias, Union, final, override

from pydantic import BaseModel

from leaf_cloud.utils.results import SimulationResult
from leaf_cloud.leaf_types import (
    ResourceMetrics,
    SimulationStatus,
    TokenFlowLogEntry,
)



# Import config and models at runtime to avoid circular imports
LEAFCloudConfig: TypeAlias = Any
CarbonModel: TypeAlias = Any
EnergyModel: TypeAlias = Any

logger = logging.getLogger(__name__)

# Type aliases for better code readability
ResourceID: TypeAlias = str
Timestamp: TypeAlias = float
UtilizationRatio: TypeAlias = float  # 0.0 to 1.0
PowerWatts: TypeAlias = float
CarbonKgCO2e: TypeAlias = float
EnergyKWh: TypeAlias = float

# Lazy imports to avoid circular dependencies
def _import_leaf_cloud_config() -> Any:
    from leaf_cloud.config import LEAFCloudConfig
    return LEAFCloudConfig

def _import_carbon_model() -> Any:
    from leaf_cloud.models.carbon import CarbonModel
    return CarbonModel

def _import_energy_model() -> Any:
    from leaf_cloud.models.energy import EnergyModel
    return EnergyModel

@dataclass
class ResourceMetric:
    """Type definition for a single metric data point.
    
    Attributes:
        time: Timestamp of the metric
        value: Numeric value or state string
    """
    time: Timestamp
    value: Union[float, str]


class ResourceStats:
    """Container for resource statistics with type-safe operations.
    
    This class provides thread-safe operations for recording and managing
    time-series metrics for cloud resources.
    
    Attributes:
        resource_id: Unique identifier for the resource
        metrics: Dictionary of metric types to lists of ResourceMetric objects
    """
    def __init__(self, resource_id: str):
        self.resource_id = resource_id
        self.metrics: Dict[str, List[ResourceMetric]] = defaultdict(list)
    
    def add_utilization(self, timestamp: Timestamp, value: UtilizationRatio, /) -> None:
        """Add a utilization measurement.
        
        Args:
            timestamp: Simulation time when the measurement was taken
            value: Utilization ratio (0.0 to 1.0)
            
        Raises:
            ValueError: If value is not between 0.0 and 1.0
        """
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"Utilization must be between 0.0 and 1.0, got {value}")
        self.metrics['utilization'].append(ResourceMetric(time=timestamp, value=value))
    
    def add_power_consumption(self, timestamp: Timestamp, value: PowerWatts, /) -> None:
        """Add a power consumption measurement.
        
        Args:
            timestamp: Simulation time when the measurement was taken
            value: Power consumption in watts
            
        Raises:
            ValueError: If value is negative
        """
        if value < 0:
            raise ValueError(f"Power consumption cannot be negative, got {value}W")
        self.metrics['power_consumption'].append(ResourceMetric(time=timestamp, value=value))
    
    def add_carbon_emissions(self, timestamp: Timestamp, value: CarbonKgCO2e, /) -> None:
        """Add a carbon emissions measurement.
        
        Args:
            timestamp: Simulation time when the measurement was taken
            value: Carbon emissions in kgCO2e
            
        Raises:
            ValueError: If value is negative
        """
        if value < 0:
            raise ValueError(f"Carbon emissions cannot be negative, got {value} kgCO2e")
        self.metrics['carbon_emissions'].append(ResourceMetric(time=timestamp, value=value))
    
    def add_state_change(self, timestamp: Timestamp, state: str, /) -> None:
        """Add a state change event.
        
        Args:
            timestamp: Simulation time when the state change occurred
            state: New state of the resource
            
        Raises:
            ValueError: If state is empty or not a string
        """
        if not isinstance(state, str) or not state:
            raise ValueError("State must be a non-empty string")
        self.metrics['state_changes'].append(ResourceMetric(time=timestamp, value=state))

    def add_replica_count(self, timestamp: Timestamp, replicas: float, /) -> None:
        """Record the replica count for the resource at a given time."""
        if replicas < 0:
            raise ValueError(f"Replica count cannot be negative, got {replicas}")
        self.metrics['replica_count'].append(ResourceMetric(time=timestamp, value=replicas))
        



class MetricsCollector:
    """Collects and processes metrics during LEAF-Cloud simulation.
    
    This class is responsible for:
    - Collecting resource utilization, power, and carbon metrics
    - Tracking token flow events
    - Calculating aggregate statistics
    - Generating simulation reports
    
    Attributes:
        config: LEAF-Cloud configuration
        resource_stats: Dictionary mapping resource IDs to their metrics
        token_flow_log: List of token flow events
    """
    
    def __init__(self, config: Any) -> None:
        """Initialize the metrics collector.
        
        Args:
            config: LEAF-Cloud configuration object
            
        Raises:
            TypeError: If config is not a LEAFCloudConfig instance
        """
        # Lazy import to avoid circular imports
        LEAFCloudConfig = _import_leaf_cloud_config()
        
        if not isinstance(config, LEAFCloudConfig):
            raise TypeError("config must be a LEAFCloudConfig instance")
            
        self.config = config
        self.resource_stats: dict[ResourceID, ResourceStats] = {}
        self.token_flow_log: list[TokenFlowLogEntry] = []
        self._initialized = False
        logger.debug("Initialized MetricsCollector with config: %s", config)
    
    def initialize(self, resource_ids: list[ResourceID], /) -> None:
        """Initialize metrics collection for the given resource IDs.
        
        Args:
            resource_ids: List of resource identifiers to track
            
        Raises:
            ValueError: If resource_ids is empty or contains duplicates
            TypeError: If resource_ids contains non-string values
        """
        if not resource_ids:
            raise ValueError("At least one resource ID must be provided")
            
        if len(resource_ids) != len(set(resource_ids)):
            counts = defaultdict(int)
            for rid in resource_ids:
                counts[rid] += 1
            dupes = [rid for rid, count in counts.items() if count > 1]
            raise ValueError(f"Resource IDs must be unique. Duplicates: {', '.join(dupes)}")
            
        if not all(isinstance(rid, str) for rid in resource_ids):
            raise TypeError("All resource IDs must be strings")
            
        self.resource_stats = {rid: ResourceStats(resource_id=rid) for rid in resource_ids}
        self._initialized = True
        logger.info("Initialized metrics collection for %d resources", len(resource_ids))
    
    def collect_periodic_stats(
        self, 
        resource_mapping: dict[ResourceID, Any], 
        energy_model: Any | None = None,
        carbon_model: Any | None = None,
        timestamp: Timestamp = 0.0,
        /,
    ) -> None:
        """Collect periodic statistics for all resources.
        
        This method should be called at regular intervals during the simulation
        to collect metrics for all tracked resources.
        
        Args:
            resource_mapping: Dictionary mapping resource IDs to resource objects
            energy_model: Optional energy model for power calculations
            carbon_model: Optional carbon model for emissions calculations
            timestamp: Current simulation time
            
        Raises:
            RuntimeError: If metrics collector is not initialized
            TypeError: If timestamp is not a number
        """
        if not self._initialized:
            raise RuntimeError("Metrics collector must be initialized before collecting stats")
            
        if not isinstance(timestamp, (int, float)):
            raise TypeError(f"Timestamp must be a number, got {type(timestamp).__name__}")
            
        # Low-noise periodic trace
        if int(timestamp) <= 1:
            logger.debug("Collecting periodic stats at timestamp: %f", timestamp)
        
        # Import models only when needed
        if energy_model is not None or carbon_model is not None:
            EnergyModel = _import_energy_model()
            CarbonModel = _import_carbon_model()
            
            # Validate model types if provided
            if energy_model is not None and not isinstance(energy_model, EnergyModel):
                raise TypeError(f"energy_model must be an instance of EnergyModel, got {type(energy_model).__name__}")
                
            if carbon_model is not None and not isinstance(carbon_model, CarbonModel):
                raise TypeError(f"carbon_model must be an instance of CarbonModel, got {type(carbon_model).__name__}")
        
        for resource_id, resource in resource_mapping.items():
            try:
                # Initialize stats for new resources
                if resource_id not in self.resource_stats:
                    logger.debug("Discovered new resource: %s", resource_id)
                    self.resource_stats[resource_id] = ResourceStats(resource_id)
                    
                stats = self.resource_stats[resource_id]
                
                # Record utilization (default to 0.0 if not available)
                utilization = float(getattr(resource, 'utilization_ratio', 0.0))
                target_ids = None
                try:
                    attrs = getattr(resource, "attributes", None)
                    if isinstance(attrs, dict):
                        target_ids = attrs.get("target_ids")
                except Exception:
                    target_ids = None

                if target_ids:
                    aggregated_util = 0.0
                    total_weight = 0.0
                    targets_found = False
                    for target_id in target_ids:
                        target_resource = resource_mapping.get(target_id)
                        if target_resource is None:
                            continue
                        targets_found = True
                        target_util = float(getattr(target_resource, "utilization_ratio", 0.0))
                        try:
                            weight = float(getattr(target_resource, "capacity", 0.0) or 0.0)
                        except Exception:
                            weight = 0.0
                        if weight <= 0.0:
                            weight = 1.0
                        aggregated_util += target_util * weight
                        total_weight += weight
                    if targets_found and total_weight > 0.0:
                        utilization = max(0.0, min(1.0, aggregated_util / total_weight))

                stats.add_utilization(timestamp, utilization)
                # Brief, low-noise logging for early ticks to diagnose zero utilization
                try:
                    if len(stats.metrics.get('utilization', [])) <= 5:
                        logger.debug("MetricsCollector: t=%.3f res=%s utilization=%.3f", float(timestamp), resource_id, float(utilization))
                except Exception:
                    pass
                
                # Record power consumption preferring resource's own method, then energy model
                # 1) If resource exposes get_power_consumption() returning kW, use it (convert to W)
                # 2) Else if energy model is available, derive from utilization
                power_sampled = False
                if hasattr(resource, 'get_power_consumption') and callable(getattr(resource, 'get_power_consumption')):
                    try:
                        # Many resource implementations return kW; convert to W
                        kw = float(resource.get_power_consumption(timestamp))
                        power_watts = kw * 1000.0
                        stats.add_power_consumption(timestamp, power_watts)
                        power_sampled = True
                    except Exception as e:
                        logger.debug("Resource get_power_consumption failed for %s: %s", resource_id, str(e))
                        # Fall through to energy_model path if available
                if energy_model is not None and not power_sampled:
                    try:
                        # Prefer a direct calculate_power if available on the model
                        if hasattr(energy_model, 'calculate_power') and callable(getattr(energy_model, 'calculate_power')):
                            power_watts = float(energy_model.calculate_power(
                                resource=resource,
                                utilization=utilization,
                                timestamp=timestamp
                            ))
                        else:
                            # Fallback: derive instantaneous power using the energy model's resource curve (kW → W)
                            # IMPORTANT: Do not multiply by logical throughput capacity (e.g., concurrency).
                            # Use physical instance count if available; otherwise 1.0
                            res_type = getattr(resource, 'specific_type', getattr(resource, 'resource_type', type(resource).__name__.lower()))
                            instances = getattr(resource, 'current_instances', None)
                            try:
                                capacity_factor = float(int(instances)) if instances is not None and int(instances) > 0 else 1.0
                            except Exception:
                                capacity_factor = 1.0
                            power_kw = float(energy_model.calculate_resource_energy(
                                utilization=utilization,
                                resource_type=res_type,
                                capacity=capacity_factor,
                            ))
                            power_watts = power_kw * 1000.0
                        if not (hasattr(energy_model, 'calculate_power') and callable(getattr(energy_model, 'calculate_power'))):
                            logger.debug("Fallback: computed power via energy curve for %s", resource_id)
                        stats.add_power_consumption(timestamp, power_watts)
                    except Exception as e:
                        logger.warning(
                            "Error calculating power for %s: %s", 
                            resource_id, 
                            str(e),
                            exc_info=logger.isEnabledFor(logging.DEBUG)
                        )
                
                # Record carbon emissions if carbon model is available and we have power data
                power_series = stats.metrics.get('power_consumption', [])
                if carbon_model is not None and power_series:
                    try:
                        # Convert last power sample (W) over small interval (s) to energy (kWh)
                        interval_seconds = 0.1
                        last_power_w = float(power_series[-1].value)
                        energy_kwh = (last_power_w / 1000.0) * (interval_seconds / 3600.0)
                        region = getattr(resource, 'region', getattr(self.config, 'default_region', 'unknown'))
                        carbon = carbon_model.calculate_resource_carbon(energy_kwh, region)
                        stats.add_carbon_emissions(timestamp, carbon)
                        if int(timestamp) <= 1:
                            logger.debug("Fallback: computed carbon via energy slice for %s", resource_id)
                    except Exception as e:
                        logger.warning(
                            "Error calculating carbon for %s: %s", 
                            resource_id, 
                            str(e),
                            exc_info=logger.isEnabledFor(logging.DEBUG)
                        )
                
                # Record state changes if the resource has a state attribute
                if hasattr(resource, 'state'):
                    current_state = str(getattr(resource, 'state', 'unknown'))
                    state_series = stats.metrics.get('state_changes', [])
                    if not state_series or state_series[-1].value != current_state:
                        stats.add_state_change(timestamp, current_state)
                        
            except Exception as e:
                logger.error(
                    "Unexpected error processing resource %s: %s", 
                    resource_id, 
                    str(e),
                    exc_info=True
                )
    
    def add_token_flow_event(self, event: TokenFlowLogEntry, /) -> None:
        """Add a token flow event to the log.
        
        Args:
            event: TokenFlowLogEntry containing event details
            
        Raises:
            TypeError: If event is not a TokenFlowLogEntry
        """
        if not isinstance(event, TokenFlowLogEntry):
            raise TypeError(f"event must be a TokenFlowLogEntry, got {type(event).__name__}")
            
        self.token_flow_log.append(event)
        logger.debug("Added token flow event for token %s at %s", 
                   getattr(event, 'token_id', 'unknown'),
                   getattr(event, 'timestamp', 'unknown'))
    
    
    def generate_report(
        self,
        config: LEAFCloudConfig,
        start_time: datetime,
        end_time: datetime,
        completed_tokens: int,
        active_tokens: int,
        error_count: int = 0,
        /,
    ) -> SimulationResult:
        """Generate a comprehensive simulation result report.
        
        This method aggregates all collected metrics and generates a final
        report of the simulation run.
        
        Args:
            config: LEAF-Cloud configuration used for the simulation
            start_time: Simulation start time
            end_time: Simulation end time
            completed_tokens: Number of tokens that completed processing
            active_tokens: Number of tokens still active at end of simulation
            error_count: Number of errors encountered during simulation
            
        Returns:
            SimulationResult object containing all collected metrics
            
        Raises:
            ValueError: If end_time is before start_time
            TypeError: If any argument has an invalid type
        """
            
        # Input validation
        if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
            raise TypeError("start_time and end_time must be datetime objects")
            
        if end_time < start_time:
            raise ValueError("end_time cannot be before start_time")
            
        if not isinstance(completed_tokens, int) or completed_tokens < 0:
            raise ValueError("completed_tokens must be a non-negative integer")
            
        if not isinstance(active_tokens, int) or active_tokens < 0:
            raise ValueError("active_tokens must be a non-negative integer")
            
        if not isinstance(error_count, int) or error_count < 0:
            raise ValueError("error_count must be a non-negative integer")
        
        logger.info("Generating simulation report from %s to %s", start_time, end_time)
        
        try:
            # Calculate duration in seconds with millisecond precision
            duration = round((end_time - start_time).total_seconds(), 3)
            
            # Calculate aggregate metrics
            metrics = self._calculate_aggregate_metrics()
            
            # Convert resource stats to ResourceMetrics format
            resource_metrics = {}
            for rid, stats in self.resource_stats.items():
                utilization_data = stats.metrics.get('utilization', [])
                power_data = stats.metrics.get('power_consumption', [])
                carbon_data = stats.metrics.get('carbon_emissions', [])
                state_data = stats.metrics.get('state_changes', [])

                # 2. Use the safe local variables for calculations
                avg_util = self._calculate_average_utilization(utilization_data)
                total_energy = self._calculate_energy_consumption(power_data)
                total_carbon = self._calculate_total_carbon(carbon_data)

                resource_metrics[rid] = {
                    'average_utilization': avg_util,
                    'total_energy_kwh': total_energy,
                    'total_carbon_kgco2e': total_carbon,
                    # 3. Use the safe local variables for counts
                    'state_changes': len(state_data),
                    'measurement_count': {
                        'utilization': len(utilization_data),
                        'power': len(power_data),
                        'carbon': len(carbon_data)
                    }
                }
        except Exception as e:
            logger.error("Failed to generate simulation report: %s", str(e), exc_info=True)
            raise RuntimeError(f"Failed to generate simulation report: {str(e)}") from e

        # Update metrics with token counts
        metrics.update({
            'completed_tokens': completed_tokens,
            'active_tokens': active_tokens,
            'error_count': error_count
        })
        
        return SimulationResult(
            config_snapshot=config.dict() if hasattr(config, 'dict') else {},
            start_time=start_time,
            end_time=end_time,
            status='completed',
            resource_metrics=resource_metrics,
            token_flow_logs=self.token_flow_log,
            metrics=metrics,
            completed_tokens=completed_tokens,
            active_tokens=active_tokens,
            error_count=error_count
        )
        
    
    def _calculate_median_utilization(
        self, 
        resource_metrics: dict[str, dict[str, Any]]
    ) -> float:
        """Calculate the median utilization across all resources.
        
        Args:
            resource_metrics: Dictionary of resource metrics
            
        Returns:
            Median utilization as a float between 0.0 and 1.0
        """
        if not resource_metrics:
            return 0.0
            
        utilizations = [
            m["average_utilization"] 
            for m in resource_metrics.values() 
            if m["average_utilization"] > 0
        ]
        
        if not utilizations:
            return 0.0
            
        utilizations.sort()
        mid = len(utilizations) // 2
        
        if len(utilizations) % 2 == 0:
            return (utilizations[mid - 1] + utilizations[mid]) / 2
        return utilizations[mid]

    def _calculate_aggregate_metrics(self):
        """
        Calculate aggregate metrics from the collected data.
        
        This internal method processes all collected metrics to calculate:
        - Per-resource averages and totals
        - System-wide aggregates
        - Derived metrics including energy, carbon, and utilization statistics
        """
        try:
            if not self.resource_stats:
                return {}
                
            metrics = {
                'total_resources': len(self.resource_stats),
                'total_events': len(self.token_flow_log),
                'resource_metrics': {},
                'system_metrics': {
                    'total_power_watts': 0.0,
                    'total_energy_kwh': 0.0,
                    'total_carbon_kgco2e': 0.0,
                    'average_utilization': 0.0,
                    'resource_count': len(self.resource_stats),
                    'active_resources': 0,
                    'peak_power_watts': 0.0,
                    'resource_utilization': {
                        'min': 0.0,
                        'max': 0.0,
                        'median': 0.0
                    },
                    'carbon_intensity': 0.0
                },
                                'timestamp': datetime.now(UTC).isoformat()
            }
            
            # Calculate per-resource metrics
            utilizations = []
            total_power = 0.0
            total_energy = 0.0
            total_carbon = 0.0
            
            for resource_id, stats in self.resource_stats.items():
                try:
                    # Extract recorded series from dict-backed storage
                    util_series = stats.metrics.get('utilization', [])
                    power_series = stats.metrics.get('power_consumption', [])
                    carbon_series = stats.metrics.get('carbon_emissions', [])
                    state_series = stats.metrics.get('state_changes', [])

                    # Calculate per-resource metrics
                    avg_util = self._calculate_average_utilization(util_series)
                    energy = self._calculate_energy_consumption(power_series)
                    carbon = self._calculate_total_carbon(carbon_series)
                    
                    # Update system-wide totals
                    if power_series:
                        current_power = power_series[-1].value if power_series else 0.0
                        total_power += current_power
                        metrics['system_metrics']['peak_power_watts'] = max(
                            metrics['system_metrics']['peak_power_watts'],
                            current_power
                        )
                    
                    total_energy += energy
                    total_carbon += carbon
                    
                    if avg_util > 0.1:  # Consider resource active if >10% utilization
                        metrics['system_metrics']['active_resources'] += 1
                    
                    if avg_util > 0:  # Only include non-zero utilizations in stats
                        utilizations.append(avg_util)
                    
                    # Store per-resource metrics
                    metrics['resource_metrics'][resource_id] = {
                        'average_utilization': avg_util,
                        'total_energy_kwh': energy,
                        'total_carbon_kgco2e': carbon,
                        'state_changes': len(state_series),
                        'measurement_count': {
                            'utilization': len(util_series),
                            'power': len(power_series),
                            'carbon': len(carbon_series)
                        }
                    }
                    
                except Exception as e:
                    logger.error(
                        "Error calculating metrics for resource %s: %s", 
                        resource_id, 
                        str(e),
                        exc_info=logger.isEnabledFor(logging.DEBUG)
                    )
            
            # Calculate system-wide metrics
            metrics['system_metrics'].update({
                'total_power_watts': total_power,
                'total_energy_kwh': total_energy,
                'total_carbon_kgco2e': total_carbon,
                'carbon_intensity': round(
                    (total_carbon / total_energy * 1000) if total_energy > 0 else 0.0,
                    2
                )
            })
            
            if utilizations:
                metrics['system_metrics'].update({
                    'average_utilization': sum(utilizations) / len(utilizations),
                    'resource_utilization': {
                        'min': min(utilizations),
                        'max': max(utilizations),
                        'median': sorted(utilizations)[len(utilizations)//2]
                    }
                })
            
            logger.debug(
                "Calculated aggregate metrics for %d resources with %d events",
                len(self.resource_stats),
                len(self.token_flow_log)
            )
            
            return metrics
                
        except Exception as e:
            logger.error(
                "Error calculating aggregate metrics: %s", 
                str(e),
                exc_info=logger.isEnabledFor(logging.DEBUG)
            )
            raise RuntimeError(f"Failed to calculate aggregate metrics: {str(e)}") from e
    
    def get_summary(self) -> dict:
        """Get a summary of collected metrics.
        
        Returns:
            Dictionary containing aggregate metrics and statistics
        """
        return self._calculate_aggregate_metrics()
    
    def increment_counter(self, counter_name: str, tags: Optional[Dict[str, str]] = None) -> None:
        """Increment a counter metric.
        
        Args:
            counter_name: Name of the counter to increment
            tags: Optional tags to associate with the metric
        """
        # For now, just log the counter increment
        # In a full implementation, this would update internal counters
        logger.debug("Counter incremented: %s (tags: %s)", counter_name, tags)
    
    def record_histogram(self, histogram_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Record a value in a histogram metric.
        
        Args:
            histogram_name: Name of the histogram
            value: Value to record
            tags: Optional tags to associate with the metric
        """
        # For now, just log the histogram value
        # In a full implementation, this would update internal histograms
        logger.debug("Histogram recorded: %s = %f (tags: %s)", histogram_name, value, tags)
    
    def record_value(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Record a single numeric value for a metric.
        
        This is a convenience wrapper to support callers that expect a generic
        value-recording API. Internally it forwards to ``record_histogram`` so
        aggregations can still be derived downstream.
        
        Args:
            metric_name: Name of the metric to record
            value: Numeric value to record
            tags: Optional key-value tags for the metric datapoint
        """
        try:
            self.record_histogram(metric_name, float(value), tags)
        except Exception as e:
            # Never fail the orchestrator due to metrics collection issues
            logger.debug("Failed to record value for %s: %s", metric_name, str(e))
    
    def update(self, **kwargs) -> None:
        """Update metrics with provided data.
        
        Args:
            **kwargs: Arbitrary keyword arguments containing metric data
        """
        # For now, just log the update
        # In a full implementation, this would update internal metrics
        logger.debug("Metrics updated with: %s", kwargs)
    
    def record_event_processing_time(
        self, 
        event_type: str, 
        processing_time: float, 
        tokens_processed: int = 0
    ) -> None:
        """Record event processing time metrics.
        
        Args:
            event_type: Type of event being processed
            processing_time: Time taken to process the event in seconds
            tokens_processed: Number of tokens processed during the event
        """
        logger.debug(
            "Event processing time recorded: %s took %.4fs (tokens: %d)", 
            event_type, processing_time, tokens_processed
        )

    def record_token_processing_time(
        self,
        token_id: str,
        processing_time: float,
        place: str,
        token_type: str = "unknown",
    ) -> None:
        """Record per-token processing time.

        Adds a histogram entry and a lightweight log entry so downstream consumers can derive stats if needed.
        """
        try:
            self.record_histogram(
                "token_processing_time_seconds",
                float(processing_time),
                tags={"place": place, "token_type": token_type},
            )
        except Exception:
            pass
        try:
            # Best-effort structured log
            from leaf_cloud.leaf_types import TokenFlowLogEntry
            self.token_flow_log.append(
                TokenFlowLogEntry(
                    timestamp=datetime.now(UTC).timestamp(),
                    event_type="token_processed",
                    details={
                        "token_id": token_id,
                        "processing_time": float(processing_time),
                        "place": place,
                        "token_type": token_type,
                    },
                )
            )
        except Exception:
            # Do not fail metrics collection if types import is unavailable
            pass
    
    def _calculate_average_utilization(
        self, 
        utilization_metrics: list[ResourceMetric]
    ) -> float:
        """Calculate the average utilization from a list of utilization metrics.
        
        Args:
            utilization_metrics: List of utilization metric points
            
        Returns:
            Average utilization as a float between 0.0 and 1.0
        """
        if not utilization_metrics:
            return 0.0
            
        total = sum(s.value for s in utilization_metrics)
        return total / len(utilization_metrics)
    
    def _calculate_energy_consumption(
        self, 
        power_metrics: list[ResourceMetric]
    ) -> float:
        """Calculate energy consumption using trapezoidal integration of power over time.
        
        Args:
            power_metrics: List of power consumption metrics
            
        Returns:
            Total energy consumption in kilowatt-hours (kWh)
        """
        if len(power_metrics) < 2:
            return 0.0
            
        energy = 0.0
        # Iterate adjacent power samples; no strict length requirement
        for prev, curr in zip(power_metrics, power_metrics[1:]):
            # Time difference in hours (convert from seconds)
            dt_hours = (curr.time - prev.time) / 3600.0
            # Average power in kW (convert from W to kW)
            avg_power_kw = (curr.value + prev.value) / 2000.0
            energy += avg_power_kw * dt_hours
            
        return energy
    
    def _calculate_total_carbon(
        self, 
        carbon_metrics: list[ResourceMetric]
    ) -> float:
        """Calculate total carbon emissions from carbon metrics.
        
        Args:
            carbon_metrics: List of carbon emission metrics
            
        Returns:
            Total carbon emissions in kgCO2e
        """
        if not carbon_metrics:
            return 0.0
            
        return sum(s.value for s in carbon_metrics)
    
    def _update_power_timeline(
        self, 
        timeline: dict[float, float], 
        power_metrics: list[ResourceMetric]
    ) -> None:
        """Update the power timeline with power measurements.

        Args:
            timeline: Dictionary to update with time: power entries
            power_metrics: List of power consumption metrics
        """
        for point in power_metrics:
            try:
                time_point = float(point.time)
                power = float(point.value)
            except (TypeError, ValueError):
                continue
            timeline[time_point] = timeline.get(time_point, 0.0) + power
    
    def _get_default_resource_metrics(self) -> dict[str, Any]:
        """Get default values for resource metrics.
        
        Returns:
            Dictionary with default values for resource metrics
        """
        return {
            "average_utilization": 0.0,
            "total_energy_kwh": 0.0,
            "total_carbon_kgco2e": 0.0,
            "state_changes": 0,
            "measurement_count": {
                "utilization": 0,
                "power": 0,
                "carbon": 0
            }
        }
