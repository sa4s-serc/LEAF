import logging

from typing import Dict, Any, List
from tabulate import tabulate
import matplotlib 
matplotlib.use('Agg')  # Use a non-GUI backend for matplotlib
import matplotlib.pyplot as plt
from collections import defaultdict

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
    """Format number with specified precision and handle None values."""
    if value is None:
        return "N/A"
    return f"{value:.{precision}f}"

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
    
    duration = sim_info.get('duration', 'N/A')
    sim_time = sim_info.get('simulation_time_reached', 'N/A')
    exec_time = sim_info.get('execution_time', 'N/A')
    total_tokens = sim_info.get('total_tokens', 'N/A')
    completed_tokens = sim_info.get('completed_tokens', 'N/A')
    active_tokens = sim_info.get('active_tokens', 'N/A')
    
    # Calculate completion percentage
    if isinstance(total_tokens, (int, float)) and isinstance(completed_tokens, (int, float)) and total_tokens > 0:
        completion_percentage = (completed_tokens / total_tokens) * 100
    else:
        completion_percentage = 'N/A'
    
    # Create a table
    headers = ["Metric", "Value"]
    rows = [
        ["Duration (planned)", f"{format_number(duration)} seconds"],
        ["Simulation Time Reached", f"{format_number(sim_time)} seconds"],
        ["Execution Time", f"{format_number(exec_time)} seconds"],
        ["Total Tokens", total_tokens],
        ["Completed Tokens", completed_tokens],
        ["Active Tokens", active_tokens],
        ["Completion Rate", f"{format_number(completion_percentage)}%" if completion_percentage != 'N/A' else 'N/A']
    ]
    
    print(create_table(headers, rows))

def analyze_workload_stats(workload_stats: Dict[str, Any]) -> None:
    """Analyze and print workload statistics."""
    if not workload_stats:
        return
    
    print_section_header("WORKLOAD STATISTICS")
    
    headers = ["Workload", "Total Tokens", "Avg Rate", "Min Rate", "Max Rate", "Std Dev", "Duration (s)", "Tokens/s"]
    rows = []
    
    for workload_name, stats in workload_stats.items():
        rows.append([
            workload_name,
            stats.get('total_tokens', 'N/A'),
            format_number(stats.get('avg_rate', 0)),
            format_number(stats.get('min_rate', 0)),
            format_number(stats.get('max_rate', 0)),
            format_number(stats.get('std_dev_rate', 0)),
            format_number(stats.get('duration_seconds', 0)),
            format_number(stats.get('tokens_per_second', 0))
        ])
    
    print(create_table(headers, rows))

def analyze_latency(latency_metrics: Dict[str, Any]) -> None:
    """Analyze and print latency metrics."""
    print_section_header("LATENCY ANALYSIS")
    
    avg_latency = latency_metrics.get('average', 0)
    p95_latency = latency_metrics.get('percentile_95', 0)
    p99_latency = latency_metrics.get('percentile_99', 0)
    
    # Summary table
    headers = ["Metric", "Value (ms)"]
    rows = [
        ["Average Latency", format_number(avg_latency)],
        ["95th Percentile", format_number(p95_latency)],
        ["99th Percentile", format_number(p99_latency)]
    ]
    
    print(create_table(headers, rows))
    
    # Calculate latency distribution
    latencies = latency_metrics.get('latencies', [])
    if latencies:
        # Group latencies into ranges
        ranges = defaultdict(int)
        for latency in latencies:
            if latency < 0.1:
                ranges["< 0.1"] += 1
            elif latency < 0.5:
                ranges["0.1 - 0.5"] += 1
            elif latency < 1.0:
                ranges["0.5 - 1.0"] += 1
            elif latency < 1.5:
                ranges["1.0 - 1.5"] += 1
            else:
                ranges["> 1.5"] += 1
        
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
    
    total_energy = energy_metrics.get('total_kwh', 0)
    
    # Summary table
    print_subsection_header("Total Energy Consumption")
    print(f"Total Energy: {format_number(total_energy)} kWh")
    
    # By resource type breakdown
    by_type = energy_metrics.get('by_resource_type', {})
    if by_type:
        print_subsection_header("Breakdown by Resource Type")
        type_headers = ["Resource Type", "Energy (kWh)", "Percentage"]
        type_rows = []
        
        for rtype, energy in by_type.items():
            percentage = (energy / total_energy) * 100 if total_energy else 0
            type_rows.append([rtype.capitalize(), format_number(energy), f"{percentage:.1f}%"])
        
        print(create_table(type_headers, type_rows))
    
    # By individual resource
    resource_energy = energy_metrics.get('resource_energy', {})
    if resource_energy:
        print_subsection_header("Breakdown by Individual Resource")
        resource_headers = ["Resource", "Energy (kWh)", "Percentage"]
        resource_rows = []
        
        # Sort resources by energy consumption (descending)
        sorted_resources = sorted(resource_energy.items(), key=lambda x: x[1], reverse=True)
        
        for resource, energy in sorted_resources:
            percentage = (energy / total_energy) * 100 if total_energy else 0
            # Extract resource name from the full path
            resource_name = resource.split('.')[-1]
            resource_rows.append([resource_name, format_number(energy), f"{percentage:.1f}%"])
        
        print(create_table(resource_headers, resource_rows))

def analyze_carbon(carbon_metrics: Dict[str, Any]) -> None:
    """Analyze and print carbon emission metrics."""
    print_section_header("CARBON EMISSIONS ANALYSIS")
    
    total_carbon = carbon_metrics.get('total_carbon_emissions', 0)
    
    # Summary
    print_subsection_header("Total Carbon Emissions")
    print(f"Total Carbon Emissions: {format_number(total_carbon)} kg CO2e")
    
    # By region breakdown
    regional = carbon_metrics.get('regional_emissions', {})
    if regional:
        print_subsection_header("Breakdown by Region")
        region_headers = ["Region", "Emissions (kg CO2e)", "Percentage"]
        region_rows = []
        
        for region, emissions in regional.items():
            percentage = (emissions / total_carbon) * 100 if total_carbon else 0
            region_rows.append([region, format_number(emissions), f"{percentage:.1f}%"])
        
        print(create_table(region_headers, region_rows))
    
    # By resource breakdown
    resource_emissions = carbon_metrics.get('resource_emissions', {})
    if resource_emissions:
        print_subsection_header("Breakdown by Resource")
        resource_headers = ["Resource", "Emissions (kg CO2e)", "Percentage"]
        resource_rows = []
        
        # Sort resources by emissions (descending)
        sorted_resources = sorted(resource_emissions.items(), key=lambda x: x[1], reverse=True)
        
        for resource, emissions in sorted_resources:
            percentage = (emissions / total_carbon) * 100 if total_carbon else 0
            # Extract resource name from the full path
            resource_name = resource.split('.')[-1]
            resource_rows.append([resource_name, format_number(emissions), f"{percentage:.1f}%"])
        
        print(create_table(resource_headers, resource_rows))

def analyze_scaling(scaling_metrics: Dict[str, Any]) -> None:
    """Analyze and print scaling metrics."""
    print_section_header("SCALING EFFICIENCY ANALYSIS")
    
    avg_util = scaling_metrics.get('average_utilization', 0)
    max_pods = scaling_metrics.get('max_pods', 0)
    scaling_events = scaling_metrics.get('scaling_events', 0)
    
    # Summary table
    headers = ["Metric", "Value"]
    rows = [
        ["Average Utilization", f"{format_number(avg_util * 100)}%"],
        ["Maximum Pods", max_pods],
        ["Total Scaling Events", scaling_events]
    ]
    
    print(create_table(headers, rows))
    
    # Utilization assessment
    print_subsection_header("Utilization Assessment")
    if avg_util < 0.3:
        print("⚠️ Low resource utilization (< 30%). Consider downsizing resources to improve efficiency.")
    elif avg_util > 0.8:
        print("⚠️ High resource utilization (> 80%). Consider adding resources to prevent potential bottlenecks.")
    else:
        print("✅ Resource utilization is in an optimal range (30-80%).")
    
    if scaling_events > 100:
        print(f"⚠️ High number of scaling events ({scaling_events}). This may indicate resource volatility.")

def analyze_resource_utilization(resource_util: Dict[str, Dict[str, float]]) -> None:
    """Analyze and print resource utilization metrics."""
    print_section_header("RESOURCE UTILIZATION ANALYSIS")
    
    if not resource_util:
        print("No resource utilization data available.")
        return
    
    # Create a table for resource utilization
    headers = ["Resource", "Avg Util", "Max Util", "Min Util", "Efficiency"]
    rows = []
    
    for resource, metrics in resource_util.items():
        avg_util = metrics.get('avg', 0)
        max_util = metrics.get('max', 0)
        min_util = metrics.get('min', 0)
        
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
            efficiency_rating = f"{format_number(efficiency)}% ✅"
        elif efficiency >= 60:
            efficiency_rating = f"{format_number(efficiency)}% ⚠️"
        else:
            efficiency_rating = f"{format_number(efficiency)}% ❌"
        
        # Extract resource name from the full path
        resource_name = resource.split('.')[-1]
        rows.append([resource_name, avg_util_str, max_util_str, min_util_str, efficiency_rating])
    
    # Sort by efficiency (extract numerical value from efficiency_rating)
    rows.sort(key=lambda x: float(x[4].split('%')[0]), reverse=True)
    
    print(create_table(headers, rows))
    
    # Identify most and least efficient resources
    print_subsection_header("Resource Efficiency Highlights")
    if rows:
        most_efficient = rows[0]
        least_efficient = rows[-1]
        
        print(f"Most Efficient Resource: {most_efficient[0]} ({most_efficient[4]})")
        print(f"Least Efficient Resource: {least_efficient[0]} ({least_efficient[4]})")
        
        # Count over/under-utilized resources
        overutilized = sum(1 for row in rows if float(row[2].rstrip('%')) > 90)
        underutilized = sum(1 for row in rows if float(row[1].rstrip('%')) < 20)
        
        if overutilized > 0:
            print(f"⚠️ {overutilized} resources exceeding 90% utilization at peak - risk of performance issues")
        
        if underutilized > 0:
            print(f"⚠️ {underutilized} resources below 20% average utilization - potential cost savings opportunity")

def analyze_resource_states(resource_states: Dict[str, List[Dict[str, Any]]]) -> None:
    """Analyze and print resource state transitions."""
    print_section_header("RESOURCE STATE TRANSITIONS")
    
    if not resource_states:
        print("No resource state data available.")
        return
    
    interesting_resources = []
    stable_resources = []
    
    for resource, states in resource_states.items():
        if len(states) <= 1:
            stable_resources.append(resource)
            continue
        
        # Count state changes
        state_changes = sum(1 for i in range(1, len(states)) if states[i].get('state') != states[i-1].get('state'))
        
        if state_changes > 0:
            resource_name = resource.split('.')[-1]
            interesting_resources.append((resource_name, state_changes, states))
    
    # Sort by number of state changes (descending)
    interesting_resources.sort(key=lambda x: x[1], reverse=True)
    
    if interesting_resources:
        print_subsection_header("Resources with State Changes")
        state_headers = ["Resource", "State Changes", "Details"]
        state_rows = []
        
        for resource_name, changes, states in interesting_resources:
            # Summarize the states
            state_summary = " → ".join([f"{s.get('state')}@{format_number(s.get('time', 0))}s" for s in states])
            if len(state_summary) > 70:  # Truncate long summaries
                state_summary = state_summary[:67] + "..."
            
            state_rows.append([resource_name, changes, state_summary])
        
        print(create_table(state_headers, state_rows))
    
    if stable_resources:
        print_subsection_header("Stable Resources")
        print(f"The following {len(stable_resources)} resources had no state changes:")
        stable_names = [resource.split('.')[-1] for resource in stable_resources]
        print(", ".join(stable_names))

def perform_additional_analysis(results: Dict[str, Any]) -> None:
    """Perform additional analyses on the simulation results."""
    print_section_header("ADDITIONAL INSIGHTS")
    
    insights = []
    
    # Resource with highest carbon emission
    if 'carbon_metrics' in results and 'resource_emissions' in results['carbon_metrics']:
        resource_emissions = results['carbon_metrics']['resource_emissions']
        if resource_emissions:
            highest_carbon_resource = max(resource_emissions.items(), key=lambda x: x[1])
            insights.append(f"Highest carbon emitter: {highest_carbon_resource[0].split('.')[-1]} "
                           f"({format_number(highest_carbon_resource[1])} kg CO2e)")
    
    # Latency analysis
    if 'latency_metrics' in results and 'latencies' in results['latency_metrics']:
        latencies = results['latency_metrics']['latencies']
        if latencies:
            high_latency_count = sum(1 for l in latencies if l > 1.0)
            high_latency_perc = (high_latency_count / len(latencies)) * 100 if latencies else 0
            
            if high_latency_perc > 15:
                insights.append(f"⚠️ {format_number(high_latency_perc)}% of requests have high latency (>1ms)")
            else:
                insights.append(f"✅ Only {format_number(high_latency_perc)}% of requests have high latency (>1ms)")
    
    # Efficiency ratio (energy per token)
    if ('energy_metrics' in results and 'total_kwh' in results['energy_metrics'] and
            'simulation_info' in results and 'total_tokens' in results['simulation_info']):
        total_energy = results['energy_metrics']['total_kwh']
        total_tokens = results['simulation_info']['total_tokens']
        
        if total_tokens > 0:
            energy_per_token = (total_energy / total_tokens) * 1000  # in Wh per token
            insights.append(f"Energy efficiency: {format_number(energy_per_token)} Wh per token")
    
    # Carbon efficiency
    if ('carbon_metrics' in results and 'total_carbon_emissions' in results['carbon_metrics'] and
            'simulation_info' in results and 'total_tokens' in results['simulation_info']):
        total_carbon = results['carbon_metrics']['total_carbon_emissions']
        total_tokens = results['simulation_info']['total_tokens']
        
        if total_tokens > 0:
            carbon_per_token = (total_carbon / total_tokens) * 1000  # in g CO2e per token
            insights.append(f"Carbon efficiency: {format_number(carbon_per_token)} g CO2e per token")
    
    # Performance bottleneck detection
    if 'resource_utilization' in results:
        resource_util = results['resource_utilization']
        high_util_resources = [(r, metrics['max']) for r, metrics in resource_util.items() if metrics['max'] > 0.9]
        
        if high_util_resources:
            # Sort by highest utilization
            high_util_resources.sort(key=lambda x: x[1], reverse=True)
            bottleneck = high_util_resources[0][0].split('.')[-1]
            insights.append(f"⚠️ Potential bottleneck detected: {bottleneck} reached {format_number(high_util_resources[0][1] * 100)}% utilization")
    
    # Recommend infrastructure changes based on utilization
    if 'resource_utilization' in results:
        resource_util = results['resource_utilization']
        
        # Find underutilized and overutilized resources
        underutilized = [(r, metrics['avg']) for r, metrics in resource_util.items() if metrics['avg'] < 0.2]
        overutilized = [(r, metrics['avg']) for r, metrics in resource_util.items() if metrics['avg'] > 0.8]
        
        if underutilized:
            resources = ", ".join([r.split('.')[-1] for r, _ in underutilized[:3]])
            if len(underutilized) > 3:
                resources += f" and {len(underutilized) - 3} more"
            insights.append(f"💡 Consider downsizing these underutilized resources: {resources}")
        
        if overutilized:
            resources = ", ".join([r.split('.')[-1] for r, _ in overutilized[:3]])
            if len(overutilized) > 3:
                resources += f" and {len(overutilized) - 3} more"
            insights.append(f"💡 Consider upgrading these overutilized resources: {resources}")
    
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
    sim_info = results.get('simulation_info', {})
    duration = sim_info.get('duration', 0)
    sim_time = sim_info.get('simulation_time_reached', 0)
    
    completion_percentage = (sim_time / duration * 100) if duration > 0 else 0
    if completion_percentage < 100:
        summary_points.append(f"⚠️ Simulation only completed {format_number(completion_percentage)}% of planned duration")
        recommendations.append("Review simulation parameters or increase resources to ensure full completion")
    
    # Analyze resource utilization
    resource_util = results.get('resource_utilization', {})
    avg_utils = [metrics['avg'] for _, metrics in resource_util.items()]
    if avg_utils:
        overall_avg_util = sum(avg_utils) / len(avg_utils)
        
        if overall_avg_util < 0.3:
            summary_points.append(f"⚠️ Overall resource utilization is low ({format_number(overall_avg_util * 100)}%)")
            recommendations.append("Consider consolidating resources or downsizing infrastructure")
        elif overall_avg_util > 0.8:
            summary_points.append(f"⚠️ Overall resource utilization is high ({format_number(overall_avg_util * 100)}%)")
            recommendations.append("Consider adding resources to prevent performance issues")
        else:
            summary_points.append(f"✅ Overall resource utilization is optimal ({format_number(overall_avg_util * 100)}%)")
    
    # Analyze energy efficiency
    if 'energy_metrics' in results and 'simulation_info' in results:
        energy = results['energy_metrics']
        total_energy = energy.get('total_kwh', 0)
        total_tokens = sim_info.get('total_tokens', 0)
        
        if total_tokens > 0:
            energy_per_token = (total_energy / total_tokens) * 1000  # Wh per token
            if energy_per_token > 10:  # Arbitrary threshold
                summary_points.append(f"⚠️ Energy efficiency is low ({format_number(energy_per_token)} Wh per token)")
                recommendations.append("Optimize energy usage by reviewing resource allocation")
            else:
                summary_points.append(f"✅ Energy efficiency is good ({format_number(energy_per_token)} Wh per token)")
    
    # Analyze latency
    if 'latency_metrics' in results:
        latency = results['latency_metrics']
        p95_latency = latency.get('percentile_95', 0)
        
        if p95_latency > 1.0:
            summary_points.append(f"⚠️ 95th percentile latency is high ({format_number(p95_latency)} ms)")
            recommendations.append("Investigate resources with high utilization to improve responsiveness")
        else:
            summary_points.append(f"✅ Latency performance is good (95th percentile: {format_number(p95_latency)} ms)")
    
    # Print summary points
    for point in summary_points:
        print(f"• {point}")
    
    if recommendations:
        print("\nRecommendations:")
        for i, rec in enumerate(recommendations, 1):
            print(f"{i}. {rec}")
    else:
        print("\nNo specific recommendations - the system appears to be well-optimized.")
