from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from leaf_cloud.core.petri_net import PetriNet, Place, TokenColor, Token
from leaf_cloud.core.resource import Resource, ResourceType
from leaf_cloud.utils.power_utils import PowerProfile
from leaf_cloud.gcp.compute import ComputeResource
from leaf_cloud.gcp.generic_component import GCPServiceCategory, GenericGCPComponent
from leaf_cloud.terraform.model_builder import NetworkTopologyBuilder

from .parser import K8sResource

logger = logging.getLogger(__name__)


def _parse_cpu(value: Any) -> float:
    """Convert Kubernetes CPU notation to cores."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    if not text:
        return 0.0

    try:
        if text.endswith("m"):
            return float(text[:-1]) / 1000.0
        if text.endswith("k"):
            return float(text[:-1]) * 1000.0
        if text.endswith("g"):
            return float(text[:-1]) * 1000_000.0
        return float(text)
    except ValueError:
        logger.debug("Unable to parse CPU quantity '%s'", value)
        return 0.0


def _parse_memory_gb(value: Any) -> float:
    """Convert Kubernetes memory notation to gibibytes."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        # Assume bytes
        return float(value) / (1024 ** 3)

    text = str(value).strip()
    if not text:
        return 0.0

    units = {
        "ki": 1024,
        "mi": 1024 ** 2,
        "gi": 1024 ** 3,
        "ti": 1024 ** 4,
        "k": 1000,
        "m": 1000 ** 2,
        "g": 1000 ** 3,
        "t": 1000 ** 4,
    }
    try:
        for suffix, multiplier in units.items():
            if text.lower().endswith(suffix):
                numeric = float(text[: -len(suffix)])
                return (numeric * multiplier) / (1024 ** 3)
        return float(text) / (1024 ** 3)
    except ValueError:
        logger.debug("Unable to parse memory quantity '%s'", value)
        return 0.0


def _aggregate_container_resources(
    containers: Sequence[Dict[str, Any]]
) -> Tuple[float, float, float, float]:
    cpu_request_total = 0.0
    mem_request_total = 0.0
    cpu_limit_total = 0.0
    mem_limit_total = 0.0
    for container in containers:
        if not isinstance(container, dict):
            continue
        resources = container.get("resources") or {}
        if not isinstance(resources, dict):
            resources = {}
        requests = resources.get("requests") or {}
        if isinstance(requests, dict):
            cpu_request_total += _parse_cpu(requests.get("cpu"))
            mem_request_total += _parse_memory_gb(requests.get("memory"))
        limits = resources.get("limits") or {}
        if isinstance(limits, dict):
            cpu_limit_total += _parse_cpu(limits.get("cpu"))
            mem_limit_total += _parse_memory_gb(limits.get("memory"))
    return cpu_request_total, mem_request_total, cpu_limit_total, mem_limit_total


@dataclass
class KubernetesResourceBinding:
    resource: K8sResource
    model: Resource


class K8sWorkloadResource(ComputeResource):
    """Compute resource representing a Kubernetes workload."""

    def __init__(
        self,
        name: str,
        replicas: int,
        cpu_requests: float,
        memory_requests_gb: float,
        cpu_limits: float,
        memory_limits_gb: float,
        namespace: str,
        workload_kind: str,
        labels: Dict[str, str],
        attributes: Optional[Dict[str, Any]] = None,
        region: str = "k8s-cluster",
    ) -> None:
        self._zero_power_profile: Optional[PowerProfile] = None
        requested_cpu = max(0.0, cpu_requests)
        limit_cpu = max(requested_cpu, max(0.0, cpu_limits))

        replicas = max(1, replicas)
        guaranteed_capacity = requested_cpu * replicas
        if guaranteed_capacity <= 0.0:
            guaranteed_capacity = float(replicas)

        burst_capacity = limit_cpu * replicas if limit_cpu > 0.0 else guaranteed_capacity
        if burst_capacity <= 0.0:
            burst_capacity = float(replicas)

        vcpus_source = limit_cpu if limit_cpu > 0.0 else requested_cpu or 1.0
        vcpus = max(1, int(math.ceil(vcpus_source)))

        memory_request_total = max(0.0, memory_requests_gb)
        memory_limit_total = max(memory_request_total, max(0.0, memory_limits_gb))
        memory_total = max(memory_limit_total, 0.25)

        capacity = max(1e-3, float(burst_capacity))

        super().__init__(
            name=name,
            capacity=capacity,
            vcpus=vcpus,
            memory_gb=memory_total,
            region=region,
            machine_type="k8s-workload",
        )
        # Tag workloads as logical consumers so the energy model can special-case them.
        self.specific_type = "k8s-workload"
        self.replicas = replicas
        self.cpu_requests = requested_cpu
        self.cpu_limits = limit_cpu
        self.memory_requests_gb = memory_request_total
        self.memory_limits_gb = memory_limit_total
        self.guaranteed_capacity = max(1e-3, float(guaranteed_capacity))
        self.burst_capacity = capacity
        self.namespace = namespace
        self.workload_kind = workload_kind
        self.labels = dict(labels)
        self.attributes.update(attributes or {})
        self.attributes.setdefault("namespace", namespace)
        self.attributes.setdefault("kind", workload_kind)
        self.attributes.setdefault("replicas", self.replicas)
        self.attributes.setdefault("cpu_requests", self.cpu_requests)
        self.attributes.setdefault("memory_requests_gb", self.memory_requests_gb)
        self.attributes.setdefault("cpu_limits", self.cpu_limits)
        self.attributes.setdefault("memory_limits_gb", self.memory_limits_gb)
        self.attributes.setdefault("guaranteed_capacity", self.guaranteed_capacity)
        self.attributes.setdefault("burst_capacity", self.burst_capacity)
        self.attributes.setdefault("labels", dict(labels))
        self.attributes.setdefault("capacity_unit", "cpu")
    def _create_power_profile(self) -> PowerProfile:
        if self._zero_power_profile is None:
            self._zero_power_profile = PowerProfile(idle_power_kw=0.0, max_power_kw=0.0)
        return self._zero_power_profile

    @property
    def current_instances(self) -> int:
        """Return the current number of replicas."""
        return self.replicas


class K8sNetworkResource(GenericGCPComponent):
    """Network-layer abstraction for Kubernetes objects (Service, Ingress, Gateway)."""

    def __init__(
        self,
        name: str,
        namespace: str,
        kind: str,
        capacity: float = 1.0,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        category = GCPServiceCategory.NETWORK
        super().__init__(
            name=name,
            capacity=max(1.0, capacity),
            resource_type=ResourceType.NETWORK,
            region="k8s-cluster",
            service_category=category,
            service_name=f"k8s-{kind.lower()}",
            attributes=attributes or {},
        )
        self.attributes.setdefault("namespace", namespace)
        self.attributes.setdefault("kind", kind)


class KubernetesModelBuilder:
    """Builds LEAF-Cloud resources and Petri nets from Kubernetes manifests."""

    NON_OPERATIONAL_KINDS = {"ConfigMap", "Secret", "ServiceAccount", "Namespace"}

    def __init__(
        self,
        resources: Sequence[K8sResource],
        cluster_name: str = "k8s-cluster",
    ) -> None:
        self.resources = list(resources)
        self.cluster_name = cluster_name
        self.petri_net = PetriNet(name="KubernetesModel")
        self.resource_mapping: Dict[str, Resource] = {}
        self._workloads: Dict[str, KubernetesResourceBinding] = {}
        self._services: List[KubernetesResourceBinding] = []
        self._autoscalers: Dict[str, Dict[str, Any]] = {}
        self.service_targets: Dict[str, List[str]] = {}
        self._service_name_to_id: Dict[str, str] = {}
        self._pod_resource_samples: List[Tuple[float, float]] = []
        self.workload_autoscalers: Dict[str, Dict[str, Any]] = {}
        self._pod_resource_samples: List[Tuple[float, float]] = []
        self._workload_summary: Dict[str, float] = {}

    def build_model(self) -> PetriNet:
        self._ensure_global_places()
        self._map_resources()
        self._apply_autoscalers()
        self._link_services()

        topology_builder = NetworkTopologyBuilder(self.petri_net)
        topology_builder.build(list(self.resource_mapping.values()))
        logger.info(
            "Built Kubernetes Petri net with %d places and %d transitions",
            len(self.petri_net.places),
            len(self.petri_net.transitions),
        )
        return self.petri_net

    def summarize_workload_resources(self) -> Dict[str, float]:
        """Return aggregate CPU/memory requests across mapped workloads."""
        summary = {
            "workload_count": 0.0,
            "total_replicas": 0.0,
            "total_cpu_requests": 0.0,
            "total_cpu_limits": 0.0,
            "total_memory_requests_gb": 0.0,
            "total_memory_limits_gb": 0.0,
        }
        for binding in self._workloads.values():
            workload = binding.model
            replicas = max(1, getattr(workload, "replicas", 1))
            summary["workload_count"] += 1
            summary["total_replicas"] += replicas
            summary["total_cpu_requests"] += getattr(workload, "cpu_requests", 0.0) * replicas
            summary["total_cpu_limits"] += getattr(workload, "cpu_limits", 0.0) * replicas
            summary["total_memory_requests_gb"] += getattr(workload, "memory_requests_gb", 0.0) * replicas
            summary["total_memory_limits_gb"] += getattr(workload, "memory_limits_gb", 0.0) * replicas

        self._workload_summary = summary
        return summary

    def _ensure_global_places(self) -> None:
        if PetriNet.SOURCE_PLACE_ID not in self.petri_net.places:
            source_place = Place(
                id=PetriNet.SOURCE_PLACE_ID,
                name="Source",
                token_color=TokenColor.REQUEST,
            )
            self.petri_net.add_place(source_place)
        if PetriNet.SINK_PLACE_ID not in self.petri_net.places:
            sink_place = Place(
                id=PetriNet.SINK_PLACE_ID,
                name="Sink",
                token_color=TokenColor.REQUEST,
            )
            self.petri_net.add_place(sink_place)

    def _register_resource(self, k8s_res: K8sResource, model_res: Resource) -> None:
        self.resource_mapping[k8s_res.resource_id] = model_res
        self._ensure_resource_state_place(model_res)
        if isinstance(model_res, ComputeResource):
            self._workloads[k8s_res.resource_id] = KubernetesResourceBinding(k8s_res, model_res)
        elif isinstance(model_res, GenericGCPComponent):
            self._services.append(KubernetesResourceBinding(k8s_res, model_res))
            if k8s_res.kind == "Service":
                key = f"{k8s_res.namespace}/{k8s_res.name}"
                self._service_name_to_id[key] = k8s_res.resource_id

    def _ensure_resource_state_place(self, resource: Resource) -> None:
        state_place_id = f"ResourceState_{resource.name}"
        if state_place_id in self.petri_net.places:
            return

        capacity_limit = getattr(resource, "capacity", None)
        place_capacity: Optional[int] = None
        if isinstance(capacity_limit, (int, float)) and capacity_limit > 0:
            place_capacity = int(math.ceil(float(capacity_limit)))
        state_place = Place(
            id=state_place_id,
            name=f"State_{resource.name}",
            capacity=place_capacity,
        )
        self.petri_net.add_place(state_place)

        token = Token(
            color=TokenColor.RESOURCE,
            attributes={
                "resource_id": getattr(resource, "id", resource.name),
                "resource_name": resource.name,
            },
            token_id=f"resource_token_{resource.name}",
        )
        self.petri_net.add_token(state_place_id, token)

        place_obj = self.petri_net.places.get(state_place_id)

        if isinstance(resource, ComputeResource):
            desired_slots: Optional[int] = None
            try:
                replicas = getattr(resource, "replicas", None)
                concurrency = getattr(resource, "concurrency", None)
                if replicas is not None and concurrency is not None:
                    desired_slots = int(max(1, int(replicas)) * max(1, int(concurrency)))
            except Exception:
                desired_slots = None

            if desired_slots is None:
                try:
                    cap = getattr(resource, "capacity", None)
                    if cap is not None:
                        desired_slots = int(max(1, int(math.ceil(float(cap)))))
                except Exception:
                    desired_slots = None

            if desired_slots and desired_slots > 1:
                if place_obj is not None:
                    for _ in range(desired_slots - 1):
                        try:
                            place_obj.add_token(Token(color=TokenColor.RESOURCE))
                        except Exception:
                            break

        if place_obj is not None:
            capacity_attr = getattr(resource, "capacity", None)
            if isinstance(capacity_attr, (int, float)) and capacity_attr > 0:
                slot_capacity = float(capacity_attr)
            else:
                try:
                    slot_capacity = float(place_obj.token_count)
                except Exception:
                    slot_capacity = float(len(place_obj.tokens))
            try:
                setattr(resource, "_petri_slot_capacity", slot_capacity)
            except Exception:
                try:
                    resource.attributes.setdefault("petri_slot_capacity", slot_capacity)
                except Exception:
                    pass

    def _map_resources(self) -> None:
        for resource in self.resources:
            if resource.kind in self.NON_OPERATIONAL_KINDS:
                continue
            mapper = getattr(self, f"_map_{resource.kind.lower()}", None)
            if callable(mapper):
                model_res = mapper(resource)
                if model_res:
                    self._register_resource(resource, model_res)
                continue

            # Fallback for other compute-centric resources
            if resource.kind in {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}:
                model_res = self._map_workload(resource)
                if model_res:
                    self._register_resource(resource, model_res)
            elif resource.kind in {"Service", "Ingress", "Gateway"}:
                model_res = self._map_network(resource)
                if model_res:
                    self._register_resource(resource, model_res)

    def _map_deployment(self, resource: K8sResource) -> Optional[Resource]:
        return self._map_workload(resource)

    def _map_statefulset(self, resource: K8sResource) -> Optional[Resource]:
        return self._map_workload(resource)

    def _map_daemonset(self, resource: K8sResource) -> Optional[Resource]:
        return self._map_workload(resource)

    def _map_job(self, resource: K8sResource) -> Optional[Resource]:
        return self._map_workload(resource)

    def _map_cronjob(self, resource: K8sResource) -> Optional[Resource]:
        job_template = resource.spec.get("jobTemplate", {})
        if isinstance(job_template, dict):
            spec = job_template.get("spec", {})
            if isinstance(spec, dict):
                resource.spec = spec
        return self._map_workload(resource)

    def derive_average_pod_resources(
        self,
        default_cpu: float = 0.5,
        default_memory_gb: float = 1.0,
    ) -> Tuple[float, float]:
        """Estimate representative pod CPU (cores) and memory (GiB)."""
        if not self._pod_resource_samples:
            for resource in self.resources:
                template = resource.spec.get("template", {})
                pod_spec = template.get("spec", {}) if isinstance(template, dict) else {}
                containers = pod_spec.get("containers") if isinstance(pod_spec, dict) else None
                if not isinstance(containers, Sequence):
                    continue
                cpu_req, mem_req, _, _ = _aggregate_container_resources(containers)
                if cpu_req <= 0.0 and mem_req <= 0.0:
                    continue
                self._pod_resource_samples.append(
                    (max(cpu_req, 0.0), max(mem_req, 0.0))
                )

        if not self._pod_resource_samples:
            return default_cpu, default_memory_gb

        cpu_total = 0.0
        mem_total = 0.0
        for cpu_req, mem_req in self._pod_resource_samples:
            cpu_total += cpu_req if cpu_req > 0.0 else default_cpu
            mem_total += mem_req if mem_req > 0.0 else default_memory_gb

        sample_count = len(self._pod_resource_samples)
        avg_cpu = cpu_total / sample_count
        avg_mem = mem_total / sample_count
        return (
            avg_cpu if avg_cpu > 0.0 else default_cpu,
            avg_mem if avg_mem > 0.0 else default_memory_gb,
        )

    def _map_workload(self, resource: K8sResource) -> Optional[Resource]:
        template = resource.spec.get("template", {})
        pod_spec = template.get("spec", {}) if isinstance(template, dict) else {}
        containers = pod_spec.get("containers") if isinstance(pod_spec, dict) else None
        if not isinstance(containers, Sequence):
            logger.debug("Workload %s has no containers; skipping", resource.resource_id)
            return None

        (
            cpu_request_total,
            mem_request_total,
            cpu_limit_total,
            mem_limit_total,
        ) = _aggregate_container_resources(containers)
        replicas = self._resolve_replicas(resource)

        attrs = {
            "k8s": resource.to_dict(),
            "selectors": dict(resource.selectors),
        }
        if cpu_request_total > 0.0 or mem_request_total > 0.0:
            self._pod_resource_samples.append(
                (max(cpu_request_total, 0.0), max(mem_request_total, 0.0))
            )
        workload = K8sWorkloadResource(
            name=f"k8s.{resource.namespace}.{resource.name}",
            replicas=replicas,
            cpu_requests=cpu_request_total,
            memory_requests_gb=mem_request_total,
            cpu_limits=cpu_limit_total,
            memory_limits_gb=mem_limit_total,
            namespace=resource.namespace,
            workload_kind=resource.kind,
            labels=resource.labels,
            attributes=attrs,
            region=self.cluster_name,
        )
        return workload

    def _map_service(self, resource: K8sResource) -> Optional[Resource]:
        return self._map_network(resource)

    def _map_ingress(self, resource: K8sResource) -> Optional[Resource]:
        return self._map_network(resource, kind_override="Ingress")

    def _map_gateway(self, resource: K8sResource) -> Optional[Resource]:
        return self._map_network(resource, kind_override="Gateway")

    def _map_horizontalpodautoscaler(self, resource: K8sResource) -> Optional[Resource]:
        spec = resource.spec
        target = spec.get("scaleTargetRef", {}) if isinstance(spec, dict) else {}
        if not isinstance(target, dict):
            return None

        target_kind = str(target.get("kind") or "").strip()
        target_name = str(target.get("name") or "").strip()
        if not target_kind or not target_name:
            return None

        key = self._workload_key(target_kind, resource.namespace, target_name)
        self._autoscalers[key] = {
            "min_replicas": spec.get("minReplicas"),
            "max_replicas": spec.get("maxReplicas"),
            "metrics": spec.get("metrics"),
            "behavior": spec.get("behavior"),
        }

        autoscaler = GenericGCPComponent(
            name=f"k8s.{resource.namespace}.{resource.name}",
            resource_type=ResourceType.GENERIC,
            service_category=GCPServiceCategory.COMPUTE,
            service_name="k8s-hpa",
            attributes={
                "k8s": resource.to_dict(),
                "target": key,
            },
        )
        return autoscaler

    def _map_network(self, resource: K8sResource, kind_override: Optional[str] = None) -> Optional[Resource]:
        kind = kind_override or resource.kind
        attrs = {
            "k8s": resource.to_dict(),
            "selectors": dict(resource.selectors),
        }
        network_name = f"k8s.service.{resource.namespace}.{resource.name}"
        return K8sNetworkResource(
            name=network_name,
            namespace=resource.namespace,
            kind=kind,
            capacity=1.0,
            attributes=attrs,
        )

    def _resolve_replicas(self, resource: K8sResource) -> int:
        spec = resource.spec
        replicas = 1
        if resource.kind in {"Deployment", "StatefulSet"}:
            replicas = spec.get("replicas", replicas)
        elif resource.kind == "Job":
            replicas = spec.get("parallelism") or spec.get("completions") or replicas
        elif resource.kind == "CronJob":
            replicas = spec.get("concurrencyPolicy") or replicas
        elif resource.kind == "DaemonSet":
            # DaemonSets run one pod per node; use autoscaler hints later if available
            replicas = spec.get("updateStrategy", {}).get("rollingUpdate", {}).get("maxUnavailable", 1)
        try:
            return max(1, int(replicas))
        except (TypeError, ValueError):
            return 1

    def _apply_autoscalers(self) -> None:
        for target, config in self._autoscalers.items():
            workload_binding = self._workloads.get(target)
            if not workload_binding:
                continue
            workload = workload_binding.model
            autoscaler = config
            workload.attributes.setdefault("autoscaler", autoscaler)

            min_replicas = autoscaler.get("min_replicas")
            max_replicas = autoscaler.get("max_replicas")
            if isinstance(min_replicas, int):
                workload.attributes["min_replicas"] = min_replicas
            if isinstance(max_replicas, int):
                workload.attributes["max_replicas"] = max_replicas
                # Adjust capacity upper bound to reflect autoscaler
                try:
                    per_replica_capacity = workload.capacity / max(1, workload.attributes.get("replicas", 1))
                    workload.capacity = max(workload.capacity, per_replica_capacity * max_replicas)
                except Exception:
                    pass
            metrics = autoscaler.get("metrics") or []
            target_cpu_util = None
            if isinstance(metrics, list):
                for metric in metrics:
                    if not isinstance(metric, dict):
                        continue
                    if str(metric.get("type", "")).lower() != "resource":
                        continue
                    resource_metric = metric.get("resource") or {}
                    if str(resource_metric.get("name", "")).lower() != "cpu":
                        continue
                    target_spec = resource_metric.get("target") or {}
                    if str(target_spec.get("type", "")).lower() == "utilization":
                        target_cpu_util = target_spec.get("averageUtilization")
                        break
            if target_cpu_util is not None:
                workload.attributes["target_cpu_utilization"] = target_cpu_util
            current_replicas = workload.attributes.get("replicas")
            try:
                current_replicas = int(current_replicas)
            except (TypeError, ValueError):
                current_replicas = getattr(workload, "replicas", 1)
            try:
                current_replicas = max(1, int(current_replicas))
            except Exception:
                current_replicas = 1
            info = {
                "min_replicas": int(min_replicas) if isinstance(min_replicas, int) else None,
                "max_replicas": int(max_replicas) if isinstance(max_replicas, int) else None,
                "target_cpu_utilization": target_cpu_util,
                "current_replicas": current_replicas,
            }
            self.workload_autoscalers[target] = info

    def _link_services(self) -> None:
        for binding in self._services:
            resource = binding.resource
            model = binding.model
            if resource.kind == "Service":
                targets = self._match_workloads(resource.selectors)
                model.attributes["targets"] = [w.model.name for w in targets]
                target_ids = [w.resource.resource_id for w in targets]
                if target_ids:
                    logger.debug(
                        "Service %s routes to workloads %s",
                        resource.resource_id,
                        target_ids,
                    )
                    unique_ids = list(dict.fromkeys(target_ids))
                    model.attributes["target_ids"] = unique_ids
                    self.service_targets[resource.resource_id] = unique_ids
                    total_capacity = sum(
                        max(0.0, float(getattr(w.model, "capacity", 0.0) or 0.0))
                        for w in targets
                    )
                    if total_capacity > 0:
                        try:
                            model.capacity = max(
                                float(getattr(model, "capacity", 0.0) or 0.0),
                                total_capacity,
                            )
                        except Exception:
                            pass
                        model.attributes["aggregate_target_capacity"] = total_capacity
            elif resource.kind in {"Ingress", "Gateway"}:
                services = self._discover_ingress_backends(resource)
                model.attributes["targets"] = services
                target_ids: List[str] = []
                for svc_name in services:
                    svc_key = f"{resource.namespace}/{svc_name}"
                    svc_id = self._service_name_to_id.get(svc_key)
                    if not svc_id:
                        continue
                    target_ids.extend(self.service_targets.get(svc_id, []))
                if target_ids:
                    logger.debug(
                        "%s %s routes to service workloads %s",
                        resource.kind,
                        resource.resource_id,
                        target_ids,
                    )
                    unique_ids = list(dict.fromkeys(target_ids))
                    model.attributes["target_ids"] = unique_ids
                    self.service_targets[resource.resource_id] = unique_ids
                    total_capacity = 0.0
                    for target_id in unique_ids:
                        target_resource = self.resource_mapping.get(target_id)
                        if target_resource is None:
                            continue
                        try:
                            total_capacity += max(
                                0.0, float(getattr(target_resource, "capacity", 0.0) or 0.0)
                            )
                        except Exception:
                            continue
                    if total_capacity > 0:
                        try:
                            model.capacity = max(
                                float(getattr(model, "capacity", 0.0) or 0.0),
                                total_capacity,
                            )
                        except Exception:
                            pass
                        model.attributes["aggregate_target_capacity"] = total_capacity

    def _match_workloads(self, selector: Dict[str, str]) -> List[KubernetesResourceBinding]:
        if not selector:
            return []
        matched: List[KubernetesResourceBinding] = []
        for binding in self._workloads.values():
            labels = binding.resource.labels
            if all(labels.get(k) == v for k, v in selector.items()):
                matched.append(binding)
        return matched

    def _discover_ingress_backends(self, resource: K8sResource) -> List[str]:
        services: List[str] = []
        spec = resource.spec
        rules = spec.get("rules") if isinstance(spec, dict) else None
        if not isinstance(rules, Sequence):
            return services
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            http = rule.get("http")
            if not isinstance(http, dict):
                continue
            paths = http.get("paths")
            if not isinstance(paths, Sequence):
                continue
            for path in paths:
                if not isinstance(path, dict):
                    continue
                backend = path.get("backend") or {}
                if not isinstance(backend, dict):
                    continue
                svc = backend.get("service") or {}
                if isinstance(svc, dict):
                    name = svc.get("name")
                    if isinstance(name, str):
                        services.append(name)
        return services

    def _workload_key(self, kind: str, namespace: str, name: str) -> str:
        ns = namespace or "default"
        return f"{kind.lower()}::{ns}/{name}"

    def register_cluster_nodes(
        self,
        cluster_rid: str,
        cluster_obj: Any,
    ) -> Tuple[List[str], List[str]]:
        """Register new cluster nodes and deregister removed ones.

        Args:
            cluster_rid: Resource ID of the cluster.
            cluster_obj: The GKECluster object.

        Returns:
            Tuple of (registered_node_ids, removed_node_ids).
        """
        registered_ids = []
        removed_ids = []
        
        # Identify current node IDs from the cluster object
        current_nodes = getattr(cluster_obj, "nodes", [])
        current_node_ids = {node.id for node in current_nodes}
        
        # 1. Register new nodes
        for node in current_nodes:
            if node.id not in self.resource_mapping:
                self.resource_mapping[node.id] = node
                registered_ids.append(node.id)
                
        # 2. Remove stale nodes associated with this cluster
        # Iterate over a copy of keys to allow modification
        cluster_ref = self.resource_mapping.get(cluster_rid)
        cluster_name = getattr(cluster_ref, "name", "") if cluster_ref else ""
        
        for rid, resource in list(self.resource_mapping.items()):
            # Check if it's a GKE Node
            # We check specific_type or class name to be safe
            is_node = getattr(resource, "specific_type", "") == "gke-node" or type(resource).__name__ == "GKENode"
            if not is_node:
                continue
                
            # Check if it belongs to this cluster
            # We can check node.cluster if available, or name prefix
            cluster = getattr(resource, "cluster", None)
            is_my_node = False
            
            if cluster and getattr(cluster, "id", "") == cluster_rid:
                is_my_node = True
            elif cluster_name and resource.name.startswith(f"{cluster_name}-node-"):
                is_my_node = True
                
            if is_my_node and rid not in current_node_ids:
                del self.resource_mapping[rid]
                removed_ids.append(rid)

        if registered_ids or removed_ids:
            logger.debug(
                "Autoscaling registered %d new GKE node(s) and removed %d node(s) for cluster %s",
                len(registered_ids),
                len(removed_ids),
                cluster_rid,
            )
            
        return registered_ids, removed_ids
