#!/bin/bash
set -euo pipefail

# --- 1. Change the parameters below ---
gcsfuse_version="3.7.101"
commit_hash="4c30a3e763a1e718ed6ce2b4260150f833c0104e"
bucket_name="cd-test-bucket-${USER}"
location="us-central1"
zone="us-central1-f"
machine_type="t2a-standard-48"
image_project="rhel-cloud"
image_family="rhel-9-arm64"
project="gcs-fuse-test"
vm_name="${USER}-cd-test-${image_family}"

# --- 2. Upload to details.txt to user provided bucket ---
# Format: version, commit hash, empty line
gcloud storage buckets create gs://${bucket_name} --project=${project} --location=${location} || true
(echo "$gcsfuse_version"; echo "$commit_hash") | gcloud storage cp - gs://${bucket_name}/version-detail/details.txt

# --- 3. Create buckets which will be used by the test ---
gcloud storage buckets create gs://${vm_name} --project=${project} --location=${location} || true
gcloud storage buckets create gs://${vm_name}-hns --project=${project} --location=${location} --uniform-bucket-level-access --enable-hierarchical-namespace || true
gcloud storage buckets create gs://${vm_name}-parallel --project=${project} --location=${location} || true
gcloud storage buckets create gs://${vm_name}-hns-parallel --project=${project} --location=${location} --uniform-bucket-level-access --enable-hierarchical-namespace || true

# --- 4. Delete and recreate the VM ---
gcloud compute instances delete ${vm_name} --zone=${zone} --quiet || true
gcloud compute instances create ${vm_name} \
    --machine-type=${machine_type} \
    --image-project=${image_project} --zone=${zone} \
    --image-family=${image_family} \
    --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/devstorage.read_write \
    --boot-disk-size=75GiB \
    --metadata run-on-zb-only=false,run-read-cache-only=false,custom_bucket=${bucket_name},run-light-test=false,startup-script-url=https://raw.githubusercontent.com/GoogleCloudPlatform/gcsfuse/refs/heads/master/tools/cd_scripts/e2e_test.sh
