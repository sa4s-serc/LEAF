"""Unit tests for the results module."""

import json
import yaml
import pytest
import csv
from datetime import datetime, timezone
from unittest.mock import patch
from typing import Any, Dict, List, Optional

from leaf_cloud.utils.results import (
    SimulationResult,
    ResourceMetrics,
    export_results,
    load_results,
)

from leaf_cloud.leaf_types import SimulationStatus


def create_valid_test_data(
    simulation_id: str = "test_sim_123",
    metrics: Optional[Dict[str, Any]] = None,
    token_flow_logs: Optional[List[Dict[str, Any]]] = None,
    resource_metrics: Optional[Dict[str, Any]] = None,
    **overrides: Any
) -> Dict[str, Any]:
    """Create valid test data for SimulationResult.from_dict().

    Args:
        simulation_id: Simulation identifier
        metrics: Optional simulation metrics
        token_flow_logs: Optional token flow logs
        resource_metrics: Optional resource metrics
        **overrides: Override any field in the test data

    Returns:
        Dictionary with test data formatted for SimulationResult.from_dict()
    """
    # Default timestamps
    start_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    end_time = datetime(2023, 1, 1, 12, 1, 0, tzinfo=timezone.utc)
    
    # Default metrics
    if metrics is None:
        metrics = {"duration_seconds": 60.0, "tokens_processed": 1000}
    
    # Default token flow logs
    if token_flow_logs is None:
        token_flow_logs = [
            {
                "timestamp": 1.0,
                "event_type": "token_created",
                "details": {"token_id": "t1", "resource_id": "res1"},
            },
            {
                "timestamp": 2.0,
                "event_type": "token_processed",
                "details": {"token_id": "t1", "resource_id": "res1"},
            },
        ]
    
    # Default resource metrics
    if resource_metrics is None:
        resource_metrics = {
            "res1": {
                "resource_id": "res1",
                "resource_type": "test_resource",
                "region": "test_region",
                "metrics": {"avg_utilization": 0.5},
                "timestamps": [1.0, 2.0, 3.0],
                "values": [0.1, 0.2, 0.3],
                "utilization": [
                    {"timestamp": 1.0, "value": 0.1},
                    {"timestamp": 2.0, "value": 0.2},
                    {"timestamp": 3.0, "value": 0.3},
                ],
            }
        }
    
    # Base test data structure
    test_data = {
        "metadata": {
            "simulation_id": simulation_id,
            "start_time": start_time.timestamp(),
            "end_time": end_time.timestamp(),
            "duration_seconds": 60.0,
            "timestamp": start_time.timestamp(),
            "version": "1.0.0",
            "leaf_cloud_version": "1.0.0",
            "config_hash": "test_config_hash",
            "status": "COMPLETED",
        },
        "config_snapshot": {
            "simulation": {
                "duration_seconds": 60.0,
                "random_seed": 42,
            },
            "resources": [
                {
                    "id": "res1",
                    "type": "test_resource",
                    "region": "test_region",
                }
            ],
        },
        "resource_metrics": resource_metrics,
        "token_flow_logs": token_flow_logs,
        "system_events": [],
        "raw_model_outputs": {
            "test_model": {
                "test_output": 42,
            }
        },
        "schema_version": "1.0.0",
        "status": "COMPLETED",
        "metrics": metrics,
    }
    
    # Apply any overrides
    test_data.update(overrides)
    return test_data


def create_resource_metrics_data(
    resource_id: str = "res1",
    resource_type: str = "test_resource",
    region: str = "test_region",
    timestamps: Optional[List[float]] = None,
    values: Optional[List[float]] = None,
    additional_metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create resource metrics data for testing.
    
    Args:
        resource_id: Resource identifier
        resource_type: Type of resource
        region: Resource region
        timestamps: List of timestamps
        values: List of values
        additional_metrics: Additional metrics to include
        
    Returns:
        Dictionary with resource metrics data
    """
    if timestamps is None:
        timestamps = [1.0, 2.0, 3.0]
    if values is None:
        values = [0.1, 0.2, 0.3]
    if additional_metrics is None:
        additional_metrics = {}
    
    return {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "region": region,
        "metrics": {"avg_utilization": sum(values) / len(values), **additional_metrics},
        "timestamps": timestamps,
        "values": values,
        "utilization": [
            {"timestamp": t, "value": v} for t, v in zip(timestamps, values)
        ],
    }


class TestResourceMetrics:
    """Test cases for the ResourceMetrics class."""

    def test_init_with_basic_params(self):
        """Test ResourceMetrics initialization with basic parameters."""
        metrics = ResourceMetrics(
            resource_id="test_res_1",
            metrics={"cpu_usage": 0.5, "memory_usage": 0.3},
            timestamps=[1.0, 2.0, 3.0],
            values=[0.4, 0.5, 0.6],
        )
        
        assert metrics.resource_id == "test_res_1"
        assert metrics.metrics == {"cpu_usage": 0.5, "memory_usage": 0.3}
        assert metrics.timestamps == [1.0, 2.0, 3.0]
        assert metrics.values == [0.4, 0.5, 0.6]
        assert metrics.resource_type == "test_resource"  # default
        assert metrics.region == "test_region"  # default

    def test_dynamic_attributes(self):
        """Test setting and getting dynamic attributes."""
        metrics = ResourceMetrics(resource_id="test_res_1")
        
        # Set dynamic attributes
        metrics.power_consumption = 100.0
        metrics.custom_metric = "test_value"
        
        # Get dynamic attributes
        assert metrics.power_consumption == 100.0
        assert metrics.custom_metric == "test_value"
        
        # Test accessing non-existent attribute
        with pytest.raises(AttributeError):
            _ = metrics.non_existent_attr

    def test_add_metric(self):
        """Test adding a metric with timestamp."""
        metrics = ResourceMetrics(resource_id="test_res_1", region="test_region")
        
        metrics.add_metric("cpu_usage", 0.75, timestamp=5.0)
        
        assert metrics.timestamps == [5.0]
        assert metrics.values == [0.75]
        assert metrics.resource_id == "test_res_1"
        assert metrics.region == "test_region"
        assert metrics.metrics["cpu_usage"] == [(5.0, 0.75)]

    def test_to_dict(self):
        """Test converting ResourceMetrics to dictionary."""
        metrics = ResourceMetrics(
            resource_id="test_res_1",
            metrics={"cpu_usage": 0.5},
            timestamps=[1.0, 2.0],
            values=[0.4, 0.6],
        )
        metrics.resource_type = "compute"
        metrics.region = "us-west-1"
        metrics.custom_attr = "test"
        
        result = metrics.to_dict()
        
        assert result["resource_id"] == "test_res_1"
        assert result["resource_type"] == "compute"
        assert result["region"] == "us-west-1"
        assert result["metrics"] == {"cpu_usage": 0.5}
        assert result["timestamps"] == [1.0, 2.0]
        assert result["values"] == [0.4, 0.6]
        assert result["custom_attr"] == "test"


class TestSimulationResult:
    """Test cases for the SimulationResult class."""

    def test_from_dict_basic(self):
        """Test creating SimulationResult from dictionary with basic data."""
        test_data = create_valid_test_data()
        result = SimulationResult.from_dict(test_data)

        assert result.simulation_id == "test_sim_123"
        assert result.status == SimulationStatus.COMPLETED
        assert result.metrics == {"duration_seconds": 60.0, "tokens_processed": 1000}
        assert len(result.resource_metrics) == 1
        assert "res1" in result.resource_metrics
        assert len(result.token_flow_logs) == 2

    def test_from_dict_with_custom_resource_metrics(self):
        """Test creating SimulationResult with custom resource metrics."""
        custom_metrics = {
            "res1": create_resource_metrics_data(
                resource_id="res1",
                timestamps=[1.0, 2.0, 3.0, 4.0],
                values=[0.2, 0.4, 0.6, 0.8],
                additional_metrics={"power_draw": 150.0}
            ),
            "res2": create_resource_metrics_data(
                resource_id="res2",
                resource_type="storage",
                region="us-east-1",
                timestamps=[1.0, 2.0],
                values=[0.1, 0.3],
            ),
        }
        
        test_data = create_valid_test_data(resource_metrics=custom_metrics)
        result = SimulationResult.from_dict(test_data)

        assert len(result.resource_metrics) == 2
        assert "res1" in result.resource_metrics
        assert "res2" in result.resource_metrics
        
        # Check res1 metrics
        res1_metrics = result.resource_metrics["res1"]
        assert res1_metrics.resource_id == "res1"
        assert res1_metrics.resource_type == "test_resource"
        assert res1_metrics.timestamps == [1.0, 2.0, 3.0, 4.0]
        assert res1_metrics.values == [0.2, 0.4, 0.6, 0.8]
        
        # Check res2 metrics
        res2_metrics = result.resource_metrics["res2"]
        assert res2_metrics.resource_id == "res2"
        assert res2_metrics.resource_type == "storage"
        assert res2_metrics.region == "us-east-1"

    def test_add_resource_metric(self):
        """Test adding a resource metric to existing result."""
        test_data = create_valid_test_data()
        result = SimulationResult.from_dict(test_data)
        
        # Add metric to existing resource
        result.add_resource_metric("res1", "power_draw_w", 100.0, timestamp=3.0)
        
        res_metrics = result.resource_metrics["res1"]
        assert res_metrics.metrics["power_draw_w"] == [(3.0, 100.0)]
        assert 3.0 in res_metrics.timestamps
        assert 100.0 in res_metrics.values
        
        # Add metric to new resource
        result.add_resource_metric("res2", "cpu_usage", 0.8, timestamp=4.0)
        
        assert "res2" in result.resource_metrics
        res2_metrics = result.resource_metrics["res2"]
        assert res2_metrics.resource_id == "res2"
        assert res2_metrics.metrics["cpu_usage"] == [(4.0, 0.8)]

    def test_log_token_flow(self):
        """Test logging a token flow event."""
        test_data = create_valid_test_data(token_flow_logs=[])
        result = SimulationResult.from_dict(test_data)
        
        details = {"token_id": "t1", "from": "res1", "to": "res2"}
        result.log_token_flow("TOKEN_TRANSFERRED", details=details, timestamp=5.0)
        
        assert len(result.token_flow_logs) == 1
        log_entry = result.token_flow_logs[0]
        assert log_entry["event_type"] == "TOKEN_TRANSFERRED"
        assert log_entry["details"] == details
        assert log_entry["timestamp"] == 5.0

    def test_to_dict_serialization(self):
        """Test converting SimulationResult back to dictionary."""
        test_data = create_valid_test_data()
        result = SimulationResult.from_dict(test_data)
        
        result_dict = result.to_dict()
        
        assert result_dict["metadata"]["simulation_id"] == "test_sim_123"
        assert result_dict["status"] == "completed"
        assert "resource_metrics" in result_dict
        assert "token_flow_logs" in result_dict
        assert "config_snapshot" in result_dict

    def test_mark_completed(self):
        """Test marking simulation as completed."""
        test_data = create_valid_test_data()
        result = SimulationResult.from_dict(test_data)
        
        result.mark_completed()
        
        assert result.status == "completed"
        assert result.end_time is not None

    def test_mark_failed(self):
        """Test marking simulation as failed."""
        test_data = create_valid_test_data()
        result = SimulationResult.from_dict(test_data)
        
        test_error = ValueError("Test error")
        result.mark_failed(test_error)
        
        assert result.status == SimulationStatus.FAILED
        assert result.error is not None
        assert result.error["type"] == "ValueError"
        assert result.error["message"] == "Test error"

    def test_to_dataframes(self):
        """Test converting results to pandas DataFrames."""
        test_data = create_valid_test_data()
        result = SimulationResult.from_dict(test_data)
        
        dfs = result.to_dataframes()
        
        assert "resource_metrics" in dfs
        assert "token_flow" in dfs
        
        # Check resource DataFrame
        res_df = dfs["resource_metrics"]
        assert "resource_id" in res_df.columns
        assert "timestamp" in res_df.columns
        assert "value" in res_df.columns
        assert len(res_df) == 3  # timestamps [1.0, 2.0, 3.0]
        
        # Check token flow DataFrame
        token_df = dfs["token_flow"]
        assert "timestamp" in token_df.columns
        assert "event_type" in token_df.columns
        assert len(token_df) == 2  # two token flow events

    def test_export_method(self, tmp_path):
        """Test the export method."""
        test_data = create_valid_test_data()
        result = SimulationResult.from_dict(test_data)
        
        output_file = tmp_path / "test_export.json"
        
        with patch('leaf_cloud.utils.results.export_results') as mock_export:
            exported_path = result.export(str(output_file), "json")
            
            mock_export.assert_called_once_with(result, str(output_file), "json")
            assert exported_path == str(output_file)


class TestExportFunctions:
    """Test cases for the module-level export functions."""
    
    def test_export_results_json(self, tmp_path):
        """Test exporting results to JSON format."""
        test_data = create_valid_test_data(
            metrics={"steps": 100, "total_energy": 250.5},
            resource_metrics={
                "res1": create_resource_metrics_data(
                    timestamps=[1.0, 2.0, 3.0],
                    values=[45.2, 47.8, 50.1],
                    additional_metrics={"power_draw": 150.0}
                )
            }
        )
        
        result = SimulationResult.from_dict(test_data)
        output_file = tmp_path / "test.json"
        
        export_results(result, str(output_file), "json")
        
        assert output_file.exists()
        with open(output_file, 'r') as f:
            exported_data = json.load(f)
            assert exported_data["status"] == "completed"
            assert exported_data["metadata"]["simulation_id"] == "test_sim_123"
    
    def test_export_results_yaml(self, tmp_path):
        """Test exporting results to YAML format."""
        test_data = create_valid_test_data(
            metrics={"steps": 100, "efficiency": 0.85}
        )
        
        result = SimulationResult.from_dict(test_data)
        output_file = tmp_path / "test.yaml"
        
        export_results(result, str(output_file), "yaml")
        
        assert output_file.exists()
        with open(output_file, 'r') as f:
            content = f.read()
            assert 'status: COMPLETED' in content
            assert 'simulation_id: test_sim_123' in content
    
    def test_export_results_csv(self, tmp_path):
        """Test exporting results to CSV format."""
        test_data = create_valid_test_data(
            metrics={"steps": 100, "tokens_processed": 5000},
            resource_metrics={
                "res1": create_resource_metrics_data(
                    timestamps=[1.0, 2.0, 3.0],
                    values=[0.3, 0.5, 0.7]
                )
            }
        )
        result = SimulationResult.from_dict(test_data)
        output_dir = tmp_path / "test_csv_export"

        export_results(result, output_dir, "csv")

        assert output_dir.is_dir()
        assert (output_dir / "simulation_summary.csv").exists()
        assert (output_dir / "resource_metrics.csv").exists()
        assert (output_dir / "token_flow_logs.csv").exists()

        with open(output_dir / "resource_metrics.csv", 'r') as f:
            reader = csv.reader(f)
            header = next(reader)
            assert header == ['resource_id', 'timestamp', 'value', 'metric_name']
            first_row = next(reader)
            assert first_row[0] == "res1"
    
    def test_export_results_with_dict_input(self, tmp_path):
        """Test exporting results when input is a dictionary."""
        test_data = create_valid_test_data()
        output_file = tmp_path / "test_dict.json"
        
        # Export directly from dictionary
        export_results(test_data, str(output_file), "json")
        
        assert output_file.exists()
        with open(output_file, 'r') as f:
            exported_data = json.load(f)
            assert exported_data["status"] == "completed"
    
    def test_export_results_unsupported_format(self, tmp_path):
        """Test exporting with unsupported format raises ValueError."""
        test_data = create_valid_test_data()
        result = SimulationResult.from_dict(test_data)
        output_file = tmp_path / "test.unsupported"
        
        with pytest.raises(ValueError, match="Unsupported export format"):
            export_results(result, str(output_file), "unsupported")


class TestLoadResults:
    """Test cases for loading results from files."""
    
    def test_load_results_json(self, tmp_path):
        """Test loading results from a JSON file."""
        test_data = create_valid_test_data(
            metrics={"steps": 100, "duration": 45.5},
            resource_metrics={
                "res1": create_resource_metrics_data(
                    timestamps=[1.0, 2.0, 3.0],
                    values=[0.2, 0.4, 0.6]
                )
            }
        )
        
        # Create and save test file
        test_file = tmp_path / "test.json"
        with open(test_file, 'w') as f:
            json.dump(test_data, f, indent=2)
        
        # Load results
        loaded_result = load_results(str(test_file))
        
        assert loaded_result.simulation_id == "test_sim_123"
        assert loaded_result.status == SimulationStatus.COMPLETED
        assert loaded_result.metrics == {"steps": 100, "duration": 45.5}
        assert len(loaded_result.resource_metrics) == 1
        assert "res1" in loaded_result.resource_metrics
    
    def test_load_results_yaml(self, tmp_path):
        """Test loading results from a YAML file."""
        test_data = create_valid_test_data(
            metrics={"iterations": 50, "accuracy": 0.95}
        )
        
        # Create and save test file
        test_file = tmp_path / "test.yaml"
        with open(test_file, 'w') as f:
            yaml.dump(test_data, f)
        
        # Load results
        loaded_result = load_results(str(test_file))
        
        assert loaded_result.simulation_id == "test_sim_123"
        assert loaded_result.metrics == {"iterations": 50, "accuracy": 0.95}
    
    def test_load_results_unsupported_format(self, tmp_path):
        """Test loading from unsupported format raises ValueError."""
        test_file = tmp_path / "test.unsupported"
        test_file.write_text("dummy content")
        
        with pytest.raises(ValueError, match="Unsupported file format"):
            load_results(str(test_file))
    
    def test_load_results_nonexistent_file(self):
        """Test loading from non-existent file raises appropriate error."""
        with pytest.raises(FileNotFoundError):
            load_results("nonexistent_file.json")


class TestErrorHandling:
    """Test cases for error handling scenarios."""
    
    def test_invalid_status_conversion(self):
        """Test handling of invalid status values."""
        test_data = create_valid_test_data()
        test_data["status"] = "failed"
        
        result = SimulationResult.from_dict(test_data)
        assert result.status.value == SimulationStatus.FAILED.value
    
    def test_missing_required_fields(self):
        """Test handling of missing required fields."""
        minimal_data = {
            "metadata": {"simulation_id": "test_sim"},
            "status": "completed"
        }
        
        result = SimulationResult.from_dict(minimal_data)
        assert result.simulation_id == "test_sim"
        assert result.status == SimulationStatus.COMPLETED.value
        assert len(result.resource_metrics) == 0
        assert len(result.token_flow_logs) == 0
    
    def test_invalid_timestamp_formats(self):
        """Test handling of various timestamp formats."""
        test_data = create_valid_test_data()
        
        # Test with string timestamp
        test_data["metadata"]["start_time"] = "2023-01-01T12:00:00Z"
        result = SimulationResult.from_dict(test_data)
        assert result.start_time is not None
        
        # Test with None timestamp
        test_data["metadata"]["start_time"] = None
        test_data["metadata"]["end_time"] = datetime.now(timezone.utc).timestamp() # Ensure end_time is not None
        result = SimulationResult.from_dict(test_data)
        assert result.start_time is None


class TestIntegration:
    """Integration tests combining multiple functionalities."""
    
    def test_full_workflow(self, tmp_path):
        """Test complete workflow: create -> modify -> export -> load."""
        # Create initial result
        test_data = create_valid_test_data(
            metrics={"steps": 100},
            resource_metrics={
                "res1": create_resource_metrics_data(
                    timestamps=[1.0, 2.0],
                    values=[0.3, 0.7]
                )
            }
        )
        
        result = SimulationResult.from_dict(test_data)
        
        # Modify result
        result.add_resource_metric("res2", "cpu_usage", 0.8, timestamp=3.0)
        result.log_token_flow("TOKEN_CREATED", {"token_id": "t2"}, timestamp=4.0)
        
        # Export result
        output_file = tmp_path / "integration_test.json"
        result.export(str(output_file), "json")
        
        # Load and verify
        loaded_result = load_results(str(output_file))
        
        assert loaded_result.simulation_id == result.simulation_id
        assert loaded_result.status == result.status
        assert len(loaded_result.resource_metrics) == 2
        assert "res1" in loaded_result.resource_metrics
        assert "res2" in loaded_result.resource_metrics
        assert len(loaded_result.token_flow_logs) == 3  # 2 original + 1 added
    
    def test_round_trip_consistency(self, tmp_path):
        """Test that data remains consistent through save/load cycle."""
        original_data = create_valid_test_data(
            metrics={"precision": 0.95, "recall": 0.89},
            resource_metrics={
                "gpu1": create_resource_metrics_data(
                    resource_type="gpu",
                    region="us-west-2",
                    timestamps=[0.5, 1.5, 2.5],
                    values=[0.1, 0.6, 0.9]
                )
            }
        )
        
        # Create result and export
        result = SimulationResult.from_dict(original_data)
        output_file = tmp_path / "round_trip.json"
        result.export(str(output_file), "json")
        
        # Load and compare
        loaded_result = load_results(str(output_file))
        
        assert loaded_result.simulation_id == result.simulation_id
        assert loaded_result.status == result.status
        assert loaded_result.metrics == result.metrics
        
        # Check resource metrics consistency
        original_res = result.resource_metrics["gpu1"]
        loaded_res = loaded_result.resource_metrics["gpu1"]
        
        assert loaded_res.resource_id == original_res.resource_id
        assert loaded_res.resource_type == original_res.resource_type
        assert loaded_res.region == original_res.region
        assert loaded_res.timestamps == original_res.timestamps
        assert loaded_res.values == original_res.values