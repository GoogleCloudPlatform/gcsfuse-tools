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
# Purpose: Automates end-to-end deployment of the SA Key Rotator Cloud Run Job
#          and Cloud Scheduler trigger from the tools repository (Source of Truth).
#
# Actions:
# 1. Checks prerequisites (gcloud CLI, active authentication).
# 2. Enables required Google Cloud APIs.
# 3. Creates/Configures Service Accounts with required IAM roles.
# 4. Ensures Artifact Registry repository exists.
# 5. Builds container image from repository Dockerfile using Cloud Build.
# 6. Creates/Updates Cloud Run Job with environment variables.
# 7. Creates/Updates Cloud Scheduler trigger.
#
# Usage: ./deploy.sh [OPTIONS]
# Options:
#   -h, --help    Show this help message.
# ==============================================================================

set -euo pipefail

# --- Configuration Constants & Environment Overrides ---
readonly PROJECT_ID="${PROJECT_ID:-gcs-fuse-test}"
readonly REGION="${REGION:-us-central1}"
readonly REPO_NAME="${REPO_NAME:-gcsfuse-tools}"
readonly APP_NAME="${APP_NAME:-sa-key-rotator}"
readonly IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${APP_NAME}:latest"
readonly JOB_NAME="${JOB_NAME:-gcsfuse-integration-tests-key-rotator-job}"
readonly SCHEDULE_NAME="${SCHEDULE_NAME:-gcsfuse-integration-tests-key-rotator-job-scheduler-trigger}"
readonly CRON_SCHEDULE="${CRON_SCHEDULE:-0 0 1 * *}"

# Service Accounts
readonly RUNNER_SA_NAME="${RUNNER_SA_NAME:-gcsfuse-it-key-rotator-sa}"
readonly RUNNER_SA_EMAIL="${RUNNER_SA_EMAIL:-${RUNNER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"
readonly SCHEDULER_SA_NAME="${SCHEDULER_SA_NAME:-gcsfuse-it-key-rotator-sched}"
readonly SCHEDULER_SA_EMAIL="${SCHEDULER_SA_EMAIL:-${SCHEDULER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"

# Job Environment Variables Default
readonly DEFAULT_SECRET_CONFIGS="gcsfuse-integration-tests|creds-integration-tests|gcs-fuse-test,gcsfuse-integration-tests|creds-integration-tests|gcs-fuse-test-ml,requester-pays-tester|requester-pays-tester|gcs-fuse-test,requester-pays-tester|requester-pays-tester|gcs-fuse-test-ml"
readonly SECRET_CONFIGS="${SECRET_CONFIGS:-${DEFAULT_SECRET_CONFIGS}}"
readonly DRY_RUN="${DRY_RUN:-false}"

# Directory of this script
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

Single-command deployment script for the Service Account Key Rotator.
Builds the container image from the repository and deploys both the Cloud Run Job
and Cloud Scheduler trigger.

Options:
  -h, --help    Show this help message.

Environment Variables (Optional overrides):
  PROJECT_ID           Target GCP Project (default: ${PROJECT_ID})
  REGION               GCP Region for Cloud Run & Scheduler (default: ${REGION})
  SECRET_CONFIGS       Secret configuration tuples (default: 4 GCSFuse test targets)
  DRY_RUN              Initial dry-run mode for deployed job (default: ${DRY_RUN})
  RUNNER_SA_EMAIL      Service account email for Cloud Run Job
  SCHEDULER_SA_EMAIL   Service account email for Cloud Scheduler

Example:
  ./deploy.sh
EOF
  exit 0
}

# --- Deployment Steps ---

check_prerequisites() {
  command -v gcloud >/dev/null 2>&1 || error_exit "gcloud CLI is not installed."
  log "Prerequisites verified."
  log "Target Project:        ${PROJECT_ID}"
  log "Target Region:         ${REGION}"
  log "Container Image:       ${IMAGE_NAME}"
  log "Cloud Run Job:         ${JOB_NAME}"
  log "Cloud Scheduler:       ${SCHEDULE_NAME}"
  log "Runner SA:             ${RUNNER_SA_EMAIL}"
  log "Scheduler SA:          ${SCHEDULER_SA_EMAIL}"
}

enable_apis() {
  log "Enabling required Google Cloud APIs..."
  gcloud services enable \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    iam.googleapis.com \
    --project "${PROJECT_ID}" || error_exit "Failed to enable APIs."
}

setup_service_accounts() {
  log "Checking Service Accounts..."

  # 1. Runner Service Account
  if ! gcloud iam service-accounts describe "${RUNNER_SA_EMAIL}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    log "Creating Runner Service Account: ${RUNNER_SA_EMAIL}..."
    gcloud iam service-accounts create "${RUNNER_SA_NAME}" \
      --project "${PROJECT_ID}" \
      --display-name "GCSFuse IT Key Rotator Job SA" || error_exit "Failed to create Runner Service Account."
  else
    log "Runner Service Account exists: ${RUNNER_SA_EMAIL}"
  fi

  # 2. Scheduler Service Account
  if ! gcloud iam service-accounts describe "${SCHEDULER_SA_EMAIL}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    log "Creating Scheduler Service Account: ${SCHEDULER_SA_EMAIL}..."
    gcloud iam service-accounts create "${SCHEDULER_SA_NAME}" \
      --project "${PROJECT_ID}" \
      --display-name "GCSFuse IT Key Rotator Scheduler Trigger SA" || error_exit "Failed to create Scheduler Service Account."
  else
    log "Scheduler Service Account exists: ${SCHEDULER_SA_EMAIL}"
  fi

  # 3. Grant Cloud Run Invoker role to Scheduler SA
  log "Ensuring Scheduler SA has roles/run.invoker..."
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SCHEDULER_SA_EMAIL}" \
    --role="roles/run.invoker" \
    --condition=None \
    --quiet >/dev/null || error_exit "Failed to grant roles/run.invoker to Scheduler SA."
}

setup_artifact_registry() {
  log "Checking Artifact Registry repository [${REPO_NAME}] in [${REGION}]..."
  if ! gcloud artifacts repositories describe "${REPO_NAME}" --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    log "Creating Artifact Registry repository '${REPO_NAME}'..."
    gcloud artifacts repositories create "${REPO_NAME}" \
      --repository-format=docker \
      --location="${REGION}" \
      --project="${PROJECT_ID}" \
      --description="Docker repository for GCSFuse tools" || error_exit "Failed to create Artifact Registry repository."
  else
    log "Artifact Registry repository exists."
  fi
}

build_image() {
  log "Building and pushing container image from repo source: ${IMAGE_NAME}"
  gcloud builds submit \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --tag "${IMAGE_NAME}" \
    "${SCRIPT_DIR}" || error_exit "Container build failed."
}

deploy_cloud_run_job() {
  log "Deploying Cloud Run Job: ${JOB_NAME}"

  local env_flag="^#^SECRET_CONFIGS=${SECRET_CONFIGS}#DRY_RUN=${DRY_RUN}"
  local common_args=(
    --project "${PROJECT_ID}"
    --image "${IMAGE_NAME}"
    --region "${REGION}"
    --service-account "${RUNNER_SA_EMAIL}"
    --set-env-vars "${env_flag}"
    --task-timeout=600s
  )

  if gcloud run jobs describe "${JOB_NAME}" --project "${PROJECT_ID}" --region "${REGION}" >/dev/null 2>&1; then
    log "Cloud Run Job exists. Updating..."
    gcloud run jobs update "${JOB_NAME}" "${common_args[@]}" || error_exit "Failed to update Cloud Run Job."
  else
    log "Cloud Run Job does not exist. Creating..."
    gcloud run jobs create "${JOB_NAME}" "${common_args[@]}" || error_exit "Failed to create Cloud Run Job."
  fi
}

deploy_scheduler() {
  log "Deploying Cloud Scheduler trigger: ${SCHEDULE_NAME}"

  local job_uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
  local common_args=(
    --project "${PROJECT_ID}"
    --location "${REGION}"
    --schedule "${CRON_SCHEDULE}"
    --uri "${job_uri}"
    --http-method POST
    --oauth-service-account-email "${SCHEDULER_SA_EMAIL}"
  )

  if gcloud scheduler jobs describe "${SCHEDULE_NAME}" --project "${PROJECT_ID}" --location "${REGION}" >/dev/null 2>&1; then
    log "Scheduler trigger exists. Updating..."
    gcloud scheduler jobs update http "${SCHEDULE_NAME}" "${common_args[@]}" || error_exit "Failed to update Scheduler trigger."
  else
    log "Scheduler trigger does not exist. Creating..."
    gcloud scheduler jobs create http "${SCHEDULE_NAME}" "${common_args[@]}" || error_exit "Failed to create Scheduler trigger."
  fi
}

main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage
        ;;
    esac
  done

  log "========================================================================="
  log "Starting deployment for ${APP_NAME} from tools repository (SOT)..."
  log "========================================================================="
  check_prerequisites
  enable_apis
  setup_service_accounts
  setup_artifact_registry
  build_image
  deploy_cloud_run_job
  deploy_scheduler

  log "========================================================================="
  log "Deployment completed successfully!"
  log "Monitor Cloud Run Job:    https://console.cloud.google.com/run/jobs/details/${REGION}/${JOB_NAME}/executions?project=${PROJECT_ID}"
  log "Monitor Cloud Scheduler:  https://console.cloud.google.com/cloudscheduler?project=${PROJECT_ID}"
  log "========================================================================="
}

main "$@"
