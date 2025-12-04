from collections import defaultdict
import matplotlib.pyplot as plt
import logging

from typing import Dict, Any, List
from tabulate import tabulate
import matplotlib

matplotlib.use("Agg")  # Use a non-GUI backend for matplotlib

"""
utils/analyser.py

This module provides functions to analyze and visualize simulation results,
including simulation information, workload statistics, latency metrics,
energy consumption, carbon emissions, scaling efficiency, and resource utilization.
"""

# Configure root logger
logger = logging.getLogger(__name__)

# ========== Utility Functions ==========


def format_number(value, precision=2):
    """Format number with specified precision and handle None and string values.
    
    Args:
        value: The value to format (int, float, or string)
        precision: Number of decimal places to round to (default: 2)
        
    Returns:
        str: Formatted number as string, or "N/A" if value is None or not a number
    """
    if value is None:
        return "N/A"
    try:
        # Try to convert to float first to handle string numbers
        num = float(value)
        return f"{num:.{precision}f}"
    except (ValueError, TypeError):
        # Return as-is if it can't be converted to a number
        return str(value)


def print_section_header(title, width=80):
    """Print a formatted section header."""
    print(f"\n{'=' * width}")
    print(f"{title.center(width)}")
    print(f"{'=' * width}")


def print_subsection_header(title, width=80):
    """Print a formatted subsection header."""
    print(f"\n{'-' * width}")
    print(f"{title}")
    print(f"{'-' * width}")


def create_table(headers, rows):
    """Create a formatted table."""
    return tabulate(rows, headers=headers, tablefmt="grid")


# ========== Analysis Functions ==========


def analyze_simulation_info(sim_info: Dict[str, Any]) -> None:
    """Analyze and print simulation information."""
    print_section_header("SIMULATION OVERVIEW")

    duration = sim_info.get("duration", "N/A")
    sim_time = sim_info.get("simulation_time_reached", "N/A")
    exec_time = sim_info.get("execution_time", "N/A")
    total_tokens = sim_info.get("total_tokens", "N/A")
    completed_tokens = sim_info.get("completed_tokens", "N/A")
    active_tokens = sim_info.get("active_tokens", "N/A")

    # Calculate completion percentage
    if (
        isinstance(total_tokens, (int, float))
        and isinstance(completed_tokens, (int, float))
        and total_tokens > 0
    ):
        completion_percentage = (completed_tokens / total_tokens) * 100
    else:
        completion_percentage = "N/A"

    # Create a table
    headers = ["Metric", "Value"]
    rows = [
        ["Duration (planned)", f"{format_number(duration)} seconds"],
        ["Simulation Time Reached", f"{format_number(sim_time)} seconds"],
        ["Execution Time", f"{format_number(exec_time)} seconds"],
        ["Total Tokens", total_tokens],
        ["Completed Tokens", completed_tokens],
        ["Active Tokens", active_tokens],
        [
            "Completion Rate",
            (
                f"{format_number(completion_percentage)}%"
                if completion_percentage != "N/A"
                else "N/A"
            ),
        ],
    ]

    print(create_table(headers, rows))


def analyze_workload_stats(workload_stats: Dict[str, Any]) -> None:
    """Analyze and print workload statistics.
    
    Args:
        workload_stats: Dictionary containing workload statistics. Can be either:
                      - Simple format: {"total_requests": 1000, "successful_requests": 950, ...}
                      - Detailed format: {"workload_name": {"total_tokens": 1000, ...}, ...}
    """
    if not workload_stats:
        return

    print_section_header("WORKLOAD STATISTICS")

    # Check if this is a simple stats format (from test)
    if all(k in workload_stats for k in ["total_requests", "successful_requests"]):
        headers = ["Metric", "Value"]
        rows = [
            ["Total Requests", format_number(workload_stats.get("total_requests"))],
            ["Successful Requests", format_number(workload_stats.get("successful_requests"))],
            ["Failed Requests", format_number(workload_stats.get("failed_requests"))],
            ["Request Rate", f"{format_number(workload_stats.get('request_rate'))}/s"],
            ["Success Rate", f"{format_number(workload_stats.get('successful_requests', 0) / workload_stats['total_requests'] * 100 if workload_stats.get('total_requests') else 0, 2)}%"]
        ]
        print(create_table(headers, rows))
        return

    # Original detailed format handling
    headers = [
        "Workload",
        "Total Tokens",
        "Avg Rate",
        "Min Rate",
        "Max Rate",
        "Std Dev",
        "Duration (s)",
        "Tokens/s",
    ]
    rows = []

    for workload_name, stats in workload_stats.items():
        if not isinstance(stats, dict):
            # Skip if stats is not a dictionary
            continue

        rows.append(
            [
                workload_name,
                format_number(stats.get("total_tokens")),
                format_number(stats.get("avg_rate")),
                format_number(stats.get("min_rate")),
                format_number(stats.get("max_rate")),
                format_number(stats.get("std_dev")),
                format_number(stats.get("duration")),
                format_number(stats.get("tokens_per_second")),
            ]
        )

    if rows:  # Only print if we have valid data
        print(create_table(headers, rows))


def analyze_latency(latency_metrics: Dict[str, Any]) -> None:
    """Analyze and print latency metrics."""
    print_section_header("LATENCY ANALYSIS")

    avg_latency = latency_metrics.get("average", 0)
    p95_latency = latency_metrics.get("percentile_95", 0)
    p99_latency = latency_metrics.get("percentile_99", 0)

    # Check if this is end-to-end latency data
    end_to_end_enabled = latency_metrics.get("end_to_end_enabled", False)
    
    if end_to_end_enabled:
        print_subsection_header("End-to-End Latency Summary")
        
        # End-to-end latency breakdown
        end_to_end_stats = latency_metrics.get("end_to_end", {})
        user_to_infra_stats = latency_metrics.get("user_to_infrastructure", {})
        infrastructure_stats = latency_metrics.get("infrastructure_only", {})
        
        headers = ["Component", "Average (ms)", "P95 (ms)", "P99 (ms)"]
        rows = []
        
        if end_to_end_stats:
            rows.append([
                "Total End-to-End",
                format_number(end_to_end_stats.get("average", 0)),
                format_number(end_to_end_stats.get("p95", 0)),
                format_number(end_to_end_stats.get("p99", 0))
            ])
        
        if user_to_infra_stats:
            rows.append([
                "User-to-Infrastructure",
                format_number(user_to_infra_stats.get("average", 0)),
                format_number(user_to_infra_stats.get("p95", 0)),
                format_number(user_to_infra_stats.get("p99", 0))
            ])
        
        if infrastructure_stats:
            rows.append([
                "Infrastructure-Internal",
                format_number(infrastructure_stats.get("average", 0)),
                format_number(infrastructure_stats.get("p95", 0)),
                format_number(infrastructure_stats.get("p99", 0))
            ])
        
        if rows:
            print(create_table(headers, rows))
        
        # Show latency breakdown by user region if available
        latency_breakdown = latency_metrics.get("latency_breakdown", {})
        if latency_breakdown:
            print_subsection_header("End-to-End Latency by User Region")
            breakdown_headers = ["User Region", "Infrastructure Region", "User-to-Infra (ms)", "Total E2E (ms)"]
            breakdown_rows = []
            
            for token_id, breakdown in list(latency_breakdown.items())[:10]:  # Show first 10 tokens
                breakdown_rows.append([
                    breakdown.get("user_region", "N/A"),
                    breakdown.get("infrastructure_region", "N/A"),
                    format_number(breakdown.get("user_to_infra_latency", 0)),
                    format_number(breakdown.get("total_end_to_end_latency", 0))
                ])
            
            if breakdown_rows:
                print(create_table(breakdown_headers, breakdown_rows))
                if len(latency_breakdown) > 10:
                    print(f"... and {len(latency_breakdown) - 10} more tokens")
    
    else:
        # Standard latency analysis (infrastructure-only)
        print_subsection_header("Infrastructure Latency Summary")
        
        # Summary table
        headers = ["Metric", "Value (ms)"]
        
        # Check if these are estimated values (more precision needed)
        if latency_metrics.get("estimated", False):
            rows = [
                ["Average Latency (est.)", f"{avg_latency:.1f}"],
                ["95th Percentile (est.)", f"{p95_latency:.1f}"],
                ["99th Percentile (est.)", f"{p99_latency:.1f}"],
            ]
        else:
            rows = [
                ["Average Latency", format_number(avg_latency)],
                ["95th Percentile", format_number(p95_latency)],
                ["99th Percentile", format_number(p99_latency)],
            ]

        print(create_table(headers, rows))

    # Calculate latency distribution
    latencies = latency_metrics.get("latencies", [])
    if not latencies and end_to_end_enabled:
        # Try to get latencies from end-to-end stats
        end_to_end_stats = latency_metrics.get("end_to_end", {})
        latencies = end_to_end_stats.get("raw", [])
    
    if latencies:
        # Group latencies into ranges (convert to milliseconds if needed)
        ranges = defaultdict(int)
        for latency in latencies:
            # Convert to milliseconds if the values are in seconds
            latency_ms = latency * 1000 if latency < 10 else latency
            
            if latency_ms < 50:
                ranges["< 50"] += 1
            elif latency_ms < 100:
                ranges["50 - 100"] += 1
            elif latency_ms < 200:
                ranges["100 - 200"] += 1
            elif latency_ms < 500:
                ranges["200 - 500"] += 1
            else:
                ranges["> 500"] += 1

        print_subsection_header("Latency Distribution")
        dist_headers = ["Range (ms)", "Count", "Percentage"]
        dist_rows = []

        for range_name, count in ranges.items():
            percentage = (count / len(latencies)) * 100
            dist_rows.append([range_name, count, f"{percentage:.1f}%"])

        print(create_table(dist_headers, dist_rows))


def analyze_energy(energy_metrics: Dict[str, Any]) -> None:
    """Analyze and print energy metrics."""
    print_section_header("ENERGY CONSUMPTION ANALYSIS")

    total_energy = energy_metrics.get("total_kwh", 0)

    # Summary table
    print_subsection_header("Total Energy Consumption")
    print(f"Total Energy: {format_number(total_energy, 4)} kWh")

    # By resource type breakdown
    by_type = energy_metrics.get("by_resource_type", {})
    if by_type:
        print_subsection_header("Breakdown by Resource Type")
        type_headers = ["Resource Type", "Energy (kWh)", "Percentage"]
        type_rows = []

        for rtype, energy in by_type.items():
            percentage = (energy / total_energy) * 100 if total_energy else 0
            type_rows.append(
                [
                    rtype.capitalize(),
                    format_number(energy),
                    f"{percentage:.1f}%",
                ]
            )

        print(create_table(type_headers, type_rows))

    # By individual resource
    resource_energy = energy_metrics.get("resource_energy", {})
    if resource_energy:
        print_subsection_header("Breakdown by Individual Resource")
        resource_headers = ["Resource", "Energy (kWh)", "Percentage"]
        resource_rows = []

        # Sort resources by energy consumption (descending)
        sorted_resources = sorted(
            resource_energy.items(), key=lambda x: x[1], reverse=True
        )

        for resource, energy in sorted_resources:
            percentage = (energy / total_energy) * 100 if total_energy else 0
            # Extract resource name from the full path
            resource_name = resource.split(".")[-1]
            resource_rows.append(
                [resource_name, format_number(energy), f"{percentage:.1f}%"]
            )

        print(create_table(resource_headers, resource_rows))


def analyze_carbon(carbon_metrics: Dict[str, Any]) -> None:
    """Analyze and print carbon emission metrics."""
    print_section_header("CARBON EMISSIONS ANALYSIS")

    total_carbon = carbon_metrics.get("total_carbon_emissions", 0)

    # Summary
    print_subsection_header("Total Carbon Emissions")
    print(f"Total Carbon Emissions: {format_number(total_carbon, 6)} kg CO2e")

    # By region breakdown
    regional = carbon_metrics.get("regional_emissions", {})
    if regional:
        print_subsection_header("Breakdown by Region")
        region_headers = ["Region", "Emissions (kg CO2e)", "Percentage"]
        region_rows = []

        for region, emissions in regional.items():
            percentage = (
                (emissions / total_carbon) * 100 if total_carbon else 0
            )
            region_rows.append(
                [region, format_number(emissions), f"{percentage:.1f}%"]
            )

        print(create_table(region_headers, region_rows))

    # By resource breakdown
    resource_emissions = carbon_metrics.get("resource_emissions", {})
    if resource_emissions:
        print_subsection_header("Breakdown by Resource")
        resource_headers = ["Resource", "Emissions (kg CO2e)", "Percentage"]
        resource_rows = []

        # Sort resources by emissions (descending)
        sorted_resources = sorted(
            resource_emissions.items(), key=lambda x: x[1], reverse=True
        )

        for resource, emissions in sorted_resources:
            percentage = (
                (emissions / total_carbon) * 100 if total_carbon else 0
            )
            # Extract resource name from the full path
            resource_name = resource.split(".")[-1]
            resource_rows.append(
                [resource_name, format_number(emissions), f"{percentage:.1f}%"]
            )

        print(create_table(resource_headers, resource_rows))


def analyze_cost(cost_data: Dict[str, Any]) -> None:
    """Analyze and print cost metrics from cost_data.

    Expects cost_data in the structure produced by InfracostEstimator/CostMetrics
    (i.e., with total_* fields and projects[].resources[] breakdown).
    """
    if not isinstance(cost_data, dict) or not cost_data:
        return

    print_section_header("COST ANALYSIS")

    # Summary table
    currency = cost_data.get("currency", "USD")
    total_monthly = cost_data.get("total_monthly_cost")
    total_hourly = cost_data.get("total_hourly_cost")
    total_sim = cost_data.get("total_simulation_cost")

    headers = ["Metric", "Value"]
    rows = [
        ["Monthly Total", f"${format_number(total_monthly, 2)} {currency}"],
        ["Hourly Total", f"${format_number(total_hourly, 6)} {currency}/h"],
        ["Simulation Total", f"${format_number(total_sim, 4)} {currency}"]
        if total_sim is not None
        else ["Simulation Total", "N/A"],
    ]
    print(create_table(headers, rows))

    # Breakdown by resource (across all projects)
    resources = []
    for proj in cost_data.get("projects", []) or []:
        pname = proj.get("display_name") or proj.get("name") or "project"
        for res in proj.get("resources", []) or []:
            resources.append(
                {
                    "project": pname,
                    "name": res.get("name") or "resource",
                    "type": res.get("resource_type") or "",
                    "hourly": res.get("hourly_cost"),
                    "monthly": res.get("monthly_cost"),
                    "simulation": res.get("simulation_cost"),
                }
            )

    if resources:
        print_subsection_header("Breakdown by Resource")
        res_headers = [
            "Project",
            "Resource",
            "Type",
            "Hourly ($/h)",
            "Monthly ($)",
            "Simulation ($)",
        ]
        # Sort by simulation cost desc then hourly cost desc
        def _key(r):
            sc = r.get("simulation")
            hc = r.get("hourly")
            try:
                return (float(sc) if sc is not None else -1.0, float(hc) if hc is not None else -1.0)
            except Exception:
                return (0.0, 0.0)

        resources.sort(key=_key, reverse=True)
        res_rows: List[List[str]] = []
        for r in resources:
            res_rows.append(
                [
                    r["project"],
                    r["name"],
                    r["type"],
                    format_number(r.get("hourly"), 6),
                    format_number(r.get("monthly"), 2),
                    format_number(r.get("simulation"), 4),
                ]
            )
        print(create_table(res_headers, res_rows))

    # Breakdown by resource type/service if available (simulation period, when provided)
    by_type = cost_data.get("cost_by_resource_type") or {}
    if by_type:
        print_subsection_header("Breakdown by Resource Type (Simulation)")
        type_headers = ["Resource Type", "Simulation Cost ($)"]
        type_rows = [
            [rtype, format_number(cost, 4)] for rtype, cost in sorted(by_type.items(), key=lambda x: float(x[1] or 0), reverse=True)
        ]
        print(create_table(type_headers, type_rows))

    by_service = cost_data.get("cost_by_service") or {}
    if by_service:
        print_subsection_header("Breakdown by Service (Simulation)")
        svc_headers = ["Service", "Simulation Cost ($)"]
        svc_rows = [
            [svc, format_number(cost, 4)] for svc, cost in sorted(by_service.items(), key=lambda x: float(x[1] or 0), reverse=True)
        ]
        print(create_table(svc_headers, svc_rows))


def analyze_scaling(scaling_metrics: Dict[str, Any]) -> None:
    """Analyze and print scaling metrics."""
    print_section_header("SCALING EFFICIENCY ANALYSIS")

    avg_util = scaling_metrics.get("average_utilization", 0)
    max_pods = scaling_metrics.get("max_pods", 0)
    scaling_events = scaling_metrics.get("scaling_events", 0)

    # Summary table
    headers = ["Metric", "Value"]
    rows = [
        ["Average Utilization", f"{format_number(avg_util * 100)}%"],
        ["Maximum Pods", max_pods],
        ["Total Scaling Events", scaling_events],
    ]

    print(create_table(headers, rows))

    # Utilization assessment
    print_subsection_header("Utilization Assessment")
    if avg_util < 0.3:
        print(
            "WARNING: Low resource utilization (< 30%). Consider downsizing resources to improve efficiency."
        )
    elif avg_util > 0.8:
        print(
            "WARNING: High resource utilization (> 80%). Consider adding resources to prevent potential bottlenecks."
        )
    else:
        print("GOOD: Resource utilization is in an optimal range (30-80%).")

    if scaling_events > 100:
        print(
            f"WARNING: High number of scaling events ({scaling_events}). This may indicate resource volatility."
        )


def analyze_resource_utilization(
    resource_util: Dict[str, Dict[str, float]],
) -> None:
    """Analyze and print resource utilization metrics."""
    print_section_header("RESOURCE UTILIZATION ANALYSIS")

    if not resource_util:
        print("No resource utilization data available.")
        return

    # Create a table for resource utilization
    headers = ["Resource", "Avg Util", "Max Util", "Min Util", "Efficiency"]
    rows = []

    for resource, metrics in resource_util.items():
        avg_util = metrics.get("avg", 0)
        max_util = metrics.get("max", 0)
        min_util = metrics.get("min", 0)

        # Calculate an efficiency score - higher is better
        # Perfect utilization would be consistently around 70-80%
        # Too high is risky, too low is wasteful
        target_util = 0.75
        efficiency = 100 - (abs(avg_util - target_util) * 100)
        efficiency = max(0, efficiency)  # Ensure not negative

        # Format as percentages
        avg_util_str = f"{format_number(avg_util * 100)}%"
        max_util_str = f"{format_number(max_util * 100)}%"
        min_util_str = f"{format_number(min_util * 100)}%"

        # Determine efficiency rating
        if efficiency >= 80:
            efficiency_rating = f"{format_number(efficiency)}% [GOOD]"
        elif efficiency >= 60:
            efficiency_rating = f"{format_number(efficiency)}% [WARN]"
        else:
            efficiency_rating = f"{format_number(efficiency)}% [POOR]"

        # Extract resource name from the full path
        resource_name = resource.split(".")[-1]
        rows.append(
            [
                resource_name,
                avg_util_str,
                max_util_str,
                min_util_str,
                efficiency_rating,
            ]
        )

    # Sort by efficiency (extract numerical value from efficiency_rating)
    rows.sort(key=lambda x: float(x[4].split("%")[0]), reverse=True)

    print(create_table(headers, rows))

    # Identify most and least efficient resources
    print_subsection_header("Resource Efficiency Highlights")
    if rows:
        most_efficient = rows[0]
        least_efficient = rows[-1]

        print(
            f"Most Efficient Resource: {most_efficient[0]} ({most_efficient[4]})"
        )
        print(
            f"Least Efficient Resource: {least_efficient[0]} ({least_efficient[4]})"
        )

        # Count over/under-utilized resources
        overutilized = sum(1 for row in rows if float(row[2].rstrip("%")) > 90)
        underutilized = sum(
            1 for row in rows if float(row[1].rstrip("%")) < 20
        )

        if overutilized > 0:
            print(
                f"WARNING: {overutilized} resources exceeding 90% utilization at peak - risk of performance issues"
            )

        if underutilized > 0:
            print(
                f"WARNING: {underutilized} resources below 20% average utilization - potential cost savings opportunity"
            )


def analyze_resource_states(
    resource_states: Dict[str, List[Dict[str, Any]]],
) -> None:
    """Analyze and print resource state transitions."""
    print_section_header("RESOURCE STATE TRANSITIONS")

    if not resource_states:
        print("No resource state data available.")
        return

    # Handle case where resource_states might be the full results dict
    if isinstance(resource_states, dict) and 'resource_metrics' in resource_states:
        print("No explicit resource state transitions available.")
        print("Resource metrics are available for utilization analysis.")
        return

    interesting_resources = []
    stable_resources = []

    for resource, states in resource_states.items():
        if not isinstance(states, list) or len(states) <= 1:
            stable_resources.append(resource)
            continue

        # Count state changes
        state_changes = sum(
            1
            for i in range(1, len(states))
            if states[i].get("state") != states[i - 1].get("state")
        )

        if state_changes > 0:
            resource_name = resource.split(".")[-1]
            interesting_resources.append(
                (resource_name, state_changes, states)
            )

    # Sort by number of state changes (descending)
    interesting_resources.sort(key=lambda x: x[1], reverse=True)

    if interesting_resources:
        print_subsection_header("Resources with State Changes")
        state_headers = ["Resource", "State Changes", "Details"]
        state_rows = []

        for resource_name, changes, states in interesting_resources:
            # Summarize the states
            state_summary = " → ".join(
                [
                    f"{s.get('state')}@{format_number(s.get('time', 0))}s"
                    for s in states
                ]
            )
            if len(state_summary) > 70:  # Truncate long summaries
                state_summary = state_summary[:67] + "..."

            state_rows.append([resource_name, changes, state_summary])

        print(create_table(state_headers, state_rows))

    if stable_resources:
        print_subsection_header("Stable Resources")
        print(
            f"The following {len(stable_resources)} resources had no state changes:"
        )
        stable_names = [
            resource.split(".")[-1] for resource in stable_resources
        ]
        print(", ".join(stable_names))


def perform_additional_analysis(results: Dict[str, Any]) -> None:
    """Perform additional analyses on the simulation results."""
    print_section_header("ADDITIONAL INSIGHTS")

    insights = []

    # Resource with highest carbon emission
    if (
        "carbon_metrics" in results
        and "resource_emissions" in results["carbon_metrics"]
    ):
        resource_emissions = results["carbon_metrics"]["resource_emissions"]
        if resource_emissions:
            highest_carbon_resource = max(
                resource_emissions.items(), key=lambda x: x[1]
            )
            insights.append(
                f"Highest carbon emitter: {highest_carbon_resource[0].split('.')[-1]} "
                f"({format_number(highest_carbon_resource[1])} kg CO2e)"
            )

    # Latency analysis
    if (
        "latency_metrics" in results
        and "latencies" in results["latency_metrics"]
    ):
        latencies = results["latency_metrics"]["latencies"]
        if latencies:
            high_latency_count = sum(1 for l in latencies if l > 1.0)
            high_latency_perc = (
                (high_latency_count / len(latencies)) * 100 if latencies else 0
            )

            if high_latency_perc > 15:
                insights.append(
                    f"WARNING: {format_number(high_latency_perc)}% of requests have high latency (>1ms)"
                )
            else:
                insights.append(
                    f"GOOD: Only {format_number(high_latency_perc)}% of requests have high latency (>1ms)"
                )

    # Efficiency ratio (energy per token)
    if (
        "energy_metrics" in results
        and "total_kwh" in results["energy_metrics"]
        and "simulation_info" in results
        and "total_tokens" in results["simulation_info"]
    ):
        total_energy = results["energy_metrics"]["total_kwh"]
        total_tokens = results["simulation_info"]["total_tokens"]

        if total_tokens > 0:
            energy_per_token = (
                total_energy / total_tokens
            ) * 1000  # in Wh per token
            insights.append(
                f"Energy efficiency: {format_number(energy_per_token)} Wh per token"
            )

    # Carbon efficiency
    if (
        "carbon_metrics" in results
        and "total_carbon_emissions" in results["carbon_metrics"]
        and "simulation_info" in results
        and "total_tokens" in results["simulation_info"]
    ):
        total_carbon = results["carbon_metrics"]["total_carbon_emissions"]
        total_tokens = results["simulation_info"]["total_tokens"]

        if total_tokens > 0:
            carbon_per_token = (
                total_carbon / total_tokens
            ) * 1000  # in g CO2e per token
            insights.append(
                f"Carbon efficiency: {format_number(carbon_per_token)} g CO2e per token"
            )

    # Performance bottleneck detection
    if "resource_utilization" in results:
        resource_util = results["resource_utilization"]
        high_util_resources = [
            (r, metrics["max"])
            for r, metrics in resource_util.items()
            if metrics["max"] > 0.9
        ]

        if high_util_resources:
            # Sort by highest utilization
            high_util_resources.sort(key=lambda x: x[1], reverse=True)
            bottleneck = high_util_resources[0][0].split(".")[-1]
            insights.append(
                f"WARNING: Potential bottleneck detected: {bottleneck} reached {format_number(high_util_resources[0][1] * 100)}% utilization"
            )

    # Recommend infrastructure changes based on utilization
    if "resource_utilization" in results:
        resource_util = results["resource_utilization"]

        # Find underutilized and overutilized resources
        underutilized = [
            (r, metrics["avg"])
            for r, metrics in resource_util.items()
            if metrics["avg"] < 0.2
        ]
        overutilized = [
            (r, metrics["avg"])
            for r, metrics in resource_util.items()
            if metrics["avg"] > 0.8
        ]

        if underutilized:
            resources = ", ".join(
                [r.split(".")[-1] for r, _ in underutilized[:3]]
            )
            if len(underutilized) > 3:
                resources += f" and {len(underutilized) - 3} more"
            insights.append(
                f"💡 Consider downsizing these underutilized resources: {resources}"
            )

        if overutilized:
            resources = ", ".join(
                [r.split(".")[-1] for r, _ in overutilized[:3]]
            )
            if len(overutilized) > 3:
                resources += f" and {len(overutilized) - 3} more"
            insights.append(
                f"💡 Consider upgrading these overutilized resources: {resources}"
            )

    # Print all insights
    for insight in insights:
        print(f"• {insight}")


# ========== Main Analysis Function ==========


def generate_summary_and_recommendations(results: Dict[str, Any]) -> None:
    """Generate a summary and recommendations based on the analysis."""
    print_section_header("SUMMARY AND RECOMMENDATIONS")

    summary_points = []
    recommendations = []

    # Analyze simulation completion
    sim_info = results.get("simulation_info", {})
    duration = sim_info.get("duration", 0)
    sim_time = sim_info.get("simulation_time_reached", 0)

    completion_percentage = (sim_time / duration * 100) if duration > 0 else 0
    summary_points.append(f"• Completion Rate: {format_number(completion_percentage)}%")
    
    if completion_percentage < 100:
        recommendations.append(
            "Review simulation parameters or increase resources to ensure full completion"
        )
    
    # Add success rate if workload stats are available
    if "workload_stats" in results:
        workload = results["workload_stats"]
        total_requests = workload.get("total_requests", 0)
        successful_requests = workload.get("successful_requests", 0)
        
        if total_requests > 0:
            success_rate = (successful_requests / total_requests) * 100
            summary_points.append(f"• Success Rate: {format_number(success_rate)}%")

    # Analyze resource utilization
    resource_util = results.get("resource_utilization", {})
    
    # Handle both flat and nested resource utilization formats
    if resource_util and all(isinstance(v, (int, float)) for v in resource_util.values()):
        # Flat format from test data: {"cpu_avg": 65.5, "memory_avg": 45.2}
        avg_utils = [v / 100.0 if k.endswith('_avg') else v for k, v in resource_util.items()]
    else:
        # Nested format: {"resource1": {"avg": 0.65, "min": 0.5, ...}, ...}
        avg_utils = [metrics.get("avg", 0) for _, metrics in resource_util.items()
                    if isinstance(metrics, dict)]
    
    if avg_utils:
        overall_avg_util = sum(avg_utils) / len(avg_utils)
        # Convert to percentage if needed (test data is in percentage)
        if any(isinstance(v, (int, float)) and v > 1 for v in resource_util.values()):
            overall_avg_util /= 100.0

        if overall_avg_util < 0.3:
            summary_points.append(
                f"⚠️ Overall resource utilization is low ({format_number(overall_avg_util * 100)}%)"
            )
            recommendations.append(
                "Consider consolidating resources or downsizing infrastructure"
            )
        elif overall_avg_util > 0.8:
            summary_points.append(
                f"⚠️ Overall resource utilization is high ({format_number(overall_avg_util * 100)}%)"
            )
            recommendations.append(
                "Consider adding resources to prevent performance issues"
            )
        else:
            summary_points.append(
                f"✅ Overall resource utilization is optimal ({format_number(overall_avg_util * 100)}%)"
            )

    # Analyze energy efficiency
    if "energy_metrics" in results and "simulation_info" in results:
        energy = results["energy_metrics"]
        total_energy = energy.get("total_kwh", 0)
        total_tokens = sim_info.get("total_tokens", 0)

        if total_tokens > 0:
            energy_per_token = (
                total_energy / total_tokens
            ) * 1000  # Wh per token
            if energy_per_token > 10:  # Arbitrary threshold
                summary_points.append(
                    f"⚠️ Energy efficiency is low ({format_number(energy_per_token)} Wh per token)"
                )
                recommendations.append(
                    "Optimize energy usage by reviewing resource allocation"
                )
            else:
                summary_points.append(
                    f"✅ Energy efficiency is good ({format_number(energy_per_token)} Wh per token)"
                )

    # Analyze latency
    if "latency_metrics" in results:
        latency = results["latency_metrics"]
        p95_latency = latency.get("percentile_95", 0)

        if p95_latency > 1.0:
            summary_points.append(
                f"⚠️ 95th percentile latency is high ({format_number(p95_latency)} ms)"
            )
            recommendations.append(
                "Investigate resources with high utilization to improve responsiveness"
            )
        else:
            summary_points.append(
                f"✅ Latency performance is good (95th percentile: {format_number(p95_latency)} ms)"
            )

    # Print summary points
    for point in summary_points:
        print(f"• {point}")

    if recommendations:
        print("\nRecommendations:")
        for i, rec in enumerate(recommendations, 1):
            print(f"{i}. {rec}")
    else:
        print(
            "\nNo specific recommendations - the system appears to be well-optimized."
        )
