resource google_project_service "google_run_service" {
  service = "run.googleapis.com"
}

resource google_cloud_run_service "spring_boot_terraform_cloud_run_service" {
  name = "spring-boot-hello-service"
  location = "us-central1"

  template {
    metadata {
      annotations = {
        "autoscaling.knative.dev/minScale" = "1"
        "autoscaling.knative.dev/maxScale" = "10"
      }
    }
    spec {
      containers {
        image = "us-central1-docker.pkg.dev/leaf-test-3/spring-apps/spring-boot-terraform-cloud-run-demo:latest"
      }
    }
  }

  traffic {
    percent = 100
    latest_revision = true
  }

  depends_on = [google_project_service.google_run_service]
}

data google_iam_policy "iam_policy_all_users" {
  binding {
    role = "roles/run.invoker"
    members = [
      "allUsers",
    ]
  }
}

resource google_cloud_run_service_iam_policy "hello_all_users" {
  service = google_cloud_run_service.spring_boot_terraform_cloud_run_service.name
  location = google_cloud_run_service.spring_boot_terraform_cloud_run_service.location
  policy_data = data.google_iam_policy.iam_policy_all_users.policy_data
}

output "url" {
  value = "${google_cloud_run_service.spring_boot_terraform_cloud_run_service.status[0].url}"
}