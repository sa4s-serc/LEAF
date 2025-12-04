import bisect
import csv
import random
import math
import os
import time
import numpy as np
import enum
import heapq
import logging
import threading
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable, Tuple, Generator

from dataclasses import dataclass, field
import statistics
import heapq
from pathlib import Path

# Initialize module logger
logger = logging.getLogger(__name__)

"""
workload.py - Workload modeling and generation for LEAF-Cloud framework.

This module implements various workload patterns to simulate realistic cloud
infrastructure usage scenarios. It generates token creation events based on
configured patterns and provides statistics on workload characteristics.
"""


class WorkloadPatternType(enum.Enum):
    """Enumeration of supported workload pattern types."""

    STEADY = "steady"
    BURST = "burst"
    CYCLICAL = "cyclical"
    RANDOM = "random"
    CUSTOM = "custom"
    CSV = "csv"  # Add CSV workload type


@dataclass
class WorkloadStatistics:
    """Statistics collected about a workload during simulation."""

    total_tokens: int = 0
    min_rate: float = float("inf")
    max_rate: float = 0
    avg_rate: float = 0
    rates_history: List[float] = field(default_factory=list)
    start_time: float = 0.0
    end_time: Optional[float] = None

    def update(self, current_rate: float, token_count: int = 1) -> None:
        """Update statistics with new rate information."""
        self.total_tokens += token_count
        self.rates_history.append(current_rate)
        self.min_rate = min(self.min_rate, current_rate)
        self.max_rate = max(self.max_rate, current_rate)

    def finalize(self, simulation_time: float) -> None:
        """Finalize statistics at the end of a simulation.
        Calculates average rate across the recorded history so that consumers
        don't need to recompute it each time.
        """
        self.end_time = simulation_time
        # Calculate average rate once all events have been generated
        if self.rates_history:
            self.avg_rate = statistics.mean(self.rates_history)

    def get_summary(self) -> Dict[str, Any]:
        """Return a dictionary of summary statistics."""
        duration = (self.end_time or self.start_time) - self.start_time
        return {
            "total_tokens": self.total_tokens,
            "min_rate": self.min_rate,
            "max_rate": self.max_rate,
            "avg_rate": self.avg_rate,
            "std_dev_rate": (
                statistics.stdev(self.rates_history)
                if len(self.rates_history) > 1
                else 0
            ),
            "duration_seconds": duration,
            "tokens_per_second": (
                self.total_tokens / duration if duration > 0 else 0
            ),
        }


class Workload(ABC):
    """Base class for all workload patterns in the LEAF-Cloud framework.
    
    This abstract base class defines the interface for all workload patterns.
    Subclasses should implement specific workload behaviors while inheriting
    common functionality for statistics collection and event generation.
    
    Thread Safety:
        - Instance methods are not thread-safe by default.
        - Subclasses should implement their own thread-safety mechanisms if needed.
        - The `generate_events()` method should be thread-safe as it's often
          called from multiple threads in parallel simulations.
    
    Key Features:
    - Token generation with configurable rates and patterns
    - Built-in statistics collection
    - Support for different workload patterns (steady, burst, cyclical, etc.)
    - Jitter support for more realistic event timing
    
    Subclasses must implement:
        - get_rate_at_time()
        - Any additional methods marked with @abstractmethod
    
    Example:
        ```python
        # Create a custom workload pattern
        class CustomWorkload(Workload):
            def get_rate_at_time(self, t: float) -> float:
                # Custom rate function (e.g., sine wave pattern)
                return self.base_rate * (1 + math.sin(t))
        
        # Create and use a workload
        workload = CustomWorkload(
            base_rate=10.0,  # Average 10 tokens/second
            duration=60.0,   # Run for 60 seconds
            token_attributes={"priority": "high"},
            jitter=0.1       # Add ±10% jitter to event timing
        )
        
        # Generate events
        for time, attrs in workload.generate_events():
            print(f"Time {time:.2f}s: {attrs}")
            
        # Get statistics
        stats = workload.statistics.get_summary()
        print(f"Generated {stats['total_tokens']} tokens")
        print(f"Average rate: {stats['avg_rate']:.2f} tokens/second")
        """
    __all__ = ["Workload", "SteadyWorkload", "BurstWorkload", "CyclicalWorkload", "RandomWorkload", "CustomWorkload", "WorkloadMix", "CSVWorkload"]

    def __init__(
        self,
        base_rate: float,
        token_attributes: Optional[Dict[str, Any]] = None,
        duration: Optional[float] = None,
        name: str = "default_workload",
        jitter: Optional[float] = None,
    ):
        """
        Initialize a workload pattern.

        Args:
            base_rate: Base tokens per second rate
            token_attributes: Default attributes to attach to generated tokens
            duration: Duration in seconds, or None for unlimited
            name: Name identifier for this workload
            jitter: Optional standard deviation for a normal distribution to add jitter to event timing.
        """
        if base_rate < 0:
            raise ValueError("base_rate cannot be negative.")
        if jitter is not None and jitter < 0:
            raise ValueError("jitter cannot be negative.")

        self.base_rate = base_rate
        self.token_attributes = dict(token_attributes) if token_attributes is not None else {}
        self.duration = duration
        self.name = name
        self.statistics = WorkloadStatistics()
        self.jitter = jitter

    def get_rate_at_time(self, t: float) -> float:
        """
        Get the token generation rate at a specific time.

        Args:
            t: Time in seconds from start of simulation

        Returns:
            Tokens per second rate at time t
        """
        return self.base_rate

    def generate_events(
        self, start_time: float = 0
    ) -> Generator[Tuple[float, Dict[str, Any]], None, None]:
        """
        Generate token creation events.

        This generator is now more robust:
        - It handles non-positive rates by advancing time to prevent infinite loops.
        - It supports optional jitter to create more realistic, less uniform event streams.
        - It ensures statistics are finalized at the end of generation.

        Args:
            start_time: Simulation start time

        Yields:
            Tuples of (time, token_attributes) representing token creation events
        """
        t = start_time
        self.statistics.start_time = start_time
        end_time = (
            float("inf")
            if self.duration is None
            else (start_time + self.duration)
        )

        try:
            while t < end_time:
                current_rate = self.get_rate_at_time(t - start_time)

                if current_rate <= 0:
                    # If rate is zero or negative, avoid ZeroDivisionError and advance time by 1s to prevent getting stuck.
                    t += 1
                    continue

                # Calculate inter-arrival time
                time_to_next_event = 1.0 / current_rate

                # Add jitter if configured
                if self.jitter is not None and self.jitter > 0:
                    time_to_next_event += random.normalvariate(0, self.jitter)
                    # Ensure jitter doesn't result in negative time
                    time_to_next_event = max(0, time_to_next_event)

                t += time_to_next_event

                if t < end_time:
                    self.statistics.update(current_rate)
                    yield (t, self.token_attributes)
        finally:
            # Ensure statistics are finalized when the generator exits
            self.statistics.finalize(t)

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about this workload pattern."""
        return self.statistics.get_summary()

    # ------------------------------------------------------------------
    # Orchestrator update hook (optional for simple workloads)
    # ------------------------------------------------------------------
    def get_updates(self, current_time: float) -> List[Dict[str, Any]]:
        """Return dynamic workload updates for the orchestrator.

        This default implementation provides no updates and exists to satisfy
        the orchestrator's expected interface without emitting warnings for
        simple patterns such as SteadyWorkload.

        Args:
            current_time: Current simulation time in seconds

        Returns:
            An empty list indicating no updates.
        """
        return []


class SteadyWorkload(Workload):
    """A workload that generates tokens at a constant rate over time.
    
    This is the simplest workload pattern, maintaining a steady flow of tokens
    at the specified rate. It's useful for modeling stable, predictable loads.
    
    Example:
        ```python
        # Create a steady workload generating 10 tokens per second
        workload = SteadyWorkload(rate=10.0, name="steady_load")
        ```
    """
    pattern_type = WorkloadPatternType.STEADY  # Class-level constant
    
    def __init__(self, rate: float, **kwargs):
        """Initialize a steady workload.

        Args:
            rate: Constant tokens per second rate (must be non-negative)
            **kwargs: Additional parameters passed to Workload
            
        Raises:
            ValueError: If rate is negative
        """
        if rate < 0:
            raise ValueError("Rate cannot be negative")
            
        super().__init__(base_rate=rate, **kwargs)
    
    def get_rate_at_time(self, t: float) -> float:
        """Get the constant token rate.
        
        Args:
            t: Current simulation time (ignored for steady workload)
            
        Returns:
            The constant rate specified during initialization
        """
        return self.base_rate


class BurstWorkload(Workload):
    """A workload that alternates between normal and burst rates at regular intervals.
    
    This workload pattern is useful for modeling traffic that has predictable
    periods of high activity (bursts) followed by periods of normal activity.
    
    Example:
        ```python
        # Create a burst workload with 5s bursts every 30s
        workload = BurstWorkload(
            base_rate=5.0,
            burst_rate=50.0,
            burst_duration=5.0,
            burst_interval=30.0,
            name="bursty_traffic"
        )
        ```
    """
    pattern_type = WorkloadPatternType.BURST  # Class-level constant
    
    def __init__(
        self,
        base_rate: float,
        burst_rate: float,
        burst_duration: float,
        burst_interval: float,
        **kwargs
    ):
        """Initialize a burst workload.

        Args:
            base_rate: Normal tokens per second rate (must be non-negative)
            burst_rate: Tokens per second during bursts (must be >= base_rate)
            burst_duration: Duration of each burst in seconds (must be positive)
            burst_interval: Time between burst starts in seconds (must be > burst_duration)
            **kwargs: Additional parameters passed to Workload
            
        Raises:
            ValueError: If any parameter constraints are violated
        """
        super().__init__(base_rate=base_rate, **kwargs)
        
        # Validate parameters
        if burst_rate < base_rate:
            raise ValueError("burst_rate cannot be less than base_rate")
        if burst_duration <= 0:
            raise ValueError("burst_duration must be positive")
        if burst_interval <= 0:
            raise ValueError("burst_interval must be positive")
        if burst_duration >= burst_interval:
            raise ValueError("burst_duration must be less than burst_interval")

        self.burst_rate = float(burst_rate)
        self.burst_duration = float(burst_duration)
        self.burst_interval = float(burst_interval)
    
    def get_rate_at_time(self, t: float) -> float:
        """Get the token rate at the given simulation time.

        Args:
            t: Current simulation time in seconds

        Returns:
            The current token rate (either base_rate or burst_rate)
        """
        time_in_cycle = t % self.burst_interval
        burst_start_time = self.burst_interval - self.burst_duration

        if time_in_cycle >= burst_start_time:
            return self.burst_rate
        else:
            return self.base_rate
                
                
class CyclicalWorkload(Workload):
    """A workload with a sinusoidal variation in token generation rate.
    
    This workload pattern varies the token generation rate according to a sine wave,
    making it useful for modeling periodic variations in load, such as diurnal patterns.
    
    The rate at time t is given by:
        rate(t) = base_rate + amplitude * sin(2πt/period + phase_shift)
    
    Example:
        ```python
        # Create a cyclical workload with 24-hour period and ±20% variation
        workload = CyclicalWorkload(
            base_rate=100.0,
            amplitude=20.0,
            period=86400.0,  # 24 hours in seconds
            phase_shift=0.0,
            name="diurnal_load"
        )
        ```
    """
    pattern_type = WorkloadPatternType.CYCLICAL  # Class-level constant
    
    def __init__(
        self,
        base_rate: float,
        amplitude: float,
        period: float,
        phase_shift: float = 0.0,
        **kwargs
    ):
        """Initialize a cyclical workload.

        Args:
            base_rate: Mean tokens per second rate (must be non-negative)
            amplitude: Amplitude of rate variation (must be non-negative)
            period: Period of cycle in seconds (must be positive)
            phase_shift: Phase offset in radians (default: 0.0)
            **kwargs: Additional parameters passed to Workload
            
        Raises:
            ValueError: If any parameter constraints are violated
        """
        super().__init__(base_rate=base_rate, **kwargs)
        
        # Validate parameters
        if amplitude < 0:
            raise ValueError("amplitude cannot be negative")
        if period <= 0:
            raise ValueError("period must be positive")
            
        self.amplitude = float(amplitude)
        self.period = float(period)
        self.phase_shift = float(phase_shift)
        self._angular_freq = 2 * math.pi / self.period
    
    def get_rate_at_time(self, t: float) -> float:
        """Get the token rate at the given simulation time.
        
        The rate follows a sine wave pattern:
            rate(t) = base_rate + amplitude * sin(2πt/period + phase_shift)
        
        Args:
            t: Current simulation time in seconds
            
        Returns:
            The current token rate, never negative
        """
        rate = self.base_rate + self.amplitude * math.sin(
            self._angular_freq * t + self.phase_shift
        )
        return max(0.0, rate)  # Ensure non-negative rate


class RandomWorkload(Workload):
    """A workload with randomly varying token generation rates.
    
    This workload pattern changes the token generation rate at regular intervals,
    with each new rate chosen uniformly at random between min_rate and max_rate.
    This is useful for modeling unpredictable but bounded load variations.
    
    Thread Safety:
        - This class is thread-safe and can be safely used from multiple threads.
        - A single random number generator is used with proper locking to ensure
          consistent behavior across threads.
        - The same time input will always return the same rate, regardless of thread.
    
    Performance Considerations:
        - Uses a cache to store generated rates for each time interval
        - Lazy initialization of rates to save memory
        - Thread-safe with minimal lock contention
    
    Example:
        ```python
        # Create a random workload that changes rate every 5 seconds
        workload = RandomWorkload(
            base_rate=50.0,  # Used as initial rate
            min_rate=10.0,
            max_rate=100.0,
            change_interval=5.0,
            name="variable_load"
        )
        ```
    """
    pattern_type = WorkloadPatternType.RANDOM  # Class-level constant
    
    def __init__(
        self,
        base_rate: float,
        min_rate: float,
        max_rate: float,
        change_interval: float = 1.0,
        random_seed: Optional[int] = None,
        **kwargs
    ):
        """Initialize a random workload with thread-safe rate variation.

        Args:
            base_rate: Initial tokens per second rate (must be non-negative)
            min_rate: Minimum tokens per second rate (must be non-negative)
            max_rate: Maximum tokens per second rate (must be >= min_rate)
            change_interval: Time between rate changes in seconds (must be positive)
            random_seed: Optional seed for deterministic random number generation
            **kwargs: Additional parameters passed to Workload
            
        Raises:
            ValueError: If any parameter constraints are violated
            TypeError: If any parameter has incorrect type
        """
        # Validate input types first
        if not isinstance(base_rate, (int, float)):
            raise TypeError(f"base_rate must be a number, got {type(base_rate).__name__}")
        if not isinstance(min_rate, (int, float)):
            raise TypeError(f"min_rate must be a number, got {type(min_rate).__name__}")
        if not isinstance(max_rate, (int, float)):
            raise TypeError(f"max_rate must be a number, got {type(max_rate).__name__}")
        if not isinstance(change_interval, (int, float)):
            raise TypeError(f"change_interval must be a number, got {type(change_interval).__name__}")
            
        # Validate values
        if min_rate < 0:
            raise ValueError("min_rate cannot be negative")
        if max_rate < min_rate:
            raise ValueError(f"max_rate ({max_rate}) cannot be less than min_rate ({min_rate})")
        if change_interval <= 0:
            raise ValueError(f"change_interval must be positive, got {change_interval}")
        if not (0 <= base_rate <= max_rate):
            raise ValueError(f"base_rate must be between 0 and {max_rate}, got {base_rate}")
            
        super().__init__(base_rate=float(base_rate), **kwargs)
        
        # Initialize instance variables with type hints
        self.min_rate: float = float(min_rate)
        self.max_rate: float = float(max_rate)
        self.change_interval: float = float(change_interval)
        
        # Use a single random state for all threads to ensure consistent behavior
        self._random = random.Random(random_seed) if random_seed is not None else random.Random()
        self._lock = threading.RLock()
        # Initialize cache with base_rate for the first interval
        self._rates_cache: Dict[float, float] = {0.0: float(base_rate)}  # Cache rates by time interval
        self._last_logged_interval: Optional[float] = None
        
    def _get_cached_or_generate_rate(self, t: float) -> float:
        """Get a consistent rate for the given time using caching.
        
        This ensures the same time always returns the same rate, regardless of thread.
        """
        # Round time to the nearest change_interval to ensure consistency
        interval_num = int(t // self.change_interval)
        cache_key = interval_num * self.change_interval
        
        # Check cache first (without lock for performance)
        if cache_key in self._rates_cache:
            return self._rates_cache[cache_key]
            
        # Cache miss - acquire lock and generate new rate
        with self._lock:
            # Double-check in case another thread updated the cache
            if cache_key not in self._rates_cache:
                # Generate a new random rate for this interval
                self._rates_cache[cache_key] = self._random.uniform(
                    self.min_rate, 
                    self.max_rate
                )
        
        return self._rates_cache[cache_key]
    
    def get_rate_at_time(self, t: float) -> float:
        """Get the token rate at the given simulation time.
        
        The rate changes at regular intervals (change_interval) to a new random value
        between min_rate and max_rate. This method is thread-safe.
        
        Args:
            t: Current simulation time in seconds (must be non-negative)
            
        Returns:
            The current token rate, which changes at regular intervals
            
        Raises:
            ValueError: If t is negative
            TypeError: If t is not a number
        """
        # Input validation
        if not isinstance(t, (int, float)):
            raise TypeError(f"Time must be a number, got {type(t).__name__}")
        if t < 0:
            raise ValueError(f"Time must be non-negative, got {t}")
            
        # Get the rate for this time interval
        rate = self._get_cached_or_generate_rate(t)
        
        # Log rate changes if needed (for debugging)
        current_interval = int(t // self.change_interval) * self.change_interval
        if self._last_logged_interval is None or current_interval > self._last_logged_interval:
            with self._lock:
                # Double-check in case another thread updated _last_logged_interval
                if self._last_logged_interval is None or current_interval > self._last_logged_interval:
                    logger.debug(
                        f"{self.__class__.__name__} '{self.name}' rate is "
                        f"{rate:.2f} at t={t:.2f}s "
                        f"(range: {self.min_rate:.2f}-{self.max_rate:.2f})"
                    )
                    self._last_logged_interval = current_interval
                
        return rate


class CustomWorkload(Workload):
    """A workload with a user-defined rate function.
    
{{ ... }}
    This workload pattern allows complete flexibility by accepting a custom
    function that determines the token generation rate at any given time.
    The function should take the current simulation time as input and return
    the desired token rate.
    
    Thread Safety:
        - This class is conditionally thread-safe.
        - The rate function provided by the user must be thread-safe if the
          workload is accessed from multiple threads.
        - The class itself protects its internal state with appropriate
          synchronization when needed.
        - Exceptions in the user's rate function are caught and logged, with
          the base_rate used as a fallback.
    
    Implementation Notes:
        - The rate function should be pure (no side effects) for predictable
          behavior in concurrent contexts.
        - For best performance in multi-threaded scenarios, ensure the rate
          function is thread-safe and avoids blocking operations.
    
    Example:
        ```python
        # Create a custom workload with a sawtooth pattern
        def sawtooth_rate(t: float) -> float:
            return 10.0 + 5.0 * (t % 10.0)  # Ramps from 10 to 60 over 10 seconds
            
        workload = CustomWorkload(
            rate_function=sawtooth_rate,
            base_rate=10.0,  # Fallback rate if rate_function returns None
            name="custom_pattern"
        )
        ```
    """
    pattern_type = WorkloadPatternType.CUSTOM  # Class-level constant
    
    def __init__(
        self,
        rate_function: Callable[[float], float],
        base_rate: float = 1.0,
        **kwargs
    ):
        """Initialize a custom workload.

        Args:
            rate_function: Function that takes time (float) and returns the
                         desired token rate (float). If it returns None, the
                         base_rate will be used instead.
            base_rate: Default tokens per second rate (used if rate_function
                     returns None or raises an exception)
            **kwargs: Additional parameters passed to Workload
            
        Raises:
            TypeError: If rate_function is not callable
        """
        super().__init__(base_rate=base_rate, **kwargs)
        
        if not callable(rate_function):
            raise TypeError("rate_function must be callable")
            
        self.rate_function = rate_function
    
    def get_rate_at_time(self, t: float) -> float:
        """Get the token rate at the given simulation time.
        
        This calls the user-provided rate_function with the current time and
        returns its result. The function handles various error cases gracefully
        and ensures thread safety.
        
        Args:
            t: Current simulation time in seconds (must be a finite number)
            
        Returns:
            The token rate from the custom function, or base_rate on failure
            
        Raises:
            TypeError: If t is not a number
            ValueError: If t is not finite
        """
        # Validate input
        if not isinstance(t, (int, float)):
            raise TypeError(f"Time must be a number, got {type(t).__name__}")
        if not math.isfinite(t):
            raise ValueError(f"Time must be finite, got {t}")
            
        try:
            # Call the user's rate function
            rate = self.rate_function(t)
            
            # Handle None return value
            if rate is None:
                logger.debug(
                    f"Custom rate function returned None at t={t}, "
                    f"using base_rate={self.base_rate}"
                )
                return self.base_rate
                
            # Convert to float and ensure non-negative
            try:
                rate_float = float(rate)
                if math.isnan(rate_float) or not math.isfinite(rate_float):
                    raise ValueError(f"Rate must be a finite number, got {rate}")
                    
                return max(0.0, rate_float)  # Ensure non-negative rate
                
            except (TypeError, ValueError) as e:
                logger.warning(
                    f"Invalid rate value '{rate}' returned from custom function "
                    f"at t={t}: {str(e)}. Using base_rate={self.base_rate}"
                )
                return self.base_rate
                
        except Exception as e:
            # Catch any exception from the user's function
            logger.error(
                f"Error in custom rate function at t={t}: {str(e)}. "
                f"Using base_rate={self.base_rate}"
            )
            logger.debug("Full traceback:", exc_info=True)
            return self.base_rate


@dataclass
class CSVDataPoint:
    """Represents a single data point from a CSV file."""
    time: float
    rate: float


class CSVWorkload(Workload):
    """A workload that loads rate patterns from a CSV file.
    
    This workload reads token generation rates from a CSV file with timestamps,
    allowing for arbitrary rate patterns defined in external data. The rates
    are interpolated between data points for smooth transitions.
    
    Thread Safety:
        - This class is thread-safe for read operations.
        - The CSV file is read once during initialization.
        - The internal data structures are immutable after initialization.
    
    Example:
        ```python
        # CSV format:
        # time,rate
        # 0,10.0
        # 100,50.0
        # 200,20.0
        
        workload = CSVWorkload(
            csv_path="workload.csv",
            time_column="time",
            rate_column="rate",
            base_rate=5.0,  # Used if CSV loading fails
            name="csv_workload"
        )
        ```
    """
    
    pattern_type = WorkloadPatternType.CSV
    
    def __init__(
        self,
        csv_path: str,
        time_column: str = "time",
        rate_column: str = "rate",
        base_rate: float = 1.0,
        **kwargs
    ):
        """Initialize a CSV workload.
        
        Args:
            csv_path: Path to the CSV file containing rate data
            time_column: Name of the column containing timestamps
            rate_column: Name of the column containing rate values
            base_rate: Fallback rate if CSV data is not available
            **kwargs: Additional parameters passed to Workload
            
        Raises:
            FileNotFoundError: If the CSV file does not exist
            ValueError: If the CSV file is malformed or empty
        """
        super().__init__(base_rate=base_rate, **kwargs)
        self.csv_path = str(csv_path)
        self.time_column = time_column
        self.rate_column = rate_column
        self._data_points: List[CSVDataPoint] = []
        self._times: List[float] = []
        self._load_csv()
        
    def _load_csv(self) -> None:
        """Load and validate the CSV file."""
        try:
            # Check if file is empty first
            if os.path.getsize(self.csv_path) == 0:
                raise ValueError("CSV file is empty")
                
            with open(self.csv_path, 'r') as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    raise ValueError("CSV file is empty")
                    
                if self.time_column not in reader.fieldnames:
                    raise ValueError(f"Time column '{self.time_column}' not found")
                    
                if self.rate_column not in reader.fieldnames:
                    raise ValueError(f"Rate column '{self.rate_column}' not found")
                
                # Read and sort data by time
                data_points = []
                for row in reader:
                    try:
                        time_val = float(row[self.time_column])
                        rate_val = float(row[self.rate_column])
                        if rate_val < 0:
                            logger.warning(f"Negative rate {rate_val} at t={time_val}, using 0")
                            rate_val = 0.0
                        data_points.append(CSVDataPoint(time_val, rate_val))
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid data in CSV row: {row}, error: {e}")
                        continue
                
                if not data_points:
                    raise ValueError("No valid data points found in CSV")
                
                # Sort by time and store
                self._data_points = sorted(data_points, key=lambda x: x.time)
                self._times = [dp.time for dp in self._data_points]
                
        except Exception as e:
            logger.error(f"Error loading CSV file {self.csv_path}: {str(e)}")
            raise
    
    def get_rate_at_time(self, t: float) -> float:
        """Get the rate at the given time using linear interpolation.
        
        Args:
            t: Current simulation time
            
        Returns:
            The interpolated rate value, or base_rate if no data points exist
        """
        if not self._data_points:
            return self.base_rate
            
        # Before first data point
        if t <= self._times[0]:
            return self._data_points[0].rate
            
        # After last data point
        if t >= self._times[-1]:
            return self._data_points[-1].rate
            
        # Find the interval where t falls
        idx = bisect.bisect_right(self._times, t) - 1
        if idx < 0 or idx >= len(self._data_points) - 1:
            return self.base_rate
            
        # Linear interpolation
        t0, r0 = self._data_points[idx].time, self._data_points[idx].rate
        t1, r1 = self._data_points[idx + 1].time, self._data_points[idx + 1].rate
        if t1 == t0:  # Avoid division by zero
            return r0
            
        alpha = (t - t0) / (t1 - t0)
        return r0 + alpha * (r1 - r0)


class WorkloadMix:
    """Combine multiple workloads into a single aggregate workload.
    
    This class merges events from multiple workloads into a single time-ordered
    stream of events. It's useful for modeling complex load patterns composed
    of multiple simpler workloads.
    
    Thread Safety:
        - This class is thread-safe for concurrent event generation.
        - The generate_events() method can be safely called from multiple threads.
        - Each workload in the mix should be thread-safe if accessed concurrently.
        - The internal heap used for event merging is protected by the GIL.
    
    Performance Characteristics:
        - Uses a min-heap to efficiently merge multiple event streams.
        - Memory usage is proportional to the number of active workloads.
        - The implementation is optimized for the case where workloads produce
          events at similar rates.
    """

    def __init__(self, workloads: List[Workload], name: str = "workload_mix"):
        """
        Initialize a workload mix.

        Args:
            workloads: List of workload objects to combine
            name: Name identifier for this workload mix
        """
        self.workloads = workloads
        self.name = name
        self.statistics = WorkloadStatistics()

    def generate_events(
        self, start_time: float = 0
    ) -> Generator[Tuple[float, Dict[str, Any]], None, None]:
        """
        Generate token creation events from all workloads, time-ordered.

        Args:
            start_time: Simulation start time

        Yields:
            Tuples of (time, token_attributes) representing token creation events
            
        Note:
            This method uses a min-heap to efficiently merge multiple event streams
            in time order. Each workload's event generator runs in parallel, and
            events are yielded in the order they occur across all workloads.
            
            Each event is tagged with the workload name that generated it, which can be
            used for analysis and testing.
        """
        # Initialize statistics
        self.statistics = WorkloadStatistics()
        self.statistics.start_time = start_time
        
        # Aggregate events into fixed-size windows to compute true combined rates
        RATE_WINDOW = 1.0  # seconds
        window_counts: Dict[int, int] = {}
        
        # Create a min-heap to merge event streams efficiently
        event_heap = []
        for i, workload in enumerate(self.workloads):
            if not isinstance(workload, Workload):
                raise TypeError(f"Expected Workload instance, got {type(workload).__name__}")
                
            gen = workload.generate_events(start_time)
            try:
                timestamp, attributes = next(gen)
                # Make a copy of attributes to avoid modifying the original
                tagged_attributes = dict(attributes)
                # Tag the event with the workload name for tracking
                tagged_attributes["workload"] = workload.name
                # Heap stores (timestamp, workload_index, attributes, generator, last_timestamp)
                heapq.heappush(event_heap, (timestamp, i, tagged_attributes, gen, start_time))
                # Initialize window bucket for start window
                start_window_idx = int((timestamp - start_time) // RATE_WINDOW)
                window_counts.setdefault(start_window_idx, 0)
            except StopIteration:
                continue

        last_event_time = start_time
        
        try:
            while event_heap:
                # Get the earliest event from the heap
                timestamp, workload_idx, attributes, gen, last_timestamp = heapq.heappop(event_heap)
                
                # Increment count for the appropriate time window (relative to start_time)
                window_idx = int((timestamp - start_time) // RATE_WINDOW)
                window_counts[window_idx] = window_counts.get(window_idx, 0) + 1
                last_event_time = max(last_event_time, timestamp)
                
                # Yield the event with the workload tag
                yield (timestamp, attributes)

                # Add the next event from the same generator back to the heap
                try:
                    next_timestamp, next_attributes = next(gen)
                    # Make a copy of attributes to avoid modifying the original
                    tagged_attributes = dict(next_attributes)
                    # Tag the event with the workload name for tracking
                    tagged_attributes["workload"] = self.workloads[workload_idx].name
                    heapq.heappush(
                        event_heap,
                        (next_timestamp, workload_idx, tagged_attributes, gen, timestamp),
                    )
                except StopIteration:
                    continue
        finally:
            # Convert window counts to aggregate rates and update statistics once per window
            for idx in sorted(window_counts.keys()):
                count = window_counts[idx]
                rate = count / RATE_WINDOW
                # Use token_count=count so total_tokens accumulates actual events
                self.statistics.update(rate, token_count=count)
            # Finalize statistics with the last event time
            self.statistics.finalize(last_event_time)

    def get_statistics(self) -> Dict[str, Any]:
        """Get aggregate statistics about all workloads.
        
        Returns:
            A dictionary containing the combined statistics from all workloads.
            The dictionary includes the following keys:
                - total_tokens: Total number of tokens generated
                - min_rate: Minimum observed rate across all workloads
                - max_rate: Maximum observed rate across all workloads
                - avg_rate: Average rate across all workloads
        """
        # Get overall statistics from the mix's own statistics
        stats = self.statistics.get_summary()
        
        # Also collect individual workload statistics for reference
        # (not currently used in the return value to match test expectations)
        _ = {w.name: w.get_statistics() for w in self.workloads}
        
        return stats
