"""Utilities for synthesizing minimal Terraform clusters for Kubernetes-only runs."""

from __future__ import annotations

import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict


@dataclass
class SyntheticGKECluster:
    """Represents a temporary Terraform workspace for a fake GKE cluster."""

    temp_dir: TemporaryDirectory
    path: Path
    cluster_name: str

    def cleanup(self) -> None:
        """Remove the temporary directory."""
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        ivalue = int(value)
        return ivalue if ivalue > 0 else default
    except (TypeError, ValueError):
        return default


def _sanitize_name(name: str) -> str:
    allowed = [c for c in name if c.isalnum() or c in "-"]
    return "".join(allowed) or "synthetic-gke"


def create_synthetic_gke_cluster(fallback_cfg: Dict[str, Any]) -> SyntheticGKECluster:
    """Create a temporary Terraform project describing a minimal GKE cluster."""
    temp_dir = TemporaryDirectory(prefix="leaf_synth_gke_")
    path = Path(temp_dir.name)

    raw_name = fallback_cfg.get("cluster_name") or f"synthetic-{uuid.uuid4().hex[:8]}"
    cluster_name = _sanitize_name(str(raw_name).lower())
    region = str(fallback_cfg.get("region") or "us-central1")
    machine_type = str(fallback_cfg.get("machine_type") or "e2-standard-2")

    requested_default = _coerce_positive_int(fallback_cfg.get("default_count"), 0)
    min_count = _coerce_positive_int(fallback_cfg.get("min_count"), 1)
    if requested_default > 0:
        initial_count = requested_default
    else:
        initial_count = min_count

    max_override = _coerce_positive_int(fallback_cfg.get("max_count"), 0)
    if max_override > 0:
        max_count = max(max_override, min_count)
    else:
        # If no max specified, allow generous headroom so autoscaler can react.
        max_count = max(initial_count + 4, min_count * 3)

    node_count = min(initial_count, max_count)

    hcl = textwrap.dedent(
        f"""
        terraform {{
          required_version = ">= 1.0.0"
          required_providers {{
            google = {{
              source  = "hashicorp/google"
              version = ">= 4.0.0"
            }}
          }}
        }}

        provider "google" {{
          project = "synthetic-project"
          region  = "{region}"
        }}

        resource "google_container_cluster" "synthetic" {{
          name               = "{cluster_name}"
          location           = "{region}"
          remove_default_node_pool = true
          initial_node_count = {min_count}
          networking_mode    = "VPC_NATIVE"
        }}

        resource "google_container_node_pool" "synthetic_pool" {{
          name       = "{cluster_name}-pool"
          location   = "{region}"
          cluster    = google_container_cluster.synthetic.name
          node_count = {node_count}

          autoscaling {{
            min_node_count = {min_count}
            max_node_count = {max_count}
          }}

          node_config {{
            machine_type = "{machine_type}"
            disk_size_gb = 100
            oauth_scopes = []
          }}
        }}
        """
    ).strip()

    (path / "main.tf").write_text(hcl, encoding="utf-8")
    return SyntheticGKECluster(temp_dir=temp_dir, path=path, cluster_name=cluster_name)
