"""
Utility functions and classes for event handlers.

This module provides common functionality used across different event handlers
to reduce code duplication and ensure consistent behavior. The TokenUtils class
provides methods for token lifecycle management and logging.
"""
import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

from leaf_cloud.leaf_types import TokenFlowLogEntry

if TYPE_CHECKING:
    from ..models import Token, TokenColor

logger = logging.getLogger(__name__)

class TokenUtils:
    """
    Utility class for common token-related operations.
    
    This class provides static methods for creating tokens and logging token
    lifecycle events in a consistent manner across the application.
    """
    
    @staticmethod
    def create_token(
        event_data: Dict[str, Any],
        seq_counter: Any,
        default_color: str = "REQUEST"
    ) -> Optional['Token']:
        """
        Create a new token from event data.
        
        Args:
            event_data: Dictionary containing token attributes. May include:
                - id: Optional custom token ID
                - type: Token type/color (defaults to default_color)
                - creation_time: Optional timestamp (defaults to current time)
                - user_region: Optional user region (for end-to-end latency)
                - Any additional attributes to store with the token
            seq_counter: Iterator for generating unique token IDs.
            default_color: Default token color if not specified in event_data.
            
        Returns:
            Token: A new Token instance with the specified attributes.
            None: If token creation fails.
        """
        from ..models import Token, TokenColor
        
        try:
            token_id = event_data.get("id", f"token_{next(seq_counter)}")
            token_type = event_data.get("type", default_color)
            token_color = TokenColor(token_type.lower())
            
            attributes = {}
            for key, value in event_data.items():
                if key not in ["id", "type", "creation_time", "target_place"] and not key.startswith("_") and not callable(value):
                    attributes[key] = value
            
            # Add end-to-end latency support: assign user region if enabled
            if not attributes.get("user_region"):
                user_region = TokenUtils._assign_user_region_if_enabled()
                if user_region:
                    attributes["user_region"] = user_region
                    logger.debug("Assigned user region '%s' to token %s", user_region, token_id)
                
            return Token(
                id=token_id,
                color=token_color,
                attributes=attributes,
                creation_time=event_data.get("creation_time", 0.0),
            )
        except Exception as e:
            logger.error("Failed to create token: %s", str(e), exc_info=True)
            return None
    
    @staticmethod
    def _assign_user_region_if_enabled() -> Optional[str]:
        """
        Assign a user region if end-to-end latency is enabled.
        
        This method checks if end-to-end latency is enabled in the configuration
        and assigns a user region based on the configured distribution.
        
        Returns:
            str: Assigned user region if end-to-end latency is enabled, None otherwise.
        """
        try:
            # Try to get the latency model configuration
            from ...config import LEAFCloudConfig
            from ...models.latency import LatencyModel
            
            # Load configuration (this could be optimized by caching)
            config = LEAFCloudConfig()
            
            # Check if end-to-end latency is enabled
            if hasattr(config, 'latency') and hasattr(config.latency, 'end_to_end_enabled'):
                if config.latency.end_to_end_enabled:
                    # Create a temporary latency model to use the region assignment logic
                    latency_model = LatencyModel(config.latency)
                    return latency_model.assign_user_region()
            elif hasattr(config, 'models') and hasattr(config.models, 'latency'):
                if hasattr(config.models.latency, 'end_to_end_enabled') and config.models.latency.end_to_end_enabled:
                    # Create a temporary latency model to use the region assignment logic
                    latency_model = LatencyModel(config.models.latency)
                    return latency_model.assign_user_region()
                    
        except Exception as e:
            logger.debug("Could not assign user region: %s", str(e))
            
        return None
    
    @staticmethod
    def log_token_creation(
        token: 'Token',
        place_name: str,
        timestamp: float
    ) -> TokenFlowLogEntry:
        """
        Log the creation of a token.

        Args:
            token: The token that was created.
            place_name: Name of the place where the token was created.
            timestamp: Current simulation time.

        Returns:
            A TokenFlowLogEntry object for the creation event.
        """
        return TokenFlowLogEntry(
            timestamp=timestamp,
            event_type="token_creation",
            details={
                "token_id": token.id,
                "place": place_name,
                "attributes": getattr(token, 'attributes', {}),
            }
        )
    
    @staticmethod
    def log_token_completion(
        token: 'Token',
        place_name: str,
        timestamp: float
    ) -> TokenFlowLogEntry:
        """
        Log the completion (final removal) of a token.

        Args:
            token: The token that was completed.
            place_name: Name of the place where the token was removed.
            timestamp: Current simulation time.

        Returns:
            A TokenFlowLogEntry object for the completion event.
        """
        attributes = getattr(token, 'attributes', {})
        infrastructure_latency = timestamp - token.creation_time
        
        # Calculate end-to-end latency if user region is available
        end_to_end_metrics = TokenUtils._calculate_end_to_end_metrics(
            token, infrastructure_latency
        )
        
        # Base metrics with infrastructure latency and explicit timestamps for latency model adapters
        metrics = {
            "total_time": infrastructure_latency,
            "infrastructure_latency": infrastructure_latency,
            "creation_time": getattr(token, 'creation_time', None),
            "completion_time": timestamp,
        }
        
        # Add end-to-end metrics if available
        if end_to_end_metrics:
            metrics.update(end_to_end_metrics)
            # Update total_time to reflect end-to-end latency when enabled
            if end_to_end_metrics.get("end_to_end_enabled"):
                # Convert total end-to-end latency from ms to seconds for consistency
                total_end_to_end_seconds = end_to_end_metrics["total_end_to_end_latency_ms"] / 1000.0
                metrics["total_time"] = total_end_to_end_seconds
                metrics["end_to_end_latency"] = total_end_to_end_seconds
                
                logger.debug(
                    "Token %s end-to-end completion: user_to_infra=%.2fms, infrastructure=%.2fms, total=%.2fms",
                    token.id,
                    end_to_end_metrics["user_to_infra_latency_ms"],
                    end_to_end_metrics["infrastructure_latency_ms"],
                    end_to_end_metrics["total_end_to_end_latency_ms"]
                )
        
        return TokenFlowLogEntry(
            timestamp=timestamp,
            event_type="token_completed",
            details={
                "token_id": token.id,
                "place": place_name,
                "attributes": attributes,
                "metrics": metrics
            }
        )
    
    @staticmethod
    def _calculate_end_to_end_metrics(
        token: 'Token', 
        infrastructure_latency: float
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate end-to-end latency metrics for a token if end-to-end latency is enabled.
        
        Args:
            token: The token to calculate metrics for
            infrastructure_latency: Infrastructure processing latency in seconds
            
        Returns:
            Dictionary with end-to-end metrics if enabled, None otherwise
        """
        try:
            attributes = getattr(token, 'attributes', {})
            user_region = attributes.get('user_region')
            
            if not user_region:
                return None
                
            # Try to get the latency model configuration
            from ...config import LEAFCloudConfig
            from ...models.latency import LatencyModel
            
            # Load configuration
            config = LEAFCloudConfig()
            
            # Check if end-to-end latency is enabled
            latency_config = None
            if hasattr(config, 'latency') and hasattr(config.latency, 'end_to_end_enabled'):
                if config.latency.end_to_end_enabled:
                    latency_config = config.latency
            elif hasattr(config, 'models') and hasattr(config.models, 'latency'):
                if hasattr(config.models.latency, 'end_to_end_enabled') and config.models.latency.end_to_end_enabled:
                    latency_config = config.models.latency
            
            if not latency_config:
                return None
                
            # Create latency model and calculate end-to-end latency
            latency_model = LatencyModel(latency_config)
            
            # Get infrastructure region (try to infer from token or use default)
            infrastructure_region = attributes.get('infrastructure_region')
            if not infrastructure_region:
                # Try to infer from token history or resource mapping on the fly
                try:
                    from ...simulation import get_current_simulation
                    sim = get_current_simulation()
                    mb = getattr(sim, "_model_builder", None) if sim else None
                    mapping = getattr(mb, "resource_mapping", {}) if mb else {}
                    if mapping:
                        # Heuristic: first resource with a region
                        for res in mapping.values():
                            maybe_region = getattr(res, "region", None)
                            if maybe_region:
                                infrastructure_region = maybe_region
                                break
                except Exception:
                    infrastructure_region = None
                # Final fallback
                infrastructure_region = infrastructure_region or "us-central1"
            
            # Convert infrastructure latency from seconds to milliseconds
            infrastructure_latency_ms = infrastructure_latency * 1000
            
            # Calculate end-to-end latency
            end_to_end_result = latency_model.calculate_end_to_end_latency(
                user_region=user_region,
                infrastructure_region=infrastructure_region,
                infrastructure_latency=infrastructure_latency_ms,
                apply_stochastic_variation=False  # Already applied during simulation
            )
            
            return {
                "user_region": user_region,
                "infrastructure_region": infrastructure_region,
                "user_to_infra_latency_ms": end_to_end_result["user_to_infra_latency"],
                "infrastructure_latency_ms": infrastructure_latency_ms,
                "total_end_to_end_latency_ms": end_to_end_result["total_end_to_end_latency"],
                "end_to_end_enabled": True
            }
            
        except Exception as e:
            logger.debug("Could not calculate end-to-end metrics: %s", str(e))
            return None
        
    @staticmethod
    def log_token_consumption(
        token: 'Token',
        source_place: str,
        transition_name: str,
        timestamp: float
    ) -> TokenFlowLogEntry:
        """
        Log the consumption of a token by a transition.

        Args:
            token: The token being consumed.
            source_place: Name of the source place.
            transition_name: Name of the consuming transition.
            timestamp: Current simulation time.

        Returns:
            A TokenFlowLogEntry object for the consumption event.
        """
        return TokenFlowLogEntry(
            timestamp=timestamp,
            event_type="token_consumed",
            details={
                "token_id": token.id,
                "place": source_place,
                "transition": transition_name,
                "attributes": getattr(token, 'attributes', {})
            }
        )
        
    @staticmethod
    def log_token_production(
        token: 'Token',
        transition_name: str,
        place_name: str,
        timestamp: float
    ) -> TokenFlowLogEntry:
        """
        Log the production of a token by a transition.

        Args:
            token: The token being produced.
            transition_name: Name of the producing transition.
            place_name: Name of the target place.
            timestamp: Current simulation time.

        Returns:
            A TokenFlowLogEntry object for the production event.
        """
        return TokenFlowLogEntry(
            timestamp=timestamp,
            event_type="token_produced",
            details={
                "token_id": token.id,
                "transition": transition_name,
                "place": place_name,
                "attributes": getattr(token, 'attributes', {})
            }
        )
