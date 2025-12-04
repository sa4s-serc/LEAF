from pathlib import Path

from leaf_cloud.kubernetes.parser import KubernetesParser


def test_parser_loads_multi_document_manifest() -> None:
    manifest_path = Path("examples/gke-deployment/deployment.yaml")
    parser = KubernetesParser(manifest_path)

    resources = parser.parse(force=True)

    assert len(resources) >= 10

    email_deployment = next(
        res for res in resources if res.kind == "Deployment" and res.name == "emailservice"
    )
    email_service = next(
        res for res in resources if res.kind == "Service" and res.name == "emailservice"
    )

    assert email_deployment.resource_id == "deployment::default/emailservice"
    assert email_service.selectors == {"app": "emailservice"}
    assert email_service.source.path == manifest_path
    assert email_service.source.start_line > 0


def test_parser_reuses_cache_on_second_parse(tmp_path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        """
apiVersion: v1
kind: ConfigMap
metadata:
  name: sample-config
data:
  key: value
""",
        encoding="utf-8",
    )

    parser = KubernetesParser(manifest)
    first = parser.parse(force=True)
    second = parser.parse()

    assert first == second
    assert parser.unsupported_kinds.get("ConfigMap") is None
