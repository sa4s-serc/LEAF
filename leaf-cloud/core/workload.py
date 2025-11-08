import random
import math
import time
import enum
from typing import List, Dict, Any, Optional, Callable, Tuple, Generator
from dataclasses import dataclass, field
import statistics

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


@dataclass
class WorkloadStatistics:
    """Statistics collected about a workload during simulation."""
    total_tokens: int = 0
    min_rate: float = float('inf')
    max_rate: float = 0
    avg_rate: float = 0
    rates_history: List[float] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    
    def update(self, current_rate: float, token_count: int = 1) -> None:
        """Update statistics with new rate information."""
        self.total_tokens += token_count
        self.rates_history.append(current_rate)
        self.min_rate = min(self.min_rate, current_rate)
        self.max_rate = max(self.max_rate, current_rate)
        self.avg_rate = self.total_tokens / len(self.rates_history) if self.rates_history else 0
    
    def finalize(self) -> None:
        """Finalize statistics at the end of a simulation."""
        self.end_time = time.time()
    
    def get_summary(self) -> Dict[str, Any]:
        """Return a dictionary of summary statistics."""
        duration = (self.end_time or time.time()) - self.start_time
        return {
            "total_tokens": self.total_tokens,
            "min_rate": self.min_rate,
            "max_rate": self.max_rate,
            "avg_rate": self.avg_rate,
            "std_dev_rate": statistics.stdev(self.rates_history) if len(self.rates_history) > 1 else 0,
            "duration_seconds": duration,
            "tokens_per_second": self.total_tokens / duration if duration > 0 else 0
        }


class Workload:
    """Base class for all workload patterns."""
    
    def __init__(
        self, 
        base_rate: float,
        token_attributes: Dict[str, Any] = {},
        duration: Optional[float] = None,
        name: str = "default_workload"
    ):
        """
        Initialize a workload pattern.
        
        Args:
            base_rate: Base tokens per second rate
            token_attributes: Default attributes to attach to generated tokens
            duration: Duration in seconds, or None for unlimited
            name: Name identifier for this workload
        """
        self.base_rate = base_rate
        self.token_attributes = token_attributes or {}
        self.duration = duration
        self.name = name
        self.statistics = WorkloadStatistics()
        self.pattern_type = WorkloadPatternType.STEADY
        
    def get_rate_at_time(self, t: float) -> float:
        """
        Get the token generation rate at a specific time.
        
        Args:
            t: Time in seconds from start of simulation
            
        Returns:
            Tokens per second rate at time t
        """
        return self.base_rate
    
    def generate_events(self, start_time: float = 0) -> Generator[Tuple[float, Dict[str, Any]], None, None]:
        """
        Generate token creation events.
        
        Args:
            start_time: Simulation start time
            
        Yields:
            Tuples of (time, token_attributes) representing token creation events
        """
        t = start_time
        end_time = None if self.duration is None else (start_time + self.duration)
        
        while end_time is None or t < end_time:
            current_rate = self.get_rate_at_time(t - start_time)
            
            if current_rate > 0:
                # Calculate time to next token based on current rate
                time_to_next = 1.0 / current_rate
                t += time_to_next
                
                if end_time is None or t <= end_time:
                    # Create a new token with current time and attributes
                    token_attrs = self.token_attributes.copy()
                    token_attrs.update({
                        "creation_time": t,
                        "workload_name": self.name
                    })
                    
                    self.statistics.update(current_rate)
                    yield (t, token_attrs)
            else:
                # If rate is zero or negative, advance time by a small amount
                t += 0.1
                
        self.statistics.finalize()
        
    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about this workload pattern."""
        return self.statistics.get_summary()


class SteadyWorkload(Workload):
    """Workload with constant rate over time."""
    
    def __init__(self, rate: float, **kwargs):
        """
        Initialize a steady workload.
        
        Args:
            rate: Constant tokens per second rate
            **kwargs: Additional parameters passed to Workload
        """
        super().__init__(base_rate=rate, **kwargs)
        self.pattern_type = WorkloadPatternType.STEADY


class BurstWorkload(Workload):
    """Workload with periodic bursts of high activity."""
    
    def __init__(
        self, 
        base_rate: float,
        burst_rate: float,
        burst_duration: float,
        burst_interval: float,
        **kwargs
    ):
        """
        Initialize a burst workload.
        
        Args:
            base_rate: Normal tokens per second rate
            burst_rate: Burst tokens per second rate
            burst_duration: Duration of each burst in seconds
            burst_interval: Time between burst starts in seconds
            **kwargs: Additional parameters passed to Workload
        """
        super().__init__(base_rate=base_rate, **kwargs)
        self.burst_rate = burst_rate
        self.burst_duration = burst_duration
        self.burst_interval = burst_interval
        self.pattern_type = WorkloadPatternType.BURST
        
    def get_rate_at_time(self, t: float) -> float:
        """Get token rate, which alternates between base and burst rates."""
        cycle_position = t % self.burst_interval
        if cycle_position < self.burst_duration:
            return self.burst_rate
        return self.base_rate


class CyclicalWorkload(Workload):
    """Workload with sinusoidal variation over time."""
    
    def __init__(
        self,
        base_rate: float,
        amplitude: float,
        period: float,
        phase_shift: float = 0.0,
        **kwargs
    ):
        """
        Initialize a cyclical workload.
        
        Args:
            base_rate: Mean tokens per second rate
            amplitude: Amplitude of rate variation
            period: Period of cycle in seconds
            phase_shift: Offset in radians for the sine wave
            **kwargs: Additional parameters passed to Workload
        """
        super().__init__(base_rate=base_rate, **kwargs)
        self.amplitude = amplitude
        self.period = period
        self.phase_shift = phase_shift
        self.pattern_type = WorkloadPatternType.CYCLICAL
        
    def get_rate_at_time(self, t: float) -> float:
        """Get token rate based on sinusoidal function."""
        angular_freq = 2 * math.pi / self.period
        rate = self.base_rate + self.amplitude * math.sin(angular_freq * t + self.phase_shift)
        return rate if rate > 0 else 0  # Ensure non-negative rate


class RandomWorkload(Workload):
    """Workload with random rate variations."""
    
    def __init__(
        self,
        base_rate: float,
        min_rate: float,
        max_rate: float,
        change_interval: float = 1.0,
        **kwargs
    ):
        """
        Initialize a random workload.
        
        Args:
            base_rate: Mean tokens per second rate
            min_rate: Minimum tokens per second rate
            max_rate: Maximum tokens per second rate
            change_interval: Time between rate changes in seconds
            **kwargs: Additional parameters passed to Workload
        """
        super().__init__(base_rate=base_rate, **kwargs)
        self.min_rate = min_rate
        self.max_rate = max_rate
        self.change_interval = change_interval
        self.pattern_type = WorkloadPatternType.RANDOM
        self._last_change_time = 0
        self._current_rate = base_rate
        
    def get_rate_at_time(self, t: float) -> float:
        """Get token rate, randomly changing at specified intervals."""
        interval_index = int(t / self.change_interval)
        
        if interval_index > self._last_change_time:
            self._last_change_time = interval_index
            self._current_rate = random.uniform(self.min_rate, self.max_rate)
        
        return self._current_rate


class CustomWorkload(Workload):
    """Workload with a custom rate function."""
    
    def __init__(
        self, 
        rate_function: Callable[[float], float],
        base_rate: float = 1.0,
        **kwargs
    ):
        """
        Initialize a custom workload.
        
        Args:
            rate_function: Function taking time as input and returning rate
            base_rate: Default rate if function returns None
            **kwargs: Additional parameters passed to Workload
        """
        super().__init__(base_rate=base_rate, **kwargs)
        self.rate_function = rate_function
        self.pattern_type = WorkloadPatternType.CUSTOM
        
    def get_rate_at_time(self, t: float) -> float:
        """Get token rate from the custom function."""
        rate = self.rate_function(t)
        # If function returns None, use base rate
        return self.base_rate if rate is None else rate


class WorkloadMix:
    """Combine multiple workloads into a single aggregate workload."""
    
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
        
    def generate_events(self, start_time: float = 0) -> Generator[Tuple[float, Dict[str, Any]], None, None]:
        """
        Generate token creation events from all workloads, time-ordered.
        
        Args:
            start_time: Simulation start time
            
        Yields:
            Tuples of (time, token_attributes) representing token creation events
        """
        # Create generators for each workload
        generators = [workload.generate_events(start_time) for workload in self.workloads]
        next_events = []
        
        # Initialize with first event from each generator
        for i, gen in enumerate(generators):
            try:
                event = next(gen)
                next_events.append((event, i, gen))
            except StopIteration:
                pass
        
        # Loop until all generators are exhausted
        while next_events:
            # Get the earliest event
            next_events.sort(key=lambda x: x[0][0])  # Sort by timestamp
            (t, token_attrs), i, gen = next_events.pop(0)
            
            # Calculate the instantaneous rate at this point
            elapsed = t - start_time
            if elapsed > 0:
                current_rate = self.statistics.total_tokens / elapsed
                self.statistics.update(current_rate)
            
            yield (t, token_attrs)
            
            # Get next event from the same generator
            try:
                event = next(gen)
                next_events.append((event, i, gen))
            except StopIteration:
                pass
        
        self.statistics.finalize()
        
    def get_statistics(self) -> Dict[str, Any]:
        """Get aggregate statistics about all workloads."""
        overall_stats = self.statistics.get_summary()
        workload_stats = {w.name: w.get_statistics() for w in self.workloads}
        return {
            "overall": overall_stats,
            "per_workload": workload_stats
        }