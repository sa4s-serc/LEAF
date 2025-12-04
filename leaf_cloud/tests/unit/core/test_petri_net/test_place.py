"""
Unit tests for the Place class in the Petri net implementation.

These tests verify the functionality of the Place class, including:
- Token management (add/remove)
- Capacity constraints
- Thread safety
- State validation
"""
import threading
import time
import pytest
import uuid
from unittest.mock import MagicMock, patch
from leaf_cloud.core.petri_net import Place, Token, TokenColor, PetriNetResourceError, PetriNetValidationError

class TestPlace:
    """Test cases for the Place class."""

    def test_place_initialization(self):
        """Test basic place initialization."""
        # Test with just ID
        place1 = Place("p1")
        assert place1.id == "p1"
        assert place1.name == "p1"  # Name should default to ID
        assert place1.capacity is None
        assert place1.is_empty  # Changed from is_empty() to is_empty
        assert not place1.is_full  # Changed from is_full() to is_full
        
        # Test with all parameters
        place2 = Place("p2", "Test Place", 10)
        assert place2.id == "p2"
        assert place2.name == "Test Place"
        assert place2.capacity == 10
        
        # Test invalid capacity
        with pytest.raises(PetriNetValidationError):
            Place("p3", capacity=0)  # Capacity must be positive or None

    def test_add_token(self):
        """Test adding tokens to a place."""
        place = Place("p1", capacity=2)
        token1 = Token(color=TokenColor.REQUEST, attributes={"id": "t1"}, creation_time=0.0)
        token2 = Token(color=TokenColor.REQUEST, attributes={"id": "t2"}, creation_time=0.0)
        
        # Add first token
        assert place.add_token(token1) is True
        assert token1 in place.tokens
        assert place.token_count == 1
        
        # Add second token
        assert place.add_token(token2) is True
        assert token2 in place.tokens
        assert place.token_count == 2
        
        # Try to add beyond capacity
        token3 = Token(color=TokenColor.REQUEST, attributes={"id": "t3"}, creation_time=0.0)
        with pytest.raises(PetriNetResourceError):
            place.add_token(token3)  # Should raise when at capacity
        assert token3 not in place.tokens
        assert place.token_count == 2  # Should still be 2   
        # Test with unlimited capacity
        unlimited_place = Place("unlimited")
        for i in range(100):
            token = Token(
                token_id=f"token_{i}",
                color=TokenColor.REQUEST,
                attributes={"id": f"t{i}"},
                creation_time=0.0
            )
            assert unlimited_place.add_token(token) is True
        assert unlimited_place.token_count == 100

    def test_remove_token(self):
        """Test removing tokens from a place."""
        place = Place("p1")
        token1 = Token(color=TokenColor.REQUEST, attributes={"id": "t1"}, creation_time=0.0)
        token2 = Token(color=TokenColor.REQUEST, attributes={"id": "t2"}, creation_time=0.0)
        
        # Add tokens
        place.add_token(token1)
        place.add_token(token2)
        
        # Remove first token
        assert place.remove_token(token1) is True
        assert token1 not in place.tokens
        assert place.token_count == 1
        
        # Try to remove non-existent token
        assert place.remove_token(token1) is False  # Already removed
        assert place.token_count == 1  # Should still be 1   
        # Remove second token
        assert place.remove_token(token2) is True
        assert token2 not in place.tokens
        assert place.is_empty

    def test_token_filtering(self):
        """Test filtering tokens by color."""
        place = Place("filter_place")
        tokens = [
            Token(
                token_id=f"t{i}",
                color=TokenColor.REQUEST if i % 2 == 0 else TokenColor.COMPUTE,
                attributes={},
                creation_time=0.0
            )
            for i in range(10)
        ]
        
        # Add all tokens
        for token in tokens:
            place.add_token(token)
        
        # Filter by color
        request_tokens = place.get_tokens(TokenColor.REQUEST)
        assert len(request_tokens) == 5
        assert all(t.color == TokenColor.REQUEST for t in request_tokens)
        
        compute_tokens = place.get_tokens(TokenColor.COMPUTE)
        assert len(compute_tokens) == 5
        assert all(t.color == TokenColor.COMPUTE for t in compute_tokens)
        
        # Count tokens by color
        assert place.count_tokens(TokenColor.REQUEST) == 5
        assert place.count_tokens(TokenColor.COMPUTE) == 5
        assert place.count_tokens(TokenColor.STORAGE) == 0  # A color with no tokens
        assert place.count_tokens() == 10  # All tokens

    def test_clear_tokens(self):
        """Test clearing all tokens from a place."""
        place = Place("p1")
        tokens = [Token(color=TokenColor.REQUEST, attributes={"id": f"t{i}"}, creation_time=0.0) 
                 for i in range(5)]
        
        for token in tokens:
            place.add_token(token)
            
        assert place.token_count == 5
        place.clear_tokens()
        assert place.token_count == 0
        assert len(place.tokens) == 0

    def test_thread_safety(self):
        """Test that place operations are thread-safe."""
        place = Place("p1", capacity=100)
        tokens = [Token(color=TokenColor.REQUEST, attributes={"id": f"t{i}"}, creation_time=0.0) 
                 for i in range(100)]
        
        def add_tokens(start, end):
            for i in range(start, end):
                try:
                    place.add_token(tokens[i])
                except PetriNetResourceError:
                    pass  # Expected if we hit capacity in concurrent adds
        
        # Create multiple threads to add tokens
        threads = []
        for i in range(0, 100, 25):
            t = threading.Thread(target=add_tokens, args=(i, i+25))
            threads.append(t)
            t.start()
            
        # Wait for all threads to complete
        for t in threads:
            t.join()
            
        # Verify all tokens were added
        assert place.token_count == 100
        assert all(token in place.tokens for token in tokens)
        def remove_tokens(start, end):
            for i in range(start, end):
                place.remove_token(tokens[i])
        
        # Create and start threads for removal
        threads = []
        for i in range(0, 100, 25):
            t = threading.Thread(target=remove_tokens, args=(i, i+25))
            threads.append(t)
            t.start()
            
        # Wait for all threads to complete
        for t in threads:
            t.join()
            
        # Verify all tokens were removed
        assert place.token_count == 0
        assert place.is_empty

    def test_place_equality(self):
        """Test place equality comparison."""
        place1 = Place("p1", "Test Place")
        place2 = Place("p1", "Different Name")  # Same ID, different name
        place3 = Place("p2", "Test Place")     # Different ID, same name
        
        # Only ID matters for equality
        assert place1 == place2
        assert place1 != place3  # Different ID
        assert place1 != "not a place"  # Different type
        
        # Hashing
        places = {place1, place2}
        assert len(places) == 1  # Only one unique place (p1)
        assert place1 in places
        assert place2 in places  # Should match place1
        assert place2 in places

    def test_place_representation(self):
        """Test string representation of a place."""
        place = Place("test_place", "Test Place", 10)
        assert "test_place" in str(place)
        assert "Test Place" in str(place)
        assert "10" in str(place)
