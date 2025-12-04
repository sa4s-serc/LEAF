"""Integration tests for end-to-end latency functionality."""

import pytest
import tempfile
import os
import yaml
import json
from pathlib import Path
from leaf_cloud.leaf import LEAFCloud
from leaf_cloud.config import LEAFCloudConfig


@pytest.fixture
def temp_config_file():
    """Create a temporary configuration file with end-to-end latency enabled."""
    config_data = {
        'simulation': {
            'duration': 10,
            'time_step': 1.0
        },
        'workload': {
            'type': 'steady',
            'params': {
                'rate': 10.0
            }
        },
        'models': {
            'latency': {
                'end_to_end_enabled': True,
                'user_distribution': {
                    'us-central1': 0.6,
                    'us-east1': 0.4
                },
                'user_to_infrastructure_latency': {
                    'us-central1_us-central1': 5.0,
                    'us-east1_us-east1': 5.0,
                    'us-central1_us-east1': 45.0,
                    'us-east1_us-central1': 45.0
                },
                'default_user_to_infra_latency': 50.0
            }
        },
        'output_dir': 'test_output'
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as temp_file:
        yaml.dump(config_data, temp_file)
        temp_file_path = temp_file.name
    
    yield temp_file_path
    
    # Clean up
    os.unlink(temp_file_path)


@pytest.fixture
def temp_terraform_dir():
    """Create a temporary directory with minimal Terraform files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a simple main.tf file
        main_tf = Path(temp_dir) / "main.tf"
        with open(main_tf, "w") as f:
            f.write("""
            resource "google_compute_instance" "test_vm" {
              name         = "test-vm"
              machine_type = "e2-medium"
              zone         = "us-central1-a"
            }
            """)
        
        yield temp_dir


def test_end_to_end_latency_simulation(temp_config_file, temp_terraform_dir):
    """Test running a simulation with end-to-end latency enabled."""
    # Initialize LEAF with the test configuration
    leaf = LEAFCloud(temp_config_file)
    
    # Load the Terraform configuration
    leaf.load_terraform(temp_terraform_dir)
    
    # Build the model
    leaf.build_model()
    
    # Configure a simple workload
    leaf.configure_workload({
        'type': 'steady',
        'params': {
            'rate': 10.0
        }
    })
    
    # Run the simulation with end-to-end latency enabled
    result = leaf.run_simulation(
        duration=5.0,
        end_to_end_latency=True
    )
    
    # Verify that the simulation completed successfully
    assert result is not None
    assert result.metadata is not None
    assert result.metadata.duration_seconds == 5.0
    
    # Check that resource metrics include latency data
    assert result.resource_metrics is not None
    for resource_id, metrics in result.resource_metrics.items():
        assert metrics.request_latency_ms is not None
        assert len(metrics.request_latency_ms) > 0
    
    # Check that token traces include user region information
    if result.token_traces:
        for trace in result.token_traces:
            if trace.event_type == "token_created" and "user_region" in trace.details:
                # Found at least one token with user region - test passes
                break
        else:
            # No tokens with user region found
            pytest.fail("No tokens with user_region found in token traces")


def test_backward_compatibility(temp_config_file, temp_terraform_dir):
    """Test that simulations work correctly when end-to-end latency is disabled."""
    # Load the config and modify it to disable end-to-end latency
    config = LEAFCloudConfig.from_file(temp_config_file)
    config.models.latency.end_to_end_enabled = False
    
    # Save the modified config
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as temp_file:
        yaml.dump(config.to_dict(), temp_file)
        modified_config_path = temp_file.name
    
    try:
        # Initialize LEAF with the modified configuration
        leaf = LEAFCloud(modified_config_path)
        
        # Load the Terraform configuration
        leaf.load_terraform(temp_terraform_dir)
        
        # Build the model
        leaf.build_model()
        
        # Configure a simple workload
        leaf.configure_workload({
            'type': 'steady',
            'params': {
                'rate': 10.0
            }
        })
        
        # Run the simulation with end-to-end latency disabled
        result = leaf.run_simulation(
            duration=5.0,
            end_to_end_latency=False
        )
        
        # Verify that the simulation completed successfully
        assert result is not None
        assert result.metadata is not None
        
        # Check that resource metrics include latency data (infrastructure-only)
        assert result.resource_metrics is not None
        for resource_id, metrics in result.resource_metrics.items():
            assert metrics.request_latency_ms is not None
            assert len(metrics.request_latency_ms) > 0
        
        # Check that token traces don't include user region information
        if result.token_traces:
            for trace in result.token_traces:
                if trace.event_type == "token_created":
                    assert "user_region" not in trace.details
    finally:
        # Clean up
        os.unlink(modified_config_path)


def test_results_format_with_end_to_end_latency(temp_config_file, temp_terraform_dir):
    """Test that simulation results include end-to-end latency breakdown."""
    # Initialize LEAF with the test configuration
    leaf = LEAFCloud(temp_config_file)
    
    # Load the Terraform configuration
    leaf.load_terraform(temp_terraform_dir)
    
    # Build the model
    leaf.build_model()
    
    # Configure a simple workload
    leaf.configure_workload({
        'type': 'steady',
        'params': {
            'rate': 10.0
        }
    })
    
    # Create a temporary output directory
    with tempfile.TemporaryDirectory() as output_dir:
        # Run the simulation with end-to-end latency enabled and save results
        result = leaf.run_simulation(
            duration=5.0,
            end_to_end_latency=True,
            output_dir=output_dir
        )
        
        # Check that results file was created
        results_file = Path(output_dir) / "raw_simulation_results.json"
        assert results_file.exists()
        
        # Load the results file and check for end-to-end latency data
        with open(results_file, 'r') as f:
            results_data = json.load(f)
        
        # Check for end-to-end latency information in the results
        if 'raw_model_outputs' in results_data:
            # The simulation should have run with end-to-end latency
            assert 'end_to_end_latency' in str(results_data)
        
        # Get metrics summary and check for end-to-end latency information
        summary = leaf.get_metrics_summary(result)
        assert 'latency' in summary.lower()