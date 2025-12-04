"""
Main entry point for the LEAF-Cloud CLI.

This module provides the command-line interface for LEAF-Cloud operations.
"""

import click
import logging
from pathlib import Path
from typing import Optional

from .cli import COMMAND_MODULES
from .cli.cli_utils import setup_logging

# Set up logging
logger = logging.getLogger(__name__)

@click.group()
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug logging."
)
@click.option(
    "--log-file",
    type=click.Path(path_type=Path),
    help="Path to log file."
)
@click.pass_context
def cli(ctx, debug: bool, log_file: Optional[Path]):
    """LEAF-Cloud: Lifecycle Assessment Framework for Cloud Infrastructure."""
    ctx.ensure_object(dict)
    
    # Setup logging with curated debug: keep root at INFO to avoid spam, elevate key modules to DEBUG when --debug
    base_level = logging.INFO
    setup_logging(base_level, log_file)
    if debug:
        for name in [
            "leaf_cloud.cli.simulate",
            "leaf_cloud.simulation.core",
            "leaf_cloud.terraform.parser",
            "leaf_cloud.terraform.model_builder",
            "leaf_cloud.leaf",
            "leaf_cloud.orchestrator.core",
        ]:
            logging.getLogger(name).setLevel(logging.DEBUG)
    
    ctx.obj['debug'] = debug
    ctx.obj['log_file'] = log_file

# Register all command modules
for module in COMMAND_MODULES:
    if hasattr(module, 'register_commands'):
        module.register_commands(cli)
    elif hasattr(module, 'cli'):
        cli.add_command(module.cli)

if __name__ == "__main__":
    cli()