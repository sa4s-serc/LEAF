"""
Constants for the LEAF-Cloud orchestrator.

This module contains constants that are used throughout the orchestrator package,
including default event handlers and other configuration values.
"""
from typing import List, Type

from .handlers import (
    BaseEventHandler,
    TokenCreationHandler,
    TokenDistributionHandler,
    TokenCompletionHandler,
    TransitionCompletionHandler,
)

# Default event handlers that are registered when the orchestrator starts
DEFAULT_EVENT_HANDLERS: List[Type[BaseEventHandler]] = [
    TokenCreationHandler,
    TokenDistributionHandler,
    TokenCompletionHandler,
    TransitionCompletionHandler,
]
