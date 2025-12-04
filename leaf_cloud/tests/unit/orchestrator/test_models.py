"""
Unit tests for the orchestrator models.

These tests verify the functionality of the data models used in the orchestrator module,
including SimulationState, Token, and related classes.
"""
import dataclasses
import time
import uuid
from typing import Dict, Any, List, Optional

import pytest

from leaf_cloud.orchestrator.models import (
    SimulationState, Token, TokenColor, TokenFlowLogEntry,
    TokenCreationLog, TokenDistributionLog, TransitionFiredLog,
    TokenCompletedLog, ProgressState
)


class TestSimulationState:
    """Test cases for the SimulationState enum."""
    
    def test_enum_values(self):
        """Test that all expected states are present and have correct values."""
        assert len(SimulationState) == 10, "Unexpected number of simulation states"
        assert SimulationState.INITIALIZING.value == 1
        assert SimulationState.PARSING.value == 2
        assert SimulationState.BUILDING.value == 3
        assert SimulationState.CONFIGURING.value == 4
        assert SimulationState.READY.value == 5
        assert SimulationState.RUNNING.value == 6
        assert SimulationState.PAUSED.value == 7
        assert SimulationState.COMPLETED.value == 8
        assert SimulationState.FAILED.value == 9
        assert SimulationState.EXPORTING.value == 10
    
    def test_enum_ordering(self):
        """Test that states have the expected ordering."""
        states = list(SimulationState)
        for i in range(1, len(states)):
            assert states[i-1].value < states[i].value, \
                f"Unexpected ordering: {states[i-1]} and {states[i]}"
    
    def test_string_representation(self):
        """Test the string representation of states."""
        assert str(SimulationState.INITIALIZING) == "SimulationState.INITIALIZING"
        assert str(SimulationState.RUNNING) == "SimulationState.RUNNING"
        assert str(SimulationState.COMPLETED) == "SimulationState.COMPLETED"


class TestToken:
    """Test cases for the Token class."""
    
    @pytest.fixture
    def sample_token(self) -> Token:
        """Create a sample token for testing."""
        return Token(
            id="token-123",
            color=TokenColor.REQUEST,
            attributes={"key1": "value1", "count": 42},
            creation_time=time.time()
        )
    
    def test_token_initialization(self, sample_token: Token):
        """Test token initialization with all attributes."""
        assert sample_token.id == "token-123"
        assert sample_token.color == TokenColor.REQUEST
        assert sample_token.attributes == {"key1": "value1", "count": 42}
        assert isinstance(sample_token.creation_time, float)
        assert sample_token.completion_time is None
        assert sample_token.history == []
    
    def test_token_equality(self, sample_token: Token):
        """Test token equality based on all attributes."""
        # Different attributes should make tokens unequal
        token2 = Token(
            id="token-123",
            color=TokenColor.WORKLOAD,  # Different color
            attributes={"different": "attributes"},
            creation_time=time.time() + 100  # Different time
        )
        assert sample_token != token2, "Tokens with different attributes should not be equal"
        
        # Different ID
        token3 = Token(
            id="token-456",  # Different ID
            color=TokenColor.REQUEST,
            attributes={"key1": "value1", "count": 42},
            creation_time=sample_token.creation_time
        )
        assert sample_token != token3, "Tokens with different IDs should not be equal"
        
        # Exactly the same token
        token4 = Token(
            id="token-123",
            color=TokenColor.REQUEST,
            attributes={"key1": "value1", "count": 42},
            creation_time=sample_token.creation_time
        )
        assert sample_token == token4, "Tokens with same attributes should be equal"
    
    def test_token_immutability(self, sample_token: Token):
        """Test that tokens are immutable and cannot be modified after creation."""
        # Verify that attributes cannot be modified directly
        with pytest.raises(dataclasses.FrozenInstanceError):
            sample_token.id = "new-id"
            
        with pytest.raises(dataclasses.FrozenInstanceError):
            sample_token.color = TokenColor.WORKLOAD
            
        # Verify that history cannot be modified through direct assignment
        with pytest.raises(dataclasses.FrozenInstanceError):
            sample_token.history = [{"time": time.time(), "event": "test"}]
            
        # Verify that the token is hashable (a benefit of being immutable)
        token_dict = {sample_token: "test"}
        assert sample_token in token_dict
        
        # Test that with_updates creates a new token with the specified changes
        new_attributes = {"new_key": "new_value", **sample_token.attributes}
        updated_token = sample_token.with_updates(attributes=new_attributes)
        
        # Original token should not be modified
        assert sample_token.attributes != updated_token.attributes
        assert "new_key" in updated_token.attributes
        assert updated_token.attributes["new_key"] == "new_value"
    
    def test_token_equality(self, sample_token: Token):
        """Test that tokens are equal only when all fields match."""
        # Create an identical token using with_updates
        identical_token = sample_token.with_updates()
        
        # Identical tokens should be equal
        assert sample_token == identical_token
        assert hash(sample_token) == hash(identical_token)
        
        # Tokens with different attributes should not be equal
        different_token = sample_token.with_updates(
            attributes={"different": "attributes"}
        )
        assert sample_token != different_token, "Tokens with different attributes should not be equal"
        assert hash(sample_token) != hash(different_token)
        
        # Tokens with different IDs should not be equal
        different_id_token = Token(
            id="different-id",
            color=sample_token.color,
            attributes=dict(sample_token.attributes),
            creation_time=sample_token.creation_time,
            history=list(sample_token.history),
            completion_time=sample_token.completion_time
        )
        assert sample_token != different_id_token, "Tokens with different IDs should not be equal"
        assert hash(sample_token) != hash(different_id_token)
    
    def test_token_completion(self, sample_token: Token):
        """Test token completion status."""
        # Token starts as not completed
        assert sample_token.completion_time is None
        
        # Create a new completed token using with_updates
        completion_time = time.time()
        completed_token = sample_token.with_updates(completion_time=completion_time)
        
        # Verify completion time was set
        assert completed_token.completion_time == completion_time
        
        # Original token should remain unchanged
        assert sample_token.completion_time is None
    
    def test_token_history(self, sample_token: Token):
        """Test token history entries."""
        # Token starts with no history
        assert len(sample_token.history) == 0
        
        # Create history entries
        entry1 = {"time": time.time(), "event": "created", "place": "start"}
        entry2 = {"time": time.time() + 1, "event": "moved", "place": "middle"}
        
        # Create a new token with history using with_updates
        token_with_history = sample_token.with_updates(history=[entry1, entry2])
        
        # Verify history is preserved
        assert len(token_with_history.history) == 2
        assert token_with_history.history[0] == entry1
        assert token_with_history.history[1] == entry2
        
        # Original token should remain unchanged
        assert len(sample_token.history) == 0


class TestProgressState:
    """Test cases for the ProgressState class."""
    
    def test_initialization(self):
        """Test initialization with default and custom values."""
        # Test with default values
        progress = ProgressState(0.0, 100.0, 0, 0, 0, 0.0)
        assert progress.current_time == 0.0
        assert progress.duration == 100.0
        assert progress.completed_tokens == 0
        assert progress.pending_events == 0
        assert progress.errors == 0
        assert progress.events_per_sec == 0.0
        assert progress.final_status is None
        
        # Test with custom values
        progress = ProgressState(
            current_time=50.5,
            duration=100.0,
            completed_tokens=10,
            pending_events=5,
            errors=2,
            events_per_sec=25.5,
            final_status="Completed"
        )
        assert progress.current_time == 50.5
        assert progress.duration == 100.0
        assert progress.completed_tokens == 10
        assert progress.pending_events == 5
        assert progress.errors == 2
        assert progress.events_per_sec == 25.5
        assert progress.final_status == "Completed"
    
    def test_progress_calculation(self):
        """Test progress percentage calculation."""
        # Test with non-zero duration
        progress = ProgressState(25.0, 100.0, 0, 0, 0, 0.0)
        assert progress.progress_percentage == 25.0
        
        # Test with zero duration (should not raise division by zero)
        progress = ProgressState(10.0, 0.0, 0, 0, 0, 0.0)
        assert progress.progress_percentage == 0.0
        
        # Test with negative duration (edge case)
        progress = ProgressState(10.0, -100.0, 0, 0, 0, 0.0)
        assert progress.progress_percentage == 0.0
        
        # Test with current_time > duration (should cap at 100%)
        progress = ProgressState(150.0, 100.0, 0, 0, 0, 0.0)
        assert progress.progress_percentage == 100.0
    
    def test_remaining_time_calculation(self):
        """Test remaining time calculation."""
        # Test with non-zero events per second
        progress = ProgressState(50.0, 100.0, 100, 50, 0, 10.0)
        assert progress.estimated_remaining_time == 5.0  # 50 events / 10 events per second
        
        # Test with zero events per second (should return None)
        progress = ProgressState(50.0, 100.0, 100, 50, 0, 0.0)
        assert progress.estimated_remaining_time is None
        
        # Test with no pending events (should return 0)
        progress = ProgressState(50.0, 100.0, 100, 0, 0, 10.0)
        assert progress.estimated_remaining_time == 0.0
    
    def test_immutability(self):
        """Test that ProgressState instances are immutable."""
        progress = ProgressState(0.0, 100.0, 0, 0, 0, 0.0)
        
        # Test that attributes cannot be modified directly
        with pytest.raises(dataclasses.FrozenInstanceError):
            progress.current_time = 10.0
            
        with pytest.raises(dataclasses.FrozenInstanceError):
            progress.completed_tokens = 5
            
        with pytest.raises(dataclasses.FrozenInstanceError):
            progress.final_status = "Failed"
    
    def test_edge_cases(self):
        """Test edge cases in ProgressState initialization."""
        # Test with negative values
        progress = ProgressState(-10.0, -100.0, -5, -2, -1, -10.0)
        assert progress.current_time == -10.0
        assert progress.duration == -100.0
        assert progress.completed_tokens == -5
        assert progress.pending_events == -2
        assert progress.errors == -1
        assert progress.events_per_sec == -10.0
        
        # Test with very large values
        large_num = float('inf')
        progress = ProgressState(large_num, large_num, 2**64, 2**64, 2**64, large_num)
        assert progress.current_time == large_num
        assert progress.duration == large_num
        assert progress.completed_tokens == 2**64
        assert progress.pending_events == 2**64
        assert progress.errors == 2**64
        assert progress.events_per_sec == large_num
    
    def test_string_representation(self):
        """Test the string representation of ProgressState."""
        progress = ProgressState(50.0, 100.0, 10, 5, 2, 25.0, "Running")
        s = str(progress)
        
        # Check that all important fields are in the string representation
        assert "50.0/100.0s (50.0%)" in s
        assert "10 completed" in s
        assert "5 pending" in s
        assert "Errors: 2" in s
        assert "25.0 events/s" in s
        assert "Status: Running" in s
        
        # Test with no final status
        progress = ProgressState(50.0, 100.0, 10, 5, 2, 25.0)
        s = str(progress)
        assert "Running" not in s


class TestLogTypes:
    """Test cases for the log type classes."""
    
    def test_token_creation_log(self):
        """Test the TokenCreationLog type."""
        log: TokenCreationLog = {
            "time": 123.45,
            "event": "token_created",
            "token_id": "token-123",
            "place": "start",
            "color": "request"
        }
        
        assert log["time"] == 123.45
        assert log["event"] == "token_created"
        assert log["token_id"] == "token-123"
        assert log["place"] == "start"
        assert log["color"] == "request"
    
    def test_token_distribution_log(self):
        """Test the TokenDistributionLog type."""
        log: TokenDistributionLog = {
            "time": 123.45,
            "event": "token_distribution",
            "distribution": {"place1": 5, "place2": 3}
        }
        
        assert log["time"] == 123.45
        assert log["event"] == "token_distribution"
        assert log["distribution"] == {"place1": 5, "place2": 3}
    
    def test_transition_fired_log(self):
        """Test the TransitionFiredLog type."""
        log: TransitionFiredLog = {
            "time": 123.45,
            "event": "transition_fired",
            "transition": "t1",
            "input_places": {"p1": 1},
            "output_places": {"p2": 1},
            "resources": [{"id": "r1", "type": "cpu"}],
            "delay": 1.5
        }
        
        assert log["time"] == 123.45
        assert log["event"] == "transition_fired"
        assert log["transition"] == "t1"
        assert log["input_places"] == {"p1": 1}
        assert log["output_places"] == {"p2": 1}
        assert log["resources"] == [{"id": "r1", "type": "cpu"}]
        assert log["delay"] == 1.5
    
    def test_token_completed_log(self):
        """Test the TokenCompletedLog type."""
        log: TokenCompletedLog = {
            "time": 123.45,
            "event": "token_completed",
            "token_id": "token-123",
            "place": "end"
        }
        
        assert log["time"] == 123.45
        assert log["event"] == "token_completed"
        assert log["token_id"] == "token-123"
        assert log["place"] == "end"
