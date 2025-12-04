"""Unit tests for the CarbonModel class."""

import unittest
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, List, Any, Tuple, Optional, Union
from dataclasses import dataclass

# Import the CarbonModel class and related types
from leaf_cloud.models.carbon import (
    CarbonModel,
    CarbonEmissionsResult,
    OptimizationRecommendation,
    load_carbon_model,
    ResourceID,
    Region,
    EnergyKwh,
    CarbonEmissions,
    CarbonFactor,
)

# Test configuration
@dataclass
class MockCarbonConfig:
    """Mock configuration class for testing CarbonModel."""
    regions: Dict[Region, CarbonFactor]
    default_factor: CarbonFactor

# Sample test data
TEST_REGIONS = {
    "us-central1": 0.3,  # kg CO2e/kWh
    "europe-west1": 0.2,
    "asia-southeast1": 0.4,
}
DEFAULT_FACTOR: CarbonFactor = 0.5  # kg CO2e/kWh

# Sample resource mapping for testing
RESOURCE_MAPPING = {
    "vm-1": {"region": "us-central1", "type": "compute"},
    "vm-2": {"region": "europe-west1", "type": "compute"},
    "storage-1": {"region": "us-central1", "type": "storage"},
    "db-1": {"region": "asia-southeast1", "type": "database"},
}

# Sample energy data for testing
SAMPLE_ENERGY_DATA = {
    "energy_metrics": {
        "resource_energy": {
            "vm-1": 10.5,  # kWh
            "vm-2": 8.2,
            "storage-1": 2.1,
            "db-1": 5.7,
        }
    }
}

class TestCarbonModel(unittest.TestCase):
    """Test cases for the CarbonModel class."""
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a mock config object
        self.config = MockCarbonConfig(
            regions=TEST_REGIONS.copy(),
            default_factor=DEFAULT_FACTOR,
        )
        
        # Initialize the model
        self.model = CarbonModel(self.config)
        
        # Set up resource mapping
        self.model.resource_mapping = RESOURCE_MAPPING.copy()
    
    def test_initialization(self):
        """Test that the model initializes correctly."""
        self.assertEqual(self.model.name, "carbon")
        self.assertEqual(self.model.carbon_factors, TEST_REGIONS)
        self.assertEqual(self.model.default_factor, DEFAULT_FACTOR)
        self.assertEqual(self.model.resource_mapping, RESOURCE_MAPPING)
    
    def test_get_carbon_factor(self):
        """Test getting carbon factors for different regions."""
        # Test existing regions
        self.assertAlmostEqual(self.model.get_carbon_factor("us-central1"), 0.3)
        self.assertAlmostEqual(self.model.get_carbon_factor("europe-west1"), 0.2)
        
        # Test non-existent region (should return default)
        self.assertEqual(self.model.get_carbon_factor("unknown-region"), DEFAULT_FACTOR)
    
    def test_calculate_resource_carbon(self):
        """Test carbon calculation for a single resource."""
        # Test with valid input
        emissions = self.model.calculate_resource_carbon(10.0, "us-central1")
        self.assertAlmostEqual(emissions, 3.0)  # 10 * 0.3 = 3.0
        
        # Test with zero energy
        self.assertEqual(self.model.calculate_resource_carbon(0, "us-central1"), 0.0)
        
        # Test with unknown region (should use default factor)
        emissions = self.model.calculate_resource_carbon(10.0, "unknown-region")
        self.assertEqual(emissions, 5.0)  # 10 * 0.5 = 5.0
    
    def test_aggregate_carbon_footprint(self):
        """Test aggregation of carbon footprint across resources."""
        # Prepare test data: resource_id -> (energy_kwh, region)
        resource_emissions = {
            "vm-1": (10.0, "us-central1"),  # 10 * 0.3 = 3.0
            "vm-2": (20.0, "europe-west1"),  # 20 * 0.2 = 4.0
            "storage-1": (5.0, "us-central1"),  # 5 * 0.3 = 1.5
            "db-1": (10.0, "unknown-region"),  # 10 * 0.5 = 5.0
        }
        
        expected_total = 3.0 + 4.0 + 1.5 + 5.0
        expected_regional = {
            "us-central1": 3.0 + 1.5,  # vm-1 + storage-1
            "europe-west1": 4.0,  # vm-2
            "unknown-region": 5.0,  # db-1
        }
        
        # Calculate and verify results
        result = self.model.aggregate_carbon_footprint(resource_emissions)
        
        self.assertIsInstance(result, dict)
        self.assertAlmostEqual(result["total_carbon_emissions"], expected_total)
        self.assertEqual(set(result["regional_emissions"].keys()), set(expected_regional.keys()))
        
        for region, expected_emissions in expected_regional.items():
            self.assertAlmostEqual(result["regional_emissions"][region], expected_emissions)
        
        # Check individual resource emissions
        self.assertAlmostEqual(result["resource_emissions"]["vm-1"], 3.0)
        self.assertAlmostEqual(result["resource_emissions"]["vm-2"], 4.0)
        self.assertAlmostEqual(result["resource_emissions"]["storage-1"], 1.5)
        self.assertAlmostEqual(result["resource_emissions"]["db-1"], 5.0)
    
    def test_calculate_with_energy_data(self):
        """Test the main calculate method with energy data."""
        # Calculate with direct resource mapping
        result = self.model.calculate(SAMPLE_ENERGY_DATA, RESOURCE_MAPPING)
        
        # Verify the structure of the result
        self.assertIsInstance(result, dict)
        self.assertIn("total_carbon_emissions", result)
        self.assertIn("regional_emissions", result)
        self.assertIn("resource_emissions", result)
        
        # Should have emissions for all resources
        self.assertEqual(len(result["resource_emissions"]), 4)
        
        # Verify some calculations
        self.assertAlmostEqual(
            result["resource_emissions"]["vm-1"], 
            10.5 * TEST_REGIONS["us-central1"]
        )
        self.assertAlmostEqual(
            result["resource_emissions"]["vm-2"],
            8.2 * TEST_REGIONS["europe-west1"]
        )
    
    def test_calculate_with_invalid_energy_data(self):
        """Test calculate method with invalid or missing energy data."""
        # Test with empty energy data - should return empty results
        with self.assertNoLogs(level='WARNING'):
            empty_result = self.model.calculate({}, RESOURCE_MAPPING)
            self.assertEqual(empty_result["total_carbon_emissions"], 0.0)
            self.assertEqual(empty_result["regional_emissions"], {})
            self.assertEqual(empty_result["resource_emissions"], {})
        
        # Test with None values - these should be filtered out without warnings
        with self.assertNoLogs(level='WARNING'):
            result = self.model.calculate({"resource_energy": {"vm-1": None}}, RESOURCE_MAPPING)
            self.assertNotIn("vm-1", result["resource_emissions"])
        
        # Test with invalid energy value - should be filtered out without warnings
        with self.assertNoLogs(level='WARNING'):
            result = self.model.calculate({"resource_energy": {"vm-1": "invalid"}}, RESOURCE_MAPPING)
            self.assertNotIn("vm-1", result["resource_emissions"])
    
    def test_get_optimization_recommendations(self):
        """Test generation of optimization recommendations."""
        # First calculate some emissions
        result = self.model.calculate(SAMPLE_ENERGY_DATA, RESOURCE_MAPPING)
        
        # Get recommendations
        recommendations = self.model.get_optimization_recommendations(result)
        
        # Should get recommendations for resources in higher-carbon regions
        self.assertGreater(len(recommendations), 0)
        
        # Check structure of recommendations
        for rec in recommendations:
            self.assertIn("resource_id", rec)
            self.assertIn("current_region", rec)
            self.assertIn("current_carbon_factor", rec)
            self.assertIn("emissions_kg_co2e", rec)
            self.assertIn("recommended_regions", rec)
            self.assertIn("potential_savings_percent", rec)
            
            # Current region should be in our test regions
            self.assertIn(rec["current_region"], ["us-central1", "europe-west1", "asia-southeast1"])
            
            # Should recommend lower-carbon regions
            if rec["current_region"] == "asia-southeast1":
                # Should recommend both us-central1 and europe-west1 as better
                self.assertIn("europe-west1", rec["recommended_regions"])
                self.assertIn("us-central1", rec["recommended_regions"])
    
    def test_load_carbon_model(self):
        """Test the load_carbon_model factory function."""
        # Create a model using the factory function
        model = load_carbon_model(self.config)
        
        # Verify it's a CarbonModel instance with correct config
        self.assertIsInstance(model, CarbonModel)
        self.assertEqual(model.carbon_factors, TEST_REGIONS)
        self.assertEqual(model.default_factor, DEFAULT_FACTOR)


if __name__ == "__main__":
    unittest.main()
