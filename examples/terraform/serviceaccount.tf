resource "google_service_account" "cloudsql_service_account" {
  project      = var.project_id
  account_id   = "cloudsql-service-account-id"
  display_name = "Service Account for Cloud SQL"
}

output "service_account_email" {
  value = google_service_account.cloudsql_service_account.email
}
