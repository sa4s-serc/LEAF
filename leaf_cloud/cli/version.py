"""
Version information and display utilities for LEAF-Cloud framework.

This module provides version information and display functionality.
"""

import click
from rich import print as rprint
from rich.panel import Panel

from .. import __version__


def register_commands(cli: click.Group) -> None:
    """Register the 'version' command with the main Click group.
    
    Args:
        cli: The main Click group to register the command with.
    """
    @cli.command(
        name="version",
        help="Display version information.",
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    def version_cmd():
        """Display version information for LEAF-Cloud."""
        print_version_info()


def print_version_info() -> None:
    """
    Print the version information using rich formatting.
    """
    version_info = f"[bold]Version:[/] {__version__}\n"
    
    rprint(
        Panel.fit(
            version_info,
            title="[bold blue]LEAF-CLOUD FRAMEWORK[/]",
            border_style="blue",
            padding=(1, 2),
        )
    )
    rprint("Layered Eco-centric Analytical Framework for Cloud Infrastructure")
