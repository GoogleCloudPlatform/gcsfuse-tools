#! /bin/bash
# Copyright 2025 Google LLC
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

# Print commands and their arguments as they are executed.
set -x
# Exit immediately if a command exits with a non-zero status.
set -e

# Extract the metadata parameters passed, for which we need the zone of the GCE VM 
# on which the tests are supposed to run.
ZONE=$(curl -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/zone)
echo "Got ZONE=\"${ZONE}\" from metadata server."
# The format for the above extracted zone is projects/{project-id}/zones/{zone}, thus, from this
# need extracted zone name.
ZONE_NAME=$(basename $ZONE)
# This parameter is passed as the GCE VM metadata at the time of creation.(Logic is handled in louhi stage script)
GCSFUSE_VERSION=$(gcloud compute instances describe "$HOSTNAME" --zone="$ZONE_NAME" --format='get(metadata.gcsfuse_version)')
echo "GCSFUSE_VERSION : \"${GCSFUSE_VERSION}\""
GCS_BUCKET_WITH_FIO_TEST_DATA=$(gcloud compute instances describe "$HOSTNAME" --zone="$ZONE_NAME" --format='get(metadata.GCS_BUCKET_WITH_FIO_TEST_DATA)')
echo "GCS_BUCKET_WITH_FIO_TEST_DATA : \"${GCS_BUCKET_WITH_FIO_TEST_DATA}\""

# run the following commands to add starterscriptuser
sudo adduser --ingroup google-sudoers --disabled-password --home=/home/starterscriptuser --gecos "" starterscriptuser
# Run the following as starterscriptuser
sudo -u starterscriptuser bash -c '
    export GCSFUSE_VERSION='$GCSFUSE_VERSION'
    export GCS_BUCKET_WITH_FIO_TEST_DATA='$GCS_BUCKET_WITH_FIO_TEST_DATA'

    echo "Installing dependencies..."
    sudo apt-get update
    sudo apt-get install libaio1 libaio-dev -y
    sudo apt-get install gcc make  -y

    sudo apt install golang-go fio
    go_version=$(go version)
    fio_version=$(fio --version)
    gcsfuse_version=$GCSFUSE_VERSION

    touch details.txt
    echo "go version : ${go_version}" >> details.txt
    echo "fio version : ${fio_version}" >> details.txt
    echo "GCSFuse version: ${gcsfuse_version} >> details.txt

    echo "Cloning into gcsfuse..."
    git clone  https://github.com/GoogleCloudPlatform/gcsfuse.git 
    cd gcsfuse
    git checkout $gcsfuse_version
    go build .
    cd ..

    # Get the fio workload files
    wget -O seq-read.fio https://raw.githubusercontent.com/GoogleCloudPlatform/gcsfuse-tools/master/perf-benchmarking-for-releases/fio-job-files/sequential-reads.fio
    wget -O rand-read.fio https://raw.githubusercontent.com/GoogleCloudPlatform/gcsfuse-tools/master/perf-benchmarking-for-releases/fio-job-files/random-reads.fio
    wget -O seq-writes.fio https://raw.githubusercontent.com/GoogleCloudPlatform/gcsfuse-tools/master/perf-benchmarking-for-releases/fio-job-files/sequential-writes.fio

    mkdir mount-point
    curr_dir=$(pwd)
    gcsfuse_bin=${curr-dir}/gcsfuse/gcsfuse
    mnt=${curr_dir}/mount-point

    # Cleaning the pagecache, dentries and inode cache before the starting the workload.
    echo "Drop page cache..."
    echo 3 > /proc/sys/vm/drop_caches

    # Running the sequential reads
    $gcsfuse_bin $GCS_BUCKET_WITH_FIO_TEST_DATA $mnt
    echo "Started the fio workload for sequential reads"
    DIR=$mnt fio seq-read.fio --output-format=json > sequential-read-benchmark.json
    umount $mnt
    echo 1 > /proc/sys/vm/drop_caches

    # Running the random reads
    echo "Started the fio workload for sequential reads"
    $gcsfuse_bin $GCS_BUCKET_WITH_FIO_TEST_DATA $mnt
    DIR=$mnt fio rand-read.fio --output-format=json > random-read-benchmark.json
    umount $mnt
    echo 1 > /proc/sys/vm/drop_caches

    # Running the sequential writes
    $gcsfuse_bin $GCS_BUCKET_WITH_FIO_TEST_DATA $mnt
    echo "Started the fio workload for sequential reads"
    DIR=$mnt fio seq-writes.fio --output-format=json > sequential-write-benchmark.json
    umount $mnt
    echo 1 > /proc/sys/vm/drop_caches

    # Clearing out the write data from the GCS Bucket
    gcloud storage rm gs://$GCS_BUCKET_WITH_FIO_TEST_DATA/write* 

    echo "Copying benchmark run details to GCS bucket..."
    gcloud storage cp details.txt gs://gcsfuse-release-benchmarks/${GCSFUSE_VERSION}/
    gcloud storage cp sequential-read-benchmark.json gs://gcsfuse-release-benchmarks/${GCSFUSE_VERSION}/
    gcloud storage cp random-read-benchmark.json gs://gcsfuse-release-benchmarks/${GCSFUSE_VERSION}/
    gcloud storage cp sequential-write-benchmark.json gs://gcsfuse-release-benchmarks/${GCSFUSE_VERSION}/

    echo "Done!"
'