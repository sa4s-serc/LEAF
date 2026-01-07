import uuid
import heapq
import logging
import weakref
import threading
import time
from enum import Enum, auto
from typing import (
    Dict, 
    List, 
    Set, 
    Tuple, 
    Any, 
    Optional, 
    Callable, 
    Union, 
    cast,
    TypeVar,
    ClassVar,
    Deque,
    DefaultDict,
    Iterator,
    TYPE_CHECKING,
)
from dataclasses import dataclass, field, asdict
from contextlib import contextmanager, ExitStack
from collections import defaultdict, deque
from functools import wraps, lru_cache
from datetime import datetime
from threading import RLock, Condition
from enum import IntEnum
import tracemalloc
import os

# Type variable for generic token types
T = TypeVar('T', bound='Token')

# Configure logging
logger = logging.getLogger(__name__)

# Enable tracemalloc for memory tracking only when explicitly requested
if os.environ.get("LEAF_ENABLE_TRACEMALLOC") == "1":
    tracemalloc.start()

if TYPE_CHECKING:
    from leaf_cloud.core.resource import ResourceType

"""
Colored Petri Net (CPN) implementation for the LEAF-Cloud framework.
This module provides the core simulation engine for modeling cloud infrastructure
using Colored Petri Nets, allowing for dynamic token flow through places and transitions.
"""


# Configure logging
logger = logging.getLogger(__name__)


class PetriNetError(Exception):
    """Base class for all Petri net related errors."""
    pass


class PetriNetValidationError(PetriNetError, ValueError):
    """Raised when there's a validation error in the Petri net.
    
    This includes invalid configurations, missing required components, or
    violations of Petri net rules.
    """
    pass


class PetriNetStateError(PetriNetError, RuntimeError):
    """Raised when the Petri net is in an invalid state for the operation.
    
    Examples:
        - Firing a disabled transition
        - Modifying the net during simulation
        - Accessing a non-existent place or transition
    """
    pass


class PetriNetSimulationError(PetriNetError, RuntimeError):
    """Raised when there's an error during simulation.
    
    This includes issues like deadlocks, timeouts, or other runtime problems
    that prevent the simulation from continuing.
    """
    pass


class PetriNetResourceError(PetriNetError, RuntimeError):
    """Raised when there's an issue with resources in the Petri net.
    
    This includes:
        - Attempting to exceed place capacity
        - Missing or invalid tokens
        - Resource allocation failures
    """
    pass


class PetriNetConcurrencyError(PetriNetError, RuntimeError):
    """Raised when there are concurrency-related issues.
    
    This includes thread safety violations, deadlocks, or race conditions
    in the Petri net's concurrent operations.
    """
    pass


import gc
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from enum import Enum
from queue import Empty, PriorityQueue
from threading import RLock
from typing import (Any, Callable, ClassVar, Dict, FrozenSet, List, Optional,
                    Set, Tuple, Type, TypeVar, Union, cast)

# Type variable for the enum class
E = TypeVar('E', bound=Enum)

class TokenColor(str, Enum):
    """Represents the types of tokens that can exist in the Petri net.
    
    This is similar to ResourceType but with an additional REQUEST type.
    """
    COMPUTE = "compute"
    STORAGE = "storage"
    NETWORK = "network"
    SECURITY = "security"
    GENERIC = "generic"
    DATABASE = "database"
    REQUEST = "request"
    RESOURCE = "resource"
    
    @classmethod
    def from_resource_type(cls, resource_type: 'ResourceType') -> 'TokenColor':
        """Convert a ResourceType to the corresponding TokenColor."""
        try:
            return cls(resource_type.value)
        except ValueError:
            # If the value doesn't match any TokenColor, return GENERIC as fallback
            return cls.GENERIC


class Token:
    """
    Represents a colored token in the Petri net with thread-safe operations.
    
    This class implements the flyweight pattern to reduce memory usage for tokens
    with the same attributes. It also includes weakref support for better memory
    management and tracking.
    
    Thread Safety:
        - All public methods are thread-safe using reentrant locks.
        - Instance creation is thread-safe due to class-level locks.
        - Weak references are managed in a thread-safe manner.
        
    Memory Management:
        - Uses weak references to track instances and prevent memory leaks.
        - Implements a cleanup mechanism to remove unreferenced tokens from the cache.
        - Supports manual cleanup of resources when needed.
    """
    
    # Class-level weak set to track all token instances
    _instances: ClassVar[weakref.WeakSet['Token']] = weakref.WeakSet()
    _instance_lock: ClassVar[RLock] = RLock()
    
    # Flyweight pattern: cache token instances with the same attributes
    _token_cache: ClassVar[Dict[Tuple, 'Token']] = {}
    _cache_lock: ClassVar[RLock] = RLock()
    
    # Track if cleanup has been registered with atexit
    _cleanup_registered: ClassVar[bool] = False
    _cleanup_lock: ClassVar[RLock] = RLock()
    
    def __new__(
        cls,
        color: TokenColor = TokenColor.GENERIC,
        attributes: Optional[Dict[str, Any]] = None,
        creation_time: Optional[float] = None,
        history: Optional[List[str]] = None,
        token_id: Optional[str] = None,
    ) -> 'Token':
        """Create or get a token with the given attributes.
        
        This method implements the flyweight pattern to reuse token instances with
        the same attributes, reducing memory usage. It ensures thread safety and
        proper cleanup of resources.
        
        Args:
            color: The color/type of the token. Defaults to TokenColor.GENERIC.
            attributes: Additional attributes for the token. If None, an empty dict is used.
                      Defaults to None.
            creation_time: When the token was created in simulation time. If None,
                         uses the current system time. Defaults to None.
            history: List of place IDs the token has visited. If None, an empty list is used.
                   Defaults to None.
            token_id: Optional explicit ID for the token. If None, a UUID is generated.
                    Defaults to None.
            
        Returns:
            Token: A new or existing Token instance with the given attributes.
            
        Raises:
            TypeError: If color is not a TokenColor or attributes is not a dictionary.
            ValueError: If token_id is not a valid string when provided.
            
        Example:
            >>> # Create a new token with attributes
            >>> token1 = Token(color=TokenColor.COMPUTE, attributes={"cpu": 4, "memory": 8192})
            >>> 
            >>> # Get the same token again (same attributes)
            >>> token2 = Token(color=TokenColor.COMPUTE, attributes={"cpu": 4, "memory": 8192})
            >>> token1 is token2  # Same object due to flyweight pattern
            True
            >>> 
            >>> # Different attributes create different tokens
            >>> token3 = Token(color=TokenColor.COMPUTE, attributes={"cpu": 8})
            >>> token1 is token3
            False
        """
        # Input validation
        if not isinstance(color, TokenColor):
            raise TypeError(f"color must be a TokenColor, got {type(color).__name__}")
            
        if attributes is not None and not isinstance(attributes, dict):
            raise TypeError(f"attributes must be a dict, got {type(attributes).__name__}")
            
        if token_id is not None and not isinstance(token_id, str):
            raise TypeError(f"token_id must be a string, got {type(token_id).__name__}")
            
        # Validate creation_time
        if creation_time is not None and not isinstance(creation_time, (int, float)):
            raise TypeError(f"creation_time must be a number or None, got {type(creation_time).__name__}")
        
        # Always create a new token instance when token_id is provided
        # This ensures tokens with different IDs are treated as distinct instances
        if token_id is not None:
            self = super().__new__(cls)
            self._id = token_id
            self._color = color
            self._attributes = dict(attributes or {})
            self._creation_time = creation_time if creation_time is not None else time.time()
            self._history = list(history or []) if history is not None else []
            self._lock = RLock()
            
            # Add to instance tracking
            with cls._instance_lock:
                cls._instances.add(self)
                
            return self
            
        # For tokens without an explicit ID, always create a fresh instance to avoid
        # sharing mutable state across callers. We keep instance tracking for metrics
        # but do not reuse instances from any cache.
        self = super().__new__(cls)
        self._id = str(uuid.uuid4())  # Generate a new UUID for the token
        self._color = color
        self._attributes = dict(attributes or {})
        self._creation_time = creation_time if creation_time is not None else time.time()
        self._history = list(history or []) if history is not None else []
        self._lock = RLock()

        # Register cleanup on interpreter shutdown if not already done
        with cls._cleanup_lock:
            if not cls._cleanup_registered:
                import atexit
                atexit.register(cls.cleanup_all)
                cls._cleanup_registered = True

        # Track instance
        with cls._instance_lock:
            cls._instances.add(self)

        return self
    
    @classmethod
    def _cleanup_cache(cls) -> None:
        """Clean up the token cache by removing unreferenced tokens."""
        # Force garbage collection first
        gc.collect()
        
        with cls._cache_lock:
            # Make a copy of items to avoid modifying the dict during iteration
            items = list(cls._token_cache.items())
            
            # Find keys with tokens that only have one reference (the cache)
            to_remove = []
            for key, token in items:
                # Get the reference count and subtract 2 (one for the cache, one for the local variable)
                ref_count = sys.getrefcount(token)
                if ref_count <= 2:
                    to_remove.append(key)
            
            # Remove the unreferenced tokens
            for key in to_remove:
                token = cls._token_cache.pop(key, None)
                if token is not None:
                    with cls._instance_lock:
                        if token in cls._instances:
                            cls._instances.remove(token)
                            
            # Force garbage collection again to clean up any remaining references
            gc.collect()
    
    @classmethod
    def cleanup_all(cls) -> None:
        """Clean up all token instances and clear caches.
        
        This method is registered with atexit to ensure proper cleanup when the program exits.
        It can also be called manually for testing purposes.
        """
        # First, clear any references in the cache
        with cls._cache_lock:
            # Clear the cache first to break any circular references
            cls._token_cache.clear()
            
        # Force garbage collection to clean up any remaining references
        gc.collect()
        
        # Now clear the instances
        with cls._instance_lock:
            cls._instances.clear()
            
        # Force garbage collection again
        gc.collect()
            
        # Reset the cleanup registration flag to allow re-registration if needed
        with cls._cleanup_lock:
            cls._cleanup_registered = False
    
    @property
    def id(self) -> str:
        """Get the unique identifier of the token."""
        return self._id
        
    @property
    def color(self) -> TokenColor:
        """Get the color/type of the token."""
        with self._lock:
            return self._color
    
    @property
    def attributes(self) -> Dict[str, Any]:
        """Get a copy of the token's attributes."""
        with self._lock:
            return self._attributes.copy()
    
    @property
    def creation_time(self) -> float:
        """Get when the token was created in simulation time."""
        with self._lock:
            return self._creation_time
    
    @property
    def history(self) -> List[str]:
        """Get a copy of the token's history."""
        with self._lock:
            return self._history.copy()
    
    def __hash__(self) -> int:
        # Hash should be stable and consistent with __eq__ (which uses only ID)
        # Including mutable attributes in hash breaks Set/Dict when attributes change
        return hash(self._id)
        
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Token):
            return NotImplemented
        return self._id == other.id
    
    def __repr__(self) -> str:
        return f"Token(id='{self._id}', color={self._color})"
    
    def clone(self) -> 'Token':
        """Create a copy of the token with a new ID but same attributes."""
        with self._lock:
            return Token(
                color=self._color,
                attributes=self._attributes.copy(),
                creation_time=self._creation_time,
                history=self._history.copy(),
            )
    
    def update_attribute(self, key: str, value: Any) -> None:
        """Update a specific attribute of the token."""
        with self._lock:
            self._attributes[key] = value
    
    def add_to_history(self, place_id: str) -> None:
        """Record that the token visited a specific place."""
        with self._lock:
            self._history.append(place_id)
    
    def get_memory_usage(self) -> int:
        """Get the approximate memory usage of this token in bytes."""
        size = 0
        with self._lock:
            # Approximate size of primitive attributes
            size += len(self._id) * 2  # Assuming UTF-16
            size += sys.getsizeof(self._color)
            size += sys.getsizeof(self._creation_time)
            
            # Size of attributes dictionary
            size += sum(
                len(k) * 2 + sys.getsizeof(v)
                for k, v in self._attributes.items()
            )
            
            # Size of history list
            size += sum(len(place_id) * 2 for place_id in self._history)
            
        return size
    
    @classmethod
    def get_instance_count(cls) -> int:
        """Get the current number of token instances."""
        with cls._instance_lock:
            return len(cls._instances)
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear the token cache.
        
        This is primarily used for testing to ensure a clean state.
        """
        with cls._cache_lock:
            cls._token_cache.clear()
            
        # Force garbage collection to clean up any remaining references
        gc.collect()
        
        # Force garbage collection
        gc.collect()


class Place:
    """
    Thread-safe implementation of a place in the Petri net where tokens can reside.
    
    This class provides thread-safe operations for managing tokens in a place,
    with support for capacity limits and token filtering.
    
    Thread Safety:
        All public methods are thread-safe using reentrant locks.
    
    Attributes:
        id: Unique identifier for the place
        name: Human-readable name for the place
        capacity: Maximum number of tokens allowed (None for unlimited)
    """
    __slots__ = ('_id', '_name', '_capacity', '_tokens', '_lock', '__weakref__')
    
    # Class-level weak set to track all place instances
    _instances: ClassVar[weakref.WeakSet['Place']] = weakref.WeakSet()
    _instance_lock: ClassVar[RLock] = RLock()
    
    def __init__(self, id: str, name: str = "", capacity: Optional[int] = None) -> None:
        """Initialize a new place.
        
        Args:
            id: Unique identifier for the place
            name: Human-readable name (defaults to id if empty)
            capacity: Maximum number of tokens allowed (None for unlimited)
            
        Raises:
            PetriNetValidationError: If capacity is not positive or invalid
        """
        if capacity is not None and capacity <= 0:
            raise PetriNetValidationError(f"Capacity must be positive or None, got {capacity}")
            
        self._id = id
        self._name = name or id
        self._capacity = capacity
        self._tokens: Set[Token] = set()
        self._lock = RLock()
        
        # Register instance
        with Place._instance_lock:
            Place._instances.add(self)
    
    @property
    def id(self) -> str:
        """Get the unique identifier of the place."""
        return self._id
    
    @property
    def name(self) -> str:
        """Get the name of the place."""
        return self._name
    
    @property
    def capacity(self) -> Optional[int]:
        """Get the capacity of the place."""
        return self._capacity
    
    @property
    def tokens(self) -> Set[Token]:
        """Get a copy of the tokens in this place."""
        with self._lock:
            return set(self._tokens)
            
    @property
    def token_count(self) -> int:
        """Get the current number of tokens in the place."""
        with self._lock:
            return len(self._tokens)
            
    def count_tokens(self, color: Optional[TokenColor] = None) -> int:
        """
        Count tokens in this place, optionally filtered by color.

        Args:
            color: Optional color to filter tokens by

        Returns:
            Number of tokens matching the criteria
        """
        with self._lock:
            if color is None:
                return len(self._tokens)
            return sum(1 for token in self._tokens if token.color == color)
            
    def __eq__(self, other: Any) -> bool:
        """Check if two places are equal based on their ID."""
        if not isinstance(other, Place):
            return False
        return self._id == other._id
        
    def __hash__(self) -> int:
        """Get hash of the place based on its ID."""
        return hash(self._id)
        
    def __str__(self) -> str:
        """String representation of the place.
        
        Returns:
            str: A string in the format "Place(id='{id}', name='{name}', capacity={capacity})"
        """
        return f"Place(id='{self._id}', name='{self._name}', capacity={self._capacity})"
        
    def __contains__(self, token: Token) -> bool:
        """Check if a token is in this place."""
        with self._lock:
            result = token in self._tokens
            return result
            
    def clear_tokens(self) -> None:
        """Remove all tokens from this place."""
        with self._lock:
            self._tokens.clear()
    
    @property
    def is_full(self) -> bool:
        """Check if the place is at capacity."""
        if self._capacity is None:
            return False
        with self._lock:
            return len(self._tokens) >= self._capacity
    
    @property
    def is_empty(self) -> bool:
        """Check if the place has no tokens."""
        with self._lock:
            return not bool(self._tokens)
    
    def add_token(self, token: Token) -> bool:
        """Add a token to this place if capacity allows.
        
        Args:
            token: The token to add
            
        Returns:
            bool: True if the token was added, False if capacity would be exceeded

        Raises:
            TypeError: If token is not an instance of Token
            PetriNetResourceError: If the place is at capacity and cannot accept more tokens

        Example:
            >>> place = Place("p1", capacity=2)
            >>> token = Token()
            >>> place.add_token(token)  # Returns True
            True
            >>> place.add_token(Token())  # Returns True
            True
            >>> place.add_token(Token())  # Returns False, at capacity
            False
        """
        if not isinstance(token, Token):
            raise TypeError(f"Expected Token, got {type(token).__name__}")
            
        with self._lock:
            if self.is_full:
                error_msg = f"Place '{self._id}' is at capacity ({self._capacity} tokens)"
                raise PetriNetResourceError(error_msg)
                
            try:
                # Add the token and verify it was added
                before_count = len(self._tokens)
                self._tokens.add(token)
                after_count = len(self._tokens)
                
                # Add to token history
                token.add_to_history(self._id)
                return True
                
            except Exception as e:
                import traceback
                traceback.print_exc()
                raise PetriNetResourceError(
                    f"Failed to add token to place '{self._id}': {str(e)}"
                ) from e
    
    def remove_token(self, token: Token) -> bool:
        """Remove a token from this place if it exists.
        
        Args:
            token: The token to remove
            
        Returns:
            bool: True if the token was removed, False if it wasn't found
        """
        with self._lock:
            if token in self._tokens:
                self._tokens.remove(token)
                return True
            return False
    
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


class Arc:
    """
    Thread-safe implementation of a connection between a place and a transition.
    
    This class represents a directed connection in the Petri net, either from a place
    to a transition (input arc) or from a transition to a place (output arc).
    
    Thread Safety:
        All public methods are thread-safe.
    
    Attributes:
        id: Unique identifier for the arc
        place_id: ID of the connected place
        transition_id: ID of the connected transition
        direction: 'input' for place→transition or 'output' for transition→place
        weight: Number of tokens required/produced
        guard: Optional function that determines if tokens can flow through this arc
    """
    __slots__ = ('_id', '_place_id', '_transition_id', '_direction', 
                '_weight', '_guard', '_lock', '__weakref__')
    
    # Class-level weak set to track all arc instances
    _instances: ClassVar[weakref.WeakSet['Arc']] = weakref.WeakSet()
    _instance_lock: ClassVar[RLock] = RLock()
    
    def __init__(
        self,
        id: str,
        place_id: str,
        transition_id: str,
        direction: str,
        weight: int = 1,
        guard: Optional[Callable[[Token], bool]] = None,
    ):
        """Initialize a new arc.
        
        Args:
            id: Unique identifier for the arc
            place_id: ID of the connected place
            transition_id: ID of the connected transition
            direction: 'input' for place→transition or 'output' for transition→place
            weight: Number of tokens required/produced (default: 1)
            guard: Optional function that determines if tokens can flow through this arc
            
        Raises:
            PetriNetValidationError: If direction or weight is invalid
            
        Example:
            >>> # Create an input arc from place 'p1' to transition 't1'
            >>> arc = Arc("a1", "p1", "t1", "input", weight=2)
            >>> arc.direction
            'input'
        """
        # Validate and normalize direction (case-insensitive)
        if not isinstance(direction, str) or direction.lower() not in ("input", "output"):
            raise PetriNetValidationError(
                f"Arc direction must be 'input' or 'output', got {direction!r}"
            )
            
        if not isinstance(weight, int) or weight <= 0:
            raise PetriNetValidationError(
                f"Weight must be a positive integer, got {weight!r}"
            )
            
        self._id = id
        self._place_id = place_id
        self._transition_id = transition_id
        self._direction = direction.lower()  # Normalize to lowercase
        self._weight = weight
        self._guard = guard or (lambda _: True)
        self._lock = RLock()
        
        # Register instance
        with Arc._instance_lock:
            Arc._instances.add(self)
    
    @property
    def id(self) -> str:
        """Get the unique identifier of the arc."""
        return self._id
    
    @property
    def place_id(self) -> str:
        """Get the ID of the connected place."""
        return self._place_id
    
    @property
    def transition_id(self) -> str:
        """Get the ID of the connected transition."""
        return self._transition_id
    
    @property
    def direction(self) -> str:
        """Get the direction of the arc ('input' or 'output')."""
        return self._direction
    
    @property
    def weight(self) -> int:
        """Get the weight of the arc."""
        return self._weight
    
    @weight.setter
    def weight(self, value: int) -> None:
        """Set the weight of the arc."""
        if value <= 0:
            raise ValueError(f"Weight must be positive, got {value}")
        with self._lock:
            self._weight = value
    
    def can_accept_token(self, token: Token) -> bool:
        """Check if a token can flow through this arc based on the guard function.
        
        Args:
            token: The token to check
            
        Returns:
            bool: True if the token can flow through this arc, False otherwise
            
        Raises:
            TypeError: If token is not an instance of Token
        """
        with self._lock:
            if not isinstance(token, Token):
                raise TypeError(f"Expected Token, got {type(token).__name__}")
            
            # If no guard is set, all tokens are accepted
            if self._guard is None:
                return True
                
            try:
                # Call the guard function and ensure it returns a boolean
                result = self._guard(token)
                if not isinstance(result, bool):
                    logger.warning(
                        f"Guard function returned non-boolean value: {result}"
                    )
                    return False
                return result
            except Exception as e:
                logger.warning(f"Error in arc guard function: {e}")
                return False
    
    def set_guard(self, guard: Optional[Callable[[Token], bool]] = None) -> None:
        """Set the guard function for this arc.
        
        The guard function determines which tokens can pass through this arc.
        It should take a Token as input and return a boolean indicating
        whether the token is allowed to pass (True) or not (False).
        
        Args:
            guard: A function that takes a Token and returns a boolean.
                   If None, all tokens will be allowed to pass.
                   
        Raises:
            PetriNetValidationError: If guard is not callable (and not None)
            
        Example:
            >>> def only_red_tokens(token):
            ...     return token.color == 'red'
            >>> arc = Arc("a1", "p1", "t1", "input")
            >>> arc.set_guard(only_red_tokens)  # Only red tokens can pass
        """
        with self._lock:
            if guard is None:
                self._guard = None
            elif callable(guard):
                # Store the guard function directly without wrapping
                # We'll handle the call and type checking in can_accept_token
                self._guard = guard
            else:
                raise PetriNetValidationError(
                    f"Guard must be a callable or None, got {type(guard).__name__}"
                )
    
    def __str__(self) -> str:
        """String representation of the arc.
        
        Note: Uses standard Petri net notation for readability:
        - Input arcs: place → transition
        - Output arcs: transition → place
        """
        if self._direction == "output":
            # Output arc: transition → place
            return f"Arc({self._transition_id} → {self._place_id}, weight={self._weight})"
        else:
            # Input arc: place → transition
            return f"Arc({self._place_id} → {self._transition_id}, weight={self._weight})"
            
    def __repr__(self) -> str:
        """Detailed string representation of the arc.
        
        Returns:
            A string in the format "<Arc id at 0x...>"
        """
        with self._lock:
            # Standard Python object representation with ID and memory address
            return f"<Arc {self._id} at {hex(id(self))}>"
    
    @classmethod
    def get_instance_count(cls) -> int:
        """Get the current number of arc instances."""
        with cls._instance_lock:
            return len(cls._instances)
    
    @classmethod
    def get_instances(cls) -> List['Arc']:
        """Get all existing arc instances."""
        with cls._instance_lock:
            return list(cls._instances)


class Transition:
    """Thread-safe implementation of a transition in the Petri net.
    
    A transition represents an activity that can move tokens from input places
    to output places when certain conditions are met.
    
    Thread Safety:
        All public methods are thread-safe using reentrant locks.
    
    Attributes:
        id: Unique identifier for the transition
        name: Human-readable name
        delay: Time delay for this transition (in simulation time units)
        priority: Priority for concurrent transition firing (higher fires first)
        guard: Optional function that determines if the transition can fire
        action: Optional function to execute when transition fires
    """
    __slots__ = (
        '_id', '_name', '_delay', '_priority', '_guard', '_action', 
        '_lock', '_enabled', '_last_fired', '_resource_id', '__weakref__'
    )
    
    # Class-level weak set to track all transition instances
    _instances: ClassVar[weakref.WeakSet['Transition']] = weakref.WeakSet()
    _instance_lock: ClassVar[RLock] = RLock()
    
    # Default guard and action functions to avoid creating new lambdas for each instance
    _DEFAULT_GUARD = staticmethod(lambda _: True)
    _DEFAULT_ACTION = staticmethod(lambda tokens: tokens)
    
    def __init__(
        self,
        id: str,
        name: str = "",
        delay: float = 0.0,
        priority: int = 0,
        guard: Optional[Callable[[Dict[str, List[Token]]], bool]] = None,
        action: Optional[Callable[[Dict[str, List[Token]]], Dict[str, List[Token]]]] = None,
        resource_id: Optional[str] = None,
    ):
        """Initialize a new transition.
        
        A transition represents an activity that can move tokens from input places
        to output places when certain conditions are met.
        
        Args:
            id: Unique identifier for the transition
            name: Human-readable name (defaults to id if empty)
            delay: Time delay for this transition (in simulation time units)
            priority: Priority for concurrent transition firing (higher fires first)
            guard: Optional function that determines if the transition can fire
            action: Optional function to transform input tokens to output tokens
            resource_id: Optional ID of the resource this transition is associated with
            
        Raises:
            PetriNetValidationError: If delay or priority are invalid
            
        Example:
            >>> def has_enough_tokens(tokens):
            ...     return len(tokens.get('input_place', [])) >= 2
            >>> 
            >>> def process_tokens(tokens):
            ...     # Consume 2 tokens, produce 1 result
            ...     consumed = tokens['input_place'][:2]
            ...     result = Token(color='processed')
            ...     return {'output_place': [result]}
            >>> 
            >>> t = Transition("t1", "Process Step", delay=1.0, priority=1,
            ...               guard=has_enough_tokens, action=process_tokens)
        """
        if not isinstance(delay, (int, float)) or delay < 0:
            raise PetriNetValidationError(
                f"Delay must be a non-negative number, got {delay!r}"
            )
            
        if not isinstance(priority, int):
            raise PetriNetValidationError(
                f"Priority must be an integer, got {type(priority).__name__}"
            )
            
        if guard is not None and not callable(guard):
            raise PetriNetValidationError(
                f"Guard must be a callable or None, got {type(guard).__name__}"
            )
            
        if action is not None and not callable(action):
            raise PetriNetValidationError(
                f"Action must be a callable or None, got {type(action).__name__}"
            )
            
        self._id = id
        self._name = name or id
        self._delay = float(delay)
        self._priority = int(priority)
        self._guard = guard or self._DEFAULT_GUARD
        self._action = action or self._DEFAULT_ACTION
        self._resource_id = resource_id
        self._lock = RLock()
        self._enabled = False
        self._last_fired: Optional[float] = None
        
        # Register instance
        with Transition._instance_lock:
            Transition._instances.add(self)
    
    @property
    def id(self) -> str:
        """Get the unique identifier of the transition."""
        return self._id
    
    @property
    def name(self) -> str:
        """Get the name of the transition."""
        return self._name
    
    @property
    def delay(self) -> float:
        """Get the delay of the transition."""
        return self._delay
    
    @delay.setter
    def delay(self, value: float) -> None:
        """Set the delay of the transition."""
        if value < 0:
            raise ValueError(f"Delay must be non-negative, got {value}")
        with self._lock:
            self._delay = float(value)
    
    @property
    def priority(self) -> int:
        """Get the priority of the transition."""
        return self._priority
    
    @priority.setter
    def priority(self, value: int) -> None:
        """Set the priority of the transition."""
        if not isinstance(value, int):
            raise TypeError(f"Priority must be an integer, got {type(value).__name__}")
        with self._lock:
            self._priority = value

    @property
    def resource_id(self) -> Optional[str]:
        """Get the resource ID this transition is associated with."""
        return self._resource_id
    
    @property
    def is_enabled(self) -> bool:
        """Check if the transition is currently enabled."""
        with self._lock:
            return self._enabled
    
    @property
    def last_fired(self) -> Optional[float]:
        """Get the last time the transition fired, or None if never fired."""
        with self._lock:
            return self._last_fired
    
    def set_guard(self, guard: Optional[Callable[[Dict[str, List[Token]]], bool]]) -> None:
        """Set the guard function for this transition.
        
        The guard function determines whether the transition can fire based on the
        available input tokens. It should return True if the transition can fire,
        or False otherwise.
        
        Args:
            guard: A function that takes a dictionary of {place_id: [tokens]} and
                  returns a boolean. If None, a default function that always
                  returns True will be used.
                  
        Raises:
            PetriNetValidationError: If guard is not callable (and not None)
            
        Example:
            >>> def has_min_tokens(tokens):
            ...     # Require at least 2 tokens in 'input_place'
            ...     return len(tokens.get('input_place', [])) >= 2
            >>> 
            >>> t = Transition("t1")
            >>> t.set_guard(has_min_tokens)
        """
        if guard is not None and not callable(guard):
            raise PetriNetValidationError(
                f"Guard must be a callable or None, got {type(guard).__name__}"
            )
            
        with self._lock:
            self._guard = guard or self._DEFAULT_GUARD
    
    def set_action(self, action: Optional[Callable[[Dict[str, List[Token]]], Dict[str, List[Token]]]]) -> None:
        """Set the action function for this transition.
        
        The action function is called when the transition fires. It consumes input
        tokens and produces output tokens. The function should return a dictionary
        mapping output place IDs to lists of tokens to add to those places.
        
        Args:
            action: A function that takes a dictionary of {input_place_id: [tokens]}
                   and returns a dictionary of {output_place_id: [tokens]}.
                   If None, a default function that passes tokens through unchanged
                   will be used.
                   
        Raises:
            PetriNetValidationError: If action is not callable (and not None)
            
        Example:
            >>> def process_tokens(tokens):
            ...     # Simple action that consumes tokens from 'in_place' and
            ...     # produces a single processed token to 'out_place'
            ...     processed = Token(color='processed')
            ...     return {'out_place': [processed]}
            >>> 
            >>> t = Transition("t1")
            >>> t.set_action(process_tokens)
        """
        if action is not None and not callable(action):
            raise PetriNetValidationError(
                f"Action must be a callable or None, got {type(action).__name__}"
            )
            
        with self._lock:
            self._action = action or self._DEFAULT_ACTION
    
    def get_delay(self, tokens: Dict[str, List[Token]]) -> float:
        """Get the delay for this transition, can be overridden for dynamic delays.

        Args:
            tokens: Dictionary of input place IDs to tokens being consumed

        Returns:
            Delay time in simulation units
        """
        with self._lock:
            return self._delay
    
    def can_fire(self, tokens: Dict[str, List[Token]]) -> bool:
        """Check if the transition can fire with the given input tokens.
        
        Args:
            tokens: Dictionary of input place IDs to tokens being consumed
            
        Returns:
            bool: True if the transition can fire, False otherwise
            
        Raises:
            TypeError: If tokens is not a dictionary
        """
        if not isinstance(tokens, dict):
            raise TypeError(f"Expected dict, got {type(tokens).__name__}")
            
        with self._lock:
            try:
                return self._guard(tokens)
            except Exception as e:
                logger.warning(
                    f"Error in guard function for transition '{self._id}': {e}"
                )
                return False
    
    def execute_action(self, tokens: Dict[str, List[Token]]) -> Dict[str, List[Token]]:
        """Execute the transition's action with the given input tokens.
        
        Args:
            tokens: Dictionary of input place IDs to tokens being consumed
            
        Returns:
            Dictionary of output place IDs to lists of tokens to produce
            
        Raises:
            TypeError: If tokens is not a dictionary
            RuntimeError: If the action fails
        """
        if not isinstance(tokens, dict):
            raise TypeError(f"Expected dict, got {type(tokens).__name__}")
            
        with self._lock:
            try:
                result = self._action(tokens)
                self._last_fired = time.time()
                return result
            except Exception as e:
                raise RuntimeError(
                    f"Error in action function for transition '{self._id}': {e}"
                ) from e
    
    def set_enabled(self, enabled: bool) -> None:
        """Set the enabled state of the transition.
        
        This is used internally by the PetriNet class to track which transitions
        are currently enabled based on token availability.
        """
        with self._lock:
            self._enabled = enabled
    
    def __str__(self) -> str:
        with self._lock:
            return (
                f"Transition(id='{self._id}', name='{self._name}', "
                f"delay={self._delay}, priority={self._priority}, "
                f"enabled={self._enabled})"
            )
    
    def __repr__(self) -> str:
        return f"<Transition {self._id} at {id(self)}>"
    
    @classmethod
    def get_instance_count(cls) -> int:
        """Get the current number of transition instances."""
        with cls._instance_lock:
            return len(cls._instances)
    
    @classmethod
    def get_instances(cls) -> List['Transition']:
        """Get all existing transition instances."""
        with cls._instance_lock:
            return list(cls._instances)


class Event:
    """
    Represents a scheduled event in the simulation.
{{ ... }}

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
        priority: int = 0,
    ) -> None:
        self.time = time
        self.transition_id = transition_id
        self.tokens = tokens
        self.priority = priority
        self.id = str(uuid.uuid4())  # Unique ID for breaking ties in heapq

    def __lt__(self, other: "Event") -> bool:
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
    """A Colored Petri Net simulation engine for modeling and simulating distributed systems.
    
    This class implements a high-performance, thread-safe Colored Petri Net (CPN) simulator
    with support for:
    - Colored tokens with custom attributes
    - Thread-safe operations
    - Event-driven simulation with timing
    - Priority-based transition firing
    - Guard conditions on arcs and transitions
    - Custom transition actions
    - Incremental state updates for performance
    
    The CPN model consists of:
    - Places: Hold tokens representing system state
    - Transitions: Model activities that consume and produce tokens
    - Arcs: Connect places to transitions (input arcs) and transitions to places (output arcs)
    - Tokens: Colored data containers that flow through the net
    
    Example usage:
        ```python
        # Create a new Petri net
        net = PetriNet("my_net")
        
        # Add places
        p1 = Place("p1", "Input Place", capacity=10)
        p2 = Place("p2", "Output Place")
        net.add_place(p1)
        net.add_place(p2)
        
        # Add a transition
        t1 = Transition("t1", "Process Step", delay=1.0)
        net.add_transition(t1)
        
        # Connect with arcs
        net.add_arc(Arc("a1", "p1", "t1", "input"))
        net.add_arc(Arc("a2", "t1", "p2", "output"))
        
        # Add tokens and simulate
        net.add_token("p1", Token())
        stats = net.simulate(max_steps=10)
        ```
    
    Thread Safety:
        All public methods are thread-safe using reentrant locks.
    """
    
    # Special place IDs for source and sink
    SOURCE_PLACE_ID: ClassVar[str] = "global_source_place"
    SINK_PLACE_ID: ClassVar[str] = "global_sink_place"

    def __init__(self, name: str):
        """Initialize a new Petri net.
        
        A Petri net is a mathematical modeling language for the description of distributed systems.
        It consists of places, transitions, and arcs that connect them.
        
        Args:
            name: A descriptive name for the Petri net
            
        Raises:
            PetriNetValidationError: If the name is invalid
            
        Example:
            >>> net = PetriNet("MyPetriNet")
            >>> net.name
            'MyPetriNet'
            >>> # Use as a context manager for automatic cleanup
            >>> with PetriNet("temp_net") as net:
            ...     # Use the net...
            ...     pass  # net.cleanup() is called automatically
            
        Note:
            The PetriNet is initialized with two special places:
            - 'global_source_place': For injecting tokens into the system
            - 'global_sink_place': For consuming tokens from the system
        """
        if not isinstance(name, str):
            raise PetriNetValidationError(
                f"Name must be a string, got {type(name).__name__}"
            )
            
        name = name.strip()
        if not name:
            raise PetriNetValidationError("Name cannot be empty or whitespace")
            
        if not name[0].isalpha() and name[0] != '_':
            raise PetriNetValidationError(
                "Name must start with a letter or underscore"
            )
            
        if not all(c.isalnum() or c in '_-' for c in name):
            raise PetriNetValidationError(
                "Name can only contain alphanumeric characters, underscores, or hyphens"
            )
            
        self.name: str = name
        
        # Core Petri net components
        self.places: Dict[str, Place] = {}
        self.transitions: Dict[str, Transition] = {}
        self.arcs: Dict[str, Arc] = {}

        # Optimization structures
        # Maps transition_id to its input/output arcs for faster access
        self.input_arcs: Dict[str, List[Arc]] = defaultdict(list)
        self.output_arcs: Dict[str, List[Arc]] = defaultdict(list)
        
        # Maps place_id to set of transition_ids that consume from it
        self.place_to_input_transitions: Dict[str, Set[str]] = defaultdict(set)
        
        # Tracks places that changed in the last transition firing
        # Used for incremental enablement checking
        self._last_affected_places: Set[str] = set()

        # Simulation state
        self.event_queue: List[Event] = []  # Min-heap of pending events
        self.current_time: float = 0.0      # Current simulation time
        self.is_running: bool = False       # Simulation running flag
        self._lock = threading.RLock()      # Reentrant lock for thread safety
        
        # Initialize with default source and sink places
        self._initialize_default_places()

    def _initialize_default_places(self) -> None:
        """Initialize the default source and sink places.
        
        This method is called during PetriNet initialization to create the
        special source and sink places that are available in every net.
        
        The source place (global_source_place) is where tokens can be injected
        into the system, and the sink place (global_sink_place) is where tokens
        can be consumed from the system.
        
        Both places have unlimited capacity by default.
        """
        # Create source place (unlimited capacity)
        source_place = Place(
            id=PetriNet.SOURCE_PLACE_ID,
            name="Source",
            capacity=None  # Unlimited capacity
        )
        
        # Create sink place (unlimited capacity)
        sink_place = Place(
            id=PetriNet.SINK_PLACE_ID,
            name="Global Sink",
            capacity=None  # Unlimited capacity
        )
        
        # Add to places dictionary
        # Note: We don't use add_place() here to avoid potential lock issues
        # during initialization. This is safe because __init__ is single-threaded.
        self.places[source_place.id] = source_place
        self.places[sink_place.id] = sink_place
        
        logger.debug("Initialized default source and sink places")
    
    def __str__(self) -> str:
        """Return a string representation of the Petri net.
        
        Returns:
            The name of the Petri net
        """
        return self.name

    def add_place(self, place: Place) -> None:
        """Add a place to the Petri net.
        
        Places hold tokens and represent states or conditions in the Petri net.
        Each place must have a unique ID within the net.
        
        Args:
            place: The Place instance to add to the net. Must be a valid Place object.
            
        Raises:
            TypeError: If place is not an instance of Place.
            PetriNetValidationError: If place has an invalid ID or conflicts with reserved names.
            PetriNetStateError: If the net is running or if a place with the same ID exists.
            
        Example:
            >>> net = PetriNet("my_net")
            >>> # Create and add a place with capacity 10
            >>> p1 = Place("p1", "Input Place", capacity=10)
            >>> net.add_place(p1)
            >>> 
            >>> # Try to add a duplicate place (raises PetriNetStateError)
            >>> try:
            ...     net.add_place(Place("p1", "Duplicate Place"))
            ... except PetriNetStateError as e:
            ...     print(f"Error: {e}")
            Error: Place with ID 'p1' already exists
            
        Note:
            The place is not copied; the net keeps a reference to the
            provided Place object. Modifying the place after adding it
            to the net may lead to unexpected behavior.
            
        Thread Safety:
            This method is thread-safe and uses a reentrant lock to prevent
            concurrent modifications to the net's state.
        """
        # Input validation
        if not isinstance(place, Place):
            raise TypeError(
                f"Expected Place, got {type(place).__name__}"
            )
            
        # Validate place ID
        if not isinstance(place.id, str) or not place.id.strip():
            raise PetriNetValidationError(
                f"Place ID must be a non-empty string, got {place.id!r}"
            )
            
        with self._lock:
            # Check simulation state
            if self.is_running:
                raise PetriNetStateError(
                    "Cannot modify net while simulation is running"
                )
                
            # Check for duplicate place
            if place.id in self.places:
                raise PetriNetStateError(
                    f"Place with ID '{place.id}' already exists"
                )
                
            # Check for reserved place IDs
            if place.id in (self.SOURCE_PLACE_ID, self.SINK_PLACE_ID):
                raise PetriNetValidationError(
                    f"Place ID '{place.id}' is reserved for internal use"
                )
                
            # Add the place to the net
            self.places[place.id] = place
            logger.debug(
                f"Added place '{place.id}' (name: '{place.name}') to net '{self.name}'"
            )

    def add_transition(self, transition: Transition) -> None:
        """Add a transition to the Petri net.
        
        Transitions represent events or actions that can occur in the Petri net.
        Each transition must have a unique ID within the net.
        
        Args:
            transition: The Transition instance to add to the net. Must be a valid Transition object.
            
        Raises:
            TypeError: If transition is not an instance of Transition.
            PetriNetValidationError: If transition has an invalid ID.
            PetriNetStateError: If the net is running or if a transition with the same ID exists.
            
        Example:
            >>> net = PetriNet("my_net")
            >>> # Create and add a transition with delay and priority
            >>> t1 = Transition("t1", "Process Step", delay=1.0, priority=1)
            >>> net.add_transition(t1)
            >>> 
            >>> # Try to add a duplicate transition (raises PetriNetStateError)
            >>> try:
            ...     net.add_transition(Transition("t1", "Duplicate Transition"))
            ... except PetriNetStateError as e:
            ...     print(f"Error: {e}")
            Error: Transition with ID 't1' already exists
            
        Note:
            The transition is not copied; the net keeps a reference to the
            provided Transition object. Modifying the transition after adding it
            to the net may lead to unexpected behavior.
            
        Thread Safety:
            This method is thread-safe and uses a reentrant lock to prevent
            concurrent modifications to the net's state.
        """
        # Input validation
        if not isinstance(transition, Transition):
            raise TypeError(
                f"Expected Transition, got {type(transition).__name__}"
            )
            
        # Validate transition ID
        if not isinstance(transition.id, str) or not transition.id.strip():
            raise PetriNetValidationError(
                f"Transition ID must be a non-empty string, got {transition.id!r}"
            )
            
        with self._lock:
            # Check simulation state
            if self.is_running:
                raise PetriNetStateError(
                    "Cannot modify net while simulation is running"
                )
                
            # Check for duplicate transition
            if transition.id in self.transitions:
                raise PetriNetStateError(
                    f"Transition with ID '{transition.id}' already exists"
                )
                
            # Add the transition to the net
            self.transitions[transition.id] = transition
            logger.debug(
                f"Added transition '{transition.id}' (name: '{transition.name}') to net '{self.name}'"
            )
            
            # Initialize arc lists for this transition
            self.input_arcs[transition.id] = []
            self.output_arcs[transition.id] = []
            
    def add_arc(self, arc: Arc) -> None:
        """Add an arc to the Petri net.
        
        Arcs connect places to transitions (input arcs) or transitions to places (output arcs).
        Each arc must have a unique ID within the net.
        
        Args:
            arc: The Arc instance to add to the net. Must be a valid Arc object.
            
        Raises:
            TypeError: If arc is not an instance of Arc.
            PetriNetValidationError: If arc has an invalid configuration.
            PetriNetStateError: If the net is running or if an arc with the same ID exists.
            
        Example:
            >>> net = PetriNet("test_net")
            >>> net.add_place(Place("p1", "Input Place"))
            >>> net.add_transition(Transition("t1", "Test Transition"))
            >>> arc = Arc("a1", "p1", "t1", "input")
            >>> net.add_arc(arc)
            
        Note:
            The arc is not copied; the net keeps a reference to the
            provided Arc object. Modifying the arc after adding it
            to the net may lead to unexpected behavior.
            
        Thread Safety:
            This method is thread-safe and uses a reentrant lock to prevent
            concurrent modifications to the net's state.
        """
        if not isinstance(arc, Arc):
            raise TypeError(f"Expected Arc, got {type(arc).__name__}")
            
        # Validate arc ID
        if not isinstance(arc.id, str) or not arc.id.strip():
            raise PetriNetValidationError(
                f"Arc ID must be a non-empty string, got {arc.id!r}"
            )
            
        with self._lock:
            # Check simulation state
            if self.is_running:
                raise PetriNetStateError(
                    "Cannot modify net while simulation is running"
                )
                
            # Check for duplicate arc
            if arc.id in self.arcs:
                raise PetriNetStateError(
                    f"Arc with ID '{arc.id}' already exists in the net"
                )
                
            # Validate that the place and transition exist
            if arc.place_id not in self.places:
                raise PetriNetValidationError(
                    f"Place with ID '{arc.place_id}' does not exist in the net"
                )
                
            if arc.transition_id not in self.transitions:
                raise PetriNetValidationError(
                    f"Transition with ID '{arc.transition_id}' does not exist in the net"
                )
                
            # Validate direction
            if arc.direction.lower() not in ("input", "output"):
                raise PetriNetValidationError(
                    f"Arc direction must be 'input' or 'output', got {arc.direction!r}"
                )
            
            # Add the arc to the net
            self.arcs[arc.id] = arc
            
            # Add to appropriate map for faster access
            if arc.direction.lower() == "input":
                self.input_arcs[arc.transition_id].append(arc)
                # Maintain reverse lookup for fast incremental enabled-transition search
                self.place_to_input_transitions[arc.place_id].add(arc.transition_id)
            else:  # output
                self.output_arcs[arc.transition_id].append(arc)
                
            logger.debug(
                f"Added {arc.direction} arc '{arc.id}' from place '{arc.place_id}' to "
                f"transition '{arc.transition_id}' in net '{self.name}'"
            )

    def add_token(self, place_id: str, token: Token) -> bool:
        """
        Add a token to a specific place in a thread-safe manner.

        Args:
            place_id: ID of the place to add the token to
            token: The token to add (must be a Token instance)

        Returns:
            bool: True if token was added successfully. Returns False if place doesn't exist.

        Raises:
            ValueError: If place_id is invalid
            TypeError: If token is not an instance of Token
        """
        if not isinstance(place_id, str) or not place_id:
            raise ValueError("place_id must be a non-empty string")
            
        if not isinstance(token, Token):
            raise TypeError(f"token must be an instance of Token, got {type(token).__name__}")
            
        with self._lock:
            if place_id not in self.places:
                logger.debug(f"Attempted to add token to non-existent place: {place_id}")
                return False
                
            return self.places[place_id].add_token(token)

    def find_enabled_transitions(
        self, candidates: Optional[Set[str]] = None
    ) -> List[Tuple[str, Dict[str, List[Token]]]]:
        """
        Find all transitions that can fire and their corresponding input tokens.

        Args:
            candidates: Optional set of transition IDs to evaluate. If None, all transitions
                in the Petri net are considered.

        Returns:
            List of tuples in the form (transition_id, {place_id: [tokens]}) sorted by transition
            priority (higher priority first).
        """
        enabled: List[Tuple[str, Dict[str, List[Token]]]] = []

        # Decide which transitions to inspect
        transition_iter = (
            candidates if candidates is not None else self.transitions.keys()
        )

        for transition_id in transition_iter:
            transition = self.transitions[transition_id]
            input_tokens_map = self._get_consumable_tokens(transition_id)

            if self._can_fire(transition, input_tokens_map):
                enabled.append((transition_id, input_tokens_map))

        # Sort by priority (higher values first)
        enabled.sort(
            key=lambda x: self.transitions[x[0]].priority, reverse=True
        )
        return enabled

    def _get_consumable_tokens(
        self, transition_id: str
    ) -> Dict[str, List[Token]]:
        """Get the tokens that can be consumed by a transition."""
        consumable_tokens = {}
        for arc in self.input_arcs[transition_id]:
            place = self.places[arc.place_id]
            # Sort tokens deterministically by their unique ID to ensure repeatable behaviour
            tokens = sorted(
                (
                    token
                    for token in place.get_tokens()
                    if arc.can_accept_token(token)
                ),
                key=lambda t: t.id,
            )
            consumable_tokens[arc.place_id] = list(tokens)
        return consumable_tokens

    def _can_fire(
        self, transition: Transition, input_tokens_map: Dict[str, List[Token]]
    ) -> bool:
        """Check if a transition can fire based on its guard and input tokens."""
        if not input_tokens_map:
            return False  # No input tokens

        for arc in self.input_arcs[transition.id]:
            place_id = arc.place_id
            tokens = input_tokens_map[place_id]
            if len(tokens) < arc.weight:
                return False  # Not enough tokens for this arc

        # Use the transition's public API to evaluate guards
        try:
            return transition.can_fire(input_tokens_map)
        except Exception:
            # Fallback to private attribute if needed for backward compatibility
            guard_fn = getattr(transition, "_guard", None)
            if callable(guard_fn):
                return bool(guard_fn(input_tokens_map))
            return True

    def schedule_transition(
        self, transition_id: str, input_tokens: Dict[str, List[Token]]
    ) -> None:
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
            priority=transition.priority,
        )

        heapq.heappush(self.event_queue, event)

    def fire_transition(
        self, transition_id: str, tokens: Dict[str, List[Token]]
    ) -> Dict[str, List[Token]]:
        """
        Fire a transition, moving tokens from input to output places.

        This method is now more robust:
        1. It verifies that tokens are still available before consuming them.
        2. It consumes exactly `arc.weight` tokens from each input place.
        3. The default action is to clone all consumed tokens to each output place.

        Args:
            transition_id: The ID of the transition to fire.
            tokens: A dictionary of available input tokens that enabled the transition.

        Returns:
            A dictionary of output tokens, or an empty dictionary if the transition could not fire.
        """
        transition = self.transitions[transition_id]
        logger.debug(
            f"Attempting to fire transition {transition.name} at time {self.current_time}"
        )

        # 1. Verify that the required tokens are still present and select them.
        consumed_tokens: Dict[str, List[Token]] = {}
        for arc in self.input_arcs[transition_id]:
            place = self.places[arc.place_id]
            available_tokens = tokens.get(arc.place_id, [])

            # Filter to tokens that are actually still in the place
            # Sort present tokens deterministically to ensure predictable consumption order
            present_tokens = sorted(
                (t for t in available_tokens if t in place.tokens),
                key=lambda tok: tok.id,
            )

            if len(present_tokens) < arc.weight:
                logger.debug(
                    f"Transition {transition.name} could not fire: not enough tokens in {place.name}. "
                    f"Required: {arc.weight}, Found: {len(present_tokens)}. Tokens may have been consumed by another transition."
                )
                # --- THIS IS THE FIX ---
                return {}  # Return an empty dictionary on failure
                # --- END OF FIX ---

            # Select the specific tokens to consume
            consumed_tokens[arc.place_id] = present_tokens[: arc.weight]

        # 2. Deterministically acquire all required place locks to avoid deadlocks
        involved_place_ids = sorted(
            {arc.place_id for arc in self.input_arcs[transition_id]}
            | {arc.place_id for arc in self.output_arcs[transition_id]}
        )

        with ExitStack() as stack:
            for pid in involved_place_ids:
                stack.enter_context(self.places[pid]._lock)

            # 2a. Consume the selected tokens from input places
            for place_id, tokens_to_consume in consumed_tokens.items():
                place = self.places[place_id]
                for token in tokens_to_consume:
                    place.remove_token(token)  # This should not fail now

            # 2b. Execute the transition's action to produce new tokens
            action_fn = getattr(transition, 'action', None)
            if not callable(action_fn):
                action_fn = getattr(transition, '_action', None)
            if callable(action_fn):
                output_tokens_map = action_fn(consumed_tokens)
            else:
                # Default action: clone all consumed tokens and pass them to each output place.
                all_consumed_cloned = [
                    token.clone()
                    for token_list in consumed_tokens.values()
                    for token in token_list
                ]
                output_tokens_map = {
                    arc.place_id: all_consumed_cloned
                    for arc in self.output_arcs[transition_id]
                }

            # 2c. Produce tokens in output places
            for arc in self.output_arcs[transition_id]:
                for token in output_tokens_map.get(arc.place_id, []):
                    if not self.places[arc.place_id].add_token(token):
                        logger.warning(
                            f"Place {self.places[arc.place_id].name} is full. Token {token.id} was dropped."
                        )

        # Record which places were affected so the simulation loop can do an
        # incremental enabled‐transition search.
        affected_places = set(consumed_tokens.keys()) | set(
            output_tokens_map.keys()
        )
        self._last_affected_places = affected_places

        logger.debug(f"Successfully fired transition {transition.name}")
        return output_tokens_map

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
                raise ValueError(
                    f"Transition {transition_id} does not exist or has no output arcs"
                )

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
                raise ValueError(
                    f"Transition {transition_id} does not exist or has no input arcs"
                )

            input_tokens = {}
            for arc in self.input_arcs[transition_id]:
                place = self.places[arc.place_id]
                input_tokens[place.id] = list(place.get_tokens())

            return input_tokens

    def count_tokens(self) -> int:
        """Return the total number of tokens currently in the net."""
        with self._lock:
            return sum(len(place.tokens) for place in self.places.values())

    def _schedule_enabled_transitions(
        self, affected_places: Optional[Set[str]] = None
    ):
        """Schedule transitions that are enabled.

        If affected_places is provided, only transitions that have an input arc
        from any of those places are considered.  This greatly reduces the search
        space compared with scanning *all* transitions on every step.
        """
        if affected_places is None:
            enabled = self.find_enabled_transitions()
        else:
            candidate_transitions: Set[str] = set()
            for pid in affected_places:
                candidate_transitions.update(
                    self.place_to_input_transitions.get(pid, set())
                )
            enabled = self.find_enabled_transitions(candidate_transitions)

        for transition_id, input_tokens in enabled:
            self.schedule_transition(transition_id, input_tokens)

    def simulate(
        self, max_steps: Optional[int] = None, max_time: Optional[float] = None
    ) -> Dict[str, Any]:
        """Run the simulation until a termination condition is met.
        
        This method executes the discrete-event simulation by processing events from the
        priority queue until either the maximum steps, maximum simulation time, or no more
        events are available. The simulation is thread-safe and can be stopped gracefully.

        The simulation loop:
        1. Processes events in chronological order from the event queue
        2. Fires enabled transitions
        3. Schedules newly enabled transitions
        4. Collects statistics about the simulation run

        Args:
            max_steps: Maximum number of transition firings (None for unlimited).
                Must be a positive integer if specified.
            max_time: Maximum simulation time (None for unlimited).
                Must be a positive float if specified.

        Returns:
            Dict containing simulation statistics with the following keys:
            - steps: Number of transitions fired
            - simulation_time: Total simulated time
            - real_time: Wall-clock time taken for the simulation
            - status: 'completed' if simulation finished normally, 'stopped' if interrupted
            - timestamp: ISO timestamp when simulation ended

        Raises:
            ValueError: If max_steps or max_time are invalid
            PetriNetStateError: If the simulation is already running or in an invalid state
            RuntimeError: For unexpected errors during simulation

        Example:
            >>> net = PetriNet("test_net")
            >>> # Set up your Petri net...
            >>> stats = net.simulate(max_steps=1000, max_time=60.0)
            >>> print(f"Simulation completed in {stats['real_time']:.2f} seconds")
            >>> print(f"Simulated {stats['simulation_time']} time units")
            >>> print(f"Fired {stats['steps']} transitions")

        Note:
            - The simulation can be stopped by setting self.is_running = False from another thread
            - This method is thread-safe and can be called from multiple threads
            - All time values are in seconds (float)
        """
        # Input validation
        if max_steps is not None and (not isinstance(max_steps, int) or max_steps <= 0):
            raise ValueError("max_steps must be a positive integer or None")
        if max_time is not None and (not isinstance(max_time, (int, float)) or max_time <= 0):
            raise ValueError("max_time must be a positive number or None")
        with self._lock:
            # Initialize variables that need cleanup
            steps = 0
            start_real_time = time.time()
            last_log_time = start_real_time
            last_step_count = 0
            
            try:
                # Check if simulation is already running
                if self.is_running:
                    raise PetriNetStateError("Simulation is already running")

                logger.debug(
                    f"Starting simulation with max_steps={max_steps}, max_time={max_time}"
                )
                
                self.is_running = True
                
                try:
                    # Initial check for enabled transitions at time 0
                    self._schedule_enabled_transitions()
                    
                    # Main simulation loop
                    while self.is_running:
                        # Check termination conditions
                        if max_steps is not None and steps >= max_steps:
                            logger.debug(f"Reached max steps ({max_steps}). Ending simulation.")
                            break

                        if max_time is not None and self.current_time >= max_time:
                            logger.debug(f"Reached max time ({max_time}). Ending simulation.")
                            break
                            
                        # Check for events
                        if not self.event_queue:
                            logger.debug("No more events in the queue. Simulation complete.")
                            break
                            
                        # Process next event
                        try:
                            event = heapq.heappop(self.event_queue)
                            self.current_time = event.time
                            
                            # Execute the transition with its input tokens map
                            success_map = self.fire_transition(event.transition_id, event.tokens)
                            success = bool(success_map)
                            if success:
                                steps += 1
                                # Schedule newly enabled transitions
                                # Only re-check transitions affected by the last firing
                                self._schedule_enabled_transitions(self._last_affected_places)
                                
                                # Periodic logging (every 1 second or 1000 steps)
                                current_time = time.time()
                                if (current_time - last_log_time >= 1.0 or 
                                    (last_step_count > 0 and steps - last_step_count >= 1000)):
                                    logger.debug(
                                        f"Simulation progress: step={steps}, "
                                        f"time={self.current_time:.2f}, "
                                        f"events={len(self.event_queue)}"
                                    )
                                    last_log_time = current_time
                                    last_step_count = steps
                                    
                        except Exception as e:
                            logger.error(f"Error processing event at time {self.current_time}: {e}")
                            raise RuntimeError(f"Error during simulation: {e}") from e
                            
                except Exception as e:
                    logger.error(f"Simulation error: {e}", exc_info=True)
                    self.is_running = False
                    raise
                    
                finally:
                    # Always ensure we clean up properly
                    if self.is_running:
                        logger.debug("Simulation stopped by user request")
                    
                    try:
                        # Collect final statistics
                        real_time = time.time() - start_real_time
                        stats = {
                            'steps': steps,
                            'simulation_time': float(self.current_time),
                            'real_time': real_time,
                            'status': 'completed' if not self.is_running else 'stopped',
                            'timestamp': datetime.datetime.now().isoformat(),
                            'steps_per_second': steps / real_time if real_time > 0 else float('inf'),
                            'simulation_speed': self.current_time / real_time if real_time > 0 else float('inf')
                        }
                        
                        logger.info(
                            f"Simulation completed: {steps} steps in {real_time:.2f}s "
                            f"(simulated {self.current_time:.2f} time units, "
                            f"{stats['steps_per_second']:.1f} steps/s, "
                            f"{stats['simulation_speed']:.1f}x real-time)"
                        )
                        
                        return stats
                        
                    finally:
                        # Ensure simulation is always marked as not running
                        self.is_running = False
                        # Clear the event queue to prevent memory leaks
                        self.event_queue.clear()
                    
            except Exception as e:
                self.is_running = False
                if not isinstance(e, (ValueError, PetriNetStateError, RuntimeError)):
                    raise RuntimeError(f"Unexpected error during simulation: {e}") from e
                raise

    def cleanup_all(cls) -> None:
        """Deprecated placeholder; Token.cleanup_all exists on Token, not PetriNet."""
        logger.debug("PetriNet.cleanup_all is a no-op; use Token.cleanup_all instead")
    
    def cleanup(self) -> None:
        """Clean up Petri net resources (tokens, places, transitions, arcs)."""
        # Implementation provided later in this class; keep correct docstring only
    
    def __enter__(self):
        """Enable the use of the PetriNet as a context manager.
        
        Returns:
            self: The PetriNet instance
            
        Example:
            with PetriNet("my_net") as net:
                # Use the net...
                pass  # net.cleanup() is called automatically
        """
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Clean up resources when exiting the context manager.
        
        This ensures that cleanup() is called even if an exception occurs.
        """
        self.cleanup()
        
    def __del__(self) -> None:
        """Clean up resources when the token is garbage collected.
        
        Note:
            Relying on __del__ for cleanup is not recommended as it's not guaranteed
            to be called in all situations. Prefer using the context manager pattern
            or calling cleanup() explicitly.
        """
        try:
            self.cleanup()
        except Exception as e:
            # Log but don't raise exceptions in __del__
            logger.debug(f"Error in PetriNet.__del__: {e}", exc_info=True)

    def cleanup(self) -> None:
        """Clean up all resources used by the Petri net.
        
        This method should be called when the Petri net is no longer needed to ensure
        proper cleanup of all resources, including tokens, places, transitions, and arcs.
        After calling this method, the Petri net should not be used anymore.
        
        This method is idempotent and can be called multiple times safely.
        
        Example:
            >>> net = PetriNet("test_net")
            >>> # Use the net...
            >>> net.cleanup()  # Clean up resources when done
            
        Note:
            This method is automatically called when the Petri net is used as a context manager.
        """
        with self._lock:
            try:
                # Stop any running simulation
                self.is_running = False
                
                # Clear all tokens from places first (must use the place API to clear internal set)
                for place in self.places.values():
                    try:
                        place.clear_tokens()
                    except Exception as e:
                        logger.warning(f"Error clearing tokens from place {place.id}: {e}", exc_info=True)
                
                # Clear all collections
                self.places.clear()
                self.transitions.clear()
                self.arcs.clear()
                self.input_arcs.clear()
                self.output_arcs.clear()
                self.place_to_input_transitions.clear()
                self._last_affected_places.clear()
                self.event_queue.clear()
                
                # Reset simulation state
                self.current_time = 0.0
                self.step_count = 0
                
                logger.debug(f"Successfully cleaned up Petri net '{self.name}'")
                
            except Exception as e:
                logger.error(f"Error during Petri net cleanup: {e}", exc_info=True)
                raise

    def reset(self) -> None:
        """Reset the simulation to its initial state.
        
        This method resets all simulation state, including:
        - Clears all tokens from places
        - Resets the event queue
        - Resets simulation time to 0.0
        - Stops any running simulation
        - Resets step counter
        - Clears any cached state
        
        Raises:
            PetriNetStateError: If the simulation is in an inconsistent state
            
        Example:
            >>> net = PetriNet("test_net")
            >>> # Run some simulation steps...
            >>> net.reset()  # Reset to initial state
            >>> net.current_time
            0.0
            >>> net.is_running
            False
            
        Note:
            This method is thread-safe and can be called at any time, even when
            the simulation is running. It will first stop the simulation if it's
            currently running.
        """
        with self._lock:
            try:
                # Stop the simulation if it's running
                if self.is_running:
                    logger.debug("Stopping simulation for reset")
                    self.is_running = False
                
                # Clear all tokens from places
                logger.debug("Clearing tokens from all places")
                for place_id, place in self.places.items():
                    try:
                        place.clear_tokens()
                    except Exception as e:
                        raise PetriNetStateError(
                            f"Failed to clear tokens from place '{place_id}': {e}"
                        ) from e

                # Reset simulation state
                logger.debug("Resetting simulation state")
                self.event_queue = []
                self.current_time = 0.0
                self.step_count = 0
                self._next_transition_index = 0
                self._last_affected_places = set()
                
                logger.debug(
                    f"Successfully reset simulation '{self.name}' to initial state"
                )
                
            except Exception as e:
                error_msg = f"Error resetting simulation: {e}"
                logger.error(error_msg, exc_info=True)
                if not isinstance(e, PetriNetStateError):
                    raise PetriNetStateError(error_msg) from e
                raise

    def get_transition_by_id(self, transition_id: str) -> Optional[Transition]:
        """Find a transition by its ID."""
        if transition_id in self.transitions:
            return self.transitions[transition_id]
        return None

    def get_place_token_mapping(
        self, transition_id: str
    ) -> Dict[str, List[Token]]:
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
        for transition in self.transitions.values():
            if transition.name == name:
                return transition
        return None

    def visualize(self) -> None:
        """
        Visualize the Petri net structure.

        This is a placeholder for future visualization capabilities.
        """
        logger.debug(f"Petri Net: {self.name}")
        logger.debug("Places:")
        for place in self.places.values():
            logger.info(
                f"  {place.id} ({place.name}): {len(place.tokens)} tokens"
            )

        logger.debug("Transitions:")
        for transition in self.transitions.values():
            logger.info(
                f"  {transition.id} ({transition.name}), Delay: {transition.delay}, Priority: {transition.priority}"
            )

        logger.debug("Arcs:")
        for arc in self.arcs.values():
            logger.info(
                f"  Arc {arc.id}: {arc.place_id} -> {arc.transition_id} ({arc.direction}, weight={arc.weight})"
            )
