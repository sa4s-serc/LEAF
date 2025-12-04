"""
Module for handling the 'clean' command.

This command removes temporary files and directories generated during simulations.
"""

import logging
import os
import shutil
import sys

import click
from rich.prompt import Confirm

logger = logging.getLogger(__name__)

# Define constants for directories and files to be cleaned
DIRECTORIES_TO_CLEAN = ["results", "diagrams", "analysis_output"]
FILES_TO_REMOVE = [
    "parsed_infrastructure.json",
    "model.json",
    "intermediate_tf_resources_export.json",
]


def register_commands(cli: click.Group) -> None:
    """Register the 'clean' command with the main Click group.
    
    Args:
        cli: The main Click group to register the command with.
    """
    @cli.command(
        name="clean",
        help="Clean up results and temporary files.",
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    @click.option(
        "--force",
        "-f",
        is_flag=True,
        help="Force deletion without confirmation.",
    )
    @click.pass_context
    def clean_cmd(ctx: click.Context, force: bool) -> None:
        """Remove temporary files and directories generated during simulations.
        
        This command will remove the following by default:
        - Directories: results/, diagrams/, analysis_output/
        - Files: parsed_infrastructure.json, model.json, intermediate_tf_resources_export.json
        """
        if not force:
            click.echo("This command will remove the following files and directories:")
            click.echo("\nDirectories to be removed:")
            for d in DIRECTORIES_TO_CLEAN:
                click.echo(f"  - {d}/")
            click.echo("\nFiles to be removed:")
            for f in FILES_TO_REMOVE:
                click.echo(f"  - {f}")
            
            if not Confirm.ask("\nAre you sure you want to continue?", default=False):
                click.echo("Cleanup aborted by user.")
                logger.info("Cleanup aborted by user.")
                ctx.exit(0)

        try:
            run_cleanup()
        except Exception as e:
            logger.error(f"Cleanup failed: {e}", exc_info=True)
            click.secho(f"An error occurred during cleanup: {e}", fg="red")
            ctx.exit(1)


def run_cleanup():
    """
    Perform the cleanup of generated files and directories.
    """
    logger.info("Starting cleanup of temporary files and results...")
    cleaned_items = []

    for dir_path in DIRECTORIES_TO_CLEAN:
        if _clean_directory(dir_path):
            cleaned_items.append(dir_path)

    for file_path in FILES_TO_REMOVE:
        if _remove_file(file_path):
            cleaned_items.append(file_path)

    if cleaned_items:
        print("\nCleanup complete. The following items were removed:")
        for item in cleaned_items:
            print(f"  - {item}")
    else:
        print("\nNo temporary files or directories found to clean.")

    logger.info("Cleanup process finished.")


def _clean_directory(dir_path: str) -> bool:
    """
    Remove a directory and its contents.

    Args:
        dir_path: The path to the directory to clean.

    Returns:
        True if the directory existed and was removed, False otherwise.
    """
    if not os.path.isdir(dir_path):
        logger.debug(f"Directory '{dir_path}' not found, nothing to clean.")
        return False

    try:
        shutil.rmtree(dir_path)
        logger.info(f"Removed directory tree: {dir_path}")
        return True
    except OSError as e:
        logger.error(
            f"Error removing directory {dir_path}: {e}", exc_info=True
        )
        print(
            f"Warning: Could not remove {dir_path}. Reason: {e}",
            file=sys.stderr,
        )
        return False


def _remove_file(file_path: str) -> bool:
    """
    Remove a single file if it exists.

    Args:
        file_path: The path to the file to remove.

    Returns:
        True if the file existed and was removed, False otherwise.
    """
    if not os.path.exists(file_path):
        logger.debug(f"File '{file_path}' not found, skipping.")
        return False

    try:
        os.remove(file_path)
        logger.info(f"Successfully removed file: {file_path}")
        return True
    except OSError as e:
        logger.error(f"Error removing file {file_path}: {e}", exc_info=True)
        print(
            f"Warning: Could not remove {file_path}. Reason: {e}",
            file=sys.stderr,
        )
        return False
