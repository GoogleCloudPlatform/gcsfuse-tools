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
    echo ""
    echo "Example:"
    echo "  bash create_benchmark_tables.sh rw-benchmark-v3 us-south1 c4-standard-96 'gVNIC+ tier_1 networking (200Gbps)' 'Hyperdisk balanced'"
    exit 1
fi

rm -rf /tmp/create_benchmark_tables* # Remove previous directories.
TMP_DIR=$(mktemp -d -t create_benchmark_tables.XXXXXX)
TABLES_FILE="${TMP_DIR}/tables.md"
RESULTS_BUCKET_NAME="gcsfuse-release-benchmarks-results"
GCSFUSE_VERSION=$1
REGION=$2
MACHINE_TYPE=$3
NETWORKING=$4
DISK_TYPE=$5

# Files to read from GCS.
RANDOM_READ_RES="gcsfuse-random-read-workload-benchmark.json"
SEQ_READ_RES="gcsfuse-sequential-read-workload-benchmark.json"
WRITE_RES="gcsfuse-write-workload-benchmark.json"

if gcloud storage objects describe "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/success.txt" &>/dev/null; then
    echo "Found the success.txt file in the bucket."
else
    echo "Unable to locate success.txt file in the bucket. Exiting..."
    exit 1
fi

gcloud storage cp "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/${RANDOM_READ_RES}" "${TMP_DIR}/${RANDOM_READ_RES}"
gcloud storage cp "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/${SEQ_READ_RES}" "${TMP_DIR}/${SEQ_READ_RES}"
gcloud storage cp "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/${WRITE_RES}" "${TMP_DIR}/${WRITE_RES}"

RANDOM_READ_RES="${TMP_DIR}/${RANDOM_READ_RES}"
SEQ_READ_RES="${TMP_DIR}/${SEQ_READ_RES}"
WRITE_RES="${TMP_DIR}/${WRITE_RES}"

# Helper methods
# Get size of jobs
job_count() {
    local file=$1
    job_count=$(jq '.jobs | length' "$file")
    if [[ "$job_count" -le 0 ]]; then 
        echo "Exiting found no jobs in experiment result file: $file"
        exit 1
    fi
    echo "$job_count"
    return 0
}

ROW_COUNT=0
filesize=()
bw_bytes=()
nrfiles=()
iops=()
mean=()
bs=()

populate_column_from_job_options() {
    local file=$1
    local field_to_extract=$2
    local -n array_ref=$3
    array_ref=() # clear the array
    local jq_output
    jq_output=$(jq -r --arg jq_field_name "$field_to_extract" '.jobs[]?."job options"[$jq_field_name]?' "$file")
    while IFS= read -r value; do
        if [[ "$value" == "null" ]]; then
            # try getting from global options.
            value=$(jq -r --arg jq_field_name "$field_to_extract" '."global options"[$jq_field_name]?' "$file")
        fi
        if [[ "$value" == "null" ]]; then 
            echo "value is null for field [$field_to_extract] from file [$file] and not found in global options."
            exit 1
        fi
        array_ref+=("$value")
    done <<< "$jq_output"
    if [[ "${#array_ref[@]}" -ne "$ROW_COUNT" ]]; then 
        echo "not enough rows for field [$field_to_extract] for file [$file]."
        exit 1
    fi
    return 0
}

populate_column_from_read() {
    local file=$1
    local field_to_extract=$2
    local -n array_ref=$3
    array_ref=() # clear the array
    local jq_output
    jq_output=$(jq -r --arg jq_field_name "$field_to_extract" '.jobs[]?.read[$jq_field_name]?' "$file")
    while IFS= read -r value; do
        if [[ "$value" == "null" ]]; then
            # try getting from global options.
            value=$(jq -r --arg jq_field_name "$field_to_extract" '."global options"[$jq_field_name]?' "$file")
        fi
        if [[ "$value" == "null" ]]; then 
            echo "value is null for field [$field_to_extract] from file [$file] and not found in global options."
            exit 1
        fi
        array_ref+=("$value")
    done <<< "$jq_output"
    if [[ "${#array_ref[@]}" -ne "$ROW_COUNT" ]]; then 
        echo "not enough rows for field [$field_to_extract] for file [$file]."
        exit 1
    fi
    return 0
}

populate_column_from_read_clat_ns() {
    local file=$1
    local field_to_extract=$2
    local -n array_ref=$3
    array_ref=() # clear the array
    local jq_output
    jq_output=$(jq -r --arg jq_field_name "$field_to_extract" '.jobs[]?.read.clat_ns[$jq_field_name]?' "$file")
    while IFS= read -r value; do
        if [[ "$value" == "null" ]]; then
            # try getting from global options.
            value=$(jq -r --arg jq_field_name "$field_to_extract" '."global options"[$jq_field_name]?' "$file")
        fi
        if [[ "$value" == "null" ]]; then 
            echo "value is null for field [$field_to_extract] from file [$file] and not found in global options."
            exit 1
        fi
        array_ref+=("$value")
    done <<< "$jq_output"
    if [[ "${#array_ref[@]}" -ne "$ROW_COUNT" ]]; then 
        echo "not enough rows for field [$field_to_extract] for file [$file]."
        exit 1
    fi
    return 0
}

populate_column_from_write() {
    local file=$1
    local field_to_extract=$2
    local -n array_ref=$3
    array_ref=() # clear the array
    local jq_output
    jq_output=$(jq -r --arg jq_field_name "$field_to_extract" '.jobs[]?.write[$jq_field_name]?' "$file")
    while IFS= read -r value; do
        if [[ "$value" == "null" ]]; then
            # try getting from global options.
            value=$(jq -r --arg jq_field_name "$field_to_extract" '."global options"[$jq_field_name]?' "$file")
        fi
        if [[ "$value" == "null" ]]; then 
            echo "value is null for field [$field_to_extract] from file [$file] and not found in global options."
            exit 1
        fi
        array_ref+=("$value")
    done <<< "$jq_output"
    if [[ "${#array_ref[@]}" -ne "$ROW_COUNT" ]]; then 
        echo "not enough rows for field [$field_to_extract] for file [$file]."
        exit 1
    fi
    return 0
}

populate_column_from_write_clat_ns() {
    local file=$1
    local field_to_extract=$2
    local -n array_ref=$3
    array_ref=() # clear the array
    local jq_output
    jq_output=$(jq -r --arg jq_field_name "$field_to_extract" '.jobs[]?.write.clat_ns[$jq_field_name]?' "$file")
    while IFS= read -r value; do
        if [[ "$value" == "null" ]]; then
            # try getting from global options.
            value=$(jq -r --arg jq_field_name "$field_to_extract" '."global options"[$jq_field_name]?' "$file")
        fi
        if [[ "$value" == "null" ]]; then 
            echo "value is null for field [$field_to_extract] from file [$file] and not found in global options."
            exit 1
        fi
        array_ref+=("$value")
    done <<< "$jq_output"
    if [[ "${#array_ref[@]}" -ne "$ROW_COUNT" ]]; then 
        echo "not enough rows for field [$field_to_extract] for file [$file]."
        exit 1
    fi
    return 0
}

populate_all_columns_common() {
    local file="$1"
    populate_column_from_job_options "$file" "bs" "bs"
    populate_column_from_job_options "$file" "filesize" "filesize"
    populate_column_from_job_options "$file" "nrfiles" "nrfiles"
}

populate_all_columns_for_read() {
    local file="$1"
    populate_all_columns_common "$file"
    populate_column_from_read "$file" "bw_bytes" "bw_bytes"
    populate_column_from_read "$file" "iops" "iops"
    populate_column_from_read_clat_ns "$file" "mean" "mean"
}

populate_all_columns_for_write() {
    local file="$1"
    populate_all_columns_common "$file"
    populate_column_from_write "$file" "bw_bytes" "bw_bytes"
    populate_column_from_write "$file" "iops" "iops"
    populate_column_from_write_clat_ns "$file" "mean" "mean"
}

convert_to_gib() {
    local precision=3 bytes_value
    for ((i = 0; i < ROW_COUNT; i++)); do
        bytes_value=${bw_bytes[i]}
        bw_bytes[i]=$(awk -v prec="$precision" -v bval="$bytes_value" 'BEGIN { printf "%.*f\n", prec, bval / 1073741824 }')
    done
}

convert_iops() {
    local iops_value
    for ((i = 0; i < ROW_COUNT; i++)); do
        iops_value=${iops[i]}
        iops[i]=$(awk -v n="$iops_value" 'BEGIN { printf "%.2fK\n", n / 1000 }')
    done
}

convert_mean() {
    local precision=2 mean_value
    for ((i = 0; i < ROW_COUNT; i++)); do
        mean_value=${mean[i]}
        mean[i]=$(awk -v p="$precision" -v ns="$mean_value" 'BEGIN { printf "%.*fms\n", p, ns / 1E6 }')
    done
}

create_table() {
    local table_name="$1"
    local file="$2"
    local workflow_type="$3"
    echo "### $table_name"
    ROW_COUNT=$(job_count "$file")
    if [[ "$workflow_type" == "read" ]]; then
        populate_all_columns_for_read "$file"
    else
        populate_all_columns_for_write "$file"
    fi
    convert_to_gib
    convert_iops
    convert_mean
    echo "| File Size | BlockSize | nrfiles |Bandwidth in (GiB/sec) | IOPs  |  Avg Latency (msec) |"
    echo "|---|---|---|---|---|---|"
    for ((i = 0; i < ROW_COUNT; i++)); do
        echo "| ${filesize[i]} | ${bs[i]} | ${nrfiles[i]} | ${bw_bytes[i]} | ${iops[i]} | ${mean[i]} |"
    done
    echo ""
}

create_tables() {
    echo "## GCSFuse Benchmarking on ${MACHINE_TYPE%%-*} machine-type"
    echo "* VM Type: ${MACHINE_TYPE}"
    echo "* VM location: ${REGION}"
    echo "* Networking: ${NETWORKING}"
    echo "* Disk Type: ${DISK_TYPE}"
    echo "* GCS Bucket location: ${REGION}"
    echo ""
    create_table "Sequential Reads" "$SEQ_READ_RES" "read"
    create_table "Random Reads" "$RANDOM_READ_RES" "read"
    create_table "Sequential Writes" "$WRITE_RES" "write"
}

create_tables > "${TABLES_FILE}"

cat "${TABLES_FILE}"
# copy file to results bucket

gcloud storage cp "${TABLES_FILE}" "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/"