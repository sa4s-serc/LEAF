from pathlib import Path

import pytest

from leaf_cloud.kubernetes.model_builder import KubernetesModelBuilder
from leaf_cloud.kubernetes.parser import KubernetesParser


def test_model_builder_creates_resources_and_petri_net() -> None:
    manifest_path = Path("examples/gke-deployment/deployment.yaml")
    parser = KubernetesParser(manifest_path)
    resources = parser.parse(force=True)

    builder = KubernetesModelBuilder(resources)
    petri_net = builder.build_model()

    assert builder.resource_mapping, "Expected Kubernetes resources to be mapped"
    deployment_key = "deployment::default/emailservice"
    assert deployment_key in builder.resource_mapping

    resource = builder.resource_mapping[deployment_key]
    assert resource.name.startswith("k8s.default.emailservice")
    assert petri_net.transitions, "Petri net should contain transitions for workloads"
    assert any("emailservice" in transition_id for transition_id in petri_net.transitions)

    service_binding = next(
        (res for res in builder.resource_mapping.values() if getattr(res, "service_name", "") == "k8s-service"),
        None,
    )
    assert service_binding is not None
    assert service_binding.attributes.get("targets"), "Service should include matched workloads"


def test_autoscaler_adjusts_workload_capacity(tmp_path) -> None:
    manifest = tmp_path / "autoscaled.yaml"
    manifest.write_text(
        """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  namespace: prod
spec:
  replicas: 2
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: app
          image: example.com/web:latest
          resources:
            requests:
              cpu: 250m
              memory: 256Mi
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: web-hpa
  namespace: prod
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: web
  minReplicas: 2
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 75
""",
        encoding="utf-8",
    )

    parser = KubernetesParser(manifest)
    resources = parser.parse(force=True)
    builder = KubernetesModelBuilder(resources)
    builder.build_model()

    key = "deployment::prod/web"
    workload = builder.resource_mapping[key]

    assert workload.attributes.get("autoscaler")
    assert pytest.approx(workload.capacity, rel=0.01) >= 2.5
    assert workload.attributes.get("max_replicas") == 5
