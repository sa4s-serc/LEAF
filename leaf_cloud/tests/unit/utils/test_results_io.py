"""Unit tests for results_io module."""

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, mock_open, patch

import pytest
from filelock import Timeout as FileLockTimeout

from leaf_cloud.utils.schemas.results import RawSimulationResult, ProcessedSimulationResult
from leaf_cloud.utils.results_io import (
    RAW_RESULT_EXT,
    PROCESSED_RESULT_EXT,
    MAX_FILE_SIZE,
    _validate_file_path,
    get_result_metadata,
    read_result_file,
    write_result_file,
    convert_result_file,
    backup_result_file,
    atomic_write,
)

# Sample test data
SAMPLE_TIMESTAMP = "2023-01-01T00:00:00+00:00"
SAMPLE_SIMULATION_ID = "test-simulation-123"

SAMPLE_DESCRIPTION = "Test simulation run"

def get_raw_result_fixture() -> Dict[str, Any]:
    """Return a raw result fixture with all required fields."""
    return {
        "metadata": {
            "simulation_id": SAMPLE_SIMULATION_ID,
            "timestamp": 1672531200.0,  # 2023-01-01T00:00:00+00:00 as timestamp
            "start_time": 1672531200.0,  # 2023-01-01T00:00:00+00:00
            "end_time": 1672534800.0,    # 2023-01-01T01:00:00+00:00
            "duration_seconds": 3600.0,
            "version": "1.0.0",
            "schema_version": "1.0.0",
            "leaf_cloud_version": "1.0.0",  # Required field
            "config_hash": "test-config-hash",  # Required field
            "description": "Test simulation run",  # Optional field
        },
        "config_snapshot": {
            "workload": {
                "type": "test-workload"
            }
        },
        "resource_metrics": {},
        "token_flow_logs": [],
        "system_events": [],
        "raw_model_outputs": {},
        "power_metrics": {},
        "schema_version": "1.0.0"
    }




class TestAtomicWrite:
    """Tests for the atomic_write context manager."""

    def test_atomic_write_success(self, tmp_path):
        """Test successful atomic write operation."""
        test_file = tmp_path / "test.txt"
        test_content = "Test content"

        with atomic_write(test_file, "w") as f:
            f.write(test_content)

        assert test_file.exists()
        assert test_file.read_text() == test_content

    def test_atomic_write_error_cleanup(self, tmp_path):
        """Test that temp file is cleaned up on error."""
        test_file = tmp_path / "test.txt"

        with pytest.raises(ValueError, match="test error"):
            with atomic_write(test_file, "w") as f:
                f.write("Partial content")
                raise ValueError("test error")

        # Should not exist on error
        assert not test_file.exists()


class TestValidateFilePath:
    """Tests for the _validate_file_path function."""

    def test_validate_existing_file(self, tmp_path, monkeypatch):
        """Test validation of an existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content")
        
        # Mock os.stat to control file size
        original_stat = os.stat
        
        def mock_stat(path, follow_symlinks=True):
            if path == str(test_file):
                stat_result = os.stat_result(
                    (0o644, 0, 0, 0, 0, 0, 1024, 0, 0, 0)  # 1KB file
                )
                return stat_result
            return original_stat(path, follow_symlinks=follow_symlinks)
            
        monkeypatch.setattr(os, 'stat', mock_stat)
        
        def _validate_file_path(file_path: Path) -> None:
            """Validate a file path for security and sanity.

            Args:
                file_path: Path to validate.

            Raises:
                ValueError: If the path is invalid or insecure.
                FileNotFoundError: If the file doesn't exist.
                FileTooLargeError: If the file is too large.
            """
            file_path = file_path.resolve()  # Resolve any symlinks

            # Check file exists
            if not file_path.exists():
                raise FileNotFoundError(f"File not found: {file_path}")

            # Check file size
            file_size = file_path.stat().st_size
            if file_size > MAX_FILE_SIZE:
                raise ValueError(
                    f"File {file_path} is too large ({file_size/1024/1024:.2f}MB). "
                    f"Maximum allowed size is {MAX_FILE_SIZE/1024/1024}MB"
                )

        _validate_file_path(test_file)  # Should not raise

    def test_validate_nonexistent_file(self, tmp_path):
        """Test validation of a non-existent file."""
        test_file = tmp_path / "nonexistent.txt"
        with pytest.raises(FileNotFoundError):
            _validate_file_path(test_file)

    def test_validate_large_file(self, tmp_path, monkeypatch):
        """Test validation of a file that's too large."""
        test_file = tmp_path / "large.bin"
        test_file.write_bytes(b'a' * 10)  # Create a small file with some content

        # Create a mock stat result with a large file size
        class MockStatResult:
            def __init__(self, file_size):
                self.st_size = file_size

        # Mock os.stat to report a large file size
        def mock_stat(path, *args, **kwargs):
            if str(path) == str(test_file):
                return MockStatResult(MAX_FILE_SIZE + 1)
            return os.stat(path, *args, **kwargs)

        monkeypatch.setattr(os, 'stat', mock_stat)

        # Now this should raise the ValueError as expected
        with pytest.raises(ValueError) as exc_info:
            _validate_file_path(test_file)
        
        assert "too large" in str(exc_info.value)


class TestGetResultMetadata:
    """Tests for the get_result_metadata function."""

    def test_get_raw_result_metadata(self, tmp_path):
        """Test getting metadata from a raw result file."""
        test_file = tmp_path / "test.raw.json"
        test_file.write_text(json.dumps(get_raw_result_fixture()))

        metadata = get_result_metadata(test_file)
        assert metadata["type"] == "raw"
        assert metadata["simulation_id"] == SAMPLE_SIMULATION_ID
        assert metadata["schema_version"] == "1.0.0"



    def test_get_metadata_invalid_json(self, tmp_path):
        """Test getting metadata from an invalid JSON file."""
        test_file = tmp_path / "invalid.json"
        test_file.write_text('{"invalid": "json')

        with pytest.raises(ValueError, match="Invalid JSON"):
            get_result_metadata(test_file)


class TestReadResultFile:
    """Tests for the read_result_file function."""

    def test_read_raw_result_file(self, tmp_path):
        """Test reading a raw result file."""
        # Create a test file
        test_file = tmp_path / "test.raw.json"
        test_data = get_raw_result_fixture()
        
        # Write the test data
        test_file.write_text(json.dumps(test_data))
        
        # Read the file
        result = read_result_file(test_file, result_type="raw", validate=True, convert=True)
        
        # Verify the result
        assert isinstance(result, RawSimulationResult)
        assert hasattr(result.metadata, 'leaf_cloud_version'), "metadata should have a leaf_cloud_version attribute"
        assert hasattr(result.metadata, 'config_hash'), "metadata should have a config_hash attribute"
        assert hasattr(result.metadata, 'simulation_id'), "metadata should have a simulation_id attribute"
        assert hasattr(result.metadata, 'start_time'), "metadata should have a start_time attribute"
        assert hasattr(result.metadata, 'end_time'), "metadata should have an end_time attribute"
        assert hasattr(result.metadata, 'duration_seconds'), "metadata should have a duration_seconds attribute"
        assert result.metadata.simulation_id == SAMPLE_SIMULATION_ID
        
        # Test reading without conversion (returns dict)
        dict_result = read_result_file(test_file, result_type="raw", validate=True, convert=True)
        
        # Verify the raw Pydantic model result
        assert isinstance(dict_result, RawSimulationResult), \
            f"Expected RawSimulationResult when validate=True, got {type(dict_result).__name__}"
        assert dict_result.metadata.simulation_id == SAMPLE_SIMULATION_ID
        assert dict_result.metadata.leaf_cloud_version == "1.0.0"
        assert dict_result.metadata.duration_seconds == 3600.0

    def test_read_with_retry_success(self, tmp_path):
        """Test that read retries on temporary failures."""
        test_file = tmp_path / "test_retry.raw.json"
        test_data = get_raw_result_fixture()
        
        # Create the test file first with a known content
        with open(test_file, "w") as f:
            json.dump(test_data, f)
        
        # Create a mock for the file object that will fail first time
        call_count = 0
        
        def mock_file_operations(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            
            if call_count == 1:
                # First call fails with IOError
                raise IOError("Temporary read error")
            elif call_count == 2:
                # Second call succeeds
                return mock_open(read_data=json.dumps(test_data))()
            else:
                # Any additional calls should use the real implementation
                return open(*args, **kwargs)
        
        # Patch the open function in the results_io module only
        with patch('leaf_cloud.utils.results_io.open', side_effect=mock_file_operations) as mock_open_func, \
             patch('time.sleep') as mock_sleep:
            
            # This should succeed after retry
            result = read_result_file(
                test_file, 
                result_type="raw", 
                validate=True,
                convert=True,
                max_retries=2, 
                retry_delay=0.01
            )
            
            # Verify retry behavior
            assert call_count == 2  # Should have been called twice (1 failure + 1 success)
            assert mock_sleep.call_count == 1
            assert mock_sleep.call_args[0][0] == 0.01
            
            # Verify the result is correct
            from leaf_cloud.utils.schemas.results import RawSimulationResult
            assert isinstance(result, RawSimulationResult)
            assert result.metadata.simulation_id == SAMPLE_SIMULATION_ID


class TestWriteResultFile:
    """Tests for the write_result_file function."""

    def test_write_raw_result(self, tmp_path):
        """Test writing a raw result file."""
        # Create a test directory
        output_dir = tmp_path / "output"
        test_file = output_dir / "test.raw.json"
        
        # Create test data
        test_data = get_raw_result_fixture()
        
        # Create a real file descriptor for fileno()
        test_fd, test_file_path = tempfile.mkstemp()
        os.close(test_fd)  # Close it immediately since we just need the fd
        
        # Create mock file with real file descriptor
        mock_file = MagicMock()
        mock_file.fileno.return_value = test_fd
        mock_file.__enter__.return_value = mock_file
        mock_file.__exit__.return_value = None
        
        # Track if mkdir was called
        mkdir_called = []
        
        def mock_mkdir(self, parents=False, exist_ok=False):
            mkdir_called.append((self, parents, exist_ok))
            # Actually create the directory for the test
            os.makedirs(str(self), exist_ok=exist_ok)
            
        # Mock file operations
        with patch.object(Path, 'mkdir', side_effect=mock_mkdir, autospec=True) as mock_mkdir_func, \
             patch('os.path.exists', return_value=False), \
             patch('os.chmod'), \
             patch('os.path.getsize', return_value=1024), \
             patch('os.path.isfile', return_value=False), \
             patch('os.fsync'), \
             patch('leaf_cloud.utils.results_io.atomic_write', autospec=True) as mock_atomic_write:
            
            # Ensure the directory doesn't exist yet
            assert not output_dir.exists()
            
            # Write the test data
            output_path = write_result_file(
                test_data, 
                str(test_file),  # Use string path to test path handling
                overwrite=True,
                pretty=False  # Avoid platform-specific line endings
            )
            
            # Verify the directory was created
            assert len(mkdir_called) > 0, "Path.mkdir should have been called to create the directory"
            assert any(str(output_dir) in str(call[0]) for call in mkdir_called), \
                f"Expected Path.mkdir to be called with {output_dir}, but got {mkdir_called}"
            
            # Verify the file was written via atomic_write
            mock_atomic_write.assert_called_once()
            call_args, call_kwargs = mock_atomic_write.call_args
            
            # atomic_write is called with (file_path, mode='w', **kwargs)
            assert len(call_args) >= 1, "atomic_write should be called with at least one argument"
            assert str(call_args[0]) == str(test_file), \
                f"Expected file to be written at {test_file}, but got {call_args[0] if call_args else 'nothing'}"
                
            # The mode is the second positional argument or in kwargs
            mode = call_args[1] if len(call_args) > 1 else call_kwargs.get('mode', 'w')
            assert mode == 'w', \
                f"Expected file to be written in write mode, but got {mode}"
            
            # Verify the output path is correct
            assert output_path == test_file
            
            # Get the content that was passed to atomic_write
            write_calls = mock_atomic_write.return_value.__enter__.return_value.write.call_args_list
            written_json = ''.join(call[0][0] for call in write_calls)
            written_data = json.loads(written_json)
            
            # Verify the data was written correctly
            assert written_data["metadata"]["simulation_id"] == SAMPLE_SIMULATION_ID
            assert written_data["metadata"]["schema_version"] == "1.0.0"
            
            # Verify the atomic_write context manager was used properly
            mock_atomic_write.return_value.__exit__.assert_called_once_with(None, None, None)
            
            # Verify the file permissions were set (os.chmod is called by atomic_write)

    def test_write_existing_file_no_overwrite(self, tmp_path):
        """Test that write fails if file exists and overwrite=False."""
        test_file = tmp_path / "test_no_overwrite.raw.json"
        test_data = get_raw_result_fixture()
        
        # Create the file first
        with open(test_file, "w") as f:
            json.dump({"test": "original"}, f)
        
        # Try to write without overwrite
        with pytest.raises(FileExistsError):
            write_result_file(test_data, test_file, overwrite=False)
        
        # Verify the file wasn't changed
        with open(test_file, "r") as f:
            content = json.load(f)
        assert content.get("test") == "original"

    def test_write_existing_file_with_overwrite(self, tmp_path):
        """Test that write succeeds if file exists and overwrite=True."""
        test_file = tmp_path / "test_overwrite.raw.json"
        test_data = get_raw_result_fixture()
        
        # Create the file first with different content
        with open(test_file, "w") as f:
            json.dump({"test": "original"}, f)
        
        # Overwrite the file with our test data
        output_path = write_result_file(test_data, test_file, overwrite=True)
        
        # Verify the file was overwritten
        assert output_path == test_file
        with open(test_file, 'r') as f:
            data = json.load(f)
            
        assert data["metadata"]["simulation_id"] == SAMPLE_SIMULATION_ID
        assert data["metadata"]["version"] == "1.0.0"


class TestConvertResultFile:
    """Tests for the convert_result_file function."""

    def test_convert_raw_to_processed(self, tmp_path):
        """Test converting a raw result to a processed result."""
        input_file = tmp_path / "input.raw.json"
        output_file = tmp_path / "output.processed.json"
        
        # Get raw result fixture and write it to file
        raw_data = get_raw_result_fixture()
        with open(input_file, "w") as f:
            json.dump(raw_data, f)

        # Mock the actual conversion logic since it's complex
        with patch(
            "leaf_cloud.utils.results_io.read_result_file",
            return_value=RawSimulationResult(**raw_data),
        ) as mock_read, patch(
            "leaf_cloud.utils.results_io.write_result_file"
        ) as mock_write:
            mock_write.return_value = output_file

            result_path = convert_result_file(
                input_file, output_file, output_format="processed"
            )

            assert result_path == output_file
            mock_read.assert_called_once()
            mock_write.assert_called_once()

    def test_auto_detect_output_format(self, tmp_path):
        """Test auto-detection of output format from filename."""
        input_file = tmp_path / "input.raw.json"
        output_file = tmp_path / "output.processed.json"
        
        # Get raw result fixture and write it to file
        raw_data = get_raw_result_fixture()
        with open(input_file, "w") as f:
            json.dump(raw_data, f)

        with patch(
            "leaf_cloud.utils.results_io.read_result_file",
            return_value=RawSimulationResult(**raw_data),
        ) as mock_read, patch("leaf_cloud.utils.results_io.write_result_file") as mock_write:
            mock_write.return_value = output_file

            result_path = convert_result_file(input_file, output_file)

            assert result_path == output_file
            mock_read.assert_called_once()
            mock_write.assert_called_once()


class TestBackupResultFile:
    """Tests for the backup_result_file function."""

    def test_backup_file(self, tmp_path, monkeypatch):
        """Test creating a backup of a file."""
        # Create a test file
        test_file = tmp_path / "test_backup.raw.json"
        test_file.write_text('{"test": "data"}')
        
        # Create a backup directory
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        
        # Mock the current time for consistent backup filenames
        class MockDateTime:
            @classmethod
            def now(cls):
                return datetime(2023, 1, 1, 12, 0, 0)
        
        # Patch the datetime in the results_io module
        monkeypatch.setattr('leaf_cloud.utils.results_io.datetime', MockDateTime)
        
        # Create a backup
        backup_path = backup_result_file(test_file, backup_dir=backup_dir)
        
        # Verify backup was created with expected name
        # The actual implementation adds .bak before the extension
        expected_backup = backup_dir / "test_backup.raw.20230101_120000.bak.json"
        assert str(backup_path) == str(expected_backup), \
            f"Expected backup path {expected_backup}, got {backup_path}"
        assert backup_path.exists(), "Backup file was not created"
        assert backup_path.read_text() == '{"test": "data"}', "Backup content doesn't match"
        
        # Verify original file still exists
        assert test_file.exists(), "Original file was removed"

    def test_backup_with_rotation(self, tmp_path, monkeypatch):
        """Test backup rotation when max_backups is reached."""
        # Create a test file
        source_file = tmp_path / "source.txt"
        source_file.write_text("Original content")
        
        # Create a backup directory with existing backups
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        
        # Mock the current time for consistent backup filenames
        mock_time = datetime(2023, 1, 1, 12, 0, 0)
        datetime_mock = MagicMock(wraps=datetime)
        datetime_mock.now.return_value = mock_time
        monkeypatch.setattr('datetime.datetime', datetime_mock)
        
        # Create some old backups with different timestamps
        old_backups = []
        for i in range(3):
            # Move time forward for each backup
            mock_time = datetime(2023, 1, 1, 12, i, 0)
            datetime_mock.now.return_value = mock_time
            
            # Create backup with timestamp in name
            backup_path = backup_result_file(
                source_file, 
                backup_dir=backup_dir,
                suffix=f"old_{i}"
            )
            backup_path.write_text(f"Old backup {i}")
            old_backups.append(backup_path)
        
        # Move time forward for new backup
        mock_time = datetime(2023, 1, 1, 12, 3, 0)
        datetime_mock.now.return_value = mock_time
        
        # Create a new backup with rotation
        backup_result_file(
            source_file, 
            backup_dir=backup_dir,
            max_backups=3,
            suffix="new"
        )
        
        # Get all backup files
        backup_files = sorted(list(backup_dir.glob("source*")))

        # Should have max_backups + 1 files (the new one plus the old ones)
        assert len(backup_files) == 4  # 3 old + 1 new

        # The new backup should exist with the expected name
        new_backup_path = backup_dir / "sourcenew.txt"
        assert new_backup_path.exists()
        assert new_backup_path in backup_files

    def test_backup_source_not_found(self, tmp_path):
        """Test backup when source file doesn't exist."""
        source_file = tmp_path / "nonexistent.txt"
        with pytest.raises(FileNotFoundError):
            backup_result_file(source_file)
