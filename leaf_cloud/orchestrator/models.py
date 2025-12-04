"""
Data models and type definitions for the LEAF-Cloud Orchestrator.

This module defines all data structures and type hints used throughout the orchestrator package.
"""
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Literal, Optional, TypedDict

# For making dictionaries hashable
class frozendict(dict):
    """An immutable dictionary that can be used as a dictionary key"""
    def __hash__(self):
        return hash(tuple(sorted(self.items())))


class SimulationState(Enum):
    """Represents the current state of the simulation."""
    INITIALIZING = auto()
    PARSING = auto()
    BUILDING = auto()
    CONFIGURING = auto()
    READY = auto()
    RUNNING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    STOPPED = auto()
    FAILED = auto()
    EXPORTING = auto()


class TokenColor(Enum):
    """Represents the color/type of a token in the Petri net."""
    REQUEST = "request"
    WORKLOAD = "workload"
    RESOURCE = "resource"
    CONTROL = "control"


@dataclass(frozen=True, eq=True)
class Token:
    """Represents a token in the Petri net."""
    id: str
    color: TokenColor
    attributes: Dict[str, Any]
    creation_time: float
    history: List[Dict[str, Any]] = field(default_factory=list)
    completion_time: Optional[float] = None
    
    def __post_init__(self):
        # Ensure attributes and history are properly typed
        if not isinstance(self.attributes, dict):
            object.__setattr__(self, 'attributes', dict(self.attributes))
        if not isinstance(self.history, list):
            object.__setattr__(self, 'history', list(self.history))
    
    def __hash__(self):
        """Generate a hash based on the token's properties."""
        return hash((
            self.id,
            self.color,
            tuple(sorted((k, v) for k, v in self.attributes.items())),
            self.creation_time,
            tuple(tuple(sorted(h.items())) for h in self.history),
            self.completion_time if self.completion_time is not None else 0
        ))
        
    def with_updates(
        self,
        attributes: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        completion_time: Optional[float] = None
    ) -> 'Token':
        """Return a new Token with the specified updates."""
        return Token(
            id=self.id,
            color=self.color,
            attributes=dict(attributes) if attributes is not None else dict(self.attributes),
            creation_time=self.creation_time,
            history=list(history) if history is not None else list(self.history),
            completion_time=completion_time if completion_time is not None else self.completion_time
        )


class TokenFlowLogEntry(TypedDict, total=False):
    """Base type for all token flow log entries (aligned with framework types)."""
    timestamp: float
    event_type: str


class TokenCreationLog(TokenFlowLogEntry):
    """Log entry for the creation of a token."""
    event_type: Literal["token_creation"]
    token_id: str
    place: str
    attributes: Dict[str, Any]


class TokenDistributionLog(TokenFlowLogEntry):
    """Log entry for the distribution of tokens."""
    event_type: Literal["token_distribution"]
    distribution: Dict[str, int]


class TransitionFiredLog(TokenFlowLogEntry):
    """Log entry for a transition being fired."""
    event_type: Literal["transition_fired"]
    transition: str
    input_places: Dict[str, int]
    output_places: Dict[str, int]
    resources: Optional[List[Dict[str, Any]]]
    delay: Optional[float]


class TokenCompletedLog(TokenFlowLogEntry):
    """Log entry for a token completing its lifecycle."""
    event_type: Literal["token_completed"]
    token_id: str
    place: str


@dataclass(frozen=True)
class ProgressState:
    """
    Immutable dataclass to hold the state for the simulation progress display.
    
    Attributes:
        current_time: Current simulation time in seconds.
        duration: Total expected duration of the simulation in seconds.
        completed_tokens: Number of tokens that have completed processing.
        pending_events: Number of events waiting to be processed.
        errors: Number of errors encountered during simulation.
        events_per_sec: Current rate of event processing in events per second.
        final_status: Optional final status message when simulation completes.
    """
    current_time: float
    duration: float
    completed_tokens: int
    pending_events: int
    errors: int
    events_per_sec: float
    final_status: Optional[str] = None
    
    @property
    def progress_percentage(self) -> float:
        """
        Calculate the simulation progress as a percentage.
        
        Returns:
            float: Progress percentage between 0.0 and 100.0
        """
        if self.duration <= 0:
            return 0.0
        return min(100.0, max(0.0, (self.current_time / self.duration) * 100.0))
    
    @property
    def estimated_remaining_time(self) -> Optional[float]:
        """
        Estimate the remaining time based on current processing rate.
        
        Returns:
            Optional[float]: Estimated remaining time in seconds, 0.0 if no pending events,
                           or None if rate is zero or negative.
        """
        if self.pending_events <= 0:
            return 0.0
        if self.events_per_sec <= 0:
            return None
        return self.pending_events / self.events_per_sec
    
    def __str__(self) -> str:
        """
        Return a human-readable string representation of the progress state.
        
        Returns:
            str: Formatted string with progress information.
        """
        parts = [
            f"Progress: {self.current_time:.1f}/{self.duration:.1f}s ({self.progress_percentage:.1f}%)",
            f"Tokens: {self.completed_tokens} completed, {self.pending_events} pending",
            f"Errors: {self.errors}",
            f"Rate: {self.events_per_sec:.1f} events/s"
        ]
        
        if self.estimated_remaining_time is not None:
            parts.append(f"Remaining: {self.estimated_remaining_time:.1f}s")
            
        if self.final_status:
            parts.append(f"Status: {self.final_status}")
            
        return " | ".join(parts)
