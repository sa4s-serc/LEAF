"""
Module for handling the 'intermediate' command.

This command exports the intermediate representation of the model from Terraform files.
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

import click
from rich.logging import RichHandler

from .cli_utils import progress_context
from ..exceptions import LeafCloudError
from ..simulation.intermediate import export_intermediate_model

logger = logging.getLogger(__name__)


def register_commands(cli: click.Group) -> None:
    """Register the 'intermediate' command with the main Click group.
    
    Args:
        cli: The main Click group to register the command with.
    """
    @cli.command(
        name="intermediate",
        help="Build and export the intermediate model from Terraform files.",
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
    @click.option(
        "--var-files",
        type=click.Path(exists=True, dir_okay=False, path_type=Path, resolve_path=True),
        multiple=True,
        help="Paths to Terraform .tfvars files. Can be specified multiple times.",
    )
    @click.option(
        "--output",
        "-o",
        type=click.Path(dir_okay=False, writable=True, path_type=Path, resolve_path=True),
        default="intermediate_tf_resources.json",
        show_default=True,
        help="Path to the output JSON file.",
    )
    @click.pass_context
    def intermediate_cmd(
        ctx: click.Context,
        terraform: Path,
        config: Optional[Path],
        var_files: tuple[Path, ...],
        output: Path,
    ) -> None:
        """Export the intermediate representation of Terraform infrastructure.
        
        This command parses Terraform files and exports an intermediate
        representation of the infrastructure model. This can be useful for
        debugging or as input to other tools.
        """
        try:
            run_intermediate_export(terraform, config, var_files, output)
        except LeafCloudError as e:
            logger.error(
                f"Failed to export intermediate model: {e}", exc_info=True
            )
            ctx.exit(1)
        except Exception as e:
            logger.error(
                f"Unexpected error during intermediate model export: {e}",
                exc_info=True,
            )
            ctx.exit(1)


def run_intermediate_export(
    terraform_dir: Path,
    config_path: Optional[Path],
    var_files: Tuple[Path, ...],
    output_path: Path,
) -> None:
    """Export the intermediate representation of the infrastructure model.

    Args:
        terraform_dir: Directory containing Terraform files.
        config_path: Optional path to the LEAF-Cloud configuration file.
        var_files: Tuple of paths to Terraform .tfvars files.
        output_path: Path to the output JSON file.
    """
    logger.info(f"Exporting intermediate model from '{terraform_dir}' to '{output_path}'")
    
    with progress_context("Exporting intermediate model...") as (progress, task):
        click.echo(f"Loading infrastructure from: {terraform_dir}")
        
        try:
            # Ensure output directory exists
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Export the intermediate model using the new function
            export_intermediate_model(
                terraform_dir=terraform_dir,
                config_path=config_path,
                var_files=var_files,
                output_path=output_path
            )
            
            # Update progress
            progress.update(task, description="Export completed!")
            
            # Show success message
            click.secho(
                f"✓ Successfully exported intermediate model to: {output_path.absolute()}",
                fg="green",
            )
            logger.info(f"Successfully exported intermediate model to {output_path}")
            
        except Exception as e:
            progress.update(task, description="Export failed!")
            logger.error(f"Failed to export intermediate model: {str(e)}", exc_info=True)
            raise LeafCloudError(f"Failed to export intermediate model: {str(e)}") from e
