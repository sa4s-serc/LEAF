"""
Unit tests for the Place class in the Petri Net module.

This module contains tests for the Place class, which represents a place in a Petri net
where tokens can reside. The tests cover token management, capacity constraints, and
thread safety.
"""

import pytest
import threading
import time
from typing import Set, Optional

from leaf_cloud.core.petri_net import (
    Place, 
    Token, 
    TokenColor, 
    PetriNetValidationError,
    PetriNetResourceError
)


class TestPlace:
    """Test cases for the Place class."""
    
    @pytest.fixture
    def sample_place(self) -> Place:
        """Create a sample place with default parameters for testing."""
        return Place(id="test_place", name="Test Place", capacity=10)
    
    @pytest.fixture
    def sample_tokens(self) -> Set[Token]:
        """Create a set of sample tokens for testing."""
        return {
            Token(color=TokenColor.COMPUTE, token_id=f"token_{i}")
            for i in range(5)
        }
    
    def test_place_creation(self):
        """Test place creation with various parameters."""
        # Test with all parameters
        place1 = Place(id="p1", name="Place 1", capacity=5)
        assert place1.id == "p1"
        assert place1.name == "Place 1"
        assert place1.capacity == 5
        assert place1.token_count == 0
        
        # Test with default name (should use id)
        place2 = Place(id="p2")
        assert place2.name == "p2"
        
        # Test with unlimited capacity
        place3 = Place(id="p3", capacity=None)
        assert place3.capacity is None
        
        # Test invalid capacity
        with pytest.raises(PetriNetValidationError):
            Place(id="invalid", capacity=0)
        
        with pytest.raises(PetriNetValidationError):
            Place(id="invalid", capacity=-1)
    
    def test_add_token(self, sample_place: Place):
        """Test adding tokens to a place."""
        # Create a token with a unique ID
        token = Token(color=TokenColor.NETWORK, token_id="token1")
        
        # Add a single token
        assert sample_place.add_token(token)
        assert sample_place.token_count == 1
        assert token in sample_place.get_tokens()
        
        # Test adding the same token again (should be idempotent)
        assert sample_place.add_token(token)
        assert sample_place.token_count == 1  # Count should not change
        
        # Test capacity constraints
        place = Place(id="small_place", capacity=1)
        token2 = Token(color=TokenColor.STORAGE, token_id="token2")
        
        # First token should be added successfully
        assert place.add_token(token)
        
        # Second token should raise PetriNetResourceError due to capacity
        with pytest.raises(PetriNetResourceError, match="is at capacity"):
            place.add_token(token2)
            
        # Token count should still be 1
        assert place.token_count == 1
        
        # Test adding to unlimited capacity place with unique tokens
        unlimited = Place(id="unlimited")
        for i in range(100):
            assert unlimited.add_token(Token(color=TokenColor.COMPUTE, token_id=f"token_{i}"))
        assert unlimited.token_count == 100
    
    def test_remove_token(self, sample_place: Place, sample_tokens: Set[Token]):
        """Test removing tokens from a place."""
        # Add some tokens
        for token in sample_tokens:
            sample_place.add_token(token)
        
        # Remove a token
        token_to_remove = next(iter(sample_tokens))
        assert sample_place.remove_token(token_to_remove)
        assert sample_place.token_count == len(sample_tokens) - 1
        assert token_to_remove not in sample_place.get_tokens()
        
        # Try to remove a token that's not in the place
        new_token = Token(color=TokenColor.COMPUTE)
        assert not sample_place.remove_token(new_token)
    
    def test_get_tokens(self, sample_place: Place):
        """Test retrieving tokens from a place with filtering."""
        # Create and add tokens with different colors
        tokens = [
            Token(color=TokenColor.COMPUTE if i % 2 == 0 else TokenColor.STORAGE, 
                  token_id=f"token_{i}")
            for i in range(5)  # Create 5 tokens
        ]
        
        for token in tokens:
            sample_place.add_token(token)
        
        # Get all tokens
        all_tokens = sample_place.get_tokens()
        assert len(all_tokens) == 5
        
        # Filter by color
        compute_tokens = sample_place.get_tokens(TokenColor.COMPUTE)
        assert len(compute_tokens) == 3  # 3 tokens with COMPUTE color (0, 2, 4)
        assert all(t.color == TokenColor.COMPUTE for t in compute_tokens)
        
        storage_tokens = sample_place.get_tokens(TokenColor.STORAGE)
        assert len(storage_tokens) == 2  # 2 tokens with STORAGE color (1, 3)
        assert all(t.color == TokenColor.STORAGE for t in storage_tokens)
        
        # Test with non-existent color
        assert len(sample_place.get_tokens(TokenColor.NETWORK)) == 0
    
    def test_capacity_property(self):
        """Test the capacity property and its validation."""
        # Test valid capacity through constructor
        place1 = Place(id="test1", capacity=10)
        assert place1.capacity == 10
        
        # Test unlimited capacity through constructor
        place2 = Place(id="test2", capacity=None)
        assert place2.capacity is None
        
        # Test default capacity (should be None)
        place3 = Place(id="test3")
        assert place3.capacity is None
        
        # Test invalid capacity values in constructor
        with pytest.raises(PetriNetValidationError):
            Place(id="invalid1", capacity=0)
        
        with pytest.raises(PetriNetValidationError):
            Place(id="invalid2", capacity=-5)
    
    def test_token_count_property(self, sample_place: Place):
        """Test the token_count property."""
        assert sample_place.token_count == 0
        
        # Add some tokens with unique IDs
        tokens = [
            Token(color=TokenColor.COMPUTE, token_id=f"token_{i}")
            for i in range(3)
        ]
        for token in tokens:
            sample_place.add_token(token)
        
        # Verify the count matches the number of unique tokens added
        assert sample_place.token_count == 3
        
        # Remove a token and verify the count is updated
        sample_place.remove_token(tokens[0])
        assert sample_place.token_count == 2
    
    def test_thread_safety(self):
        """Test that place operations are thread-safe."""
        place = Place(id="concurrent", capacity=1000)
        tokens = [Token(color=TokenColor.COMPUTE, token_id=str(i)) for i in range(1000)]
        
        # Function to add tokens
        def add_tokens(start: int, end: int):
            for i in range(start, end):
                place.add_token(tokens[i])
        
        # Create multiple threads to add tokens concurrently
        threads = []
        batch_size = 100
        for i in range(0, len(tokens), batch_size):
            t = threading.Thread(
                target=add_tokens,
                args=(i, min(i + batch_size, len(tokens)))
            )
            threads.append(t)
            t.start()
        
        # Wait for all threads to complete
        for t in threads:
            t.join()
        
        # Verify all tokens were added exactly once
        assert place.token_count == len(tokens)
        token_set = place.get_tokens()
        assert len(token_set) == len(tokens)
        assert all(token in token_set for token in tokens)
    
    def test_clear_tokens(self, sample_place: Place, sample_tokens: Set[Token]):
        """Test clearing all tokens from a place."""
        # Add some tokens
        for token in sample_tokens:
            sample_place.add_token(token)
        
        # Verify the expected number of tokens were added
        assert sample_place.token_count == len(sample_place.get_tokens())
        
        # Clear tokens
        sample_place.clear_tokens()
        
        # Verify place is empty
        assert sample_place.token_count == 0
        assert len(sample_place.get_tokens()) == 0
    
    def test_contains_token(self, sample_place: Place):
        """Test checking if a place contains a specific token."""
        token1 = Token(color=TokenColor.COMPUTE, token_id="t1")
        token2 = Token(color=TokenColor.STORAGE, token_id="t2")
        
        sample_place.add_token(token1)
        
        assert token1 in sample_place
        assert token2 not in sample_place
    
    def test_equality(self):
        """Test place equality based on ID."""
        place1 = Place(id="p1", name="Place 1")
        place2 = Place(id="p1", name="Different name")
        place3 = Place(id="p2")
        
        assert place1 == place2  # Same ID
        assert place1 != place3  # Different ID
        assert place1 != "not a place"  # Different type
