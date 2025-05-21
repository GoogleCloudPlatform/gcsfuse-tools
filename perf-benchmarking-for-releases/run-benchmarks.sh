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

# Print commands and their arguments as they are executed.
set -x
# Exit immediately if a command exits with a non-zero status.
set -e

# Validate input arguments
if [ "$#" -ne 6 ]; then
    echo "Usage: $0 <GCSFUSE_VERSION> <PROJECT_ID> <REGION> <MACHINE_TYPE> <IMAGE_FAMILY> <IMAGE_PROJECT>"
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

# Array for LSSD supported machines
# Add machine types that support local SSDs (NVMe) here
LSSD_SUPPORTED_MACHINES=("n2-standard-96" "c2-standard-60" "c2d-standard-112" "c3-standard-88" "c3d-standard-180")

# Check if the chosen machine type is directly present in the LSSD_SUPPORTED_MACHINES array
VM_LOCAL_SSD_ARGS=""
LSSD_ENABLED="false"

if [[ " ${LSSD_SUPPORTED_MACHINES[@]} " =~ " ${MACHINE_TYPE} " ]]; then
    echo "Machine type ${MACHINE_TYPE} supports LSSDs. Attaching 16 local NVMe SSDs (375GB each)."
    LSSD_ENABLED="true"
    # Construct the --local-ssd flags for 16 local SSDs
    for i in {0..15}; do
        VM_LOCAL_SSD_ARGS+=" --local-ssd=interface=NVME,size=375GB"
    done
else
    echo "Machine type ${MACHINE_TYPE} does not support LSSDs based on the configured list, or it's not set up for LSSD benchmarking."
    echo "VM will be created without local SSDs."
fi


# Cleanup function to be called on exit
cleanup() {
    echo "Initiating cleanup..."

    # Delete VM if it exists
    if gcloud compute instances describe "${VM_NAME}" --zone="${VM_ZONE}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
        echo "Deleting VM: ${VM_NAME}"
        gcloud compute instances delete "${VM_NAME}" --zone="${VM_ZONE}" --project="${PROJECT_ID}" --delete-disks=all -q >/dev/null
    else
        echo "VM '${VM_NAME}' not found; skipping deletion."
    fi

    # Delete GCS bucket with test data if it exists
    if gcloud storage buckets list --project="${PROJECT_ID}" --filter="name:(${GCS_BUCKET_WITH_FIO_TEST_DATA})" --format="value(name)" | grep -q "^${GCS_BUCKET_WITH_FIO_TEST_DATA}$"; then
        echo "Deleting GCS bucket: ${GCS_BUCKET_WITH_FIO_TEST_DATA}"
        gcloud storage rm -r "gs://${GCS_BUCKET_WITH_FIO_TEST_DATA}" -q >/dev/null
    else
        echo "Bucket '${GCS_BUCKET_WITH_FIO_TEST_DATA}' not found; skipping deletion."
    fi

    echo "Cleanup complete."
}


# Register the cleanup function to run on EXIT signal
trap cleanup EXIT

# Create the GCS bucket for FIO test data in the specified REGION
echo "Creating GCS test data bucket: gs://${GCS_BUCKET_WITH_FIO_TEST_DATA} in region: ${REGION}"
gcloud storage buckets create "gs://${GCS_BUCKET_WITH_FIO_TEST_DATA}" --project="${PROJECT_ID}" --location="${REGION}"

# Clear the existing GCSFUSE_VERSION directory in the results bucket
echo "Clearing previous data in gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/..."
gcloud storage rm -r "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/**" --quiet || true

# Upload FIO job files to the results bucket for the VM to download
echo "Uploading all .fio job files from local 'fio-job-files/' directory to gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/fio-job-files/..."
gcloud storage cp fio-job-files/*.fio "gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/fio-job-files/"
echo "FIO job files uploaded."

# Get the project number
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

# Construct the Storage Transfer Service account email
STS_ACCOUNT="project-${PROJECT_NUMBER}@storage-transfer-service.iam.gserviceaccount.com"

# Grant the service account 'roles/storage.admin' permissions on the newly created bucket
# This allows the service account to manage the bucket and perform transfers
gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET_WITH_FIO_TEST_DATA}" \
  --member="serviceAccount:${STS_ACCOUNT}" \
  --role="roles/storage.admin"

# Since file generation with fio is painfully slow, we will use storage transfer
# job to transfer test data from a fixed GCS bucket to the newly created bucket.
# Note : We need to copy only read data.
echo "Creating storage transfer job to copy read data to gs://${GCS_BUCKET_WITH_FIO_TEST_DATA}..."

TRANSFER_JOB_NAME=$(gcloud transfer jobs create \
  gs://gcsfuse-release-benchmark-fio-data \
  gs://${GCS_BUCKET_WITH_FIO_TEST_DATA} \
   --include-prefixes=read \
  --project="${PROJECT_ID}" \
  --format="value(name)" \
  --no-async) 

echo "Transfer completed."


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
    --metadata GCSFUSE_VERSION="${GCSFUSE_VERSION}",GCS_BUCKET_WITH_FIO_TEST_DATA="${GCS_BUCKET_WITH_FIO_TEST_DATA}",RESULTS_BUCKET_NAME="${RESULTS_BUCKET_NAME}",LSSD_ENABLED="${LSSD_ENABLED}" \
    --metadata-from-file=startup-script=starter-script.sh \
    ${VM_LOCAL_SSD_ARGS}
echo "VM created. Benchmarks will run on the VM."

echo "Waiting for benchmarks to complete on VM (polling for success.txt)..."

SUCCESS_FILE_PATH="gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/success.txt"
LOG_FILE_PATH="gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/benchmark_run.log"
SLEEP_TIME=600  # 10 minutes
sleep "$SLEEP_TIME"
#max 9 retries amounting to ~1hr30mins time
MAX_RETRIES=9

for ((i=1; i<=MAX_RETRIES; i++)); do
    if gcloud storage objects describe "${SUCCESS_FILE_PATH}" &> /dev/null; then
        echo "Benchmarks completed. success.txt found."
        echo "Results are available in gs://${RESULTS_BUCKET_NAME}/${GCSFUSE_VERSION}/"
        echo "Benchmark log file: $LOG_FILE_PATH"
        exit 0
    fi

    echo "Attempt $i/$MAX_RETRIES: success.txt not found. Sleeping for $((SLEEP_TIME / 60)) minutes..."
    sleep "$SLEEP_TIME"
done

# Failure case: success.txt was not found after retries
echo "Timed out waiting for success.txt after $((MAX_RETRIES * SLEEP_TIME / 60)) minutes. Perhaps there is some error."
echo "Benchmark log file (for troubleshooting): $LOG_FILE_PATH"
exit 1

# The trap command will handle the cleanup on script exit.
