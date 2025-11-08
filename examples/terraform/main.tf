/***
********************public IP******************************
*****************************************************************
*/

resource "google_sql_database_instance" "my_public_instance" {
  project          = var.project_id
  name             = "main-instance"
  database_version = "POSTGRES_15"
  region           = var.region

  deletion_protection = false

  settings {
    # Second-generation instance tiers are based on the machine
    # type. See argument reference below.
    tier = "db-custom-4-8192"
  }
}

resource "google_sql_database" "my-database1" {
  depends_on      = [google_sql_database_instance.my_public_instance]
  project         = var.project_id
  name            = "my-database1"
  instance        = google_sql_database_instance.my_public_instance.name
  deletion_policy = "DELETE"
}

resource "google_sql_user" "myuser1" {

  depends_on = [google_sql_database_instance.my_public_instance]

  project  = var.project_id
  name     = "henry"
  password = "mypassword"
  instance = google_sql_database_instance.my_public_instance.name
}


/***
********************private IP VPC******************************
*****************************************************************
*/


resource "google_sql_database_instance" "my_private_instance" {

  depends_on = [google_service_networking_connection.private_vpc_connection]


  project          = var.project_id
  name             = "private-instance"
  region           = var.region
  database_version = "POSTGRES_15"

  deletion_protection = false

  settings {
    tier = "db-custom-4-8192"
    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.nw1-vpc.self_link
      enable_private_path_for_google_cloud_services = true
    }
  }
}

resource "google_sql_database" "my-database2" {
  depends_on = [google_sql_database_instance.my_private_instance]

  project         = var.project_id
  name            = "my-database2"
  instance        = google_sql_database_instance.my_private_instance.name
  deletion_policy = "DELETE"
}

resource "google_sql_user" "myuser2" {

  depends_on = [google_sql_database_instance.my_private_instance]

  project  = var.project_id
  name     = "henry"
  password = "mypassword"
  instance = google_sql_database_instance.my_private_instance.name
}



resource google_cloud_run_service "spring_boot_terraform_cloud_run_service_ip_demo" {
  name = "spring-boot-hello-service"
  location = "us-central1"

  template {
    spec {
      containers {
        image = "us-central1-docker.pkg.dev/leaf-test1-onlineboutique/spring-apps/spring-boot-terraform-cloud-run-demo:latest"
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

