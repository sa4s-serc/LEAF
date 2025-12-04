"""
Handler for token completion events in the LEAF-Cloud Orchestrator.

This module provides the TokenCompletionHandler class which is responsible for
handling token completion events in the simulation. It processes completed tokens,
updates metrics, and logs completion events.
"""
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .base import BaseEventHandler
from .utils import TokenUtils

if TYPE_CHECKING:
    from ...orchestrator.models import TokenFlowLogEntry, Token
    from ...orchestrator import Orchestrator

logger = logging.getLogger(__name__)

class TokenCompletionHandler(BaseEventHandler):
    """
    Handler for token completion events.
    
    This handler processes token completion events by updating token state,
    recording metrics, and logging completion information.
    """
    
    def can_handle(self, event_type: str) -> bool:
        """
        Check if this handler can process the given event type.
        
        Args:
            event_type: The type of event to check.
            
        Returns:
            bool: True if the event type is 'token_completion', False otherwise.
            
        Example:
            >>> handler = TokenCompletionHandler(orchestrator)
            >>> handler.can_handle("token_completion")
            True
            >>> handler.can_handle("other_event")
            False
        """
        return event_type == "token_completion"
    
    def handle(
        self, 
        event_data: Dict[str, Any]
    ) -> tuple[int, list["TokenFlowLogEntry"]]:
        """
        Handle a token completion event.
        
        This method processes a token completion event by finding the specified token,
        updating its completion time, logging the completion, and performing any
        necessary cleanup or metrics recording.
        
        Args:
            event_data: Dictionary containing completion information with keys:
                - token_id: ID of the completed token (required)
                - place: Name of the place where completion occurred (default: 'sink')
            token_flow_log: List to append token flow events to.
            
        Returns:
            int: 1 if a token was successfully completed, 0 otherwise.
            
        Example:
            >>> event_data = {
            ...     "token_id": "token_123",
            ...     "place": "sink"
            ... }
            >>> handler.handle(event_data, [])
            1  # Returns 1 on success
        """
        if not self.orchestrator.petri_net:
            logger.warning("No Petri net available for token completion")
            return 0, []
            
        token_id = event_data.get("token_id")
        place_name = event_data.get("place", "sink")
        
        if not token_id:
            logger.warning("No token_id provided in completion event")
            return 0, []
            
        place = self.orchestrator.petri_net.get_place_by_name(place_name)
        if not place:
            logger.warning("Could not find place '%s' for token completion", place_name)
            return 0, []
            
        # Find and remove the token (Place.tokens returns a copy Set[Token])
        logger.debug("TokenCompletionHandler: attempting completion for token_id=%s in place='%s' (count=%d)", token_id, place_name, place.token_count)
        token = None
        try:
            for t in place.tokens:  # type: ignore[assignment]
                if getattr(t, 'id', None) == token_id:
                    token = t
                    break
        except Exception as e:
            logger.error("TokenCompletionHandler: error iterating tokens in place '%s': %s", place_name, str(e), exc_info=True)
            return 0, []
        if token is not None:
            try:
                removed = place.remove_token(token)
                if not removed:
                    logger.warning("TokenCompletionHandler: token '%s' could not be removed from place '%s'", token_id, place_name)
                    return 0, []
            except Exception as e:
                logger.error("TokenCompletionHandler: exception removing token '%s' from place '%s': %s", token_id, place_name, str(e), exc_info=True)
                return 0, []
                
        if not token:
            logger.debug("Token '%s' not found in place '%s'", token_id, place_name)
            return 0, []
            
        # Update token completion time
        try:
            token.completion_time = self.orchestrator.current_time  # type: ignore[attr-defined]
        except Exception:
            pass
        
        # Log the completion using the utility class
        log_entry = TokenUtils.log_token_completion(
            token=token,
            place_name=place_name,
            timestamp=self.orchestrator.current_time
        )
        logger.debug("TokenCompletionHandler: completed token_id=%s at t=%.3f in place='%s'", token_id, self.orchestrator.current_time, place_name)

        # Perform any final processing
        self._process_completed_token(token, place_name)

        return 1, [log_entry]
    
    def _process_completed_token(self, token: 'Token', place_name: str) -> None:
        """
        Perform any final processing for a completed token.
        
        This method handles additional processing that should occur when a token
        completes its lifecycle, such as updating metrics or triggering dependent events.
        It also calculates end-to-end latency if enabled.
        
        Args:
            token: The completed token.
            place_name: The name of the place where the token completed.
            
        Note:
            This method can be extended to add custom processing logic for
            completed tokens, such as triggering dependent events or updating
            resource utilization statistics.
        """
        # Calculate processing times
        infrastructure_processing_time = token.completion_time - token.creation_time
        
        # Update metrics if metrics collector is available
        if hasattr(self.orchestrator, 'metrics') and self.orchestrator.metrics:
            try:
                # Record basic processing time
                self.orchestrator.metrics.record_token_processing_time(
                    token_id=token.id,
                    processing_time=infrastructure_processing_time,
                    place=place_name,
                    token_type=token.color.value if hasattr(token, 'color') else 'unknown'
                )
                
                # Calculate and record end-to-end latency if enabled
                attributes = getattr(token, 'attributes', {})
                user_region = attributes.get('user_region')
                
                if user_region:
                    try:
                        from ...config import LEAFCloudConfig
                        from ...models.latency import LatencyModel
                        
                        # Load configuration
                        config = LEAFCloudConfig()
                        
                        # Check if end-to-end latency is enabled
                        latency_config = None
                        if hasattr(config, 'models') and hasattr(config.models, 'latency'):
                            if hasattr(config.models.latency, 'end_to_end_enabled') and config.models.latency.end_to_end_enabled:
                                latency_config = config.models.latency
                        
                        if latency_config:
                            # Create latency model and calculate end-to-end latency
                            latency_model = LatencyModel(latency_config)
                            
                            # Get infrastructure region (try to infer from token or use default)
                            infrastructure_region = attributes.get('infrastructure_region', "us-central1")
                            
                            # Convert infrastructure latency from seconds to milliseconds
                            infrastructure_latency_ms = infrastructure_processing_time * 1000
                            
                            # Calculate end-to-end latency
                            end_to_end_result = latency_model.calculate_end_to_end_latency(
                                user_region=user_region,
                                infrastructure_region=infrastructure_region,
                                infrastructure_latency=infrastructure_latency_ms,
                                apply_stochastic_variation=True
                            )
                            
                            # Record end-to-end latency metrics
                            total_end_to_end_seconds = end_to_end_result["total_end_to_end_latency"] / 1000.0
                            user_to_infra_seconds = end_to_end_result["user_to_infra_latency"] / 1000.0
                            
                            # Record separate metrics for different latency components
                            self.orchestrator.metrics.record_value(
                                'end_to_end_latency_seconds', 
                                total_end_to_end_seconds,
                                tags={
                                    'user_region': user_region,
                                    'infrastructure_region': infrastructure_region,
                                    'token_type': token.color.value if hasattr(token, 'color') else 'unknown'
                                }
                            )
                            
                            self.orchestrator.metrics.record_value(
                                'user_to_infrastructure_latency_seconds',
                                user_to_infra_seconds,
                                tags={
                                    'user_region': user_region,
                                    'infrastructure_region': infrastructure_region
                                }
                            )
                            
                            logger.debug(
                                "Recorded end-to-end latency for token %s: user_to_infra=%.2fms, infrastructure=%.2fms, total=%.2fms",
                                token.id,
                                end_to_end_result["user_to_infra_latency"],
                                infrastructure_latency_ms,
                                end_to_end_result["total_end_to_end_latency"]
                            )
                            
                    except Exception as e:
                        logger.debug("Could not calculate end-to-end latency metrics: %s", str(e))
                        
            except Exception as e:
                logger.warning("Error recording token metrics: %s", str(e))
