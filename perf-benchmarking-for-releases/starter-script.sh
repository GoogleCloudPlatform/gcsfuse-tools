#!/bin/bash
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Print commands and their arguments as they are executed.
set -x
# Exit immediately if a command exits with a non-zero status.
set -e

# Determine OS family (Debian/Ubuntu or RHEL/CentOS) for package management
OS_FAMILY=""
if grep -qE "Ubuntu|Debian" /etc/os-release; then
    OS_FAMILY="debian_ubuntu"
elif grep -qE "Red Hat|CentOS" /etc/os-release; then
    OS_FAMILY="rhel_centos"
else
    echo "Unsupported OS. Exiting."
    exit 1
fi

# Install common dependencies before adding starterscriptuser
if [[ "$OS_FAMILY" == "debian_ubuntu" ]]; then
    sudo apt-get update > /dev/null 2>&1
    sudo apt-get install -y git libaio1 libaio-dev gcc make mdadm build-essential python3-setuptools python3-crcmod fuse > /dev/null 2>&1
elif [[ "$OS_FAMILY" == "rhel_centos" ]]; then
    sudo yum makecache > /dev/null 2>&1
    sudo yum -y install git fuse libaio libaio-devel gcc make mdadm redhat-rpm-config python3-devel python3-setuptools python3-pip > /dev/null 2>&1
    pip3 install crcmod > /dev/null 2>&1
fi

# Install fio-3.39
git clone -b fio-3.39 https://github.com/axboe/fio.git
cd fio
./configure && sudo make && sudo make install
cd ..

# Add starterscriptuser based on OS type
if ! id "starterscriptuser" &>/dev/null; then
    if [[ "$OS_FAMILY" == "debian_ubuntu" ]]; then
        sudo adduser --ingroup google-sudoers --disabled-password --home=/home/starterscriptuser --gecos "" starterscriptuser
    elif [[ "$OS_FAMILY" == "rhel_centos" ]]; then
        sudo adduser -g google-sudoers --home-dir=/home/starterscriptuser starterscriptuser
    fi
fi

# Run the rest of the benchmark setup and execution as starterscriptuser
sudo -u starterscriptuser OS_FAMILY="$OS_FAMILY" bash <<'EOF'
set -x
set -e

BENCHMARK_LOG_FILE="/tmp/benchmark_run.log"
# Redirect stdout and stderr to BENCHMARK_LOG_FILE and also to original stdout/stderr
exec > >(tee -a "$BENCHMARK_LOG_FILE") 2>&1

cleanup() {
    # Unmount GCSFuse mount point
    if mount | grep -q "$MNT"; then
        sudo umount "$MNT" || echo "Failed to unmount $MNT"
    fi
}
trap cleanup EXIT

echo "Timestamp: $(date)"

cd ~/ # Ensure we are in the starterscriptuser's home directory
echo "Current directory: $(pwd)"
echo "User: $(whoami)"

# Fetch metadata parameters
BS=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/BS" -H "Metadata-Flavor: Google")
FILESIZE=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/FILESIZE" -H "Metadata-Flavor: Google")
NRFILES=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/NRFILES" -H "Metadata-Flavor: Google")
NUM_JOB=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/NUM_JOB" -H "Metadata-Flavor: Google")
BENCH_TYPE=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/BENCH_TYPE" -H "Metadata-Flavor: Google")
GCSFUSE_VERSION=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/GCSFUSE_VERSION" -H "Metadata-Flavor: Google")
MACHINE_TYPE=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/MACHINE_TYPE" -H "Metadata-Flavor: Google")
GCS_BUCKET_WITH_FIO_TEST_DATA=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/GCS_BUCKET_WITH_FIO_TEST_DATA" -H "Metadata-Flavor: Google")
RESULTS_BUCKET_NAME=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/RESULTS_BUCKET_NAME" -H "Metadata-Flavor: Google")

# Determine system architecture
ARCHITECTURE=""
if [[ "$OS_FAMILY" == "debian_ubuntu" ]]; then
    ARCHITECTURE=$(dpkg --print-architecture)
elif [[ "$OS_FAMILY" == "rhel_centos" ]]; then
    uname_arch=$(uname -i)
    if [[ "$uname_arch" == "x86_64" ]]; then
        ARCHITECTURE="amd64"
    elif [[ "$uname_arch" == "aarch64" ]]; then
        ARCHITECTURE="arm64"
    else
        echo "Unsupported architecture: $uname_arch. Exiting."
        exit 1
    fi
fi

# Install Go
wget -nv --tries=3 --waitretry=5 -O go_tar.tar.gz "https://go.dev/dl/go1.24.0.linux-${ARCHITECTURE}.tar.gz"
sudo tar -C /usr/local -xzf go_tar.tar.gz
export PATH=$PATH:/usr/local/go/bin

# Clone and build gcsfuse
git clone https://github.com/GoogleCloudPlatform/gcsfuse.git
cd gcsfuse
git checkout "$GCSFUSE_VERSION"
go build .
cd ..

MOUNT_POINT="mount-point"
CURR_DIR=$(pwd)
GCSFUSE_BIN="$CURR_DIR/gcsfuse/gcsfuse"
MNT="$CURR_DIR/$MOUNT_POINT"
SSD_MOUNT_DIR="/mnt/disks/local_ssd"
FIO_JOB_DIR="/tmp/fio_jobs"

# Download all FIO job spec files
mkdir -p "$FIO_JOB_DIR"
gcloud storage cp "gs://$RESULTS_BUCKET_NAME/$GCSFUSE_VERSION/$MACHINE_TYPE/fio-job-files/*.fio" "$FIO_JOB_DIR/"


# Mount dir for gcsfuse
mkdir -p "$MNT"
echo "Mounting GCSFuse now..."
"$GCSFUSE_BIN" --implicit-dirs "$GCS_BUCKET_WITH_FIO_TEST_DATA" "$MNT"

RESULT_FILE="${BENCH_TYPE}-benchmark_res_${NUM_JOB}_${NRFILES}.json"
DIR="$MNT" NUM_JOB="$NUM_JOB" BS="$BS" FILESIZE="$FILESIZE" NRFILES="$NRFILES" fio "${FIO_JOB_DIR}/${BENCH_TYPE}-workload.fio" --output-format=json --output="$RESULT_FILE"
RESULT_PATH="gs://$RESULTS_BUCKET_NAME/$GCSFUSE_VERSION/results/$MACHINE_TYPE/$BENCH_TYPE/$FILESIZE/$BS"
gcloud storage rm "$RESULT_PATH/$RESULT_FILE" || true
gcloud storage cp "$RESULT_FILE" "${RESULT_PATH}/"
        
rm -f "$RESULT_FILE" # Clean up local files
# Unmount GCSFuse
echo "Unmounting GCSFuse"
sudo umount "$MNT"

EOF

echo "Starter script finished execution on VM."

# The trap command will handle the cleanup on script exit.
