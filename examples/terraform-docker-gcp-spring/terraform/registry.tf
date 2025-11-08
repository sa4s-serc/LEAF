resource "google_artifact_registry_repository" "ar-aula-spring" {
  location      = var.region
  repository_id = "ar-aula-spring"
  format        = "DOCKER"
  description   = "Docker repository for aula spring app"
}
