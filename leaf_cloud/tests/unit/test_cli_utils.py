"""Unit tests for CLI utilities."""

from unittest.mock import MagicMock, patch
import pytest
from rich.progress import Progress, TaskID

from leaf_cloud.cli.cli_utils import (
    create_progress,
    create_progress_bar,
    progress_context,
    print_header,
    print_success,
    print_warning,
    print_error,
    display_table,
)


class TestCreateProgress:
    """Tests for create_progress function."""

    def test_create_progress_default(self):
        """Test creating a progress bar with default parameters."""
        progress = create_progress()
        assert isinstance(progress, Progress)
        assert progress.disable is False

    def test_create_progress_with_description(self):
        """Test creating a progress bar with a custom description."""
        # Test
        progress = create_progress("Custom description")
        
        # Verify we got a Progress instance
        assert isinstance(progress, Progress)
        
        # Verify the progress bar is not disabled
        assert progress.disable is False


class TestCreateProgressBar:
    """Tests for create_progress_bar function."""

    @patch("leaf_cloud.cli.cli_utils.create_progress")
    def test_create_progress_bar_default(self, mock_create_progress):
        """Test creating a progress bar with default parameters."""
        # Setup
        mock_progress = MagicMock(spec=Progress)
        mock_task_id = MagicMock(spec=TaskID)
        mock_progress.add_task.return_value = mock_task_id
        mock_create_progress.return_value = mock_progress

        # Test
        progress, task_id = create_progress_bar()

        # Assert
        assert progress is mock_progress
        assert task_id is mock_task_id
        mock_progress.add_task.assert_called_once_with("Working...", total=100.0)

    @patch("leaf_cloud.cli.cli_utils.create_progress")
    def test_create_progress_bar_custom_params(self, mock_create_progress):
        """Test creating a progress bar with custom parameters."""
        # Setup
        mock_progress = MagicMock(spec=Progress)
        mock_task_id = MagicMock(spec=TaskID)
        mock_progress.add_task.return_value = mock_task_id
        mock_create_progress.return_value = mock_progress

        # Test
        progress, task_id = create_progress_bar("Custom", total=50.0)

        # Assert
        mock_progress.add_task.assert_called_once_with("Custom", total=50.0)


class TestProgressContext:
    """Tests for progress_context context manager."""

    @patch("leaf_cloud.cli.cli_utils.create_progress")
    def test_progress_context_success(self, mock_create_progress):
        """Test progress_context with successful execution."""
        # Setup
        mock_progress = MagicMock(spec=Progress)
        mock_task_id = MagicMock(spec=TaskID)
        mock_progress.add_task.return_value = mock_task_id
        mock_create_progress.return_value = mock_progress

        # Test
        with progress_context("Processing...", total=100) as (progress, task_id):
            assert progress is mock_progress
            assert task_id is mock_task_id
            mock_progress.start.assert_called_once()
            mock_progress.stop.assert_not_called()

        # Assert cleanup
        mock_progress.stop.assert_called_once()

    @patch("leaf_cloud.cli.cli_utils.create_progress")
    def test_progress_context_with_error(self, mock_create_progress):
        """Test progress_context when an error occurs."""
        # Setup
        mock_progress = MagicMock(spec=Progress)
        mock_task_id = MagicMock(spec=TaskID)
        mock_progress.add_task.return_value = mock_task_id
        mock_create_progress.return_value = mock_progress

        # Test with exception
        with pytest.raises(ValueError, match="Test error"):
            with progress_context("Processing...") as (progress, _):
                mock_progress.start.assert_called_once()
                raise ValueError("Test error")

        # Assert cleanup still happens
        mock_progress.stop.assert_called_once()


class TestConsoleOutput:
    """Tests for console output functions."""

    @patch("rich.console.Console.print")
    def test_print_header(self, mock_print):
        """Test print_header function."""
        print_header("Test Header")
        mock_print.assert_called_once()
        args, kwargs = mock_print.call_args
        assert "Test Header" in str(args[0].renderable)
        assert kwargs.get("style") is None  # Style is part of the Panel

    @patch("rich.console.Console.print")
    def test_print_success(self, mock_print):
        """Test print_success function."""
        print_success("Success!")
        mock_print.assert_called_once_with("✅ [green]Success![/green]")

    @patch("rich.console.Console.print")
    def test_print_warning(self, mock_print):
        """Test print_warning function."""
        print_warning("Warning!")
        mock_print.assert_called_once_with("⚠️  [yellow]Warning![/yellow]")

    @patch("rich.console.Console.print")
    def test_print_error(self, mock_print):
        """Test print_error function."""
        print_error("Error!")
        mock_print.assert_called_once_with("❌ [red]Error![/red]")


class TestDisplayTable:
    """Tests for display_table function."""

    @patch("rich.console.Console.print")
    def test_display_table_empty(self, mock_print):
        """Test display_table with empty data."""
        display_table([])
        mock_print.assert_called_once_with("[yellow]No data to display[/yellow]")

    @patch("rich.console.Console.print")
    def test_display_table_with_data(self, mock_print):
        """Test display_table with data."""
        data = [
            {"Name": "Alice", "Age": 30},
            {"Name": "Bob", "Age": 25},
        ]
        display_table(data, "Test Table")
        
        # Check that print was called with a Table object
        assert mock_print.call_count == 1
        table = mock_print.call_args[0][0]
        assert table.title == "Test Table"
        assert len(table.columns) == 2  # Name and Age columns
        assert len(table.rows) == 2  # Two data rows
