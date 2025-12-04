"""Unit tests for the ScalingModel class."""

import unittest
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, List, Any, Tuple, Union
from dataclasses import dataclass

# Import the ScalingModel class and related types
from leaf_cloud.models.scaling import ScalingModel, ScalingAnalysis, ScalingRecommendation, InstanceHistory

# Test configuration
TEST_CONFIG = {
    "min_pods": 1,
    "max_pods": 10,
    "cpu_threshold": 0.7,
    "memory_threshold": 0.8,
    "cooldown_period": 300,
}

@dataclass
class MockConfig:
    """Mock configuration class for testing."""
    min_pods: int
    max_pods: int
    cpu_threshold: float
    memory_threshold: float
    cooldown_period: int

class TestScalingModel(unittest.TestCase):
    """Test cases for the ScalingModel class."""
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a mock config object
        self.config = MockConfig(**TEST_CONFIG)
        
        # Initialize the model
        self.model = ScalingModel(self.config)
        
        # Sample resource mapping for testing
        self.resource_mapping = {
            "gke-cluster-1": {"type": "gke_cluster", "region": "us-central1"},
            "instance-group-1": {"type": "instance_group", "region": "us-east1"},
            "non-scaling-resource": {"type": "other", "region": "us-west1"},
        }
        
        # Sample simulation results for testing
        self.sample_events = [
            {
                "group_id": "gke-cluster-1",
                "time": 100.0,
                "new_state": {"instance_count": 2, "status": "RUNNING"},
            },
            {
                "group_id": "gke-cluster-1",
                "time": 200.0,
                "new_state": {"instance_count": 3, "status": "RUNNING"},
            },
            {
                "group_id": "gke-cluster-1",
                "time": 300.0,
                "new_state": {"instance_count": 4, "status": "RUNNING"},
            },
            {
                "group_id": "instance-group-1",
                "time": 150.0,
                "new_state": {"instance_count": 5, "status": "RUNNING"},
            },
        ]
    
    def test_initialization(self):
        """Test that the model initializes correctly with valid config."""
        self.assertEqual(self.model.name, "scaling")
        self.assertEqual(self.model.config.min_pods, 1)
        self.assertEqual(self.model.config.max_pods, 10)
        self.assertEqual(self.model.config.cpu_threshold, 0.7)
        self.assertEqual(self.model.config.memory_threshold, 0.8)
    
    def test_validate_config_valid(self):
        """Test config validation with valid values."""
        # Should not raise any exceptions
        self.model._validate_config(self.config)
    
    def test_validate_config_invalid(self):
        """Test config validation with invalid values."""
        # Test max_pods < min_pods
        invalid_config = MockConfig(min_pods=5, max_pods=4, cpu_threshold=0.5, memory_threshold=0.6, cooldown_period=300)
        with self.assertRaises(ValueError):
            self.model._validate_config(invalid_config)
        
        # Test invalid CPU threshold
        invalid_config = MockConfig(min_pods=1, max_pods=10, cpu_threshold=1.5, memory_threshold=0.6, cooldown_period=300)
        with self.assertRaises(ValueError):
            self.model._validate_config(invalid_config)
        
        # Test invalid memory threshold
        invalid_config = MockConfig(min_pods=1, max_pods=10, cpu_threshold=0.5, memory_threshold=0, cooldown_period=300)
        with self.assertRaises(ValueError):
            self.model._validate_config(invalid_config)
    
    def test_identify_autoscaling_groups(self):
        """Test identification of autoscaling groups."""
        self.model.resource_mapping = self.resource_mapping
        groups = self.model._identify_autoscaling_groups()
        
        # Should only return resources with 'gke' or 'instance_group' in the ID
        self.assertIn("gke-cluster-1", groups)
        self.assertIn("instance-group-1", groups)
        self.assertNotIn("non-scaling-resource", groups)
    
    def test_get_instance_history(self):
        """Test extraction of instance history from events."""
        # Filter events for a specific group
        group_events = [e for e in self.sample_events if e["group_id"] == "gke-cluster-1"]
        history = self.model._get_instance_history(group_events)
        
        # Should return list of (timestamp, instance_count) tuples
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0], (100.0, 2))
        self.assertEqual(history[1], (200.0, 3))
        self.assertEqual(history[2], (300.0, 4))
        
        # Test with empty events
        self.assertEqual(self.model._get_instance_history([]), [])
    
    def test_count_scaling_events(self):
        """Test counting of scaling events from instance history."""
        # No changes
        self.assertEqual(self.model._count_scaling_events([(100, 2), (200, 2), (300, 2)]), 0)
        
        # One change
        self.assertEqual(self.model._count_scaling_events([(100, 2), (200, 2), (300, 3)]), 1)
        
        # Multiple changes
        self.assertEqual(self.model._count_scaling_events([(100, 2), (200, 3), (300, 3), (400, 1)]), 2)
        
        # Not enough data points
        self.assertEqual(self.model._count_scaling_events([(100, 2)]), 0)
        self.assertEqual(self.model._count_scaling_events([]), 0)
    
    def test_calculate(self):
        """Test the main calculate method with simulation results."""
        # Create a mock simulation results object
        mock_results = MagicMock()
        mock_results.resource_stats = self.sample_events
        
        # Run the calculation
        analysis = self.model.calculate(mock_results, self.resource_mapping)
        
        # Should return analysis for both autoscaling groups
        self.assertIn("gke-cluster-1", analysis)
        self.assertIn("instance-group-1", analysis)
        
        # Check the analysis for gke-cluster-1
        gke_analysis = analysis["gke-cluster-1"]
        self.assertEqual(gke_analysis["scaling_events"], 2)  # 2 changes: 2->3, 3->4
        self.assertEqual(len(gke_analysis["instance_history"]), 3)
        self.assertAlmostEqual(gke_analysis["average_instances"], 3.0)  # (2+3+4)/3
        self.assertEqual(gke_analysis["peak_instances"], 4)
        self.assertEqual(gke_analysis["min_instances"], 2)
        
        # Check the analysis for instance-group-1
        ig_analysis = analysis["instance-group-1"]
        self.assertEqual(ig_analysis["scaling_events"], 0)  # Only one data point
        self.assertEqual(len(ig_analysis["instance_history"]), 1)
        self.assertEqual(ig_analysis["average_instances"], 5.0)
        self.assertEqual(ig_analysis["peak_instances"], 5)
        self.assertEqual(ig_analysis["min_instances"], 5)
    
    def test_get_scaling_recommendations(self):
        """Test generation of scaling recommendations."""
        # Create a test analysis
        analysis = {
            "gke-cluster-1": {
                "scaling_events": 25,  # High number of scaling events
                "instance_history": [(100, 10), (200, 10), (300, 10)],  # At max capacity
                "average_instances": 10.0,  # At max capacity
                "peak_instances": 10,  # At max capacity
                "min_instances": 10,  # At max capacity
            },
            "instance-group-1": {
                "scaling_events": 5,
                "instance_history": [(100, 1), (200, 1), (300, 1)],  # At min capacity
                "average_instances": 1.0,  # At min capacity
                "peak_instances": 1,
                "min_instances": 1,
            },
        }
        
        # Get recommendations
        recommendations = self.model.get_scaling_recommendations(analysis)
        
        # Should have recommendations for both groups
        self.assertEqual(len(recommendations), 3)  # max_instances, min_instances, and thrashing
        
        # Check recommendation types
        rec_types = {r["type"] for r in recommendations}
        self.assertIn("max_instances", rec_types)
        self.assertIn("min_instances", rec_types)
        self.assertIn("thrashing", rec_types)
        
        # Check the max_instances recommendation
        max_rec = next(r for r in recommendations if r["type"] == "max_instances")
        self.assertEqual(max_rec["group_id"], "gke-cluster-1")
        self.assertIn("increasing max_pods", max_rec["suggestion"])
        
        # Check the min_instances recommendation
        min_rec = next(r for r in recommendations if r["type"] == "min_instances")
        self.assertEqual(min_rec["group_id"], "instance-group-1")
        self.assertIn("lowering min_pods", min_rec["suggestion"])
        
        # Check the thrashing recommendation
        thrashing_rec = next(r for r in recommendations if r["type"] == "thrashing")
        self.assertEqual(thrashing_rec["group_id"], "gke-cluster-1")
        self.assertIn("cooldown period", thrashing_rec["suggestion"])
    
    @patch('logging.Logger.warning')
    def test_deprecated_methods(self, mock_warning):
        """Test that deprecated methods return expected values and log warnings."""
        # Test each deprecated method
        self.assertEqual(self.model.calculate_required_pods(), 0)
        self.assertEqual(self.model.predict_scaling_behavior(), [])
        self.assertEqual(self.model.estimate_cost_and_efficiency(), {})
        self.assertEqual(self.model.get_geo_adjustment_factor(), 1.0)
        self.assertEqual(self.model.compare_regions(), {})
        
        # Get all warning messages that were logged
        warning_messages = [args[0] for args, _ in mock_warning.call_args_list]
        
        # Debug output to see what logs were captured
        print(f"Captured warning messages: {warning_messages}")
        
        # Check that warnings were logged for each deprecated method
        self.assertGreater(len(warning_messages), 0, "No warning messages were logged")
        
        # Verify each method logged a deprecation warning
        expected_methods = [
            'calculate_required_pods',
            'predict_scaling_behavior',
            'estimate_cost_and_efficiency',
            'get_geo_adjustment_factor',
            'compare_regions'
        ]
        
        # Get all log messages as a single string for easier searching
        all_messages = "\n".join(warning_messages)
        
        # Check that we have at least one warning per method
        for method in expected_methods:
            self.assertIn(method, all_messages, 
                         f"No deprecation warning found for {method}")
    
    def test_load_scaling_model(self):
        """Test the factory function for creating a ScalingModel."""
        model = ScalingModel.load_scaling_model(self.config)
        self.assertIsInstance(model, ScalingModel)
        self.assertEqual(model.config.min_pods, self.config.min_pods)
        self.assertEqual(model.config.max_pods, self.config.max_pods)

if __name__ == "__main__":
    unittest.main()
