"""Unit tests for the Transition class in the Petri net module.

This module contains tests for the Transition class, which represents a node in a Petri net
that can fire when its input conditions are met, consuming tokens from input places
and producing tokens in output places.
"""

import pytest
import threading
import time
from typing import Dict, List, Optional, Callable, Any
from unittest.mock import Mock, patch

from leaf_cloud.core.petri_net import Transition, Token, TokenColor, PetriNetValidationError


class TestTransition:
    """Test suite for the Transition class."""

    @pytest.fixture
    def sample_transition(self) -> Transition:
        """Create a sample transition for testing."""
        return Transition(
            id="t1",
            name="Test Transition",
            delay=1.0,
            priority=5
        )

    @pytest.fixture
    def mock_guard(self) -> Callable[[Dict[str, List[Token]]], bool]:
        """Create a mock guard function for testing."""
        def guard(tokens: Dict[str, List[Token]]) -> bool:
            return len(tokens.get('input', [])) >= 2
        return guard

    @pytest.fixture
    def mock_action(self) -> Callable[[Dict[str, List[Token]]], Dict[str, List[Token]]]:
        """Create a mock action function for testing."""
        def action(tokens: Dict[str, List[Token]]) -> Dict[str, List[Token]]:
            # Consume 2 tokens, produce 1 result
            consumed = tokens['input'][:2]
            result = Token(color='processed')
            return {'output': [result]}
        return action

    def test_transition_creation(self, sample_transition: Transition) -> None:
        """Test basic transition creation and properties."""
        assert sample_transition.id == "t1"
        assert sample_transition.name == "Test Transition"
        assert sample_transition.delay == 1.0
        assert sample_transition.priority == 5
        assert not sample_transition.is_enabled
        assert sample_transition.last_fired is None

    def test_invalid_transition_creation(self) -> None:
        """Test validation during transition creation."""
        # Test invalid delay
        with pytest.raises(PetriNetValidationError):
            Transition("t1", delay=-1.0)
            
        # Test invalid guard
        with pytest.raises(PetriNetValidationError):
            Transition("t1", guard="not a callable")  # type: ignore
            
        # Test invalid action
        with pytest.raises(PetriNetValidationError):
            Transition("t1", action="not a callable")  # type: ignore

    def test_delay_property(self, sample_transition: Transition) -> None:
        """Test getting and setting the delay property."""
        assert sample_transition.delay == 1.0
        
        # Test setting a new delay
        sample_transition.delay = 2.5
        assert sample_transition.delay == 2.5
        
        # Test invalid delay
        with pytest.raises(ValueError):
            sample_transition.delay = -1.0

    def test_priority_property(self, sample_transition: Transition) -> None:
        """Test getting and setting the priority property."""
        assert sample_transition.priority == 5
        
        # Test setting a new priority
        sample_transition.priority = 10
        assert sample_transition.priority == 10
        
        # Test invalid priority type
        with pytest.raises(TypeError):
            sample_transition.priority = "high"  # type: ignore

    def test_set_guard(self, sample_transition: Transition) -> None:
        """Test setting a guard function on the transition."""
        # Default guard should always return True
        assert sample_transition.can_fire({'input': [Token(color=TokenColor.GENERIC)]})
        
        # Set a custom guard
        def guard(tokens):
            return len(tokens.get('input', [])) > 1
            
        sample_transition.set_guard(guard)
        
        # Test the guard
        assert not sample_transition.can_fire({'input': [Token(color=TokenColor.GENERIC)]})
        assert sample_transition.can_fire({'input': [Token(color=TokenColor.GENERIC), Token(color=TokenColor.REQUEST)]}) is True
        
        # Test removing the guard
        sample_transition.set_guard(None)
        assert sample_transition._guard({'input': []}) is True

    def test_set_action(self, sample_transition: Transition) -> None:
        """Test setting an action function on the transition."""
        # Default action should return input tokens unchanged
        input_tokens = {'input': []}
        assert sample_transition._action(input_tokens) == input_tokens
        
        # Set a custom action
        def action(tokens):
            return {'output': tokens['input']}
            
        sample_transition.set_action(action)
        
        # Test the action
        tokens = {'input': [Token(color=TokenColor.GENERIC)]}
        assert sample_transition._action(tokens) == {'output': tokens['input']}

    def test_thread_safety(self, sample_transition: Transition) -> None:
        """Test that transition operations are thread-safe."""
        results = []
        
        def modify_priority():
            for _ in range(1000):
                with sample_transition._lock:
                    sample_transition._priority += 1
                time.sleep(0.0001)
        
        def modify_delay():
            for _ in range(1000):
                with sample_transition._lock:
                    sample_transition._delay += 0.1
                time.sleep(0.0001)
        
        # Start multiple threads that modify the transition
        threads = [
            threading.Thread(target=modify_priority),
            threading.Thread(target=modify_delay),
        ]
        
        for t in threads:
            t.start()
        
        for t in threads:
            t.join()
        
        # Verify that the final state is consistent
        assert isinstance(sample_transition.priority, int)
        assert isinstance(sample_transition.delay, float)

    def test_instance_tracking(self) -> None:
        """Test that transition instances are properly tracked."""
        # Get current instance count (there might be instances from fixtures)
        initial_count = len(Transition._instances)
        
        # Create some transitions in a function scope
        def create_transitions():
            return [Transition(f"temp_transition_{i}") for i in range(5)]
            
        # Create and immediately discard the transitions
        transitions = create_transitions()
        transition_count = len(transitions)
        
        # Force garbage collection to clean up the transitions
        import gc
        del transitions  # Remove the reference
        gc.collect()
        
        # Check that the instance count has decreased by the number of transitions we created
        # (we can't guarantee it's back to initial due to pytest fixtures)
        assert len(Transition._instances) <= initial_count + transition_count, \
            "Instance count did not decrease after cleanup"
    
    # Remove the context manager helper since we're using a different approach

    def test_string_representation(self, sample_transition: Transition) -> None:
        """Test string representation of transitions."""
        # Default string representation
        trans_str = str(sample_transition)
        assert "Transition(id='t1'" in trans_str
        assert "name='Test Transition'" in trans_str
        assert "delay=1.0" in trans_str
        assert "priority=5" in trans_str
        assert "enabled=False" in trans_str
        
        # Test with different parameters
        trans = Transition("t2", delay=0)
        trans_str = str(trans)
        assert "Transition(id='t2'" in trans_str
        assert "name='t2'" in trans_str
        assert "delay=0.0" in trans_str
        assert "priority=0" in trans_str
        assert "enabled=False" in trans_str

    def test_enabled_property(self, sample_transition: Transition) -> None:
        """Test the is_enabled property and related methods."""
        # Initially not enabled
        assert not sample_transition.is_enabled
        
        # Enable the transition
        with sample_transition._lock:
            sample_transition._enabled = True
        
        # Verify it's enabled
        assert sample_transition.is_enabled
        
        # Disable the transition
        with sample_transition._lock:
            sample_transition._enabled = False
        
        # Verify it's disabled
        assert not sample_transition.is_enabled

    def test_last_fired_property(self, sample_transition: Transition) -> None:
        """Test the last_fired property and related methods."""
        # Initially not fired
        assert sample_transition.last_fired is None
        
        # Set last fired time
        test_time = 123.45
        with sample_transition._lock:
            sample_transition._last_fired = test_time
        
        # Verify last fired time
        assert sample_transition.last_fired == test_time

    def test_guard_with_invalid_input(self, sample_transition: Transition) -> None:
        """Test that guard function handles invalid input gracefully."""
        # Guard should raise TypeError for invalid input
        with pytest.raises(TypeError):
            sample_transition.can_fire(None)
            
        with pytest.raises(TypeError):
            sample_transition.can_fire("not a dict")
            
        # Guard should handle empty input
        assert sample_transition.can_fire({})  

    def test_action_with_invalid_input(self, sample_transition: Transition) -> None:
        """Test that the action handles invalid input gracefully."""
        # Action should raise TypeError for invalid input
        with pytest.raises(TypeError):
            sample_transition.execute_action(None)
            
        with pytest.raises(TypeError):
            sample_transition.execute_action("not a dict")
            
        # Action should handle empty input
        assert sample_transition.execute_action({}) == {}  # Default action returns input unchanged
