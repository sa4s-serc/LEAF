from enum import Enum
from typing import Dict, List, Optional, Union, Any
import math
import logging
from leaf_cloud.core.resource import Resource, ResourceType, ResourceState

"""
gcp/security.py

This module defines security-related GCP resources for the LEAF-Cloud framework.
It models IAM, Security Command Center, Cloud KMS, Cloud Armor resources,
including their specific attributes, energy models, and Petri net mappings.
"""

# Configure logger
logger = logging.getLogger(__name__)


class SecurityResourceType(Enum):
    """Specific security resource types in GCP"""

    IAM = "iam"
    SECURITY_COMMAND_CENTER = "security_command_center"
    CLOUD_KMS = "cloud_kms"
    CLOUD_ARMOR = "cloud_armor"
    SECRET_MANAGER = "secret_manager"
    IDENTITY_PLATFORM = "identity_platform"
    ACCESS_TRANSPARENCY = "access_transparency"
    VPC_SERVICE_CONTROLS = "vpc_service_controls"


class SecurityOperationType(Enum):
    """Types of security operations in GCP"""

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    ENCRYPTION = "encryption"
    DECRYPTION = "decryption"
    THREAT_DETECTION = "threat_detection"
    AUDIT_LOGGING = "audit_logging"
    KEY_ROTATION = "key_rotation"
    WAF_FILTERING = "waf_filtering"


class SecurityResource(Resource):
    """Base class for all security resources in GCP"""

    def __init__(
        self,
        name: str,
        capacity: float = 1.0,
        security_type: SecurityResourceType = SecurityResourceType.IAM,
        region: Optional[str] = None,
        processing_capacity_ops: float = 1000.0,
        base_latency_ms: float = 10.0,
    ):
        """
        Initialize a security resource.

        Args:
            name: Unique name for the resource
            capacity: Maximum capacity of the resource
            security_type: Specific type of security resource
            region: The GCP region where the resource is deployed
            processing_capacity_ops: Operations per second capacity
            base_latency_ms: Base latency in milliseconds for security operations
        """
        super().__init__(name, ResourceType.SECURITY, region)
        self.capacity = capacity
        self.security_type = security_type
        self.processing_capacity_ops = processing_capacity_ops
        self.base_latency_ms = base_latency_ms
        # Power parameters (placeholders, to be calibrated)
        self._base_power_kw: float = 0.0
        self._power_per_op_kw: float = 0.0
        # Track operations and utilization
        self.active_operations = 0
        self.operations_per_second = 0.0
        self.total_operations = 0

    def _set_power_parameters(
        self, base_power_w: float, power_per_op_w: float
    ):
        """Set power parameters, converting from W to kW."""
        self._base_power_kw = base_power_w / 1000.0  # Convert base power to kW
        self._power_per_op_kw = (
            power_per_op_w / 1000.0
        )  # Convert power per op to kW

    def get_power_consumption(self, timestamp: float) -> float:
        """
        Calculate instantaneous power consumption in kW.

        Security equipment power consumption is modeled based on:
        1. Base power (idle state)
        2. Power per operation, scaled by the number of active operations

        Returns:
            Instantaneous power consumption in kW.
        """
        # Calculate power based on active operations (do not scale operational part
        # with any external base scaling factors; subclasses may adjust base only)
        operational_power = self.operations_per_second * self._power_per_op_kw
        return self._base_power_kw + operational_power

    def record_utilization(self, timestamp: float) -> None:
        """Record utilization and cap history to prevent unbounded growth."""
        super().record_utilization(timestamp)
        # Cap to last 1000 records for stability
        if len(self.utilization_history) > 1000:
            self.utilization_history = self.utilization_history[-1000:]

    def get_current_latency_ms(self) -> float:
        """
        Calculate the current latency including base latency and load effects.

        Returns:
            Current latency in milliseconds
        """
        # Security operations can experience higher latency under load
        # At 100% utilization, latency can triple
        load_factor = 1.0 + (self.utilization_ratio * 2.0)
        return self.base_latency_ms * load_factor

    def map_to_petri_net_elements(self) -> Dict[str, Any]:
        """
        Map this security resource to Petri net elements.

        Returns:
            Dictionary of Petri net elements representing this resource
        """
        # Security resources typically have verification/validation steps
        return {
            "places": [
                {
                    "id": f"{self.id}_request",
                    "name": f"{self.name}_request",
                    "tokens": 0,
                },
                {
                    "id": f"{self.id}_validation",
                    "name": f"{self.name}_validation",
                    "tokens": 0,
                },
                {
                    "id": f"{self.id}_processing",
                    "name": f"{self.name}_processing",
                    "tokens": 0,
                },
                {
                    "id": f"{self.id}_complete",
                    "name": f"{self.name}_complete",
                    "tokens": 0,
                },
            ],
            "transitions": [
                {
                    "id": f"{self.id}_validate",
                    "name": f"{self.name}_validate",
                    "delay": self.get_current_latency_ms()
                    * 0.3
                    / 1000.0,  # 30% of time for validation
                },
                {
                    "id": f"{self.id}_process",
                    "name": f"{self.name}_process",
                    "delay": self.get_current_latency_ms()
                    * 0.7
                    / 1000.0,  # 70% of time for processing
                },
            ],
            "arcs": [
                {"from": f"{self.id}_request", "to": f"{self.id}_validate"},
                {"from": f"{self.id}_validate", "to": f"{self.id}_validation"},
                {"from": f"{self.id}_validation", "to": f"{self.id}_process"},
                {"from": f"{self.id}_process", "to": f"{self.id}_complete"},
            ],
            "resource_id": self.id,
        }


class IAM(SecurityResource):
    """
    Google Cloud Identity and Access Management (IAM) resource.
    """

    def __init__(
        self,
        name: str,
        region: str = "global",  # IAM is typically global
        processing_capacity_ops: float = 5000.0,
        policy_count: int = 100,
    ):
        """
        Initialize an IAM resource.

        Args:
            name: Name of the IAM resource
            region: GCP region (typically global for IAM)
            processing_capacity_ops: Operations per second capacity
            policy_count: Number of IAM policies managed
        """
        super().__init__(
            name,
            capacity=1.0,
            security_type=SecurityResourceType.IAM,
            region=region,
            processing_capacity_ops=processing_capacity_ops,
            base_latency_ms=5.0,
        )  # IAM has relatively low latency
        # Low power for auth operations
        self._set_power_parameters(base_power_w=0.5, power_per_op_w=0.0001)

        self.policy_count = policy_count
        self.roles_count = 0
        self.service_accounts_count = 0
        self.auth_operations_per_second = 0.0

    def add_role(self) -> None:
        """Add a role to this IAM resource"""
        self.roles_count += 1
        logger.debug(
            f"Added role to IAM {self.name}, now has {self.roles_count} roles"
        )

    def add_service_account(self) -> None:
        """Add a service account to this IAM resource"""
        self.service_accounts_count += 1
        logger.debug(
            f"Added service account to IAM {self.name}, now has {self.service_accounts_count} service accounts"
        )

    def update_auth_rate(self, operations_per_second: float) -> None:
        """Update the authentication/authorization operations rate"""
        self.auth_operations_per_second = operations_per_second
        self.operations_per_second = operations_per_second

    def get_power_consumption(self, timestamp: float) -> float:
        """
        Calculate power consumption for IAM.
        IAM power depends on policy count and authentication rate.
        """
        # Scale only the idle/base component; keep operational part intact
        operational_power = self.operations_per_second * self._power_per_op_kw
        # Additional factor based on policy complexity applied to base only
        policy_factor = 1.0 + (
            min(1.5, math.log10(max(1, self.policy_count)) / 10) * 0.2
        )
        return (self._base_power_kw * policy_factor) + operational_power

    def get_current_latency_ms(self) -> float:
        """
        Calculate the current IAM operation latency.

        Returns:
            Current latency in milliseconds
        """
        base_latency = super().get_current_latency_ms()

        # Complex policies can increase latency
        if self.policy_count > 1000:
            policy_factor = 1.5
        elif self.policy_count > 500:
            policy_factor = 1.3
        elif self.policy_count > 100:
            policy_factor = 1.1
        else:
            policy_factor = 1.0

        return base_latency * policy_factor


class SecurityCommandCenter(SecurityResource):
    """
    Google Security Command Center resource.
    """

    def __init__(
        self,
        name: str,
        region: str = "global",  # SCC is typically global
        processing_capacity_ops: float = 2000.0,
        monitored_assets_count: int = 100,
        tier: str = "standard",
    ):
        """
        Initialize a Security Command Center resource.

        Args:
            name: Name of the SCC resource
            region: GCP region (typically global for SCC)
            processing_capacity_ops: Operations per second capacity
            monitored_assets_count: Number of assets being monitored
            tier: SCC tier (standard, premium)
        """
        super().__init__(
            name,
            capacity=1.0,
            security_type=SecurityResourceType.SECURITY_COMMAND_CENTER,
            region=region,
            processing_capacity_ops=processing_capacity_ops,
            base_latency_ms=20.0,
        )  # SCC has higher base latency
        # Higher power for scanning/analysis
        self._set_power_parameters(base_power_w=2.0, power_per_op_w=0.001)

        self.monitored_assets_count = monitored_assets_count
        self.tier = tier
        self.active_findings = 0
        self.scan_interval_hours = 24.0  # Default daily scanning
        self.integrations = []  # External integrations

    def add_monitored_asset(self, count: int = 1) -> None:
        """Add monitored assets to SCC"""
        self.monitored_assets_count += count
        logger.debug(
            f"Added {count} monitored assets to SCC {self.name}, now monitoring {self.monitored_assets_count} assets"
        )

    def add_integration(self, integration_name: str) -> None:
        """Add an external integration to SCC"""
        if integration_name not in self.integrations:
            self.integrations.append(integration_name)
            logger.debug(
                f"Added integration {integration_name} to SCC {self.name}"
            )

    def update_scan_interval(self, hours: float) -> None:
        """Update the scanning interval"""
        self.scan_interval_hours = hours

    def get_power_consumption(self, timestamp: float) -> float:
        """
        Calculate power consumption for Security Command Center.
        SCC power depends on assets monitored, scanning frequency, and tier.
        """
        # Scale only the base/idle portion; operational remains linear with ops rate
        operational_power = self.operations_per_second * self._power_per_op_kw
        base_scale = (
            (1.0 + (min(2.0, math.log10(max(1, self.monitored_assets_count)) / 5)))
            * min(2.0, 24.0 / max(1.0, self.scan_interval_hours))
            * (1.5 if self.tier.lower() == "premium" else 1.0)
        )
        return (self._base_power_kw * base_scale) + operational_power

    def get_current_latency_ms(self) -> float:
        """
        Calculate the current SCC operation latency.

        Returns:
            Current latency in milliseconds
        """
        base_latency = super().get_current_latency_ms()

        # Premium tier has better performance
        tier_factor = 0.8 if self.tier.lower() == "premium" else 1.0

        # More assets means more processing time
        asset_factor = 1.0 + (self.monitored_assets_count / 5000)

        return base_latency * tier_factor * min(2.0, asset_factor)


class CloudKMS(SecurityResource):
    """
    Google Cloud Key Management Service (KMS) resource.
    """

    def __init__(
        self,
        name: str,
        region: str,
        processing_capacity_ops: float = 10000.0,
        key_count: int = 10,
        protection_level: str = "software",
    ):
        """
        Initialize a Cloud KMS resource.

        Args:
            name: Name of the KMS resource
            region: GCP region
            processing_capacity_ops: Operations per second capacity
            key_count: Number of keys managed
            protection_level: KMS protection level (software, hsm, external)
        """
        super().__init__(
            name,
            capacity=1.0,
            security_type=SecurityResourceType.CLOUD_KMS,
            region=region,
            processing_capacity_ops=processing_capacity_ops,
            base_latency_ms=15.0,
        )  # KMS has moderate latency
        self._set_power_parameters(
            base_power_w=1.5, power_per_op_w=0.0005
        )  # Crypto is intensive

        self.key_count = key_count
        self.protection_level = protection_level
        self.key_rotation_interval_days = 90  # Default 90-day rotation
        self.crypto_operations_per_second = 0.0

        # Protection level affects both power and latency
        self._adjust_for_protection_level()

    def _adjust_for_protection_level(self):
        """Adjust resource parameters based on protection level"""
        if self.protection_level.lower() == "hsm":
            self.base_latency_ms *= 1.2
            # HSM uses more energy
        elif self.protection_level.lower() == "external":
            self.base_latency_ms *= 1.5
            # External key management has higher latency

    def add_key(self, count: int = 1) -> None:
        """Add cryptographic keys to KMS"""
        self.key_count += count
        logger.debug(
            f"Added {count} keys to KMS {self.name}, now managing {self.key_count} keys"
        )

    def update_crypto_operations_rate(
        self, operations_per_second: float
    ) -> None:
        """Update the cryptographic operations rate"""
        self.crypto_operations_per_second = operations_per_second
        self.operations_per_second = operations_per_second

    def get_power_consumption(self, timestamp: float) -> float:
        """
        Calculate power consumption for Cloud KMS.
        KMS power depends on operation type, key count, and protection level.
        """
        # Scale only base power for key count and protection level; ops remain linear
        operational_power = self.operations_per_second * self._power_per_op_kw
        key_factor = 1.0 + (
            min(1.5, math.log10(max(1, self.key_count)) / 10) * 0.1
        )
        if self.protection_level.lower() == "hsm":
            protection_factor = 1.8
        elif self.protection_level.lower() == "external":
            protection_factor = 0.7
        else:
            protection_factor = 1.0
        base_scaled = self._base_power_kw * key_factor * protection_factor
        return base_scaled + operational_power

    def get_current_latency_ms(self) -> float:
        """
        Calculate the current KMS operation latency.

        Returns:
            Current latency in milliseconds
        """
        base_latency = super().get_current_latency_ms()

        # High operation rates can cause contention
        if self.crypto_operations_per_second > 8000:
            ops_factor = 1.5
        elif self.crypto_operations_per_second > 5000:
            ops_factor = 1.3
        elif self.crypto_operations_per_second > 2000:
            ops_factor = 1.1
        else:
            ops_factor = 1.0

        return base_latency * ops_factor


class CloudArmor(SecurityResource):
    """
    Google Cloud Armor resource for web application firewall (WAF) capabilities.
    """

    def __init__(
        self,
        name: str,
        region: str = "global",  # Cloud Armor is typically global
        processing_capacity_ops: float = 20000.0,
        rule_count: int = 10,
        protection_tier: str = "standard",
    ):
        """
        Initialize a Cloud Armor resource.

        Args:
            name: Name of the Cloud Armor resource
            region: GCP region (typically global for Cloud Armor)
            processing_capacity_ops: Operations per second capacity
            rule_count: Number of security rules
            protection_tier: Protection tier (standard, advanced)
        """
        super().__init__(
            name,
            capacity=1.0,
            security_type=SecurityResourceType.CLOUD_ARMOR,
            region=region,
            processing_capacity_ops=processing_capacity_ops,
            base_latency_ms=2.0,
        )  # Cloud Armor adds minimal latency
        # WAF requires significant power
        self._set_power_parameters(base_power_w=5.0, power_per_op_w=0.0002)

        self.rule_count = rule_count
        self.protection_tier = protection_tier
        self.requests_per_second = 0.0
        self.ddos_protection_enabled = True
        self.custom_rules = []
        self.targets = (
            []
        )  # Load balancers protected by this Cloud Armor policy

    def add_rule(self, rule_name: str) -> None:
        """Add a security rule to Cloud Armor"""
        if rule_name not in self.custom_rules:
            self.custom_rules.append(rule_name)
            self.rule_count += 1
            logger.debug(
                f"Added rule {rule_name} to Cloud Armor {self.name}, now has {self.rule_count} rules"
            )

    def add_protected_target(self, target_name: str) -> None:
        """Add a target (load balancer) protected by this Cloud Armor policy"""
        if target_name not in self.targets:
            self.targets.append(target_name)
            logger.debug(
                f"Added protected target {target_name} to Cloud Armor {self.name}"
            )

    def update_request_rate(self, requests_per_second: float) -> None:
        """Update the request processing rate"""
        self.requests_per_second = requests_per_second
        self.operations_per_second = requests_per_second

    def get_power_consumption(self, timestamp: float) -> float:
        """
        Calculate power consumption for Cloud Armor.
        Cloud Armor power depends on rule count, request rate, and protection tier.
        """
        # Scale only base idle draw; operational part stays linear with request rate
        operational_power = self.operations_per_second * self._power_per_op_kw
        rule_factor = 1.0 + (
            min(2.0, math.log10(max(1, self.rule_count)) / 5) * 0.3
        )
        tier_factor = 1.5 if self.protection_tier.lower() == "advanced" else 1.0
        ddos_factor = 1.2 if self.ddos_protection_enabled else 1.0
        base_scaled = self._base_power_kw * rule_factor * tier_factor * ddos_factor
        return base_scaled + operational_power

    def get_current_latency_ms(self) -> float:
        """
        Calculate the current Cloud Armor operation latency.

        Returns:
            Current latency in milliseconds
        """
        base_latency = super().get_current_latency_ms()

        # Advanced tier might have slightly higher latency due to more inspection
        tier_factor = (
            1.2 if self.protection_tier.lower() == "advanced" else 1.0
        )

        # More rules can increase latency
        rule_factor = 1.0 + (self.rule_count / 200)

        # High request rates can increase latency
        if self.requests_per_second > 15000:
            request_factor = 1.3
        elif self.requests_per_second > 10000:
            request_factor = 1.2
        elif self.requests_per_second > 5000:
            request_factor = 1.1
        else:
            request_factor = 1.0

        return (
            base_latency * tier_factor * min(1.5, rule_factor) * request_factor
        )


class SecretManager(SecurityResource):
    """
    Google Secret Manager resource for storing and accessing secrets securely.
    """

    def __init__(
        self,
        name: str,
        region: str,
        processing_capacity_ops: float = 5000.0,
        secret_count: int = 20,
    ):
        """
        Initialize a Secret Manager resource.

        Args:
            name: Name of the Secret Manager resource
            region: GCP region
            processing_capacity_ops: Operations per second capacity
            secret_count: Number of secrets managed
        """
        super().__init__(
            name,
            capacity=1.0,
            security_type=SecurityResourceType.SECRET_MANAGER,
            region=region,
            processing_capacity_ops=processing_capacity_ops,
            base_latency_ms=8.0,
        )  # Secret Manager has low-moderate latency
        self._set_power_parameters(base_power_w=0.8, power_per_op_w=0.00015)

        self.secret_count = secret_count
        self.access_operations_per_second = 0.0
        self.total_secret_versions = (
            secret_count  # Initially one version per secret
        )

    def add_secret(self, count: int = 1) -> None:
        """Add secrets to Secret Manager"""
        self.secret_count += count
        self.total_secret_versions += count  # Add one version per new secret
        logger.debug(
            f"Added {count} secrets to Secret Manager {self.name}, now managing {self.secret_count} secrets"
        )

    def update_access_rate(self, operations_per_second: float) -> None:
        """Update the secret access operations rate"""
        self.access_operations_per_second = operations_per_second
        self.operations_per_second = operations_per_second

    def get_power_consumption(self, timestamp: float) -> float:
        """Calculate power consumption for Secret Manager."""
        # Scale only base power based on number of secrets; keep ops linear
        operational_power = self.operations_per_second * self._power_per_op_kw
        secret_factor = 1.0 + (
            min(1.5, math.log10(max(1, self.secret_count)) / 10) * 0.2
        )
        return (self._base_power_kw * secret_factor) + operational_power

    def get_current_latency_ms(self) -> float:
        """Calculate the current Secret Manager operation latency."""
        base_latency = super().get_current_latency_ms()

        # High access rates can increase latency
        if self.access_operations_per_second > 4000:
            ops_factor = 1.3
        elif self.access_operations_per_second > 2000:
            ops_factor = 1.2
        elif self.access_operations_per_second > 1000:
            ops_factor = 1.1
        else:
            ops_factor = 1.0

        return base_latency * ops_factor
