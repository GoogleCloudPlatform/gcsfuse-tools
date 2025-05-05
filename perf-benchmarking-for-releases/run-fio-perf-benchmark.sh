#! /bin/bash
# Copyright 2025 Google LLC
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

# Print commands and their arguments as they are executed.
set -x
# Exit immediately if a command exits with a non-zero status.
set -e

GCSFUSE_VERSION=$1
PROJECT_ID=$2
ZONE=$3
MACHINE_TYPE=$4
IMAGE_FAMILY=$5
IMAGE_PROJECT=$6

extract_region() {
  local input="$1"
  local regex='^([a-z]+-[a-z0-9]+)-\w+$'

  if [[ "$input" =~ $regex ]]; then
    echo "${BASH_REMATCH[1]}"
  else
    echo "" # Or handle no match differently, e.g., return an error code
  fi
}

# For fio runs, we need the test bucket to be in the same region as the VM,
# hence, we will create this bucket on the fly.
GCS_BUCKET_WITH_FIO_TEST_DATA=gcsfuse-release-benchmark-${GCSFUSE_VERSION}
REGION=$(extract_region ${ZONE})
gcloud storage buckets create gs://${GCS_BUCKET_WITH_FIO_TEST_DATA} \
    --location=${REGION}
# Since file generation with fio is painfully slow, we will use storage transfer 
# job to transfer test data from a fixed GCS bucket to the newly created bucket.
# Note : We need to copy only read data.
gcloud transfer jobs create \
    --source=gs://gcsfuse-release-benchmark-fio-data \
    --destination=gs://${GCS_BUCKET_WITH_FIO_TEST_DATA} \
    --include-prefixes="read" \
    --schedule-starts-at=$(date -I) \
    --schedule-repeats-every=0



VM_NAME=gcsfuse-perf-benchmark-version-${GCSFUSE_VERSION}
# Create the VM based on the config passed by user
gcloud compute instances create ${VM_NAME} \
--project=${PROJECT_ID}\
--image-family=${IMAGE_FAMILY} \
--machine-type=${MACHINE_TYPE} \
--image-project=${IMAGE_PROJECT} \
--zone=${ZONE} \
--boot-disk-size=1000GB \
--network-interface=network-tier=PREMIUM,nic-type=GVNIC \
--scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/devstorage.read_write \
--network-performance-configs=total-egress-bandwidth-tier=TIER_1 \
--metadata gcsfuse_version=${GCSFUSE_VERSION},GCS_BUCKET_WITH_FIO_TEST_DATA=${GCS_BUCKET_WITH_FIO_TEST_DATA} \
--metadata-from-file=startup-script=starter-script.sh

