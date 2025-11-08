import os
import sys
import argparse
import logging
from typing import Dict, Any
import json
import yaml
import pandas as pd
import time
import shutil
from .leaf_cloud import create_framework
from .exceptions import LeafCloudError
from .utils import (
    analyze_carbon,
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

#!/usr/bin/env python3
"""
LEAF-Cloud Framework Command Line Interface

This module serves as the main entry point for the LEAF-Cloud framework,
providing a command-line interface for interacting with the tool's functionality.
It handles argument parsing, configuration loading, and coordinates the
execution of operations across different modules.
"""


# Import the main framework class

# Configure logging (default to WARNING, user-friendly format)
logging.basicConfig(level=logging.WARNING,
                    format='[%(levelname)s] %(message)s')
logging.disable(logging.CRITICAL)  # Disable all logging by default
logger = logging.getLogger(__name__)


def setup_parser() -> argparse.ArgumentParser:
    """
    Set up command-line argument parser.

    Returns:
        Configured ArgumentParser instance
    """
    parser = argparse.ArgumentParser(
        description="LEAF-Cloud: Layered Eco-centric Analytical Framework for Cloud Infrastructure",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
        Examples:
        # Run simulation with default parameters
        python main.py simulate --terraform ./terraform_files/
        
        # Run simulation with custom workload rate
        python main.py simulate --terraform ./terraform_files/ --workload-rate 150
        
        # Generate visualization diagrams
        python main.py diagram --terraform ./terraform_files/ --types deployment class
        
        # Export simulation results to JSON
        python main.py export --terraform ./terraform_files/ --format json
        """
    )

    subparsers = parser.add_subparsers(
        dest='command', help='Command to execute')

    # Simulate command
    simulate_parser = subparsers.add_parser(
        'simulate', help='Run simulation on Terraform files')
    simulate_parser.add_argument(
        '--terraform', '-t', required=True, help='Directory containing Terraform files')
    simulate_parser.add_argument(
        '--config', '-c', help='Path to configuration file')
    simulate_parser.add_argument('--workload-rate', '-w', type=float,
                                 default=100.0, help='Base workload rate (requests/sec)')
    simulate_parser.add_argument(
        '--workload-type', '-wt', choices=['steady', 'burst', 'cyclical', 'random', 'custom', 'mix'], default='steady',
        help='Type of workload to simulate (steady, burst, cyclical, random, custom, or mix)')
    
    # Add specific parameters for each workload type
    # Burst workload parameters
    simulate_parser.add_argument('--burst-base-rate', type=float, help='Base rate for burst workload')
    simulate_parser.add_argument('--burst-peak-rate', type=float, help='Peak rate during bursts')
    simulate_parser.add_argument('--burst-duration', type=float, help='Duration of each burst in seconds')
    simulate_parser.add_argument('--burst-interval', type=float, help='Interval between bursts in seconds')
    
    # Cyclical workload parameters
    simulate_parser.add_argument('--cycle-amplitude', type=float, help='Amplitude of cyclical variation')
    simulate_parser.add_argument('--cycle-period', type=float, help='Period of cycle in seconds')
    simulate_parser.add_argument('--cycle-phase', type=float, help='Phase shift in radians')
    
    # Random workload parameters
    simulate_parser.add_argument('--random-min-rate', type=float, help='Minimum rate for random workload')
    simulate_parser.add_argument('--random-max-rate', type=float, help='Maximum rate for random workload')
    simulate_parser.add_argument('--random-change-interval', type=float, help='Interval between rate changes in seconds')
    
    # Custom workload parameters
    simulate_parser.add_argument('--custom-pattern-file', type=str, help='Path to custom workload pattern file (JSON)')
    simulate_parser.add_argument('--custom-scale-factor', type=float, default=1.0, help='Scale factor to apply to custom workload pattern')
    
    # Mix workload parameters
    simulate_parser.add_argument('--mix-config-file', type=str, help='Path to mix workload configuration file (JSON)')
    simulate_parser.add_argument(
        '--duration', '-d', type=float, default=3600.0, help='Simulation duration in seconds')
    simulate_parser.add_argument(
        '--iterations', '-i', type=int, default=1, help='Number of simulation iterations')
    simulate_parser.add_argument(
        '--output', '-o', help='Output directory for results')
    simulate_parser.add_argument(
        '--var-files', '-v', nargs='+', help='Terraform variable files')
    simulate_parser.add_argument(
        '--queue-factor', '-qf', type=float, default=1.0,
        help='Queuing factor to expand resource capacities (default 1.0)')

    # Intermediate model command
    intermediate_parser = subparsers.add_parser(
        'intermediate', help='Build intermediate model from Terraform files')
    intermediate_parser.add_argument(
        '--terraform', '-t', required=True, help='Directory containing Terraform files')
    intermediate_parser.add_argument(
        '--config', '-c', help='Path to configuration file')
    intermediate_parser.add_argument(
        '--var-files', '-v', nargs='+', help='Terraform variable files')
    intermediate_parser.add_argument(
        '--queue-factor', '-qf', type=float, default=1.0,
        help='Queuing factor to expand resource capacities (default 1.0)')
    intermediate_parser.add_argument(
        '--output', '-o', help='Output directory for intermediate model', default='parsed_infrastructure.json')
    intermediate_parser.add_argument(
        '--workload-rate', '-w', type=float,
        default=100.0, help='Base workload rate (requests/sec)')
    intermediate_parser.add_argument(
        '--workload-type', '-wt', choices=['steady', 'burst', 'cyclical', 'random'], default='steady',
        help='Type of workload to simulate (steady, burst, cyclical, or random)')

    # Analyze command
    analyze_parser = subparsers.add_parser(
        'analyze', help='Analyze existing simulation results')
    analyze_parser.add_argument(
        '--results', '-r', required=True, help='Path to simulation results file')
    analyze_parser.add_argument('--metrics', '-m', nargs='+', choices=['latency', 'energy', 'carbon', 'scaling', 'all'],
                                default=['all'], help='Metrics to analyze')

    # Export command
    export_parser = subparsers.add_parser(
        'export', help='Export simulation results to various formats')
    export_parser.add_argument(
        '--terraform', '-t', help='Directory containing Terraform files')
    export_parser.add_argument(
        '--format', '-f', choices=['json', 'csv', 'yaml'], default='json', help='Export format')
    export_parser.add_argument('--output', '-o', help='Output file path')
    export_parser.add_argument('--workload-rate', '-w', type=float,
                               default=100.0, help='Base workload rate (requests/sec)')

    # Diagram command
    diagram_parser = subparsers.add_parser(
        'diagram', help='Generate visualizations')
    diagram_parser.add_argument(
        '--terraform', '-t', required=True, help='Directory containing Terraform files')
    diagram_parser.add_argument('--types', choices=['class', 'deployment', 'all'], nargs='+', default=['all'],
                                help='Types of diagrams to generate')
    diagram_parser.add_argument(
        '--output', '-o', help='Output directory for diagrams')
    diagram_parser.add_argument('--workload-rate', '-w', type=float,
                                default=100.0, help='Base workload rate (requests/sec)')

    # Version command
    subparsers.add_parser('version', help='Display version information')

    # Clean command
    subparsers.add_parser('clean', help='Clean up temporary files')

    # Global options
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        default='WARNING', help='Logging level')
    parser.add_argument('--enable-logging', action='store_true', default=False,
                        help='Enable logging output (off by default)')

    return parser


def configure_logging(log_level: str) -> None:
    """
    Configure logging based on command-line option: adjust root logger and handlers.

    Args:
        log_level: Desired logging level
    """
    numeric_level = getattr(logging, log_level.upper(), None)
    if isinstance(numeric_level, int):
        root = logging.getLogger()
        root.setLevel(numeric_level)
        for handler in root.handlers:
            handler.setLevel(numeric_level)
        logger.info(f"Log level set to {log_level}")


def run_simulation(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Run simulation based on command-line arguments.

    Args:
        args: Command-line arguments

    Returns:
        Simulation results
    """
    logger.info(
        f"Starting simulation for Terraform directory: {args.terraform}")

    start_time = time.time()

    try:
        # Create framework instance
        framework = create_framework(args.config)

        # Load Terraform configuration
        terraform_data = framework.load_terraform(args.terraform, args.var_files)
        logger.info(
            f"Loaded {len(terraform_data.get('resources', {}))} resources from Terraform")

        # Use the helper function to configure workload
        workload_config = configure_workload_config(args)
        framework.configure_workload(workload_config)

        # Build model
        petri_net, model = framework.build_model(workload_rate=args.workload_rate)
        # Expand resource capacities by queueing factor
        if args.queue_factor and args.queue_factor != 1.0:
            for resource in model.resource_mapping.values():
                resource.capacity *= args.queue_factor
            logger.info(f"Applied queueing factor {args.queue_factor:.2f} to resource capacities")

        logger.info(
            f"Built model with {len(petri_net.places)} places and {len(petri_net.transitions)} transitions")

        # Export model
        model.export_tf_resources("parsed_infrastructure.json")

        # Run simulation (iterations parameter currently unsupported)
        results = framework.run_simulation(args.duration)

        # Export results
        if args.output:
            output_path = os.path.join(
                args.output, f"simulation_results_{int(time.time())}.json")
        else:
            output_path = None

        output_file = framework.export_results(output_path)
        logger.info(f"Results exported to {output_file}")

        elapsed_time = time.time() - start_time
        logger.info(f"Simulation completed in {elapsed_time:.2f} seconds")

        # Print simulation summary
        print("\n===== SIMULATION SUMMARY =====")
        # metrics_summary contains data from processed_metrics (based on avg utilization but now correctly calculating kWh)
        metrics_summary = framework.get_metrics_summary() 
        
        # Simulation Info (from raw results)
        sim_info = results.get('simulation_info', {})
        print(f"\n=== Simulation Info ===")
        print(f"Duration: {sim_info.get('duration', 0):.2f} seconds")
        print(f"Simulation Time Reached: {sim_info.get('simulation_time_reached', 0):.2f} seconds")
        print(f"Execution Time: {sim_info.get('execution_time', 0):.2f} seconds")
        # Corrected token counts
        total_tokens_sim_info = sim_info.get('total_tokens', 0)
        completed_tokens_sim_info = sim_info.get('completed_tokens', 0)
        active_tokens_sim_info = sim_info.get('active_tokens', 0)
        print(f"Total Tokens (in system + completed): {total_tokens_sim_info}")
        print(f"Completed Tokens: {completed_tokens_sim_info}")
        print(f"Active Tokens (at end): {active_tokens_sim_info}")


        # Latency Metrics (from processed_metrics via metrics_summary)
        if 'latency' in metrics_summary and metrics_summary['latency']:
            avg_lat = metrics_summary['latency'].get('average')
            p95 = metrics_summary['latency'].get('p95')
            print(f"\n=== Latency ===")
            if avg_lat is not None: print(f"Average Latency: {avg_lat:.2f} ms")
            if p95 is not None: print(f"95th Percentile Latency: {p95:.2f} ms")

        # Energy Metrics (from processed_metrics via metrics_summary)
        if 'energy' in metrics_summary and metrics_summary['energy']:
            total_energy_processed = metrics_summary['energy'].get('total_kwh', 0.0)
            avg_power_processed = 0
            if sim_info.get('duration', 0) > 0 and total_energy_processed is not None:
                avg_power_processed = total_energy_processed / (sim_info['duration'] / 3600.0) # kW
            
            print(f"\n=== Energy Consumption (from processed_metrics) ===")
            if total_energy_processed is not None: print(f"Total Energy: {total_energy_processed:.6f} kWh")
            print(f"Average Power: {avg_power_processed:.3f} kW")
        
        # Carbon Metrics (from processed_metrics via metrics_summary)
        if 'carbon' in metrics_summary and metrics_summary['carbon']:
            total_carbon_processed = metrics_summary['carbon'].get('total_co2eq_kg', 0.0)
            avg_emission_rate_processed = 0
            if sim_info.get('duration', 0) > 0 and total_carbon_processed is not None:
                 avg_emission_rate_processed = total_carbon_processed / (sim_info['duration'] / 3600.0) # kg CO2e/hour
            
            print(f"\n=== Carbon Footprint (from processed_metrics) ===")
            if total_carbon_processed is not None: print(f"Total Carbon Footprint: {total_carbon_processed:.6f} kg CO2e")
            print(f"Average Emission Rate: {avg_emission_rate_processed:.6f} kg CO2e/hour")

        processed_metrics_data = results.get("processed_metrics", {})
        resource_energy_from_processed = processed_metrics_data.get('energy', {}).get('resource_energy', {})
        resource_carbon_from_processed = processed_metrics_data.get('carbon', {}).get('resource_emissions', {})

        if 'resource_utilization' in results and results['resource_utilization']:
            print("\n=== Detailed Resource Metrics ===")
            for resource, util_stats in results['resource_utilization'].items():
                res_info = results.get('resource_info', {}).get(resource, {})
                res_type = res_info.get('type', 'unknown')
                res_region = res_info.get('region', 'unknown')
                print(f"\n{resource} ({res_type}, {res_region}):")
                print(f"  Utilization: {util_stats.get('avg', 0):.2%} (avg), {util_stats.get('max', 0):.2%} (max), {util_stats.get('min', 0):.2%} (min)")
                
                # Energy from processed_metrics
                energy_val = resource_energy_from_processed.get(resource)
                if energy_val is not None:
                    avg_power_val = 0
                    if sim_info.get('duration', 0) > 0:
                        avg_power_val = energy_val / (sim_info['duration'] / 3600.0)
                    print(f"  Energy (processed): {energy_val:.6f} kWh (avg power: {avg_power_val:.6f} kW)")
                else: # Fallback to raw results if not in processed
                    if 'resource_energy_consumption' in results and resource in results['resource_energy_consumption']:
                        energy_raw = results['resource_energy_consumption'][resource]
                        energy_total_raw = energy_raw.get('total_kwh', energy_raw.get('sum', 0))
                        print(f"  Energy (raw sum): {energy_total_raw:.6f} kWh")

                # Carbon from processed_metrics
                carbon_val = resource_carbon_from_processed.get(resource)
                if carbon_val is not None:
                    avg_carbon_rate_val = 0
                    if sim_info.get('duration', 0) > 0:
                        avg_carbon_rate_val = carbon_val / (sim_info['duration'] / 3600.0)
                    print(f"  Carbon (processed): {carbon_val:.6f} kg CO2e (avg rate: {avg_carbon_rate_val:.6f} kg CO2e/h)")
                else: # Fallback to raw results
                    if 'resource_carbon_footprint' in results and resource in results['resource_carbon_footprint']:
                        carbon_raw = results['resource_carbon_footprint'][resource]
                        carbon_total_raw = carbon_raw.get('total_kg_co2e', carbon_raw.get('sum', 0))
                        print(f"  Carbon (raw sum): {carbon_total_raw:.6f} kg CO2e")


        print(f"\nResults written to: {output_file}")

        return results
    except LeafCloudError as e:
        logger.error(f"Simulation command failed: {str(e)}", exc_info=True)
        print(f"Error during simulation: {str(e)}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.critical(f"An unexpected error occurred in the simulation command: {str(e)}", exc_info=True)
        print(f"An unexpected error occurred: {str(e)}. Please check logs for details.", file=sys.stderr)
        sys.exit(1)


def configure_workload_config(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Configure workload based on command-line arguments.
    
    Args:
        args: Command-line arguments
        
    Returns:
        Workload configuration dictionary
    """
    # Configure workload based on type
    workload_config = {
        "type": args.workload_type,
        "duration": args.duration,
        "params": {}
    }
    
    # Configure parameters based on workload type
    if args.workload_type == 'steady':
        workload_config["params"] = {"rate": args.workload_rate}
    
    elif args.workload_type == 'burst':
        # Default values for burst workload
        base_rate = args.workload_rate * 0.5  # 50% of the specified rate
        burst_rate = args.workload_rate * 5    # 5x the specified rate
        burst_duration = 60.0                  # 1 minute bursts
        burst_interval = 300.0                 # Every 5 minutes
        
        # Check for additional arguments
        if hasattr(args, 'burst_base_rate') and args.burst_base_rate is not None:
            base_rate = args.burst_base_rate
        if hasattr(args, 'burst_peak_rate') and args.burst_peak_rate is not None:
            burst_rate = args.burst_peak_rate
        if hasattr(args, 'burst_duration') and args.burst_duration is not None:
            burst_duration = args.burst_duration
        if hasattr(args, 'burst_interval') and args.burst_interval is not None:
            burst_interval = args.burst_interval
            
        workload_config["params"] = {
            "base_rate": base_rate,
            "burst_rate": burst_rate,
            "burst_duration": burst_duration,
            "burst_interval": burst_interval
        }
    
    elif args.workload_type == 'cyclical':
        # Default values for cyclical workload
        base_rate = args.workload_rate
        amplitude = args.workload_rate * 0.5   # 50% variation
        period = 3600.0                       # 1 hour cycle
        phase_shift = 0.0                      # No phase shift
        
        # Check for additional arguments
        if hasattr(args, 'cycle_amplitude') and args.cycle_amplitude is not None:
            amplitude = args.cycle_amplitude
        if hasattr(args, 'cycle_period') and args.cycle_period is not None:
            period = args.cycle_period
        if hasattr(args, 'cycle_phase') and args.cycle_phase is not None:
            phase_shift = args.cycle_phase
            
        workload_config["params"] = {
            "base_rate": base_rate,
            "amplitude": amplitude,
            "period": period,
            "phase_shift": phase_shift
        }
    
    elif args.workload_type == 'random':
        # Default values for random workload
        base_rate = args.workload_rate
        min_rate = max(args.workload_rate * 0.5, 1.0)  # 50% of base, minimum 1
        max_rate = args.workload_rate * 1.5           # 150% of base
        change_interval = 60.0                        # Change every minute
        
        # Check for additional arguments
        if hasattr(args, 'random_min_rate') and args.random_min_rate is not None:
            min_rate = args.random_min_rate
        if hasattr(args, 'random_max_rate') and args.random_max_rate is not None:
            max_rate = args.random_max_rate
        if hasattr(args, 'random_change_interval') and args.random_change_interval is not None:
            change_interval = args.random_change_interval
            
        workload_config["params"] = {
            "base_rate": base_rate,
            "min_rate": min_rate,
            "max_rate": max_rate,
            "change_interval": change_interval
        }
        
    elif args.workload_type == 'custom':
        # Default values for custom workload
        base_rate = args.workload_rate
        scale_factor = 1.0
        pattern_file = None
        pattern = None
        
        # Check for additional arguments
        if hasattr(args, 'custom_scale_factor') and args.custom_scale_factor is not None:
            scale_factor = args.custom_scale_factor
            
        if hasattr(args, 'custom_pattern_file') and args.custom_pattern_file is not None:
            pattern_file = args.custom_pattern_file
            try:
                import json
                with open(pattern_file, 'r') as f:
                    pattern = json.load(f)
                logger.info(f"Loaded custom workload pattern from {pattern_file}")
            except Exception as e:
                logger.error(f"Failed to load custom pattern file: {e}")
                logger.warning("Using default pattern")
                pattern_file = None
                pattern = None
        
        # Default pattern if no file is provided or loading failed
        if pattern is None:
            logger.info("Using default alternating pattern for custom workload")
            pattern = [
                {"time": 0, "rate": base_rate * 0.5},
                {"time": 300, "rate": base_rate * 1.5},
                {"time": 600, "rate": base_rate * 0.5},
                {"time": 900, "rate": base_rate * 1.5},
                {"time": 1200, "rate": base_rate * 0.5}
            ]
        
        workload_config["params"] = {
            "base_rate": base_rate,
            "scale_factor": scale_factor,
            "pattern": pattern
        }
    
    elif args.workload_type == 'mix':
        # For mix, we'll default to a combination of steady and burst
        mix_config_file = None
        
        # Check for mix configuration file
        if hasattr(args, 'mix_config_file') and args.mix_config_file is not None:
            mix_config_file = args.mix_config_file
            try:
                import json
                with open(mix_config_file, 'r') as f:
                    mix_config = json.load(f)
                workload_config["params"] = mix_config
                logger.info(f"Loaded mix workload configuration from {mix_config_file}")
            except Exception as e:
                logger.error(f"Failed to load mix configuration file: {e}")
                logger.warning("Using default mix configuration")
                # Fall back to default configuration
                mix_config_file = None
        
        # Use default configuration if no file is provided or loading failed
        if mix_config_file is None:
            workload_config["params"] = {
                "workloads": [
                    {
                        "type": "steady",
                        "params": {"rate": args.workload_rate * 0.7}  # 70% steady load
                    },
                    {
                        "type": "burst",
                        "params": {
                            "base_rate": args.workload_rate * 0.1,     # 10% base
                            "burst_rate": args.workload_rate * 2,      # 2x bursts
                            "burst_duration": 120.0,                   # 2 minute bursts
                            "burst_interval": 900.0                    # Every 15 minutes
                        }
                    }
                ]
            }
    
    else:  # Default to steady for any unsupported type
        logger.warning(f"Workload type '{args.workload_type}' not fully supported. Using provided parameters or defaults.")
        workload_config["params"] = {"rate": args.workload_rate}
        
    return workload_config


def build_intermediate_model(args: argparse.Namespace) -> None:
    """
    Build an intermediate model based on Terraform files.

    Args:
        args: Command-line arguments
    """
    start_time = time.time()
    logger.info(
        f"Building intermediate model for Terraform directory: {args.terraform}")

    try:
        # Create framework instance
        framework = create_framework(args.config)

        # Load Terraform configuration
        terraform_data = framework.load_terraform(args.terraform, args.var_files)
        logger.info(
            f"Loaded {len(terraform_data.get('resources', {}))} resources from Terraform")

        # Configure workload based on type
        workload_config = configure_workload_config(args)
        framework.configure_workload(workload_config)

        # Build model
        # after:
        petri_net, model = framework.build_model(workload_rate=args.workload_rate)

        # Expand resource capacities by queueing factor
        if args.queue_factor and args.queue_factor != 1.0:
            for resource in model.resource_mapping.values():
                resource.capacity *= args.queue_factor
            logger.info(f"Applied queueing factor {args.queue_factor:.2f} to resource capacities")

        logger.info(
            f"Built model with {len(petri_net.places)} places and {len(petri_net.transitions)} transitions")

        # Export model
        model.export_tf_resources(args.output)

        elapsed_time = time.time() - start_time
        logger.info(
            f"Intermediate model built and exported to {args.output} in {elapsed_time:.2f} seconds")
        print(f"Intermediate model successfully built and exported to: {args.output}")

    except LeafCloudError as e:
        logger.error(f"Build intermediate model command failed: {str(e)}", exc_info=True)
        print(f"Error building intermediate model: {str(e)}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.critical(f"An unexpected error occurred in the build intermediate model command: {str(e)}", exc_info=True)
        print(f"An unexpected error occurred: {str(e)}. Please check logs for details.", file=sys.stderr)
        sys.exit(1)

    print(f"Intermediate model written to: {args.output}")
    print(f"Time taken: {elapsed_time:.2f} seconds")

    return


def analyze_results(args: argparse.Namespace) -> None:
    """
    Analyze existing simulation results with enhanced visualizations and insights.

    Args:
        args: Command-line arguments
    """
    logger.info(f"Analyzing results from file: {args.results}")

    try:
        with open(args.results, 'r') as f:
            if args.results.endswith('.json'):
                results_data = json.load(f)
            elif args.results.endswith(('.yaml', '.yml')):
                results_data = yaml.safe_load(f)
            else:
                # This specific ValueError will be caught by the broader ValueError below if not handled separately
                raise ValueError(f"Unsupported file format for {args.results}. Please use JSON or YAML.")

        if not results_data or not isinstance(results_data, dict):
            logger.error(f"No valid data or incorrect format in results file: {args.results}. Aborting analysis.")
            print(f"Error: No valid data or incorrect format found in {args.results}. Analysis cannot proceed.", file=sys.stderr)
            sys.exit(1)

    except FileNotFoundError:
        logger.error(f"Results file not found: {args.results}", exc_info=True)
        print(f"Error: Results file not found at {args.results}", file=sys.stderr)
        sys.exit(1)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        logger.error(f"Error decoding results file {args.results}: {str(e)}", exc_info=True)
        print(f"Error: Could not parse {args.results}. The file may be corrupted or not valid JSON/YAML. Details: {str(e)}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e: # Catches the unsupported format error specifically or other ValueErrors
        logger.error(f"Invalid value or unsupported file format for {args.results}: {str(e)}", exc_info=True)
        print(f"Error: {str(e)}", file=sys.stderr)
        sys.exit(1)
    except IOError as e:
        logger.error(f"IO error reading results file {args.results}: {str(e)}", exc_info=True)
        print(f"Error: Could not read results file {args.results}. An I/O error occurred: {str(e)}", file=sys.stderr)
        sys.exit(1)
    except Exception as e: # Catch-all for any other unexpected errors during file loading
        logger.critical(f"An unexpected error occurred while loading results file {args.results}: {str(e)}", exc_info=True)
        print(f"An unexpected error occurred while loading {args.results}. Please check logs. Details: {str(e)}", file=sys.stderr)
        sys.exit(1)

    # If file loading was successful, proceed with analysis using results_data
    try:
        # Print header
        print("\n")
        print("=" * 80)
        print("DETAILED SIMULATION ANALYSIS REPORT".center(80))
        print("=" * 80)
        print(f"Results file: {args.results}")
        print("-" * 80)

        # Rename results_data to results for the rest of the function for minimal changes to existing logic
        results = results_data

        # Base analyses
        metrics_to_analyze = args.metrics if hasattr(args, 'metrics') else ['all']
        
        # Always analyze simulation info
        if 'simulation_info' in results:
            analyze_simulation_info(results['simulation_info'])
        
        # Analyze workload stats if present
        if 'workload_stats' in results:
            analyze_workload_stats(results['workload_stats'])
        
        # Analyze based on requested metrics
        if 'all' in metrics_to_analyze or 'latency' in metrics_to_analyze:
            if 'latency_metrics' in results:
                analyze_latency(results['latency_metrics'])
        
        if 'all' in metrics_to_analyze or 'energy' in metrics_to_analyze:
            if 'energy_metrics' in results:
                analyze_energy(results['energy_metrics'])
        
        if 'all' in metrics_to_analyze or 'carbon' in metrics_to_analyze:
            if 'carbon_metrics' in results:
                analyze_carbon(results['carbon_metrics'])
        
        if 'all' in metrics_to_analyze or 'scaling' in metrics_to_analyze:
            if 'scaling_metrics' in results:
                analyze_scaling(results['scaling_metrics'])
        
        if 'all' in metrics_to_analyze or 'utilization' in metrics_to_analyze:
            if 'resource_utilization' in results:
                analyze_resource_utilization(results['resource_utilization'])
        
        if 'all' in metrics_to_analyze or 'states' in metrics_to_analyze:
            if 'resource_states' in results:
                analyze_resource_states(results['resource_states'])
        
        # Additional analyses and insights
        perform_additional_analysis(results)
        
        # Final summary and recommendations
        generate_summary_and_recommendations(results)

        print("\n" + "=" * 80)
        print("END OF ANALYSIS REPORT".center(80))
        print("=" * 80 + "\n")

    except Exception as e:
        logger.error(f"Error during results analysis: {str(e)}", exc_info=True)
        print(f"An error occurred during the analysis of results: {str(e)}. Please check logs for details.", file=sys.stderr)
        sys.exit(1)


def export_results(args: argparse.Namespace) -> None:
    """
    Export simulation results to specified format.

    Args:
        args: Command-line arguments
    """
    if not args.terraform and not args.output:
        logger.error("Either --terraform or --output must be specified for export.")
        print("Error: Either specify a Terraform directory (--terraform) to run a new simulation and export, "
              "or an existing results file (--output) to re-export.", file=sys.stderr)
        sys.exit(1)

    # Branch 1: New Simulation and Export
    if args.terraform:
        try:
            logger.info(f"Running simulation first using Terraform directory: {args.terraform}")
            framework = create_framework(args.config) # Use config
            framework.load_terraform(args.terraform, getattr(args, 'var_files', None))
            
            workload_config = configure_workload_config(args) # Use helper for consistency
            framework.configure_workload(workload_config)

            _, model = framework.build_model()
            # Optional: Export intermediate model. Make filename unique.
            if hasattr(model, 'export_tf_resources'):
                 tf_resources_export_path = f"intermediate_tf_resources_export_{int(time.time())}.json"
                 model.export_tf_resources(tf_resources_export_path)
                 logger.info(f"Intermediate Terraform resources exported to {tf_resources_export_path}")

            simulation_results_data = framework.run_simulation()

            if not simulation_results_data:
                logger.error("Simulation did not produce any results to export.")
                print("Error: Simulation completed but produced no results. Cannot export.", file=sys.stderr)
                sys.exit(1)

            export_format = args.format.lower()
            if args.output:
                output_path = args.output
                output_dir = os.path.dirname(output_path)
                if output_dir and not os.path.exists(output_dir):
                    os.makedirs(output_dir, exist_ok=True)
                    logger.info(f"Created directory for output: {output_dir}")
            else:
                timestamp = int(time.time())
                if export_format == 'json':
                    output_path = f"simulation_results_{timestamp}.json"
                elif export_format == 'csv':
                    output_path = f"simulation_results_{timestamp}.csv"
                elif export_format == 'yaml':
                    output_path = f"simulation_results_{timestamp}.yaml"
                else:
                    logger.error(f"Unsupported export format: {args.format}")
                    print(f"Error: Unsupported export format '{args.format}'. Please use 'json', 'csv', or 'yaml'.", file=sys.stderr)
                    sys.exit(1)
            
            # Use the results from run_simulation() for export
            framework.export_results(output_path, file_format=export_format, results_data=simulation_results_data)

            logger.info(f"Simulation results successfully exported to {output_path} in {export_format} format.")
            print(f"Simulation results successfully exported to: {output_path}")

        except LeafCloudError as e:
            logger.error(f"Export command failed during LEAF-Cloud operation: {str(e)}", exc_info=True)
            print(f"Error during export: {str(e)}", file=sys.stderr)
            sys.exit(1)
        except IOError as e:
            logger.error(f"File I/O error during export: {str(e)}", exc_info=True)
            print(f"Error writing export file: {str(e)}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            logger.critical(f"An unexpected error occurred during simulation and export: {str(e)}", exc_info=True)
            print(f"An unexpected error occurred: {str(e)}. Please check logs for details.", file=sys.stderr)
            sys.exit(1)

    # Branch 2: Re-export Existing Results
    else: # args.output is the source file
        source_file_path = args.output
        target_export_format = args.format.lower()
        logger.info(f"Re-exporting existing results from {source_file_path} to format: {target_export_format}")

        loaded_results_data = None
        try:
            with open(source_file_path, 'r') as f:
                if source_file_path.lower().endswith('.json'):
                    loaded_results_data = json.load(f)
                elif source_file_path.lower().endswith(('.yaml', '.yml')):
                    loaded_results_data = yaml.safe_load(f)
                else:
                    raise ValueError(f"Unsupported source file format: {source_file_path}. Can only load from JSON or YAML.")
            
            if not loaded_results_data or not isinstance(loaded_results_data, dict):
                logger.error(f"No valid data or incorrect format in source file: {source_file_path}.")
                print(f"Error: No valid data or incorrect format found in {source_file_path}. Re-export cannot proceed.", file=sys.stderr)
                sys.exit(1)

        except FileNotFoundError:
            logger.error(f"Source results file not found: {source_file_path}", exc_info=True)
            print(f"Error: Source results file not found at {source_file_path}", file=sys.stderr)
            sys.exit(1)
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            logger.error(f"Error decoding source results file {source_file_path}: {str(e)}", exc_info=True)
            print(f"Error: Could not parse {source_file_path}. File may be corrupted or not valid JSON/YAML. Details: {str(e)}", file=sys.stderr)
            sys.exit(1)
        except ValueError as e:
            logger.error(f"ValueError loading source file {source_file_path}: {str(e)}", exc_info=True)
            print(f"Error: {str(e)}", file=sys.stderr)
            sys.exit(1)
        except IOError as e:
            logger.error(f"IO error reading source results file {source_file_path}: {str(e)}", exc_info=True)
            print(f"Error: Could not read source file {source_file_path}. An I/O error occurred: {str(e)}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            logger.critical(f"An unexpected error occurred loading source file {source_file_path}: {str(e)}", exc_info=True)
            print(f"An unexpected error occurred loading {source_file_path}. Please check logs. Details: {str(e)}", file=sys.stderr)
            sys.exit(1)

        try:
            base, _ = os.path.splitext(source_file_path)
            # If user specifies args.reexport_output_path, use it. Otherwise, generate one.
            # Assuming CLI might have args.reexport_output for this specific scenario.
            # For now, always generate a new name to be safe.
            target_output_path = getattr(args, 'reexport_output', f"{base}_reexported.{target_export_format}")
            if target_output_path == source_file_path and os.path.splitext(source_file_path)[1].lstrip('.').lower() == target_export_format:
                 logger.warning(f"Source and target for re-export are identical ({target_output_path}) with the same format. Re-exporting anyway.")
            elif target_output_path == source_file_path:
                 target_output_path = f"{base}_reexported.{target_export_format}"
                 logger.info(f"Target for re-export was same as source; using new path: {target_output_path}")

            target_output_dir = os.path.dirname(target_output_path)
            if target_output_dir and not os.path.exists(target_output_dir):
                os.makedirs(target_output_dir, exist_ok=True)

            if target_export_format == 'json':
                with open(target_output_path, 'w') as f:
                    json.dump(loaded_results_data, f, indent=4)
            elif target_export_format == 'csv':
                try:
                    import pandas as pd
                    # This conversion might need to be more robust based on actual data structure
                    if isinstance(loaded_results_data, list):
                        df = pd.DataFrame(loaded_results_data)
                    elif isinstance(loaded_results_data, dict):
                        # Attempt to make it a list of one record, or handle specific structures
                        df = pd.DataFrame([loaded_results_data]) 
                    else:
                        raise ValueError("Data structure not directly mappable to CSV table.")
                    df.to_csv(target_output_path, index=False)
                except ImportError:
                    logger.error("Pandas library is required for CSV export but not found.")
                    print("Error: Pandas library not found. Install with 'pip install pandas'.", file=sys.stderr)
                    sys.exit(1)
                except ValueError as e:
                    logger.error(f"Could not convert data to CSV for {target_output_path}: {str(e)}", exc_info=True)
                    print(f"Error: Data from {source_file_path} could not be converted to CSV. {str(e)}", file=sys.stderr)
                    sys.exit(1)
            elif target_export_format == 'yaml':
                with open(target_output_path, 'w') as f:
                    yaml.dump(loaded_results_data, f, default_flow_style=False)
            else:
                logger.error(f"Unsupported target export format: {args.format}")
                print(f"Error: Unsupported target export format '{args.format}'. Use 'json', 'csv', or 'yaml'.", file=sys.stderr)
                sys.exit(1)
            
            logger.info(f"Results from {source_file_path} successfully re-exported to {target_output_path} in {target_export_format} format.")
            print(f"Results from {source_file_path} successfully re-exported to: {target_output_path}")

        except IOError as e:
            logger.error(f"File I/O error during re-export to {target_output_path}: {str(e)}", exc_info=True)
            print(f"Error writing re-export file {target_output_path}: {str(e)}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            logger.critical(f"An unexpected error occurred during re-export: {str(e)}", exc_info=True)
            print(f"An unexpected error occurred during re-export. Please check logs. Details: {str(e)}", file=sys.stderr)
            sys.exit(1)


def generate_diagrams(args: argparse.Namespace) -> None:
    """
    Generate visualizations based on Terraform files.

    Args:
        args: Command-line arguments
    """
    logger.info(f"Attempting to generate diagrams for Terraform directory: {args.terraform}")
    try:
        # Create framework instance using config
        framework = create_framework(args.config)

        # Load Terraform configuration, allowing for var_files
        framework.load_terraform(args.terraform, getattr(args, 'var_files', None))

        # Build model
        # The model itself isn't directly used here other than its existence being a prerequisite for visualization
        _, model = framework.build_model() # build_model returns results, model
        if not model:
            logger.error("Model building failed or returned no model. Cannot generate diagrams.")
            print("Error: Failed to build the model from Terraform. Diagrams cannot be generated.", file=sys.stderr)
            sys.exit(1)

        # Determine diagram types
        diagram_types_to_generate = args.types
        if 'all' in diagram_types_to_generate:
            # Assuming 'class' and 'deployment' are the primary supported types for 'all'
            # This list might need to be dynamic based on framework capabilities in the future
            diagram_types_to_generate = ['class', 'deployment'] 
        
        # Ensure diagram_types_to_generate is a list, even if one type is specified
        if not isinstance(diagram_types_to_generate, list):
            diagram_types_to_generate = [diagram_types_to_generate]

        output_dir = args.output or "diagrams"
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"Diagrams will be saved in: {os.path.abspath(output_dir)}")

        generated_files_paths = []
        valid_diagrams_generated_count = 0

        for diagram_type in diagram_types_to_generate:
            diagram_type_lower = diagram_type.lower()
            # Generate a unique filename for each diagram to avoid overwrites in quick succession
            timestamp = int(time.time())
            # Default extension, can be overridden by specific visualizers if they return full path
            output_filename = f"{diagram_type_lower}_diagram_{timestamp}.png" 
            output_path = os.path.join(output_dir, output_filename)
            
            logger.info(f"Generating {diagram_type_lower} diagram at {output_path}...")
            # The generate_visualization might need to be more flexible or specific about types
            # For now, assuming it takes a string like 'class_diagram' or 'deployment_diagram'
            # We'll map common names to what the framework might expect
            framework_diagram_key = diagram_type_lower # Or map: e.g., if diagram_type_lower == 'class' -> 'class_diagram'
            if diagram_type_lower == 'class':
                 framework_diagram_key = 'class_diagram'
            elif diagram_type_lower == 'deployment':
                 framework_diagram_key = 'deployment_diagram'
            # Add more mappings if needed or make generate_visualization accept 'class', 'deployment' directly

            framework.generate_visualization(framework_diagram_key, output_path)
            generated_files_paths.append(output_path)
            valid_diagrams_generated_count += 1
            logger.info(f"Successfully generated {diagram_type_lower} diagram: {output_path}")

        if valid_diagrams_generated_count > 0:
            logger.info(f"Successfully generated {valid_diagrams_generated_count} diagram(s).")
            print("\n===== GENERATED DIAGRAMS =====")
            for file_path in generated_files_paths:
                print(f" - {os.path.abspath(file_path)}")
        else:
            logger.warning("No diagrams were generated. This might be due to unrecognized types or other issues.")
            print("Warning: No diagrams were generated. Please check the specified types and logs.", file=sys.stderr)

    except LeafCloudError as e:
        logger.error(f"Diagram generation failed due to a LEAF-Cloud framework error: {str(e)}", exc_info=True)
        print(f"Error during diagram generation: {str(e)}", file=sys.stderr)
        sys.exit(1)
    except (IOError, OSError) as e:
        logger.error(f"File system error during diagram generation: {str(e)}", exc_info=True)
        print(f"Error interacting with the file system (e.g., creating directory or writing file): {str(e)}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.critical(f"An unexpected error occurred during diagram generation: {str(e)}", exc_info=True)
        print(f"An unexpected error occurred: {str(e)}. Please check logs for details.", file=sys.stderr)
        sys.exit(1)


def show_version() -> None:
    """Display version information for the LEAF-Cloud framework."""
    # These would be defined elsewhere or imported
    version = "1.0.0"
    build_date = "2025-04-21"

    print("\n===== LEAF-CLOUD FRAMEWORK =====")
    print(f"Version: {version}")
    print(f"Build Date: {build_date}")
    print("Layered Eco-centric Analytical Framework for Cloud Infrastructure")


def cleanup() -> None:
    """
    Clean up temporary files and results.
    Cleans up any temporary files or results generated during the simulation
    or analysis process.
    """
    logger.info("Starting cleanup of temporary files and results...")

    # Helper function to clean contents of a directory
    def clean_directory_contents(dir_path: str):
        if not os.path.isdir(dir_path):
            logger.info(f"Directory '{dir_path}' not found, nothing to clean from it.")
            return
        
        logger.info(f"Cleaning contents of directory: {dir_path}")
        for item_name in os.listdir(dir_path):
            item_path = os.path.join(dir_path, item_name)
            try:
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.remove(item_path)
                    logger.debug(f"Removed file/link: {item_path}")
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                    logger.debug(f"Removed directory: {item_path}")
            except FileNotFoundError:
                logger.warning(f"Item not found during cleanup (possibly already removed): {item_path}")
            except OSError as e:
                logger.error(f"Error removing item {item_path}: {str(e)}", exc_info=True)
            except Exception as e:
                logger.error(f"Unexpected error removing item {item_path}: {str(e)}", exc_info=True)

    # Clean contents of 'results' directory
    results_dir = "results"
    try:
        clean_directory_contents(results_dir)
    except Exception as e:
        logger.error(f"An unexpected error occurred while cleaning '{results_dir}': {str(e)}", exc_info=True)
        # Continue to next cleanup step despite this error

    # Clean contents of 'diagrams' directory
    diagrams_dir = "diagrams"
    try:
        clean_directory_contents(diagrams_dir)
    except Exception as e:
        logger.error(f"An unexpected error occurred while cleaning '{diagrams_dir}': {str(e)}", exc_info=True)
        # Continue to next cleanup step

    # Files to remove
    files_to_remove = ["parsed_infrastructure.json", "model.json", "intermediate_tf_resources_export.json"]
    # Add other potential temp files if they become standard, e.g., unique intermediate model exports
    # For now, we'll also look for the timestamped intermediate model export if it was created by export_results
    # This is a bit of a guess; ideally, such files would be tracked or put in a temp dir.
    # Example: intermediate_tf_resources_export_*.json
    # We can use glob to find these, but for simplicity, let's list known ones.
    # The timestamped one from export_results is not cleaned here as its name is dynamic.
    # Consider a dedicated temp directory for such dynamic files if cleanup is critical.

    for file_path in files_to_remove:
        logger.info(f"Attempting to remove file: {file_path}")
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Successfully removed file: {file_path}")
            else:
                logger.info(f"File not found, nothing to remove: {file_path}")
        except FileNotFoundError: # Should be caught by os.path.exists, but good for race conditions
            logger.info(f"File not found (possibly already removed): {file_path}")
        except OSError as e:
            logger.error(f"Error removing file {file_path}: {str(e)}", exc_info=True)
        except Exception as e:
            logger.error(f"Unexpected error removing file {file_path}: {str(e)}", exc_info=True)
            
    logger.info("Cleanup process finished.")
    # No explicit return needed as it's -> None
    

def main() -> int:
    parser = setup_parser()
    args = parser.parse_args()

    # Configure logging: re-enable and set level if enabled, else disable all
    if args.enable_logging:
        logging.disable(logging.NOTSET)
        configure_logging(args.log_level)
        matplotlib_logger = logging.getLogger('matplotlib')
        matplotlib_logger.setLevel(logging.WARNING)
        logger.debug("Set matplotlib logger level to WARNING to reduce verbosity.")
    else:
        logging.disable(logging.CRITICAL)

    try:
        # Process commands
        if args.command == 'simulate':
            run_simulation(args)
        elif args.command == 'analyze':
            analyze_results(args)
        elif args.command == 'export':
            export_results(args)
        elif args.command == 'diagram':
            generate_diagrams(args)
        elif args.command == 'version':
            show_version()
        elif args.command == 'clean':
            # Clean up results and temporary files
            cleanup()
        elif args.command == 'intermediate':
            build_intermediate_model(args)
        else:
            parser.print_help()
            return 1

        return 0

    except Exception as e:
        logger.error(f"Error executing command '{args.command}': {str(e)}")
        print(f"Error: {str(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
