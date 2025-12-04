"""Unit tests for the ResultsProcessor class."""

import pytest
from unittest.mock import MagicMock, patch, ANY
import numpy as np
from datetime import datetime

from leaf_cloud.utils.results_processor import ResultsProcessor
from leaf_cloud.utils.schemas.results import (
    RawSimulationResult,
    SimulationMetadata,
    TimeSeriesDataPoint,
    RawResourceMetrics,
    ProcessedSimulationResult,
    AnalysisMetadata,
    MetricStatistics,
)


class TestResultsProcessor:
    """Test suite for the ResultsProcessor class."""

    @pytest.fixture
    def mock_orchestrator(self):
        """Create a mock orchestrator for testing."""
        mock_orch = MagicMock()
        return mock_orch

    @pytest.fixture
    def processor(self, mock_orchestrator):
        """Create a ResultsProcessor instance with a mock orchestrator."""
        return ResultsProcessor(mock_orchestrator)

    @pytest.fixture
    def sample_raw_results(self):
        """Create sample raw simulation results for testing."""
        return RawSimulationResult(
            metadata=SimulationMetadata(
                simulation_id="test_sim_123",
                start_time=datetime(2023, 1, 1, 0, 0, 0).timestamp(),
                end_time=datetime(2023, 1, 1, 1, 0, 0).timestamp(),
                duration_seconds=3600,
                resource_count=2,
                token_count=100,
                leaf_cloud_version="1.0.0",
            ),
            config_snapshot={
                "simulation": {
                    "duration_seconds": 3600,
                    "warmup_seconds": 300,
                    "cooldown_seconds": 300,
                    "random_seed": 42,
                },
                "resources": {
                    "resource1": {
                        "type": "gpu",
                        "region": "us-west1",
                        "config": {"model": "A100", "count": 1}
                    },
                    "resource2": {
                        "type": "cpu",
                        "region": "us-east1",
                        "config": {"cpus": 4, "memory_gb": 16}
                    }
                },
                "workload": {
                    "arrival_rate": 10.0,
                    "request_distribution": "poisson",
                    "token_distribution": {
                        "min_tokens": 1,
                        "max_tokens": 1000,
                        "distribution": "lognormal",
                        "mean": 5.0,
                        "sigma": 1.0
                    }
                },
                "metrics": {
                    "sampling_interval_seconds": 10,
                    "tracked_metrics": [
                        "power_draw_w",
                        "cpu_utilization",
                        "memory_utilization"
                    ]
                }
            },
            resource_metrics={
                "resource1": RawResourceMetrics(
                    resource_type="gpu",
                    region="us-west1",
                    power_draw_w=[
                        TimeSeriesDataPoint(timestamp=0, value=100.0),
                        TimeSeriesDataPoint(timestamp=1800, value=150.0),
                        TimeSeriesDataPoint(timestamp=3600, value=200.0),
                    ],
                    cpu_utilization=[
                        TimeSeriesDataPoint(timestamp=0, value=0.5),
                        TimeSeriesDataPoint(timestamp=1800, value=0.6),
                        TimeSeriesDataPoint(timestamp=3600, value=0.7),
                    ],
                    memory_utilization=[
                        TimeSeriesDataPoint(timestamp=0, value=0.4),
                        TimeSeriesDataPoint(timestamp=1800, value=0.5),
                        TimeSeriesDataPoint(timestamp=3600, value=0.6),
                    ],
                ),
                "resource2": RawResourceMetrics(
                    resource_type="cpu",
                    region="us-east1",
                    power_draw_w=[
                        TimeSeriesDataPoint(timestamp=0, value=200.0),
                        TimeSeriesDataPoint(timestamp=1800, value=250.0),
                        TimeSeriesDataPoint(timestamp=3600, value=300.0),
                    ],
                    cpu_utilization=[
                        TimeSeriesDataPoint(timestamp=0, value=0.6),
                        TimeSeriesDataPoint(timestamp=1800, value=0.7),
                        TimeSeriesDataPoint(timestamp=3600, value=0.8),
                    ],
                    memory_utilization=[
                        TimeSeriesDataPoint(timestamp=0, value=0.3),
                        TimeSeriesDataPoint(timestamp=1800, value=0.4),
                        TimeSeriesDataPoint(timestamp=3600, value=0.5),
                    ],
                ),
            },
            token_traces=[],  # Simplified for initial tests
        )

    def test_init(self, mock_orchestrator):
        """Test ResultsProcessor initialization."""
        processor = ResultsProcessor(mock_orchestrator)
        assert processor.orchestrator == mock_orchestrator
        assert processor._resource_cache == {}

    def test_process_returns_processed_simulation_result(self, processor, sample_raw_results):
        """Test that process() returns a ProcessedSimulationResult."""
        # Mock the orchestrator to return resource metadata
        processor.orchestrator.get_resource_metadata.return_value = {
            "resource1": {"type": "gpu", "region": "us-west"},
            "resource2": {"type": "cpu", "region": "us-east"},
        }
        
        # Mock the helper methods to return simplified results
        with patch.object(processor, '_run_metric_models') as mock_run_models, \
             patch.object(processor, '_calculate_per_resource_analysis') as mock_calc_res_analysis, \
             patch.object(processor, '_analyze_token_metrics') as mock_token_analysis, \
             patch.object(processor, '_calculate_summary_metrics') as mock_summary_metrics, \
             patch.object(processor, '_group_analysis_by_attribute') as mock_group_analysis, \
             patch.object(processor, '_analyze_by_processing_path') as mock_path_analysis, \
             patch.object(processor, '_calculate_efficiency_metrics') as mock_efficiency_metrics, \
             patch.object(processor, '_calculate_cost_analysis') as mock_cost_analysis, \
             patch.object(processor, '_generate_distributions') as mock_distributions:
            
            # Set up return values for the mocks
            mock_run_models.return_value = {"energy_models": {}, "carbon_models": {}, "cost_models": {}}
            mock_calc_res_analysis.return_value = {}
            mock_token_analysis.return_value = {}
            mock_summary_metrics.return_value = {}
            mock_group_analysis.return_value = {}
            mock_path_analysis.return_value = {}
            mock_efficiency_metrics.return_value = {}
            mock_cost_analysis.return_value = {}
            mock_distributions.return_value = {}
            
            # Call the method under test
            result = processor.process(sample_raw_results)
            
            # Verify the result is a ProcessedSimulationResult
            assert isinstance(result, ProcessedSimulationResult)
            assert isinstance(result.metadata, AnalysisMetadata)
            assert result.metadata.raw_simulation_id == "test_sim_123"
            
            # Verify the helper methods were called
            mock_run_models.assert_called_once()
            mock_calc_res_analysis.assert_called_once()
            mock_token_analysis.assert_called_once()
            mock_summary_metrics.assert_called_once()
            mock_group_analysis.assert_called()  # Called multiple times with different attributes
            mock_path_analysis.assert_called_once()
            mock_efficiency_metrics.assert_called_once()
            mock_cost_analysis.assert_called_once()
            mock_distributions.assert_called_once()

    def test_calculate_energy_consumption(self, processor):
        """Test energy consumption calculation from power time series."""
        # Create a simple power time series (100W for 1 hour = 100 Wh)
        power_series = [
            TimeSeriesDataPoint(timestamp=0, value=100.0),    # 100W at start
            TimeSeriesDataPoint(timestamp=1800, value=100.0),  # 100W at 30 min
            TimeSeriesDataPoint(timestamp=3600, value=100.0),  # 100W at 60 min
        ]
        
        # Calculate energy for 1 hour (3600 seconds)
        energy_wh = processor._calculate_energy_consumption(power_series, 3600)
        
        # Should be approximately 100 Wh (100W * 1h)
        assert energy_wh == pytest.approx(100.0, rel=1e-3)
        
        # Test with varying power levels
        power_series_varied = [
            TimeSeriesDataPoint(timestamp=0, value=0.0),      # 0W at start
            TimeSeriesDataPoint(timestamp=1800, value=100.0),  # 100W at 30 min
            TimeSeriesDataPoint(timestamp=3600, value=0.0),    # 0W at 60 min
        ]
        
        # Should be approximately 50 Wh (trapezoidal integration of triangle)
        energy_wh = processor._calculate_energy_consumption(power_series_varied, 3600)
        assert energy_wh == pytest.approx(50.0, rel=1e-3)

    def test_calculate_carbon_emissions(self, processor):
        """Test carbon emissions calculation."""
        # Test with known carbon intensity (gCO2e/kWh)
        test_cases = [
            (100.0, "us-west", 25.0),    # 100 Wh * 250 g/kWh / 1000 = 25 g
            (200.0, "us-east", 70.0),    # 200 Wh * 350 g/kWh / 1000 = 70 g
            (50.0, "europe", 15.0),      # 50 Wh * 300 g/kWh / 1000 = 15 g
            (1000.0, "asia", 500.0),     # 1000 Wh * 500 g/kWh / 1000 = 500 g
        ]
        
        for energy_wh, region, expected_emissions in test_cases:
            emissions = processor._calculate_carbon_emissions(energy_wh, region)
            assert emissions == pytest.approx(expected_emissions, rel=1e-3)
        
        # Test with default region (global average)
        emissions = processor._calculate_carbon_emissions(100.0, "unknown-region")
        assert emissions == pytest.approx(40.0, rel=1e-3)  # 100 Wh * 400 g/kWh / 1000

    def test_calculate_resource_cost(self, processor):
        """Test resource cost calculation."""
        # Test with explicit hourly rate
        resource_meta = {"hourly_rate": 0.5}  # $0.50 per hour
        cost = processor._calculate_resource_cost(resource_meta, 3600)  # 1 hour
        assert cost == pytest.approx(0.5, rel=1e-3)
        
        # Test with 30 minutes
        cost = processor._calculate_resource_cost(resource_meta, 1800)  # 0.5 hours
        assert cost == pytest.approx(0.25, rel=1e-3)
        
        # Test with default rate based on resource type
        resource_meta = {"type": "gpu"}  # Default rate is $0.90/hour
        cost = processor._calculate_resource_cost(resource_meta, 3600)  # 1 hour
        assert cost == pytest.approx(0.9, rel=1e-3)
        
        # Test with unknown resource type (should use default of $0.05/hour)
        resource_meta = {"type": "unknown"}
        cost = processor._calculate_resource_cost(resource_meta, 3600)  # 1 hour
        assert cost == pytest.approx(0.05, rel=1e-3)
        
        # Test with zero duration
        cost = processor._calculate_resource_cost(resource_meta, 0)
        assert cost == 0.0
        
        # Test with invalid resource meta (should return None)
        cost = processor._calculate_resource_cost(None, 3600)
        assert cost is None

    def test_calculate_statistics(self, processor):
        """Test calculation of statistics from time series data."""
        # Create a simple time series
        time_series = [
            TimeSeriesDataPoint(timestamp=0, value=10.0),
            TimeSeriesDataPoint(timestamp=1, value=20.0),
            TimeSeriesDataPoint(timestamp=2, value=30.0),
            TimeSeriesDataPoint(timestamp=3, value=40.0),
            TimeSeriesDataPoint(timestamp=4, value=50.0),
        ]
        
        # Calculate statistics
        stats = processor._calculate_statistics(time_series)
        
        # Verify basic statistics
        assert stats.mean == pytest.approx(30.0, rel=1e-3)
        assert stats.min == 10.0
        assert stats.max == 50.0
        assert stats.count == 5
        
        # Verify percentiles (approximate due to floating point)
        assert stats.p25 == pytest.approx(20.0, rel=1e-3)
        assert stats.p50 == pytest.approx(30.0, rel=1e-3)
        assert stats.p75 == pytest.approx(40.0, rel=1e-3)
        
        # Test with empty series
        empty_stats = processor._calculate_statistics([])
        assert empty_stats.mean == 0
        assert empty_stats.count == 0
        
        # Test with single value
        single_stats = processor._calculate_statistics([TimeSeriesDataPoint(timestamp=0, value=42.0)])
        assert single_stats.mean == 42.0
        assert single_stats.min == 42.0
        assert single_stats.max == 42.0
        assert single_stats.count == 1

    def test_create_single_value_metric(self, processor):
        """Test creation of a single value metric."""
        value = 42.0
        metric = processor._create_single_value_metric(value)
        
        # All statistics should be equal to the input value
        assert metric.mean == value
        assert metric.std == 0.0
        assert metric.min == value
        assert metric.max == value
        assert metric.p25 == value
        assert metric.p50 == value
        assert metric.p75 == value
        assert metric.p95 == value
        assert metric.p99 == value
        assert metric.count == 1

    def test_get_resource_attr(self, processor):
        """Test getting attributes from resource objects or dictionaries."""
        # Test with dictionary
        resource_dict = {"type": "gpu", "region": "us-west"}
        assert processor._get_resource_attr(resource_dict, "type") == "gpu"
        assert processor._get_resource_attr(resource_dict, "region") == "us-west"
        assert processor._get_resource_attr(resource_dict, "missing", "default") == "default"
        
        # Test with object
        class Resource:
            def __init__(self):
                self.type = "cpu"
                self.region = "us-east"
        
        resource_obj = Resource()
        assert processor._get_resource_attr(resource_obj, "type") == "cpu"
        assert processor._get_resource_attr(resource_obj, "region") == "us-east"
        assert processor._get_resource_attr(resource_obj, "missing", "default") == "default"
        
        # Test with None
        assert processor._get_resource_attr(None, "type", "default") == "default"
