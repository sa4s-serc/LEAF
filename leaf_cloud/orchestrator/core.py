"""
Core Orchestrator class for the LEAF-Cloud framework.

This module contains the main Orchestrator class that coordinates the simulation
lifecycle, including model building, event processing, and metrics collection.
"""
import heapq
import itertools
import logging
import math
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TypeVar
from contextlib import nullcontext

from leaf_cloud.core.petri_net import PetriNet, Token
from leaf_cloud.core.resource import Resource

from ..config import LEAFCloudConfig
from ..terraform.model_builder import ModelBuilder
from ..leaf_types import SimulationStatus
from ..utils.results import SimulationResult
from .exceptions import (
    SimulationError,
    StateTransitionError
)
from .constants import DEFAULT_EVENT_HANDLERS
from .handlers.base import BaseEventHandler
from .registry import EventHandlerRegistry
from .metrics import MetricsCollector
from .models import (
    ProgressState,
    SimulationState
)

logger = logging.getLogger(__name__)

# Type variable for generic type hints
T = TypeVar('T')

# Type alias for event queue items
EventQueueItem = Tuple[float, int, int, str, Dict[str, Any]]  # timestamp, priority, seq, event_type, data


class Orchestrator:
    """
    Coordinates the simulation lifecycle, including model building, event processing,
    and metrics collection.
    """

    def __init__(self, config: Optional[LEAFCloudConfig] = None, debug: bool = False) -> None:
        """
        Initialize the Orchestrator with the given configuration.

        Args:
            config: The LEAF-Cloud configuration object. If None, a default
                   configuration will be used.
            debug: Enable debug mode with verbose logging.

        Raises:
            ValueError: If required configuration values are missing or invalid
        """
        logger.info("Initializing Orchestrator...")
        logger.debug("Creating new Orchestrator instance with config: %s", 
                   "default config" if config is None else "custom config")

        # Initialize configuration with validation
        if config is None:
            logger.debug("Using default LEAFCloudConfig")
            self.config = LEAFCloudConfig()
        else:
            logger.debug("Using provided LEAFCloudConfig")
            self.config = config

        # Validate required configuration sections
        logger.debug("Validating configuration...")
        self._validate_config()
        logger.debug("Configuration validation completed")

        self._event_seq_counter = itertools.count()
        
        # Store debug flag
        self._debug = debug
        
        # Initialize state with proper type hints
        self._state: SimulationState = SimulationState.INITIALIZING
        self._lock: threading.RLock = threading.RLock()
        self._stop_requested: bool = False
        
        # Initialize components with proper type hints
        self._model_builder: Optional[ModelBuilder] = None
        self._petri_net: Optional[PetriNet] = None
        self._workloads: List[Any] = []
        self._constraint_manager: Optional[Any] = None
        self._resource_state_index: Dict[str, Any] = {}
        self._resource_token_active: Dict[str, Dict[str, Dict[str, float]]] = {}
        self._resource_token_queues: Dict[str, Dict[str, deque[float]]] = {}
        self._resource_busy_time_window: Dict[str, float] = {}
        self._unschedulable_workloads: Dict[str, Dict[str, Any]] = {}
        self._last_utilization_sync_time: float = 0.0

        # Initialize metrics with validation
        logger.debug("Initializing MetricsCollector...")
        self._metrics = MetricsCollector(self.config)
        # ---------- Lazy‑load analytical models (avoids circular imports) ----------
        import importlib, logging as _lg
        _lg.getLogger(__name__).info("Loading analytical models…")
        try:
            EnergyModel  = importlib.import_module("leaf_cloud.models.energy").EnergyModel
            CarbonModel  = importlib.import_module("leaf_cloud.models.carbon").CarbonModel
            LatencyModel = importlib.import_module("leaf_cloud.models.latency").LatencyModel
            ScalingModel = importlib.import_module("leaf_cloud.models.scaling").ScalingModel

            self.energy_model   = EnergyModel(self.config.models.energy)   if hasattr(self.config.models, "energy")   else None
            self.carbon_model   = CarbonModel(self.config.models.carbon)   if hasattr(self.config.models, "carbon")   else None
            self.latency_model  = LatencyModel(self.config.models.latency) if hasattr(self.config.models, "latency")  else None
            self.scaling_model  = ScalingModel(self.config.models.scaling) if hasattr(self.config.models, "scaling")  else None
        except Exception as exc:        # noqa: broad‑except (import‑safety guard)
            _lg.getLogger(__name__).warning("Analytical models unavailable: %s", exc)
            self.energy_model = self.carbon_model = self.latency_model = self.scaling_model = None
        # Create output directory
        output_dir = getattr(self.config, 'output_dir', None)
        if output_dir is None:
            logger.warning("No output directory specified in config, using default")
            output_dir = Path("output")
            self.config.output_dir = output_dir
        else:
            # Ensure output_dir is a Path object
            output_dir = Path(output_dir)
            
        logger.info("Creating output directory: %s", output_dir)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            logger.debug("Successfully created output directory: %s", output_dir.absolute())
        except Exception as e:
            logger.error("Failed to create output directory %s: %s", output_dir, str(e), exc_info=True)
            raise
        
        # Initialize event handler registry
        logger.debug("Initializing event handler registry...")
        self._event_registry = EventHandlerRegistry()
        self._register_default_handlers()
        logger.debug("Registered %d default event handlers", 
                   len(self._event_registry.get_all()))
        
        # Initialize simulation state with proper type hints
        self._current_time: float = 0.0
        self._start_time: Optional[datetime] = None
        self._end_time: Optional[datetime] = None
        self._event_queue: List[EventQueueItem] = []
        
        # Initialize error tracking
        self._errors: List[Dict[str, Any]] = []
        self._retry_count: int = 0
        
        logger.info("Orchestrator initialized with configuration: %s", 
                  self.config.__class__.__name__)
        logger.debug("Initialization complete. Current state: %s", self._state.name)
        self._is_initialized = True

    # -----------------------------------------------------------------------
    # Helper accessors for metrics-aware scaling
    # -----------------------------------------------------------------------
    def _get_latest_metric_value(self, resource_id: str, metric_name: str) -> Optional[float]:
        """Fetch the latest recorded metric value for a resource."""
        try:
            stats = getattr(self._metrics, "resource_stats", {}).get(resource_id)
            if not stats:
                return None
            series = stats.metrics.get(metric_name)
            if not series:
                return None
            return float(series[-1].value)
        except Exception:
            return None

    def _get_recent_metric_average(
        self, resource_id: str, metric_name: str, window: int = 3
    ) -> Optional[float]:
        """Return a small moving average for a metric to reduce single-sample swings."""
        try:
            stats = getattr(self._metrics, "resource_stats", {}).get(resource_id)
            if not stats:
                return None
            series = stats.metrics.get(metric_name)
            if not series:
                return None
            recent = series[-window:] if window > 0 else series
            vals = [float(p.value) for p in recent if isinstance(p.value, (int, float))]
            if not vals:
                return None
            return sum(vals) / len(vals)
        except Exception:
            return None

    def _record_replica_metric(self, resource_id: str, replicas: float) -> None:
        """Append a replica-count metric for downstream reporting."""
        try:
            stats = getattr(self._metrics, "resource_stats", {}).get(resource_id)
            if not stats:
                return
            stats.add_replica_count(self._current_time, float(replicas))
        except Exception:
            pass

    # ---------------------------------------------------------------------------
    # Post-simulation analysis (re-usable by all exit paths)
    # ---------------------------------------------------------------------------
    def _run_post_simulation_analysis(self) -> Dict[str, Any]:
        """
        Convert raw MetricsCollector data into the shape each analytical
        model expects and run them in dependency order.
        """
        analysis: Dict[str, Any] = {}

        # ---------- adapter: ResourceStats → nested util dict ----------
        def _adapt(mc: MetricsCollector) -> Dict[str, Any]:
            util: Dict[str, list[dict]] = {}
            replicas: Dict[str, list[dict]] = {}
            for rid, stats in mc.resource_stats.items():
                util[rid] = [
                    {"time": m.time, "value": m.value}
                    for m in stats.metrics.get("utilization", [])
                ]
                replica_series = stats.metrics.get("replica_count")
                if replica_series:
                    replicas[rid] = [
                        {"time": m.time, "value": m.value}
                        for m in replica_series
                    ]
            raw = {"utilization": util}
            if replicas:
                raw["replica_count"] = replicas
            return {"raw_results": raw}

        adapted_metrics = _adapt(self._metrics)
        mapping = self.model_builder.resource_mapping if self.model_builder else {}

        # Inspect utilization for emptiness to avoid noisy warnings downstream
        util_dict = adapted_metrics.get("raw_results", {}).get("utilization", {})
        util_has_data = any(bool(points) for points in util_dict.values())

        if self.energy_model and util_has_data:
            analysis["energy"] = self.energy_model.calculate(adapted_metrics, mapping)
            if self.carbon_model:
                analysis["carbon"] = self.carbon_model.calculate(analysis["energy"], mapping)

        if self.latency_model and getattr(self._metrics, "token_flow_log", []):
            # Adapt TokenFlowLogEntry objects → dicts the latency model expects
            try:
                adapted_logs: list[dict] = []
                for ev in self._metrics.token_flow_log:
                    if isinstance(ev, dict):
                        # Normalize event key
                        evt = ev.get("event") or ev.get("event_type") or ev.get("type") or "token_event"
                        details = ev.get("details", {}) or {}
                        adapted: dict = {
                            "timestamp": ev.get("timestamp"),
                            "event": evt,
                            "details": details,
                        }
                        # Elevate token payload to top-level expected by latency model
                        # Prefer explicit 'token' key if present in details; otherwise synthesize from fields
                        if "token" in details and isinstance(details["token"], dict):
                            adapted["token"] = details["token"]
                        elif evt == "token_completed":
                            try:
                                adapted["token"] = {
                                    "id": details.get("token_id"),
                                    "creation_time": details.get("metrics", {}).get("creation_time", details.get("creation_time")),
                                    "completion_time": details.get("metrics", {}).get("completion_time", details.get("completion_time", ev.get("timestamp"))),
                                    # Optional context
                                    "user_region": details.get("attributes", {}).get("user_region"),
                                    "infrastructure_region": details.get("attributes", {}).get("infrastructure_region"),
                                }
                            except Exception:
                                pass
                        adapted_logs.append(adapted)
                    # Also consider our internal TokenFlowLogEntry dataclass
                    elif hasattr(ev, 'event_type'):
                        details = getattr(ev, 'details', {}) or {}
                        evt = getattr(ev, 'event_type', None) or details.get('event') or 'token_event'
                        adapted = {
                            "timestamp": getattr(ev, 'timestamp', None),
                            "event": evt,
                            "details": details,
                        }
                        if evt == "token_completed":
                            try:
                                adapted["token"] = {
                                    "id": details.get("token_id"),
                                    "creation_time": details.get("metrics", {}).get("creation_time", details.get("creation_time")),
                                    "completion_time": details.get("metrics", {}).get("completion_time", getattr(ev, 'timestamp', None)),
                                    "user_region": details.get("attributes", {}).get("user_region"),
                                    "infrastructure_region": details.get("attributes", {}).get("infrastructure_region"),
                                }
                            except Exception:
                                pass
                        adapted_logs.append(adapted)
                    else:
                        # Pydantic model or object with attributes
                        try:
                            data = ev.model_dump()  # pydantic v2
                        except Exception:
                            try:
                                data = ev.dict()  # pydantic v1
                            except Exception:
                                data = {
                                    "timestamp": getattr(ev, "timestamp", None),
                                    "event_type": getattr(ev, "event_type", None),
                                    "details": getattr(ev, "details", {}),
                                }
                        evt = data.get("event") or data.get("event_type") or data.get("type") or "token_event"
                        details = data.get("details", {}) or {}
                        adapted: dict = {
                            "timestamp": data.get("timestamp"),
                            "event": evt,
                            "details": details,
                        }
                        if "token" in details and isinstance(details["token"], dict):
                            adapted["token"] = details["token"]
                        elif evt == "token_completed":
                            try:
                                adapted["token"] = {
                                    "id": details.get("token_id"),
                                    "creation_time": details.get("metrics", {}).get("creation_time", details.get("creation_time")),
                                    "completion_time": details.get("metrics", {}).get("completion_time", data.get("timestamp")),
                                    "user_region": details.get("attributes", {}).get("user_region"),
                                    "infrastructure_region": details.get("attributes", {}).get("infrastructure_region"),
                                }
                            except Exception:
                                pass
                        adapted_logs.append(adapted)
                latency_input = {"token_flow_log": adapted_logs}
                analysis["latency"] = self.latency_model.calculate(
                    latency_input,
                    mapping,
                )
            except Exception as e:
                self._log_error("Failed to adapt token_flow_log for latency model", error=e)

        if self.scaling_model and util_has_data:
            analysis["scaling"] = self.scaling_model.calculate(adapted_metrics, mapping)

        if getattr(self, "_unschedulable_workloads", None):
            try:
                analysis["unschedulable_pods"] = list(self._unschedulable_workloads.values())
            except Exception:
                analysis["unschedulable_pods"] = []

        return analysis

    def _validate_config(self) -> None:
        """
        Validate the configuration object and its required attributes.
        
        Raises:
            ValueError: If required configuration values are missing or invalid
        """
        # Check for required configuration sections
        required_sections = [
            'simulation', 'logging', 'metrics', 'models'
        ]
        
        for section in required_sections:
            if not hasattr(self.config, section):
                raise ValueError(
                    f"Missing required configuration section: {section}"
                )
            
            # Ensure the section is not empty if it's required
            section_value = getattr(self.config, section, None)
            if section_value is None:
                raise ValueError(
                    f"Configuration section '{section}' cannot be None"
                )
        
        # Check for workload configuration under simulation
        if not hasattr(self.config.simulation, 'workload'):
            raise ValueError(
                "Missing required configuration section: simulation.workload"
            )
        
        if self.config.simulation.workload is None:
            raise ValueError(
                "Configuration section 'simulation.workload' cannot be None"
            )
        
        # Validate simulation parameters
        if not hasattr(self.config.simulation, 'duration') or self.config.simulation.duration <= 0:
            raise ValueError(
                "Simulation duration must be a positive number"
            )
            
        if not hasattr(self.config.simulation, 'time_step') or self.config.simulation.time_step <= 0:
            raise ValueError(
                "Simulation time step must be a positive number"
            )
        
        # Log successful validation
        logger.debug("Configuration validation successful")
    
    def cleanup(self):
        """Clean up resources used by the orchestrator.
        
        This method should be called when the orchestrator is no longer needed
        to ensure proper cleanup of resources. It's safe to call this method
        multiple times.
        """
        if not self._is_initialized:
            return
            
        logger.debug("Cleaning up Orchestrator resources")
        
        # Stop any running simulation
        if getattr(self, "_state", None) == SimulationState.RUNNING:
            try:
                self.stop()
            except Exception as e:
                logger.warning("Error while stopping simulation during cleanup: %s", str(e))
        
        # Clear event handlers
        with self._lock:
            self._event_registry.clear()
            
            # Clear other resources
            self._petri_net = None
            self._model_builder = None
            self._workloads = []
            self._constraint_manager = None
            
            # Clear the event queue
            self._event_queue.clear()
            
            # Reset state
            self._state = SimulationState.INITIALIZING
            self._current_time = 0.0
            self._errors = []
            self._retry_count = 0
            self._is_initialized = False
            
        logger.debug("Orchestrator cleanup completed")
        
    @property
    def petri_net(self) -> Optional[PetriNet]:
        """Get the current Petri net model."""
        return self._petri_net

    @petri_net.setter
    def petri_net(self, value: Any) -> None:
        """Set the Petri net model.
        
        Args:
            value: The Petri net model to set.
            
        Raises:
            ValueError: If the value is not a valid Petri net.
        """
        logger.debug("Setting Petri net model...")
        if value is not None:
            logger.debug("Validating Petri net model...")
            if not hasattr(value, 'places') or not hasattr(value, 'transitions'):
                error_msg = "Invalid Petri net: missing required attributes"
                logger.error(error_msg)
                raise ValueError(error_msg)
            
            places = len(value.places) if hasattr(value, 'places') else 0
            transitions = len(value.transitions) if hasattr(value, 'transitions') else 0
            logger.info("Setting Petri net with %d places and %d transitions", 
                      places, transitions)
        else:
            logger.debug("Clearing current Petri net model")
            
        self._petri_net = value
        logger.debug("Petri net model set successfully")

    @property
    def state(self) -> SimulationState:
        """Get the current simulation state."""
        return self._state
    
    @state.setter
    def state(self, new_state: SimulationState) -> None:
        """
        Set a new simulation state with validation.
        
        Args:
            new_state: The new state to transition to.
            
        Raises:
            StateTransitionError: If the transition is not allowed.
        """
        with self._lock:
            if not self._is_valid_transition(new_state):
                raise StateTransitionError(
                    f"Invalid state transition from {self._state.name} to {new_state.name}"
                )
            logger.debug("State transition: %s -> %s", self._state.name, new_state.name)
            self._state = new_state
    
    @property
    def current_time(self) -> float:
        """Get the current simulation time."""
        return self._current_time
    
    @property
    def metrics(self) -> MetricsCollector:
        """Get the metrics collector instance."""
        return self._metrics
    
    @property
    def model_builder(self) -> Any:
        """Get the model builder instance."""
        return self._model_builder
    
    def configure_workload(self, workload_config: Dict[str, Any]) -> None:
        """Configure workload for the simulation.
        
        Args:
            workload_config: Dictionary containing workload configuration
        """
        logger.debug("Configuring workload: %s", workload_config)
        
        # Store workload configuration
        self._workload_config = workload_config
        
        # Create workload objects based on configuration and register them so
        # scaling can derive request rate from self._workloads
        workload_type = (workload_config or {}).get('type', 'steady')
        params = (workload_config or {}).get('params', {}) or {}
        duration = workload_config.get('duration', params.get('duration'))

        def _normalize_burst_params(source: Dict[str, Any]) -> Dict[str, float]:
            if not isinstance(source, dict):
                source = {}
            base_rate = source.get('base_rate', source.get('rate', source.get('burst_base_rate', 10.0)))
            peak_rate = source.get('burst_rate', source.get('peak_rate', source.get('burst_peak_rate', base_rate)))
            burst_duration = source.get('burst_duration', source.get('duration', 5.0))
            idle_duration = source.get('idle_duration')
            burst_interval = source.get('burst_interval', source.get('interval'))

            try:
                base_rate = float(base_rate)
            except Exception:
                base_rate = 10.0

            try:
                peak_rate = float(peak_rate)
            except Exception:
                peak_rate = base_rate

            try:
                burst_duration = float(burst_duration)
            except Exception:
                burst_duration = 5.0

            if idle_duration is not None:
                try:
                    idle_duration = float(idle_duration)
                except Exception:
                    idle_duration = None

            if burst_interval is not None:
                try:
                    burst_interval = float(burst_interval)
                except Exception:
                    burst_interval = None

            if burst_interval is None and idle_duration is not None:
                burst_interval = burst_duration + idle_duration

            if burst_interval is None:
                burst_interval = max(burst_duration * 2.0, 1.0)

            if burst_interval <= burst_duration:
                burst_interval = burst_duration * 2.0

            if peak_rate < base_rate:
                peak_rate = base_rate

            normalized = dict(source)
            normalized.update({
                'base_rate': base_rate,
                'burst_rate': peak_rate,
                'peak_rate': peak_rate,
                'burst_duration': burst_duration,
                'burst_interval': burst_interval,
            })
            if 'duration' in source or 'burst_duration' not in source:
                normalized.setdefault('duration', burst_duration)
            if 'interval' in source or 'burst_interval' not in source:
                normalized.setdefault('interval', burst_interval)
            return normalized

        # Default name helper
        def _name(default: str) -> str:
            try:
                return str(workload_config.get('name') or params.get('name') or default)
            except Exception:
                return default

        created_workloads: list[Any] = []
        try:
            from leaf_cloud.core.workload import (
                SteadyWorkload,
                BurstWorkload,
                RandomWorkload,
                CyclicalWorkload,
                CSVWorkload,
                WorkloadMix,
                Workload,
            )
        
            if workload_type == 'steady':
                rate = float(params.get('rate', 100.0))
                created_workloads.append(
                    SteadyWorkload(rate=rate, duration=duration, name=_name('steady_workload'))
                )
                # Store minimal params for event generation helper
                self._workload_params = {'type': 'steady', 'rate': rate, 'duration': duration or 3600.0}

            elif workload_type == 'burst':
                normalized_params = _normalize_burst_params(params)
                created_workloads.append(
                    BurstWorkload(
                        base_rate=float(normalized_params.get('base_rate', 10.0)),
                        burst_rate=float(normalized_params.get('burst_rate', 50.0)),
                        burst_duration=float(normalized_params.get('burst_duration', 5.0)),
                        burst_interval=float(normalized_params.get('burst_interval', 30.0)),
                        duration=duration,
                        name=_name('burst_workload'),
                    )
                )
                normalized_config = dict(workload_config)
                normalized_config['params'] = normalized_params
                self._workload_params = normalized_config

            elif workload_type == 'random':
                created_workloads.append(
                    RandomWorkload(
                        base_rate=float(params.get('base_rate', params.get('rate', 10.0))),
                        min_rate=float(params.get('min_rate', 5.0)),
                        max_rate=float(params.get('max_rate', 50.0)),
                        change_interval=float(params.get('change_interval', 1.0)),
                        duration=duration,
                        name=_name('random_workload'),
                    )
                )
                self._workload_params = workload_config

            elif workload_type == 'cyclical':
                created_workloads.append(
                    CyclicalWorkload(
                        base_rate=float(params.get('base_rate', params.get('rate', 10.0))),
                        amplitude=float(params.get('amplitude', 5.0)),
                        period=float(params.get('period', 60.0)),
                        phase_shift=float(params.get('phase_shift', 0.0)),
                        duration=duration,
                        name=_name('cyclical_workload'),
                    )
                )
                self._workload_params = workload_config

            elif workload_type == 'csv':
                csv_path = params.get('csv_file') or params.get('file') or params.get('csv_path')
                if not csv_path:
                    raise ValueError("CSV workload requires 'csv_file' or 'file' path in params")
                created_workloads.append(
                    CSVWorkload(
                        csv_path=str(csv_path),
                        time_column=str(params.get('time_column', 'time')),
                        rate_column=str(params.get('rate_column', 'rate')),
                        base_rate=float(params.get('base_rate', 1.0)),
                        duration=duration,
                        name=_name('csv_workload'),
                    )
                )
                self._workload_params = workload_config

            elif workload_type == 'mix':
                sub_defs = params.get('workloads') or []
                sub_workloads: list[Workload] = []
                for idx, sub in enumerate(sub_defs):
                    try:
                        sub_type = sub.get('type', 'steady')
                        sub_params = sub.get('params', {})
                        sub_duration = sub.get('duration', sub_params.get('duration', duration))
                        if sub_type == 'steady':
                            sub_workloads.append(
                                SteadyWorkload(
                                    rate=float(sub_params.get('rate', 10.0)),
                                    duration=sub_duration,
                                    name=sub.get('name', f'steady_{idx}')
                                )
                            )
                        elif sub_type == 'burst':
                            normalized_sub = _normalize_burst_params(sub_params)
                            sub_workloads.append(
                                BurstWorkload(
                                    base_rate=float(normalized_sub.get('base_rate', 10.0)),
                                    burst_rate=float(normalized_sub.get('burst_rate', 50.0)),
                                    burst_duration=float(normalized_sub.get('burst_duration', 5.0)),
                                    burst_interval=float(normalized_sub.get('burst_interval', 30.0)),
                                    duration=sub_duration,
                                    name=sub.get('name', f'burst_{idx}')
                                )
                            )
                            sub.update({'params': normalized_sub})
                        elif sub_type == 'random':
                            sub_workloads.append(
                                RandomWorkload(
                                    base_rate=float(sub_params.get('base_rate', sub_params.get('rate', 10.0))),
                                    min_rate=float(sub_params.get('min_rate', 5.0)),
                                    max_rate=float(sub_params.get('max_rate', 50.0)),
                                    change_interval=float(sub_params.get('change_interval', 1.0)),
                                    duration=sub_duration,
                                    name=sub.get('name', f'random_{idx}')
                                )
                            )
                        elif sub_type == 'cyclical':
                            sub_workloads.append(
                                CyclicalWorkload(
                                    base_rate=float(sub_params.get('base_rate', sub_params.get('rate', 10.0))),
                                    amplitude=float(sub_params.get('amplitude', 5.0)),
                                    period=float(sub_params.get('period', 60.0)),
                                    phase_shift=float(sub_params.get('phase_shift', 0.0)),
                                    duration=sub_duration,
                                    name=sub.get('name', f'cyclical_{idx}')
                                )
                            )
                    except Exception as sub_err:
                        logger.warning("Failed to create sub-workload %d: %s", idx, str(sub_err))
                        continue
                if sub_workloads:
                    created_workloads.append(WorkloadMix(sub_workloads, name=_name('workload_mix')))
                self._workload_params = workload_config

            else:
                # Unknown type: fallback to steady
                rate = float(params.get('rate', 100.0))
                created_workloads.append(
                    SteadyWorkload(rate=rate, duration=duration, name=_name('steady_workload'))
                )
                self._workload_params = {'type': 'steady', 'rate': rate, 'duration': duration or 3600.0}

        except Exception as e:
            logger.warning("Failed to construct workload(s) from config: %s", str(e))
            created_workloads = []
            # Keep _workload_params so event generation still works
            self._workload_params = workload_config

        # Register created workloads so scaling can read them
        if created_workloads:
            # Replace any previously configured workloads
            self._workloads = created_workloads
            logger.info("Registered %d workload(s) for scaling", len(self._workloads))
        else:
            logger.debug("No workload instances were created; scaling will use fallbacks")
            
        logger.info("Workload configured: type=%s", workload_type)
    
    def _is_valid_transition(self, new_state: SimulationState) -> bool:
        """Check if a state transition is valid."""
        # Define valid state transitions
        valid_transitions = {
            SimulationState.INITIALIZING: {
                SimulationState.PARSING,
                SimulationState.FAILED,
            },
            SimulationState.PARSING: {
                SimulationState.BUILDING,
                SimulationState.FAILED,
            },
            SimulationState.BUILDING: {
                SimulationState.READY,
                SimulationState.FAILED,
            },
            SimulationState.READY: {
                SimulationState.RUNNING,
                SimulationState.FAILED,
            },
            SimulationState.RUNNING: {
                SimulationState.PAUSED,
                SimulationState.COMPLETED,
                SimulationState.STOPPED,
                SimulationState.FAILED,
            },
            SimulationState.PAUSED: {
                SimulationState.RUNNING,
                SimulationState.FAILED,
            },
            SimulationState.COMPLETED: {
                SimulationState.READY,
                SimulationState.EXPORTING,
            },
            SimulationState.STOPPED: {
                SimulationState.READY,
            },
            SimulationState.EXPORTING: {
                SimulationState.READY,
                SimulationState.FAILED,
            },
            SimulationState.FAILED: {
                SimulationState.READY,
            },
        }
        
        # Allow staying in the same state
        if new_state == self._state:
            return True
            
        # Check if the transition is valid
        return new_state in valid_transitions.get(self._state, set())
    
    def add_event_handler(self, handler: BaseEventHandler) -> None:
        """
        Add an event handler to the orchestrator's registry.

        Args:
            handler: The event handler to add.
        """
        self._event_registry.add(handler)
    
    def remove_event_handler(self, handler_type: Type[BaseEventHandler]) -> bool:
        """
        Remove an event handler of the specified type from the registry.

        Args:
            handler_type: The type of handler to remove.

        Returns:
            True if a handler was removed, False otherwise.
        """
        return self._event_registry.remove(handler_type)
    
    def _register_default_handlers(self) -> None:
        """Register default event handlers to the registry."""
        for handler_cls in DEFAULT_EVENT_HANDLERS:
            try:
                handler = handler_cls(self)
                self._event_registry.add(handler)
            except (ValueError, TypeError) as e:
                # Log and continue if a handler fails to register
                logger.error(
                    f"Failed to register default handler {handler_cls.__name__}: {e}",
                    exc_info=True
                )
    
    
    def _get_handler(self, event_type: str) -> Optional[BaseEventHandler]:
        """
        Get the first handler that can handle the given event type.

        Args:
            event_type: The type of event to handle.

        Returns:
            The first handler that can handle the event, or None if none found.
        """
        for handler in self._event_registry.get_all():
            if handler.can_handle(event_type):
                return handler
        return None
    
    def schedule_event(
        self,
        timestamp: float,
        event_type: str,
        event_data: Dict[str, Any],
        priority: int = 0
    ) -> None:
        """
        Schedule an event to be processed at a specific simulation time.
        
        This method adds an event to the simulation's event queue with the specified
        timestamp, type, and data. Events are processed in order of their timestamp,
        with events at the same timestamp processed in order of priority (lower numbers
        first) and then in the order they were scheduled.
        
        Args:
            timestamp: The simulation time at which the event should occur.
                      Must be a non-negative float.
            event_type: A string identifying the type of event.
                       Must be a non-empty string.
            event_data: A dictionary containing event-specific data.
                       Must be a dictionary (can be empty).
            priority: The priority of the event (lower numbers are processed first).
                     Must be an integer.
                     
        Raises:
            ValueError: If any input parameter is invalid.
            TypeError: If any input parameter has an incorrect type.
            
        Example:
            >>> orchestrator.schedule_event(
            ...     timestamp=10.5,
            ...     event_type="token_creation",
            ...     event_data={"color": "request", "count": 1},
            ...     priority=0
            ... )
        """
        # Input validation
        if not isinstance(timestamp, (int, float)) or math.isnan(timestamp):
            raise TypeError(f"timestamp must be a number, got {type(timestamp).__name__}")
            
        if timestamp < 0:
            raise ValueError(f"timestamp must be non-negative, got {timestamp}")
            
        if not isinstance(event_type, str):
            raise TypeError(f"event_type must be a string, got {type(event_type).__name__}")
            
        if not event_type.strip():
            raise ValueError("event_type must not be empty or whitespace only")
            
        if not isinstance(event_data, dict):
            raise TypeError(f"event_data must be a dictionary, got {type(event_data).__name__}")
            
        if not isinstance(priority, int):
            raise TypeError(f"priority must be an integer, got {type(priority).__name__}")
        
        # Log a warning for past-due events but still schedule them
        if timestamp < self._current_time:
            logger.warning(
                "Scheduling event in the past: %s at %.2f (current time: %.2f)",
                event_type, timestamp, self._current_time
            )
        
        # Generate a unique sequence number to ensure stable sorting
        # for events with the same timestamp and priority
        seq = next(self._event_seq_counter)
        
        with self._lock:
            # Add the event to the priority queue
            heapq.heappush(
                self._event_queue,
                (timestamp, priority, seq, event_type, event_data)
            )
            
            logger.debug(
                "Scheduled event: %s at %.2f (priority: %d, queue size: %d)",
                event_type,
                timestamp,
                priority,
                len(self._event_queue)
            )
    
    def process_next_event(self) -> bool:
        """
        Process the next event in the queue.
        
        This method retrieves and processes the next event from the priority queue.
        Events are processed in order of their timestamp, with ties broken by
        priority (lower numbers first) and then by the order they were scheduled.
        
        Returns:
            bool: 
                - True if an event was successfully processed
                - False if the queue is empty or the simulation is not in a running state
                
        Raises:
            RuntimeError: If the event queue is corrupted or contains invalid data
            
        Note:
            - This method is thread-safe and should be called from the main simulation loop
            - Events without handlers are logged as warnings but do not stop the simulation
            - Errors during event processing are caught, logged, and stored for later reference
        """
        # Check if we can process events in the current state
        if self._state != SimulationState.RUNNING and self._state != SimulationState.PAUSED:
            logger.debug(
                "Skipping event processing in state: %s", 
                self._state.name
            )
            return False
            
        with self._lock:
            # Check if there are any events to process
            if not self._event_queue:
                return False
                
            try:
                # Get the next event (smallest timestamp, then priority, then event_id)
                event = heapq.heappop(self._event_queue)
                
                # Validate event structure
                if len(event) != 5 or not isinstance(event[3], str) or not isinstance(event[4], dict):
                    raise RuntimeError("Invalid event structure in queue")
                    
                timestamp, _, _, event_type, event_data = event
                
                # Validate timestamp
                if not isinstance(timestamp, (int, float)) or math.isnan(timestamp):
                    raise ValueError(f"Invalid timestamp in event: {timestamp}")
                    
                # Update the current simulation time
                self._current_time = timestamp
                
                # Log event processing
                logger.debug(
                    "Processing event: %s at %.2f (queue size: %d)", 
                    event_type, 
                    timestamp, 
                    len(self._event_queue)
                )
                
                # Find a handler for this event type
                handler = self._get_handler(event_type)
                
                if handler:
                    try:
                        # Process the event with the handler
                        start_time = time.time()
                        tokens_processed, new_log_entries = handler.handle(event_data)
                        processing_time = time.time() - start_time
                        
                        if new_log_entries:
                            self._metrics.token_flow_log.extend(new_log_entries)

                        # Log slow event processing
                        if processing_time > 0.1:  # 100ms threshold
                            logger.warning(
                                "Slow event processing: %s took %.3f seconds",
                                event_type,
                                processing_time,
                                tokens_processed
                            )
                            
                    except Exception as e:
                        # Log the error but don't let it crash the simulation
                        logger.error(
                            "Error processing %s event: %s",
                            event_type, 
                            str(e),
                            exc_info=True
                        )
                        self._log_error(
                            f"Error processing {event_type} event",
                            error=e,
                            event_type=event_type,
                            event_data=event_data,
                        )
                        
                        # Update error metrics
                        if hasattr(self, '_metrics'):
                            self._metrics.increment_counter('event_errors')
                else:
                    logger.warning("No handler found for event type: %s", event_type)
                    if hasattr(self, '_metrics'):
                        self._metrics.increment_counter('unhandled_events')
                
                # Update metrics
                if hasattr(self, '_metrics'):
                    self._metrics.record_value('event_processing_time', processing_time)
                    self._metrics.increment_counter('events_processed')
                
                return True
                
            except Exception as e:
                # Catch any unexpected errors during event processing
                error_msg = f"Unexpected error processing event: {str(e)}"
                logger.critical(error_msg, exc_info=True)
                self._log_error(
                    "Critical error in event processing",
                    error=e,
                    event_type=event_type,
                    event_data=event_data,
                )
                
                # Transition to error state if we encounter a critical error
                try:
                    self._transition_state(SimulationState.FAILED)
                except Exception as state_error:
                    logger.critical(
                        "Failed to transition to FAILED state: %s", 
                        str(state_error),
                        exc_info=True
                    )
                
                # Re-raise to allow the simulation to handle the error
                raise RuntimeError(error_msg) from e
    
    def _log_error(
        self,
        message: str,
        error: Optional[Exception] = None,
        severity: str = "error",
        **extra: Any
    ) -> None:
        """
        Log an error, store it for later reference, and update error metrics.
        
        This method provides a centralized way to handle all error logging in the orchestrator.
        It ensures consistent error formatting, adds contextual information, and updates
        error metrics for monitoring and reporting.
        
        Args:
            message: A clear, descriptive error message. Should be a complete sentence.
            error: The exception that caused the error, if any.
            severity: The severity level of the error. One of: 'debug', 'info', 'warning', 
                    'error', 'critical'. Defaults to 'error'.
            **extra: Additional context to include with the error. Common keys include:
                   - event_type: Type of event being processed
                   - handler: Name of the handler processing the event
                   - current_time: Current simulation time
                   - state: Current simulation state
                   - Any other relevant context for debugging
                   
        Raises:
            ValueError: If severity is not one of the allowed values.
            
        Note:
            - Thread-safe: Uses a lock to ensure thread safety when updating error logs
            - Metrics: Updates error counters in the metrics collector if available
            - Error Storage: Maintains a bounded history of errors for debugging
            - Context: Automatically adds simulation state and timestamp to all errors
        """
        # Validate severity level
        valid_severities = {"debug", "info", "warning", "error", "critical"}
        if severity not in valid_severities:
            raise ValueError(
                f"Invalid severity '{severity}'. Must be one of: {valid_severities}"
            )
        
        # Get current time just once for consistency
        current_time = time.time()
        
        # Prepare error information with required fields
        error_info = {
            # Core fields
            "timestamp": current_time,
            "message": message,
            "severity": severity,
            
            # Simulation context
            "simulation_state": getattr(self, '_state', None),
            "simulation_time": getattr(self, '_current_time', None),
            "event_seq_counter": next(self._event_seq_counter),
            
            # Error details (if available)
            "error_type": error.__class__.__name__ if error else None,
            "error_message": str(error) if error else None,
            "error_traceback": traceback.format_exc() if error else None,
            
            # Additional context
            **extra
        }
        
        # Add thread information for debugging concurrency issues
        try:
            error_info["thread"] = threading.current_thread().name
        except Exception:
            pass  # Don't let thread info collection fail the error logging
        
        # Store the error in the error history
        try:
            with self._lock:
                # Keep only the most recent 1000 errors to prevent memory issues
                if len(self._errors) >= 1000:
                    self._errors.pop(0)
                self._errors.append(error_info)
        except Exception as e:
            # If we can't store the error, at least log it
            logger.critical(
                "Failed to store error in error history: %s",
                str(e),
                exc_info=True
            )
        
        # Update metrics if metrics collector is available
        # Only count error/critical severities to avoid noisy warning metrics
        if hasattr(self, '_metrics') and self._metrics is not None:
            try:
                if severity in {"error", "critical"}:
                    # Increment error counter
                    self._metrics.increment_counter(
                        f"error_{severity}",
                        tags={"error_type": error_info["error_type"] if error else "unknown"}
                    )
                    # Record error rate
                    self._metrics.record_histogram(
                        "error_rate",
                        value=1,
                        tags={"severity": severity}
                    )
            except Exception as e:
                logger.warning(
                    "Failed to update error metrics: %s",
                    str(e),
                    exc_info=True
                )
        
        # Only log verbose errors in debug mode
        if getattr(self, '_debug', False) or severity in {"critical"}:
            # Log the error at the appropriate level
            log_method = getattr(logger, severity, logger.error)
            log_context = {
                "simulation_state": str(getattr(self, '_state', 'UNKNOWN')),
                "simulation_time": getattr(self, '_current_time', None),
                "error_type": error_info["error_type"],
                "error_message": error_info["error_message"],
                **extra
            }
            
            # Include stack trace for errors and critical issues
            if severity in {"error", "critical"} and error is not None:
                log_context["traceback"] = traceback.format_exc()
            
            # Log the error with context
            log_method(
                "%s (state=%s, time=%.2f, severity=%s)",
                message,
                log_context["simulation_state"],
                log_context["simulation_time"] or 0.0,
                severity,
                extra={"extra": log_context}
            )
        
        # Also set logger level to DEBUG if debug mode is enabled
        if getattr(self, '_debug', False) and logger.level > logging.DEBUG:
            logger.setLevel(logging.DEBUG)
    
    def _process_event(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Process a single event with comprehensive error handling and validation.

        This method is responsible for dispatching events to their respective handlers
        with proper error handling, input validation, and performance monitoring.

        Args:
            event_type: The type of event to process. Must be a non-empty string.
            event_data: The event data. Must be a dictionary.

        Raises:
            ValueError: If event_type is empty or event_data is not a dictionary.
            TypeError: If event_type is not a string or event_data is not a dictionary.
        """
        # Input validation
        if not isinstance(event_type, str):
            raise TypeError(f"event_type must be a string, got {type(event_type).__name__}")
        if not event_type:
            raise ValueError("event_type cannot be empty")
        if not isinstance(event_data, dict):
            raise TypeError(f"event_data must be a dictionary, got {type(event_data).__name__}")

        logger.debug("Processing event: %s", event_type)
        
        handler = None
        try:
            handler = self._get_handler(event_type)
            
            if not handler:
                available_handlers = [h.__class__.__name__ for h in self._event_registry.get_all()]
                logger.warning(
                    "No handler found for event type: %s. Available handlers: %s",
                    event_type,
                    available_handlers
                )
                return 0, []
                
            if not hasattr(handler, 'handle') or not callable(handler.handle):
                error_msg = f"Handler for {event_type} does not implement required 'handle' method"
                logger.error(error_msg)
                self._log_error(error_msg, handler_type=type(handler).__name__)
                return 0, []

            start_time = time.time()
            tokens_processed = 0
            log_entries = []

            try:
                tokens_processed, log_entries = handler.handle(event_data)

                # Ensure token flow logs are captured
                if log_entries:
                    if not hasattr(self, '_token_flow_log'):
                        logger.debug("Initializing _token_flow_log")
                        self._token_flow_log = []
                    self._token_flow_log.extend(log_entries)
                    if hasattr(self, 'metrics') and self.metrics is not None:
                        try:
                            self.metrics.token_flow_log.extend(log_entries)
                        except Exception:
                            pass
                else:
                    # If handler didn't return logs but processed tokens, synthesize a minimal log entry
                    if tokens_processed and tokens_processed > 0:
                        try:
                            from leaf_cloud.leaf_types import TokenFlowLogEntry
                            synthetic = TokenFlowLogEntry(
                                timestamp=self._current_time,
                                event_type=event_type,
                                details={"tokens_processed": int(tokens_processed), **(event_data or {})},
                            )
                            if not hasattr(self, '_token_flow_log'):
                                self._token_flow_log = []
                            self._token_flow_log.append(synthetic)
                            if hasattr(self, 'metrics') and self.metrics is not None:
                                self.metrics.token_flow_log.append(synthetic)
                        except Exception:
                            pass

            except Exception as e:
                error_context = {
                    'event_type': event_type,
                    'handler': handler.__class__.__name__,
                    'event_data_keys': list(event_data.keys()) if event_data else [],
                    'current_time': self._current_time,
                    'state': self._state.name
                }
                error_msg = (
                    f"Error in {handler.__class__.__name__} handler for event '{event_type}': {str(e)}\n"
                    f"Context: {error_context}"
                )
                print(f"\n[ORCHESTRATOR] EXCEPTION: {error_msg}")
                logger.error(error_msg, exc_info=True)
                self._log_error(f"Error in {handler.__class__.__name__} handler", error=e, **error_context)

                if isinstance(e, (KeyboardInterrupt, SystemExit, MemoryError)):
                    raise
                return 0, []

            processing_time = time.time() - start_time

            if self.metrics:
                try:
                    self.metrics.record_event_processing_time(
                        event_type=event_type,
                        processing_time=processing_time,
                        tokens_processed=tokens_processed
                    )
                except Exception as e:
                    self._log_error(
                        "Failed to record event processing time",
                        error=e,
                        event_type=event_type,
                        processing_time=processing_time
                    )

            # Return the log entries that were added to _token_flow_log
            return tokens_processed, log_entries
            
        except Exception as e:
            logger.critical(
                "Critical error in event processing pipeline for event '%s': %s",
                event_type, str(e), exc_info=True
            )
            if isinstance(e, (KeyboardInterrupt, SystemExit, MemoryError)):
                raise
            self._log_error(
                "Critical error in event processing pipeline",
                error=e,
                event_type=event_type,
                handler_type=handler.__class__.__name__ if handler else 'None',
                current_time=self._current_time,
                state=self._state.name
            )
            return 0, []  # Always return a tuple

    # --- Petri Net integration helpers ---
    def _schedule_enabled_transitions(self) -> None:
        """Find enabled transitions in the Petri Net and schedule their completion events.

        This drives real token traversal. It inspects the Petri Net for transitions
        that can fire, computes each transition's delay, and enqueues a
        `transition_completion` event at current_time + delay with the input/output
        meta needed by the handler.
        """
        try:
            if not hasattr(self, "_petri_net") or self._petri_net is None:
                return

            enabled = self._petri_net.find_enabled_transitions()
            for transition_id, input_tokens in enabled:
                transition = self._petri_net.transitions.get(transition_id)
                if transition is None:
                    continue

                # Determine delay from transition, default to 0.0
                try:
                    delay = float(transition.get_delay(input_tokens))
                except Exception:
                    delay = float(getattr(transition, "delay", 0.0) or 0.0)

                fire_time = self._current_time + max(0.0, delay)

                # Build event payload describing intent to fire this transition.
                # The actual consumption/production is performed by the handler via PetriNet.fire_transition.
                input_place_names = [self._petri_net.places[pid].name for pid in input_tokens.keys() if pid in self._petri_net.places]
                output_place_names = [self._petri_net.places[arc.place_id].name for arc in self._petri_net.output_arcs.get(transition_id, []) if arc.place_id in self._petri_net.places]

                event_payload = {
                    "transition_id": transition_id,
                    "transition": transition.name,
                    "input_places": input_place_names,
                    "output_places": output_place_names,
                    # Provide a snapshot of token ids to help the handler be deterministic when firing
                    "input_token_ids": {
                        pid: [t.id for t in toks]
                        for pid, toks in input_tokens.items()
                    },
                }

                try:
                    self._update_resource_busy_slots(input_tokens, delta=1)
                except Exception:
                    pass

                self.schedule_event(
                    timestamp=fire_time,
                    event_type="transition_completion",
                    event_data=event_payload,
                    priority=0,
                )
        except Exception as e:
            # Never fail the simulation due to scheduling; log and continue with stack trace
            try:
                logger.exception("Failed to schedule enabled transitions: %s", str(e))
            except Exception:
                pass
            self._log_error("Failed to schedule enabled transitions", error=e, current_time=getattr(self, "_current_time", 0.0))
    
    def _update_metrics_if_needed(self) -> None:
        """
        Update metrics if enough time has passed since the last update.
        
        This ensures we don't update metrics too frequently, which could impact
        performance during long simulations.
        """
        if (self._current_time - self._last_metrics_update) >= self._metrics_update_interval:
            self._update_metrics()
    
    def _synchronize_resource_utilization_from_petri_net(self) -> None:
        """
        Mirror Petri net slot usage back into Resource objects so utilization,
        power, and energy metrics reflect actual token activity.
        """
        petri_net = getattr(self, "petri_net", None)
        model_builder = getattr(self, "_model_builder", None)
        if petri_net is None or model_builder is None:
            return

        resource_mapping = getattr(model_builder, "resource_mapping", None)
        if not resource_mapping:
            return

        current_time = getattr(self, "_current_time", 0.0)
        window = max(1e-9, current_time - self._last_utilization_sync_time)

        allocations_by_id: Dict[str, float] = {}
        capacity_by_id: Dict[str, float] = {}
        pending_network: List[Tuple[str, Resource]] = []

        service_targets_map: Dict[str, List[str]] = {}
        try:
            service_targets_map = getattr(model_builder, "service_targets", {}) or {}
        except Exception:
            service_targets_map = {}

        for resource_id, resource in resource_mapping.items():
            resource_name = getattr(resource, "name", None)
            if not resource_name:
                continue

            try:
                total_slots = float(getattr(resource, "_petri_slot_capacity", 0.0) or 0.0)
            except Exception:
                total_slots = 0.0

            if total_slots <= 0.0:
                place_id = f"ResourceState_{resource_name}"
                place = petri_net.places.get(place_id) if hasattr(petri_net, "places") else None
                if place is not None:
                    try:
                        total_slots = float(place.token_count)
                    except Exception:
                        try:
                            total_slots = float(len(place.tokens))
                        except Exception:
                            total_slots = 0.0

            busy_time = float(self._resource_busy_time_window.get(resource_name, 0.0))
            active_tokens = self._resource_token_active.get(resource_name, {})
            for token_id, info in active_tokens.items():
                last_time = info.get("last", info.get("start", current_time))
                delta_time = max(0.0, current_time - last_time)
                busy_time += delta_time
                info["last"] = current_time
            self._resource_busy_time_window[resource_name] = 0.0

            try:
                setattr(resource, "_petri_slot_capacity", float(total_slots))
            except Exception:
                pass

            network_targets = False
            if service_targets_map and resource_id in service_targets_map:
                network_targets = True
            elif getattr(resource, "attributes", None):
                targets_attr = resource.attributes.get("target_ids")
                if targets_attr:
                    network_targets = True
                    if resource_id not in service_targets_map:
                        try:
                            service_targets_map[resource_id] = list(dict.fromkeys(list(targets_attr)))
                        except Exception:
                            service_targets_map[resource_id] = list(targets_attr)

            if network_targets:
                pending_network.append((resource_id, resource))
                allocations_by_id[resource_id] = 0.0
                try:
                    capacity_by_id[resource_id] = float(getattr(resource, "capacity", 0.0) or 0.0)
                except Exception:
                    capacity_by_id[resource_id] = 0.0
                continue

            res_capacity = getattr(resource, "capacity", None)
            if isinstance(res_capacity, (int, float)) and total_slots > 0.0:
                utilization_ratio = busy_time / (window * total_slots)
                utilization_ratio = max(0.0, min(1.0, utilization_ratio))
                allocated_capacity = utilization_ratio * float(res_capacity)
            else:
                utilization_ratio = busy_time / max(window, 1e-9)
                utilization_ratio = max(0.0, min(1.0, utilization_ratio))
                allocated_capacity = utilization_ratio

            allocated_capacity = max(0.0, float(allocated_capacity))
            if isinstance(res_capacity, (int, float)):
                allocated_capacity = min(float(res_capacity), allocated_capacity)

            resource_lock = getattr(resource, "_lock", None)
            if resource_lock:
                try:
                    with resource_lock:
                        resource.allocated_capacity = float(allocated_capacity)
                except Exception:
                    continue
            else:
                resource.allocated_capacity = float(allocated_capacity)

            allocations_by_id[resource_id] = float(resource.allocated_capacity)
            try:
                capacity_by_id[resource_id] = float(res_capacity) if isinstance(res_capacity, (int, float)) else float(total_slots)
            except Exception:
                capacity_by_id[resource_id] = float(total_slots)

            try:
                resource.record_utilization(current_time)
            except Exception:
                pass

        if pending_network:
            for resource_id, resource in pending_network:
                target_ids = list(dict.fromkeys(service_targets_map.get(resource_id, [])))
                if not target_ids:
                    resource.allocated_capacity = 0.0
                    allocations_by_id[resource_id] = 0.0
                    try:
                        capacity_by_id[resource_id] = float(getattr(resource, "capacity", 0.0) or 0.0)
                    except Exception:
                        capacity_by_id[resource_id] = 0.0
                    try:
                        resource.record_utilization(current_time)
                    except Exception:
                        pass
                    continue

                total_allocated = sum(allocations_by_id.get(target_id, 0.0) for target_id in target_ids)
                inferred_capacity = sum(capacity_by_id.get(target_id, 0.0) for target_id in target_ids)

                try:
                    service_capacity = float(getattr(resource, "capacity", 0.0) or 0.0)
                except Exception:
                    service_capacity = 0.0

                if service_capacity <= 0.0:
                    service_capacity = max(1e-3, inferred_capacity)
                    try:
                        resource.capacity = service_capacity
                    except Exception:
                        pass

                allocated_capacity = min(service_capacity, total_allocated)

                resource_lock = getattr(resource, "_lock", None)
                if resource_lock:
                    try:
                        with resource_lock:
                            resource.allocated_capacity = float(allocated_capacity)
                    except Exception:
                        continue
                else:
                    resource.allocated_capacity = float(allocated_capacity)

                try:
                    setattr(resource, "_petri_slot_capacity", max(1e-3, float(service_capacity)))
                except Exception:
                    pass

                allocations_by_id[resource_id] = float(allocated_capacity)
                capacity_by_id[resource_id] = float(service_capacity)

                try:
                    resource.record_utilization(current_time)
                except Exception:
                    pass

        self._last_utilization_sync_time = current_time

    def _refresh_resource_state_index(self) -> None:
        """Rebuild mapping of resource state places for quick lookup."""
        self._resource_state_index = {}
        self._resource_token_active = {}
        self._resource_token_queues = {}
        self._resource_busy_time_window = {}
        self._last_utilization_sync_time = getattr(self, "_current_time", 0.0)

        petri_net = getattr(self, "petri_net", None)
        model_builder = getattr(self, "_model_builder", None)
        if petri_net is None or model_builder is None:
            return

        resource_mapping = getattr(model_builder, "resource_mapping", None)
        if not resource_mapping:
            return

        for resource in resource_mapping.values():
            resource_name = getattr(resource, "name", None)
            if not resource_name:
                continue
            place_id = f"ResourceState_{resource_name}"
            if place_id not in petri_net.places:
                continue

            self._resource_state_index[place_id] = resource
            self._resource_token_active[resource_name] = {}
            self._resource_token_queues[resource_name] = {}
            self._resource_busy_time_window[resource_name] = 0.0

            place = petri_net.places.get(place_id)
            if place is not None:
                try:
                    slot_capacity = float(getattr(resource, "_petri_slot_capacity", 0.0) or 0.0)
                except Exception:
                    slot_capacity = 0.0
                if slot_capacity <= 0.0:
                    try:
                        slot_capacity = float(place.token_count)
                    except Exception:
                        try:
                            slot_capacity = float(len(place.tokens))
                        except Exception:
                            slot_capacity = 0.0
                try:
                    setattr(resource, "_petri_slot_capacity", max(1e-3, slot_capacity))
                except Exception:
                    pass

    def _update_resource_busy_slots(self, token_map: Dict[str, List[Token]], delta: int) -> None:
        """Track active sessions and accumulated busy time for resource tokens."""
        if not token_map or not self._resource_state_index:
            return

        now = getattr(self, "_current_time", 0.0)

        for place_id, tokens in token_map.items():
            resource = self._resource_state_index.get(place_id)
            if resource is None or not tokens:
                continue

            resource_name = getattr(resource, "name", None)
            if not resource_name:
                continue

            active_tokens = self._resource_token_active.setdefault(resource_name, {})
            queue_map = self._resource_token_queues.setdefault(resource_name, {})
            busy_window = self._resource_busy_time_window

            for token in tokens:
                token_id = getattr(token, "id", None) or f"{id(token)}"
                if delta > 0:
                    if token_id in active_tokens:
                        queue = queue_map.setdefault(token_id, deque())
                        queue.append(now)
                    else:
                        active_tokens[token_id] = {"start": now, "last": now}
                else:
                    active_entry = active_tokens.get(token_id)
                    if active_entry is not None:
                        last_time = active_entry.get("last", active_entry.get("start", now))
                        busy_duration = max(0.0, now - last_time)
                        busy_window[resource_name] = busy_window.get(resource_name, 0.0) + busy_duration
                        active_tokens.pop(token_id, None)

                    queue = queue_map.get(token_id)
                    promote_next = active_entry is not None
                    if queue:
                        if promote_next:
                            next_start = max(now, queue.popleft())
                            active_tokens[token_id] = {"start": next_start, "last": now}
                        else:
                            queue.popleft()
                        if not queue:
                            queue_map.pop(token_id, None)
                    elif queue is not None and not queue:
                        queue_map.pop(token_id, None)

    def _update_resource_processing_delay(self, resource: Any) -> None:
        """Propagate a resource's dynamic processing delay to its Petri transition."""
        petri_net = getattr(self, "_petri_net", None)
        if petri_net is None:
            return
        name = getattr(resource, "name", None)
        if not name:
            return
        transition_id = f"Transition_Process_{name}"
        transition = petri_net.transitions.get(transition_id)
        if transition is None:
            return
        try:
            new_delay = float(max(0.0, resource.get_processing_delay()))
        except Exception:
            return
        try:
            transition.delay = new_delay
        except Exception:
            logger.debug(
                "Failed to update transition delay for %s",
                transition_id,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
    
    def _update_metrics(self, force: bool = False) -> None:
        """
        Update simulation metrics with comprehensive error handling and performance monitoring.
        
        This method collects and updates various simulation metrics including:
        - Token counts across all places in the Petri net
        - Event queue size and processing statistics
        - System state and timing information
        - Performance metrics for the metrics collection itself
        
        Args:
            force: If True, forces an update even if minimal time hasn't passed since
                  the last update. This is useful for capturing metrics at critical points.
                  
        Raises:
            TypeError: If the 'force' parameter is not a boolean.
            
        Note:
            - Metrics collection is thread-safe and handles missing or invalid components gracefully
            - Performance impact is minimized through batching and rate limiting
            - Detailed error context is captured for troubleshooting
            - Metrics collection time is tracked and reported
        """
        # Input validation
        if not isinstance(force, bool):
            raise TypeError(f"'force' must be a boolean, got {type(force).__name__}")
            
        # Skip if metrics collection is not initialized
        if not hasattr(self, '_metrics') or not self._metrics:
            logger.debug("Metrics collector not initialized, skipping update")
            return
            
        # Skip if not enough time has passed since last update (unless forced)
        time_since_last_update = getattr(self, '_current_time', 0) - getattr(self, '_last_metrics_update', 0)
        if not force and time_since_last_update < getattr(self, '_metrics_update_interval', 1.0):
            return
            
        start_time = time.time()
        metrics_data = {}
        
        try:
            # Initialize thread-safe context
            with self._lock if hasattr(self, '_lock') else nullcontext():
                # Ensure metrics collector is initialized (safety net)
                try:
                    if (hasattr(self, '_metrics') and self._metrics is not None and
                            not getattr(self._metrics, '_initialized', False) and
                            hasattr(self, '_model_builder') and self._model_builder is not None and
                            hasattr(self._model_builder, 'resource_mapping') and self._model_builder.resource_mapping):
                        resource_ids = list(self._model_builder.resource_mapping.keys())
                        if resource_ids:
                            self._metrics.initialize(resource_ids)
                            logger.info("Fallback: MetricsCollector lazy-initialized for %d resources", len(resource_ids))
                except Exception as e:
                    logger.debug("Lazy init MetricsCollector failed: %s", str(e), exc_info=logger.isEnabledFor(logging.DEBUG))
                # Collect per-resource time series from real resource objects (utilization/power/carbon)
                try:
                    if (hasattr(self, '_model_builder') and self._model_builder is not None and
                            hasattr(self._model_builder, 'resource_mapping') and self._model_builder.resource_mapping and
                            hasattr(self, '_metrics') and self._metrics is not None and
                            getattr(self, '_current_time', None) is not None):
                        self._synchronize_resource_utilization_from_petri_net()
                        self._metrics.collect_periodic_stats(
                            self._model_builder.resource_mapping,
                            getattr(self, 'energy_model', None),
                            getattr(self, 'carbon_model', None),
                            self._current_time,
                        )
                        logger.debug(
                            "Collected periodic stats at t=%.3f for %d resources",
                            self._current_time,
                            len(self._model_builder.resource_mapping),
                        )
                except Exception as e:
                    logger.debug("collect_periodic_stats failed: %s", str(e), exc_info=logger.isEnabledFor(logging.DEBUG))
                # 1. Collect token metrics
                token_metrics = self._collect_token_metrics()
                
                # 2. Collect queue metrics
                queue_metrics = self._collect_queue_metrics()
                
                # 3. Collect system state metrics
                system_metrics = self._collect_system_metrics()
                
                # 4. Combine all metrics
                metrics_data = {
                    **token_metrics,
                    **queue_metrics,
                    **system_metrics,
                    'metrics_update_duration': 0,  # Will be updated below
                    'metrics_update_timestamp': time.time(),
                }
                
                # 5. Update metrics collector
                self._metrics.update(**metrics_data)
                
                # 6. Update last metrics update time
                if hasattr(self, '_current_time'):
                    self._last_metrics_update = self._current_time
                
                # 7. Calculate and store metrics collection duration
                metrics_data['metrics_update_duration'] = time.time() - start_time
                
                # 8. Log successful update (at debug level)
                logger.debug(
                    "Updated metrics in %.4f seconds: %s",
                    metrics_data['metrics_update_duration'],
                    {k: v for k, v in metrics_data.items() if k != 'metrics_update_duration'}
                )
                
        except Exception as e:
            # Log detailed error information
            self._handle_metrics_error(e, start_time)
            
    def _collect_token_metrics(self) -> Dict[str, Any]:
        """Collect token-related metrics from the Petri net."""
        token_metrics = {
            'token_count': 0,
            'place_counts': {},
            'token_types': {}
        }
        
        if not hasattr(self, '_petri_net') or not self._petri_net:
            return token_metrics
            
        try:
            if hasattr(self._petri_net, 'places'):
                places = self._petri_net.places
                token_metrics['token_count'] = sum(len(getattr(place, 'tokens', [])) for place in places)
                
                # Count tokens per place
                token_metrics['place_counts'] = {
                    place.name: len(getattr(place, 'tokens', []))
                    for place in places
                    if hasattr(place, 'name')
                }
                
                # Count token types if available
                if hasattr(self, '_token_types') and self._token_types:
                    token_metrics['token_types'] = {
                        t_type: sum(1 for place in places 
                                  for token in getattr(place, 'tokens', []) 
                                  if getattr(token, 'type', None) == t_type)
                        for t_type in self._token_types
                    }
                    
        except Exception as e:
            logger.warning("Error collecting token metrics: %s", str(e), exc_info=True)
            
        return token_metrics
        
    def _collect_queue_metrics(self) -> Dict[str, Any]:
        """Collect event queue metrics."""
        if not hasattr(self, '_event_queue'):
            return {}
            
        try:
            queue_size = len(self._event_queue)
            return {
                'event_queue_size': queue_size,
                'queue_utilization': min(1.0, queue_size / max(1, getattr(self, '_max_queue_size', 1000)))
            }
        except Exception as e:
            logger.warning("Error collecting queue metrics: %s", str(e), exc_info=True)
            return {}
            
    def _collect_system_metrics(self) -> Dict[str, Any]:
        """Collect system-level metrics."""
        metrics = {
            'current_time': getattr(self, '_current_time', 0.0),
            'state': getattr(self, '_state', 'UNKNOWN'),
            'workload_count': len(getattr(self, '_workloads', [])),
            'handler_count': len(getattr(self, '_event_handlers', [])),
            'error_count': len(getattr(self, '_errors', []))
        }
        
        # Add memory usage if psutil is available
        try:
            import psutil
            process = psutil.Process()
            mem_info = process.memory_info()
            metrics.update({
                'memory_rss_mb': mem_info.rss / (1024 * 1024),  # Convert to MB
                'memory_percent': process.memory_percent(),
            })
        except ImportError:
            pass  # psutil not available, skip memory metrics
            
        return metrics
        
    def _handle_metrics_error(self, error: Exception, start_time: float) -> None:
        """Handle errors during metrics collection."""
        # Calculate duration even in case of error
        duration = time.time() - start_time
        
        # Prepare error context
        error_context = {
            'error_type': error.__class__.__name__,
            'error_message': str(error),
            'duration_seconds': duration,
            'current_time': getattr(self, '_current_time', None),
            'state': getattr(self, '_state', 'UNKNOWN'),
            'has_petri_net': hasattr(self, '_petri_net') and bool(self._petri_net),
            'has_event_queue': hasattr(self, '_event_queue'),
            'has_metrics': hasattr(self, '_metrics') and bool(self._metrics)
        }
        
        # Log the error with context
        logger.error(
            "Error updating metrics after %.4f seconds: %s",
            duration,
            error_context,
            exc_info=True
        )
        
        # Record the error for metrics and reporting
        self._log_error(
            "Error updating metrics",
            error=error,
            severity="error",
            **error_context
        )
    
    
    def _update_resource_scaling(self) -> None:
        """Update resource scaling based on current workload using real resource objects."""
        if not self._model_builder:
            logger.debug("No ModelBuilder available for resource scaling")
            return
            
        if not hasattr(self._model_builder, 'resource_mapping'):
            logger.warning("ModelBuilder missing resource_mapping attribute")
            return
            
        if not self._model_builder.resource_mapping:
            logger.debug("No resources in resource_mapping for scaling")
            return
            
        # Import here to avoid circular imports
        from ..gcp.compute import CloudRun, GKECluster
        try:
            from ..gcp.storage import CloudSQL
        except Exception:
            CloudSQL = tuple()  # type: ignore
        try:
            from ..gcp.network import NetworkResource
        except Exception:
            NetworkResource = tuple()  # type: ignore
        
        # Calculate current request rate from workload
        current_rate = self._calculate_current_request_rate()
        
        # Log scaling operation details
        resource_count = len(self._model_builder.resource_mapping)
        logger.debug(f"Updating scaling for {resource_count} resources at rate {current_rate:.1f} req/s")
        cluster_pod_demand = self._estimate_cluster_pod_demand(current_rate)
        
        # Track scaling results
        scaled_resources = 0
        failed_resources = 0
        
        # Parameters to derive utilization hints for non-autoscaled resources
        db_qpr = 1.0  # DB queries per request
        avg_payload_mb = 0.25  # Average payload size per request in MB
        try:
            wl_params = {}
            if hasattr(self, '_workload_params') and isinstance(self._workload_params, dict):
                wl_params = self._workload_params.get('params', self._workload_params) or {}
            db_qpr = float(wl_params.get('db_queries_per_request', wl_params.get('db_qpr', db_qpr)))
            avg_payload_mb = float(wl_params.get('payload_mb', wl_params.get('avg_payload_mb', avg_payload_mb)))
        except Exception:
            pass
        
        # Update scaling for all scalable resources using real resource objects
        resource_items = list(self._model_builder.resource_mapping.items())
        for resource_id, resource in resource_items:
            try:
                if isinstance(resource, CloudRun):
                    # Scale CloudRun based on request rate using real scaling logic
                    success = resource.scale(current_rate, self._current_time)
                    if success:
                        scaled_resources += 1
                    else:
                        logger.debug(f"CloudRun resource {resource.name} scaling returned False")

                    # After scaling, synchronize Petri net slot tokens with instances * concurrency
                    try:
                        self._sync_resource_state_place_tokens(resource)
                    except Exception:
                        # Don't interrupt scaling cycle due to token sync issues
                        pass
                        
                elif isinstance(resource, GKECluster):
                    pods_override = cluster_pod_demand.get(resource_id) if cluster_pod_demand else None
                    if pods_override is not None and pods_override > 0:
                        resource.scale(
                            request_rate=pods_override,
                            pod_capacity=1.0,
                            timestamp=self._current_time,
                            pods_override=pods_override,
                        )
                    else:
                        # Scale GKE cluster (using default pod capacity)
                        pod_capacity = 50.0  # Default pod capacity
                        resource.scale(current_rate, pod_capacity, self._current_time)
                    scaled_resources += 1

                    # Surface any newly created or removed nodes in the model builder mapping
                    try:
                        registrar = getattr(self._model_builder, "register_cluster_nodes", None)
                        if callable(registrar):
                            registered_ids, removed_ids = registrar(
                                cluster_rid=resource_id,
                                cluster_obj=resource,
                            )
                            if registered_ids:
                                logger.info(
                                    "Autoscaling registered %d new GKE node(s) for %s: %s",
                                    len(registered_ids),
                                    resource_id,
                                    ", ".join(registered_ids),
                                )
                            if removed_ids:
                                logger.info(
                                    "Autoscaling removed %d GKE node(s) for %s: %s",
                                    len(removed_ids),
                                    resource_id,
                                    ", ".join(removed_ids),
                                )
                    except Exception as reg_err:
                        logger.debug(
                            "Failed to register scaled GKE nodes for %s: %s",
                            resource_id,
                            reg_err,
                        )

                    try:
                        self._sync_resource_state_place_tokens(resource)
                    except Exception:
                        pass
                    
                elif isinstance(resource, CloudSQL):
                    # Provide request-rate hint so CloudSQL utilization can reflect load
                    try:
                        req_rate = float(current_rate * db_qpr)
                        setattr(resource, '_last_request_rate', req_rate)
                        self._update_resource_processing_delay(resource)
                        # Debug hint is useful but too chatty per tick; throttle to first few seconds
                        if self._current_time <= 1.0:
                            logger.debug(
                                "Set CloudSQL request rate hint for %s: %.2f qps",
                                getattr(resource, 'name', resource_id), req_rate
                            )
                    except Exception:
                        pass
                    try:
                        self._sync_resource_state_place_tokens(resource)
                    except Exception:
                        pass
                
                elif isinstance(resource, NetworkResource):
                    # Provide throughput hint so network utilization scales with traffic
                    try:
                        throughput_mbps = float(current_rate * avg_payload_mb * 8.0)  # MB/s → Mb/s
                        setattr(resource, 'current_throughput_mbps', throughput_mbps)
                        if self._current_time <= 1.0:
                            logger.debug(
                                "Set Network throughput hint for %s: %.2f Mbps",
                                getattr(resource, 'name', resource_id), throughput_mbps
                            )
                    except Exception:
                        pass
                    try:
                        self._sync_resource_state_place_tokens(resource)
                    except Exception:
                        pass
                    
                else:
                    # Log unsupported resource types for debugging
                    logger.debug(f"Skipping scaling for unsupported resource type: {type(resource).__name__}")
                    
            except Exception as e:
                failed_resources += 1
                resource_name = getattr(resource, 'name', resource_id)
                logger.warning(
                    "Failed to scale resource %s (%s): %s", 
                    resource_name,
                    type(resource).__name__,
                    str(e)
                )
                # Continue with other resources even if one fails
                
        # Log scaling summary if there were any operations
        if scaled_resources > 0 or failed_resources > 0:
            logger.debug(f"Resource scaling completed: {scaled_resources} successful, {failed_resources} failed")

    def _sync_resource_state_place_tokens(self, resource: Any) -> None:
        """Ensure `ResourceState_<name>` has tokens equal to instances * concurrency (or capacity).

        This keeps runtime concurrency aligned with the Petri net slots after scaling events.
        """
        try:
            if not hasattr(self, '_petri_net') or not self._petri_net:
                return
            place_id = f"ResourceState_{getattr(resource, 'name', '')}"
            if not place_id or place_id not in self._petri_net.places:
                return

            place = self._petri_net.places[place_id]

            # Determine desired slots ONLY for resources with explicit instances+concurrency
            desired_slots: Optional[int] = None
            instances = getattr(resource, 'current_instances', None)
            concurrency = getattr(resource, 'concurrency', None)
            
            # For K8s workloads, use replicas instead of current_instances
            if instances is None:
                instances = getattr(resource, 'replicas', None)
            
            # For K8s workloads without concurrency, use capacity directly
            if instances is not None and concurrency is None:
                # K8s workload: use capacity as slot count
                capacity = getattr(resource, 'capacity', None)
                if capacity is not None:
                    try:
                        desired_slots = max(1, int(capacity))
                    except Exception:
                        desired_slots = None
            elif instances is not None and concurrency is not None:
                # Regular resource with instances and concurrency
                try:
                    desired_slots = int(max(1, int(instances)) * max(1, int(concurrency)))
                except Exception:
                    desired_slots = None

            # If we cannot determine slots, skip sync
            if desired_slots is None:
                return
            try:
                setattr(resource, "_petri_slot_capacity", float(desired_slots))
            except Exception:
                pass

            # Current tokens
            try:
                current = place.token_count
            except Exception:
                current = len(place.tokens)

            delta = desired_slots - current
            if delta > 0:
                # Add tokens up to capacity
                for _ in range(delta):
                    try:
                        place.add_token(Token())  # type: ignore[name-defined]
                    except Exception:
                        # Capacity reached or other constraint
                        break
            elif delta < 0:
                # Remove extra tokens
                try:
                    # Remove arbitrary tokens
                    tokens_list = list(place.tokens)
                    to_remove = min(len(tokens_list), -delta)
                    for i in range(to_remove):
                        place.remove_token(tokens_list[i])
                except Exception:
                    pass
        except Exception:
            # Silent guard: token sync should not affect orchestrator flow
            pass
    
    def _calculate_current_request_rate(self) -> float:
        """Calculate the current request rate based on active workloads with improved error handling."""
        total_rate = 0.0
        workload_count = 0
        
        # Calculate rate from all active workloads
        for workload in self._workloads:
            if hasattr(workload, 'get_rate_at_time'):
                try:
                    rate = workload.get_rate_at_time(self._current_time)
                    if rate is not None and rate >= 0:
                        total_rate += rate
                        workload_count += 1
                    else:
                        logger.debug(f"Workload returned invalid rate: {rate}")
                except Exception as e:
                    logger.debug("Failed to get rate from workload: %s", str(e))
        
        # If we got rates from workloads, use them
        if workload_count > 0:
            logger.debug(f"Calculated request rate from {workload_count} workloads: {total_rate:.1f} req/s")
            return total_rate
        
        # Fallback 1: Estimate from recent events in the queue
        if self._event_queue:
            # Look at events in the last second to estimate rate
            recent_events = [e for e in self._event_queue if e[0] > self._current_time - 1.0 and e[0] <= self._current_time]
            if recent_events:
                total_rate = len(recent_events)
                logger.debug(f"Estimated request rate from {len(recent_events)} recent events: {total_rate:.1f} req/s")
                return total_rate
        
        # Fallback 2: Use configured workload rate if available
        if hasattr(self, '_config') and self._config:
            try:
                if hasattr(self._config.simulation, 'workload') and self._config.simulation.workload:
                    if hasattr(self._config.simulation.workload, 'params') and 'rate' in self._config.simulation.workload.params:
                        total_rate = float(self._config.simulation.workload.params['rate'])
                    elif hasattr(self._config.simulation.workload, 'rate'):
                        total_rate = float(self._config.simulation.workload.rate)
                    
                    if total_rate > 0:
                        logger.debug(f"Using configured workload rate: {total_rate:.1f} req/s")
                        return total_rate
            except Exception as e:
                logger.debug(f"Failed to get configured workload rate: {e}")
        
        # Final fallback: Use a reasonable default
        total_rate = 100.0
        logger.debug(f"Using default request rate: {total_rate:.1f} req/s")

        return total_rate

    def _estimate_cluster_pod_demand(self, current_rate: float) -> Dict[str, int]:
        try:
            from ..gcp.compute import GKECluster
        except Exception:
            return {}
        mapping = getattr(self._model_builder, "resource_mapping", {}) or {}
        workloads: List[Resource] = []
        for resource in mapping.values():
            attrs = getattr(resource, "attributes", None)
            if not isinstance(attrs, dict):
                continue
            k8s_meta = attrs.get("k8s")
            if not isinstance(k8s_meta, dict):
                continue
            kind = str(k8s_meta.get("kind", "")).lower()
            if kind not in {"deployment", "statefulset", "daemonset", "job", "cronjob"}:
                continue
            workloads.append(resource)

        if not workloads:
            return {}

        workload_weights: List[Tuple[Resource, float]] = []
        total_weight = 0.0
        for workload in workloads:
            attrs = getattr(workload, "attributes", {}) or {}
            cpu_requests = attrs.get("cpu_requests", getattr(workload, "cpu_requests", 0.0))
            try:
                cpu_requests = float(cpu_requests)
            except (TypeError, ValueError):
                cpu_requests = 0.0
            replicas_attr = attrs.get("replicas", getattr(workload, "replicas", 1))
            try:
                replicas_val = max(1, int(replicas_attr))
            except (TypeError, ValueError):
                replicas_val = 1
            weight = cpu_requests * replicas_val
            if weight <= 0.0:
                weight = 1.0
            workload_weights.append((workload, weight))
            total_weight += weight

        if not workload_weights:
            return {}

        if total_weight <= 0.0:
            total_weight = float(len(workload_weights))

        # Strict HPA-only mode: only workloads with bound HPAs can change replicas.
        strict = False
        try:
            cfg_models = getattr(self, "config").models if hasattr(self, "config") else None
            scaling_cfg = getattr(cfg_models, "scaling", None) if cfg_models else None
            strict = bool(getattr(scaling_cfg, "strict_hpa_only", False)) if scaling_cfg is not None else False
        except Exception:
            strict = False

        clusters = [
            (res_id, resource)
            for res_id, resource in mapping.items()
            if isinstance(resource, GKECluster)
        ]
        primary_cluster = clusters[0][1] if clusters else None
        usable_cpu = usable_mem = None
        if primary_cluster:
            try:
                usable_cpu, usable_mem = primary_cluster.get_node_capacity_limits()
            except Exception:
                usable_cpu = usable_mem = None

        total_pods = 0
        for workload, weight in workload_weights:
            share = weight / total_weight if total_weight > 0 else 1.0 / len(workload_weights)
            workload_rate = max(0.0, float(current_rate) * share)
            try:
                attrs["_last_workload_rate"] = workload_rate
            except Exception:
                pass
            attrs = getattr(workload, "attributes", {}) or {}
            has_hpa = bool(attrs.get("autoscaler")) or ("target_cpu_utilization" in attrs)

            k8s_meta = attrs.get("k8s", {})
            namespace = "default"
            if isinstance(k8s_meta, dict):
                namespace = k8s_meta.get("namespace") or namespace
            kind = str((k8s_meta or {}).get("kind") or attrs.get("kind") or "").lower()
            workload_name = (k8s_meta or {}).get("name") or getattr(workload, "name", "unknown")
            if kind and workload_name:
                workload_id = f"{kind}::{namespace}/{workload_name}"
            else:
                workload_id = getattr(workload, "resource_id", None) or getattr(workload, "name", "unknown")
            cpu_per_pod = attrs.get("cpu_requests", getattr(workload, "cpu_requests", 0.0))
            mem_per_pod = attrs.get("memory_requests_gb", getattr(workload, "memory_requests_gb", 0.0))
            try:
                cpu_per_pod = float(cpu_per_pod or 0.0)
            except (TypeError, ValueError):
                cpu_per_pod = 0.0
            try:
                mem_per_pod = float(mem_per_pod or 0.0)
            except (TypeError, ValueError):
                mem_per_pod = 0.0

            unschedulable = False
            reason = None
            if primary_cluster and usable_cpu is not None and cpu_per_pod > 0.0 and cpu_per_pod - usable_cpu > 1e-9:
                unschedulable = True
                reason = (
                    f"CPU request {cpu_per_pod:.2f} vCPU exceeds node capacity {usable_cpu:.2f} vCPU"
                )
            if primary_cluster and usable_mem is not None and mem_per_pod > 0.0 and mem_per_pod - usable_mem > 1e-9:
                unschedulable = True
                mem_reason = (
                    f"memory request {mem_per_pod:.2f} GiB exceeds node capacity {usable_mem:.2f} GiB"
                )
                reason = f"{reason}; {mem_reason}" if reason else mem_reason

            if unschedulable:
                pending = attrs.get("replicas", getattr(workload, "replicas", 1))
                try:
                    pending = max(1, int(pending))
                except Exception:
                    pending = 1
                attrs["pending_pods"] = pending
                if reason:
                    attrs["unschedulable_reason"] = reason
                entry = {
                    "resource_id": workload_id,
                    "workload_name": workload_name,
                    "namespace": namespace,
                    "cpu_request": cpu_per_pod,
                    "memory_request_gb": mem_per_pod,
                    "node_cpu_limit": usable_cpu,
                    "node_memory_limit_gb": usable_mem,
                    "pending_replicas": pending,
                    "reason": reason,
                }
                self._unschedulable_workloads[workload_id] = entry
                logger.warning(
                    "Workload %s marked unschedulable: %s",
                    entry["workload_name"],
                    reason,
                )
                try:
                    workload.attributes["desired_replicas"] = 0
                except Exception:
                    pass
                continue
            else:
                self._unschedulable_workloads.pop(workload_id, None)

            latest_util = self._get_latest_metric_value(workload_id, "utilization")
            desired: Optional[int] = None
            if has_hpa:
                desired = self._calculate_hpa_desired_replicas(workload, attrs, latest_util)

            if desired is None and strict and not has_hpa:
                # Do not scale replicas for non-HPA workloads in strict mode
                desired = attrs.get("replicas", getattr(workload, "replicas", 1))
                try:
                    desired = max(1, int(desired))
                except Exception:
                    desired = 1

            if desired is None:
                desired = self._estimate_workload_desired_replicas(workload, workload_rate)

            try:
                desired = max(1, int(desired))
            except Exception:
                desired = 1

            self._record_replica_metric(workload_id, desired)

            try:
                workload.replicas = desired
                attrs["replicas"] = desired
                # Sync Petri net tokens immediately to reflect new capacity
                self._sync_resource_state_place_tokens(workload)
                per_replica_capacity = max(
                    float(attrs.get("cpu_limits") or getattr(workload, "cpu_limits", 0.0) or 0.0),
                    float(attrs.get("cpu_requests") or getattr(workload, "cpu_requests", 0.0) or 1.0),
                    1.0,
                )
                workload.capacity = max(1e-3, per_replica_capacity * desired)
                attrs["burst_capacity"] = workload.capacity
            except Exception:
                pass

            try:
                workload.attributes["desired_replicas"] = desired
            except Exception:
                pass
            total_pods += desired

        if not clusters:
            return {}
        cluster_id = clusters[0][0]
        return {cluster_id: total_pods}

    def _calculate_hpa_desired_replicas(
        self,
        workload: Resource,
        attrs: Dict[str, Any],
        latest_util: Optional[float],
    ) -> Optional[int]:
        """Use HPA-style logic driven by measured CPU utilization."""
        metric_id = (
            getattr(workload, "resource_id", None)
            or attrs.get("resource_id")
            or getattr(workload, "name", None)
            or ""
        )
        target_util = attrs.get("target_cpu_utilization")
        if target_util is None:
            return None
        try:
            target_util = float(target_util)
        except (TypeError, ValueError):
            return None
        if target_util <= 0.0:
            return None

        current_replicas = attrs.get("replicas", getattr(workload, "replicas", 1))
        try:
            current_replicas = max(1, int(current_replicas))
        except (TypeError, ValueError):
            current_replicas = 1

        if latest_util is None:
            # Try a small moving average; if still None, skip scaling
            latest_util = self._get_recent_metric_average(
                metric_id,
                "utilization",
                window=3,
            )
            if latest_util is None:
                return None

        # Estimate utilization from workload rate to avoid starving HPA when metrics are sparse.
        workload_rate = attrs.get("_last_workload_rate")
        per_pod_capacity = None
        try:
            cpu_req = float(attrs.get("cpu_requests", getattr(workload, "cpu_requests", 0.0)) or 0.0)
            if cpu_req > 0.0:
                requests_per_vcpu = 25.0
                scaling_cfg = getattr(getattr(self, "config", None), "models", None)
                scaling_cfg = getattr(scaling_cfg, "scaling", None)
                if scaling_cfg is not None:
                    try:
                        requests_per_vcpu = max(1.0, float(getattr(scaling_cfg, "requests_per_vcpu", requests_per_vcpu) or requests_per_vcpu))
                    except Exception:
                        pass
                per_pod_capacity = cpu_req * requests_per_vcpu
        except Exception:
            per_pod_capacity = None

        util_est = None
        if workload_rate is not None and per_pod_capacity:
            try:
                util_est = float(workload_rate) / (max(1, current_replicas) * per_pod_capacity)
                util_est = max(0.0, util_est)
            except Exception:
                util_est = None

        # Use a smoothed utilization signal to avoid oscillating on single samples.
        util_avg = self._get_recent_metric_average(
            metric_id,
            "utilization",
            window=3,
        )
        decision_util = max(
            float(latest_util),
            float(util_avg) if util_avg is not None else 0.0,
            float(util_est) if util_est is not None else 0.0,
        )

        current_pct = max(0.0, decision_util) * 100.0
        scale_ratio = current_pct / max(target_util, 1e-3)
        raw_desired = scale_ratio * current_replicas
        if raw_desired > current_replicas:
            desired = math.ceil(raw_desired)
        else:
            # Require two consecutive low readings before downscaling to avoid oscillation.
            low_streak = attrs.get("_hpa_low_streak", 0)
            if current_pct < target_util:
                low_streak += 1
            else:
                low_streak = 0
            attrs["_hpa_low_streak"] = low_streak

            if low_streak < 2:
                desired = current_replicas
            else:
                desired = math.floor(raw_desired)
        desired = max(1, desired)

        min_rep = attrs.get("min_replicas")
        max_rep = attrs.get("max_replicas")
        if isinstance(min_rep, int):
            desired = max(desired, min_rep)
        if isinstance(max_rep, int) and max_rep > 0:
            desired = min(desired, max_rep)
        return desired

    def _estimate_workload_desired_replicas(self, workload: Resource, workload_rate: float) -> int:
        attrs = getattr(workload, "attributes", {}) or {}
        replicas = attrs.get("replicas", getattr(workload, "replicas", 1))
        try:
            replicas = max(1, int(replicas))
        except (TypeError, ValueError):
            replicas = 1

        scaling_cfg = getattr(getattr(self, "config", None), "models", None)
        scaling_cfg = getattr(scaling_cfg, "scaling", None)
        min_capacity = 1.0
        requests_per_vcpu = 25.0
        if scaling_cfg is not None:
            try:
                min_capacity = max(0.1, float(getattr(scaling_cfg, "request_capacity", min_capacity) or min_capacity))
            except Exception:
                min_capacity = 1.0
            try:
                requests_per_vcpu = max(1.0, float(getattr(scaling_cfg, "requests_per_vcpu", requests_per_vcpu) or requests_per_vcpu))
            except Exception:
                requests_per_vcpu = 25.0

        try:
            per_pod_capacity = float(getattr(workload, "capacity", 0.0)) / max(1, replicas)
        except Exception:
            per_pod_capacity = min_capacity

        attrs = getattr(workload, "attributes", {}) or {}
        capacity_unit = str(attrs.get("capacity_unit", "")).lower()
        if capacity_unit in {"cpu", "cores"}:
            cpu_per_pod = attrs.get("cpu_requests", getattr(workload, "cpu_requests", 0.0))
            try:
                cpu_per_pod = float(cpu_per_pod)
            except (TypeError, ValueError):
                cpu_per_pod = 0.0
            if cpu_per_pod <= 0.0:
                cpu_per_pod = per_pod_capacity
            per_pod_capacity = cpu_per_pod * requests_per_vcpu

        if per_pod_capacity <= 0.0:
            per_pod_capacity = min_capacity

        per_pod_capacity = max(per_pod_capacity, min_capacity)

        if workload_rate <= 0.0:
            desired = replicas
        else:
            desired = math.ceil(workload_rate / per_pod_capacity)

        min_rep = attrs.get("min_replicas")
        max_rep = attrs.get("max_replicas")
        if isinstance(min_rep, int):
            desired = max(desired, min_rep)
        if isinstance(max_rep, int) and max_rep > 0:
            desired = min(desired, max_rep)

        return max(1, desired)
    
    def _check_workload_updates(self) -> None:
        """
        Check for and process workload updates.
        
        This method iterates through all registered workloads, retrieves any pending
        updates, and schedules corresponding events. It includes comprehensive error
        handling to ensure the simulation continues even if individual workloads fail.
        
        The method performs the following steps for each workload:
        1. Validates the workload instance using _validate_workload_interface()
        2. Retrieves pending updates using _get_workload_updates()
        3. Validates and processes each update using _process_single_update()
        4. Schedules events for valid updates
        
        Note:
            - Continues processing other workloads if one fails
            - Logs detailed error information for debugging
            - Handles partial failures gracefully
            - Validates all inputs before processing
            - Thread-safe access to workloads list
            - Uses helper methods for better code organization
            - Includes performance metrics collection
            
        Raises:
            RuntimeError: Only if a critical error occurs that prevents further processing
                        of any workloads (e.g., lock acquisition failure).
                        Individual workload errors are caught and logged.
        """
        # Log the start of workload update check
        logger.debug("Starting workload update check")
        
        # Skip if no workloads are registered
        if not self._workloads:
            logger.debug("No workloads registered, skipping update check")
            return
        
        # Track performance metrics
        start_time = time.time()
        # Iterate safely over a snapshot of workloads
        try:
            workloads_snapshot = list(self._workloads)
            current_time = self._current_time
            for workload in workloads_snapshot:
                # Get a descriptive name for logging
                workload_name = self._get_workload_name(workload)

                # Validate workload interface
                self._validate_workload_interface(workload)

                # Get updates from the workload
                updates = self._get_workload_updates(workload, current_time, workload_name)

                # Process each update
                for update in updates:
                    try:
                        self._process_single_update(update, workload_name, current_time)
                    except Exception as e:
                        self._log_error(
                            f"Error processing update from workload '{workload_name}': {str(e)}",
                            error=e,
                            update=update,
                            workload=workload_name
                        )
                        continue

        except Exception as e:
            self._log_error(
                f"Error checking updates for workloads: {str(e)}",
                error=e,
                severity="warning",
            )

    def _get_workload_name(self, workload: Any) -> str:
        """Return a descriptive workload name for logging.

        Prefers a 'name' attribute; falls back to the class name.
        """
        try:
            if hasattr(workload, 'name') and isinstance(getattr(workload, 'name'), str):
                return str(getattr(workload, 'name'))
        except Exception:
            pass
        try:
            return type(workload).__name__
        except Exception:
            return "workload"

    def _validate_workload_interface(self, workload: Any) -> None:
        """Validate that a workload exposes the expected interface.

        Currently we require a callable 'get_updates(current_time)' method.
        """
        if not hasattr(workload, 'get_updates') or not callable(getattr(workload, 'get_updates')):
            raise TypeError(f"Workload {self._get_workload_name(workload)} must implement get_updates(current_time)")

    def _get_workload_updates(
        self,
        workload: Any,
        current_time: float,
        workload_name: str
    ) -> List[Dict[str, Any]]:
        """Safely get updates from a workload with error handling.
        
        Args:
            workload: The workload to get updates from
            current_time: Current simulation time
            workload_name: Name of the workload for logging
                
        Returns:
            List of update dictionaries, or empty list if no updates
                
        Note:
            - Handles None returns from workload.get_updates()
            - Converts single updates to a list
            - Logs errors and returns empty list on failure
        """
        try:
            updates = workload.get_updates(current_time)
                
            # Handle None or empty updates
            if not updates:
                return []
                    
            # Convert single update to list if needed
            if not isinstance(updates, (list, tuple)):
                updates = [updates]
                    
            # Validate each update is a dictionary
            valid_updates = []
            for update in updates:
                if not isinstance(update, dict):
                    logger.warning(
                        f"Invalid update format from workload '{workload_name}': expected dict, got {type(update).__name__}"
                    )
                    continue
                valid_updates.append(update)
                    
            return valid_updates
                    
        except Exception as e:
            self._log_error(
                f"Error getting updates from workload '{workload_name}': {str(e)}",
                error=e,
                severity="warning",
                workload=workload_name
            )
            return []

    def _process_single_update(
        self,
        update: Dict[str, Any],
        workload_name: str,
        current_time: float
    ) -> bool:
        """Process a single update from a workload.
            
        Args:
            update: The update to process
            workload_name: Name of the workload for logging
            current_time: Current simulation time
                
        Returns:
            bool: True if the update was processed successfully, False otherwise
        """
        try:
            # Validate update format
            if not isinstance(update, dict):
                logger.warning(
                    f"Invalid update format from workload '{workload_name}': expected dict, got {type(update).__name__}"
                )
                return False
                    
            # Extract required fields with defaults
            event_type = update.get('type')
            if not event_type:
                logger.warning(f"Missing 'type' in update from workload '{workload_name}'")
                return False
                    
            # Schedule the event
            self.schedule_event(
                timestamp=current_time,
                event_type=event_type,
                event_data=update.get('data', {}),
                priority=update.get('priority', 0)
            )
            
            return True
                
        except Exception as e:
            self._log_error(
                f"Error processing update from {workload_name}",
                error=e,
                update=update,
                workload_type=workload_name,
                current_time=current_time,
                severity="warning"
            )
            return False

    def _check_constraints(self) -> bool:
        """
        Verify that all registered constraints are satisfied.
        
        This method verifies that all registered constraints are satisfied given the
        current state of the simulation. It handles errors gracefully to
        ensure the simulation can continue even if constraint checking fails.
        
        Returns:
            bool: True if all constraints are satisfied, False otherwise.
        
        Raises:
            RuntimeError: If there is an error accessing the constraint manager.
        """
        if self._constraint_manager is None:
            # Avoid chatty debug every tick; only log once per run
            if not hasattr(self, "_logged_no_constraint_manager"):
                logger.debug("No constraint manager registered, skipping constraint check")
                self._logged_no_constraint_manager = True
            return True
                
        try:
            # Prepare the current metrics, handling cases where metrics might not be available
            current_metrics = {}
            if hasattr(self, '_metrics') and self._metrics is not None:
                try:
                    current_metrics = self._metrics.get_current()
                except Exception as metrics_error:
                    logger.warning(
                        "Error getting current metrics for constraint checking: %s",
                        str(metrics_error)
                    )
            
            # Check all constraints
            constraints_satisfied = self._constraint_manager.check_all(
                petri_net=self._petri_net if hasattr(self, '_petri_net') else None,
                current_time=self._current_time if hasattr(self, '_current_time') else 0.0,
            metrics=current_metrics
        )
        
            # Log constraint violations at debug level
            if not constraints_satisfied:
                logger.debug(
                    "Constraint violation detected at time %.2f (state: %s)",
                    self._current_time if hasattr(self, '_current_time') else 0.0,
                    self._state.name if hasattr(self, '_state') else 'UNKNOWN'
                )
                
            return constraints_satisfied
        
        except Exception as e:
            # Log the error with detailed context
            error_context = {
                'current_time': self._current_time if hasattr(self, '_current_time') else None,
                'state': self._state.name if hasattr(self, '_state') else 'UNKNOWN',
                'has_petri_net': hasattr(self, '_petri_net') and self._petri_net is not None,
                'has_metrics': hasattr(self, '_metrics') and self._metrics is not None
            }
            
            logger.error(
                "Error checking constraints: %s\nContext: %s",
                str(e),
                error_context,
                exc_info=True
            )
            
            # Record the error for metrics and reporting
            self._log_error(
                "Error checking constraints",
                error=e,
                **error_context
            )
            
            # Return False to indicate constraint checking failed
            return False
        
    
    def _transition_state(self, new_state: SimulationState) -> None:
        """
        Transition to a new state with validation.
        
        Args:
            new_state: The new state to transition to.
            
        Raises:
            StateTransitionError: If the transition is not allowed.
        """
        with self._lock:
            if self._state == new_state:
                return  # No state change needed
                
            if not self._is_valid_transition(new_state):
                raise StateTransitionError(
                    f"Invalid state transition from {self._state} to {new_state}"
                )
            
            old_state = self._state
            self._state = new_state
            logger.debug(
                "State transition: %s -> %s",
                old_state.name,
                new_state.name
            )
    
    def _validate_simulation_ready(self) -> None:
        """
        Validate that the simulation is ready to run.
        
        Raises:
            SimulationError: If the simulation is not in the READY state.
        """
        if self._state != SimulationState.READY:
            raise SimulationError(
                f"Simulation must be in READY state to start, current state: {self._state}"
            )
            
    def run_simulation(self, max_duration: Optional[float] = None) -> SimulationResult:
        """
        Run the simulation.
        
        This method executes the main simulation loop, processing events in chronological order
        until either all events are processed, the maximum duration is reached, or a stop
        is requested. It handles state transitions, metrics collection, and error handling.
        
        Args:
            max_duration: Maximum simulation time in seconds. If None, runs until completion.
                         Must be a positive number if specified.
            
        Returns:
            SimulationResult: The compiled results of the simulation.
            
        Raises:
            TypeError: If max_duration is not a number or None.
            ValueError: If max_duration is not positive.
            SimulationError: If the simulation encounters an error during execution.
            StateTransitionError: If there's an invalid state transition.
            
        Example:
            >>> orchestrator = Orchestrator(config=my_config)
            >>> result = orchestrator.run_simulation(max_duration=3600)  # Run for 1 hour
            >>> print(f"Simulation completed in {result.duration:.2f} seconds")
        """
        # Input validation
        if max_duration is not None:
            if not isinstance(max_duration, (int, float)):
                raise TypeError(f"max_duration must be a number, got {type(max_duration).__name__}")
            if max_duration <= 0:
                raise ValueError(f"max_duration must be positive, got {max_duration}")
        
        # Validate simulation is ready to run
        self._validate_simulation_ready()
        
        try:
            # Transition to RUNNING state
            self._transition_state(SimulationState.RUNNING)
            self._start_time = datetime.now()
            
            # Initialize simulation state
            self._current_time = 0.0
            self._last_metrics_update = 0.0
            self._metrics_update_interval = getattr(self.config.simulation, 'metrics_interval', 1.0)

            # Initialize metrics collection for resources once (if mapping available)
            try:
                if (hasattr(self, '_metrics') and self._metrics is not None and
                        hasattr(self, '_model_builder') and self._model_builder is not None and
                        hasattr(self._model_builder, 'resource_mapping') and self._model_builder.resource_mapping):
                    resource_ids = list(self._model_builder.resource_mapping.keys())
                    if resource_ids:
                        logger.info("Initializing metrics collection for %d resources", len(resource_ids))
                        try:
                            self._metrics.initialize(resource_ids)
                            logger.debug("MetricsCollector initialized for resources: %s", resource_ids)
                        except Exception as init_err:
                            logger.warning("Failed to initialize MetricsCollector: %s", str(init_err), exc_info=logger.isEnabledFor(logging.DEBUG))
            except Exception as e:
                logger.warning("Unexpected error during metrics initialization: %s", str(e), exc_info=logger.isEnabledFor(logging.DEBUG))
            
            logger.info(
                "Starting simulation with %d initial events%s", 
                len(self._event_queue),
                f" (max duration: {max_duration}s)" if max_duration is not None else ""
            )
            
            # Main simulation loop
            logger.info("Simulation loop starting with %d queued events", len(self._event_queue))
            while self._event_queue and not self._stop_requested:
                try:
                    # Check for max duration before processing next event
                    if max_duration is not None and self._current_time >= max_duration:
                        logger.info(
                            "Simulation reached maximum duration of %.2f seconds (current time: %.2f)",
                            max_duration, self._current_time
                        )
                        break
                    
                    # Get the next event (scheduled for the earliest time)
                    event_time, _, _, event_type, event_data = self._event_queue[0]
                    
                    # Update simulation time
                    if event_time > self._current_time:
                        self._current_time = event_time
                        
                        # Check max duration after time update
                        if max_duration is not None and self._current_time >= max_duration:
                            logger.info(
                                "Simulation reached maximum duration of %.2f seconds",
                                max_duration
                            )
                            break
                        
                        # Update metrics at regular intervals
                        self._update_metrics_if_needed()
                        
                        # Update resource scaling based on current workload
                        self._update_resource_scaling()
                    
                    # Process the event (removes it from the queue)
                    heapq.heappop(self._event_queue)
                    try:
                        self._process_event(event_type, event_data)
                        if event_type in ("token_creation", "token_completion", "token_distribution", "transition_completion"):
                            logger.debug("Processed event: %s at t=%.3f (queue=%d)", event_type, self._current_time, len(self._event_queue))
                    except Exception as e:
                        self._log_error(
                            f"Error processing {event_type} event at time {event_time}",
                            error=e,
                            event_type=event_type,
                            event_time=event_time,
                            current_time=self._current_time
                        )
                        # Continue with next event even if one fails
                    
                    # Check for workload updates
                    try:
                        self._check_workload_updates()
                    except Exception as e:
                        self._log_error(
                            "Error checking workload updates",
                            error=e,
                            current_time=self._current_time
                        )
                    
                    # Check for resource constraints
                    try:
                        if not self._check_constraints():
                            logger.warning("Constraint violation detected, stopping simulation")
                            self._transition_state(SimulationState.FAILED)
                            raise SimulationError("Constraint violation detected")
                    except Exception as e:
                        self._log_error(
                            "Error checking constraints",
                            error=e,
                            current_time=self._current_time
                        )
                        # Continue simulation even if constraint checking fails
                        
                except IndexError:
                    # No more events in the queue
                    logger.debug("Event queue is empty, ending simulation loop")
                    break
                except Exception as e:
                    error_msg = f"Unexpected error in simulation loop: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    self._log_error(
                        "Critical error in simulation loop",
                        error=e,
                        current_time=self._current_time,
                        severity="critical"
                    )
                    # Re-raise to trigger simulation failure
                    raise SimulationError(error_msg) from e
            
            # Final metrics update
            try:
                self._update_metrics(force=True)
            except Exception as e:
                self._log_error(
                    "Error during final metrics update",
                    error=e,
                    current_time=self._current_time,
                    severity="warning"  # Non-fatal error
                )
            
            # Handle simulation completion
            try:
                if self._stop_requested:
                    self._transition_state(SimulationState.STOPPED)
                    logger.info(
                        "Simulation stopped by user request after %.2f seconds",
                        self._current_time
                    )
                elif self._state == SimulationState.FAILED:
                    logger.error("Simulation ended with errors after %.2f seconds", self._current_time)
                else:
                    self._transition_state(SimulationState.COMPLETED)
                    logger.debug(
                        "Simulation completed successfully in %.2f simulated seconds",
                        self._current_time
                    )
                    
                return self._compile_results()
                
            except Exception as e:
                error_msg = f"Error during simulation completion: {str(e)}"
                logger.error(error_msg, exc_info=True)
                raise SimulationError(error_msg) from e
                
        except Exception as e:
            try:
                self._transition_state(SimulationState.FAILED)
            except Exception as state_error:
                logger.error("Failed to transition to FAILED state: %s", str(state_error))
                
            error_msg = f"Simulation failed after {self._current_time:.2f} seconds: {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            # Try to compile partial results before raising the error
            try:
                results = self._compile_results()
                logger.info("Partial results compiled successfully")
            except Exception as compile_error:
                logger.error("Failed to compile partial results: %s", str(compile_error))
                results = None
                
            raise SimulationError(error_msg) from e
    
    def _compile_results(self) -> SimulationResult:
        """
        Compile the simulation results into a structured format.
        
        Returns:
            SimulationResult: The compiled simulation results.
            
        This method collects all relevant data from the simulation, including:
        - Simulation metadata (start/end times, duration, etc.)
        - Metrics and statistics
        - Token flow logs
        - Error information
        - Final state of the Petri net
        """
        end_time = datetime.now()
        duration = (end_time - self._start_time).total_seconds() if self._start_time else 0.0
        
        # Get final metrics
        metrics = {}
        if hasattr(self, '_metrics') and self._metrics:
            try:
                metrics.update(self._metrics.get_summary())
            except Exception as e:
                logger.error("Error getting metrics summary: %s", str(e), exc_info=True)
        
        # Get token statistics
        token_stats = {
            "total_created": 0,
            "total_completed": 0,
            "active": 0,
            "by_type": {}
        }
        
        # Count tokens by type and status
        if hasattr(self, '_token_flow_log') and self._token_flow_log:
            for entry in self._token_flow_log:
                if hasattr(entry, 'event_type'):
                    if entry.event_type == "token_creation":
                        token_stats["total_created"] += 1
                        token_type = entry.details.get("token_type", "unknown") if hasattr(entry, 'details') else "unknown"
                        token_stats["by_type"][token_type] = token_stats["by_type"].get(token_type, 0) + 1
                    elif entry.event_type in ("token_completion", "token_completed"):
                        token_stats["total_completed"] += 1
        
        # Count active tokens
        if hasattr(self, '_petri_net') and self._petri_net:
            active_tokens = 0
            try:
                # Get places from the Petri net
                if hasattr(self._petri_net, 'places'):
                    places = self._petri_net.places
                    if isinstance(places, dict):
                        # If places is a dict, iterate over values
                        for place in places.values():
                            if hasattr(place, 'tokens'):
                                active_tokens += len(place.tokens)
                    elif hasattr(places, '__iter__'):
                        # If places is iterable, iterate directly
                        for place in places:
                            if hasattr(place, 'tokens'):
                                active_tokens += len(place.tokens)
                            elif hasattr(place, 'token_count'):
                                active_tokens += place.token_count
            except Exception as e:
                logger.debug("Error counting active tokens: %s", str(e))
                active_tokens = 0
            
            token_stats["active"] = active_tokens
        
        # Compile error information
        errors = []
        if hasattr(self, '_errors') and self._errors:
            errors = [{
                "time": e.get("time", 0.0),
                "message": str(e.get("error", "Unknown error")),
                "event_type": e.get("event_type", "unknown"),
                "event_data": e.get("event_data", {})
            } for e in self._errors]
        
        # Prepare raw_results payload with core timing and token stats
        raw_results_payload: Dict[str, Any] = {
            "wall_clock_duration": duration,
            "simulation_duration": self._current_time,
            "token_stats": token_stats,
            "state": self._state.name if hasattr(self._state, 'name') else str(self._state)
        }

        # Attach per-resource time series (utilization, and optionally power/carbon) collected by MetricsCollector
        try:
            if hasattr(self, '_metrics') and self._metrics and getattr(self._metrics, 'resource_stats', None):
                utilization: Dict[str, List[Dict[str, float]]] = {}
                power: Dict[str, List[Dict[str, float]]] = {}
                carbon: Dict[str, List[Dict[str, float]]] = {}
                replicas: Dict[str, List[Dict[str, float]]] = {}

                for rid, stats in self._metrics.resource_stats.items():
                    try:
                        util_points = stats.metrics.get('utilization', [])
                        if util_points:
                            utilization[rid] = [{"time": float(p.time), "value": float(p.value)} for p in util_points]

                        power_points = stats.metrics.get('power_consumption', [])
                        if power_points:
                            power[rid] = [{"time": float(p.time), "value": float(p.value)} for p in power_points]

                        carbon_points = stats.metrics.get('carbon_emissions', [])
                        if carbon_points:
                            carbon[rid] = [{"time": float(p.time), "value": float(p.value)} for p in carbon_points]
                        replica_points = stats.metrics.get('replica_count', [])
                        if replica_points:
                            replicas[rid] = [{"time": float(p.time), "value": float(p.value)} for p in replica_points]
                    except Exception as e:
                        logger.debug("Failed to adapt metrics for resource %s: %s", rid, str(e))

                if utilization:
                    raw_results_payload.setdefault('utilization', utilization)
                    raw_results_payload.setdefault('resource_utilization', utilization)
                if power:
                    raw_results_payload.setdefault('power_watts', power)
                if carbon:
                    raw_results_payload.setdefault('carbon_kgco2e', carbon)
                if replicas:
                    raw_results_payload.setdefault('replica_count', replicas)
        except Exception as e:
            logger.debug("Failed to attach raw time series to results: %s", str(e))

        # Create the result object
        # Prefer locally captured token logs; fallback to metrics collector logs
        token_logs = []
        try:
            if hasattr(self, '_token_flow_log') and self._token_flow_log:
                token_logs = self._token_flow_log
            elif hasattr(self, '_metrics') and self._metrics and getattr(self._metrics, 'token_flow_log', None):
                token_logs = self._metrics.token_flow_log
            else:
                logger.info("Fallback: no explicit token logs found; latency analysis may be limited")
        except Exception:
            token_logs = []

        # Normalize token logs to plain dicts for canonical SimulationResult
        normalized_logs: list[dict] = []
        try:
            for ev in token_logs or []:
                if isinstance(ev, dict):
                    normalized_logs.append(ev)
                else:
                    # Pydantic BaseModel compatibility without hard import
                    dump = getattr(ev, "model_dump", None)
                    if callable(dump):
                        normalized_logs.append(dump())
                    else:
                        normalized_logs.append({
                            "timestamp": getattr(ev, "timestamp", None),
                            "event_type": getattr(ev, "event_type", None),
                            "details": getattr(ev, "details", {}) or {},
                        })
        except Exception:
            normalized_logs = []

        result = SimulationResult(
            start_time=self._start_time.timestamp() if self._start_time else None,
            end_time=end_time.timestamp(),
            status=SimulationStatus.COMPLETED,
            config_snapshot=self.config.to_dict() if hasattr(self, 'config') else {},
            resource_metrics={},  # Will be populated by conversion method
            token_flow_logs=normalized_logs,
            raw_results=raw_results_payload,
            metrics=metrics,
            error=errors[0] if errors else None,
            analysis_results=self._run_post_simulation_analysis()
        )
        
        logger.info(
            "Simulation completed in %.2f seconds (wall clock), %.2f simulated seconds. "
            "Processed %d events with %d errors.",
            duration,
            self._current_time,
            len(self._token_flow_log) if hasattr(self, '_token_flow_log') else 0,
            len(errors)
        )
        
        return result
        
    def _run_single_iteration(
        self,
        duration: float,
        progress_callback: Optional[Callable[[ProgressState], None]] = None,
    ) -> SimulationResult:
        """
        Run a single iteration of the simulation.
        
        Args:
            duration: The duration of the simulation iteration.
            progress_callback: Optional callback for progress updates.
            
        Returns:
            The simulation result.
            
        Raises:
            SimulationError: If the simulation encounters an error.
        """
        self._start_time = datetime.now()
        self._current_time = 0.0
        
        # Generate initial events
        self._generate_initial_events(duration)
        
        # Main simulation loop
        last_progress_update = 0.0
        completed_tokens = 0
        
        while self._event_queue and not self._stop_requested:
            event_processed = self.process_next_event()
            
            # Update progress periodically
            current_time = time.time()
            if current_time - last_progress_update >= 0.1:  # Update at most 10x per second
                self._update_progress(
                    duration, completed_tokens, progress_callback
                )
                last_progress_update = current_time
            
            # Check for simulation end
            if self._current_time >= duration:
                break
        
        # Final progress update
        self._end_time = datetime.now()
        self._update_progress(
            duration, completed_tokens, progress_callback, final=True
        )
        
        # Generate the final report
        return self._generate_final_report(completed_tokens)
    
    def _generate_initial_events(self, duration: float) -> None:
        """Generate initial events for the simulation."""
        # Schedule token creation events from workloads
        for workload in self._workloads:
            if hasattr(workload, 'generate_events'):
                for event_time, event_data in workload.generate_events(duration):
                    self.schedule_event(
                        timestamp=event_time,
                        event_type="token_creation",
                        event_data=event_data,
                        priority=0
                    )
        
        # Periodic statistics collection is handled internally via
        # `_update_metrics_if_needed()` driven by simulation time advancement.
        # No explicit `collect_stats` events are scheduled.
    
    def _update_progress(
        self,
        duration: float,
        completed_tokens: int,
        callback: Optional[Callable[[ProgressState], None]] = None,
        final: bool = False,
    ) -> None:
        """Update progress and notify the callback if provided."""
        if not callback:
            return
        
        pending_events = len(self._event_queue)
        events_per_sec = 0.0
        
        if self._start_time:
            elapsed = (datetime.now() - self._start_time).total_seconds()
            if elapsed > 0:
                events_processed = completed_tokens + len(self._metrics.token_flow_log)
                events_per_sec = events_processed / elapsed
        
        state = ProgressState(
            current_time=self._current_time,
            duration=duration,
            completed_tokens=completed_tokens,
            pending_events=pending_events,
            errors=len(self._errors),
            events_per_sec=events_per_sec,
            final_status="Completed" if final else None,
        )
        
        try:
            callback(state)
        except Exception as e:
            logger.warning("Error in progress callback: %s", str(e))
    
    def _generate_final_report(self, completed_tokens: int) -> SimulationResult:
        """Generate the final simulation report and attach post-hoc analysis."""
        if not self._start_time or not self._end_time:
            raise SimulationError("Simulation not properly started or completed")
        
        # Step 1: Generate the base report with all the original metrics.
        # This call remains exactly as it was.
        base_report = self._metrics.generate_report(
            config=self.config,
            start_time=self._start_time,
            end_time=self._end_time,
            completed_tokens=completed_tokens,
            active_tokens=sum(len(p.tokens) for p in self._petri_net.places.values())
            if self._petri_net else 0,
            error_count=len(self._errors),
        )

        # Step 2: Run the new, powerful analysis engine.
        # (This assumes you've already added the _run_post_simulation_analysis helper method).
        final_analysis = self._run_post_simulation_analysis()

        # Step 3: Attach the new analysis results to the base report.
        base_report.analysis_results = final_analysis
        
        # Step 4: Return the complete, enriched report.
        return base_report
    
    def stop(self) -> None:
        """
        Request the simulation to stop at the next opportunity.
        
        This method is thread-safe and idempotent, meaning it can be called multiple times
        without side effects. It sets a flag that will be checked during the next iteration
        of the simulation loop.
        
        Note:
            - The simulation may not stop immediately; it will complete the current event
              before checking the stop flag.
            - If the simulation is not running, this method will log a debug message
              but will not raise an error.
            - This method is safe to call from any thread.
            
        Example:
            >>> orchestrator = Orchestrator(config=my_config)
            >>> # In a separate thread or signal handler:
            >>> orchestrator.stop()  # Request the simulation to stop
        """
        try:
            with self._lock:
                if not hasattr(self, '_stop_requested'):
                    logger.debug("Stop requested but orchestrator not fully initialized")
                    return
                    
                if not self._stop_requested:  # Only log if this is a new stop request
                    self._stop_requested = True
                    logger.info(
                        "Simulation stop requested (current state: %s, time: %.2f)",
                        self._state.name,
                        getattr(self, '_current_time', 0.0)
                    )
                else:
                    logger.debug("Stop already requested, ignoring duplicate call")
                    
        except Exception as e:
            # Ensure we don't raise exceptions from a stop request
            logger.error(
                "Error while processing stop request: %s",
                str(e),
                exc_info=True
            )
            # Re-raise only if we can't recover
            if not hasattr(self, '_stop_requested'):
                raise RuntimeError("Failed to process stop request due to internal error") from e
                
    def reset(self) -> None:
        """
        Reset the orchestrator to its initial state.
        
        This method resets all internal state of the orchestrator, including:
        - Simulation state and timing information
        - Event queue and error tracking
        - Metrics collection
        - Model builder and workloads
        
        The method is idempotent and can be called multiple times without side effects.
        It's thread-safe and handles partial initialization states gracefully.
        
        Raises:
            RuntimeError: If a critical error occurs during reset that leaves the
                        orchestrator in an inconsistent state.
            
        Example:
            >>> orchestrator = Orchestrator(config=my_config)
            >>> # After a simulation run:
            >>> orchestrator.reset()  # Reset to initial state for a new simulation
        """
        try:
            with self._lock:
                # Log the reset attempt with current state for debugging
                current_state = getattr(self, '_state', 'UNINITIALIZED')
                logger.info(
                    "Initiating orchestrator reset (current state: %s, time: %.2f)",
                    current_state.name if hasattr(current_state, 'name') else current_state,
                    getattr(self, '_current_time', 0.0)
                )
                
                # Reset core simulation state
                self._state = SimulationState.INITIALIZING
                self._current_time = 0.0
                self._event_queue = []
                self._errors = []
                self._retry_count = 0
                self._stop_requested = False
                self._start_time = None
                self._end_time = None
                
                try:
                    # Reset metrics collector
                    if hasattr(self, 'config'):
                        self._metrics = MetricsCollector(self.config)
                    else:
                        logger.warning("Cannot reset metrics: config not available")
                        self._metrics = MetricsCollector(LEAFCloudConfig())
                    
                    # Reset model builder if available
                    if hasattr(self, '_model_builder') and self._model_builder is not None:
                        try:
                            self._model_builder.reset()
                        except Exception as e:
                            logger.error(
                                "Error resetting model builder: %s",
                                str(e),
                                exc_info=True
                            )
                            # Continue with reset even if model builder reset fails
                    
                    # Clear workloads
                    if hasattr(self, '_workloads'):
                        self._workloads = []
                    
                    logger.info("Orchestrator successfully reset to initial state")
                    
                except Exception as e:
                    # If we get here, we have a serious problem
                    error_msg = "Critical error during reset operation"
                    logger.critical(error_msg, exc_info=True)
                    self._state = SimulationState.FAILED
                    raise RuntimeError(f"{error_msg}: {str(e)}") from e
                
        except Exception as e:
            # Only log if it's not a runtime error (which we already logged)
            if not isinstance(e, RuntimeError):
                logger.error(
                    "Unexpected error during orchestrator reset: %s",
                    str(e),
                    exc_info=True
                )
            raise  # Re-raise the original exception
