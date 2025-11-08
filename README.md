![](public/leaf.svg)

# LEAF-Cloud: Layered Eco-centric Analytical Framework for Cloud Infrastructure

LEAF-Cloud models, simulates, and analyzes Terraform-defined cloud infrastructure with an emphasis on sustainability metrics. 


## Features

- Terraform configuration parsing and normalization.
- Layered Petri net modeling of cloud resources and workloads.
- Simulation outputs covering latency, energy consumption, carbon footprint, autoscaling behavior, and utilization.
- CLI utilities for model export, analysis, reporting, and diagram generation.
- Recommendations derived from simulation results to guide sustainable design decisions.

## Quick Start

```bash
cd leaf-cloud
#  Install backend dependencies and SMT solver
pip install -r leaf-cloud/requirements.txt
pysmt-install --z3

#  Run the sample simulation
python -m leaf-cloud.main simulate \
  --terraform ./examples/terraform/ \
  --duration 36 --iterations 1 \
  --output results

# Expected output: results/simulation_results_TIMESTAMP.json
```

## Requirements

**Backend & CLI**
- Python 3.9 or newer
- Terraform 1.0.0+
- `pysmt` with the Z3 backend (`pysmt-install --z3`)

**Web Application**
- Node.js 16+
- npm 8+ (or yarn 1.22+)


## Repository Structure

- `leaf-cloud/` - Python package with the parser, model builder, simulation engine, and CLI entrypoints.
- `leaf-webview/` - Vite/React frontend for visualization.
- `examples/` - Terraform scenarios, workload definitions, and published artifacts.
- `docs/` - Supplementary documentation (architecture notes released alongside the paper).
- `public/` - Shared assets (logos, diagrams).

## Configuration

Place an optional `env.yaml` inside `leaf-cloud/` to override default constants:
- `energy_units`: measurement units (default kWh).
- `latency_units`: default seconds.
- `carbon_units`: default kg CO2.
- Additional solver and workload parameters (see `leaf-cloud/leaf/config/defaults.py`).

If no file is provided, embedded defaults are used.

## CLI Usage

All commands run from the project root:

```bash
python -m leaf-cloud.main <command> [options]
```

### Global Options
- `--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}`
- `--enable-logging`

### Commands

- `simulate` - Execute a simulation for Terraform input.
- `intermediate` - Export the intermediate Petri net model (`parsed_infrastructure.json` by default).
- `analyze` - Post-process stored results (latency, energy, carbon, scaling, utilization, states).
- `export` - Emit results in JSON, CSV, or YAML (optionally running a new simulation first).
- `diagram` - Generate class, deployment, or data-flow diagrams.
- `version` - Display tool version.
- `clean` - Remove generated results, diagrams, and temporary files.

Use `python -m leaf-cloud.main <command> --help` for full argument listings.

### Web Application & Server

```bash
# Backend API (from project root, with virtualenv activated)
python -m leaf-cloud.server --host 0.0.0.0 --port 5000 --debug

# Frontend (in a new shell)
cd leaf-webview
npm run dev     # or yarn dev
```

Visit the printed URL (typically http://localhost:5173) to access the dashboard.

## Usage Examples

**Single simulation with custom workload:**

```bash
python -m leaf-cloud.main simulate \
  --terraform ./examples/microservices-demo/terraform/ \
  --workload-type burst --workload-rate 25 \
  --duration 1800 --iterations 3 --queue-factor 1.2 \
  --output results/burst_scenario
```

Artifacts produced:
- `results/burst_scenario/simulation_results_TIMESTAMP.json`
- `results/burst_scenario/metrics_summary.csv`
- Optional diagrams under `results/burst_scenario/diagrams/` (when `diagram` is run).

**Analyze existing results:**

```bash
python -m leaf-cloud.main analyze \
  --results results/burst_scenario/simulation_results_TIMESTAMP.json \
  --metrics latency energy carbon
```

**Generate diagrams for quick architecture review:**

```bash
python -m leaf-cloud.main diagram \
  --terraform ./examples/simple_deployment/ \
  --types class deployment \
  --output diagrams/simple_deployment
```

## Architecture Overview

LEAF-Cloud implements the layered modeling approach described in the manuscript. The Terraform parser produces an intermediate representation that is progressively enriched before feeding the simulation engine and metrics processors. See `docs/architecture.md` for the complete code-to-paper traceability guide.

### Layered Model

- **Abstract Layer** - Normalizes Terraform resources into canonical compute, storage, networking, security, and generic primitives (`leaf-cloud/core/resource.py`). This isolates downstream logic from provider-specific naming or attributes.
- **Specialized Layer** - Adds provider semantics (for example GCP) and attaches scaling limits, capacity, and energy annotations (`leaf-cloud/gcp/*.py`). Specialized components map to Petri net structures with consistent transition semantics.
- **Workload Layer** - Generates workload traces (steady, burst, cyclical, random) and binds them to entry transitions in the Petri net (`leaf-cloud/core/workload.py`). Workloads can inject demand shocks or diurnal patterns referenced in the evaluation.

### Processing Pipeline

1. **Parser** (`leaf-cloud/leaf/parser`) ingests Terraform modules, variables, and state to build an abstract resource graph.
2. **Model Builder** (`leaf-cloud/leaf/model`) overlays the layered abstractions and produces a Petri net specification with transition rates and capacities.
3. **Simulation Engine** (`leaf-cloud/leaf/simulation`) executes timed Petri net runs, emitting raw traces of tokens, queues, and transition firings.
4. **Metrics Processor** (`leaf-cloud/leaf/metrics`) derives latency, throughput, energy consumption, and carbon estimates using calibrated power models.
5. **Reporting & Visualization** (`leaf-cloud/leaf/reporting`, `leaf-webview/`) export structured results, charts, and diagrams for inspection.

CLI commands orchestrate each stage, while the Flask server wraps the same pipeline for the web UI.

### Petri Net Mapping

- Terraform compute resources (VMs, containers, functions) become processing places with service rates derived from CPU, memory, and autoscaling parameters.
- Storage and database resources translate into buffer places with I/O throughput and consistency constraints.
- Network constructs (load balancers, gateways, queues) map to routing transitions, allowing latency modeling across tiers.
- Energy and carbon calculations track token residence time and resource utilization, folding in regional grid mix metadata.

This mapping ensures that every Terraform element contributes to a measurable block in the simulation, supporting the eco-centric analysis presented in the manuscript.

## License & Availability
**LEAF is currently under a closed license.**
### Usage Restrictions
- Commercial use not permitted
- Redistribution not allowed