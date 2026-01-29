#!/bin/bash

# Helper to calculate stats for a specific CSV column
# Usage: get_col_stats <file> <col_num>
# Returns: "AVG MAX"
get_col_stats() {
    local file=$1
    local col=$2
    awk -F',' -v c="$col" 'NR>1 {sum+=$c; if($c>max) max=$c; count++} END {if(count>0) printf "%.2f %.2f", sum/count, max+0; else print "0 0"}' "$file"
}

calculate_metrics() {
    local MONITOR_FILE=$1
    
    if [ ! -f "$MONITOR_FILE" ]; then
        echo "0" "0" "0" "0" "0" "0" "0" "0" "0" "0" "0" "0"
        return
    fi
    
    # Column mapping based on header:
    # timestamp(1), cpu(2), mem_rss(3), mem_vsz(4), page_cache(5), 
    # sys_cpu(6), net_rx(7), net_tx(8)
    
    # Using the helper function to reduce repetition
    read AVG_CPU MAX_CPU < <(get_col_stats "$MONITOR_FILE" 2)
    read AVG_MEM_RSS MAX_MEM_RSS < <(get_col_stats "$MONITOR_FILE" 3)
    read AVG_PAGE_CACHE MAX_PAGE_CACHE < <(get_col_stats "$MONITOR_FILE" 5)
    read AVG_SYS_CPU MAX_SYS_CPU < <(get_col_stats "$MONITOR_FILE" 6)
    read AVG_NET_RX MAX_NET_RX < <(get_col_stats "$MONITOR_FILE" 7)
    read AVG_NET_TX MAX_NET_TX < <(get_col_stats "$MONITOR_FILE" 8)
    
    echo "$AVG_CPU" "$MAX_CPU" "$AVG_MEM_RSS" "$MAX_MEM_RSS" "$AVG_PAGE_CACHE" "$MAX_PAGE_CACHE" "$AVG_SYS_CPU" "$MAX_SYS_CPU" "$AVG_NET_RX" "$MAX_NET_RX" "$AVG_NET_TX" "$MAX_NET_TX"
}

start_monitoring() {
    local GCSFUSE_PID=$1
    local MONITOR_FILE=$2
    local MONITOR_STOP_FLAG=$3
    local MONITOR_PID_FILE=$4
    
    rm -f "$MONITOR_STOP_FLAG"
    
    # Initialize previous tracking variables
    PREV_PROC_TIME=0; PREV_SYSTEM_TIME=0
    PREV_SYS_IDLE=0; PREV_SYS_ACTIVE=0; PREV_SYS_IOWAIT=0
    PREV_NET_RX=0; PREV_NET_TX=0
    
    {
        while [ ! -f "$MONITOR_STOP_FLAG" ]; do
            if [ -d "/proc/$GCSFUSE_PID" ]; then
                TIMESTAMP=$(date +%s)
                
                # --- CPU Calculation (Process) ---
                PROC_STAT=$(cat /proc/$GCSFUSE_PID/stat 2>/dev/null || echo "")
                if [ -n "$PROC_STAT" ]; then
                    PROC_UTIME=$(echo "$PROC_STAT" | awk '{print $14}')
                    PROC_STIME=$(echo "$PROC_STAT" | awk '{print $15}')
                    PROC_TIME=$((PROC_UTIME + PROC_STIME))
                    SYSTEM_STAT=$(head -1 /proc/stat)
                    SYSTEM_TIME=$(echo "$SYSTEM_STAT" | awk '{sum=0; for(i=2;i<=NF;i++) sum+=$i; print sum}')
                    
                    if [ $PREV_PROC_TIME -gt 0 ]; then
                        PROC_DELTA=$((PROC_TIME - PREV_PROC_TIME))
                        SYSTEM_DELTA=$((SYSTEM_TIME - PREV_SYSTEM_TIME))
                        if [ $SYSTEM_DELTA -gt 0 ]; then
                            CPU_PERCENT=$(echo "scale=2; ($PROC_DELTA * 100) / $SYSTEM_DELTA" | bc 2>/dev/null || echo "0")
                        else
                            CPU_PERCENT="0"
                        fi
                    else
                        CPU_PERCENT="0"
                    fi
                    PREV_PROC_TIME=$PROC_TIME
                    PREV_SYSTEM_TIME=$SYSTEM_TIME
                else
                    CPU_PERCENT="0"
                fi
                
                # --- Memory Stats ---
                MEM_RSS_KB=$(ps -p $GCSFUSE_PID -o rss= 2>/dev/null || echo "0")
                MEM_VSZ_KB=$(ps -p $GCSFUSE_PID -o vsz= 2>/dev/null || echo "0")
                MEM_RSS_MB=$(echo "scale=2; $MEM_RSS_KB / 1024" | bc 2>/dev/null || echo "0")
                MEM_VSZ_MB=$(echo "scale=2; $MEM_VSZ_KB / 1024" | bc 2>/dev/null || echo "0")
                
                # --- Page Cache ---
                PAGE_CACHE_KB=$(grep "^Cached:" /proc/meminfo | awk '{print $2}')
                PAGE_CACHE_GB=$(echo "scale=2; $PAGE_CACHE_KB / 1024 / 1024" | bc 2>/dev/null || echo "0")
                
                # --- Network Stats ---
                NET_STATS=$(awk 'NR>2 {rx+=$2; tx+=$10} END {print rx, tx}' /proc/net/dev 2>/dev/null || echo "0 0")
                NET_RX_BYTES=$(echo "$NET_STATS" | awk '{print $1}')
                NET_TX_BYTES=$(echo "$NET_STATS" | awk '{print $2}')
                
                if [ $PREV_NET_RX -gt 0 ]; then
                    NET_RX_DELTA=$((NET_RX_BYTES - PREV_NET_RX))
                    NET_TX_DELTA=$((NET_TX_BYTES - PREV_NET_TX))
                    NET_RX_MBPS=$(echo "scale=2; $NET_RX_DELTA / 2 / 1048576" | bc 2>/dev/null || echo "0")
                    NET_TX_MBPS=$(echo "scale=2; $NET_TX_DELTA / 2 / 1048576" | bc 2>/dev/null || echo "0")
                else
                    NET_RX_MBPS="0"; NET_TX_MBPS="0"
                fi
                PREV_NET_RX=$NET_RX_BYTES; PREV_NET_TX=$NET_TX_BYTES
                
                # --- System CPU ---
                SYS_STAT=$(head -1 /proc/stat)
                SYS_IDLE=$(echo "$SYS_STAT" | awk '{print $5}')
                SYS_IOWAIT=$(echo "$SYS_STAT" | awk '{print $6}')
                SYS_ACTIVE=$(echo "$SYS_STAT" | awk '{sum=0; for(i=2;i<=NF;i++) {if(i!=5 && i!=6) sum+=$i} print sum}')
                
                if [ $PREV_SYS_ACTIVE -gt 0 ]; then
                    SYS_IDLE_DELTA=$((SYS_IDLE - PREV_SYS_IDLE))
                    SYS_IOWAIT_DELTA=$((SYS_IOWAIT - PREV_SYS_IOWAIT))
                    SYS_ACTIVE_DELTA=$((SYS_ACTIVE - PREV_SYS_ACTIVE))
                    SYS_TOTAL_DELTA=$((SYS_ACTIVE_DELTA + SYS_IDLE_DELTA + SYS_IOWAIT_DELTA))
                    
                    if [ $SYS_TOTAL_DELTA -gt 0 ]; then
                        SYSTEM_CPU=$(echo "scale=2; ($SYS_ACTIVE_DELTA * 100) / $SYS_TOTAL_DELTA" | bc 2>/dev/null || echo "0")
                    else
                        SYSTEM_CPU="0"
                    fi
                else
                    SYSTEM_CPU="0"
                fi
                PREV_SYS_IDLE=$SYS_IDLE; PREV_SYS_IOWAIT=$SYS_IOWAIT; PREV_SYS_ACTIVE=$SYS_ACTIVE
                
                echo "$TIMESTAMP,$CPU_PERCENT,$MEM_RSS_MB,$MEM_VSZ_MB,$PAGE_CACHE_GB,$SYSTEM_CPU,$NET_RX_MBPS,$NET_TX_MBPS" >> "$MONITOR_FILE"
            fi
            sleep 2
        done
    } &
    
    echo $! > "$MONITOR_PID_FILE"
}

stop_monitoring() {
    local MONITOR_PID=$1
    local MONITOR_STOP_FLAG=$2
    touch "$MONITOR_STOP_FLAG"
    sleep 1
    kill $MONITOR_PID 2>/dev/null || true
    wait $MONITOR_PID 2>/dev/null || true
}