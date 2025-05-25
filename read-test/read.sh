#!/bin/bash

# set -x

file_name=${1:-"out.txt"}
current_dir=$(pwd)
file_name=$current_dir/$file_name

# take a default value of 2nd argument, in a single line
enabled=${2:-"yes"}

set +e # Don't fail the script in case of failure.
umount ~/bucket
set -e # Fail the script in case of failure.

if [[ "$enabled" == "yes" ]]; then
    gcsfuse --read-inactive-stream-timeout 10s --implicit-dirs --client-protocol grpc princer-gcsfuse-tail-scale-test-bkt ~/bucket | tee -a $file_name
else
    gcsfuse --implicit-dirs --client-protocol grpc princer-gcsfuse-tail-scale-test-bkt ~/bucket | tee -a $file_name
fi

cd ~/bucket

# patterns=("read" "randread")
patterns=("read")

# jobs=(1 16 48 96)
jobs=(96)

# file_sizes=("256K" "1M" "5M" "15M" "30M" "60M" "120M" "250M" "500M" "1G" "2G")
file_sizes=("120M")

for job in ${jobs[@]}; do
    for size in ${file_sizes[@]}; do
        mkdir -p $size
        for pattern in ${patterns[@]}; do
            echo "Running for $pattern over $size files with $job jobs..." | tee -a $file_name
            BLOCK_SIZE=1M FILE_SIZE=$size MODE=$pattern NUMJOBS=$job fio ~/dev/gcsfuse-tools/read-test/read.fio | tee -a $file_name
            echo "Running for $pattern over $size files with $job jobs completed." | tee -a $file_name
            # sleep 300s
            sleep 30s
        done
    done
done

cd ~/

umount ~/bucket
