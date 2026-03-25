#!/bin/bash

parse_test_params() {
    local TEST_ID=$1
    local TEST_LINE
    local LINE_NUM
    
    # Calculate line number (TEST_ID + 1 to skip header)
    LINE_NUM=$((TEST_ID + 1))
    
    TEST_LINE=$(awk -F',' -v line="$LINE_NUM" 'NR==line {print}' test-cases.csv)
    
    if [ -z "$TEST_LINE" ]; then
        echo "ERROR: Could not read test ID $TEST_ID (line $LINE_NUM) from test-cases.csv" >&2
        return 1
    fi
    
    IO_TYPE=$(echo "$TEST_LINE" | cut -d',' -f1 | tr -d ' \r')
    THREADS=$(echo "$TEST_LINE" | cut -d',' -f2 | tr -d ' \r')
    FILE_SIZE=$(echo "$TEST_LINE" | cut -d',' -f3 | tr -d ' \r')
    BS=$(echo "$TEST_LINE" | cut -d',' -f4 | tr -d ' \r')
    IO_DEPTH=$(echo "$TEST_LINE" | cut -d',' -f5 | tr -d ' \r')
    NRFILES=$(echo "$TEST_LINE" | cut -d',' -f6 | tr -d ' \r')
    DIRECT=$(echo "$TEST_LINE" | cut -d',' -f7 | tr -d ' \r')
    
    if [ -z "$BS" ] || [ -z "$FILE_SIZE" ] || [ -z "$IO_DEPTH" ]; then
        echo "ERROR: Invalid parameters from CSV line: $TEST_LINE" >&2
        return 1
    fi
    return 0
}

run_test_iterations() {
    local TEST_DIR=$1
    local FIO_JOB=$2
    local MONITOR_FILE=$3
    local GCSFUSE_BIN_PATH=$4
    local MOUNT_ARGS=$5
    
    for ((i=1; i<=ITERATIONS; i++)); do
        echo "  Iteration $i/$ITERATIONS"
        
        # Mount
        GCSFUSE_LOG_FILE="$TEST_DIR/gcsfuse_mount_${i}.log"
        LOG_FORMAT="text"
        LOG_SEVERITY="info"
        
        if [[ "$VM_NAME" == *"kokoro"* ]]; then
            GCSFUSE_LOG_FILE="$TEST_DIR/gcsfuse_mount_${i}.json"
            LOG_FORMAT="json"
            LOG_SEVERITY="trace"
        fi
        
        $GCSFUSE_BIN_PATH $MOUNT_ARGS \
            --log-format $LOG_FORMAT \
            --log-severity $LOG_SEVERITY \
            --log-file "$GCSFUSE_LOG_FILE" \
            "$BUCKET" "$MOUNT_DIR"
        
        # Verify mount success before proceeding
        if ! mountpoint -q "$MOUNT_DIR"; then
            echo "ERROR: Failed to mount GCSFuse on $MOUNT_DIR" >&2
            return 1
        fi
            
        GCSFUSE_PID=$(pgrep -f "gcsfuse.*${MOUNT_DIR}" | head -1)
        
        # Monitor
        MONITOR_STOP_FLAG="$TEST_DIR/monitor_stop_${i}"
        MONITOR_PID_FILE="$TEST_DIR/monitor_pid_${i}"
        start_monitoring "$GCSFUSE_PID" "$MONITOR_FILE" "$MONITOR_STOP_FLAG" "$MONITOR_PID_FILE"
        
        # Wait for monitor to spin up
        sleep 1
        MONITOR_PID=$(cat "$MONITOR_PID_FILE" 2>/dev/null)

        # Drop Cache
        sync
        sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null || true
        echo "Dropped Page, Dentries, Inodes, Metadata Cache"

        mkdir -p "$TEST_DATA_DIR"

        # Populate Metadata
        echo "Populating metadata for $TEST_DATA_DIR"
        RES_MEM=$(ps -o rss= -p "$GCSFUSE_PID" | tail -n 1 | tr -d ' ')
        echo "GCSFuse Resident Memory before: $((RES_MEM / 1024)) MB"
        POPULATE_START=$(date +%s)

        if ! ls -R "$TEST_DATA_DIR" 1> /dev/null 2>&1; then :; fi
        POPULATE_END=$(date +%s)
        POPULATE_DURATION=$((POPULATE_END - POPULATE_START))
        RES_MEM=$(ps -o rss= -p "$GCSFUSE_PID" | tail -n 1 | tr -d ' ')
        echo "GCSFuse Resident Memory before: $((RES_MEM / 1024)) MB"
        echo "Populated metadata for $TEST_DATA_DIR in ${POPULATE_DURATION}s"

        # --- TIME START ---
        START_TIME=$(date +%s)

        # Run FIO wrapped in an OS-level timeout (30 minutes / 1800s) to prevent infinite hanging
        OUTPUT_FILE="${TEST_DIR}/fio_output_${i}.json"
        FIO_EXIT_CODE=0
        timeout -k 30 1800 fio "$FIO_JOB" --alloc-size=$((2 * 1024 * 1024)) --output-format=json --output="$OUTPUT_FILE" || FIO_EXIT_CODE=$?
        
        if [ $FIO_EXIT_CODE -ne 0 ]; then
            echo "WARNING: FIO failed or OS TIMEOUT REACHED (Exit Code $FIO_EXIT_CODE). Ignoring to continue the orchestrator..." >&2
            stop_monitoring "$MONITOR_PID" "$MONITOR_STOP_FLAG"
            sudo fusermount -uz "$MOUNT_DIR" 2>/dev/null || sudo umount -l "$MOUNT_DIR" 2>/dev/null || true
            
            # Record the duration safely so your fio_durations.csv columns don't misalign
            echo "$(( $(date +%s) - START_TIME ))sec_timeout" >> "${TEST_DIR}/iter_durations.txt"
            
            # Pad the rest of the iterations so CSV columns stay aligned
            for ((j=i+1; j<=ITERATIONS; j++)); do
                echo "skipped" >> "${TEST_DIR}/iter_durations.txt"
            done
            return 0
        fi
        
        # --- TIME END ---
        END_TIME=$(date +%s)
        DURATION=$((END_TIME - START_TIME))
        echo "  [$(date +'%H:%M:%S')] FIO finished. Duration: ${DURATION}s ...!!!..."
        echo "${DURATION}sec" >> "${TEST_DIR}/iter_durations.txt"

        stop_monitoring "$MONITOR_PID" "$MONITOR_STOP_FLAG"
        
        # Unmount
        sudo fusermount -uz "$MOUNT_DIR" 2>/dev/null || sudo umount -l "$MOUNT_DIR" 2>/dev/null || true
        
        # Clean cache again
        sync
        sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null || true
        
        sleep 2
    done
}

execute_test() {
    local TEST_ID=$1
    local TEST_DIR_NAME=$2
    local GCSFUSE_BIN_PATH=$3
    local MOUNT_ARGS=$4
    local MATRIX_ID=${5:-""}
    local CONFIG_ID=${6:-""}
    local CONFIG_LABEL=${7:-""}
    local COMMIT=${8:-""}

    if ! parse_test_params "$TEST_ID"; then return 1; fi
    
    echo "Running Test Matrix Entry $MATRIX_ID (Test ID: $TEST_ID, BS=$BS, File Size=$FILE_SIZE, IO Depth=$IO_DEPTH, IO Type=$IO_TYPE, Threads=$THREADS, Nrfiles=$NRFILES, Direct=$DIRECT)"
    
    TEST_DIR="test-${TEST_DIR_NAME}"
    mkdir -p "$TEST_DIR"
    
    # Generate FIO Job
    FIO_JOB="$TEST_DIR/job.fio"
    TEST_DATA_DIR="$MOUNT_DIR/${VM_NAME}/${FILE_SIZE}"
    export BS FILE_SIZE IO_DEPTH IO_TYPE THREADS NRFILES DIRECT TEST_DATA_DIR
    envsubst '$BS $FILE_SIZE $IO_DEPTH $IO_TYPE $THREADS $NRFILES $DIRECT $TEST_DATA_DIR' < jobfile.fio > "$FIO_JOB"
    
    MONITOR_FILE="$TEST_DIR/monitor.log"
    echo "timestamp,cpu_percent,mem_rss_mb,mem_vsz_mb,page_cache_gb,system_cpu_percent,net_rx_mbps,net_tx_mbps" > "$MONITOR_FILE"
    
    if ! run_test_iterations "$TEST_DIR" "$FIO_JOB" "$MONITOR_FILE" "$GCSFUSE_BIN_PATH" "$MOUNT_ARGS"; then
        echo "Test failed, uploading logs for debugging..." >&2
        gcloud storage cp -r "$TEST_DIR" "${RESULT_BASE}/"
        return 1
    fi
    
    # Calculate Metrics
    read AVG_CPU MAX_CPU AVG_MEM_RSS MAX_MEM_RSS AVG_PAGE_CACHE MAX_PAGE_CACHE AVG_SYS_CPU MAX_SYS_CPU AVG_NET_RX MAX_NET_RX AVG_NET_TX MAX_NET_TX < <(calculate_metrics "$MONITOR_FILE")
    echo "  Results: CPU=${AVG_CPU}% Mem=${AVG_MEM_RSS}MB NetRX=${AVG_NET_RX}MB/s"

    # Combine durations and write to the summary file
    if [ -f "${TEST_DIR}/iter_durations.txt" ]; then
        local LINE_NUM=$((TEST_ID + 1))
        local RAW_TEST_LINE=$(awk -F',' -v line="$LINE_NUM" 'NR==line {print}' test-cases.csv | tr -d '\r\n')
        local DURATION_STR=$(paste -sd, "${TEST_DIR}/iter_durations.txt" | sed 's/,/, /g')
        echo "${RAW_TEST_LINE}, ${DURATION_STR}" >> "$WORKSPACE/fio_durations.csv"
        gcloud storage cp "$WORKSPACE/fio_durations.csv" "${RESULT_BASE}/fio_durations.csv" 2>/dev/null || true
    fi

    TEST_PARAMS="{\"test_id\":\"$TEST_ID\",\"bs\":\"$BS\",\"file_size\":\"$FILE_SIZE\",\"io_depth\":\"$IO_DEPTH\",\"io_type\":\"$IO_TYPE\",\"threads\":\"$THREADS\",\"nrfiles\":\"$NRFILES\",\"direct\":\"$DIRECT\",\"config_id\":\"$CONFIG_ID\",\"config_label\":\"$CONFIG_LABEL\",\"commit\":\"$COMMIT\",\"mount_args\":\"$MOUNT_ARGS\",\"avg_cpu\":\"$AVG_CPU\",\"peak_cpu\":\"$MAX_CPU\",\"avg_mem_mb\":\"$AVG_MEM_RSS\",\"peak_mem_mb\":\"$MAX_MEM_RSS\",\"avg_page_cache_gb\":\"$AVG_PAGE_CACHE\",\"peak_page_cache_gb\":\"$MAX_PAGE_CACHE\",\"avg_sys_cpu\":\"$AVG_SYS_CPU\",\"peak_sys_cpu\":\"$MAX_SYS_CPU\",\"avg_net_rx_mbps\":\"$AVG_NET_RX\",\"peak_net_rx_mbps\":\"$MAX_NET_RX\",\"avg_net_tx_mbps\":\"$AVG_NET_TX\",\"peak_net_tx_mbps\":\"$MAX_NET_TX\"}"
    
    jq ".tests += [{\"matrix_id\":$MATRIX_ID,\"test_id\":$TEST_ID,\"config_id\":$CONFIG_ID,\"status\":\"success\",\"params\":$TEST_PARAMS}]" manifest.json > manifest_tmp.json
    mv manifest_tmp.json manifest.json
    
    gcloud storage cp -r "$TEST_DIR" "${RESULT_BASE}/"
    return 0
}
