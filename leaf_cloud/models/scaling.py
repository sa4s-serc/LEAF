from __future__ import annotations

import math
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any, Union, cast, TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from leaf_cloud.config import LEAFCloudConfig
    from leaf_cloud.leaf_types import SimulationResult

"""
Dynamic Scaling Model for LEAF-Cloud Framework

This module implements the dynamic scaling model for GKE pods in the LEAF-Cloud framework.
It provides functionality to calculate required pod counts based on request rates,
resource utilization, and geographic factors.
"""

# Configure logging
logger = logging.getLogger("leaf_cloud.models.scaling")


# Type aliases for better readability
ResourceMapping = Dict[str, Any]  # Maps resource IDs to resource objects
InstanceHistory = List[Tuple[float, int]]  # List of (timestamp, instance_count) tuples

class ScalingMetrics(TypedDict, total=False):
    """Type definition for scaling metrics dictionary."""
    scaling_events: int
    instance_history: InstanceHistory
    average_instances: float
    peak_instances: int
    min_instances: int

class ScalingRecommendation(TypedDict):
    """Type definition for scaling recommendation dictionary."""
    group_id: str
    type: str  # 'max_instances', 'min_instances', or 'thrashing'
    suggestion: str

# For backward compatibility with existing code
ScalingAnalysis = Dict[str, Dict[str, Any]]

class ScalingModel:
    """
    Analyzes the dynamic scaling behavior of autoscaling resource groups in the simulation.

    This model processes simulation results to extract metrics about how resource
    groups with autoscaling enabled (e.g., GKE node pools) behave under load.
    It identifies scaling events, tracks instance counts, and provides recommendations
    for tuning autoscaling parameters.
    """

    def __init__(self, config: "LEAFCloudConfig.ScalingModelConfig", resource_mapping: Optional[ResourceMapping] = None) -> None:
        """
        Initialize the scaling model with a configuration object and optional resource mapping.

        Args:
            config: ScalingModelConfig object containing all scaling parameters.
            resource_mapping: Optional dictionary mapping resource IDs to their configurations.
                           If not provided, an empty dictionary will be used.
        """
        self.name: str = "scaling"
        self.config: "LEAFCloudConfig.ScalingModelConfig" = config
        self.resource_mapping: ResourceMapping = resource_mapping or {}
        self._validate_config(config)
        logger.info("Scaling model initialized.")

    def _validate_config(self, config: "LEAFCloudConfig.ScalingModelConfig") -> None:
        """
        Validates the scaling configuration.
        
        Args:
            config: The scaling model configuration to validate.
            
        Raises:
            ValueError: If any configuration values are invalid.
        """
        if config.min_pods < 1:
            logger.warning("Minimum pods set below 1. This may not be realistic.")

        if config.max_pods < config.min_pods:
            error_msg = f"Maximum pods ({config.max_pods}) cannot be less than minimum pods ({config.min_pods})."
            logger.error(error_msg)
            raise ValueError("Invalid scaling configuration: max_pods < min_pods")

        if not (0 < config.cpu_threshold <= 1):
            logger.error(f"Invalid CPU threshold {config.cpu_threshold}. Must be between 0 and 1.")
            raise ValueError("Invalid CPU threshold")

        if not (0 < config.memory_threshold <= 1):
            logger.error(f"Invalid memory threshold {config.memory_threshold}. Must be between 0 and 1.")
            raise ValueError("Invalid memory threshold")


    def calculate(
        self,
        simulation_results: Union["SimulationResult", Dict[str, Any]],
        resource_mapping: ResourceMapping,
    ) -> Dict[str, ScalingMetrics]:
        """
        Analyze scaling behavior from simulation results.

        Args:
            simulation_results: The raw results from the simulation. Can be either a
                SimulationResult object or a dictionary with simulation data.
            resource_mapping: A dictionary mapping resource IDs to resource objects.

        Returns:
            Dict[str, Dict[str, Any]]: A dictionary with scaling analysis for each autoscaling group,
            where each value is a dictionary containing:
                - scaling_events: Number of scaling events
                - instance_history: List of (timestamp, instance_count) tuples
                - average_instances: Average number of instances
                - peak_instances: Maximum number of instances
                - min_instances: Minimum number of instances
        """
        # The attribute on the results object is named resource_stats
        resource_state_changes: List[Dict[str, Any]] = getattr(
            simulation_results, "resource_stats", []
        )
        self.resource_mapping = resource_mapping
        autoscaling_groups = self._identify_autoscaling_groups()
        scaling_analysis: Dict[str, ScalingMetrics] = {}

        for group_id in autoscaling_groups:
            group_events = [
                e
                for e in resource_state_changes
                if e.get("group_id") == group_id
            ]
            if not group_events:
                continue

            instance_history = self._get_instance_history(group_events)
            if not instance_history:  # Skip if no valid instance history
                continue

            scaling_events = self._count_scaling_events(instance_history)

            scaling_analysis[group_id] = ScalingMetrics(
                scaling_events=scaling_events,
                instance_history=instance_history,
                average_instances=sum(c for _, c in instance_history) / len(instance_history),
                peak_instances=max(c for _, c in instance_history),
                min_instances=min(c for _, c in instance_history),
            )

        return scaling_analysis

    def get_scaling_recommendations(
        self, 
        scaling_results: Dict[str, ScalingMetrics]
    ) -> List[ScalingRecommendation]:
        """
        Generate recommendations for tuning autoscaling parameters.

        Args:
            scaling_results: A dictionary mapping group IDs to their scaling metrics.

        Returns:
            A list of scaling recommendations, where each recommendation contains:
            - group_id: The ID of the autoscaling group
            - type: The type of recommendation ('max_instances', 'min_instances', or 'thrashing')
            - suggestion: A human-readable suggestion for improvement
        """
        recommendations: List[ScalingRecommendation] = []
        
        for group_id, metrics in scaling_results.items():
            # Recommendation if peak instances hit the configured max
            if metrics["peak_instances"] >= self.config.max_pods:
                recommendations.append(ScalingRecommendation(
                    group_id=group_id,
                    type="max_instances",
                    suggestion=(
                        f"Peak instances ({metrics['peak_instances']}) reached the configured max "
                        f"({self.config.max_pods}). Consider increasing max_pods if this is a bottleneck."
                    )
                ))

            # Recommendation if instance count is constantly low
            if metrics["average_instances"] <= self.config.min_pods * 1.1:
                recommendations.append(ScalingRecommendation(
                    group_id=group_id,
                    type="min_instances",
                    suggestion=(
                        f"Average instance count ({metrics['average_instances']:.2f}) is very close to the minimum "
                        f"({self.config.min_pods}). Consider lowering min_pods to save costs if performance is acceptable."
                    )
                ))

            # Recommendation for scaling thrashing (arbitrary threshold for 'frequent')
            if metrics["scaling_events"] > 20:
                recommendations.append(ScalingRecommendation(
                    group_id=group_id,
                    type="thrashing",
                    suggestion=(
                        f"Frequent scaling events ({metrics['scaling_events']}) were detected. "
                        f"Consider increasing the cooldown period (current: {self.config.cooldown_period}s) "
                        f"or adjusting utilization targets to prevent thrashing."
                    )
                ))
                
        return recommendations

    def _identify_autoscaling_groups(self) -> List[str]:
        """
        Identifies resource groups that have autoscaling enabled.
        
        Returns:
            A list of resource IDs that are identified as autoscaling groups.
            This identifies resources with 'gke' in their ID or resources of type 'instance_group'.
            
        Example:
            >>> model = ScalingModel(config)
            >>> model.resource_mapping = {
            ...     "gke-cluster-1": {"type": "gke_cluster"},
            ...     "instance-group-1": {"type": "instance_group"},
            ...     "other-resource": {"type": "other"}
            ... }
            >>> model._identify_autoscaling_groups()
            ['gke-cluster-1', 'instance-group-1']
        """
        return [
            res_id
            for res_id, res in self.resource_mapping.items()
            if "gke" in res_id or (isinstance(res, dict) and res.get("type") == "instance_group")
        ]

    def _get_instance_history(
        self, events: List[Dict[str, Any]]
    ) -> InstanceHistory:
        """
        Tracks the number of active instances over time from a list of events.
        
        Args:
            events: List of event dictionaries containing state changes. Each event should
                   have a 'new_state' key with a dictionary value containing 'instance_count'.
                   May optionally include a 'time' key for the event timestamp.
            
        Returns:
            A list of (timestamp, instance_count) tuples, where timestamp is a float
            representing the event time and instance_count is an integer.
            
        Example:
            >>> events = [
            ...     {"time": 1.0, "new_state": {"instance_count": 2}},
            ...     {"time": 2.0, "new_state": {"instance_count": 3}},
            ...     {"new_state": {"instance_count": 4}}  # Uses default time 0.0
            ... ]
            >>> model = ScalingModel(config)
            >>> model._get_instance_history(events)
            [(1.0, 2), (2.0, 3), (0.0, 4)]
        """
        history: InstanceHistory = []
        
        for event in events:
            if not isinstance(event, dict):
                logger.warning(f"Skipping invalid event (not a dictionary): {event}")
                continue
                
            new_state = event.get("new_state")
            if not isinstance(new_state, dict):
                logger.debug(f"Skipping event with missing or invalid 'new_state': {event}")
                continue
                
            instance_count = new_state.get("instance_count")
            if not isinstance(instance_count, (int, float)):
                logger.debug(f"Skipping event with missing or invalid 'instance_count': {event}")
                continue
                
            timestamp = float(event.get("time", 0.0))
            history.append((timestamp, int(instance_count)))
            
        return history

    def _count_scaling_events(
        self, instance_history: InstanceHistory
    ) -> int:
        """
        Counts the number of times the instance count changed in the history.
        
        Args:
            instance_history: List of (timestamp, instance_count) tuples, where timestamp
                            is a float and instance_count is an integer.
            
        Returns:
            The number of times the instance count changed between consecutive events.
            Returns 0 if there are fewer than 2 entries in the history.
            
        Example:
            >>> history = [(1.0, 2), (2.0, 2), (3.0, 3), (4.0, 3), (5.0, 1)]
            >>> model = ScalingModel(config)
            >>> model._count_scaling_events(history)
            2  # Changes from 2->3 and 3->1
        """
        if not instance_history:
            logger.debug("Empty instance history provided")
            return 0
            
        if len(instance_history) < 2:
            logger.debug("Insufficient history to detect scaling events")
            return 0
            
        count = 0
        prev_count = instance_history[0][1]
        
        for timestamp, current_count in instance_history[1:]:
            if not isinstance(current_count, int):
                logger.warning(
                    f"Non-integer instance count {current_count} at time {timestamp}"
                )
                continue
                
            if current_count != prev_count:
                count += 1
                logger.debug(
                    f"Scaling event detected at time {timestamp}: "
                    f"{prev_count} -> {current_count} instances"
                )
                prev_count = current_count
                
        logger.info(f"Detected {count} scaling events in the instance history")
        return count

    @classmethod
    def load_scaling_model(
        cls, 
        config: "LEAFCloudConfig", 
        resource_mapping: Optional[ResourceMapping] = None
    ) -> "ScalingModel":
        """
        Factory method to create a ScalingModel instance from a configuration object.
        
        This method serves as the preferred way to instantiate a ScalingModel,
        as it performs validation of the configuration before creating the instance.
        
        Args:
            config: The LEAFCloudConfig object containing scaling parameters.
            resource_mapping: Optional dictionary mapping resource IDs to their
                           configurations. If not provided, an empty dictionary
                           will be used.
            
        Returns:
            A new instance of ScalingModel with the provided configuration.
            
        Example:
            >>> from leaf_cloud.config import LEAFCloudConfig
            >>> config = LEAFCloudConfig(
            ...     min_pods=2,
            ...     max_pods=10,
            ...     cooldown_period=300
            ... )
            >>> model = ScalingModel.load_scaling_model(config)
            >>> isinstance(model, ScalingModel)
            True
            
        Note:
            The method validates the configuration before creating the instance.
            If validation fails, a ValueError will be raised.
        """
        # Create a temporary instance to validate the config
        temp_instance = cls.__new__(cls)
        temp_instance.config = config
        temp_instance.resource_mapping = {}
        
        # Validate configuration using the instance method
        try:
            temp_instance._validate_config(config)
        except ValueError as e:
            logger.error(f"Configuration validation failed: {e}")
            raise ValueError(
                f"Invalid configuration provided to ScalingModel.load_scaling_model(): {e}"
            )
        
        # Ensure resource_mapping is a dictionary
        resource_mapping = resource_mapping or {}
        
        # Create and return the actual instance
        return cls(config, resource_mapping)
