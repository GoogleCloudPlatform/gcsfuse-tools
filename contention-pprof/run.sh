#!/bin/bash

# set -x

workloads=(
    "96threads_2G_4K_seq_read_singlefile"
    "96threads_2G_4K_seq_read_multifiles"
    "96threads_2G_4K_rand_read_singlefile"
    "96threads_2G_4K_rand_read_multifiles"
    "96threads_2G_1M_seq_read_singlefile"
    "96threads_2G_1M_seq_read_multifiles"
    "96threads_2G_1M_rand_read_singlefile"
    "96threads_2G_1M_rand_read_multifiles"
)

for workload in ${workloads[@]}; do
    file_name=${1:-"out.txt"}

    # Unmount if already mounted
    umount ~/bucket-zonal || true

    export GODEBUG=runtimecontentionstacks=1

    # Mount gcs
    mkdir -p ~/bucket-zonal
    gcsfuse --enable-cloud-profiling=true --profiling-label=princer-$workload-nolog1 --profiling-goroutines --profiling-mutex=true --metadata-cache-ttl-secs=-1 --stat-cache-max-size-mb=-1 --type-cache-max-size-mb=-1 fastbyte-team-princer-zb-write-test-uw4a ~/bucket-zonal

    fio --section=$workload /home/princer_google_com/dev/gcsfuse-tools/contention-pprof/read.fio | tee -a $file_name

    # Unmount gcs.
    umount ~/bucket-zonal || true
done
