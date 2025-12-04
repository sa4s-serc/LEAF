# leaf-cloud/utils/capacity_mappings.py
import logging
import re  # For parsing CPU strings like "1000m"
from typing import Dict, Any, Optional, Union

from leaf_cloud.core.resource import ResourceType

logger = logging.getLogger(__name__)

# --- Helper for CPU parsing ---


def _parse_cpu_to_float(cpu_str: str) -> Optional[float]:
    """Converts CPU string (e.g., "1", "1.0", "1000m", "0.5") to float vCPU equivalent."""
    if not isinstance(cpu_str, str):
        try:  # Handle direct numbers if provided
            return float(cpu_str)
        except (ValueError, TypeError):
            logger.warning(f"CPU value is not a string or number: {cpu_str}")
            return None

    if cpu_str.endswith("m"):  # Millicpu
        try:
            return float(cpu_str[:-1]) / 1000.0
        except ValueError:
            logger.warning(f"Invalid millicpu value: {cpu_str}")
            return None
    try:
        return float(cpu_str)
    except ValueError:
        logger.warning(f"Invalid CPU float value: {cpu_str}")
        return None


# --- Configuration for Capacity Mapping ---
TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP: Dict[
    str, Dict[str, Union[Dict[str, float], str]]
] = {
    "google_compute_instance": {
        "machine_type": {
            # GCP E2 series (vCPUs)
            "e2-micro": 0.25,
            "e2-small": 0.5,
            "e2-medium": 1.0,
            "e2-standard-2": 2.0,
            "e2-standard-4": 4.0,
            "e2-standard-8": 8.0,
            "e2-standard-16": 16.0,
            "e2-standard-32": 32.0,
            # GCP N1 series (vCPUs)
            "n1-standard-1": 1.0,
            "n1-standard-2": 2.0,
            "n1-standard-4": 4.0,
            "n1-standard-8": 8.0,
            "n1-standard-16": 16.0,
            "n1-standard-32": 32.0,
            "n1-standard-64": 64.0,
            "n1-standard-96": 96.0,
            # GCP N2 series (vCPUs)
            "n2-standard-2": 2.0,
            "n2-standard-4": 4.0,
            "n2-standard-8": 8.0,
            "n2-standard-16": 16.0,
            "n2-standard-32": 32.0,
            "n2-standard-48": 48.0,
            "n2-standard-64": 64.0,
            "n2-standard-80": 80.0,
            "n2-standard-96": 96.0,
            "n2-standard-128": 128.0,
            # GCP N2D series (vCPUs)
            "n2d-standard-2": 2.0,
            "n2d-standard-4": 4.0,
            "n2d-standard-8": 8.0,
            "n2d-standard-16": 16.0,
            "n2d-standard-32": 32.0,
            "n2d-standard-48": 48.0,
            "n2d-standard-64": 64.0,
            "n2d-standard-80": 80.0,
            "n2d-standard-96": 96.0,
            "n2d-standard-128": 128.0,
            "n2d-standard-224": 224.0,
            # GCP C2 series (Compute Optimized) (vCPUs)
            "c2-standard-4": 4.0,
            "c2-standard-8": 8.0,
            "c2-standard-16": 16.0,
            "c2-standard-30": 30.0,
            "c2-standard-60": 60.0,
            # Shared core (vCPUs)
            "f1-micro": 0.2,
            "g1-small": 0.5,
        }
    },
    "google_sql_database_instance": {
        "settings.tier": {
            # Based on vCPUs generally
            "db-f1-micro": 1.0,
            "db-g1-small": 1.0,
            "db-n1-standard-1": 1.0,
            "db-n1-standard-2": 2.0,
            "db-n1-standard-4": 4.0,
            "db-n1-standard-8": 8.0,
            "db-n1-standard-16": 16.0,
            "db-n1-standard-32": 32.0,
            "db-n1-standard-64": 64.0,
            "db-n1-standard-96": 96.0,
            "db-n1-highmem-2": 2.0,
            "db-n1-highmem-4": 4.0,
            # Custom tiers (format: db-custom-<vcpus>-<memory_mb>)
            "db-custom-4-8192": 4.0,  # 4 vCPUs, 8GB RAM
        }
    },
    "google_container_node_pool": {
        "node_config.machine_type": "google_compute_instance.machine_type",
        "initial_node_count": "multiplier",
        "autoscaling.min_node_count": "multiplier_fallback",
        "node_count": "multiplier_fallback",
    },
    "google_cloudfunctions_function": {
        # Capacity based on memory (e.g., 1 unit per 256MB, adjust as needed)
        "available_memory_mb": {
            128: 0.5,
            256: 1.0,
            512: 2.0,
            1024: 4.0,
            2048: 8.0,
            4096: 16.0,
            8192: 32.0,
        },
        # For 2nd Gen Cloud Functions, these attributes are under 'service_config'
        "service_config.available_memory": {  # Values like "128Mi", "256Mi", "1Gi"
            "128Mi": 0.5,
            "256Mi": 1.0,
            "512Mi": 2.0,
            "1Gi": 4.0,
            "2Gi": 8.0,
            "4Gi": 16.0,
            "8Gi": 32.0,
        },
        "service_config.cpu": "cpu_direct",  # Special handler for CPU string
    },
    "google_cloud_run_service": {
        "template.spec.containers[0].resources.limits.cpu": "cpu_direct",
        # container_concurrency is more about request handling limit per instance, not raw compute capacity.
        # It's handled separately if needed by the simulation logic for CloudRun resources.
    },
    "google_redis_instance": {
        # Capacity based on memory_size_gb (e.g., 1 unit per GB)
        "memory_size_gb": {
            1: 1.0,
            2: 2.0,
            4: 4.0,
            8: 8.0,
            16: 16.0,
            32: 32.0,  # ... and so on
            5: 5.0,
            10: 10.0,
            20: 20.0,
            25: 25.0,
            50: 50.0,
            100: 100.0,
            300: 300.0,
        }
    },
    "google_memcache_instance": {
        # This is per-node CPU, acts as base_capacity_unit
        "node_config.cpu_count": "multiplier_direct",
        # Could also map memory to capacity, for now focusing on CPU
        "node_config.memory_size_mb": {},
        "node_count": "multiplier",  # Multiplies the per-node capacity
    },
}

RESOURCE_TYPE_DEFAULT_CAPACITIES: Dict[ResourceType, float] = {
    ResourceType.COMPUTE: 1.0,
    ResourceType.DATABASE: 1.0,
    ResourceType.STORAGE: 1.0,
    ResourceType.NETWORK: 1.0,
    ResourceType.SECURITY: 1.0,
    ResourceType.GENERIC: 1.0,
}

DIRECT_QUANTITY_ATTRIBUTES = {
    "google_compute_instance_group_manager": ["target_size"],
    "google_compute_region_instance_group_manager": ["target_size"],
    # For 2nd Gen
    "google_cloudfunctions_function": ["service_config.max_instance_count"],
    "google_cloud_run_service": [
        # or max_scale depending on simulation goal
        "template.spec.autoscaling.min_scale",
        "template.spec.autoscaling.max_scale",
    ],
    # Already handled as multiplier in TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP
    "google_memcache_instance": ["node_count"],
    # but listed here for clarity if used directly.
}


def _get_value_from_config(config: Dict[str, Any], key_path: str) -> Any:
    keys = key_path.split(".")
    value = config
    # Handle list indexing like containers[0].resources
    for key in keys:
        match = re.fullmatch(r"(.+)\[(\d+)\]", key)
        if match:
            list_key, index_str = match.groups()
            index = int(index_str)
            if (
                isinstance(value, dict)
                and list_key in value
                and isinstance(value[list_key], list)
                and index < len(value[list_key])
            ):
                value = value[list_key][index]
            else:
                return None
        elif isinstance(value, dict) and key in value:
            value = value[key]
            # Special handling for CloudSQL settings which can be a list
            # If we just accessed 'settings' and it's a list, use the first element
            if key == "settings" and isinstance(value, list) and len(value) > 0:
                value = value[0]
        else:
            return None
    return value


def get_capacity_from_config(
    tf_resource_type: str,
    tf_config: Dict[str, Any],
    leaf_resource_type: ResourceType,
) -> float:
    # 🚫 GUARD-RAIL 1: Skip control-plane/IAM resources at the source.
    # This guarantees every downstream stage sees 0 kWh for these types.
    
    # Define control-plane resources that should have zero capacity
    CONTROL_PLANE_RESOURCES = {
        "google_project_service",
        "google_service_networking_connection", 
        "google_service_account",
        "google_artifact_registry_repository",
        "google_cloudbuild_trigger",
        "google_clouddeploy_delivery_pipeline",
        "null_resource",
        "google_compute_address",
        "google_compute_global_address",
        "google_compute_router",
        "google_compute_router_nat", 
        "google_compute_firewall",
        "google_compute_subnetwork",
        "google_vpc_access_connector",
        "google_dns_managed_zone",
        "google_dns_record_set",
        "google_monitoring_alert_policy",
        "google_logging_project_sink",
        "google_monitoring_notification_channel",
        "google_project_iam_custom_role",
        "google_organization_policy",
        "google_folder_organization_policy",
        "google_project_organization_policy",
    }
    
    # IAM-related resource prefixes
    IAM_RESOURCE_PREFIXES = (
        "google_project_iam_",
        "google_service_account_iam_",
        "google_cloud_run_service_iam_",
        "google_sql_user_iam_",
        "google_storage_bucket_iam_",
        "google_bigquery_dataset_iam_",
        "google_pubsub_topic_iam_",
        "google_pubsub_subscription_iam_",
        "google_folder_iam_",
        "google_organization_iam_",
        "google_artifact_registry_repository_iam_",
    )
    
    # Check if this is a control-plane resource
    if (tf_resource_type in CONTROL_PLANE_RESOURCES or 
        any(tf_resource_type.startswith(prefix) for prefix in IAM_RESOURCE_PREFIXES)):
        logger.debug(f"Setting capacity to 0.0 for control-plane resource: {tf_resource_type}")
        return 0.0
    
    custom_capacity_tag = _get_value_from_config(
        tf_config, "labels.simulation_capacity"
    )
    if custom_capacity_tag:
        try:
            capacity = float(custom_capacity_tag)
            logger.info(
                f"Using custom 'simulation_capacity' tag: {capacity} for {tf_resource_type} {tf_config.get('name', '')}"
            )
            return capacity
        except ValueError:
            logger.warning(
                f"Invalid 'simulation_capacity' tag value: {custom_capacity_tag}. Ignoring."
            )

    base_capacity_unit = 1.0
    multiplier = 1.0
    found_specific_mapping = False

    if tf_resource_type in TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP:
        mappings = TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP[tf_resource_type]
        for attr_key, map_or_ref in mappings.items():
            attr_value = _get_value_from_config(tf_config, attr_key)
            if attr_value is not None:
                found_specific_mapping = (
                    True  # Mark if any relevant attribute is found
                )
                if isinstance(map_or_ref, str):
                    if map_or_ref == "multiplier":
                        try:
                            multiplier *= float(attr_value)
                        except ValueError:
                            logger.warning(
                                f"Could not parse multiplier '{attr_value}' for {attr_key} in {tf_resource_type}"
                            )
                    elif (
                        map_or_ref == "multiplier_fallback"
                        and multiplier == 1.0
                    ):
                        try:
                            multiplier *= float(attr_value)
                        except ValueError:
                            logger.warning(
                                f"Could not parse fallback multiplier '{attr_value}' for {attr_key} in {tf_resource_type}"
                            )
                    # For direct values that act as base capacity for a sub-entity, then multiplied
                    elif map_or_ref == "multiplier_direct":
                        try:
                            # e.g. memcache node_cpu_count
                            base_capacity_unit = float(attr_value)
                        except ValueError:
                            logger.warning(
                                f"Could not parse direct multiplier base '{attr_value}' for {attr_key} in {tf_resource_type}"
                            )
                    elif map_or_ref == "cpu_direct":
                        parsed_cpu = _parse_cpu_to_float(attr_value)
                        if parsed_cpu is not None:
                            base_capacity_unit = parsed_cpu
                        else:
                            found_specific_mapping = (
                                False  # Parsing failed, don't count as mapped
                            )
                    elif "." in map_or_ref:  # Reference
                        ref_tf_type, ref_attr_key = map_or_ref.split(".", 1)
                        if (
                            ref_tf_type
                            in TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP
                            and ref_attr_key
                            in TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP[
                                ref_tf_type
                            ]
                        ):
                            value_map = (
                                TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP[
                                    ref_tf_type
                                ][ref_attr_key]
                            )
                            if (
                                isinstance(value_map, dict)
                                and attr_value in value_map
                            ):
                                base_capacity_unit = value_map[attr_value]
                                logger.debug(
                                    f"Mapped {tf_resource_type}.{attr_key} ('{attr_value}') to base {base_capacity_unit} via ref {map_or_ref}"
                                )
                            # else: found_specific_mapping = False # Value not in referenced map
                        # else: found_specific_mapping = False # Reference not found
                    # else: found_specific_mapping = False # Unknown string command
                elif isinstance(map_or_ref, dict):
                    if attr_value in map_or_ref:
                        base_capacity_unit = map_or_ref[attr_value]
                        logger.debug(
                            f"Mapped {tf_resource_type}.{attr_key} ('{attr_value}') to base {base_capacity_unit}"
                        )
                    # else: found_specific_mapping = False # Value not in map
                # else: found_specific_mapping = False # Invalid map_or_ref type
            # else: # attr_value is None, don't unset found_specific_mapping if already true from another attr
            # pass

    # Apply DIRECT_QUANTITY_ATTRIBUTES as multipliers
    # This should apply *after* base_capacity_unit is determined from specific mappings.
    if tf_resource_type in DIRECT_QUANTITY_ATTRIBUTES:
        for attr_key in DIRECT_QUANTITY_ATTRIBUTES[tf_resource_type]:
            # Avoid double-counting if already handled as a 'multiplier' in TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP
            # This check is a bit heuristic; depends on consistent naming in the maps.
            if attr_key in TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP.get(
                tf_resource_type, {}
            ):
                map_type = TERRAFORM_ATTRIBUTE_TO_CAPACITY_UNIT_MAP[
                    tf_resource_type
                ][attr_key]
                if (
                    map_type == "multiplier"
                    or map_type == "multiplier_fallback"
                ):
                    logger.debug(
                        f"Skipping {attr_key} from DIRECT_QUANTITY_ATTRIBUTES as it's already a multiplier in main map for {tf_resource_type}"
                    )
                    continue

            attr_value = _get_value_from_config(tf_config, attr_key)
            if attr_value is not None:
                try:
                    val = float(attr_value)
                    # If this is a primary scaling factor (e.g. max_instance_count for serverless)
                    # and no other multiplier has been explicitly set from the main map, use this.
                    # Otherwise, multiply. This handles cases like GKE node_count (main map) vs target_size (direct).
                    if (
                        attr_key == "service_config.max_instance_count"
                        or attr_key == "template.spec.autoscaling.max_scale"
                    ):
                        if (
                            multiplier == 1.0
                        ):  # Only if no other multiplier from main map took precedence
                            multiplier = val
                        # A specific multiplier (e.g. initial_node_count) was already set
                        else:
                            multiplier *= val
                    else:
                        multiplier *= val
                    found_specific_mapping = (
                        True  # Counts as specific if it contributes
                    )
                    logger.debug(
                        f"Applied direct quantity attribute {tf_resource_type}.{attr_key} ('{attr_value}') as multiplier, new multiplier: {multiplier}"
                    )
                except ValueError:
                    logger.warning(
                        f"Could not parse direct quantity value '{attr_value}' for attribute '{attr_key}' in {tf_resource_type}"
                    )

    calculated_capacity = base_capacity_unit * multiplier

    if (
        found_specific_mapping and calculated_capacity > 0
    ):  # Ensure capacity is positive
        logger.debug(
            f"Capacity for {tf_resource_type} ({tf_config.get('name', '')}): {calculated_capacity:.2f} (base: {base_capacity_unit:.2f}, mult: {multiplier:.2f})"
        )
        return calculated_capacity
    else:
        default_capacity = RESOURCE_TYPE_DEFAULT_CAPACITIES.get(
            leaf_resource_type, 1.0
        )
        logger.debug(
            f"No specific capacity mapping or zero capacity for {tf_resource_type} ({tf_config.get('name', '')}). Using default for {leaf_resource_type.name}: {default_capacity}"
        )
        return default_capacity
