"""
Visualization utilities for LEAF-Cloud.

This module provides functions for creating and saving various types of plots
using Plotly.
"""

from pathlib import Path
from typing import List, Optional, Union

import plotly.graph_objects as go


def save_plot(fig: "go.Figure", output_path: Union[str, Path], format: str = "png") -> None:
    """Save a plotly figure to a file.

    Args:
        fig: The plotly figure to save
        output_path: Path to save the figure to
        format: Output format (png, jpg, svg, pdf, etc.)
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_image(output_path, format=format)


def create_time_series_plot(
    x: List[float],
    y: List[float],
    title: str,
    x_label: str = "Time (s)",
    y_label: str = "Value",
) -> "go.Figure":
    """Create a time series plot using plotly.

    Args:
        x: X-axis values (time)
        y: Y-axis values
        title: Plot title
        x_label: X-axis label
        y_label: Y-axis label

    Returns:
        A plotly Figure object
    """
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines"))
    
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        showlegend=False,
        template="plotly_white",
    )
    
    return fig


def create_histogram(
    data: List[float],
    title: str,
    x_label: str = "Value",
    y_label: str = "Frequency",
    nbins: Optional[int] = None,
) -> "go.Figure":
    """Create a histogram using plotly.

    Args:
        data: Data to plot
        title: Plot title
        x_label: X-axis label
        y_label: Y-axis label
        nbins: Number of bins (auto if None)

    Returns:
        A plotly Figure object
    """
    fig = go.Figure()
    
    fig.add_trace(
        go.Histogram(
            x=data,
            nbinsx=nbins,
            marker=dict(color="#636efa"),
            opacity=0.75,
        )
    )
    
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        showlegend=False,
        template="plotly_white",
        bargap=0.1,
    )
    
    return fig
