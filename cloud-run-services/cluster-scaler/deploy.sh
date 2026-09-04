#!/usr/bin/env bash
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
# Script: deploy.sh
# Purpose: Automates end-to-end deployment of GKE Cluster Scaler to Google Cloud Run
#          and provisions periodic Cloud Scheduler triggers with secure OIDC auth.
#
# Actions:
# 1. Validates command-line arguments and prerequisites (gcloud CLI, authentication).
# 2. Enables required Google Cloud APIs (Container, Run, Scheduler, Build, Artifact Registry).
# 3. Provisions runtime and scheduler Service Accounts with least-privilege IAM roles.
# 4. Ensures Artifact Registry repository exists.
# 5. Compiles container image via Google Cloud Build.
# 6. Deploys Cloud Run service with authentication enforced.
# 7. Configures Cloud Scheduler job with OIDC authentication and payload.
#
# Usage: ./deploy.sh [OPTIONS]
# Options:
#   -p, --project PROJECT_ID          Target GCP Project ID
#   -r, --region REGION               GCP Region for Cloud Run & Scheduler (default: us-central1)
#   -s, --schedule CRON_SCHEDULE      Cron schedule expression (default: "0 2 * * *")
#   -a, --service-account EMAIL       Runtime Service Account email for Cloud Run
#   --scheduler-sa EMAIL              Invocation Service Account email for Cloud Scheduler
#   -t, --threshold DAYS              Idle days threshold before scaling to 0 (default: 7)
#   -d, --dry-run                     Configure default invocation payload in dry-run mode
#   -h, --help                        Show this help message and exit
# ==============================================================================

set -euo pipefail

# --- Defaults & Configuration ---
PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-central1}"
APP_NAME="cluster-scaler"
REPO_NAME="${REPO_NAME:-gcsfuse-tools}"
SERVICE_NAME="${SERVICE_NAME:-${APP_NAME}}"
SCHEDULE_NAME="${SCHEDULE_NAME:-${APP_NAME}-scheduler}"
CRON_SCHEDULE="${CRON_SCHEDULE:-0 2 * * *}"
IDLE_DAYS_THRESHOLD="${IDLE_DAYS_THRESHOLD:-7}"
DRY_RUN="${DRY_RUN:-false}"
AUTO_GRANT_ROLES="${AUTO_GRANT_ROLES:-false}"
NO_GRANT_ROLES="${NO_GRANT_ROLES:-false}"

RUNNER_SA_NAME="${RUNNER_SA_NAME:-${APP_NAME}-sa}"
SCHEDULER_SA_NAME="${SCHEDULER_SA_NAME:-${APP_NAME}-sched}"
RUNNER_SA_EMAIL="${RUNNER_SA_EMAIL:-}"
SCHEDULER_SA_EMAIL="${SCHEDULER_SA_EMAIL:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Helper Functions ---

log() {
  echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')] $*"
}

error_exit() {
  log "ERROR: $1" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Deploys ${APP_NAME} to Google Cloud Run and configures a periodic Cloud Scheduler trigger.

Options:
  -p, --project PROJECT_ID          Target GCP Project ID (Required if PROJECT_ID env var is unset)
  -r, --region REGION               GCP Region for Cloud Run & Scheduler (Default: ${REGION})
  -s, --schedule CRON_SCHEDULE      Cron schedule expression (Default: "${CRON_SCHEDULE}")
  -a, --service-account EMAIL       Runtime Service Account email for Cloud Run
  --scheduler-sa EMAIL              Invocation Service Account email for Cloud Scheduler
  -y, --yes, --auto-grant-roles     Automatically grant missing IAM roles to Service Accounts without prompting
      --no-grant-roles              Do not prompt or attempt to grant missing IAM roles
  -t, --threshold DAYS              Idle days threshold before scaling to 0 (Default: ${IDLE_DAYS_THRESHOLD})
  -d, --dry-run                     Configure default invocation payload in dry-run mode (Default: ${DRY_RUN})
  -h, --help                        Show this help message and exit

Environment Variables (Optional overrides):
  PROJECT_ID, REGION, CRON_SCHEDULE, IDLE_DAYS_THRESHOLD, DRY_RUN,
  AUTO_GRANT_ROLES, NO_GRANT_ROLES, RUNNER_SA_EMAIL, SCHEDULER_SA_EMAIL,
  SERVICE_NAME, SCHEDULE_NAME

Examples:
  # Deploy to specific project:
  $(basename "$0") --project my-gcp-project

  # Deploy with automatic IAM granting:
  $(basename "$0") -p my-gcp-project -y

  # Deploy with custom schedule and region in dry-run mode:
  $(basename "$0") -p my-gcp-project -r europe-west1 -s "0 0 * * *" --dry-run
EOF
  exit 0
}

# --- Parse Arguments ---

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -p|--project)
      PROJECT_ID="$2"
      shift 2
      ;;
    -r|--region)
      REGION="$2"
      shift 2
      ;;
    -s|--schedule)
      CRON_SCHEDULE="$2"
      shift 2
      ;;
    -a|--service-account)
      RUNNER_SA_EMAIL="$2"
      shift 2
      ;;
    --scheduler-sa)
      SCHEDULER_SA_EMAIL="$2"
      shift 2
      ;;
    -y|--yes|--auto-grant-roles)
      AUTO_GRANT_ROLES="true"
      shift 1
      ;;
    --no-grant-roles)
      NO_GRANT_ROLES="true"
      shift 1
      ;;
    -t|--threshold)
      IDLE_DAYS_THRESHOLD="$2"
      shift 2
      ;;
    -d|--dry-run)
      DRY_RUN="true"
      shift 1
      ;;
    -h|--help)
      usage
      ;;
    *)
      error_exit "Unknown option: $1. Run '$(basename "$0") --help' for usage."
      ;;
  esac
done

# --- Validate Parameters ---

if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
  if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
    error_exit "Target GCP Project is required. Specify with --project or set PROJECT_ID environment variable."
  fi
fi

if [[ -z "${RUNNER_SA_EMAIL}" ]]; then
  RUNNER_SA_EMAIL="${RUNNER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
fi

if [[ -z "${SCHEDULER_SA_EMAIL}" ]]; then
  SCHEDULER_SA_EMAIL="${SCHEDULER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
fi

IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${APP_NAME}:latest"

# --- Deployment Workflow ---

log_dry_run() {
  echo "[DRY-RUN] $*"
}

execute_cmd() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "$*"
  else
    "$@"
  fi
}

check_prerequisites() {
  log "Checking prerequisites..."
  command -v gcloud >/dev/null 2>&1 || error_exit "gcloud CLI is not installed or not in PATH."
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "Prerequisites verified for project '${PROJECT_ID}' (simulation mode)."
    return 0
  fi
  gcloud auth print-access-token >/dev/null 2>&1 || error_exit "gcloud authentication failed. Run 'gcloud auth login' or configure ADC credentials."
  log "Prerequisites verified for project '${PROJECT_ID}'."
}

enable_apis() {
  log "Enabling required Google Cloud APIs..."
  execute_cmd gcloud services enable \
    container.googleapis.com \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    iam.googleapis.com \
    --project "${PROJECT_ID}"
  log "APIs enabled/verified successfully."
}

check_project_iam_role() {
  local project="$1"
  local member="$2"
  local role="$3"
  gcloud projects get-iam-policy "${project}" \
    --flatten="bindings[].members" \
    --filter="bindings.role:${role} AND bindings.members:${member}" \
    --format="value(bindings.role)" 2>/dev/null | grep -Fq "${role}"
}

check_cloud_run_invoker_role() {
  local svc="$1"
  local project="$2"
  local region="$3"
  local member="$4"
  gcloud run services get-iam-policy "${svc}" \
    --project="${project}" \
    --region="${region}" \
    --flatten="bindings[].members" \
    --filter="bindings.role:roles/run.invoker AND bindings.members:${member}" \
    --format="value(bindings.role)" 2>/dev/null | grep -Fq "roles/run.invoker"
}

prompt_for_permission() {
  local prompt_text="$1"
  if [[ "${AUTO_GRANT_ROLES}" == "true" ]]; then
    return 0
  fi
  if [[ "${NO_GRANT_ROLES}" == "true" ]]; then
    return 1
  fi
  if [[ -t 0 ]]; then
    local response
    read -r -p "${prompt_text} [Y/n]: " response || return 1
    if [[ -z "${response}" || "${response}" =~ ^[yY]([eE][sS])?$ ]]; then
      return 0
    else
      return 1
    fi
  else
    log "ERROR: Non-interactive session detected and '${prompt_text}' requires authorization."
    log "ERROR: Automatically granting IAM roles in non-interactive sessions is disabled by default for security."
    log "ERROR: To proceed, specify --auto-grant-roles (or -y / --yes) to authorize role assignment, or --no-grant-roles to bypass."
    exit 1
  fi
}

ensure_project_iam_role() {
  local project="$1"
  local member="$2"
  local role="$3"

  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "Checking IAM role '${role}' on '${member}' in project '${project}'..."
    log_dry_run "If missing, would prompt user for permission and execute: gcloud projects add-iam-policy-binding ${project} --member=${member} --role=${role} --condition=None --quiet"
    return 0
  fi

  if check_project_iam_role "${project}" "${member}" "${role}"; then
    log "IAM role '${role}' is already present on '${member}'."
    return 0
  fi

  log "WARNING: IAM role '${role}' is NOT present on '${member}' in project '${project}'."
  if [[ "${NO_GRANT_ROLES}" == "true" ]]; then
    log "Skipping granting '${role}' because --no-grant-roles was specified."
    return 0
  fi

  if prompt_for_permission "Do you permit granting role '${role}' to '${member}' in project '${project}'?"; then
    log "Granting IAM role '${role}' to '${member}'..."
    if gcloud projects add-iam-policy-binding "${project}" \
        --member="${member}" \
        --role="${role}" \
        --condition=None \
        --quiet >/dev/null; then
      log "Successfully granted IAM role '${role}' to '${member}'."
    else
      log "WARNING: Failed to grant IAM role '${role}' to '${member}'."
      log "If you lack 'resourcemanager.projects.setIamPolicy', please request a Project IAM Admin run:"
      log "  gcloud projects add-iam-policy-binding ${project} --member=\"${member}\" --role=\"${role}\""
    fi
  else
    log "Permission declined by user for role '${role}' on '${member}'. Proceeding without granting."
  fi
}

ensure_cloud_run_invoker() {
  local svc="$1"
  local project="$2"
  local region="$3"
  local member="$4"

  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "Checking Cloud Run Invoker on '${svc}' for '${member}'..."
    log_dry_run "If missing, would prompt user for permission and execute: gcloud run services add-iam-policy-binding ${svc} --project=${project} --region=${region} --member=${member} --role=roles/run.invoker --quiet"
    return 0
  fi

  if check_cloud_run_invoker_role "${svc}" "${project}" "${region}" "${member}"; then
    log "IAM role 'roles/run.invoker' is already present on Cloud Run service '${svc}' for '${member}'."
    return 0
  fi

  log "WARNING: IAM role 'roles/run.invoker' is NOT present on Cloud Run service '${svc}' for '${member}'."
  if [[ "${NO_GRANT_ROLES}" == "true" ]]; then
    log "Skipping granting 'roles/run.invoker' on '${svc}' because --no-grant-roles was specified."
    return 0
  fi

  if prompt_for_permission "Do you permit granting 'roles/run.invoker' to '${member}' on Cloud Run service '${svc}'?"; then
    log "Granting 'roles/run.invoker' to '${member}' on service '${svc}'..."
    if gcloud run services add-iam-policy-binding "${svc}" \
        --project="${project}" \
        --region="${region}" \
        --member="${member}" \
        --role="roles/run.invoker" \
        --quiet >/dev/null; then
      log "Successfully granted 'roles/run.invoker' on service '${svc}' to '${member}'."
    else
      log "WARNING: Failed to grant 'roles/run.invoker' on service '${svc}' to '${member}'."
      log "Please ask an authorized administrator to run:"
      log "  gcloud run services add-iam-policy-binding ${svc} --project=${project} --region=${region} --member=\"${member}\" --role=\"roles/run.invoker\""
    fi
  else
    log "Permission declined by user for 'roles/run.invoker' on service '${svc}'. Proceeding without granting."
  fi
}

setup_service_accounts() {
  log "Configuring Service Accounts..."

  # 1. Runner SA (Cloud Run runtime)
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud iam service-accounts describe ${RUNNER_SA_EMAIL} --project=${PROJECT_ID}"
    log_dry_run "gcloud iam service-accounts create ${RUNNER_SA_NAME} --project=${PROJECT_ID} --display-name=\"GKE Cluster Scaler Cloud Run Runtime SA\""
  else
    if ! gcloud iam service-accounts describe "${RUNNER_SA_EMAIL}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
      log "Creating runtime service account '${RUNNER_SA_EMAIL}'..."
      gcloud iam service-accounts create "${RUNNER_SA_NAME}" \
        --project "${PROJECT_ID}" \
        --display-name "GKE Cluster Scaler Cloud Run Runtime SA"
    else
      log "Runtime service account '${RUNNER_SA_EMAIL}' already exists."
    fi
  fi

  # Verify and assign IAM roles to Runner SA
  ensure_project_iam_role "${PROJECT_ID}" "serviceAccount:${RUNNER_SA_EMAIL}" "roles/container.admin"
  ensure_project_iam_role "${PROJECT_ID}" "serviceAccount:${RUNNER_SA_EMAIL}" "roles/logging.logWriter"

  # 2. Scheduler SA (Cloud Scheduler invoker)
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud iam service-accounts describe ${SCHEDULER_SA_EMAIL} --project=${PROJECT_ID}"
    log_dry_run "gcloud iam service-accounts create ${SCHEDULER_SA_NAME} --project=${PROJECT_ID} --display-name=\"GKE Cluster Scaler Scheduler Invoker SA\""
  else
    if ! gcloud iam service-accounts describe "${SCHEDULER_SA_EMAIL}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
      log "Creating scheduler service account '${SCHEDULER_SA_EMAIL}'..."
      gcloud iam service-accounts create "${SCHEDULER_SA_NAME}" \
        --project "${PROJECT_ID}" \
        --display-name "GKE Cluster Scaler Scheduler Invoker SA"
    else
      log "Scheduler service account '${SCHEDULER_SA_EMAIL}' already exists."
    fi
  fi
}

setup_artifact_registry() {
  log "Ensuring Artifact Registry repository '${REPO_NAME}' exists in '${REGION}'..."
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud artifacts repositories describe ${REPO_NAME} --project=${PROJECT_ID} --location=${REGION}"
    log_dry_run "gcloud artifacts repositories create ${REPO_NAME} --project=${PROJECT_ID} --location=${REGION} --repository-format=docker --description=\"Docker repository for gcsfuse automation tools\""
  else
    if ! gcloud artifacts repositories describe "${REPO_NAME}" --project "${PROJECT_ID}" --location "${REGION}" >/dev/null 2>&1; then
      log "Creating Artifact Registry repository '${REPO_NAME}'..."
      gcloud artifacts repositories create "${REPO_NAME}" \
        --project "${PROJECT_ID}" \
        --location "${REGION}" \
        --repository-format docker \
        --description "Docker repository for gcsfuse automation tools"
    else
      log "Artifact Registry repository '${REPO_NAME}' already exists."
    fi
  fi
}

build_image() {
  log "Building container image '${IMAGE_NAME}' via Google Cloud Build..."
  execute_cmd gcloud builds submit "${SCRIPT_DIR}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --tag "${IMAGE_NAME}"
  log "Container image step completed."
}

deploy_cloud_run() {
  log "Deploying '${SERVICE_NAME}' to Cloud Run in region '${REGION}'..."
  execute_cmd gcloud run deploy "${SERVICE_NAME}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --image "${IMAGE_NAME}" \
    --platform managed \
    --service-account "${RUNNER_SA_EMAIL}" \
    --no-allow-unauthenticated \
    --timeout 540s \
    --memory 512Mi \
    --set-env-vars "PROJECT_ID=${PROJECT_ID},IDLE_DAYS_THRESHOLD=${IDLE_DAYS_THRESHOLD},DRY_RUN=${DRY_RUN}" \
    --quiet

  # Verify and grant Scheduler SA permission to invoke this Cloud Run service
  ensure_cloud_run_invoker "${SERVICE_NAME}" "${PROJECT_ID}" "${REGION}" "serviceAccount:${SCHEDULER_SA_EMAIL}"
}

setup_cloud_scheduler() {
  log "Configuring Cloud Scheduler trigger '${SCHEDULE_NAME}'..."

  local service_url
  if [[ "${DRY_RUN}" == "true" ]]; then
    service_url="https://${SERVICE_NAME}-preview-${REGION}.a.run.app"
    log_dry_run "gcloud run services describe ${SERVICE_NAME} --project=${PROJECT_ID} --region=${REGION} --format='value(status.url)'"
  else
    service_url="$(gcloud run services describe "${SERVICE_NAME}" --project "${PROJECT_ID}" --region "${REGION}" --format="value(status.url)")"
    if [[ -z "${service_url}" ]]; then
      error_exit "Failed to retrieve Cloud Run service URL for '${SERVICE_NAME}'."
    fi
  fi

  local payload="{\"project\":\"${PROJECT_ID}\",\"idle_days_threshold\":${IDLE_DAYS_THRESHOLD},\"dry_run\":${DRY_RUN}}"

  local common_args=(
    --project "${PROJECT_ID}"
    --location "${REGION}"
    --schedule "${CRON_SCHEDULE}"
    --uri "${service_url}"
    --http-method POST
    --message-body "${payload}"
    --oidc-service-account-email "${SCHEDULER_SA_EMAIL}"
    --oidc-token-audience "${service_url}"
    --time-zone "UTC"
    --quiet
  )

  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud scheduler jobs describe ${SCHEDULE_NAME} --project=${PROJECT_ID} --location=${REGION}"
    log_dry_run "gcloud scheduler jobs create/update http ${SCHEDULE_NAME} --schedule=\"${CRON_SCHEDULE}\" --uri=\"${service_url}\" --headers=\"Content-Type=application/json\" --oidc-service-account-email=\"${SCHEDULER_SA_EMAIL}\""
  else
    if gcloud scheduler jobs describe "${SCHEDULE_NAME}" --project "${PROJECT_ID}" --location "${REGION}" >/dev/null 2>&1; then
      log "Updating existing Cloud Scheduler job '${SCHEDULE_NAME}'..."
      gcloud scheduler jobs update http "${SCHEDULE_NAME}" \
        "${common_args[@]}" \
        --update-headers "Content-Type=application/json"
    else
      log "Creating new Cloud Scheduler job '${SCHEDULE_NAME}'..."
      gcloud scheduler jobs create http "${SCHEDULE_NAME}" \
        "${common_args[@]}" \
        --headers "Content-Type=application/json"
    fi
  fi

  log "================================================================="
  log "Deployment completed successfully!"
  log "Service Name:         ${SERVICE_NAME}"
  log "Service URL:          ${service_url}"
  log "Target Project:       ${PROJECT_ID}"
  log "Region:               ${REGION}"
  log "Scheduler Job:        ${SCHEDULE_NAME} (${CRON_SCHEDULE})"
  log "Idle Threshold:       ${IDLE_DAYS_THRESHOLD} days"
  log "Dry Run Mode:         ${DRY_RUN}"
  log "Runner SA:            ${RUNNER_SA_EMAIL}"
  log "Scheduler SA:         ${SCHEDULER_SA_EMAIL}"
  log "================================================================="
}

main() {
  check_prerequisites
  enable_apis
  setup_service_accounts
  setup_artifact_registry
  build_image
  deploy_cloud_run
  setup_cloud_scheduler
}

main
