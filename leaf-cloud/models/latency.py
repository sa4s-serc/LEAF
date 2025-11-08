import os
import yaml
import numpy as np
import logging
from typing import Dict, List, Optional, Tuple, Union

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
    
    # Default base latency values for different resource types (in milliseconds)
    DEFAULT_BASE_LATENCY = {
        'compute': {
            'vm-standard': 5.0,       # Standard VM processing time
            'vm-highcpu': 3.0,        # High CPU VMs are faster
            'vm-highmem': 4.0,        # High Memory VMs
            'gke-node': 8.0,          # GKE nodes 
            'cloud-function': 100.0,  # Cold start penalty
            'cloud-run': 80.0,        # Cloud Run cold start
            'default': 10.0           # Default compute latency
        },
        'storage': {
            'persistent-disk': 5.0,    # Persistent disks
            'cloud-storage': 30.0,     # Cloud Storage access
            'cloud-sql': 15.0,         # Cloud SQL queries
            'bigtable': 8.0,           # Bigtable access
            'firestore': 20.0,         # Firestore operations
            'default': 20.0            # Default storage latency
        },
        'network': {
            'load-balancer': 10.0,     # Load balancer processing
            'vpn': 50.0,               # VPN connections
            'cdn': 5.0,                # CDN edge latency
            'interconnect': 15.0,      # Dedicated interconnect
            'default': 20.0            # Default network latency
        },
        'security': {
            'kms': 25.0,               # Key management operations
            'armor': 15.0,             # Cloud Armor processing
            'iam': 10.0,               # IAM checks
            'default': 15.0            # Default security latency
        },
        'default': 20.0                # Default latency for unknown types
    }
    
    # Default congestion parameters for resource categories
    DEFAULT_CONGESTION_PARAMS = {
        'compute': {'threshold': 0.75, 'factor': 2.5, 'exp': 2.0},  # Significant at high util
        'storage': {'threshold': 0.80, 'factor': 2.0, 'exp': 1.5},  # IOPS based congestion
        'network': {'threshold': 0.60, 'factor': 3.0, 'exp': 2.5},  # Network saturates earlier
        'security': {'threshold': 0.85, 'factor': 1.5, 'exp': 1.2}, # Less affected by congestion
        'default': {'threshold': 0.70, 'factor': 2.0, 'exp': 2.0}   # Default parameters
    }
    
    # Regional network latency factors (multiplier for cross-region requests)
    DEFAULT_REGION_LATENCY_FACTORS = {
        # Format: ('source_region', 'destination_region'): factor
        ('us-central1', 'us-east1'): 1.2,
        ('us-central1', 'us-west1'): 1.3,
        ('us-central1', 'europe-west1'): 2.5,
        ('us-central1', 'asia-east1'): 3.0,
        # Default case for same region
        'same-region': 1.0,
        # Default case for different regions
        'different-region': 2.0
    }
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the latency model with resource-specific latency values.
        
        Args:
            config_path: Path to configuration file containing latency factors
        """
        self.base_latency = self.DEFAULT_BASE_LATENCY.copy()
        self.congestion_params = self.DEFAULT_CONGESTION_PARAMS.copy()
        self.region_latency_factors = self.DEFAULT_REGION_LATENCY_FACTORS.copy()
        
        # Load latency factors from configuration if available
        if config_path and os.path.exists(config_path):
            self._load_latency_factors(config_path)
            
        logger.info(f"Latency model initialized with factors for multiple resource types")
    
    def _load_latency_factors(self, config_path: str) -> None:
        """
        Load latency factors and parameters from configuration file.
        
        Args:
            config_path: Path to YAML configuration file
        """
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                
            if 'base_latency' in config:
                # Update default latency values with config values
                for category, values in config['base_latency'].items():
                    if category in self.base_latency and isinstance(self.base_latency[category], dict):
                        self.base_latency[category].update(values)
                    else:
                        self.base_latency[category] = values
                logger.debug(f"Loaded base latency values from {config_path}")
                
            if 'congestion_params' in config:
                # Update congestion parameters with config values
                self.congestion_params.update(config['congestion_params'])
                logger.debug(f"Loaded congestion parameters from {config_path}")
                
            if 'region_latency_factors' in config:
                # Update region latency factors with config values
                self.region_latency_factors.update(config['region_latency_factors'])
                logger.debug(f"Loaded region latency factors from {config_path}")
                
        except Exception as e:
            logger.error(f"Error loading latency factors: {e}")
    
    def get_base_latency(self, resource_type: str) -> float:
        """
        Get the base latency for a specific resource type.
        
        Args:
            resource_type: Type of cloud resource (e.g., 'vm-standard', 'cloud-storage')
            
        Returns:
            Base latency value in milliseconds
        """
        # Extract the main category from resource type
        main_type = resource_type.split('-')[0] if '-' in resource_type else resource_type
        
        # Check if resource type exists in its category
        if main_type in self.base_latency:
            if isinstance(self.base_latency[main_type], dict):
                # Get specific resource type latency or default for category
                return self.base_latency[main_type].get(
                    resource_type, 
                    self.base_latency[main_type].get('default', self.base_latency['default'])
                )
            return self.base_latency[main_type]
        
        # Return default latency if resource type not found
        return self.base_latency['default']
    
    def get_congestion_params(self, resource_type: str) -> Dict:
        """
        Get the congestion parameters for a specific resource type.
        
        Args:
            resource_type: Type of cloud resource
            
        Returns:
            Dictionary of congestion parameters
        """
        # Get main resource category (compute, storage, etc.)
        main_type = resource_type.split('-')[0] if '-' in resource_type else resource_type
        
        # Return congestion parameters for the main type or default
        return self.congestion_params.get(main_type, self.congestion_params['default'])
    
    def get_region_latency_factor(self, source_region: str, dest_region: str) -> float:
        """
        Get the latency factor for traffic between regions.
        
        Args:
            source_region: Source GCP region
            dest_region: Destination GCP region
            
        Returns:
            Latency multiplier factor
        """
        # If source and destination are the same, use same-region factor
        if source_region == dest_region:
            return self.region_latency_factors.get('same-region', 1.0)
        
        # Try to get specific region pair factor
        region_pair = (source_region, dest_region)
        reverse_pair = (dest_region, source_region)
        
        return self.region_latency_factors.get(
            region_pair,
            self.region_latency_factors.get(
                reverse_pair,
                self.region_latency_factors.get('different-region', 2.0)
            )
        )
    
    def calculate_transition_delay(self, 
                                  resource_type: str, 
                                  utilization: float = 0.0,
                                  source_region: Optional[str] = None,
                                  dest_region: Optional[str] = None) -> float:
        """
        Calculate delay for a single transition (δ(t) in equation 1).
        
        Formula: δ(t) = BaseLatency * RegionFactor * CongestionFactor
        
        Args:
            resource_type: Type of resource being used
            utilization: Current resource utilization (0.0 to 1.0)
            source_region: Source GCP region (optional)
            dest_region: Destination GCP region (optional)
            
        Returns:
            Transition delay in milliseconds
        """
        # Get base latency for resource type
        base_latency = self.get_base_latency(resource_type)
        
        # Apply region factor if regions are specified
        region_factor = 1.0
        if source_region and dest_region:
            region_factor = self.get_region_latency_factor(source_region, dest_region)
        
        # Calculate congestion effect
        congestion_factor = self.calculate_congestion_factor(resource_type, utilization)
        
        # Total delay is base latency multiplied by both factors
        delay = base_latency * region_factor * congestion_factor
        
        return delay
    
    def calculate_congestion_factor(self, resource_type: str, utilization: float) -> float:
        """
        Calculate congestion factor based on resource utilization (Δcongestion).
        
        Formula: CongestionFactor = 1.0 if utilization < threshold
                 CongestionFactor = 1.0 + factor * ((utilization - threshold) / (1 - threshold))^exp
                 
        Args:
            resource_type: Type of resource
            utilization: Current resource utilization (0.0 to 1.0)
            
        Returns:
            Congestion multiplier (1.0 means no congestion)
        """
        # Clamp utilization to [0,1] range
        utilization = max(0.0, min(1.0, utilization))
        
        # Get congestion parameters for this resource type
        params = self.get_congestion_params(resource_type)
        threshold = params['threshold']
        factor = params['factor']
        exp = params['exp']
        
        # If utilization is below threshold, no congestion effect
        if utilization <= threshold:
            return 1.0
        
        # Calculate congestion effect using exponential model
        # This creates a steep curve as utilization approaches 100%
        normalized_util = (utilization - threshold) / (1.0 - threshold)
        congestion_factor = 1.0 + factor * (normalized_util ** exp)
        
        return congestion_factor
    
    def calculate_path_latency(self, path: List[Dict]) -> Dict:
        """
        Calculate total latency along a path (equation 1).
        
        Formula: L = Sum(δ(t)) for all transitions t in path
        
        Args:
            path: List of dictionaries with transition information
                 [{'resource_type': str, 'utilization': float, 
                   'source_region': str, 'dest_region': str}, ...]
                
        Returns:
            Dictionary with total and per-transition latency information
        """
        total_latency = 0.0
        transition_latencies = []
        critical_transitions = []
        
        # Calculate latency for each transition in the path
        for i, transition in enumerate(path):
            resource_type = transition.get('resource_type', 'default')
            utilization = transition.get('utilization', 0.0)
            source_region = transition.get('source_region')
            dest_region = transition.get('dest_region')
            
            # Calculate this transition's delay
            delay = self.calculate_transition_delay(
                resource_type, utilization, source_region, dest_region
            )
            
            # Add to total
            total_latency += delay
            
            # Store the individual transition latency
            transition_latencies.append({
                'index': i,
                'resource_type': resource_type,
                'latency': delay,
                'utilization': utilization,
                'congestion_factor': self.calculate_congestion_factor(resource_type, utilization)
            })
            
            # Identify potential critical transitions (top 20% by latency)
            if delay > (total_latency / len(path)) * 1.5:
                critical_transitions.append({
                    'index': i,
                    'resource_type': resource_type,
                    'latency': delay,
                    'percentage': 0.0  # Will update after calculating total
                })
        
        # Update percentage of total for critical transitions
        for transition in critical_transitions:
            transition['percentage'] = (transition['latency'] / total_latency) * 100
        
        # Sort critical transitions by latency
        critical_transitions.sort(key=lambda x: x['latency'], reverse=True)
        
        # Prepare results
        results = {
            'total_latency': total_latency,
            'transition_latencies': transition_latencies,
            'critical_transitions': critical_transitions,
            'avg_transition_latency': total_latency / len(path) if path else 0.0,
            'max_transition_latency': max([t['latency'] for t in transition_latencies]) if transition_latencies else 0.0
        }
        
        return results
    
    def analyze_critical_path(self, paths: List[List[Dict]]) -> Dict:
        """
        Analyze multiple paths to identify the critical path (highest latency).
        
        Args:
            paths: List of paths, each a list of transition dictionaries
            
        Returns:
            Dictionary with critical path analysis
        """
        path_results = []
        
        # Calculate latency for each path
        for i, path in enumerate(paths):
            path_latency = self.calculate_path_latency(path)
            path_results.append({
                'path_id': i,
                'total_latency': path_latency['total_latency'],
                'transitions': len(path),
                'details': path_latency
            })
        
        # Sort paths by total latency
        path_results.sort(key=lambda x: x['total_latency'], reverse=True)
        
        # Critical path is the one with highest latency
        critical_path = path_results[0] if path_results else None
        
        return {
            'all_paths': path_results,
            'critical_path': critical_path,
            'critical_path_latency': critical_path['total_latency'] if critical_path else 0.0,
            'path_count': len(paths)
        }
    
    def calculate(self, results: Dict[str, any]) -> Dict:
        """
        Calculate actual latency metrics based on token creation and completion times.
        Returns average, p95, p99 and raw latencies.
        """
        latencies = []
        create_times = {}
        log = results.get('token_flow', {}).get('token_flow_log', [])
        for event in log:
            if event.get('event') == 'token_creation':
                create_times[event['token_id']] = event['time']
            elif event.get('event') == 'token_completed':
                tid = event['token_id']
                if tid in create_times:
                    lat = event['time'] - create_times[tid]
                    latencies.append(lat)
        if not latencies:
            return {'latencies': [], 'average': 0.0, 'percentile_95': 0.0, 'percentile_99': 0.0}
        average = sum(latencies) / len(latencies)
        p95 = float(np.percentile(latencies, 95))
        p99 = float(np.percentile(latencies, 99))
        return {'latencies': latencies, 'average': average, 'percentile_95': p95, 'percentile_99': p99}
    
    def get_latency_recommendations(self, 
                                   path_analysis: Dict,
                                   threshold_percentage: float = 20.0) -> List[Dict]:
        """
        Generate recommendations to reduce latency based on path analysis.
        
        Args:
            path_analysis: Result from analyze_critical_path
            threshold_percentage: Consider transitions that make up this percentage of path latency
            
        Returns:
            List of latency optimization recommendations
        """
        recommendations = []
        
        if not path_analysis or 'critical_path' not in path_analysis:
            return recommendations
        
        # Get the critical path details
        critical_path = path_analysis['critical_path']
        critical_transitions = critical_path['details']['critical_transitions']
        
        # Generate recommendations for the critical transitions
        for transition in critical_transitions:
            if transition['percentage'] < threshold_percentage:
                continue
                
            recommendation = {
                'transition_index': transition['index'],
                'resource_type': transition['resource_type'],
                'latency': transition['latency'],
                'percentage_of_path': round(transition['percentage'], 2)
            }
            
            # Add specific recommendations based on resource type
            resource_type = transition['resource_type']
            main_type = resource_type.split('-')[0] if '-' in resource_type else resource_type
            
            if main_type == 'compute':
                recommendation['recommendations'] = [
                    "Upgrade to a higher performance VM instance",
                    "Optimize application code to reduce processing time",
                    "Consider using a compute-optimized instance type"
                ]
            elif main_type == 'storage':
                recommendation['recommendations'] = [
                    "Use caching mechanisms to reduce storage access",
                    "Consider database query optimization",
                    "Evaluate using SSD-based storage for better performance"
                ]
            elif main_type == 'network':
                recommendation['recommendations'] = [
                    "Evaluate moving resources to the same region",
                    "Use CDN for content delivery to reduce latency",
                    "Consider premium networking tier for critical traffic"
                ]
            elif main_type == 'security':
                recommendation['recommendations'] = [
                    "Optimize security rule configurations",
                    "Implement caching for frequently used security tokens",
                    "Review IAM permission checking logic"
                ]
            else:
                recommendation['recommendations'] = [
                    "Review resource configuration for performance bottlenecks",
                    "Consider upgrading resource to a higher performance tier"
                ]
                
            recommendations.append(recommendation)
        
        # Sort recommendations by impact (highest latency first)
        recommendations.sort(key=lambda x: x['latency'], reverse=True)
        
        return recommendations

def load_latency_model(config_path: Optional[str] = None) -> LatencyModel:
    """
    Factory function to create and initialize a latency model.
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        Initialized LatencyModel instance
    """
    return LatencyModel(config_path)