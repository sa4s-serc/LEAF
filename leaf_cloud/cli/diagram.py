"""
Module for handling the 'diagram' command.

This command generates visualizations of the infrastructure model from Terraform files.
"""

import logging
from pathlib import Path
from typing import List, Optional

import click

from ..exceptions import LeafCloudError
from ..visualization import generate_diagrams, DIAGRAM_TYPES

logger = logging.getLogger(__name__)


def register_commands(cli: click.Group) -> None:
    """Register the 'diagram' command with the main Click group.
    
    Args:
        cli: The main Click group to register the command with.
    """
    @cli.command(
        name="diagram",
        help="Generate visualizations of the infrastructure model.",
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    @click.option(
        "--terraform",
        "-t",
        type=click.Path(exists=True, file_okay=True, path_type=Path, resolve_path=True),
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
        "--types",
        "-d",
        type=click.Choice(DIAGRAM_TYPES, case_sensitive=False),
        multiple=True,
        default=["deployment"],
        help="Types of diagrams to generate. Can be specified multiple times.",
    )
    @click.option(
        "--output-dir",
        "-o",
        type=click.Path(file_okay=False, path_type=Path, resolve_path=True),
        default="diagrams",
        help="Directory to save the generated diagrams.",
    )
    @click.pass_context
    def diagram_cmd(
        ctx: click.Context,
        terraform: Path,
        config: Optional[Path],
        types: List[str],
        output_dir: Path,
    ) -> None:
        """Generate visualizations of the infrastructure model from Terraform files.
        
        This command creates visual representations of your infrastructure based on
        the provided Terraform configuration. Multiple diagram types can be generated
        in a single run.
        """
        try:
            run_diagram_generation(terraform, config, types, output_dir)
        except LeafCloudError as e:
            logger.error(f"Diagram generation failed: {e}", exc_info=True)
            ctx.exit(1)
        except Exception as e:
            logger.error(f"Unexpected error during diagram generation: {e}", exc_info=True)
            ctx.exit(1)


def run_diagram_generation(
    terraform_dir: Path,
    config_path: Optional[Path],
    diagram_types: List[str],
    output_dir: Path,
) -> None:
    """Generate visualizations based on the provided Terraform files.

    Args:
        terraform_dir: Path to the directory containing Terraform files.
        config_path: Optional path to the LEAF-Cloud configuration file.
        diagram_types: List of diagram types to generate.
        output_dir: Directory to save the generated diagrams.

    Raises:
        LeafCloudError: If diagram generation fails.
    """
    logger.info(f"Generating diagrams for Terraform directory: {terraform_dir}")
    
    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)
    
    click.echo(f"Generating {', '.join(diagram_types)} diagrams...")
    click.echo(f"Diagrams will be saved in: {output_dir.absolute()}")

    try:
        # Generate diagrams using the visualization module
        generated_files = generate_diagrams(
            terraform_dir=terraform_dir,
            config_path=config_path,
            diagram_types=diagram_types or None,  # None means all types
            output_dir=output_dir
        )
        
        # Print success messages
        for file_path in generated_files:
            click.secho(f"  ✓ Successfully generated diagram: {file_path}", fg="green")
            
        logger.info(f"Successfully generated {len(generated_files)} diagrams")
        
    except Exception as e:
        error_msg = f"Failed to generate diagrams: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise LeafCloudError(error_msg) from e
