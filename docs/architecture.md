# LEAF-Cloud Architecture & Implementation Guide

This document bridges the IEEE Software manuscript "LEAF: A Layered Emission Assessment Framework for Cloud Deployments" with the codebase located in this repository. 

## 1. Traceability Between Manuscript and Code

| Manuscript Section / Figure | Conceptual Focus | Implementation Anchors |
| --- | --- | --- |
| Sec. "Modeling Carbon Impact in Layers" | Abstract/Specialized/Workload layers | `leaf-cloud/core/resource.py`, `leaf-cloud/gcp/*.py`, `leaf-cloud/core/workload.py` |
| "Processing Pipeline: From IaC to Carbon Metrics" & Fig. 1 | Parser -> Weaver -> Simulator -> Estimator pipeline | `leaf-cloud/terraform/parser.py`, `leaf-cloud/terraform/model_builder.py`, `leaf-cloud/orchestrator.py`, `leaf-cloud/models/*.py` |
| Supplementary Sec."Case Study and Evaluation Methodology" | Project selection, workload profiles, evaluation metrics | `examples/`, commands in Sec.10 below |
| Manuscript Eq. (Energy / Carbon computation) | Power models and aggregation | `leaf-cloud/models/energy.py`, `leaf-cloud/models/carbon.py`, `leaf-cloud/models/aggregation.py` |
| Manuscript discussion on autoscaling and state transitions | Petri net token flow & resource state machine | `leaf-cloud/core/petri_net.py`, `leaf-cloud/core/resource.py`, `leaf-cloud/terraform/model_builder.py` |



## 2. Repository Map

- `leaf-cloud/`: Python source for the CLI, REST server, and simulation engine.
  - `terraform/parser.py`: HashiCorp Configuration Language (HCL) ingestion and variable resolution.
  - `terraform/model_builder.py`: Translates parsed Terraform into a layered Petri net.
  - `core/`: Abstract resource types (`resource.py`), Petri net runtime (`petri_net.py`), and workload generators (`workload.py`).
  - `gcp/`: Specialized resource implementations (Compute, Storage, Network, Security, Generic) matching the manuscript's specialized layer.
  - `models/`: Quantitative estimators (latency, energy, carbon, scaling) and metric aggregation.
  - `orchestrator.py`: Coordinates the parser, model builder, workloads, simulation runs, and metric models.
  - `leaf_cloud.py` & `main.py`: High-level API and CLI entry point mirroring the paper's workflow diagram.
- `leaf-webview/`: Vite/React frontend that consumes the REST API (optional for reproducing manuscript results).
- `examples/`: Terraform projects and workload presets corresponding to Case Studies A-C discussed in the paper.
- `docs/`: Documentation artifacts (this file, future diagrams).
- `results/`: Default output folder for generated simulation artifacts.

## 3. End-to-End Workflow (Paper Figure 1 -> Code)

1. **Terraform Parser (IaC ingestion)**  
   - Entry: `TerraformParser.parse_all()` in `leaf-cloud/terraform/parser.py`.  
   - Responsibilities: enumerates `.tf` files, inlines modules, resolves variables (default values, `.tfvars`, environment overrides), and emits a provider-agnostic intermediate representation (IR).  
   - Output structure: `{"resources": {id: {...}}, "variables": {...}, "modules": {...}, "outputs": {...}}`. The IR is the "Parser" box in Fig. 1.

2. **Weaver / Model Builder (Layered model)**  
   - Entry: `ModelBuilder.build_model()` in `leaf-cloud/terraform/model_builder.py`.  
   - Steps (see Manuscript "Processing Pipeline"):
     - `_process_terraform_resources()` categorizes each IR resource into abstract types using `RESOURCE_CLASS_MAP`.
     - `_build_network_topology()` maps dependencies (explicit `depends_on`, implicit references) into Petri net transitions and arcs.
     - `_add_workload_generation()` injects workload transitions, connecting the Source place (`PetriNet.SOURCE_PLACE_ID`) to workload queues.
   - Every Terraform resource is represented by:
     - An **In-Queue** place (`In_<resource>`), **Processing** place (`Proc_<resource>`), **Out-Queue** place, and a **State** place capturing `ResourceState` transitions.
     - Capacity annotations derived from resource configuration (e.g., `google_compute_instance_group_manager` autoscaling rules). See `_configure_compute_resource()` and friends inside the builder.

3. **Simulation Engine (Petri Net runtime)**  
   - Entry: `PetriNet.simulate()` and supporting methods (`fire_transition`, `_process_event_queue`) in `leaf-cloud/core/petri_net.py`.  
   - Token (`TokenColor`) track compute, storage, network, security, and generic flows as described in the manuscript's layered model.  
   - Resource lifecycle is enforced by `Resource.allocate()` / `Resource.deallocate()` in `core/resource.py`, ensuring state logs (AVAILABLE, ALLOCATED, DEGRADED, etc.) capture autoscaling events and saturation.

4. **Estimator (Metrics models)**  
   - Orchestrated in `leaf-cloud/orchestrator.py` after simulation completes.  
   - Models loaded in `leaf_cloud.LEAFCloud.__init__`:
     - `EnergyModel.calculate()` -> implements `E_i(t) = f_i(u_i(t), R_i(t))` (Manuscript Eq. 1).
     - `CarbonModel.calculate()` -> multiplies energy traces by region intensity `I_region` (Manuscript Eq. 2).
     - `LatencyModel` and `ScalingModel` derive response-times, queue lengths, and autoscaling diagnostics.
   - Aggregation via `MetricAggregator.aggregate()` in `leaf-cloud/models/aggregation.py` yields total/average metrics matching the tables in the paper.

5. **Outputs & Visualization**  
   - CLI handles export (`leaf-cloud/main.py` -> `export` subcommand) and diagram generation using `export/class_diagram.py` & `export/deployment_diagram.py`.  
   - Time-series JSON and CSV outputs live under the chosen `--output` directory (`results/` by default) and were the basis for figures/tables in Sec.5 of the paper.

## 4. Layered Modeling Details

### 4.1 Abstract Resource Layer
- Defined by `Resource` base class and `ResourceType` enum (`leaf-cloud/core/resource.py`).  
- Each resource stores capacity, allocation history, utilization trace, and state transitions.  
- Non-operational Terraform blocks (e.g., IAM roles) map to `GenericGCPComponent` (`leaf-cloud/gcp/generic_component.py`) with near-zero baseline energy, reflecting the manuscript's discussion on "configuration-only resources".

### 4.2 Specialized Resource Layer
- Implemented by GCP-specific subclasses (`leaf-cloud/gcp/*.py`). Examples:
  - `ComputeEngineVM`, `CloudRun`, `CloudFunction` in `gcp/compute.py` handle machine types, vCPU counts, and autoscaling metadata.
  - `CloudSQL`, `PersistentDisk`, `Bigtable` in `gcp/storage.py` define storage capacity, IOPS, and standby replicas.
  - `LoadBalancer`, `VPC`, `APIGateway` in `gcp/network.py` encode ingress/egress characteristics.
  - `IAM`, `CloudArmor`, `SecretManager` in `gcp/security.py` manage security-centric states.
- Region-specific carbon factors and idle power defaults are injected via `_load_env_config()` in `ModelBuilder` and the energy/carbon models (`models/energy.py`, `models/carbon.py`). Override with `env.yaml` to match enterprise-specific coefficients.

### 4.3 Workload Layer
- Workload primitives in `leaf-cloud/core/workload.py` implement the patterns cited in the manuscript (steady, burst, cyclical, random, custom, mix).
- CLI flags (`--workload-type`, `--burst-peak-rate`, `--cycle-period`, etc.) map directly to constructor arguments for each workload class.
- Runtime metrics (`WorkloadStatistics`) feed into analysis functions (`leaf-cloud/utils/analyser.py`) to report throughput, queue depth, and scaling responsiveness.

## 5. Terraform Intermediate Representation (IR)

1. **File Discovery**: `_find_terraform_files()` recurses directories and module paths.  
2. **Variable Resolution**: `_load_variable_definitions()` + `_load_variable_values()` merge defaults, environment overrides, and `.tfvars`.  
3. **Normalization**: `_convert_to_pure_dict()` produces primitives suitable for JSON export.  
4. **Module Expansion**: `_process_module_calls()` and `_extract_module_resources()` inline module outputs (aligns with supplementary emphasis on reproducibility).  
5. **Persisting IR**: The `intermediate` CLI command writes `parsed_infrastructure.json`, reflecting the IR referenced in the supplementary worked example.

The IR schema matches the "Parser output" described in the paper; each entry contains `type`, `name`, `config`, and module address metadata.

## 6. Petri Net Construction Algorithm

`ModelBuilder.build_model()` orchestrates the steps noted as the "Weaver Module" in the manuscript:

1. **Resource Instantiation** (`_create_resource_object`): Selects the proper specialized class from `RESOURCE_CLASS_MAP` based on Terraform `type`. Fallback: `GenericGCPComponent`.
2. **Place & Transition Creation** (`_create_queue_places`, `_create_processing_transition`): For each resource, the builder creates:
   - Input queue place `In_<resource>`
   - Processing place `Proc_<resource>`
   - Output queue `Out_<resource>`
   - State place `State_<resource>` capturing `ResourceState`.
3. **Dependency Modeling** (`_connect_resource_dependencies`): Translates dependency edges into Petri net arcs, allowing request tokens to traverse the infrastructure graph.
4. **Capacity & Delay Injection**:
   - Compute resources use `_configure_compute_resource_capacity()` to translate vCPU/memory to processing rates.
   - Storage/network resources rely on `_configure_storage_throughput()` and similar helpers for queue factors and network latency.
5. **Workload Binding** (`_add_workload_generation` & `_create_token_transfer_action`): Associates workload emissions with queue places; workload parameters such as `db_queries_per_request` adjust transition weights as noted in the paper's load propagation example.

The resulting `PetriNet` instance contains:
- `places`: dictionary keyed by IDs (see `PetriNet.places`), including source/sink sentinel nodes.
- `transitions`: queue processors and dependency transitions with guard/weight metadata.
- `arcs`: directional edges with weights for load amplification/attenuation.

## 7. Simulation Runtime

- Discrete-event scheduling uses a binary heap (`heapq`) inside `PetriNet._event_queue`.
- Token creation, consumption, and completion events are logged via the `TokenFlowLogEntry` typed dicts in `leaf-cloud/orchestrator.py`, enabling traceability (Manuscript emphasis on transparency).
- `Resource.allocate()` / `Resource.deallocate()` enforce capacity constraints and maintain utilization histories for downstream metrics.
- Concurrency controls (thread-safe locks) allow multi-iteration simulations (`--iterations`) by orchestrating sequential runs with shared configuration.

## 8. Metrics Estimation & Reporting

### 8.1 Energy Model (`models/energy.py`)
- Loads per-resource coefficients from `config/` or `env.yaml`.
- Uses tailored formulas:
  ```text
  E_i(t) = (P_idle_i + alpha_i * u_i(t)) * Deltat
  ```
  where `u_i(t)` is utilization ratio and `Deltat` is the sample interval derived from simulation timestamps.
- Aggregates energy over time (`calculate_total_energy`) and across resources (`aggregate_energy_consumption`).

### 8.2 Carbon Model (`models/carbon.py`)
- Converts energy traces to emissions using region intensity values (`I_region`) sourced from provider datasets (see supplementary references).  
- Supports override via `env.yaml` to match enterprise-specific footprint inventories.

### 8.3 Latency & Scaling Models
- `models/latency.py` computes queuing delays using Little's Law and service rate annotations.
- `models/scaling.py` inspects autoscaling events triggered during simulation (state transitions to `SCALING` in `Resource` objects).

### 8.4 Aggregation & Recommendations
- `models/aggregation.py` produces per-layer summaries (compute, storage, network, security) aligning with the tables in the manuscript.
- `utils/analyser.py` and `generate_summary_and_recommendations()` assemble narrative outputs comparable to manuscript discussion points and practitioner guidance.

## 9. Configuration & Calibration

- `env.yaml` (root of `leaf-cloud/`) captures default region, project, and parameter overrides (queue factors, carbon intensity, workload defaults).  
- Manuscript-specific coefficients (e.g., Cloud Carbon Footprint baselines [3], Google sustainability reports [13]) are encoded in YAML/JSON resources under `leaf-cloud/config.py` and loaded by the metric models. Update these files to align with new data releases.
- `examples/*/workload/*.json` (if present) provide custom workload patterns for reproduction.
