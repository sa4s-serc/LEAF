"""Resource type mapping for LEAF-Cloud.

This module provides centralized mapping between Terraform resource types
and LEAF-Cloud resource types.
"""

from typing import Dict, Optional

# Mapping from Terraform resource types to LEAF-Cloud resource types
RESOURCE_TYPE_MAP = {
    # Compute resources
    "google_compute_instance": "ComputeEngineVM",
    "google_container_cluster": "GKECluster",
    "google_container_node_pool": "GKENode",
    "google_cloudfunctions_function": "CloudFunction",
    "google_cloud_run_service": "CloudRun",
    "google_app_engine_application": "AppEngine",
    
    # Storage resources
    "google_storage_bucket": "CloudStorage",
    "google_compute_disk": "PersistentDisk",
    "google_sql_database_instance": "CloudSQL",
    "google_bigtable_instance": "Bigtable",
    "google_firestore_document": "Firestore",
    
    # Networking resources
    "google_compute_network": "VPC",
    "google_compute_subnetwork": "Subnet",
    "google_compute_firewall": "Firewall",
    "google_compute_router": "Router",
    "google_compute_vpn_gateway": "VPNGateway",
    
    # Security resources
    "google_cloud_identity_group": "CloudIdentity",
    "google_cloud_run_service_iam_binding": "CloudRun",
    "google_cloudfunctions_function_iam_binding": "CloudFunction",
    
    # Monitoring & Logging
    "google_monitoring_alert_policy": "Monitoring",
    "google_logging_metric": "Logging",
    
    # Serverless
    "google_cloud_scheduler_job": "CloudScheduler",
    "google_cloud_tasks_queue": "CloudTasks",
    
    # Database
    "google_spanner_instance": "Spanner",
    "google_redis_instance": "Memorystore",
    
    # AI/ML
    "google_ai_platform_model": "AIPlatform",
    "google_vertex_ai_dataset": "VertexAI",
}

def get_resource_type(terraform_type: str) -> str:
    """Get the LEAF-Cloud resource type for a Terraform resource type.
    
    Args:
        terraform_type: The Terraform resource type (e.g., 'google_compute_instance')
        
    Returns:
        The corresponding LEAF-Cloud resource type, or the Terraform type if no mapping exists
    """
    # Try exact match first
    if terraform_type in RESOURCE_TYPE_MAP:
        return RESOURCE_TYPE_MAP[terraform_type]
    
    # Try prefix match for resource types with suffixes (e.g., _iam_binding)
    for tf_type, leaf_type in RESOURCE_TYPE_MAP.items():
        if terraform_type.startswith(tf_type):
            return leaf_type
    
    # Default to the Terraform type if no mapping is found
    return terraform_type
