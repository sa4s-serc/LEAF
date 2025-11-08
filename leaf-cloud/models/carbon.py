import os
import yaml
from typing import Dict, List, Optional, Union, Tuple, Any
import numpy as np
import logging

"""
Carbon Footprint Estimation Module for LEAF-Cloud Framework

This module implements the carbon footprint estimation model for GCP resources
based on their energy consumption and regional carbon intensity factors.
It provides functionality to calculate and aggregate carbon emissions across
all resources in the infrastructure model.

Implements equations 5 and 6 from the LEAF-Cloud mathematical foundation:
- Equation 5: Carbon footprint per resource based on energy and regional factors
- Equation 6: Aggregation of carbon footprint across all resources
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
    
    # Default carbon intensity factors (kg CO2e per kWh) by GCP region
    # These are fallback values if config file doesn't provide them
    DEFAULT_CARBON_FACTORS = {
        'us-central1': 0.456,     # Iowa
        'us-east1': 0.379,        # South Carolina
        'us-east4': 0.303,        # Northern Virginia
        'us-west1': 0.076,        # Oregon (high renewable %)
        'us-west2': 0.192,        # Los Angeles
        'us-west3': 0.459,        # Salt Lake City
        'us-west4': 0.368,        # Las Vegas
        'europe-west1': 0.088,    # Belgium (low carbon)
        'europe-west2': 0.208,    # London
        'europe-west3': 0.312,    # Frankfurt
        'europe-west4': 0.417,    # Netherlands
        'europe-west6': 0.027,    # Zurich (very low carbon)
        'asia-east1': 0.541,      # Taiwan
        'asia-east2': 0.632,      # Hong Kong
        'asia-northeast1': 0.506, # Tokyo
        'asia-northeast2': 0.425, # Osaka
        'asia-northeast3': 0.449, # Seoul
        'asia-south1': 0.708,     # Mumbai (high carbon)
        'asia-southeast1': 0.493, # Singapore
        'asia-southeast2': 0.689, # Jakarta
        'australia-southeast1': 0.589, # Sydney
        'southamerica-east1': 0.065, # São Paulo (low carbon, hydro power)
        'global': 0.450,          # Global average as fallback
    }
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the carbon model with regional carbon intensity factors.
        
        Args:
            config_path: Path to configuration file containing carbon factors
        """
        self.carbon_factors = self.DEFAULT_CARBON_FACTORS.copy()
        
        # Load carbon factors from configuration if available
        if config_path and os.path.exists(config_path):
            self._load_carbon_factors(config_path)
            
        logger.info(f"Carbon model initialized with {len(self.carbon_factors)} regional factors")
    
    def _load_carbon_factors(self, config_path: str) -> None:
        """
        Load carbon intensity factors from configuration file.
        
        Args:
            config_path: Path to YAML configuration file
        """
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                
            if 'carbon_factors' in config:
                # Update default factors with values from config
                self.carbon_factors.update(config['carbon_factors'])
                logger.debug(f"Loaded carbon factors from {config_path}")
            else:
                logger.warning(f"No carbon_factors found in {config_path}")
                
        except Exception as e:
            logger.error(f"Error loading carbon factors: {e}")
    
    def get_carbon_factor(self, region: str) -> float:
        """
        Get the carbon intensity factor for a specific GCP region.
        
        Args:
            region: GCP region code (e.g., 'us-central1')
            
        Returns:
            Carbon intensity factor in kg CO2e per kWh
        """
        # Return the region-specific factor or global average as fallback
        return self.carbon_factors.get(region, self.carbon_factors['global'])
    
    def calculate_resource_carbon(self, 
                                 energy_consumption: float, 
                                 region: str) -> float:
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
    
    def aggregate_carbon_footprint(self, 
                                  resource_emissions: Dict[str, Tuple[float, str]]) -> Dict:
        """
        Aggregate carbon footprint across all resources (Equation 6).
        
        Formula: C_total = Sum(C_i) for all resources i
        
        Args:
            resource_emissions: Dictionary mapping resource IDs to tuples of 
                              (energy_consumption, region)
        
        Returns:
            Dictionary with total and per-region carbon emissions
        """
        total_emissions = 0.0
        regional_emissions = {}
        resource_emissions_result = {}
        
        # Calculate carbon emissions for each resource
        for resource_id, (energy, region) in resource_emissions.items():
            emissions = self.calculate_resource_carbon(energy, region)
            resource_emissions_result[resource_id] = emissions
            total_emissions += emissions
            
            # Aggregate by region
            if region not in regional_emissions:
                regional_emissions[region] = 0.0
            regional_emissions[region] += emissions
        
        # Prepare results
        results = {
            # preferred name used by CarbonModel and tests
            'total_carbon_emissions': total_emissions,
            # alias expected downstream by leaf_cloud / CLI
            'total_co2eq_kg':       total_emissions,
            'regional_emissions':   regional_emissions,
            'resource_emissions':   resource_emissions_result
        }
        
        return results
    
    def calculate_total_carbon(self, 
                              energy_by_resource_region: Dict[str, Dict[str, float]]) -> Dict:
        """
        Calculate total carbon footprint from energy consumption by resource and region.
        
        Args:
            energy_by_resource_region: Nested dictionary mapping resources to regions to energy
                                     {resource_id: {region: energy_consumption}}
                                     
        Returns:
            Dictionary with carbon emissions results
        """
        # Convert the nested dict to format expected by aggregate_carbon_footprint
        resource_emissions = {}
        for resource_id, regions in energy_by_resource_region.items():
            for region, energy in regions.items():
                # If a resource spans multiple regions, use ID with region suffix
                if len(regions) > 1:
                    key = f"{resource_id}_{region}"
                else:
                    key = resource_id
                resource_emissions[key] = (energy, region)
        
        # Calculate and return emissions
        return self.aggregate_carbon_footprint(resource_emissions)
    
    def get_optimization_recommendations(self, 
                                        carbon_results: Dict,
                                        energy_threshold: float = 10.0) -> List[Dict]:
        """
        Generate recommendations to reduce carbon footprint.
        
        Args:
            carbon_results: Carbon emission results from calculate_total_carbon
            energy_threshold: Minimum energy consumption to consider for optimization
            
        Returns:
            List of recommendations
        """
        recommendations = []
        
        # Find high-carbon resources that could be relocated
        for resource_id, emissions in carbon_results['resource_emissions'].items():
            # Skip resources with minimal emissions
            if emissions < energy_threshold * 0.1:  # 10% of threshold
                continue
                
            # Extract resource region from the ID if it contains a region suffix
            resource_parts = resource_id.split('_')
            current_region = resource_parts[-1] if len(resource_parts) > 1 and resource_parts[-1] in self.carbon_factors else None
            
            # Find better regions based on carbon intensity
            if current_region:
                better_regions = {
                    region: factor for region, factor in self.carbon_factors.items()
                    if factor < self.carbon_factors[current_region] * 0.7  # 30% better
                }
                
                if better_regions:
                    # Sort regions by carbon intensity
                    sorted_regions = sorted(better_regions.items(), key=lambda x: x[1])[:3]
                    
                    recommendations.append({
                        'resource_id': resource_id,
                        'current_region': current_region,
                        'current_carbon_factor': self.carbon_factors[current_region],
                        'emissions': emissions,
                        'recommended_regions': sorted_regions,
                        'potential_savings_percent': round((1 - sorted_regions[0][1] / self.carbon_factors[current_region]) * 100, 1)
                    })
        
        return recommendations
    
    def calculate(self, results: Dict[str, Any], energy_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate carbon emissions based on provided energy metrics and resource regions.
        """
        # energy_data: result of EnergyModel.calculate, contains 'resource_energy' which should be kWh per resource
        resource_energy_kwh = energy_data.get('resource_energy', {}) # This should now be kWh
        
        resource_info = results.get('resource_info', {})
        emissions_input = {}
        
        if not resource_energy_kwh:
            logger.warning("CarbonModel.calculate: 'resource_energy' in energy_data is empty. No carbon emissions will be calculated.")
            return {
                'total_carbon_emissions': 0.0,
                'regional_emissions': {},
                'resource_emissions': {}
            }

        for rid, energy_kwh_val in resource_energy_kwh.items(): # energy_kwh_val renamed
            region = resource_info.get(rid, {}).get('region', 'global')
            if energy_kwh_val is None: # Skip if energy is None
                logger.warning(f"CarbonModel.calculate: Energy for resource {rid} is None. Skipping carbon calculation for this resource.")
                continue
            emissions_input[rid] = (float(energy_kwh_val), region) # Ensure energy is float

        if not emissions_input:
            logger.warning("CarbonModel.calculate: emissions_input is empty after processing (e.g. all energies were None). No carbon emissions will be calculated.")
            return {
                'total_carbon_emissions': 0.0,
                'regional_emissions': {},
                'resource_emissions': {}
            }
            
        carbon_results = self.aggregate_carbon_footprint(emissions_input)
        return carbon_results

def load_carbon_model(config_path: Optional[str] = None) -> CarbonModel:
    """
    Factory function to create and initialize a carbon model.
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        Initialized CarbonModel instance
    """
    return CarbonModel(config_path)