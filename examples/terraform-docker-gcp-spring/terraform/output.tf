output "service-url" {
  value = google_cloud_run_service.cr-aula-spring.status[0].url
}
