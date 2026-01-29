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
        $GCSFUSE_BIN_PATH $MOUNT_ARGS \
            --log-format text \
            --log-severity info \
            --log-file "$GCSFUSE_LOG_FILE" \
            "$BUCKET" "$MOUNT_DIR"
            
        GCSFUSE_PID=$(pgrep -f "gcsfuse.*${MOUNT_DIR}" | head -1)
        
        # Monitor
        MONITOR_STOP_FLAG="$TEST_DIR/monitor_stop_${i}"
        MONITOR_PID_FILE="$TEST_DIR/monitor_pid_${i}"
        start_monitoring "$GCSFUSE_PID" "$MONITOR_FILE" "$MONITOR_STOP_FLAG" "$MONITOR_PID_FILE"
        
        # Wait for monitor to spin up
        sleep 1
        MONITOR_PID=$(cat "$MONITOR_PID_FILE" 2>/dev/null)

        # Populate Metadata
        mkdir -p "$TEST_DATA_DIR"
        if ! ls -R "$TEST_DATA_DIR" 1> /dev/null 2>&1; then :; fi
        
        # Drop Cache
        sync
        sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null || true
        
        # Run FIO
        OUTPUT_FILE="${TEST_DIR}/fio_output_${i}.json"
        if ! fio "$FIO_JOB" --output-format=json --output="$OUTPUT_FILE"; then
            echo "ERROR: FIO execution failed" >&2
            stop_monitoring "$MONITOR_PID" "$MONITOR_STOP_FLAG"
            fusermount -u "$MOUNT_DIR" 2>/dev/null
            return 1
        fi
        
        stop_monitoring "$MONITOR_PID" "$MONITOR_STOP_FLAG"
        
        # Unmount
        fusermount -u "$MOUNT_DIR" 2>/dev/null || umount "$MOUNT_DIR" 2>/dev/null || true
        
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
    local MANIFEST_ENTRY_TYPE=$5
    local MATRIX_ID=${6:-""}
    local CONFIG_ID=${7:-""}
    local CONFIG_LABEL=${8:-""}
    local COMMIT=${9:-""}

    if ! parse_test_params "$TEST_ID"; then return 1; fi
    
    echo "Running Test $TEST_ID (BS=$BS, Threads=$THREADS)"
    
    TEST_DIR="test-${TEST_DIR_NAME}"
    mkdir -p "$TEST_DIR"
    
    # Generate FIO Job
    FIO_JOB="$TEST_DIR/job.fio"
    TEST_DATA_DIR="$MOUNT_DIR/$FILE_SIZE"
    export BS FILE_SIZE IO_DEPTH IO_TYPE THREADS NRFILES TEST_DATA_DIR
    envsubst '$BS $FILE_SIZE $IO_DEPTH $IO_TYPE $THREADS $NRFILES $TEST_DATA_DIR' < jobfile.fio > "$FIO_JOB"
    
    MONITOR_FILE="$TEST_DIR/monitor.log"
    echo "timestamp,cpu_percent,mem_rss_mb,mem_vsz_mb,page_cache_gb,system_cpu_percent,net_rx_mbps,net_tx_mbps" > "$MONITOR_FILE"
    
    if ! run_test_iterations "$TEST_DIR" "$FIO_JOB" "$MONITOR_FILE" "$GCSFUSE_BIN_PATH" "$MOUNT_ARGS"; then
        return 1
    fi
    
    # Calculate Metrics
    read AVG_CPU MAX_CPU AVG_MEM_RSS MAX_MEM_RSS AVG_PAGE_CACHE MAX_PAGE_CACHE AVG_SYS_CPU MAX_SYS_CPU AVG_NET_RX MAX_NET_RX AVG_NET_TX MAX_NET_TX < <(calculate_metrics "$MONITOR_FILE")
    
    echo "  Results: CPU=${AVG_CPU}% Mem=${AVG_MEM_RSS}MB NetRX=${AVG_NET_RX}MB/s"
    gcloud storage cp -r "$TEST_DIR" "${RESULT_BASE}/"
    
    # Update Manifest
    if [ "$MANIFEST_ENTRY_TYPE" = "multi" ]; then
        TEST_PARAMS="{\"test_id\":\"$TEST_ID\",\"bs\":\"$BS\",\"file_size\":\"$FILE_SIZE\",\"io_depth\":\"$IO_DEPTH\",\"io_type\":\"$IO_TYPE\",\"threads\":\"$THREADS\",\"nrfiles\":\"$NRFILES\",\"config_id\":\"$CONFIG_ID\",\"config_label\":\"$CONFIG_LABEL\",\"commit\":\"$COMMIT\",\"mount_args\":\"$MOUNT_ARGS\",\"avg_cpu\":\"$AVG_CPU\",\"peak_cpu\":\"$MAX_CPU\",\"avg_mem_mb\":\"$AVG_MEM_RSS\",\"peak_mem_mb\":\"$MAX_MEM_RSS\",\"avg_page_cache_gb\":\"$AVG_PAGE_CACHE\",\"peak_page_cache_gb\":\"$MAX_PAGE_CACHE\",\"avg_sys_cpu\":\"$AVG_SYS_CPU\",\"peak_sys_cpu\":\"$MAX_SYS_CPU\",\"avg_net_rx_mbps\":\"$AVG_NET_RX\",\"peak_net_rx_mbps\":\"$MAX_NET_RX\",\"avg_net_tx_mbps\":\"$AVG_NET_TX\",\"peak_net_tx_mbps\":\"$MAX_NET_TX\"}"
        jq ".tests += [{\"matrix_id\":$MATRIX_ID,\"test_id\":$TEST_ID,\"config_id\":$CONFIG_ID,\"status\":\"success\",\"params\":$TEST_PARAMS}]" manifest.json > manifest_tmp.json
    else
        TEST_PARAMS="{\"bs\":\"$BS\",\"file_size\":\"$FILE_SIZE\",\"io_depth\":\"$IO_DEPTH\",\"io_type\":\"$IO_TYPE\",\"threads\":\"$THREADS\",\"nrfiles\":\"$NRFILES\",\"avg_cpu\":\"$AVG_CPU\",\"peak_cpu\":\"$MAX_CPU\",\"avg_mem_mb\":\"$AVG_MEM_RSS\",\"peak_mem_mb\":\"$MAX_MEM_RSS\",\"avg_page_cache_gb\":\"$AVG_PAGE_CACHE\",\"peak_page_cache_gb\":\"$MAX_PAGE_CACHE\",\"avg_sys_cpu\":\"$AVG_SYS_CPU\",\"peak_sys_cpu\":\"$MAX_SYS_CPU\",\"avg_net_rx_mbps\":\"$AVG_NET_RX\",\"peak_net_rx_mbps\":\"$MAX_NET_RX\",\"avg_net_tx_mbps\":\"$AVG_NET_TX\",\"peak_net_tx_mbps\":\"$MAX_NET_TX\"}"
        jq ".tests += [{\"test_id\":$TEST_ID,\"status\":\"success\",\"params\":$TEST_PARAMS}]" manifest.json > manifest_tmp.json
    fi
    mv manifest_tmp.json manifest.json
    return 0
}