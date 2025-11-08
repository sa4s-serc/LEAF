import logging
import statistics
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field
import json
import csv
from datetime import datetime
from pathlib import Path
from ..core.resource import Resource, ResourceState, UtilizationRecord
from ..core.workload import WorkloadStatistics

import matplotlib.pyplot as plt


"""
models/aggregation.py

This module handles the aggregation of simulation results, statistical analysis,
and report generation for the LEAF-Cloud framework. It consolidates data from multiple
simulation runs to provide comprehensive metrics about resource utilization, performance,
and environmental impact.
"""

logger = logging.getLogger(__name__)

@dataclass
class MetricSummary:
    """Statistical summary of a single metric across multiple simulation runs."""
    name: str
    mean: float
    median: float
    min_value: float
    max_value: float
    variance: float
    std_dev: float
    percentiles: Dict[int, float] = field(default_factory=dict)
    samples: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the metric summary to a dictionary."""
        return {
            "name": self.name,
            "mean": self.mean,
            "median": self.median,
            "min": self.min_value,
            "max": self.max_value,
            "variance": self.variance,
            "std_dev": self.std_dev,
            "percentiles": self.percentiles,
            "sample_count": self.samples
        }


@dataclass
class ResourceMetrics:
    """Aggregated metrics for a single resource across simulation runs."""
    resource_id: str
    resource_name: str
    resource_type: str
    utilization: MetricSummary
    energy_consumption: MetricSummary
    carbon_footprint: Optional[MetricSummary] = None
    allocation_time: Optional[MetricSummary] = None
    additional_metrics: Dict[str, MetricSummary] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the resource metrics to a dictionary."""
        result = {
            "resource_id": self.resource_id,
            "resource_name": self.resource_name,
            "resource_type": self.resource_type,
            "utilization": self.utilization.to_dict(),
            "energy_consumption": self.energy_consumption.to_dict(),
        }
        
        if self.carbon_footprint:
            result["carbon_footprint"] = self.carbon_footprint.to_dict()
        
        if self.allocation_time:
            result["allocation_time"] = self.allocation_time.to_dict()
        
        if self.additional_metrics:
            result["additional_metrics"] = {
                name: metric.to_dict() for name, metric in self.additional_metrics.items()
            }
        
        return result


@dataclass
class WorkloadMetrics:
    """Aggregated metrics for a workload across simulation runs."""
    workload_name: str
    token_count: MetricSummary
    arrival_rate: MetricSummary
    processing_time: Optional[MetricSummary] = None
    latency: Optional[MetricSummary] = None
    completion_rate: Optional[MetricSummary] = None
    additional_metrics: Dict[str, MetricSummary] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the workload metrics to a dictionary."""
        result = {
            "workload_name": self.workload_name,
            "token_count": self.token_count.to_dict(),
            "arrival_rate": self.arrival_rate.to_dict(),
        }
        
        if self.processing_time:
            result["processing_time"] = self.processing_time.to_dict()
        
        if self.latency:
            result["latency"] = self.latency.to_dict()
        
        if self.completion_rate:
            result["completion_rate"] = self.completion_rate.to_dict()
        
        if self.additional_metrics:
            result["additional_metrics"] = {
                name: metric.to_dict() for name, metric in self.additional_metrics.items()
            }
        
        return result


@dataclass
class SimulationSummary:
    """Overall summary of aggregated simulation results."""
    run_count: int
    total_simulation_time: MetricSummary
    system_metrics: Dict[str, MetricSummary] = field(default_factory=dict)
    resource_metrics: Dict[str, ResourceMetrics] = field(default_factory=dict)
    workload_metrics: Dict[str, WorkloadMetrics] = field(default_factory=dict)
    execution_time: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the simulation summary to a dictionary."""
        return {
            "run_count": self.run_count,
            "total_simulation_time": self.total_simulation_time.to_dict(),
            "system_metrics": {
                name: metric.to_dict() for name, metric in self.system_metrics.items()
            },
            "resource_metrics": {
                r_id: metrics.to_dict() for r_id, metrics in self.resource_metrics.items()
            },
            "workload_metrics": {
                w_name: metrics.to_dict() for w_name, metrics in self.workload_metrics.items()
            },
            "execution_time": self.execution_time,
            "timestamp": self.timestamp
        }


class MetricAggregator:
    """
    Aggregates and analyzes metrics from multiple simulation runs.
    Calculates statistical properties and generates summary reports.
    """
    
    def __init__(self):
        """Initialize a new metric aggregator."""
        self.run_results = []
        self.resource_data = {}  # Dictionary mapping resource ID to list of metrics
        self.workload_data = {}  # Dictionary mapping workload name to list of metrics
        self.system_metrics = {}  # Dictionary mapping metric name to list of values
        
    def add_simulation_run(self, run_results: Dict[str, Any]) -> None:
        """
        Add results from a single simulation run to the aggregator.
        
        Args:
            run_results: Dictionary containing results from a simulation run
        """
        self.run_results.append(run_results)
        
        # Extract and organize resource metrics
        resources = run_results.get('resources', {})
        for r_id, r_data in resources.items():
            if r_id not in self.resource_data:
                self.resource_data[r_id] = []
            self.resource_data[r_id].append(r_data)
        
        # Extract and organize workload metrics
        workloads = run_results.get('workloads', {})
        for w_name, w_data in workloads.items():
            if w_name not in self.workload_data:
                self.workload_data[w_name] = []
            self.workload_data[w_name].append(w_data)
        
        # Extract and organize system-level metrics
        system = run_results.get('system', {})
        for metric_name, value in system.items():
            if metric_name not in self.system_metrics:
                self.system_metrics[metric_name] = []
            self.system_metrics[metric_name].append(value)
            
        logger.info(f"Added simulation run to aggregator. Total runs: {len(self.run_results)}")
    
    def _calculate_metric_summary(self, values: List[float], name: str) -> MetricSummary:
        """
        Calculate statistical properties for a list of values.
        
        Args:
            values: List of numeric values to analyze
            name: Name of the metric
            
        Returns:
            MetricSummary containing statistical properties
        """
        if not values:
            logger.warning(f"No values provided for metric: {name}")
            return MetricSummary(
                name=name,
                mean=0.0,
                median=0.0,
                min_value=0.0,
                max_value=0.0,
                variance=0.0,
                std_dev=0.0,
                samples=0
            )
        
        values = [float(v) for v in values if v is not None]
        
        # Calculate basic statistics
        mean_val = statistics.mean(values)
        median_val = statistics.median(values)
        min_val = min(values)
        max_val = max(values)
        
        # Calculate variance and standard deviation
        variance_val = statistics.variance(values) if len(values) > 1 else 0.0
        std_dev_val = statistics.stdev(values) if len(values) > 1 else 0.0
        
        # Calculate percentiles
        percentiles = {}
        for p in [10, 25, 50, 75, 90, 95, 99]:
            percentiles[p] = np.percentile(values, p)
            
        return MetricSummary(
            name=name,
            mean=mean_val,
            median=median_val,
            min_value=min_val,
            max_value=max_val,
            variance=variance_val,
            std_dev=std_dev_val,
            percentiles=percentiles,
            samples=len(values)
        )
    
    def aggregate_resource_metrics(self) -> Dict[str, ResourceMetrics]:
        """
        Aggregate metrics for all resources across simulation runs.
        
        Returns:
            Dictionary mapping resource IDs to aggregated ResourceMetrics
        """
        result = {}
        
        for r_id, data_list in self.resource_data.items():
            if not data_list:
                continue
                
            # Extract common resource properties
            resource_name = data_list[0].get('name', r_id)
            resource_type = data_list[0].get('type', 'unknown')
            
            # Extract utilization data across runs
            utilization_values = [d.get('utilization', 0.0) for d in data_list]
            utilization_summary = self._calculate_metric_summary(utilization_values, "utilization")
            
            # Extract energy consumption data across runs
            energy_values = [d.get('energy_consumption', 0.0) for d in data_list]
            energy_summary = self._calculate_metric_summary(energy_values, "energy_consumption")
            
            # Create the resource metrics object
            resource_metrics = ResourceMetrics(
                resource_id=r_id,
                resource_name=resource_name,
                resource_type=resource_type,
                utilization=utilization_summary,
                energy_consumption=energy_summary
            )
            
            # Add carbon footprint metrics if available
            if any('carbon_footprint' in d for d in data_list):
                carbon_values = [d.get('carbon_footprint', 0.0) for d in data_list]
                carbon_summary = self._calculate_metric_summary(carbon_values, "carbon_footprint")
                resource_metrics.carbon_footprint = carbon_summary
            
            # Add allocation time metrics if available
            if any('allocation_time' in d for d in data_list):
                alloc_values = [d.get('allocation_time', 0.0) for d in data_list]
                alloc_summary = self._calculate_metric_summary(alloc_values, "allocation_time")
                resource_metrics.allocation_time = alloc_summary
            
            result[r_id] = resource_metrics
            
        return result
    
    def aggregate_workload_metrics(self) -> Dict[str, WorkloadMetrics]:
        """
        Aggregate metrics for all workloads across simulation runs.
        
        Returns:
            Dictionary mapping workload names to aggregated WorkloadMetrics
        """
        result = {}
        
        for w_name, data_list in self.workload_data.items():
            if not data_list:
                continue
            
            # Extract token count data across runs
            token_values = [d.get('total_tokens', 0) for d in data_list]
            token_summary = self._calculate_metric_summary(token_values, "token_count")
            
            # Extract arrival rate data across runs
            rate_values = [d.get('avg_rate', 0.0) for d in data_list]
            rate_summary = self._calculate_metric_summary(rate_values, "arrival_rate")
            
            # Create the workload metrics object
            workload_metrics = WorkloadMetrics(
                workload_name=w_name,
                token_count=token_summary,
                arrival_rate=rate_summary
            )
            
            # Add processing time metrics if available
            if any('processing_time' in d for d in data_list):
                proc_values = [d.get('processing_time', 0.0) for d in data_list]
                proc_summary = self._calculate_metric_summary(proc_values, "processing_time")
                workload_metrics.processing_time = proc_summary
            
            # Add latency metrics if available
            if any('latency' in d for d in data_list):
                latency_values = [d.get('latency', 0.0) for d in data_list]
                latency_summary = self._calculate_metric_summary(latency_values, "latency")
                workload_metrics.latency = latency_summary
            
            # Add completion rate metrics if available
            if any('completion_rate' in d for d in data_list):
                comp_values = [d.get('completion_rate', 0.0) for d in data_list]
                comp_summary = self._calculate_metric_summary(comp_values, "completion_rate")
                workload_metrics.completion_rate = comp_summary
            
            result[w_name] = workload_metrics
            
        return result
    
    def aggregate_system_metrics(self) -> Dict[str, MetricSummary]:
        """
        Aggregate system-level metrics across simulation runs.
        
        Returns:
            Dictionary mapping metric names to MetricSummary objects
        """
        result = {}
        
        for metric_name, values in self.system_metrics.items():
            summary = self._calculate_metric_summary(values, metric_name)
            result[metric_name] = summary
            
        return result
    
    def generate_summary(self) -> SimulationSummary:
        """
        Generate a comprehensive summary of simulation results.
        
        Returns:
            SimulationSummary object containing aggregated metrics
        """
        # Calculate simulation time statistics
        sim_time_values = [r.get('simulation_time', 0.0) for r in self.run_results]
        sim_time_summary = self._calculate_metric_summary(sim_time_values, "simulation_time")
        
        # Aggregate all metrics
        resource_metrics = self.aggregate_resource_metrics()
        workload_metrics = self.aggregate_workload_metrics()
        system_metrics = self.aggregate_system_metrics()
        
        # Create and return the summary
        summary = SimulationSummary(
            run_count=len(self.run_results),
            total_simulation_time=sim_time_summary,
            system_metrics=system_metrics,
            resource_metrics=resource_metrics,
            workload_metrics=workload_metrics
        )
        
        logger.info(f"Generated summary from {len(self.run_results)} simulation runs")
        return summary
    
    def export_summary_to_json(self, summary: SimulationSummary, filepath: str) -> None:
        """
        Export a simulation summary to a JSON file.
        
        Args:
            summary: The SimulationSummary to export
            filepath: Path to save the JSON file
        """
        with open(filepath, 'w') as f:
            json.dump(summary.to_dict(), f, indent=2)
        logger.info(f"Exported simulation summary to {filepath}")
    
    def export_summary_to_csv(self, summary: SimulationSummary, directory: str) -> None:
        """
        Export a simulation summary to CSV files (one per metric type).
        
        Args:
            summary: The SimulationSummary to export
            directory: Directory to save the CSV files
        """
        dir_path = Path(directory)
        dir_path.mkdir(parents=True, exist_ok=True)
        
        # Export resource metrics
        resource_data = []
        for r_id, metrics in summary.resource_metrics.items():
            row = {
                'resource_id': r_id,
                'resource_name': metrics.resource_name,
                'resource_type': metrics.resource_type,
                'mean_utilization': metrics.utilization.mean,
                'mean_energy': metrics.energy_consumption.mean
            }
            if metrics.carbon_footprint:
                row['mean_carbon'] = metrics.carbon_footprint.mean
            resource_data.append(row)
        
        if resource_data:
            df = pd.DataFrame(resource_data)
            df.to_csv(dir_path / 'resource_metrics.csv', index=False)
        
        # Export workload metrics
        workload_data = []
        for w_name, metrics in summary.workload_metrics.items():
            row = {
                'workload_name': w_name,
                'mean_token_count': metrics.token_count.mean,
                'mean_arrival_rate': metrics.arrival_rate.mean
            }
            if metrics.latency:
                row['mean_latency'] = metrics.latency.mean
            workload_data.append(row)
        
        if workload_data:
            df = pd.DataFrame(workload_data)
            df.to_csv(dir_path / 'workload_metrics.csv', index=False)
        
        # Export system metrics
        system_data = []
        for metric_name, summary_metric in summary.system_metrics.items():
            row = {
                'metric_name': metric_name,
                'mean': summary_metric.mean,
                'median': summary_metric.median,
                'min': summary_metric.min_value,
                'max': summary_metric.max_value,
                'std_dev': summary_metric.std_dev
            }
            system_data.append(row)
        
        if system_data:
            df = pd.DataFrame(system_data)
            df.to_csv(dir_path / 'system_metrics.csv', index=False)
            
        logger.info(f"Exported simulation summary CSVs to {directory}")
    
    def generate_visualization(self, summary: SimulationSummary, directory: str) -> None:
        """
        Generate visualization charts for the simulation summary.
        
        Args:
            summary: The SimulationSummary to visualize
            directory: Directory to save the visualization files
        """
        dir_path = Path(directory)
        dir_path.mkdir(parents=True, exist_ok=True)
        
        # Plot resource utilization
        plt.figure(figsize=(12, 6))
        resources = list(summary.resource_metrics.values())
        if resources:
            names = [r.resource_name[:15] for r in resources]  # Truncate long names
            values = [r.utilization.mean for r in resources]
            
            plt.bar(names, values)
            plt.title('Mean Resource Utilization')
            plt.xlabel('Resource')
            plt.ylabel('Utilization Ratio')
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            plt.savefig(dir_path / 'resource_utilization.png')
            
        # Plot energy consumption
        plt.figure(figsize=(12, 6))
        if resources:
            names = [r.resource_name[:15] for r in resources]  # Truncate long names
            values = [r.energy_consumption.mean for r in resources]
            
            plt.bar(names, values)
            plt.title('Mean Energy Consumption')
            plt.xlabel('Resource')
            plt.ylabel('Energy Units')
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            plt.savefig(dir_path / 'energy_consumption.png')
        
        # Plot workload metrics if available
        workloads = list(summary.workload_metrics.values())
        if workloads and any(w.latency for w in workloads):
            plt.figure(figsize=(10, 6))
            names = [w.workload_name for w in workloads if w.latency]
            values = [w.latency.mean for w in workloads if w.latency]
            
            plt.bar(names, values)
            plt.title('Mean Workload Latency')
            plt.xlabel('Workload')
            plt.ylabel('Latency (ms)')
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            plt.savefig(dir_path / 'workload_latency.png')
        
        logger.info(f"Generated visualization charts in {directory}")


def combine_simulation_results(results_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Combine multiple simulation result dictionaries into a single aggregated result.
    
    Args:
        results_list: List of simulation result dictionaries to combine
        
    Returns:
        Dictionary containing the aggregated results
    """
    if not results_list:
        return {}
    
    aggregator = MetricAggregator()
    for result in results_list:
        aggregator.add_simulation_run(result)
    
    summary = aggregator.generate_summary()
    return summary.to_dict()


def generate_report(simulation_results: Dict[str, Any], 
                   output_format: str = 'json',
                   output_path: str = None) -> Optional[str]:
    """
    Generate a formatted report from simulation results.
    
    Args:
        simulation_results: Dictionary containing simulation results
        output_format: Format for the output ('json', 'csv', or 'both')
        output_path: Path to save the report (directory for CSV, file for JSON)
        
    Returns:
        Path to the generated report file(s) or None if no output path provided
    """
    if not simulation_results:
        logger.warning("No simulation results to generate report from")
        return None
    
    # Create an aggregator and generate a summary
    aggregator = MetricAggregator()
    aggregator.add_simulation_run(simulation_results)
    summary = aggregator.generate_summary()
    
    if not output_path:
        logger.warning("No output path provided for report generation")
        return None
    
    # Export in the requested format(s)
    if output_format == 'json' or output_format == 'both':
        json_path = output_path
        if output_format == 'both':
            json_path = Path(output_path) / 'summary.json'
        aggregator.export_summary_to_json(summary, json_path)
        
    if output_format == 'csv' or output_format == 'both':
        csv_dir = output_path
        if output_format != 'csv':
            csv_dir = Path(output_path) / 'csv_reports'
        aggregator.export_summary_to_csv(summary, csv_dir)
    
    # Generate visualizations if possible
    try:
        vis_dir = output_path
        if not (output_format == 'csv' and not isinstance(output_path, Path)):
            vis_dir = Path(output_path) / 'visualizations'
        aggregator.generate_visualization(summary, vis_dir)
    except Exception as e:
        logger.warning(f"Failed to generate visualizations: {e}")
    
    return output_path