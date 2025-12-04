# LEAF-Cloud Orchestrator

The Orchestrator is the core component of the LEAF-Cloud framework, responsible for managing the simulation lifecycle, event processing, and metrics collection.

## Table of Contents
- [Overview](#overview)
- [Key Features](#key-features)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [API Reference](#api-reference)
- [Examples](#examples)
- [Configuration](#configuration)
- [Extending the Orchestrator](#extending-the-orchestrator)
- [Performance Considerations](#performance-considerations)
- [Troubleshooting](#troubleshooting)

## Overview

The Orchestrator coordinates the execution of cloud infrastructure simulations by managing events, processing tokens through a Petri net, and collecting metrics. It provides a flexible and extensible framework for modeling complex distributed systems.

## Key Features

- **Event-driven simulation** with priority-based scheduling
- **Token-based workflow** for modeling system states
- **Extensible event handler** system
- **Comprehensive metrics** collection and reporting
- **Thread-safe** implementation
- **Support for distributed simulation** (future)

## Quick Start

```python
from leaf_cloud.orchestrator import Orchestrator
from leaf_cloud.config import LEAFCloudConfig

# Create a configuration
config = LEAFCloudConfig({
    "simulation.id": "my_simulation",
    "simulation.duration": 3600,  # 1 hour in simulation time
    "metrics.interval": 1.0,      # Collect metrics every second
})

# Create an orchestrator instance
orchestrator = Orchestrator(config=config)

# Set up your simulation (add places, transitions, etc.)
# ...

# Run the simulation
result = orchestrator.run_simulation()

# Generate a report
from leaf_cloud.orchestrator import ReportGenerator
report = ReportGenerator(result)
report.save_report("simulation_results")
```

## Architecture

The Orchestrator follows an event-driven architecture with these main components:

1. **Event Loop**: Manages the simulation timeline and processes events in priority order.
2. **Petri Net**: Models the system state using places and transitions.
3. **Event Handlers**: Process specific types of events (token creation, transitions, etc.).
4. **Metrics Collector**: Tracks and aggregates simulation metrics.
5. **Report Generator**: Creates reports from simulation results.

## API Reference

### Core Classes

#### `Orchestrator`

The main class that manages the simulation lifecycle.

**Key Methods:**
- `run_simulation()`: Run the simulation until completion or error.
- `schedule_event(timestamp, event_type, event_data)`: Schedule a new event.
- `stop()`: Stop the simulation gracefully.
- `reset()`: Reset the simulation to its initial state.

**Properties:**
- `state`: Current simulation state (INITIALIZING, READY, RUNNING, etc.).
- `current_time`: Current simulation time.
- `event_queue_size`: Number of pending events.

#### `ReportGenerator`

Generates reports from simulation results.

**Key Methods:**
- `to_dict()`: Convert results to a dictionary.
- `to_json(pretty=True)`: Convert results to a JSON string.
- `to_csv(output_dir, prefix)`: Export results to CSV files.
- `save_report(output_dir, prefix, formats)`: Save reports in multiple formats.

### Event Handlers

The Orchestrator uses an event-driven architecture with specialized handlers for different types of events. Here's how to use and extend the built-in handlers:

#### `BaseEventHandler`

Base class for all event handlers. Defines the interface that all handlers must implement.

**Key Methods:**
- `can_handle(event_type)`: Returns True if this handler can process the given event type.
- `handle(event_data, token_flow_log)`: Processes the event and returns the number of tokens processed.

#### Built-in Handlers

##### `TokenCreationHandler`

Handles the creation of new tokens in the simulation.

**Example:**
```python
from leaf_cloud.orchestrator.handlers import TokenCreationHandler

# Create a custom token creation handler
class CustomTokenCreationHandler(TokenCreationHandler):
    def handle(self, event_data, token_flow_log):
        # Add custom logic before token creation
        print(f"Creating token with data: {event_data}")
        
        # Call parent implementation
        token = super().handle(event_data, token_flow_log)
        
        # Add custom logic after token creation
        print(f"Created token: {token.id}")
        return token
```

##### `TokenDistributionHandler`

Handles the distribution of tokens between places in the Petri net.

**Example:**
```python
from leaf_cloud.orchestrator.handlers import TokenDistributionHandler

# Create a custom distribution handler
class CustomDistributionHandler(TokenDistributionHandler):
    def handle(self, event_data, token_flow_log):
        # Add custom distribution logic
        print(f"Distributing tokens: {event_data}")
        
        # Call parent implementation
        result = super().handle(event_data, token_flow_log)
        
        # Add custom logic after distribution
        print(f"Distributed {result} tokens")
        return result
```

##### `TokenCompletionHandler`

Handles the completion of token processing.

**Example:**
```python
from leaf_cloud.orchestrator.handlers import TokenCompletionHandler

class CustomCompletionHandler(TokenCompletionHandler):
    def handle(self, event_data, token_flow_log):
        # Add custom completion logic
        print(f"Completing token: {event_data}")
        
        # Call parent implementation
        result = super().handle(event_data, token_flow_log)
        
        # Add custom logic after completion
        print(f"Completed token processing")
        return result
```

##### `TransitionCompletionHandler`

Handles the completion of transitions in the Petri net.

**Example:**
```python
from leaf_cloud.orchestrator.handlers import TransitionCompletionHandler

class CustomTransitionHandler(TransitionCompletionHandler):
    def handle(self, event_data, token_flow_log):
        # Add custom transition logic
        print(f"Processing transition: {event_data['transition']}")
        
        # Call parent implementation
        result = super().handle(event_data, token_flow_log)
        
        # Add custom logic after transition
        print(f"Transition completed, consumed {result} tokens")
        return result
```

#### Registering Custom Handlers

To use your custom handlers, register them with the Orchestrator:

```python
from leaf_cloud.orchestrator import Orchestrator

# Create orchestrator instance
orchestrator = Orchestrator()

# Register custom handlers
orchestrator.register_handler(CustomTokenCreationHandler(orchestrator))
orchestrator.register_handler(CustomDistributionHandler(orchestrator))
orchestrator.register_handler(CustomCompletionHandler(orchestrator))
orchestrator.register_handler(CustomTransitionHandler(orchestrator))

# Now when you run your simulation, the custom handlers will be used
```

#### Creating a Custom Event Handler

You can also create completely custom event handlers by subclassing `BaseEventHandler`:

```python
from leaf_cloud.orchestrator.handlers import BaseEventHandler

class CustomEventHandler(BaseEventHandler):
    def __init__(self, orchestrator, custom_param):
        super().__init__(orchestrator)
        self.custom_param = custom_param
    
    def can_handle(self, event_type):
        return event_type == "custom_event"
    
    def handle(self, event_data, token_flow_log):
        print(f"Handling custom event with data: {event_data}")
        print(f"Custom parameter: {self.custom_param}")
        
        # Process the event and return the number of tokens processed
        return 1
```

#### Event Data Format

Each handler expects event data in a specific format. Here are the expected formats for built-in handlers:

1. **Token Creation**:
   ```python
   {
       "type": "token_creation",
       "place": "place_name",
       "count": 1,
       "attributes": {"key": "value"},
       "color": "request"
   }
   ```

2. **Token Distribution**:
   ```python
   {
       "type": "token_distribution",
       "source": "source_place",
       "targets": {"target_place1": 1, "target_place2": 2},
       "tokens": [token1, token2],
       "attributes": {"key": "value"}
   }
   ```

3. **Token Completion**:
   ```python
   {
       "type": "token_completion",
       "token_id": "token_123",
       "place": "place_name"
   }
   ```

4. **Transition Completion**:
   ```python
   {
       "type": "transition_completion",
       "transition": "transition_name",
       "input_places": ["input_place1", "input_place2"],
       "output_places": ["output_place1", "output_place2"],
       "consumed_tokens": {"input_place1": 1, "input_place2": 1},
       "new_tokens": {"output_place1": 1, "output_place2": 1}
   }
   ```

#### Default Event Handlers

The following event handlers are registered by default:

1. `TokenCreationHandler`: Handles `token_creation` events
2. `TokenDistributionHandler`: Handles `token_distribution` events
3. `TokenCompletionHandler`: Handles `token_completion` events
4. `TransitionCompletionHandler`: Handles `transition_completion` events

Abstract base class for all event handlers.

**Methods to Implement:**
- `can_handle(event_type)`: Return True if this handler can process the event type.
- `handle(event_data, token_flow_log)`: Process the event.

#### Built-in Handlers

- `TokenCreationHandler`: Handles token creation events.
- `TokenDistributionHandler`: Distributes tokens between places.
- `TokenCompletionHandler`: Handles token completion events.
- `TransitionCompletionHandler`: Processes transition firings.

## Examples

### Basic Simulation

```python
# Create and configure the orchestrator
config = LEAFCloudConfig({"simulation.id": "basic_example"})
orchestrator = Orchestrator(config)

# Set up the Petri net
# ...

# Run the simulation
result = orchestrator.run_simulation()

# Generate a report
report = ReportGenerator(result)
report.save_report("basic_simulation_results")
```

### Custom Event Handler

```python
from leaf_cloud.orchestrator.handlers.base import BaseEventHandler

class CustomEventHandler(BaseEventHandler):
    def can_handle(self, event_type):
        return event_type == "custom_event"
    
    def handle(self, event_data, token_flow_log):
        print(f"Processing custom event: {event_data}")
        # Process the event...
        return 1  # Number of events processed

# Register the custom handler
orchestrator.register_handler(CustomHandler(orchestrator))
```

## Configuration

The Orchestrator can be configured using a `LEAFCloudConfig` object. Key configuration options include:

```python
{
    "simulation.id": "unique_id",          # Unique identifier for the simulation
    "simulation.duration": 3600,           # Maximum simulation time (seconds)
    "metrics.interval": 1.0,               # How often to collect metrics (seconds)
    "metrics.retention": 60,               # Number of metric samples to keep
    "logging.level": "INFO",               # Logging level (DEBUG, INFO, WARNING, ERROR)
    "logging.file": "simulation.log"       # Optional log file
}
```

## Extending the Orchestrator

### Adding Custom Event Types

1. Create a new event handler class that inherits from `BaseEventHandler`.
2. Implement the `can_handle` and `handle` methods.
3. Register the handler with the orchestrator using `register_handler()`.

### Custom Metrics

To add custom metrics:

```python
# In your event handler or simulation code
self.orchestrator.metrics.record_metric("custom_metric", value)
```

## Performance Considerations

- Use appropriate batch sizes for token operations.
- Be mindful of the metrics collection interval.
- Consider disabling detailed logging in production.
- For large simulations, monitor memory usage.

## Troubleshooting

### Common Issues

1. **Simulation not starting**:
   - Check that all required places and transitions are defined.
   - Verify that the initial marking places have tokens.

2. **Performance problems**:
   - Increase the metrics collection interval.
   - Reduce the verbosity of logging.
   - Check for inefficient event handlers.

3. **Unexpected behavior**:
   - Enable debug logging for more detailed information.
   - Check the simulation state and event queue.

### Getting Help

For additional support, please open an issue in the project repository with:
- A description of the problem
- Relevant configuration
- Steps to reproduce
- Any error messages or logs
