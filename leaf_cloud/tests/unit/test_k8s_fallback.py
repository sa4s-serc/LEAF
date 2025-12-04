import types

from leaf_cloud.leaf import LEAFCloud
from leaf_cloud.kubernetes.model_builder import K8sWorkloadResource
from leaf_cloud.gcp.compute import GKECluster


def test_apply_kubernetes_node_fallback_injects_synthetic_nodes():
    framework = LEAFCloud()
    workload = K8sWorkloadResource(
        name="k8s.default.test",
        replicas=2,
        cpu_requests=0.5,
        memory_requests_gb=0.5,
        cpu_limits=1.0,
        memory_limits_gb=1.0,
        namespace="default",
        workload_kind="Deployment",
        labels={},
    )
    workload_id = "deployment::default/test"
    framework.resource_mapping = {workload_id: workload}
    framework._kube_workload_summary = {
        "workload_count": 1,
        "total_replicas": 2,
        "total_cpu_requests": 1.0,
        "total_cpu_limits": 2.0,
        "total_memory_requests_gb": 1.0,
        "total_memory_limits_gb": 2.0,
    }
    framework._kube_fallback_config = {
        "enabled": True,
        "machine_type": "e2-standard-2",
        "region": "us-central1",
        "default_count": 0,
        "min_count": 1,
        "cpu_headroom": 0.75,
        "memory_headroom": 0.8,
    }

    metrics = {
        workload_id: {
            "resource_type": "compute",
            "region": "k8s-cluster",
            "utilization": [
                {"timestamp": 0.0, "value": 0.5},
                {"timestamp": 1.0, "value": 0.6},
            ],
        }
    }
    result = types.SimpleNamespace(resource_metrics=metrics)

    framework.apply_kubernetes_node_fallback(result)

    synthetic_nodes = [rid for rid in framework.resource_mapping if rid.endswith("node[0]")]
    assert synthetic_nodes, "Expected at least one synthetic node in the resource mapping"
    node_id = synthetic_nodes[0]
    assert node_id in result.resource_metrics
    node_metrics = result.resource_metrics[node_id]
    assert node_metrics["resource_type"] == "compute"
    assert len(node_metrics["utilization"]) == 2


def test_k8s_workload_and_cluster_report_zero_power():
    workload = K8sWorkloadResource(
        name="k8s.default.zero",
        replicas=1,
        cpu_requests=0.5,
        memory_requests_gb=0.5,
        cpu_limits=1.0,
        memory_limits_gb=1.0,
        namespace="default",
        workload_kind="Deployment",
        labels={},
    )
    assert workload.get_power_consumption(0.0) == 0.0

    cluster = GKECluster(name="test-cluster", region="us-central1")
    assert cluster.get_power_consumption(0.0) == 0.0
