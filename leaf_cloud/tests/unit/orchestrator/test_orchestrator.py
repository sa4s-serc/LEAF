"""
Unit tests for the LEAF-Cloud Orchestrator core and event handlers.
"""
import pytest
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from leaf_cloud.config import LEAFCloudConfig

from leaf_cloud.orchestrator.core import Orchestrator
from leaf_cloud.orchestrator.handlers.base import BaseEventHandler
from leaf_cloud.orchestrator.handlers.token_creation import TokenCreationHandler
from leaf_cloud.orchestrator.handlers.token_distribution import TokenDistributionHandler
from leaf_cloud.orchestrator.handlers.token_completion import TokenCompletionHandler
from leaf_cloud.orchestrator.handlers.transition_completion import TransitionCompletionHandler
from leaf_cloud.orchestrator.registry import EventHandlerRegistry
from leaf_cloud.leaf_types import TokenFlowLogEntry


@pytest.fixture
def mock_config():
    """Fixture for a mock LEAFCloudConfig."""
    config = MagicMock(spec=LEAFCloudConfig)
    config.simulation = MagicMock()
    config.metrics = MagicMock()
    config.logging = MagicMock()
    config.workload = MagicMock()
    config.models = MagicMock()
    config.output_dir = MagicMock()
    config.simulation.duration = 100
    config.simulation.time_step = 1
    config.metrics.token_flow_log = "/tmp/log.json"
    config.logging.level = "INFO"
    return config


@pytest.fixture
def mock_orchestrator(mock_config):
    """Fixture to create a mock Orchestrator instance with no default handlers."""
    with patch.object(Orchestrator, '_register_default_handlers', return_value=None):
        orchestrator = Orchestrator(config=mock_config)
        orchestrator._metrics = MagicMock()
        orchestrator._petri_net = MagicMock()
        orchestrator._model_builder = MagicMock()
        return orchestrator


class TestOrchestratorEventProcessing:
    """Tests for the Orchestrator's event processing logic."""

    def test_process_event_success(self, mock_orchestrator):
        """Test that _process_event successfully processes an event."""
        # Arrange
        event_type = "TOKEN_CREATION"
        event_data = {"place": "p1", "count": 5}
        # Define a concrete mock handler class that implements the abstract methods
        class MockSuccessHandler(BaseEventHandler):
            EVENT_TYPE = "TOKEN_CREATION"
            
            def can_handle(self, event_type: str) -> bool:
                return event_type == self.EVENT_TYPE

            def handle(self, event_data: dict) -> tuple[int, list]:
                # This method is abstract and must be implemented,
                # but its logic will be mocked on the instance.
                raise NotImplementedError("This should be mocked")

        mock_handler = MockSuccessHandler(orchestrator=mock_orchestrator)
        
        # Mock the handle method
        mock_handler.handle = MagicMock(return_value=(
            5, 
            [TokenFlowLogEntry(
                timestamp=datetime.now(timezone.utc).timestamp(),
                event_type="test",
                details={"test": "debug"}
            )]
        ))

        # Add the valid handler to the registry
        mock_orchestrator._event_registry.add(mock_handler)

        # Act
        tokens_processed, log_entries = mock_orchestrator._process_event(event_type, event_data)
        
        # Debug output
        print(f"[TEST] Tokens processed: {tokens_processed}")
        print(f"[TEST] Log entries: {log_entries}")
        if log_entries:
            print(f"[TEST] First log entry type: {getattr(log_entries[0], 'event_type', 'N/A')}")
        
        # Assert
        print("\n[TEST] Running assertions")
        print(f"[TEST] Checking if handle was called with: {event_data}")
        mock_handler.handle.assert_called_once_with(event_data)
        print("[TEST] Handle call verified")
        
        print(f"[TEST] Checking log entries length (expected 1): {len(log_entries) if log_entries else 0}")
        assert len(log_entries) == 1, f"Expected 1 log entry, got {len(log_entries) if log_entries else 0}"
        
        print(f"[TEST] Checking log entry type (expected 'test'): {log_entries[0].event_type}")
        assert log_entries[0].event_type == "test"
        
        print("[TEST] Verifying record_events was not called")
        mock_orchestrator._metrics.record_events.assert_not_called()
        print("[TEST] All assertions passed")

    def test_process_event_handler_not_found(self, mock_orchestrator, caplog):
        """Test that a warning is logged if no handler is found for an event."""
        # Arrange
        event_type = "UNKNOWN_EVENT"
        event_data = {}

        # Act
        with caplog.at_level(logging.WARNING):
            _, log_entries = mock_orchestrator._process_event(event_type, event_data)

        # Assert
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelname == "WARNING"
        assert f"No handler found for event type: {event_type}" in record.message
        assert log_entries == []

    def test_process_event_handler_exception(self, mock_orchestrator):
        """Test that an exception in a handler is caught and logged."""
        # Arrange
        event_type = "FAIL_EVENT"
        event_data = {"reason": "testing exception"}
        class MockExceptionHandler(BaseEventHandler):
            def can_handle(self, event_type: str) -> bool: return False
            def handle(self, event_data: dict) -> tuple[int, list]: return 0, []

        mock_handler = MockExceptionHandler(orchestrator=mock_orchestrator)
        mock_handler.can_handle = MagicMock(side_effect=lambda e: e == event_type)
        exception = Exception("Handler failed")
        mock_handler.handle = MagicMock(side_effect=exception)
        mock_orchestrator._event_registry.add(mock_handler)

        mock_orchestrator._log_error = MagicMock()

        # Act
        mock_orchestrator._process_event(event_type, event_data)

        # Assert
        mock_orchestrator._log_error.assert_called_once()
        call_args, call_kwargs = mock_orchestrator._log_error.call_args
        assert "Error in" in call_args[0]
        assert call_kwargs["error"] == exception
        mock_orchestrator._metrics.record_events.assert_not_called()


class TestEventHandlers:
    """Tests for individual event handlers."""

    @pytest.mark.parametrize("handler_class", [
        TokenCreationHandler,
        TokenDistributionHandler,
        TokenCompletionHandler,
        TransitionCompletionHandler
    ])
    def test_handler_interface(self, handler_class, mock_orchestrator):
        """Test that each handler returns the correct tuple (tokens_processed, log_entries)."""
        handler = handler_class(orchestrator=mock_orchestrator)
        mock_orchestrator.petri_net.find_token.return_value = MagicMock()
        # Create mock data that is sufficient for handlers to run without error
        mock_event_data = {
            'petri_net': MagicMock(),
            'simulation_state': MagicMock(),
            'transition': MagicMock(),
            'place': MagicMock(),
            'token': MagicMock(),
            'count': 1
        }
        # Mock methods that might be called
        if hasattr(handler, '_log_token_creation'):
            handler._log_token_creation = MagicMock(return_value=[])

        tokens_processed, log_entries = handler.handle(mock_event_data)

        assert isinstance(tokens_processed, int)
        assert isinstance(log_entries, list)
        for entry in log_entries:
            assert isinstance(entry, TokenFlowLogEntry)

class TestWorkloadConfiguration:
    def test_configure_burst_accepts_peak_rate(self, mock_orchestrator):
        workload_cfg = {
            'type': 'burst',
            'params': {
                'base_rate': 10,
                'peak_rate': 100,
                'duration': 60,
                'interval': 300,
            },
        }
        mock_orchestrator.configure_workload(workload_cfg)
        assert mock_orchestrator._workloads, "Expected workload to be registered"
        workload = mock_orchestrator._workloads[0]
        assert workload.burst_rate == pytest.approx(100.0)
        assert workload.burst_duration == pytest.approx(60.0)
        assert workload.burst_interval == pytest.approx(300.0)
        stored = mock_orchestrator._workload_params.get('params', {})
        assert stored.get('burst_rate') == pytest.approx(100.0)
        assert stored.get('burst_duration') == pytest.approx(60.0)
        assert stored.get('burst_interval') == pytest.approx(300.0)
