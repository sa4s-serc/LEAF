from pathlib import Path

from leaf_cloud.leaf import LEAFCloud


def test_leafcloud_builds_kubernetes_model_without_terraform() -> None:
    manifest_path = Path("examples/gke-deployment/deployment.yaml")
    leaf = LEAFCloud()

    leaf.load_kubernetes(manifest_path)
    model = leaf.build_model()

    assert leaf.resource_mapping, "Expected resource mapping populated from Kubernetes manifests"
    assert any(entry.get("source_type") == "kubernetes" for entry in model["resources"])
    assert leaf.petri_net is not None
    assert len(leaf.petri_net.transitions) > 0

    builder = leaf._create_resource_mapping()
    assert builder.resource_mapping
    assert getattr(builder, "petri_net") is leaf.petri_net
