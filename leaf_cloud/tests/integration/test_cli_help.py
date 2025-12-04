import os
import shutil
import json
import pathlib
import pytest
from click.testing import CliRunner

from leaf_cloud.main import cli

# Test configuration paths
TEST_CONFIG_DIR = pathlib.Path(__file__).parent / "test_config"
MINIMAL_CONFIG = TEST_CONFIG_DIR / "minimal_config.yaml"


def test_help_command():
    """Test that the help command works."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0, f"Help command failed with output: {result.output}"
    assert "Show this message and exit." in result.output


def test_version_command():
    """Test that the version command works."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0, f"Version command failed with output: {result.output}"
    # Check for the version format (e.g., "Version: X.Y.Z")
    assert "Version:" in result.output


def test_simulate_help():
    """Test that the simulate command help works."""
    runner = CliRunner()
    result = runner.invoke(cli, ["simulate", "--help"])
    assert result.exit_code == 0, f"Simulate help command failed with output: {result.output}"
    assert "Run a simulation based on Terraform files." in result.output


def test_analyze_help():
    """Test that the analyze command help works."""
    runner = CliRunner()
    result = runner.invoke(cli, ["analyze", "--help"])
    assert result.exit_code == 0, f"Analyze help command failed with output: {result.output}"
    assert "Analyze existing simulation results." in result.output


def test_diagram_help():
    """Test that the diagram command help works."""
    runner = CliRunner()
    result = runner.invoke(cli, ["diagram", "--help"])
    assert result.exit_code == 0, f"Diagram help command failed with output: {result.output}"
    assert "Generate visualizations of the infrastructure model." in result.output


def test_export_help():
    """Test that the export command help works."""
    runner = CliRunner()
    result = runner.invoke(cli, ["export", "--help"])
    assert result.exit_code == 0, f"Export help command failed with output: {result.output}"
    assert "Run a simulation and export the results." in result.output


def test_simulate_with_missing_required_args():
    """Test that simulate command shows help when no arguments are provided."""
    runner = CliRunner()
    result = runner.invoke(cli, ["simulate"])
    # The command should show help when no arguments are provided
    assert result.exit_code == 0
    assert "Usage: cli simulate" in result.output


def test_clean_command(tmp_path):
    """Test the clean command with a temporary directory."""
    runner = CliRunner()
    result = runner.invoke(cli, ["clean", "--help"])
    assert result.exit_code == 0, f"Clean help command failed with output: {result.output}"
