#!/bin/bash

# --- 1. Change the parameters below ---
gcsfuse_version="3.7.101"
commit_hash="4c30a3e763a1e718ed6ce2b4260150f833c0104e"
bucket_name="ashmeenbkt"
vm_name="ashmeen-release-test-rhel-9-arm64-2"
location="us-central1"
zone="us-central1-f"
machine_type="t2a-standard-48"
image_project="rhel-cloud"
image_family="rhel-9-arm64"

# --- 2. Create details.txt ---
# Format: version, commit hash, empty line
echo "$gcsfuse_version" > details.txt
echo "$commit_hash" >> details.txt

# --- 3. Upload to details.txt to user provided bucket ---
gcloud storage cp details.txt gs://${bucket_name}/version-detail/details.txt

# --- 4. Create buckets which will be used by the test ---
gcloud storage buckets create gs://${vm_name} --project=gcs-fuse-test --location=${location}
gcloud storage buckets create gs://${vm_name}-hns --project=gcs-fuse-test --location=${location} --uniform-bucket-level-access --enable-hierarchical-namespace
gcloud storage buckets create gs://${vm_name}-parallel --project=gcs-fuse-test --location=${location}
gcloud storage buckets create gs://${vm_name}-hns-parallel --project=gcs-fuse-test --location=${location} --uniform-bucket-level-access --enable-hierarchical-namespace

# --- 5. Delete and recreate the VM ---
gcloud compute instances delete ${vm_name} --zone=${zone} --quiet
gcloud compute instances create ${vm_name} \
    --machine-type=${machine_type} \
    --image-project=${image_project} --zone=${zone} \
    --image-family=${image_family} \
    --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/devstorage.read_write \
    --boot-disk-size=75GiB \
    --metadata run-on-zb-only=false,run-read-cache-only=false,custom_bucket=${bucket_name},run-light-test=false,startup-script-url=https://raw.githubusercontent.com/GoogleCloudPlatform/gcsfuse/refs/heads/master/tools/cd_scripts/e2e_test.sh \