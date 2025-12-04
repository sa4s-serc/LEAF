"""
Module for handling the 'export' command.

This command runs a new simulation and exports the results to a specified format.
"""

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union, TYPE_CHECKING

import click
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..exceptions import LeafCloudError
# Lazy import to avoid circular dependency
# from ..simulation.export import export_results

# Import for type checking only to avoid circular imports
if TYPE_CHECKING:
    from ..orchestrator.core import Orchestrator

logger = logging.getLogger(__name__)

# Define common options
export_format_option = click.option(
    "--format",
    "-f",
    type=click.Choice(["json", "csv", "yaml", "html"], case_sensitive=False),
    default="json",
    show_default=True,
    help="Export format for the results.",
)

output_option = click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, writable=True, path_type=Path, resolve_path=True),
    help="Path to the output file (default: results.<format>).",
)

def register_commands(cli: click.Group) -> None:
    """Register the 'export' command with the main Click group.
    
    Args:
        cli: The main Click group to register the command with.
    """
    # Create a command group for export with subcommands for different workload types
    @cli.group(
        name="export",
        help="Run a simulation and export the results.",
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    @click.option(
        "--terraform",
        "-t",
        type=click.Path(
            exists=True, file_okay=True, path_type=Path, resolve_path=True
        ),
        required=True,
        help="Directory containing Terraform files or path to a Terraform plan.json file.",
    )
    @click.option(
        "--config",
        "-c",
        type=click.Path(exists=True, dir_okay=False, path_type=Path, resolve_path=True),
        help="Path to the LEAF-Cloud configuration file.",
    )
    @export_format_option
    @output_option
    @click.pass_context
    def export_cmd(
        ctx: click.Context,
        terraform: Path,
        config: Optional[Path],
        format: str,
        output: Optional[Path],
    ) -> None:
        """Run a simulation and export the results to the specified format."""
        # Store common parameters in the context
        ctx.ensure_object(dict)
        ctx.obj["terraform"] = terraform
        ctx.obj["config"] = config
        ctx.obj["format"] = format
        ctx.obj["output"] = output

    # Register subcommands for different workload types
    register_workload_commands(export_cmd)


def register_workload_commands(export_group: click.Group) -> None:
    """Register workload-specific subcommands for the export command.
    
    Args:
        export_group: The Click group to register the subcommands with.
    """
    # Steady workload subcommand
    @export_group.command(
        "steady",
        help="Simulate a steady, constant workload.",
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    @click.option(
        "--workload-rate",
        type=float,
        default=100.0,
        show_default=True,
        help="Constant workload rate in requests/sec.",
    )
    @click.pass_context
    def steady_workload(ctx: click.Context, workload_rate: float) -> None:
        """Run a simulation with a steady workload and export results."""
        workload_config = {
            "type": "steady",
            "params": {"rate": workload_rate},
        }
        _run_and_export(ctx, workload_config)

    # Add more workload types here as needed
    # @export_group.command(...)
    # def another_workload(...):
    #     ...


def _run_and_export(ctx: click.Context, workload_config: Dict[str, Any]) -> None:
    """
    Run a simulation with the given workload configuration and export the results.
    
    Args:
        ctx: Click context containing command parameters.
        workload_config: Workload configuration dictionary.
    """
    terraform = ctx.obj["terraform"]
    config = ctx.obj["config"]
    file_format = ctx.obj["format"]
    output_path = ctx.obj["output"]
    
    # Set default output path if not provided
    if not output_path:
        output_path = Path(f"results.{file_format}")
    else:
        output_path = Path(output_path)

    try:
        start_time = time.time()
        logger.info(f"Starting simulation to export results for {terraform}.")

        # Initialize framework with progress indicator
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            task = progress.add_task("Running simulation...", total=None)

            # Initialize and configure the framework
            from ..leaf import LEAFCloud  # Lazy import to avoid circular imports
            framework = LEAFCloud(config_path=config)
            framework.load_terraform(str(terraform))
            framework.configure_workload(workload_config)
            framework.build_model()
            
            # Run simulation and get results
            results = framework.run_simulation()
            
            # Convert results to a serializable format if needed
            if hasattr(results, 'to_dict'):
                results_data = results.to_dict()
            elif hasattr(results, '__dict__'):
                results_data = results.__dict__
            else:
                results_data = results

            # Export results using the new export module (lazy import)
            try:
                from ..simulation.export import export_results
                export_results(
                    results=results_data,
                    output_format=file_format,
                    output_path=output_path
                )
            except ImportError as e:
                logger.warning(f"Could not import export_results: {e}")
                # Fallback: just save as JSON
                import json
                with open(output_path, 'w') as f:
                    json.dump(results_data, f, indent=2, default=str)

            elapsed_time = time.time() - start_time
            progress.update(task, description="Simulation completed!")

        # Display success message
        click.secho(
            f"✓ Successfully exported results to: {output_path} (took {elapsed_time:.2f}s)",
            fg="green",
        )
        logger.info(f"Results exported to {output_path} in {elapsed_time:.2f}s.")

    except LeafCloudError as e:
        logger.error(f"Export failed: {e}", exc_info=True)
        click.secho(f"Error: {e}", fg="red")
        ctx.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error during export: {e}", exc_info=True)
        click.secho(f"An unexpected error occurred: {e}", fg="red")
        ctx.exit(1)
