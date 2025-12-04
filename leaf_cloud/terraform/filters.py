from __future__ import annotations

from typing import Tuple

# Centralized non-operational resource filtering logic

IAM_RESOURCE_PREFIXES: Tuple[str, ...] = (
    "google_project_iam_",
    "google_service_account_iam_",
    "google_cloud_run_service_iam_",
    "google_sql_user_iam_",
    "google_sql_database_iam_",
    "google_storage_bucket_iam_",
    "google_bigquery_dataset_iam_",
    "google_bigquery_table_iam_",
    "google_pubsub_topic_iam_",
    "google_pubsub_subscription_iam_",
    "google_folder_iam_",
    "google_organization_iam_",
    "google_artifact_registry_repository_iam_",
)

NON_OPERATIONAL_PREFIXES: Tuple[str, ...] = (
    "google_project_service",
    "google_service_networking_",
    "google_logging_",
    "google_monitoring_",
    "google_cloud_scheduler_",
    "google_cloud_tasks_",
)

NON_OPERATIONAL_EXACT = {
    "null_resource",
    "local_file",
    "local_exec",
    "external",
    # Cloud SQL metadata helpers
    "google_sql_database",
    "google_sql_user",
    # Common IAM variants that sometimes slip through
    "google_cloud_run_service_iam_member",
    # Artifact repos that dont consume runtime resources
    "google_artifact_registry_repository",
    # Static IP addresses - no active processing, minimal energy impact
    "google_compute_address",
    "google_compute_global_address",
    # Service accounts - identity/security only, no runtime processing
    "google_service_account",
}


def is_non_operational(res_type: str) -> bool:
    """Return True if the Terraform resource type is considered non-operational.

    Centralized single source of truth used by both the Terraform parser and
    the model builder so counts remain consistent across the pipeline.
    """
    if res_type in NON_OPERATIONAL_EXACT:
        return True

    for prefix in NON_OPERATIONAL_PREFIXES:
        if res_type.startswith(prefix):
            return True

    for iam_prefix in IAM_RESOURCE_PREFIXES:
        if res_type.startswith(iam_prefix):
            return True

    if "_iam_" in res_type and (
        res_type.endswith("_binding")
        or res_type.endswith("_member")
        or res_type.endswith("_policy")
    ):
        return True

    return False


