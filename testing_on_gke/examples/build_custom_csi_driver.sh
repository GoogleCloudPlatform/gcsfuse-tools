#!/bin/bash
#
# Copyright 2024 Google LLC
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

if [ -n "$_BUILD_CUSTOM_CSI_DRIVER_SH_SOURCED" ]; then
  return
fi
export _BUILD_CUSTOM_CSI_DRIVER_SH_SOURCED=1

SCRIPT_DIR=$(cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd)
source "${SCRIPT_DIR}/environment.sh" "${@}"

function ensureGcsfuseCode() {
  printf "\nEnsuring we have gcsfuse code ...\n\n\n"
  if test -z "${gcsfuse_src_dir}"; then
    export gcsfuse_src_dir="${src_dir}"/gcsfuse
  fi
  if test -z "${force_update_gcsfuse_code}"; then
    export force_update_gcsfuse_code=${DEFAULT_FORCE_UPDATE_GCSFUSE_CODE}
  fi

  # clone gcsfuse code if needed
  if ! test -d "${gcsfuse_src_dir}"; then
    mkdir -pv $(dirname "${gcsfuse_src_dir}") && cd $(dirname "${gcsfuse_src_dir}") && git clone ${gcsfuse_github_path} && cd "${gcsfuse_src_dir}" && git switch ${gcsfuse_branch} && cd - >/dev/null && cd - >/dev/null
  elif ${force_update_gcsfuse_code}; then
    cd ${gcsfuse_src_dir} && git fetch --all && git reset --hard origin/${gcsfuse_branch} && cd - >/dev/null
  fi
}

function ensureGcsFuseCsiDriverCode() {
  printf "\nEnsuring we have gcs-fuse-csi-driver code ...\n\n"
  if test -z "${csi_src_dir}"; then
    export csi_src_dir="${src_dir}"/gcs-fuse-csi-driver
  fi
  # clone csi-driver code if needed
  if ! test -d "${csi_src_dir}"; then
    mkdir -pv $(dirname "${csi_src_dir}") && cd $(dirname "${csi_src_dir}") && git clone ${csi_driver_github_path} && cd "${csi_src_dir}" && git switch ${csi_driver_branch} && cd - >/dev/null && cd - >/dev/null
  fi
}

function createCustomCsiDriverIfNeeded() {
  if ${use_custom_csi_driver} && test -z "${applied_custom_csi_driver}"; then
    printf "\nCreating a new custom CSI driver ...\n\n"

    # Create a bucket (if needed) for storing GCSFuse binaries.
    if test -z "${package_bucket}"; then
      package_bucket=${project_id}-${cluster_name}-gcsfuse-bin
      package_bucket=${package_bucket/google/}
    fi
    if [[ ${#package_bucket} -gt 63 ]] ; then
      echoerror "package_bucket \"${package_bucket}\" is too long (should be <= 63)"
      return 1
    fi
    # If package_bucket does not already exist, create it.
    if (! (gcloud storage buckets list --project=${project_id} | grep -wqo ${package_bucket}) ); then
      region=$(echo ${zone} | rev | cut -d- -f2- | rev)
      gcloud storage buckets create gs://${package_bucket} --project=${project_id} --location=${region}
    fi

    # Ensure that gcsfuse source code is available by now for building a binary
    # from it.
    ensureGcsfuseCode

    # Build new gcsfuse binaries.
    printf "\nBuilding a new GCSFuse binary from ${gcsfuse_src_dir} ...\n\n"
    cd "${gcsfuse_src_dir}"
    rm -rfv ./bin ./sbin
    GOOS=linux GOARCH=amd64 go run tools/build_gcsfuse/main.go . . v3
    # Copy the binary to a GCS bucket for csi driver build.
    gcloud storage -q cp ./bin/gcsfuse gs://${package_bucket}/linux/amd64/
    gcloud storage -q cp gs://${package_bucket}/linux/amd64/gcsfuse gs://${package_bucket}/linux/arm64/ # needed as build on arm64 doesn't work on cloudtop.
    # clean-up
    rm -rfv "${gcsfuse_src_dir}"/bin "${gcsfuse_src_dir}"/sbin
    cd - >/dev/null

    # Build and install csi driver
    ensureGcsFuseCsiDriverCode
    cd "${csi_src_dir}"
    make generate-spec-yaml
    printf "\nBuilding a new custom CSI driver using the above GCSFuse binary ...\n\n"
    registry=gcr.io/${project_id}/${USER}/${cluster_name}
    if ! which uuidgen; then
      # try to install uuidgen
      sudo apt-get update && sudo apt-get install -y uuid-runtime
      # confirm that it got installed.
      which uuidgen
    fi
    stagingversion=$(uuid)
    make build-image-and-push-multi-arch REGISTRY=${registry} GCSFUSE_PATH=gs://${package_bucket} STAGINGVERSION=${stagingversion}

    readonly subregistry=gcs-fuse-csi-driver-sidecar-mounter
    applied_custom_csi_driver=${registry}/${subregistry}:${stagingversion}
    printf "\n\nCreated custom csi driver \" ${applied_custom_csi_driver} \" . To use it in future runs, please pass environment variable \" custom_csi_driver=${applied_custom_csi_driver} \" .\n\n"

    # Verify that the csi-driver image is a good image to use..
    printf "\nVerifying that ${applied_custom_csi_driver} is a valid GCSFuse csi driver image ...\n\n"
    sleep 30
    verify_csi_driver_image ${applied_custom_csi_driver}

    cd - >/dev/null
  fi
}


if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  initialize_environment
  createCustomCsiDriverIfNeeded
fi

