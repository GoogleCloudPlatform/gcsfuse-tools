# Terraform Module: Cloud Run Automation Services & Cloud Scheduler

Declarative Infrastructure-as-Code (IaC) Terraform module for provisioning and orchestrating the `gcsfuse-tools` automation services suite (`cluster-scaler`, `gcsfuse-reservation-cleaner`, and `vm-stopper`) on Google Cloud Platform.

---

## 1. Overview & Architecture

This module manages the complete lifecycle of:
1. **Google Cloud Run v2 Services**: Fully managed, auto-scaling, containerized execution runtime with enforced authentication (`--no-allow-unauthenticated`), custom timeouts (540s), and resource limits (512Mi / 1 vCPU).
2. **Google Cloud Scheduler Triggers**: Periodic cron jobs triggering Cloud Run services via authenticated HTTPS POST requests with OIDC identity tokens and JSON payloads.
3. **Dedicated Service Accounts & IAM Roles**: Principle of least privilege applied across Runtime Service Accounts (Runner SAs) and Cloud Scheduler Invoker identities (`roles/run.invoker`).

```
                              ┌────────────────────────────────────────┐
                              │          Google Cloud Project          │
                              │                                        │
┌─────────────────────────┐   │   ┌────────────────────────────────┐   │
│  Cloud Scheduler Jobs   │───┼──▶│     Cloud Run v2 Services      │   │
│                         │   │   │                                │   │
│ • cluster-scaler (0 2)  │   │   │ • cluster-scaler (Flask)       │───┼──▶ GKE APIs
│ • res-cleaner   (0 0 1) │   │   │ • res-cleaner (Flask)          │───┼──▶ Compute / Monitoring
│ • vm-stopper    (0 20)  │   │   │ • vm-stopper (Flask)           │───┼──▶ Compute / Logging
└─────────────────────────┘   │   └────────────────────────────────┘   │
       │                      │                   ▲                    │
       │ OIDC Token Auth      │                   │ Runtime Roles      │
       ▼                      │                   │                    │
┌─────────────────────────┐   │   ┌────────────────────────────────┐   │
│ Cloud Scheduler SAs     │───┼───│       Runner Service SAs       │   │
│ roles/run.invoker       │   │   │ Least Privilege Admin/Viewer   │   │
└─────────────────────────┘   │   └────────────────────────────────┘   │
                              └────────────────────────────────────────┘
```

---

## 2. Prerequisites & Provider Setup

- **Terraform CLI**: version `>= 1.0.0`
- **Google Cloud Provider**: version `>= 5.0, < 7.0`
- **Google Cloud APIs Enabled**:
  - `run.googleapis.com` (Cloud Run Admin API)
  - `cloudscheduler.googleapis.com` (Cloud Scheduler API)
  - `iam.googleapis.com` (Identity and Access Management API)
  - `artifactregistry.googleapis.com` (Artifact Registry API)
  - `container.googleapis.com` (Kubernetes Engine API - for cluster-scaler)
  - `compute.googleapis.com` (Compute Engine API - for cleaner & stopper)
  - `monitoring.googleapis.com` (Cloud Monitoring API - for cleaner)
  - `logging.googleapis.com` (Cloud Logging API - for vm-stopper & logs)

---

## 3. Quickstart

### Step 1: Prepare Variables
Create your `terraform.tfvars` from the provided example:
```bash
cp terraform.tfvars.example terraform.tfvars
```
Edit `terraform.tfvars` and specify your `project_id` and desired options:
```hcl
project_id = "my-gcp-project-id"
region     = "us-central1"
dry_run    = false
```

### Step 2: Initialize & Validate
```bash
terraform init
terraform validate
```

### Step 3: Plan & Apply
```bash
terraform plan -out=tfplan
terraform apply tfplan
```

---

## 4. Input Variables

| Name | Type | Default | Required | Description |
| :--- | :--- | :--- | :---: | :--- |
| `project_id` | `string` | `n/a` | **Yes** | Target Google Cloud Project ID where services and schedulers are provisioned. |
| `region` | `string` | `"us-central1"` | No | Google Cloud region for Cloud Run services and Cloud Scheduler jobs. |
| `time_zone` | `string` | `"UTC"` | No | Timezone for Cloud Scheduler cron execution (e.g. `UTC`, `America/Los_Angeles`). |
| `dry_run` | `bool` | `false` | No | Global dry-run flag passed to Cloud Run environment and scheduler payloads. |
| `create_service_accounts` | `bool` | `true` | No | Whether to create dedicated Service Accounts or use supplied existing SA emails. |
| `artifact_registry_repo` | `string` | `"gcsfuse-tools"` | No | Artifact Registry Docker repository name where container images reside. |
| `ingress` | `string` | `"INGRESS_TRAFFIC_ALL"` | No | Ingress traffic specification for Cloud Run (`INGRESS_TRAFFIC_ALL` or `INGRESS_TRAFFIC_INTERNAL_ONLY`). |
| `enable_cluster_scaler` | `bool` | `true` | No | Toggle deployment of GKE Cluster Scaler service, IAM, and scheduler. |
| `enable_reservation_cleaner` | `bool` | `true` | No | Toggle deployment of GCE Reservation Cleaner service, IAM, and scheduler. |
| `enable_vm_stopper` | `bool` | `true` | No | Toggle deployment of GCE VM Stopper service, IAM, and scheduler. |
| `cluster_scaler_service_name` | `string` | `"cluster-scaler"` | No | Cloud Run service name for GKE Cluster Scaler. |
| `cluster_scaler_schedule_name` | `string` | `"cluster-scaler-scheduler"` | No | Cloud Scheduler job name for GKE Cluster Scaler. |
| `cluster_scaler_schedule` | `string` | `"0 2 * * *"` | No | Cron schedule for GKE Cluster Scaler (Daily at 02:00 UTC). |
| `cluster_scaler_image` | `string` | `""` | No | Container image URL override. Defaults to Artifact Registry path if empty. |
| `cluster_scaler_idle_days_threshold` | `number` | `7` | No | Days of inactivity before resizing idle node pools to size 0. |
| `cluster_scaler_max_workers` | `number` | `10` | No | Concurrency worker threads for GKE Cluster Scaler. |
| `cluster_scaler_runner_sa_email` | `string` | `""` | No | Existing Runtime SA email (used when `create_service_accounts = false`). |
| `cluster_scaler_scheduler_sa_email` | `string` | `""` | No | Existing Invoker SA email (used when `create_service_accounts = false`). |
| `reservation_cleaner_service_name` | `string` | `"gcsfuse-reservation-cleaner"` | No | Cloud Run service name for GCE Reservation Cleaner. |
| `reservation_cleaner_schedule_name` | `string` | `"gcsfuse-reservation-cleaner-scheduler"` | No | Cloud Scheduler job name for GCE Reservation Cleaner. |
| `reservation_cleaner_schedule` | `string` | `"0 0 1 * *"` | No | Cron schedule for Reservation Cleaner (Monthly on 1st at 00:00 UTC). |
| `reservation_cleaner_image` | `string` | `""` | No | Container image URL override for Reservation Cleaner. |
| `reservation_cleaner_delete_idle_days` | `number` | `60` | No | Days of continuous 0-utilization before an unused reservation is deleted. |
| `reservation_cleaner_delete_never_used` | `bool` | `true` | No | Whether to delete reservations never used since creation. |
| `reservation_cleaner_max_age_days` | `number` | `180` | No | Maximum age in days before an idle reservation is deleted. |
| `reservation_cleaner_lookback_days` | `number` | `730` | No | Cloud Monitoring historical metric lookback window (days). |
| `reservation_cleaner_max_workers` | `number` | `10` | No | Concurrency worker threads for Reservation Cleaner. |
| `reservation_cleaner_runner_sa_email` | `string` | `""` | No | Existing Runtime SA email for Reservation Cleaner. |
| `reservation_cleaner_scheduler_sa_email` | `string` | `""` | No | Existing Invoker SA email for Reservation Cleaner. |
| `vm_stopper_service_name` | `string` | `"vm-stopper"` | No | Cloud Run service name for GCE VM Stopper. |
| `vm_stopper_schedule_name` | `string` | `"vm-stopper-scheduler"` | No | Cloud Scheduler job name for GCE VM Stopper. |
| `vm_stopper_schedule` | `string` | `"0 20 * * *"` | No | Cron schedule for VM Stopper (Daily at 20:00 UTC). |
| `vm_stopper_image` | `string` | `""` | No | Container image URL override for VM Stopper. |
| `vm_stopper_idle_days_threshold` | `number` | `7` | No | Inactivity days before stopping running standalone VMs. |
| `vm_stopper_stopped_days_threshold` | `number` | `90` | No | Days in STOPPED state before considering a VM for deletion. |
| `vm_stopper_delete_stopped_vms` | `bool` | `false` | No | Whether to permanently delete VMs stopped longer than threshold. |
| `vm_stopper_max_workers` | `number` | `20` | No | Concurrency worker threads for VM Stopper. |
| `vm_stopper_runner_sa_email` | `string` | `""` | No | Existing Runtime SA email for VM Stopper. |
| `vm_stopper_scheduler_sa_email` | `string` | `""` | No | Existing Invoker SA email for VM Stopper. |

---

## 5. Outputs

| Name | Type | Description |
| :--- | :--- | :--- |
| `cluster_scaler_service_url` | `string` | HTTPS URL of the deployed `cluster-scaler` Cloud Run service. |
| `cluster_scaler_scheduler_job_name` | `string` | Resource name of the `cluster-scaler` Cloud Scheduler job. |
| `cluster_scaler_runner_sa_email` | `string` | Runtime Service Account email used by `cluster-scaler`. |
| `cluster_scaler_scheduler_sa_email` | `string` | Invocation Service Account email used by `cluster-scaler-scheduler`. |
| `reservation_cleaner_service_url` | `string` | HTTPS URL of the deployed `gcsfuse-reservation-cleaner` Cloud Run service. |
| `reservation_cleaner_scheduler_job_name` | `string` | Resource name of the `gcsfuse-reservation-cleaner` Cloud Scheduler job. |
| `reservation_cleaner_runner_sa_email` | `string` | Runtime Service Account email used by `reservation-cleaner`. |
| `reservation_cleaner_scheduler_sa_email` | `string` | Invocation Service Account email used by `reservation-cleaner-scheduler`. |
| `vm_stopper_service_url` | `string` | HTTPS URL of the deployed `vm-stopper` Cloud Run service. |
| `vm_stopper_scheduler_job_name` | `string` | Resource name of the `vm-stopper` Cloud Scheduler job. |
| `vm_stopper_runner_sa_email` | `string` | Runtime Service Account email used by `vm-stopper`. |
| `vm_stopper_scheduler_sa_email` | `string` | Invocation Service Account email used by `vm-stopper-scheduler`. |
| `service_urls` | `map(string)` | Map of enabled service names to their deployed Cloud Run HTTPS URLs. |
| `scheduler_jobs` | `map(string)` | Map of enabled service names to their Cloud Scheduler job names. |
| `runner_service_accounts` | `map(string)` | Map of enabled service names to their Runtime Service Account emails. |
| `scheduler_service_accounts` | `map(string)` | Map of enabled service names to their Scheduler Invoker Service Account emails. |

---

## 6. Deployment Recipes

### Recipe 1: Deploy Only GKE Cluster Scaler
```hcl
project_id                 = "my-gcp-project"
enable_cluster_scaler      = true
enable_reservation_cleaner = false
enable_vm_stopper          = false

cluster_scaler_idle_days_threshold = 14
cluster_scaler_schedule            = "0 1 * * *" # Run at 01:00 UTC daily
```

### Recipe 2: Safe Dry-Run Deployment (All Services)
Deploy the full suite in evaluation-only dry-run mode to inspect potential actions without modifying any infrastructure:
```hcl
project_id = "my-gcp-project"
dry_run    = true
```

### Recipe 3: Using Pre-Existing Enterprise Service Accounts
If corporate security mandates using centrally provisioned service accounts:
```hcl
project_id               = "my-gcp-project"
create_service_accounts  = false

cluster_scaler_runner_sa_email    = "corp-gke-scaler@my-gcp-project.iam.gserviceaccount.com"
cluster_scaler_scheduler_sa_email = "corp-scheduler@my-gcp-project.iam.gserviceaccount.com"

reservation_cleaner_runner_sa_email    = "corp-res-cleaner@my-gcp-project.iam.gserviceaccount.com"
reservation_cleaner_scheduler_sa_email = "corp-scheduler@my-gcp-project.iam.gserviceaccount.com"

vm_stopper_runner_sa_email    = "corp-vm-stopper@my-gcp-project.iam.gserviceaccount.com"
vm_stopper_scheduler_sa_email = "corp-scheduler@my-gcp-project.iam.gserviceaccount.com"
```

### Recipe 4: Multi-Project Target Governance
When Cloud Run services run in a central tooling project (`tooling-project`) and remediate resources across target application projects (`app-project-1`, `app-project-2`):
1. Deploy this module in `tooling-project`.
2. Retrieve the runner service account emails from Terraform outputs (`runner_service_accounts`).
3. Grant the required roles in the target projects:
```bash
# GKE Cluster Scaler Runner SA in target project
gcloud projects add-iam-policy-binding app-project-1 \
  --member="serviceAccount:cluster-scaler-sa@tooling-project.iam.gserviceaccount.com" \
  --role="roles/container.admin"

# Reservation Cleaner Runner SA in target project
gcloud projects add-iam-policy-binding app-project-1 \
  --member="serviceAccount:gcsfuse-res-cleaner-sa@tooling-project.iam.gserviceaccount.com" \
  --role="roles/compute.instanceAdmin.v1"
gcloud projects add-iam-policy-binding app-project-1 \
  --member="serviceAccount:gcsfuse-res-cleaner-sa@tooling-project.iam.gserviceaccount.com" \
  --role="roles/monitoring.viewer"

# VM Stopper Runner SA in target project
gcloud projects add-iam-policy-binding app-project-1 \
  --member="serviceAccount:vm-stopper-sa@tooling-project.iam.gserviceaccount.com" \
  --role="roles/compute.instanceAdmin.v1"
gcloud projects add-iam-policy-binding app-project-1 \
  --member="serviceAccount:vm-stopper-sa@tooling-project.iam.gserviceaccount.com" \
  --role="roles/logging.viewer"
```

---

## 7. Teardown

To delete all provisioned Cloud Run services, IAM role bindings, and Cloud Scheduler triggers:
```bash
terraform destroy
```
