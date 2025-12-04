"""
Handler for transition completion events in the LEAF-Cloud Orchestrator.

This module provides the TransitionCompletionHandler class which is responsible for
handling transition completion events in the simulation. It manages the consumption
of input tokens and production of output tokens when a transition fires.
"""
import logging
from typing import Any, Dict, List, TYPE_CHECKING

from leaf_cloud.leaf_types import TokenFlowLogEntry

from .base import BaseEventHandler
from .utils import TokenUtils

if TYPE_CHECKING:
    from ...orchestrator.models import TokenFlowLogEntry, Token, Transition
    from ...orchestrator import Orchestrator

logger = logging.getLogger(__name__)

class TransitionCompletionHandler(BaseEventHandler):
    """
    Handler for transition completion events.
    
    This handler processes transition completion events by consuming input tokens
    from input places and producing output tokens to output places according to
    the Petri net transition rules.
    """
    
    def can_handle(self, event_type: str) -> bool:
        """
        Check if this handler can process the given event type.
        
        Args:
            event_type: The type of event to check.
            
        Returns:
            bool: True if the event type is 'transition_completion', False otherwise.
            
        Example:
            >>> handler = TransitionCompletionHandler(orchestrator)
            >>> handler.can_handle("transition_completion")
            True
            >>> handler.can_handle("other_event")
            False
        """
        return event_type == "transition_completion"
    
    def handle(
        self, 
        event_data: Dict[str, Any]
    ) -> tuple[int, list["TokenFlowLogEntry"]]:
        """
        Handle a transition completion event.
        
        This method processes a transition completion by consuming the specified
        number of tokens from input places and producing new tokens in output places.
        
        Args:
            event_data: Dictionary containing transition information with keys:
                - transition: Name of the transition that completed (required)
                - input_places: List of input place names
                - output_places: List of output place names
                - consumed_tokens: Dict mapping input place names to token counts
                - new_tokens: Dict mapping output place names to token counts
            token_flow_log: List to append token flow events to.
            
        Returns:
            int: Number of tokens consumed, or 0 if an error occurred.
        """
        if not self.orchestrator.petri_net:
            logger.warning("No Petri net available for transition completion")
            return 0, []
            
        # Resolve transition by id or name
        transition_id = event_data.get("transition_id")
        transition_name = event_data.get("transition")
        petri_net = self.orchestrator.petri_net
        if not petri_net:
            logger.warning("No Petri net available for transition completion")
            return 0, []

        # We need the canonical transition id
        if not transition_id:
            # Attempt to find by name
            try:
                for tid, t in petri_net.transitions.items():
                    if t.name == transition_name:
                        transition_id = tid
                        break
            except Exception:
                pass
        if not transition_id:
            logger.warning("TransitionCompletionHandler: missing transition identifier")
            return 0, []

        # Optionally constrain to the token ids snapshot for determinism
        input_token_ids = event_data.get("input_token_ids", {})  # {place_id: [ids]}

        # Build a filtered input map using the snapshot of token ids if provided
        effective_input_tokens = {}
        try:
            for arc in petri_net.input_arcs.get(transition_id, []):
                place = petri_net.places.get(arc.place_id)
                if not place:
                    continue
                tokens = list(place.get_tokens())
                if input_token_ids and arc.place_id in input_token_ids:
                    # keep only tokens whose id is in snapshot, preserve order by id
                    allowed_ids = set(input_token_ids[arc.place_id])
                    tokens = [t for t in tokens if getattr(t, "id", None) in allowed_ids]
                effective_input_tokens[arc.place_id] = tokens
        except Exception as e:
            logger.debug("TransitionCompletionHandler: failed building input token map: %s", str(e))
            # Fallback: use PetriNet to compute consumable tokens
            try:
                for pid, toks in petri_net._get_consumable_tokens(transition_id).items():
                    effective_input_tokens[pid] = list(toks)
            except Exception:
                return 0, []

        # Fire the transition via the Petri Net engine
        output_tokens_map = petri_net.fire_transition(transition_id, effective_input_tokens)
        if not output_tokens_map:
            try:
                self.orchestrator._update_resource_busy_slots(effective_input_tokens, delta=-1)
            except Exception:
                pass
            return 0, []

        log_entries: List['TokenFlowLogEntry'] = []

        # Log consumption
        try:
            for pid, toks in effective_input_tokens.items():
                place_name = petri_net.places.get(pid).name if pid in petri_net.places else pid
                for token in toks[:]:
                    try:
                        log_entry = TokenUtils.log_token_consumption(
                            token=token,
                            source_place=place_name,
                            transition_name=transition_name or transition_id,
                            timestamp=self.orchestrator.current_time
                        )
                        log_entries.append(log_entry)
                        logger.debug(
                            "TransitionCompletionHandler: logged token_consumed for token=%s from place=%s",
                            getattr(token, 'id', 'unknown'),
                            place_name
                        )
                    except Exception as e:
                        logger.warning(
                            "TransitionCompletionHandler: failed to log token_consumed for token=%s: %s",
                            getattr(token, 'id', 'unknown'),
                            str(e)
                        )
        except Exception as e:
            logger.error("TransitionCompletionHandler: error iterating input tokens for consumption logging: %s", str(e), exc_info=True)

        # Log production and schedule completion for tokens reaching sink
        sink_place_id = getattr(petri_net, 'SINK_PLACE_ID', 'global_sink_place')
        logger.debug("TransitionCompletionHandler: sink_place_id=%s", sink_place_id)
        try:
            for out_pid, tokens in output_tokens_map.items():
                place_name = petri_net.places.get(out_pid).name if out_pid in petri_net.places else out_pid
                for token in tokens:
                    log_entries.append(TokenUtils.log_token_production(
                        token=token,
                        transition_name=transition_name or transition_id,
                        place_name=place_name,
                        timestamp=self.orchestrator.current_time
                    ))
                    # If token ended in sink, schedule token completion
                    is_sink = out_pid == sink_place_id or place_name.lower() in {"sink", "global sink", "global_sink_place"}
                    logger.debug("TransitionCompletionHandler: token=%s produced to place=%s (out_pid=%s), is_sink=%s", 
                                token.id, place_name, out_pid, is_sink)
                    if is_sink:
                        try:
                            self.orchestrator.schedule_event(
                                timestamp=self.orchestrator.current_time,
                                event_type="token_completion",
                                event_data={"token_id": token.id, "place": place_name},
                                priority=1,
                            )
                            logger.debug("TransitionCompletionHandler: scheduled token_completion for token=%s", token.id)
                        except Exception as e:
                            logger.warning("TransitionCompletionHandler: failed to schedule completion for token=%s: %s", token.id, str(e))
        except Exception:
            pass

        try:
            self.orchestrator._update_resource_busy_slots(effective_input_tokens, delta=-1)
        except Exception:
            pass

        # Log the transition completion
        try:
            log_entries.append(TokenFlowLogEntry(
                timestamp=self.orchestrator.current_time,
                event_type="transition_fired",
                details={
                    "transition": transition_name or transition_id,
                    "input_places": list(effective_input_tokens.keys()),
                    "output_places": list(output_tokens_map.keys()),
                }
            ))
        except Exception:
            pass

        # After firing, schedule any newly enabled transitions
        try:
            if hasattr(self.orchestrator, "_schedule_enabled_transitions"):
                self.orchestrator._schedule_enabled_transitions()
        except Exception:
            pass

        # Return number of consumed tokens
        total_consumed = sum(len(toks) for toks in effective_input_tokens.values())
        return total_consumed, log_entries
    
