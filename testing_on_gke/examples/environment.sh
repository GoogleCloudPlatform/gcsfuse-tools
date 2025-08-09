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

if [ -n "$_ENVIRONMENT_SH_SOURCED" ]; then
  return
fi
export _ENVIRONMENT_SH_SOURCED=1

SCRIPT_DIR=$(cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd)
source "${SCRIPT_DIR}/util.sh" "${@}"

# Default values, to be used for parameters in case user does not specify them.
# GCP related
readonly DEFAULT_ZONE="us-west1-b"
# GKE cluster related
readonly DEFAULT_NODE_POOL=default-pool
readonly DEFAULT_MACHINE_TYPE="n2-standard-96"
readonly DEFAULT_NUM_NODES=8
readonly DEFAULT_NUM_SSD=16
readonly DEFAULT_APPNAMESPACE=default
readonly DEFAULT_KSA=default
readonly DEFAULT_USE_CUSTOM_CSI_DRIVER=true
readonly DEFAULT_CUSTOM_CSI_DRIVER=
# GCSFuse tools/GKE GCSFuse CSI Driver source code related
readonly DEFAULT_SRC_DIR="$(realpath .)/src"
readonly csi_driver_github_path=https://github.com/googlecloudplatform/gcs-fuse-csi-driver
readonly csi_driver_branch=main
readonly gcsfuse_tools_github_path=https://github.com/googlecloudplatform/gcsfuse-tools
readonly DEFAULT_GCSFUSE_TOOLS_BRANCH=main
readonly gcsfuse_github_path=https://github.com/googlecloudplatform/gcsfuse
readonly DEFAULT_GCSFUSE_BRANCH=master
# Test runtime configuration
# 5 minutes
readonly DEFAULT_POD_WAIT_TIME_IN_SECONDS=300
# 1 week
readonly DEFAULT_POD_TIMEOUT_IN_SECONDS=604800
readonly DEFAULT_FORCE_UPDATE_GCSFUSE_TOOLS_CODE=false
readonly DEFAULT_FORCE_UPDATE_GCSFUSE_CODE=false
readonly DEFAULT_ZONAL=false

# Config for exporting fio outputs to a Bigquery table.
readonly DEFAULT_BQ_PROJECT_ID='gcs-fuse-test-ml'
readonly DEFAULT_BQ_DATASET_ID='gke_test_tool_outputs'
readonly DEFAULT_BQ_TABLE_ID='fio_outputs'

# Create and return a unique experiment_id taking
# into account user's passed experiment_id.
function create_unique_experiment_id() {
  new_uuid=$(cat /dev/urandom | tr -dc 'a-z0-9' | fold -w 4 | head -n 1)
  local generated_unique_experiment_id=${USER}-$(date +%Y%m%d-%H%M%S)-${new_uuid}
  if [ $# -gt 0 ] && [ -n "${1}" ]; then
    local user_provided_experiment_id="${1}"
    experiment_id=${user_provided_experiment_id// /-}"-"${generated_unique_experiment_id}
  else
    experiment_id=${generated_unique_experiment_id}
  fi
  echo "${experiment_id}"
}

function printHelp() {
  echo "Usage guide: "
  echo "[ENV_OPTIONS] "${0}" [ARGS]"
  echo ""
  echo "ENV_OPTIONS (all are optional): "
  echo ""
  # GCP related
  echo "project_id=<project-id>"
  echo "project_number=<number>"
  echo "zone=<region-zone default=\"${DEFAULT_ZONE}\">"
  # GKE cluster related
  echo "cluster_name=<cluster-name>"
  echo "node_pool=<pool-name default=\"$ {DEFAULT_NODE_POOL}\">"
  echo "machine_type=<machine-type default=\"$ {DEFAULT_MACHINE_TYPE}\">"
  echo "num_nodes=<number from 1-8, default=\"$ {DEFAULT_NUM_NODES}\">"
  echo "num_ssd=<number from 0-16, default=\"$ {DEFAULT_NUM_SSD}\">"
  echo "custom_csi_driver=<string representing the full path of the csi-driver image hash e.g. gcr.io/<registry-name>:<hash>, default=\"$ {DEFAULT_CUSTOM_CSI_DRIVER}\". If it is non-empty, then use_custom_csi_driver is assumed true, but a custom driver is not built and the given custom csi driver is used instead. >"
  echo "use_custom_csi_driver=<true|false, true means build and use a new custom csi driver using gcsfuse code, default=\"$ {DEFAULT_USE_CUSTOM_CSI_DRIVER}">"
  # GCSFuse/GKE GCSFuse CSI Driver source code related
  echo "src_dir=<\"directory/to/clone/github/repos/if/needed\", used for creating local clones of repos in case when gcsfuse, gcsfuse_tools_src_dir or csi_src_dir are not passed, default=\"$ {DEFAULT_SRC_DIR}\">"
  echo "gcsfuse_tools_branch=<name-of-gcsfuse-tools-branch-for-cloning>, used for locally cloning, in case gcsfuse_tools_src_dir has not been passed, default=\"$ {DEFAULT_GCSFUSE_TOOLS_BRANCH}\">"
  echo "gcsfuse_tools_src_dir=<\"/path/of/gcsfuse-tools/src/to/use/if/available\", default=\"$ {DEFAULT_SRC_DIR}/gcsfuse-tools>"
  echo "gcsfuse_branch=<name-of-gcsfuse-branch-for-cloning>, used for locally cloning, in case gcsfuse_src_dir has not been passed, default=\"$ {DEFAULT_GCSFUSE_BRANCH}\">"
  echo "gcsfuse_src_dir=<\"/path/of/gcsfuse/src/to/use/if/available\", default=\"$ {DEFAULT_SRC_DIR}/gcsfuse>"
  echo "csi_src_dir=<\"/path/of/gcs-fuse-csi-driver/to/use/if/available\", default=\"$ {DEFAULT_SRC_DIR}\"/gcs-fuse-csi-driver>""
  # Test runtime configuration
  echo "pod_wait_time_in_seconds=<number e.g. 60 for checking pod status every 1 min, default=\"$ {DEFAULT_POD_WAIT_TIME_IN_SECONDS}\">"
  echo "pod_timeout_in_seconds=<number e.g. 3600 for timing out pod runs, should be more than the value of pod_wait_time_in_seconds, default=\"$ {DEFAULT_POD_TIMEOUT_IN_SECONDS}\">"
  echo "experiment_id=<Optional description of this particular test-run, it does not need to be unique e.g. \"cache test #43"
  echo "workload_config=<path/to/workload/configuration/file e.g. /a/b/c.json >"
  echo "output_dir=</absolute/path/to/output/dir, output files will be written at output_dir/fio/output.csv and output_dir/dlio/output.csv>"
  echo "force_update_gcsfuse_tools_code=<true|false, to force-update the gcsfuse-tools code to given branch if gcsfuse_tools_src_dir has been set. Default=\"$ {DEFAULT_FORCE_UPDATE_GCSFUSE_TOOLS_CODE}\">"
  echo "force_update_gcsfuse_code=<true|false, to force-update the gcsfuse-code to given branch if gcsfuse_src_dir has been set. Default=\"$ {DEFAULT_FORCE_UPDATE_GCSFUSE_CODE}\">"
  echo "zonal=<true|false, to convey that at least one of the buckets in the given workload configuration is a zonal bucket which can't be read/written using gcloud. Default=\"$ {DEFAULT_ZONAL}\"> "
  echo ""
  echo ""
  echo ""
  echo "ARGS (all are optional) : "
  echo ""
  echo "--debug     Print out shell commands for debugging. Aliases: -debug "
  echo "--help      Print out this help. Aliases: -help, -h"
}

function printRunParameters() {
  echo "Running $0 with following parameters:"
  echo ""
  # GCP related
  echo "project_id=\"${project_id}\""
  echo "project_number=\"${project_number}\""
  echo "zone=\"${zone}\""
  # GKE cluster related
  echo "cluster_name=\"${cluster_name}\""
  echo "node_pool=\"${node_pool}\""
  echo "machine_type=\"${machine_type}\""
  echo "num_nodes=\"${num_nodes}\""
  echo "num_ssd=\"${num_ssd}\""
  echo "appnamespace=\"${appnamespace}\""
  echo "ksa=\"${ksa}\""
  echo "use_custom_csi_driver=\"${use_custom_csi_driver}\""
  echo "custom_csi_driver=\"${custom_csi_driver}\""
  # GCSFuse/GKE GCSFuse CSI Driver source code related
  echo "src_dir=\"${src_dir}\""
  echo "gcsfuse_tools_src_dir=\"${gcsfuse_tools_src_dir}\""
  if test -n "${gcsfuse_src_dir}"; then
    echo "gcsfuse_src_dir=\"${gcsfuse_src_dir}\""
  fi
  if test -n "${csi_src_dir}"; then
    echo "csi_src_dir=\"${csi_src_dir}\""
  fi
  echo "gke_testing_dir=\"${gke_testing_dir}\""
  # Test runtime configuration
  echo "pod_wait_time_in_seconds=\"${pod_wait_time_in_seconds}\""
  echo "pod_timeout_in_seconds=\"${pod_timeout_in_seconds}\""
  echo "experiment_id=User passed: \"${user_passed_experiment_id}\", internally created: \"${experiment_id}\""
  echo "workload_config=\"${workload_config}\""
  echo "output_dir=\"${output_dir}\""
  echo "force_update_gcsfuse_tools_code=\"${force_update_gcsfuse_tools_code}\""
  if test -n "${force_update_gcsfuse_code}"; then
    echo "force_update_gcsfuse_code=\"${force_update_gcsfuse_code}\""
  fi
  echo "zonal=\"${zonal}\""
  if ${only_parse}; then
    echo "only_parse=${only_parse}"
  fi
  echo ""
  echo ""
  echo ""
}

function initialize_environment() {
  # Set environment variables.
  # GCP related
  if test -z "${project_id}"; then
      exitWithError "project_id was not set"
  fi
  if test -z "${project_number}"; then
      exitWithError "project_number was not set"
  fi
  test -n "${zone}" || export zone=${DEFAULT_ZONE}
  # GKE cluster related
  if test -z "${cluster_name}"; then
    exitWithError "cluster_name was not set."
  fi
  test -n "${node_pool}" || export node_pool=${DEFAULT_NODE_POOL}
  test -n "${machine_type}" || export machine_type=${DEFAULT_MACHINE_TYPE}
  test -n "${num_nodes}" || export num_nodes=${DEFAULT_NUM_NODES}
  test -n "${num_ssd}" || export num_ssd=${DEFAULT_NUM_SSD}
  # test -n "${appnamespace}" ||
  export appnamespace=${DEFAULT_APPNAMESPACE}
  # test -n "${ksa}" ||
  export ksa=${DEFAULT_KSA}

  applied_custom_csi_driver=
  if test -z "${custom_csi_driver}"; then
    echo "custom_csi_driver has not been set, so assuming \"${DEFAULT_CUSTOM_CSI_DRIVER}\" for it ..."
    export custom_csi_driver="${DEFAULT_CUSTOM_CSI_DRIVER}"
    if test -z "${use_custom_csi_driver}"; then
      echo "use_custom_csi_driver has not been set, so assuming \"${DEFAULT_USE_CUSTOM_CSI_DRIVER}\" for it ..."
      export use_custom_csi_driver="${DEFAULT_USE_CUSTOM_CSI_DRIVER}"
    elif [[ ${use_custom_csi_driver} = "true" ]]; then
      echo "User has enabled use_custom_csi_driver, without passing a custom_csi_driver, so a custom driver will be built in this run."
    elif [[ ${use_custom_csi_driver} != "false" ]]; then
      exitWithError "Unsupported value passed for use_custom_csi_driver: ${use_custom_csi_driver}. Supported values: true/false ."
    fi
  else
    echo "User passed custom_csi_driver=${custom_csi_driver}. This will be used this run."
    printf "\nVerifying that ${custom_csi_driver} is a valid GCSFuse csi driver image ...\n\n"
    verify_csi_driver_image ${custom_csi_driver}
    if test -z "${use_custom_csi_driver}"; then
      echo "use_custom_csi_driver has not been set, so setting it to true as custom_csi_driver has been set to \"${custom_csi_driver}\""
      export use_custom_csi_driver=true
    elif [[ ${use_custom_csi_driver} = "false" ]]; then
      exitWithError "User has disabled use_custom_csi_driver, while passing a custom_csi_driver. This is unsupported."
    elif [[ ${use_custom_csi_driver} != "true" ]]; then
      exitWithError "Unsupported value passed for use_custom_csi_driver: ${use_custom_csi_driver}. Supported values: true or false ."
    fi
    applied_custom_csi_driver=${custom_csi_driver}
  fi

  test -n "${gcsfuse_tools_branch}" || export gcsfuse_tools_branch="${DEFAULT_GCSFUSE_TOOLS_BRANCH}"
  test -n "${gcsfuse_branch}" || export gcsfuse_branch="${DEFAULT_GCSFUSE_BRANCH}"

  # GCSFuse/GKE GCSFuse CSI Driver source code related
  if test -n "${src_dir}"; then
    if ! test -d "${src_dir}"; then
      exitWithError "src_dir \"${src_dir}\" does not exist"
    fi
    export src_dir="$(realpath "${src_dir}")"
  else
    export src_dir=${DEFAULT_SRC_DIR}
    mkdir -pv "${src_dir}"
  fi

  if test -n "${gcsfuse_tools_src_dir}"; then
    if ! test -d "${gcsfuse_tools_src_dir}"; then
      exitWithError "gcsfuse_tools_src_dir \"${gcsfuse_tools_src_dir}\" does not exist"
    fi
    export gcsfuse_tools_src_dir="$(realpath "${gcsfuse_tools_src_dir}")"
  else
    export gcsfuse_tools_src_dir="${src_dir}"/gcsfuse-tools
  fi

  if test -z "${force_update_gcsfuse_tools_code}"; then
    export force_update_gcsfuse_tools_code=${DEFAULT_FORCE_UPDATE_GCSFUSE_TOOLS_CODE}
  fi

  export gke_testing_dir="${gcsfuse_tools_src_dir}"/testing_on_gke

  if test -n "${gcsfuse_src_dir}"; then
    if ! test -d "${gcsfuse_src_dir}"; then
      exitWithError "gcsfuse_src_dir has been passed as \"${gcsfuse_src_dir}\", which does not exist"
    fi
    export gcsfuse_src_dir="$(realpath "${gcsfuse_src_dir}")"
  fi

  if test -n "${csi_src_dir}"; then
    if ! test -d "${csi_src_dir}"; then
      exitWithError "csi_src_dir \"${csi_src_dir}\" does not exist"
    fi
    export csi_src_dir="$(realpath "${csi_src_dir}")"
  fi

  # Test runtime configuration
  test -n "${pod_wait_time_in_seconds}" || export pod_wait_time_in_seconds="${DEFAULT_POD_WAIT_TIME_IN_SECONDS}"
  test -n "${pod_timeout_in_seconds}" || export pod_timeout_in_seconds="${DEFAULT_POD_TIMEOUT_IN_SECONDS}"

  if test -z ${only_parse} ; then
    export only_parse=false
  elif [ "$only_parse" != "true" ] && [ "$only_parse" != "false" ]; then
    exitWithError "Unexpected value of only_parse: ${only_parse}. Expected: true or false ."
  fi

  # If user passes only_parse=true, then expect an experiment_id
  # also with it, and use it as it is.
  if ${only_parse};
    then
    if [ -z "${experiment_id}" ]; then
      exitWithError "experiment_id not passed with only_parse=true"
    fi
  else
    # create a new experiment_id
    export user_passed_experiment_id="${experiment_id}"
    export experiment_id=$(create_unique_experiment_id "${user_passed_experiment_id}")
  fi

  if [[ ${pod_timeout_in_seconds} -le ${pod_wait_time_in_seconds} ]]; then
    exitWithError "pod_timeout_in_seconds (${pod_timeout_in_seconds}) <= pod_wait_time_in_seconds (${pod_wait_time_in_seconds})"
  fi

  if test -n "${workload_config}"; then
    if ! test -f "${workload_config}"; then
      exitWithError "workload_config \"${workload_config}\" does not exist"
    fi
    export workload_config="$(realpath "${workload_config}")"
  else
      export workload_config="${gke_testing_dir}"/examples/workloads.json
  fi

  if test -n "${output_dir}"; then
    if ! test -d "${output_dir}"; then
      exitWithError "output_dir \"${output_dir}\" does not exist"
    fi
    export output_dir="$(realpath "${output_dir}")"
  else
    export output_dir="${gke_testing_dir}"/examples
  fi

  if test -z "${zonal}"; then
    echo "env var zonal not set, so assuming ${DEFAULT_ZONAL} for it."
    export zonal=${DEFAULT_ZONAL}
  elif [[ ${zonal} != "true" && "${zonal}" != "false" ]]; then
    exitWithError "env var zonal should be set as false, or true, but received: ${zonal}"
  fi

  printRunParameters
}

function main() {
  initialize_environment
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main
fi
