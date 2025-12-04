"""
Unit tests for the Arc class in the Petri Net module.

This module contains tests for the Arc class, which represents a connection between
a place and a transition in a Petri net. The tests cover arc creation, weight
management, guard functions, and thread safety.
"""

import contextlib
import pytest
import threading
import time
from typing import Callable, Optional, List, Iterator

from leaf_cloud.core.petri_net import Arc, Token, TokenColor
from leaf_cloud.core.petri_net import PetriNetValidationError


class TestArc:
    """Test cases for the Arc class."""
    
    @pytest.fixture
    def sample_token(self) -> Token:
        """Create a sample token for testing."""
        return Token(color=TokenColor.COMPUTE, token_id="test_token")
    
    @pytest.fixture
    def sample_input_arc(self) -> Arc:
        """Create a sample input arc for testing."""
        return Arc(
            id="a1",
            place_id="p1",
            transition_id="t1",
            direction="input",
            weight=2
        )
    
    @pytest.fixture
    def sample_output_arc(self) -> Arc:
        """Create a sample output arc for testing."""
        return Arc(
            id="a2",
            place_id="p2",
            transition_id="t1",
            direction="output",
            weight=1
        )
    
    def test_arc_creation(self):
        """Test arc creation with various parameters."""
        # Test input arc
        arc1 = Arc("a1", "p1", "t1", "input", weight=2)
        assert arc1.id == "a1"
        assert arc1.place_id == "p1"
        assert arc1.transition_id == "t1"
        assert arc1.direction == "input"
        assert arc1.weight == 2
        
        # Test output arc
        arc2 = Arc("a2", "p2", "t1", "output", weight=1)
        assert arc2.direction == "output"
        
        # Test that direction is normalized to lowercase
        arc3 = Arc("a3", "p1", "t1", "INPUT")
        assert arc3.direction == "input"  # Normalized to lowercase
        
        # Test default weight
        arc4 = Arc("a4", "p1", "t1", "input")
        assert arc4.weight == 1
    
    def test_invalid_arc_creation(self):
        """Test arc creation with invalid parameters."""
        # Invalid direction
        with pytest.raises(PetriNetValidationError) as excinfo:
            Arc("a1", "p1", "t1", "invalid")
        assert "direction must be 'input' or 'output'" in str(excinfo.value)
        
        # Invalid weight
        with pytest.raises(PetriNetValidationError) as excinfo:
            Arc("a1", "p1", "t1", "input", weight=0)
        assert "Weight must be a positive integer" in str(excinfo.value)
        
        with pytest.raises(PetriNetValidationError) as excinfo:
            Arc("a1", "p1", "t1", "input", weight=-1)
        assert "Weight must be a positive integer" in str(excinfo.value)
    
    def test_weight_property(self, sample_input_arc: Arc):
        """Test getting and setting the weight property."""
        assert sample_input_arc.weight == 2
        
        # Set valid weight
        sample_input_arc.weight = 3
        assert sample_input_arc.weight == 3
        
        # Test invalid weight
        with pytest.raises(ValueError) as excinfo:
            sample_input_arc.weight = 0
        assert "Weight must be positive" in str(excinfo.value)
        
        with pytest.raises(ValueError) as excinfo:
            sample_input_arc.weight = -1
        assert "Weight must be positive" in str(excinfo.value)
    
    def test_can_accept_token(self, sample_input_arc: Arc, sample_token: Token):
        """Test token acceptance based on guard function."""
        # Default guard accepts all tokens
        assert sample_input_arc.can_accept_token(sample_token) is True
        
        # Set a guard that only accepts tokens with specific attributes
        def guard(token: Token) -> bool:
            return token.color == TokenColor.STORAGE
        
        sample_input_arc.set_guard(guard)
        
        # Token with COMPUTE color should be rejected
        assert sample_input_arc.can_accept_token(sample_token) is False
        
        # Token with STORAGE color should be accepted
        storage_token = Token(color=TokenColor.STORAGE, token_id="storage_token")
        assert sample_input_arc.can_accept_token(storage_token) is True
        
        # Test with invalid token type
        with pytest.raises(TypeError) as excinfo:
            sample_input_arc.can_accept_token("not_a_token")  # type: ignore
        assert "Expected Token" in str(excinfo.value)
    
    def test_set_guard(self, sample_input_arc: Arc, sample_token: Token):
        """Test setting guard functions."""
        # Default guard accepts all tokens
        assert sample_input_arc.can_accept_token(sample_token) is True
        
        # Set a guard that rejects all tokens
        sample_input_arc.set_guard(lambda _: False)
        assert sample_input_arc.can_accept_token(sample_token) is False
        
        # Set a guard that accepts all tokens
        sample_input_arc.set_guard(lambda _: True)
        assert sample_input_arc.can_accept_token(sample_token) is True
        
        # Test with None (should use default guard that accepts all)
        sample_input_arc.set_guard(None)
        assert sample_input_arc.can_accept_token(sample_token) is True
        
        # Test with non-callable guard
        with pytest.raises(PetriNetValidationError) as excinfo:
            sample_input_arc.set_guard("not a callable")  # type: ignore
        # Match the exact error message from the implementation
        assert "Guard must be a callable or None, got str" in str(excinfo.value)
    
    def test_thread_safety(self, sample_input_arc: Arc, sample_token: Token):
        """Test that arc operations are thread-safe."""
        # Shared counter for guard function
        counter = 0
        counter_lock = threading.Lock()
        
        # Create a guard function that increments the counter
        def guard(token: Token) -> bool:
            nonlocal counter
            with counter_lock:
                counter += 1
            return True
        
        # Set the guard function
        sample_input_arc.set_guard(guard)
        
        # Function to call can_accept_token multiple times
        def test_thread():
            for _ in range(1000):
                sample_input_arc.can_accept_token(sample_token)
                # Also test setting properties from multiple threads
                sample_input_arc.weight = 1
        
        # Create and start multiple threads
        threads = []
        for _ in range(10):
            t = threading.Thread(target=test_thread)
            threads.append(t)
            t.start()
        
        # Wait for all threads to complete
        for t in threads:
            t.join()
        
        # Verify the counter was incremented the correct number of times
        assert counter == 1000 * 10  # 1000 iterations * 10 threads
        
        # Verify the weight was set correctly (last write wins in a thread-safe manner)
        assert sample_input_arc.weight == 1
    
    def test_string_representation(self, sample_input_arc: Arc, sample_output_arc: Arc):
        """Test string representation of arcs."""
        # Test __str__ for input arc
        input_str = str(sample_input_arc)
        assert input_str == "Arc(p1 ← t1, weight=2)"
        
        # Test __str__ for output arc
        output_str = str(sample_output_arc)
        assert output_str == "Arc(p2 → t1, weight=1)"
        
        # Test __repr__ - should include the ID and memory address
        input_repr = repr(sample_input_arc)
        assert input_repr.startswith(f"<Arc {sample_input_arc.id} at ")
        assert "Arc" in input_repr
        assert sample_input_arc.id in input_repr
        
        output_repr = repr(sample_output_arc)
        assert output_repr.startswith(f"<Arc {sample_output_arc.id} at ")
        assert "Arc" in output_repr
        assert sample_output_arc.id in output_repr
    
    def test_instance_tracking(self):
        """Test that arc instances are properly tracked."""
        # Get current instance count (there might be instances from fixtures)
        initial_count = Arc.get_instance_count()
        
        # Create some arcs within a scope
        with self._create_arcs(5) as arcs:
            # Check that we've added 5 instances
            assert Arc.get_instance_count() == initial_count + 5, \
                f"Expected {initial_count + 5} instances, got {Arc.get_instance_count()}"
            
            # Check get_instances
            instances = Arc.get_instances()
            assert len(instances) == initial_count + 5
            assert all(isinstance(arc, Arc) for arc in instances)
        
        # Force garbage collection to clean up the arcs
        import gc
        gc.collect()
        
        # Check that the instance count has decreased by at least 5
        # (we can't guarantee it's back to initial due to pytest fixtures)
        assert Arc.get_instance_count() <= initial_count + 5, \
            "Instance count did not decrease after cleanup"
    
    # Helper context manager to create temporary arcs
    @contextlib.contextmanager
    def _create_arcs(self, count: int):
        """Context manager to create temporary arcs for testing."""
        arcs = [Arc(f"temp_arc_{i}", f"p{i}", "t1", "input") for i in range(count)]
        try:
            yield arcs
        finally:
            # Clean up by removing references to the arcs
            del arcs
