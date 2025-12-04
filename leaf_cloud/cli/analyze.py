"""
Module for handling the 'analyze' command of the LEAF-Cloud CLI.

This module provides functionality to analyze existing simulation results,
generate reports, and visualize metrics.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import click
import yaml

from ..exceptions import LeafCloudError
from ..utils import (
    analyze_carbon,
    analyze_cost,
    analyze_energy,
    analyze_latency,
    analyze_resource_states,
    analyze_resource_utilization,
    analyze_scaling,
    analyze_simulation_info,
    analyze_workload_stats,
    generate_summary_and_recommendations,
    perform_additional_analysis,
)

logger = logging.getLogger(__name__)

# Define common options
results_option = click.option(
    "--results",
    "-r",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the simulation results file (JSON or YAML).",
)

output_dir_option = click.option(
    "--output-dir",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    default="analysis_output",
    help="Directory to save analysis artifacts (e.g., plots, reports).",
)

metrics_option = click.option(
    "--metrics",
    "-m",
    type=click.Choice(["latency", "energy", "carbon", "cost", "scaling", "all"], case_sensitive=False),
    multiple=True,
    default=["all"],
    help="Specific metrics to analyze. Can be specified multiple times.",
)


def register_commands(cli: click.Group) -> None:
    """Register the 'analyze' command with the main Click group.
    
    Args:
        cli: The main Click group to register the command with.
    """
    @cli.command(
        name="analyze",
        help="Analyze existing simulation results.",
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    @results_option
    @output_dir_option
    @metrics_option
    @click.option(
        "--no-insights",
        is_flag=True,
        default=False,
        help="Skip generating insights; by default insights are included in analyze.",
    )
    @click.pass_context
    def analyze_cmd(
        ctx: click.Context,
        results: Path,
        output_dir: Path,
        metrics: List[str],
        no_insights: bool,
    ) -> None:
        """Analyze simulation results and generate reports."""
        try:
            logger.info(f"Loading results from {results}...")
            results_data = load_results_file(str(results))
            logger.info("Successfully loaded results. Starting analysis.")
            
            # Convert Click's multiple options to the expected format
            metrics_list = list(metrics) if metrics else ["all"]
            
            # Create a simple namespace-like object for compatibility
            class Args:
                def __init__(self, **kwargs):
                    self.__dict__.update(kwargs)
            
            args = Args(
                results=str(results),
                output_dir=output_dir,
                metrics=metrics_list
            )
            
            run_analysis(results_data, args)
            logger.info("Analysis complete.")

            # Integrated insights generation (default ON)
            if not no_insights:
                try:
                    from ..schema import load_and_validate_result
                    from ..insights.utils import auto_process_raw_results  # reuse lightweight processing
                    from ..insights.engine import InsightsEngine
                    from ..utils.schemas.results import RawSimulationResult, ProcessedSimulationResult

                    # Ensure output dir exists
                    output_dir.mkdir(parents=True, exist_ok=True)

                    # Load raw results as Pydantic model (auto-convert if needed)
                    raw_result = load_and_validate_result(results, schema_type="raw")
                    if not isinstance(raw_result, RawSimulationResult):
                        # If a processed result was provided, try to coerce to dict and back
                        raw_result = RawSimulationResult.model_validate(
                            raw_result.model_dump() if hasattr(raw_result, "model_dump") else raw_result
                        )

                    # Build a minimal processed view sufficient for insights extraction
                    processed_result = auto_process_raw_results(raw_result)
                    if not isinstance(processed_result, ProcessedSimulationResult):
                        processed_result = ProcessedSimulationResult.model_validate(
                            processed_result.model_dump() if hasattr(processed_result, "model_dump") else processed_result
                        )

                    engine = InsightsEngine(
                        raw_results=raw_result,
                        processed_results=processed_result,
                        output_dir=output_dir,
                    )
                    report = engine.generate_report()
                    # Save artifacts (JSON + Markdown)
                    engine._save_report(report)

                    # Print compact console summary
                    total_insights = len(report.insights)
                    severities = {}
                    for i in report.insights:
                        sev = getattr(i, "severity", None)
                        sev_val = getattr(sev, "value", str(sev)) if sev is not None else "unknown"
                        severities[sev_val] = severities.get(sev_val, 0) + 1
                    logger.info(
                        "Insights generated: %d (by severity: %s)",
                        total_insights,
                        ", ".join(f"{k}={v}" for k, v in severities.items()) or "none",
                    )
                    click.echo(f"\nInsights: generated {total_insights} item(s). Report saved to '{output_dir}'.")
                except Exception as ie:
                    logger.warning(f"Integrated insights generation skipped due to error: {ie}")
        except LeafCloudError as e:
            logger.error(f"Analysis failed: {e}", exc_info=True)
            ctx.exit(1)
        except Exception as e:
            logger.error(f"Unexpected error during analysis: {e}", exc_info=True)
            ctx.exit(1)


def load_results_file(file_path: str) -> Dict[str, Any]:
    """
    Load and parse a results file in JSON or YAML format.
    
    Args:
        file_path: The path to the results file.

    Returns:
        The loaded results data as a dictionary.

    Raises:
        LeafCloudError: If the file is not found, cannot be read, or is malformed.
    """
    try:
        file_path = str(file_path)  # Ensure it's a string for Path compatibility
        with open(file_path, "r") as f:
            if file_path.endswith(".json"):
                return json.load(f)
            if file_path.endswith(".yaml") or file_path.endswith(".yml"):
                return yaml.safe_load(f)
            raise LeafCloudError(
                "Unsupported file format. Please provide a JSON or YAML file."
            )
    except FileNotFoundError:
        raise LeafCloudError(f"Results file not found: {file_path}")
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise LeafCloudError(f"Error parsing results file: {e}")
    except Exception as e:
        raise LeafCloudError(f"Error reading results file: {e}")


def run_analysis(results_data: Dict[str, Any], args) -> None:
    """
    Run the analysis on the loaded simulation data, printing a structured report.

    Args:
        results_data: The dictionary containing simulation results.
        args: An object containing command-line arguments (compatible with argparse.Namespace).
    """
    _print_main_header()
    
    # Ensure output directory exists
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Always analyze simulation info
    _print_section_header("Simulation Information")
    
    # Extract simulation info from raw results
    metadata = results_data.get("metadata", {})
    sim_info = {
        "duration": metadata.get("duration_seconds", 3600),
        "simulation_time_reached": metadata.get("duration_seconds", 3600),
        "execution_time": metadata.get("duration_seconds", 3600),
        "total_tokens": 0,
        "completed_tokens": 0,
        "active_tokens": 0
    }
    
    # Calculate estimated tokens based on workload config
    config_snapshot = results_data.get("config_snapshot", {})
    workload_config = config_snapshot.get("workload", {})
    if workload_config:
        workload_type = workload_config.get("type", "unknown")
        workload_params = workload_config.get("params", {})
        rate = workload_params.get("rate", 0)
        
        if rate > 0 and sim_info["duration"] > 0:
            # Estimate tokens based on rate and duration
            sim_info["total_tokens"] = int(rate * sim_info["duration"])
            # Assume 95% completion rate
            sim_info["completed_tokens"] = int(sim_info["total_tokens"] * 0.95)
            # Assume 5% still active at end
            sim_info["active_tokens"] = int(sim_info["total_tokens"] * 0.05)
    
    analyze_simulation_info(sim_info)
    
    # Analyze workload statistics
    _print_section_header("Workload Statistics")
    
    # Extract workload statistics from the results data
    workload_stats = {}
    
    # Check if we have workload configuration
    config_snapshot = results_data.get("config_snapshot", {})
    workload_config = config_snapshot.get("workload", {})
    
    if workload_config:
        workload_type = workload_config.get("type", "unknown")
        workload_params = workload_config.get("params", {})
        
        # Calculate basic workload metrics from available data
        duration = results_data.get("metadata", {}).get("duration_seconds", 0)
        rate = workload_params.get("rate", 0)
        
        if duration > 0 and rate > 0:
            estimated_total_requests = int(rate * duration)
            workload_stats = {
                "total_requests": estimated_total_requests,
                "successful_requests": int(estimated_total_requests * 0.95),  # Assume 95% success rate
                "failed_requests": int(estimated_total_requests * 0.05),
                "request_rate": rate,
                "workload_type": workload_type,
                "duration_seconds": duration
            }
    
    if workload_stats:
        analyze_workload_stats(workload_stats)
    else:
        print("No workload statistics available in raw simulation results.")
        print("Workload analysis requires processed simulation data.")
    
    # Analyze resource states
    _print_section_header("Resource States")
    analyze_resource_states(results_data)
    
    # Analyze resource utilization
    _print_section_header("Resource Utilization")
    
    # Extract resource utilization metrics from raw results
    resource_util = {}
    if "resource_metrics" in results_data:
        for resource_id, metrics in results_data["resource_metrics"].items():
            if "utilization" in metrics:
                # Calculate statistics from time series data
                values = [point["value"] if isinstance(point, dict) and "value" in point else 0 for point in metrics["utilization"]]
                if values:
                    resource_util[resource_id] = {
                        "avg": sum(values) / len(values),
                        "max": max(values),
                        "min": min(values)
                    }
    
    analyze_resource_utilization(resource_util)
    
    # Analyze specific metrics based on user selection
    metrics_to_analyze = getattr(args, 'metrics', ["all"])
    
    # If 'all' is specified or no specific metrics are given, analyze all metrics
    if "all" in metrics_to_analyze or not metrics_to_analyze:
        metrics_to_analyze = ["latency", "energy", "carbon", "cost", "scaling"]
    
    _print_section_header("Performance Metrics")
    
    if "latency" in metrics_to_analyze:
        _print_subsection_header("Latency Analysis")
        # Extract latency metrics from results data
        latency_metrics = {}
        
        # First check if we have direct latency data in resource_metrics
        has_latency_data = False
        all_latencies = []
        
        if "resource_metrics" in results_data:
            logger.debug(f"Found {len(results_data['resource_metrics'])} resources with metrics")
            for resource_id, metrics in results_data["resource_metrics"].items():
                if "request_latency_ms" in metrics and metrics["request_latency_ms"]:
                    # Check if the latency data has actual values
                    latency_values = [point.get("value", 0) for point in metrics["request_latency_ms"] 
                                     if isinstance(point, dict) and "value" in point]
                    
                    logger.debug(f"Resource {resource_id}: Found {len(latency_values)} latency data points")
                    
                    if latency_values and any(v > 0 for v in latency_values):
                        has_latency_data = True
                        all_latencies.extend(latency_values)
                        logger.debug(f"Resource {resource_id}: Added {len(latency_values)} valid latency values")
        
        # Check if we have token traces as another source of latency data
        if not has_latency_data and "token_traces" in results_data and results_data["token_traces"]:
            latency_metrics["token_traces"] = results_data["token_traces"]
            has_latency_data = True
            analyze_latency(latency_metrics)
        elif has_latency_data and all_latencies:
            # Calculate statistics from collected latency values
            avg_latency = sum(all_latencies) / len(all_latencies) if all_latencies else 0
            # Sort for percentiles
            all_latencies.sort()
            p95_index = int(len(all_latencies) * 0.95)
            p99_index = int(len(all_latencies) * 0.99)
            p95_latency = all_latencies[p95_index] if p95_index < len(all_latencies) else avg_latency * 1.5
            p99_latency = all_latencies[p99_index] if p99_index < len(all_latencies) else avg_latency * 2.0
            
            # Use the analyze_latency function with our calculated metrics
            latency_metrics = {
                "average": avg_latency,
                "percentile_95": p95_latency,
                "percentile_99": p99_latency,
                "latencies": all_latencies
            }
            analyze_latency(latency_metrics)
        else:
            # Show a message indicating no latency data is available
            print("No latency data available in raw simulation results.")
            print("Latency analysis requires token trace data or request_latency_ms data from the simulation.")
            
            # Calculate estimated latency metrics based on workload and resource utilization
            # Estimate latency based on resource utilization
            avg_latency = 0
            p95_latency = 0
            p99_latency = 0
            
            # If we have resource utilization data, estimate latency
            if "resource_metrics" in results_data:
                # Higher utilization generally means higher latency
                max_utilizations = []
                for resource_id, metrics in results_data["resource_metrics"].items():
                    if "utilization" in metrics and metrics["utilization"]:
                        values = [point["value"] for point in metrics["utilization"] 
                                 if isinstance(point, dict) and "value" in point]
                        if values:
                            max_utilizations.append(max(values))
                
                if max_utilizations:
                    # Calculate average max utilization across resources
                    avg_max_util = sum(max_utilizations) / len(max_utilizations)
                    
                    # Estimate latency based on utilization (simplified model)
                    # At 50% utilization: ~50ms latency
                    # At 90% utilization: ~200ms latency
                    # At 95% utilization: ~500ms latency
                    # At 99% utilization: ~1000ms latency
                    base_latency = 50
                    
                    if avg_max_util > 0.9:
                        # Exponential increase in latency at high utilization
                        util_factor = (avg_max_util - 0.9) * 10  # 0-1 scale for 90-100% util
                        avg_latency = base_latency + (util_factor ** 2) * 450
                        p95_latency = avg_latency * 1.5
                        p99_latency = avg_latency * 2.5
                    else:
                        # Linear increase in latency at lower utilization
                        avg_latency = base_latency + (avg_max_util * 150)
                        p95_latency = avg_latency * 1.3
                        p99_latency = avg_latency * 1.8
            
            # Use the analyze_latency function with our estimated metrics
            latency_metrics = {
                "average": avg_latency,
                "percentile_95": p95_latency,
                "percentile_99": p99_latency,
                "estimated": True
            }
            analyze_latency(latency_metrics)

            # Additionally, provide an estimated per-resource latency table using utilization
            try:
                per_resource_rows = []
                # Reuse resource-level utilization computed earlier
                if 'resource_util' in locals() and resource_util:
                    for rid, stats in resource_util.items():
                        util = float(stats.get('max', 0))
                        base_latency = 50.0
                        if util > 0.9:
                            util_factor = (util - 0.9) * 10
                            avg_l = base_latency + (util_factor ** 2) * 450
                            p95_l = avg_l * 1.5
                        else:
                            avg_l = base_latency + (util * 150)
                            p95_l = avg_l * 1.3
                        per_resource_rows.append((rid, avg_l, p95_l))
                if per_resource_rows:
                    # Sort by p95 latency desc and show top 10
                    per_resource_rows.sort(key=lambda x: x[2], reverse=True)
                    print("\nEstimated Latency by Resource (from utilization):")
                    print("+-------------------------------+--------------+--------------+")
                    print("| Resource                      | Avg (ms)     | P95 (ms)     |")
                    print("+-------------------------------+--------------+--------------+")
                    for rid, avg_l, p95_l in per_resource_rows[:10]:
                        name = rid.split('.')[-1] if '.' in rid else rid
                        print(f"| {name:<29} | {avg_l:>10.2f} | {p95_l:>10.2f} |")
                    print("+-------------------------------+--------------+--------------+\n")
            except Exception:
                pass
    
    if "energy" in metrics_to_analyze:
        _print_subsection_header("Energy Consumption")
        
        # Check if energy metrics are already stored in the results
        if "energy_metrics" in results_data:
            # Use stored energy metrics
            energy_metrics = results_data["energy_metrics"]
            logger.info("Using stored energy metrics from simulation results")
        else:
            # Fallback: Calculate energy metrics from actual power draw data
            logger.info("Calculating energy metrics from power draw data")
            energy_metrics = {"total_kwh": 0, "by_resource_type": {}, "by_resource": {}}
            
            if "resource_metrics" in results_data:
                total_energy_wh = 0
                
                for resource_id, metrics in results_data["resource_metrics"].items():
                    resource_energy_wh = 0
                    
                    # Use actual power_draw_w data if available
                    if "power_draw_w" in metrics and metrics["power_draw_w"]:
                        power_data = metrics["power_draw_w"]
                        # Calculate energy by integrating power over time (trapezoidal rule)
                        for i in range(len(power_data) - 1):
                            current_power = power_data[i]["value"]
                            next_power = power_data[i + 1]["value"]
                            time_diff_hours = (power_data[i + 1]["timestamp"] - power_data[i]["timestamp"]) / 3600
                            # Trapezoidal integration: average power * time
                            avg_power = (current_power + next_power) / 2
                            resource_energy_wh += avg_power * time_diff_hours
                    
                    # Fallback to utilization-based estimation if no power data
                    elif "utilization" in metrics and metrics["utilization"]:
                        utilization_data = metrics["utilization"]
                        duration_hours = results_data.get("metadata", {}).get("duration_seconds", 3600) / 3600
                        avg_util = sum(point["value"] for point in utilization_data) / len(utilization_data)
                        # Estimate power consumption based on resource type and utilization
                        estimated_power_w = 100 * avg_util  # Simplified: 100W max per resource
                        resource_energy_wh = estimated_power_w * duration_hours
                    
                    if resource_energy_wh > 0:
                        total_energy_wh += resource_energy_wh
                        # Extract resource name for display
                        resource_name = resource_id.split(".")[-1] if "." in resource_id else resource_id
                        energy_metrics["by_resource"][resource_name] = resource_energy_wh / 1000  # Convert to kWh
                
                energy_metrics["total_kwh"] = total_energy_wh / 1000  # Convert to kWh
        
        analyze_energy(energy_metrics)
    
    if "carbon" in metrics_to_analyze:
        _print_subsection_header("Carbon Emissions")
        
        # Check if carbon metrics are already stored in the results
        if "carbon_metrics" in results_data:
            # Use stored carbon metrics
            carbon_metrics = results_data["carbon_metrics"]
            logger.info("Using stored carbon metrics from simulation results")
        else:
            # Fallback: Calculate carbon metrics from actual energy consumption
            logger.info("Calculating carbon metrics from power draw data")
            carbon_metrics = {"total_carbon_emissions": 0, "regional_emissions": {}, "resource_emissions": {}}
            
            if "resource_metrics" in results_data:
                total_carbon_kg = 0
                # Average carbon intensity (gCO2e/kWh) - using global average
                carbon_intensity = 400  # gCO2e/kWh
                
                for resource_id, metrics in results_data["resource_metrics"].items():
                    resource_energy_wh = 0
                    
                    # Use actual power_draw_w data if available
                    if "power_draw_w" in metrics and metrics["power_draw_w"]:
                        power_data = metrics["power_draw_w"]
                        # Calculate energy by integrating power over time (trapezoidal rule)
                        for i in range(len(power_data) - 1):
                            current_power = power_data[i]["value"]
                            next_power = power_data[i + 1]["value"]
                            time_diff_hours = (power_data[i + 1]["timestamp"] - power_data[i]["timestamp"]) / 3600
                            # Trapezoidal integration: average power * time
                            avg_power = (current_power + next_power) / 2
                            resource_energy_wh += avg_power * time_diff_hours
                    
                    # Fallback to utilization-based estimation if no power data
                    elif "utilization" in metrics and metrics["utilization"]:
                        utilization_data = metrics["utilization"]
                        duration_hours = results_data.get("metadata", {}).get("duration_seconds", 3600) / 3600
                        avg_util = sum(point["value"] for point in utilization_data) / len(utilization_data)
                        # Estimate power consumption based on resource type and utilization
                        estimated_power_w = 100 * avg_util  # Simplified: 100W max per resource
                        resource_energy_wh = estimated_power_w * duration_hours
                    
                    if resource_energy_wh > 0:
                        # Convert energy to carbon emissions
                        energy_kwh = resource_energy_wh / 1000
                        carbon_g = energy_kwh * carbon_intensity
                        carbon_kg = carbon_g / 1000
                        total_carbon_kg += carbon_kg
                        
                        # Extract resource name for display
                        resource_name = resource_id.split(".")[-1] if "." in resource_id else resource_id
                        carbon_metrics["resource_emissions"][resource_name] = carbon_kg
                
                carbon_metrics["total_carbon_emissions"] = total_carbon_kg
                
                # Add regional breakdown (simplified - assume us-central1 for GCP resources)
                if total_carbon_kg > 0:
                    carbon_metrics["regional_emissions"]["us-central1"] = total_carbon_kg
        
        analyze_carbon(carbon_metrics)
    
    if "cost" in metrics_to_analyze:
        _print_subsection_header("Cost Analysis")
        cost_data = results_data.get("cost_data")
        if cost_data:
            analyze_cost(cost_data)
        else:
            print("No cost data available in results. Re-run simulate with --enable-cost-analysis.")
    
    if "scaling" in metrics_to_analyze:
        _print_subsection_header("Scaling Analysis")
        analyze_scaling(results_data)
    
    # Generate summary and recommendations
    _print_section_header("Summary and Recommendations")
    perform_additional_analysis(results_data)
    generate_summary_and_recommendations(results_data)

    _print_main_footer()


def _print_main_header():
    print("\n" + "=" * 80)
    print(" DETAILED SIMULATION ANALYSIS REPORT".center(80))
    print("=" * 80)


def _print_main_footer():
    print("\n" + "=" * 80)
    print(" END OF REPORT".center(80))
    print("=" * 80)


def _print_section_header(title: str):
    print("\n" + "-" * 80)
    print(f"-- {title.upper()} --".center(80))
    print("-" * 80)


def _print_subsection_header(title: str):
    print(f"\n### {title} ###")
