"""Unit tests for the EnergyModel in the LEAF-Cloud framework.

This module contains tests for the EnergyModel class, which is responsible
for calculating energy consumption of cloud resources based on their utilization.
"""

import pytest
from unittest.mock import MagicMock, patch
from typing import Dict, Any, List

from leaf_cloud.models.energy import EnergyModel


class MockResource:
    """Mock resource class for testing."""
    
    def __init__(self, resource_type: str, capacity: float = 1.0):
        self.specific_type = resource_type
        self.capacity = capacity


class TestEnergyModel:
    """Test suite for the EnergyModel class."""
    
    @pytest.fixture
    def energy_config(self) -> Dict[str, Any]:
        """Create a sample energy model configuration for testing."""
        return {
            "weights": {
                "vm-standard": 1.0,
                "storage": 0.5,
                "default": 0.8
            },
            "functions": {
                "vm-standard": {"idle": 0.1, "max": 1.0},
                "storage": {"idle": 0.05, "max": 0.5},
                "default": {"idle": 0.01, "max": 0.1}
            },
            "profiles": {}
        }
    
    @pytest.fixture
    def energy_model(self, energy_config: Dict[str, Any]) -> EnergyModel:
        """Create an EnergyModel instance for testing."""
        # Create a mock config object
        config = MagicMock()
        config.weights = energy_config["weights"]
        config.functions = energy_config["functions"]
        config.profiles = energy_config["profiles"]
        
        return EnergyModel(config)
    
    def test_get_energy_weight(self, energy_model: EnergyModel) -> None:
        """Test getting energy weights for different resource types."""
        # Test with specific resource type
        assert energy_model.get_energy_weight("vm-standard") == 1.0
        
        # Test with non-existent type falls back to default
        assert energy_model.get_energy_weight("vm-custom") == 0.8  # Falls back to default
        
        # Test with default fallback
        assert energy_model.get_energy_weight("unknown-type") == 0.8
    
    def test_get_energy_function(self, energy_model: EnergyModel) -> None:
        """Test getting energy function parameters."""
        # Test with specific resource type
        vm_func = energy_model.get_energy_function("vm-standard")
        assert vm_func == {"idle": 0.1, "max": 1.0}
        
        # Test with category fallback
        storage_func = energy_model.get_energy_function("storage-ssd")
        assert storage_func == {"idle": 0.05, "max": 0.5}
        
        # Test with default fallback
        default_func = energy_model.get_energy_function("unknown-type")
        assert default_func == {"idle": 0.01, "max": 0.1}
    
    def test_calculate_resource_energy(self, energy_model: EnergyModel) -> None:
        """Test calculating energy consumption for a resource."""
        # Test idle state
        idle_energy = energy_model.calculate_resource_energy(0.0, "vm-standard")
        assert idle_energy == 0.1  # Should be equal to idle power
        
        # Test full utilization
        max_energy = energy_model.calculate_resource_energy(1.0, "vm-standard")
        assert max_energy == 1.0  # Should be equal to max power
        
        # Test 50% utilization
        half_energy = energy_model.calculate_resource_energy(0.5, "vm-standard")
        assert 0.1 < half_energy < 1.0  # Should be between idle and max
        assert abs(half_energy - 0.55) < 0.01  # 0.1 + 0.5 * (1.0 - 0.1)
        
        # Test with capacity > 1
        scaled_energy = energy_model.calculate_resource_energy(0.5, "vm-standard", 2.0)
        assert abs(scaled_energy - 1.1) < 0.01  # 2 * (0.1 + 0.5 * (1.0 - 0.1))
    
    def test_calculate(self, energy_model: EnergyModel) -> None:
        """Test the main calculate method with simulation results."""
        # Create mock resource mapping
        resource_mapping = {
            "vm1": MockResource("vm-standard", 2.0),
            "storage1": MockResource("storage-hdd", 1.0)
        }
        
        # Create mock simulation results
        simulation_results = {
            "raw_results": {
                "utilization": {
                    "vm1": [
                        {"time": 0, "value": 0.0},    # 0-1s: 0% utilization
                        {"time": 1, "value": 0.5},    # 1-2s: 50% utilization
                        {"time": 2, "value": 1.0}     # 2-3s: 100% utilization
                    ],
                    "storage1": [
                        {"time": 0, "value": 0.2},    # 0-3s: 20% utilization
                        {"time": 3, "value": 0.2}
                    ]
                }
            }
        }
        
        # Calculate energy consumption
        result = energy_model.calculate(simulation_results, resource_mapping)
        
        # Verify the results
        assert "total_kwh" in result
        assert "by_resource_type" in result
        assert "by_resource_category" in result
        assert "resource_energy" in result
        
        # Verify resource-specific energy consumption
        assert "vm1" in result["resource_energy"]
        assert "storage1" in result["resource_energy"]
        
        # Verify total energy is positive
        assert result["total_kwh"] > 0

    def test_calculate_resource_energy_with_coef_profile(self) -> None:
        """Ensure profiles using coef/exp respond to utilization."""
        config = MagicMock()
        config.weights = {"default": 1.0}
        config.functions = {
            "serverless": {"idle": 0.05, "coef": 0.25, "exp": 1.5},
            "default": {"idle": 0.02, "coef": 0.10, "exp": 1.0},
        }
        config.profiles = {}

        model = EnergyModel(config)

        profile = model.get_energy_function("serverless")
        assert profile["idle"] == 0.05
        assert pytest.approx(profile["max"], rel=1e-6) == 0.3  # idle + coef

        idle_power = model.calculate_resource_energy(0.0, "serverless")
        assert pytest.approx(idle_power, rel=1e-6) == 0.05

        full_power = model.calculate_resource_energy(1.0, "serverless")
        assert pytest.approx(full_power, rel=1e-6) == 0.3

        half_util_power = model.calculate_resource_energy(0.5, "serverless")
        expected = 0.05 + 0.25 * (0.5 ** 1.5)
        assert pytest.approx(half_util_power, rel=1e-6) == expected

    def test_k8s_workload_and_cluster_profiles_zero_out_energy(self) -> None:
        """Logical Kubernetes resources should not contribute direct energy."""
        config = MagicMock()
        config.weights = {"default": 1.0}
        config.functions = {"default": {"idle": 0.05, "max": 0.2}}
        config.profiles = {
            "k8s-workload": {"idle": 0.0, "max": 0.0},
            "gke-cluster": {"idle": 0.0, "max": 0.0},
        }

        model = EnergyModel(config)

        assert model.calculate_resource_energy(0.5, "k8s-workload", capacity=10.0) == 0.0
        assert model.calculate_resource_energy(0.7, "gke-cluster", capacity=5.0) == 0.0
    
    def test_get_efficiency_recommendations(self, energy_model: EnergyModel) -> None:
        """Test generating efficiency recommendations."""
        # Create test data
        energy_results = {
            "total_kwh": 100.0,
            "resource_energy": {
                "vm1": 60.0,    # 60% of total
                "storage1": 30.0, # 30% of total
                "net1": 10.0     # 10% of total (below threshold)
            }
        }
        
        # Mock resource mapping
        energy_model.resource_mapping = {
            "vm1": MockResource("vm-standard"),
            "storage1": MockResource("storage-ssd"),
            "net1": MockResource("network")
        }
        
        # Get recommendations with 15% threshold
        recommendations = energy_model.get_efficiency_recommendations(
            energy_results,  # type: ignore
            threshold_percentage=15.0
        )
        
        # Should only return recommendations for resources above threshold (vm1 and storage1)
        assert len(recommendations) == 2
        
        # Check that recommendations are sorted by energy consumption (descending)
        assert recommendations[0]["resource_id"] == "vm1"
        assert recommendations[0]["energy_consumption_kwh"] == 60.0
        assert abs(recommendations[0]["percentage_of_total"] - 60.0) < 0.01
        assert len(recommendations[0]["recommendations"]) > 0
        
        assert recommendations[1]["resource_id"] == "storage1"
        assert recommendations[1]["energy_consumption_kwh"] == 30.0
        assert abs(recommendations[1]["percentage_of_total"] - 30.0) < 0.01
        assert len(recommendations[1]["recommendations"]) > 0


if __name__ == "__main__":
    pytest.main(["-v", __file__])
