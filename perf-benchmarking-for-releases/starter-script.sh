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

# This script runs on a GCE VM to perform GCSFuse performance benchmarks.
# It installs necessary dependencies, builds GCSFuse, configures local SSDs (if enabled),
# runs FIO tests, monitors GCSFuse resource usage, and uploads results to GCS and BigQuery.

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
    sudo apt-get install -y wget git fio libaio-dev gcc make mdadm build-essential python3-setuptools python3-crcmod python3-pip python3-venv fuse jq bc procps gawk
elif [[ "$OS_FAMILY" == "rhel_centos" ]]; then
    sudo yum makecache
    sudo yum -y install git fio fuse libaio libaio-devel gcc make mdadm redhat-rpm-config python3-devel python3-setuptools python3-pip jq bc procps-ng wget gawk
    pip3 install crcmod
fi

if ! getent group google-sudoers > /dev/null; then
    sudo groupadd google-sudoers
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

UPLOAD_FAILED=false

# Function to monitor GCSFuse CPU and memory usage
monitor_gcsfuse_usage() {
  local log_file="$1"
  local gcsfuse_bin_path="$2"
  local interval=1 # Monitoring interval in seconds
  > "$log_file" # Clear previous log content

  # Find the PID of the GCSFuse process using its binary path
  local gcsfuse_pid=$(pgrep -f "$gcsfuse_bin_path" | head -n 1)
  if [ -z "$gcsfuse_pid" ]; then
    echo "GCS FUSE process not found using path '$gcsfuse_bin_path'. Cannot monitor."
    return 1
  fi
  echo "Monitoring gcsfuse process with PID: $gcsfuse_pid"

  # Get initial CPU stats for the entire system and the GCSFuse process
  local stat_before=($(grep '^cpu ' /proc/stat))
  local pstat_before=($(cat /proc/$gcsfuse_pid/stat))
  local total_before=$((${stat_before[1]} + ${stat_before[2]} + ${stat_before[3]} + ${stat_before[4]} + ${stat_before[5]} + ${stat_before[6]} + ${stat_before[7]}))
  local proc_total_before=$((${pstat_before[13]} + ${pstat_before[14]}))

  # Loop to continuously monitor until the GCSFuse process is no longer running
  while ps -p "$gcsfuse_pid" > /dev/null; do
    sleep "$interval" # Wait for the specified interval
    
    local timestamp=$(date +%s) # Current timestamp (epoch seconds)
    
    # Get final CPU stats for the entire system and the GCSFuse process
    local stat_after=($(grep '^cpu ' /proc/stat))
    # Exit loop if the process stat file no longer exists (process terminated)
    if [ ! -f /proc/$gcsfuse_pid/stat ]; then
      break
    fi
    local pstat_after=($(cat /proc/$gcsfuse_pid/stat))
    local total_after=$((${stat_after[1]} + ${stat_after[2]} + ${stat_after[3]} + ${stat_after[4]} + ${stat_after[5]} + ${stat_after[6]} + ${stat_after[7]}))
    local proc_total_after=$((${pstat_after[13]} + ${pstat_after[14]}))

    # Calculate CPU usage based on deltas
    local total_delta=$((total_after - total_before))
    local proc_total_delta=$((proc_total_after - proc_total_before))
    
    # Calculate CPU Usage as a normalized percentage of total system capacity
    local cpu_usage="0.00"
    if [ "$total_delta" -ne 0 ]; then
      cpu_usage=$(gawk -v proc_delta="$proc_total_delta" -v total_delta="$total_delta" 'BEGIN { printf "%.2f", 100 * proc_delta / total_delta }')
    fi
    
    # Get Memory usage (VmRSS - Resident Set Size) for the specific PID in MiB
    local mem_used_kb=$(grep 'VmRSS:' /proc/$gcsfuse_pid/status | gawk '{print $2}')
    local mem_used_mb=0
    if [ -n "$mem_used_kb" ]; then
        mem_used_mb=$((mem_used_kb / 1024))
    fi

    echo "$timestamp $cpu_usage $mem_used_mb" >> "$log_file"

    total_before=$total_after
    proc_total_before=$proc_total_after
  done
}

BENCHMARK_LOG_FILE="/tmp/benchmark_run.log"
# Redirect stdout and stderr to BENCHMARK_LOG_FILE and also to original stdout/stderr
exec > >(tee -a "$BENCHMARK_LOG_FILE") 2>&1

cleanup() {
    # If the monitor process is running, kill it
    if [ ! -z "$monitor_pid" ] && ps -p "$monitor_pid" > /dev/null; then
       kill "$monitor_pid"
    fi
    if [[ -f details.txt ]]; then
        gcloud storage cp details.txt "$RESULT_PATH" || echo "Failed to upload details.txt"
    fi
    if [[ -f "$BENCHMARK_LOG_FILE" ]]; then
        gcloud storage cp "$BENCHMARK_LOG_FILE" "$RESULT_PATH" || echo "Failed to upload benchmark log"
    fi
}
trap cleanup EXIT

echo "Timestamp: $(date)"

cd ~/
echo "Current directory: $(pwd)"
echo "User: $(whoami)"

# Fetch metadata parameters
GCSFUSE_VERSION=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/GCSFUSE_VERSION" -H "Metadata-Flavor: Google")
GCS_BUCKET_WITH_FIO_TEST_DATA=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/GCS_BUCKET_WITH_FIO_TEST_DATA" -H "Metadata-Flavor: Google")
RESULT_PATH=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/RESULT_PATH" -H "Metadata-Flavor: Google")
LSSD_ENABLED=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/LSSD_ENABLED" -H "Metadata-Flavor: Google")
PROJECT_ID=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/PROJECT_ID" -H "Metadata-Flavor: Google")
VM_NAME=$(hostname)
UNIQUE_ID=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/UNIQUE_ID" -H "Metadata-Flavor: Google")
GCSFUSE_MOUNT_OPTIONS_STR="implicit-dirs"

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
SSD_MOUNT_DIR="/mnt/lssd"
FIO_JOB_DIR="/tmp/fio_jobs"

# Download all FIO job spec files
mkdir -p "$FIO_JOB_DIR"
gcloud storage cp "${RESULT_PATH}/fio-job-files/*.fio" "$FIO_JOB_DIR/"

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

git clone https://github.com/GoogleCloudPlatform/gcsfuse-tools.git
cd gcsfuse-tools

python3 -m venv py_venv
source py_venv/bin/activate
python3 -m pip install -r perf-benchmarking-for-releases/requirements.txt

IFS=',' read -r -a GCSFUSE_FLAGS_ARRAY <<< "$GCSFUSE_MOUNT_OPTIONS_STR"
GCSFUSE_FLAGS=()
for flag in "${GCSFUSE_FLAGS_ARRAY[@]}"; do
    GCSFUSE_FLAGS+=("--$flag")
done

for master_fio_file in "$FIO_JOB_DIR"/*.fio; do
    echo "Processing master FIO file: $master_fio_file"

    master_basename=$(basename "$master_fio_file" .fio)
    RESULTS_SUBDIR_PATH="${RESULT_PATH}/fio_results/${master_basename}"
    SPLIT_DIR=$(mktemp -d)
    
    # Gawk script to split a single FIO master job file (potentially containing multiple job definitions)
    # into individual FIO job files. Each split file gets the global section prepended.
    gawk -v split_dir="$SPLIT_DIR" '
    /^\[global\]/ { in_global=1; global_section=""; next }
    in_global && /^\[/ && NR > 1 { in_global=0 }
    in_global { global_section = global_section $0 "\n"; next }

    /^\[/ && !in_global {
        jobname = substr($0, 2, length($0)-2)
        sanitized_jobname = gensub(/[^a-zA-Z0-9_-]/, "_", "g", jobname)
        outfile = split_dir "/" sanitized_jobname ".fio"
        print "[global]\n" global_section > outfile
        print $0 >> outfile  # Include job section header
        next
    }

    !in_global {
        print >> outfile
    }
    ' "$master_fio_file"

    for single_fio_file in "$SPLIT_DIR"/*.fio; do
        job_file_basename=$(basename "$single_fio_file" .fio)
        echo "--- Running single job: $job_file_basename ---"

        [[ "$LSSD_ENABLED" == "true" ]] && reformat_and_remount_lssd
        sudo sh -c "echo 3 > /proc/sys/vm/drop_caches"

        # Mount GCS bucket using gcsfuse
        mkdir -p "$MNT"
        "$GCSFUSE_BIN" "${GCSFUSE_FLAGS[@]}" "$GCS_BUCKET_WITH_FIO_TEST_DATA" "$MNT"
        
        monitor_log="/tmp/monitor_${job_file_basename}.log" # Log file for GCSFuse monitoring
        
        # Start GCSFuse CPU and memory monitoring in the background
        monitor_gcsfuse_usage "$monitor_log" "$GCSFUSE_BIN" &
        monitor_pid=$!

        RESULT_FILE="/tmp/gcsfuse-${job_file_basename}-benchmark.json"

        DIR="$MNT" fio "$single_fio_file" \
          --output-format=json 2>&1 \
          | sed -n '/^{/,$p' > "$RESULT_FILE"

        kill "$monitor_pid"
        wait "$monitor_pid" 2>/dev/null || true

        gcloud storage cp "$RESULT_FILE" "${RESULTS_SUBDIR_PATH}/"
        if mount | grep -q "$MNT"; then
            sudo umount "$MNT" || echo "Failed to unmount $MNT"
        fi

        read -r LOWEST_CPU HIGHEST_CPU <<< $(gawk 'BEGIN{min="inf";max="-inf"} {if($2<min)min=$2; if($2>max)max=$2} END{if(min=="inf")print "0.0 0.0"; else print min, max}' "$monitor_log")
        read -r LOWEST_MEM HIGHEST_MEM <<< $(gawk 'BEGIN{min="inf";max="-inf"} {if($3<min)min=$3; if($3>max)max=$3} END{if(min=="inf")print "0 0"; else print min, max}' "$monitor_log")

        if python3 perf-benchmarking-for-releases/upload_fio_output_to_bigquery.py \
          --result-file "$RESULT_FILE" \
          --fio-job-file "$single_fio_file" \
          --master-fio-file "$master_fio_file" \
          --lowest-cpu "$LOWEST_CPU" \
          --highest-cpu "$HIGHEST_CPU" \
          --lowest-mem "$LOWEST_MEM" \
          --highest-mem "$HIGHEST_MEM" \
          --gcsfuse-mount-options "$GCSFUSE_MOUNT_OPTIONS_STR"; then
            echo "Successfully uploaded results to BigQuery for job: $job_file_basename"
            rm -f "$RESULT_FILE" "$monitor_log"
        else
            echo "Warning: Failed to upload results to BigQuery for job: $job_file_basename. Uploading monitor log to GCS for debugging."
            gcloud storage cp "$monitor_log" "${RESULTS_SUBDIR_PATH}/" || echo "Warning: Failed to upload monitor log for ${job_file_basename}"
            UPLOAD_FAILED=true
        fi
    done

    rm -rf "$SPLIT_DIR"
done

cd ..

if [[ "$UPLOAD_FAILED" == "false" ]]; then
    # All tests ran successfully; create a success.txt file in GCS
    touch success.txt
    gcloud storage cp success.txt "$RESULT_PATH"
    rm success.txt
else
    echo "One or more BigQuery uploads failed. Not creating success.txt to indicate benchmark failure."
fi

EOF

echo "Starter script finished execution on VM."

# The trap command will handle the cleanup on script exit.
