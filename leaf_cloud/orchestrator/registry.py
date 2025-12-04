"""
Event Handler Registry for the LEAF-Cloud framework.

This module provides a dedicated registry for managing event handlers, improving
modularity and decoupling handler management from the Orchestrator.
"""

import logging
import threading
from typing import Dict, List, Type

from .handlers.base import BaseEventHandler

logger = logging.getLogger(__name__)


class EventHandlerRegistry:
    """Manages the registration and lifecycle of event handlers."""

    def __init__(self):
        self._handlers: Dict[Type[BaseEventHandler], BaseEventHandler] = {}
        self._lock = threading.RLock()

    def add(self, handler: BaseEventHandler) -> None:
        """Adds an event handler to the registry, preventing duplicates."""
        if not isinstance(handler, BaseEventHandler):
            raise TypeError(f"Handler must be an instance of BaseEventHandler, not {type(handler).__name__}.")

        with self._lock:
            handler_type = type(handler)
            if handler_type in self._handlers:
                raise ValueError(f"Handler type {handler_type.__name__} is already registered.")

            self._handlers[handler_type] = handler
            logger.debug(f"Added event handler: {handler_type.__name__}")

    def remove(self, handler_type: Type[BaseEventHandler]) -> bool:
        """Removes an event handler of the specified type."""
        if not issubclass(handler_type, BaseEventHandler):
            raise TypeError(f"Handler type must be a subclass of BaseEventHandler.")

        with self._lock:
            if handler_type in self._handlers:
                del self._handlers[handler_type]
                logger.debug(f"Removed event handler: {handler_type.__name__}")
                return True
            return False

    def get_all(self) -> List[BaseEventHandler]:
        """Returns a list of all registered event handlers."""
        with self._lock:
            return list(self._handlers.values())

    def clear(self) -> None:
        """Removes all event handlers from the registry."""
        with self._lock:
            self._handlers.clear()
            logger.debug("Cleared all event handlers from the registry.")
