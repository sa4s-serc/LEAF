"""
Tests for the Terraform model builder module.

This module contains unit tests for the ModelBuilder class that converts
Terraform configurations into LEAF-Cloud models.
"""

import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open, ANY

# Import the modules to test
from leaf_cloud.terraform.model_builder import ModelBuilder, NetworkTopologyBuilder
from leaf_cloud.core.petri_net import PetriNet, Place, Transition, Arc

# Import CloudRun from the correct location
from leaf_cloud.gcp.compute import CloudRun

# Import config
from leaf_cloud.config import LEAFCloudConfig

# Sample test data
SAMPLE_TERRAFORM_DIR = "/path/to/terraform"
SAMPLE_CONFIG = LEAFCloudConfig(
    simulation={
        "workload_params": {
            "rate": 100.0
        }
    },
    env_config_path="env.yaml"
)

@pytest.fixture
def mock_config():
    """Fixture for creating a mock LEAFCloudConfig."""
    return LEAFCloudConfig(
        simulation={
            "workload_params": {
                "rate": 100.0
            }
        },
        env_config_path="env.yaml"
    )

class TestModelBuilder:
    """Test cases for the ModelBuilder class."""

    def test_init_with_valid_parameters(self, mock_config):
        """Test initialization with valid parameters."""
        with patch('os.path.exists', return_value=True):
            builder = ModelBuilder(SAMPLE_TERRAFORM_DIR, mock_config)
            
        assert builder.terraform_path == SAMPLE_TERRAFORM_DIR
        assert builder.config == mock_config
        assert builder.workload_rate == 100.0
        assert isinstance(builder.petri_net, PetriNet)
        assert builder.petri_net.name == "LeafCloudModel"
        assert isinstance(builder.tf_resources, dict)
        assert isinstance(builder.resource_mapping, dict)
        assert len(builder.resource_pools) > 0  # Should have default resource pools

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data='key: value\nregion: us-central1\nproject: test-project\nzone: us-central1-a')
    def test_load_env_config(self, mock_file, mock_exists, mock_config):
        """Test loading environment configuration."""
        # First call is for the initial config load in __init__
        # Second call is for our test
        mock_exists.side_effect = [True, True, False]
        
        # Create a side effect that returns different content based on the file
        def mock_file_side_effect(*args, **kwargs):
            if args[0] == 'env.yaml':
                return mock_open(read_data='region: us-central1\nproject: test-project\nzone: us-central1-a')(*args, **kwargs)
            elif args[0] == 'test_env.yaml':
                return mock_open(read_data='key: value')(*args, **kwargs)
            return mock_open()(*args, **kwargs)
            
        mock_file.side_effect = mock_file_side_effect
        
        builder = ModelBuilder(SAMPLE_TERRAFORM_DIR, mock_config)
        
        # Test loading existing config
        config = builder._load_env_config("test_env.yaml")
        assert config == {"key": "value"}
        
        # Test loading non-existent config (should return default config)
        config = builder._load_env_config("nonexistent.yaml")
        assert config == {
            'region': 'us-central1',
            'project': 'default-project',
            'zone': 'us-central1-a'
        }

    @patch('os.path.exists', return_value=True)
    @patch('leaf_cloud.terraform.model_builder.CloudRun')
    def test_map_resource(self, mock_cloudrun, mock_exists, mock_config):
        """Test mapping of Terraform resources to LEAF-Cloud resources."""
        # Setup
        builder = ModelBuilder(SAMPLE_TERRAFORM_DIR, mock_config)
        
        # Create a mock CloudRun instance
        mock_instance = MagicMock()
        mock_instance.name = "test_service"
        mock_instance.region = "us-central1"
        mock_instance.cpu_limit = 1.0
        mock_instance.memory_limit_mb = 256
        mock_instance.max_instances = 10
        mock_instance.min_instances = 1
        mock_instance.concurrency = 80
        
        # Configure the mock to return our instance
        mock_cloudrun.return_value = mock_instance
        
        # Test data for a Cloud Run service
        res_type = "google_cloud_run_service"
        res_name = "test_service"
        res_config = {
            "location": ["us-central1"],
            "template": [{
                "spec": [{
                    "containers": [{
                        "resources": [{
                            "limits": {
                                "cpu": "1",
                                "memory": "256Mi"
                            }
                        }]
                    }],
                    "container_concurrency": 80
                }],
                "metadata": [{
                    "annotations": {
                        "run.googleapis.com/max-instances": "10",
                        "run.googleapis.com/min-instances": "1"
                    }
                }]
            }]
        }
        
        # Call the method under test
        result = builder._map_resource(res_type, res_name, res_config)
        
        # Verify the result is our mock instance
        assert result is mock_instance
        
        # Verify CloudRun was instantiated with the correct parameters
        mock_cloudrun.assert_called_once_with(
            name=res_name,
            region="us-central1",
            cpu_limit=1.0,
            memory_limit_mb=256,
            max_instances=10,
            min_instances=1,
            concurrency=80
        )
        
        # Verify the resource was NOT added to resource_mapping (that happens in _process_terraform_resources)
        resource_key = f"{res_type}.{res_name}"
        assert resource_key not in builder.resource_mapping

class TestNetworkTopologyBuilder:
    """Test cases for the NetworkTopologyBuilder class."""

    @pytest.fixture
    def petri_net(self):
        """Fixture for creating a Petri net for testing."""
        return PetriNet("TestNet")
    
    @pytest.fixture
    def builder(self, petri_net):
        """Fixture for creating a NetworkTopologyBuilder."""
        return NetworkTopologyBuilder(petri_net)

    def test_init(self, builder, petri_net):
        """Test initialization of NetworkTopologyBuilder."""
        assert builder.petri_net == petri_net
        assert hasattr(builder, 'logger')

    def test_ensure_place(self, builder, petri_net):
        """Test ensuring a place exists in the Petri net."""
        # Test creating a new place
        builder.ensure_place("test_place", "Test Place", capacity=10)
        
        # Verify place was created with correct properties
        assert "test_place" in petri_net.places
        place = petri_net.places["test_place"]
        assert place.name == "Test Place"
        assert place.capacity == 10
        
        # Test ensuring the same place again (should be idempotent)
        builder.ensure_place("test_place", "Updated Name", capacity=20)
        assert petri_net.places["test_place"] is place  # Same object
        assert place.name == "Test Place"  # Name shouldn't change
        assert place.capacity == 10  # Capacity shouldn't change

    def test_ensure_transition(self, builder, petri_net):
        """Test ensuring a transition exists in the Petri net."""
        # Setup mock for add_transition
        petri_net.add_transition = MagicMock()
        
        # Mock action function
        def test_action(tokens):
            return tokens
            
        # Test creating a new transition
        builder.ensure_transition(
            "test_transition", 
            "Test Transition", 
            1.0, 
            test_action
        )
        
        # Verify add_transition was called once
        petri_net.add_transition.assert_called_once()
        
        # Get the transition that was added
        transition_args = petri_net.add_transition.call_args[0][0]
        assert isinstance(transition_args, Transition)
        assert transition_args.id == "test_transition"
        assert transition_args.name == "Test Transition"
        assert transition_args.delay == 1.0
        
        # Reset the mock for the next test
        petri_net.add_transition.reset_mock()
        
        # Test ensuring the same transition again (should be idempotent)
        # First, make the transition exist in the petri_net
        petri_net.transitions = {"test_transition": transition_args}
        
        builder.ensure_transition(
            "test_transition", 
            "Updated Transition", 
            2.0, 
            lambda x: x
        )
        
        # Should not call add_transition since the transition already exists
        petri_net.add_transition.assert_not_called()

    def test_ensure_arc(self, builder, petri_net):
        """Test ensuring an arc exists in the Petri net."""
        # Setup mocks
        petri_net.add_arc = MagicMock()
        
        # Test creating an input arc
        builder.ensure_arc("test_arc", "place1", "trans1", "input")
        
        # Verify add_arc was called with correct parameters
        petri_net.add_arc.assert_called_once()
        
        # Get the Arc object passed to add_arc
        arc = petri_net.add_arc.call_args[0][0]
        assert arc.id == "test_arc"
        assert arc.place_id == "place1"
        assert arc.transition_id == "trans1"
        assert arc.direction == "input"
        
        # Reset mock for next test
        petri_net.add_arc.reset_mock()
        
        # Test creating an output arc
        builder.ensure_arc("test_arc2", "place2", "trans2", "output")
        
        # Verify add_arc was called with correct parameters
        petri_net.add_arc.assert_called_once()
        
        # Get the Arc object passed to add_arc
        arc = petri_net.add_arc.call_args[0][0]
        assert arc.id == "test_arc2"
        assert arc.place_id == "place2"
        assert arc.transition_id == "trans2"
        assert arc.direction == "output"
        
        # Test invalid direction
        with pytest.raises(ValueError, match="Arc direction must be 'input' or 'output'"):
            builder.ensure_arc("invalid_arc", "place1", "trans1", "invalid")

# Add more test cases as needed
