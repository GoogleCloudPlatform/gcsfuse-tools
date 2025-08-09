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

if [ -n "$_INSTALL_DEPENDENCIES_SH_SOURCED" ]; then
  return
fi
export _INSTALL_DEPENDENCIES_SH_SOURCED=1

SCRIPT_DIR=$(cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd)
source "${SCRIPT_DIR}/environment.sh" "${@}"

# Install dependencies.
function installDependencies() {
  printf "\nInstalling dependencies ...\n\n"
  # Refresh software repositories.
  sudo apt-get update >/dev/null
  # Get some common software dependencies.
  sudo apt-get install -y apt-transport-https ca-certificates gnupg curl >/dev/null
  # Ensure that realpath is installed.
  which realpath >/dev/null
  # Ensure that make is installed.
  which make >/dev/null || ( sudo apt-get install -y make time >/dev/null && which make >/dev/null )
  # Ensure that go is installed.
  which go >/dev/null || (version=1.22.4 && wget -O go_tar.tar.gz https://go.dev/dl/go${version}.linux-amd64.tar.gz 1>/dev/null && sudo rm -rf /usr/local/go && tar -xzf go_tar.tar.gz 1>/dev/null && sudo mv go /usr/local && echo $PATH && export PATH=$PATH:/usr/local/go/bin && echo $PATH && echo 'export PATH=$PATH:/usr/local/go/bin'>>~/.bashrc && go version)
  # for some reason, the above is unable to update the value of $PATH, so doing it explicitly below.
  export PATH=$PATH:/usr/local/go/bin
  which go >/dev/null
  # Ensure that python3 is installed.
  which python3 >/dev/null || ( sudo apt-get install -y python3 >/dev/null && which python3 >/dev/null )
  # Install more python tools.
  sudo apt-get -y install python3-dev python3-venv python3-pip >/dev/null
  # Enable python virtual environment.
  python3 -m venv .venv >/dev/null
  source .venv/bin/activate >/dev/null
  # Ensure that pip is installed.
  sudo apt-get install -y pip >/dev/null
  # python3 -m pip install --upgrade pip
  # python3 -m pip --version
  # Ensure that python-absl is installed.
  pip install absl-py >/dev/null
  # Ensure that helm is installed
  which helm >/dev/null || (cd "${src_dir}" && (test -d "./helm" || git clone https://github.com/helm/helm.git) && cd helm && make && ls -lh bin/ && mkdir -pv ~/bin && cp -fv bin/helm ~/bin/ && chmod +x ~/bin/helm && export PATH=$PATH:$HOME/bin && echo $PATH && which helm && cd - >/dev/null && cd - >/dev/null)
  # for some reason, the above is unable to update the value of $PATH, so doing it explicitly below.
  export PATH=$PATH:$HOME/bin
  which helm >/dev/null
  # Ensure that kubectl is installed
  if ! which kubectl >/dev/null ;
  then
    # Install the latest gcloud cli. Find full instructions at https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl .
    # Import the Google Cloud public key (Debian 9+ or Ubuntu 18.04+)
    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --yes --dearmor -o /usr/share/keyrings/cloud.google.gpg
    # Add the gcloud CLI distribution URI as a package source (Debian 9+ or Ubuntu 18.04+)
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee -a /etc/apt/sources.list.d/google-cloud-sdk.list
    # Update and install the gcloud CLI
    sudo apt-get update >/dev/null
    sudo apt-get install -y google-cloud-cli >/dev/null
    # install kubectl
    gcloud components install kubectl >/dev/null || sudo apt-get install -y kubectl >/dev/null
    kubectl version --client
  fi
  # Ensure that gke-gcloud-auth-plugin is installed.
  gke-gcloud-auth-plugin --version || ((gcloud components install gke-gcloud-auth-plugin >/dev/null || sudo apt-get install -y google-cloud-cli-gke-gcloud-auth-plugin >/dev/null) && gke-gcloud-auth-plugin --version)
  # Ensure that docker is installed.
  if ! which docker >/dev/null ;
  then
    sudo apt install apt-transport-https ca-certificates curl software-properties-common -y >/dev/null
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
    sudo add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu focal stable"
    apt-cache policy docker-ce
    sudo apt install docker-ce -y >/dev/null
  fi
  # Ensure that gcloud monitoring tools are installed.
  pip install --upgrade google-cloud-storage 1>/dev/null
  pip install --ignore-installed --upgrade google-api-python-client 1>/dev/null
  pip install --ignore-installed --upgrade google-cloud 1>/dev/null
  pip install --upgrade google-cloud-monitoring 1>/dev/null
  # Ensure that jq is installed.
  which jq >/dev/null || sudo apt-get install -y jq >/dev/null
  # Ensure sudoless docker is installed.
  if ! docker ps 1>/dev/null ;
  then
    echoerror "sudoless docker is not installed on this machine ($(hostname)). Please install sudoless-docker using the following commands and re-run this script ($0)"
    echoerror "sudo addgroup docker && sudo usermod -aG docker $USER && newgrp docker"
    return 1
  fi
  # Install python client for bigquery.
  # TODO: Make this conditional on bigquery export !
  pip3 install --upgrade google-cloud-bigquery >/dev/null
  pip3 install --upgrade google-cloud-storage >/dev/null
  pip install google-api-python-client >/dev/null
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  initialize_environment
  installDependencies
fi

