"""
CLI utilities for LEAF-Cloud.

This module provides shared functionality for CLI commands, including:
- Console output formatting
- Progress bars
- Table display
- Common CLI operations
"""

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import (
    Any, Dict, List, Optional, Tuple, Union, TypeVar, Generic, Type,
    Generator, Iterator, Sequence, Callable, TextIO
)
from types import TracebackType

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
    TaskID,
)
from rich.panel import Panel
from rich.table import Table

# Global console instance for consistent output
console: Console = Console()

# Type variable for generic context manager
T = TypeVar('T')

# Configure logger
logger: logging.Logger = logging.getLogger(__name__)

def setup_logging(level: int = logging.INFO, log_file: Optional[Path] = None) -> None:
    """Setup logging configuration.
    
    Args:
        level: Logging level (e.g., logging.INFO, logging.DEBUG)
        log_file: Optional path to log file
    """
    # Configure basic logging
    handlers = []
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)
    
    # File handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    
    # Configure root logger
    logging.basicConfig(
        level=level,
        handlers=handlers,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    # Align sub-module verbosity with the selected level (avoid forcing DEBUG always)
    logging.getLogger("leaf_cloud.gcp.compute").setLevel(level)

# Console Output -------------------------------------------------------------

def print_header(text: str) -> None:
    """Print a formatted header.
    
    Args:
        text: Text to display in the header
        
    Example:
        >>> print_header("Processing Complete")
        # Displays a blue panel with the text "Processing Complete"
    """
    console.print(Panel.fit(text, style="bold blue"))


def print_success(text: str) -> None:
    """Print a success message with a checkmark icon.
    
    Args:
        text: Success message to display
        
    Example:
        >>> print_success("Operation completed successfully")
        # Displays: ✅ Operation completed successfully
    """
    console.print(f"✅ [green]{text}[/green]")


def print_warning(text: str) -> None:
    """Print a warning message with a warning icon.
    
    Args:
        text: Warning message to display
        
    Example:
        >>> print_warning("This operation is deprecated")
        # Displays: ⚠️  This operation is deprecated
    """
    console.print(f"⚠️  [yellow]{text}[/yellow]")


def print_error(text: str) -> None:
    """Print an error message with an error icon.
    
    Args:
        text: Error message to display
        
    Example:
        >>> print_error("Operation failed")
        # Displays: ❌ Operation failed
    """
    console.print(f"❌ [red]{text}[/red]")


# Progress Bars --------------------------------------------------------------

def create_progress(description: str = "Processing...") -> Progress:
    """Create a rich progress bar instance.
    
    .. note::
        Prefer using `progress_context` for most use cases as it handles
        proper start/stop of the progress bar.
    
    Args:
        description: Initial description for the progress bar
        
    Returns:
        Progress: A configured Progress instance
        
    Example:
        >>> progress = create_progress("Processing...")
        >>> task_id = progress.add_task("Working...")
        >>> progress.start()
        >>> try:
        ...     # Do work
        ...     progress.update(task_id, advance=1)
        ... finally:
        ...     progress.stop()
    """
    return Progress(
        SpinnerColumn(),
        "•",
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        "[progress.percentage]{task.percentage:>3.0f}%",
        "•",
        MofNCompleteColumn(),
        "•",
        TimeElapsedColumn(),
        console=console,
        refresh_per_second=10,
    )


def create_progress_bar(
    description: str = "Working...", 
    total: Optional[float] = 100.0
) -> Tuple[Progress, TaskID]:
    """Create a progress bar with the given description and total steps.
    
    .. warning::
        This function returns a progress bar that needs to be manually started and stopped.
        In most cases, you should use `progress_context` instead, which handles this automatically.
    
    Args:
        description: Description to show next to the progress bar
        total: Total number of steps (None for indeterminate progress)
        
    Returns:
        Tuple[Progress, TaskID]: A tuple containing the progress bar instance and task ID
        
    Example:
        >>> progress, task_id = create_progress_bar("Processing...", total=100)
        >>> progress.start()
        >>> try:
        ...     # Do work
        ...     progress.update(task_id, advance=1)
        ... finally:
        ...     progress.stop()
    """
    progress = create_progress(description)
    task_id = progress.add_task(description, total=total)
    return progress, task_id


@contextmanager
def progress_context(
    description: str = "Processing...",
    total: Optional[float] = None
) -> Generator[Tuple[Progress, TaskID], None, None]:
    """Context manager for a progress bar that automatically handles start/stop.
    
    This is the preferred way to handle progress bars in the codebase as it ensures
    proper resource cleanup, even if an exception occurs.
    
    Args:
        description: Description to show next to the progress bar
        total: Total number of steps (None for indeterminate progress)
        
    Yields:
        Tuple[Progress, TaskID]: A tuple containing the progress bar instance and task ID
        
    Example:
        >>> with progress_context("Processing items...", total=100) as (progress, task_id):
        ...     for i in range(100):
        ...         # Do work
        ...         progress.update(task_id, advance=1)
        ...
        # Progress bar is automatically started before the block and stopped after
    """
    progress = create_progress(description)
    task_id = progress.add_task(description, total=total)
    
    try:
        progress.start()
        yield progress, task_id
    finally:
        progress.stop()


# Table Display -------------------------------------------------------------

def display_table(
    data: Sequence[Dict[str, Any]],
    title: str = "",
    show_header: bool = True,
    title_style: str = "bold magenta",
) -> None:
    """Display tabular data in a formatted table.
    
    Args:
        data: Sequence of dictionaries where keys are column names and values are cell contents
        title: Optional title for the table
        show_header: Whether to show column headers
        title_style: Rich style for the title
        
    Example:
        >>> data = [{"Name": "Alice", "Age": 30}, {"Name": "Bob", "Age": 25}]
        >>> display_table(data, "User Information")
        # Displays a table with the given data and title
    """
    if not data:
        console.print("[yellow]No data to display[/yellow]")
        return

    table = Table(
        show_header=show_header,
        header_style="bold magenta",
        box=None if not title else None,
        title=title,
        title_style=title_style,
    )

    # Add columns
    for key in data[0].keys():
        table.add_column(str(key))

    # Add rows
    for row in data:
        table.add_row(*[str(value) for value in row.values()])

    console.print(table)
