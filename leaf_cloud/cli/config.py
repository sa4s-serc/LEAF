"""
Configuration management commands for LEAF-Cloud framework.

This module provides commands for managing LEAF-Cloud configuration,
including loading, validating, and displaying configuration settings.
"""

import os
import sys
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List

import click
import yaml
from rich.console import Console
from rich.table import Table
from rich.syntax import Syntax

from ..config import LEAFCloudConfig, ConfigError
from ..utils import get_package_root

console = Console()


def register_commands(cli):
    """Register the 'config' command with the main Click group.
    
    Args:
        cli: The main Click group to register the command with.
    """
    @cli.group(name="config", help="Configuration management commands.")
    def config_group():
        """Configuration management commands."""
        pass

    @config_group.command(name="show")
    @click.option(
        "--format",
        type=click.Choice(["table", "yaml", "json"], case_sensitive=False),
        default="table",
        help="Output format (default: table)",
    )
    @click.option(
        "--filter",
        "filter_path",
        default=None,
        help="Filter configuration by path (e.g., 'simulation.workload')",
    )
    def show_config(format: str, filter_path: Optional[str]):
        """Show the current configuration.

        Displays the current configuration in the specified format, optionally filtered
        to show only specific sections.
        """
        try:
            config = LEAFCloudConfig.load()
            config_dict = config.to_dict()

            # Apply filter if specified
            if filter_path:
                for part in filter_path.split("."):
                    config_dict = config_dict.get(part, {})
                    if not config_dict:
                        console.print(
                            f"[yellow]Warning: No configuration found for path '{filter_path}'[/]"
                        )
                        return

            if format == "table":
                _display_config_table(config_dict, filter_path)
            elif format == "yaml":
                console.print(Syntax(yaml.dump(config_dict, sort_keys=False), "yaml"))
            elif format == "json":
                console.print_json(data=config_dict)

        except ConfigError as e:
            console.print(f"[red]Error loading configuration: {e}[/]")
            sys.exit(1)

    @config_group.command(name="validate")
    @click.argument("config_file", required=False, type=click.Path(exists=True))
    def validate_config(config_file: Optional[str] = None):
        """Validate a configuration file.

        If no file is provided, validates the default configuration.

        Args:
            config_file: Optional path to the configuration file to validate.
        """
        try:
            if config_file:
                config = LEAFCloudConfig.from_file(config_file)
                console.print(f"[green]✓ Validating configuration file: {config_file}[/]")
            else:
                config = LEAFCloudConfig.load()
                console.print("[green]✓ Validating default configuration[/]")
            
            # This will raise ConfigError if validation fails
            config.validate()
            
            # If we get here, validation passed
            console.print("\n[bold green]✓ Configuration is valid[/]")
            console.print("\nConfiguration summary:")
            console.print(f"  • Version: {config.version}")
            console.print(f"  • Base Directory: {Path(config.base_dir).resolve()}")
            console.print(f"  • Output Directory: {Path(config.output_dir).resolve()}")
            console.print(f"  • Simulation Duration: {config.simulation.duration} seconds")
            console.print(f"  • Workload Type: {config.simulation.workload_type}")
            
        except ConfigError as e:
            console.print(f"[red]✗ Configuration validation failed:[/]")
            console.print(f"[red]{str(e)}[/]")
            sys.exit(1)
        except Exception as e:
            console.print(f"[red]Error: {str(e)}[/]")
            sys.exit(1)

    @config_group.command(name="init")
    @click.option(
        "--output",
        "-o",
        type=click.Path(dir_okay=False, writable=True),
        default="leaf-config.yaml",
        help="Output file path (default: leaf-config.yaml)",
    )
    @click.option(
        "--force",
        "-f",
        is_flag=True,
        help="Overwrite the output file if it exists",
    )
    def init_config(output: str, force: bool = False):
        """Initialize a new configuration file with default values."""
        output_path = Path(output)
        if output_path.exists() and not force:
            console.print(
                f"[yellow]File {output_path} already exists. Use --force to overwrite.[/]"
            )
            return

        try:
            # Get the template file from package
            template_path = get_package_root() / "templates" / "config_template.yaml"
            
            if not template_path.exists():
                # Fallback to creating a default config
                config = LEAFCloudConfig()
                config.save(output_path)
            else:
                # Copy the template file
                shutil.copy2(template_path, output_path)
            
            console.print(f"[green]✓ Configuration file created: {output_path.resolve()}[/]")
            console.print(
                "\nNext steps:"
                f"\n  1. Review and edit the configuration: [cyan]{output_path.resolve()}[/]"
                f"\n  2. Validate the configuration: [cyan]leaf-cloud config validate {output_path}[/]"
                f"\n  3. Run with the configuration: [cyan]leaf-cloud run --config {output_path}[/]"
            )
            
        except Exception as e:
            console.print(f"[red]Error creating configuration file: {e}[/]")
            if hasattr(e, 'stderr') and e.stderr:
                console.print(f"[red]Error details: {e.stderr}[/]")
            console.print("\n[bold]Troubleshooting:[/]")
            console.print("  - Check if the output directory exists and is writable")
            console.print("  - Verify you have sufficient permissions")
            console.print("  - Try with --output in a different location")
            sys.exit(1)

    # The config group is already added to the CLI through the decorator


def _display_config_table(config_dict: Dict[str, Any], prefix: str = "") -> None:
    """Display configuration as a rich table.

    Args:
        config_dict: The configuration dictionary to display.
        prefix: Optional prefix for nested keys (used internally for recursion).
    """
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    for key, value in sorted(config_dict.items()):
        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, dict):
            # For nested dictionaries, show the key and recurse
            table.add_row(f"[bold]{full_key}[/]", "")
            _display_config_table(value, full_key)
        else:
            # For simple values, show the key-value pair
            if isinstance(value, (list, tuple)):
                # Format lists/tuples as comma-separated strings
                value_str = ", ".join(str(v) for v in value)
            else:
                value_str = str(value)
            table.add_row(full_key, value_str)

    console.print(table)
