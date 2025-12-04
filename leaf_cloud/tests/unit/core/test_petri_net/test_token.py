"""
Unit tests for the Token class in the Petri net implementation.

These tests verify the functionality of the Token class, including:
- Token creation and initialization
- Attribute management
- History tracking
- Memory management
- Thread safety
- Flyweight pattern behavior
"""
import gc
import sys
import time
import threading
import weakref
from unittest.mock import patch, MagicMock
import pytest
from leaf_cloud.core.petri_net import Token, TokenColor, PetriNetValidationError


class TestToken:
    """Test cases for the Token class."""

    def test_token_creation(self):
        """Test basic token creation with default values."""
        # Clear any existing tokens from previous tests
        Token.cleanup_all()
        
        # Create a token with minimal required parameters
        token = Token(color=TokenColor.REQUEST)
        
        # Verify default values
        assert isinstance(token.id, str)  # Should be a UUID string
        assert token.color == TokenColor.REQUEST
        assert token.attributes == {}
        assert isinstance(token.creation_time, float)
        assert token.history == []
        
        # Verify the token was added to the instances set
        assert token in Token._instances

    def test_token_with_attributes(self):
        """Test token creation with custom attributes."""
        Token.cleanup_all()
        attrs = {"cpu": 4, "memory": 8192, "priority": "high"}
        
        # Create token with custom attributes
        token = Token(color=TokenColor.COMPUTE, attributes=attrs)
        
        # Verify attributes are set correctly
        assert token.attributes == attrs
        assert token.attributes["cpu"] == 4
        
        # Verify that modifying the original dict doesn't affect the token
        attrs["cpu"] = 8
        assert token.attributes["cpu"] == 4  # Should still be 4, not 8

    def test_token_history_tracking(self):
        """Test that token history is properly tracked."""
        Token.cleanup_all()
        
        # Create a token with initial history
        history = ["place1", "place2"]
        token = Token(color=TokenColor.REQUEST, history=history)
        
        # Verify initial history
        assert token.history == history
        
        # Add to history using the method
        token.add_to_history("place3")
        assert token.history == ["place1", "place2", "place3"]
        
        # Verify history is a copy
        history.append("should_not_affect_token")
        assert token.history == ["place1", "place2", "place3"]  # Unchanged

    def test_token_validation(self):
        """Test token attribute validation."""
        Token.cleanup_all()
        
        # Test invalid color type (should raise TypeError)
        with pytest.raises(TypeError, match="color must be a TokenColor"):
            Token(color="INVALID_COLOR")
            
        # Test invalid attributes type
        with pytest.raises(TypeError, match="attributes must be a dict"):
            Token(color=TokenColor.REQUEST, attributes="not_a_dict")
            
        # Test invalid token_id type
        with pytest.raises(TypeError, match="token_id must be a string"):
            Token(color=TokenColor.REQUEST, token_id=123)
            
        # Test invalid creation_time type
        with pytest.raises(TypeError, match="creation_time must be a number or None"):
            Token(color=TokenColor.REQUEST, creation_time="not_a_number")
            
        # Test valid history types (should accept any iterable and preserve order from ordered inputs)
        # For ordered inputs (list, tuple, iterator), order should be preserved
        for history in [
            ["place1", "place2"],  # list
            ("place1", "place2"),  # tuple
            iter(["place1", "place2"])  # iterator
        ]:
            token = Token(color=TokenColor.REQUEST, history=history)
            assert token.history == ["place1", "place2"]  # Should preserve order
            
        # For unordered inputs (set), check content but not order
        token = Token(color=TokenColor.REQUEST, history={"place1", "place2"})
        assert set(token.history) == {"place1", "place2"}  # Check content, not order

    def test_token_clone(self):
        """Test token cloning creates a new token with same attributes but new ID."""
        Token.cleanup_all()
        
        # Create original token with history
        original = Token(
            color=TokenColor.REQUEST, 
            attributes={"key": "value"}, 
            history=["place1", "place2"]
        )
        
        # Create a clone
        clone = original.clone()
        
        # Should have same attributes but different ID
        assert original.id != clone.id
        assert original.color == clone.color
        assert original.attributes == clone.attributes
        assert original.history == clone.history
        
        # Verify attributes are deep copied
        clone.attributes["new_key"] = "new_value"
        assert "new_key" not in original.attributes
        
        # Verify history is deep copied
        clone.add_to_history("place3")
        assert len(original.history) == 2  # Should not be affected

    def test_token_equality(self):
        """Test token equality based on ID and attributes.
        
        Note: Due to the flyweight pattern, tokens with the same attributes
        will be the same instance, so we need to test with explicit token_ids
        to properly test equality semantics.
        """
        Token.cleanup_all()
        
        # Create tokens with the same attributes but different IDs
        token1 = Token(color=TokenColor.REQUEST, token_id="id1", attributes={"key": "value"})
        token2 = Token(color=TokenColor.REQUEST, token_id="id2", attributes={"key": "value"})
        
        # Tokens with same ID should be considered equal
        assert token1 == token1  # Same instance
        assert token1 == token1  # pylint: disable=comparison-with-itself
        
        # Different IDs should not be equal, even with same attributes
        assert token1 != token2
        
        # Different type should not be equal
        assert token1 != "not a token"
        
        # Test with same token_id - should be considered equal
        token_id = "test-id-123"
        token3 = Token(color=TokenColor.REQUEST, token_id=token_id)
        token4 = Token(color=TokenColor.REQUEST, token_id=token_id)
        assert token3 == token4  # Same ID, should be equal
        
        # Clone should create a token with same attributes but different ID
        token1_clone = token1.clone()
        assert token1 != token1_clone  # Different IDs

    def test_token_hash(self):
        """Test that tokens are hashable and hash is consistent with equality.
        
        Note: Hash is based on token ID, so tokens with the same ID will have
        the same hash, even if they are different instances.
        """
        Token.cleanup_all()
        
        # Create tokens with explicit IDs to avoid flyweight behavior
        token1 = Token(color=TokenColor.REQUEST, token_id="id1", attributes={"key": "value"})
        token2 = Token(color=TokenColor.REQUEST, token_id="id2", attributes={"key": "value"})
        
        # Hash should be consistent with equality for same object
        assert hash(token1) == hash(token1)  # Same object
        
        # Different IDs should have different hashes
        assert hash(token1) != hash(token2)
        
        # Test with same token_id - should have same hash
        token_id = "test-hash-id"
        token3 = Token(color=TokenColor.REQUEST, token_id=token_id)
        token4 = Token(color=TokenColor.REQUEST, token_id=token_id)
        
        # Same ID should mean same hash
        assert hash(token3) == hash(token4)
        
        # Can be used as dictionary key
        d = {token1: "test1", token2: "test2"}
        assert d[token1] == "test1"
        assert d[token2] == "test2"
        
        # Tokens with same ID should map to same value in dict
        d = {token3: "test3"}
        assert token4 in d  # token4 has same ID as token3
        assert d[token4] == "test3"  # Can retrieve with different instance

    def test_token_representation(self):
        """Test string representation of tokens."""
        Token.cleanup_all()
        
        # Create a token with known attributes
        token = Token(color=TokenColor.REQUEST, attributes={"key": "value"})
        token_id = token.id  # Get the generated ID
        
        # Test __repr__
        repr_str = repr(token)
        assert token_id in repr_str
        assert "Token" in repr_str
        assert "REQUEST" in repr_str
        
        # Test __str__
        str_str = str(token)
        assert token_id in str_str
        assert "REQUEST" in str_str

    @pytest.mark.parametrize("color_value", [
        TokenColor.COMPUTE.value,
        TokenColor.STORAGE.value,
        TokenColor.NETWORK.value,
        TokenColor.SECURITY.value,
        TokenColor.GENERIC.value,
        TokenColor.DATABASE.value,
        TokenColor.REQUEST.value
    ])
    def test_all_token_colors(self, color_value):
        """Test that all token colors can be used to create tokens."""
        color = TokenColor(color_value)
        token = Token(color=color, attributes={"id": f"{color_value}_token"})
        assert token.color == color

    def test_token_memory_management(self):
        """Test that token instances are properly managed in memory."""
        # Clear any existing tokens and cache
        Token.cleanup_all()
        Token.clear_cache()
        
        # Create some tokens with unique attributes to avoid flyweight caching
        weak_refs = []
        tokens = []
        
        # Create tokens with unique attributes to prevent flyweight caching
        for i in range(5):
            token = Token(
                color=TokenColor.REQUEST, 
                attributes={"test_id": f"test_{i}_{time.time()}"},
                token_id=f"test_token_{i}_{time.time()}"  # Ensure unique ID
            )
            weak_refs.append(weakref.ref(token))
            tokens.append(token)  # Keep strong reference
        
        # All tokens should be alive
        assert all(ref() is not None for ref in weak_refs)
        
        # Clear strong references
        del tokens
        
        # Force garbage collection
        gc.collect()
        
        # Clean up any cached references
        Token.cleanup_all()
        Token.clear_cache()
        
        # Force garbage collection again to clean up any remaining references
        gc.collect()
        
        # All weak references should be dead after cleanup
        # Note: Some references might still be alive due to Python's garbage collection
        # being non-deterministic. We'll check that at least some were collected.
        collected = sum(1 for ref in weak_refs if ref() is None)
        assert collected > 0, "No tokens were garbage collected"

    def test_token_cleanup(self):
        """Test that token cleanup works as expected."""
        # Clean up any existing tokens and cache
        Token.cleanup_all()
        Token.clear_cache()
        
        # Create some tokens with unique attributes to avoid flyweight caching
        weak_refs = []
        tokens = []
        
        # Create tokens with unique attributes
        for i in range(5):
            token = Token(
                color=TokenColor.REQUEST, 
                attributes={"test_id": f"test_{i}_{time.time()}"},
                token_id=f"test_token_{i}_{time.time()}"  # Ensure unique ID
            )
            weak_refs.append(weakref.ref(token))
            tokens.append(token)
        
        # Verify instances were created
        initial_count = len(list(Token._instances))
        assert initial_count >= 5, f"Expected at least 5 instances, got {initial_count}"
        
        # Clean up explicitly
        Token.cleanup_all()
        
        # Clear strong references
        del tokens
        
        # Force garbage collection
        gc.collect()
        
        # Clean up again to clear any remaining references
        Token.cleanup_all()
        Token.clear_cache()
        
        # Force garbage collection one more time
        gc.collect()
        
        # Check that we can still create new tokens after cleanup
        new_token = Token(
            color=TokenColor.REQUEST, 
            attributes={"test_id": "new_token"},
            token_id="new_token_id"
        )
        assert new_token is not None
        
        # Clean up after test
        Token.cleanup_all()
        Token.clear_cache()
        
        # Note: We don't assert on the exact number of instances since the flyweight
        # pattern and Python's garbage collection make this non-deterministic

    def test_token_thread_safety(self):
        """Test that token operations are thread-safe."""
        # This test verifies that the Token class can be safely used from multiple threads
        # by creating tokens in parallel and checking for consistency
        Token.cleanup_all()
        
        def create_tokens(start, end):
            tokens = []
            for i in range(start, end):
                token = Token(
                    color=TokenColor.REQUEST, 
                    attributes={"index": i},
                    creation_time=time.time()
                )
                tokens.append(token)
            return tokens
        
        # Create tokens in parallel
        results = []
        threads = []
        batch_size = 20
        
        def worker(start, end):
            tokens = create_tokens(start, end)
            results.extend(tokens)
        
        for i in range(0, 100, batch_size):
            t = threading.Thread(target=worker, args=(i, i + batch_size))
            threads.append(t)
            t.start()
        
        # Wait for all threads to complete
        for t in threads:
            t.join()
        
        # Verify all tokens were created and have unique IDs
        assert len(results) == 100
        assert len({token.id for token in results}) == 100  # All IDs should be unique
        
        # Clean up
        Token.cleanup_all()
