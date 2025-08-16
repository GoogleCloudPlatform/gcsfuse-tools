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

install_dependencies() {
    sudo apt-get update -y
    sudo apt-get install libaio-dev gcc make git fuse -y

    # Download the yq binary for Linux from GitHub
    sudo wget https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -O /usr/bin/yq
    # Make the downloaded file executable
    sudo chmod +x /usr/bin/yq
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

check_if_package_installed() {
    # Check for the 'package' command
    # The output is redirected to /dev/null to keep the console clean.
    local pkg="$1"
    command -v "$pkg" >/dev/null 2>&1
}

# Install fio on the VM.
install_fio_on_vm() {
    if check_if_package_installed "fio"; then
        echo "FIO is already installed on the VM"
        return 0
    fi
    # dir contains the path to the directory with version_details.yml
    local dir=$1

    # Check if yq is installed and install it if it's not.
    if ! command -v yq &> /dev/null; then
        echo "yq not found. Installing yq..."
            return 1
    fi

    echo "Fetching fio_version from ${dir}/version_details.yml"
    # Use yq to parse the YAML file and get the fio_version.
    local fio_version=$(yq e '.fio_version' "${dir}/version_details.yml")

    # Check if the version was successfully retrieved.
    if [ -z "$fio_version" ]; then
        echo "Error: Could not find fio_version in the YAML file."
        return 1
    fi

    echo "Cloning fio version: ${fio_version}"
    
    # Use a subshell (...) to contain the cd commands.
    # This prevents the script's working directory from being changed.
    (
        git clone -b fio-"${fio_version}" https://github.com/axboe/fio.git
        cd fio || { echo "Error: Failed to cd into fio directory."; exit 1; }
        ./configure && sudo make && sudo make install
    )
    
    # Check if the subshell exited successfully
    if [ $? -eq 0 ]; then
        echo "Fio installation complete."
        return 0
    else
        echo "Fio installation failed."
        return 1
    fi
}

# Install golang on the VM.
install_golang_on_vm() {
    if check_if_package_installed "go"; then
        echo "go is already installed on the VM"
        return 0
    fi
    # dir contains the path to the directory with version_details.yml
    local dir=$1
    set -e # Exit immediately if a command exits with a non-zero status.

    # Check if yq is installed and install it if it's not.
    if ! command -v yq &> /dev/null; then
        echo "yq not found. Installing yq..."
            return 1
    fi

    echo "Fetching go_version from ${dir}/version_details.yml"
    local go_version=$(yq e '.go_version' "${dir}/version_details.yml")

    if [ -z "$go_version" ]; then
        echo "Error: Could not find go_version in the YAML file."
        return 1
    fi

    echo "Installing Go version: ${go_version}"

    # --- Automated OS and Architecture Detection ---
    local os=""
    case "$(uname -s)" in
        Linux*)  os=linux;;
        Darwin*) os=darwin;;
        *)       echo "Unsupported OS: $(uname -s)"; return 1;;
    esac

    local arch=""
    case "$(uname -m)" in
        x86_64)  arch=amd64;;
        arm64)   arch=arm64;;
        aarch64) arch=arm64;;
        *)       echo "Unsupported architecture: $(uname -m)"; return 1;;
    esac

    # --- Installation Logic ---
    local go_file="go${go_version}.${os}-${arch}.tar.gz"
    local download_url="https://go.dev/dl/${go_file}"
    local download_path="/tmp/${go_file}"

    echo "Downloading ${download_url}..."
    wget "$download_url" -O "$download_path"

    # Remove any previous Go installation to avoid conflicts
    sudo rm -rf /usr/local/go

    # Extract the new Go tarball to /usr/local
    echo "Extracting Go to /usr/local/..."
    sudo tar -C /usr/local -xzf "$download_path"

    # Set up the environment PATH if it's not already configured
    local go_bin_path='/usr/local/go/bin'
    export PATH="$PATH:$go_bin_path"

    # Clean up the downloaded file
    rm "$download_path"

    echo "Go installation of version ${go_version} complete."
    go version
}

build_gcsfuse() {
    # dir contains the path to the directory with version_details.yml
    local dir=$1
    set -e

    # Check if yq is installed and install it if it's not.
    if ! command -v yq &> /dev/null; then
        echo "yq not found. Installing yq..."
            return 1
    fi

    echo "Fetching gcsfuse version from ${dir}/version_details.yml"
    local gcsfuse_version=$(yq e '.gcsfuse_version_or_commit' "${dir}/version_details.yml")

    if [ -z "$gcsfuse_version" ]; then
        echo "Error: Could not find gcsfuse_version in the YAML file."
        return 1
    fi

    echo "Building gcsfuse binary with version: ${gcsfuse_version}"

    # Use a subshell to avoid changing the script's main working directory
    (
        # Remove existing gcsfuse directory if it exists
        echo "Removing existing gcsfuse directory..."
        rm -rf gcsfuse

        # Clone the gcsfuse repository
        echo "Cloning gcsfuse repository..."
        git clone https://github.com/GoogleCloudPlatform/gcsfuse.git

        # Navigate into the cloned directory
        cd gcsfuse

        # Check out the specific version or commit
        echo "Checking out version/commit: ${gcsfuse_version}"
        git checkout "${gcsfuse_version}"

        # Build the gcsfuse binary
        echo "Building the binary..."
        go build

        # Install the binary to a system path
        echo "Installing gcsfuse to /usr/local/bin/"
        sudo cp -f gcsfuse /usr/local/bin/gcsfuse
    )

    if command -v gcsfuse &> /dev/null; then
        echo "gcsfuse installed successfully."
        gcsfuse --version
    else
        echo "Error: gcsfuse binary not found in PATH."
        return 1
    fi
}

mount_gcsfuse() {
    local mntdir="$1"
    local bucketname="$2"
    local mount_config="$3"

    # Create the mount directory if it doesn't exist
    if [ ! -d "$mntdir" ]; then
        mkdir -p "$mntdir"
    fi

    # Check if the directory is already mounted
    if mountpoint -q "$mntdir"; then
        echo "Directory '$mntdir' is already a mount point. Skipping."
        return 0
    fi
    
    # Check if the mount config file exists
    if [ ! -f "$mount_config" ]; then
        echo "Error: Mount config file not found at '$mount_config'."
        return 1
    fi

    # Mount the GCS bucket using the config file
    echo "Mounting bucket '$bucketname' to '$mntdir' with config '$mount_config'..."
    gcsfuse --config-file="$mount_config" "$bucketname" "$mntdir"

    # Verify the mount was successful
    if mountpoint -q "$mntdir"; then
        echo "Successfully mounted '$bucketname' to '$mntdir'."
        return 0
    else
        echo "Error: Failed to mount '$bucketname' to '$mntdir'."
        return 1
    fi
}

unmount_gcsfuse() {
    local mntdir="$1"

    # Check if the directory is a mount point
    if ! mountpoint -q "$mntdir"; then
        echo "Directory '$mntdir' is not a mount point. Skipping."
        return 0
    fi
    
    echo "Unmounting '$mntdir'..."

    # Use the correct unmount command based on the OS
    if [[ "$(uname)" == "Linux" ]]; then
        fusermount -uz "$mntdir"
    elif [[ "$(uname)" == "Darwin" ]]; then
        umount "$mntdir"
    else
        echo "Warning: Unsupported OS for unmounting."
        return 1
    fi

    # Verify the unmount was successful
    if ! mountpoint -q "$mntdir"; then
        echo "Successfully unmounted '$mntdir'."
        return 0
    else
        echo "Error: Failed to unmount '$mntdir'."
        return 1
    fi
}

start_benchmarking_runs() {
    dir="$1"
    iterations="$2"

    mkdir -p "${dir}/raw-results/"

    fio_job_cases="${dir}/fio_job_cases.csv"
    fio_job_file="${dir}/jobfile.fio"

    # Check if the job cases file exists
    if [ ! -f "$fio_job_cases" ]; then
        echo "Error: FIO job cases file not found at ${fio_job_cases}"
        return 1
    fi

    # Check if the job file  exists
    if [ ! -f "$fio_job_file" ]; then
        echo "Error: FIO job file not found at ${fio_job_file}"
        return 1
    fi
    
    mntdir="${dir}/mntdir/"
    mount_config="${dir}/mount_config.yml"

    # Check if the mount config file  exists
    if [ ! -f "$mount_config" ]; then
        echo "Error: mount config file not found at ${mount_config}"
        return 1
    fi
    
    # Read the CSV file line by line, skipping the header
    tail -n +2 "$fio_job_cases" | while IFS=, read -r bs file_size iodepth iotype threads nrfiles; do
        # Iterate for the specified number of runs for this job case
            # Mount the bucket once before the loop if reuse_same_mount is 'true'
        if [[ "$reuse_same_mount" == "true" ]]; then
            mount_gcsfuse "$mntdir" "$bucket" "$mount_config"
        fi
        nrfiles="${nrfiles%$'\r'}"

        echo "Experiment config: ${bs}, ${file_size}, ${iodepth}, ${iotype}, ${threads}, ${nrfiles}"

        testdir="${dir}/raw-results/fio_output_${bs}_${file_size}_${iodepth}_${iotype}_${threads}_${nrfiles}"
        mkdir -p "$testdir"
        
        timestamps_file="${testdir}/timestamps.csv"
        echo "iteration,start_time,end_time" > "$timestamps_file"

        for ((i = 1; i <= iterations; i++)); do
            echo "Starting FIO run ${i} of ${iterations} for case: bs=${bs}, file_size=${file_size}, iodepth=${iodepth}, iotype=${iotype}, threads=${threads}, nrfiles=${nrfiles}"
            # If reuse_same_mount is 'false', mount the bucket for this run
            if [[ "$reuse_same_mount" != "true" ]]; then
                mount_gcsfuse "$mntdir" "$bucket" "$mount_config"
            fi

            start_time=$(date +"%Y-%m-%dT%H:%M:%S%z")

            filename_format="${iotype}-\$jobnum/\$filenum"
            output_file="${testdir}/fio_output_iter${i}.json"
            MNTDIR=${mntdir} IODEPTH=${iodepth} IOTYPE=${iotype} BLOCKSIZE=${bs} FILESIZE=${file_size} NRFILES=${nrfiles} NUMJOBS=${threads} FILENAME_FORMAT=${filename_format} fio $fio_job_file --output-format=json > "$output_file" 2>&1 
            
            end_time=$(date +"%Y-%m-%dT%H:%M:%S%z")
            echo "${i},${start_time},${end_time}" >> "$timestamps_file"

            # If reuse_same_mount is 'false', unmount after this run
            if [[ "$reuse_same_mount" != "true" ]]; then
                unmount_gcsfuse "$mntdir"
            fi

            echo "Sleeping for 20 seconds to keep VM metrics independent for each iteration...."
            sleep 20

        done
            # Unmount the bucket once after the loop if reuse_same_mount is 'true'
        if [[ "$reuse_same_mount" == "true" ]]; then
            unmount_gcsfuse "$mntdir"
        fi
    done


}

# Check if the values were retrieved successfully
if [ -n "$bucket" ] && [ -n "$artifacts_bucket" ] && [ -n "$benchmark_id" ]; then
    echo "Metadata parameters are accessible and were retrieved successfully."
    dir="$(pwd)"
    
    # Install dependencies
    install_dependencies

    # Copy the resources necessary for running the benchmark
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
# Corrected typo from `touc` to `touch`
touch /tmp/failure.txt
# Corrected extra slash in gcloud storage command
gcloud storage cp /tmp/failure.txt gs://$artifacts_bucket/$benchmark_id/failure.txt
exit 1
