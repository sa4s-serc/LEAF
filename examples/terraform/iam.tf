
resource "google_project_iam_member" "member-role" {
  depends_on = [google_service_account.cloudsql_service_account]

  for_each = toset([
    "roles/cloudsql.client",
    "roles/cloudsql.editor",
    "roles/cloudsql.admin",
    "roles/resourcemanager.projectIamAdmin",
    "roles/vpcaccess.serviceAgent"
  ])
  role    = each.key
  project = var.project_id
  member  = "serviceAccount:${google_service_account.cloudsql_service_account.email}"
}

/*
resource "google_service_account_key" "mykey" {
  service_account_id = google_service_account.cloudsql_service_account.name
  public_key_type    = "TYPE_X509_PEM_FILE"
}


resource "local_file" "sa_json_file" {
  content  = base64decode(google_service_account_key.mykey.private_key)
  filename = "${path.module}/cloudsql-sa-key.json"

}*/