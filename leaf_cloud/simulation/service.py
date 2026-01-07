"""Shared simulation pipeline helpers for LEAF-Cloud endpoints."""
from __future__ import annotations

import json
import logging
import os
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..leaf import LEAFCloud
from ..utils.results import SimulationResult as CanonicalSimulationResult
from .synthetic_gke import create_synthetic_gke_cluster, SyntheticGKECluster

logger = logging.getLogger(__name__)

def _serialize_k8s_resource(resource: Any) -> Dict[str, Any]:
    """Convert an internal K8sResource into a serializable dictionary."""
    base: Dict[str, Any]
    try:
        # Prefer the parser-provided serializer for consistent structure
        base = resource.to_dict()  # type: ignore[attr-defined]
    except Exception:
        source = getattr(resource, "source", None)
        base = {
            "id": getattr(resource, "resource_id", ""),
            "kind": getattr(resource, "kind", ""),
            "name": getattr(resource, "name", ""),
            "namespace": getattr(resource, "namespace", "default"),
            "labels": dict(getattr(resource, "labels", {}) or {}),
            "annotations": dict(getattr(resource, "annotations", {}) or {}),
            "selectors": dict(getattr(resource, "selectors", {}) or {}),
            "depends_on": list(getattr(resource, "depends_on", []) or []),
            "source": {
                "path": str(getattr(source, "path", "")),
                "index": getattr(source, "index", 0),
                "line": getattr(source, "start_line", 1),
            },
        }

    kind = str(base.get("kind") or "").lower()
    spec = getattr(resource, "spec", {})

    if kind == "horizontalpodautoscaler":
        base["autoscaling"] = _extract_hpa_details(spec, base)
    elif kind in {"deployment", "statefulset"}:
        base["workload"] = _extract_workload_details(spec)

    return base


def _extract_hpa_details(spec: Any, resource_dict: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        return {}

    target = spec.get("scaleTargetRef", {}) or {}
    namespace = target.get("namespace") or resource_dict.get("namespace") or "default"
    target_info = {
        "kind": target.get("kind"),
        "name": target.get("name"),
        "namespace": namespace,
        "api_version": target.get("apiVersion"),
    }

    metrics = spec.get("metrics")
    if not isinstance(metrics, list):
        metrics = []

    return {
        "min_replicas": spec.get("minReplicas"),
        "max_replicas": spec.get("maxReplicas"),
        "target_cpu_utilization": spec.get("targetCPUUtilizationPercentage"),
        "metrics": metrics,
        "scale_target_ref": target_info,
    }


def _extract_workload_details(spec: Any) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        return {}

    template_spec = (
        spec.get("template", {}).get("spec", {})
        if isinstance(spec.get("template"), dict)
        else {}
    )
    containers = template_spec.get("containers") or []
    container_summaries: List[Dict[str, Any]] = []
    if isinstance(containers, list):
        for container in containers:
            if not isinstance(container, dict):
                continue
            resources = container.get("resources", {}) or {}
            container_summaries.append(
                {
                    "name": container.get("name"),
                    "image": container.get("image"),
                    "requests": dict(resources.get("requests") or {}),
                    "limits": dict(resources.get("limits") or {}),
                }
            )

    return {
        "replicas": spec.get("replicas"),
        "strategy": (spec.get("strategy") or {}).get("type") if isinstance(spec.get("strategy"), dict) else spec.get("strategy"),
        "containers": container_summaries,
    }


def _safe_time_series(points: Optional[List[Any]]) -> List[Dict[str, float]]:
    """Normalize orchestrator time-series objects into timestamp/value dictionaries."""
    series: List[Dict[str, float]] = []
    if not points:
        return series

    for point in points:
        timestamp = None
        value = None
        if hasattr(point, "timestamp"):
            timestamp = getattr(point, "timestamp")
            value = getattr(point, "value", None)
        elif isinstance(point, dict):
            timestamp = point.get("timestamp")
            value = point.get("value")

        try:
            ts = float(timestamp) if timestamp is not None else None
            val = float(value) if value is not None else None
        except (TypeError, ValueError):
            ts = None
            val = None

        if ts is None or val is None:
            continue
        series.append({"timestamp": ts, "value": val})

    series.sort(key=lambda item: item["timestamp"])
    return series

def _get_metric_attr(metrics_obj: Any, key: str) -> Any:
    """Safely retrieve a metric attribute from either an object or dict."""
    if metrics_obj is None:
        return None
    if hasattr(metrics_obj, key):
        return getattr(metrics_obj, key)
    if isinstance(metrics_obj, dict):
        value = metrics_obj.get(key)
        if isinstance(value, list):
            normalized = []
            for entry in value:
                if isinstance(entry, dict):
                    normalized.append(entry)
                else:
                    normalized.append({"timestamp": getattr(entry, "timestamp", None), "value": getattr(entry, "value", None)})
            return normalized
        return value
    return None

def _is_mixed_mode(requested_mode: Optional[str], raw_result: CanonicalSimulationResult) -> bool:
    """Check whether the simulation ran in mixed mode."""
    candidates = [
        (requested_mode or "").strip().lower(),
    ]
    snapshot_mode = str((raw_result.config_snapshot or {}).get("simulation_mode", "")).strip().lower()
    if snapshot_mode:
        candidates.append(snapshot_mode)
    return any(mode == "mixed" for mode in candidates if mode)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_k8s_resource_id(kind: Optional[str], namespace: str, name: Optional[str]) -> Optional[str]:
    if not kind or not name:
        return None
    normalized_kind = kind.strip().lower()
    ns = namespace or "default"
    return f"{normalized_kind}::{ns}/{name}"


def _build_kubernetes_scaling_metrics(
    raw_result: CanonicalSimulationResult,
    derived_resource_mapping: Dict[str, Dict[str, Any]],
    kubernetes_resources: Optional[List[Dict[str, Any]]],
    requested_mode: Optional[str],
    requested_input_type: Optional[str],
) -> Dict[str, Any]:
    """Assemble node, pod, and HPA telemetry for mixed-mode simulations."""
    input_type = (requested_input_type or "").strip().lower()
    if not input_type:
        input_type = str((raw_result.metadata or {}).get("input_type", "")).strip().lower()

    is_mixed_infrastructure = input_type == "mixed"
    if not (is_mixed_infrastructure or _is_mixed_mode(requested_mode, raw_result)):
        return {}

    resource_metrics = raw_result.resource_metrics or {}
    kubernetes_resources = kubernetes_resources or []

    def _is_gke_node(resource_id: str, resource_info: Optional[Any]) -> bool:
        if resource_id and ".node[" in resource_id:
            return True
        if resource_info is None:
            return False
        cls = getattr(resource_info, "__class__", None)
        if cls is None:
            return False
        name = getattr(cls, "__name__", "")
        return "GKENode" in name

    node_entries: List[Dict[str, Any]] = []
    node_timelines: Dict[str, Dict[str, float]] = {}
    for resource_id, metrics in resource_metrics.items():
        resource_info = derived_resource_mapping.get(resource_id)
        if not _is_gke_node(resource_id, resource_info):
            continue
        series = _safe_time_series(_get_metric_attr(metrics, "utilization"))
        if not series:
            continue
        avg_util = statistics.mean(point["value"] for point in series)
        node_region = (resource_info or {}).get("region") if isinstance(resource_info, dict) else getattr(resource_info, "region", None)
        node_entries.append(
            {
                "id": resource_id,
                "average_utilization": avg_util,
                "max_utilization": max(point["value"] for point in series),
                "min_utilization": min(point["value"] for point in series),
                "series": series,
                "region": node_region,
            }
        )
        node_timelines[resource_id] = {
            "start": series[0]["timestamp"],
            "end": series[-1]["timestamp"],
        }
    logger.debug("Kubernetes scaling: collected %d node entries", len(node_entries))

    timestamps = sorted({point["timestamp"] for node in node_entries for point in node["series"]})
    node_count_series: List[Dict[str, float]] = []
    for ts in timestamps:
        active = sum(
            1
            for node_id, timeline in node_timelines.items()
            if timeline["start"] - 1e-9 <= ts <= timeline["end"] + 1e-9
        )
        node_count_series.append({"timestamp": ts, "value": float(active)})

    node_count_time_series = {
        "timestamps": [point["timestamp"] for point in node_count_series],
        "values": [point["value"] for point in node_count_series],
    } if node_count_series else {"timestamps": [], "values": []}

    autoscaling_events: List[Dict[str, Any]] = []
    prev_value: Optional[float] = None
    for point in node_count_series:
        count = point["value"]
        ts = point["timestamp"]
        if prev_value is None:
            prev_value = count
            continue
        delta = count - prev_value
        if abs(delta) > 1e-6:
            if delta > 0:
                changed_nodes = [
                    node_id
                    for node_id, timeline in node_timelines.items()
                    if abs(timeline["start"] - ts) < 1e-6
                ]
            else:
                changed_nodes = [
                    node_id
                    for node_id, timeline in node_timelines.items()
                    if abs(timeline["end"] - ts) < 1e-6
                ]
            autoscaling_events.append(
                {
                    "timestamp": ts,
                    "type": "scale_up" if delta > 0 else "scale_down",
                    "delta": delta,
                    "node_ids": changed_nodes,
                    "node_count": count,
                }
            )
        prev_value = count

    k8s_index = {entry.get("id"): entry for entry in kubernetes_resources if entry.get("id")}

    hpa_entries: List[Dict[str, Any]] = []
    for entry in kubernetes_resources:
        if str(entry.get("kind", "")).lower() != "horizontalpodautoscaler":
            continue
        hpa_id = entry.get("id")
        hpa_metrics = resource_metrics.get(hpa_id) if hpa_id else None
        hpa_util_series = _safe_time_series(_get_metric_attr(hpa_metrics, "utilization") if hpa_metrics else None)

        autoscaling = entry.get("autoscaling") or {}
        target_ref = autoscaling.get("scale_target_ref") or {}
        target_namespace = target_ref.get("namespace") or entry.get("namespace") or "default"
        target_id = _build_k8s_resource_id(target_ref.get("kind"), target_namespace, target_ref.get("name"))
        target_metrics = resource_metrics.get(target_id) if target_id else None
        target_series = _safe_time_series(_get_metric_attr(target_metrics, "utilization") if target_metrics else None)
        current_util = target_series[-1]["value"] if target_series else None
        replica_series = _safe_time_series(_get_metric_attr(target_metrics, "replica_count") if target_metrics else None)
        current_replicas = replica_series[-1]["value"] if replica_series else None

        hpa_data = {
            "id": hpa_id,
            "name": entry.get("name"),
            "namespace": entry.get("namespace"),
            "autoscaling": autoscaling,
            "current_utilization": current_util,
            "utilization_series": target_series or hpa_util_series,
            "target_id": target_id,
            "replica_series": replica_series,
            "current_replicas": current_replicas,
        }
        hpa_entries.append(hpa_data)
    logger.debug("Kubernetes scaling: collected %d HPA entries", len(hpa_entries))

    workload_resources = {
        entry.get("id"): entry
        for entry in kubernetes_resources
        if entry.get("workload")
    }
    pod_scaling: List[Dict[str, Any]] = []
    for hpa in hpa_entries:
        autoscaling = hpa.get("autoscaling") or {}
        target_ref = autoscaling.get("scale_target_ref") or {}
        target_kind = str(target_ref.get("kind") or "").lower()
        target_namespace = target_ref.get("namespace") or hpa.get("namespace") or "default"
        target_name = target_ref.get("name")
        if target_kind and target_name:
            target_id = _build_k8s_resource_id(target_kind, target_namespace, target_name)
        else:
            target_id = None
        workload = workload_resources.get(target_id) if target_id else None
        workload_info = workload.get("workload", {}) if workload else {}
        current_replicas = hpa.get("current_replicas")
        if current_replicas is None:
            current_replicas = workload_info.get("replicas")
        pod_scaling.append(
            {
                "namespace": target_namespace,
                "workload_kind": target_ref.get("kind"),
                "workload_name": target_name,
                "workload_id": target_id,
                "current_replicas": current_replicas,
                "containers": workload_info.get("containers", []),
                "hpa_id": hpa.get("id"),
                "min_replicas": autoscaling.get("min_replicas"),
                "max_replicas": autoscaling.get("max_replicas"),
            }
        )

    summary = {
        "initial_node_count": node_count_series[0]["value"] if node_count_series else 0,
        "max_node_count": max((point["value"] for point in node_count_series), default=0),
        "final_node_count": node_count_series[-1]["value"] if node_count_series else 0,
        "hpa_count": len(hpa_entries),
        "workload_count": len(pod_scaling),
        "duration_seconds": _safe_float(getattr(raw_result.metadata, "duration_seconds", 0.0)),
    }

    return {
        "mode": "mixed",
        "nodes": node_entries,
        "node_count": node_count_series,
        "node_count_time_series": node_count_time_series,
        "autoscaling_events": autoscaling_events,
        "hpa_resources": hpa_entries,
        "pod_scaling": pod_scaling,
        "summary": summary,
    }


def run_simulation_with_metrics(
    terraform: Optional[str],
    kubernetes_manifest: Optional[str],
    input_type: str,
    mode: str,
    kubeconfig: Optional[str],
    config: Optional[Dict[str, Any]],
    var_files: Optional[List[str]],
    workload_type: str,
    workload_params: Dict[str, Any],
    duration: float,
    queue_factor: float,
    output: Optional[str],
    k8s_fallback: Optional[Dict[str, Any]] = None,
) -> SimulationResultDict:
    """Run a simulation with the given parameters and enrich it with metrics."""
    start_time = time.time()

    if duration <= 0:
        raise ValueError(f"Duration must be positive, got {duration}")

    if queue_factor <= 0:
        raise ValueError(f"Queue factor must be positive, got {queue_factor}")

    resolved_input_type = (input_type or "").strip().lower()
    fallback_enabled = bool((k8s_fallback or {}).get("enabled"))
    synthetic_cluster: Optional[SyntheticGKECluster] = None
    synthetic_infra = False

    if not terraform and resolved_input_type == "kubernetes" and fallback_enabled:
        synthetic_cluster = create_synthetic_gke_cluster(k8s_fallback or {})
        terraform = str(synthetic_cluster.path)
        synthetic_infra = True

    if var_files and not terraform:
        raise ValueError("Variable files were provided but no Terraform input was specified.")

    if var_files:
        for var_file in var_files:
            if not os.path.exists(var_file):
                raise FileNotFoundError(f"Variable file not found: {var_file}")

    adjusted_input_type = resolved_input_type
    if synthetic_infra:
        adjusted_input_type = "mixed"

    logger.info(
        "Starting simulation with parameters: %s",
        {
            "terraform": terraform,
            "kubernetes_manifest": kubernetes_manifest,
            "input_type": input_type,
            "effective_input_type": adjusted_input_type,
            "mode": mode,
            "workload_type": workload_type,
            "duration": duration,
            "queue_factor": queue_factor,
            "output": output,
            "kubeconfig": kubeconfig,
            "k8s_fallback": k8s_fallback,
        },
    )

    try:
        framework = LEAFCloud()
        framework.configure_kubernetes_fallback({} if synthetic_infra else k8s_fallback)
        logger.debug("Framework created, loading infrastructure inputs")

        try:
            if terraform:
                framework.load_terraform(terraform, var_files)
                logger.info("Successfully loaded Terraform configuration from %s", terraform)

            if kubernetes_manifest:
                framework.load_kubernetes(kubernetes_manifest, kubeconfig=kubeconfig)
                logger.info("Successfully loaded Kubernetes manifests from %s", kubernetes_manifest)

            if not terraform and not kubernetes_manifest:
                raise ValueError("No infrastructure inputs provided. Specify Terraform and/or Kubernetes manifests.")

            framework.build_model()

            logger.info("Configuring workload: type=%s, params=%s", workload_type, workload_params)
            workload_config = {"type": workload_type, "params": workload_params}
            framework.configure_workload(workload_config)

            logger.info(
                "Starting simulation (mode=%s, duration=%.2fs, queue_factor=%.2f)",
                mode,
                duration,
                queue_factor,
            )

            raw_result = framework.run_simulation(
                mode=mode,
                duration=duration,
                queue_factor=queue_factor,
                output_dir=output,
            )
            logger.info("Simulation completed successfully")

            if not synthetic_infra:
                try:
                    framework.apply_kubernetes_node_fallback(raw_result)
                except Exception as fallback_err:
                    logger.debug("Kubernetes fallback injection failed: %s", fallback_err, exc_info=True)

            resource_mapping: Dict[str, Any] = {}
            try:
                if hasattr(framework, "orchestrator") and hasattr(framework.orchestrator, "_model_builder"):
                    model_builder = framework.orchestrator._model_builder  # type: ignore[attr-defined]
                    if hasattr(model_builder, "resource_mapping"):
                        resource_mapping = model_builder.resource_mapping  # type: ignore[attr-defined]
                        logger.info(
                            "Retrieved resource mapping with %d resources from framework",
                            len(resource_mapping),
                        )
                    else:
                        logger.warning("Model builder found but no resource_mapping attribute")
                else:
                    logger.debug("No orchestrator resource mapping available from framework")
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Failed to get resource mapping from framework: %s", exc)
                resource_mapping = {}

            framework_mapping = getattr(framework, "resource_mapping", None)
            if isinstance(framework_mapping, dict) and framework_mapping:
                merged_mapping = dict(resource_mapping or {})
                merged_mapping.update(framework_mapping)
                resource_mapping = merged_mapping

            kubernetes_resources: List[Dict[str, Any]] = []
            kube_collection = getattr(framework, "_kube_resources", None)
            if kube_collection:
                for resource in kube_collection:
                    try:
                        kubernetes_resources.append(_serialize_k8s_resource(resource))
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug("Failed to serialize Kubernetes resource: %s", exc)
            results = _transform_raw_simulation_result(
                raw_result,
                resource_mapping,
                kubernetes_resources,
                mode=mode,
                input_type=adjusted_input_type or input_type,
                terraform_path=None if synthetic_infra else terraform,
                synthetic_infra=synthetic_infra,
                k8s_fallback=k8s_fallback,
                duration_seconds=duration,
            )
            logger.debug("Transformed simulation results")

            # Ensure simulation info captures infrastructure metadata
            simulation_info = results.setdefault("metrics", {}).setdefault("simulation_info", {})
            simulation_info.update(
                {
                    "input_type": adjusted_input_type or input_type,
                    "mode": mode,
                    "terraform_path": None if synthetic_infra else terraform,
                    "kubernetes_manifest": kubernetes_manifest,
                    "kubeconfig": kubeconfig,
                    "synthetic_infrastructure": synthetic_infra,
                }
            )

            analysis_results = results.get("analysis_results") or {}

            fields_to_keep = [
                "simulation_info",
                "workload_stats",
                "resource_utilization",
                "latency_metrics",
                "energy_metrics",
                "carbon_metrics",
                "cost_metrics",
                "scaling_metrics",
                "processed_metrics",
                "kubernetes_resources",
                "kubernetes_scaling",
            ]

            all_metrics = results.get("metrics", {})
            filtered_results = {k: v for k, v in all_metrics.items() if k in fields_to_keep}

            execution_time = time.time() - start_time

            output_path = None
            if output:
                try:
                    output_dir = os.path.dirname(os.path.abspath(output))
                    if output_dir:
                        os.makedirs(output_dir, exist_ok=True)
                    persistable = dict(filtered_results)
                    if analysis_results:
                        persistable["analysis_results"] = analysis_results
                    with open(output, "w", encoding="utf-8") as f:
                        json.dump(persistable, f, indent=2)
                    output_path = os.path.abspath(output)
                    logger.info("Results saved to: %s", output_path)
                except (IOError, OSError) as exc:
                    logger.error("Failed to save results to %s: %s", output, exc)
                    raise RuntimeError(f"Failed to save results: {exc}") from exc

            return {
                "success": True,
                "metrics": filtered_results,
                "analysis_results": analysis_results,
                "output_file": output_path,
                "execution_time": execution_time,
            }

        except Exception as exc:
            logger.error("Simulation failed: %s", str(exc), exc_info=True)
            if isinstance(exc, (RuntimeError, ValueError, FileNotFoundError)):
                raise
            raise RuntimeError(f"Simulation failed: {exc}") from exc

    except Exception as exc:
        logger.error("Failed to create framework: %s", str(exc), exc_info=True)
        raise RuntimeError(f"Failed to create framework: {exc}") from exc
    finally:
        if synthetic_cluster:
            synthetic_cluster.cleanup()


def _calculate_cost_metrics(terraform_path: str, duration_seconds: float) -> Dict[str, Any]:
    """Calculate cost metrics using pricing.csv when plan.json is available."""
    try:
        terraform_dir = Path(terraform_path)
        plan_path = terraform_dir / "plan.json"

        if not plan_path.exists():
            msg = f"plan.json not found at {plan_path}; skipping cost estimation"
            logger.info(msg)
            return {
                "total_monthly_cost": 0.0,
                "total_hourly_cost": 0.0,
                "total_simulation_cost": 0.0,
                "currency": "INR",
                "cost_by_service": {},
                "cost_by_resource_type": {},
                "cost_by_resource": {},
                "error": msg,
            }

        from ..cost.pricing_csv_estimator import estimate_plan_costs

        cost_data = estimate_plan_costs(
            plan_path=plan_path,
            duration_seconds=duration_seconds,
        )

        logger.info(
            "Cost estimation completed (pricing.csv): ₹%s for %ss simulation",
            f"{cost_data['total_simulation_cost']:.4f}",
            duration_seconds,
        )

        return {
            "total_monthly_cost": cost_data["total_monthly_cost"],
            "total_hourly_cost": cost_data["total_hourly_cost"],
            "total_simulation_cost": cost_data["total_simulation_cost"],
            "currency": cost_data.get("currency", "INR"),
            "cost_by_service": cost_data.get("cost_by_service", {}),
            "cost_by_resource_type": cost_data.get("cost_by_resource_type", {}),
            "cost_by_resource": cost_data.get("cost_by_resource", {}),
            "estimation_timestamp": cost_data.get("estimation_timestamp"),
        }

    except Exception as exc:
        logger.error("Error calculating cost metrics: %s", str(exc))
        return {
            "total_monthly_cost": 0.0,
            "total_hourly_cost": 0.0,
            "total_simulation_cost": 0.0,
            "currency": "INR",
            "cost_by_service": {},
            "cost_by_resource_type": {},
            "cost_by_resource": {},
            "error": f"Cost calculation error: {exc}",
        }


def _calculate_synthetic_cost_metrics(fallback_cfg: Dict[str, Any], duration_seconds: float, final_node_count: int = 0) -> Dict[str, Any]:
    """Estimate cost for synthetic GKE fallback nodes using pricing.csv."""
    from ..cost.pricing_csv_estimator import estimate_ir_costs

    def _coerce_int(value: Any, default: int) -> int:
        try:
            iv = int(value)
            return iv if iv > 0 else default
        except (TypeError, ValueError):
            return default

    region = str(fallback_cfg.get("region") or "us-central1")
    machine_type = str(fallback_cfg.get("machine_type") or "e2-standard-2")
    disk_size_gb = float(fallback_cfg.get("disk_size_gb") or 100)
    requested_default = _coerce_int(fallback_cfg.get("default_count"), 0)
    min_count = _coerce_int(fallback_cfg.get("min_count"), 1)
    initial_count = requested_default if requested_default > 0 else min_count
    max_override = _coerce_int(fallback_cfg.get("max_count"), 0)
    max_count = max(max_override, min_count) if max_override > 0 else max(initial_count + 4, min_count * 3)
    baseline_nodes = min(initial_count, max_count)
    node_count = final_node_count if final_node_count > 0 else baseline_nodes

    ir = {
        "resources": [
            {
                "type": "google_container_cluster",
                "id": "synthetic-cluster",
                "config": {"location": region},
            },
            {
                "type": "google_container_node_pool",
                "id": "synthetic-node-pool",
                "config": {
                    "location": region,
                    "node_config": [
                        {
                            "machine_type": machine_type,
                            "disk_size_gb": disk_size_gb,
                        }
                    ],
                    "initial_node_count": node_count,
                    "node_count": node_count,
                    "autoscaling": [
                        {
                            "min_node_count": min_count,
                            "max_node_count": max_count,
                        }
                    ],
                },
            },
        ]
    }

    cost_data = estimate_ir_costs(ir=ir, duration_seconds=duration_seconds)
    return {
        "total_monthly_cost": cost_data["total_monthly_cost"],
        "total_hourly_cost": cost_data["total_hourly_cost"],
        "total_simulation_cost": cost_data["total_simulation_cost"],
        "currency": cost_data.get("currency", "INR"),
        "cost_by_service": cost_data.get("cost_by_service", {}),
        "cost_by_resource_type": cost_data.get("cost_by_resource_type", {}),
        "cost_by_resource": cost_data.get("cost_by_resource", {}),
        "estimation_timestamp": cost_data.get("estimation_timestamp"),
    }


_NON_OPERATIONAL_K8S_RESOURCE_KINDS: set[str] = {
    "service",
    "serviceaccount",
    "configmap",
    "secret",
    "namespace",
}


def _is_k8s_resource(resource_id: str) -> bool:
    return "::" in resource_id


def _k8s_resource_kind(resource_id: str) -> str:
    if "::" not in resource_id:
        return ""
    return resource_id.split("::", 1)[0].lower()


def _filter_resource_emissions(
    emissions: Dict[str, float], exclude_kinds: set[str]
) -> Dict[str, float]:
    filtered: Dict[str, float] = {}
    for rid, value in emissions.items():
        kind = _k8s_resource_kind(rid)
        if kind and kind in exclude_kinds:
            continue
        filtered[rid] = value
    return filtered


def _transform_raw_simulation_result(
    raw_result: CanonicalSimulationResult,
    framework_resource_mapping: Optional[Dict[str, Any]] = None,
    kubernetes_resources: Optional[List[Dict[str, Any]]] = None,
    mode: Optional[str] = None,
    input_type: Optional[str] = None,
    terraform_path: Optional[str] = None,
    synthetic_infra: bool = False,
    k8s_fallback: Optional[Dict[str, Any]] = None,
    duration_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Transform RawSimulationResult from LEAFCloud framework to expected format."""
    import statistics

    from ..config import LEAFCloudConfig
    from ..models.carbon import CarbonModel

    analysis_results: Dict[str, Any] = {}
    raw_analysis = getattr(raw_result, "analysis_results", None)
    if isinstance(raw_analysis, dict):
        analysis_results = dict(raw_analysis)
    elif raw_analysis is not None:
        try:
            analysis_results = dict(raw_analysis)  # type: ignore[arg-type]
        except Exception:
            analysis_results = {}

    metrics: Dict[str, Any] = {
        "carbon_metrics": {},
        "energy_metrics": {},
        "cost_metrics": {},
        "latency_metrics": {},
        "scaling_metrics": {},
        "processed_metrics": {},
        "resource_utilization": {},
        "simulation_info": {},
        "workload_stats": {},
        "kubernetes_resources": kubernetes_resources or []
    }

    metrics["simulation_info"] = {
        "simulation_id": raw_result.metadata.simulation_id,
        "start_time": raw_result.metadata.start_time,
        "end_time": raw_result.metadata.end_time,
        "duration": raw_result.metadata.duration_seconds,
        "execution_time": raw_result.metadata.end_time - raw_result.metadata.start_time,
        "completed_tokens": 0,
        "total_tokens": 0
    }

    total_tokens = 0
    total_energy_kwh = 0.0
    total_carbon_kg = 0.0
    completed_from_events = 0
    completed_from_stats = 0

    try:
        token_events = (
            getattr(raw_result, "token_traces", [])
            or getattr(raw_result, "token_flow_logs", [])
            or getattr(raw_result, "token_flow_log", [])
        )
        for ev in token_events:
            if hasattr(ev, "event_type"):
                if getattr(ev, "event_type") == "token_completed":
                    completed_from_events += 1
            elif isinstance(ev, dict):
                evt = ev.get("event") or ev.get("event_type") or ev.get("eventType")
                if evt == "token_completed":
                    completed_from_events += 1
    except Exception:
        completed_from_events = 0

    try:
        raw_outputs = getattr(raw_result, "raw_model_outputs", {}) or {}
        token_stats = raw_outputs.get("token_stats", {})
        # Some converters flatten token metrics
        completed_from_stats = raw_outputs.get("tokens_completed", 0) or token_stats.get("total_completed", 0)
    except Exception:
        completed_from_stats = 0

    # Ensure resource_mapping exists even if no resource metrics are present
    resource_mapping: Dict[str, Dict[str, Any]] = {}

    if raw_result.resource_metrics:
        resource_utilization: Dict[str, Any] = {}
        latency_values: List[float] = []

        for resource_id, resource_metrics in raw_result.resource_metrics.items():
            util_series_raw = _get_metric_attr(resource_metrics, "utilization")
            util_series = _safe_time_series(util_series_raw)
            if util_series:
                values = [point["value"] for point in util_series]
                avg_utilization = statistics.mean(values)
                resource_utilization[resource_id] = {
                    "average": avg_utilization,
                    "max": max(values),
                    "min": min(values),
                    "series": util_series,
                }

            region = 'us-central1'

            if framework_resource_mapping and resource_id in framework_resource_mapping:
                framework_resource = framework_resource_mapping[resource_id]
                if hasattr(framework_resource, 'region'):
                    region = framework_resource.region
                    logger.info("Resource %s: using framework region=%s from resource.region", resource_id, region)
                elif isinstance(framework_resource, dict) and 'region' in framework_resource:
                    region = framework_resource['region']
                    logger.info("Resource %s: using framework region=%s from dict", resource_id, region)
                else:
                    logger.info(
                        "Resource %s: framework resource found but no region attribute: %s",
                        resource_id,
                        type(framework_resource)
                    )
                    if hasattr(framework_resource, '__dict__'):
                        logger.info(
                            "Resource %s: framework resource attributes: %s",
                            resource_id,
                            list(framework_resource.__dict__.keys())
                        )
            else:
                region = getattr(resource_metrics, 'region', region)
                logger.info(
                    "Resource %s: using metrics region=%s (framework mapping not available)",
                    resource_id,
                    region
                )

            resource_mapping[resource_id] = {"region": region}

            latency_series = _safe_time_series(_get_metric_attr(resource_metrics, "request_latency_ms"))
            if latency_series:
                latency_values.extend(point["value"] for point in latency_series)

            token_series = _safe_time_series(_get_metric_attr(resource_metrics, "tokens_processed"))
            if token_series:
                resource_tokens = sum(point["value"] for point in token_series)
                total_tokens += resource_tokens

        resource_energy: Dict[str, float] = {}
        try:
            from ..models.energy import EnergyModel

            energy_config = LEAFCloudConfig.EnergyModelConfig()
            energy_model = EnergyModel(energy_config)

            logger.info("=== ENERGY CALCULATION (Using resource-type-specific calculations) ===")

            for resource_id, resource_metrics in raw_result.resource_metrics.items():
                util_series = _safe_time_series(_get_metric_attr(resource_metrics, "utilization"))
                if not util_series:
                    logger.info("Resource %s: No utilization data available", resource_id)
                    continue

                avg_utilization = statistics.mean(point["value"] for point in util_series)

                resource_info = None
                resource_type = "generic"
                capacity = 1.0

                if framework_resource_mapping and resource_id in framework_resource_mapping:
                    resource_info = framework_resource_mapping[resource_id]
                    if hasattr(resource_info, 'specific_type'):
                        resource_type = resource_info.specific_type  # type: ignore[attr-defined]
                    elif isinstance(resource_info, dict):
                        resource_type = resource_info.get('specific_type', resource_type)

                    if hasattr(resource_info, 'capacity'):
                        capacity = getattr(resource_info, 'capacity', capacity)
                    elif isinstance(resource_info, dict):
                        capacity = resource_info.get('capacity', capacity)

                power_kw = energy_model.calculate_resource_energy(
                    resource_type=resource_type,
                    utilization=avg_utilization,
                    capacity=capacity
                )

                duration_seconds = raw_result.metadata.duration_seconds or 0
                resource_energy[resource_id] = power_kw * (duration_seconds / 3600.0)

            total_energy_kwh = sum(resource_energy.values())

            carbon_config = LEAFCloudConfig.CarbonModelConfig()
            carbon_model = CarbonModel(carbon_config)
            carbon_results = carbon_model.calculate(
                energy_data={"resource_energy": resource_energy},
                resource_mapping=resource_mapping,
            )
            total_carbon_kg = carbon_results.get("total_carbon_emissions", 0.0)

            resource_emissions = carbon_results.get("resource_emissions", {})
            filtered_emissions = _filter_resource_emissions(resource_emissions, _NON_OPERATIONAL_K8S_RESOURCE_KINDS)
            metrics["carbon_metrics"] = {
                "total_carbon_emissions": carbon_results.get("total_carbon_emissions", 0.0),
                "resource_emissions": filtered_emissions,
                "regional_emissions": carbon_results.get("regional_emissions", {}),
                "by_resource_category": carbon_results.get("by_resource_category", {}),
                "by_resource_type": carbon_results.get("by_resource_type", {}),
            }
            metrics["energy_metrics"] = {
                "resource_energy": resource_energy,
                "total_kwh": total_energy_kwh,
                "by_resource_category": carbon_results.get("by_resource_category", {}),
                "by_resource_type": carbon_results.get("by_resource_type", {}),
            }

            try:
                duration_seconds = float(raw_result.metadata.duration_seconds)
                scale = (3600.0 / duration_seconds) if duration_seconds > 0 else 0.0
                metrics["carbon_metrics"]["per_hour_kg_co2e"] = total_carbon_kg * scale
                metrics["energy_metrics"]["per_hour_kwh"] = total_energy_kwh * scale
            except Exception:
                metrics["carbon_metrics"]["per_hour_kg_co2e"] = 0.0
                metrics["energy_metrics"]["per_hour_kwh"] = 0.0

            logger.info("=== EMISSIONS SUMMARY (Using CarbonModel) ===")
            logger.info("Total energy: %.4f kWh", total_energy_kwh)
            logger.info("Total carbon: %.4f kg CO2e", total_carbon_kg)
            logger.info("Resource count: %d", len(filtered_emissions))

            if filtered_emissions:
                sorted_emissions = sorted(filtered_emissions.items(), key=lambda x: x[1], reverse=True)
                logger.info("Top emitters:")
                for idx, (resource_id, emissions) in enumerate(sorted_emissions[:5]):
                    energy = resource_energy.get(resource_id, 0)
                    region = resource_mapping.get(resource_id, {}).get("region", "unknown")
                    carbon_factor = carbon_model.get_carbon_factor(region)
                    logger.info(
                        "  %d. %s: %.6f kg CO2e (%.6f kWh, region=%s, factor=%s)",
                        idx + 1,
                        resource_id,
                        emissions,
                        energy,
                        region,
                        carbon_factor,
                    )
            logger.info("=== END SUMMARY ===")

        except Exception as exc:
            logger.error("Failed to calculate carbon emissions using CarbonModel: %s", str(exc))
            metrics["carbon_metrics"] = {
                "total_carbon_emissions": 0.0,
                "resource_emissions": {},
                "regional_emissions": {}
            }

        if latency_values:
            sorted_latencies = sorted(latency_values)
            metrics["latency_metrics"] = {
                "end_to_end": {
                    "average": statistics.mean(latency_values),
                    "min": min(latency_values),
                    "max": max(latency_values),
                    "p95": sorted_latencies[int(0.95 * len(sorted_latencies))] if sorted_latencies else 0,
                    "p99": sorted_latencies[int(0.99 * len(sorted_latencies))] if sorted_latencies else 0
                },
                "total_requests": len(latency_values),
                "by_resource": {},
                "by_path": {}
            }

        if 'resource_utilization' in locals() and resource_utilization:
            avg_utilization = statistics.mean([data["average"] for data in resource_utilization.values()])
            scaling_metrics = metrics.setdefault("scaling_metrics", {})
            scaling_metrics.update(
                {
                    "average_utilization": avg_utilization,
                    "max_pods": len(raw_result.resource_metrics),
                    "scaling_events": scaling_metrics.get("scaling_events", 0),
                }
            )

        if 'resource_utilization' in locals():
            metrics["resource_utilization"] = resource_utilization

        fallback_tokens = 0
        for candidate in (completed_from_events, completed_from_stats, total_tokens):
            if isinstance(candidate, (int, float)) and candidate > 0:
                fallback_tokens = candidate
                break
        metrics["simulation_info"]["completed_tokens"] = fallback_tokens
        metrics["simulation_info"]["total_tokens"] = fallback_tokens

    workload_config = raw_result.config_snapshot.get("workload", {})
    if workload_config and "params" in workload_config:
        workload_rate = workload_config["params"].get("rate", 0)
        metrics["workload_stats"] = {
            "primary_workload": {
                "avg_rate": workload_rate,
                "max_rate": workload_rate * 1.2,
                "total_tokens": total_tokens
            }
        }

    try:
        duration_seconds = float(raw_result.metadata.duration_seconds)
        scale = (3600.0 / duration_seconds) if duration_seconds > 0 else 0.0
    except Exception:
        duration_seconds = None
        scale = 0.0

    metrics["processed_metrics"] = {
        "aggregated": {
            "total_resources": len(raw_result.resource_metrics) if raw_result.resource_metrics else 0,
            "total_energy_kwh": total_energy_kwh,
            "total_carbon_kg": total_carbon_kg,
            "per_hour": {
                "energy_kwh": total_energy_kwh * scale,
                "carbon_kg": total_carbon_kg * scale
            }
        }
    }

    # Prefer orchestrator-provided aggregates (CLI uses these); fall back to recomputed totals.
    analysis_energy = (analysis_results or {}).get("energy", {}) if analysis_results else {}
    analysis_carbon = (analysis_results or {}).get("carbon", {}) if analysis_results else {}

    def _as_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    authoritative_energy = _as_float(analysis_energy.get("total_kwh"))
    authoritative_carbon = _as_float(analysis_carbon.get("total_carbon_emissions"))

    if authoritative_energy is not None:
        metrics["energy_metrics"]["total_kwh"] = authoritative_energy
        metrics["energy_metrics"]["per_hour_kwh"] = authoritative_energy * scale
        metrics["processed_metrics"]["aggregated"]["total_energy_kwh"] = authoritative_energy
        metrics["processed_metrics"]["aggregated"]["per_hour"]["energy_kwh"] = authoritative_energy * scale

    if authoritative_carbon is not None:
        metrics["carbon_metrics"]["total_carbon_emissions"] = authoritative_carbon
        metrics["carbon_metrics"]["per_hour_kg_co2e"] = authoritative_carbon * scale
        metrics["processed_metrics"]["aggregated"]["total_carbon_kg"] = authoritative_carbon
        metrics["processed_metrics"]["aggregated"]["per_hour"]["carbon_kg"] = authoritative_carbon * scale

    kube_scaling_metrics = _build_kubernetes_scaling_metrics(
        raw_result,
        resource_mapping,
        kubernetes_resources,
        requested_mode=mode,
        requested_input_type=input_type,
    )
    if kube_scaling_metrics:
        metrics["kubernetes_scaling"] = kube_scaling_metrics
        scaling_metrics = metrics.setdefault("scaling_metrics", {})
        scaling_metrics.setdefault("node_count_time_series", kube_scaling_metrics.get("node_count_time_series", {}))
        scaling_metrics.setdefault("autoscaling_events", kube_scaling_metrics.get("autoscaling_events", []))
        # Count autoscaling actions (scale up/down) for UI
        scaling_metrics["scaling_events"] = len(kube_scaling_metrics.get("autoscaling_events", []))

    # Cost metrics (after scaling so we can use final node counts for synthetic clusters)
    final_nodes = 0
    if kube_scaling_metrics:
        final_nodes = int(float(kube_scaling_metrics.get("summary", {}).get("final_node_count") or 0))
    duration_for_cost = duration_seconds if duration_seconds is not None else float(raw_result.metadata.duration_seconds or 0.0)
    if terraform_path and not synthetic_infra:
        logger.info("Calculating cost metrics using pricing.csv (plan.json)")
        cost_metrics = _calculate_cost_metrics(terraform_path, duration_for_cost)
    elif synthetic_infra and k8s_fallback:
        logger.info("Calculating cost metrics for synthetic fallback cluster using pricing.csv (final nodes=%s)", final_nodes)
        cost_metrics = _calculate_synthetic_cost_metrics(k8s_fallback, duration_for_cost, final_node_count=final_nodes)
    else:
        cost_metrics = {
            "total_monthly_cost": 0.0,
            "total_hourly_cost": 0.0,
            "total_simulation_cost": 0.0,
            "currency": "INR",
            "cost_by_service": {},
            "cost_by_resource_type": {},
            "cost_by_resource": {},
            "estimation_timestamp": datetime.utcnow().isoformat() + "Z",
            "error": "Cost metrics require Terraform input",
        }
    metrics["cost_metrics"] = cost_metrics

    return {
        "success": True,
        "metrics": metrics,
        "analysis_results": analysis_results,
        "output_file": None,
        "execution_time": raw_result.metadata.end_time - raw_result.metadata.start_time
    }
