#!/bin/bash
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# ------------------------------------------------------------------------------
# Description:
#   This script queries Google Cloud Logging to extract GCSFuse related
#   "stalled read-req" log entries for pods matching a regex in a GKE cluster.
#
#   It fetches logs in 30-minute intervals over a given time range and writes
#   the combined result to a CSV file.
#
#   It uses a default output path unless an optional path is provided.
#
# Usage:
#   ./fetch_logs.sh <cluster_name> <pod_name_regex> <start_time> <end_time> [output_file_path]
#
# Example (Default Path):
#   ./fetch_logs.sh xpk-large-scale-usc1f-a "sample-job-.*" \
#       "2025-02-04T18:00:00+05:30" "2025-02-05T10:00:00+05:30"
#
# Example (Custom Path):
#   ./fetch_logs.sh xpk-large-scale-usc1f-a "sample-job-.*" \
#       "2025-02-04T18:00:00+05:30" "2025-02-05T10:00:00+05:30" "/var/log/gcsfuse/sample_job_stalled_reads.csv"
#
# Output:
#   - CSV log file at the specified or default location.
#   - Prints total number of retry events due to stalled read requests.
# ------------------------------------------------------------------------------

# Check for correct number of arguments
if [ "$#" -lt 4 ] || [ "$#" -gt 5 ]; then
    echo "Usage: $0 <cluster_name> <pod_name_regex> <start_time> <end_time> [output_file_path]"
    echo "Example: $0 xpk-large-scale-usc1a-a \"sample-job-.*\" \"2025-06-16T02:05:00-07:00\" \"2025-06-16T03:05:00-07:00\""
    exit 1
fi

# Input parameters
cluster_name="$1"
pod_name_regex="$2"
starttime="$3"
endtime="$4"

# Set output file path
if [ "$#" -eq 5 ]; then
    log_filename="$5"
else
    # Sanitize the regex to create a valid filename prefix
    output_prefix=$(echo "$pod_name_regex" | tr -dc '[:alnum:]_-')
    if [ -z "$output_prefix" ]; then
        output_prefix="gcsfuse-sidecar"
    fi
    log_filename="/tmp/${output_prefix}-logs.csv"
fi

# Get the directory part of the log filename
dir_path=$(dirname "$log_filename")

# Create the directory if it doesn't exist
if [ ! -d "$dir_path" ]; then
    echo "Output directory '$dir_path' not found. Creating it."
    if ! mkdir -p "$dir_path"; then
        echo "Failed to create directory '$dir_path'. Exiting."
        exit 1
    fi
fi

# Write CSV header once (overwrite any existing file)
echo "timestamp,textPayload" > "$log_filename"

# Convert input times to Unix timestamps
start_timestamp=$(date -d "$starttime" +%s)
end_timestamp=$(date -d "$endtime" +%s)

# Iterate through time range in 30-minute intervals
current_start_time=$start_timestamp

while [ $current_start_time -lt $end_timestamp ]; do
    current_end_time=$((current_start_time + 1800 > end_timestamp ? end_timestamp : current_start_time + 1800)) # 30 minutes

    # Format timestamps for gcloud logging query (ISO 8601 / RFC 3339)
    start_time_formatted=$(date -d @$current_start_time --utc +%FT%T%:z)
    end_time_formatted=$(date -d @$current_end_time --utc +%FT%T%:z)

    # Temporary file to hold the output of one gcloud call
    temp_output=$(mktemp)

    # The gcloud logging read command can be used from the CLI to read logs.
    if ! gcloud logging read \
        "resource.labels.cluster_name=\"$cluster_name\" AND resource.labels.container_name=\"gke-gcsfuse-sidecar\" AND resource.labels.pod_name=~\"$pod_name_regex\" AND timestamp>=\"$start_time_formatted\" AND timestamp<=\"$end_time_formatted\" AND \"stalled read-req\"" \
        --order=ASC \
        --format='csv(timestamp,textPayload)' > "$temp_output"; then
        echo "gcloud command failed for the interval starting at $start_time_formatted. Exiting."
        rm "$temp_output"
        exit 1
    fi

    # Append logs but skip the header line from gcloud output
    tail -n +2 "$temp_output" >> "$log_filename"
    rm "$temp_output"

    current_start_time=$current_end_time
done

echo "Logs have been saved at: $log_filename"

# Calculate total number of retries (lines - 1 for header)
total_retries=$(( $(wc -l < "$log_filename") - 1 ))
echo "Total number of retries due to stalled read request = $total_retries"
