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

logs="--log-severity trace --log-format text --log-file ~/logs.txt"
if [[ "$enabled" == "yes" ]]; then
    # gcsfuse --read-inactive-stream-timeout 10s --implicit-dirs --client-protocol grpc princer-gcsfuse-tail-scale-test-bkt ~/bucket | tee -a $file_name
    gcsfuse --enable-buffered-read --read-global-max-blocks 80 --read-max-blocks-per-handle 14 --read-block-size-mb 32 --implicit-dirs princer-grpc-write-test-uw4a ~/bucket | tee -a $file_name
else
    gcsfuse --implicit-dirs princer-grpc-write-test-uw4a ~/bucket | tee -a $file_name
fi

cd ~/bucket

patterns=("read" "randread")
# patterns=("read")

# jobs=(1 16 48 96)
jobs=(1)

file_sizes=("256K" "1M" "5M" "15M" "30M" "60M" "120M" "250M" "500M" "1G" "2G")
# file_sizes=("256K")

# Function to determine number of files based on file size
get_nr_files() {
    local size=$1
    case $size in
        "256K"|"1M")
            echo 1000
            ;;
        "4M"|"5M"|"15M")
            echo 500
            ;;
        "30M"|"60M"|"120M"|"150M"|"180M")
            echo 300
            ;;
        "200"|"1G")
            echo 200
            ;;
        "2G")
            echo 100
            ;;
        # Default case for other sizes
        *)
            if [[ $size =~ ^[0-9]+[KM]$ ]]; then
                # Extract numeric value and unit
                local num=${size%[KM]}
                local unit=${size: -1}
                
                if [[ $unit == "K" ]]; then
                    # Convert KB to MB for comparison
                    local mb=$(echo "scale=2; $num / 1024" | bc -l)
                elif [[ $unit == "M" ]]; then
                    local mb=$num
                fi
                
                # Apply rules based on MB value
                if (( $(echo "$mb < 5" | bc -l) )); then
                    echo 1000
                elif (( $(echo "$mb < 200" | bc -l) )); then
                    echo 500
                else
                    echo 200  # Default for larger sizes
                fi
            else
                echo 100  # Default fallback
            fi
            ;;
    esac
}

# Function to determine block size based on file size
get_block_size() {
    local size=$1
    case $size in
        "256K")
            echo "256K"
            ;;
        *)
            echo "1M"
            ;;
    esac
}

for job in ${jobs[@]}; do
    for size in ${file_sizes[@]}; do
        # Get the appropriate number of files for this size
        nr_files=$(get_nr_files "$size")
        # Get the appropriate block size for this file size
        block_size=$(get_block_size "$size")
        
        mkdir -p $size
        for pattern in ${patterns[@]}; do
            echo "Running for $pattern over $size files with $job jobs and $nr_files files (block_size: $block_size)..." | tee -a $file_name
            NR_FILES=$nr_files BLOCK_SIZE=$block_size FILE_SIZE=$size MODE=$pattern NUMJOBS=$job fio ~/dev/gcsfuse-tools/read-test/read.fio | tee -a $file_name
            echo "Running for $pattern over $size files with $job jobs completed." | tee -a $file_name
            # sleep 300s
            sleep 120s
        done
    done
done

cd ~/

umount ~/bucket
