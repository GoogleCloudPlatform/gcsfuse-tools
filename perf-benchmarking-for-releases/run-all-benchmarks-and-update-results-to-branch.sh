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

# Fail on anything unexpected and print commands as they are executed.
set -xeuo pipefail

# Script Documentation
if [ "$#" -gt 2 ]; then
    echo "Usage: $0 <GCSFUSE_VERSION> [RERUN]"
    echo ""
    echo "RERUN: is optional argument true or false, if true it reruns benchmarks even if the benchmarks exists from previous runs (Default:false)"
    echo "Example:"
    echo "  ./run-all-benchmarks-and-update-results-to-branch.sh  <tag, commit-id-on-master, branch-name>"
    exit 1
fi

BENCHMARK_COUNT=1 # Number of times run each benchmark 
GCSFUSE_VERSION="$1"
RERUN=${2:-false}

rm -rf /tmp/update_benchmarks* # Remove previous directories.
TMP_DIR=$(mktemp -d -t update_benchmarks.XXXXXX)

if [[ "$RERUN" == "false" ]]; then
    if ! gcloud storage objects describe "gs://gcsfuse-release-benchmarks-results/${GCSFUSE_VERSION}/c4-standard-96/success.txt" &>/dev/null; then
        RERUN="true"
    fi
    if ! gcloud storage objects describe "gs://gcsfuse-release-benchmarks-results/${GCSFUSE_VERSION}/n2-standard-96/success.txt" &>/dev/null; then
        RERUN="true"
    fi
fi
echo "Logging in: ${TMP_DIR}/${GCSFUSE_VERSION}-c4"
echo "Logging in: ${TMP_DIR}/${GCSFUSE_VERSION}-n2"
if [[ "$RERUN" == "true" ]]; then
    benchmark_pids=()
    # Benchmark 1
    ./run-benchmarks.sh "$GCSFUSE_VERSION" gcs-fuse-test us-south1 n2-standard-96 ubuntu-2204-lts ubuntu-os-cloud "$BENCHMARK_COUNT" >"${TMP_DIR}/${GCSFUSE_VERSION}-n2" 2>&1 &
    benchmark_pids+=($!)
    # Benchmark 2
    ./run-benchmarks.sh "$GCSFUSE_VERSION" gcs-fuse-test us-south1 c4-standard-96 ubuntu-2204-lts ubuntu-os-cloud "$BENCHMARK_COUNT" >"${TMP_DIR}/${GCSFUSE_VERSION}-c4" 2>&1 &
    benchmark_pids+=($!)
    for pid in "${benchmark_pids[@]}"; do
        if ! wait "$pid"; then
            echo "Benchmark with PID $pid failed. Exiting."
            exit 1
        fi
    done
fi

./create_benchmark_tables.sh "$GCSFUSE_VERSION" us-south1 c4-standard-96 'gVNIC+ tier_1 networking (200Gbps)' 'Hyperdisk balanced' "$BENCHMARK_COUNT"
./create_benchmark_tables.sh "$GCSFUSE_VERSION" us-south1 n2-standard-96 'gVNIC+ tier_1 networking (100Gbps)' 'SSD persistent disk' "$BENCHMARK_COUNT"

gcloud storage cp "gs://gcsfuse-release-benchmarks-results/${GCSFUSE_VERSION}/c4-standard-96/tables.md" "${TMP_DIR}/c4-standard-96/tables.md"
gcloud storage cp "gs://gcsfuse-release-benchmarks-results/${GCSFUSE_VERSION}/n2-standard-96/tables.md" "${TMP_DIR}/n2-standard-96/tables.md"

cat "${TMP_DIR}/c4-standard-96/tables.md" >>"${TMP_DIR}/benchmarks.md"
echo " " >>"${TMP_DIR}/benchmarks.md"
cat "${TMP_DIR}/n2-standard-96/tables.md" >>"${TMP_DIR}/benchmarks.md"
# Helper method to update the benchmark file section based on markers with given file.
update_benchmarks_based_on_markers() {
    local target_file="$1"
    local source_file="$2"
    local start_marker="$3"
    local end_marker="$4"
    local temp_file

    # --- Validate inputs ---
    if [[ ! -f "$target_file" ]]; then
        echo "Error: Target file '$target_file' not found." >&2
        return 1
    fi
    if [[ ! -s "$target_file" ]]; then
        echo "Error: Target file '$target_file' is empty." >&2
        return 1
    fi
    if [[ ! -f "$source_file" ]]; then
        echo "Error: Source file '$source_file' not found." >&2
        return 1
    fi

    # --- Find marker line numbers ---
    # Get line number of the start marker (first occurrence)
    local start_line_num
    start_line_num=$(grep -n -m 1 "$start_marker" "$target_file" | cut -d: -f1)

    # Get line number of the end marker (first occurrence *after* the start marker, if possible)
    local end_line_num
    if [[ -n "$start_line_num" ]]; then
        # Search for end marker starting from the line after the start marker
        end_line_num=$(tail -n +"$((start_line_num + 1))" "$target_file" | grep -n -m 1 "$end_marker" | cut -d: -f1)
        if [[ -n "$end_line_num" ]]; then
            # Adjust end_line_num to be relative to the original file
            end_line_num=$((start_line_num + end_line_num))
        fi
    else # No start marker found directly
        end_line_num=$(grep -n -m 1 "$end_marker" "$target_file" | cut -d: -f1)
    fi

    # --- Validate markers ---
    if [[ -z "$start_line_num" ]]; then
        echo "Error: Start marker '$start_marker' not found in '$target_file'." >&2
        return 2
    fi
    if [[ -z "$end_line_num" ]]; then
        echo "Error: End marker '$end_marker' not found in '$target_file' (or not found after start marker)." >&2
        return 3
    fi
    if [[ "$start_line_num" -ge "$end_line_num" ]]; then
        echo "Error: Start marker appears on or after the end marker (Line $start_line_num vs $end_line_num)." >&2
        return 4
    fi

    # --- Create temporary file for new content ---
    temp_file=$(mktemp)
    if [[ -z "$temp_file" || ! -f "$temp_file" ]]; then
        echo "Error: Failed to create temporary file." >&2
        return 5
    fi
    # Ensure temp file is cleaned up on exit or error
    trap 'rm -f "$temp_file"' EXIT

    # --- Construct the new file content ---
    # 1. Content before the start marker (excluding the marker line itself)
    if [[ "$start_line_num" -gt 0 ]]; then
        head -n "$((start_line_num - 1))" "$target_file" >"$temp_file"
    else
        # If start marker is on line 0 (unlikely with -n 1), nothing before it.
        # This case should ideally not happen if file is not empty and marker exists.
        : >"$temp_file" # Create an empty temp file or ensure it's truncated
    fi

    # 2. The start marker itself
    echo "$start_marker" >>"$temp_file"
    echo "" >>"$temp_file"
    # 3. Content from the source file
    cat "$source_file" >>"$temp_file"
    echo "" >>"$temp_file"
    # 4. The end marker itself
    echo "$end_marker" >>"$temp_file"

    # 5. Content after the end marker (excluding the marker line itself)
    # Calculate how many lines are from the end_marker_line + 1 to the end of the file
    local total_lines
    total_lines=$(wc -l <"$target_file")
    if [[ "$end_line_num" -lt "$total_lines" ]]; then
        tail -n +"$((end_line_num + 1))" "$target_file" >>"$temp_file"
    fi

    # --- Replace the original file with the new content ---
    if mv "$temp_file" "$target_file"; then
        echo "Content updated successfully in '$target_file'."
        trap - EXIT # Remove the trap as mv succeeded
        return 0
    else
        echo "Error: Failed to replace '$target_file' with updated content." >&2
        # temp_file will be removed by the trap
        return 6
    fi
}

# Function to clone the gcsfuse repository
clone_gcsfuse_repo() {
    echo "Cloning gcsfuse repository from $REPO_URL into $CLONE_DIR..."
    if [ -d "$CLONE_DIR" ]; then
        echo "Directory $CLONE_DIR already exists. Removing it first."
        rm -rf "$CLONE_DIR" || {
            echo "Failed to remove existing directory $CLONE_DIR"
            exit 1
        }
    fi
    git clone "$REPO_URL" "$CLONE_DIR" || {
        echo "Failed to clone repository"
        exit 1
    }
    echo "Repository cloned successfully."
}

CLONE_DIR="${TMP_DIR}/gcsfuse"
REPO_URL="https://github.com/GoogleCloudPlatform/gcsfuse.git"
clone_gcsfuse_repo
pushd "$CLONE_DIR"
BRANCH_TO_UPDATE_RESULTS_FROM="update-benchmarks-for-gcs-fuse-version-${GCSFUSE_VERSION}-timestamp-$(date +%s%N)"
git checkout -b "$BRANCH_TO_UPDATE_RESULTS_FROM" || {
    echo "Failed to checkout branch $BRANCH_TO_UPDATE_RESULTS_FROM"
    exit 1
}
GCSFUSE_VERSION_LINE_PREFIX="* GCSFuse version:"
GCSFUSE_VERSION_LINE_UPDATED="* GCSFuse version: ${GCSFUSE_VERSION}"
BENCHMARKS_START_MARKER="<!-- Benchmarks start -->"
BENCHMARKS_END_MARKER="<!-- Benchmarks end -->"
update_benchmarks_based_on_markers "$CLONE_DIR/docs/benchmarks.md" "${TMP_DIR}/benchmarks.md" "$BENCHMARKS_START_MARKER" "$BENCHMARKS_END_MARKER"
sed -i "/^${GCSFUSE_VERSION_LINE_PREFIX}/c${GCSFUSE_VERSION_LINE_UPDATED}" "docs/benchmarks.md"
git add .
git commit -m "update benchmark results for version $GCSFUSE_VERSION"
git push -u origin "$BRANCH_TO_UPDATE_RESULTS_FROM"

popd

echo "Results of benchmarks updated in branch: $BRANCH_TO_UPDATE_RESULTS_FROM"
echo "Follow link to open the branch on github"
echo "https://github.com/GoogleCloudPlatform/gcsfuse/tree/${BRANCH_TO_UPDATE_RESULTS_FROM}"
