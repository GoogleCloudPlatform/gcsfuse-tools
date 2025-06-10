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
#   This script queries Google Cloud Logging to extract GCSFuse-related
#   "stalled read-req" log entries for a given job running in a GKE cluster.
#
#   It fetches logs in 30-minute intervals over a given time range, and writes
#   the combined result to a CSV file at:
#       /tmp/<job_name>-logs.csv
#
#   This CSV can be used as input for analysis tools like retries_per_interval.py.
#
# Usage:
#   ./fetch_logs.sh <cluster_name> <job_name> <start_time> <end_time>
#
# Example:
#   ./fetch_logs.sh xpk-large-scale-usc1f-a sample-job \
#       "2025-02-04T18:00:00+05:30" "2025-02-05T10:00:00+05:30"
#
# Output:
#   - CSV log file: /tmp/<job_name>-logs.csv
#   - Prints total number of retry events due to stalled read requests.
# ------------------------------------------------------------------------------

# Check for correct number of arguments
if [ "$#" -ne 4 ]; then
    echo "Usage: $0 <cluster_name> <job_name> <start_time> <end_time>"
    echo "Example: $0 xpk-large-scale-usc1f-a sample-job \"2025-02-04T18:00:00+05:30\" \"2025-02-05T10:00:00+05:30\""
    exit 1
fi

# Input parameters
cluster_name="$1"
job_name="$2"
starttime="$3"
endtime="$4"

# Output file path
log_filename="/tmp/${job_name}-logs.csv"

# Write CSV header once (overwrite any existing file)
echo "timestamp,textPayload" > "$log_filename"

# Convert input times to Unix timestamps
start_timestamp=$(date -d "$starttime" +%s)
end_timestamp=$(date -d "$endtime" +%s)

# Iterate through time range in 30-minute intervals
current_start_time=$start_timestamp

while [ $current_start_time -lt $end_timestamp ]; do
    current_end_time=$((current_start_time + 1800))  # 30 minutes

    # Format timestamps for gcloud logging query (ISO 8601 / RFC 3339)
    start_time_formatted=$(date -d @$current_start_time --utc +%FT%T%:z)
    end_time_formatted=$(date -d @$current_end_time --utc +%FT%T%:z)

    # Temporary file to hold the output of one gcloud call
    temp_output=$(mktemp)

    if ! gcloud logging read \
        "resource.labels.cluster_name=\"$cluster_name\" AND resource.labels.container_name=\"gke-gcsfuse-sidecar\" AND resource.labels.pod_name=~\"$job_name-.*\" AND timestamp>=\"$start_time_formatted\" AND timestamp<=\"$end_time_formatted\" AND \"stalled read-req\"" \
        --order=ASC \
        --format='csv(timestamp,textPayload)' > "$temp_output"; then
        echo "[ERROR] gcloud command failed for the interval starting at $start_time_formatted. Exiting."
        rm "$temp_output"
        exit 1
    fi

    # Append logs but skip the header line from gcloud output
    tail -n +2 "$temp_output" >> "$log_filename"
    rm "$temp_output"

    current_start_time=$current_end_time
done

echo "Logs saved to $log_filename"

# Calculate total number of retries (lines - 1 for header)
total_retries=$(( $(wc -l < "$log_filename") - 1 ))
echo "Total number of retries due to stalled read request = $total_retries"
