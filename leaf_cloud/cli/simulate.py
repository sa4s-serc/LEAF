"""
Module for handling the 'simulate' command of the LEAF-Cloud CLI.

This module defines the command-line interface for running simulations,
including parsing arguments for various workload types and executing the
simulation workflow.
"""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, cast

import click

from ..exceptions import LeafCloudError
from ..simulation.core import run_simulation_core

logger = logging.getLogger(__name__)

# Type aliases
ClickContext = click.core.Context
ClickGroup = click.core.Group


def register_commands(cli: ClickGroup) -> None:
    """Register the 'simulate' command with the main Click group.
    
    Args:
        cli: The main Click group to register the command with.
    """
    @cli.group(
        name="simulate",
        help="Run a simulation based on Terraform or Kubernetes inputs.",
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    @click.option(
        "--terraform",
        "-t",
        type=click.Path(
            exists=True, file_okay=True, dir_okay=True, path_type=Path, resolve_path=True
        ),
        required=False,
        help="Directory containing Terraform files or path to a Terraform plan.json file.",
    )
    @click.option(
        "--input",
        "-I",
        type=click.Path(
            exists=True, file_okay=True, dir_okay=True, path_type=Path, resolve_path=True
        ),
        help="Infrastructure input when not using Terraform (e.g., Kubernetes manifests).",
    )
    @click.option(
        "--input-type",
        type=click.Choice(["terraform", "kubernetes", "mixed"], case_sensitive=False),
        help="Type of infrastructure input provided via --terraform/--input. Use 'mixed' to load both.",
    )
    @click.option(
        "--kubeconfig",
        type=click.Path(
            exists=True, file_okay=True, dir_okay=False, path_type=Path, resolve_path=True
        ),
        help="Optional kubeconfig path for resolving cluster context when using Kubernetes manifests.",
    )
    @click.option(
        "--config",
        "-c",
        type=click.Path(
            exists=True, file_okay=True, dir_okay=False, path_type=Path, resolve_path=True
        ),
        help="Path to the simulation configuration file.",
    )
    @click.option(
        "--iterations",
        "-i",
        type=int,
        help="The number of simulation iterations.",
    )
    @click.option(
        "--output-dir",
        "-o",
        type=click.Path(file_okay=False, path_type=Path, resolve_path=True),
        default="results",
        show_default=True,
        help="Directory to save simulation results.",
    )
    @click.option(
        "--duration",
        type=float,
        help="Simulation duration in seconds (default: 3600.0).",
    )
    @click.option(
        "--enable-cost-analysis",
        is_flag=True,
        help="Enable cost estimation using Infracost CLI.",
    )
    @click.option(
        "--infracost-usage-file",
        type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path, resolve_path=True),
        help="Path to Infracost usage file for custom usage estimates.",
    )
    @click.option(
        "--end-to-end-latency",
        is_flag=True,
        help="Enable end-to-end latency modeling including user-to-infrastructure latency.",
    )
    @click.option(
        "--user-distribution",
        type=str,
        help="User geographic distribution as JSON string or file path. Format: {\"region1\": 0.4, \"region2\": 0.6}",
    )
    @click.option(
        "--mode",
        type=click.Choice(['heuristic', 'detailed'], case_sensitive=False),
        default='heuristic',
        show_default=True,
        help="Simulation mode: 'heuristic' for fast estimation or 'detailed' for full Petri net simulation.",
    )
    @click.option(
        "--debug",
        is_flag=True,
        help="Enable debug mode with verbose logging.",
    )
    @click.option(
        "--fallback-node-type",
        type=str,
        default="e2-standard-2",
        show_default=True,
        help="Machine type to assume for synthetic GKE nodes when Terraform nodes are unavailable.",
    )
    @click.option(
        "--fallback-node-region",
        type=str,
        default="us-central1",
        show_default=True,
        help="Region to assign to synthetic fallback nodes.",
    )
    @click.option(
        "--fallback-node-count",
        type=int,
        default=0,
        show_default=True,
        help="Explicit number of fallback nodes to assume (0 = autosize based on workload).",
    )
    @click.option(
        "--fallback-node-min",
        type=int,
        help="Minimum number of fallback nodes to synthesize when autosizing.",
    )
    @click.option(
        "--fallback-node-max",
        type=int,
        help="Maximum number of fallback nodes to synthesize when autosizing.",
    )
    @click.option(
        "--fallback-node-cpu-headroom",
        type=float,
        default=0.75,
        show_default=True,
        help="Target per-node CPU utilization when sizing fallback clusters.",
    )
    @click.option(
        "--fallback-node-memory-headroom",
        type=float,
        default=0.8,
        show_default=True,
        help="Target per-node memory utilization when sizing fallback clusters.",
    )
    @click.option(
        "--disable-k8s-node-fallback",
        is_flag=True,
        help="Disable automatic synthesis of GKE nodes for manifests-only simulations.",
    )
    @click.pass_context
    def simulate_cmd(
        ctx: ClickContext,
        terraform: Optional[Path],
        input: Optional[Path],
        input_type: Optional[str],
        kubeconfig: Optional[Path],
        config: Optional[Path],
        iterations: Optional[int],
        output_dir: Path,
        duration: Optional[float],
        enable_cost_analysis: bool,
        infracost_usage_file: Optional[Path],
        end_to_end_latency: bool,
        user_distribution: Optional[str],
        mode: str,
        debug: bool,
        fallback_node_type: str,
        fallback_node_region: str,
        fallback_node_count: int,
        fallback_node_min: Optional[int],
        fallback_node_max: Optional[int],
        fallback_node_cpu_headroom: float,
        fallback_node_memory_headroom: float,
        disable_k8s_node_fallback: bool,
    ) -> None:
        """Run a simulation with the specified workload type and parameters."""
        # Store common parameters in the context
        ctx.ensure_object(dict)
        resolved_input_type = (input_type or "").lower() if input_type else None

        terraform_path = terraform
        input_path = input

        if resolved_input_type is None:
            if terraform_path and input_path:
                resolved_input_type = "mixed"
            elif terraform_path:
                resolved_input_type = "terraform"
            elif input_path:
                resolved_input_type = "kubernetes"
        if resolved_input_type == "terraform" and terraform_path is None:
            terraform_path = input_path
        if resolved_input_type == "kubernetes" and input_path is None:
            input_path = terraform_path
        if resolved_input_type == "mixed":
            if terraform_path is None or input_path is None:
                raise click.UsageError("Mixed input requires both --terraform and --input paths.")

        if resolved_input_type == "terraform":
            if terraform_path is None:
                raise click.UsageError("Terraform input type requires a --terraform path.")
        elif resolved_input_type == "kubernetes":
            if input_path is None:
                raise click.UsageError("Kubernetes input type requires --input or --terraform pointing to manifests.")
        elif resolved_input_type == "mixed":
            # Paths already validated above
            pass

        if resolved_input_type is None:
            raise click.UsageError("Provide --terraform, --input, or both to define the infrastructure input.")

        ctx.obj["terraform"] = terraform_path
        ctx.obj["input_path"] = input_path
        ctx.obj["input_type"] = resolved_input_type
        ctx.obj["kubeconfig"] = kubeconfig
        ctx.obj["config"] = config
        ctx.obj["iterations"] = iterations
        ctx.obj["output_dir"] = output_dir
        ctx.obj["duration"] = duration
        ctx.obj["enable_cost_analysis"] = enable_cost_analysis
        ctx.obj["infracost_usage_file"] = infracost_usage_file
        ctx.obj["end_to_end_latency"] = end_to_end_latency
        ctx.obj["user_distribution"] = user_distribution
        ctx.obj["mode"] = mode
        ctx.obj["debug"] = debug

    # Add common latency options
    def latency_options(func):
        func = click.option(
            "--latency-stochastic",
            is_flag=True,
            help="Enable stochastic latency variation.",
        )(func)
        func = click.option(
            "--latency-distribution",
            type=click.Choice(["uniform", "normal"], case_sensitive=False),
            default="uniform",
            show_default=True,
            help="Distribution type for latency variation.",
        )(func)
        func = click.option(
            "--latency-uniform-min",
            type=float,
            default=-0.1,
            show_default=True,
            help="Minimum bound for uniform distribution.",
        )(func)
        func = click.option(
            "--latency-uniform-max",
            type=float,
            default=0.1,
            show_default=True,
            help="Maximum bound for uniform distribution.",
        )(func)
        func = click.option(
            "--latency-normal-mean",
            type=float,
            default=0.0,
            show_default=True,
            help="Mean for normal distribution.",
        )(func)
        func = click.option(
            "--latency-normal-stddev",
            type=float,
            default=0.05,
            show_default=True,
            help="Standard deviation for normal distribution.",
        )(func)
        func = click.option(
            "--latency-apply-to",
            type=str,
            multiple=True,
            default=["all"],
            show_default=True,
            help="Resource types to apply stochastic latency to.",
        )(func)
        return func

    # Steady workload subcommand
    @simulate_cmd.command("steady", help="Simulate a steady, constant workload.")
    @click.option(
        "--workload-rate",
        "-w",
        type=float,
        default=100.0,
        show_default=True,
        help="Constant workload rate in requests/sec.",
    )
    @latency_options
    @click.pass_context
    def steady_workload(
        ctx: ClickContext,
        workload_rate: float,
        **latency_kwargs,
    ) -> None:
        """Run a simulation with a steady workload."""
        ctx.obj.update(
            {
                "workload_type": "steady",
                "workload_rate": workload_rate,
                **latency_kwargs,
            }
        )
        execute(ctx)

    # Burst workload subcommand
    @simulate_cmd.command(
        "burst", help="Simulate a workload with periodic bursts of high traffic."
    )
    @click.option(
        "--base-rate",
        type=float,
        required=True,
        help="Base rate for burst workload.",
    )
    @click.option(
        "--peak-rate",
        type=float,
        required=True,
        help="Peak rate during bursts.",
    )
    @click.option(
        "--burst-duration",
        type=float,
        required=True,
        help="Duration of each burst in seconds.",
    )
    @click.option(
        "--burst-interval",
        type=float,
        required=True,
        help="Interval between bursts in seconds.",
    )
    @latency_options
    @click.pass_context
    def burst_workload(
        ctx: ClickContext,
        base_rate: float,
        peak_rate: float,
        burst_duration: float,
        burst_interval: float,
        **latency_kwargs,
    ) -> None:
        """Run a simulation with a bursty workload."""
        ctx.obj.update(
            {
                "workload_type": "burst",
                "burst_base_rate": base_rate,
                "burst_peak_rate": peak_rate,
                "burst_duration": burst_duration,
                "burst_interval": burst_interval,
                **latency_kwargs,
            }
        )
        execute(ctx)

    # Cyclical workload subcommand
    @simulate_cmd.command(
        "cyclical",
        help="Simulate a workload with cyclical (e.g., sinusoidal) variations.",
    )
    @click.option(
        "--base-rate",
        type=float,
        default=100.0,
        show_default=True,
        help="Base workload rate in requests/sec.",
    )
    @click.option(
        "--amplitude",
        type=float,
        required=True,
        help="Amplitude of cyclical variation.",
    )
    @click.option(
        "--period",
        type=float,
        required=True,
        help="Period of the cycle in seconds.",
    )
    @click.option(
        "--phase-shift",
        type=float,
        default=0.0,
        show_default=True,
        help="Phase shift of the cycle in radians.",
    )
    @latency_options
    @click.pass_context
    def cyclical_workload(
        ctx: ClickContext,
        base_rate: float,
        amplitude: float,
        period: float,
        phase_shift: float,
        **latency_kwargs,
    ) -> None:
        """Run a simulation with a cyclical workload."""
        ctx.obj.update(
            {
                "workload_type": "cyclical",
                "cyclical_base_rate": base_rate,
                "cyclical_amplitude": amplitude,
                "cyclical_period": period,
                "cyclical_phase_shift": phase_shift,
                **latency_kwargs,
            }
        )
        execute(ctx)

    # Random workload subcommand
    @simulate_cmd.command(
        "random",
        help="Simulate a workload with random variations within a range.",
    )
    @click.option(
        "--min-rate",
        type=float,
        required=True,
        help="Minimum rate for random workload.",
    )
    @click.option(
        "--max-rate",
        type=float,
        required=True,
        help="Maximum rate for random workload.",
    )
    @latency_options
    @click.pass_context
    def random_workload(
        ctx: ClickContext,
        min_rate: float,
        max_rate: float,
        **latency_kwargs,
    ) -> None:
        """Run a simulation with a random workload."""
        ctx.obj.update(
            {
                "workload_type": "random",
                "random_min_rate": min_rate,
                "random_max_rate": max_rate,
                **latency_kwargs,
            }
        )
        execute(ctx)

    # CSV workload subcommand
    @simulate_cmd.command(
        "csv",
        help="Simulate a workload from a CSV file.",
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    @click.argument(
        "csv_file",
        type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path, resolve_path=True),
        required=True,
    )
    @click.option(
        "--time-column",
        type=str,
        default="time",
        show_default=True,
        help="Name of the column containing timestamps",
    )
    @click.option(
        "--rate-column",
        type=str,
        default="rate",
        show_default=True,
        help="Name of the column containing rate values",
    )
    @click.option(
        "--base-rate",
        type=float,
        default=1.0,
        show_default=True,
        help="Fallback rate if CSV data is not available",
    )
    @click.pass_context
    def csv_workload(
        ctx: ClickContext,
        csv_file: Path,
        time_column: str,
        rate_column: str,
        base_rate: float,
        **latency_kwargs,
    ) -> None:
        """Run a simulation with a workload defined in a CSV file.
        
        The CSV file should contain at least two columns:
        - A timestamp column (default: 'time')
        - A rate column (default: 'rate')
        """
        ctx.obj.update({
            "workload_type": "csv",
            "csv_file": str(csv_file),
            "csv_time_column": time_column,
            "csv_rate_column": rate_column,
            "csv_base_rate": base_rate,
            **latency_kwargs,
        })
        execute(ctx)

    # Custom workload subcommand
    @simulate_cmd.command(
        "custom",
        help="Simulate a custom workload. All parameters must be defined in the config file.",
    )
    @latency_options
    @click.pass_context
    def custom_workload(ctx: ClickContext, **latency_kwargs) -> None:
        """Run a simulation with a custom workload."""
        ctx.obj.update({"workload_type": "custom", **latency_kwargs})
        execute(ctx)

    # Mix workload subcommand
    @simulate_cmd.command(
        "mix",
        help="Simulate a mixed workload. All parameters must be defined in the config file.",
    )
    @latency_options
    @click.pass_context
    def mix_workload(ctx: ClickContext, **latency_kwargs) -> None:
        """Run a simulation with a mixed workload."""
        ctx.obj.update({"workload_type": "mix", **latency_kwargs})
        execute(ctx)


def execute(ctx: click.Context) -> None:
    """
    Execute the simulation command with the provided arguments.

    Args:
        ctx: The Click context object containing command parameters.
    """
    # Convert Click context to a Namespace-like object for backward compatibility
    class Args:
        def __init__(self, **entries):
            self.__dict__.update(entries)
    
    # Get the parent command's params and update with current command's params
    params = {}
    if ctx.parent and hasattr(ctx.parent, 'params'):
        params.update(ctx.parent.params)
    if hasattr(ctx, 'params'):
        params.update(ctx.params)
    
    # Add any additional parameters from the context object
    if hasattr(ctx, 'obj') and isinstance(ctx.obj, dict):
        params.update(ctx.obj)
    
    # Create a Namespace-like object
    args = Args(**params)
    
    try:
        run_simulation(args)
    except LeafCloudError as e:
        logger.error(f"Simulation failed: {e}", exc_info=True)
        sys.exit(1)


def run_simulation(args) -> None:
    """
    Initialize the framework and run a simulation with the specified configuration.

    This function orchestrates the simulation process:
    1. Creates the framework instance.
    2. Loads Terraform configuration.
    3. Configures the workload and latency settings.
    4. Builds the simulation model.
    5. Runs the simulation.
    6. Prints a summary of the results.

    Args:
        args: The parsed command-line arguments containing simulation parameters.
    """
    # Convert args to a dictionary for easier access
    args_dict = vars(args) if hasattr(args, '__dict__') else dict(args)
    
    # Prepare workload config
    workload_config = configure_workload_config(args)

    input_type = (args_dict.get('input_type') or 'terraform').lower()
    terraform_dir = args_dict.get('terraform')
    input_path = args_dict.get('input_path')
    kubeconfig_path = args_dict.get('kubeconfig')

    if input_type == 'terraform':
        if terraform_dir is None and input_path is None:
            raise LeafCloudError("Terraform input requires a --terraform path.")
        terraform_dir = terraform_dir or input_path
        input_path = terraform_dir
    elif input_type == 'kubernetes':
        if input_path is None and terraform_dir is None:
            raise LeafCloudError("Kubernetes input requires --input or --terraform pointing to manifests.")
        input_path = input_path or terraform_dir
        terraform_dir = None
    elif input_type == 'mixed':
        if terraform_dir is None or input_path is None:
            raise LeafCloudError("Mixed input type requires both --terraform and --input paths.")
    else:
        raise LeafCloudError(f"Unsupported input type: {input_type}")
    
    # Prepare simulation parameters
    sim_params = {
        'terraform_dir': terraform_dir,
        'input_path': input_path,
        'input_type': input_type,
        'kubeconfig': kubeconfig_path,
        'config_path': args_dict.get('config'),
        'workload_config': workload_config,
        'duration': args_dict.get('duration'),
        'iterations': args_dict.get('iterations'),
        'output_dir': args_dict.get('output_dir'),
        'mode': args_dict.get('mode', 'heuristic')  # Add simulation mode
    }

    fallback_cfg = {
        'enabled': not args_dict.get('disable_k8s_node_fallback', False),
        'machine_type': args_dict.get('fallback_node_type'),
        'region': args_dict.get('fallback_node_region'),
        'default_count': args_dict.get('fallback_node_count'),
        'min_count': args_dict.get('fallback_node_min'),
        'max_count': args_dict.get('fallback_node_max'),
        'cpu_headroom': args_dict.get('fallback_node_cpu_headroom'),
        'memory_headroom': args_dict.get('fallback_node_memory_headroom'),
    }
    sim_params['k8s_fallback'] = fallback_cfg
    
    # Add latency settings if provided
    if args_dict.get('latency_stochastic'):
        sim_params['latency_stochastic'] = True
        sim_params['latency_distribution'] = args_dict.get('latency_distribution')
        
        # Add distribution-specific parameters
        if args_dict.get('latency_distribution') == 'uniform':
            sim_params['latency_uniform_min'] = args_dict.get('latency_uniform_min')
            sim_params['latency_uniform_max'] = args_dict.get('latency_uniform_max')
        elif args_dict.get('latency_distribution') == 'normal':
            sim_params['latency_normal_mean'] = args_dict.get('latency_normal_mean')
            sim_params['latency_normal_stddev'] = args_dict.get('latency_normal_stddev')
        
        sim_params['latency_apply_to'] = args_dict.get('latency_apply_to')
    
    # Add cost analysis parameters if provided
    if args_dict.get('enable_cost_analysis'):
        sim_params['enable_cost_analysis'] = True
        if args_dict.get('infracost_usage_file'):
            sim_params['infracost_usage_file'] = args_dict.get('infracost_usage_file')
    
    # Add end-to-end latency parameters if provided
    if args_dict.get('end_to_end_latency'):
        sim_params['end_to_end_latency'] = True
        
        # Parse user distribution if provided
        user_distribution = args_dict.get('user_distribution')
        if user_distribution:
            try:
                parsed_distribution = parse_user_distribution(user_distribution)
                sim_params['user_distribution'] = parsed_distribution
            except ValueError as e:
                logger.error(f"Invalid user distribution: {e}")
                raise LeafCloudError(f"Invalid user distribution: {e}") from e
    
    # Add any additional parameters from args_dict that might be needed
    for key in ['output', 'output_format', 'verbose', 'debug']:
        if key in args_dict and args_dict[key] is not None:
            sim_params[key] = args_dict[key]
    
    try:
        # Run the simulation using the core function
        logger.debug("Starting simulation...")
        start_time = datetime.now()
        
        results = run_simulation_core(**sim_params)
        
        end_time = datetime.now()

        # Quick replica/scheduling snapshot if replica counts are present
        try:
            if isinstance(results, dict):
                rm = results.get("resource_metrics") or {}
                replica_info = []
                for rid, metrics in rm.items():
                    if "deployment::" not in str(rid):
                        continue
                    series = metrics.get("replica_count") or []
                    if not series:
                        continue
                    first = series[0].get("value")
                    last = series[-1].get("value")
                    uniq = sorted({pt.get("value") for pt in series})
                    replica_info.append((rid, first, last, uniq))
                if replica_info:
                    for rid, first, last, uniq in replica_info:
                        print(f"Replica summary for {rid}: start={first} end={last} values={uniq}")
        except Exception:
            logger.debug("Failed to log replica summary", exc_info=logger.isEnabledFor(logging.DEBUG))
        
        # Print simulation summary only when debug logging is enabled to reduce noise
        if logger.isEnabledFor(logging.DEBUG):
            summary_lines = [
                "\n" + "=" * 80,
                "SIMULATION SUMMARY",
                "=" * 80,
                f"Start time: {start_time}",
                f"End time: {end_time}",
                f"Duration: {end_time - start_time}",
                f"Workload type: {workload_config.get('type', 'default') if workload_config else 'default'}",
                f"Iterations: {args_dict.get('iterations', 'N/A')}",
                f"Duration (simulated): {args_dict.get('duration', 'N/A')} seconds",
                "=" * 80 + "\n",
            ]
            for line in summary_lines:
                logger.debug(line)
        
        # Print a summary of the results
        if isinstance(results, dict) and logger.isEnabledFor(logging.DEBUG):
            logger.debug("RESULTS SUMMARY")
            logger.debug("=" * 80)
            
            # Print top-level metrics first
            for key in ['status', 'steps', 'resources_allocated', 'resources_released']:
                if key in results:
                    logger.debug("%s: %s", key, results[key])
            
            # Print other simple values
            for key, value in results.items():
                if key not in ['status', 'steps', 'resources_allocated', 'resources_released', 'workload_config', 'parameters', 'resource_metrics', 'token_flow_logs']:
                    if isinstance(value, (int, float, str, bool)) or value is None:
                        logger.debug("%s: %s", key, value)
            
            # Print summary if available
            if hasattr(results, 'summary'):
                logger.debug("Summary: %s", results.summary)
                
            logger.debug("=" * 80)
        
        return results
        
    except Exception as e:
        logger.error(f"Simulation failed: {str(e)}", exc_info=True)
        raise LeafCloudError(f"Simulation failed: {str(e)}") from e


def parse_user_distribution(user_distribution: str) -> Dict[str, float]:
    """
    Parse user distribution from JSON string or file path.
    
    Args:
        user_distribution: JSON string or file path containing user distribution data
        
    Returns:
        Dictionary mapping regions to percentages (0.0-1.0)
        
    Raises:
        ValueError: If the input is invalid or percentages don't sum to 1.0
    """
    distribution_data = None
    json_parse_error = None
    
    # Try to parse as JSON string first
    try:
        distribution_data = json.loads(user_distribution)
    except json.JSONDecodeError as e:
        json_parse_error = e
        # If JSON parsing fails, try to read as file path
        # Only try file path if the string looks like a path (contains / or \ or ends with .json)
        if ('/' in user_distribution or '\\' in user_distribution or 
            user_distribution.endswith('.json') or user_distribution.endswith('.JSON')):
            try:
                file_path = Path(user_distribution)
                if file_path.exists() and file_path.is_file():
                    with open(file_path, 'r', encoding='utf-8') as f:
                        distribution_data = json.load(f)
                else:
                    raise ValueError(f"File not found: {user_distribution}")
            except (IOError, json.JSONDecodeError) as file_error:
                raise ValueError(f"Invalid user distribution format. Failed to parse as JSON: {json_parse_error}. Failed to read as file: {file_error}")
        else:
            # If it doesn't look like a file path, report the JSON parsing error
            raise ValueError(f"Invalid user distribution format. Must be valid JSON string or file path. JSON error: {json_parse_error}")
    
    # Validate the parsed data
    if not isinstance(distribution_data, dict):
        raise ValueError("User distribution must be a JSON object/dictionary")
    
    # Convert and validate values
    validated_distribution = {}
    total_percentage = 0.0
    
    for region, percentage in distribution_data.items():
        if not isinstance(region, str):
            raise ValueError(f"Region names must be strings, got {type(region).__name__} for {region}")
        
        # Convert percentage to float
        try:
            percentage_float = float(percentage)
        except (TypeError, ValueError):
            raise ValueError(f"Percentage for region '{region}' must be a number, got {percentage}")
        
        # Validate percentage range
        if percentage_float < 0 or percentage_float > 1:
            raise ValueError(f"Percentage for region '{region}' must be between 0.0 and 1.0, got {percentage_float}")
        
        validated_distribution[region] = percentage_float
        total_percentage += percentage_float
    
    # Check that percentages sum to approximately 1.0 (allow small floating point errors)
    # Allow empty distribution (total = 0.0) as it can be handled by fallback logic
    if total_percentage > 0 and abs(total_percentage - 1.0) > 0.01:
        raise ValueError(f"User distribution percentages must sum to 1.0, got {total_percentage}")
    
    return validated_distribution


def configure_workload_config(args) -> Dict[str, Any]:
    """
    Build the workload configuration dictionary from command-line arguments.

    This function dynamically constructs a dictionary with a 'type' and a nested 'params'
    dictionary. It extracts parameters relevant to the selected workload type,
    filtering out any that were not provided (i.e., are None).

    Args:
        args: An object containing the command-line arguments as attributes.

    Returns:
        A dictionary containing the workload configuration.
    """
    if not hasattr(args, 'workload_type'):
        raise ValueError("Missing required argument: workload_type")
        
    params = {}
    args_dict = vars(args) if hasattr(args, '__dict__') else {}
    prefix = f"{args.workload_type}_"

    # Generic parameter extraction
    for key, value in args_dict.items():
        if key.startswith(prefix) and value is not None:
            param_name = key[len(prefix):]
            params[param_name] = value

    # Handle specific cases and argument name mappings
    if args.workload_type == "steady" and hasattr(args, 'workload_rate') and args.workload_rate is not None:
        params["rate"] = args.workload_rate
    elif args.workload_type == "burst":
        if hasattr(args, 'burst_base_rate') and args.burst_base_rate is not None:
            params["base_rate"] = args.burst_base_rate
        if hasattr(args, 'burst_peak_rate') and args.burst_peak_rate is not None:
            params["peak_rate"] = args.burst_peak_rate
        if hasattr(args, 'burst_duration') and args.burst_duration is not None:
            params["duration"] = args.burst_duration
        if hasattr(args, 'burst_interval') and args.burst_interval is not None:
            params["interval"] = args.burst_interval
    elif args.workload_type == "cyclical":
        if hasattr(args, 'cyclical_base_rate') and args.cyclical_base_rate is not None:
            params["base_rate"] = args.cyclical_base_rate
        if hasattr(args, 'cyclical_amplitude') and args.cyclical_amplitude is not None:
            params["amplitude"] = args.cyclical_amplitude
        if hasattr(args, 'cyclical_period') and args.cyclical_period is not None:
            params["period"] = args.cyclical_period
        if hasattr(args, 'cyclical_phase_shift') and args.cyclical_phase_shift is not None:
            params["phase_shift"] = args.cyclical_phase_shift
    elif args.workload_type == "random":
        if hasattr(args, 'random_min_rate') and args.random_min_rate is not None:
            params["min_rate"] = args.random_min_rate
        if hasattr(args, 'random_max_rate') and args.random_max_rate is not None:
            params["max_rate"] = args.random_max_rate
    elif args.workload_type == "csv":
        if hasattr(args, 'csv_file'):
            params["csv_file"] = args.csv_file
        if hasattr(args, 'csv_time_column') and args.csv_time_column is not None:
            params["time_column"] = args.csv_time_column
        if hasattr(args, 'csv_rate_column') and args.csv_rate_column is not None:
            params["rate_column"] = args.csv_rate_column
        if hasattr(args, 'csv_base_rate') and args.csv_base_rate is not None:
            params["base_rate"] = args.csv_base_rate

    return {"type": args.workload_type, "params": params}
