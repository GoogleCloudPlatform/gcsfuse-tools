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
    sudo apt-get update
    sudo apt-get install -y git fio libaio1 libaio-dev gcc make mdadm build-essential python3-setuptools python3-crcmod fuse
elif [[ "$OS_FAMILY" == "rhel_centos" ]]; then
    sudo yum makecache
    sudo yum -y install git fio fuse libaio libaio-devel gcc make mdadm redhat-rpm-config python3-devel python3-setuptools python3-pip
    pip3 install crcmod
fi

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

    # Upload logs and details
    if [[ -f details.txt ]]; then
        gcloud storage cp details.txt "$RESULT_PATH" || echo "Failed to upload details.txt"
    fi
    if [[ -f "$BENCHMARK_LOG_FILE" ]]; then
        gcloud storage cp "$BENCHMARK_LOG_FILE" "$RESULT_PATH" || echo "Failed to upload benchmark log"
    fi
}
trap cleanup EXIT

echo "Timestamp: $(date)"

cd ~/ # Ensure we are in the starterscriptuser's home directory
echo "Current directory: $(pwd)"
echo "User: $(whoami)"

# Fetch metadata parameters
GCSFUSE_VERSION=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/GCSFUSE_VERSION" -H "Metadata-Flavor: Google")
MACHINE_TYPE=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/MACHINE_TYPE" -H "Metadata-Flavor: Google")
GCS_BUCKET_WITH_FIO_TEST_DATA=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/GCS_BUCKET_WITH_FIO_TEST_DATA" -H "Metadata-Flavor: Google")
RESULTS_BUCKET_NAME=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/RESULTS_BUCKET_NAME" -H "Metadata-Flavor: Google")
LSSD_ENABLED=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/LSSD_ENABLED" -H "Metadata-Flavor: Google")

# Add OS Family to details.txt
echo "OS Family: $OS_FAMILY" >> details.txt

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
gcloud storage cp "gs://$RESULTS_BUCKET_NAME/$GCSFUSE_VERSION/fio-job-files/*.fio" "$FIO_JOB_DIR/"

RESULT_PATH="gs://$RESULTS_BUCKET_NAME/$GCSFUSE_VERSION/$MACHINE_TYPE/"

# Capture versions
{
    echo "GCSFuse version: $GCSFUSE_VERSION"
    echo "Go version     : $(go version)"
    echo "FIO version    : $(fio --version)"
} >> details.txt

# Create LSSD if enabled
if [[ "$LSSD_ENABLED" == "true" ]]; then
    LSSD_DEVICES=()
    for i in {0..15}; do
        DEVICE_PATH="/dev/disk/by-id/google-local-nvme-ssd-$i"
        LSSD_DEVICES+=("$DEVICE_PATH")
    done

    sudo mdadm --create /dev/md0 --level=0 --raid-devices=16 "${LSSD_DEVICES[@]}"
    sudo mdadm --detail --prefer=by-id /dev/md0 || true
    sudo mkfs.ext4 -F /dev/md0
    sudo mkdir -p "$SSD_MOUNT_DIR"
    sudo mount /dev/md0 "$SSD_MOUNT_DIR"
    sudo chmod a+w "$SSD_MOUNT_DIR"

    reformat_and_remount_lssd() {
        sudo umount /dev/md0 || echo "Warning: umount /dev/md0 failed (might not be mounted or busy)."
        sudo mkfs.ext4 -F /dev/md0
        sudo mount /dev/md0 "$SSD_MOUNT_DIR"
        sudo chmod a+w "$SSD_MOUNT_DIR"
    }
fi

# Mount GCS bucket using gcsfuse
mkdir -p "$MNT"
"$GCSFUSE_BIN" --implicit-dirs "$GCS_BUCKET_WITH_FIO_TEST_DATA" "$MNT"

for fio_job_file in "$FIO_JOB_DIR"/*.fio; do
    job_name=$(basename "$fio_job_file" .fio) # e.g., random-read-workload

    [[ "$LSSD_ENABLED" == "true" ]] && reformat_and_remount_lssd

    # Drop Page Cache
    sudo sh -c "echo 3 > /proc/sys/vm/drop_caches"
        
    RESULT_FILE="gcsfuse-${job_name}-benchmark.json"

    DIR="$MNT" fio "$fio_job_file" --output-format=json --output="$RESULT_FILE"

    gcloud storage cp "$RESULT_FILE" "$RESULT_PATH"
        
    rm -f "$RESULT_FILE" # Clean up local files
done

# All tests ran successfully; create a success.txt file in GCS
touch success.txt
gcloud storage cp success.txt "$RESULT_PATH"
rm success.txt

EOF

echo "Starter script finished execution on VM."

# The trap command will handle the cleanup on script exit.
