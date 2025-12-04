"""
Reporting and export functionality for simulation results.

This module provides utilities for generating reports and exporting simulation results
in various formats (JSON, CSV, etc.).
"""
import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from leaf_cloud.utils.results import SimulationResult

logger = logging.getLogger(__name__)

class ReportGenerator:
    """Generates reports and exports simulation results in various formats."""
    
    def __init__(self, result: SimulationResult):
        """
        Initialize the report generator with simulation results.
        
        Args:
            result: The simulation result to generate reports from.
        """
        self.result = result
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the simulation result to a dictionary."""
        return {
            "metadata": self._get_metadata(),
            "metrics": self.result.metrics,
            "token_stats": self.result.token_stats,
            "errors": self.result.errors,
            "config": self.result.config,
        }
    
    def to_json(self, pretty: bool = True) -> str:
        """
        Convert the simulation result to a JSON string.
        
        Args:
            pretty: Whether to format the JSON with indentation.
            
        Returns:
            The simulation result as a JSON string.
        """
        indent = 2 if pretty else None
        return json.dumps(
            self.to_dict(),
            indent=indent,
            default=self._json_serializer,
            ensure_ascii=False
        )
    
    def to_csv(self, output_dir: Union[str, Path], prefix: str = "simulation") -> List[Path]:
        """
        Export simulation results to CSV files.
        
        Args:
            output_dir: Directory to save CSV files to.
            prefix: Prefix for output filenames.
            
        Returns:
            List of paths to generated CSV files.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        files = []
        
        # Export token flow logs (support both legacy and canonical attributes)
        logs = getattr(self.result, "token_flow_log", None) or getattr(self.result, "token_flow_logs", None)
        if logs:
            flow_log_path = output_dir / f"{prefix}_{timestamp}_token_flow.csv"
            # Derive fieldnames from data keys, with a sensible default order
            try:
                keys = set()
                for item in logs:
                    if isinstance(item, dict):
                        keys.update(item.keys())
                    else:
                        # Pydantic model or object-like
                        keys.update([
                            k for k in ("timestamp", "event_type", "details", "time", "event")
                            if hasattr(item, k)
                        ])
                preferred_order = ["timestamp", "event_type", "time", "event", "token_id", "source", "target", "details"]
                fieldnames = [k for k in preferred_order if k in keys] or list(keys) or ["timestamp", "event_type", "details"]
            except Exception:
                fieldnames = ["timestamp", "event_type", "details"]
            self._export_to_csv(logs, flow_log_path, fieldnames=fieldnames)
            files.append(flow_log_path)
        
        # Export metrics
        if self.result.metrics:
            metrics_path = output_dir / f"{prefix}_{timestamp}_metrics.csv"
            self._export_metrics_to_csv(metrics_path)
            files.append(metrics_path)
        
        # Export errors
        if self.result.errors:
            errors_path = output_dir / f"{prefix}_{timestamp}_errors.csv"
            self._export_to_csv(
                self.result.errors,
                errors_path,
                fieldnames=["time", "event_type", "message", "details"]
            )
            files.append(errors_path)
        
        return files
    
    def save_report(
        self,
        output_dir: Union[str, Path],
        prefix: str = "simulation",
        formats: Optional[List[str]] = None
    ) -> List[Path]:
        """
        Save simulation report in the specified formats.
        
        Args:
            output_dir: Directory to save report files to.
            prefix: Prefix for output filenames.
            formats: List of formats to export (json, csv). Defaults to all.
            
        Returns:
            List of paths to generated report files.
        """
        if formats is None:
            formats = ["json", "csv"]
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        files = []
        
        if "json" in formats:
            json_path = output_dir / f"{prefix}_{timestamp}_report.json"
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(
                    self.to_dict(),
                    f,
                    indent=2,
                    default=self._json_serializer,
                    ensure_ascii=False
                )
            files.append(json_path)
        
        if "csv" in formats:
            csv_files = self.to_csv(output_dir, prefix)
            files.extend(csv_files)
        
        return files
    
    def _get_metadata(self) -> Dict[str, Any]:
        """Generate metadata for the simulation report."""
        return {
            "simulation_id": self.result.config.get("simulation.id", ""),
            "start_time": self._format_datetime(self.result.start_time),
            "end_time": self._format_datetime(self.result.end_time),
            "wall_clock_duration": self.result.wall_clock_duration,
            "simulation_duration": self.result.simulation_duration,
            "state": self.result.state.name if self.result.state else "UNKNOWN",
            "total_events": len(self.result.token_flow_log),
            "total_errors": len(self.result.errors),
        }
    
    def _export_to_csv(
        self,
        data: List[Dict[str, Any]],
        output_path: Path,
        fieldnames: Optional[List[str]] = None
    ) -> None:
        """Export data to a CSV file."""
        if not data:
            return
        
        if fieldnames is None:
            fieldnames = list(data[0].keys())
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(data)
    
    def _export_metrics_to_csv(self, output_path: Path) -> None:
        """Export metrics to a CSV file."""
        if not self.result.metrics:
            return
        
        # Flatten nested metrics
        rows = []
        for metric_name, metric_data in self.result.metrics.items():
            if isinstance(metric_data, dict):
                for sub_name, value in metric_data.items():
                    rows.append({
                        "metric": f"{metric_name}.{sub_name}",
                        "value": value
                    })
            else:
                rows.append({
                    "metric": metric_name,
                    "value": metric_data
                })
        
        self._export_to_csv(rows, output_path, fieldnames=["metric", "value"])
    
    @staticmethod
    def _json_serializer(obj: Any) -> Any:
        """Custom JSON serializer for objects not serializable by default."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, 'name') and isinstance(obj.name, str):
            return obj.name
        if hasattr(obj, 'value') and not isinstance(obj, (str, bytes, bytearray)):
            return obj.value
        if hasattr(obj, '__dict__'):
            return obj.__dict__
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    
    @staticmethod
    def _format_datetime(dt: Optional[datetime]) -> str:
        """Format a datetime object as an ISO 8601 string."""
        if dt is None:
            return ""
        return dt.isoformat()
