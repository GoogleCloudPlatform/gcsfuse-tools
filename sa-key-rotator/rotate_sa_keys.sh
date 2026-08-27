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

set -euo pipefail

# ==============================================================================
# Global Variables & State Tracking
# ==============================================================================
IS_DRY_RUN=true
HAS_ERRORS=false
declare -a PARSED_CONFIGS=()
declare -a KEY_RESULTS=()
declare -a CLEANUP_RESULTS=()

# State variables populated by step functions
ACTIVE_KEY_ID=""
ACTIVE_EXPIRES_AT=""
ACTIVE_SECRET_VERSION_ID=""
GENERATED_KEY_JSON=""

# ==============================================================================
# Helper Functions
# ==============================================================================

print_leading_padding() {
  # Prints 10 blank lines to visually isolate consecutive Cloud Run job invocation logs
  for _ in {1..10}; do
    echo ""
  done
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Rotates Service Account keys in Secret Manager for GCSFuse integration tests.
Targets and execution mode are configured via environment variables.

SAFETY DEFAULT: The script executes in DRY RUN mode by default unless explicitly
instructed to execute live rotation via DRY_RUN="false".

AUTO-CREATION: If a configured secret does not exist in Secret Manager, it is
automatically created with automatic replication before adding the new key version.

Environment Variables:
  SECRET_CONFIGS  Comma- or newline-separated list of "<SECRET_NAME>|<SA_NAME>|<PROJECT_ID>" tuples.
  DRY_RUN         Set to "false" to execute live key rotation (default: true / dry run).

Options:
  -h, --help      Display this help message and exit.

Examples:
  # Dry run (default mode):
  export SECRET_CONFIGS="gcsfuse-integration-tests|creds-integration-tests|gcs-fuse-test"
  $(basename "$0")

  # Live rotation:
  export SECRET_CONFIGS="gcsfuse-integration-tests|creds-integration-tests|gcs-fuse-test"
  export DRY_RUN="false"
  $(basename "$0")
EOF
}

parse_arguments_and_env() {
  # Parse DRY_RUN from environment variable (Defaults to true for safety)
  local dry_run_val
  dry_run_val=$(echo "${DRY_RUN:-true}" | tr '[:upper:]' '[:lower:]' | xargs)
  case "${dry_run_val}" in
    false|0|no)
      IS_DRY_RUN=false
      ;;
    *)
      IS_DRY_RUN=true
      ;;
  esac

  # Parse command line options
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage
        exit 1
        ;;
    esac
  done
}

validate_and_parse_configs() {
  local raw_configs="${SECRET_CONFIGS:-}"
  if [[ -z "${raw_configs//[$'\t\r\n ']/}" ]]; then
    echo "ERROR: Environment variable 'SECRET_CONFIGS' is required but not set or empty." >&2
    echo "Expected format: SECRET_CONFIGS=\"<SECRET_NAME>|<SA_NAME>|<PROJECT_ID>, ...\"" >&2
    exit 1
  fi

  # Parse delimited string (handles commas, newlines, semicolons) into an array
  local raw_entries=()
  IFS=',' read -ra raw_entries <<< "${raw_configs//[$'\r\n;']/,}"

  for entry in "${raw_entries[@]}"; do
    entry=$(echo "${entry}" | xargs)
    [[ -z "${entry}" ]] && continue

    # Verify exact 3-field tuple structure: <SECRET_NAME>|<SA_NAME>|<PROJECT_ID>
    local sec_name sa_id proj_id extra
    IFS='|' read -r sec_name sa_id proj_id extra <<< "${entry}"
    sec_name=$(echo "${sec_name:-}" | xargs)
    sa_id=$(echo "${sa_id:-}" | xargs)
    proj_id=$(echo "${proj_id:-}" | xargs)

    if [[ -z "${sec_name}" || -z "${sa_id}" || -z "${proj_id}" || -n "${extra:-}" ]]; then
      echo "ERROR: Malformed configuration tuple in SECRET_CONFIGS: '${entry}'" >&2
      echo "Expected format for each target: '<SECRET_NAME>|<SA_NAME>|<PROJECT_ID>'" >&2
      exit 1
    fi

    PARSED_CONFIGS+=("${sec_name}|${sa_id}|${proj_id}")
  done

  if [[ ${#PARSED_CONFIGS[@]} -eq 0 ]]; then
    echo "ERROR: No valid target configurations found in SECRET_CONFIGS." >&2
    exit 1
  fi
}

# ==============================================================================
# Task-Specific Core Functions
# ==============================================================================

inspect_secret_dry_run() {
  local secret_name="$1"
  local sa_email="$2"
  local project_id="$3"

  ACTIVE_KEY_ID=""
  ACTIVE_EXPIRES_AT=""
  ACTIVE_SECRET_VERSION_ID=""

  echo "  [Step 1/3] Checking Secret Manager secret status..."
  if ! gcloud secrets describe "${secret_name}" --project="${project_id}" --quiet >/dev/null 2>&1; then
    echo "        [DRY RUN] Secret '${secret_name}' does NOT exist in project '${project_id}'"
    echo "        [DRY RUN] Live mode will automatically create the secret with replication: automatic."
    KEY_RESULTS+=("${project_id}|${secret_name}|${sa_email}|NOT_FOUND|UNKNOWN|NOT_FOUND(WOULD_CREATE)")
    return 1
  fi

  ACTIVE_SECRET_VERSION_ID=$(gcloud secrets versions describe "latest" \
    --secret="${secret_name}" \
    --project="${project_id}" \
    --format="value(name.basename())" \
    --quiet 2>/dev/null || true)

  echo "        Fetching current secret payload..."
  local secret_payload
  secret_payload=$(gcloud secrets versions access latest \
    --secret="${secret_name}" \
    --project="${project_id}" \
    --quiet 2>/dev/null || true)

  if [[ -z "${secret_payload}" ]]; then
    echo "        [WARNING] Secret '${secret_name}' exists but has no active versions enabled."
    KEY_RESULTS+=("${project_id}|${secret_name}|${sa_email}|NO_VERSIONS|UNKNOWN|EMPTY_SECRET")
    return 1
  fi

  ACTIVE_KEY_ID=$(echo "${secret_payload}" | jq -r '.private_key_id // empty' 2>/dev/null || true)
  if [[ -z "${ACTIVE_KEY_ID}" ]]; then
    echo "        [ERROR] Could not parse 'private_key_id' from secret payload"
    KEY_RESULTS+=("${project_id}|${secret_name}|${sa_email}|PARSE_ERROR|UNKNOWN|PAYLOAD_ERROR")
    HAS_ERRORS=true
    return 1
  fi

  echo "        Active Key ID in Secret: ${ACTIVE_KEY_ID}"
  ACTIVE_EXPIRES_AT=$(gcloud iam service-accounts keys list \
    --iam-account="${sa_email}" \
    --project="${project_id}" \
    --filter="name ~ ${ACTIVE_KEY_ID}" \
    --format="value(validBeforeTime)" \
    --quiet 2>/dev/null || true)

  local status="ACTIVE_IN_SECRET"
  if [[ -z "${ACTIVE_EXPIRES_AT}" ]]; then
    ACTIVE_EXPIRES_AT="KEY_NOT_IN_IAM"
    status="NOT_FOUND_IN_IAM"
    HAS_ERRORS=true
  fi

  echo "        Active Key Expiry:       ${ACTIVE_EXPIRES_AT} [${status}]"
  KEY_RESULTS+=("${project_id}|${secret_name}|${sa_email}|${ACTIVE_KEY_ID:0:12}...|${ACTIVE_EXPIRES_AT}|${status}")
  return 0
}

generate_sa_key_in_memory() {
  local sa_email="$1"
  local project_id="$2"

  ACTIVE_KEY_ID=""
  GENERATED_KEY_JSON=""

  echo "  [Step 1/4] Creating new service account key in memory..."
  local err_out
  err_out=$(mktemp)

  local key_json
  if ! key_json=$(gcloud iam service-accounts keys create /dev/stdout \
    --iam-account="${sa_email}" \
    --project="${project_id}" \
    --quiet 2>"${err_out}"); then
    echo "  [ERROR] Failed to generate key for ${sa_email}:" >&2
    cat "${err_out}" >&2
    rm -f "${err_out}"
    return 1
  fi
  rm -f "${err_out}"

  local key_id
  key_id=$(echo "${key_json}" | jq -r '.private_key_id // empty' 2>/dev/null || true)

  if [[ -z "${key_id}" ]]; then
    echo "  [ERROR] Failed to parse private key ID for ${sa_email} from generated JSON." >&2
    return 1
  fi

  echo "        Successfully generated new key ID: ${key_id}"
  ACTIVE_KEY_ID="${key_id}"
  GENERATED_KEY_JSON="${key_json}"
  return 0
}

ensure_secret_and_add_version() {
  local secret_name="$1"
  local project_id="$2"
  local key_json="$3"

  echo ""
  echo "  [Step 2/4] Uploading new key version to Secret Manager secret [${secret_name}]..."
  if ! gcloud secrets describe "${secret_name}" --project="${project_id}" --quiet >/dev/null 2>&1; then
    echo "        Secret '${secret_name}' not found in '${project_id}'. Creating secret with automatic replication..."
    local create_output
    if ! create_output=$(gcloud secrets create "${secret_name}" \
      --replication-policy=automatic \
      --project="${project_id}" \
      --quiet 2>&1); then
      echo "  [ERROR] Failed to create secret '${secret_name}': ${create_output}" >&2
      return 1
    fi
    echo "        Successfully created secret '${secret_name}'."
  fi

  local version_add_output
  if ! version_add_output=$(printf "%s" "${key_json}" | gcloud secrets versions add "${secret_name}" \
    --data-file=- \
    --project="${project_id}" \
    --quiet 2>&1); then
    echo "  [ERROR] Failed to add secret version: ${version_add_output}" >&2
    return 1
  fi
  echo "        ${version_add_output}"

  ACTIVE_SECRET_VERSION_ID=$(gcloud secrets versions describe "latest" \
    --secret="${secret_name}" \
    --project="${project_id}" \
    --format="value(name.basename())" \
    --quiet 2>/dev/null || true)
  return 0
}

prune_older_secret_versions() {
  local secret_name="$1"
  local project_id="$2"
  local latest_version_id="$3"

  if [[ -z "${latest_version_id}" ]]; then
    latest_version_id=$(gcloud secrets versions describe "latest" \
      --secret="${secret_name}" \
      --project="${project_id}" \
      --format="value(name.basename())" \
      --quiet 2>/dev/null || true)
  fi

  if [[ -z "${latest_version_id}" ]]; then
    echo "        [WARNING] Could not determine latest version for secret '${secret_name}' in '${project_id}'"
    return 0
  fi

  if [[ "${IS_DRY_RUN}" != "true" ]]; then
    echo ""
    echo "  [Step 3/4] Pruning older Secret Manager versions (keeping only latest: Version ${latest_version_id})..."
  else
    echo ""
    echo "  [Step 2/3] Inspecting older Secret Manager versions (keeping only latest: Version ${latest_version_id})..."
  fi

  local versions_list
  versions_list=$(gcloud secrets versions list "${secret_name}" \
    --project="${project_id}" \
    --filter="state != DESTROYED" \
    --format="csv[no-heading](name.basename(),state)" \
    --quiet 2>/dev/null || true)

  while IFS="," read -r v_id v_state; do
    [[ -z "${v_id}" ]] && continue

    if [[ "${v_id}" == "${latest_version_id}" ]]; then
      echo "        Preserving active version: Version ${v_id} [${v_state}]"
      continue
    fi

    if [[ "${IS_DRY_RUN}" == "true" ]]; then
      echo "        [DRY RUN] Would destroy:  Version ${v_id} [${v_state}]"
      CLEANUP_RESULTS+=("SECRET_VERSION|${project_id}|${secret_name}|Version ${v_id}|WOULD_DESTROY")
    else
      echo "        Destroying old version:   Version ${v_id} [${v_state}]..."
      if gcloud secrets versions destroy "${v_id}" \
          --secret="${secret_name}" \
          --project="${project_id}" \
          --quiet >/dev/null 2>&1; then
        echo "        Destroyed old version:    Version ${v_id}"
        CLEANUP_RESULTS+=("SECRET_VERSION|${project_id}|${secret_name}|Version ${v_id}|DESTROYED")
      else
        echo "        [WARNING] Failed destroy: Version ${v_id}"
        CLEANUP_RESULTS+=("SECRET_VERSION|${project_id}|${secret_name}|Version ${v_id}|FAILED")
        HAS_ERRORS=true
      fi
    fi
  done <<< "${versions_list}"
}

prune_older_iam_keys() {
  local sa_email="$1"
  local project_id="$2"
  local active_key_id="$3"
  local secret_name="$4"

  if [[ -z "${active_key_id}" ]]; then
    echo "        [ERROR] Active key ID is empty. Skipping IAM key pruning to prevent accidental deletion of all keys." >&2
    HAS_ERRORS=true
    return 0
  fi

  if [[ "${IS_DRY_RUN}" != "true" ]]; then
    echo ""
    echo "  [Step 4/4] Pruning older user-managed IAM keys (keeping only active key)..."
  else
    echo ""
    echo "  [Step 3/3] Inspecting user-managed keys in IAM (keeping only active key)..."
  fi

  local keys_list=""
  local active_expiry=""
  local retries=0

  # Query IAM keys with retry to account for global replication latency on newly created keys
  while [[ ${retries} -lt 4 ]]; do
    keys_list=$(gcloud iam service-accounts keys list \
      --iam-account="${sa_email}" \
      --project="${project_id}" \
      --managed-by=user \
      --format="csv[no-heading](name.basename(),validBeforeTime)" \
      --quiet 2>/dev/null || true)

    if [[ -z "${active_key_id}" ]] || echo "${keys_list}" | grep -q "${active_key_id}"; then
      break
    fi

    retries=$((retries + 1))
    sleep 1
  done

  while IFS="," read -r k_id k_exp; do
    [[ -z "${k_id}" ]] && continue

    # If this is the active key, capture its exact expiration timestamp
    if [[ "${k_id}" == "${active_key_id}" ]]; then
      if [[ -n "${k_exp}" ]]; then
        active_expiry="${k_exp}"
      fi
      echo "        Preserving active key:   ${k_id} (expires: ${active_expiry:-Unknown})"
      continue
    fi

    if [[ "${IS_DRY_RUN}" == "true" ]]; then
      echo "        [DRY RUN] Would delete:  ${k_id} (expires: ${k_exp})"
      CLEANUP_RESULTS+=("IAM_KEY|${project_id}|${sa_email}|${k_id:0:12}...|WOULD_DELETE")
    else
      echo "        Deleting old key:        ${k_id} (expires: ${k_exp})..."
      if gcloud iam service-accounts keys delete "${k_id}" \
          --iam-account="${sa_email}" \
          --project="${project_id}" \
          --quiet 2>/dev/null; then
        echo "        Deleted old key:         ${k_id}"
        CLEANUP_RESULTS+=("IAM_KEY|${project_id}|${sa_email}|${k_id:0:12}...|DELETED")
      else
        echo "        [WARNING] Failed delete: ${k_id}"
        CLEANUP_RESULTS+=("IAM_KEY|${project_id}|${sa_email}|${k_id:0:12}...|FAILED")
        HAS_ERRORS=true
      fi
    fi
  done <<< "${keys_list}"

  if [[ "${IS_DRY_RUN}" != "true" ]]; then
    if [[ -z "${active_expiry}" ]]; then
      active_expiry="Unknown"
    fi
    echo "        Active key expires at:   ${active_expiry}"
    KEY_RESULTS+=("${project_id}|${secret_name}|${sa_email}|${active_key_id:0:12}...|${active_expiry}|ACTIVE")
  fi
}

rotate_single_target() {
  local target_idx="$1"
  local total_targets="$2"
  local config_tuple="$3"

  local secret_name sa_name project_id
  IFS="|" read -r secret_name sa_name project_id <<< "${config_tuple}"

  local sa_email
  if [[ "$sa_name" == *"@"* ]]; then
    sa_email="$sa_name"
  else
    sa_email="${sa_name}@${project_id}.iam.gserviceaccount.com"
  fi

  echo ""
  echo "========================================================================================================="
  echo "  [TARGET ${target_idx}/${total_targets}] Secret: ${secret_name} | SA: ${sa_email} | Project: ${project_id}"
  echo "========================================================================================================="
  echo "  • Secret Name:     ${secret_name}"
  echo "  • Service Account: ${sa_email}"
  echo "  • GCP Project:     ${project_id}"
  echo "---------------------------------------------------------------------------------------------------------"

  if [[ "${IS_DRY_RUN}" == "true" ]]; then
    if ! inspect_secret_dry_run "${secret_name}" "${sa_email}" "${project_id}"; then
      echo "========================================================================================================="
      return 0
    fi
    prune_older_secret_versions "${secret_name}" "${project_id}" "${ACTIVE_SECRET_VERSION_ID}"
    prune_older_iam_keys "${sa_email}" "${project_id}" "${ACTIVE_KEY_ID}" "${secret_name}"
  else
    # 1. Generate key in memory
    if ! generate_sa_key_in_memory "${sa_email}" "${project_id}"; then
      KEY_RESULTS+=("${project_id}|${secret_name}|${sa_email}|FAILED|FAILED|KEY_GEN_ERROR")
      HAS_ERRORS=true
      echo "========================================================================================================="
      return 0
    fi

    # 2. Upload to Secret Manager (auto-creating secret if missing)
    if ! ensure_secret_and_add_version "${secret_name}" "${project_id}" "${GENERATED_KEY_JSON}"; then
      KEY_RESULTS+=("${project_id}|${secret_name}|${sa_email}|${ACTIVE_KEY_ID:0:12}...|FAILED|SECRET_ADD_ERROR")
      HAS_ERRORS=true
      echo "========================================================================================================="
      return 0
    fi

    # 3. Destroy older Secret Manager versions
    prune_older_secret_versions "${secret_name}" "${project_id}" "${ACTIVE_SECRET_VERSION_ID}"

    # 4. Prune old IAM keys and record expiry
    prune_older_iam_keys "${sa_email}" "${project_id}" "${ACTIVE_KEY_ID}" "${secret_name}"
  fi

  echo "========================================================================================================="
}

render_table() {
  local title="$1"
  local header="$2"
  shift 2
  local rows=("$@")

  [[ ${#rows[@]} -eq 0 ]] && return 0

  if command -v column >/dev/null 2>&1; then
    local formatted
    formatted=$( (echo "${header}"; printf "%s\n" "${rows[@]}") | column -t -s "|" -o " | " )
    local header_line
    header_line=$(echo "${formatted}" | head -n 1)
    local width=${#header_line}
    local equals dashes
    equals=$(printf "%0.s=" $(seq 1 "${width}"))
    dashes=$(printf "%0.s-" $(seq 1 "${width}"))

    echo ""
    echo "${equals}"
    printf "%*s\n" $(( (width + ${#title}) / 2 )) "${title}"
    echo "${equals}"
    echo "${header_line}"
    echo "${dashes}"
    echo "${formatted}" | tail -n +2
    echo "${equals}"
  else
    echo ""
    echo "========================================================================================================="
    echo "  ${title}"
    echo "========================================================================================================="
    echo "${header}"
    echo "---------------------------------------------------------------------------------------------------------"
    printf "%s\n" "${rows[@]}"
    echo "========================================================================================================="
  fi
}

print_summary_tables() {
  local t1_title="Summary of Active Secret Manager Keys"
  if [[ "${IS_DRY_RUN}" == "true" ]]; then
    t1_title="Summary of Active Secret Manager Keys (DRY RUN)"
  fi

  render_table "${t1_title}" \
    "PROJECT|SECRET NAME|SERVICE ACCOUNT|KEY ID|EXPIRY (UTC)|STATUS" \
    "${KEY_RESULTS[@]}"

  if [[ ${#CLEANUP_RESULTS[@]} -gt 0 ]]; then
    local t2_title="Summary of Old/Non-Active Resources Cleaned Up"
    if [[ "${IS_DRY_RUN}" == "true" ]]; then
      t2_title="Summary of Old/Non-Active Resources Cleaned Up (DRY RUN)"
    fi
    render_table "${t2_title}" \
      "RESOURCE TYPE|PROJECT|RESOURCE NAME|VERSION / KEY ID|ACTION" \
      "${CLEANUP_RESULTS[@]}"
  fi
}

# ==============================================================================
# Main Orchestrator
# ==============================================================================

main() {
  parse_arguments_and_env "$@"
  print_leading_padding
  validate_and_parse_configs

  local total_targets=${#PARSED_CONFIGS[@]}

  echo "========================================================================================================="
  if [[ "${IS_DRY_RUN}" == "true" ]]; then
    echo "                       DRY RUN MODE (DEFAULT): Inspecting Keys and Expiry Status                         "
  else
    echo "                       LIVE MODE: Starting Key Rotation and Secret Manager Update                        "
  fi
  echo "========================================================================================================="
  echo "Loaded ${total_targets} target configuration(s) from SECRET_CONFIGS environment variable."

  local idx=0
  for config in "${PARSED_CONFIGS[@]}"; do
    idx=$((idx + 1))
    rotate_single_target "${idx}" "${total_targets}" "${config}"
  done

  print_summary_tables

  if [[ "${HAS_ERRORS}" == "true" ]]; then
    echo ""
    echo "Execution completed with one or more errors." >&2
    exit 1
  fi
}

main "$@"
