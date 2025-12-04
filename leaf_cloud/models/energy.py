from __future__ import annotations

import logging
from typing import Dict, List, Optional, Union, Tuple, Any, TypedDict, Literal, TYPE_CHECKING

import numpy as np
import yaml

if TYPE_CHECKING:
    from leaf_cloud.utils.results import SimulationResult
    from leaf_cloud.leaf_types import Resource
    from leaf_cloud.config import LEAFCloudConfig

# Type aliases for better code readability
EnergyProfile = Dict[str, float]  # e.g., {'idle': 0.1, 'max': 1.0}
EnergyWeights = Dict[str, float]  # e.g., {'vm-standard': 1.0, 'default': 0.8}
ResourceEnergy = Dict[str, float]  # Maps resource_id to energy in kWh
ResourceUtilization = Dict[str, Dict[str, Union[float, str]]]  # Resource utilization data
UtilizationDataPoint = Dict[str, float]  # Single data point with 'time' and 'value' keys
UtilizationTimeSeries = List[UtilizationDataPoint]  # Time series of utilization data
EnergyResults = Dict[str, Union[float, Dict[str, float]]]  # Structure of energy calculation results

"""
Energy Consumption Estimation Module for LEAF-Cloud Framework

This module implements the energy consumption estimation model for GCP resources
based on their utilization, resource types, and specific energy profiles.
It provides functionality to calculate and aggregate energy consumption across
all resources in the infrastructure model.
"""

# Configure logging
logger = logging.getLogger(__name__)


class EnergyModel:
    """
    Energy consumption estimation model for cloud resources.

    This class calculates energy consumption based on resource utilization
    and type-specific energy profiles loaded from the configuration.
    """

    def __init__(self, config: "LEAFCloudConfig.EnergyModelConfig") -> None:
        """
        Initialize the energy model with configuration.

        Args:
            config: Energy model configuration containing weights, functions, and profiles
        """
        self.name: str = "energy"
        self.energy_weights: EnergyWeights = config.weights
        self.energy_functions: Dict[str, EnergyProfile] = config.functions
        self.custom_energy_profiles: Dict[str, EnergyProfile] = config.profiles
        self.resource_mapping: Dict[str, Resource] = {}  # Populated during calculation
        logger.info(
            f"Energy model initialized with {len(self.energy_weights)} weights, "
            f"{len(self.energy_functions)} functions, and "
            f"{len(self.custom_energy_profiles)} custom profiles."
        )

    def get_energy_weight(self, resource_type: str) -> float:
        """
        Get the energy weight factor for a specific resource type.

        Args:
            resource_type: Type of cloud resource (e.g., 'vm-standard', 'cloud-storage')

        Returns:
            Energy weight factor for the specified resource type
        """
        # Try to get the specific resource type weight,
        # fallback to general category, then default
        main_type = (
            resource_type.split("-")[0]
            if "-" in resource_type
            else resource_type
        )
        default_weight = self.energy_weights.get("default", 1.0) 
        return self.energy_weights.get(
            resource_type,
            self.energy_weights.get(main_type, default_weight),
        )

    def get_energy_function(self, resource_type: str) -> EnergyProfile:
        """
        Get the energy function for a specific resource type.

        It first checks for a specific profile override, then falls back to
        the general function for the resource type, its main category, or the default.

        Args:
            resource_type: Type of cloud resource (e.g., 'vm-standard', 'cloud-storage')

        Returns:
            A dictionary with 'idle' and 'max' power consumption in kW.
        """
        if resource_type in self.custom_energy_profiles:
            profile: EnergyProfile = self.custom_energy_profiles[resource_type]
        else:
            main_type = (
                resource_type.split("-")[0]
                if "-" in resource_type
                else resource_type
            )
            if resource_type in self.energy_functions:
                profile = self.energy_functions[resource_type]
            elif main_type in self.energy_functions:
                profile = self.energy_functions[main_type]
            else:
                profile = self.energy_functions.get("default", {"idle": 0.0})

        if not isinstance(profile, dict):
            profile = {"idle": profile}  # type: ignore[assignment]

        normalized: EnergyProfile = {}
        for key, value in profile.items():
            if isinstance(value, (int, float)):
                normalized[key] = float(value)
            else:
                normalized[key] = value  # Preserve non-numeric extras if supplied

        normalized.setdefault("idle", 0.0)

        # Derive a reasonable max if one is not supplied but a coefficient is
        if "max" not in normalized:
            coef = normalized.get("coef")
            if isinstance(coef, (int, float)):
                normalized["max"] = normalized["idle"] + float(coef)
            else:
                normalized["max"] = normalized["idle"]

        return normalized

    def calculate_resource_energy(
        self, utilization: float, resource_type: str, capacity: float = 1.0
    ) -> float:
        """
        Calculate instantaneous power consumption for a resource.

        Args:
            utilization: Resource utilization as a float between 0.0 and 1.0
            resource_type: Type of the resource (e.g., 'vm-standard')
            capacity: The capacity multiplier for the resource (default: 1.0)

        Returns:
            Power consumption in kilowatts (kW)
        """
        utilization = max(0.0, min(1.0, utilization))
        func_params = self.get_energy_function(resource_type)
        idle_kw = float(func_params.get("idle", 0.0))
        max_kw = float(func_params.get("max", idle_kw))

        power_per_instance_kw = idle_kw
        coef = func_params.get("coef")
        exp = func_params.get("exp", 1.0)

        if isinstance(coef, (int, float)):
            exponent = float(exp) if isinstance(exp, (int, float)) else 1.0
            exponent = max(0.0, exponent)
            power_per_instance_kw = idle_kw + float(coef) * (utilization ** exponent)
            # Clamp to derived max so the curve never exceeds configured bounds
            power_per_instance_kw = min(max_kw, max(idle_kw, power_per_instance_kw))
        else:
            power_per_instance_kw = idle_kw + (max_kw - idle_kw) * utilization

        # Total power for all instances
        return power_per_instance_kw * capacity

    def calculate(
        self,
        simulation_results: Union["SimulationResult", Dict[str, Any]],
        resource_mapping: Dict[str, "Resource"],
    ) -> Dict[str, Any]:
        """
        Calculate energy consumption for each resource by integrating power over time.

        Args:
            simulation_results: The raw results from the simulation.
            resource_mapping: A dictionary mapping resource IDs to Resource objects.

        Returns:
            A dictionary with resource energy consumption and total energy.
        """
        self.resource_mapping = resource_mapping

        # Extract resource_stats from the correct location within raw_results
        if hasattr(simulation_results, "raw_results"):
            raw_results = simulation_results.raw_results
        elif (
            isinstance(simulation_results, dict)
            and "raw_results" in simulation_results
        ):
            raw_results = simulation_results.get("raw_results", {})
        else:
            # Fallback for older structures or direct dict passing
            raw_results = (
                simulation_results
                if isinstance(simulation_results, dict)
                else {}
            )

        # Get resource utilization time series data
        # Accept multiple shapes for backward/forward compatibility
        resource_utilization = raw_results.get("utilization", {})
        if not resource_utilization:
            # Legacy/alternative key used by some result builders
            resource_utilization = raw_results.get("resource_utilization", {})

        # As a last resort, adapt from resource_metrics schema if present in raw_results
        # Expecting { resource_id: { utilization: [ {timestamp, value}, ... ] } }
        if not resource_utilization and "resource_metrics" in raw_results:
            try:
                adapted: Dict[str, list[dict]] = {}
                for res_id, metrics in (raw_results.get("resource_metrics", {}) or {}).items():
                    series = metrics.get("utilization", []) or []
                    # Normalize to [{time, value}]
                    normalized = []
                    for point in series:
                        t = point.get("time") if isinstance(point, dict) and "time" in point else point.get("timestamp")
                        v = point.get("value") if isinstance(point, dict) else None
                        if t is None or v is None:
                            continue
                        normalized.append({"time": float(t), "value": float(v)})
                    if normalized:
                        adapted[res_id] = normalized
                resource_utilization = adapted
            except Exception:
                # If adaptation fails, leave as empty and fall back to warning/empty result below
                resource_utilization = {}

        # If still empty and we were passed a SimulationResult-like object, try adapting from its resource_metrics attribute
        if not resource_utilization and hasattr(simulation_results, "resource_metrics"):
            try:
                adapted: Dict[str, list[dict]] = {}
                sim_rm = getattr(simulation_results, "resource_metrics", {}) or {}
                for res_id, rm in sim_rm.items():
                    # Support both dict-shaped and object-shaped ResourceMetrics
                    if isinstance(rm, dict):
                        series = rm.get("utilization", []) or []
                        normalized = []
                        for point in series:
                            t = point.get("time") if isinstance(point, dict) and "time" in point else point.get("timestamp")
                            v = point.get("value") if isinstance(point, dict) else None
                            if t is None or v is None:
                                continue
                            normalized.append({"time": float(t), "value": float(v)})
                        if normalized:
                            adapted[res_id] = normalized
                    else:
                        # Try Pydantic RawResourceMetrics: rm.utilization is a list of objects
                        if hasattr(rm, "utilization") and isinstance(rm.utilization, list) and rm.utilization:
                            normalized = []
                            for point in rm.utilization:
                                # point may be a pydantic model with attributes
                                pt_time = getattr(point, "time", None)
                                if pt_time is None:
                                    pt_time = getattr(point, "timestamp", None)
                                pt_value = getattr(point, "value", None)
                                if pt_time is None or pt_value is None:
                                    continue
                                normalized.append({"time": float(pt_time), "value": float(pt_value)})
                            if normalized:
                                adapted[res_id] = normalized
                        else:
                            # Dataclass-like ResourceMetrics with timestamps/values arrays
                            timestamps = getattr(rm, "timestamps", [])
                            values = getattr(rm, "values", [])
                            if timestamps and values and len(timestamps) == len(values):
                                normalized = [
                                    {"time": float(t), "value": float(v)}
                                    for t, v in zip(timestamps, values)
                                    if t is not None and v is not None
                                ]
                                if normalized:
                                    adapted[res_id] = normalized
                resource_utilization = adapted
            except Exception:
                resource_utilization = {}

        if not resource_utilization:
            logger.warning(
                "EnergyModel: 'utilization' not found in simulation results. Cannot calculate energy."
            )
            return {
                "total_kwh": 0.0,
                "by_resource_type": {},
                "by_resource_category": {},
                "resource_energy": {},
            }

        resource_energy = {}

        # Process each resource's utilization time series
        for res_id, utilization_data in resource_utilization.items():
            if not utilization_data or not isinstance(utilization_data, list):
                continue

            # Sort by timestamp
            sorted_data = sorted(
                utilization_data, key=lambda x: x.get("time", 0)
            )
            total_energy = 0.0

            # Calculate energy for each time interval
            for i in range(len(sorted_data) - 1):
                time1 = sorted_data[i].get("time", 0)
                time2 = sorted_data[i + 1].get("time", time1)
                time_interval_hours = (time2 - time1) / 3600.0

                if time_interval_hours <= 0:
                    continue

                # Get utilization (0-1)
                utilization = sorted_data[i].get("value", 0.0)

                # Get resource info
                res_info = resource_mapping.get(res_id)
                if not res_info:
                    continue

                res_type = getattr(res_info, "specific_type", "unknown")
                capacity = getattr(res_info, "capacity", 1.0)

                # Calculate power and energy for this interval
                # Prefer the resource's own power model if available (captures
                # instance counts and implementation specifics like Cloud Run)
                power_kw = None
                try:
                    get_power = getattr(res_info, "get_power_consumption", None)
                    if callable(get_power):
                        power_kw = float(get_power(time1))
                except Exception:
                    power_kw = None

                if power_kw is None:
                    # Specialized handling for Cloud Run: use instance-based
                    # multiplication rather than concurrency-inflated capacity
                    if getattr(res_info, "specific_type", "") == "cloud-run":
                        try:
                            instances_val = getattr(res_info, "current_instances", 0) or 0
                            instance_multiplier = float(int(instances_val))
                        except Exception:
                            instance_multiplier = 0.0

                        # If per-instance power is exposed, use it directly
                        try:
                            idle_kw_inst = float(getattr(res_info, "single_instance_idle_power_kw", 0.0))
                            max_kw_inst = float(getattr(res_info, "single_instance_max_power_kw", idle_kw_inst))
                        except Exception:
                            idle_kw_inst = 0.0
                            max_kw_inst = 0.0

                        if idle_kw_inst > 0.0 or max_kw_inst > 0.0:
                            power_per_instance_kw = (
                                idle_kw_inst + (max_kw_inst - idle_kw_inst) * float(utilization)
                            )
                            power_kw = power_per_instance_kw * instance_multiplier
                        else:
                            # Fall back to configured profiles per instance
                            power_per_instance_kw = self.calculate_resource_energy(
                                utilization, res_type, 1.0
                            )
                            power_kw = power_per_instance_kw * instance_multiplier
                    else:
                        # Generic fallback uses declared capacity as multiplier
                        power_kw = self.calculate_resource_energy(
                            utilization, res_type, float(capacity or 1.0)
                        )
                energy_kwh = power_kw * time_interval_hours
                total_energy += energy_kwh

            # Store total energy for this resource
            if total_energy > 0:
                resource_energy[res_id] = total_energy

        # Aggregate results
        total_kwh = sum(resource_energy.values())
        by_type = {}
        by_category = {}
        for res_id, energy in resource_energy.items():
            res_info = resource_mapping[res_id]
            res_type = getattr(res_info, "specific_type", "unknown")
            category = res_type.split("-")[0] if "-" in res_type else res_type

            by_type[res_type] = by_type.get(res_type, 0.0) + energy
            by_category[category] = by_category.get(category, 0.0) + energy

        return {
            "total_kwh": total_kwh,
            "by_resource_type": by_type,
            "by_resource_category": by_category,
            "resource_energy": resource_energy,
        }

    def get_efficiency_recommendations(
        self, energy_results: EnergyResults, threshold_percentage: float = 15.0
    ) -> List[Dict[str, Any]]:
        """
        Generate recommendations to improve energy efficiency.

        This method identifies high-consumption resources and provides actionable
        recommendations for optimization based on their type.

        Args:
            energy_results: Energy consumption results from the calculate method.
            threshold_percentage: Minimum percentage of total energy to consider for optimization.

        Returns:
            A list of recommendation dictionaries.
        """
        recommendations = []
        total_energy = energy_results.get("total_kwh", 0)
        if total_energy == 0:
            return []

        threshold = total_energy * (threshold_percentage / 100.0)

        resource_energy = energy_results.get("resource_energy", {})

        # Find high-energy resources
        for resource_id, energy in resource_energy.items():
            if energy < threshold:
                continue

            # Get resource properties from the populated mapping (support object or dict)
            resource_info = self.resource_mapping.get(resource_id, {})
            resource_type = (
                resource_info.get("specific_type", "unknown")
                if isinstance(resource_info, dict)
                else getattr(resource_info, "specific_type", "unknown")
            )

            # Generate recommendation based on resource type
            recommendation = {
                "resource_id": resource_id,
                "energy_consumption_kwh": energy,
                "percentage_of_total": round((energy / total_energy) * 100, 2),
            }

            # Add specific recommendations based on resource type
            if "compute" in resource_type or "vm" in resource_type:
                recommendation["recommendations"] = [
                    "Consider rightsizing this compute resource.",
                    "Evaluate using compute-optimized instance types.",
                    "Check for idle periods and implement auto-shutdown policies.",
                ]
            elif "storage" in resource_type:
                recommendation["recommendations"] = [
                    "Review data lifecycle policies.",
                    "Consider moving cold data to lower-energy storage tiers.",
                    "Check for redundant data copies.",
                ]
            elif "network" in resource_type:
                recommendation["recommendations"] = [
                    "Optimize data transfer patterns.",
                    "Evaluate CDN usage for static content.",
                    "Check for unnecessary cross-region traffic.",
                ]
            else:
                recommendation["recommendations"] = [
                    "Review resource utilization patterns.",
                    "Consider consolidating services.",
                ]

            recommendations.append(recommendation)

        # Sort recommendations by energy impact
        recommendations.sort(
            key=lambda x: x["energy_consumption_kwh"], reverse=True
        )

        return recommendations

def load_energy_model(
    energy_config: "LEAFCloudConfig.EnergyModelConfig",
) -> EnergyModel:
    """
    Factory function to create and initialize an energy model.

    Args:
        energy_config: EnergyModelConfig object containing weights and functions.

    Returns:
        Initialized EnergyModel instance
    """
    return EnergyModel(config=energy_config)
