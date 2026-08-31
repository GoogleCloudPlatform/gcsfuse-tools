# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# ==============================================================================
# Global & Provider Configuration Variables
# ==============================================================================

variable "project_id" {
  type        = string
  description = "The Google Cloud Project ID where services, service accounts, and schedulers will be provisioned."
}

variable "region" {
  type        = string
  description = "Google Cloud region for Cloud Run services and Cloud Scheduler jobs (e.g. us-central1)."
  default     = "us-central1"
}

variable "time_zone" {
  type        = string
  description = "Timezone for Cloud Scheduler cron execution (e.g. UTC, America/Los_Angeles)."
  default     = "UTC"
}

variable "dry_run" {
  type        = bool
  description = "Global default dry-run flag passed to Cloud Run environment and Cloud Scheduler invocation payloads."
  default     = false
}

variable "create_service_accounts" {
  type        = bool
  description = "Whether Terraform should create dedicated Service Accounts or bind to existing user-supplied SA emails."
  default     = true
}

variable "artifact_registry_repo" {
  type        = string
  description = "Artifact Registry Docker repository name where container images are hosted."
  default     = "gcsfuse-tools"
}

variable "ingress" {
  type        = string
  description = "Ingress traffic specification for Cloud Run services (e.g. INGRESS_TRAFFIC_ALL, INGRESS_TRAFFIC_INTERNAL_ONLY)."
  default     = "INGRESS_TRAFFIC_ALL"
}

# ==============================================================================
# Service Activation Toggles
# ==============================================================================

variable "enable_cluster_scaler" {
  type        = bool
  description = "Whether to deploy the GKE Cluster Scaler Cloud Run service, IAM roles, and Cloud Scheduler job."
  default     = true
}

variable "enable_reservation_cleaner" {
  type        = bool
  description = "Whether to deploy the GCE Reservation Cleaner Cloud Run service, IAM roles, and Cloud Scheduler job."
  default     = true
}

variable "enable_vm_stopper" {
  type        = bool
  description = "Whether to deploy the GCE VM Stopper Cloud Run service, IAM roles, and Cloud Scheduler job."
  default     = true
}

# ==============================================================================
# GKE Cluster Scaler Configuration
# ==============================================================================

variable "cluster_scaler_service_name" {
  type        = string
  description = "Cloud Run service name for GKE Cluster Scaler."
  default     = "cluster-scaler"
}

variable "cluster_scaler_schedule_name" {
  type        = string
  description = "Cloud Scheduler job name for GKE Cluster Scaler."
  default     = "cluster-scaler-scheduler"
}

variable "cluster_scaler_schedule" {
  type        = string
  description = "Cron schedule expression for GKE Cluster Scaler (default: daily at 02:00 UTC)."
  default     = "0 2 * * *"
}

variable "cluster_scaler_image" {
  type        = string
  description = "Fully qualified container image URL for cluster-scaler. If empty, defaults to Artifact Registry repository image."
  default     = ""
}

variable "cluster_scaler_idle_days_threshold" {
  type        = number
  description = "Days of inactivity before resizing idle GKE cluster node pools to size 0."
  default     = 7
}

variable "cluster_scaler_max_workers" {
  type        = number
  description = "Concurrency worker threads for GKE Cluster Scaler."
  default     = 10
}

variable "cluster_scaler_runner_sa_email" {
  type        = string
  description = "Existing Runtime Service Account email for Cluster Scaler (used if create_service_accounts is false)."
  default     = ""
}

variable "cluster_scaler_scheduler_sa_email" {
  type        = string
  description = "Existing Invoker Service Account email for Cluster Scaler (used if create_service_accounts is false)."
  default     = ""
}

# ==============================================================================
# GCE Reservation Cleaner Configuration
# ==============================================================================

variable "reservation_cleaner_service_name" {
  type        = string
  description = "Cloud Run service name for GCE Reservation Cleaner."
  default     = "gcsfuse-reservation-cleaner"
}

variable "reservation_cleaner_schedule_name" {
  type        = string
  description = "Cloud Scheduler job name for GCE Reservation Cleaner."
  default     = "gcsfuse-reservation-cleaner-scheduler"
}

variable "reservation_cleaner_schedule" {
  type        = string
  description = "Cron schedule expression for GCE Reservation Cleaner (default: monthly on 1st at 00:00 UTC)."
  default     = "0 0 1 * *"
}

variable "reservation_cleaner_image" {
  type        = string
  description = "Fully qualified container image URL for gcsfuse-reservation-cleaner. If empty, defaults to Artifact Registry repository image."
  default     = ""
}

variable "reservation_cleaner_delete_idle_days" {
  type        = number
  description = "Days of continuous zero-utilization before an unused reservation is considered stale for deletion."
  default     = 60
}

variable "reservation_cleaner_delete_never_used" {
  type        = bool
  description = "Whether to delete reservations that have never registered any utilization since creation."
  default     = true
}

variable "reservation_cleaner_max_age_days" {
  type        = number
  description = "Maximum age in days before an idle reservation is deleted."
  default     = 180
}

variable "reservation_cleaner_lookback_days" {
  type        = number
  description = "Historical metric lookback window in days for Cloud Monitoring evaluation."
  default     = 730
}

variable "reservation_cleaner_max_workers" {
  type        = number
  description = "Concurrency worker threads for GCE Reservation Cleaner."
  default     = 10
}

variable "reservation_cleaner_runner_sa_email" {
  type        = string
  description = "Existing Runtime Service Account email for Reservation Cleaner (used if create_service_accounts is false)."
  default     = ""
}

variable "reservation_cleaner_scheduler_sa_email" {
  type        = string
  description = "Existing Invoker Service Account email for Reservation Cleaner (used if create_service_accounts is false)."
  default     = ""
}

# ==============================================================================
# GCE VM Stopper Configuration
# ==============================================================================

variable "vm_stopper_service_name" {
  type        = string
  description = "Cloud Run service name for GCE VM Stopper."
  default     = "vm-stopper"
}

variable "vm_stopper_schedule_name" {
  type        = string
  description = "Cloud Scheduler job name for GCE VM Stopper."
  default     = "vm-stopper-scheduler"
}

variable "vm_stopper_schedule" {
  type        = string
  description = "Cron schedule expression for GCE VM Stopper (default: daily at 20:00 UTC)."
  default     = "0 20 * * *"
}

variable "vm_stopper_image" {
  type        = string
  description = "Fully qualified container image URL for vm-stopper. If empty, defaults to Artifact Registry repository image."
  default     = ""
}

variable "vm_stopper_idle_days_threshold" {
  type        = number
  description = "Days of login/network inactivity before stopping running standalone VMs."
  default     = 7
}

variable "vm_stopper_stopped_days_threshold" {
  type        = number
  description = "Days in STOPPED state before considering a VM for permanent deletion (if delete_stopped_vms is true)."
  default     = 90
}

variable "vm_stopper_delete_stopped_vms" {
  type        = bool
  description = "Whether to permanently delete VMs that have remained in STOPPED state longer than stopped_days_threshold."
  default     = false
}

variable "vm_stopper_max_workers" {
  type        = number
  description = "Concurrency worker threads for GCE VM Stopper."
  default     = 20
}

variable "vm_stopper_runner_sa_email" {
  type        = string
  description = "Existing Runtime Service Account email for VM Stopper (used if create_service_accounts is false)."
  default     = ""
}

variable "vm_stopper_scheduler_sa_email" {
  type        = string
  description = "Existing Invoker Service Account email for VM Stopper (used if create_service_accounts is false)."
  default     = ""
}
