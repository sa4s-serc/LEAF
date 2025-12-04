import math
from pathlib import Path

import pytest

from leaf_cloud.kubernetes.model_builder import KubernetesModelBuilder
from leaf_cloud.kubernetes.parser import K8sDocumentSource, K8sResource


def _deployment_resource(
    name: str,
    cpu_request: str,
    memory_request: str,
    cpu_limit: str | None,
    memory_limit: str | None,
    replicas: int = 1,
) -> K8sResource:
    resources_block: dict[str, dict[str, str]] = {"requests": {"cpu": cpu_request, "memory": memory_request}}
    if cpu_limit is not None or memory_limit is not None:
        limits: dict[str, str] = {}
        if cpu_limit is not None:
            limits["cpu"] = cpu_limit
        if memory_limit is not None:
            limits["memory"] = memory_limit
        resources_block["limits"] = limits

    spec = {
        "replicas": replicas,
        "selector": {"matchLabels": {"app": name}},
        "template": {
            "metadata": {"labels": {"app": name}},
            "spec": {
                "containers": [
                    {
                        "name": f"{name}-container",
                        "resources": resources_block,
                    }
                ]
            },
        },
    }

    metadata = {"name": name, "labels": {"app": name}}
    return K8sResource(
        api_version="apps/v1",
        kind="Deployment",
        metadata=metadata,
        spec=spec,
        source=K8sDocumentSource(path=Path("test.yaml"), index=0, start_line=1),
        raw={"metadata": metadata, "spec": spec},
    )


def test_workload_capacity_uses_cpu_limits_when_available():
    resource = _deployment_resource(
        name="limit-heavy",
        cpu_request="100m",
        memory_request="128Mi",
        cpu_limit="600m",
        memory_limit="512Mi",
    )

    builder = KubernetesModelBuilder([resource], cluster_name="unit-test")
    builder.build_model()

    workload = builder.resource_mapping[resource.resource_id]

    assert pytest.approx(workload.cpu_requests, rel=1e-6) == 0.1
    assert pytest.approx(workload.cpu_limits, rel=1e-6) == 0.6
    assert pytest.approx(workload.guaranteed_capacity, rel=1e-6) == 0.1
    assert pytest.approx(workload.capacity, rel=1e-6) == 0.6
    assert math.isclose(workload.memory_limits_gb, 0.5, rel_tol=1e-6)
    assert workload.attributes["burst_capacity"] == workload.capacity


def test_workload_capacity_defaults_to_requests_when_limits_missing():
    resource = _deployment_resource(
        name="request-only",
        cpu_request="250m",
        memory_request="256Mi",
        cpu_limit=None,
        memory_limit=None,
        replicas=2,
    )

    builder = KubernetesModelBuilder([resource], cluster_name="unit-test")
    builder.build_model()

    workload = builder.resource_mapping[resource.resource_id]

    assert pytest.approx(workload.cpu_requests, rel=1e-6) == 0.25
    assert pytest.approx(workload.cpu_limits, rel=1e-6) == 0.25
    assert pytest.approx(workload.guaranteed_capacity, rel=1e-6) == 0.5
    assert pytest.approx(workload.capacity, rel=1e-6) == 0.5
    assert math.isclose(workload.memory_limits_gb, 0.25, rel_tol=1e-6)
    # Ensure Control plane still keeps integer slot count when limits < 1 vCPU total
    assert workload.vcpus == 1
