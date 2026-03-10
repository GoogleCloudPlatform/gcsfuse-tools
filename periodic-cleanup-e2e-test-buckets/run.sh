#!/bin/bash
# Copyright 2025 Google LLC
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
# Script: run.sh
# Purpose: Automates the deployment of the GCS Bucket Cleanup Cloud Run Job.
#
# Actions:
# 1. Checks prerequisites (gcloud CLI, active project).
# 2. Enables required Google Cloud APIs.
# 3. Creates/Configures the Service Account with necessary IAM roles.
# 4. Builds the container image using Cloud Build.
# 5. Creates/Updates the Cloud Run Job with environment variables.
# 6. Creates/Updates the Cloud Scheduler trigger.
#
# Usage: ./run.sh [OPTIONS]
# Options:
#   --help    Show this help message.
# ==============================================================================

set -e

# --- Configuration Constants ---
# Project where the job and resources reside.
readonly PROJECT_ID="gcs-fuse-test-ml"
# Region for Cloud Run and Scheduler.
readonly REGION="us-central1"
# App name used for image repo, job name, and schedule name.
readonly APP_NAME="gcsfuse-e2e-buckets-cleanup"
readonly IMAGE_NAME="gcr.io/${PROJECT_ID}/${APP_NAME}"
readonly JOB_NAME="${APP_NAME}-job"
readonly SCHEDULE_NAME="${APP_NAME}-schedule"
# Cron schedule: Run everyday at 2 AM
readonly CRON_SCHEDULE="0 2 * * *" 

# Service Accounts
# We use a fixed Service Account to ensure consistent job identity across deployments.
# Naming convention: gargnitin-e2e-cleanup-sa@{PROJECT_ID}.iam.gserviceaccount.com
readonly SA_NAME="gargnitin-e2e-cleanup-sa"
readonly SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Using the same SA for both Job and Scheduler for simplicity in this script,
# ensuring it has both Storage Admin (for the job) and Run Invoker (for the scheduler).
readonly JOB_SERVICE_ACCOUNT="$SA_EMAIL"
readonly SCHEDULER_SERVICE_ACCOUNT="$SA_EMAIL"

# --- Helper Functions ---

log() {
    echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')] $*"
}

error_exit() {
    log "ERROR: $1"
    exit 1
}

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Deploys the Periodic GCS Bucket Cleanup Job to Cloud Run."
    echo ""
    echo "Options:"
    echo "  --help    Show this help message."
    echo ""
    echo "Service Account:"
    echo "  The script defaults to using 'gargnitin-e2e-cleanup-sa' to ensure consistent"
    echo "  job identity across deployments. You can override this by setting environment variables."
    echo ""
    echo "Environment Variables (Optional overrides):"
    echo "  JOB_SERVICE_ACCOUNT        Service Account email for the Job."
    echo "  SCHEDULER_SERVICE_ACCOUNT  Service Account email for the Scheduler."
    exit 0
}

# --- Prerequisites Check ---
check_prerequisites() {
    command -v gcloud >/dev/null 2>&1 || error_exit "gcloud CLI is not installed."
    log "Target Project: $PROJECT_ID"
    log "Target Service Account: $SA_EMAIL"
}

# --- Service Account Setup ---
setup_service_account() {
    log "Checking Service Account: $SA_EMAIL"
    
    # Check if SA exists
    if ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
        log "Service Account does not exist. Creating..."
        gcloud iam service-accounts create "$SA_NAME" \
            --project "$PROJECT_ID" \
            --display-name "GCSFuse E2E Bucket Cleanup Service Account" || error_exit "Failed to create Service Account."
    else
        log "Service Account exists."
    fi

    log "Ensuring IAM bindings..."
    # 1. Grant Storage Admin to the SA (so the job can delete buckets)
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/storage.admin" \
        --condition=None \
        --quiet >/dev/null || error_exit "Failed to grant Storage Admin role."

    # 2. Grant Cloud Run Invoker to the SA (so the scheduler can invoke the job)
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/run.invoker" \
        --condition=None \
        --quiet >/dev/null || error_exit "Failed to grant Cloud Run Invoker role."
}

# --- API Enablement ---
enable_apis() {
    log "Enabling required APIs..."
    gcloud services enable \
        run.googleapis.com \
        cloudscheduler.googleapis.com \
        cloudbuild.googleapis.com \
        artifactregistry.googleapis.com \
        --project "$PROJECT_ID" || error_exit "Failed to enable APIs."
}

# --- Main Deployment Steps ---

build_image() {
    log "Building and pushing container image: $IMAGE_NAME"
    gcloud builds submit --project "$PROJECT_ID" --tag "$IMAGE_NAME" . || error_exit "Container build failed."
}

deploy_cloud_run_job() {
    log "Deploying Cloud Run Job: $JOB_NAME"
    
    local common_args=(
        --project "$PROJECT_ID"
        --image "$IMAGE_NAME"
        --region "$REGION"
        --service-account "$JOB_SERVICE_ACCOUNT"
        --set-env-vars PROJECT_ID="$PROJECT_ID",RETENTION_DAYS=10,DRY_RUN=False,QUIET=True
        --task-timeout=3600s
    )

    # Check if job exists to update or create
    if gcloud run jobs describe "$JOB_NAME" --project "$PROJECT_ID" --region "$REGION" >/dev/null 2>&1; then
        log "Job exists. Updating..."
        gcloud run jobs update "$JOB_NAME" "${common_args[@]}" || error_exit "Failed to update Cloud Run Job."
    else
        log "Job does not exist. Creating..."
        gcloud run jobs create "$JOB_NAME" "${common_args[@]}" || error_exit "Failed to create Cloud Run Job."
    fi
}

deploy_scheduler() {
    log "Deploying Cloud Scheduler: $SCHEDULE_NAME"
    
    local job_uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
    
    local common_args=(
        --project "$PROJECT_ID"
        --location "$REGION"
        --schedule "$CRON_SCHEDULE"
        --uri "$job_uri"
        --http-method POST
        --oauth-service-account-email "$SCHEDULER_SERVICE_ACCOUNT"
    )

    if gcloud scheduler jobs describe "$SCHEDULE_NAME" --project "$PROJECT_ID" --location "$REGION" >/dev/null 2>&1; then
        log "Schedule exists. Updating..."
        gcloud scheduler jobs update http "$SCHEDULE_NAME" "${common_args[@]}" || error_exit "Failed to update Schedule."
    else
        log "Schedule does not exist. Creating..."
        gcloud scheduler jobs create http "$SCHEDULE_NAME" "${common_args[@]}" || error_exit "Failed to create Schedule."
    fi
}

main() {
    # Parse arguments
    for arg in "$@"; do
        case $arg in
            --help)
                usage
                ;;
            *)
                # Ignore other args or fail
                ;;
        esac
    done

    log "Starting deployment for $APP_NAME..."
    check_prerequisites
    enable_apis
    setup_service_account
    build_image
    deploy_cloud_run_job
    deploy_scheduler
    
    log "Dump of created Cloud Run Job:"
    gcloud run jobs describe "$JOB_NAME" --project "$PROJECT_ID" --region "$REGION"

    log "Deployment completed successfully!"
    log "Monitor the job at: https://console.cloud.google.com/run/jobs/details/${REGION}/${JOB_NAME}/executions?project=${PROJECT_ID}"
}

main "$@"
