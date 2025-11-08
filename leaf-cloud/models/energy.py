import os
import yaml
from typing import Dict, List, Optional, Union, Tuple, Any
import numpy as np
import logging

"""
Energy Consumption Estimation Module for LEAF-Cloud Framework

This module implements the energy consumption estimation model for GCP resources
based on their utilization, resource types, and specific energy profiles.
It provides functionality to calculate and aggregate energy consumption across
all resources in the infrastructure model.

Implements equations 2, 3, and 4 from the LEAF-Cloud mathematical foundation:
- Equation 2: Energy consumption per resource based on utilization
- Equation 3: Weighted energy consumption for different resource types
- Equation 4: Aggregate energy consumption across all resources
"""

# Configure logging
logger = logging.getLogger(__name__)

class EnergyModel:
    """
    Energy consumption estimation model for cloud resources.
    """
    DEFAULT_ENERGY_WEIGHTS = {
        'compute': 1.0, 'vm-standard': 1.0, 'vm-highcpu': 1.2, 'vm-highmem': 1.15,
        'gke-node': 1.05, 'cloud-function': 0.2, 'cloud-run': 0.8, 'storage': 0.7,
        'persistent-disk': 0.5, 'cloud-storage': 0.3, 'cloud-sql': 0.8,
        'bigtable': 0.85, 'firestore': 0.6, 'network': 0.5, 'load-balancer': 0.4,
        'vpn': 0.35, 'cdn': 0.45, 'security': 0.3, 'kms': 0.25, 'armor': 0.3, 'default': 0.5
    }
    DEFAULT_ENERGY_FUNCTIONS = {
        'compute': {'idle': 0.015, 'max': 0.150},
        'storage': {'idle': 0.005, 'max': 0.10},
        'network': {'idle': 0.002, 'max': 0.05},
        'default': {'idle': 0.001, 'max': 0.005},
        "generic":  {"idle": 0.005, "max": 0.10},
        "cloud-run": {"idle": 0.003, "max": 0.060},
        "database":  {"idle": 0.090, "max": 0.750},
    }
    CUSTOM_ENERGY_PROFILES = {
        "cr-aula-spring": {"idle": 0.010, "max": 0.020},  # kW
        "google_sql_database_instance": {"idle": 0.090, "max": 1.250},
        "spring_boot_terraform_cloud_run_service_ip_demo": {"idle": 0.250, "max": 0.300},
        "my_cluster": {"idle": 0.000905, "max": 0.001222},
        "primary_nodes": {"idle": 0.00012, "max": 0.00020},
    }
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize the energy model with resource-specific factors."""
        self.energy_weights = self.DEFAULT_ENERGY_WEIGHTS.copy()
        self.energy_functions = self.DEFAULT_ENERGY_FUNCTIONS.copy()
        if config_path and os.path.exists(config_path):
            self._load_energy_factors(config_path)
        logger.info(f"Energy model initialized with {len(self.energy_functions)} function profiles.")
    
    def _load_energy_factors(self, config_path: str) -> None:
        """Load energy factors from a YAML configuration file."""
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            if config and 'energy_weights' in config:
                self.energy_weights.update(config['energy_weights'])
            if config and 'energy_functions' in config:
                self.energy_functions.update(config['energy_functions'])
                logger.debug(f"Loaded custom energy functions from {config_path}")
        except Exception as e:
            logger.error(f"Error loading energy factors from {config_path}: {e}")
    
        
    def get_energy_function(self, resource_type: str) -> Dict:
        """
        Return a per‐service override if resource_type contains “cr-aula-spring” or
        “spring_boot_terraform_cloud_run_service”; otherwise fall back to DEFAULT_ENERGY_FUNCTIONS.
        """
        # --- START: Revised Patch to handle specific resource names ---
        # This maps the short names passed by the orchestrator to the correct energy profile category.
        if resource_type == "nw1-vpc":
            logger.debug(f"Mapped resource '{resource_type}' to 'network' category via hotfix.")
            return self.energy_functions.get("network", self.energy_functions["default"])
        if resource_type in ["my-database1", "my-database2"]:
            logger.debug(f"Mapped resource '{resource_type}' to 'generic' category via hotfix.")
            return self.energy_functions.get("generic", self.energy_functions["default"])
        # --- END: Revised Patch ---

        # Original checks are preserved below as fallbacks.
        if resource_type in self.CUSTOM_ENERGY_PROFILES:
            return self.CUSTOM_ENERGY_PROFILES[resource_type]
        # ── 1) If the name contains “cr-aula-spring”, use the tiny 0.01–0.02 kW curve:
        if "cr-aula-spring" in resource_type:
            return {"idle": 0.010, "max": 0.020}

        if "spring_boot_terraform_cloud_run_service_ip_demo" in resource_type:
            return {"idle": 0.250, "max": 0.300}


        # ── 3) Otherwise, fall back to whatever DEFAULT_ENERGY_FUNCTIONS has for that type:
        if resource_type in self.energy_functions:
            return self.energy_functions[resource_type]

        main_type = resource_type.split("-")[0]
        if main_type in self.energy_functions:
            return self.energy_functions[main_type]

        logger.warning(f"Could not find a specific energy function for '{resource_type}'. Using default.")
        return self.energy_functions.get("default", {"idle": 0.0, "max": 0.0})

        
    def calculate_resource_energy(self, utilization: float, resource_type: str, capacity: float = 1.0) -> float:
        """Calculate instantaneous power consumption for a resource."""
        utilization = max(0.0, min(1.0, utilization))
        func_params = self.get_energy_function(resource_type)
        idle_kw = func_params.get('idle', 0.0)
        max_kw = func_params.get('max', idle_kw)
        
        # Power for a single instance
        power_per_instance_kw = idle_kw + (max_kw - idle_kw) * utilization
        
        # Total power for all instances
        return power_per_instance_kw * capacity
    def aggregate_energy_consumption(self,
                                    resource_data: Dict[str, Tuple[float, str, float, Optional[float]]]) -> Dict: # Added sim_duration_seconds
        """
        Aggregate energy consumption across all resources (Equation 3).
        
        Args:
            resource_data: Dictionary mapping resource IDs to tuples of
                             (utilization, resource_type, capacity, sim_duration_seconds)
        
        Returns:
            Dictionary with total and per-type energy consumption (kWh if duration provided, else kW)
        """
        total_energy_or_power = 0.0
        type_energy_or_power = {}
        main_type_energy_or_power = {}
        resource_energy_or_power_results = {}
        
        has_duration = False
        for _, _, _, sim_duration_seconds in resource_data.values():
            if sim_duration_seconds is not None and sim_duration_seconds > 0:
                has_duration = True
                break
        
        for resource_id, (util, res_type, capacity, sim_duration_seconds) in resource_data.items():
            # calculate_resource_energy returns power in kW
            power_kw = self.calculate_resource_energy(util, res_type, capacity)
            
            value_to_sum = power_kw
            if has_duration:
                # If any item has a duration, we calculate energy for all
                duration = sim_duration_seconds if sim_duration_seconds is not None and sim_duration_seconds > 0 else 0
                energy_kwh = power_kw * (duration / 3600.0) # Convert to kWh
                value_to_sum = energy_kwh
            
            resource_energy_or_power_results[resource_id] = value_to_sum
            total_energy_or_power += value_to_sum
            
            if res_type not in type_energy_or_power:
                type_energy_or_power[res_type] = 0.0
            type_energy_or_power[res_type] += value_to_sum
            
            main_type = res_type.split('-')[0] if '-' in res_type else res_type
            if main_type not in main_type_energy_or_power:
                main_type_energy_or_power[main_type] = 0.0
            main_type_energy_or_power[main_type] += value_to_sum
        
        # Determine if returning energy (kWh) or power (kW) based on sim_duration_seconds
        key_suffix = "_kwh" if has_duration else "_kw_power"

        results = {
            f'total_energy_consumption{key_suffix}': total_energy_or_power,
            f'energy_by_type{key_suffix}': type_energy_or_power,
            f'energy_by_category{key_suffix}': main_type_energy_or_power,
            f'resource_energy{key_suffix}': resource_energy_or_power_results
        }
        
        return results

    
    def calculate_total_energy(self, 
                              resources_utilization: Dict[str, Dict[str, Union[float, str]]],
                              sim_duration_seconds: Optional[float] = None # Add sim_duration_seconds
                              ) -> Dict:
        """
        Calculate total energy consumption from resource utilization data (Equation 4).
        
        Args:
            resources_utilization: Dictionary mapping resource IDs to their properties
                                 {resource_id: {'utilization': float, 'type': str, 'capacity': float}}
            sim_duration_seconds: Total simulation duration in seconds for energy calculation.
                                  If None, power (kW) will be returned instead of energy (kWh).
                                 
        Returns:
            Dictionary with energy consumption results
        """
        resource_energies_input = {} # Renamed to avoid confusion with self.resource_energies
        for resource_id, props in resources_utilization.items():
            utilization = props.get('utilization', 0.0)
            resource_type = props.get('type', 'default')
            capacity = props.get('capacity', 1.0)
            # Pass utilization, type, capacity, and duration to aggregate_energy_consumption
            resource_energies_input[resource_id] = (utilization, resource_type, capacity, sim_duration_seconds)
        
        return self.aggregate_energy_consumption(resource_energies_input)
    
    def calculate(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processes and returns the energy metrics already computed during the simulation run.
        This method avoids recalculating energy from averages, instead trusting the
        more accurate interval-based calculations from the orchestrator.
        """
        # The orchestrator already calculates detailed energy consumption and stores it.
        # This method's job is to ensure it's in the final "processed_metrics" format.
        resource_energy_consumption = results.get('resource_energy_consumption', {})
        resource_info = results.get('resource_info', {})
        
        if not resource_energy_consumption:
            logger.warning("EnergyModel.calculate: 'resource_energy_consumption' is empty in results. Returning zero metrics.")
            return {
                'total_kwh': 0.0,
                'by_resource_type': {},
                'by_resource_category': {},
                'resource_energy': {}
            }
            
        total_kwh = sum(stats.get('total_kwh', 0.0) for stats in resource_energy_consumption.values())
        
        # Aggregate by type and category using the final totals
        by_type = {}
        by_category = {}
        for res_id, stats in resource_energy_consumption.items():
            kwh = stats.get('total_kwh', 0.0)
            info = resource_info.get(res_id, {})
            res_type = info.get('type', 'default')
            category = res_type.split('-')[0]
            
            by_type[res_type] = by_type.get(res_type, 0.0) + kwh
            by_category[category] = by_category.get(category, 0.0) + kwh
            
        processed_energy_metrics = {
            'total_kwh': total_kwh,
            'by_resource_type': by_type,
            'by_resource_category': by_category,
            'resource_energy': {res_id: stats.get('total_kwh', 0.0) for res_id, stats in resource_energy_consumption.items()}
        }
        
        return processed_energy_metrics
    
    def get_efficiency_recommendations(self, 
                                     energy_results: Dict,
                                     threshold_percentage: float = 15.0) -> List[Dict]:
        """
        Generate recommendations to improve energy efficiency.
        
        Args:
            energy_results: Energy consumption results from calculate_total_energy
            threshold_percentage: Minimum percentage of total energy to consider for optimization
            
        Returns:
            List of recommendations
        """
        recommendations = []
        total_energy = energy_results.get('total_energy_consumption_kwh', energy_results.get('total_energy_consumption_kw_power', 0))
        if total_energy == 0:
            return []
        
        threshold = total_energy * (threshold_percentage / 100.0)
        
        resource_energy = energy_results.get('resource_energy_kwh', energy_results.get('resource_energy_kw_power', {}))

        # Find high-energy resources
        for resource_id, energy in resource_energy.items():
            if energy < threshold:
                continue
                
            # Get resource properties from the original data
            resource_type = "unknown"
            if hasattr(self, 'resource_mapping'): # Check if resource mapping exists
                if resource_id in self.resource_mapping:
                    resource_type = self.resource_mapping[resource_id].specific_type

            # Generate recommendation based on resource type
            recommendation = {
                'resource_id': resource_id,
                'energy_consumption': energy,
                'percentage_of_total': round((energy / total_energy) * 100, 2)
            }
            
            # Add specific recommendations based on resource type
            if 'compute' in resource_type or 'vm' in resource_type:
                recommendation['recommendations'] = [
                    "Consider rightsizing this compute resource",
                    "Evaluate using compute-optimized instance types",
                    "Check for idle periods and implement auto-shutdown policies"
                ]
            elif 'storage' in resource_type:
                recommendation['recommendations'] = [
                    "Review data lifecycle policies",
                    "Consider moving cold data to lower-energy storage tiers",
                    "Check for redundant data copies"
                ]
            elif 'network' in resource_type:
                recommendation['recommendations'] = [
                    "Optimize data transfer patterns",
                    "Evaluate CDN usage for static content",
                    "Check for unnecessary cross-region traffic"
                ]
            else:
                recommendation['recommendations'] = [
                    "Review resource utilization patterns",
                    "Consider consolidating services"
                ]
            
            recommendations.append(recommendation)
        
        # Sort recommendations by energy impact
        recommendations.sort(key=lambda x: x['energy_consumption'], reverse=True)
        
        return recommendations

def load_energy_model(config_path: Optional[str] = None) -> EnergyModel:
    """
    Factory function to create and initialize an energy model.
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        Initialized EnergyModel instance
    """
    return EnergyModel(config_path)
