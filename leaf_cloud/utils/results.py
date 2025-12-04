"""
This module provides utilities for working with simulation results in LEAF-Cloud.

It includes functions for exporting, converting, and analyzing simulation results
using the core types defined in the types module.
"""

import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd
import yaml

# Set up logging
logger = logging.getLogger(__name__)

# Import core types
from ..leaf_types import (
    ResultFormat,
    SimulationStatus
)

# Import RawSimulationResult and related schemas from schemas
from .schemas.results import RawSimulationResult

def _parse_timestamp(value: Any) -> Optional[float]:
    """Attempt to parse a timestamp from various formats."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None

@dataclass
class ResourceMetrics:
    """Metrics for a specific resource with support for dynamic metrics."""
    resource_id: str
    metrics: Dict[str, Any] = field(default_factory=dict)

    analysis_results: Optional[Dict[str, Any]] = field(default_factory=dict)
    timestamps: List[float] = field(default_factory=list)
    values: List[float] = field(default_factory=list)
    resource_type: str = "test_resource"
    region: str = "test_region"
    
    def add_metric(self, name: str, value: float, timestamp: Optional[float] = None) -> None:
        """Add a metric value with an optional timestamp."""
        if timestamp is None:
            timestamp = datetime.now(timezone.utc).timestamp()
        
        if name in self.metrics:
            if isinstance(self.metrics[name], list):
                self.metrics[name].append((timestamp, value))
            else:
                # Convert single value to list format
                self.metrics[name] = [(timestamp, value)]
        else:
            self.metrics[name] = [(timestamp, value)]
        
        self.timestamps.append(timestamp)
        self.values.append(value)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to a dictionary, including dynamic attributes."""
        return self.__dict__.copy()


@dataclass
class SimulationResult:
    """
    A comprehensive container for simulation results and metrics.
    
    This class serves as the canonical representation of simulation results
    across the LEAF-Cloud framework, supporting both simulation execution
    and result analysis workflows.
    """
    # Core metadata
    simulation_id: str = field(default_factory=lambda: f"sim_{datetime.now().timestamp()}")
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    status: SimulationStatus = SimulationStatus.INITIALIZING
    
    # Configuration and environment
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    terraform_dir: Optional[str] = None
    
    # Metrics and results
    resource_metrics: Dict[str, ResourceMetrics] = field(default_factory=dict)
    token_flow_logs: List[Dict[str, Any]] = field(default_factory=list)
    token_traces: List[Dict[str, Any]] = field(default_factory=list)
    raw_results: Dict[str, Any] = field(default_factory=dict)
    raw_model_outputs: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    
    # Additional metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Error information
    error: Optional[Dict[str, Any]] = None
    analysis_results: Optional[Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self):
        """Initialize default values after creation."""
        if self.start_time is None and self.end_time is None:
            now = datetime.now(timezone.utc).timestamp()
            self.start_time = now
            self.end_time = now

    def add_resource_metric(
        self, 
        resource_id: str, 
        metric_name: str, 
        value: Any,
        timestamp: Optional[float] = None
    ) -> None:
        """Add a metric for a specific resource."""
        logger.debug(f"Adding metric {metric_name} to resource {resource_id}")
        
        # Create ResourceMetrics if it doesn't exist
        if resource_id not in self.resource_metrics:
            self.resource_metrics[resource_id] = ResourceMetrics(resource_id=resource_id)
        
        # Add the metric
        self.resource_metrics[resource_id].add_metric(metric_name, value, timestamp)

    def log_token_flow(
        self, 
        event_type: str, 
        details: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None
    ) -> None:
        """Log a token flow event."""
        if timestamp is None:
            timestamp = datetime.now(timezone.utc).timestamp()
        
        self.token_flow_logs.append({
            'timestamp': timestamp,
            'event_type': event_type,
            'details': details or {}
        })

    def to_dict(self) -> Dict[str, Any]:
        """Convert the result to a dictionary."""
        # Build metadata
        metadata = {
            'simulation_id': self.simulation_id,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'duration_seconds': max(0.0, (self.end_time or 0) - (self.start_time or 0)),
            'leaf_cloud_version': '1.0.0',
            'timestamp': datetime.now(timezone.utc).timestamp(),
            'version': '1.0.0',
            'config_hash': self.metadata.get('config_hash', 'unknown'),
            'status': self.status.value if hasattr(self.status, 'value') else str(self.status),
        }
        metadata.update(self.metadata)
        
        # Serialize resource metrics
        resource_metrics = {}
        for rid, rm in self.resource_metrics.items():
            resource_metrics[rid] = {
                'resource_id': rm.resource_id,
                'resource_type': rm.resource_type,
                'region': rm.region,
                'utilization': [],
                'power_draw_w': [],
                'memory_usage_bytes': [],
                'network_in_bps': [],
                'network_out_bps': [],
                'disk_read_bps': [],
                'disk_write_bps': [],
                'tokens_processed': [],
                'request_latency_ms': [],
            }
            
            # Convert timestamps/values to time series format if available
            if rm.timestamps and rm.values:
                time_series_data = [
                    {'timestamp': float(t), 'value': float(v)}
                    for t, v in zip(rm.timestamps, rm.values)
                    if v is not None
                ]
                resource_metrics[rid]['utilization'] = time_series_data
        
        # Build the result
        result = {
            'metadata': metadata,
            'config_snapshot': self.config_snapshot,
            'resource_metrics': resource_metrics,
            'token_flow_logs': self.token_flow_logs,
            'system_events': [],
            'raw_model_outputs': self.raw_model_outputs,
            'schema_version': '1.0.0',
            'status': self.status.value if hasattr(self.status, 'value') else str(self.status)
        }
        
        # Add optional fields
        if self.metrics:
            result['metrics'] = self.metrics
        if self.analysis_results:
            result['analysis_results'] = self.analysis_results
        if self.error:
            result['error'] = self.error
        if self.token_traces:
            result['token_traces'] = self.token_traces
        
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SimulationResult":
        """Create a SimulationResult from a dictionary."""
        # Extract metadata
        metadata = data.get('metadata', {})
        
        # Extract core fields
        simulation_id = metadata.get('simulation_id') or data.get('simulation_id', f"sim_{datetime.now().timestamp()}")
        start_time_val = metadata.get('start_time') or data.get('start_time')
        end_time_val = metadata.get('end_time') or data.get('end_time')
        start_time = _parse_timestamp(start_time_val)
        end_time = _parse_timestamp(end_time_val)
        
        # Convert timestamps if they're datetime objects
        if isinstance(start_time, datetime):
            start_time = start_time.timestamp()
        if isinstance(end_time, datetime):
            end_time = end_time.timestamp()
        
        # Convert status
        status_str = data.get('status', metadata.get('status'))
        status = SimulationStatus.UNKNOWN
        if status_str:
            try:
                status = SimulationStatus(str(status_str).lower())
            except ValueError:
                status = SimulationStatus.UNKNOWN
        
        # Process resource metrics
        resource_metrics = {}
        raw_resource_metrics = data.get('resource_metrics', {})
        
        for res_id, metrics_data in raw_resource_metrics.items():
            if isinstance(metrics_data, dict):
                # Directly initialize ResourceMetrics from the dictionary data
                # This avoids the duplication caused by multiple `add_metric` calls
                resource_metrics[res_id] = ResourceMetrics(
                    resource_id=res_id,
                    resource_type=metrics_data.get("resource_type", "unknown"),
                    region=metrics_data.get("region", "unknown"),
                    metrics=metrics_data.get("metrics", {}),
                    timestamps=metrics_data.get("timestamps", []),
                    values=metrics_data.get("values", []),
                )
                # Handle legacy 'utilization' format
                if not resource_metrics[res_id].timestamps and "utilization" in metrics_data:
                    for point in metrics_data["utilization"]:
                        if isinstance(point, dict) and "timestamp" in point and "value" in point:
                            resource_metrics[res_id].timestamps.append(point["timestamp"])
                            resource_metrics[res_id].values.append(point["value"])
        
        # Construct normalized raw_results.utilization and resource_utilization if not provided
        normalized_util: Dict[str, List[Dict[str, float]]] = {}
        try:
            for res_id, metrics_data in raw_resource_metrics.items():
                series = metrics_data.get('utilization', []) if isinstance(metrics_data, dict) else []
                if not series:
                    continue
                normalized_points: List[Dict[str, float]] = []
                for point in series:
                    if not isinstance(point, dict):
                        continue
                    t = point.get('time') if 'time' in point else point.get('timestamp')
                    v = point.get('value')
                    if t is None or v is None:
                        continue
                    normalized_points.append({'time': float(t), 'value': float(v)})
                if normalized_points:
                    normalized_util[res_id] = normalized_points
        except Exception:
            normalized_util = {}

        # Create the result
        result = cls(
            simulation_id=simulation_id,
            start_time=start_time,
            end_time=end_time,
            status=status,
            config_snapshot=data.get('config_snapshot', {}),
            resource_metrics=resource_metrics,
            token_flow_logs=data.get('token_flow_logs', []),
            token_traces=data.get('token_traces', []),
            raw_results=data.get('raw_results', {}),
            raw_model_outputs=data.get('raw_model_outputs', {}),
            metrics=data.get('metrics', {}),
            analysis_results=data.get('analysis_results', {}),
            metadata=metadata,
            error=data.get('error')
        )

        # Populate raw_results keys for compatibility if missing
        try:
            if 'raw_results' not in data or not isinstance(result.raw_results, dict):
                result.raw_results = {}
            # Do not overwrite if already present
            if 'utilization' not in result.raw_results and normalized_util:
                result.raw_results['utilization'] = normalized_util
            if 'resource_utilization' not in result.raw_results and normalized_util:
                result.raw_results['resource_utilization'] = normalized_util
        except Exception:
            # Leave raw_results as-is on any error
            pass
        
        return result

    def mark_completed(self) -> None:
        """Mark the simulation as completed."""
        self.status = SimulationStatus.COMPLETED
        self.end_time = datetime.now(timezone.utc).timestamp()

    def mark_failed(self, error: Exception) -> None:
        """Mark the simulation as failed with an error."""
        self.status = SimulationStatus.FAILED
        self.end_time = datetime.now(timezone.utc).timestamp()
        self.error = {
            "type": error.__class__.__name__,
            "message": str(error),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    def to_dataframes(self) -> Dict[str, pd.DataFrame]:
        """Convert simulation results to pandas DataFrames."""
        dfs = {}
        
        try:
            # Convert resource metrics to DataFrames
            resource_data = []
            for res_id, metrics in self.resource_metrics.items():
                if metrics.timestamps:
                    for i, timestamp in enumerate(metrics.timestamps):
                        resource_data.append({
                            "resource_id": res_id,
                            "timestamp": timestamp,
                            "value": metrics.values[i],
                            "metric_name": "utilization"
                        })
                elif metrics.metrics:
                    for metric_name, value in metrics.metrics.items():
                        resource_data.append({
                            "resource_id": res_id,
                            "timestamp": self.start_time,
                            "value": value,
                            "metric_name": metric_name
                        })

            if resource_data:
                dfs["resource_metrics"] = pd.DataFrame(resource_data)
            
            # Convert token flow logs to DataFrame
            if self.token_flow_logs:
                flow_data = []
                for log in self.token_flow_logs:
                    flow_data.append({
                        'timestamp': log.get('timestamp'),
                        'event_type': log.get('event_type'),
                        'token_id': log.get('details', {}).get('token_id', ''),
                        'resource_id': log.get('details', {}).get('resource_id', ''),
                        'details': str(log.get('details', {}))
                    })
                
                if flow_data:
                    dfs["token_flow"] = pd.DataFrame(flow_data)
        
        except Exception as e:
            logger.warning(f"Failed to convert results to DataFrames: {e}")
        
        return dfs

    def export(
        self, 
        file_path: str, 
        fmt: Union[str, ResultFormat] = ResultFormat.JSON
    ) -> str:
        """Export the result to a file."""
        try:
            export_results(self, file_path, fmt)
            return file_path
        except Exception as e:
            logger.error(f"Failed to export results to {file_path}: {e}")
            raise

    def to_pydantic(self) -> RawSimulationResult:
        """Convert the dataclass instance to a Pydantic model instance."""
        return RawSimulationResult.model_validate(self.to_dict())

    @classmethod
    def from_orchestrator(
        cls,
        orchestrator: "Orchestrator",
        resource_stats: Dict[str, Dict[str, List[Dict[str, Any]]]],
        token_flow_log: List[Dict[str, Any]],
        completed_tokens: Optional[int] = None,
        active_tokens: Optional[int] = None,
    ) -> "SimulationResult":
        """Create a SimulationResult from an Orchestrator instance."""
        if not orchestrator:
            raise ValueError("Orchestrator instance is required")
        
        # Create metadata
        metadata = {
            'simulation_id': f"sim_{int(datetime.now().timestamp())}",
            'start_time': getattr(orchestrator, 'start_time', 0),
            'end_time': getattr(orchestrator, 'end_time', 0),
            'duration_seconds': getattr(orchestrator, 'simulation_duration', 0),
            'leaf_cloud_version': '1.0.0',
            'config_hash': getattr(orchestrator, 'config_hash', 'unknown'),
        }
        
        # Process resource metrics
        resource_metrics = {}
        resource_info = {}
        
        # Get resource information from orchestrator
        if hasattr(orchestrator, 'model_builder') and hasattr(orchestrator.model_builder, 'resource_mapping'):
            resource_info = {
                res_id: {
                    'resource_type': getattr(resource, 'resource_type', 'unknown'),
                    'region': getattr(resource, 'region', 'unknown')
                }
                for res_id, resource in orchestrator.model_builder.resource_mapping.items()
            }
        
        # Convert resource stats to ResourceMetrics
        for res_id, stats in resource_stats.items():
            info = resource_info.get(res_id, {})
            
            metrics = ResourceMetrics(
                resource_id=res_id,
                resource_type=info.get('resource_type', 'unknown'),
                region=info.get('region', 'unknown')
            )
            
            # Add utilization data
            if 'utilization' in stats:
                for item in stats['utilization']:
                    metrics.add_metric('utilization', item['value'], item['time'])
            
            resource_metrics[res_id] = metrics
        
        # Store token counts
        token_counts = {}
        if completed_tokens is not None:
            token_counts["completed_tokens"] = completed_tokens
        if active_tokens is not None:
            token_counts["active_tokens"] = active_tokens
        
        # Create result
        result = cls(
            metadata=metadata,
            resource_metrics=resource_metrics,
            token_traces=token_flow_log,
            config_snapshot=orchestrator.config.to_dict() if hasattr(orchestrator, 'config') else {},
            raw_model_outputs={"token_counts": token_counts},
            raw_results={
                # Provide both keys for compatibility with analysis models
                "utilization": {
                    res_id: stats.get("utilization", [])
                    for res_id, stats in resource_stats.items()
                },
                "resource_utilization": {
                    res_id: stats.get("utilization", [])
                    for res_id, stats in resource_stats.items()
                }
            }
        )
        
        return result


def export_results(
    results: Union[SimulationResult, Dict[str, Any]], 
    output_path: Union[str, Path], 
    format: Union[str, ResultFormat] = ResultFormat.JSON
) -> None:
    """
    Export simulation results to a file.
    
    Args:
        results: Simulation results to export
        output_path: Path to the output file
        format: Output format (json, yaml, csv, parquet)
        
    Raises:
        ValueError: If the format is not supported
        IOError: If there's an error writing to the file
    """
    if isinstance(format, str):
        try:
            format = ResultFormat(format.lower())
        except ValueError:
            raise ValueError(f"Unsupported export format: {format}")
    
    output_path = Path(output_path)
    
    # Convert to SimulationResult if needed
    if isinstance(results, dict):
        results = SimulationResult.from_dict(results)
    
    # Export based on format
    if format == ResultFormat.JSON:
        with open(output_path, 'w') as f:
            json.dump(results.to_dict(), f, indent=2)
    elif format == ResultFormat.YAML:
        with open(output_path, 'w') as f:
            yaml.dump(results.to_dict(), f, sort_keys=False)
    elif format in (ResultFormat.CSV, ResultFormat.PARQUET):
        dfs = results.to_dataframes()

        if format == ResultFormat.CSV:
            output_path.mkdir(parents=True, exist_ok=True)

            # Export main summary
            summary_df = pd.DataFrame([{
                'simulation_id': results.simulation_id,
                'start_time': results.start_time,
                'end_time': results.end_time,
                'status': results.status.value,
                'error': results.error is not None,
                **results.metrics
            }])
            summary_df.to_csv(output_path / "simulation_summary.csv", index=False)

            # Export other dataframes
            if 'resource_metrics' in dfs:
                dfs['resource_metrics'].to_csv(output_path / "resource_metrics.csv", index=False)
            if 'token_flow' in dfs:
                dfs['token_flow'].to_csv(output_path / "token_flow_logs.csv", index=False)
        
        elif format == ResultFormat.PARQUET:
            # For Parquet, we can save all tables in a single file if needed, or separate
            # Here we save the main summary. A more complex setup could use partitions.
            summary_df = pd.DataFrame([{
                'simulation_id': results.simulation_id,
                'start_time': results.start_time,
                'end_time': results.end_time,
                'status': results.status.value,
                'error': results.error is not None,
                **results.metrics
            }])
            summary_df.to_parquet(output_path, index=False)
    else:
        raise ValueError(f"Unsupported export format: {format}")


def load_results(file_path: Union[str, Path]) -> SimulationResult:
    """
    Load simulation results from a file.
    
    Args:
        file_path: Path to the results file
        
    Returns:
        Loaded SimulationResult instance
        
    Raises:
        ValueError: If the file format is not supported
        IOError: If there's an error reading the file
    """
    file_path = Path(file_path)
    
    if file_path.suffix.lower() == '.json':
        with open(file_path, 'r') as f:
            data = json.load(f)
    elif file_path.suffix.lower() in ('.yaml', '.yml'):
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
    else:
        raise ValueError(f"Unsupported file format: {file_path.suffix}")
    
    return SimulationResult.from_dict(data)


# Re-export for backward compatibility
SimulationResults = SimulationResult