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

if [ -n "$_RUN_AND_PARSE_WORKLOADS_SH_SOURCED" ]; then
  return
fi
export _RUN_AND_PARSE_WORKLOADS_SH_SOURCED=1

SCRIPT_DIR=$(cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd)
source "${SCRIPT_DIR}/environment.sh" "${@}"
source "${SCRIPT_DIR}/install_dependencies.sh" "${@}"
source "${SCRIPT_DIR}/setup_cluster.sh" "${@}"
source "${SCRIPT_DIR}/build_custom_csi_driver.sh" "${@}"

function deleteAllHelmCharts() {
  printf "Deleting all existing helm charts ...\n\n"
  helm ls --namespace=${appnamespace} | tr -s '\t' ' ' | cut -d' ' -f1 | tail -n +2 | while read helmchart; do helm uninstall ${helmchart} --namespace=${appnamespace}; done
}

function deleteAllPods() {
  deleteAllHelmCharts

  printf "Deleting all existing pods ...\n\n"
  kubectl get pods --namespace=${appnamespace}  | tail -n +2 | cut -d' ' -f1 | while read podname; do kubectl delete pods/${podname} --namespace=${appnamespace} --grace-period=0 --force || true; done
}

function deployAllFioHelmCharts() {
  printf "\nDeploying all fio helm charts ...\n\n"
  cd "${gke_testing_dir}"/examples/fio
  python3 ./run_tests.py --workload-config "${workload_config}" --experiment-id ${experiment_id} --machine-type="${machine_type}" --project-id=${project_id} --project-number=${project_number} --namespace=${appnamespace} --ksa=${ksa} --custom-csi-driver=${applied_custom_csi_driver}
  cd - >/dev/null
}

function deployAllDlioHelmCharts() {
  printf "\nDeploying all dlio helm charts ...\n\n"
  cd "${gke_testing_dir}"/examples/dlio
  python3 ./run_tests.py --workload-config "${workload_config}" --experiment-id ${experiment_id} --machine-type="${machine_type}" --project-id=${project_id} --project-number=${project_number} --namespace=${appnamespace} --ksa=${ksa} --custom-csi-driver=${applied_custom_csi_driver}

  cd - >/dev/null
}

function waitTillAllPodsComplete() {
  start_epoch=$(date +%s)
  printf "\nScanning and waiting till all pods either complete/fail, or time out (start-time epoch = ${start_epoch} seconds, timeout duration = ${pod_timeout_in_seconds} seconds) ...\n\n"
  while true; do
    cur_epoch=$(date +%s)
    time_till_timeout=$((start_epoch+pod_timeout_in_seconds-cur_epoch))
    if [[ ${time_till_timeout} -lt 0 ]]; then
      echoerror printf "\nPod-run timed out!\n\n"
      printf "Clearing all pods created in this run...\n"
      deleteAllPods
      exitWithFailure
    fi
    printf "Checking pods status at ${cur_epoch} seconds:\n"
    printf " -----------------------------------------\n"
    podslist="$(kubectl get pods --namespace=${appnamespace} -o wide)"
    echo "${podslist}"
    num_completed_pods=$(echo "${podslist}" | tail -n +2 | egrep -i 'completed|succeeded' | wc -l)
    if [ ${num_completed_pods} -gt 0 ]; then
      printf ${num_completed_pods}" pod(s) have completed.\n"
    fi
    num_noncompleted_pods=$(echo "${podslist}" | tail -n +2 | egrep -i -v 'completed|succeeded|fail|error|unknown|oomkilled' | wc -l)
    num_failed_pods=$(echo "${podslist}" | tail -n +2 | egrep -i 'failed|oomkilled|error|unknown' | wc -l)
    if [ ${num_failed_pods} -gt 0 ]; then
      printf ${num_failed_pods}" pod(s) have failed.\n\n"
    fi
    num_unknown_pods=$(echo "${podslist}" | tail -n +2 | egrep -i 'unknown' | wc -l)
    if [ ${num_unknown_pods} -gt 0 ]; then
      printf ${num_unknown_pods}" pod(s) have status 'Unknown'.\n\n"
    fi
    if [ ${num_noncompleted_pods} -eq 0 ]; then
      printf "\nAll pods have completed.\n\n"
      break
    else
      message="\n${num_noncompleted_pods} pod(s) is/are still pending/running (time till timeout=${time_till_timeout} seconds). Will check again in "${pod_wait_time_in_seconds}" seconds. Sleeping for now.\n\n"
      message+="You can take a break too if you want. Just kill this run and connect back to it later, for fetching and parsing outputs, using the following command: \n\n"
      message+="   only_parse=true experiment_id=${experiment_id} project_id=${project_id} project_number=${project_number} zone=${zone} machine_type=${machine_type}"
      message+=" use_custom_csi_driver=${use_custom_csi_driver}"
      if test -n "${custom_csi_driver}"; then
        message+=" custom_csi_driver=${custom_csi_driver}"
      fi
      message+=" gcsfuse_tools_src_dir=\"${gcsfuse_tools_src_dir}\" "
      if test -n "${gcsfuse_src_dir}"; then
        message+=" gcsfuse_src_dir=\"${gcsfuse_src_dir}\" "
      fi
      if test -d "${csi_src_dir}"; then
        message+="csi_src_dir=\"${csi_src_dir}\" "
      fi
      message+=" zonal=${zonal} "
      message+="pod_wait_time_in_seconds=${pod_wait_time_in_seconds} pod_timeout_in_seconds=${pod_timeout_in_seconds} workload_config=\" ${workload_config}\" cluster_name=${cluster_name} output_dir=\" ${output_dir}\" $0 \n"
      message+="\nbut remember that this will reset the start-timer for pod timeout.\n\n"
      message+="\nTo ssh to any specific pod, use the following command: \n"
      message+="  gcloud container clusters get-credentials ${cluster_name} --location=${zone}\n"
      message+="  kubectl config set-context --current --namespace=${appnamespace}\n"
      message+="  kubectl exec -it pods/<podname> [-c {gke-gcsfuse-sidecar|fio-tester|dlio-tester}] --namespace=${appnamespace} -- /bin/bash \n"
      message+="\nTo view cpu/memory usage of different pods/containers: \n"
      message+="  kubectl top pod [<podname>] --namespace=${appnamespace} [--containers] \n"
      message+="\nTo view the latest status of all the pods in this cluster/namespace: \n"
      message+="  kubectl get pods --namespace=${appnamespace} [-o wide] [--watch] \n"
      message+="\nTo output the configuration of all or one of the pods in this cluster/namespace (useful for debugging): \n"
      message+="  kubectl get [pods or pods/<podname>] --namespace=${appnamespace} -o yaml \n"
      printf "${message}\n\n\n"
    fi
    sleep ${pod_wait_time_in_seconds}
    unset podslist # necessary to update the value of podslist every iteration
  done
}

# Download all the fio workload outputs for the current experiment-id from the
# given bucket and file-size.
function downloadFioOutputsFromBucket() {
  local bucket=$1
  local mountpath=$2/${bucket}-mount

  mkdir -p ${mountpath}
  fusermount -uz ${mountpath} 2>/dev/null || true
  echo "Searching for FIO outputs for experiment ${experiment_id} in gs://${bucket} ..."

  cd $gcsfuse_tools_src_dir
  if ! go run $gcsfuse_tools_src_dir --implicit-dirs --o ro $bucket $mountpath > /dev/null ; then
    # If fails to mount this bucket,
    # Return to original directory before exiting..
    cd - >/dev/null

    exitWithError "Failed to mount bucket ${bucket} to ${mountpath}".
  fi

  # Return to original directory.
  cd - >/dev/null

  # If the given bucket has the fio outputs for the given experiment-id, then
  # copy/download them locally to the appropriate folder.
  src_dir="${mountpath}/fio-output/${experiment_id}"
  dst_dir="${gcsfuse_tools_src_dir}/testing_on_gke/bin/fio-logs/${experiment_id}/${bucket}"
  if test -d "${src_dir}" ; then
    mkdir -p "${dst_dir}"
    echo "Copying all files from \"${src_dir}\" to \"${dst_dir}/\" ... "
    cp -rfu "${src_dir}"/* "${dst_dir}"/
  fi

  fusermount -uz "${mountpath}" || true
  rm -rf "${mountpath}"
}

function downloadFioOutputsFromAllBucketsInWorkloadConfig() {
  local mountpath=$(realpath mounted)
  # Using jquery, find out all the relevant buckets for non-disabled fio
  # workloads in the workload-config file and download fio outputs for them all.
  cat ${workload_config} | jq 'select(.TestConfig.workloadConfig.workloads[].fioWorkload != null)' | jq -r '.TestConfig.workloadConfig.workloads[] | [.bucket] | @csv' | grep -v " " | sort | uniq | while read bucket; do
    bucket=$(echo ${bucket} | tr -d \" )
    if [[ "${bucket}" != "" ]]
    then
       downloadFioOutputsFromBucket ${bucket} "${mountpath}"
    fi
  done
  rm -rf ${mountpath}
}

function areThereAnyDLIOWorkloads() {
  lines=$(cat ${workload_config} | jq 'select(.TestConfig.workloadConfig.workloads[].dlioWorkload != null)' | jq -r '.TestConfig.workloadConfig.workloads[] | [.bucket, .dlioWorkload.numFilesTrain, .dlioWorkload.recordLength] | @csv' | grep -v " " | sort | uniq)
  while read bucket_numFilesTrain_recordLength_combo; do
    workload_bucket=$(echo ${bucket_numFilesTrain_recordLength_combo} | cut -d, -f1 | tr -d \" )
    workload_numFileTrain=$(echo ${bucket_numFilesTrain_recordLength_combo} | cut -d, -f2 | tr -d \" )
    workload_recordLength=$(echo ${bucket_numFilesTrain_recordLength_combo} | cut -d, -f3 | tr -d \" )
    if [[ "${workload_bucket}" != "" && "${workload_numFileTrain}" != "" && "${workload_recordLength}" != "" ]]
    then
      return 0
    fi
  done <<< "${lines}" # It's necessary to pass lines this way to while
  # to avoid creating a subshell for while-execution, to
  # ensure that the above return statement works in the same shell.

  return 1
}

function fetchAndParseFioOutputs() {
  printf "\nFetching and parsing fio outputs ...\n\n"
  cd "${gke_testing_dir}"/examples/fio
  parse_logs_args="--project-number=${project_number} --workload-config ${workload_config} --experiment-id ${experiment_id} --output-file ${output_dir}/fio/output.csv --project-id=${project_id} --cluster-name=${cluster_name} --namespace-name=${appnamespace} --bq-project-id=${DEFAULT_BQ_PROJECT_ID} --bq-dataset-id=${DEFAULT_BQ_DATASET_ID} --bq-table-id=${DEFAULT_BQ_TABLE_ID}"
  if ${zonal};
  then
    #  Download fio outputs from all buckets using gcsfuse because zonal buckets don't work with gcloud storage cp.
    printf "\nDownloading all fio outputs using gcsfuse mount as there are zonal buckets involved ...\n\n"
    downloadFioOutputsFromAllBucketsInWorkloadConfig

    python3 parse_logs.py ${parse_logs_args} --predownloaded-output-files
  else
    python3 parse_logs.py ${parse_logs_args}
  fi
  cd - >/dev/null
}

function fetchAndParseDlioOutputs() {
  printf "\nFetching and parsing dlio outputs ...\n\n"
  cd "${gke_testing_dir}"/examples/dlio
  python3 parse_logs.py --project-number=${project_number} --workload-config "${workload_config}" --experiment-id ${experiment_id} --output-file "${output_dir}"/dlio/output.csv --project-id=${project_id} --cluster-name=${cluster_name} --namespace-name=${appnamespace}
  cd - >/dev/null
}

function deploy_workloads() {
  deleteAllPods
  deployAllFioHelmCharts
  deployAllDlioHelmCharts
}

function monitor_and_parse_workloads() {
  # monitor pods
  waitTillAllPodsComplete

  # clean-up after run
  deleteAllPods

  # parse outputs
  fetchAndParseFioOutputs
  fetchAndParseDlioOutputs
}

function run_and_parse_workloads() {
  run_or_die deploy_workloads
  run_or_die monitor_and_parse_workloads
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  run_or_die initialize_environment
  run_or_die installDependencies
  run_or_die setup_cluster
  run_or_die run_and_parse_workloads
fi

