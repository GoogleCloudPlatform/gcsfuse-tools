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

# Note: The script uses apt-get and dpkg for installing dependencies, which ties it to Debian-based Linux distributions like Ubuntu/Debian.

# How to run this script:
# ./create_csi_driver.sh --branch-name <commit-id/branch-name/tag> --bucket <bucket-name> --project <project-id>
# Pass --skip-prerequisites to skip prerequisite installations (Docker, Go).

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Global Variables ---
BRANCH=""
BUCKET=""
PROJECT=""
SKIP_PREREQUISITES=false

# --- Functions ---

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

parse_arguments() {
  log "Parsing command-line arguments..."
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      --branch-name)
        BRANCH="$2"; shift 2;;
      --bucket)
        BUCKET="$2"; shift 2;;
      --project)
        PROJECT="$2"; shift 2;;
      --skip-prerequisites)
        SKIP_PREREQUISITES=true; shift 1;;
      *)
        log "Unknown parameter passed: $1"
        exit 1
        ;;
    esac
  done

  if [[ -z "$BRANCH" || -z "$BUCKET" || -z "$PROJECT" ]]; then
    log "Usage: $0 --branch-name <branch> --bucket <bucket> --project <project> [--skip-prerequisites](optional)"
    exit 1
  fi
}

install_prerequisites() {
  if $SKIP_PREREQUISITES; then
    log "Skipping prerequisite installation as requested."
    return
  fi

  log "Installing prerequisites (Docker, Go)..."
  
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl gnupg
  
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
  sudo chmod a+r /etc/apt/keyrings/docker.asc

  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  architecture=$(dpkg --print-architecture)
  log "Installing Go 1.24.0..."
  wget -O go.tar.gz "https://go.dev/dl/go1.24.0.linux-${architecture}.tar.gz" -q
  sudo rm -rf /usr/local/go
  tar -xzf go.tar.gz
  sudo mv go /usr/local
  export PATH=$PATH:/usr/local/go/bin
}

build_and_upload_gcsfuse() {
  log "Building and uploading gcsfuse..."

  if [ -d "gcsfuse" ]; then
    log "gcsfuse directory already exists. Resetting to branch '$BRANCH'."
    cd gcsfuse
    git fetch origin
    git checkout "$BRANCH"
    git reset --hard "origin/$BRANCH"
  else
    log "Cloning gcsfuse repository."
    git clone https://github.com/googleCloudPlatform/gcsfuse
    cd gcsfuse
    log "--- Checking out branch: $BRANCH ---"
    git checkout "$BRANCH"
  fi

COMMIT_HASH=$(git rev-parse --short HEAD)
log "Building gcsfuse from commit ${COMMIT_HASH}"
GOOS=linux GOARCH=amd64 go run tools/build_gcsfuse/main.go . . "${COMMIT_HASH}"

  log "Uploading gcsfuse binary to gs://$BUCKET/linux/amd64/"
  gcloud storage cp "./bin/gcsfuse" "gs://$BUCKET/linux/amd64/"

  rm -rf ./bin ./sbin || true
  cd ..
  sudo rm -rf gcsfuse
}

build_and_push_csi_driver() {
  log "Building and pushing gcs-fuse-csi-driver image..."

  if [ -d "gcs-fuse-csi-driver" ]; then
    log "gcs-fuse-csi-driver directory already exists. Resetting to latest on main."
    cd gcs-fuse-csi-driver
    git fetch origin
    git checkout main
    git reset --hard origin/main
  else
    log "Cloning gcs-fuse-csi-driver repository."
    git clone https://github.com/GoogleCloudPlatform/gcs-fuse-csi-driver.git
    cd gcs-fuse-csi-driver
  fi

  USER_NAME=$(whoami)
  ID=$(date +%Y%m%d%H%M%S)
  sudo make build-image-and-push-multi-arch REGISTRY="gcr.io/${PROJECT}/${USER_NAME}_${ID}" GCSFUSE_PATH="gs://$BUCKET" > /tmp/output.log

  log "Sidecar image ID:"
  grep "gcr.io/$PROJECT/${USER_NAME}_${ID}/gcs-fuse-csi-driver-sidecar-mounter:" /tmp/output.log

  cd ..
  sudo rm -rf gcs-fuse-csi-driver
}

main() {
  parse_arguments "$@"

  # To make sure the script runs in a clean environment
  cd /tmp

  install_prerequisites
  build_and_upload_gcsfuse
  build_and_push_csi_driver

  log "Script execution completed successfully!"
}

# --- Run Script ---
main "$@"
