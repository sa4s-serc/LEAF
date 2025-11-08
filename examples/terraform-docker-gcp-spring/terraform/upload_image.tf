resource "null_resource" "upload_image" {
  triggers = {
    order = google_artifact_registry_repository.ar-aula-spring.id
  }
  provisioner "local-exec" {
    command = "gcloud auth configure-docker us-central1-docker.pkg.dev && docker push us-central1-docker.pkg.dev/leaf-test-2-spring-app/ar-aula-spring/springapp:latest"
  }
}
