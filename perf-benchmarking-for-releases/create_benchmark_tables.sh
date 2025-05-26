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

create_benchmark_tables_dir="/home/mohitkyadav_google_com/gcsfuse-tools/perf-benchmarking-for-releases/"
tables_file="${create_benchmark_tables_dir}/tables.md"
RESULTS_BUCKET_NAME="gcsfuse-release-benchmarks-results"
GCSFUSE_VERSION=$1
REGION=$2
MACHINE_TYPE=$3
NETWORKING=$4
DISK_TYPE=$5

# Files to read from GCS.
RANDOM_READ_RES="gcsfuse-random-read-workload-benchmark-20250522104648.json"
SEQ_READ_RES="gcsfuse-sequential-read-workload-benchmark-20250522104623.json"
WRITE_RES="gcsfuse-write-workload-benchmark-20250522105731.json"

if gcloud storage objects describe "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/success.txt" &>/dev/null; then
    echo "Found the success.txt file in the bucket."
else
    echo "Unable to locate success.txt file in the bucket. Exiting..."
    exit 1
fi
gcloud storage cp "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${RANDOM_READ_RES}" "${create_benchmark_tables_dir}/${RANDOM_READ_RES}"
gcloud storage cp "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${SEQ_READ_RES}" "${create_benchmark_tables_dir}/${SEQ_READ_RES}"
gcloud storage cp "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${WRITE_RES}" "${create_benchmark_tables_dir}/${WRITE_RES}"

RANDOM_READ_RES="${create_benchmark_tables_dir}/${RANDOM_READ_RES}"
SEQ_READ_RES="${create_benchmark_tables_dir}/${SEQ_READ_RES}"
WRITE_RES="${create_benchmark_tables_dir}/${WRITE_RES}"

# Helper
# Checks if all arrays (names passed as args) have the same size.
# Returns 0 if same size (or 0/1 array provided), 1 if different, 2 if no args.
all_same_size() {
    [ "$#" -eq 0 ] && return 2  # No arguments
    local -n arr_ref="$1"       # Nameref to the first array
    local size="${#arr_ref[@]}" # Get size of the first array
    exit_code=0
    for arr_name in "$@"; do
        local -n current_arr="$arr_name"
        if [ "${#current_arr[@]}" -ne "$size" ]; then 
            exit_code=1
        fi
    done
    return $exit_code # All same size (or single array)
}

create_tables() {
    echo "## GCSFuse Benchmarking on c4 machine-type"
    echo "* VM Type: c4-standard-96"
    echo "* VM location: us-south1"
    echo "* Networking: gVNIC+  tier_1 networking (200Gbps)"
    echo "* Disk Type: Hyperdisk balanced"
    echo "* GCS Bucket location: us-south1"

    echo "### Sequential Reads"
    echo "| File Size | BlockSize | nrfiles |Bandwidth in (GiB/sec) | IOPs  |  Avg Latency (msec) |"
    echo "|---|---|---|---|---|---|"
    fileSize=($(jq -r '.jobs[]?."job options"?.filesize? | select(. != null)' "$SEQ_READ_RES"))
    blockSize=($(jq -r '.jobs[]?."job options"?.bs? | select(. != null)' "$SEQ_READ_RES"))
    nrFiles=($(jq -r '.jobs[]?."job options"?.nrfiles? | select(. != null)' "$SEQ_READ_RES"))
    bandwidthBytes=($(jq -r '.jobs[]?.read?.bw_bytes? | select(. != null)' "$SEQ_READ_RES"))
    iops=($(jq -r '.jobs[]?.read?.iops? | select(. != null)' "$SEQ_READ_RES"))
    clat_ns_mean=($(jq -r '.jobs[]?.read?.clat_ns?.mean | select(. != null)' "$SEQ_READ_RES"))
    # Verify same size arrays
    if ! all_same_size fileSize blockSize nrFiles bandwidthBytes iops clat_ns_mean; then
        echo "Json file parsing or data error."
        echo "Not all arrays have the same size."
        exit 1
    fi
    rows=(${#fileSize[@]})
    for ((i = 0; i < rows; i++)); do
        echo "| ${fileSize[i]} | ${blockSize[i]} | ${nrFiles[i]} | ${bandwidthBytes[i]} | ${iops[i]} | ${clat_ns_mean[i]} |"
    done
    echo "### Random Reads"
    echo "| File Size | BlockSize | nrfiles |Bandwidth in (GiB/sec) | IOPs  |  Avg Latency (msec) |"
    echo "|---|---|---|---|---|---|"
    fileSize=($(jq -r '.jobs[]?."job options"?.filesize? | select(. != null)' "$RANDOM_READ_RES"))
    blockSize=($(jq -r '.jobs[]?."job options"?.bs? | select(. != null)' "$RANDOM_READ_RES"))
    nrFiles=($(jq -r '.jobs[]?."job options"?.nrfiles? | select(. != null)' "$RANDOM_READ_RES"))
    bandwidthBytes=($(jq -r '.jobs[]?.read?.bw_bytes? | select(. != null)' "$RANDOM_READ_RES"))
    iops=($(jq -r '.jobs[]?.read?.iops? | select(. != null)' "$RANDOM_READ_RES"))
    clat_ns_mean=($(jq -r '.jobs[]?.read?.clat_ns?.mean | select(. != null)' "$RANDOM_READ_RES"))
    # Verify same size arrays
    if ! all_same_size fileSize blockSize nrFiles bandwidthBytes iops clat_ns_mean; then
        echo "Json file parsing or data error."
        echo "Not all arrays have the same size."
        exit 1
    fi
    rows=(${#fileSize[@]})
    for ((i = 0; i < rows; i++)); do
        echo "| ${fileSize[i]} | ${blockSize[i]} | ${nrFiles[i]} | ${bandwidthBytes[i]} | ${iops[i]} | ${clat_ns_mean[i]} |"
    done
    echo "### Sequential Writes"
    echo "| File Size | BlockSize | nrfiles |Bandwidth in (GiB/sec) | IOPs  |  Avg Latency (msec) |"
    echo "|---|---|---|---|---|---|"
    fileSize=($(jq -r '.jobs[]?."job options"?.filesize? | select(. != null)' "$WRITE_RES"))
    blockSize=($(jq -r '(.jobs // [])[] | (."job options"?.bs? // "1M")' "$WRITE_RES"))
    nrFiles=($(jq -r '.jobs[]?."job options"?.nrfiles? | select(. != null)' "$WRITE_RES"))
    bandwidthBytes=($(jq -r '.jobs[]?.write?.bw_bytes? | select(. != null)' "$WRITE_RES"))
    iops=($(jq -r '.jobs[]?.write?.iops? | select(. != null)' "$WRITE_RES"))
    clat_ns_mean=($(jq -r '.jobs[]?.write?.clat_ns?.mean | select(. != null)' "$WRITE_RES"))
    # Verify same size arrays
    if ! all_same_size fileSize blockSize nrFiles bandwidthBytes iops clat_ns_mean; then
        echo "Json file parsing or data error."
        echo "Not all arrays have the same size."
        exit 1
    fi
    rows=(${#fileSize[@]})
    for ((i = 0; i < rows; i++)); do
        echo "| ${fileSize[i]} | ${blockSize[i]} | ${nrFiles[i]} | ${bandwidthBytes[i]} | ${iops[i]} | ${clat_ns_mean[i]} |"
    done
}

create_tables > "${tables_file}"