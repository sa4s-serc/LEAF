"""Unit tests for the analyser module."""

import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
from pathlib import Path
import tempfile
import json

from leaf_cloud.utils.analyser import (
    format_number,
    print_section_header,
    print_subsection_header,
    create_table,
    analyze_simulation_info,
    analyze_workload_stats,
    analyze_latency,
    analyze_energy,
    analyze_carbon,
    analyze_scaling,
    analyze_resource_utilization,
    analyze_resource_states,
    perform_additional_analysis,
    generate_summary_and_recommendations,
)


class TestFormattingUtils:
    """Test the utility formatting functions in analyser.py."""

    def test_format_number_with_value(self):
        """Test formatting a number with default precision."""
        assert format_number(3.14159) == "3.14"

    def test_format_number_with_precision(self):
        """Test formatting a number with custom precision."""
        assert format_number(3.14159, precision=4) == "3.1416"  # Rounds up
        assert format_number(3.14159, precision=0) == "3"

    def test_format_number_with_none(self):
        """Test formatting None returns 'N/A'."""
        assert format_number(None) == "N/A"

    @patch("builtins.print")
    def test_print_section_header(self, mock_print):
        """Test section header formatting."""
        print_section_header("Test Section")
        mock_print.assert_any_call("\n" + "=" * 80)
        mock_print.assert_any_call("Test Section".center(80))
        assert mock_print.call_count == 3  # Includes the bottom border

    @patch("builtins.print")
    def test_print_subsection_header(self, mock_print):
        """Test subsection header formatting."""
        print_subsection_header("Test Subsection")
        mock_print.assert_any_call("\n" + "-" * 80)
        mock_print.assert_any_call("Test Subsection")
        assert mock_print.call_count == 3  # Includes the bottom border

    def test_create_table(self):
        """Test table creation with tabulate."""
        headers = ["Name", "Value"]
        rows = [
            ["Item 1", 10],
            ["Item 2", 20],
        ]
        table = create_table(headers, rows)
        assert "Item 1" in table
        assert "Item 2" in table
        assert "Name" in table
        assert "Value" in table


class TestAnalyzeSimulationInfo:
    """Test the analyze_simulation_info function."""

    @patch("builtins.print")
    def test_analyze_simulation_info_complete(self, mock_print):
        """Test with complete simulation info."""
        sim_info = {
            "duration": 3600.5,
            "simulation_time_reached": 3600.0,
            "execution_time": 12.34,
            "total_tokens": 1000,
            "completed_tokens": 750,
            "active_tokens": 50,
        }
        
        # Call the function
        analyze_simulation_info(sim_info)
        
        # Verify output contains expected values
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "SIMULATION OVERVIEW" in output
        assert "3600.50" in output  # Duration
        assert "750" in output  # Completed tokens
        assert "75.00%" in output  # Completion percentage

    @patch("builtins.print")
    def test_analyze_simulation_info_missing_values(self, mock_print):
        """Test with missing values in simulation info."""
        sim_info = {
            "duration": 3600.5,
            # Missing other required fields
        }
        
        # Call the function
        analyze_simulation_info(sim_info)
        
        # Verify output contains N/A for missing values
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "N/A" in output


class TestAnalyzeWorkloadStats:
    """Test the analyze_workload_stats function."""

    @patch("builtins.print")
    def test_analyze_workload_stats_empty(self, mock_print):
        """Test with empty workload stats."""
        analyze_workload_stats({})
        mock_print.assert_not_called()

    @patch("builtins.print")
    def test_analyze_workload_stats_with_data(self, mock_print):
        """Test with workload stats data."""
        workload_stats = {
            "total_requests": 1000,
            "successful_requests": 950,
            "failed_requests": 50,
            "request_rate": 10.5,
        }
        
        analyze_workload_stats(workload_stats)
        
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "WORKLOAD STATISTICS" in output
        assert "1000" in output
        assert "95.00%" in output  # Success rate


class TestGenerateSummaryAndRecommendations:
    """Test the generate_summary_and_recommendations function."""

    @patch("builtins.print")
    def test_generate_summary_with_complete_data(self, mock_print):
        """Test with complete simulation results."""
        results = {
            "simulation_info": {
                "duration": 3600,
                "simulation_time_reached": 3600,
                "execution_time": 10.5,
                "total_tokens": 1000,
                "completed_tokens": 1000,
                "active_tokens": 0,
            },
            "workload_stats": {
                "total_requests": 1000,
                "successful_requests": 980,
                "failed_requests": 20,
                "request_rate": 10.0,
            },
            "latency_metrics": {
                "avg_latency": 0.5,
                "p95_latency": 1.2,
                "p99_latency": 2.0,
            },
            "energy_metrics": {
                "total_energy_consumed": 5000,  # kWh
                "energy_per_request": 0.1,
            },
            "carbon_metrics": {
                "total_carbon_emitted": 2500,  # kg CO2e
                "carbon_per_request": 0.05,
            },
            "resource_utilization": {
                "cpu_avg": 65.5,
                "memory_avg": 45.2,
            },
        }
        
        generate_summary_and_recommendations(results)
        
        # Verify summary section was printed
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "SUMMARY AND RECOMMENDATIONS" in output
        assert "100.00%" in output  # Completion rate
        assert "98.00%" in output  # Success rate
        assert "Recommendations:" in output


class TestAnalyzeLatency:
    """Test the analyze_latency function."""

    @patch("builtins.print")
    def test_analyze_latency_empty(self, mock_print):
        """Test with empty latency metrics."""
        analyze_latency({})
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "LATENCY ANALYSIS" in output
        assert "Average Latency" in output
        assert "Value (ms)" in output

    @patch("builtins.print")
    def test_analyze_latency_with_data(self, mock_print):
        """Test with latency metrics data."""
        latency_metrics = {
            "average": 0.5,
            "percentile_95": 1.2,
            "percentile_99": 2.0,
            "latencies": [0.1, 0.2, 0.5, 1.2, 2.0, 0.3, 0.4, 0.6, 0.7, 0.8]
        }
        
        analyze_latency(latency_metrics)
        
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "LATENCY ANALYSIS" in output
        # Check for the values in the table format
        assert "0.5" in output  # average latency
        assert "1.2" in output  # 95th percentile
        assert "2" in output    # 99th percentile (formatted without decimal)
        assert "Latency Distribution" in output
        assert "Range (ms)" in output
        assert "Count" in output


class TestAnalyzeEnergy:
    """Test the analyze_energy function."""

    @patch("builtins.print")
    def test_analyze_energy_empty(self, mock_print):
        """Test with empty energy metrics."""
        analyze_energy({})
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "ENERGY CONSUMPTION ANALYSIS" in output
        assert "Total Energy: 0.00 kWh" in output

    @patch("builtins.print")
    def test_analyze_energy_with_data(self, mock_print):
        """Test with energy metrics data."""
        energy_metrics = {
            "total_kwh": 5000,
            "by_resource_type": {
                "compute": 3500,
                "storage": 1000,
                "network": 500
            },
            "resource_energy": {
                "cluster1.node1": 2000,
                "cluster1.node2": 1500,
                "storage1": 1000,
                "network1": 500
            }
        }
        
        analyze_energy(energy_metrics)
        
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "ENERGY CONSUMPTION ANALYSIS" in output
        assert "Total Energy: 5000.00 kWh" in output
        assert "Breakdown by Resource Type" in output
        assert "Breakdown by Individual Resource" in output
        assert "Compute" in output
        assert "Storage" in output
        assert "Network" in output


class TestAnalyzeCarbon:
    """Test the analyze_carbon function."""

    @patch("builtins.print")
    def test_analyze_carbon_empty(self, mock_print):
        """Test with empty carbon metrics."""
        analyze_carbon({})
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "CARBON EMISSIONS ANALYSIS" in output
        assert "Total Carbon Emissions: 0.00 kg CO2e" in output

    @patch("builtins.print")
    def test_analyze_carbon_with_data(self, mock_print):
        """Test with carbon metrics data."""
        carbon_metrics = {
            "total_carbon_emissions": 2500,  # kg CO2e
            "regional_emissions": {
                "us-west1": 1000,
                "us-east1": 1500
            },
            "resource_emissions": {
                "cluster1.node1": 1000,
                "cluster1.node2": 800,
                "storage1": 500,
                "network1": 200
            }
        }
        
        analyze_carbon(carbon_metrics)
        
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "CARBON EMISSIONS ANALYSIS" in output
        assert "Total Carbon Emissions: 2500.00 kg CO2e" in output
        assert "Breakdown by Region" in output
        assert "Breakdown by Resource" in output
        assert "us-west1" in output
        assert "us-east1" in output


class TestAnalyzeScaling:
    """Test the analyze_scaling function."""

    @patch("builtins.print")
    def test_analyze_scaling_empty(self, mock_print):
        """Test with empty scaling metrics."""
        analyze_scaling({})
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "SCALING EFFICIENCY ANALYSIS" in output
        assert "Average Utilization" in output
        assert "0.00%" in output
        assert "Maximum Pods" in output
        assert "Total Scaling Events" in output
        assert "Utilization Assessment" in output
        assert "Low resource utilization" in output

    @patch("builtins.print")
    def test_analyze_scaling_with_data(self, mock_print):
        """Test with scaling metrics data."""
        scaling_metrics = {
            "average_utilization": 0.75,  # 75%
            "max_pods": 20,
            "scaling_events": 15,
            "resource_usage": [0.7, 0.8, 0.65, 0.9, 0.85]  # Historical usage
        }
        
        analyze_scaling(scaling_metrics)
        
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "SCALING EFFICIENCY ANALYSIS" in output
        assert "75.00%" in output  # average_utilization
        assert "20" in output  # max_pods
        assert "15" in output  # scaling_events
        assert "Utilization Assessment" in output
        assert "optimal range" in output


class TestAnalyzeResourceUtilization:
    """Test the analyze_resource_utilization function."""

    @patch("builtins.print")
    def test_analyze_resource_utilization_empty(self, mock_print):
        """Test with empty resource utilization."""
        analyze_resource_utilization({})
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "RESOURCE UTILIZATION ANALYSIS" in output
        assert "No resource utilization data available" in output

    @patch("builtins.print")
    def test_analyze_resource_utilization_with_data(self, mock_print):
        """Test with resource utilization data."""
        resource_util = {
            "web_server_1": {
                "avg": 0.75,  # 75%
                "max": 0.95,  # 95%
                "min": 0.60,  # 60%
            },
            "database_1": {
                "avg": 0.45,  # 45%
                "max": 0.65,  # 65%
                "min": 0.30,  # 30%
            }
        }
        
        analyze_resource_utilization(resource_util)
        
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "RESOURCE UTILIZATION ANALYSIS" in output
        assert "web_server_1" in output
        assert "database_1" in output
        assert "75.00%" in output  # web_server_1 avg
        assert "95.00%" in output  # web_server_1 max
        assert "45.00%" in output  # database_1 avg
        assert "Resource Efficiency Highlights" in output


class TestAnalyzeResourceStates:
    """Test the analyze_resource_states function."""

    @patch("builtins.print")
    def test_analyze_resource_states_empty(self, mock_print):
        """Test with empty resource states."""
        analyze_resource_states({})
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "RESOURCE STATE TRANSITIONS" in output
        assert "No resource state data available" in output

    @patch("builtins.print")
    def test_analyze_resource_states_with_data(self, mock_print):
        """Test with resource states data."""
        resource_states = {
            "web_server_1": [
                {"state": "starting", "time": 0},
                {"state": "running", "time": 10},
                {"state": "scaling", "time": 30},
            ],
            "database_1": [
                {"state": "starting", "time": 0},
                {"state": "running", "time": 20},
            ],
            "load_balancer_1": [
                {"state": "running", "time": 0}
            ]
        }
        
        analyze_resource_states(resource_states)
        
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "RESOURCE STATE TRANSITIONS" in output
        assert "Resources with State Changes" in output
        assert "web_server_1" in output
        assert "database_1" in output
        assert "2" in output  # state changes for web_server_1
        assert "1" in output  # state change for database_1
        assert "starting@0.00s → running@10.00s → scaling@30.00s" in output
        assert "starting@0.00s → running@20.00s" in output


class TestPerformAdditionalAnalysis:
    """Test the perform_additional_analysis function."""

    @patch("builtins.print")
    def test_perform_additional_analysis_empty(self, mock_print):
        """Test with empty results."""
        perform_additional_analysis({})
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "ADDITIONAL INSIGHTS" in output

    @patch("builtins.print")
    def test_perform_additional_analysis_with_data(self, mock_print):
        """Test with results data."""
        results = {
            "simulation_info": {
                "duration": 3600,
                "simulation_time_reached": 3600,
                "total_tokens": 1000000  # 1M tokens
            },
            "workload_stats": {
                "total_requests": 1000,
                "successful_requests": 980,
            },
            "latency_metrics": {
                "p95_latency": 1.2,
                "latencies": [0.1, 0.2, 1.2, 0.5, 0.8, 1.5, 0.3, 0.4, 1.1, 0.9]
            },
            "energy_metrics": {
                "total_kwh": 5000,  # kWh
                "resource_consumption": {
                    "web_server_1": 3000,
                    "database_1": 1500,
                    "cache_1": 500
                }
            },
            "carbon_metrics": {
                "total_carbon_emissions": 2000,  # kg CO2e
                "resource_emissions": {
                    "web_server_1": 1000,
                    "database_1": 800,
                    "cache_1": 200
                }
            },
            "resource_utilization": {
                "web_server_1": {"avg": 0.75, "max": 0.95, "min": 0.6},
                "database_1": {"avg": 0.5, "max": 0.7, "min": 0.4}
            }
        }
        
        perform_additional_analysis(results)
        
        output = "\n".join(call[0][0] for call in mock_print.call_args_list)
        assert "ADDITIONAL INSIGHTS" in output
        assert "Highest carbon emitter" in output
        assert "Energy efficiency" in output
