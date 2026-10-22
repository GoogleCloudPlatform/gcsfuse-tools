#!/bin/bash
set -e
set -x
echo "Fetching metadata..."

# Fetch the metadata values from the metadata server
bucket=$(curl -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/bucket)
artifacts_bucket=$(curl -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/artifacts_bucket)
benchmark_id=$(curl -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/benchmark_id)
iterations=$(curl -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/iterations)
reuse_same_mount=$(curl -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/reuse_same_mount)

echo "The value of 'bucket' is: $bucket"
echo "The value of 'artifacts_bucket' is: $artifacts_bucket"
echo "The value of 'benchmark_id' is: $benchmark_id"

# Global OS/Arch detection
OS_KERNEL=$(uname -s)
ARCH_MACHINE=$(uname -m)
OS_LOWER=""
ARCH_GO=""

case "$OS_KERNEL" in
    Linux*)  OS_LOWER="linux";;
    MINGW*|MSYS*) OS_LOWER="windows";;
    *) echo "Unsupported OS: $OS_KERNEL"; exit 1;;
esac

case "$ARCH_MACHINE" in
    x86_64)  ARCH_GO="amd64";;
    aarch64|arm64) ARCH_GO="arm64";;
    *) echo "Unsupported architecture: $ARCH_MACHINE"; exit 1;;
esac

# A helper function to check if a command exists
command_exists () {
  command -v "$1" >/dev/null 2>&1
}

# Platform-agnostic function to install yq by downloading from GitHub
install_yq() {
    echo "Installing yq from GitHub releases..."
    local install_path="/usr/local/bin/yq"
    local filename="yq_${OS_LOWER}_${ARCH_GO}"

    if [[ "$OS_LOWER" == "windows" ]]; then
        install_path="/usr/bin/yq"
        filename="${filename}.exe"
    fi

    # Ensure the target directory exists
    sudo mkdir -p "$(dirname "${install_path}")"
    local download_url="https://github.com/mikefarah/yq/releases/latest/download/$filename"

    echo "Attempting to remove any existing yq at ${install_path}..."
    sudo rm -f "${install_path}"

    echo "Downloading yq from ${download_url}..."
    if ! sudo curl -L -o "${install_path}" "${download_url}"; then
        echo "Error: Failed to download yq."
        exit 1
    fi
    sudo chmod +x "${install_path}"

    echo "Verifying yq installation at ${install_path}..."
    if [[ ! -x "${install_path}" ]]; then
        echo "Error: yq not found or not executable at ${install_path}."
        exit 1
    fi
    "${install_path}" --version
    echo "yq installed successfully to ${install_path}."
}

install_dependencies() {
    echo "Detected operating system: $OS_KERNEL"
    case "$OS_KERNEL" in
      "Linux")
        echo "Installing dependencies for Linux..."
        if command_exists apt-get; then
          sudo apt-get update -y
          sudo apt-get install wget libaio-dev gcc g++ make git fuse -y
        elif command_exists dnf; then
          sudo dnf install wget libaio-devel gcc-c++ make git fuse -y
        elif command_exists yum; then
          sudo yum install wget libaio-devel gcc-c++ make git fuse -y
        else
          echo "Could not find a supported package manager."
          exit 1
        fi
        ;;

      "MINGW"*|"MSYS_NT"*)
        echo "Installing dependencies for Windows..."
        if ! command_exists choco; then
          echo "Chocolatey not found. Please install it from https://chocolatey.org and re-run the script."
          exit 1
        fi
        choco install git make wget -y
        echo "choco packages installed."
        ;;

      *)
        echo "Unsupported operating system: $OS_KERNEL."
        exit 1
        ;;
    esac
    if ! command_exists wget; then
        echo "Error: wget installation failed or was skipped."
        exit 1
    fi
    echo "Dependency installation complete."
}

copy_resources_from_artifact_bucket(){
    local artifacts_bucket=$1
    local benchmark_id=$2
    local dir=$3
    gcloud storage cp gs://$artifacts_bucket/$benchmark_id/* $dir/
}

copy_raw_results_to_artifacts_bucket(){
    local artifacts_bucket=$1
    local benchmark_id=$2
    local dir=$3
    gcloud storage cp --recursive $dir/raw-results/* gs://$artifacts_bucket/$benchmark_id/raw-results/
}

# Install fio on the VM.
install_fio_on_vm() {
    local fio_install_path="/usr/local/bin/fio"
    if [[ -x "$fio_install_path" ]]; then
        echo "FIO is already installed."
        return 0
    fi
    local dir=$1
    local fio_version=$(/usr/local/bin/yq e '.fio_version' "${dir}/version_details.yml")
    if [ -z "$fio_version" ]; then echo "Error: fio_version not found."; return 1; fi

    echo "Preparing to install fio version: ${fio_version}"
    (
        rm -rf fio
        git clone --depth 1 -b "fio-${fio_version}" https://github.com/axboe/fio.git || exit 1
        cd fio || exit 1
        ./configure || exit 1
        make -j"$(nproc)" || exit 1
        sudo make install || exit 1
    )

    if [[ -x "$fio_install_path" ]]; then
            echo "fio installed successfully to $fio_install_path"
        "$fio_install_path" --version
        return 0
    else
        echo "Fio installation process failed."
        return 1
    fi
}

# Install golang on the VM.
install_golang_on_vm() {
    if command_exists go; then
        echo "go is already installed."
        return 0
    fi
    local dir=$1
    local go_version=$(/usr/local/bin/yq e '.go_version' "${dir}/version_details.yml")
    if [ -z "$go_version" ]; then echo "Error: go_version not found."; return 1; fi

    echo "Installing Go version: ${go_version}"
    if [[ "$OS_LOWER" == "windows" ]]; then echo "Go install not supported on Windows via script"; return 1; fi

    local go_file="go${go_version}.${OS_LOWER}-${ARCH_GO}.tar.gz"
    local download_url="https://go.dev/dl/${go_file}"
    local download_path="/tmp/${go_file}"

    wget "$download_url" -O "$download_path"
    sudo rm -rf /usr/local/go
    sudo tar -C /usr/local -xzf "$download_path"
    export PATH="$PATH:/usr/local/go/bin"
    rm "$download_path"

    echo "Go installation of version ${go_version} complete."
    go version
}

build_gcsfuse() {
    local dir=$1
    local gcsfuse_install_path="/usr/local/bin/gcsfuse"
    local gcsfuse_version=$(/usr/local/bin/yq e '.gcsfuse_version_or_commit' "${dir}/version_details.yml")
    if [ -z "$gcsfuse_version" ]; then echo "Error: gcsfuse_version not found."; return 1; fi

    echo "Building gcsfuse binary with version: ${gcsfuse_version}"

    # Use a subshell to avoid changing the script's main working directory
    (
        echo "Removing existing gcsfuse directory..."
        rm -rf gcsfuse

        echo "Cloning gcsfuse repository..."
        git clone https://github.com/GoogleCloudPlatform/gcsfuse.git
        cd gcsfuse

        echo "Checking out version/commit: ${gcsfuse_version}"
        git checkout "${gcsfuse_version}"
        go build

        echo "Installing gcsfuse to ${gcsfuse_install_path}"
        sudo cp -f gcsfuse "${gcsfuse_install_path}"
    )

    if [[ -x "${gcsfuse_install_path}" ]]; then
        echo "gcsfuse installed successfully to ${gcsfuse_install_path}"
        "${gcsfuse_install_path}" --version
        return 0
    else
        echo "Error: gcsfuse binary not found or not executable at ${gcsfuse_install_path}."
        return 1
    fi
}

mount_gcsfuse() {
    local mntdir="$1"
    local bucketname="$2"
    local mount_config="$3"
    local gcsfuse_binary="/usr/local/bin/gcsfuse"

    mkdir -p "$mntdir"
    if mountpoint -q "$mntdir"; then echo "Directory '$mntdir' is already a mount point. Skipping."; return 0; fi
    if [ ! -f "$mount_config" ]; then echo "Error: Config not found."; return 1; fi

    echo "Mounting bucket '$bucketname' to '$mntdir' with config '$mount_config'..."
    "${gcsfuse_binary}" --config-file="$mount_config" "$bucketname" "$mntdir"
    if mountpoint -q "$mntdir"; then return 0; else return 1; fi
}

unmount_gcsfuse() {
    local mntdir="$1"
    if ! mountpoint -q "$mntdir"; then return 0; fi
    
    if [[ "$OS_KERNEL" == "Linux" ]]; then
        fusermount -uz "$mntdir"
    else
        echo "Warning: Unsupported OS for unmounting."
        return 1
    fi
    if ! mountpoint -q "$mntdir"; then return 0; else return 1; fi
}

start_benchmarking_runs() {
    dir="$1"
    iterations="$2"
    local fio_binary="/usr/local/bin/fio"

    mkdir -p "${dir}/raw-results/"
    fio_job_cases="${dir}/fio_job_cases.csv"
    fio_job_file="${dir}/jobfile.fio"
    mntdir="${dir}/mntdir/"
    mount_config="${dir}/mount_config.yml"

    if [ ! -f "$fio_job_cases" ] || [ ! -f "$fio_job_file" ] || [ ! -f "$mount_config" ]; then
        echo "Error: Missing configuration files."
        return 1
    fi
    
    while IFS=, read -r bs file_size iodepth iotype threads nrfiles || [[ -n "$nrfiles" ]]; do
        if [[ "$reuse_same_mount" == "true" ]]; then
            mount_gcsfuse "$mntdir" "$bucket" "$mount_config"
        fi
        nrfiles="${nrfiles%$'\r'}"
        echo "Config: ${bs}, ${file_size}, ${iodepth}, ${iotype}, ${threads}, ${nrfiles}"

        testdir="${dir}/raw-results/fio_output_${bs}_${file_size}_${iodepth}_${iotype}_${threads}_${nrfiles}"
        mkdir -p "$testdir"
        echo "iteration,start_time,end_time" > "${testdir}/timestamps.csv"

        for ((i = 1; i <= iterations; i++)); do
            echo "Starting FIO run ${i} of ${iterations} for case: bs=${bs}, file_size=${file_size}, iodepth=${iodepth}, iotype=${iotype}, threads=${threads}, nrfiles=${nrfiles}"
            # If reuse_same_mount is 'false', mount the bucket for this run
            if [[ "$reuse_same_mount" != "true" ]]; then
                mount_gcsfuse "$mntdir" "$bucket" "$mount_config"
            fi
            start_time=$(date -u +"%Y-%m-%dT%H:%M:%S%z")

            output_file="${testdir}/fio_output_iter${i}.json"
            MNTDIR=${mntdir} IODEPTH=${iodepth} IOTYPE=${iotype} BLOCKSIZE=${bs} FILESIZE=${file_size} NRFILES=${nrfiles} NUMJOBS=${threads} FILENAME_FORMAT="${iotype}-\$jobnum/\$filenum" ${fio_binary} $fio_job_file --output-format=json > "$output_file" 2>&1 
            
            echo "${i},${start_time},$(date -u +"%Y-%m-%dT%H:%M:%S%z")" >> "${testdir}/timestamps.csv"

            if [[ "$reuse_same_mount" != "true" ]]; then
                unmount_gcsfuse "$mntdir"
            fi

            echo "Sleeping for 20 seconds to keep VM metrics independent for each iteration...."
            sleep 20
        done
        if [[ "$reuse_same_mount" == "true" ]]; then
            unmount_gcsfuse "$mntdir"
        fi
    done < <(tail -n +2 "$fio_job_cases")
}

# Check if the values were retrieved successfully
if [ -n "$bucket" ] && [ -n "$artifacts_bucket" ] && [ -n "$benchmark_id" ]; then
    dir="$(pwd)"
    install_yq
    install_dependencies
    copy_resources_from_artifact_bucket "$artifacts_bucket" "$benchmark_id" "$dir"
    install_fio_on_vm "$dir"
    install_golang_on_vm "$dir"
    build_gcsfuse "$dir"
    start_benchmarking_runs "$dir" "$iterations"
    copy_raw_results_to_artifacts_bucket "$artifacts_bucket" "$benchmark_id" "$dir"
    touch /tmp/success.txt
    gcloud storage cp /tmp/success.txt gs://$artifacts_bucket/$benchmark_id/success.txt
    exit 0
else
    echo "Error: Failed to retrieve one or more metadata parameters."
fi
touch /tmp/failure.txt
gcloud storage cp /tmp/failure.txt gs://$artifacts_bucket/$benchmark_id/failure.txt
exit 1
