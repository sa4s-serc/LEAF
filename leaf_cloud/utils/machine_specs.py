"""Utility helpers for mapping GCP machine types to vCPU and memory specs."""

from __future__ import annotations

from typing import Dict, Tuple

# Minimal catalog for common node types. Values are (vcpus, memory_gb).
_GCP_MACHINE_SPECS: Dict[str, Tuple[float, float]] = {
    # E2 standard family
    "e2-standard-2": (2.0, 8.0),
    "e2-standard-4": (4.0, 16.0),
    "e2-standard-8": (8.0, 32.0),
    "e2-standard-16": (16.0, 64.0),
    "e2-standard-32": (32.0, 128.0),
    # N1 standard family
    "n1-standard-1": (1.0, 3.75),
    "n1-standard-2": (2.0, 7.5),
    "n1-standard-4": (4.0, 15.0),
    "n1-standard-8": (8.0, 30.0),
    "n1-standard-16": (16.0, 60.0),
    "n1-standard-32": (32.0, 120.0),
    # N2 standard family
    "n2-standard-2": (2.0, 8.0),
    "n2-standard-4": (4.0, 16.0),
    "n2-standard-8": (8.0, 32.0),
    "n2-standard-16": (16.0, 64.0),
    "n2-standard-32": (32.0, 128.0),
    "n2-standard-48": (48.0, 192.0),
    "n2-standard-64": (64.0, 256.0),
    "n2-standard-80": (80.0, 320.0),
    # Compute-optimized C2
    "c2-standard-4": (4.0, 16.0),
    "c2-standard-8": (8.0, 32.0),
    "c2-standard-16": (16.0, 64.0),
    "c2-standard-30": (30.0, 120.0),
    "c2-standard-60": (60.0, 240.0),
}

_DEFAULT_SPECS: Tuple[float, float] = (2.0, 8.0)


def get_machine_specs(machine_type: str | None) -> Tuple[float, float]:
    """Return (vcpus, memory_gb) for a given machine type."""
    if not machine_type:
        return _DEFAULT_SPECS
    key = machine_type.strip().lower()
    return _GCP_MACHINE_SPECS.get(key, _DEFAULT_SPECS)


__all__ = ["get_machine_specs"]
