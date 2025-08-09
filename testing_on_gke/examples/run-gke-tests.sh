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

# This script is for running fio/dlio tests using GKE.
# This is a stand-alone script, and can be invoked directly by a user.
# It takes in parameters through environment variables. For learning about them, run this script with `--help` argument.
# For debugging, pass argument `--debug` which will print all the shell commands that runs.
# It fetches gcsfuse, gcsfuse-tools and GKE GCSFuse CSI driver (gcs-fuse-csi-driver) code from github, if you don't provide it pre-existing clones of them.
# It installs all the necessary dependencies on its own.
# It creates a GKE cluster and other GCP resources (as needed), based on a number of configuration parameters e.g. gcp-project-name/number, cluster-name, zone (for resource location), machine-type (of node), number of local SSDs.
# It creates fio/dlio tests as helm charts, based on the provided JSON workload configuration file and deploys them on the GKE cluster.
# A sample workload-configuration file is available at https://github.com/GoogleCloudPlatform/gcsfuse-tools/blob/main/testing_on_gke/examples/workloads.json .

SCRIPT_DIR=$(cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd)
source "${SCRIPT_DIR}/environment.sh" "${@}"
source "${SCRIPT_DIR}/install_dependencies.sh" "${@}"
source "${SCRIPT_DIR}/setup_cluster.sh" "${@}"
source "${SCRIPT_DIR}/build_custom_csi_driver.sh" "${@}"
source "${SCRIPT_DIR}/run_and_parse_workloads.sh" "${@}"

function ensureGcsfuseToolsCode() {
  printf "\nEnsuring we have gcsfuse-tools code ...\n\n\n"
  # clone gcsfuse-tools repo if needed
  if ! test -d "${gcsfuse_tools_src_dir}"; then
    cd $(dirname "${gcsfuse_tools_src_dir}") && git clone ${gcsfuse_tools_github_path} && cd "${gcsfuse_tools_src_dir}" && git switch ${gcsfuse_tools_branch} && cd - >/dev/null && cd - >/dev/null
  elif ${force_update_gcsfuse_tools_code}; then
    cd ${gcsfuse_tools_src_dir} && git fetch --all && git reset --hard origin/${gcsfuse_tools_branch} && cd - >/dev/null
  fi

  test -d "${gke_testing_dir}" || (exitWithError "${gke_testing_dir} does not exist" )
}


# Handling of deprecated flag instance_id if it has been passed.
if test -n "${instance_id}" ; then
  deprecation_message="instance_id flag is now deprecated, but has been passed (with value \"${instance_id}\"). In future, please use experiment_id instead."

  # If instance_id is set, but experiment_id is not
  # set, then let this be only a warning message and pass the value of
  # instance_id to experiment_id.
  if test -z "${experiment_id}" ;
  then
    echowarning ${deprecation_message}" For now, setting experiment_id=\"$\{instance_id}\" ."
    export experiment_id="${instance_id}"
    unset instance_id
  else
    # Otherwise, halt the run as this is an ambiguous situation.
    exitWithError "${deprecation_message}"
  fi
fi

# Print out help if user passes argument `--help`
if ([ $# -gt 0 ] && ([ "$1" == "-help" ] || [ "$1" == "--help" ] || [ "$1" == "-h" ])); then
  printHelp
  exitWithSuccess
fi

run_or_die initialize_environment


# if only_parse is false, then run the setup and deployment
if ! ${only_parse} ; then
  run_or_die installDependencies
  run_or_die setup_cluster
  run_or_die ensureGcsfuseToolsCode
  run_or_die createCustomCsiDriverIfNeeded
  run_or_die deploy_workloads
fi

# monitor pods and parse results
run_or_die monitor_and_parse_workloads

if test -z "${custom_csi_driver}" && test -n "${applied_custom_csi_driver}"; then
  printf "\nTo reuse this custom CSI driver in future runs, pass environment variable \" custom_csi_driver=${applied_custom_csi_driver} \" .\n\n"
fi
