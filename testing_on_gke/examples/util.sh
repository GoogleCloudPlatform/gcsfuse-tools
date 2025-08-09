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

if [ -n "$_UTIL_SH_SOURCED" ]; then
  return
fi
export _UTIL_SH_SOURCED=1

set -e

# Print all the shell commands if the user passes argument `--debug`. This is
# useful for debugging the script.
if ([ $# -gt 0 ] && ([ "$1" == "-debug" ] || [ "$1" == "--debug" ])); then
  set -x
fi

# Utilities
function exitWithSuccess() { exit 0; }
function exitWithFailure() { exit 1; }
function echoerror()  { >&2 echo "Error: "$@ ; }
function echowarning()  { >&2 echo "Warning: "${@} ; }
function exitWithError()  { echoerror "$@" ; exitWithFailure ; }
function returnWithError()  { echoerror "$@" ; return 1 ; }

function run_or_die() {
  "$@"
  local status=$?
  if [ $status -ne 0 ]; then
    echoerror "Command failed with status $status: $@"
    exit $status
  fi
  return 0
}

function uuid() {
  echo $(uuidgen) | sed -e "s/\-//g" ;
}

function verify_csi_driver_image() {
  if [[ $# < 1 ]]; then
    returnWithError "No arguments passed to verify_csi_driver_image. Expected: \$1=<csi-driver-image> ."
  fi
  local csi_driver_image=${1}
  echo "Checking ${csi_driver_image} ..."
  if ! gcloud -q container images describe ${csi_driver_image} >/dev/null; then
    returnWithError "${csi_driver_image} is not a valid GCSFuse csi driver image.  !!! Please check if you missed adding /gcs-fuse-csi-driver-sidecar-mounter before the hash. !!!"
  fi
}

