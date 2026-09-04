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
# Purpose: Automates end-to-end deployment of VM Stopper to Cloud Run
#          and provisions Cloud Scheduler triggers with secure OIDC authentication.
#
# Actions:
# 1. Validates prerequisites (gcloud CLI, authentication, arguments).
# 2. Enables required Google Cloud APIs.
# 3. Creates/Configures Service Accounts with least-privilege IAM roles.
# 4. Ensures Artifact Registry repository exists.
# 5. Compiles container image from repository Dockerfile using Cloud Build.
# 6. Deploys/Updates Cloud Run service (no unauthenticated access).
# 7. Creates/Updates Cloud Scheduler job with OIDC token authentication.
#
# Usage: ./deploy.sh [OPTIONS]
# ==============================================================================

set -euo pipefail

# --- Configuration Constants & Sane Defaults ---
PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-central1}"
APP_NAME="vm-stopper"
REPO_NAME="${REPO_NAME:-gcsfuse-tools}"
SERVICE_NAME="${SERVICE_NAME:-${APP_NAME}}"
SCHEDULE_NAME="${SCHEDULE_NAME:-${APP_NAME}-scheduler}"
CRON_SCHEDULE="${CRON_SCHEDULE:-0 20 * * *}"

# Service Accounts
RUNNER_SA_NAME="${RUNNER_SA_NAME:-${APP_NAME}-sa}"
SCHEDULER_SA_NAME="${SCHEDULER_SA_NAME:-${APP_NAME}-sched}"
RUNNER_SA_EMAIL="${RUNNER_SA_EMAIL:-}"
SCHEDULER_SA_EMAIL="${SCHEDULER_SA_EMAIL:-}"
DRY_RUN="${DRY_RUN:-false}"
AUTO_GRANT_ROLES="${AUTO_GRANT_ROLES:-false}"
NO_GRANT_ROLES="${NO_GRANT_ROLES:-false}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Logging Helpers ---
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
  -d, --dry-run                     Configure default invocation payload in dry-run mode (Default: ${DRY_RUN})
  -h, --help                        Show this help message and exit

Environment Variables (Overrides):
  PROJECT_ID, REGION, CRON_SCHEDULE, AUTO_GRANT_ROLES, NO_GRANT_ROLES,
  RUNNER_SA_EMAIL, SCHEDULER_SA_EMAIL, DRY_RUN, REPO_NAME, SERVICE_NAME

Examples:
  # Deploy with target project:
  $(basename "$0") --project my-gcp-project

  # Deploy with automatic IAM role granting:
  $(basename "$0") -p my-gcp-project -y

  # Deploy with custom schedule and region in dry-run mode:
  $(basename "$0") -p my-gcp-project -r europe-west1 -s "0 2 * * *" --dry-run
EOF
  exit 0
}

# --- Argument Parsing ---
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -p|--project)
        [[ $# -ge 2 ]] || error_exit "Missing argument for $1"
        PROJECT_ID="$2"
        shift 2
        ;;
      -r|--region)
        [[ $# -ge 2 ]] || error_exit "Missing argument for $1"
        REGION="$2"
        shift 2
        ;;
      -s|--schedule)
        [[ $# -ge 2 ]] || error_exit "Missing argument for $1"
        CRON_SCHEDULE="$2"
        shift 2
        ;;
      -a|--service-account)
        [[ $# -ge 2 ]] || error_exit "Missing argument for $1"
        RUNNER_SA_EMAIL="$2"
        shift 2
        ;;
      --scheduler-sa)
        [[ $# -ge 2 ]] || error_exit "Missing argument for $1"
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
}

# --- Prerequisite Checks ---
check_prerequisites() {
  command -v gcloud >/dev/null 2>&1 || error_exit "gcloud CLI is not installed or not in PATH."

  if [[ -z "${PROJECT_ID}" ]]; then
    PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
    if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
      error_exit "Target GCP Project is required. Specify with --project or set PROJECT_ID."
    fi
  fi

  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "Prerequisites verified for project '${PROJECT_ID}' (simulation mode)."
  else
    if ! gcloud auth print-access-token >/dev/null 2>&1; then
      error_exit "Not authenticated with gcloud. Run 'gcloud auth login' or configure application credentials."
    fi
  fi

  # Resolve default service account emails if not explicitly specified
  if [[ -z "${RUNNER_SA_EMAIL}" ]]; then
    RUNNER_SA_EMAIL="${RUNNER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  fi
  if [[ -z "${SCHEDULER_SA_EMAIL}" ]]; then
    SCHEDULER_SA_EMAIL="${SCHEDULER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  fi

  IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${APP_NAME}:latest"

  log "========================================================================="
  log "Prerequisites verified."
  log "Target Project:        ${PROJECT_ID}"
  log "Target Region:         ${REGION}"
  log "Container Image:       ${IMAGE_NAME}"
  log "Cloud Run Service:     ${SERVICE_NAME}"
  log "Cloud Scheduler Job:   ${SCHEDULE_NAME}"
  log "Schedule Expression:   ${CRON_SCHEDULE}"
  log "Runner SA:             ${RUNNER_SA_EMAIL}"
  log "Scheduler SA:          ${SCHEDULER_SA_EMAIL}"
  log "Dry Run Default:       ${DRY_RUN}"
  log "========================================================================="
}

# --- API Enablement ---
enable_apis() {
  log "Enabling required Google Cloud APIs..."
  execute_cmd gcloud services enable \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    compute.googleapis.com \
    logging.googleapis.com \
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

# --- Service Account Setup ---
setup_service_accounts() {
  log "Configuring Service Accounts and IAM permissions..."

  # 1. Runner Service Account (Runtime)
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud iam service-accounts describe ${RUNNER_SA_EMAIL} --project=${PROJECT_ID}"
    log_dry_run "gcloud iam service-accounts create ${RUNNER_SA_NAME} --project=${PROJECT_ID} --display-name=\"VM Stopper Cloud Run Runner SA\""
  else
    if ! gcloud iam service-accounts describe "${RUNNER_SA_EMAIL}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
      log "Creating Runner Service Account: ${RUNNER_SA_EMAIL}..."
      gcloud iam service-accounts create "${RUNNER_SA_NAME}" \
        --project "${PROJECT_ID}" \
        --display-name "VM Stopper Cloud Run Runner SA" || error_exit "Failed to create Runner Service Account."
    else
      log "Runner Service Account exists: ${RUNNER_SA_EMAIL}"
    fi
  fi

  # Verify and assign roles to Runner SA
  for role in "roles/compute.instanceAdmin.v1" "roles/logging.viewer" "roles/logging.logWriter"; do
    ensure_project_iam_role "${PROJECT_ID}" "serviceAccount:${RUNNER_SA_EMAIL}" "${role}"
  done

  # 2. Scheduler Service Account (Invoker)
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud iam service-accounts describe ${SCHEDULER_SA_EMAIL} --project=${PROJECT_ID}"
    log_dry_run "gcloud iam service-accounts create ${SCHEDULER_SA_NAME} --project=${PROJECT_ID} --display-name=\"VM Stopper Cloud Scheduler Invoker SA\""
  else
    if ! gcloud iam service-accounts describe "${SCHEDULER_SA_EMAIL}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
      log "Creating Scheduler Service Account: ${SCHEDULER_SA_EMAIL}..."
      gcloud iam service-accounts create "${SCHEDULER_SA_NAME}" \
        --project "${PROJECT_ID}" \
        --display-name "VM Stopper Cloud Scheduler Invoker SA" || error_exit "Failed to create Scheduler Service Account."
    else
      log "Scheduler Service Account exists: ${SCHEDULER_SA_EMAIL}"
    fi
  fi
}

# --- Artifact Registry Setup ---
setup_artifact_registry() {
  log "Checking Artifact Registry repository [${REPO_NAME}] in [${REGION}]..."
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud artifacts repositories describe ${REPO_NAME} --location=${REGION} --project=${PROJECT_ID}"
    log_dry_run "gcloud artifacts repositories create ${REPO_NAME} --repository-format=docker --location=${REGION} --project=${PROJECT_ID} --description=\"Docker repository for GCSFuse tools and Cloud Run services\""
  else
    if ! gcloud artifacts repositories describe "${REPO_NAME}" --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
      log "Creating Artifact Registry Docker repository '${REPO_NAME}'..."
      gcloud artifacts repositories create "${REPO_NAME}" \
        --repository-format=docker \
        --location="${REGION}" \
        --project="${PROJECT_ID}" \
        --description="Docker repository for GCSFuse tools and Cloud Run services" || error_exit "Failed to create Artifact Registry repository."
    else
      log "Artifact Registry repository exists."
    fi
  fi
}

# --- Container Build ---
build_image() {
  log "Building and pushing container image: ${IMAGE_NAME}..."
  execute_cmd gcloud builds submit \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --tag "${IMAGE_NAME}" \
    "${SCRIPT_DIR}"
  log "Container image compilation step completed."
}

# --- Cloud Run Service Deployment ---
deploy_cloud_run_service() {
  log "Deploying Cloud Run service: ${SERVICE_NAME}..."

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

  # Retrieve deployed service HTTPS URL
  if [[ "${DRY_RUN}" == "true" ]]; then
    SERVICE_URL="https://${SERVICE_NAME}-preview-${REGION}.a.run.app"
    log_dry_run "gcloud run services describe ${SERVICE_NAME} --project=${PROJECT_ID} --region=${REGION} --format='value(status.url)'"
  else
    SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --project "${PROJECT_ID}" --region "${REGION}" --format="value(status.url)")"
    [[ -n "${SERVICE_URL}" ]] || error_exit "Failed to retrieve Cloud Run service URL."
    log "Cloud Run service deployed at: ${SERVICE_URL}"
  fi

  # Verify and grant roles/run.invoker to Scheduler SA
  ensure_cloud_run_invoker "${SERVICE_NAME}" "${PROJECT_ID}" "${REGION}" "serviceAccount:${SCHEDULER_SA_EMAIL}"
}

# --- Cloud Scheduler Setup ---
deploy_scheduler() {
  log "Configuring Cloud Scheduler trigger: ${SCHEDULE_NAME}..."

  local payload="{\"project\":\"${PROJECT_ID}\",\"dry_run\":${DRY_RUN}}"
  local common_args=(
    --project "${PROJECT_ID}"
    --location "${REGION}"
    --schedule "${CRON_SCHEDULE}"
    --uri "${SERVICE_URL}"
    --http-method POST
    --message-body "${payload}"
    --oidc-service-account-email "${SCHEDULER_SA_EMAIL}"
    --oidc-token-audience "${SERVICE_URL}"
    --time-zone "UTC"
  )

  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud scheduler jobs describe ${SCHEDULE_NAME} --project=${PROJECT_ID} --location=${REGION}"
    log_dry_run "gcloud scheduler jobs create/update http ${SCHEDULE_NAME} ${common_args[*]} --headers=\"Content-Type=application/json\""
  else
    if gcloud scheduler jobs describe "${SCHEDULE_NAME}" --project "${PROJECT_ID}" --location "${REGION}" >/dev/null 2>&1; then
      log "Updating existing Cloud Scheduler trigger..."
      gcloud scheduler jobs update http "${SCHEDULE_NAME}" "${common_args[@]}" --update-headers="Content-Type=application/json" || error_exit "Failed to update Cloud Scheduler job."
    else
      log "Creating new Cloud Scheduler trigger..."
      gcloud scheduler jobs create http "${SCHEDULE_NAME}" "${common_args[@]}" --headers="Content-Type=application/json" || error_exit "Failed to create Cloud Scheduler job."
    fi
  fi
}

main() {
  parse_args "$@"
  log "Starting deployment of ${APP_NAME}..."
  check_prerequisites
  enable_apis
  setup_service_accounts
  setup_artifact_registry
  build_image
  deploy_cloud_run_service
  deploy_scheduler

  log "========================================================================="
  log "Deployment completed successfully!"
  log "Service URL:              ${SERVICE_URL}"
  log "Monitor Cloud Run:        https://console.cloud.google.com/run/detail/${REGION}/${SERVICE_NAME}/metrics?project=${PROJECT_ID}"
  log "Monitor Cloud Scheduler:  https://console.cloud.google.com/cloudscheduler?project=${PROJECT_ID}"
  log "========================================================================="
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
