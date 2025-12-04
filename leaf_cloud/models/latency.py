from __future__ import annotations

import os
import yaml
import ast
import numpy as np
import logging
from dataclasses import dataclass, field
from typing import (
    Any, Dict, List, Optional, Tuple, Union, TypedDict, Literal, TypeVar, 
    Sequence, Callable, Type, cast
)

# Type aliases
TokenEvent = Dict[str, Any]
Token = Dict[str, Any]
ResourceMapping = Dict[str, Any]
LatencyStats = Dict[str, float]
PathLatencyStats = Dict[str, LatencyStats]
ResourceLatencyStats = Dict[str, LatencyStats]
LatencyResult = Dict[str, Any]
Transition = Dict[str, Any]
Path = List[Transition]

# Type variables
T = TypeVar('T')

# Type for region latency factors
RegionLatencyFactor = Union[float, Dict[Tuple[str, str], float]]

# Type for congestion parameters
class CongestionParams(TypedDict):
    threshold: float
    factor: float
    exp: float

# Type for latency model configuration
class LatencyModelConfig(TypedDict, total=False):
    base_latency: Dict[str, float]
    congestion_params: Dict[str, CongestionParams]
    region_latency_factors: Dict[Union[str, Tuple[str, str]], float]
    stochastic_variation: Dict[str, Any]

# Type for simulation results
class SimulationResult:
    token_flow_log: List[TokenEvent]
    resource_stats: List[Dict[str, Any]]

# Type for token history events
TokenHistoryEvent = Tuple[str, str, float]  # (event_type, resource, timestamp)

"""
Latency Estimation Module for LEAF-Cloud Framework

This module implements the latency estimation model for GCP resources
based on their utilization, network characteristics, and processing times.
It calculates end-to-end latency across resource transitions, including 
congestion effects and critical path analysis.

Implements equation 1 from the LEAF-Cloud mathematical foundation:
- Equation 1: End-to-end latency calculation across resource transitions
"""

# Configure logging
logger = logging.getLogger(__name__)


class LatencyModel:
    """
    Latency estimation model for cloud resources.

    This class calculates latency based on resource processing times,
    network delays, and congestion effects, implementing equation 1
    from the LEAF-Cloud framework.
    """

    def __init__(self, config: "LEAFCloudConfig.LatencyModelConfig") -> None:
        """
        Initialize the latency model with configuration.

        Args:
            config: Latency model configuration containing base latencies,
                   congestion parameters, and region factors.
        """
        self.name: str = "latency"
        self.config: LatencyModelConfig = config
        self.resource_mapping: ResourceMapping = {}
        self._parsed_region_factors: Dict[Union[Tuple[str, str], str], float] = self._parse_region_latency_factors(
            config.region_latency_factors
        )
        
        # Type assertions for configuration
        self.base_latency: Dict[str, Union[float, Dict[str, float]]] = config.base_latency
        self.congestion_params: Dict[str, CongestionParams] = config.congestion_params
        
        # End-to-end latency configuration
        self.end_to_end_enabled: bool = getattr(config, 'end_to_end_enabled', False)
        self.user_to_infrastructure_latency: Dict[str, float] = getattr(config, 'user_to_infrastructure_latency', {})
        self.user_distribution: Dict[str, float] = getattr(config, 'user_distribution', {})
        self.default_user_to_infra_latency: float = getattr(config, 'default_user_to_infra_latency', 50.0)
        
        if not getattr(self.__class__, "_init_logged", False):
            logger.info(
                "Latency model initialized with configuration. End-to-end enabled: %s",
                self.end_to_end_enabled,
            )
            self.__class__._init_logged = True
        else:
            logger.debug(
                "Latency model re-initialized. End-to-end enabled: %s",
                self.end_to_end_enabled,
            )

    def _parse_region_latency_factors(
        self, 
        region_factors: Dict[Union[str, Tuple[str, str]], float]
    ) -> Dict[Union[Tuple[str, str], str], float]:
        """
        Parse region latency factors from configuration.
        
        Args:
            region_factors: Dictionary mapping region pairs to latency factors.
                          Can contain string keys in tuple format (e.g., "('us-central1', 'us-east1')")
                          or direct tuple keys.
                          
        Returns:
            Dictionary with parsed region pairs as keys and latency factors as values.
        """
        parsed_factors: Dict[Union[Tuple[str, str], str], float] = {}
        for k, v in region_factors.items():
            if isinstance(k, str) and k.startswith("(") and k.endswith(")"):
                try:
                    # Safely evaluate the string representation of the tuple
                    key = ast.literal_eval(k)
                    if isinstance(key, tuple) and len(key) == 2:
                        parsed_factors[key] = v
                    else:
                        logger.warning(
                            f"Invalid region pair format: {k}. Expected a tuple of two region names."
                        )
                        parsed_factors[k] = v
                except (ValueError, SyntaxError) as e:
                    logger.warning(
                        f"Could not parse region latency factor key '{k}': {e}. Storing as string."
                    )
                    parsed_factors[k] = v
            else:
                parsed_factors[k] = v
        return parsed_factors

    def get_base_latency(self, resource_type: str) -> float:
        """
        Get the base latency for a specific resource type.

        Args:
            resource_type: Type of cloud resource (e.g., 'vm-standard', 'cloud-storage')

        Returns:
            Base latency value in milliseconds
            
        Raises:
            KeyError: If the default latency is not found in the configuration
        """
        if not resource_type:
            raise ValueError("Resource type cannot be empty")
            
        # Extract the main category from resource type
        main_type = (
            resource_type.split("-")[0]
            if "-" in resource_type
            else resource_type
        )

        # Check if resource type exists in its category
        if main_type in self.base_latency:
            main_type_latency = self.base_latency[main_type]
            if isinstance(main_type_latency, dict):
                # Get specific resource type latency or default for category
                return main_type_latency.get(
                    resource_type,
                    main_type_latency.get(
                        "default", 
                        self.base_latency.get("default", 0.0)  # type: ignore
                    ),
                )
            return main_type_latency  # type: ignore

        # Return default latency if resource type not found
        default_latency = self.base_latency.get("default")
        if default_latency is None:
            raise KeyError("Default latency not found in configuration")
            
        if isinstance(default_latency, dict):
            return default_latency.get("default", 0.0)
            
        return default_latency

    def get_congestion_params(self, resource_type: str) -> CongestionParams:
        """
        Get the congestion parameters for a specific resource type.

        Args:
            resource_type: Type of cloud resource (e.g., 'vm-standard', 'storage-ssd')

        Returns:
            Dictionary containing congestion parameters (threshold, factor, exp)
            
        Raises:
            KeyError: If neither the resource type nor default congestion params are found
        """
        if not resource_type:
            raise ValueError("Resource type cannot be empty")
            
        # Get main resource category (compute, storage, etc.)
        main_type = (
            resource_type.split("-")[0]
            if "-" in resource_type
            else resource_type
        )

        # Return congestion parameters for the main type or default
        params = self.congestion_params.get(main_type, self.congestion_params.get("default"))
        if params is None:
            raise KeyError(f"No congestion parameters found for resource type '{resource_type}' and no default available")
            
        return params

    def get_region_latency_factor(
        self, source_region: str, dest_region: str
    ) -> float:
        """
        Get the latency factor for traffic between regions.

        Args:
            source_region: Source GCP region (e.g., 'us-central1')
            dest_region: Destination GCP region (e.g., 'us-east1')

        Returns:
            Latency multiplier factor (always >= 1.0)
            
        Raises:
            ValueError: If either region is empty or invalid
        """
        if not source_region or not dest_region:
            raise ValueError("Both source_region and dest_region must be non-empty strings")
            
        # If source and destination are the same, use same-region factor
        if source_region == dest_region:
            return 1.0

        # Check for tuple key (source, dest) and wildcard matches
        key = (source_region, dest_region)
        if key in self._parsed_region_factors:
            factor = self._parsed_region_factors[key]
        elif (source_region, "*") in self._parsed_region_factors:
            factor = self._parsed_region_factors[(source_region, "*")]
        elif ("*", dest_region) in self._parsed_region_factors:
            factor = self._parsed_region_factors[("*", dest_region)]
        else:
            # Get default factor if no specific rule found
            factor = self._parsed_region_factors.get("default", 1.0)
            
        # Ensure factor is at least 1.0 (no negative or zero latency factors)
        return max(1.0, float(factor))

    def _apply_stochastic_variation(
        self, delay: float, resource_type: str
    ) -> float:
        """
        Apply stochastic variation to the calculated delay if enabled in config.

        Args:
            delay: The base delay value to vary (must be non-negative)
            resource_type: Type of resource being used (e.g., 'vm-standard', 'storage-ssd')

        Returns:
            Delay with stochastic variation applied (always >= 0.1 * original_delay)
            
        Raises:
            ValueError: If delay is negative
        """
        if delay < 0:
            raise ValueError(f"Delay must be non-negative, got {delay}")
            
        if not self.config.stochastic_variation.get("enabled", False):
            return delay

        # Check if this resource type should have variation applied
        apply_to = self.config.stochastic_variation.get("apply_to", [])
        if not apply_to:  # Default to all if not specified
            apply_to = ["all"]
            
        main_type = (
            resource_type.split("-")[0]
            if "-" in resource_type
            else resource_type
        )
        
        if "all" not in apply_to and main_type not in apply_to:
            return delay

        # Get the distribution parameters
        dist_type = self.config.stochastic_variation.get("distribution", "normal")
        params = self.config.stochastic_variation.get("params", {}).get(dist_type, {})

        try:
            variation = 0.0  # default to no variation
            # Apply the appropriate distribution
            if dist_type == "uniform" and "min" in params and "max" in params:
                min_val, max_val = float(params["min"]), float(params["max"])
                if min_val > max_val:
                    min_val, max_val = max_val, min_val
                variation = np.random.uniform(min_val, max_val)
                delay *= 1.0 + variation
            elif dist_type == "normal" and "mean" in params and "stddev" in params:
                mean, stddev = float(params["mean"]), float(params["stddev"])
                variation = np.random.normal(mean, max(0, stddev))  # Ensure non-negative stddev
                # Ensure we don't get negative delays or extreme reductions
                delay = max(0.1 * delay, delay * (1.0 + variation))
            else:
                # Unknown or misconfigured distribution – log and return original delay safely
                logger.warning(
                    "Unknown stochastic distribution '%s'. Supported: 'uniform', 'normal'. Skipping variation.",
                    dist_type,
                )
                variation = 0.0
            
            logger.debug(
                "Applied %s stochastic variation (%.2f%%) to %s delay: %.2fms -> %.2fms",
                dist_type,
                variation * 100,
                resource_type,
                delay / (1.0 + variation) if variation != 0 else delay,
                delay
            )
                
        except (ValueError, TypeError) as e:
            logger.warning(
                "Error applying stochastic variation: %s. Using original delay.",
                str(e)
            )

        return delay

    def calculate_user_to_infrastructure_latency(
        self, user_region: str, infrastructure_region: str
    ) -> float:
        """
        Calculate latency from user location to infrastructure region.
        
        Args:
            user_region: User's geographic region (e.g., 'us-central1')
            infrastructure_region: Infrastructure deployment region (e.g., 'us-east1')
            
        Returns:
            User-to-infrastructure latency in milliseconds
            
        Raises:
            ValueError: If either region is empty
        """
        if not user_region or not infrastructure_region:
            raise ValueError("Both user_region and infrastructure_region must be non-empty strings")
        
        # Create key for latency lookup
        latency_key = f"{user_region}_{infrastructure_region}"
        
        # Get latency from configuration or use default
        latency = self.user_to_infrastructure_latency.get(
            latency_key, 
            self.default_user_to_infra_latency
        )
        
        logger.debug(
            "User-to-infrastructure latency from %s to %s: %.2fms",
            user_region,
            infrastructure_region,
            latency
        )
        
        return float(latency)

    def get_weighted_user_latency(
        self, infrastructure_region: str, available_regions: Optional[List[str]] = None
    ) -> float:
        """
        Calculate weighted average user-to-infrastructure latency based on user distribution.
        
        Args:
            infrastructure_region: Infrastructure deployment region
            available_regions: List of available infrastructure regions for fallback distribution
            
        Returns:
            Weighted average latency in milliseconds
        """
        if not infrastructure_region:
            raise ValueError("Infrastructure region cannot be empty")
        
        # Use configured user distribution or create equal distribution
        user_dist = self.user_distribution
        if not user_dist and available_regions:
            # Create equal distribution across available regions as fallback
            equal_percentage = 1.0 / len(available_regions)
            user_dist = {region: equal_percentage for region in available_regions}
            logger.debug(
                "No user distribution configured, using equal distribution: %s",
                user_dist
            )
        
        if not user_dist:
            # If no distribution available, use default latency
            logger.warning(
                "No user distribution available, using default latency: %.2fms",
                self.default_user_to_infra_latency
            )
            return self.default_user_to_infra_latency
        
        # Calculate weighted average latency
        total_weighted_latency = 0.0
        total_weight = 0.0
        
        for user_region, percentage in user_dist.items():
            if percentage <= 0:
                continue
                
            user_to_infra_latency = self.calculate_user_to_infrastructure_latency(
                user_region, infrastructure_region
            )
            
            weighted_latency = user_to_infra_latency * percentage
            total_weighted_latency += weighted_latency
            total_weight += percentage
            
            logger.debug(
                "User region %s (%.1f%%): %.2fms -> weighted: %.2fms",
                user_region,
                percentage * 100,
                user_to_infra_latency,
                weighted_latency
            )
        
        # Normalize if weights don't sum to 1.0
        if total_weight > 0:
            weighted_average = total_weighted_latency / total_weight
        else:
            weighted_average = self.default_user_to_infra_latency
            logger.warning(
                "No valid user distribution weights, using default latency: %.2fms",
                weighted_average
            )
        
        logger.debug(
            "Weighted user-to-infrastructure latency for %s: %.2fms (total_weight=%.3f)",
            infrastructure_region,
            weighted_average,
            total_weight
        )
        
        return weighted_average

    def assign_user_region(self, available_regions: Optional[List[str]] = None) -> str:
        """
        Randomly assign a user region based on the configured distribution.
        
        Args:
            available_regions: List of available regions for fallback equal distribution
            
        Returns:
            Selected user region string
        """
        # Use configured user distribution or create equal distribution
        user_dist = self.user_distribution
        if not user_dist and available_regions:
            # Create equal distribution across available regions as fallback
            equal_percentage = 1.0 / len(available_regions)
            user_dist = {region: equal_percentage for region in available_regions}
        
        if not user_dist:
            # If no distribution available, return a default region
            default_region = "us-central1"  # Default fallback region
            logger.warning(
                "No user distribution available, using default region: %s",
                default_region
            )
            return default_region
        
        # Create cumulative distribution for random selection
        regions = list(user_dist.keys())
        weights = list(user_dist.values())
        
        # Normalize weights to ensure they sum to 1.0
        total_weight = sum(weights)
        if total_weight <= 0:
            # If all weights are zero or negative, use equal distribution
            weights = [1.0 / len(regions)] * len(regions)
            total_weight = 1.0
        else:
            weights = [w / total_weight for w in weights]
        
        # Use numpy's random choice with probabilities
        selected_region = np.random.choice(regions, p=weights)
        
        logger.debug(
            "Assigned user region: %s (from distribution: %s)",
            selected_region,
            {r: f"{w*100:.1f}%" for r, w in zip(regions, weights)}
        )
        
        return str(selected_region)

    def calculate_end_to_end_latency(
        self,
        user_region: str,
        infrastructure_region: str,
        infrastructure_latency: float,
        apply_stochastic_variation: bool = True
    ) -> Dict[str, float]:
        """
        Calculate complete end-to-end latency including user-to-infrastructure and infrastructure-internal components.
        
        Args:
            user_region: User's geographic region
            infrastructure_region: Infrastructure deployment region
            infrastructure_latency: Infrastructure-internal processing latency in milliseconds
            apply_stochastic_variation: Whether to apply stochastic variation to user-to-infra latency
            
        Returns:
            Dictionary containing:
                - user_to_infra_latency: User-to-infrastructure latency component
                - infrastructure_latency: Infrastructure-internal latency component
                - total_end_to_end_latency: Sum of both components
                
        Raises:
            ValueError: If any input parameters are invalid
        """
        if not user_region or not infrastructure_region:
            raise ValueError("Both user_region and infrastructure_region must be non-empty strings")
        
        if infrastructure_latency < 0:
            raise ValueError(f"Infrastructure latency must be non-negative, got {infrastructure_latency}")
        
        # Calculate user-to-infrastructure latency
        user_to_infra_latency = self.calculate_user_to_infrastructure_latency(
            user_region, infrastructure_region
        )
        
        # Apply stochastic variation to user-to-infrastructure latency if enabled
        if apply_stochastic_variation:
            user_to_infra_latency = self._apply_stochastic_variation(
                user_to_infra_latency, "network"  # Treat user-to-infra as network latency
            )
        
        # Calculate total end-to-end latency
        total_latency = user_to_infra_latency + infrastructure_latency
        
        result = {
            "user_to_infra_latency": user_to_infra_latency,
            "infrastructure_latency": infrastructure_latency,
            "total_end_to_end_latency": total_latency
        }
        
        logger.debug(
            "End-to-end latency calculation: user=%s, infra=%s, user_to_infra=%.2fms, infra_internal=%.2fms, total=%.2fms",
            user_region,
            infrastructure_region,
            user_to_infra_latency,
            infrastructure_latency,
            total_latency
        )
        
        return result

    def calculate_weighted_end_to_end_latency(
        self,
        infrastructure_region: str,
        infrastructure_latency: float,
        available_regions: Optional[List[str]] = None,
        apply_stochastic_variation: bool = True
    ) -> Dict[str, Any]:
        """
        Calculate weighted end-to-end latency based on user distribution.
        
        Args:
            infrastructure_region: Infrastructure deployment region
            infrastructure_latency: Infrastructure-internal processing latency in milliseconds
            available_regions: List of available regions for fallback distribution
            apply_stochastic_variation: Whether to apply stochastic variation
            
        Returns:
            Dictionary containing:
                - weighted_user_to_infra_latency: Weighted average user-to-infrastructure latency
                - infrastructure_latency: Infrastructure-internal latency component
                - weighted_total_latency: Weighted total end-to-end latency
                - user_latency_breakdown: Per-region latency breakdown
        """
        if not infrastructure_region:
            raise ValueError("Infrastructure region cannot be empty")
        
        if infrastructure_latency < 0:
            raise ValueError(f"Infrastructure latency must be non-negative, got {infrastructure_latency}")
        
        # Use configured user distribution or create equal distribution
        user_dist = self.user_distribution
        if not user_dist and available_regions:
            equal_percentage = 1.0 / len(available_regions)
            user_dist = {region: equal_percentage for region in available_regions}
        
        if not user_dist:
            # Fallback to single default calculation
            default_result = self.calculate_end_to_end_latency(
                "us-central1", infrastructure_region, infrastructure_latency, apply_stochastic_variation
            )
            return {
                "weighted_user_to_infra_latency": default_result["user_to_infra_latency"],
                "infrastructure_latency": infrastructure_latency,
                "weighted_total_latency": default_result["total_end_to_end_latency"],
                "user_latency_breakdown": {"us-central1": default_result}
            }
        
        # Calculate weighted latency across all user regions
        total_weighted_user_to_infra = 0.0
        total_weight = 0.0
        user_breakdown = {}
        
        for user_region, percentage in user_dist.items():
            if percentage <= 0:
                continue
            
            # Calculate end-to-end latency for this user region
            region_result = self.calculate_end_to_end_latency(
                user_region, infrastructure_region, infrastructure_latency, apply_stochastic_variation
            )
            
            # Weight the user-to-infrastructure component
            weighted_user_to_infra = region_result["user_to_infra_latency"] * percentage
            total_weighted_user_to_infra += weighted_user_to_infra
            total_weight += percentage
            
            # Store breakdown for this region
            user_breakdown[user_region] = {
                **region_result,
                "user_percentage": percentage,
                "weighted_contribution": weighted_user_to_infra
            }
            
            logger.debug(
                "User region %s (%.1f%%): user_to_infra=%.2fms, total=%.2fms, weighted_contribution=%.2fms",
                user_region,
                percentage * 100,
                region_result["user_to_infra_latency"],
                region_result["total_end_to_end_latency"],
                weighted_user_to_infra
            )
        
        # Normalize if weights don't sum to 1.0
        if total_weight > 0:
            weighted_user_to_infra_avg = total_weighted_user_to_infra / total_weight
        else:
            weighted_user_to_infra_avg = self.default_user_to_infra_latency
        
        # Calculate weighted total latency
        weighted_total = weighted_user_to_infra_avg + infrastructure_latency
        
        result = {
            "weighted_user_to_infra_latency": weighted_user_to_infra_avg,
            "infrastructure_latency": infrastructure_latency,
            "weighted_total_latency": weighted_total,
            "user_latency_breakdown": user_breakdown,
            "total_user_weight": total_weight
        }
        
        logger.debug(
            "Weighted end-to-end latency for %s: weighted_user_to_infra=%.2fms, infra=%.2fms, total=%.2fms",
            infrastructure_region,
            weighted_user_to_infra_avg,
            infrastructure_latency,
            weighted_total
        )
        
        return result

    def calculate_transition_delay(
        self,
        resource_type: str,
        utilization: float = 0.0,
        source_region: Optional[str] = None,
        dest_region: Optional[str] = None,
    ) -> float:
        """
        Calculate delay for a single transition (δ(t) in equation 1).

        Formula: δ(t) = BaseLatency * RegionFactor * CongestionFactor * (1 + StochasticVariation)

        Args:
            resource_type: Type of resource being used (e.g., 'vm-standard', 'storage-ssd')
            utilization: Current resource utilization (0.0 to 1.0)
            source_region: Source GCP region (e.g., 'us-central1')
            dest_region: Destination GCP region (e.g., 'us-east1')

        Returns:
            Transition delay in milliseconds with stochastic variation applied if enabled
            
        Raises:
            ValueError: If resource_type is empty or utilization is outside [0, 1]
        """
        if not resource_type:
            raise ValueError("Resource type cannot be empty")
            
        if not 0.0 <= utilization <= 1.0:
            raise ValueError(f"Utilization must be between 0.0 and 1.0, got {utilization}")

        # Get base latency for resource type
        base_latency = self.get_base_latency(resource_type)

        # Apply region factor if both regions are specified
        region_factor = 1.0
        if source_region is not None and dest_region is not None:
            region_factor = self.get_region_latency_factor(
                source_region, dest_region
            )

        # Calculate congestion effect
        congestion_factor = self.calculate_congestion_factor(
            resource_type, utilization
        )

        # Calculate base delay without stochastic variation
        delay = base_latency * region_factor * congestion_factor
        
        # Log the components for debugging
        logger.debug(
            "Transition delay for %s (util=%.2f): base=%.2fms * region=%.2f * congestion=%.2f = %.2fms",
            resource_type,
            utilization,
            base_latency,
            region_factor,
            congestion_factor,
            delay
        )

        # Apply stochastic variation if enabled
        delay = self._apply_stochastic_variation(delay, resource_type)
        
        # Ensure non-negative delay
        return max(0.0, delay)

    def calculate_congestion_factor(
        self, resource_type: str, utilization: float
    ) -> float:
        """
        Calculate congestion factor based on resource utilization (Δcongestion).

        Formula: 
        - If utilization ≤ threshold: CongestionFactor = 1.0
        - If utilization > threshold: CongestionFactor = 1.0 + factor * ((utilization - threshold) / (1 - threshold))^exp

        Args:
            resource_type: Type of resource (e.g., 'vm-standard', 'storage-ssd')
            utilization: Current resource utilization (0.0 to 1.0)

        Returns:
            Congestion multiplier (always ≥ 1.0, where 1.0 means no congestion)
            
        Raises:
            ValueError: If utilization is outside [0, 1] or resource_type is invalid
        """
        if not resource_type:
            raise ValueError("Resource type cannot be empty")
            
        # Clamp utilization to [0,1] range and log if it was outside
        if utilization < 0.0 or utilization > 1.0:
            logger.warning(
                "Utilization %.2f is outside [0, 1] range for %s. Clamping to valid range.",
                utilization,
                resource_type
            )
            utilization = max(0.0, min(1.0, utilization))

        try:
            # Get congestion parameters for this resource type
            params = self.get_congestion_params(resource_type)
            threshold = float(params["threshold"])
            factor = float(params["factor"])
            exp = float(params["exp"])
            
            # Validate parameters
            if not 0.0 <= threshold < 1.0:
                raise ValueError(f"Threshold must be in [0, 1), got {threshold}")
                
            if factor < 0.0:
                logger.warning(
                    "Negative congestion factor %.2f for %s. Using absolute value.",
                    factor,
                    resource_type
                )
                factor = abs(factor)
                
            # If utilization is below threshold, no congestion effect
            if utilization <= threshold:
                return 1.0

            # Calculate congestion effect using exponential model
            # This creates a steep curve as utilization approaches 100%
            normalized_util = (utilization - threshold) / (1.0 - threshold)
            congestion_factor = 1.0 + factor * (normalized_util ** exp)
            
            logger.debug(
                "Congestion factor for %s at %.1f%% utilization: %.2f (threshold=%.2f, factor=%.2f, exp=%.2f)",
                resource_type,
                utilization * 100,
                congestion_factor,
                threshold,
                factor,
                exp
            )

            return max(1.0, congestion_factor)  # Ensure at least 1.0
            
        except (KeyError, TypeError) as e:
            logger.error(
                "Error calculating congestion factor for %s: %s. Using default of 1.0.",
                resource_type,
                str(e)
            )
            return 1.0

    def calculate(
        self,
        simulation_results: Union["SimulationResult", Dict[str, Any]],
        resource_mapping: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Calculate latency metrics from simulation token events.

        Args:
            simulation_results: The raw results from the simulation.
            resource_mapping: A dictionary mapping resource IDs to resource objects.

        Returns:
            A dictionary containing latency statistics.
        """
        # Accept both object with attribute and dict-based inputs
        token_events = getattr(simulation_results, "token_flow_log", [])
        if not token_events and hasattr(simulation_results, "token_flow_logs"):
            token_events = getattr(simulation_results, "token_flow_logs", [])
        if not token_events and hasattr(simulation_results, "token_traces"):
            token_events = getattr(simulation_results, "token_traces", [])
        if not token_events and isinstance(simulation_results, dict):
            token_events = simulation_results.get("token_flow_log", [])
            if not token_events:
                # Fallbacks seen elsewhere in the codebase
                token_events = simulation_results.get("token_traces", [])
                if not token_events:
                    token_events = simulation_results.get("token_flow_logs", [])
        logger.debug(
            "Starting latency calculation with %d token events",
            len(token_events),
        )
        self.resource_mapping = resource_mapping

        if not token_events:
            logger.warning(
                "LatencyModel: 'token_events' is empty. No latency calculated."
            )
            return self._empty_latency_results()

        # Log the first few token events for debugging
        for i, event in enumerate(token_events[:3]):
            logger.debug(
                "Token event %d: %s",
                i,
                {
                    "event_type": event.get("event"),
                    "token_id": (
                        event.get("token", {}).get("id")
                        if isinstance(event.get("token"), dict)
                        else str(event.get("token"))
                    ),
                    "time": event.get("time"),
                },
            )

        # Normalize events into dicts and reconstruct per-token histories
        normalized_events: list[dict] = []
        for ev in token_events:
            try:
                if isinstance(ev, dict):
                    evt = ev.get("event") or ev.get("event_type") or ev.get("type")
                    details = ev.get("details", {}) or {}
                    norm = {
                        "timestamp": ev.get("timestamp"),
                        "event": evt,
                        "details": details,
                    }
                    if "token" in ev:
                        norm["token"] = ev.get("token")
                else:
                    # Pydantic/BaseModel or object
                    try:
                        data = ev.model_dump()  # pydantic v2
                    except Exception:
                        try:
                            data = ev.dict()  # pydantic v1
                        except Exception:
                            data = {
                                "timestamp": getattr(ev, "timestamp", None),
                                "event": getattr(ev, "event", None) or getattr(ev, "event_type", None),
                                "details": getattr(ev, "details", {}),
                            }
                    evt = data.get("event") or data.get("event_type")
                    details = data.get("details", {}) or {}
                    norm = {
                        "timestamp": data.get("timestamp"),
                        "event": evt,
                        "details": details,
                    }
                    if "token" in data:
                        norm["token"] = data.get("token")
                normalized_events.append(norm)
            except Exception:
                continue

        try:
            normalized_events.sort(key=lambda e: (float(e.get("timestamp")) if e.get("timestamp") is not None else 0.0))
        except Exception:
            pass

        histories_by_token: dict[str, list[tuple]] = {}
        for ev in normalized_events:
            evt = ev.get("event")
            if not evt:
                continue
            details = ev.get("details", {}) or {}
            token_id = details.get("token_id")
            place = details.get("place") or details.get("resource") or details.get("resource_id")
            ts = ev.get("timestamp")
            if not token_id or place is None or ts is None:
                continue
            if evt in ("token_produced", "token_arrived", "place_entered"):
                histories_by_token.setdefault(str(token_id), []).append(("start", str(place), float(ts)))
            elif evt in ("token_consumed", "token_departed", "place_left"):
                histories_by_token.setdefault(str(token_id), []).append(("arrival", str(place), float(ts)))

        completed_tokens = []
        for event in normalized_events:
            if event.get("event") == "token_completed":
                token = event.get("token", {}) or {}
                
                # If token dict is empty or missing required fields, try to build from details
                if not token or not token.get("id"):
                    details = event.get("details", {}) or {}
                    metrics = details.get("metrics", {}) or {}
                    
                    # Reconstruct token dict from details
                    token_id = details.get("token_id")
                    if token_id:
                        token = {
                            "id": token_id,
                            "creation_time": metrics.get("creation_time", 0.0),
                            "completion_time": metrics.get("completion_time", event.get("timestamp", 0.0)),
                            "attributes": details.get("attributes", {})
                        }
                        logger.debug(
                            "Reconstructed token from details: id=%s, creation_time=%.3f, completion_time=%.3f",
                            token_id,
                            token.get("creation_time", 0.0),
                            token.get("completion_time", 0.0)
                        )
                
                if not isinstance(token, dict):
                    logger.warning("Skipping invalid token in event: %s", event)
                    continue
                try:
                    tid = token.get("id") or event.get("details", {}).get("token_id")
                    if tid and "history" not in token and histories_by_token.get(str(tid)):
                        token["history"] = histories_by_token[str(tid)]
                except Exception:
                    pass
                completed_tokens.append(token)
                # Guard against mixed timebases; log only sane values
                try:
                    ct = float(token.get("creation_time", 0))
                    ft = float(token.get("completion_time", 0))
                    latency_val = ft - ct
                    if -10.0 < latency_val < 1e6:
                        logger.debug(
                            "Completed token %s: creation_time=%.3f, completion_time=%.3f, latency=%.3f",
                            token.get("id"), ct, ft, latency_val,
                        )
                except Exception:
                    pass
                if "history" in token:
                    logger.debug(
                        "Token %s history: %s",
                        token.get("id"),
                        token["history"],
                    )

        if not completed_tokens:
            event_types = list(set(e.get("event") for e in normalized_events if e.get("event")))
            logger.warning(
                "LatencyModel: No valid TOKEN_COMPLETED events found. Total events: %d, Event types: %s",
                len(token_events),
                event_types,
            )
            logger.debug("LatencyModel: Sample events (first 5): %s", normalized_events[:5])
            return self._empty_latency_results()

        # Calculate latencies (infrastructure-only or end-to-end based on configuration)
        latencies = []
        user_to_infra_latencies = []
        infrastructure_latencies = []
        end_to_end_breakdown = {}
        
        for token in completed_tokens:
            if "completion_time" not in token or "creation_time" not in token:
                logger.warning(
                    "Token %s missing required timestamps: %s",
                    token.get("id"),
                    token,
                )
                continue
            
            # Calculate infrastructure-internal latency (existing behavior)
            infrastructure_latency_seconds = token["completion_time"] - token["creation_time"]
            infrastructure_latencies.append(infrastructure_latency_seconds)
            
            if self.end_to_end_enabled:
                # Calculate end-to-end latency including user-to-infrastructure component
                user_region = token.get("user_region")
                infrastructure_region = self._get_token_infrastructure_region(token)
                
                if user_region and infrastructure_region:
                    # Convert infrastructure latency from seconds to milliseconds for calculation
                    infrastructure_latency_ms = infrastructure_latency_seconds * 1000
                    
                    # Calculate end-to-end latency using the dedicated method
                    end_to_end_result = self.calculate_end_to_end_latency(
                        user_region=user_region,
                        infrastructure_region=infrastructure_region,
                        infrastructure_latency=infrastructure_latency_ms,
                        apply_stochastic_variation=True
                    )
                    
                    # Extract latency components (convert back to seconds for consistency)
                    user_to_infra_latency_seconds = end_to_end_result["user_to_infra_latency"] / 1000.0
                    total_end_to_end_seconds = end_to_end_result["total_end_to_end_latency"] / 1000.0
                    
                    user_to_infra_latencies.append(user_to_infra_latency_seconds)
                    latencies.append(total_end_to_end_seconds)
                    
                    # Store breakdown for this token
                    end_to_end_breakdown[token.get("id", "unknown")] = {
                        "user_region": user_region,
                        "infrastructure_region": infrastructure_region,
                        "user_to_infra_latency": end_to_end_result["user_to_infra_latency"],
                        "infrastructure_latency": infrastructure_latency_ms,
                        "total_end_to_end_latency": end_to_end_result["total_end_to_end_latency"]
                    }
                    
                    logger.debug(
                        "Token %s end-to-end latency: user_to_infra=%.2fms + infrastructure=%.2fms = %.2fms total",
                        token.get("id"),
                        end_to_end_result["user_to_infra_latency"],
                        infrastructure_latency_ms,
                        end_to_end_result["total_end_to_end_latency"]
                    )
                else:
                    # Fallback to infrastructure-only if user region info is missing
                    latencies.append(infrastructure_latency_seconds)
                    logger.warning(
                        "Token %s missing user_region or infrastructure_region info, using infrastructure-only latency",
                        token.get("id")
                    )
            else:
                # Use infrastructure-only latency (existing behavior)
                latencies.append(infrastructure_latency_seconds)
                logger.debug(
                    "Token %s infrastructure latency: %.3f seconds", 
                    token.get("id"), 
                    infrastructure_latency_seconds
                )

        if not latencies:
            logger.warning(
                "No valid latencies could be calculated from %d completed tokens",
                len(completed_tokens),
            )
            return self._empty_latency_results()

        # Analyze latency per resource and path
        if len(latencies) <= 5:
            logger.debug("Calculating per-resource and per-path latencies")

        # Determine if tokens have usable histories (avoid calling helpers that warn)
        def _has_history_pairs(tokens: list[dict]) -> bool:
            try:
                for t in tokens:
                    hist = t.get("history") if isinstance(t, dict) else None
                    if isinstance(hist, list):
                        # Look for at least one start->arrival pair
                        for i in range(1, len(hist)):
                            try:
                                if (
                                    isinstance(hist[i - 1], (list, tuple))
                                    and isinstance(hist[i], (list, tuple))
                                    and len(hist[i - 1]) >= 3
                                    and len(hist[i]) >= 3
                                    and hist[i - 1][0] == "start"
                                    and hist[i][0] == "arrival"
                                ):
                                    return True
                            except Exception:
                                continue
                return False
            except Exception:
                return False

        if _has_history_pairs(completed_tokens):
            resource_latency = self._calculate_resource_latency(completed_tokens)
            path_latency = self._calculate_path_latency_from_tokens(completed_tokens)
        else:
            # Fallback: derive per-resource durations directly from normalized token traces
            durations_by_res: dict[str, list[float]] = {}
            # Rebuild a minimal history from token traces if not already available
            # We re-run a lightweight reconstruction scoped to completed token ids
            try:
                completed_ids = {str(t.get("id")) for t in completed_tokens if isinstance(t, dict) and t.get("id")}
            except Exception:
                completed_ids = set()
            # Try to use 'normalized_events' built earlier in this method if available
            try:
                events_iter = normalized_events  # type: ignore[name-defined]
            except Exception:
                events_iter = []
            last_start: dict[tuple[str, str], float] = {}
            for ev in events_iter:
                try:
                    evt = ev.get("event")
                    details = ev.get("details", {}) or {}
                    tid = str(details.get("token_id")) if details.get("token_id") is not None else None
                    if not tid or (completed_ids and tid not in completed_ids):
                        continue
                    place = details.get("place") or details.get("resource") or details.get("resource_id")
                    ts = ev.get("timestamp")
                    if place is None or ts is None:
                        continue
                    key = (tid, str(place))
                    if evt in ("token_produced", "token_arrived", "place_entered"):
                        last_start[key] = float(ts)
                    elif evt in ("token_consumed", "token_departed", "place_left"):
                        if key in last_start:
                            duration = float(ts) - float(last_start.pop(key))
                            if duration >= 0:
                                durations_by_res.setdefault(str(place), []).append(duration)
                except Exception:
                    continue
            # Build stats dict
            resource_latency = {}
            try:
                for res, durs in durations_by_res.items():
                    if not durs:
                        continue
                    resource_latency[res] = {
                        "average": float(np.mean(durs)),
                        "p95": float(np.percentile(durs, 95)),
                        "p99": float(np.percentile(durs, 99)),
                        "max": float(np.max(durs)),
                        "min": float(np.min(durs)),
                        "count": len(durs),
                    }
            except Exception:
                resource_latency = {}
            # For path latency, synthesize simple paths from production order if needed
            path_latency = {}

        # Build result structure
        result = {
            "total_requests": len(completed_tokens),
            "end_to_end": {
                "raw": latencies,
                "average": float(np.mean(latencies)),
                "p95": float(np.percentile(latencies, 95)),
                "p99": float(np.percentile(latencies, 99)),
                "max": float(np.max(latencies)),
                "min": float(np.min(latencies)),
            },
            "by_resource": resource_latency,
            "by_path": path_latency,
        }
        
        # Add end-to-end specific metrics if enabled
        if self.end_to_end_enabled:
            result["end_to_end_enabled"] = True
            result["latency_breakdown"] = end_to_end_breakdown
            
            # Add user-to-infrastructure latency statistics
            if user_to_infra_latencies:
                result["user_to_infrastructure"] = {
                    "raw": user_to_infra_latencies,
                    "average": float(np.mean(user_to_infra_latencies)),
                    "p95": float(np.percentile(user_to_infra_latencies, 95)),
                    "p99": float(np.percentile(user_to_infra_latencies, 99)),
                    "max": float(np.max(user_to_infra_latencies)),
                    "min": float(np.min(user_to_infra_latencies)),
                }
            
            # Add infrastructure-only latency statistics for comparison
            if infrastructure_latencies:
                result["infrastructure_only"] = {
                    "raw": infrastructure_latencies,
                    "average": float(np.mean(infrastructure_latencies)),
                    "p95": float(np.percentile(infrastructure_latencies, 95)),
                    "p99": float(np.percentile(infrastructure_latencies, 99)),
                    "max": float(np.max(infrastructure_latencies)),
                    "min": float(np.min(infrastructure_latencies)),
                }
        else:
            result["end_to_end_enabled"] = False

        logger.debug(
            "Latency calculation completed. Processed %d tokens, average latency: %.3f",
            len(completed_tokens),
            result["end_to_end"]["average"],
        )
        return result

    def _get_token_infrastructure_region(self, token: Dict[str, Any]) -> Optional[str]:
        """
        Extract the infrastructure region from a token's data or history.
        
        Args:
            token: Token dictionary containing token data and history
            
        Returns:
            Infrastructure region string if found, None otherwise
        """
        # First check if the token has a direct infrastructure_region field
        if "infrastructure_region" in token:
            return token["infrastructure_region"]
        
        # Try to infer from the first resource in the token's history
        if "history" in token and isinstance(token["history"], list) and token["history"]:
            first_event = token["history"][0]
            if isinstance(first_event, (list, tuple)) and len(first_event) >= 2:
                resource_id = first_event[1]  # Resource ID is typically the second element
                
                # Try to get region from resource mapping
                if resource_id in self.resource_mapping:
                    resource_info = self.resource_mapping[resource_id]
                    if hasattr(resource_info, 'region'):
                        return resource_info.region
                    elif isinstance(resource_info, dict) and 'region' in resource_info:
                        return resource_info['region']
        
        # Try to extract from resource IDs that might contain region info
        if "history" in token and isinstance(token["history"], list):
            for event in token["history"]:
                if isinstance(event, (list, tuple)) and len(event) >= 2:
                    resource_id = str(event[1])
                    # Look for common GCP region patterns in resource IDs
                    for region in ["us-central1", "us-east1", "us-west1", "europe-west1", "asia-southeast1"]:
                        if region in resource_id.lower():
                            return region
        
        # Default fallback - could be made configurable
        logger.debug("Could not determine infrastructure region for token %s, using default", token.get("id"))
        return "us-central1"  # Default region

    def _empty_latency_results(self) -> Dict:
        result = {
            "total_requests": 0,
            "end_to_end": {
                "raw": [],
                "average": 0,
                "p95": 0,
                "p99": 0,
                "max": 0,
                "min": 0,
            },
            "by_resource": {},
            "by_path": {},
        }
        
        # Add end-to-end specific fields if enabled
        if self.end_to_end_enabled:
            result["end_to_end_enabled"] = True
            result["latency_breakdown"] = {}
            result["user_to_infrastructure"] = {
                "raw": [],
                "average": 0,
                "p95": 0,
                "p99": 0,
                "max": 0,
                "min": 0,
            }
            result["infrastructure_only"] = {
                "raw": [],
                "average": 0,
                "p95": 0,
                "p99": 0,
                "max": 0,
                "min": 0,
            }
        else:
            result["end_to_end_enabled"] = False
            
        return result

    def _calculate_resource_latency(self, tokens: List[Dict]) -> Dict:
        """
        Calculate latency statistics per resource based on token histories.

        Args:
            tokens: List of completed tokens with their histories

        Returns:
            Dictionary mapping resource names to latency statistics
        """
        resource_stats = {}

        for token in tokens:
            if "history" not in token or not isinstance(
                token["history"], list
            ):
                logger.debug(
                    "Token %s has no history or invalid history format",
                    token.get("id"),
                )
                continue

            # Process each transition in the token's history
            for i, event in enumerate(token["history"]):
                if not isinstance(event, (list, tuple)) or len(event) < 3:
                    continue

                event_type, resource, timestamp = event[0], event[1], event[2]

                # Only process 'start' and 'arrival' events for now
                if event_type not in ("start", "arrival"):
                    continue

                if resource not in resource_stats:
                    resource_stats[resource] = []

                # Calculate time spent at this resource
                if (
                    i > 0
                    and token["history"][i - 1][0] == "start"
                    and event_type == "arrival"
                ):
                    prev_timestamp = token["history"][i - 1][2]
                    duration = timestamp - prev_timestamp
                    resource_stats[resource].append(duration)
                    logger.debug(
                        "Resource %s: token %s spent %.3f seconds",
                        resource,
                        token.get("id"),
                        duration,
                    )

        # Calculate statistics for each resource
        result = {}
        for resource, durations in resource_stats.items():
            if not durations:
                continue

            result[resource] = {
                "average": float(np.mean(durations)),
                "p95": float(np.percentile(durations, 95)),
                "p99": float(np.percentile(durations, 99)),
                "max": float(np.max(durations)),
                "min": float(np.min(durations)),
                "count": len(durations),
            }

        if not result:
            logger.warning(
                "Could not calculate per-resource latencies. "
                "Token histories may lack required timing information."
            )

        return result

    def _calculate_path_latency_from_tokens(self, tokens: List[Dict]) -> Dict:
        """
        Calculate latency statistics per path based on token histories.

        Args:
            tokens: List of completed tokens with their histories

        Returns:
            Dictionary mapping path strings to latency statistics
        """
        path_latencies = {}

        for token in tokens:
            if "history" not in token or not isinstance(
                token["history"], list
            ):
                continue

            # Create path key from 'start' events in the token's history
            path_parts = [
                item[1]
                for item in token.get("history", [])
                if item[0] == "start"
            ]
            if not path_parts:
                logger.debug(
                    "Token %s has no 'start' events in history",
                    token.get("id"),
                )
                continue

            path_key = " -> ".join(path_parts)

            # Calculate end-to-end latency for this token
            if "completion_time" not in token or "creation_time" not in token:
                logger.debug(
                    "Token %s missing timestamps, skipping path calculation",
                    token.get("id"),
                )
                continue

            latency = token["completion_time"] - token["creation_time"]

            if path_key not in path_latencies:
                path_latencies[path_key] = []

            path_latencies[path_key].append(latency)
            logger.debug(
                "Path %s: token %s latency %.3f",
                path_key,
                token.get("id"),
                latency,
            )

        if not path_latencies:
            logger.warning("No valid paths found in token histories")
            return {}

        # Calculate statistics for each path
        result = {}
        for path, latencies in path_latencies.items():
            if not latencies:
                continue

            result[path] = {
                "average": float(np.mean(latencies)),
                "p95": float(np.percentile(latencies, 95)),
                "p99": float(np.percentile(latencies, 99)),
                "max": float(np.max(latencies)),
                "min": float(np.min(latencies)),
                "count": len(latencies),
            }

        return result

    def get_latency_recommendations(self, latency_results: Dict) -> List[Dict]:
        """
        Generate recommendations to reduce latency based on simulation results.

        Args:
            latency_results: The results from the calculate method.

        Returns:
            A list of latency optimization recommendations.
        """
        recommendations = []

        # 1. High-latency resource recommendations
        resource_latencies = sorted(
            latency_results.get("by_resource", {}).items(),
            key=lambda item: item[1]["average"],
            reverse=True,
        )
        if resource_latencies:
            # Recommend optimizing the top 3 slowest resources
            for res_id, metrics in resource_latencies[:3]:
                resource_info = self.resource_mapping.get(res_id)
                if not resource_info:
                    continue
                res_type = getattr(resource_info, "resource_type", "unknown")
                recommendations.append(
                    {
                        "type": "resource_optimization",
                        "resource_id": res_id,
                        "resource_type": res_type,
                        "average_latency": metrics["average"],
                        "suggestion": f"Optimize '{res_id}' ({res_type}) as it is a major latency contributor. "
                        f"Consider scaling up, code optimization, or using a higher performance tier.",
                    }
                )

        # 2. High-latency path recommendations
        path_latencies = sorted(
            latency_results.get("by_path", {}).items(),
            key=lambda item: item[1]["average"],
            reverse=True,
        )
        if path_latencies:
            # Recommend optimizing the slowest path
            path, metrics = path_latencies[0]
            recommendations.append(
                {
                    "type": "path_optimization",
                    "path": path,
                    "average_latency": metrics["average"],
                    "suggestion": f"The request path '{path}' is the critical path. "
                    f"Analyze the resources in this path for bottlenecks.",
                }
            )

        # 3. Inter-region latency recommendations
        for path, metrics in path_latencies:
            path_nodes = path.split(" -> ")
            if len(path_nodes) > 1:
                for i in range(len(path_nodes) - 1):
                    src_res_id = path_nodes[i]
                    dst_res_id = path_nodes[i + 1]
                    src_info = self.resource_mapping.get(src_res_id)
                    dst_info = self.resource_mapping.get(dst_res_id)
                    src_region = (
                        getattr(src_info, "region", None) if src_info else None
                    )
                    dst_region = (
                        getattr(dst_info, "region", None) if dst_info else None
                    )

                    if src_region and dst_region and src_region != dst_region:
                        recommendations.append(
                            {
                                "type": "colocation",
                                "source_resource": src_res_id,
                                "dest_resource": dst_res_id,
                                "source_region": src_region,
                                "dest_region": dst_region,
                                "suggestion": f"High latency detected between '{src_res_id}' in {src_region} and "
                                f"'{dst_res_id}' in {dst_region}. Consider colocating them in the "
                                f"same region to reduce network latency.",
                            }
                        )

        return recommendations


def load_latency_model(
    latency_config: "LEAFCloudConfig.LatencyModelConfig",
) -> LatencyModel:
    """
    Factory function to create and initialize a latency model.

    Args:
        latency_config: LatencyModelConfig object containing latency parameters.

    Returns:
        Initialized LatencyModel instance
    """
    return LatencyModel(config=latency_config)
