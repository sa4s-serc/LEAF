"""
This module defines the SimulationState enum and related exceptions for tracking the state of LEAF-Cloud simulations.

The SimulationState enum represents the various states a simulation can be in and provides
methods to validate state transitions and query state properties.

Thread Safety:
    - The SimulationState enum is thread-safe for state transitions.
    - The transition_to() method uses a reentrant lock to ensure thread safety.
    - All public methods are designed to be thread-safe.

State Transitions:
    INITIALIZING → PARSING → BUILDING → READY → RUNNING → COMPLETED
        ↑                  ↓              ↓         ↓
        └------------------┴--------------┴---------┘
            (on error)    (on error)    (on error)

Example:
    >>> from simulation_state import SimulationState, SimulationStateError
    >>> current_state = SimulationState.INITIALIZING
    >>> try:
    ...     if current_state.can_transition_to(SimulationState.PARSING):
    ...         current_state = SimulationState.PARSING
    ... except SimulationStateError as e:
    ...     print(f"State transition error: {e}")
"""
from enum import Enum, auto
import threading
from typing import Set, Dict, Type, Optional, Any, ClassVar, TypeVar, cast


class SimulationStateError(Exception):
    """Base exception for all simulation state related errors."""
    pass


class InvalidStateTransitionError(SimulationStateError):
    """Raised when an invalid state transition is attempted.
    
    Attributes:
        from_state: The current state of the simulation
        to_state: The target state that was attempted
        reason: Optional explanation of why the transition is invalid
    """
    def __init__(self, from_state: 'SimulationState', to_state: 'SimulationState', 
                 reason: Optional[str] = None):
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason or "Invalid state transition"
        super().__init__(f"Cannot transition from {from_state.value} to {to_state.value}: {reason}")


class SimulationStateValidationError(SimulationStateError, ValueError):
    """Raised when there's an error validating simulation state."""
    pass


# Type variable for better type checking
_StateT = TypeVar('_StateT', bound='SimulationState')

class SimulationState(str, Enum):
    """
    Enumeration of possible simulation states with transition validation.

    This class manages the state machine for LEAF-Cloud simulations, ensuring that only
    valid state transitions are allowed. It provides methods to check transition validity
    and query state properties.

    Thread Safety:
        - All state transitions are protected by a reentrant lock.
        - The class is designed to be thread-safe for concurrent access.
        - The transition_to() method is atomic and thread-safe.

    States:
        INITIALIZING: Initial state when simulation is being set up
        PARSING: Parsing input configuration and models
        BUILDING: Building the simulation model
        READY: Simulation is ready to start
        RUNNING: Simulation is currently executing
        PAUSED: Simulation is paused (can be resumed)
        COMPLETED: Simulation finished successfully
        FAILED: Simulation failed with an error

    Example:
        >>> state = SimulationState.INITIALIZING
        >>> state.can_transition_to(SimulationState.PARSING)
        True
        >>> state.transition_to(SimulationState.PARSING)
        >>> state
        <SimulationState.PARSING: 'parsing'>
    """

    # State definitions with proper type hints
    INITIALIZING: 'SimulationState' = "initializing"
    PARSING: 'SimulationState' = "parsing"
    BUILDING: 'SimulationState' = "building"
    READY: 'SimulationState' = "ready"
    RUNNING: 'SimulationState' = "running"
    PAUSED: 'SimulationState' = "paused"
    COMPLETED: 'SimulationState' = "completed"
    FAILED: 'SimulationState' = "failed"

    # Class-level lock for thread safety
    _lock: ClassVar[threading.RLock] = threading.RLock()

    # Type annotation for valid state transitions
    _TRANSITIONS: ClassVar[Dict['SimulationState', Set['SimulationState']]] = {
        INITIALIZING: {PARSING, FAILED},
        PARSING: {BUILDING, FAILED},
        BUILDING: {READY, FAILED},
        READY: {RUNNING, FAILED},
        RUNNING: {PAUSED, COMPLETED, FAILED},
        PAUSED: {RUNNING, FAILED},
    }
    
    # Terminal states that cannot be transitioned from
    _TERMINAL_STATES: ClassVar[Set['SimulationState']] = {COMPLETED, FAILED}

    def can_transition_to(self, new_state: 'SimulationState', raise_on_invalid: bool = False) -> bool:
        """
        Check if a transition to the new state is valid.

        This method is thread-safe and can be called from multiple threads.

        Args:
            new_state: The target state to transition to. Must be a SimulationState enum value.
            raise_on_invalid: If True, raises InvalidStateTransitionError for invalid transitions
                             instead of returning False.

        Returns:
            bool: True if the transition is valid, False otherwise
                (only when raise_on_invalid is False).

        Raises:
            InvalidStateTransitionError: If raise_on_invalid is True and the transition is invalid.
            TypeError: If new_state is not a SimulationState.

        Example:
            >>> state = SimulationState.INITIALIZING
            >>> state.can_transition_to(SimulationState.PARSING)
            True
            >>> state.can_transition_to(SimulationState.RUNNING, raise_on_invalid=True)
            Traceback (most recent call last):
                ...
            InvalidStateTransitionError: Cannot transition from initializing to running: Invalid state transition
        """
        if not isinstance(new_state, SimulationState):
            error_msg = f"Expected SimulationState, got {type(new_state).__name__}"
            if raise_on_invalid:
                raise TypeError(error_msg)
            return False
            
        # No need for a lock for read-only operations
        if self == new_state:
            return True  # Staying in the same state is always allowed
            
        if self in self._TERMINAL_STATES:
            error_msg = f"Cannot transition from terminal state: {self.value}"
            if raise_on_invalid:
                raise InvalidStateTransitionError(self, new_state, error_msg)
            return False
            
        valid_transitions = self._TRANSITIONS.get(self, frozenset())
        if new_state not in valid_transitions:
            if raise_on_invalid:
                valid_states = ', '.join(s.value for s in valid_transitions)
                raise InvalidStateTransitionError(
                    self, 
                    new_state,
                    f"Valid transitions from {self.value} are: {valid_states}"
                )
            return False
            
        return True


    @property
    def is_terminal(self) -> bool:
        """
        Check if this is a terminal state (COMPLETED or FAILED).
        
        Terminal states are states from which no further transitions are possible.
        Once a simulation reaches a terminal state, it cannot transition to any other state.

        Returns:
            bool: True if this is a terminal state
            
        Example:
            >>> SimulationState.COMPLETED.is_terminal
            True
            >>> SimulationState.RUNNING.is_terminal
            False
        """
        return self in self._TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        """
        Check if this is an active state where the simulation is running or can be.
        
        Active states are those where the simulation is either:
        - In progress (RUNNING, PAUSED)
        - Ready to run (READY)
        - Being set up (INITIALIZING, PARSING, BUILDING)

        Returns:
            bool: True if this is an active state
            
        Example:
            >>> SimulationState.RUNNING.is_active
            True
            >>> SimulationState.COMPLETED.is_active
            False
        """
        return not self.is_terminal
        
    @property
    def next_possible_states(self) -> frozenset['SimulationState']:
        """
        Get the set of valid states that can be transitioned to from the current state.
        
        This method is thread-safe and returns an immutable frozenset to prevent
        accidental modification of the internal state.
        
        Returns:
            frozenset[SimulationState]: Immutable set of valid next states.
            
        Example:
            >>> SimulationState.INITIALIZING.next_possible_states
            frozenset({<SimulationState.PARSING: 'parsing'>, <SimulationState.FAILED: 'failed'>})
        """
        # No lock needed for read-only access to _TRANSITIONS
        if self.is_terminal:
            return frozenset()
        return frozenset(self._TRANSITIONS.get(self, frozenset()))
