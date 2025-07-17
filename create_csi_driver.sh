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

# How to run this script:
# ./create_csi_driver.sh --branch-name <commit-id/branch-name/tag> --bucket <bucket-name> --project <project-id>

# Exit immediately if a command exits with a non-zero status.
set -e
set +x 

# --- Script Variables ---
# Initialize variables with default or empty values
BRANCH=""
BUCKET=""
PROJECT=""

# --- Argument Parsing ---
echo "--- Parsing command-line arguments ---"
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --branch-name)
      BRANCH="$2"
      shift # past argument
      shift # past value
      ;;
    --bucket)
      BUCKET="$2"
      shift # past argument
      shift # past value
      ;;
    --project)
      PROJECT="$2"
      shift # past argument
      shift # past value
      ;;
    *)
      echo "Unknown parameter passed: $1"
      exit 1
      ;;
  esac
done

# Validate required arguments after parsing
if [[ -z "$BRANCH" || -z "$BUCKET" || -z "$PROJECT" ]]; then
  echo "Usage: $0 --branch-name <gcsfuse_branch> --bucket <gcs_bucket> --project <gcp_project_id>"
  exit 1
fi

cd /tmp
# --- Install Prerequisites e.g. Docker ---
# Update package index
sudo apt-get update
# Install required dependencies
sudo apt-get install -y ca-certificates curl gnupg
# Add Docker’s official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo tee /etc/apt/keyrings/docker.asc > /dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
# Add Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
# Update again with Docker repo included
sudo apt-get update
# Create the docker group (may already exist)
sudo groupadd docker || :
# Add your current user to the docker group
sudo usermod -aG docker $USER
# Install Docker
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin


# --- Build and Upload gcsfuse ---
echo "--- Ensuring gcsfuse repository is ready ---"
if [ -d "gcsfuse" ]; then
  echo "gcsfuse directory already exists. Pulling latest changes."
  cd gcsfuse
  git pull origin "$BRANCH"
else
  echo "Cloning gcsfuse repository."
  git clone https://github.com/googleCloudPlatform/gcsfuse
  cd gcsfuse
fi
echo "--- Checking out branch: $BRANCH ---"
git checkout "$BRANCH"
echo "--- Building gcsfuse binary ---"
GOOS=linux GOARCH=amd64 go run tools/build_gcsfuse/main.go . . v3
echo "--- Uploading gcsfuse binary to gs://$BUCKET/linux/amd64/ ---"
gsutil cp "./bin/gcsfuse" "gs://$BUCKET/linux/amd64/"
echo "--- Cleaning up gcsfuse build artifacts ---"
rm -r "./bin"
if [ -d "./sbin" ]; then
  rm -r "./sbin"
fi
cd ..

# --- Build and Push gcs-fuse-csi-driver Image ---
echo "--- Ensuring gcs-fuse-csi-driver repository is ready ---"
if [ -d "gcs-fuse-csi-driver" ]; then
  echo "gcs-fuse-csi-driver directory already exists. Pulling latest changes."
  cd gcs-fuse-csi-driver
  git pull origin main
else
  echo "Cloning gcs-fuse-csi-driver repository."
  git clone https://github.com/GoogleCloudPlatform/gcs-fuse-csi-driver.git
  cd gcs-fuse-csi-driver
fi

echo "--- Building and pushing gcs-fuse-csi-driver multi-arch image ---"
USER_NAME=$(whoami)

# Execute the make command without redirecting stderr to output.log
# Only stdout is redirected to output.log for later parsing of the image ID.
# This ensures that any errors from 'make' will be visible in the console.
make build-image-and-push-multi-arch REGISTRY="gcr.io/$PROJECT/${USER_NAME}_${BRANCH}" GCSFUSE_PATH="gs://$BUCKET" > ~/output.log

echo "--- Sidecar image ID ----"

# The 'grep' command will still read from the output.log for the image ID.
cat ~/output.log | grep "gcr.io/$PROJECT/${USER_NAME}_${BRANCH}/gcs-fuse-csi-driver-sidecar-mounter:"

echo "--- Script execution completed successfully! ---"
