provider google {
  project = var.project_id
  version = ">= 3.3"
}

variable "project_id" {
 description = "The Google Cloud Project ID"
 type = string
}