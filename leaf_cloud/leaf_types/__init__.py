"""
Core data types for the LEAF-Cloud framework.

This module contains shared data structures and type definitions used throughout
the codebase to prevent circular imports and provide a single source of truth
for type information.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel


class SimulationStatus(str, Enum):
    """Status of a simulation run."""
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPORTING = "exporting"
    UNKNOWN = "unknown"


class ResultFormat(str, Enum):
    """Supported export formats for simulation results."""
    JSON = "json"
    YAML = "yaml"
    CSV = "csv"
    PARQUET = "parquet"
    HTML = "html"


class TokenFlowLogEntry(BaseModel):
    """Base class for token flow log entries."""
    timestamp: float
    event_type: str
    details: Dict[str, Any] = {}


class ResourceMetrics(BaseModel):
    """Metrics for a single resource."""
    average_utilization: float = 0.0
    total_energy_kwh: float = 0.0
    total_carbon_kgco2e: float = 0.0
    state_changes: int = 0
    measurement_count: Dict[str, int] = field(default_factory=dict)


"""Expose the canonical SimulationResult from utils for consistency."""
from ..utils.results import SimulationResult  # re-export canonical class
SimulationResults = SimulationResult  # backward compatibility

__all__ = [
    'SimulationStatus',
    'ResultFormat',
    'TokenFlowLogEntry',
    'ResourceMetrics',
    'SimulationResult',
    'SimulationResults'
]
