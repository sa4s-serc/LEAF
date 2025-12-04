"""Unit tests for the simulation intermediate representation."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open, ANY

from leaf_cloud.simulation.intermediate import (
    export_intermediate_model,
    ModelExportError
)
from leaf_cloud.exceptions import LeafCloudError


class TestIntermediateModel:
    """Test cases for the intermediate model representation and export."""
    
    @pytest.fixture
    def mock_terraform_dir(self, tmp_path):
        """Create a mock Terraform directory with a basic configuration."""
        terraform_dir = tmp_path / "terraform"
        terraform_dir.mkdir()
        
        # Create a simple Terraform configuration
        (terraform_dir / "main.tf").write_text("""
        resource "aws_instance" "example" {
          ami           = "ami-123456"
          instance_type = "t2.micro"
        }
        """)
        
        return terraform_dir
    
    @patch('leaf_cloud.leaf.LEAFCloud')
    @patch('leaf_cloud.config.LEAFCloudConfig')
    @patch('leaf_cloud.simulation.intermediate._validate_terraform_dir')
    @patch('leaf_cloud.simulation.intermediate._validate_var_files')
    @patch('builtins.open', new_callable=mock_open)
    @patch('json.dump')
    @patch('leaf_cloud.simulation.intermediate.LEAFCloudConfig')  # Patch the imported LEAFCloudConfig
    def test_export_intermediate_model(
        self, 
        mock_imported_config_class,
        mock_json_dump,
        mock_file_open,
        mock_validate_var_files,
        mock_validate_terraform_dir,
        mock_config_class,
        mock_leaf_cloud_class,
        mock_terraform_dir,
        tmp_path
    ):
        # Configure the imported LEAFCloudConfig mock
        mock_imported_config = MagicMock()
        mock_imported_config.return_value = mock_imported_config
        mock_imported_config.load_from_file = MagicMock()
        mock_imported_config_class.return_value = mock_imported_config
        """Test exporting the intermediate model from a Terraform directory."""
        output_file = tmp_path / "model.json"
        
        # Create a mock config file with valid YAML content
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: '1.0'\nbase_dir: /tmp\n")
        
        # Setup mocks
        mock_leaf_cloud = MagicMock()
        mock_leaf_cloud_class.return_value = mock_leaf_cloud
        
        # Create a mock config with load_from_file method
        mock_config = MagicMock()
        mock_config.load_from_file = MagicMock()  # Mock the instance method
        mock_config_class.return_value = mock_config
        mock_config_class.from_file.return_value = mock_config  # Mock the class method
        
        # Mock validation functions
        mock_validate_var_files.return_value = []
        
        # Mock the model data that would be returned by _create_intermediate_model
        expected_model = {
            "version": "1.0",
            "terraform_dir": str(mock_terraform_dir),
            "resources": [
                {
                    "type": "aws_instance",
                    "name": "example",
                    "attributes": {
                        "ami": "ami-123456",
                        "instance_type": "t2.micro"
                    }
                }
            ],
            "modules": [],
            "variables": [],
            "outputs": [],
            "config": {},
            "metadata": {
                "generated_by": "LEAF-Cloud",
                "format_version": "1.0"
            }
        }
        
        # Mock the get_intermediate_model method
        mock_leaf_cloud.get_intermediate_model.return_value = expected_model
        
        # Setup the mock to return the config when from_file is called
        mock_config_class.from_file.return_value = mock_config
        
        # Call the function with output path
        result = export_intermediate_model(
            str(mock_terraform_dir),
            config_path=str(tmp_path / "config.yaml"),
            output_path=str(output_file)
        )
        
        # Verify the result
        assert result is None  # Should return None when output_path is provided
        
        # Verify the output file was written
        mock_file_open.assert_called_once_with(output_file, 'w', encoding='utf-8')
        mock_json_dump.assert_called_once_with(
            expected_model,
            ANY,  # file handle
            indent=2,
            default=str,
            ensure_ascii=False
        )
        
        # Verify LEAFCloud was initialized and used correctly
        mock_leaf_cloud_class.assert_called_once_with(config_path=str(tmp_path / "config.yaml"))
        mock_leaf_cloud.load_terraform.assert_called_once_with(
            str(mock_terraform_dir),
            var_files=None
        )
        mock_leaf_cloud.build_model.assert_called_once()
    
    @patch('leaf_cloud.leaf.LEAFCloud')
    @patch('leaf_cloud.config.LEAFCloudConfig')
    @patch('leaf_cloud.simulation.intermediate._validate_terraform_dir')
    @patch('leaf_cloud.simulation.intermediate._validate_var_files')
    def test_export_intermediate_model_return_dict(
        self, 
        mock_validate_var_files,
        mock_validate_terraform_dir,
        mock_config_class,
        mock_leaf_cloud_class,
        mock_terraform_dir
    ):
        """Test exporting the intermediate model and returning it as a dict."""
        # Setup mocks
        mock_leaf_cloud = MagicMock()
        mock_leaf_cloud_class.return_value = mock_leaf_cloud
        mock_config = MagicMock()
        mock_config_class.return_value = mock_config
        
        # Mock validation functions
        mock_validate_var_files.return_value = []
        
        # Mock the model data that would be returned by _create_intermediate_model
        expected_model = {
            "version": "1.0",
            "terraform_dir": str(mock_terraform_dir),
            "resources": [
                {
                    "type": "aws_instance",
                    "name": "example",
                    "attributes": {
                        "ami": "ami-123456",
                        "instance_type": "t2.micro"
                    }
                }
            ]
        }
        
        # Patch _create_intermediate_model to return our test data
        with patch('leaf_cloud.simulation.intermediate._create_intermediate_model', 
                  return_value=expected_model) as mock_create_model:
            
            # Export the model
            result = export_intermediate_model(
                str(mock_terraform_dir),
                output_path=str(output_file)
            )
            
            # Verify the file was created and contains the expected data
            assert output_file.exists()
            with open(output_file, 'r') as f:
                model_data = json.load(f)
            
            assert model_data == expected_model
            
            # Verify the function returns the path to the output file
            assert result == str(output_file)
            
            # Verify _create_intermediate_model was called with the correct arguments
            mock_create_model.assert_called_once()
            
            # Verify LEAFCloud was initialized correctly
            mock_leaf_cloud_class.assert_called_once()
    
    @patch('leaf_cloud.leaf.LEAFCloud')
    @patch('leaf_cloud.config.LEAFCloudConfig')
    def test_export_intermediate_model_return_dict(self, mock_config, mock_leaf_cloud_class, mock_terraform_dir):
        """Test exporting the intermediate model as a dictionary (no output_path)."""
        # Setup mocks
        mock_leaf_cloud = MagicMock()
        mock_leaf_cloud_class.return_value = mock_leaf_cloud
        
        # Mock the model data that would be returned by _create_intermediate_model
        expected_model = {"resources": [{"type": "test", "name": "example"}]}
        
        with patch('leaf_cloud.simulation.intermediate._create_intermediate_model', 
                  return_value=expected_model):
            
            # Export the model without output_path
            result = export_intermediate_model(str(mock_terraform_dir))
            
            # Verify the function returns the model as a dictionary
            assert result == expected_model
    
    def test_export_intermediate_model_invalid_dir(self):
        """Test exporting with a non-existent Terraform directory."""
        with pytest.raises(FileNotFoundError):
            export_intermediate_model("/path/that/does/not/exist")
    
    def test_export_intermediate_model_no_tf_files(self, tmp_path):
        """Test exporting with a directory that has no .tf files."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        
        with pytest.raises(FileNotFoundError, match="No Terraform configuration files"):
            export_intermediate_model(str(empty_dir))
    
    @patch('leaf_cloud.leaf.LEAFCloud')
    @patch('leaf_cloud.config.LEAFCloudConfig')
    @patch('leaf_cloud.simulation.intermediate.LEAFCloudConfig')  # Patch the imported LEAFCloudConfig
    def test_export_intermediate_model_export_error(
        self, 
        mock_imported_config_class,
        mock_config_class,
        mock_leaf_cloud_class, 
        mock_terraform_dir,
        tmp_path
    ):
        """Test handling of errors during model export."""
        # Configure the imported LEAFCloudConfig mock
        mock_imported_config = MagicMock()
        mock_imported_config.return_value = mock_imported_config
        mock_imported_config.load_from_file = MagicMock()
        mock_imported_config_class.return_value = mock_imported_config
        
        # Setup mocks
        mock_leaf_cloud = MagicMock()
        mock_leaf_cloud_class.return_value = mock_leaf_cloud
        
        # Create a mock config with load_from_file method
        mock_config = MagicMock()
        mock_config.load_from_file = MagicMock()
        mock_config_class.return_value = mock_config
        
        # Mock _create_intermediate_model to raise an exception
        with patch('leaf_cloud.simulation.intermediate._create_intermediate_model', 
                  side_effect=Exception("Test error")):
            
            with pytest.raises(ModelExportError, match="Failed to export intermediate model"):
                export_intermediate_model(str(mock_terraform_dir))
