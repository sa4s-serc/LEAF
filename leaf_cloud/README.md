# LEAF-Cloud: Layered Eco-centric Analytical Framework

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-1.0.0-blue)](__version__.py)

LEAF-Cloud (Layered Eco-centric Analytical Framework for Cloud) is a comprehensive framework for modeling, simulating, and analyzing Google Cloud Platform (GCP) infrastructure with a focus on eco-centric metrics like energy usage and carbon emissions. The framework uses colored Petri nets (CPNs) to simulate interactions among GCP services and resource utilization.

## 🌟 Key Features

- **Terraform Integration**: Parse and analyze existing Terraform configurations
- **Multi-layered Modeling**:
  - Layer 1: Abstract GCP Resource Types (Compute, Storage, Network, Security)
  - Layer 2: Specialized GCP Components (GKE, Cloud Functions, etc.)
  - Workload Layer: Application models with request rates and resource demands
- **Advanced Workload Patterns**:
  - Steady, burst, cyclical, random, and custom workload types
  - Configurable workload parameters and patterns
  - Support for mixed workload scenarios
- **Comprehensive Metrics**:
  - Latency estimation with congestion modeling
  - Energy consumption calculations
  - Carbon footprint analysis using regional carbon factors
  - Resource utilization and scaling behavior
- **Command-line Interface**:
  - Intuitive CLI with subcommands for different operations
  - Support for configuration files and command-line overrides
  - Progress tracking and logging
- **Analysis & Visualization**:
  - Generate detailed reports and insights
  - Create infrastructure diagrams
  - Export results in multiple formats

## 📦 Installation

### Prerequisites
- Python 3.8 or higher
- Terraform (for parsing configurations)
- Git (for cloning the repository)

### Install from Source

1. Clone the repository:
   ```bash
   git clone https://github.com/your-org/leaf-cloud.git
   cd leaf-cloud
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. Install the package in development mode with all dependencies:
   ```bash
   pip install -e .
   ```

4. Verify the installation:
   ```bash
   leaf-cloud --version
   ```

## 🚀 Quick Start

### Basic Commands

```bash
# Show help and available commands
leaf-cloud --help

# Run a simulation with default settings
leaf-cloud simulate --terraform /path/to/terraform/configs

# Analyze existing simulation results
leaf-cloud analyze --results /path/to/results.json

# Generate infrastructure diagrams
leaf-cloud diagram --output /path/to/output/

# Clean up temporary files
leaf-cloud clean
```

### Example: Running Simulations

#### 1. Steady Workload
```bash
leaf-cloud simulate steady \
  --terraform ./infra/ \
  --workload-rate 100 \
  --duration 3600
```

#### 2. Burst Workload
```bash
leaf-cloud simulate burst \
  --terraform ./infra/ \
  --base-rate 50 \
  --peak-rate 200 \
  --burst-duration 300 \
  --burst-interval 1800
```

#### 3. Cyclical Workload
```bash
leaf-cloud simulate cyclical \
  --terraform ./infra/ \
  --base-rate 100 \
  --amplitude 50 \
  --period 3600
```

### Analyzing Results

```bash
# Generate insights from simulation results
leaf-cloud insights \
  --results ./results/simulation_20230628/ \
  --output-dir ./analysis/

# Export results to different formats
leaf-cloud export --format json --output results.json
```

## 🛠️ Command Reference

| Command       | Description                                      |
|---------------|--------------------------------------------------|
| `simulate`   | Run infrastructure simulations with various workload patterns |
| `analyze`    | Analyze simulation results and generate reports  |
| `diagram`    | Generate infrastructure diagrams and visualizations |
| `export`     | Export simulation data in various formats (JSON, CSV, etc.) |
| `insights`   | Generate detailed insights and recommendations   |
| `clean`      | Remove temporary files and simulation artifacts  |
| `version`    | Show version and build information               |

### Simulation Workload Types

| Workload Type | Description                                    | Key Parameters                     |
|---------------|------------------------------------------------|-----------------------------------|
| `steady`     | Constant workload rate                        | `--workload-rate`                 |
| `burst`      | Periodic bursts of high traffic               | `--base-rate`, `--peak-rate`, `--burst-duration`, `--burst-interval` |
| `cyclical`   | Sinusoidal workload variations                | `--base-rate`, `--amplitude`, `--period` |
| `random`     | Random variations between min and max rates   | `--min-rate`, `--max-rate`        |
| `mix`        | Combination of multiple workload patterns     | Configured via YAML config file   |
| `custom`     | Custom workload defined in configuration      | Defined in configuration file     |

## 🌐 REST API Reference

LEAF-Cloud provides a RESTful API for programmatic access to its features. All API endpoints return JSON responses with the following structure:

```json
{
  "status": "success|error",
  "message": "Descriptive message",
  "data": {}
}
```

### Error Responses

Error responses include an error code and message:

```json
{
  "status": "error",
  "message": "Error description",
  "error": {
    "code": "ERROR_CODE",
    "details": "Additional error details"
  }
}
```

### Authentication

All API endpoints require authentication. Include your API key in the `Authorization` header:

```
Authorization: Bearer YOUR_API_KEY
```

### Endpoints

#### 1. Get API Version

```
GET /version
```

Returns the current API version information.

**Example Response:**
```json
{
  "status": "success",
  "message": "LEAF Cloud API",
  "data": {
    "version": "1.0.0",
    "name": "LEAF Cloud"
  }
}
```

#### 2. Run Simulation

```
POST /simulate
```

Run a simulation with the specified parameters.

**Request Body:**
```json
{
  "terraform": "/path/to/terraform",
  "workload": {
    "type": "steady",
    "rate": 100
  },
  "duration": 3600
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Simulation completed successfully",
  "data": {
    "simulation_id": "sim_12345",
    "status": "completed",
    "results_path": "/results/sim_12345"
  }
}
```

#### 3. Analyze Results

```
POST /analyze
```

Analyze simulation results.

**Request Body:**
```json
{
  "results_path": "/path/to/results",
  "metrics": ["latency", "throughput", "energy"]
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Analysis completed",
  "data": {
    "metrics": {
      "latency": {"avg": 45.2, "p95": 78.5, "max": 120.3},
      "throughput": {"avg": 95.7, "max": 120.1},
      "energy": {"total_kwh": 12.5, "co2_kg": 5.8}
    }
  }
}
```

#### 4. Export Simulation

```
POST /export/simulation
```

Export simulation results in the specified format.

**Request Body:**
```json
{
  "simulation_id": "sim_12345",
  "format": "json",
  "output_path": "/exports/sim_12345.json"
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Export completed successfully",
  "data": {
    "output_file": "/exports/sim_12345.json",
    "format": "json",
    "size_bytes": 24576
  }
}
```

#### 5. Generate Class Diagram

```
POST /diagram/class
```

Generate a class diagram from Terraform configuration.

**Request Body:**
```json
{
  "terraform": "/path/to/terraform",
  "output_dir": "/path/to/output",
  "filename": "class_diagram.png"
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Class diagram generated successfully",
  "data": {
    "diagram_file": "/path/to/output/class_diagram.png"
  }
}
```

#### 6. Generate Deployment Diagram

```
POST /diagram/deployment
```

Generate a deployment diagram from Terraform configuration.

**Request Body:**
```json
{
  "terraform": "/path/to/terraform",
  "output_dir": "/path/to/output",
  "filename": "deployment_diagram.png"
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Deployment diagram generated successfully",
  "data": {
    "diagram_file": "/path/to/output/deployment_diagram.png"
  }
}
```

#### 7. Clean Up

```
POST /clean
```

Clean up generated files and temporary data.

**Request Body:**
```json
{
  "patterns": ["*.tmp", "*.log"],
  "directories": ["/tmp/leaf_cloud"]
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Cleanup completed successfully",
  "data": {
    "files_removed": ["/tmp/leaf_cloud/temp_123.tmp", "/tmp/leaf_cloud/run_456.log"],
    "directories_removed": ["/tmp/leaf_cloud/cache", "/tmp/leaf_cloud/temp"]
  }
}
```

## 📊 Advanced Features

### Metrics and Analysis

LEAF-Cloud provides comprehensive analysis capabilities:

- **Performance Analysis**:
  - End-to-end latency estimation
  - Resource utilization metrics
  - Throughput and capacity planning
  - Bottleneck identification

- **Eco-centric Metrics**:
  - Energy consumption modeling
  - Carbon footprint calculation using regional carbon factors
  - Resource efficiency analysis

- **Cost Analysis**:
  - Infrastructure cost estimation
  - Cost optimization recommendations
  - Resource right-sizing suggestions

- **Scaling Analysis**:
  - Auto-scaling behavior evaluation
  - Load distribution analysis
  - Over-provisioning detection

### Configuration

LEAF-Cloud can be configured using YAML configuration files or command-line arguments. Common configuration options include:

- Simulation parameters (duration, time step, iterations)
- Workload definitions
- Resource constraints
- Regional settings
- Output preferences

Example configuration file (`config.yaml`):

```yaml
simulation:
  duration: 3600  # seconds
  time_step: 1.0
  iterations: 3
  warmup_period: 300  # seconds

workload:
  type: "burst"
  params:
    base_rate: 50
    peak_rate: 200
    burst_duration: 300
    burst_interval: 1800

output:
  directory: "./results"
  format: "json"
  save_intermediate: true
```

## 🏗️ Project Structure

```
leaf-cloud/
├── cli/                 # Command-line interface modules
│   ├── __init__.py      # Command module registration
│   ├── analyze.py       # Analysis commands
│   ├── clean.py         # Cleanup commands
│   ├── diagram.py       # Diagram generation
│   ├── export.py        # Export functionality
│   ├── insights.py      # Insights generation
│   ├── intermediate.py  # Intermediate model export
│   ├── simulate.py      # Simulation commands
│   └── version.py       # Version information
│
├── core/                # Core simulation engine
│   ├── __init__.py      # Core package exports
│   ├── petri_net.py     # Petri net implementation
│   ├── resource.py      # Resource management
│   └── workload.py      # Workload generation
│
├── export/              # Export functionality
│   ├── __init__.py
│   ├── csv_exporter.py
│   └── json_exporter.py
│
├── gcp/                 # GCP-specific implementations
│   ├── __init__.py
│   ├── compute.py       # Compute resources
│   ├── network.py       # Networking components
│   ├── security.py      # Security services
│   └── storage.py       # Storage services
│
├── insights/            # Analysis and insights
│   ├── __init__.py
│   ├── analyzer.py
│   └── visualizer.py
│
├── models/              # Data models and metrics
│   ├── __init__.py
│   ├── carbon.py        # Carbon footprint model
│   ├── energy.py        # Energy consumption model
│   ├── latency.py       # Latency model
│   └── scaling.py       # Scaling model
│
├── terraform/           # Terraform integration
│   ├── __init__.py
│   ├── model_builder.py # Builds models from Terraform
│   ├── parser.py        # Parses Terraform files
│   └── utils.py         # Terraform utilities
│
├── utils/               # Utility functions
│   ├── __init__.py
│   ├── config.py        # Configuration handling
│   ├── logger.py        # Logging utilities
│   └── validation.py    # Data validation
│
├── leaf.py              # Main LEAFCloud class
├── orchestrator.py      # Simulation orchestration
├── config.py            # Configuration models
├── main.py              # CLI entry point
└── README.md            # This file
```

## 📚 Documentation

For detailed documentation, please refer to:

1. [API Documentation](API_DOCUMENTATION.md) - Comprehensive API reference
2. [Overview](Overview.md) - In-depth system architecture and design
3. [Examples](./examples/) - Example configurations and use cases


