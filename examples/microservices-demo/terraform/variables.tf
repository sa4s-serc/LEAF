# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

variable "gcp_project_id" {
  type        = string
  description = "The GCP project ID to apply this config to"
}

variable "name" {
  type        = string
  description = "Name given to the new GKE cluster"
  default     = "online-boutique"
}

variable "region" {
  type        = string
  description = "Region of the new GKE cluster"
  default     = "us-central1"
}

variable "zone" {
  type        = string
  description = "Zone of the new GKE cluster"
  default     = "us-central1-c"
}

variable "namespace" {
  type        = string
  description = "Kubernetes Namespace in which the Online Boutique resources are to be deployed"
  default     = "default"
}

variable "filepath_manifest" {
  type        = string
  description = "Path to Online Boutique's Kubernetes resources, written using Kustomize"
  default     = "../kustomize/"
}

variable "memorystore" {
  type        = bool
  description = "If true, Online Boutique's in-cluster Redis cache will be replaced with a Google Cloud Memorystore Redis cache"
}

variable "node_count" {
  type        = number
  description = "Initial number of nodes to run in the default node pool"
  default     = 1
}

variable "node_machine_type" {
  type        = string
  description = "GCE machine type to use for worker nodes in the default node pool"
  default     = "e2-standard-2"
}

variable "node_disk_size_gb" {
  type        = number
  description = "Boot disk size (in GB) for each node in the default node pool"
  default     = 30
}

variable "min_node_count" {
  type        = number
  description = "Minimum number of nodes for the GKE node pool autoscaler"
  default     = 1
}

variable "max_node_count" {
  type        = number
  description = "Maximum number of nodes for the GKE node pool autoscaler"
  default     = 4
}
