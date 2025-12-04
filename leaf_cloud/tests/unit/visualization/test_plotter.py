"""Unit tests for the visualization plotter module."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from leaf_cloud.visualization.plotter import plot_time_series

class TestPlotter:
    """Test cases for plotter functions."""

    @pytest.fixture
    def setup_temp_dir(self):
        """Set up a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    def test_plot_time_series_basic(self, setup_temp_dir):
        """Test basic time series plot generation."""
        # Test data
        x = [1, 2, 3, 4, 5]
        y = [10, 15, 13, 18, 22]
        output_path = setup_temp_dir / "basic_plot.png"
        
        # Generate plot
        result_path = plot_time_series(
            x=x,
            y=y,
            title="Test Plot",
            xlabel="X Axis",
            ylabel="Y Axis",
            output_path=output_path
        )
        
        # Verify
        assert result_path == output_path
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    def test_plot_time_series_customization(self, setup_temp_dir):
        """Test plot customization options."""
        # Convert numpy arrays to lists to avoid truth value ambiguity
        x = list(np.linspace(0, 10, 100))
        y = list(np.sin(x))
        output_path = setup_temp_dir / "custom_plot.png"
        
        # Generate plot with custom options
        result_path = plot_time_series(
            x=x,
            y=y,
            title="Custom Plot",
            xlabel="Time",
            ylabel="Amplitude",
            output_path=output_path,
            color='red',
            linewidth=3.0,
            markersize=8,
            figsize=(12, 8),
            dpi=150,
            grid=False
        )
        
        # Verify
        assert result_path == output_path
        assert output_path.exists()

    def test_plot_time_series_empty_data(self, setup_temp_dir):
        """Test behavior with empty data."""
        output_path = setup_temp_dir / "empty_plot.png"
        
        with pytest.raises(ValueError, match="x and y must have the same length"):
            plot_time_series(
                x=[],
                y=[1, 2, 3],  # Mismatched lengths
                title="Empty Data Test",
                xlabel="X",
                ylabel="Y",
                output_path=output_path
            )

    def test_plot_time_series_invalid_output_path(self):
        """Test behavior with invalid output path."""
        with pytest.raises(PermissionError):
            plot_time_series(
                x=[1, 2, 3],
                y=[1, 4, 9],
                title="Invalid Path Test",
                xlabel="X",
                ylabel="Y",
                output_path="/nonexistent/directory/plot.png"
            )

    def test_plot_time_series_single_point(self, setup_temp_dir):
        """Test plotting with a single data point."""
        output_path = setup_temp_dir / "single_point.png"
        
        result_path = plot_time_series(
            x=[1],
            y=[42],
            title="Single Point",
            xlabel="X",
            ylabel="Y",
            output_path=output_path
        )
        
        assert result_path.exists()

    @patch('matplotlib.pyplot.savefig')
    def test_plot_time_errors(self, mock_savefig):
        """Test handling of plotting errors."""
        mock_savefig.side_effect = RuntimeError("Failed to save figure")
        
        with tempfile.NamedTemporaryFile(suffix='.png') as tmp_file:
            with pytest.raises(RuntimeError, match="Failed to generate plot"):
                plot_time_series(
                    x=[1, 2, 3],
                    y=[1, 4, 9],
                    title="Error Test",
                    xlabel="X",
                    ylabel="Y",
                    output_path=tmp_file.name
                )

    def test_plot_time_series_large_dataset(self, setup_temp_dir):
        """Test plotting with a large dataset."""
        output_path = setup_temp_dir / "large_dataset.png"
        n_points = 10000
        
        # Convert numpy arrays to lists to avoid truth value ambiguity
        x = list(np.linspace(0, 10, n_points))
        y = list(np.sin(x) + np.random.normal(0, 0.1, n_points))
        
        result_path = plot_time_series(
            x=x,
            y=y,
            title="Large Dataset",
            xlabel="X",
            ylabel="Y",
            output_path=output_path
        )
        
        assert result_path.exists()
        assert output_path.stat().st_size > 0

# Add more test cases for other plot types and configurations as needed
