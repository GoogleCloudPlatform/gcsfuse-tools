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
# Script: deploy_all.sh
# Purpose: Unified deployment and scheduling orchestrator for GCSFuse Cloud Run
#          automation services (cluster-scaler, gcsfuse-reservation-cleaner, vm-stopper).
#
# Lifecycle Stages:
# 1. Preflight validation & offline unit test gating across selected services.
# 2. Idempotent Google Cloud API enablement and Artifact Registry verification.
# 3. Dedicated Service Account creation and least-privilege IAM bindings.
# 4. Container image compilation and publication via Google Cloud Build.
# 5. Cloud Run service deployment (512Mi memory, 540s timeout, auth enforced).
# 6. Cloud Scheduler HTTP trigger creation/updating with OIDC authentication tokens.
#
# Usage: ./deploy_all.sh [OPTIONS]
# ==============================================================================

set -Eeuo pipefail

# --- ANSI Color Formatting & Diagnostics ---
if [[ -t 1 ]] && [[ "${TERM:-}" != "dumb" ]]; then
  COLOR_RESET="\033[0m"
  COLOR_BOLD="\033[1m"
  COLOR_CYAN="\033[36m"
  COLOR_GREEN="\033[32m"
  COLOR_YELLOW="\033[33m"
  COLOR_RED="\033[31m"
  COLOR_MAGENTA="\033[35m"
else
  COLOR_RESET=""
  COLOR_BOLD=""
  COLOR_CYAN=""
  COLOR_GREEN=""
  COLOR_YELLOW=""
  COLOR_RED=""
  COLOR_MAGENTA=""
fi

log_info() {
  echo -e "${COLOR_CYAN}[INFO]${COLOR_RESET} [$(date +'%Y-%m-%dT%H:%M:%S%z')] $*"
}

log_success() {
  echo -e "${COLOR_GREEN}[SUCCESS]${COLOR_RESET} [$(date +'%Y-%m-%dT%H:%M:%S%z')] $*"
}

log_warn() {
  echo -e "${COLOR_YELLOW}[WARN]${COLOR_RESET} [$(date +'%Y-%m-%dT%H:%M:%S%z')] $*"
}

log_error() {
  echo -e "${COLOR_RED}[ERROR]${COLOR_RESET} [$(date +'%Y-%m-%dT%H:%M:%S%z')] $*" >&2
}

log_dry_run() {
  echo -e "${COLOR_MAGENTA}[DRY-RUN]${COLOR_RESET} $*"
}

error_exit() {
  log_error "$1"
  exit 1
}

# Error trap handler for line reporting
error_handler() {
  local exit_code="$1"
  local line_no="$2"
  local last_command="$3"
  log_error "Command '${last_command}' failed on line ${line_no} with exit code ${exit_code}."
}
trap 'error_handler $? $LINENO "$BASH_COMMAND"' ERR

# --- Directories & Defaults ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Configurable Parameters with Environment Variable Fallbacks
PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-central1}"
SERVICES_INPUT="${SERVICES:-all}"
REPO_NAME="${REPO_NAME:-gcsfuse-tools}"
DRY_RUN="${DRY_RUN:-false}"
SKIP_TESTS="${SKIP_TESTS:-false}"

# Service Account Overrides
GLOBAL_RUNNER_SA="${RUNNER_SA_EMAIL:-${SERVICE_ACCOUNT:-}}"
GLOBAL_SCHEDULER_SA="${SCHEDULER_SA_EMAIL:-${SCHEDULER_SA:-}}"

# IAM Role Automation & Interactive Permissions
AUTO_GRANT_ROLES="${AUTO_GRANT_ROLES:-false}"
NO_GRANT_ROLES="${NO_GRANT_ROLES:-false}"

# Cron Schedules
CLUSTER_SCALER_SCHEDULE="${CLUSTER_SCALER_SCHEDULE:-0 2 * * *}"
CLEANER_SCHEDULE="${CLEANER_SCHEDULE:-0 0 1 * *}"
VM_STOPPER_SCHEDULE="${VM_STOPPER_SCHEDULE:-0 20 * * *}"

# Parameters
IDLE_DAYS_THRESHOLD="${IDLE_DAYS_THRESHOLD:-7}"

# Supported Canonical Services
SUPPORTED_SERVICES=("cluster-scaler" "gcsfuse-reservation-cleaner" "vm-stopper")

# --- Usage & Help Menu ---
usage() {
  cat <<'USAGE_EOF'
Usage: deploy_all.sh [OPTIONS]

Unified deployment and scheduling orchestrator for GCSFuse Cloud Run Services:
  - cluster-scaler                 (GKE Idle Cluster & Node Pool Scaler)
  - gcsfuse-reservation-cleaner    (GCE Compute Reservation Cleaner & Cost Engine)
  - vm-stopper                     (GCE Idle VM Stopper & Lifecycle Remediation)

Options:
  -p, --project PROJECT_ID          Target GCP Project ID (Default: active gcloud project)
  -r, --region REGION               GCP Region for Cloud Run & Scheduler (Default: us-central1)
  -s, --services SERVICES           Comma- or space-separated list of services to deploy:
                                    'all', 'cluster-scaler', 'gcsfuse-reservation-cleaner', 'vm-stopper'
                                    (Default: all)
  -a, --service-account EMAIL       Override runtime Service Account email across services
      --scheduler-sa EMAIL          Override scheduler invoker Service Account email across services
  -y, --yes, --auto-grant-roles     Automatically grant missing IAM roles to Service Accounts without prompting
      --no-grant-roles              Do not prompt or attempt to grant missing IAM roles
  -d, --dry-run                     Preview planned commands without executing mutating API calls,
                                    and configure default scheduler payload to dry_run: true
      --skip-tests                  Skip pre-deployment offline unit test validation
      --repo-name REPO_NAME         Artifact Registry repository name (Default: gcsfuse-tools)

Schedule Customization:
      --cluster-scaler-schedule CRON    Cron schedule for cluster-scaler (Default: "0 2 * * *")
      --cleaner-schedule CRON           Cron schedule for reservation-cleaner (Default: "0 0 1 * *")
      --vm-stopper-schedule CRON        Cron schedule for vm-stopper (Default: "0 20 * * *")
  -t, --threshold DAYS                  Idle days threshold for cluster-scaler (Default: 7)

General:
  -h, --help                        Show this detailed help message and exit

Environment Variables:
  PROJECT_ID, REGION, SERVICES, RUNNER_SA_EMAIL, SCHEDULER_SA_EMAIL,
  AUTO_GRANT_ROLES, NO_GRANT_ROLES, REPO_NAME, DRY_RUN, SKIP_TESTS,
  CLUSTER_SCALER_SCHEDULE, CLEANER_SCHEDULE, VM_STOPPER_SCHEDULE, IDLE_DAYS_THRESHOLD

Examples:
  # Deploy all services to active project:
  ./deploy_all.sh --project my-gcp-project

  # Deploy with automatic IAM role granting:
  ./deploy_all.sh -p my-gcp-project -y

  # Deploy only vm-stopper and cluster-scaler in dry-run mode:
  ./deploy_all.sh -p my-gcp-project -s "vm-stopper,cluster-scaler" --dry-run

  # Deploy reservation cleaner with customized schedule:
  ./deploy_all.sh -p my-gcp-project -s gcsfuse-reservation-cleaner --cleaner-schedule "0 3 * * 0"
USAGE_EOF
  exit 0
}

# --- CLI Argument Parsing ---
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -p|--project)
        [[ $# -ge 2 && ! "$2" =~ ^- ]] || error_exit "Missing argument for $1"
        PROJECT_ID="$2"
        shift 2
        ;;
      -r|--region)
        [[ $# -ge 2 && ! "$2" =~ ^- ]] || error_exit "Missing argument for $1"
        REGION="$2"
        shift 2
        ;;
      -s|--services)
        [[ $# -ge 2 && ! "$2" =~ ^- ]] || error_exit "Missing argument for $1"
        SERVICES_INPUT="$2"
        shift 2
        ;;
      -a|--service-account)
        [[ $# -ge 2 && ! "$2" =~ ^- ]] || error_exit "Missing argument for $1"
        GLOBAL_RUNNER_SA="$2"
        shift 2
        ;;
      --scheduler-sa)
        [[ $# -ge 2 && ! "$2" =~ ^- ]] || error_exit "Missing argument for $1"
        GLOBAL_SCHEDULER_SA="$2"
        shift 2
        ;;
      -y|--yes|--auto-grant-roles)
        AUTO_GRANT_ROLES="true"
        shift
        ;;
      --no-grant-roles)
        NO_GRANT_ROLES="true"
        shift
        ;;
      -d|--dry-run)
        DRY_RUN="true"
        shift
        ;;
      --skip-tests)
        SKIP_TESTS="true"
        shift
        ;;
      --repo-name)
        [[ $# -ge 2 && ! "$2" =~ ^- ]] || error_exit "Missing argument for $1"
        REPO_NAME="$2"
        shift 2
        ;;
      --cluster-scaler-schedule)
        [[ $# -ge 2 && ! "$2" =~ ^- ]] || error_exit "Missing argument for $1"
        CLUSTER_SCALER_SCHEDULE="$2"
        shift 2
        ;;
      --cleaner-schedule|--reservation-cleaner-schedule)
        [[ $# -ge 2 && ! "$2" =~ ^- ]] || error_exit "Missing argument for $1"
        CLEANER_SCHEDULE="$2"
        shift 2
        ;;
      --vm-stopper-schedule)
        [[ $# -ge 2 && ! "$2" =~ ^- ]] || error_exit "Missing argument for $1"
        VM_STOPPER_SCHEDULE="$2"
        shift 2
        ;;
      -t|--threshold)
        [[ $# -ge 2 && ! "$2" =~ ^- ]] || error_exit "Missing argument for $1"
        IDLE_DAYS_THRESHOLD="$2"
        shift 2
        ;;
      -h|--help)
        usage
        ;;
      *)
        error_exit "Unknown flag or argument: '$1'. Use --help for usage instructions."
        ;;
    esac
  done
}

# --- Service Selection Resolution & Validation ---
declare -a TARGET_SERVICES=()

resolve_and_validate_services() {
  local raw_input="${SERVICES_INPUT}"
  # Replace commas with spaces
  raw_input="${raw_input//,/ }"

  local parsed_list=()
  for item in ${raw_input}; do
    # Normalize aliases
    case "${item}" in
      all)
        parsed_list=("${SUPPORTED_SERVICES[@]}")
        ;;
      cluster-scaler)
        parsed_list+=("cluster-scaler")
        ;;
      gcsfuse-reservation-cleaner|reservation-cleaner|cleaner)
        parsed_list+=("gcsfuse-reservation-cleaner")
        ;;
      vm-stopper|stopper)
        parsed_list+=("vm-stopper")
        ;;
      *)
        error_exit "Unknown service: '${item}'. Supported: cluster-scaler, gcsfuse-reservation-cleaner, vm-stopper, all"
        ;;
    esac
  done

  # Deduplicate services while preserving order
  local seen=""
  for svc in "${parsed_list[@]}"; do
    if [[ ! " ${seen} " =~ " ${svc} " ]]; then
      TARGET_SERVICES+=("${svc}")
      seen="${seen} ${svc}"
    fi
  done

  if [[ ${#TARGET_SERVICES[@]} -eq 0 ]]; then
    error_exit "No valid services selected for deployment."
  fi
}

# --- Target Project & Region Resolution ---
resolve_project_and_region() {
  if [[ -z "${PROJECT_ID}" ]]; then
    if command -v gcloud >/dev/null 2>&1; then
      PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
      if [[ "${PROJECT_ID}" == "(unset)" ]]; then
        PROJECT_ID=""
      fi
    fi
  fi

  if [[ -z "${PROJECT_ID}" ]]; then
    error_exit "Target GCP Project ID is required. Specify via -p/--project, PROJECT_ID env var, or configure 'gcloud config set project <PROJECT_ID>'."
  fi
}

# --- Command Execution Abstraction for Dry-Run Support ---
execute_cmd() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "$*"
  else
    "$@"
  fi
}

# --- Step 1: Preflight Offline Unit Test Gating ---
run_offline_unit_tests() {
  if [[ "${SKIP_TESTS}" == "true" ]]; then
    log_warn "Pre-deployment unit testing bypassed via --skip-tests."
    return 0
  fi

  log_info "================================================================="
  log_info "Stage 1: Pre-Deployment Offline Unit Test Gating"
  log_info "================================================================="

  for svc in "${TARGET_SERVICES[@]}"; do
    local test_dir="${SCRIPT_DIR}/${svc}/tests"
    if [[ -d "${test_dir}" ]]; then
      log_info "Executing unit test suite for '${svc}'..."
      if ! python3 -m unittest discover -s "${test_dir}" -v; then
        error_exit "Unit test gating failed for '${svc}'. Fix test failures before deploying."
      fi
      log_success "Unit tests passed for '${svc}'."
    else
      log_warn "No test directory found at '${test_dir}', skipping unit tests for '${svc}'."
    fi
  done
  log_success "All offline unit test suites passed successfully."
}

# --- Step 2: Prerequisites & API Enablement ---
check_prerequisites_and_apis() {
  log_info "================================================================="
  log_info "Stage 2: Prerequisites Verification & API Enablement"
  log_info "================================================================="

  command -v gcloud >/dev/null 2>&1 || error_exit "gcloud CLI is not installed or not in PATH."

  if [[ "${DRY_RUN}" == "false" ]]; then
    gcloud auth print-access-token >/dev/null 2>&1 || error_exit "gcloud authentication failed. Run 'gcloud auth login' or configure application default credentials."
  else
    log_info "Dry-run mode enabled: Skipping active credential verification."
  fi

  log_info "Target Project: ${PROJECT_ID}"
  log_info "Target Region:  ${REGION}"
  log_info "Selected Services: ${TARGET_SERVICES[*]}"

  # Compile required APIs based on selected services
  local apis=(
    "run.googleapis.com"
    "cloudscheduler.googleapis.com"
    "cloudbuild.googleapis.com"
    "artifactregistry.googleapis.com"
    "iam.googleapis.com"
    "logging.googleapis.com"
  )

  for svc in "${TARGET_SERVICES[@]}"; do
    case "${svc}" in
      cluster-scaler)
        apis+=("container.googleapis.com")
        ;;
      gcsfuse-reservation-cleaner)
        apis+=("compute.googleapis.com" "monitoring.googleapis.com")
        ;;
      vm-stopper)
        apis+=("compute.googleapis.com")
        ;;
    esac
  done

  # Deduplicate API list
  local unique_apis=()
  local seen_apis=""
  for api in "${apis[@]}"; do
    if [[ ! " ${seen_apis} " =~ " ${api} " ]]; then
      unique_apis+=("${api}")
      seen_apis="${seen_apis} ${api}"
    fi
  done

  log_info "Enabling required Google Cloud APIs: ${unique_apis[*]}..."
  execute_cmd gcloud services enable "${unique_apis[@]}" --project="${PROJECT_ID}"
  log_success "Required APIs verified/enabled."
}

# --- Step 3: Artifact Registry Repository Setup ---
setup_artifact_registry() {
  log_info "================================================================="
  log_info "Stage 3: Artifact Registry Repository Verification"
  log_info "================================================================="

  log_info "Ensuring Artifact Registry repository '${REPO_NAME}' exists in '${REGION}'..."
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud artifacts repositories describe ${REPO_NAME} --project=${PROJECT_ID} --location=${REGION}"
    log_dry_run "gcloud artifacts repositories create ${REPO_NAME} --project=${PROJECT_ID} --location=${REGION} --repository-format=docker --description=\"Docker repository for gcsfuse automation tools\""
  else
    if ! gcloud artifacts repositories describe "${REPO_NAME}" --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1; then
      log_info "Creating Artifact Registry repository '${REPO_NAME}'..."
      gcloud artifacts repositories create "${REPO_NAME}" \
        --project="${PROJECT_ID}" \
        --location="${REGION}" \
        --repository-format=docker \
        --description="Docker repository for gcsfuse automation tools"
    else
      log_info "Artifact Registry repository '${REPO_NAME}' already exists."
    fi
  fi
  log_success "Artifact Registry repository verified."
}

# --- Step 4: IAM Checking & Permission Authorization Helpers ---

# Check if a project-level IAM role is bound to a member
check_project_iam_role() {
  local project="$1"
  local member="$2"
  local role="$3"
  gcloud projects get-iam-policy "${project}" \
    --flatten="bindings[].members" \
    --filter="bindings.role:${role} AND bindings.members:${member}" \
    --format="value(bindings.role)" 2>/dev/null | grep -Fq "${role}"
}

# Check if Cloud Run Invoker role is bound to a member on a specific service
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

# Prompt user for permission to grant an IAM role
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
    log_info "Non-interactive session detected; automatically granting role (${prompt_text}). Pass --no-grant-roles to prevent."
    return 0
  fi
}

# Ensure project-level IAM role with check and permission prompt
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
    log_info "IAM role '${role}' is already present on '${member}'."
    return 0
  fi

  log_warn "IAM role '${role}' is NOT present on '${member}' in project '${project}'."
  if [[ "${NO_GRANT_ROLES}" == "true" ]]; then
    log_warn "Skipping granting '${role}' because --no-grant-roles was specified."
    return 0
  fi

  if prompt_for_permission "Do you permit granting role '${role}' to '${member}' in project '${project}'?"; then
    log_info "Granting IAM role '${role}' to '${member}'..."
    if gcloud projects add-iam-policy-binding "${project}" \
        --member="${member}" \
        --role="${role}" \
        --condition=None \
        --quiet >/dev/null; then
      log_success "Successfully granted IAM role '${role}' to '${member}'."
    else
      log_warn "Failed to grant IAM role '${role}' to '${member}'."
      log_warn "If you lack 'resourcemanager.projects.setIamPolicy', please request a Project IAM Admin run:"
      log_warn "  gcloud projects add-iam-policy-binding ${project} --member=\"${member}\" --role=\"${role}\""
    fi
  else
    log_warn "Permission declined by user for role '${role}' on '${member}'. Proceeding without granting."
  fi
}

# Ensure Cloud Run Invoker role with check and permission prompt
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
    log_info "IAM role 'roles/run.invoker' is already present on Cloud Run service '${svc}' for '${member}'."
    return 0
  fi

  log_warn "IAM role 'roles/run.invoker' is NOT present on Cloud Run service '${svc}' for '${member}'."
  if [[ "${NO_GRANT_ROLES}" == "true" ]]; then
    log_warn "Skipping granting 'roles/run.invoker' on '${svc}' because --no-grant-roles was specified."
    return 0
  fi

  if prompt_for_permission "Do you permit granting 'roles/run.invoker' to '${member}' on Cloud Run service '${svc}'?"; then
    log_info "Granting 'roles/run.invoker' to '${member}' on service '${svc}'..."
    if gcloud run services add-iam-policy-binding "${svc}" \
        --project="${project}" \
        --region="${region}" \
        --member="${member}" \
        --role="roles/run.invoker" \
        --quiet >/dev/null; then
      log_success "Successfully granted 'roles/run.invoker' on service '${svc}' to '${member}'."
    else
      log_warn "Failed to grant 'roles/run.invoker' on service '${svc}' to '${member}'."
      log_warn "Please ask an authorized administrator to run:"
      log_warn "  gcloud run services add-iam-policy-binding ${svc} --project=${project} --region=${region} --member=\"${member}\" --role=\"roles/run.invoker\""
    fi
  else
    log_warn "Permission declined by user for 'roles/run.invoker' on service '${svc}'. Proceeding without granting."
  fi
}

# --- Step 5: Individual Service Deployment ---
# Summary tracking arrays
declare -a SUMMARY_SERVICES=()
declare -a SUMMARY_URLS=()
declare -a SUMMARY_JOBS=()
declare -a SUMMARY_SCHEDULES=()

deploy_service() {
  local svc="$1"
  local svc_dir="${SCRIPT_DIR}/${svc}"

  log_info "================================================================="
  log_info "Stage 4: Deploying Service '${svc}'"
  log_info "================================================================="

  # 1. Resolve Service Account Names and Roles
  local default_runner_sa_name
  local default_sched_sa_name
  local iam_roles=()
  local cron_schedule
  local env_vars="PROJECT_ID=${PROJECT_ID},DRY_RUN=${DRY_RUN}"
  local payload="{\"project\":\"${PROJECT_ID}\",\"dry_run\":${DRY_RUN}}"

  case "${svc}" in
    cluster-scaler)
      default_runner_sa_name="cluster-scaler-sa"
      default_sched_sa_name="cluster-scaler-sched"
      iam_roles=("roles/container.admin" "roles/logging.logWriter")
      cron_schedule="${CLUSTER_SCALER_SCHEDULE}"
      env_vars="PROJECT_ID=${PROJECT_ID},IDLE_DAYS_THRESHOLD=${IDLE_DAYS_THRESHOLD},DRY_RUN=${DRY_RUN}"
      payload="{\"project\":\"${PROJECT_ID}\",\"idle_days_threshold\":${IDLE_DAYS_THRESHOLD},\"dry_run\":${DRY_RUN}}"
      ;;
    gcsfuse-reservation-cleaner)
      default_runner_sa_name="gcsfuse-res-cleaner-sa"
      default_sched_sa_name="gcsfuse-res-cleaner-sched"
      iam_roles=("roles/compute.instanceAdmin.v1" "roles/monitoring.viewer" "roles/logging.logWriter")
      cron_schedule="${CLEANER_SCHEDULE}"
      ;;
    vm-stopper)
      default_runner_sa_name="vm-stopper-sa"
      default_sched_sa_name="vm-stopper-sched"
      iam_roles=("roles/compute.instanceAdmin.v1" "roles/logging.viewer" "roles/logging.logWriter")
      cron_schedule="${VM_STOPPER_SCHEDULE}"
      ;;
  esac

  local runner_sa_email="${GLOBAL_RUNNER_SA}"
  if [[ -z "${runner_sa_email}" ]]; then
    runner_sa_email="${default_runner_sa_name}@${PROJECT_ID}.iam.gserviceaccount.com"
  fi

  local sched_sa_email="${GLOBAL_SCHEDULER_SA}"
  if [[ -z "${sched_sa_email}" ]]; then
    sched_sa_email="${default_sched_sa_name}@${PROJECT_ID}.iam.gserviceaccount.com"
  fi

  # 2. Provision Runner Service Account and IAM Roles
  log_info "Configuring runtime service account '${runner_sa_email}'..."
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud iam service-accounts describe ${runner_sa_email} --project=${PROJECT_ID}"
    log_dry_run "gcloud iam service-accounts create ${default_runner_sa_name} --project=${PROJECT_ID} --display-name=\"${svc} Runtime SA\""
  else
    if ! gcloud iam service-accounts describe "${runner_sa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
      log_info "Creating runtime service account '${runner_sa_email}'..."
      gcloud iam service-accounts create "${default_runner_sa_name}" \
        --project="${PROJECT_ID}" \
        --display-name="${svc} Runtime SA"
    fi
  fi

  for role in "${iam_roles[@]}"; do
    ensure_project_iam_role "${PROJECT_ID}" "serviceAccount:${runner_sa_email}" "${role}"
  done

  # 3. Provision Scheduler Invoker Service Account
  log_info "Configuring scheduler invoker service account '${sched_sa_email}'..."
  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud iam service-accounts describe ${sched_sa_email} --project=${PROJECT_ID}"
    log_dry_run "gcloud iam service-accounts create ${default_sched_sa_name} --project=${PROJECT_ID} --display-name=\"${svc} Scheduler Invoker SA\""
  else
    if ! gcloud iam service-accounts describe "${sched_sa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
      log_info "Creating scheduler service account '${sched_sa_email}'..."
      gcloud iam service-accounts create "${default_sched_sa_name}" \
        --project="${PROJECT_ID}" \
        --display-name="${svc} Scheduler Invoker SA"
    fi
  fi

  # 4. Build and Publish Container Image
  local image_uri="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${svc}:latest"
  log_info "Building container image '${image_uri}' via Cloud Build..."
  execute_cmd gcloud builds submit "${svc_dir}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --tag="${image_uri}"

  # 5. Deploy Cloud Run Service
  log_info "Deploying Cloud Run service '${svc}' to region '${REGION}'..."
  execute_cmd gcloud run deploy "${svc}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --image="${image_uri}" \
    --platform=managed \
    --service-account="${runner_sa_email}" \
    --no-allow-unauthenticated \
    --timeout=540s \
    --memory=512Mi \
    --set-env-vars="${env_vars}" \
    --quiet

  # 6. Retrieve Service URL and Grant Invoker Role to Scheduler SA
  local service_url
  if [[ "${DRY_RUN}" == "true" ]]; then
    service_url="https://${svc}-preview-${REGION}.a.run.app"
    log_dry_run "gcloud run services describe ${svc} --project=${PROJECT_ID} --region=${REGION} --format='value(status.url)'"
  else
    service_url="$(gcloud run services describe "${svc}" --project="${PROJECT_ID}" --region="${REGION}" --format="value(status.url)")"
    if [[ -z "${service_url}" ]]; then
      error_exit "Failed to resolve Cloud Run service URL for '${svc}'."
    fi
  fi

  ensure_cloud_run_invoker "${svc}" "${PROJECT_ID}" "${REGION}" "serviceAccount:${sched_sa_email}"

  # 7. Configure Cloud Scheduler Job
  local job_name="${svc}-scheduler"
  log_info "Configuring Cloud Scheduler job '${job_name}' (${cron_schedule})..."

  local sched_args=(
    --project="${PROJECT_ID}"
    --location="${REGION}"
    --schedule="${cron_schedule}"
    --time-zone="UTC"
    --uri="${service_url}"
    --http-method=POST
    --message-body="${payload}"
    --oidc-service-account-email="${sched_sa_email}"
    --oidc-token-audience="${service_url}"
  )

  if [[ "${DRY_RUN}" == "true" ]]; then
    log_dry_run "gcloud scheduler jobs describe ${job_name} --project=${PROJECT_ID} --location=${REGION}"
    log_dry_run "gcloud scheduler jobs create/update http ${job_name} ${sched_args[*]} --headers=\"Content-Type=application/json\" --quiet"
  else
    if gcloud scheduler jobs describe "${job_name}" --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1; then
      log_info "Updating existing Cloud Scheduler job '${job_name}'..."
      gcloud scheduler jobs update http "${job_name}" "${sched_args[@]}" --update-headers="Content-Type=application/json" --quiet
    else
      log_info "Creating new Cloud Scheduler job '${job_name}'..."
      gcloud scheduler jobs create http "${job_name}" "${sched_args[@]}" --headers="Content-Type=application/json" --quiet
    fi
  fi

  # Record summary entry
  SUMMARY_SERVICES+=("${svc}")
  SUMMARY_URLS+=("${service_url}")
  SUMMARY_JOBS+=("${job_name}")
  SUMMARY_SCHEDULES+=("${cron_schedule}")

  log_success "Deployment and scheduling configured for '${svc}'."
}

# --- Step 5: Summary Report ---
print_summary() {
  log_info "================================================================="
  log_success "All requested services processed successfully!"
  log_info "================================================================="
  echo ""
  echo -e "${COLOR_BOLD}Deployment Summary:${COLOR_RESET}"
  printf "%-30s | %-45s | %-32s | %-12s\n" "Service Name" "Service URL" "Scheduler Job" "Schedule"
  echo "------------------------------------------------------------------------------------------------------------------------"
  for i in "${!SUMMARY_SERVICES[@]}"; do
    printf "%-30s | %-45s | %-32s | %-12s\n" \
      "${SUMMARY_SERVICES[$i]}" \
      "${SUMMARY_URLS[$i]}" \
      "${SUMMARY_JOBS[$i]}" \
      "${SUMMARY_SCHEDULES[$i]}"
  done
  echo "------------------------------------------------------------------------------------------------------------------------"
  echo -e "${COLOR_BOLD}Target Project:${COLOR_RESET}     ${PROJECT_ID}"
  echo -e "${COLOR_BOLD}Region:${COLOR_RESET}             ${REGION}"
  echo -e "${COLOR_BOLD}Dry Run Mode:${COLOR_RESET}       ${DRY_RUN}"
  echo ""
}

# --- Main Entrypoint ---
main() {
  parse_args "$@"
  resolve_and_validate_services
  resolve_project_and_region

  run_offline_unit_tests
  check_prerequisites_and_apis
  setup_artifact_registry

  for svc in "${TARGET_SERVICES[@]}"; do
    deploy_service "${svc}"
  done

  print_summary
}

main "$@"
