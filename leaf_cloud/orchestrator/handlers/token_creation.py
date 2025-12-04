"""
Handler for token creation events in the LEAF-Cloud Orchestrator.

This module provides the TokenCreationHandler class which is responsible for
handling token creation events in the simulation. It creates new tokens and
places them in the specified target places within the Petri net.
"""
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .base import BaseEventHandler
from .utils import TokenUtils

if TYPE_CHECKING:
    from ...orchestrator.models import TokenFlowLogEntry, Token
    from ...orchestrator import Orchestrator

logger = logging.getLogger(__name__)

class TokenCreationHandler(BaseEventHandler):
    """
    Handler for token creation events.
    
    This handler processes token creation events by creating new tokens and
    placing them in the specified target places within the Petri net.
    """
    
    def can_handle(self, event_type: str) -> bool:
        """
        Check if this handler can process the given event type.
        
        Args:
            event_type: The type of event to check.
            
        Returns:
            bool: True if the event type is 'token_creation', False otherwise.
            
        Example:
            >>> handler = TokenCreationHandler(orchestrator)
            >>> handler.can_handle("token_creation")
            True
            >>> handler.can_handle("other_event")
            False
        """
        return event_type == "token_creation"
    
    def handle(
        self, 
        event_data: Dict[str, Any]
    ) -> tuple[int, list["TokenFlowLogEntry"]]:
        """
        Handle a token creation event.
        
        This method creates a new token based on the event data and places it
        in the specified target place within the Petri net. The orchestrator typically
        directs initial workload tokens to 'global_source_place'.
        
        Args:
            event_data: Dictionary containing token information with optional keys:
                - target_place: Name of the place to add the token to (default: 'workload_queue')
                - id: Optional token ID (auto-generated if not provided)
                - type: Token type (default: 'REQUEST')
                - Any additional attributes to be stored with the token
            token_flow_log: List to append token flow events to.
            
        Returns:
            int: 1 if a token was successfully created and placed, 0 otherwise.
            
        Example:
            >>> event_data = {
            ...     "target_place": "queue1",
            ...     "type": "TASK",
            ...     "priority": 1
            ... }
            >>> handler.handle(event_data, [])
            1  # Returns 1 on success
        """
        if not self.orchestrator.petri_net:
            logger.warning("No Petri net available for token creation.")
            return 0, []
            
        # Create token using the Petri net Token class
        from ...core.petri_net import Token, TokenColor
        
        try:
            # Read batch count (default 1)
            try:
                count = int(event_data.get("count", 1))
            except (TypeError, ValueError):
                count = 1
            if count < 1:
                return 0, []

            token_type = event_data.get("token_type", event_data.get("type", "REQUEST"))
            # Convert token type to TokenColor
            if isinstance(token_type, str):
                try:
                    token_color = TokenColor(token_type.lower())
                except ValueError:
                    token_color = TokenColor.REQUEST
            else:
                token_color = TokenColor.REQUEST

            # Extract attributes from event data (exclude control/meta fields)
            attributes = {}
            for key, value in event_data.items():
                if key not in ["id", "type", "token_type", "creation_time", "target_place", "count"] and not key.startswith("_") and not callable(value):
                    attributes[key] = value

            # If end-to-end latency is enabled, assign a user_region and a default infrastructure_region when missing
            try:
                cfg = getattr(self.orchestrator, 'config', None)
                if cfg and hasattr(cfg, 'models') and hasattr(cfg.models, 'latency') and getattr(cfg.models.latency, 'end_to_end_enabled', False):
                    if 'user_region' not in attributes:
                        try:
                            from ...models.latency import LatencyModel
                            lm = LatencyModel(cfg.models.latency)
                            attributes['user_region'] = lm.assign_user_region()
                        except Exception:
                            pass
                    if 'infrastructure_region' not in attributes:
                        # Try to infer a representative region from the resource mapping
                        region_val = None
                        try:
                            mb = getattr(self.orchestrator, '_model_builder', None)
                            mapping = getattr(mb, 'resource_mapping', {}) if mb else {}
                            for res in mapping.values():
                                region_val = getattr(res, 'region', None)
                                if region_val:
                                    break
                        except Exception:
                            region_val = None
                        attributes['infrastructure_region'] = region_val or 'us-central1'
            except Exception:
                pass

            # Prefer explicit target, else map to the Petri Net's source if available
            target_place_name = event_data.get("target_place", "global_source_place")
            # Try name first
            entry_place = self.orchestrator.petri_net.get_place_by_name(target_place_name)
            # Fallback to common human-readable default name(s)
            if not entry_place and target_place_name in ("workload_queue", "request_queue"):
                entry_place = self.orchestrator.petri_net.get_place_by_name("Workload Queue") or \
                               self.orchestrator.petri_net.get_place_by_name("Request Queue")
                if not entry_place:
                    try:
                        from ...core.petri_net import PetriNet as _PN
                        entry_place = self.orchestrator.petri_net.places.get(getattr(_PN, 'SOURCE_PLACE_ID', 'global_source_place'))
                    except Exception:
                        entry_place = None
            # Fallback to ID lookup if available
            if not entry_place and hasattr(self.orchestrator.petri_net, 'places'):
                entry_place = self.orchestrator.petri_net.places.get(target_place_name)

            if not entry_place:
                logger.warning(
                    "Could not find target place '%s' for token creation.",
                    target_place_name
                )
                return 0, []

            created = 0
            logs: list["TokenFlowLogEntry"] = []

            # Create 'count' tokens; generate unique IDs using the orchestrator's sequence counter
            for _ in range(count):
                # If batch size > 1, ignore any provided 'id' to avoid collisions
                token_id = event_data.get("id") if count == 1 else None
                if not token_id:
                    token_id = f"token_{next(self.orchestrator._event_seq_counter)}"

                token = Token(
                    color=token_color,
                    attributes=attributes,
                    creation_time=self.orchestrator.current_time,
                    token_id=token_id
                )
                entry_place.add_token(token)
                logs.append(
                    TokenUtils.log_token_creation(
                        token=token,
                        place_name=entry_place.name,
                        timestamp=self.orchestrator.current_time
                    )
                )
                created += 1

            logger.debug(
                "TokenCreationHandler: created %d token(s) in place='%s' at t=%.3f",
                created,
                entry_place.name,
                self.orchestrator.current_time,
            )

            # Schedule enabled transitions once after batch creation
            try:
                if hasattr(self.orchestrator, "_schedule_enabled_transitions"):
                    self.orchestrator._schedule_enabled_transitions()
            except Exception:
                pass

            return created, logs

        except Exception as e:
            logger.error("Failed to create token(s): %s", str(e), exc_info=True)
            return 0, []
