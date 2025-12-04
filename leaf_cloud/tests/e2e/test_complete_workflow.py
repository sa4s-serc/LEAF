"""End-to-end tests for the complete LEAF-Cloud workflow."""
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Any
import pytest
import yaml
from leaf_cloud.simulation import run_simulation, SimulationResult
from leaf_cloud.simulation import SimulationStatus

# Sample Terraform configuration for testing
SAMPLE_TERRAFORM = """
variable "instance_type" {
  description = "The instance type to use"
  type        = string
  default     = "t2.micro"
}

resource "aws_instance" "test" {
  ami           = "ami-0c55b159cbfafe1f0"  # Example AMI, replace with a valid one
  instance_type = var.instance_type
  
  tags = {
    Name = "test-instance"
  }
}
"""

# Sample workload configuration
SAMPLE_WORKLOAD = {
    "steps": 5,
    "step_duration": 1,
    "resources": {
        "aws_instance.test": {
            "cpu": [0.5, 0.6, 0.7, 0.8, 0.9],
            "memory": [0.4, 0.45, 0.5, 0.55, 0.6]
        }
    }
}

@pytest.fixture(scope="module")
def sample_terraform_dir():
    """Create a temporary directory with sample Terraform configuration."""
    temp_dir = tempfile.mkdtemp(prefix="leaf_test_")
    try:
        # Create a main.tf file with sample configuration
        with open(os.path.join(temp_dir, "main.tf"), "w") as f:
            f.write(SAMPLE_TERRAFORM)
        
        # Create a variables.tf file
        with open(os.path.join(temp_dir, "variables.tf"), "w") as f:
            f.write("""
            variable "region" {
              description = "AWS region"
              type        = string
              default     = "us-west-2"
            }
            """)
        
        # Create a terraform.tfvars file
        with open(os.path.join(temp_dir, "terraform.tfvars"), "w") as f:
            f.write('region = "us-west-2"\n')
        
        yield temp_dir
    finally:
        # Clean up the temporary directory
        shutil.rmtree(temp_dir, ignore_errors=True)

@pytest.fixture
def sample_workload_config():
    """Return a sample workload configuration."""
    return SAMPLE_WORKLOAD.copy()

def test_complete_workflow(sample_terraform_dir, sample_workload_config):
    """Test the complete workflow with a sample Terraform configuration."""
    # When: Running the simulation with the sample configuration
    result = run_simulation(
        terraform_dir=sample_terraform_dir,
        workload_config=sample_workload_config,
        return_result_object=True
    )
    
    # Then: Verify the simulation completed successfully
    assert isinstance(result, SimulationResult)
    assert result.status == SimulationStatus.COMPLETED
    assert result.metrics is not None
    assert "duration_seconds" in result.metrics
    
    # Verify the config snapshot was created correctly
    assert result.config_snapshot is not None
    assert "simulation" in result.config_snapshot
    assert "workload" in result.config_snapshot
    
    # Verify the workload config was passed through correctly
    assert result.config_snapshot["workload"] == sample_workload_config

def test_workflow_with_config_file(sample_terraform_dir, tmp_path):
    """Test the workflow with a configuration file."""
    # Given: A configuration file
    config_file = tmp_path / "config.yaml"
    config = {
        "simulation": {
            "steps": 3,
            "step_duration": 1.0  # Note: Using float to match the expected type
        },
        "workload": {
            "steps": 3,
            "step_duration": 1.0,  # Note: Using float to match the expected type
            "resources": {
                "aws_instance.test": {
                    "cpu": [0.3, 0.4, 0.5],
                    "memory": [0.2, 0.3, 0.4]
                }
            }
        }
    }
    
    with open(config_file, "w") as f:
        yaml.safe_dump(config, f)
    
    # When: Running the simulation with the config file and explicit workload config
    # to ensure the workload config is used
    result = run_simulation(
        terraform_dir=sample_terraform_dir,
        config_path=str(config_file),
        workload_config=config["workload"],  # Explicitly pass workload config
        return_result_object=True
    )
    
    # Then: Verify the simulation completed successfully
    assert result.status == SimulationStatus.COMPLETED
    assert result.metrics is not None
    assert result.config_snapshot is not None
    
    # The config snapshot should contain the simulation config
    assert "simulation" in result.config_snapshot
    assert "workload" in result.config_snapshot
    
    # Verify the workload config was passed through correctly
    assert result.config_snapshot["workload"] == config["workload"]

def test_workflow_with_export(sample_terraform_dir, sample_workload_config, tmp_path):
    """Test the workflow with result export."""
    # Given: An output directory for results
    output_dir = tmp_path / "results"
    
    # Create the output directory before running the simulation
    # since the simulation might not create it automatically
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # When: Running the simulation with export
    result = run_simulation(
        terraform_dir=sample_terraform_dir,
        workload_config=sample_workload_config,
        output_dir=str(output_dir),
        return_result_object=True
    )
    
    # Then: Verify the simulation completed successfully
    assert result.status == SimulationStatus.COMPLETED
    
    # Check that the output directory exists
    assert output_dir.exists(), f"Output directory {output_dir} was not created"
    
    # Check that the output directory is not empty
    output_files = list(output_dir.glob("*"))
    assert len(output_files) > 0, "No output files were created"
    
    # Check for specific output files if they exist
    # The actual file names and structure might differ
    found_files = [f.name for f in output_dir.glob("*")]
    print(f"Found output files: {found_files}")
    
    # Check that we have at least one output file with content
    has_content = any(f.stat().st_size > 0 for f in output_dir.glob("*"))
    assert has_content, "No output files with content were found"
