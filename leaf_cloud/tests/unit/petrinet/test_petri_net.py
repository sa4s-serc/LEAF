"""
Unit tests for the Petri Net module in LEAF-Cloud.

This module contains tests for the Petri Net components:
- Token
- Place
- Arc
- Transition
- Event
- PetriNet
"""
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

from leaf_cloud.core.petri_net import (
    Arc,
    Event,
    PetriNet,
    PetriNetError,
    PetriNetResourceError,
    PetriNetSimulationError,
    PetriNetStateError,
    PetriNetValidationError,
    Place,
    Token,
    TokenColor,
    Transition,
)

from leaf_cloud.tests.unit.petrinet.test_petri_net_utils import (
    PetriNetTestMixin,
    SAMPLE_PLACE_CONFIG,
    SAMPLE_TRANSITION_CONFIG,
    SAMPLE_ARC_CONFIG,
    create_token,
    create_place,
    create_transition,
    create_arc,
    token,
    place,
    transition,
    arc,
    petri_net,
    timestamp,
    sample_token_attrs,
)


class TestToken(PetriNetTestMixin):
    """Test cases for the Token class."""
    
    def test_token_creation_defaults(self):
        """Test token creation with default values."""
        token = Token()
        assert token.color == TokenColor.GENERIC
        assert token.attributes == {}
        assert isinstance(token.creation_time, float)
        assert token.history == []
        assert isinstance(token.id, str)
        assert len(token.id) > 0
    
    def test_token_creation_custom_attrs(self, sample_token_attrs):
        """Test token creation with custom attributes."""
        token = Token(**sample_token_attrs)
        self.assert_token_equals(token, sample_token_attrs)
    
    def test_token_equality(self):
        """Test that tokens with the same ID are considered equal."""
        # Create tokens with explicit different IDs
        token1 = Token(color=TokenColor.COMPUTE, attributes={"value": 1}, token_id="id1")
        token2 = Token(color=TokenColor.COMPUTE, attributes={"value": 1}, token_id="id2")
        
        # Tokens with same ID should be equal
        assert token1 == token1
        
        # Tokens with different IDs should not be equal, even with same attributes
        assert token1 != token2, "Tokens with different IDs should not be equal"
        
        # Create another token with same ID as token1
        token1_copy = Token(color=TokenColor.STORAGE, attributes={"value": 2}, token_id="id1")
        
        # Tokens with same ID should be equal even if other attributes differ
        assert token1 == token1_copy, "Tokens with same ID should be equal even if other attributes differ"
    
    def test_token_clone(self):
        """Test that cloning a token creates a new token with same attributes but new ID."""
        token = Token(color=TokenColor.STORAGE, attributes={"size": 1024}, token_id="test-id")
        clone = token.clone()
        
        # Clone should be a different object
        assert clone is not token
        
        # Clone should have the same attributes (except ID which should be different)
        assert clone.color == token.color
        assert clone.attributes == token.attributes
        
        # Clone should have a different ID
        assert clone.id != token.id
        
        # Modifying the clone should not affect the original
        clone.update_attribute("size", 2048)
        assert clone.attributes["size"] == 2048
        assert token.attributes["size"] == 1024
        
        clone.update_attribute("cpu", 8)
        assert clone.attributes["cpu"] == 8
        assert "cpu" not in token.attributes
        
        # Modifying clone should not affect original
        clone.update_attribute("cpu", 8)
        assert "cpu" not in token.attributes
    
    def test_token_history(self, create_token):
        """Test token history tracking."""
        token = create_token(history=["p1"])
        token.add_to_history("p2")
        
        assert token.history == ["p1", "p2"]
        
        # History should be a copy, not a reference
        history = token.history
        history.append("p3")
        assert token.history == ["p1", "p2"]
    
    def test_token_attributes(self, create_token):
        """Test token attribute management."""
        token = create_token(attributes={"cpu": 4, "memory": 8192})
        
        # Test getting attributes
        assert token.attributes["cpu"] == 4
        assert token.attributes["memory"] == 8192
        
        # Test updating attributes
        token.update_attribute("cpu", 8)
        assert token.attributes["cpu"] == 8
        
        # Test adding new attribute
        token.update_attribute("disk", 100)
        assert token.attributes["disk"] == 100
        
        # Test that attributes dict is a copy
        attrs = token.attributes
        attrs["network"] = 1000
        assert "network" not in token.attributes
    
    def test_token_thread_safety(self, create_token):
        """Test thread safety of token operations."""
        import threading
        
        token = create_token(attributes={"counter": 0})
        num_threads = 10
        increments = 100
        
        def increment_counter():
            for _ in range(increments):
                with token._lock:  # pylint: disable=protected-access
                    counter = token.attributes.get("counter", 0)
                    token.update_attribute("counter", counter + 1)
        
        threads = [
            threading.Thread(target=increment_counter)
            for _ in range(num_threads)
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert token.attributes["counter"] == num_threads * increments
    
    def test_token_memory_management(self, create_token):
        """Test that token instances are properly managed in memory."""
        # Clear any existing instances and cache
        Token.cleanup_all()
        Token.clear_cache()
        
        # Force garbage collection to clean up any lingering references
        import gc
        import weakref
        weak_refs = []
        
        def create_and_track_tokens():
            # Create some tokens with unique IDs
            tokens = [
                Token(color=TokenColor.COMPUTE, attributes={"id": i}, token_id=f"token_{i}")
                for i in range(5)
            ]
            # Create weak references to track the tokens
            weak_refs.extend([weakref.ref(token) for token in tokens])
            return tokens
        
        # Clear any existing token instances before the test
        Token.cleanup_all()
        
        try:
            # Create tokens and keep weak references
            tokens = create_and_track_tokens()
            
            # Should have exactly 5 instances
            instance_count = Token.get_instance_count()
            assert instance_count == 5, \
                f"Expected 5 instances after creation, got {instance_count}"
            
            # Verify weak references are alive
            assert all(ref() is not None for ref in weak_refs), "Some token references are already dead"
            
            # Clear references and force cleanup
            del tokens
            
            # Force garbage collection
            gc.collect()
            
            # Manually clean up the cache and instances
            Token.cleanup_all()
            
            # After cleanup, we should have 0 instances left
            final_count = Token.get_instance_count()
            
            # Check weak references
            live_refs = [ref for ref in weak_refs if ref() is not None]
            
            # If there are still live references, print debug info
            if live_refs:
                print(f"\nWarning: {len(live_refs)} token references still alive after cleanup")
                for i, ref in enumerate(live_refs):
                    token = ref()
                    if token is not None:
                        print(f"  {i+1}. ID: {token.id}, Type: {type(token).__name__}")
            
            # Verify all instances were cleaned up
            assert final_count == 0, f"Expected 0 instances after cleanup, got {final_count}"
            
        finally:
            # Always ensure cleanup happens, even if the test fails
            Token.cleanup_all()
            # Re-enable garbage collection
            gc.set_debug(0)
    
    def test_token_validation(self):
        """Test that token validation works as expected."""
        # Test valid token creation
        token = Token(color=TokenColor.COMPUTE, attributes={"cpu": 4})
        assert token is not None
        
        # Test invalid color type
        with pytest.raises(TypeError, match="color must be a TokenColor"):
            Token(color="invalid-color")  # type: ignore
            
        # Test invalid attributes type
        with pytest.raises(TypeError, match="attributes must be a dict"):
            Token(color=TokenColor.COMPUTE, attributes="not-a-dict")  # type: ignore
            
        # Test invalid token_id type
        with pytest.raises(TypeError, match="token_id must be a string"):
            Token(color=TokenColor.COMPUTE, token_id=123)  # type: ignore
            
        # Test with various history inputs - should accept any iterable
        token_with_list_history = Token(history=["place1", "place2"])
        assert token_with_list_history.history == ["place1", "place2"]
        
        # Test with None history - should be converted to empty list
        token_with_none_history = Token(history=None)
        assert token_with_none_history.history == []
        
        # Test with empty history - should be converted to empty list
        token_with_empty_history = Token(history=[])
        assert token_with_empty_history.history == []
        
        # Test with custom iterable - should be converted to list
        token_with_set_history = Token(history={"place1", "place2"})
        assert isinstance(token_with_set_history.history, list)
        assert set(token_with_set_history.history) == {"place1", "place2"}
        
        # Test with list history - should be copied
        history_list = ["p1", "p2"]
        token_with_list = Token(history=history_list)
        assert token_with_list.history == history_list
        assert token_with_list.history is not history_list  # Should be a copy
        
        # Invalid creation_time type - should be a number or None
        with pytest.raises(TypeError):
            Token(creation_time="not_a_float")  # type: ignore
