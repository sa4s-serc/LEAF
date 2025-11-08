import uuid
import heapq
import logging
from enum import Enum
from typing import Dict, List, Set, Tuple, Any, Optional, Callable, Union, cast
from dataclasses import dataclass, field
import threading
from collections import defaultdict
import time

"""
Colored Petri Net (CPN) implementation for the LEAF-Cloud framework.
This module provides the core simulation engine for modeling cloud infrastructure
using Colored Petri Nets, allowing for dynamic token flow through places and transitions.
"""


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TokenColor(Enum):
    """Represents the types of tokens that can exist in the Petri net."""
    REQUEST = "request"
    COMPUTE = "compute"
    STORAGE = "storage"
    NETWORK = "network"
    SECURITY = "security"
    GENERIC = "generic"

@dataclass
class Token:
    """
    Represents a colored token in the Petri net.
    
    Attributes:
        id: Unique identifier for the token
        color: The color/type of the token
        attributes: Additional attributes associated with the token
        creation_time: When the token was created in simulation time
        history: List of places the token has visited
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    color: TokenColor = TokenColor.GENERIC
    attributes: Dict[str, Any] = field(default_factory=dict)
    creation_time: float = field(default_factory=time.time)
    history: List[str] = field(default_factory=list)
    
    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if not isinstance(other, Token):
            return NotImplemented
        return self.id == other.id

    def clone(self) -> 'Token':
        """Create a copy of the token with a new ID but same attributes."""
        return Token(
            color=self.color,
            attributes=self.attributes.copy(),
            creation_time=self.creation_time,
            history=self.history.copy()
        )
    
    def update_attribute(self, key: str, value: Any) -> None:
        """Update a specific attribute of the token."""
        self.attributes[key] = value
        
    def add_to_history(self, place_id: str) -> None:
        """Record that the token visited a specific place."""
        self.history.append(place_id)

class Place:
    """
    Represents a place in the Petri net where tokens can reside.
    
    Attributes:
        id: Unique identifier for the place
        name: Human-readable name for the place
        capacity: Maximum number of tokens the place can hold (None for unlimited)
        tokens: Set of tokens currently in this place
        resource_type: Type of resource this place represents (compute, storage, etc.)
    """
    def __init__(
        self, 
        id: str, 
        name: Optional[str] = None, 
        capacity: Optional[int] = None,
        resource_type: Optional[str] = None
    ):
        self.id = id
        self.name = name or id
        self.capacity = capacity
        self.tokens = set()
        self.resource_type = resource_type
        self._lock = threading.RLock()  # Reentrant lock for thread safety
        
    def add_token(self, token: Token) -> bool:
        """
        Add a token to this place if capacity allows.
        
        Args:
            token: The token to add
            
        Returns:
            bool: True if token was added, False if at capacity
        """
        with self._lock:
            if self.capacity is not None and len(self.tokens) >= self.capacity:
                return False
                
            self.tokens.add(token)
            token.add_to_history(self.id)
            return True
    
    def remove_token(self, token: Optional[Token] = None) -> bool:
        with self._lock:
            if not self.tokens:
                return False

            if token is not None:
                if token in self.tokens:
                    self.tokens.remove(token)
                    return True
                return False

            if self.tokens: # Ensure not empty before pop
                self.tokens.pop()
            return True
    
    def get_tokens(self, color: Optional[TokenColor] = None) -> Set[Token]:
        """
        Get all tokens in this place, optionally filtered by color.
        
        Args:
            color: Optional color to filter tokens by
            
        Returns:
            Set of tokens matching the criteria
        """
        with self._lock:
            if color is None:
                return self.tokens.copy()
            return {t for t in self.tokens if t.color == color}
    
    def token_count(self, color: Optional[TokenColor] = None) -> int:
        """
        Count tokens in this place, optionally filtered by color.
        
        Args:
            color: Optional color to filter tokens by
            
        Returns:
            Number of tokens matching the criteria
        """
        return len(self.get_tokens(color))
    
    def is_full(self) -> bool:
        """Check if the place is at capacity."""
        return self.capacity is not None and len(self.tokens) >= self.capacity
    
    def is_empty(self) -> bool:
        """Check if the place has no tokens."""
        return len(self.tokens) == 0

class Arc:
    """
    Represents a connection between a place and a transition.
    
    Attributes:
        id: Unique identifier for the arc
        place_id: ID of the connected place
        transition_id: ID of the connected transition
        direction: 'input' for place→transition or 'output' for transition→place
        weight: Number of tokens required/produced
        guard: Optional function that determines if tokens can flow through this arc
    """
    def __init__(
        self, 
        id: str,
        place_id: str, 
        transition_id: str, 
        direction: str,
        weight: int = 1,
        guard: Optional[Callable[[Token], bool]] = None
    ):
        self.id = id
        self.place_id = place_id
        self.transition_id = transition_id
        
        if direction not in ['input', 'output']:
            raise ValueError("Arc direction must be 'input' or 'output'")
        self.direction = direction
        
        self.weight = weight
        self.guard = guard or (lambda _: True)  # Default guard always allows tokens
    
    def can_accept_token(self, token: Token) -> bool:
        """Check if a token can flow through this arc based on the guard function."""
        return self.guard(token)

class Transition:
    """
    Represents an activity that can move tokens from input places to output places.
    
    Attributes:
        id: Unique identifier for the transition
        name: Human-readable name
        delay: Time delay for this transition (in simulation time units)
        priority: Priority for concurrent transition firing (higher fires first)
        guard: Optional function that determines if the transition can fire
        action: Optional function to execute when transition fires
    """
    def __init__(
        self, 
        id: str, 
        name: Optional[str] = None,
        delay: float = 0.0,
        priority: int = 0,
        guard: Optional[Callable[[Dict[str, List[Token]]], bool]] = None,
        action: Optional[Callable[[Dict[str, List[Token]]], Dict[str, List[Token]]]] = None
    ):
        self.id = id
        self.name = name or id
        self.delay = delay
        self.priority = priority
        self.guard = guard or (lambda _: True)
        self.action = action or (lambda tokens: tokens)
        self._lock = threading.RLock()
    
    def get_delay(self, tokens: Dict[str, List[Token]]) -> float:
        """
        Get the delay for this transition, can be overridden for dynamic delays.
        
        Args:
            tokens: Dictionary of input place IDs to tokens being consumed
            
        Returns:
            Delay time in simulation units
        """
        return self.delay

class Event:
    """
    Represents a scheduled event in the simulation.
    
    Attributes:
        time: Simulation time when this event should occur
        transition_id: ID of the transition associated with this event
        tokens: Dictionary of place IDs to tokens involved in this event
        priority: Priority for events occurring at the same time
    """
    def __init__(
        self, 
        time: float, 
        transition_id: str, 
        tokens: Dict[str, List[Token]],
        priority: int = 0
    ):
        self.time = time
        self.transition_id = transition_id
        self.tokens = tokens
        self.priority = priority
        self.id = str(uuid.uuid4())  # Unique ID for breaking ties in heapq
        
    def __lt__(self, other: 'Event') -> bool:
        """
        Compare events for priority queue ordering.
        First by time, then by priority, then by ID for consistent ordering.
        """
        if self.time != other.time:
            return self.time < other.time
        if self.priority != other.priority:
            return self.priority > other.priority  # Higher priority first
        return self.id < other.id  # Arbitrary but consistent

class PetriNet:
    """
    Main class for the Colored Petri Net simulation engine.
    
    Manages places, transitions, arcs, and the simulation execution.
    """
    SOURCE_PLACE_ID = "global_source_place"
    SINK_PLACE_ID = "global_sink_place"

    def __init__(self, name: str):
        self.name: str = name
        self.places: Dict[str, Place] = {}
        self.transitions: Dict[str, Transition] = {}
        self.arcs: Dict[str, Arc] = {}
        
        # Maps for faster access during simulation
        self.input_arcs: Dict[str, List[Arc]] = defaultdict(list)  # transition_id -> list of input arcs
        self.output_arcs: Dict[str, List[Arc]] = defaultdict(list)  # transition_id -> list of output arcs
        
        # Simulation state
        self.event_queue: List[Event] = []  # Priority queue of pending events
        self.current_time: float = 0.0
        self.is_running: bool = False
        self._lock = threading.RLock()

        # Add global source and sink places
        source_place = Place(id=PetriNet.SOURCE_PLACE_ID, name="Source", capacity=None)  # None for unlimited capacity
        sink_place = Place(id=PetriNet.SINK_PLACE_ID, name="Global Sink", capacity=None)  # None for unlimited capacity
        # Directly add to self.places to avoid potential lock issues if add_place is called before lock is fully initialized in some contexts
        # Or ensure add_place is safe to call here.
        # For simplicity here, direct assignment, assuming __init__ is single-threaded context for this part.
        self.places[source_place.id] = source_place
        self.places[sink_place.id] = sink_place
        
    def __str__(self):
        return self.name
    
    def add_place(self, place: Place) -> None:
        """Add a place to the Petri net."""
        with self._lock:
            if place.id in self.places:
                raise ValueError(f"Place with ID {place.id} already exists")
            self.places[place.id] = place
    
    def add_transition(self, transition: Transition) -> None:
        """Add a transition to the Petri net."""
        with self._lock:
            if transition.id in self.transitions:
                raise ValueError(f"Transition with ID {transition.id} already exists")
            self.transitions[transition.id] = transition
    
    def add_arc(self, arc: Arc) -> None:
        """Add an arc to the Petri net."""
        with self._lock:
            if arc.id in self.arcs:
                raise ValueError(f"Arc with ID {arc.id} already exists")
                
            # Validate references
            if arc.place_id not in self.places:
                raise ValueError(f"Place {arc.place_id} does not exist")
            if arc.transition_id not in self.transitions:
                raise ValueError(f"Transition {arc.transition_id} does not exist")
                
            self.arcs[arc.id] = arc
            
            # Add to appropriate map for faster access
            if arc.direction == 'input':
                self.input_arcs[arc.transition_id].append(arc)
            else:  # output
                self.output_arcs[arc.transition_id].append(arc)
    
    def add_token(self, place_id: str, token: Token) -> bool:
        """
        Add a token to a specific place.
        
        Args:
            place_id: ID of the place to add the token to
            token: The token to add
            
        Returns:
            bool: True if token was added successfully
        """
        with self._lock:
            if place_id not in self.places:
                raise ValueError(f"Place {place_id} does not exist")
            
            return self.places[place_id].add_token(token)
    
    def find_enabled_transitions(self) -> List[Tuple[str, Dict[str, List[Token]]]]:
        """
        Find all transitions that can fire and their corresponding input tokens.
        
        Returns:
            List of tuples (transition_id, {place_id: [tokens]})
        """
        enabled_transitions = []
        
        for transition_id, transition in self.transitions.items():
            # Get input arcs for this transition
            arcs = self.input_arcs[transition_id]
            if not arcs:
                # No input arcs means the transition is always ready to fire
                enabled_transitions.append((transition_id, {}))
                continue
            
            # Group arcs by place to handle multiple arcs from the same place
            arcs_by_place = defaultdict(list)
            for arc in arcs:
                arcs_by_place[arc.place_id].append(arc)
            
            # For each place, find valid token combinations
            valid_tokens_by_place = {}
            for place_id, place_arcs in arcs_by_place.items():
                place = self.places[place_id]
                available_tokens = place.get_tokens()
                
                # Find tokens that match any arc's guard
                valid_tokens = []
                for token in available_tokens:
                    if any(arc.can_accept_token(token) for arc in place_arcs):
                        valid_tokens.append(token)
                
                # If we don't have enough tokens for any place, this transition can't fire
                total_weight = sum(arc.weight for arc in place_arcs)
                if len(valid_tokens) < total_weight:
                    break
                    
                valid_tokens_by_place[place_id] = valid_tokens
            
            # If we found valid tokens for all input places
            if len(valid_tokens_by_place) == len(arcs_by_place):
                # Check if the transition's guard allows firing
                if transition.guard(valid_tokens_by_place):
                    enabled_transitions.append((transition_id, valid_tokens_by_place))
        
        return enabled_transitions
    
    def schedule_transition(self, transition_id: str, input_tokens: Dict[str, List[Token]]) -> None:
        """
        Schedule a transition to fire.
        
        Args:
            transition_id: ID of the transition to fire
            input_tokens: Dictionary of {place_id: [tokens]} for input tokens
        """
        transition = self.transitions[transition_id]
        delay = transition.get_delay(input_tokens)
        event_time = self.current_time + delay
        
        event = Event(
            time=event_time,
            transition_id=transition_id,
            tokens=input_tokens,
            priority=transition.priority
        )
        
        heapq.heappush(self.event_queue, event)
    
    def fire_transition(self, transition_id: str, input_tokens: Dict[str, List[Token]]) -> bool:
        """
        Execute a transition firing.
        
        Args:
            transition_id: ID of the transition to fire
            input_tokens: Dictionary of {place_id: [tokens]} for input tokens
            
        Returns:
            bool: True if firing was successful
        """
        with self._lock:
            transition = self.transitions[transition_id]
            
            # First, try to remove input tokens
            for place_id, tokens in input_tokens.items():
                place = self.places[place_id]
                for token in tokens:
                    success = place.remove_token(token)
                    if not success:
                        # If any token removal fails, abort the firing
                        # This could happen if another thread already removed a token
                        logger.warning(f"Failed to remove token from place {place_id}")
                        return False
            
            # Execute the transition's action
            try:
                output_tokens = transition.action(input_tokens)
            except Exception as e:
                logger.error(f"Error executing transition action: {e}")
                # In case of error, try to put back input tokens
                for place_id, tokens in input_tokens.items():
                    place = self.places[place_id]
                    for token in tokens:
                        place.add_token(token)
                return False
            
            # Place output tokens
            for place_id, tokens in output_tokens.items():
                place = self.places.get(place_id)
                if not place:
                    logger.error(f"Output place {place_id} does not exist")
                    continue
                    
                for token in tokens:
                    success = place.add_token(token)
                    if not success:
                        logger.warning(f"Failed to add token to place {place_id} (possibly at capacity)")
            
            return True
    
    def get_output_tokens(self, transition_id: str) -> Dict[str, List[Token]]:
        """
        Get the output tokens produced by a transition.
        
        Args:
            transition_id: ID of the transition
            
        Returns:
            Dictionary of {place_id: [tokens]} for output tokens
        """
        with self._lock:
            if transition_id not in self.output_arcs:
                raise ValueError(f"Transition {transition_id} does not exist or has no output arcs")
            
            output_tokens = {}
            for arc in self.output_arcs[transition_id]:
                place = self.places[arc.place_id]
                output_tokens[place.id] = list(place.get_tokens())
                
            return output_tokens
        
    def get_input_tokens(self, transition_id: str) -> Dict[str, List[Token]]:
        """
        Get the input tokens consumed by a transition.
        
        Args:
            transition_id: ID of the transition
            
        Returns:
            Dictionary of {place_id: [tokens]} for input tokens
        """
        with self._lock:
            if transition_id not in self.input_arcs:
                raise ValueError(f"Transition {transition_id} does not exist or has no input arcs")
            
            input_tokens = {}
            for arc in self.input_arcs[transition_id]:
                place = self.places[arc.place_id]
                input_tokens[place.id] = list(place.get_tokens())
                
            return input_tokens

    def simulate(self, max_steps: Optional[int] = None, max_time: Optional[float] = None) -> Dict[str, Any]:
        """
        Run the simulation until a termination condition is met.
        
        Args:
            max_steps: Maximum number of transition firings (None for unlimited)
            max_time: Maximum simulation time (None for unlimited)
            
        Returns:
            Dict containing simulation statistics
        """
        with self._lock:
            self.is_running = True
            steps = 0
            start_real_time = time.time()
            
            while self.is_running:
                if max_steps is not None and steps >= max_steps:
                    break
                    
                if max_time is not None and self.current_time >= max_time:
                    break
                
                if not self.event_queue:
                    # If there are no scheduled events, look for enabled transitions
                    enabled = self.find_enabled_transitions()
                    for transition_id, input_tokens in enabled:
                        self.schedule_transition(transition_id, input_tokens)
                    
                    if not self.event_queue:
                        # If still no events, we're done
                        break
                
                # Get the next event
                event = heapq.heappop(self.event_queue)
                self.current_time = event.time
                
                # Execute the event
                success = self.fire_transition(event.transition_id, event.tokens)
                if success:
                    steps += 1
            
            self.is_running = False
            
            # Collect statistics
            stats = {
                "steps": steps,
                "simulation_time": self.current_time,
                "real_time": time.time() - start_real_time,
                "token_counts": {place_id: len(place.tokens) for place_id, place in self.places.items()},
                "final_state": {
                    place_id: [
                        {"id": token.id, "color": token.color.value, **token.attributes}
                        for token in place.tokens
                    ]
                    for place_id, place in self.places.items()
                }
            }
            
            return stats
    
    def stop_simulation(self) -> None:
        """Stop the simulation."""
        self.is_running = False
    
    def reset(self) -> None:
        """Reset the simulation to its initial state."""
        with self._lock:
            # Clear all tokens
            for place in self.places.values():
                place.tokens.clear()
            
            # Reset simulation state
            self.event_queue = []
            self.current_time = 0.0
            self.is_running = False
        
    def get_transition_by_id(self, transition_id: str) -> Optional[Transition]:
        """Find a transition by its ID."""
        if transition_id in self.transitions:
            return self.transitions[transition_id]
        return None

    def get_place_token_mapping(self, transition_id : str) -> Dict[str, List[Token]]:
        """Get the mapping of place to tokens for a transition"""
        mapping = {}
        for arc in self.input_arcs[transition_id]:
            place_id = arc.place_id
            tokens = self.places[place_id].get_tokens()
            mapping[place_id] = list(tokens)
        return mapping

    def get_place_by_name(self, name: str) -> Optional[Place]:
        """Find a place by its human-readable name."""
        for place in self.places.values():
            if place.name == name:
                return place
        return None
    
    def get_transition_by_name(self, name: str) -> Optional[Transition]:
        """Find a transition by its human-readable name."""
        if name in self.transitions:
            return self.transitions[name]
        return None

    def visualize(self) -> None:
        """
        Visualize the Petri net structure.
        
        This is a placeholder for future visualization capabilities.
        """
        logger.info(f"Petri Net: {self.name}")
        logger.info("Places:")
        for place in self.places.values():
            logger.info(f"  {place.id} ({place.name}): {len(place.tokens)} tokens")
        
        logger.info("Transitions:")
        for transition in self.transitions.values():
            logger.info(f"  {transition.id} ({transition.name}), Delay: {transition.delay}, Priority: {transition.priority}")
        
        logger.info("Arcs:")
        for arc in self.arcs.values():
            logger.info(f"  Arc {arc.id}: {arc.place_id} -> {arc.transition_id} ({arc.direction}, weight={arc.weight})")