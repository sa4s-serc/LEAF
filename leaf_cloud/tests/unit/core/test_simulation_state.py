"""
Unit tests for the SimulationState enum in the core module.

These tests verify the functionality of the SimulationState enum, including:
- State values and properties
- String representation
- Enum operations
"""
import pytest
from leaf_cloud.core.simulation_state import SimulationState, InvalidStateTransitionError

class TestSimulationState:
    """Test cases for the SimulationState enum."""

    def test_enum_values(self):
        """Test that all expected enum values are defined."""
        # Check that all expected states are present
        # Note: The actual implementation includes 11 members (8 states + 3 internal)
        assert len(SimulationState) == 11
        
        # Check the actual state values
        assert SimulationState.INITIALIZING.value == "initializing"
        assert SimulationState.PARSING.value == "parsing"
        assert SimulationState.BUILDING.value == "building"
        assert SimulationState.READY.value == "ready"
        assert SimulationState.RUNNING.value == "running"
        assert SimulationState.PAUSED.value == "paused"
        assert SimulationState.COMPLETED.value == "completed"
        assert SimulationState.FAILED.value == "failed"
        
        # Check that values are unique
        values = [state.value for state in SimulationState 
                 if not state.name.startswith('_')]  # Skip internal members
        assert len(values) == len(set(values)), "Duplicate enum values found"

    def test_string_representation(self):
        """Test the string representation of SimulationState."""
        # Test __str__
        assert str(SimulationState.INITIALIZING) == "SimulationState.INITIALIZING"
        assert str(SimulationState.RUNNING) == "SimulationState.RUNNING"
        assert str(SimulationState.COMPLETED) == "SimulationState.COMPLETED"
        
        # Test __repr__
        assert repr(SimulationState.INITIALIZING) == "<SimulationState.INITIALIZING: 'initializing'>"
        assert repr(SimulationState.RUNNING) == "<SimulationState.RUNNING: 'running'>"
        assert repr(SimulationState.COMPLETED) == "<SimulationState.COMPLETED: 'completed'>"

    def test_enum_operations(self):
        """Test enum operations like iteration, comparison, and hashing."""
        # Get only the actual state values (excluding internal members)
        states = [s for s in SimulationState if not s.name.startswith('_')]
        
        # Should have 8 actual states
        assert len(states) == 8
        
        # Test comparison
        assert SimulationState.INITIALIZING == SimulationState.INITIALIZING
        assert SimulationState.INITIALIZING != SimulationState.RUNNING
        
        # Test hashing and set operations
        state_set = {
            SimulationState.INITIALIZING, 
            SimulationState.RUNNING, 
            SimulationState.COMPLETED
        }
        assert len(state_set) == 3
        assert SimulationState.INITIALIZING in state_set
        assert SimulationState.PAUSED not in state_set
        
        # Test in operator - in Python's Enum, both the enum member and its value can be checked with 'in'
        assert SimulationState.INITIALIZING in SimulationState
        # Note: In Python's Enum, both the enum member and its value can be checked with 'in'
        # So "initializing" in SimulationState will be True because it matches the value of INITIALIZING
        assert "initializing" in SimulationState  # This is True in Python's Enum implementation
        
        # Test getattr
        assert getattr(SimulationState, "INITIALIZING") == SimulationState.INITIALIZING
        assert getattr(SimulationState, "RUNNING") == SimulationState.RUNNING
        
        with pytest.raises(AttributeError):
            getattr(SimulationState, "NON_EXISTENT")
