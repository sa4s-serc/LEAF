![](public/leaf.svg)

# LEAF-Cloud: Layered Eco-centric Analytical Framework for Cloud Infrastructure

LEAF-Cloud models, simulates, and analyzes Terraform-defined cloud infrastructure with an emphasis on sustainability metrics. 


## Features

- Terraform configuration parsing and normalization.
- Layered Petri net modeling of cloud resources and workloads.
- Simulation outputs covering latency, energy consumption, carbon footprint, autoscaling behavior, and utilization.
- CLI utilities for model export, analysis, and reporting.
- Recommendations derived from simulation results to guide sustainable design decisions.

## Quick Start

```bash
# From the repo root
pip install -r leaf_cloud/requirements.txt
pysmt-install --z3

# Run a minimal steady workload simulation
python -m leaf_cloud simulate \
  --terraform .\examples\spring-boot-terraform-cloud-run-demo\terraform\ \
  --mode detailed --duration 10 steady --workload-rate 100

# Expected output: results/simulation_results_TIMESTAMP.json (timestamped)
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

- `leaf_cloud/` - Python package with parser, orchestrator, simulation engine, insights, and CLI entrypoints.
- `examples/` - Terraform scenarios and workload samples.
- `docs/` - Supplementary documentation (architecture notes released alongside the paper).
- `public/` - Shared assets (logos).
- `results/` - Default output folder for simulations (created as needed).

## Configuration

Place an optional `env.yaml` inside `leaf_cloud/` to override default constants:
- `energy_units`: measurement units (default kWh).
- `latency_units`: default seconds.
- `carbon_units`: default kg CO2.
- Additional solver and workload parameters (see `leaf_cloud/config.py`).

If no file is provided, embedded defaults are used.

## CLI Usage

Run everything from the repo root:

```bash
python -m leaf_cloud [--debug] [--log-file path] <command> [options]
```

### Commands

- `simulate` – Provide Terraform input plus a workload subcommand: `steady`, `burst`, `cyclical`, `random`, `csv`, `custom`, or `mix`. Supports heuristic vs detailed modes, cost analysis, and burst tuning flags (`--base-rate`, `--peak-rate`, `--burst-duration`, `--burst-interval`).
- `analyze` – Post-process existing results (`--metrics latency|energy|carbon|cost|scaling|all`) and emit insights to `analysis_output/` by default.
- `export` – Run a steady simulation and write results as `json|csv|yaml|html` (wrapper around `simulate steady`).
- `intermediate` – Build and export the intermediate model from Terraform (`intermediate_tf_resources.json` by default).
- `config` – `show`, `validate`, or `init` a configuration file.
- `clean` – Remove generated `results/`, `analysis_output/`, and temporary JSON exports.
- `version` – Display tool version.

Use `python -m leaf_cloud <command> --help` (and `simulate --help`) for full argument listings.

## Usage Examples

**Analyze existing results with insights disabled:**

```bash
python -m leaf_cloud analyze \
  --results results/simulation_results_TIMESTAMP.json \
  --metrics latency energy carbon cost \
  --no-insights
```

## Architecture Overview

LEAF-Cloud implements the layered modeling approach described in the manuscript. The Terraform parser produces an intermediate representation that is progressively enriched before feeding the simulation engine and metrics processors. See `docs/architecture.md` for the complete code-to-paper traceability guide.

### Layered Model

- **Abstract Layer** - Normalizes Terraform resources into canonical compute, storage, networking, security, and generic primitives (`leaf_cloud/core/resource.py`). This isolates downstream logic from provider-specific naming or attributes.
- **Specialized Layer** - Adds provider semantics (for example GCP) and attaches scaling limits, capacity, and energy annotations (`leaf_cloud/gcp/*.py`). Specialized components map to Petri net structures with consistent transition semantics.
- **Workload Layer** - Generates workload traces (steady, burst, cyclical, random) and binds them to entry transitions in the Petri net (`leaf_cloud/core/workload.py`). Workloads can inject demand shocks or diurnal patterns referenced in the evaluation.

### Processing Pipeline

1. **Parser** (`leaf_cloud/terraform/parser.py`) ingests Terraform modules, variables, and state to build an abstract resource graph.
2. **Model Builder** (`leaf_cloud/terraform/model_builder.py`) overlays the layered abstractions and produces a Petri net specification with transition rates and capacities.
3. **Simulation Engine** (`leaf_cloud/simulation/core.py`) executes timed Petri net runs, emitting raw traces of tokens, queues, and transition firings.
4. **Metrics Processor** (`leaf_cloud/utils/results_processor.py`, `leaf_cloud/utils/analyser.py`) derives latency, throughput, energy consumption, and carbon estimates using calibrated power models.
5. **Reporting & Visualization** (`leaf_cloud/export/`, `leaf_cloud/visualization/`) export structured results and charts for inspection.

CLI commands orchestrate each stage, while the Flask server wraps the same pipeline for the web UI.

### Petri Net Mapping

- Terraform compute resources (VMs, containers, functions) become processing places with service rates derived from CPU, memory, and autoscaling parameters.
- Storage and database resources translate into buffer places with I/O throughput and consistency constraints.
- Network constructs (load balancers, gateways, queues) map to routing transitions, allowing latency modeling across tiers.
- Energy and carbon calculations track token residence time and resource utilization, folding in regional grid mix metadata.

This mapping ensures that every Terraform element contributes to a measurable block in the simulation, supporting the eco-centric analysis presented in the manuscript.
