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

if [ -n "$_SETUP_CLUSTER_SH_SOURCED" ]; then
  return
fi
export _SETUP_CLUSTER_SH_SOURCED=1

SCRIPT_DIR=$(cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd)
source "${SCRIPT_DIR}/environment.sh" ${@}

# Make sure you have access to the necessary GCP resources. The easiest way to enable it is to use <your-ldap>@google.com as active auth. 
function ensureGcpAuthsAndConfig() {
  # gcloud auth application-default login --no-launch-browser
  gcloud auth list
  # grep -q ${USER}
  gcloud config set project ${project_id}
  gcloud config list
}

# Verify that the passed machine configuration parameters (machine-type, num-nodes, num-ssd) are compatible. 
# This is to fail fast, right at the start of the script, rather than failing at
# cluster/nodepool creation, which takes a lot longer.
# Source of constraints:
# https://cloud.google.com/compute/docs/disks/local-ssd#lssd_disk_options . 
function validateMachineConfig() {
  echo "Validating input machine configuration ..."
  local machine_type=${1}
  local num_nodes=${2}
  local num_ssd=${3}

  if test ${num_nodes} -le 0; then
    echo "num_nodes is too low (minimium=1) at "${num_nodes}
  fi

  case "${machine_type}" in
  "n2-standard-96")
    if [ ${num_ssd} -ne 0 -a ${num_ssd} -ne 16 -a ${num_ssd} -ne 24 ]; then
      echoerror "Unsupported num-ssd "${num_ssd}" with given machine-type "${machine_type}". It should be 0, 16 or 24"
      return 1
    fi
    ;; 
  "n2-standard-48")
    if [ ${num_ssd} -ne 0 -a ${num_ssd} -ne 8 -a ${num_ssd} -ne 16 -a ${num_ssd} -ne 24 ]; then
      echoerror "Unsupported num-ssd "${num_ssd}" with given machine-type "${machine_type}". It should be 0, 8, 16 or 24"
      return 1
    fi
    ;; 
  "n2-standard-32")
    if [ ${num_ssd} -ne 0 -a ${num_ssd} -ne 4 -a ${num_ssd} -ne 8 -a ${num_ssd} -ne 16 -a ${num_ssd} -ne 24 ]; then
      echoerror "Unsupported num-ssd "${num_ssd}" with given machine-type "${machine_type}". It should be 0, 4, 8, 16 or 24"
      return 1
    fi
    ;; 
  *) ;; 
  esac

  return 0
}

function doesNodePoolExist() {
  local cluster_name=${1}
  local zone=${2}
  local node_pool=${3}
  gcloud container node-pools list --project=${project_id} --cluster=${cluster_name} --zone=${zone} | grep -owq ${node_pool}
}

function deleteExistingNodePool() {
  local cluster_name=${1}
  local zone=${2}
  local node_pool=${3}
  if doesNodePoolExist ${cluster_name} ${zone} ${node_pool};
  then
    gcloud -q container node-pools delete ${node_pool} --project=${project_id} --cluster ${cluster_name} --zone ${zone}
  fi
}

function resizeExistingNodePool() {
  local cluster_name=${1}
  local zone=${2}
  local node_pool=${3}
  local num_nodes=${4}
  if doesNodePoolExist ${cluster_name} ${zone} ${node_pool};
  then
    gcloud -q container clusters resize ${cluster_name} --project=${project_id} --node-pool=${node_pool} --num-nodes=${num_nodes} --zone ${zone}
  fi
}

function createNewNodePool() {
  local cluster_name=${1}
  local zone=${2}
  local node_pool=${3}
  local machine_type=${4}
  local num_nodes=${5}
  local num_ssd=${6}
  gcloud container node-pools create ${node_pool} --project=${project_id} --cluster ${cluster_name} --ephemeral-storage-local-ssd count=${num_ssd} --network-performance-configs=total-egress-bandwidth-tier=TIER_1 --machine-type ${machine_type} --zone ${zone} --num-nodes ${num_nodes} --workload-metadata=GKE_METADATA --enable-gvnic
}

function getMachineTypeInNodePool() {
  local cluster=${1}
  local node_pool=${2}
  local zone=${3}
  gcloud container node-pools describe --project=${project_id} --cluster=${cluster_name} ${node_pool} --zone=${zone} | grep -w 'machineType' | tr -s '\t' ' ' | rev | cut -d' ' -f1 | rev
}

function getNumNodesInNodePool() {
  local cluster=${1}
  local node_pool=${2}
  local zone=${3}
  gcloud container node-pools describe --project=${project_id} --cluster=${cluster_name} ${node_pool} --zone=${zone} | grep -w 'initialNodeCount' | tr -s '\t' ' ' | rev | cut -d' ' -f1 | rev
}

function getNumLocalSSDsPerNodeInNodePool() {
  local cluster=${1}
  local node_pool=${2}
  local zone=${3}
  gcloud container node-pools describe --project=${project_id} --cluster=${cluster_name} ${node_pool} --zone=${zone} | grep -w 'localSsdCount' | tr -s '\t' ' ' | rev | cut -d' ' -f1 | rev
}

function doesClusterExist() {
  local cluster_name=${1}
  gcloud container clusters list --project=${project_id} | grep -woq ${cluster_name}
}

# Create and set up (or reconfigure) a GKE cluster.
function ensureGkeCluster() {
  echo "Creating/updating cluster ${cluster_name} ..."
  if doesClusterExist ${cluster_name};
  then
    existing_machine_type=$(getMachineTypeInNodePool ${cluster_name} ${node_pool} ${zone})
    if [ "${existing_machine_type}" != "${machine_type}" ] ; then
      echo "Internally changing machine-type from ${machine_type} to ${existing_machine_type} ..."
      machine_type=${existing_machine_type}
    fi
    cluster_updation_command="gcloud container clusters update ${cluster_name} --project=${project_id} --location=${zone}"
    ${cluster_updation_command} --workload-pool=${project_id}.svc.id.goog
    # Separating in two update calls as gcloud doesn't support updating these
    # two fields in a single call.
    if ${zonal}; then
      ${cluster_updation_command} --private-ipv6-google-access-type=bidirectional
    fi
  else
    # Create a new cluster
    cluster_creation_args="--project=${project_id} --zone ${zone} --workload-pool=${project_id}.svc.id.goog --machine-type ${machine_type} --image-type COS_CONTAINERD --num-nodes ${num_nodes} --ephemeral-storage-local-ssd count=${num_ssd} --network-performance-configs=total-egress-bandwidth-tier=TIER_1 --workload-metadata=GKE_METADATA --enable-gvnic"
    if ${zonal}; then
      cluster_creation_args+=" --private-ipv6-google-access-type=bidirectional"
    fi
    gcloud container clusters create ${cluster_name} ${cluster_creation_args}
  fi
}

function ensureRequiredNodePoolConfiguration() {
  echo "Creating/updating node-pool ${node_pool} ..."
  function createNodePool() { createNewNodePool ${cluster_name} ${zone} ${node_pool} ${machine_type} ${num_nodes} ${num_ssd}; }
  function deleteNodePool() { deleteExistingNodePool ${cluster_name} ${zone} ${node_pool}; }
  function recreateNodePool() { deleteNodePool && createNodePool; }

  if doesNodePoolExist ${cluster_name} ${zone} ${node_pool};
  then

    existing_machine_type=$(getMachineTypeInNodePool ${cluster_name} ${node_pool} ${zone})
    num_existing_localssd_per_node=$(getNumLocalSSDsPerNodeInNodePool ${cluster_name} ${node_pool} ${zone})
    num_existing_nodes=$(getNumNodesInNodePool ${cluster_name} ${node_pool} ${zone})

    if [ "${existing_machine_type}" != "${machine_type}" ]; then
      echo "cluster "${node_pool}" exists, but machine-type differs, so deleting and re-creating the node-pool."
      recreateNodePool
    elif [ ${num_existing_nodes} -ne ${num_nodes} ]; then
      echo "cluster "${node_pool}" exists, but number of nodes differs, so resizing the node-pool."
      resizeExistingNodePool ${cluster_name} ${zone} ${node_pool} ${num_nodes}
    elif [ ${num_existing_localssd_per_node} -ne ${num_ssd} ]; then
      echo "cluster "${node_pool}" exists, but number of SSDs differs, so deleting and re-creating the node-pool"
      recreateNodePool
    else
      echo "cluster "${node_pool}" already exists"
    fi
  else
    createNodePool
  fi
}

function enableManagedCsiDriver() {
  printf "\nEnabling csi add-on ...\n\n"
  gcloud -q container clusters update ${cluster_name} \
    --project=${project_id} \
    --update-addons GcsFuseCsiDriver=ENABLED \
    --location=${zone}
}

function activateCluster() {
  printf "\nConfiguring cluster credentials ...\n\n"
  gcloud container clusters get-credentials ${cluster_name} --project=${project_id} --location=${zone}
  kubectl config current-context
}

function createKubernetesServiceAccountForCluster() {
  printf "\nCreating namespace and KSA ...\n\n"
  log="$(kubectl create namespace ${appnamespace} 2>&1)" || [[ "$log" == *"already exists"* ]]
  log="$(kubectl create serviceaccount ${ksa} --namespace ${appnamespace} 2>&1)" || [[ "$log" == *"already exists"* ]]
  kubectl config set-context --current --namespace=${appnamespace}
  # Validate it
  kubectl config view --minify | grep namespace:
}

function setup_cluster() {
  ensureGcpAuthsAndConfig
  validateMachineConfig ${machine_type} ${num_nodes} ${num_ssd}
  ensureGkeCluster
  # ensureRequiredNodePoolConfiguration
  enableManagedCsiDriver
  activateCluster
  createKubernetesServiceAccountForCluster
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  initialize_environment
  setup_cluster
fi

