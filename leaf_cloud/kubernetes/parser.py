from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import yaml

logger = logging.getLogger(__name__)

_LINE_KEY = "__leaf_line__"


class _LinePreservingLoader(yaml.SafeLoader):
    """YAML loader that records the start line for each mapping node."""


def _construct_mapping(loader: _LinePreservingLoader, node: yaml.Node, deep: bool = False) -> Dict[str, Any]:
    mapping = yaml.SafeLoader.construct_mapping(loader, node, deep=deep)
    mapping[_LINE_KEY] = node.start_mark.line + 1  # type: ignore[attr-defined]
    return mapping


_LinePreservingLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,  # type: ignore[arg-type]
)


@dataclass(frozen=True)
class K8sDocumentSource:
    """Metadata about where a Kubernetes manifest document originated."""

    path: Path
    index: int
    start_line: int = 1

    def identifier(self) -> str:
        return f"{self.path}:{self.start_line}"


@dataclass(frozen=True)
class K8sOwnerReference:
    """Represents a Kubernetes owner reference relationship."""

    api_version: str
    kind: str
    name: str
    uid: Optional[str] = None

    def ref(self) -> str:
        namespace_part = ""  # Namespace not provided in owner refs
        return f"{self.kind}/{namespace_part}{self.name}"


@dataclass
class K8sResource:
    """Normalized representation of a Kubernetes manifest."""

    api_version: str
    kind: str
    metadata: Dict[str, Any]
    spec: Dict[str, Any]
    source: K8sDocumentSource
    raw: Dict[str, Any]
    name: str = field(init=False)
    namespace: str = field(init=False)
    labels: Dict[str, str] = field(default_factory=dict)
    annotations: Dict[str, str] = field(default_factory=dict)
    selectors: Dict[str, str] = field(default_factory=dict)
    owner_references: List[K8sOwnerReference] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = str(self.metadata.get("name", "")).strip()
        self.namespace = str(self.metadata.get("namespace") or "default").strip()
        self.labels = self._extract_labels(self.metadata, self.spec)
        self.annotations = self._extract_annotations(self.metadata)
        self.selectors = self._extract_selectors(self.spec)
        self.owner_references = self._extract_owner_refs(self.metadata)
        self.depends_on = [
            f"{ref.kind}/{ref.name}" for ref in self.owner_references if ref.name
        ]

    @property
    def resource_id(self) -> str:
        namespace = self.namespace or "default"
        return f"{self.kind.lower()}::{namespace}/{self.name}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.resource_id,
            "kind": self.kind,
            "name": self.name,
            "namespace": self.namespace,
            "labels": dict(self.labels),
            "annotations": dict(self.annotations),
            "selectors": dict(self.selectors),
            "depends_on": list(self.depends_on),
            "source": {
                "path": str(self.source.path),
                "index": self.source.index,
                "line": self.source.start_line,
            },
        }

    def _extract_labels(self, metadata: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, str]:
        labels: Dict[str, str] = {}
        for candidate in (
            metadata.get("labels"),
            spec.get("selector", {}).get("matchLabels") if isinstance(spec, dict) else None,
            spec.get("template", {}).get("metadata", {}).get("labels") if isinstance(spec, dict) else None,
        ):
            if isinstance(candidate, dict):
                labels.update({str(k): str(v) for k, v in candidate.items()})
        return labels

    def _extract_annotations(self, metadata: Dict[str, Any]) -> Dict[str, str]:
        annotations = metadata.get("annotations", {}) if isinstance(metadata, dict) else {}
        if not isinstance(annotations, dict):
            return {}
        return {str(k): str(v) for k, v in annotations.items()}

    def _extract_selectors(self, spec: Dict[str, Any]) -> Dict[str, str]:
        selectors: Dict[str, str] = {}
        if not isinstance(spec, dict):
            return selectors

        selector = spec.get("selector")
        if isinstance(selector, dict):
            if "matchLabels" in selector and isinstance(selector["matchLabels"], dict):
                selectors.update({str(k): str(v) for k, v in selector["matchLabels"].items()})
            else:
                selectors.update({str(k): str(v) for k, v in selector.items() if isinstance(v, (str, int, float))})
        return selectors

    def _extract_owner_refs(self, metadata: Dict[str, Any]) -> List[K8sOwnerReference]:
        owner_refs: List[K8sOwnerReference] = []
        refs = metadata.get("ownerReferences") if isinstance(metadata, dict) else None
        if not isinstance(refs, Sequence):
            return owner_refs

        for ref in refs:
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or "").strip()
            name = str(ref.get("name") or "").strip()
            api_version = str(ref.get("apiVersion") or "v1").strip()
            uid = ref.get("uid")
            if kind and name:
                owner_refs.append(K8sOwnerReference(api_version=api_version, kind=kind, name=name, uid=uid))
        return owner_refs


class KubernetesParser:
    """Parser for Kubernetes manifest files."""

    SUPPORTED_KINDS: Tuple[str, ...] = (
        "Deployment",
        "StatefulSet",
        "DaemonSet",
        "Service",
        "Ingress",
        "Gateway",
        "ServiceAccount",
        "ConfigMap",
        "Secret",
        "Job",
        "CronJob",
        "HorizontalPodAutoscaler",
        "Pod",
        "Namespace",
        "PersistentVolumeClaim",
    )

    def __init__(self, manifest_path: Path | str, kubeconfig: Optional[Path | str] = None) -> None:
        self.manifest_path = Path(manifest_path).resolve()
        self.kubeconfig = Path(kubeconfig).resolve() if kubeconfig else None
        self._resources: List[K8sResource] = []
        self._parsed = False
        self._unsupported: Dict[str, int] = {}

    @property
    def unsupported_kinds(self) -> Dict[str, int]:
        """Kinds observed that are not currently supported."""
        return dict(self._unsupported)

    def parse(self, force: bool = False) -> List[K8sResource]:
        if self._parsed and not force:
            return list(self._resources)

        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Kubernetes manifest path not found: {self.manifest_path}")

        self._resources.clear()
        self._unsupported.clear()

        manifest_files = self._collect_manifest_files(self.manifest_path)
        for path in manifest_files:
            self._parse_file(path)

        self._parsed = True
        logger.info("Parsed %d Kubernetes manifests from %s", len(self._resources), self.manifest_path)
        if self._unsupported:
            unsupported_summary = ", ".join(f"{k} ({v})" for k, v in sorted(self._unsupported.items()))
            logger.warning("Unsupported Kubernetes kinds encountered: %s", unsupported_summary)
        return list(self._resources)

    def iter_resources(self) -> Iterator[K8sResource]:
        if not self._parsed:
            self.parse()
        yield from self._resources

    def _collect_manifest_files(self, manifest_path: Path) -> List[Path]:
        if manifest_path.is_file():
            return [manifest_path]

        yaml_extensions = {".yaml", ".yml", ".json"}
        files: List[Path] = []
        for root, _, filenames in os.walk(manifest_path):
            for fname in filenames:
                if Path(fname).suffix.lower() in yaml_extensions:
                    files.append(Path(root, fname))

        if not files:
            logger.warning("No Kubernetes manifests found under %s", manifest_path)
        return sorted(files)

    def _parse_file(self, path: Path) -> None:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="utf-8", errors="ignore")

        documents = list(yaml.load_all(content, Loader=_LinePreservingLoader))

        for index, document in enumerate(documents):
            if document is None:
                continue
            if not isinstance(document, dict):
                logger.debug("Skipping non-dict manifest in %s (doc %d)", path, index)
                continue

            start_line = self._strip_internal_keys(document)
            resource = self._normalize_document(document, path, index, start_line)
            if resource:
                self._resources.append(resource)

    def _strip_internal_keys(self, node: Any) -> int:
        start_line = 1

        def _recurse(value: Any) -> None:
            nonlocal start_line
            if isinstance(value, dict):
                line = value.pop(_LINE_KEY, None)
                if isinstance(line, int) and line > 0 and start_line == 1:
                    start_line = line
                for nested in value.values():
                    _recurse(nested)
            elif isinstance(value, list):
                for item in value:
                    _recurse(item)

        _recurse(node)
        return start_line

    def _normalize_document(
        self,
        document: Dict[str, Any],
        path: Path,
        index: int,
        start_line: int,
    ) -> Optional[K8sResource]:
        api_version = str(document.get("apiVersion") or "").strip()
        kind = str(document.get("kind") or "").strip()
        metadata = document.get("metadata") or {}
        spec = document.get("spec") or {}

        if not api_version or not kind:
            logger.debug("Skipping manifest without apiVersion/kind in %s (doc %d)", path, index)
            return None

        if not isinstance(metadata, dict):
            metadata = {}
        if not isinstance(spec, dict):
            spec = {}

        if not metadata.get("name"):
            logger.debug("Skipping %s manifest without metadata.name in %s (doc %d)", kind, path, index)
            return None

        source = K8sDocumentSource(path=path, index=index, start_line=start_line)
        resource = K8sResource(
            api_version=api_version,
            kind=kind,
            metadata=metadata,
            spec=spec,
            source=source,
            raw=document,
        )

        if kind not in self.SUPPORTED_KINDS:
            self._unsupported[kind] = self._unsupported.get(kind, 0) + 1

        return resource
