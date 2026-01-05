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


# A helper function to check if a command exists
command_exists () {
  command -v "$1" >/dev/null 2>&1
}

# Platform-agnostic function to install yq by downloading from GitHub
install_yq() {
    echo "Installing yq from GitHub releases..."
    local OS=$(uname -s)
    local arch=$(uname -m)
    local install_path="/usr/local/bin/yq" # Default for Linux/macOS

    # Adjust install_path for Windows/MSYS
    if [[ "$OS" == "MINGW"* || "$OS" == "MSYS_NT"* ]]; then
        install_path="/usr/bin/yq"
    fi

    # Ensure the target directory exists
    sudo mkdir -p "$(dirname "${install_path}")"

    echo "Detected OS: $OS, Architecture: $arch"

    local download_arch=""
    case "$arch" in
      x86_64)  download_arch="amd64";;
      aarch64|arm64) download_arch="arm64";;
      *) echo "Unsupported architecture for yq download: $arch"; exit 1;;
    esac

    local filename=""
    case "$OS" in
      "Linux")
        filename="yq_linux_${download_arch}"
        ;;
      "Darwin") # macOS
        filename="yq_darwin_${download_arch}"
        ;;
      "MINGW"*|"MSYS_NT"*) # Windows (Git Bash, MSYS)
        OS="Windows"
        filename="yq_windows_${download_arch}.exe"
        ;;
      *)
        echo "Unsupported operating system for yq installation: $OS"
        exit 1
        ;;
    esac

    local download_url="https://github.com/mikefarah/yq/releases/latest/download/$filename"

    echo "Attempting to remove any existing yq at ${install_path}..."
    sudo rm -f "${install_path}"

    echo "Downloading yq for ${OS} ${download_arch} from ${download_url}..."
    if ! sudo curl -L -o "${install_path}" "${download_url}"; then
        echo "Error: Failed to download yq."
        exit 1
    fi
    sudo chmod +x "${install_path}"

    echo "Verifying yq installation at ${install_path}..."
    if [[ ! -x "${install_path}" ]]; then
        echo "Error: yq not found or not executable at ${install_path}."
        ls -l "${install_path}"
        exit 1
    fi

    echo "yq executable is at: ${install_path}"
    echo "yq Version:"
    "${install_path}" --version

    echo "yq installed successfully to ${install_path}."
}


install_dependencies() {
    # Get the operating system and store it in a variable
    OS=$(uname -s)

    echo "Detected operating system: $OS"

    case "$OS" in
      "Linux")
        echo "Installing dependencies for Linux..."
        # Check for a Debian-based system (apt)
        if command_exists apt-get; then
          sudo apt-get update -y
          # Note: libaio-dev is a Linux-specific dependency
          sudo apt-get install wget libaio-dev gcc g++ make git fuse -y
          echo "apt-get packages installed."
        # Check for a Red Hat-based system (dnf or yum)
        elif command_exists dnf; then
          sudo dnf install wget libaio-devel gcc-c++ make git fuse -y
          echo "dnf packages installed."
        elif command_exists yum; then
          sudo yum install wget libaio-devel gcc-c++ make git fuse -y
          echo "yum packages installed."
        # Add more package managers as needed (e.g., pacman for Arch)
        else
          echo "Could not find a supported package manager (apt-get, dnf, or yum). Please install dependencies manually."
          exit 1
        fi
        ;;

      "Darwin")
        echo "Installing dependencies for macOS..."
        # Check for Homebrew
        if ! command_exists brew; then
          echo "Homebrew not found. Please install it from https://brew.sh and re-run the script."
          exit 1
        fi
        # Install build tools, git, and fuse
        # Note: libaio-dev is not available on macOS, as it is a Linux-specific library.
        # The build tools will be installed via Xcode Command Line Tools.
        echo "Installing Xcode Command Line Tools..."
        xcode-select --install
        echo "Installing homebrew packages..."
        if ! command_exists wget; then
            echo "Installing wget via Homebrew..."
            brew install wget
        else
            echo "wget not found. Please install wget, or install Homebrew to install it."
            exit 1
        fi

        brew install gcc git coreutils
        echo "brew packages installed."
        ;;

      "MINGW"*|"MSYS_NT"*)
        echo "Installing dependencies for Windows (Git Bash/MSYS2)..."
        # Check for Chocolatey
        if ! command_exists choco; then
          echo "Chocolatey not found. Please install it from https://chocolatey.org and re-run the script."
          exit 1
        fi
        # Note: libaio-dev is not available on Windows.
        # Install build tools and Git using Chocolatey
        echo "Installing Chocolatey packages..."
        choco install git make wget -y
        echo "choco packages installed."
        ;;

      *)
        echo "Unsupported operating system: $OS. Please install dependencies manually."
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

check_if_package_installed() {
    # Check for the 'package' command
    # The output is redirected to /dev/null to keep the console clean.
    local pkg="$1"
    command -v "$pkg" >/dev/null 2>&1
}

# Install fio on the VM.
install_fio_on_vm() {
    local fio_install_path="/usr/local/bin/fio"
    # Check if fio is already installed and executable at the expected path
    if [[ -x "$fio_install_path" ]]; then
        echo "FIO is already installed at $fio_install_path"
        "$fio_install_path" --version
        return 0
    fi
    # dir contains the path to the directory with version_details.yml
    local dir=$1
    echo "Fetching fio_version from ${dir}/version_details.yml"
    # Use yq to parse the YAML file and get the fio_version.
    local fio_version
    fio_version=$(/usr/local/bin/yq e '.fio_version' "${dir}/version_details.yml")

    # Check if the version was successfully retrieved.
    if [ -z "$fio_version" ]; then
        echo "Error: Could not find fio_version in the YAML file."
        return 1
    fi

    echo "Preparing to install fio version: ${fio_version}"
    (
        if [ -d "fio" ]; then
            echo "Removing existing fio directory..."
            rm -rf fio
        fi

        echo "Cloning fio version: ${fio_version}..."
        if ! git clone --depth 1 -b "fio-${fio_version}" https://github.com/axboe/fio.git; then
            echo "Error: Failed to clone fio repository."
            exit 1
        fi

        cd fio || { echo "Error: Failed to cd into fio directory."; exit 1; }

        echo "Configuring fio..."
        if ! ./configure; then
            echo "Error: fio configuration failed."
            exit 1
        fi

        echo "Building fio..."
        if ! make -j"$(nproc)"; then
            echo "Error: fio build failed."
            exit 1
        fi

        echo "Installing fio to $fio_install_path..."
        if ! sudo make install; then
            echo "Error: fio installation failed."
            exit 1
        fi
    )

    if [ $? -eq 0 ]; then
        echo "Fio installation process complete."
        if [[ -x "$fio_install_path" ]]; then
            echo "fio installed successfully to $fio_install_path"
            "$fio_install_path" --version
            return 0
        else
            echo "Error: fio not found at $fio_install_path after installation."
            return 1
        fi
    else
        echo "Fio installation process failed."
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

    echo "Fetching go_version from ${dir}/version_details.yml"
    local go_version=$(/usr/local/bin/yq e '.go_version' "${dir}/version_details.yml")

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

install_unzip_on_vm() {
    # Helper to check for command existence
    command_exists() {
        command -v "$1" >/dev/null 2>&1
    }

    if command_exists unzip; then
        echo "unzip is already installed."
        return 0
    fi

    echo "unzip not found, attempting to install..."

    local os_kernel
    os_kernel=$(uname -s)

    case "$os_kernel" in
        "Linux")
            echo "Linux detected."
            if command_exists apt; then
                sudo apt update && sudo apt install -y unzip
            elif command_exists yum; then
                sudo yum install -y unzip
            elif command_exists dnf; then
                sudo dnf install -y unzip
            elif command_exists pacman; then
                sudo pacman -Sy --noconfirm unzip
            else
                echo "Unsupported Linux package manager for unzip installation."
                exit 1
            fi
            ;;
        "Darwin")
            echo "macOS detected."
            if command_exists brew; then
                brew install unzip
            else
                echo "Homebrew not found. Please install Homebrew from https://brew.sh and rerun."
                exit 1
            fi
            ;;
        "MINGW"*|"MSYS_NT"*)
            echo "Windows (MINGW/MSYS) detected. Using Chocolatey..."
            if ! command_exists choco; then
                echo "Chocolatey not found. Please install Chocolatey from https://chocolatey.org and rerun."
                echo "Note: Chocolatey operations typically require Administrator privileges."
                exit 1
            fi
            # choco install -y <package>
            choco install -y unzip
            ;;
        *)
            echo "Unsupported OS for unzip installation: $os_kernel"
            exit 1
            ;;
    esac

    # Final check to ensure unzip is now available
    if ! command_exists unzip; then
        echo "Error: unzip installation failed."
        exit 1
    else
        echo "unzip installed successfully."
    fi
}


build_custom_cpp_fio_engine() {
    local artifacts_bucket=$1
    local dir=$2
    
    local download_file_name="cpp-storage-fio-engine-main.zip"
    local source_dir_name="cpp-storage-fio-engine-main"
    local engine_dir="${dir}/custom-engine"

    # Check if a custom fio engine is already built.
    if [ -f "${engine_dir}/libcpp-storage-fio-engine.so" ]; then
        echo "Custom C++ FIO engine is already built. Skipping build process." >&2
        echo "${engine_dir}/libcpp-storage-fio-engine.so"
        return 0
    fi

    # Check for bazelisk and install if it's not present.
    if ! command -v bazelisk &> /dev/null; then
        echo "bazelisk not found. Installing now..." >&2
        go install github.com/bazelbuild/bazelisk@latest
    fi

    echo "Downloading C++ fio engine source from GCS bucket..." >&2
    gcloud storage cp "gs://${artifacts_bucket}/benchmarking-resources/${download_file_name}" "${dir}/"
    
    echo "Unzipping source code..." >&2
    unzip -q "${dir}/${download_file_name}" -d "${dir}"
    
    echo "Building the custom fio engine with bazelisk..." >&2
    (
        cd "${dir}/${source_dir_name}" || { echo "Error: Failed to cd into the source directory." >&2; exit 1; }
        # Get the path to the bazelisk binary
        BAZELISK_BIN="$(go env GOPATH)/bin/bazelisk"
        # Run the build command
        "${BAZELISK_BIN}" build -c opt //:ioengine_shared
        # Move the built library to the designated directory
        mkdir -p "${engine_dir}"
        mv bazel-bin/libcpp-storage-fio-engine.so "${engine_dir}/"
    )

    # Check if the build was successful
    if [ -f "${engine_dir}/libcpp-storage-fio-engine.so" ]; then
        echo "Build complete. Engine located at: ${engine_dir}/libcpp-storage-fio-engine.so" >&2
        echo "${engine_dir}/libcpp-storage-fio-engine.so"
        # Clean up the downloaded files
        rm -rf "${dir}/${source_dir_name}"
        rm "${dir}/${download_file_name}"
        return 0
    else
        echo "Fio engine build failed." >&2
        return 1
    fi
}

start_benchmarking_runs() {
    dir="$1"
    iterations="$2"
    fio_custom_engine_path="$3"
    # Check if the custom engine path is empty
    if [ -z "$fio_custom_engine_path" ]; then
        echo "Error: The path to the custom FIO engine is empty. Exiting."
        exit 1
    fi

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
        
    # Read the CSV file line by line, skipping the header.
    while IFS=, read -r bs file_size iodepth iotype threads nrfiles || [[ -n "$nrfiles" ]]; do
        # Iterate for the specified number of runs for this job case       
        nrfiles="${nrfiles%$'\r'}"

        echo "Experiment config: ${bs}, ${file_size}, ${iodepth}, ${iotype}, ${threads}, ${nrfiles}"

        testdir="${dir}/raw-results/fio_output_${bs}_${file_size}_${iodepth}_${iotype}_${threads}_${nrfiles}"
        mkdir -p "$testdir"
        
        timestamps_file="${testdir}/timestamps.csv"
        echo "iteration,start_time,end_time" > "$timestamps_file"

        for ((i = 1; i <= iterations; i++)); do
            echo "Starting FIO run ${i} of ${iterations} for case: bs=${bs}, file_size=${file_size}, iodepth=${iodepth}, iotype=${iotype}, threads=${threads}, nrfiles=${nrfiles}"
            start_time=$(date -u +"%Y-%m-%dT%H:%M:%S%z")

            filename_format="${bucket}/${iotype}-\$jobnum/\$filenum"
            output_file="${testdir}/fio_output_iter${i}.json"

            # Use the custom engine if the path is provided
            local engine_option="--ioengine=psync"
            if [ -n "$fio_custom_engine_path" ]; then
                engine_option="--ioengine=external:${fio_custom_engine_path}"
                echo "Using custom fio engine: ${fio_custom_engine_path}"
            fi

            IODEPTH=${iodepth} IOTYPE=${iotype} BLOCKSIZE=${bs} FILESIZE=${file_size} NRFILES=${nrfiles} NUMJOBS=${threads} FILENAME_FORMAT=${filename_format} fio $fio_job_file --output-format=json "${engine_option}" > "$output_file" 2>&1 
            
            end_time=$(date -u +"%Y-%m-%dT%H:%M:%S%z")
            echo "${i},${start_time},${end_time}" >> "$timestamps_file"

            echo "Sleeping for 20 seconds to keep VM metrics independent for each iteration...."
            sleep 20

        done
    done < <(tail -n +2 "$fio_job_cases")


}

# Check if the values were retrieved successfully
if [ -n "$bucket" ] && [ -n "$artifacts_bucket" ] && [ -n "$benchmark_id" ]; then
    echo "Metadata parameters are accessible and were retrieved successfully."
    dir="$(pwd)"
    
    install_yq
    # Install dependencies
    install_dependencies

    # Copy the resources necessary for running the benchmark
    copy_resources_from_artifact_bucket "$artifacts_bucket" "$benchmark_id" "$dir"

    install_fio_on_vm "$dir"
    install_golang_on_vm "$dir"
    install_unzip_on_vm "$dir"

    # Build the custom C++ FIO engine and get its path
    fio_engine_path=$(build_custom_cpp_fio_engine "$artifacts_bucket" "$dir")

    # Start the benchmarking runs with the custom engine
    start_benchmarking_runs "$dir" "$iterations" "$fio_engine_path"

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
