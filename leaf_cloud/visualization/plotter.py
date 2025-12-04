"""Plotting utilities for LEAF-Cloud visualizations.

This module provides functions to generate various types of plots and charts
for visualizing simulation results and metrics. It uses matplotlib as the
underlying plotting library.

Example:
    ```python
    from pathlib import Path
    from leaf_cloud.visualization.plotter import plot_time_series

    # Generate a simple time series plot
    time_points = [1, 2, 3, 4, 5]
    values = [10, 15, 13, 18, 22]
    
    plot_time_series(
        x=time_points,
        y=values,
        title="Sample Time Series",
        xlabel="Time (s)",
        ylabel="Value",
        output_path="output/plot.png"
    )
    ```
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

# Set non-interactive backend
matplotlib.use("Agg")

# Initialize module logger
logger = logging.getLogger(__name__)

# Type aliases
ColorType = Union[str, Tuple[float, float, float], Tuple[float, float, float, float]]
FigureSize = Tuple[float, float]

def plot_time_series(
    x: Sequence[float],
    y: Sequence[float],
    title: str,
    xlabel: str,
    ylabel: str,
    output_path: Union[str, Path],
    figsize: FigureSize = (10, 6),
    color: ColorType = "blue",
    linewidth: float = 2.0,
    markersize: float = 6.0,
    grid: bool = True,
    dpi: int = 300,
    **kwargs: Any
) -> Path:
    """Plot a time-series line chart and save to file.

    This function creates a line plot of y vs. x and saves it to the specified
    output path. The plot includes proper labels, title, and optional grid.

    Args:
        x: Sequence of x-coordinates (time points).
        y: Sequence of y-coordinates (values).
        title: Title of the plot.
        xlabel: Label for the x-axis.
        ylabel: Label for the y-axis.
        output_path: Path where the plot image will be saved.
        figsize: Figure size as (width, height) in inches. Defaults to (10, 6).
        color: Color of the line. Can be a string (e.g., 'blue', '#1f77b4'),
              RGB/RGBA tuple, or any valid matplotlib color spec.
        linewidth: Width of the line in points. Defaults to 2.0.
        markersize: Size of the markers in points. Set to 0 to disable markers.
                  Defaults to 6.0.
        grid: Whether to show grid lines. Defaults to True.
        dpi: Resolution in dots per inch. Defaults to 300.
        **kwargs: Additional keyword arguments passed to plt.plot().

    Returns:
        Path to the saved plot file.

    Raises:
        ValueError: If x and y have different lengths.
        FileNotFoundError: If the output directory doesn't exist and cannot be created.
        PermissionError: If the output file cannot be written.
        RuntimeError: If there's an error generating the plot.

    Example:
        ```python
        # Basic usage
        plot_time_series(
            x=[1, 2, 3, 4],
            y=[10, 20, 15, 25],
            title="Sample Plot",
            xlabel="Time (s)",
            ylabel="Value",
            output_path="output/plot.png"
        )

        # Custom styling
        plot_time_series(
            x=time_points,
            y=values,
            title="Custom Styled Plot",
            xlabel="Time",
            ylabel="Metric",
            output_path="output/custom_plot.png",
            color="#ff5733",
            linewidth=3.0,
            markersize=8.0,
            linestyle="--"
        )
        ```
    """
    # Input validation
    if len(x) != len(y):
        raise ValueError(f"x and y must have the same length, got {len(x)} and {len(y)}")
    
    if not x:
        raise ValueError("x and y cannot be empty")
    
    # Convert output_path to Path object
    output_path = Path(output_path).resolve()
    
    # Ensure output directory exists
    output_dir = output_path.parent
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        # Test write access
        (output_dir / ".write_test").touch(exist_ok=True)
    except (OSError, PermissionError) as e:
        raise PermissionError(f"Cannot write to output directory {output_dir}: {str(e)}") from e
    
    # Create figure and plot
    try:
        logger.debug("Creating time series plot: %s", title)
        
        plt.figure(figsize=figsize)
        
        # Plot the data
        plt.plot(
            x, y,
            marker="o" if markersize > 0 else "",
            markersize=markersize,
            linewidth=linewidth,
            color=color,
            **kwargs
        )
        
        # Add labels and title
        plt.title(title, pad=20)
        plt.xlabel(xlabel, labelpad=10)
        plt.ylabel(ylabel, labelpad=10)
        
        # Add grid if requested
        if grid:
            plt.grid(True, linestyle='--', alpha=0.7)
        
        # Adjust layout
        plt.tight_layout()
        
        # Save the figure
        plt.savefig(output_path, dpi=dpi, bbox_inches='tight')
        logger.info("Saved plot to %s", output_path)
        
        return output_path
        
    except Exception as e:
        logger.error("Failed to generate plot: %s", str(e))
        raise RuntimeError(f"Failed to generate plot: {str(e)}") from e
        
    finally:
        # Always close the figure to free memory
        plt.close()
