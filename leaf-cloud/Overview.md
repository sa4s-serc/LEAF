# LEAF-Cloud Project Overview

<!-- Metrics Units: workload rate in requests per second (req/s), duration in seconds (s), energy in kWh, carbon footprint in kgCO₂eq -->
LEAF-Cloud (Layered Eco-centric Analytical Framework for Cloud) is a comprehensive framework for modeling, analyzing, and optimizing Google Cloud Platform (GCP) infrastructure with an eco-centric perspective. The project aims to estimate key operational parameters including latency, energy usage, and carbon footprint while accounting for the dynamic behavior of cloud resources.

## Core Concept

The framework uses colored Petri nets (CPNs) to simulate interactions among GCP services and resource utilization. By modeling cloud infrastructure as a layered system, LEAF-Cloud provides insights into both performance and environmental impacts of cloud deployments.

## Key Features

1. **Layered Modeling Approach**:
   - Layer 1: Abstract GCP Resource Types (Compute, Storage, Network, Security)
   - Layer 2: Specialized GCP Components (specific services like GKE, Cloud Functions, etc.)
   - Workload Layer: Application models with request rates and resource demands

2. **Comprehensive Metrics Estimation**:
   - Latency: Calculates total delay across resource transitions
   - Energy Usage: Measures resource-specific energy consumption
   - Carbon Footprint: Derives emissions using regional carbon factors

3. **Dynamic Scaling Simulation**:
   - Models GKE pod scaling based on workload, capacity, and geolocation
   - Accurately represents real-world operational behavior

4. **Terraform Integration**:
   - Parses existing Terraform configurations to build models
   - Generates optimized Terraform templates from simulation results

5. **Visualization and Reporting**:
   - Produces class and deployment diagrams
   - Generates detailed metrics and analysis reports

6. **Advanced Resource Management**:
   - Atomic allocate/deallocate operations with automatic rollback on failure
   - Overload handling: resources accept excess load and log a degraded state rather than failing
   - Queueing factor (CLI `--queue-factor`) to expand resource capacity modeling queue buffers

7. **Flexible Logging Configuration**:
   - Default logging is suppressed to warnings and errors only
   - Command-line flags `--enable-logging` + `--log-level` control verbosity
   - User-friendly log format: `[LEVEL] message`

## Mathematical Foundation

The project implements several key mathematical models:

- **Latency Estimation**: Calculates delays along critical paths including congestion factors
- **Energy Consumption**: Models resource-specific energy functions with utilization factors
- **Carbon Footprint**: Converts energy to emissions using regional carbon intensity values
- **Dynamic Scaling**: Estimates required resources based on demand and capacity equations

## Implementation Details

The system is implemented as a Python application with a modular architecture:

- **Core Engine**: Petri net simulation implementation with token flow mechanics
- **GCP Services**: Detailed models of all major GCP service categories
- **Mathematical Models**: Implementation of key equations for metrics estimation
- **Integration Layer**: Connections to external tools and visualization capabilities

## Workflow

1. Users define their GCP infrastructure using the framework's API or by importing Terraform configurations
2. The system constructs a layered Petri net model of the infrastructure
3. Apply workload patterns and optional CLI parameters:
   - `--workload-rate` to set request rate
   - `--duration`, `--iterations` to control simulation length
   - `--queue-factor` to expand capacities for queuing
   - `--enable-logging` and `--log-level` to adjust output
4. The simulation engine runs, performing atomic token flows and tracking resource utilization (including overloads)
5. Mathematical models calculate performance and sustainability metrics
6. Results and time-series utilization histories are exported and visualized
7. Optional: generate optimized Terraform configurations or visualization diagrams

## Use Cases

- **Infrastructure Planning**: Estimate resource requirements and costs before deployment
- **Environmental Impact Assessment**: Calculate carbon footprint of cloud operations
- **Performance Optimization**: Identify bottlenecks and latency issues
- **Capacity Planning**: Determine optimal scaling parameters for dynamic workloads
- **Terraform Integration**: Generate eco-efficient infrastructure-as-code templates

The LEAF-Cloud framework bridges the gap between performance engineering and environmental sustainability in cloud computing, providing organizations with the tools to make informed decisions about their GCP infrastructure.
----
# File Responsibilities in LEAF-Cloud Project

### app.py
- Serves as the entry point for the web/GUI interface of the LEAF-Cloud framework
- Provides a user interface for configuring and running simulations
- Displays visualization of simulation results (latency, energy usage, carbon footprint)
- Allows users to interact with the model, adjust parameters, and view results dynamically

### main.py
- Command-line entry point for the LEAF-Cloud framework
- Handles argument parsing and configuration loading
- Orchestrates the overall simulation workflow
- Coordinates the interactions between different modules

### leaf_cloud.py
- Implements the core LEAF-Cloud framework class
- Serves as the main integration point for all modules
- Manages the layered structure (Abstract GCP Resources, Specialized GCP Components, Workload)
- Provides API for running simulations and retrieving results

### orchestrator.py
- Coordinates the simulation lifecycle
- Manages the execution flow from model loading to result generation
- Handles the scheduling and synchronization of concurrent simulations
- Implements error handling and recovery mechanisms

### config.py
- Defines configuration parameters and default values
- Implements configuration validation
- Loads configuration from files (env.yaml)
- Provides a centralized access point for framework settings

### env.yaml
- Contains environment-specific configuration
- Stores default values for framework parameters
- Defines regional carbon factors
- Specifies simulation parameters (timeouts, iterations, etc.)

## Core Module

### core/\_\_init\_\_.py
- Package initialization for the core module
- Exports key classes and functions

### core/petri_net.py
- Implements the Colored Petri Net (CPN) simulation engine
- Defines token, place, transition, and arc classes
- Provides mechanisms for token flow and transition firing
- Implements the simulation clock and event queue
- Handles concurrent token movements and resource contention

### core/resource.py
- Defines the abstract resource class hierarchy
- Implements resource allocation and deallocation mechanisms
- Manages resource states and capacity constraints
- Tracks resource utilization throughout simulation

### core/workload.py
- Implements workload modeling and generation
- Defines workload patterns (steady, burst, cyclical)
- Generates token creation events based on workload profiles
- Provides statistics about workload characteristics

## GCP Module

### gcp/\_\_init\_\_.py
- Package initialization for the GCP module
- Exports key GCP resource classes

### gcp/generic_component.py
- Defines common attributes and behaviors
- Provides interface for integration with Petri nets
- Implements monitoring and reporting capabilities

### gcp/compute.py
- Models Compute Engine VMs, GKE, App Engine, Cloud Functions, and Cloud Run
- Implements service-specific attributes and behaviors
- Defines the dynamic scaling logic for GKE pods using equation (7)
- Maps compute resources to Petri net elements

### gcp/storage.py
- Models Cloud Storage, Persistent Disks, Cloud SQL, Bigtable, Firestore
- Implements storage-specific attributes (IOPS, throughput)
- Defines energy and latency models for storage operations
- Maps storage resources to Petri net elements

### gcp/network.py
- Models VPC, Load Balancers, CDN, DNS, Interconnect, VPN
- Implements network-specific attributes (bandwidth, latency)
- Defines energy models for network operations
- Maps network resources to Petri net elements

### gcp/security.py
- Models IAM, Security Command Center, Cloud KMS, Cloud Armor
- Implements security-specific processing delays
- Defines energy overhead for security operations
- Maps security resources to Petri net elements

## Models Module

### models/\_\_init\_\_.py
- Package initialization for the models module
- Exports key modeling classes and functions

### models/latency.py
- Implements the latency estimation model (equation 1)
- Calculates transition delays (δ(t))
- Estimates congestion-based delays (Δcongestion)
- Computes critical path latency

### models/energy.py
- Implements the energy consumption model (equations 2, 3, 4)
- Defines energy weight factors (wi)
- Implements resource utilization to energy mapping functions (fi(xi))
- Calculates per-resource and total energy consumption

### models/carbon.py
- Implements the carbon footprint estimation model (equations 5, 6)
- Defines regional carbon factors (CF(regioni))
- Calculates carbon emissions based on energy consumption
- Aggregates carbon footprint across resources

### models/scaling.py
- Implements the dynamic scaling model for GKE pods (equation 7)
- Calculates required pod count based on request rate and capacity
- Adjusts scaling based on geolocation factors
- Handles autoscaling constraints and thresholds

### models/constraints.py
- Defines resource and operational constraints
- Implements constraint validation
- Models service limits and quotas
- Handles constraint violations during simulation

### models/aggregation.py
- Implements result aggregation across simulation runs
- Calculates statistical properties (mean, variance, percentiles)
- Combines results from multiple simulation iterations
- Generates summary reports and metrics

## Terraform Module

### terraform/\_\_init\_\_.py
- Package initialization for the Terraform module
- Exports key Terraform integration classes

### terraform/parser.py
- Parses Terraform configuration files
- Extracts resource definitions and attributes
- Maps Terraform resources to LEAF-Cloud model elements
- Handles Terraform variable resolution

### terraform/generator.py
- Generates Terraform configuration from simulation results
- Creates resource blocks with optimized parameters
- Implements the mapping logic between simulation outputs and Terraform syntax
- Produces deployable Terraform files with estimated resource requirements

### terraform/model_builder.py
- Builds the LEAF-Cloud model from Terraform configurations
- Translates Terraform resources into Petri net elements
- Establishes relationships between resources
- Sets initial parameters based on Terraform input

## Export Module

### export/\_\_init\_\_.py
- Package initialization for the export module
- Exports key visualization classes

### export/class_diagram.py
- Generates class diagrams of the modeled infrastructure
- Visualizes relationships between GCP components
- Creates UML representations of the resource hierarchy
- Exports diagrams in standard formats (SVG, PNG)

### export/deployment_diagram.py
- Creates deployment diagrams showing the physical layout
- Visualizes regional distribution of resources
- Shows network connections and data flows
- Exports deployment visualizations in standard formats

## Utils Module

### utils/\_\_init\_\_.py
- Package initialization for the utils module
- Exports utility functions

### utils/helpers.py
- Provides common utility functions
- Implements logging and error handling
- Offers data conversion and formatting tools
- Contains mathematical utilities for model calculations

# Basic simulation with default parameters
python -m leaf-cloud.main simulate --terraform ./terraform_files/

# Simulation with custom workload rate (200 requests/sec)
python -m leaf-cloud.main simulate --terraform ./terraform_files/ --workload-rate 200

# Simulation with custom duration (30 minutes) and output directory
python -m leaf-cloud.main simulate --terraform ./terraform_files/ --duration 1800 --output ./results/

# Simulation with custom config file and multiple iterations
python -m leaf-cloud.main simulate --terraform ./terraform_files/ --config config.yaml --iterations 5

# Simulation with custom terraform variable files
python -m leaf-cloud.main simulate --terraform ./terraform_files/ --var-files prod.tfvars test.tfvars

# Simulation with queue factor (expand resource capacity modeling queue buffers)
python -m leaf-cloud.main simulate --terraform ./terraform_files/ --queue-factor 1.5

# Simulation with logging enabled and custom log level
python -m leaf-cloud.main simulate --terraform ./terraform_files/ --enable-logging --log-level DEBUG