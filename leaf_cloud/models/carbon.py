from __future__ import annotations
from typing import Any, Dict, List, Literal, Optional, TypedDict, Tuple, Union, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from ..config import LEAFCloudConfig

# Type aliases for better readability
ResourceID = str
Region = str
EnergyKwh = float
CarbonEmissions = float
CarbonFactor = float

# Type for resource emissions mapping
ResourceEmissionsInput = Dict[ResourceID, Tuple[EnergyKwh, Region]]

# Type for carbon emissions result
class CarbonEmissionsResult(TypedDict):
    total_carbon_emissions: CarbonEmissions
    regional_emissions: Dict[Region, CarbonEmissions]
    resource_emissions: Dict[ResourceID, CarbonEmissions]

# Type for optimization recommendation
class OptimizationRecommendation(TypedDict):
    resource_id: str
    current_region: str
    current_carbon_factor: float
    emissions_kg_co2e: float
    recommended_regions: Dict[Region, CarbonFactor]
    potential_savings_percent: float

"""
Carbon Footprint Estimation Module for LEAF-Cloud Framework

This module implements the carbon footprint estimation model for GCP resources
based on their energy consumption and regional carbon intensity factors.
It provides functionality to calculate and aggregate carbon emissions across
all resources in the infrastructure model.
"""


# Configure logging
logger = logging.getLogger(__name__)


class CarbonModel:
    """
    Carbon footprint estimation model for cloud resources.

    This class calculates carbon emissions based on energy consumption
    and regional carbon intensity factors, implementing equations 5 and 6
    from the LEAF-Cloud framework.
    """

    def __init__(self, config: "LEAFCloudConfig.CarbonModelConfig") -> None:
        """
        Initialize the carbon model with configuration.

        Args:
            config: Carbon model configuration containing regional factors and default values.
        """
        self.name: str = "carbon"
        self.carbon_factors: Dict[Region, CarbonFactor] = config.regions
        self.default_factor: CarbonFactor = config.default_factor
        self.resource_mapping: Dict[ResourceID, Dict[str, Any]] = {}

        logger.info(
            f"Carbon model initialized with {len(self.carbon_factors)} regional factors and "
            f"default factor {self.default_factor}."
        )

    def get_carbon_factor(self, region: Region) -> CarbonFactor:
        """
        Get the carbon intensity factor for a specific GCP region.

        Args:
            region: GCP region code (e.g., 'us-central1')

        Returns:
            Carbon intensity factor in kg CO2e per kWh
        """
        # Return the region-specific factor or the configured default factor as fallback
        return self.carbon_factors.get(region, self.default_factor)

    def calculate_resource_carbon(
        self, energy_consumption: EnergyKwh, region: Region
    ) -> CarbonEmissions:
        """
        Calculate carbon emissions for a single resource (Equation 5).

        Formula: C_i = E_i * CF(region_i)
        Where:
            - C_i is carbon emissions for resource i
            - E_i is energy consumption for resource i
            - CF(region_i) is carbon factor for the region of resource i

        Args:
            energy_consumption: Energy used by resource in kWh
            region: GCP region where the resource is deployed

        Returns:
            Carbon emissions in kg CO2e
        """
        carbon_factor = self.get_carbon_factor(region)
        carbon_emissions = energy_consumption * carbon_factor

        return carbon_emissions

    def aggregate_carbon_footprint(
        self, resource_emissions: ResourceEmissionsInput
    ) -> CarbonEmissionsResult:
        """
        Aggregate carbon footprint across all resources.

        Args:
            resource_emissions: Dictionary mapping resource IDs to tuples of
                            (energy_consumption, region)

        Returns:
            Dictionary with the following keys:
            - total_carbon_emissions: Total carbon emissions in kg CO2e
            - regional_emissions: Dict mapping regions to their total emissions
            - resource_emissions: Dict mapping resource IDs to their emissions
        """
        total_emissions: CarbonEmissions = 0.0
        regional_emissions: Dict[Region, CarbonEmissions] = {}
        resource_emissions_result: Dict[ResourceID, CarbonEmissions] = {}

        # Calculate carbon emissions for each resource
        for resource_id, (energy, region) in resource_emissions.items():
            if energy is None or region is None:
                continue

            try:
                emissions = self.calculate_resource_carbon(energy, region)
                resource_emissions_result[resource_id] = emissions
                total_emissions += emissions

                # Aggregate by region
                if region not in regional_emissions:
                    regional_emissions[region] = 0.0
                regional_emissions[region] += emissions
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Error calculating carbon for resource {resource_id}: {str(e)}"
                )
                continue

        # Prepare and return typed results
        return CarbonEmissionsResult(
            total_carbon_emissions=total_emissions,
            regional_emissions=regional_emissions,
            resource_emissions=resource_emissions_result,
        )

    def get_optimization_recommendations(
        self, carbon_results: CarbonEmissionsResult
    ) -> List[OptimizationRecommendation]:
        """
        Generate recommendations to reduce carbon footprint by relocating resources.

        Args:
            carbon_results: Carbon emission results from the calculate method.

        Returns:
            A list of recommendation dictionaries.
        """
        recommendations: List[OptimizationRecommendation] = []
        resource_emissions = carbon_results.get("resource_emissions", {})

        for resource_id, emissions in resource_emissions.items():
            if emissions <= 0:
                continue

            resource_info = self.resource_mapping.get(resource_id, {})
            if isinstance(resource_info, dict):
                current_region = resource_info.get("region")
            else:
                current_region = getattr(resource_info, "region", None)

            if not current_region or current_region not in self.carbon_factors:
                continue

            current_factor = self.get_carbon_factor(current_region)

            # Find regions that are at least 25% cleaner
            better_regions = {
                region: factor
                for region, factor in self.carbon_factors.items()
                if factor < current_factor * 0.75
            }

            if better_regions:
                sorted_regions = sorted(
                    better_regions.items(), key=lambda item: item[1]
                )[:3]
                best_new_region, best_new_factor = sorted_regions[0]

                recommendations.append(
                    {
                        "resource_id": resource_id,
                        "current_region": current_region,
                        "current_carbon_factor": current_factor,
                        "emissions_kg_co2e": emissions,
                        "recommended_regions": dict(sorted_regions),
                        "potential_savings_percent": round(
                            (1 - best_new_factor / current_factor) * 100, 1
                        ),
                    }
                )

        recommendations.sort(
            key=lambda x: x["emissions_kg_co2e"], reverse=True
        )
        return recommendations

    def calculate(
        self, 
        energy_data: Union[Any, Dict[str, Any]], 
        resource_mapping: Optional[Dict[ResourceID, Dict[str, Any]]] = None
    ) -> CarbonEmissionsResult:
        """
        Calculate carbon emissions based on energy metrics and resource regions.

        Args:
            energy_data: Either a SimulationResult object or a dictionary containing energy metrics.
            resource_mapping: A dictionary mapping resource IDs to resource objects.

        Returns:
            A dictionary with processed carbon metrics in the format:
            {
                'total_carbon_emissions': float,
                'regional_emissions': Dict[str, float],
                'resource_emissions': Dict[str, float]
            }
        """
        # Update resource mapping if provided
        if resource_mapping is not None:
            self.resource_mapping = resource_mapping

        # Handle both SimulationResult and dictionary inputs
        resource_energy: Dict[ResourceID, Optional[EnergyKwh]] = {}
        
        if hasattr(energy_data, "energy_metrics"):
            # Get energy metrics from SimulationResult
            energy_metrics = getattr(energy_data, "energy_metrics", {}) or {}
            resource_energy = getattr(energy_metrics, "get", lambda k, v: {})("resource_energy", {})
        elif isinstance(energy_data, dict):
            # Handle direct dictionary input
            if "energy_metrics" in energy_data:
                energy_metrics = energy_data.get("energy_metrics", {}) or {}
                resource_energy = energy_metrics.get("resource_energy", {})
            else:
                # Backward compatibility for direct resource_energy
                resource_energy = energy_data.get("resource_energy", {})

        if not resource_energy:
            logger.warning(
                "CarbonModel: No energy data found. No carbon emissions calculated."
            )
            return CarbonEmissionsResult(
                total_carbon_emissions=0.0,
                regional_emissions={},
                resource_emissions={},
            )

        # Extract resource energy and region information
        emissions_input: ResourceEmissionsInput = {}
        for res_id, energy_kwh in resource_energy.items():
            if energy_kwh is None:
                continue

            resource_info = self.resource_mapping.get(res_id, {})
            region: Region = "global"
            
            if isinstance(resource_info, dict):
                region = resource_info.get("region", "global")
            elif hasattr(resource_info, "region"):
                region = getattr(resource_info, "region", "global")

            try:
                emissions_input[res_id] = (float(energy_kwh), region)
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Invalid energy value '{energy_kwh}' for resource {res_id}: {str(e)}"
                )
                continue

        if not emissions_input:
            logger.warning(
                "CarbonModel: No valid energy data found after processing. No carbon emissions calculated."
            )
            return CarbonEmissionsResult(
                total_carbon_emissions=0.0,
                regional_emissions={},
                resource_emissions={},
            )

        # Calculate and return emissions
        return self.aggregate_carbon_footprint(emissions_input)


def load_carbon_model(
    carbon_config: "LEAFCloudConfig.CarbonModelConfig",
) -> CarbonModel:
    """
    Factory function to create and initialize a carbon model.

    Args:
        carbon_config: CarbonModelConfig object containing regional factors and default factor.

    Returns:
        Initialized CarbonModel instance
    """
    return CarbonModel(config=carbon_config)
