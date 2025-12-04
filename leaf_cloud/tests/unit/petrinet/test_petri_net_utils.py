"""Test utilities for Petri Net module tests.

This module provides common test utilities and fixtures for testing the Petri Net module.
"""
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

from leaf_cloud.core.petri_net import (
    Arc,
    Event,
    PetriNet,
    Place,
    Token,
    TokenColor,
    Transition,
)

# Re-export assert_dict_contains from conftest
from leaf_cloud.tests.conftest import assert_dict_contains

# Type aliases for test readability
TokenDict = Dict[str, Any]

# Common test values
TEST_TIMESTAMP = 1625587200.0  # 2021-07-07 00:00:00

# Sample token attributes for testing
SAMPLE_TOKEN_ATTRS = {
    "color": TokenColor.COMPUTE,
    "attributes": {"cpu": 4, "memory": 8192, "disk": 100},
    "creation_time": TEST_TIMESTAMP,
    "history": ["p1", "p2"],
}

# Sample place configuration for testing
SAMPLE_PLACE_CONFIG = {
    "id": "test_place",
    "name": "Test Place",
    "capacity": 100,
}

# Sample transition configuration for testing
SAMPLE_TRANSITION_CONFIG = {
    "id": "test_transition",
    "name": "Test Transition",
    "delay": 1.0,
    "priority": 1,
}

# Sample arc configuration for testing
SAMPLE_ARC_CONFIG = {
    "id": "test_arc",
    "place_id": "test_place",
    "transition_id": "test_transition",
    "direction": "input",
    "weight": 1,
}


# Fixtures
@pytest.fixture
def sample_token_attrs() -> Dict[str, Any]:
    """Return a dictionary of sample token attributes for testing."""
    return {
        "color": TokenColor.COMPUTE,
        "attributes": {"cpu": 4, "memory": 8192, "disk": 100},
        "creation_time": TEST_TIMESTAMP,
        "history": ["p1", "p2"],
        "token_id": "test_token_id"
    }


@pytest.fixture
def timestamp() -> float:
    """Return a fixed timestamp for testing."""
    return TEST_TIMESTAMP


@pytest.fixture
def create_token(sample_token_attrs: Dict[str, Any]) -> callable:
    """Factory fixture to create a Token with custom attributes.
    
    Args:
        sample_token_attrs: Default attributes to use
        
    Returns:
        A function that creates a Token with optional overrides
    """
    def _create_token(**overrides: Any) -> Token:
        attrs = {**sample_token_attrs, **overrides}
        return Token(**attrs)
    return _create_token


@pytest.fixture
def token(create_token: callable) -> Token:
    """Create a sample token for testing."""
    return create_token()


@pytest.fixture
def create_place() -> callable:
    """Factory fixture to create a Place with custom attributes."""
    def _create_place(**overrides: Any) -> Place:
        attrs = {**SAMPLE_PLACE_CONFIG, **overrides}
        return Place(**attrs)
    return _create_place


@pytest.fixture
def place(create_place: callable) -> Place:
    """Create a sample place for testing."""
    return create_place()


@pytest.fixture
def create_transition() -> callable:
    """Factory fixture to create a Transition with custom attributes."""
    def _create_transition(**overrides: Any) -> Transition:
        attrs = {**SAMPLE_TRANSITION_CONFIG, **overrides}
        return Transition(**attrs)
    return _create_transition


@pytest.fixture
def transition(create_transition: callable) -> Transition:
    """Create a sample transition for testing."""
    return create_transition()


@pytest.fixture
def create_arc() -> callable:
    """Factory fixture to create an Arc with custom attributes."""
    def _create_arc(**overrides: Any) -> Arc:
        attrs = {**SAMPLE_ARC_CONFIG, **overrides}
        return Arc(**attrs)
    return _create_arc


@pytest.fixture
def arc(create_arc: callable) -> Arc:
    """Create a sample arc for testing."""
    return create_arc()


@pytest.fixture
def petri_net() -> PetriNet:
    """Create a sample Petri net for testing."""
    return PetriNet("test_net")


class PetriNetTestMixin:
    """Mixin class with common test methods for Petri net related tests."""
    
    def assert_token_equals(self, token: Token, expected: Dict[str, Any]) -> None:
        """Assert that a token has the expected attributes.
        
        Args:
            token: The token to check
            expected: Dictionary of expected attribute values
        """
        for attr, value in expected.items():
            if attr == "history":
                assert list(token.history) == value, f"Token history mismatch"
            elif hasattr(token, attr):
                assert getattr(token, attr) == value, f"Token {attr} mismatch"
            elif attr == "attributes":
                for k, v in value.items():
                    assert k in token.attributes, f"Attribute {k} not found"
                    assert token.attributes[k] == v, f"Attribute {k} value mismatch"

    def assert_place_has_tokens(
        self, 
        place: Place, 
        expected_count: int,
        color: Optional[TokenColor] = None,
    ) -> None:
        """Assert that a place has the expected number of tokens.
        
        Args:
            place: The place to check
            expected_count: Expected number of tokens
            color: Optional token color to filter by
        """
        actual_count = place.token_count(color=color)
        assert actual_count == expected_count, \
            f"Expected {expected_count} tokens, got {actual_count}"

    def assert_transition_enabled(
        self, 
        transition: Transition, 
        expected: bool = True
    ) -> None:
        """Assert that a transition is enabled or disabled.
        
        Args:
            transition: The transition to check
            expected: Whether the transition should be enabled
        """
        assert transition.is_enabled == expected, \
            f"Transition enabled state should be {expected}"
