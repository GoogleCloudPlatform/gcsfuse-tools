#!/bin/bash

# --- 1. Change the parameters below ---
gcsfuse_version="3.5.5"
commit_hash=""
bucket_name=""
vm_name=""
location="us-central1"
zone="us-central1-f"
machine-type="t2a-standard-48"

# --- 2. Create details.txt ---
# Format: version, commit hash, empty line
echo "$gcs_version" > details.txt
echo "$commit_hash" >> details.txt
echo "" >> details.txt

# --- 3. Upload to details.txt to user provided bucket ---
gcloud storage cp details.txt gs://${bucket_name}/version-detail/details.txt

# --- 3. Create buckets which will be used by the test ---
gcloud storage buckets create gs://${vm_name} --project=gcs-fuse-test --location=${location}
gcloud storage buckets create gs://${vm_name}-hns --project=gcs-fuse-test --location=${location} --uniform-bucket-level-access --enable-hierarchical-namespace
gcloud storage buckets create gs://${vm_name}-parallel --project=gcs-fuse-test --location=${location}
gcloud storage buckets create gs://${vm_name}-hns-parallel --project=gcs-fuse-test --location=${location} --uniform-bucket-level-access --enable-hierarchical-namespace

gcloud compute instances delete ${vm_name} --zone=${zone} --quiet

gcloud compute instances create ${vm_name} \
    --machine-type=${machine-type} \
    --image-project=rhel-cloud --zone=${zone} \
    --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/devstorage.read_write \
    --boot-disk-size=75GiB \
    --metadata run-on-zb-only=false,run-read-cache-only=false,run-light-test=true,custom_bucket=${bucket_name},startup-script-url=https://raw.githubusercontent.com/GoogleCloudPlatform/gcsfuse/refs/heads/master/tools/cd_scripts/e2e_test.sh \
    --image=rhel-9-arm64-v20250812