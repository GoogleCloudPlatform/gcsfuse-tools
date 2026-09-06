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
# Individual Service Outputs: GKE Cluster Scaler
# ==============================================================================

output "cluster_scaler_service_url" {
  description = "HTTPS URL of the deployed GKE Cluster Scaler Cloud Run service (null if disabled)."
  value       = one(google_cloud_run_v2_service.cluster_scaler[*].uri)
}

output "cluster_scaler_scheduler_job_name" {
  description = "Resource name of the GKE Cluster Scaler Cloud Scheduler job (null if disabled)."
  value       = one(google_cloud_scheduler_job.cluster_scaler[*].name)
}

output "cluster_scaler_runner_sa_email" {
  description = "Runtime Service Account email used by GKE Cluster Scaler (null if disabled)."
  value       = var.enable_cluster_scaler ? local.cluster_scaler_runner_sa : null
}

output "cluster_scaler_scheduler_sa_email" {
  description = "Invocation Service Account email used by GKE Cluster Scaler Cloud Scheduler (null if disabled)."
  value       = var.enable_cluster_scaler ? local.cluster_scaler_scheduler_sa : null
}

# ==============================================================================
# Individual Service Outputs: GCE Reservation Cleaner
# ==============================================================================

output "reservation_cleaner_service_url" {
  description = "HTTPS URL of the deployed GCE Reservation Cleaner Cloud Run service (null if disabled)."
  value       = one(google_cloud_run_v2_service.reservation_cleaner[*].uri)
}

output "reservation_cleaner_scheduler_job_name" {
  description = "Resource name of the GCE Reservation Cleaner Cloud Scheduler job (null if disabled)."
  value       = one(google_cloud_scheduler_job.reservation_cleaner[*].name)
}

output "reservation_cleaner_runner_sa_email" {
  description = "Runtime Service Account email used by GCE Reservation Cleaner (null if disabled)."
  value       = var.enable_reservation_cleaner ? local.reservation_cleaner_runner_sa : null
}

output "reservation_cleaner_scheduler_sa_email" {
  description = "Invocation Service Account email used by GCE Reservation Cleaner Cloud Scheduler (null if disabled)."
  value       = var.enable_reservation_cleaner ? local.reservation_cleaner_scheduler_sa : null
}

# ==============================================================================
# Individual Service Outputs: GCE VM Stopper
# ==============================================================================

output "vm_stopper_service_url" {
  description = "HTTPS URL of the deployed GCE VM Stopper Cloud Run service (null if disabled)."
  value       = one(google_cloud_run_v2_service.vm_stopper[*].uri)
}

output "vm_stopper_scheduler_job_name" {
  description = "Resource name of the GCE VM Stopper Cloud Scheduler job (null if disabled)."
  value       = one(google_cloud_scheduler_job.vm_stopper[*].name)
}

output "vm_stopper_runner_sa_email" {
  description = "Runtime Service Account email used by GCE VM Stopper (null if disabled)."
  value       = var.enable_vm_stopper ? local.vm_stopper_runner_sa : null
}

output "vm_stopper_scheduler_sa_email" {
  description = "Invocation Service Account email used by GCE VM Stopper Cloud Scheduler (null if disabled)."
  value       = var.enable_vm_stopper ? local.vm_stopper_scheduler_sa : null
}

# ==============================================================================
# Composite Lookup Maps
# ==============================================================================

output "service_urls" {
  description = "Map of enabled service names to their deployed Cloud Run HTTPS URLs."
  value = {
    for k, v in {
      "cluster-scaler"              = one(google_cloud_run_v2_service.cluster_scaler[*].uri)
      "gcsfuse-reservation-cleaner" = one(google_cloud_run_v2_service.reservation_cleaner[*].uri)
      "vm-stopper"                  = one(google_cloud_run_v2_service.vm_stopper[*].uri)
    } : k => v if v != null
  }
}

output "scheduler_jobs" {
  description = "Map of enabled service names to their Cloud Scheduler job names."
  value = {
    for k, v in {
      "cluster-scaler"              = one(google_cloud_scheduler_job.cluster_scaler[*].name)
      "gcsfuse-reservation-cleaner" = one(google_cloud_scheduler_job.reservation_cleaner[*].name)
      "vm-stopper"                  = one(google_cloud_scheduler_job.vm_stopper[*].name)
    } : k => v if v != null
  }
}

output "runner_service_accounts" {
  description = "Map of enabled service names to their Runtime Service Account emails."
  value = {
    for k, v in {
      "cluster-scaler"              = var.enable_cluster_scaler ? local.cluster_scaler_runner_sa : null
      "gcsfuse-reservation-cleaner" = var.enable_reservation_cleaner ? local.reservation_cleaner_runner_sa : null
      "vm-stopper"                  = var.enable_vm_stopper ? local.vm_stopper_runner_sa : null
    } : k => v if v != null
  }
}

output "scheduler_service_accounts" {
  description = "Map of enabled service names to their Scheduler Invoker Service Account emails."
  value = {
    for k, v in {
      "cluster-scaler"              = var.enable_cluster_scaler ? local.cluster_scaler_scheduler_sa : null
      "gcsfuse-reservation-cleaner" = var.enable_reservation_cleaner ? local.reservation_cleaner_scheduler_sa : null
      "vm-stopper"                  = var.enable_vm_stopper ? local.vm_stopper_scheduler_sa : null
    } : k => v if v != null
  }
}
