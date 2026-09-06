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
# Purpose: Automates end-to-end deployment of gcsfuse-reservation-cleaner to
#          Google Cloud Run and provisions periodic Cloud Scheduler triggers
#          with secure OIDC authentication.
#
# Actions:
# 1. Validates prerequisites (gcloud CLI, active GCP authentication).
# 2. Enables required Google Cloud APIs (Cloud Run, Cloud Scheduler, Cloud Build,
#    Artifact Registry, Compute Engine, Cloud Monitoring, IAM).
# 3. Creates and configures Service Accounts with least-privilege IAM roles.
# 4. Ensures Artifact Registry repository exists.
# 5. Compiles container image from repository Dockerfile using Cloud Build.
# 6. Deploys/Updates Cloud Run service with authentication enforcement.
# 7. Provisions/Updates Cloud Scheduler job with OIDC authentication token.
#
# Usage: ./deploy.sh [OPTIONS]
# ==============================================================================

set -euo pipefail

# --- Configuration Defaults & Overrides ---
PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-central1}"
APP_NAME="gcsfuse-reservation-cleaner"
REPO_NAME="${REPO_NAME:-gcsfuse-tools}"
SERVICE_NAME="${SERVICE_NAME:-${APP_NAME}}"
SCHEDULE_NAME="${SCHEDULE_NAME:-${APP_NAME}-scheduler}"
CRON_SCHEDULE="${CRON_SCHEDULE:-0 0 1 * *}"

# Service Accounts
RUNNER_SA_NAME="${RUNNER_SA_NAME:-gcsfuse-res-cleaner-sa}"
SCHEDULER_SA_NAME="${SCHEDULER_SA_NAME:-gcsfuse-res-cleaner-sched}"
RUNNER_SA_EMAIL="${RUNNER_SA_EMAIL:-}"
SCHEDULER_SA_EMAIL="${SCHEDULER_SA_EMAIL:-}"
DRY_RUN="${DRY_RUN:-false}"
AUTO_GRANT_ROLES="${AUTO_GRANT_ROLES:-false}"
NO_GRANT_ROLES="${NO_GRANT_ROLES:-false}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Logging & Error Helpers ---
log() {
  echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')] $*"
}

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

error_exit() {
  log "ERROR: $1" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Deploys ${APP_NAME} to Google Cloud Run and provisions a periodic Cloud Scheduler trigger.

Options:
  -p, --project PROJECT_ID          Target GCP Project ID (Required if not set via PROJECT_ID env var)
  -r, --region REGION               GCP Region for Cloud Run & Scheduler (Default: ${REGION})
  -s, --schedule CRON_SCHEDULE      Cron schedule expression (Default: "${CRON_SCHEDULE}")
  -a, --service-account EMAIL       Runtime Service Account email for Cloud Run
      --scheduler-sa EMAIL          Invocation Service Account email for Cloud Scheduler
  -y, --yes, --auto-grant-roles     Automatically grant missing IAM roles to Service Accounts without prompting
      --no-grant-roles              Do not prompt or attempt to grant missing IAM roles
  -d, --dry-run                     Configure default scheduler payload in dry-run mode (Default: ${DRY_RUN})
  -h, --help                        Show this help message and exit

Environment Variables (Optional overrides):
  PROJECT_ID, REGION, CRON_SCHEDULE, AUTO_GRANT_ROLES, NO_GRANT_ROLES,
  RUNNER_SA_EMAIL, SCHEDULER_SA_EMAIL, DRY_RUN, REPO_NAME, SERVICE_NAME

Examples:
  # Deploy with explicit project:
  $(basename "$0") --project my-gcp-project

  # Deploy with automatic IAM role granting:
  $(basename "$0") -p my-gcp-project -y

  # Deploy with custom region, schedule, and dry-run mode:
  $(basename "$0") -p my-gcp-project -r europe-west4 -s "0 2 1 * *" --dry-run
EOF
  exit 0
}

# --- Parse Command Line Arguments ---
parse_args() {
  while [[ $# -gt 0 ]]; do
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
      -d|--dry-run)
        DRY_RUN="true"
        shift
        ;;
      -h|--help)
        usage
        ;;
      *)
        error_exit "Unknown argument: $1. Use --help for usage details."
        ;;
    esac
  done
}

# --- Pre-flight Checks ---
check_prerequisites() {
  command -v gcloud >/dev/null 2>&1 || error_exit "gcloud CLI is not installed or not in PATH."

  if [[ -z "${PROJECT_ID}" ]]; then
    # Attempt resolving from active gcloud config
    PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
    if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
      error_exit "Target GCP Project is required. Specify with --project or set PROJECT_ID environment variable."
    fi
  fi

  # Compute default service account emails if not explicitly specified
  if [[ -z "${RUNNER_SA_EMAIL}" ]]; then
    RUNNER_SA_EMAIL="${RUNNER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  fi

  if [[ -z "${SCHEDULER_SA_EMAIL}" ]]; then
    SCHEDULER_SA_EMAIL="${SCHEDULER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  fi

  IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${APP_NAME}:latest"

  log "========================================================================="
  log "Starting deployment for ${APP_NAME}..."
  log "========================================================================="
  log "Target Project:        ${PROJECT_ID}"
  log "Target Region:         ${REGION}"
  log "Container Image:       ${IMAGE_NAME}"
  log "Cloud Run Service:     ${SERVICE_NAME}"
  log "Cloud Scheduler Job:   ${SCHEDULE_NAME} ('${CRON_SCHEDULE}')"
  log "Runner SA:             ${RUNNER_SA_EMAIL}"
  log "Scheduler SA:          ${SCHEDULER_SA_EMAIL}"
  log "Dry Run Default:       ${DRY_RUN}"
  log "========================================================================="
}

# --- Step 1: Enable Google Cloud APIs ---
enable_apis() {
  log "Enabling required Google Cloud APIs..."
  execute_cmd gcloud services enable \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    compute.googleapis.com \
    monitoring.googleapis.com \
    iam.googleapis.com \
    --project "${PROJECT_ID}"
  log "Required APIs enabled/verified."
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

# --- Step 2: Provision & Configure Service Accounts ---
setup_service_accounts() {
  log "Configuring Service Accounts and IAM bindings..."

  # 1. Runner Service Account
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud iam service-accounts describe ${RUNNER_SA_EMAIL} --project=${PROJECT_ID}"
    log_dry_run "gcloud iam service-accounts create ${RUNNER_SA_NAME} --project=${PROJECT_ID} --display-name=\"GCSFuse Reservation Cleaner Runner SA\""
  else
    if ! gcloud iam service-accounts describe "${RUNNER_SA_EMAIL}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
      log "Creating Runner Service Account: ${RUNNER_SA_EMAIL}..."
      gcloud iam service-accounts create "${RUNNER_SA_NAME}" \
        --project "${PROJECT_ID}" \
        --display-name "GCSFuse Reservation Cleaner Runner SA" || error_exit "Failed to create Runner Service Account."
    else
      log "Runner Service Account exists: ${RUNNER_SA_EMAIL}"
    fi
  fi

  # Assign required roles to Runner SA
  local runner_roles=(
    "roles/compute.admin"
    "roles/monitoring.viewer"
    "roles/logging.logWriter"
  )

  for role in "${runner_roles[@]}"; do
    ensure_project_iam_role "${PROJECT_ID}" "serviceAccount:${RUNNER_SA_EMAIL}" "${role}"
  done

  # 2. Scheduler Service Account
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud iam service-accounts describe ${SCHEDULER_SA_EMAIL} --project=${PROJECT_ID}"
    log_dry_run "gcloud iam service-accounts create ${SCHEDULER_SA_NAME} --project=${PROJECT_ID} --display-name=\"GCSFuse Reservation Cleaner Scheduler Invoker SA\""
  else
    if ! gcloud iam service-accounts describe "${SCHEDULER_SA_EMAIL}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
      log "Creating Scheduler Service Account: ${SCHEDULER_SA_EMAIL}..."
      gcloud iam service-accounts create "${SCHEDULER_SA_NAME}" \
        --project "${PROJECT_ID}" \
        --display-name "GCSFuse Reservation Cleaner Scheduler Invoker SA" || error_exit "Failed to create Scheduler Service Account."
    else
      log "Scheduler Service Account exists: ${SCHEDULER_SA_EMAIL}"
    fi
  fi
}

# --- Step 3: Setup Artifact Registry Repository ---
setup_artifact_registry() {
  log "Checking Artifact Registry repository [${REPO_NAME}] in [${REGION}]..."
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud artifacts repositories describe ${REPO_NAME} --location=${REGION} --project=${PROJECT_ID}"
    log_dry_run "gcloud artifacts repositories create ${REPO_NAME} --repository-format=docker --location=${REGION} --project=${PROJECT_ID} --description=\"Docker repository for GCSFuse automation services\""
  else
    if ! gcloud artifacts repositories describe "${REPO_NAME}" --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
      log "Creating Artifact Registry repository '${REPO_NAME}'..."
      gcloud artifacts repositories create "${REPO_NAME}" \
        --repository-format=docker \
        --location="${REGION}" \
        --project="${PROJECT_ID}" \
        --description="Docker repository for GCSFuse automation services" || error_exit "Failed to create Artifact Registry repository."
    else
      log "Artifact Registry repository exists."
    fi
  fi
}

# --- Step 4: Build & Push Container Image ---
build_image() {
  log "Building and pushing container image via Cloud Build: ${IMAGE_NAME}..."
  execute_cmd gcloud builds submit \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --tag "${IMAGE_NAME}" \
    "${SCRIPT_DIR}"
  log "Container build step completed."
}

# --- Step 5: Deploy Cloud Run Service ---
deploy_cloud_run_service() {
  log "Deploying Cloud Run Service: ${SERVICE_NAME}..."

  execute_cmd gcloud run deploy "${SERVICE_NAME}" \
    --project "${PROJECT_ID}" \
    --image "${IMAGE_NAME}" \
    --region "${REGION}" \
    --service-account "${RUNNER_SA_EMAIL}" \
    --no-allow-unauthenticated \
    --timeout 540s \
    --memory 512Mi \
    --set-env-vars "PROJECT_ID=${PROJECT_ID},DRY_RUN=${DRY_RUN}" \
    --quiet

  if [[ "${DRY_RUN}" == "true" ]]; then
    SERVICE_URL="https://${SERVICE_NAME}-preview-${REGION}.a.run.app"
    log_dry_run "gcloud run services describe ${SERVICE_NAME} --project=${PROJECT_ID} --region=${REGION} --format='value(status.url)'"
  else
    SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --project "${PROJECT_ID}" --region "${REGION}" --format="value(status.url)")"
    log "Cloud Run Service URL: ${SERVICE_URL}"
  fi

  # Verify and grant roles/run.invoker to Scheduler SA
  ensure_cloud_run_invoker "${SERVICE_NAME}" "${PROJECT_ID}" "${REGION}" "serviceAccount:${SCHEDULER_SA_EMAIL}"
}

# --- Step 6: Deploy Cloud Scheduler Job ---
deploy_scheduler() {
  log "Deploying Cloud Scheduler trigger: ${SCHEDULE_NAME}..."

  local payload="{\"project\":\"${PROJECT_ID}\",\"dry_run\":${DRY_RUN}}"
  local common_args=(
    --project "${PROJECT_ID}"
    --location "${REGION}"
    --schedule "${CRON_SCHEDULE}"
    --time-zone "UTC"
    --uri "${SERVICE_URL}"
    --http-method POST
    --message-body "${payload}"
    --oidc-service-account-email "${SCHEDULER_SA_EMAIL}"
    --oidc-token-audience "${SERVICE_URL}"
    --attempt-deadline "540s"
  )

  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud scheduler jobs describe ${SCHEDULE_NAME} --project=${PROJECT_ID} --location=${REGION}"
    log_dry_run "gcloud scheduler jobs create/update http ${SCHEDULE_NAME} ${common_args[*]} --headers=\"Content-Type=application/json\""
  else
    if gcloud scheduler jobs describe "${SCHEDULE_NAME}" --project "${PROJECT_ID}" --location "${REGION}" >/dev/null 2>&1; then
      log "Scheduler trigger exists. Updating..."
      gcloud scheduler jobs update http "${SCHEDULE_NAME}" "${common_args[@]}" --update-headers="Content-Type=application/json" || error_exit "Failed to update Scheduler trigger."
    else
      log "Scheduler trigger does not exist. Creating..."
      gcloud scheduler jobs create http "${SCHEDULE_NAME}" "${common_args[@]}" --headers="Content-Type=application/json" || error_exit "Failed to create Scheduler trigger."
    fi
  fi
}

# --- Main Entrypoint ---
main() {
  parse_args "$@"
  check_prerequisites
  enable_apis
  setup_service_accounts
  setup_artifact_registry
  build_image
  deploy_cloud_run_service
  deploy_scheduler

  log "========================================================================="
  log "Deployment completed successfully!"
  log "Service Endpoint:        ${SERVICE_URL}"
  log "Monitor Cloud Run:       https://console.cloud.google.com/run/detail/${REGION}/${SERVICE_NAME}/metrics?project=${PROJECT_ID}"
  log "Monitor Cloud Scheduler: https://console.cloud.google.com/cloudscheduler?project=${PROJECT_ID}"
  log "========================================================================="
}

main "$@"
