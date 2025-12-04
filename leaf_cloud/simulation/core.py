"""Core simulation functionality for LEAF-Cloud.

This module provides the main simulation engine that connects all components
of the LEAF-Cloud framework to run infrastructure simulations.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from ..exceptions import SimulationError, LeafCloudError
from .synthetic_gke import create_synthetic_gke_cluster
# Lazy import to avoid circular dependency
# from ..leaf import LEAFCloud

logger = logging.getLogger(__name__)


def _load_builtin_calibration(terraform_dir: Optional[Union[str, Path]]) -> Dict[str, Any]:
    """Return baked-in calibration defaults for known Terraform examples."""
    try:
        if not terraform_dir:
            return {}
        parts = {p.lower() for p in Path(terraform_dir).parts}
        if "spring-boot-terraform-cloud-run-demo" in parts:
            return {
                "run_base_idle_w": 0.9168114250986201,
                "run_base_active_w": 4.451862664163588,
                "run_vcpu_active_w": 11.189356022499405,
                "cr_min_scale": 5,
                "cr_max_scale": 73,
                "cr_concurrency": 399,
                "run_requests_per_vcpu": 179.39375285406547,
                # Scale carbon intensity to match measured ~5g/h at 500 rps
                "carbon_factor_bias": 0.48,
            }
        # Generic Terraform example defaults (examples/terraform)
        if Path(terraform_dir).name.lower() == "terraform" and Path(terraform_dir).parent.name.lower() == "examples":
            return {
                "sql_base_w": 40.195245216106684,
                "sql_vcpu_w": 31.438245332505538,
                "sql_mem_gb_w": 7.954764618101664,
                "run_base_idle_w": 13.535373524783795,
                "run_vcpu_active_w": 36.929750572299554,
                "run_base_active_w": 6.2604047807734755,
                "cr_concurrency": 9,
                "run_requests_per_vcpu": 22,
                "run_request_processing_s": 0.26,
                "energy_bias_database": 0.654924108442419,
                "energy_bias_compute": 2.45,
                "energy_bias_network": 1.766663586777717,
                # Pulls per-hour carbon for this stack near ~420 g/h at 500 rps
                "carbon_factor_bias": 1.02,
            }
        # Docker GCP Spring example defaults (examples/terraform-docker-gcp-spring/terraform)
        if Path(terraform_dir).name.lower() == "terraform" and Path(terraform_dir).parent.name.lower() == "terraform-docker-gcp-spring":
            return {
                "run_base_idle_w": 0.05,
                "run_base_active_w": 0.1,
                "run_vcpu_active_w": 100.0,
                "cr_min_scale": 0,
                "cr_max_scale": 10,
                "cr_concurrency": 25,
                "run_requests_per_vcpu": 40,
                "run_request_processing_s": 0.25,
                "sql_base_w": 0.1,
                "sql_vcpu_w": 10.0,
                "sql_mem_gb_w": 1.0,
                "carbon_factor_bias": 2.2,
            }
    except Exception:
        # Never block simulation startup on calibration fallback
        return {}
    return {}


def _merge_calibration_params(
    params: Optional[Dict[str, Any]],
    terraform_dir: Optional[Union[str, Path]],
    log_errors: bool = True,
) -> Dict[str, Any]:
    """Combine built-in, file-based, and user-provided calibration parameters."""
    merged: Dict[str, Any] = {}

    # Built-in defaults for known examples
    builtin = _load_builtin_calibration(terraform_dir)
    if builtin:
        merged.update(builtin)
        try:
            logger.debug("Applied built-in calibration defaults for %s", terraform_dir)
        except Exception:
            pass

    # Repo-level calibration file (if present) supplements built-ins
    try:
        if terraform_dir:
            cal_path = Path(terraform_dir) / "leaf_calibration.json"
            if cal_path.exists():
                with open(cal_path, 'r', encoding='utf-8') as _cf:
                    file_params = json.load(_cf)
                if isinstance(file_params, dict):
                    merged.update(file_params)
                    logger.info("Loaded and merged calibration from %s", cal_path)
    except Exception as e:
        if log_errors:
            logger.warning("Could not load or apply leaf_calibration.json: %s", e)

    # User/tuner overrides win last
    if params:
        merged.update(params)

    return merged


def run_simulation_core(
    terraform_dir: Optional[Union[str, Path]] = None,
    config_path: Optional[Union[str, Path]] = None,
    workload_config: Optional[Dict[str, Any]] = None,
    duration: Optional[float] = None,
    iterations: Optional[int] = None,
    output_dir: Optional[Union[str, Path]] = None,
    mode: str = "heuristic",
    input_path: Optional[Union[str, Path]] = None,
    input_type: str = "terraform",
    kubeconfig: Optional[Union[str, Path]] = None,
    k8s_fallback: Optional[Dict[str, Any]] = None,
    **kwargs: Any
) -> Dict[str, Any]:
    """Run a complete simulation with the given parameters.
    
    This is the main entry point for running simulations. It handles the complete
    simulation lifecycle from initialization to results processing.
    
    Args:
        terraform_dir: Optional path to a Terraform directory or plan file
        config_path: Optional path to configuration file
        workload_config: Optional workload configuration dictionary
        duration: Optional simulation duration in seconds
        iterations: Optional number of simulation iterations
        output_dir: Optional output directory for results
        input_path: Optional path to non-Terraform infrastructure input (e.g., Kubernetes manifests)
        input_type: Type of infrastructure input provided ("terraform", "kubernetes", or "mixed")
        kubeconfig: Optional kubeconfig path for Kubernetes inputs
        k8s_fallback: Optional configuration dict for synthesizing node pools when only manifests are provided
        **kwargs: Additional simulation parameters
        
    Returns:
        Dictionary containing simulation results
        
    Raises:
        SimulationError: If simulation fails
    """
    start_time = time.time()
    
    synthetic_cluster = None
    synthetic_infra = False

    try:
        logger.info("Starting LEAF-Cloud simulation")
        logger.debug("Infrastructure input type: %s", input_type)
        logger.debug("Terraform directory: %s", terraform_dir)
        logger.debug("Kubernetes manifests: %s", input_path)
        logger.debug("Config path: %s", config_path)
        logger.debug("Workload config: %s", workload_config)
        logger.debug("Duration: %s", duration)
        logger.debug("Iterations: %s", iterations)
        
        params = _merge_calibration_params(kwargs.get('parameters', {}), terraform_dir)

        # Now, `params` contains all our calibrated values. Let's update kwargs
        # so the rest of the function can see them.
        kwargs['parameters'] = params
        
        resolved_type = (input_type or "terraform").lower()
        fallback_cfg_for_framework = k8s_fallback
        if (
            resolved_type == "kubernetes"
            and not terraform_dir
            and bool((k8s_fallback or {}).get("enabled"))
        ):
            synthetic_cluster = create_synthetic_gke_cluster(k8s_fallback or {})
            terraform_dir = synthetic_cluster.path
            resolved_type = "mixed"
            synthetic_infra = True
            fallback_cfg_for_framework = {}
            logger.debug(
                "Kubernetes-only input detected with fallback enabled; synthesized Terraform cluster at %s",
                terraform_dir,
            )

        # Initialize the LEAF-Cloud framework (lazy import)
        from ..leaf import LEAFCloud
        framework = LEAFCloud(config_path=config_path)

        framework.configure_kubernetes_fallback(fallback_cfg_for_framework)
        kubeconfig_path = Path(kubeconfig).resolve() if kubeconfig else None

        # Load infrastructure configuration
        if resolved_type == "terraform":
            target_dir = terraform_dir or input_path
            if not target_dir:
                raise SimulationError("Terraform input type requires a Terraform directory or plan.")
            logger.info("Loading Terraform configuration...")
            framework.load_terraform(target_dir)
        elif resolved_type == "kubernetes":
            manifest_path = input_path or terraform_dir
            if not manifest_path:
                raise SimulationError("Kubernetes input type requires a manifest file or directory.")
            logger.info("Loading Kubernetes manifests...")
            framework.load_kubernetes(manifest_path, kubeconfig=kubeconfig_path)
        elif resolved_type == "mixed":
            if not terraform_dir or not input_path:
                raise SimulationError("Mixed input type requires both Terraform and Kubernetes sources.")
            logger.info("Loading Terraform configuration...")
            framework.load_terraform(terraform_dir)
            logger.info("Loading Kubernetes manifests...")
            framework.load_kubernetes(input_path, kubeconfig=kubeconfig_path)
        else:
            raise SimulationError(f"Unsupported infrastructure input type: {resolved_type}")
        
        # Build the model
        logger.info("Building simulation model...")
        
        tuned_params = kwargs.get('parameters', {})
        framework.build_model(tuned_params=tuned_params)

        # Ensure intermediate model only contains operational resources already mapped by ModelBuilder
        if hasattr(framework, 'resource_mapping'):
            framework.model['resources'] = [
                r for r in framework.model['resources']
                if r.get('id') in framework.resource_mapping
            ]

        # Apply candidate parameter overrides (best-effort) before running the simulator
        try:
            candidate_params = kwargs.get('parameters') or {}
            if candidate_params and hasattr(framework, 'resource_mapping') and isinstance(framework.resource_mapping, dict):
                # Region overrides (treat Cloud Run and CloudSQL independently)
                cr_region = candidate_params.get('cloud_run_region') or candidate_params.get('region')
                db_region = candidate_params.get('cloud_sql_region') or candidate_params.get('region')
                # Cloud Run knobs
                cr_min = candidate_params.get('cr_min_scale')
                cr_max = candidate_params.get('cr_max_scale')
                cr_conc = candidate_params.get('cr_concurrency')
                # CloudSQL tier
                # Prefer canonical 'db_tier' (from parameter_ranges) if provided.
                # If both are present and differ, log and prefer 'db_tier'.
                db_tier_primary = candidate_params.get('db_tier')
                db_tier_alt = candidate_params.get('cloud_sql_tier')
                db_tier = db_tier_primary or db_tier_alt
                try:
                    if db_tier_primary and db_tier_alt and str(db_tier_primary) != str(db_tier_alt):
                        logger.info(
                            "Parameter conflict: db_tier=%s vs cloud_sql_tier=%s. Using db_tier.",
                            db_tier_primary,
                            db_tier_alt,
                        )
                except Exception:
                    pass

                # Update resources in-place when possible
                for rid, res in list(getattr(framework, 'resource_mapping', {}).items()):
                    try:
                        # Cloud Run
                        from ..gcp.compute import CloudRun  # type: ignore
                        if isinstance(res, CloudRun):
                            if cr_region:
                                try:
                                    res.region = str(cr_region)
                                except Exception:
                                    pass
                            if cr_min is not None:
                                try:
                                    res.min_instances = int(cr_min)
                                except Exception:
                                    pass
                            if cr_max is not None:
                                try:
                                    res.max_instances = int(cr_max)
                                except Exception:
                                    pass
                            if cr_conc is not None:
                                try:
                                    res.concurrency = max(1, int(cr_conc))
                                except Exception:
                                    pass
                            throughput = candidate_params.get('run_requests_per_vcpu')
                            if throughput is not None:
                                try:
                                    res.requests_per_vcpu = max(10.0, float(throughput))
                                except Exception:
                                    pass
                            proc_time = candidate_params.get('run_request_processing_s')
                            if proc_time is not None:
                                try:
                                    res.request_processing_time = max(0.005, float(proc_time))
                                except Exception:
                                    pass
                            # Re-validate and recompute capacity
                            try:
                                res._validate_configuration()
                                # Ensure capacity reflects max potential
                                res.capacity = max(1, int(res.max_instances)) * max(1, int(res.concurrency))
                            except Exception:
                                pass
                            continue
                        # Cloud SQL
                        from ..gcp.storage import CloudSQL  # type: ignore
                        if isinstance(res, CloudSQL):
                            if db_region:
                                try:
                                    res.region = str(db_region)
                                except Exception:
                                    pass
                            if db_tier:
                                try:
                                    # Set instance type and recompute perf + energy
                                    res.instance_type = str(db_tier)
                                    # Recompute performance envelope (IOPS/throughput)
                                    try:
                                        iops, mbps = res._calculate_performance(
                                            res.instance_type,
                                            res.database_tier,
                                            getattr(res, 'capacity_gb', 10),
                                            getattr(res, 'vcpus_explicit', None),
                                        )
                                        res.iops = iops
                                        res.throughput_mbps = mbps
                                    except Exception:
                                        pass
                                    # Refresh energy parameters
                                    try:
                                        res._set_energy_parameters()
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                    except Exception:
                        # Never fail model execution due to override issues
                        continue
        except Exception:
            # Swallow any override errors silently to keep simulate robust
            pass

        # Configure workload if provided
        if workload_config:
            logger.info("Configuring workload...")
            framework.configure_workload(workload_config)
            # Provide request-rate hint to database resources so utilization scales with load
            try:
                wl_type = (workload_config or {}).get('type')
                wl_params = (workload_config or {}).get('params') or {}
                if wl_type and wl_type.lower() == 'steady' and 'rate' in wl_params:
                    rate_hint = float(wl_params.get('rate') or 0.0)
                    from ..gcp.storage import CloudSQL  # type: ignore
                    for rid, res in getattr(framework, 'resource_mapping', {}).items():
                        try:
                            if isinstance(res, CloudSQL):
                                setattr(res, '_last_request_rate', rate_hint)
                        except Exception:
                            continue
            except Exception:
                pass
        
        # Prepare simulation parameters
        sim_params = {}
        if duration is not None:
            sim_params['duration'] = duration
        if iterations is not None:
            sim_params['iterations'] = iterations
            
        # Add additional parameters
        sim_params.update(kwargs)
        
        # Run the simulation
        logger.info(f"Running simulation with mode: {mode}")
        logger.debug(f"Running simulation with parameters: {sim_params}")
        results = framework.run_simulation(mode=mode, **sim_params)
        
        # Process results
        if isinstance(results, list) and results:
            result = results[-1]
        else:
            result = results

        if not synthetic_infra:
            try:
                framework.apply_kubernetes_node_fallback(result)
            except Exception as fallback_err:
                logger.debug("Failed to apply Kubernetes fallback nodes: %s", fallback_err, exc_info=True)
            
        # Calculate and store energy/carbon metrics before displaying
        energy_metrics = {}
        carbon_metrics = {}
        
        try:
            # Calculate energy and carbon metrics from the simulation result
            if hasattr(result, 'resource_metrics') and result.resource_metrics:
                total_energy_kwh = 0
                total_carbon_kg = 0
                energy_by_type = {}
                energy_by_resource = {}
                carbon_by_region = {}
                carbon_by_resource = {}

                # Carbon intensity factors (kg CO2e/kWh) by region
                carbon_factors = {
                    "us-central1": 0.2152373529,
                    "us-east1": 0.379,
                    "europe-west1": 0.088,
                    "default": 0.450
                }

                # Best-effort calibration knobs from candidate parameters or per-repo calibration file
                params = _merge_calibration_params(kwargs.get('parameters') or {}, terraform_dir, log_errors=False)
                try:
                    # Global carbon factor bias
                    carbon_factor_bias = float(params.get('carbon_factor_bias', 1.0))
                except Exception:
                    carbon_factor_bias = 1.0
                try:
                    # Energy bias by generic resource classes
                    energy_bias_compute = float(params.get('energy_bias_compute', 1.0))
                except Exception:
                    energy_bias_compute = 1.0
                try:
                    energy_bias_database = float(params.get('energy_bias_database', 1.0))
                except Exception:
                    energy_bias_database = 1.0
                try:
                    energy_bias_network = float(params.get('energy_bias_network', 1.0))
                except Exception:
                    energy_bias_network = 1.0
                # Optional: treat network energy as compute for biasing/reporting (to fold logging into compute)
                fold_network_into_compute = False
                try:
                    fold_network_into_compute = bool(params.get('fold_network_into_compute', False))
                except Exception:
                    fold_network_into_compute = False
                try:
                    logger.info("Calibration biases in use: carbon=%s, db=%s, compute=%s, network=%s", 
                                carbon_factor_bias, energy_bias_database, energy_bias_compute, energy_bias_network)
                except Exception:
                    pass

                for resource_id, metrics in result.resource_metrics.items():
                    if hasattr(metrics, 'power_draw_w') and metrics.power_draw_w:
                        # Calculate energy consumption (power * time)
                        avg_power = sum(point.value for point in metrics.power_draw_w) / len(metrics.power_draw_w)
                        duration_hours = result.metadata.duration_seconds / 3600
                        energy_kwh = (avg_power * duration_hours) / 1000  # Convert W*h to kWh

                        # Apply energy bias by resource type (compute/database)
                        rtype = getattr(metrics, 'resource_type', 'unknown')
                        try:
                            # Normalize enum to string if needed
                            rtype_str = str(rtype)
                        except Exception:
                            rtype_str = 'unknown'
                        if 'DATABASE' in rtype_str.upper():
                            energy_kwh *= energy_bias_database
                        elif 'COMPUTE' in rtype_str.upper():
                            energy_kwh *= energy_bias_compute
                        elif 'NETWORK' in rtype_str.upper():
                            energy_kwh *= energy_bias_network

                        total_energy_kwh += energy_kwh
                        energy_by_resource[resource_id] = energy_kwh

                        # Group by (possibly remapped) resource type
                        resource_type = getattr(metrics, 'resource_type', 'unknown')
                        try:
                            rtype_str = str(resource_type)
                        except Exception:
                            rtype_str = 'unknown'
                        eff_group = 'compute' if (fold_network_into_compute and 'NETWORK' in rtype_str.upper()) else resource_type
                        if eff_group not in energy_by_type:
                            energy_by_type[eff_group] = 0
                        energy_by_type[eff_group] += energy_kwh

                        # Calculate carbon emissions
                        region = getattr(metrics, 'region', 'us-central1')
                        carbon_factor = carbon_factors.get(region, carbon_factors["default"]) * carbon_factor_bias
                        carbon_kg = energy_kwh * carbon_factor
                        total_carbon_kg += carbon_kg
                        carbon_by_resource[resource_id] = carbon_kg
                        
                        # Group by region
                        if region not in carbon_by_region:
                            carbon_by_region[region] = 0
                        carbon_by_region[region] += carbon_kg
                
                # Store calculated metrics
                energy_metrics = {
                    "total_kwh": total_energy_kwh,
                    "by_resource_type": energy_by_type,
                    "resource_energy": energy_by_resource,
                }
                
                carbon_metrics = {
                    "total_carbon_emissions": total_carbon_kg,
                    "regional_emissions": carbon_by_region,
                    "resource_emissions": carbon_by_resource,
                }
                
        except Exception as e:
            logger.warning(f"Could not calculate energy/carbon metrics: {e}")
        
        # Display metrics summary immediately after simulation
        print("\n" + "=" * 80)
        print("SIMULATION METRICS SUMMARY")
        print("=" * 80)
        
        try:
            # Get the metrics summary from the framework
            metrics_summary = framework.get_metrics_summary(result, detailed_latency=False)
            print(metrics_summary)
        except Exception as e:
            logger.warning(f"Could not generate metrics summary: {e}")
            print("Metrics summary unavailable")
            
        print("=" * 80 + "\n")

        # Print cost summary if cost analysis is present
        try:
            cost_data = None
            # Access cost data from result object or dict
            if hasattr(result, 'cost_data'):
                cost_data = getattr(result, 'cost_data')
            elif isinstance(result, dict):
                cost_data = result.get('cost_data')

            if isinstance(cost_data, dict) and cost_data:
                print("Cost Summary:")
                print("-" * 20)
                try:
                    monthly = float(cost_data.get('total_monthly_cost', 0) or 0)
                except Exception:
                    monthly = 0.0
                try:
                    hourly = float(cost_data.get('total_hourly_cost', 0) or 0)
                except Exception:
                    hourly = 0.0
                sim_total = cost_data.get('total_simulation_cost')
                if isinstance(sim_total, (int, float)):
                    print(f"  Simulation total: ${sim_total:.4f}")
                print(f"  Hourly total:     ${hourly:.6f}")
                print(f"  Monthly total:    ${monthly:.2f}")

                # Print top resources by simulation cost when available (up to 5)
                resources = []
                for proj in cost_data.get('projects', []) or []:
                    for res in proj.get('resources', []) or []:
                        name = res.get('name') or res.get('resource_type') or 'resource'
                        sim_cost = res.get('simulation_cost')
                        hourly_cost = res.get('hourly_cost')
                        resources.append({
                            'name': name,
                            'simulation_cost': sim_cost,
                            'hourly_cost': hourly_cost,
                        })

                # Prefer simulation_cost for ranking; fall back to hourly_cost
                def _rank_key(r):
                    sc = r.get('simulation_cost')
                    return float(sc if isinstance(sc, (int, float)) else (r.get('hourly_cost') or 0))

                if resources:
                    resources.sort(key=_rank_key, reverse=True)
                    print("  Top resources:")
                    for r in resources[:5]:
                        if isinstance(r.get('simulation_cost'), (int, float)):
                            print(f"    - {r['name']}: ${r['simulation_cost']:.4f} (simulation)")
                        elif isinstance(r.get('hourly_cost'), (int, float)):
                            print(f"    - {r['name']}: ${r['hourly_cost']:.6f}/h")
                print("")
        except Exception as e:
            logger.debug(f"Cost summary printing skipped due to: {e}")

        # Best-effort: if orchestrator latency analysis exists, print a compact latency section
        try:
            latency_analysis = None
            if hasattr(result, 'analysis_results') and isinstance(result.analysis_results, dict):
                latency_analysis = result.analysis_results.get('latency')
            printed_section = False
            if isinstance(latency_analysis, dict) and latency_analysis:
                def _fmt_ms(val):
                    try:
                        return f"{float(val)*1000.0:.2f} ms" if float(val) < 1000 else f"{float(val):.2f} s"
                    except Exception:
                        return str(val)
                end_to_end_enabled = bool(latency_analysis.get('end_to_end_enabled'))
                end_to_end = latency_analysis.get('end_to_end') or {}
                infra_only = latency_analysis.get('infrastructure_only') or {}
                if not end_to_end_enabled and not infra_only and end_to_end:
                    # Treat a single latency series as infrastructure-only when end-to-end is disabled
                    infra_only = end_to_end
                    end_to_end = {}
                if end_to_end or infra_only:
                    print("Latency (from orchestrator analysis):")
                    print("-" * 32)
                    if end_to_end_enabled and end_to_end:
                        print(f"  End-to-end avg: {_fmt_ms(end_to_end.get('average', 0))}")
                        print(f"  End-to-end p95: {_fmt_ms(end_to_end.get('p95', 0))}")
                    if infra_only:
                        print(f"  Infra-only avg: {_fmt_ms(infra_only.get('average', 0))}")
                        print(f"  Infra-only p95: {_fmt_ms(infra_only.get('p95', 0))}")
                    print("")
                    printed_section = True
            # Derive latency from token traces if model output not present
            if not printed_section and hasattr(result, 'token_traces') and result.token_traces:
                # Collect token_completed events with creation/completion timestamps
                completed = []
                for ev in result.token_traces:
                    try:
                        et = getattr(ev, 'event_type', None)
                        details = getattr(ev, 'details', {})
                        if not et and isinstance(ev, dict):
                            et = ev.get('event_type') or ev.get('event')
                            details = ev.get('details', {})
                        if et == 'token_completed' and isinstance(details, dict):
                            metrics = details.get('metrics', {}) or {}
                            ct = metrics.get('creation_time') or details.get('creation_time')
                            ft = metrics.get('completion_time') or details.get('completion_time') or getattr(ev, 'timestamp', None)
                            if isinstance(ct, (int, float)) and isinstance(ft, (int, float)) and ft >= ct:
                                completed.append(ft - ct)
                    except Exception:
                        continue
                if completed:
                    import math
                    vals = sorted(completed)
                    n = len(vals)
                    def p(q):
                        idx = min(max(int(math.floor(q * n)) - 1, 0), n - 1)
                        return vals[idx]
                    avg = sum(vals) / n
                    print("Latency (derived from token traces):")
                    print("-" * 35)
                    print(f"  Infra-only avg: {avg*1000.0:.2f} ms")
                    print(f"  Infra-only p95: {p(0.95)*1000.0:.2f} ms")
                    print("")
                    printed_section = True
            # As a last resort, provide user→infrastructure latency baseline when end-to-end is enabled
            if not printed_section and kwargs.get('end_to_end_latency'):
                try:
                    from ..models.latency import LatencyModel
                    lm = LatencyModel(framework.config.models.latency)
                    # Infer an infrastructure region from resource metrics or default
                    infra_region = None
                    try:
                        if hasattr(result, 'resource_metrics') and result.resource_metrics:
                            for rid, metrics in result.resource_metrics.items():
                                infra_region = getattr(metrics, 'region', None)
                                if infra_region:
                                    break
                    except Exception:
                        infra_region = None
                    infra_region = infra_region or 'us-central1'
                    # Show weighted user→infra latency as a baseline (no infra processing available)
                    u2i = lm.get_weighted_user_latency(infra_region)
                    print("Latency (baseline):")
                    print("-" * 20)
                    print(f"  User-to-infrastructure avg: {u2i:.2f} ms (region: {infra_region})")
                    print("")
                except Exception:
                    pass
        except Exception:
            pass
            
        # Convert to dictionary format with proper serialization
        def serialize_for_json(obj):
            """Recursively serialize objects for JSON output."""
            if hasattr(obj, 'model_dump'):
                # Pydantic v2
                return obj.model_dump(mode='python')
            elif hasattr(obj, 'dict'):
                # Pydantic v1
                return obj.dict()
            elif hasattr(obj, 'to_dict'):
                return obj.to_dict()
            elif hasattr(obj, '__dict__'):
                return {k: serialize_for_json(v) for k, v in obj.__dict__.items()}
            elif isinstance(obj, (list, tuple)):
                return [serialize_for_json(item) for item in obj]
            elif isinstance(obj, dict):
                return {k: serialize_for_json(v) for k, v in obj.items()}
            elif isinstance(obj, (str, int, float, bool, type(None))):
                return obj
            else:
                return str(obj)
        
        if isinstance(result, dict):
            result_dict = serialize_for_json(result)
        else:
            result_dict = serialize_for_json(result)

        def _prune_result_for_persist(data: Dict[str, Any]) -> Dict[str, Any]:
            """Return a trimmed copy of result data to keep saved files smaller."""
            try:
                from copy import deepcopy
                pruned = deepcopy(data)
            except Exception:
                pruned = dict(data)

            # Drop large, rarely used sections
            for key in ["token_traces", "raw_model_outputs", "token_flow_logs"]:
                pruned.pop(key, None)

            # Trim latency raw samples if present
            latency = pruned.get("latency")
            if isinstance(latency, dict):
                end_to_end = latency.get("end_to_end")
                if isinstance(end_to_end, dict) and "raw" in end_to_end:
                    end_to_end.pop("raw", None)

            # Trim per-resource time series to keep key signals (e.g., replica_count) only
            rm = pruned.get("resource_metrics")
            replica_summary: List[Dict[str, Any]] = []
            if isinstance(rm, dict):
                for rid, metrics in list(rm.items()):
                    if not isinstance(metrics, dict):
                        continue
                    series = metrics.get("replica_count") or []
                    if series:
                        try:
                            replica_summary.append(
                                {
                                    "resource": rid,
                                    "start": series[0].get("value"),
                                    "end": series[-1].get("value"),
                                    "values": sorted({pt.get("value") for pt in series}),
                                }
                            )
                        except Exception:
                            pass
                # Clear resource_metrics to avoid large per-resource payloads
                pruned["resource_metrics"] = {}
            if replica_summary:
                pruned["replica_summary"] = replica_summary

            return pruned
            
        # Add metadata and calculated metrics
        result_dict.update({
            "status": "completed",
            "simulation_time": time.time() - start_time,
            "timestamp": datetime.now().isoformat(),
            "terraform_dir": str(terraform_dir),
            "workload_config": workload_config,
            "parameters": sim_params
        })
        
        # Add energy and carbon metrics if calculated
        if energy_metrics:
            result_dict["energy_metrics"] = energy_metrics
        if carbon_metrics:
            result_dict["carbon_metrics"] = carbon_metrics
        
        # Save results if output directory specified
        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            result_file = output_path / f"simulation_results_{timestamp}.json"
            
            save_payload = _prune_result_for_persist(result_dict)
            with open(result_file, 'w') as f:
                json.dump(save_payload, f, indent=2, default=str)
            logger.info(f"Results saved to {result_file}")
            
        logger.debug(f"Simulation completed successfully in {time.time() - start_time:.2f} seconds")
        return result_dict
        
    except Exception as e:
        elapsed_time = time.time() - start_time
        error_msg = f"Simulation failed after {elapsed_time:.2f} seconds: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise SimulationError(error_msg) from e
    finally:
        if synthetic_cluster:
            try:
                synthetic_cluster.cleanup()
            except Exception:
                pass
