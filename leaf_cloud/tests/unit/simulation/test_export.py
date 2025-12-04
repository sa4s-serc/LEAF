"""Unit tests for the simulation export functionality."""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock

from leaf_cloud.simulation.export import (
    export_results,
    ExportFormat,
    _export_to_json,
    _export_to_yaml,
    _export_to_csv,
    _export_to_html
)


class TestExportFunctions:
    """Test cases for the export module's top-level functions."""
    
    def test_export_results_to_file(self, tmp_path):
        """Test exporting simulation results to a file."""
        # Create test data
        results = {"status": "completed", "data": [1, 2, 3]}
        output_file = tmp_path / "results.json"
        
        # Mock the underlying export function
        with patch('leaf_cloud.simulation.export._export_to_json') as mock_export:
            mock_export.return_value = json.dumps(results)
            
            # Export results
            export_results(results, "json", str(output_file))
            
            # Verify the file was created and contains the correct data
            assert output_file.exists()
            # Check that _export_to_json was called with the results
            mock_export.assert_called_once_with(results)
    
    def test_export_results_return_string(self):
        """Test exporting simulation results as a string."""
        # Create test data
        results = {"status": "completed", "data": [1, 2, 3]}
        
        # Mock the underlying export function
        with patch('leaf_cloud.simulation.export._export_to_json') as mock_export:
            mock_export.return_value = json.dumps(results)
            
            # Export results
            result = export_results(results, "json")
            
            # Verify the result is the JSON string
            assert result == json.dumps(results)
            # Check that _export_to_json was called with the results
            mock_export.assert_called_once_with(results)
    
    def test_export_results_invalid_format(self):
        """Test exporting with an invalid format raises an error."""
        with pytest.raises(ValueError, match="Unsupported export format"):
            export_results({}, "invalid_format")
    
    def test_export_to_json(self):
        """Test the JSON export function."""
        data = {"key": "value", "nested": {"a": 1, "b": 2}}
        result = _export_to_json(data, indent=2)
        
        # Parse back to verify it's valid JSON
        parsed = json.loads(result)
        assert parsed == data
    
    def test_export_to_yaml(self):
        """Test the YAML export function."""
        data = {"key": "value", "nested": {"a": 1, "b": 2}}
        
        # Mock yaml.dump to avoid actual YAML dependency in tests
        with patch('yaml.dump') as mock_dump:
            mock_dump.return_value = "yaml_content"
            result = _export_to_yaml(data, default_flow_style=False)
            
            assert result == "yaml_content"
            mock_dump.assert_called_once()
    
    @patch('leaf_cloud.simulation.export.csv.DictWriter')
    def test_export_to_csv(self, mock_dict_writer):
        """Test the CSV export function."""
        # Setup test data
        data = [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25}
        ]
        
        # Setup mock for the writer
        mock_writer = MagicMock()
        mock_dict_writer.return_value = mock_writer
        
        # Call the function
        result = _export_to_csv(data)
        
        # Verify the result is a string
        assert isinstance(result, str)
        
        # Verify DictWriter was called with correct fieldnames
        mock_dict_writer.assert_called_once()
        args, kwargs = mock_dict_writer.call_args
        assert set(kwargs.get('fieldnames', [])) == {'name', 'age'}
        
        # Verify writer methods were called
        mock_writer.writeheader.assert_called_once()
        mock_writer.writerows.assert_called_once_with(data)
    
    def test_export_to_csv_empty_data(self):
        """Test CSV export with empty data."""
        # Call the function with empty data
        result = _export_to_csv([])
        
        # Verify the result is an empty string
        assert result == ""
    
    @patch('builtins.open', new_callable=mock_open)
    def test_export_to_html(self, mock_file):
        """Test the HTML export function."""
        data = [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25}
        ]
        
        # Call the function with test data
        result = _export_to_html(data, "Test Title")
        
        # Basic checks for HTML structure
        assert "<html>" in result
        assert "<title>Test Title</title>" in result
        assert "<table>" in result
        
        # Check that all data is present in the output
        assert "Alice" in result
        assert "Bob" in result
        assert "30" in result
        assert "25" in result
    
    def test_export_format_enum(self):
        """Test the ExportFormat enum functionality."""
        # Test from_string with valid formats
        assert ExportFormat.from_string("json") == ExportFormat.JSON
        assert ExportFormat.from_string("YAML") == ExportFormat.YAML
        assert ExportFormat.from_string("CSV") == ExportFormat.CSV
        assert ExportFormat.from_string("html") == ExportFormat.HTML
        
        # Test from_string with invalid format
        with pytest.raises(ValueError, match="Unsupported export format"):
            ExportFormat.from_string("invalid_format")


# Removed SimulationExporter tests as the class is not in the current implementation
