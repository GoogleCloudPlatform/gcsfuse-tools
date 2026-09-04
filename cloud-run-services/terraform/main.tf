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

terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ==============================================================================
# Local Values: Dynamic Image & Service Account Resolutions
# ==============================================================================

locals {
  # Resolve container image URLs
  cluster_scaler_image      = var.cluster_scaler_image != "" ? var.cluster_scaler_image : "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repo}/${var.cluster_scaler_service_name}:latest"
  reservation_cleaner_image = var.reservation_cleaner_image != "" ? var.reservation_cleaner_image : "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repo}/${var.reservation_cleaner_service_name}:latest"
  vm_stopper_image          = var.vm_stopper_image != "" ? var.vm_stopper_image : "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repo}/${var.vm_stopper_service_name}:latest"

  # Resolve runtime Service Account emails
  cluster_scaler_runner_sa      = var.create_service_accounts ? (length(google_service_account.cluster_scaler_runner) > 0 ? google_service_account.cluster_scaler_runner[0].email : "") : (var.cluster_scaler_runner_sa_email != "" ? var.cluster_scaler_runner_sa_email : "cluster-scaler-sa@${var.project_id}.iam.gserviceaccount.com")
  reservation_cleaner_runner_sa = var.create_service_accounts ? (length(google_service_account.reservation_cleaner_runner) > 0 ? google_service_account.reservation_cleaner_runner[0].email : "") : (var.reservation_cleaner_runner_sa_email != "" ? var.reservation_cleaner_runner_sa_email : "gcsfuse-res-cleaner-sa@${var.project_id}.iam.gserviceaccount.com")
  vm_stopper_runner_sa          = var.create_service_accounts ? (length(google_service_account.vm_stopper_runner) > 0 ? google_service_account.vm_stopper_runner[0].email : "") : (var.vm_stopper_runner_sa_email != "" ? var.vm_stopper_runner_sa_email : "vm-stopper-sa@${var.project_id}.iam.gserviceaccount.com")

  # Resolve scheduler invocation Service Account emails
  cluster_scaler_scheduler_sa      = var.create_service_accounts ? (length(google_service_account.cluster_scaler_scheduler) > 0 ? google_service_account.cluster_scaler_scheduler[0].email : "") : (var.cluster_scaler_scheduler_sa_email != "" ? var.cluster_scaler_scheduler_sa_email : "cluster-scaler-sched@${var.project_id}.iam.gserviceaccount.com")
  reservation_cleaner_scheduler_sa = var.create_service_accounts ? (length(google_service_account.reservation_cleaner_scheduler) > 0 ? google_service_account.reservation_cleaner_scheduler[0].email : "") : (var.reservation_cleaner_scheduler_sa_email != "" ? var.reservation_cleaner_scheduler_sa_email : "gcsfuse-res-cleaner-sched@${var.project_id}.iam.gserviceaccount.com")
  vm_stopper_scheduler_sa          = var.create_service_accounts ? (length(google_service_account.vm_stopper_scheduler) > 0 ? google_service_account.vm_stopper_scheduler[0].email : "") : (var.vm_stopper_scheduler_sa_email != "" ? var.vm_stopper_scheduler_sa_email : "vm-stopper-sched@${var.project_id}.iam.gserviceaccount.com")

  # Least-privilege IAM roles per service runner
  cluster_scaler_runner_roles = [
    "roles/container.admin",
    "roles/logging.logWriter",
  ]
  reservation_cleaner_runner_roles = [
    "roles/compute.instanceAdmin.v1",
    "roles/monitoring.viewer",
    "roles/logging.logWriter",
  ]
  vm_stopper_runner_roles = [
    "roles/compute.instanceAdmin.v1",
    "roles/logging.viewer",
    "roles/logging.logWriter",
  ]
}

# ==============================================================================
# Service Account Resources
# ==============================================================================

# Cluster Scaler Service Accounts
resource "google_service_account" "cluster_scaler_runner" {
  count        = (var.create_service_accounts && var.enable_cluster_scaler) ? 1 : 0
  account_id   = "cluster-scaler-sa"
  display_name = "GKE Cluster Scaler Runtime Service Account"
  description  = "Dedicated runtime identity for GKE Cluster Scaler Cloud Run service"
  project      = var.project_id
}

resource "google_service_account" "cluster_scaler_scheduler" {
  count        = (var.create_service_accounts && var.enable_cluster_scaler) ? 1 : 0
  account_id   = "cluster-scaler-sched"
  display_name = "GKE Cluster Scaler Cloud Scheduler Invoker Service Account"
  description  = "Dedicated invoker identity for GKE Cluster Scaler Cloud Scheduler triggers"
  project      = var.project_id
}

# Reservation Cleaner Service Accounts
resource "google_service_account" "reservation_cleaner_runner" {
  count        = (var.create_service_accounts && var.enable_reservation_cleaner) ? 1 : 0
  account_id   = "gcsfuse-res-cleaner-sa"
  display_name = "GCE Reservation Cleaner Runtime Service Account"
  description  = "Dedicated runtime identity for GCE Reservation Cleaner Cloud Run service"
  project      = var.project_id
}

resource "google_service_account" "reservation_cleaner_scheduler" {
  count        = (var.create_service_accounts && var.enable_reservation_cleaner) ? 1 : 0
  account_id   = "gcsfuse-res-cleaner-sched"
  display_name = "GCE Reservation Cleaner Cloud Scheduler Invoker Service Account"
  description  = "Dedicated invoker identity for GCE Reservation Cleaner Cloud Scheduler triggers"
  project      = var.project_id
}

# VM Stopper Service Accounts
resource "google_service_account" "vm_stopper_runner" {
  count        = (var.create_service_accounts && var.enable_vm_stopper) ? 1 : 0
  account_id   = "vm-stopper-sa"
  display_name = "GCE VM Stopper Runtime Service Account"
  description  = "Dedicated runtime identity for GCE VM Stopper Cloud Run service"
  project      = var.project_id
}

resource "google_service_account" "vm_stopper_scheduler" {
  count        = (var.create_service_accounts && var.enable_vm_stopper) ? 1 : 0
  account_id   = "vm-stopper-sched"
  display_name = "GCE VM Stopper Cloud Scheduler Invoker Service Account"
  description  = "Dedicated invoker identity for GCE VM Stopper Cloud Scheduler triggers"
  project      = var.project_id
}

# ==============================================================================
# Least-Privilege IAM Role Bindings (Project Level)
# ==============================================================================

resource "google_project_iam_member" "cluster_scaler_runner" {
  count   = var.enable_cluster_scaler ? length(local.cluster_scaler_runner_roles) : 0
  project = var.project_id
  role    = local.cluster_scaler_runner_roles[count.index]
  member  = "serviceAccount:${local.cluster_scaler_runner_sa}"
}

resource "google_project_iam_member" "reservation_cleaner_runner" {
  count   = var.enable_reservation_cleaner ? length(local.reservation_cleaner_runner_roles) : 0
  project = var.project_id
  role    = local.reservation_cleaner_runner_roles[count.index]
  member  = "serviceAccount:${local.reservation_cleaner_runner_sa}"
}

resource "google_project_iam_member" "vm_stopper_runner" {
  count   = var.enable_vm_stopper ? length(local.vm_stopper_runner_roles) : 0
  project = var.project_id
  role    = local.vm_stopper_runner_roles[count.index]
  member  = "serviceAccount:${local.vm_stopper_runner_sa}"
}

# ==============================================================================
# Cloud Run v2 Services
# ==============================================================================

# 1. GKE Cluster Scaler Service
resource "google_cloud_run_v2_service" "cluster_scaler" {
  count    = var.enable_cluster_scaler ? 1 : 0
  name     = var.cluster_scaler_service_name
  location = var.region
  project  = var.project_id
  ingress  = var.ingress

  template {
    service_account = local.cluster_scaler_runner_sa
    timeout         = "540s"
    scaling {
      max_instance_count = 10
    }
    containers {
      image = local.cluster_scaler_image
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "IDLE_DAYS_THRESHOLD"
        value = tostring(var.cluster_scaler_idle_days_threshold)
      }
      env {
        name  = "DRY_RUN"
        value = tostring(var.dry_run)
      }
      env {
        name  = "MAX_WORKERS"
        value = tostring(var.cluster_scaler_max_workers)
      }
    }
  }

  depends_on = [google_project_iam_member.cluster_scaler_runner]
}

# 2. GCE Reservation Cleaner Service
resource "google_cloud_run_v2_service" "reservation_cleaner" {
  count    = var.enable_reservation_cleaner ? 1 : 0
  name     = var.reservation_cleaner_service_name
  location = var.region
  project  = var.project_id
  ingress  = var.ingress

  template {
    service_account = local.reservation_cleaner_runner_sa
    timeout         = "540s"
    scaling {
      max_instance_count = 10
    }
    containers {
      image = local.reservation_cleaner_image
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "DELETE_IDLE_DAYS"
        value = tostring(var.reservation_cleaner_delete_idle_days)
      }
      env {
        name  = "DELETE_NEVER_USED"
        value = tostring(var.reservation_cleaner_delete_never_used)
      }
      env {
        name  = "MAX_AGE_DAYS"
        value = tostring(var.reservation_cleaner_max_age_days)
      }
      env {
        name  = "LOOKBACK_DAYS"
        value = tostring(var.reservation_cleaner_lookback_days)
      }
      env {
        name  = "DRY_RUN"
        value = tostring(var.dry_run)
      }
      env {
        name  = "MAX_WORKERS"
        value = tostring(var.reservation_cleaner_max_workers)
      }
    }
  }

  depends_on = [google_project_iam_member.reservation_cleaner_runner]
}

# 3. GCE VM Stopper Service
resource "google_cloud_run_v2_service" "vm_stopper" {
  count    = var.enable_vm_stopper ? 1 : 0
  name     = var.vm_stopper_service_name
  location = var.region
  project  = var.project_id
  ingress  = var.ingress

  template {
    service_account = local.vm_stopper_runner_sa
    timeout         = "540s"
    scaling {
      max_instance_count = 10
    }
    containers {
      image = local.vm_stopper_image
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "IDLE_DAYS_THRESHOLD"
        value = tostring(var.vm_stopper_idle_days_threshold)
      }
      env {
        name  = "STOPPED_DAYS_THRESHOLD"
        value = tostring(var.vm_stopper_stopped_days_threshold)
      }
      env {
        name  = "DELETE_STOPPED_VMS"
        value = tostring(var.vm_stopper_delete_stopped_vms)
      }
      env {
        name  = "DRY_RUN"
        value = tostring(var.dry_run)
      }
      env {
        name  = "MAX_WORKERS"
        value = tostring(var.vm_stopper_max_workers)
      }
    }
  }

  depends_on = [google_project_iam_member.vm_stopper_runner]
}

# ==============================================================================
# Cloud Run Invoker IAM Bindings (Service Level)
# ==============================================================================

resource "google_cloud_run_v2_service_iam_member" "cluster_scaler_invoker" {
  count    = var.enable_cluster_scaler ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.cluster_scaler[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.cluster_scaler_scheduler_sa}"
}

resource "google_cloud_run_v2_service_iam_member" "reservation_cleaner_invoker" {
  count    = var.enable_reservation_cleaner ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.reservation_cleaner[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.reservation_cleaner_scheduler_sa}"
}

resource "google_cloud_run_v2_service_iam_member" "vm_stopper_invoker" {
  count    = var.enable_vm_stopper ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.vm_stopper[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.vm_stopper_scheduler_sa}"
}

# ==============================================================================
# Cloud Scheduler HTTP Trigger Jobs
# ==============================================================================

# 1. GKE Cluster Scaler Scheduler Job
resource "google_cloud_scheduler_job" "cluster_scaler" {
  count       = var.enable_cluster_scaler ? 1 : 0
  name        = var.cluster_scaler_schedule_name
  description = "Trigger GKE Cluster Scaler to evaluate and resize idle GKE cluster node pools"
  schedule    = var.cluster_scaler_schedule
  time_zone   = var.time_zone
  project     = var.project_id
  region      = var.region

  http_target {
    http_method = "POST"
    uri         = google_cloud_run_v2_service.cluster_scaler[0].uri
    body = jsonencode({
      project             = var.project_id
      idle_days_threshold = var.cluster_scaler_idle_days_threshold
      dry_run             = var.dry_run
    })
    headers = {
      "Content-Type" = "application/json"
    }

    oidc_token {
      service_account_email = local.cluster_scaler_scheduler_sa
      audience              = google_cloud_run_v2_service.cluster_scaler[0].uri
    }
  }

  depends_on = [google_cloud_run_v2_service_iam_member.cluster_scaler_invoker]
}

# 2. GCE Reservation Cleaner Scheduler Job
resource "google_cloud_scheduler_job" "reservation_cleaner" {
  count       = var.enable_reservation_cleaner ? 1 : 0
  name        = var.reservation_cleaner_schedule_name
  description = "Trigger GCE Reservation Cleaner to evaluate and delete stale compute reservations"
  schedule    = var.reservation_cleaner_schedule
  time_zone   = var.time_zone
  project     = var.project_id
  region      = var.region

  http_target {
    http_method = "POST"
    uri         = google_cloud_run_v2_service.reservation_cleaner[0].uri
    body = jsonencode({
      project           = var.project_id
      delete_idle_days  = var.reservation_cleaner_delete_idle_days
      delete_never_used = var.reservation_cleaner_delete_never_used
      max_age_days      = var.reservation_cleaner_max_age_days
      lookback_days     = var.reservation_cleaner_lookback_days
      dry_run           = var.dry_run
    })
    headers = {
      "Content-Type" = "application/json"
    }

    oidc_token {
      service_account_email = local.reservation_cleaner_scheduler_sa
      audience              = google_cloud_run_v2_service.reservation_cleaner[0].uri
    }
  }

  depends_on = [google_cloud_run_v2_service_iam_member.reservation_cleaner_invoker]
}

# 3. GCE VM Stopper Scheduler Job
resource "google_cloud_scheduler_job" "vm_stopper" {
  count       = var.enable_vm_stopper ? 1 : 0
  name        = var.vm_stopper_schedule_name
  description = "Trigger GCE VM Stopper to evaluate and stop idle compute engine instances"
  schedule    = var.vm_stopper_schedule
  time_zone   = var.time_zone
  project     = var.project_id
  region      = var.region

  http_target {
    http_method = "POST"
    uri         = google_cloud_run_v2_service.vm_stopper[0].uri
    body = jsonencode({
      project                = var.project_id
      idle_days_threshold    = var.vm_stopper_idle_days_threshold
      stopped_days_threshold = var.vm_stopper_stopped_days_threshold
      delete_stopped_vms     = var.vm_stopper_delete_stopped_vms
      dry_run                = var.dry_run
    })
    headers = {
      "Content-Type" = "application/json"
    }

    oidc_token {
      service_account_email = local.vm_stopper_scheduler_sa
      audience              = google_cloud_run_v2_service.vm_stopper[0].uri
    }
  }

  depends_on = [google_cloud_run_v2_service_iam_member.vm_stopper_invoker]
}
