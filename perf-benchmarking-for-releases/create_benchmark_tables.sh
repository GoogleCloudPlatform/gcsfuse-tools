#!/bin/bash

# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Fail on anything unexpected.
set -euo pipefail

# Script Documentation
if [ "$#" -ne 5 ]; then
    echo "Usage: $0 <GCSFUSE_VERSION> <REGION> <MACHINE_TYPE> <NETWORKING> <DISK_TYPE>"
    echo ""
    echo "Example:"
    echo "  bash create_benchmark_tables.sh  <tag, commit-id-on-master, branch-name> us-south1 c4-standard-96 'gVNIC+ tier_1 networking (200Gbps)' 'Hyperdisk balanced'"
    exit 1
fi

TMP_DIR=$(mktemp -d -t create_benchmark_tables.XXXXXX)
trap 'rm -rf "$TMP_DIR"' EXIT # Ensure cleanup on exit

TABLES_FILE="${TMP_DIR}/tables.md"
RESULTS_BUCKET_NAME="gcsfuse-release-benchmarks-results"
GCSFUSE_VERSION=$1
REGION=$2
MACHINE_TYPE=$3
NETWORKING=$4
DISK_TYPE=$5

# Files to read from GCS. Basenames
RANDOM_READ_RES_BASENAME="gcsfuse-random-read-workload-benchmark.json"
SEQ_READ_RES_BASENAME="gcsfuse-sequential-read-workload-benchmark.json"
WRITE_RES_BASENAME="gcsfuse-write-workload-benchmark.json"

JSON_FILES_BASENAMES=(
    "$RANDOM_READ_RES_BASENAME"
    "$SEQ_READ_RES_BASENAME"
    "$WRITE_RES_BASENAME"
)

if ! gcloud storage objects describe "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/success.txt" &>/dev/null; then
    echo "Unable to locate success.txt file in the bucket gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/. Exiting..." >&2
    exit 1
fi
echo "Found the success.txt file in the bucket."

for basename in "${JSON_FILES_BASENAMES[@]}"; do
    gcloud storage cp "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/${basename}" "${TMP_DIR}/${basename}"
done

# Update variables to point to local copies
RANDOM_READ_RES="${TMP_DIR}/${RANDOM_READ_RES_BASENAME}"
SEQ_READ_RES="${TMP_DIR}/${SEQ_READ_RES_BASENAME}"
WRITE_RES="${TMP_DIR}/${WRITE_RES_BASENAME}"

# Global arrays for storing column data
ROW_COUNT=0
declare -a filesize bw_bytes nrfiles iops lat_mean bs

# Helper methods
# Get size of jobs
job_count() {
    local file=$1
    local count
    count=$(jq '.jobs | length' "$file")
    if [[ "$count" -le 0 ]]; then
        echo "Exiting: found no jobs in experiment result file: $file" >&2
        exit 1
    fi
    echo "$count"
}

# Generalized function to populate an array from jq output
# Args: $1: json_file, $2: jq_path_job_level, $3: jq_path_global_level, $4: field_to_extract, $5: array_name_ref
populate_array_from_jq() {
    local file="$1"
    local jq_job_path_template="$2" # e.g., '.jobs[]?."job options"' or '.jobs[]?.read'
    local jq_global_path_template="$3" # e.g., '."global options"'
    local field_name="$4"
    local -n array_ref="$5" # Nameref to the array to populate

    array_ref=() # Clear the array

    local jq_job_query="${jq_job_path_template}[\"${field_name}\"]?"
    local jq_global_query="${jq_global_path_template}[\"${field_name}\"]?"

    # Read values using mapfile for efficiency
    mapfile -t values < <(jq -r "$jq_job_query" "$file")

    for i in "${!values[@]}"; do
        local value="${values[i]}"
        if [[ "$value" == "null" ]]; then
            value=$(jq -r "$jq_global_query" "$file")
        fi
        if [[ "$value" == "null" ]]; then
            echo "Value is null for field [$field_name] from file [$file] (job path: $jq_job_query, global path: $jq_global_query) and not found in global options." >&2
            exit 1
        fi
        array_ref+=("$value")
    done

    if [[ "${#array_ref[@]}" -ne "$ROW_COUNT" ]]; then
        echo "Not enough rows (${#array_ref[@]} vs $ROW_COUNT) for field [$field_name] for file [$file]." >&2
        exit 1
    fi
}

# Populate all columns based on operation type (read/write)
# Args: $1: json_file, $2: operation_type ("read" or "write")
populate_all_columns() {
    local file="$1"
    local op_type="$2" # "read" or "write"
    local lat_ns_op_type="${op_type}.lat_ns" # "read.lat_ns" or "write.lat_ns"

    populate_array_from_jq "$file" '.jobs[]?."job options"' '."global options"' "bs" "bs"
    populate_array_from_jq "$file" '.jobs[]?."job options"' '."global options"' "filesize" "filesize"
    populate_array_from_jq "$file" '.jobs[]?."job options"' '."global options"' "nrfiles" "nrfiles"

    populate_array_from_jq "$file" ".jobs[]?.$op_type" '."global options"' "bw_bytes" "bw_bytes"
    populate_array_from_jq "$file" ".jobs[]?.$op_type" '."global options"' "iops" "iops"
    populate_array_from_jq "$file" ".jobs[]?.$lat_ns_op_type" '."global options"' "mean" "lat_mean"
}

declare -A bytes_in=( [MB]="1000000" [GB]="1000000000" )
convert_bytes_to() {
    local division="${bytes_in[$1]}"
    local precision=3
    for ((i = 0; i < ROW_COUNT; i++)); do
        bw_bytes[i]=$(awk -v prec="$precision" -v div="$division" -v bval="${bw_bytes[i]}" 'BEGIN { printf "%.*f\n", prec, bval / div }')
    done
}

format_iops_to_kilo() {
    for ((i = 0; i < ROW_COUNT; i++)); do
        iops[i]=$(awk -v n="${iops[i]}" 'BEGIN { printf "%.2fK\n", n / 1000 }')
    done
}

convert_lat_mean_ns_to_ms() {
    local -n lat_array_ref="$1"
    local precision=2
    for ((i = 0; i < ROW_COUNT; i++)); do
        lat_array_ref[i]=$(awk -v p="$precision" -v ns="${lat_array_ref[i]}" 'BEGIN { printf "%.*fms\n", p, ns / 1E6 }')
    done
}

create_table() {
    local table_name="$1"
    local file="$2"
    local workflow_type="$3" # "read" or "write"
    local bw_in="$4"
    echo "### $table_name"
    ROW_COUNT=$(job_count "$file")
    populate_all_columns "$file" "$workflow_type"
    convert_bytes_to "${bw_in}"
    format_iops_to_kilo
    convert_lat_mean_ns_to_ms "lat_mean"

    echo "| File Size | BlockSize | nrfiles | Bandwidth in (${bw_in}/sec) | IOPs | IOPs Avg Latency (ms) |"
    echo "|---|---|---|---|---|---|---|---|"
    for ((i = 0; i < ROW_COUNT; i++)); do
        echo "| ${filesize[i]} | ${bs[i]} | ${nrfiles[i]} | ${bw_bytes[i]} | ${iops[i]} | ${lat_mean[i]} |"
    done
    echo ""
}

create_tables_markdown_content() {
    echo "## GCSFuse Benchmarking on ${MACHINE_TYPE%%-*} machine-type"
    echo "* VM Type: ${MACHINE_TYPE}"
    echo "* VM location: ${REGION}"
    echo "* Networking: ${NETWORKING}"
    echo "* Disk Type: ${DISK_TYPE}"
    echo "* GCS Bucket location: ${REGION}"
    echo ""
    create_table "Sequential Reads" "$SEQ_READ_RES" "read" "GB"
    create_table "Random Reads" "$RANDOM_READ_RES" "read" "MB"
    create_table "Sequential Writes" "$WRITE_RES" "write" "MB"
}

create_tables_markdown_content > "${TABLES_FILE}"

# copy file to results bucket
gcloud storage cp "${TABLES_FILE}" "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/"

echo "Benchmark table successfully created and uploaded to gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/tables.md"
