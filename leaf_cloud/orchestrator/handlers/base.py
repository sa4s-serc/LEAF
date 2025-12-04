"""
Base handler class for event processing in the LEAF-Cloud Orchestrator.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ...orchestrator.models import TokenFlowLogEntry

class BaseEventHandler(ABC):
    """Abstract base class for event handlers in the simulation."""
    
    def __init__(self, orchestrator: 'Orchestrator'):
        """Initialize the event handler with a reference to the orchestrator."""
        self.orchestrator = orchestrator
    
    @abstractmethod
    def can_handle(self, event_type: str) -> bool:
        """Check if this handler can process the given event type.
        
        Args:
            event_type: The type of the event to check.
            
        Returns:
            bool: True if this handler can process the event, False otherwise.
        """
        pass
    
    @abstractmethod
    def handle(
        self, 
        event_data: Dict[str, Any]
    ) -> tuple[int, list["TokenFlowLogEntry"]]:
        """Handle an event and return the number of tokens processed and any log entries.

        Args:
            event_data: The event data to process.

        Returns:
            A tuple containing:
            - The number of tokens processed.
            - A list of token flow log entries created during handling.
        """
        pass
