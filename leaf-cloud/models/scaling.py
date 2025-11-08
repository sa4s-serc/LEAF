import math
import logging
from typing import Dict, List, Optional, Tuple, Any

"""
Dynamic Scaling Model for LEAF-Cloud Framework

This module implements the dynamic scaling model for GKE pods in the LEAF-Cloud framework.
It provides functionality to calculate required pod counts based on request rates,
resource utilization, and geographic factors.

Implements equation 7 from the LEAF-Cloud mathematical foundation:
- Equation 7: Dynamic pod scaling based on request rates, regional factors, and utilization
"""

# Configure logging
logger = logging.getLogger(__name__)
class ScalingModel:
    """
    Dynamic scaling model for Kubernetes pods.
    """
    DEFAULT_REQUEST_CAPACITY = 200
    DEFAULT_CPU_THRESHOLD = 0.8
    DEFAULT_MEMORY_THRESHOLD = 0.8
    DEFAULT_MIN_PODS = 1
    DEFAULT_MAX_PODS = 100
    DEFAULT_SCALING_BUFFER = 0.2
    DEFAULT_COOLDOWN_PERIOD = 300
    REGION_LATENCY_FACTORS = {
        "us-central1": 1.0, "us-east1": 1.02, "us-west1": 1.03,
        "europe-west1": 1.1, "asia-east1": 1.2, "australia-southeast1": 1.25
    }
    REGION_CAPACITY_FACTORS = {
        "us-central1": 1.0, "us-east1": 1.0, "us-west1": 0.98,
        "europe-west1": 0.95, "asia-east1": 0.92, "australia-southeast1": 0.9
    }
    
    def __init__(
        self,
        config_path: Optional[str] = None,
        request_capacity: float = DEFAULT_REQUEST_CAPACITY,
        cpu_threshold: float = DEFAULT_CPU_THRESHOLD,
        memory_threshold: float = DEFAULT_MEMORY_THRESHOLD,
        min_pods: int = DEFAULT_MIN_PODS,
        max_pods: int = DEFAULT_MAX_PODS,
        scaling_buffer: float = DEFAULT_SCALING_BUFFER,
        cooldown_period: int = DEFAULT_COOLDOWN_PERIOD,
        base_region: str = "us-central1"
    ):
        """Initialize the scaling model, loading parameters from a config file if provided."""
        self.request_capacity = request_capacity
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.min_pods = min_pods
        self.max_pods = max_pods
        self.scaling_buffer = scaling_buffer
        self.cooldown_period = cooldown_period
        self.base_region = base_region
        self.last_scale_time = 0
        self.current_pods = min_pods

        if config_path and os.path.exists(config_path):
            self._load_scaling_params(config_path)

        if self.request_capacity is None:
            logger.warning("request_capacity is None after init, setting to default.")
            self.request_capacity = ScalingModel.DEFAULT_REQUEST_CAPACITY
        if self.min_pods < 1: self.min_pods = 1
        if self.max_pods < self.min_pods: self.max_pods = self.min_pods
            
        logger.info(f"Scaling model initialized with request capacity {self.request_capacity} and base region {self.base_region}")

    def _load_scaling_params(self, config_path: str) -> None:
        """Load scaling parameters from a YAML configuration file."""
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            if config and 'scaling' in config:
                scaling_config = config['scaling']
                self.request_capacity = float(scaling_config.get('Rpod', self.request_capacity))
                self.cpu_threshold = float(scaling_config.get('cpu_threshold', self.cpu_threshold))
                self.memory_threshold = float(scaling_config.get('memory_threshold', self.memory_threshold))
                self.min_pods = int(scaling_config.get('min_pods', self.min_pods))
                self.max_pods = int(scaling_config.get('max_pods', self.max_pods))
                self.scaling_buffer = float(scaling_config.get('scaling_buffer', self.scaling_buffer))
                self.cooldown_period = int(scaling_config.get('cooldown_period', self.cooldown_period))
                logger.debug(f"Loaded scaling parameters from {config_path}")
            else:
                logger.warning(f"No 'scaling' section found in {config_path}")
        except Exception as e:
            logger.error(f"Error loading scaling parameters from {config_path}: {e}")
    
    def calculate_required_pods(self, request_rate: float, region: str = None, cpu_utilization: float = None, memory_utilization: float = None, current_time: float = None) -> int:
        """Calculate the required number of pods based on current request rate and resource utilization."""
        region = region or self.base_region
        capacity_factor = self.REGION_CAPACITY_FACTORS.get(region, 1.0)
        
        if self.request_capacity is None or self.request_capacity == 0:
             logger.error(f"Invalid request_capacity: {self.request_capacity}. Using default 10.")
             effective_capacity = 10 * capacity_factor
        else:
            effective_capacity = self.request_capacity * capacity_factor

        if effective_capacity == 0:
            logger.error("Effective capacity is zero, cannot scale. Returning max_pods to avoid division by zero.")
            return self.max_pods

        request_based_pods = math.ceil(request_rate / effective_capacity)
        
        utilization_based_pods = self.current_pods
        if cpu_utilization is not None and cpu_utilization > 0:
            cpu_based_pods = math.ceil(self.current_pods * cpu_utilization / self.cpu_threshold)
            utilization_based_pods = max(utilization_based_pods, cpu_based_pods)
        if memory_utilization is not None and memory_utilization > 0:
            memory_based_pods = math.ceil(self.current_pods * memory_utilization / self.memory_threshold)
            utilization_based_pods = max(utilization_based_pods, memory_based_pods)
        
        required_pods = max(request_based_pods, utilization_based_pods)
        required_pods = max(self.min_pods, min(self.max_pods, required_pods))
        
        if current_time is not None and (current_time - self.last_scale_time) < self.cooldown_period:
            return self.current_pods
        
        if required_pods != self.current_pods and current_time is not None:
            self.last_scale_time = current_time
            
        self.current_pods = required_pods
        return required_pods
    
    def predict_scaling_behavior(
        self, 
        request_rates: List[float], 
        region: str = None,
        initial_pods: int = None,
        time_step: float = 60
    ) -> List[int]:
        """
        Predict scaling behavior over time for a sequence of request rates.
        
        Args:
            request_rates: List of request rates over time
            region: GCP region for scaling calculations
            initial_pods: Starting pod count (defaults to min_pods)
            time_step: Time between request rate samples (seconds)
            
        Returns:
            List of predicted pod counts corresponding to each request rate
        """
        if initial_pods is not None:
            self.current_pods = max(self.min_pods, min(self.max_pods, initial_pods))
        else:
            self.current_pods = self.min_pods
        
        self.last_scale_time = 0
        current_time = 0.0
        pod_counts = []
        
        for rate in request_rates:
            current_time += time_step
            pods = self.calculate_required_pods(rate, region=region, current_time=current_time)
            pod_counts.append(pods)
            
        return pod_counts
    
    def estimate_cost_and_efficiency(
        self,
        request_rates: List[float],
        region: str = None,
        pod_cost_per_hour: float = 0.05,  # Example cost in USD
        time_step: float = 60  # seconds
    ) -> Dict[str, float]:
        """
        Estimate cost and efficiency metrics for the given request rates.
        
        Args:
            request_rates: List of request rates over time
            region: GCP region for calculations
            pod_cost_per_hour: Cost per pod per hour in USD
            time_step: Time between request rate samples in seconds
            
        Returns:
            Dictionary with cost and efficiency metrics
        """
        pod_counts = self.predict_scaling_behavior(request_rates, region, time_step=time_step)
        
        # Calculate total pod-hours
        hours_per_step = time_step / 3600  # Convert seconds to hours
        total_pod_hours = sum(pod_counts) * hours_per_step
        
        # Calculate cost
        total_cost = total_pod_hours * pod_cost_per_hour
        
        # Calculate efficiency metrics
        total_capacity = sum([p * self.request_capacity for p in pod_counts]) * hours_per_step
        total_requests = sum(request_rates) * hours_per_step
        utilization = total_requests / total_capacity if total_capacity > 0 else 0
        
        # Calculate average pods and max pods
        avg_pods = sum(pod_counts) / len(pod_counts) if pod_counts else 0
        max_pods_used = max(pod_counts) if pod_counts else 0
        
        return {
            "total_cost": total_cost,
            "total_pod_hours": total_pod_hours,
            "average_utilization": utilization,
            "average_pods": avg_pods,
            "max_pods_used": max_pods_used
        }
    
    def get_geo_adjustment_factor(self, region: str) -> float:
        """
        Calculate the combined geographic adjustment factor for a region.
        
        Args:
            region: GCP region name
            
        Returns:
            Combined adjustment factor (latency * capacity)
        """
        latency_factor = self.REGION_LATENCY_FACTORS.get(region, 1.0)
        capacity_factor = self.REGION_CAPACITY_FACTORS.get(region, 1.0)
        return latency_factor * capacity_factor
    
    def compare_regions(self, request_rate: float) -> Dict[str, Tuple[int, float]]:
        """
        Compare pod requirements across different regions for the same request rate.
        
        Args:
            request_rate: The request rate to analyze
            
        Returns:
            Dictionary mapping region names to tuples of (pod_count, adjustment_factor)
        """
        results = {}
        
        for region in self.REGION_LATENCY_FACTORS.keys():
            pods = self.calculate_required_pods(request_rate, region=region)
            factor = self.get_geo_adjustment_factor(region)
            results[region] = (pods, factor)
            
        return results
    
    def calculate(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate basic scaling metrics: average utilization across resources,
        peak concurrency as max_pods, and transition fire count as scaling_events.
        """
        # Average utilization across all resources
        util_dict = results.get('resource_utilization', {})
        if util_dict:
            avg_utils = [stats.get('avg', 0.0) for stats in util_dict.values()]
            average_utilization = sum(avg_utils) / len(avg_utils)
        else:
            average_utilization = 0.0
        # Peak concurrency (max tokens in any place)
        peak = 0
        token_flow = results.get('token_flow', {}).get('place_token_counts', {})
        for counts in token_flow.values():
            for entry in counts:
                peak = max(peak, entry.get('count', 0))
        max_pods = peak
        # Scaling events count transitions fired
        events = results.get('token_flow', {}).get('token_flow_log', [])
        scaling_events = sum(1 for e in events if e.get('event') == 'transition_fired')
        return {
            'average_utilization': average_utilization,
            'max_pods': max_pods,
            'scaling_events': scaling_events
        }
def load_scaling_model(config_path: Optional[str] = None) -> ScalingModel:
    """Factory function to create and initialize a scaling model."""
    return ScalingModel(config_path=config_path)