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
set -euo pipefail

echo "" > nohup.out

MACHINE_TYPE=("c4-standard-96")
GCSFUSE_VERSION="v3.3.0"
BENCH_TYPE="seq-read" # seq-write, rand-read, seq-read
project="gcs-fuse-test"
region="us-central1"
image_family="ubuntu-2204-lts"
image_project="ubuntu-os-cloud"
FILE_SIZE=("10M")
BS=("1M")
NUM_JOBS=("128")
NRFILE=("20")
for m in "${MACHINE_TYPE[@]}"; do
for f in "${FILE_SIZE[@]}"; do
  for s in "${BS[@]}"; do
    for t in "${NUM_JOBS[@]}"; do
      for j in "${NRFILE[@]}"; do
          echo "Running experiment for machine type: $m for BENCH $BENCH_TYPE with filesize: $f, blocksize: $s, numjobs: $t, nrfile: $j"
          echo "" > current_benchmark.log
          ./run-benchmarks.sh "$GCSFUSE_VERSION" "$project" "$region" "$m" "$image_family" "$image_project" "$t" "$j" "$s" "$f" "$BENCH_TYPE"> current_benchmark.log 2>&1
          RESULT_PATH="gs://gcsfuse-release-benchmarks-results/$GCSFUSE_VERSION/results/$m/$BENCH_TYPE/$f/$s"
          RESULT_FILE="${BENCH_TYPE}-benchmark_res_${t}_${j}.json"
          TMP_FILE=$(mktemp /tmp/${BENCH_TYPE}-bench-res.XXXXXX)
          gcloud storage cp "$RESULT_PATH/$RESULT_FILE" "$TMP_FILE"
          echo "Res file: $TMP_FILE"
          BW_IN_MBPS=$(jq '(.jobs[].read.bw_bytes + .jobs[].write.bw_bytes) / (1000 * 1000)' "$TMP_FILE")
          echo "$m $region $BENCH_TYPE FileSize=$f BlockSize=$s NumJobs=$t NrFile=$j MBPS=$BW_IN_MBPS" >> results.txt
          echo "Completed Successfully..."
        done
      done
    done
  done
done
