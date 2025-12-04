"""
Handler for token distribution events in the LEAF-Cloud Orchestrator.

This module provides the TokenDistributionHandler class which is responsible for
handling token distribution events in the simulation. It manages the distribution
of tokens to different places in the Petri net based on configurable strategies.
"""
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .base import BaseEventHandler
from .utils import TokenUtils

if TYPE_CHECKING:
    from ...orchestrator.models import TokenFlowLogEntry, Token
    from ...orchestrator import Orchestrator

logger = logging.getLogger(__name__)

class TokenDistributionHandler(BaseEventHandler):
    """
    Handler for token distribution events.
    
    This handler processes token distribution events by routing tokens to
    specified places in the Petri net using configurable distribution strategies.
    """
    
    def can_handle(self, event_type: str) -> bool:
        """
        Check if this handler can process the given event type.
        
        Args:
            event_type: The type of event to check.
            
        Returns:
            bool: True if the event type is 'token_distribution', False otherwise.
            
        Example:
            >>> handler = TokenDistributionHandler(orchestrator)
            >>> handler.can_handle("token_distribution")
            True
            >>> handler.can_handle("other_event")
            False
        """
        return event_type == "token_distribution"
    
    def handle(
        self, 
        event_data: Dict[str, Any]
    ) -> tuple[int, list["TokenFlowLogEntry"]]:
        """
        Handle a token distribution event.
        
        This method processes a token distribution event by moving tokens from a source place
        to multiple target places according to the specified distribution map.
        
        Args:
            event_data: Dictionary containing distribution information with keys:
                - source_place: Name of the source place (required)
                - distribution: Dict mapping target place names to number of tokens to distribute
            token_flow_log: List to append token flow events to.
            
        Returns:
            int: Total number of tokens distributed, or 0 if an error occurred.
            
        Example:
            >>> event_data = {
            ...     "source_place": "source_queue",
            ...     "distribution": {
            ...         "worker_pool_1": 2,
            ...         "worker_pool_2": 1
            ...     }
            ... }
            >>> handler.handle(event_data, [])
            3  # Returns number of tokens distributed
        """
        if not self.orchestrator.petri_net:
            logger.warning("No Petri net available for token distribution")
            return 0, []
            
        # Get source place
        source_place_name = event_data.get("source_place")
        if not source_place_name:
            logger.warning("No source_place specified in distribution event")
            return 0, []
            
        source_place = self.orchestrator.petri_net.get_place_by_name(source_place_name)
        if not source_place:
            logger.warning("Source place '%s' not found", source_place_name)
            return 0, []        
        
        # Get distribution map
        distribution = event_data.get("distribution", {})
        if not distribution:
            logger.warning("No distribution specified in event data")
            return 0, []
        
        # Get target places
        target_places = {}
        for place_name, count in distribution.items():
            place = self.orchestrator.petri_net.get_place_by_name(place_name)
            if place:
                target_places[place] = count
            else:
                logger.warning("Target place '%s' not found", place_name)
                return 0, []
        
        # Distribute tokens
        total_distributed = 0
        log_entries = []

        for target_place, count in target_places.items():
            for _ in range(count):
                # Stop if no more tokens available in the source
                if getattr(source_place, "token_count", 0) == 0:
                    break

                # Select a token from the source using the public API
                token = next(iter(source_place.get_tokens()), None)
                if token is None:
                    break

                # Move the token using the public API to preserve thread safety
                removed = source_place.remove_token(token)
                if not removed:
                    # Token may have been taken concurrently; try next
                    continue

                try:
                    target_place.add_token(token)
                except Exception:
                    # If adding fails (e.g., capacity), attempt to return token to source
                    try:
                        source_place.add_token(token)
                    except Exception:
                        # As a last resort, drop the token back failure silently but log
                        logger.exception(
                            "Failed to return token to source place '%s' after target add failure",
                            source_place_name,
                        )
                    # Skip logging as distribution didn't occur
                    continue

                total_distributed += 1

                # Log the successful distribution
                log_entry = self._log_token_distribution(
                    token=token,
                    source_place=source_place_name,
                    target_place=target_place.name,
                )
                log_entries.append(log_entry)

        return total_distributed, log_entries
    

    
    def _log_token_distribution(
        self,
        token: 'Token',
        source_place: str,
        target_place: str
    ) -> 'TokenFlowLogEntry':
        """
        Log the distribution of a token.
        
        Args:
            token: The token being distributed.
            source_place: Name of the source place.
            target_place: Name of the target place.
            token_flow_log: List to append the distribution event to.
            
        Note:
            This method creates a standardized log entry for token distribution
            events, which can be used for monitoring and analysis.
            
        Example:
            >>> token = Token(id="t1", color=TokenColor.REQUEST,
            ...              attributes={"priority": "high"}, creation_time=0.0)
            >>> log = []
            >>> handler._log_token_distribution(token, "source_q", "target_q", log)
            >>> log[0]["event"]
            'token_distributed'
        """
        return {
            "time": self.orchestrator.current_time,
            "event": "token_distributed",
            "token_id": getattr(token, 'id', str(id(token))),
            "source_place": source_place,
            "target_place": target_place,
            "attributes": getattr(token, 'attributes', {})
        }
