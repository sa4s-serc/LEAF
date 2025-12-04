"""
Power and energy calculation utilities for cloud resources.

This module provides standardized functions for calculating power and energy
consumption of cloud resources, with consistent units (kW for power, 
kWh for energy).
"""

from dataclasses import dataclass
from typing import Dict, Optional

# Common power constants (in kW)
IDLE_POWER_PER_VCPU = 0.002  # 2W per vCPU
IDLE_POWER_PER_GB_RAM = 0.00038  # 0.38W per GB
ACTIVE_POWER_PER_VCPU = 0.015  # 15W per vCPU at 100% utilization
ACTIVE_POWER_PER_GB_RAM = 0.0005  # 0.5W per GB at 100% utilization
BASE_SYSTEM_POWER_KW = 0.04  # 40W base system power

@dataclass(frozen=True)
class MachinePowerCoefficients:
    """Express idle/max watt contributions explicitly in watts."""

    base_idle_w: float
    idle_w_per_vcpu: float
    idle_w_per_gb: float
    active_w_per_vcpu: float
    active_w_per_gb: float


def _scale_coefficients(scale: float) -> MachinePowerCoefficients:
    """Scale default constants by a factor to express tuned watts."""
    return MachinePowerCoefficients(
        base_idle_w=BASE_SYSTEM_POWER_KW * 1000.0 * scale,
        idle_w_per_vcpu=IDLE_POWER_PER_VCPU * 1000.0 * scale,
        idle_w_per_gb=IDLE_POWER_PER_GB_RAM * 1000.0 * scale,
        active_w_per_vcpu=ACTIVE_POWER_PER_VCPU * 1000.0 * scale,
        active_w_per_gb=ACTIVE_POWER_PER_GB_RAM * 1000.0 * scale,
    )


DEFAULT_POWER_COEFFICIENTS = MachinePowerCoefficients(
    base_idle_w=BASE_SYSTEM_POWER_KW * 1000.0,
    idle_w_per_vcpu=IDLE_POWER_PER_VCPU * 1000.0,
    idle_w_per_gb=IDLE_POWER_PER_GB_RAM * 1000.0,
    active_w_per_vcpu=ACTIVE_POWER_PER_VCPU * 1000.0,
    active_w_per_gb=ACTIVE_POWER_PER_GB_RAM * 1000.0,
)


# Machine families we have calibrated against Cloud Carbon Footprint data.
_MACHINE_TYPE_POWER_OVERRIDES: Dict[str, MachinePowerCoefficients] = {
    # Shared-core variants
    "e2-micro": _scale_coefficients(0.32),
    "e2-small": _scale_coefficients(0.34),
    "e2-medium": _scale_coefficients(0.36),
    "f1-micro": _scale_coefficients(0.9),
    "g1-small": _scale_coefficients(0.85),
}

_MACHINE_FAMILY_POWER_OVERRIDES: Dict[str, MachinePowerCoefficients] = {
    # E2 family (AMD Rome / Intel Cascade Lake mix)
    "e2-standard": _scale_coefficients(0.34),
    "e2-highmem": _scale_coefficients(0.36),
    "e2-highcpu": _scale_coefficients(0.30),
    # N2/N2D families
    "n2-standard": _scale_coefficients(0.85),
    "n2-highmem": _scale_coefficients(0.88),
    "n2-highcpu": _scale_coefficients(0.80),
    "n2d-standard": _scale_coefficients(0.78),
    "n2d-highmem": _scale_coefficients(0.80),
    "n2d-highcpu": _scale_coefficients(0.72),
    # N1 (legacy Intel)
    "n1-standard": _scale_coefficients(1.05),
    "n1-highmem": _scale_coefficients(1.08),
    "n1-highcpu": _scale_coefficients(1.00),
    # C2 (Cascade Lake) and C3 (AMD Genoa)
    "c2-standard": _scale_coefficients(0.78),
    "c2d-standard": _scale_coefficients(0.70),
    "c3-standard": _scale_coefficients(0.38),
    "c3-highmem": _scale_coefficients(0.35),
    "c3-highcpu": _scale_coefficients(0.32),
    # T2D (Tau, AMD Milan)
    "t2d-standard": _scale_coefficients(0.55),
    # M-series memory optimized
    "m1-ultramem": _scale_coefficients(0.95),
    "m1-megamem": _scale_coefficients(0.95),
    "m2-ultramem": _scale_coefficients(0.90),
    "m2-megamem": _scale_coefficients(0.90),
    # GPU-backed A2
    "a2-highgpu": _scale_coefficients(1.20),
    "a2-megagpu": _scale_coefficients(1.25),
}


def _normalize_machine_type(machine_type: Optional[str]) -> Optional[str]:
    if not machine_type:
        return None
    return machine_type.strip().lower()


def _extract_machine_family(machine_type: str) -> str:
    """
    Best-effort extraction of the family identifier from a GCE machine type.

    Examples:
        c3-highmem-16 -> c3-highmem
        n2-standard-8 -> n2-standard
    """
    parts = machine_type.split("-")
    if len(parts) <= 1:
        return machine_type
    suffix = parts[-1]
    # If the suffix contains any digit we treat it as the size identifier.
    if any(ch.isdigit() for ch in suffix):
        return "-".join(parts[:-1])
    return machine_type


def _get_power_coefficients(machine_type: Optional[str]) -> MachinePowerCoefficients:
    """Look up explicit watt coefficients for a machine type or family."""
    normalized = _normalize_machine_type(machine_type)
    if normalized:
        if normalized in _MACHINE_TYPE_POWER_OVERRIDES:
            return _MACHINE_TYPE_POWER_OVERRIDES[normalized]
        family = _extract_machine_family(normalized)
        if family in _MACHINE_FAMILY_POWER_OVERRIDES:
            return _MACHINE_FAMILY_POWER_OVERRIDES[family]
    return DEFAULT_POWER_COEFFICIENTS


def build_power_profile_from_machine(
    vcpus: int,
    memory_gb: float,
    machine_type: Optional[str] = None,
    preemptible: bool = False,
) -> "PowerProfile":
    """
    Construct a PowerProfile using explicit watt coefficients per machine family.

    Args:
        vcpus: Instance vCPU count.
        memory_gb: Instance memory in GiB.
        machine_type: Optional GCE machine type string.
        preemptible: Whether the node is preemptible/spot.
    """
    coeffs = _get_power_coefficients(machine_type)
    idle_w = (
        coeffs.base_idle_w
        + (float(vcpus) * coeffs.idle_w_per_vcpu)
        + (float(memory_gb) * coeffs.idle_w_per_gb)
    )
    active_w = (
        (float(vcpus) * coeffs.active_w_per_vcpu)
        + (float(memory_gb) * coeffs.active_w_per_gb)
    )
    idle_kw = idle_w / 1000.0
    max_kw = (idle_w + active_w) / 1000.0

    if preemptible:
        idle_kw *= 0.91
        max_kw *= 0.91

    return PowerProfile(
        idle_power_kw=idle_kw,
        max_power_kw=max_kw,
    )


@dataclass
class PowerProfile:
    """Stores power consumption characteristics of a resource."""

    idle_power_kw: float = 0.0  # Power at 0% utilization (kW)
    max_power_kw: float = 0.0  # Power at 100% utilization (kW)
    min_utilization: float = 0.0  # Min utilization threshold
    max_utilization: float = 1.0  # Max utilization threshold
    efficiency_factor: float = 1.0  # Multiplier for power efficiency

    @classmethod
    def from_specs(
        cls,
        vcpus: int,
        memory_gb: float,
        base_system_power_kw: float = BASE_SYSTEM_POWER_KW,
        efficiency_factor: float = 1.0
    ) -> 'PowerProfile':
        """
        Create a power profile from resource specifications.

        Args:
            vcpus: Number of virtual CPUs
            memory_gb: Memory size in GB
            base_system_power_kw: Base system power in kW
            efficiency_factor: Efficiency factor (lower is more efficient)

        Returns:
            PowerProfile instance with calculated power characteristics
        """
        # Calculate idle power (base + CPU + memory)
        idle_power = (
            base_system_power_kw +
            (vcpus * IDLE_POWER_PER_VCPU) +
            (memory_gb * IDLE_POWER_PER_GB_RAM)
        )

        # Calculate max power (idle + active components)
        max_power = idle_power + (
            (vcpus * ACTIVE_POWER_PER_VCPU) +
            (memory_gb * ACTIVE_POWER_PER_GB_RAM)
        )

        # Apply efficiency factor
        return cls(
            idle_power_kw=idle_power * efficiency_factor,
            max_power_kw=max_power * efficiency_factor,
            min_utilization=0.0,
            max_utilization=1.0,
            efficiency_factor=efficiency_factor
        )

    def calculate_power(
        self,
        utilization: float,
        min_utilization: float = None,
        max_utilization: float = None
    ) -> float:
        """
        Calculate power consumption for this profile.

        Args:
            utilization: Current utilization (0.0 to 1.0)
            min_utilization: Override minimum utilization threshold
            max_utilization: Override maximum utilization threshold

        Returns:
            Power consumption in kW

        Raises:
            ValueError: If utilization is outside [0, 1] or 
                max_power < idle_power
        """
        if not 0 <= utilization <= 1:
            msg = f"Utilization must be between 0 and 1, got {utilization}"
            raise ValueError(msg)
            
        if self.max_power_kw < self.idle_power_kw:
            msg = "max_power_kw must be greater than or equal to idle_power_kw"
            raise ValueError(msg)

        if min_utilization is not None and max_utilization is not None:
            if not (0 <= min_utilization <= 1 and 0 <= max_utilization <= 1):
                raise ValueError(
                    "min_utilization and max_utilization "
                    "must be between 0 and 1"
                )

            if min_utilization > max_utilization:
                msg = "min_utilization must be <= max_utilization"
                raise ValueError(msg)

        # Clamp utilization within min/max bounds
        min_util = min_utilization or self.min_utilization
        max_util = max_utilization or self.max_utilization
        util = max(min_util, min(utilization, max_util))

        # Linear interpolation between idle and max power
        power_range = self.max_power_kw - self.idle_power_kw
        return self.idle_power_kw + (util * power_range)


def calculate_energy(
    power_kw: float,
    duration_hours: float
) -> float:
    """
    Calculate energy consumption.

    Args:
        power_kw: Power consumption in kilowatts (kW)
        duration_hours: Duration in hours

    Returns:
        Energy consumption in kilowatt-hours (kWh)
    """
    return power_kw * duration_hours



class PowerMixin:
    """Mixin class for resources with power consumption capabilities."""

    def __init__(self, *args, **kwargs):
        self._power_profile: Optional[PowerProfile] = None
        super().__init__(*args, **kwargs)

    @property
    def power_profile(self) -> PowerProfile:
        """Get the power profile, initializing if necessary."""
        if self._power_profile is None:
            self._power_profile = self._create_power_profile()
        return self._power_profile

    def _create_power_profile(self) -> PowerProfile:
        """Create a power profile for this resource."""
        msg = "Subclasses must implement _create_power_profile"
        raise NotImplementedError(msg)

    def get_power_consumption(self, utilization: float) -> float:
        """Calculate power consumption for this resource.

        Args:
            utilization: Current utilization (0.0 to 1.0)

        Returns:
            Power consumption in kW
        """
        if not hasattr(self, '_power_profile'):
            self._power_profile = self._create_power_profile()
            
        return self._power_profile.calculate_power(utilization)


__all__ = [
    "PowerProfile",
    "calculate_energy",
    "PowerMixin",
    "build_power_profile_from_machine",
]
