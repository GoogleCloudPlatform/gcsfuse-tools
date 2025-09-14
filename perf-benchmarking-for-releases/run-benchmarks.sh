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

# Validate input arguments
if [ "$#" -ne 11 ]; then
    echo "Usage: $0 <GCSFUSE_VERSION> <PROJECT_ID> <REGION> <MACHINE_TYPE> <IMAGE_FAMILY> <IMAGE_PROJECT> <NUM_JOB> <NRFILES> <BS> <FILESIZE> <BENCH_TYPE>"
    echo ""
    echo "<GCSFUSE_VERSION> can be a Git tag (e.g. v1.0.0), branch name (e.g. main), or a commit ID on master."
    echo ""
    echo "This script should be run from the 'perf-benchmarking-for-releases' directory."
    echo ""
    echo "Example:"
    echo "  bash run-benchmarks.sh v2.12.0 gcs-fuse-test us-south1 n2-standard-96 ubuntu-2204-lts ubuntu-os-cloud 3"
    exit 1
fi

echo "!!! Ensure your account has the following permissions:"
echo "Read access to:    gs://gcsfuse-release-benchmark-fio-data"
echo "Read/Write access to: gs://gcsfuse-release-benchmarks-results"

GCSFUSE_VERSION=$1
PROJECT_ID=$2
REGION=$3
MACHINE_TYPE=$4
IMAGE_FAMILY=$5
IMAGE_PROJECT=$6
NUM_JOB=$7
NRFILES=$8
BS=$9
FILESIZE=${10}
BENCH_TYPE=${11}


# Generate unique names for VM and buckets using timestamp and random number
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RAND_SUFFIX=$(head /dev/urandom | tr -dc a-z0-9 | head -c 8)
UNIQUE_ID="${TIMESTAMP}-${RAND_SUFFIX}"

VM_NAME="gcsfuse-perf-benchmark-${IMAGE_FAMILY}-${UNIQUE_ID}"
GCS_BUCKET_WITH_FIO_TEST_DATA="gcsfuse-release-benchmark-data-${UNIQUE_ID}"
RESULTS_BUCKET_NAME="gcsfuse-release-benchmarks-results"

# For VM creation, we need a zone within the specified region.
# We will pick the first available zone, typically ending with '-a'.
VM_ZONE="${REGION}-a"

echo "Starting GCSFuse performance benchmarking for version: ${GCSFUSE_VERSION}"
echo "VM Name: ${VM_NAME}"
echo "Test Data Bucket: gs://${GCS_BUCKET_WITH_FIO_TEST_DATA}"
echo "Results Bucket: gs://${RESULTS_BUCKET_NAME}"
echo "Project ID: ${PROJECT_ID}"
echo "Region: ${REGION}"
echo "VM Zone: ${VM_ZONE}" 
echo "Machine Type: ${MACHINE_TYPE}"

# Cleanup function to be called on exit
cleanup() {
    echo "Initiating cleanup..."
    # Delete VM if it exists
    echo "Deleting VM: ${VM_NAME}"
    gcloud compute instances delete "${VM_NAME}" --zone="${VM_ZONE}" --project="${PROJECT_ID}" --delete-disks=all -q >/dev/null || true
    echo "Cleanup complete."
}


# Register the cleanup function to run on EXIT signal
trap cleanup EXIT

echo "Creating GCS test data bucket: gs://${GCS_BUCKET_WITH_FIO_TEST_DATA} in region: ${REGION}"

# Clear the existing GCSFUSE_VERSION directory in the results bucket for the machine-type
echo "Clearing previous data in gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}..."
gcloud storage rm -r "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/**" --quiet > /dev/null 2>&1 || true

# Upload FIO job files to the results bucket for the VM to download"
echo "Uploading all .fio job files from local 'fio-job-files/' directory to gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/fio-job-files/..."
gcloud storage cp fio-job-files/*.fio "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/fio-job-files/"
gcloud storage cp starter-script.sh "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/starter-script.sh"
echo "FIO job files uploaded."

cd data-prep
 SECONDS=0
go run main.go --project="$PROJECT_ID" --region="$REGION" --parallelism=80 -nrfile="$NRFILES" --numjobs="$NUM_JOB" --filesize="$FILESIZE" --bucket="$GCS_BUCKET_WITH_FIO_TEST_DATA" --op_type="setup" --bench_type="$BENCH_TYPE"
echo "data-prep took $SECONDS seconds"
cd ..


# Create the VM based on the config passed by user
echo "Creating VM: ${VM_NAME} in zone ${VM_ZONE}..."
gcloud compute instances create "${VM_NAME}" \
    --project="${PROJECT_ID}" \
    --image-family="${IMAGE_FAMILY}" \
    --machine-type="${MACHINE_TYPE}" \
    --image-project="${IMAGE_PROJECT}" \
    --zone="${VM_ZONE}" \
    --boot-disk-size=1000GB \
    --network-interface=network-tier=PREMIUM,nic-type=GVNIC \
    --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/devstorage.read_write \
    --network-performance-configs=total-egress-bandwidth-tier=TIER_1 \
    --metadata BENCH_TYPE="$BENCH_TYPE",NUM_JOB="${NUM_JOB}",NRFILES="${NRFILES}",BS="${BS}",FILESIZE="${FILESIZE}",GCSFUSE_VERSION="${GCSFUSE_VERSION}",MACHINE_TYPE="${MACHINE_TYPE}",GCS_BUCKET_WITH_FIO_TEST_DATA="${GCS_BUCKET_WITH_FIO_TEST_DATA}",RESULTS_BUCKET_NAME="${RESULTS_BUCKET_NAME}" \

gcloud compute os-login ssh-keys add --key="$(ssh-add -L | grep publickey)" --project="$PROJECT_ID"

MAX_RETRIES=30
SLEEP_TIME=3

run_command_on_vm() {
    for ((i=1; i<=MAX_RETRIES; i++)); do
        sleep "$SLEEP_TIME"
        if ! gcloud compute ssh "$VM_NAME" "--zone=${VM_ZONE}" "--project=${PROJECT_ID}" "--command=$1" -- -o "Hostname=nic0.${VM_NAME}.${VM_ZONE}.c.${PROJECT_ID}.internal.gcpnode.com"; then
            echo "Retrying..."
        else
            return 0
        fi
    done
    echo "Failed after $((MAX_RETRIES * SLEEP_TIME)) seconds after $((MAX_RETRIES)) retries."
    exit 1
}

gcloud storage cp starter-script.sh "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/starter-script.sh"
run_command_on_vm "hostname" > /dev/null 2>&1
run_command_on_vm "gcloud storage cp gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/${MACHINE_TYPE}/starter-script.sh ~/"
SECONDS=0
run_command_on_vm "bash ~/starter-script.sh"
echo "starter script took $SECONDS seconds"



