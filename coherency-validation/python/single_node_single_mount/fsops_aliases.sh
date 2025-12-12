#!/bin/bash

# Locate the python script directory relative to this script
# Script is in shared/coherency-validation/python/single_node_single_mount
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# PYTHON_MODULE_DIR is the parent of single_node_single_mount (i.e., python/)
PYTHON_MODULE_DIR="$(dirname "$SCRIPT_DIR")"

# FSOPS_SCRIPT is in python/fsops.py
FSOPS_SCRIPT="$PYTHON_MODULE_DIR/fsops.py"

# Detect Project Root (Grandparent of Grandparent of script dir)
# .../shared/coherency-validation/python -> .../
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"

# Source env if not already
if [ -z "$VIRTUAL_ENV" ]; then
    if [ -f "$VENV_DIR/bin/activate" ]; then
        source "$VENV_DIR/bin/activate"
    fi
fi

export PYTHONDONTWRITEBYTECODE=1
REQUIRE_SHARED_SLEEP=false

if [ -z "$SILENT_LOAD" ]; then
    echo "Setting up fsops aliases..."
    echo "  Script Dir: $SCRIPT_DIR"
    echo "  Python Dir: $PYTHON_MODULE_DIR"
    echo "  Fsops Script: $FSOPS_SCRIPT"
fi

# --- Dynamic Path Resolution ---
export PYTHONPATH="$PYTHON_MODULE_DIR:$PYTHONPATH"

PATHS_JSON=$(python3 -c '
import sys
import os
# Explicitly add the module dir passed as argument
sys.path.insert(0, sys.argv[1])
try:
    from single_node_single_mount import config
    import json
    
    shared_state_dir = config.SHARED_STATE_DIR
    config_file = os.path.join(shared_state_dir, "scenario_config")
    
    print(json.dumps({
        "WORK_DIR": config.WORK_DIR,
        "SHARED_ROOT": config.SHARED_ROOT_BASE, 
        "MOUNT_PATH": config.MOUNT_PATH,
        "MOUNT_ROOT": config.MOUNT_ROOT,
        "BUCKET_NAME": config.BUCKET_NAME,
        "SCENARIO_FILE": config.SHARED_SCENARIO_FILE,
        "SPECIFIC_CONFIG": config.SHARED_SPECIFIC_CONFIG_FILE,
        "GLOBAL_CONFIG": config.SHARED_GLOBAL_CONFIG_FILE
    }))
except ImportError as e:
    print(f"Error importing config: {e}", file=sys.stderr)
    sys.exit(1)
' "$PYTHON_MODULE_DIR")

if [ $? -ne 0 ]; then
    echo "Error: Failed to resolve paths from Python config. Aliases might be broken."
else
    WORK_ROOT=$(echo "$PATHS_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin)['WORK_DIR'])")
    SHARED_ROOT=$(echo "$PATHS_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin)['SHARED_ROOT'])")
    MOUNT_PATH=$(echo "$PATHS_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin)['MOUNT_PATH'])")
    MOUNT_ROOT=$(echo "$PATHS_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin)['MOUNT_ROOT'])")
    BUCKET_NAME=$(echo "$PATHS_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin)['BUCKET_NAME'])")
    SCENARIO_FILE=$(echo "$PATHS_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin)['SCENARIO_FILE'])")
    SPECIFIC_CONFIG=$(echo "$PATHS_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin)['SPECIFIC_CONFIG'])")
    GLOBAL_CONFIG=$(echo "$PATHS_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin)['GLOBAL_CONFIG'])")
fi

# --- Helpers ---

_get_sleep_time() {
    python3 -c "
import json, os
try:
    with open('$GLOBAL_CONFIG') as f:
        print(json.load(f).get('sleep_seconds_after_shared_file_write', 15))
except:
    print(15)
"
}

_update_global_config() {
    python3 -c "
import json, os, sys
key, val, path = sys.argv[1], sys.argv[2], sys.argv[3]
if val.lower() == 'true': val = True
elif val.lower() == 'false': val = False
elif val.isdigit(): val = int(val)

data = {}
if os.path.exists(path):
    try:
        with open(path, 'r') as f: data = json.load(f)
    except: pass
data[key] = val
try:
    with open(path, 'w') as f: json.dump(data, f, indent=4)
except Exception as e: print(f'Error: {e}')
" "$1" "$2" "$GLOBAL_CONFIG"
    
    if [ "$REQUIRE_SHARED_SLEEP" = "true" ]; then
        sleep_time=$(_get_sleep_time)
        echo "Updated config. Waiting ${sleep_time}s..."
        sleep "$sleep_time"
    else
        echo "Updated config."
    fi
}

_check_stepmode() {
    # Returns 0 if in stepmode (or config missing), 1 if in complete mode
    python3 -c "
import json, os, sys
try:
    if os.path.exists('$SPECIFIC_CONFIG'):
        with open('$SPECIFIC_CONFIG') as f:
            data = json.load(f)
            mode = data.get('execution_mode', 'stepmode')
            if mode != 'stepmode':
                sys.exit(1)
except:
    pass
"
}

# --- Logging Wrapper ---
_log_wrapper() {
    if ! _check_stepmode; then
        echo "Error: Manual operations are not allowed in 'complete' execution mode." >&2
        return 1
    fi

    LOG_INFO=$(python3 -c "
import json, os, sys
global_path = '$GLOBAL_CONFIG'
specific_path = '$SPECIFIC_CONFIG'
enabled = False
log_file = ''

if os.path.exists(global_path):
    try:
        with open(global_path, 'r') as f: enabled = json.load(f).get('logging_enabled', False)
    except: pass

if enabled and os.path.exists(specific_path):
    try:
        with open(specific_path, 'r') as f: log_file = json.load(f).get('log_file_path', '')
    except: pass

print(f'{enabled}|{log_file}')
")

    IFS='|' read -r LOGGING_ENABLED LOG_FILE <<< "$LOG_INFO"
    
    # Create a temporary file for output capture
    TMP_OUT=$(mktemp)
    
    # Run the command, capturing output to temp file and stdout simultaneously
    # We use tee to stream while capturing. We rely on PIPESTATUS to get the exit code.
    
    "$@" 2>&1 | tee "$TMP_OUT"
    RET_CODE=${PIPESTATUS[0]}
    
    if [ "$LOGGING_ENABLED" == "True" ] && [ -n "$LOG_FILE" ]; then
        # Retry loop for writing to shared log file
        MAX_RETRIES=3
        COUNT=0
        SUCCESS=false
        
        while [ $COUNT -lt $MAX_RETRIES ]; do
            if cat "$TMP_OUT" >> "$LOG_FILE"; then
                SUCCESS=true
                break
            else
                echo "Warning: Failed to write to log file. Retrying ($((COUNT+1))/$MAX_RETRIES)..." >&2
                sleep 15
                COUNT=$((COUNT+1))
            fi
        done
        
        if [ "$SUCCESS" = false ]; then
             echo "Error: Failed to write to log file after $MAX_RETRIES attempts." >&2
        fi
        
        if [ "$REQUIRE_SHARED_SLEEP" = "true" ]; then
            sleep_time=$(_get_sleep_time)
            sleep "$sleep_time"
        fi
    fi
    
    rm -f "$TMP_OUT"
    return $RET_CODE
}

_interactive_wrapper() {
    if ! _check_stepmode; then
        echo "Error: Manual operations are not allowed in 'complete' execution mode." >&2
        return 1
    fi

    # Resolve Log File Path for user instruction & logging
    LOG_INFO=$(python3 -c "
import json, os
try:
    enabled = json.load(open('$GLOBAL_CONFIG')).get('logging_enabled', False)
    log_file = json.load(open('$SPECIFIC_CONFIG')).get('log_file_path', '')
    print(f'{enabled}|{log_file}')
except: print('False|')
")
    IFS='|' read -r LOGGING_ENABLED LOG_FILE <<< "$LOG_INFO"
    
    if [ "$LOGGING_ENABLED" == "True" ] && [ -n "$LOG_FILE" ]; then
        echo -e "\n\033[1;33mATTENTION: This command is interactive. Output will be logged to: $LOG_FILE\033[0m\n only after the whole command finishes." >&2
    fi
    
    # Temp file for capturing output
    TMP_INTERACTIVE_OUT=$(mktemp)
    
    # Ignore SIGINT in the wrapper so we can handle cleanup/logging
    trap '' INT
    
    # Run pipeline:
    # 1. Python (subshell restores INT): Receives SIGINT, handles it, exits 0.
    # 2. Tee (subshell ignores INT): Ignores SIGINT, waits for EOF, captures all output.
    ( trap - INT; "$@" 2>&1 ) | ( trap '' INT; tee "$TMP_INTERACTIVE_OUT" )
    RET_CODE=${PIPESTATUS[0]}
    
    # Restore SIGINT in wrapper
    trap - INT
    
    # Append to shared log file if enabled
    if [ "$LOGGING_ENABLED" == "True" ] && [ -n "$LOG_FILE" ]; then
        MAX_RETRIES=3
        COUNT=0
        SUCCESS=false
        
        while [ $COUNT -lt $MAX_RETRIES ]; do
            if cat "$TMP_INTERACTIVE_OUT" >> "$LOG_FILE"; then
                SUCCESS=true
                break
            else
                echo "Warning: Failed to write to log file. Retrying ($((COUNT+1))/$MAX_RETRIES)..." >&2
                sleep 15
                COUNT=$((COUNT+1))
            fi
        done
        
        if [ "$SUCCESS" = false ]; then
             echo "Error: Failed to write to log file after $MAX_RETRIES attempts." >&2
        fi
        
        if [ "$REQUIRE_SHARED_SLEEP" = "true" ]; then
            sleep_time=$(_get_sleep_time)
            sleep "$sleep_time"
        fi
    fi
    
    rm -f "$TMP_INTERACTIVE_OUT"
    return $RET_CODE
}

# --- File System Operations Aliases ---
alias createfile='_log_wrapper python3 "$FSOPS_SCRIPT" createfile'
alias createfilewith2ndcontent='_log_wrapper python3 "$FSOPS_SCRIPT" createfilewith2ndcontent'
alias create2ndfile='_log_wrapper python3 "$FSOPS_SCRIPT" create2ndfile'
alias updatefile='_log_wrapper python3 "$FSOPS_SCRIPT" updatefile'
alias deletefile='_log_wrapper python3 "$FSOPS_SCRIPT" deletefile'
alias delete2ndfile='_log_wrapper python3 "$FSOPS_SCRIPT" delete2ndfile'
alias readfile='_log_wrapper python3 "$FSOPS_SCRIPT" readfile'
alias readfileandfail='_log_wrapper python3 "$FSOPS_SCRIPT" readfileandfail'
alias readfilehasupdatedcontent='_log_wrapper python3 "$FSOPS_SCRIPT" readfilehasupdatedcontent'
alias readfilehasoriginalcontent='_log_wrapper python3 "$FSOPS_SCRIPT" readfilehasoriginalcontent'
alias read2ndfile='_log_wrapper python3 "$FSOPS_SCRIPT" read2ndfile'
alias read2ndfileandfail='_log_wrapper python3 "$FSOPS_SCRIPT" read2ndfileandfail'
alias listfile='_log_wrapper python3 "$FSOPS_SCRIPT" listfile'
alias listfileandfail='_log_wrapper python3 "$FSOPS_SCRIPT" listfileandfail'
alias list2ndfile='_log_wrapper python3 "$FSOPS_SCRIPT" list2ndfile'
alias list2ndfileandfail='_log_wrapper python3 "$FSOPS_SCRIPT" list2ndfileandfail'
alias createdir='_log_wrapper python3 "$FSOPS_SCRIPT" createdir'
alias listdir='_log_wrapper python3 "$FSOPS_SCRIPT" listdir'
alias listdirandfail='_log_wrapper python3 "$FSOPS_SCRIPT" listdirandfail'
alias deletedir='_log_wrapper python3 "$FSOPS_SCRIPT" deletedir'
alias renamedir='_log_wrapper python3 "$FSOPS_SCRIPT" renamedir'
alias list2nddir='_log_wrapper python3 "$FSOPS_SCRIPT" list2nddir'
alias renamefile='_log_wrapper python3 "$FSOPS_SCRIPT" renamefile'
alias createsymlink='_log_wrapper python3 "$FSOPS_SCRIPT" createsymlink'
alias listsymlink='_log_wrapper python3 "$FSOPS_SCRIPT" listsymlink'
alias readfromsymlink='_log_wrapper python3 "$FSOPS_SCRIPT" readfromsymlink'
alias deletesymlink='_log_wrapper python3 "$FSOPS_SCRIPT" deletesymlink'
alias listsymlinkandfail='_log_wrapper python3 "$FSOPS_SCRIPT" listsymlinkandfail'
alias readfromsymlinkandfail='_log_wrapper python3 "$FSOPS_SCRIPT" readfromsymlinkandfail'
alias movesymlink='_log_wrapper python3 "$FSOPS_SCRIPT" movesymlink'
alias list2ndsymlink='_log_wrapper python3 "$FSOPS_SCRIPT" list2ndsymlink'
alias readfrom2ndsymlink='_log_wrapper python3 "$FSOPS_SCRIPT" readfrom2ndsymlink'
alias checkfilehasupdatedsize='_log_wrapper python3 "$FSOPS_SCRIPT" checkfilehasupdatedsize'
alias checkfilehasoriginalsize='_log_wrapper python3 "$FSOPS_SCRIPT" checkfilehasoriginalsize'
alias checkfilehasoriginalsizeandfail='_log_wrapper python3 "$FSOPS_SCRIPT" checkfilehasoriginalsizeandfail'
alias readdirectfile='_log_wrapper python3 "$FSOPS_SCRIPT" readdirectfile'
alias readdirectfileandfail='_log_wrapper python3 "$FSOPS_SCRIPT" readdirectfileandfail'
alias readdirectfilehasupdatedcontent='_log_wrapper python3 "$FSOPS_SCRIPT" readdirectfilehasupdatedcontent'
alias readdirectfilehasoriginalcontent='_log_wrapper python3 "$FSOPS_SCRIPT" readdirectfilehasoriginalcontent'
alias readdirect2ndfile='_log_wrapper python3 "$FSOPS_SCRIPT" readdirect2ndfile'
alias writedirectfile='_log_wrapper python3 "$FSOPS_SCRIPT" writedirectfile'
alias writedirectfilewithupdatedcontent='_log_wrapper python3 "$FSOPS_SCRIPT" writedirectfilewithupdatedcontent'
alias writedirectfilewithoutflush='_interactive_wrapper python3 "$FSOPS_SCRIPT" writedirectfilewithoutflush'
alias writedirectfilewithoutsync='_log_wrapper python3 "$FSOPS_SCRIPT" writedirectfilewithoutsync'
alias writefilewithoutsync='_log_wrapper python3 "$FSOPS_SCRIPT" writefilewithoutsync'
alias writefilewithoutflush='_interactive_wrapper python3 "$FSOPS_SCRIPT" writefilewithoutflush'
alias writefile='_log_wrapper python3 "$FSOPS_SCRIPT" writefile'
alias writefilewithoutsyncorflush='_interactive_wrapper python3 "$FSOPS_SCRIPT" writefilewithoutsyncorflush'

# --- Workflow Aliases & Navigation ---

alias root='cd "$WORK_ROOT"'
alias shared='cd "$SHARED_ROOT"'
alias mountdir='cd "$MOUNT_PATH"'

_get_ts() {
    python3 -c 'import time; print("{:.9f}".format(time.time()))'
}

execute_scenario() {
    local start_dir="$PWD"
    # cd to the PYTHON_MODULE_DIR which contains the packages
    cd "$PYTHON_MODULE_DIR"
    python3 -m single_node_single_mount.execute_scenarios "$@"
    local ret=$?
    
    if [ $ret -eq 0 ] && [ -d "$MOUNT_PATH" ]; then
        # On success, switch to the mount directory to facilitate testing
        cd "$MOUNT_PATH"
    else
        # On failure, return to where we started
        cd "$start_dir"
    fi
}

abort_current_scenario() {
    if ! _check_stepmode; then
        echo "Error: Manual operations are not allowed in 'complete' execution mode." >&2
        return 1
    fi
    if [ ! -f "$SPECIFIC_CONFIG" ]; then
        echo "Error: No scenario currently running."
        return 1
    fi
    ts=$(_get_ts)
    hn=$(hostname)
    dn=$(basename "$PWD")
    scenario_name=$(python3 -c "import json; print(json.load(open('$SPECIFIC_CONFIG')).get('scenario_name', ''))" 2>/dev/null)
    
    # Log if enabled
    LOG_INFO=$(python3 -c "
import json, os
try:
    enabled = json.load(open('$GLOBAL_CONFIG')).get('logging_enabled', False)
    log_file = json.load(open('$SPECIFIC_CONFIG')).get('log_file_path', '')
    print(f'{enabled}|{log_file}')
except: print('False|')
")
    IFS='|' read -r LOGGING_ENABLED LOG_FILE <<< "$LOG_INFO"
    
    msg="[$ts] \$ Aborted scenario: \"$scenario_name\""
    
    # Construct message block
    MSG_BLOCK=$(printf "\n\n/////////////////////////////////////////////////////////////////////////////////////////\n%s\n//////////////////////////////////////////////////////////////////////////////////////////////\n\n\n" "$msg")
    
    # Print to stdout
    echo "$MSG_BLOCK"
    
    if [ "$LOGGING_ENABLED" == "True" ] && [ -n "$LOG_FILE" ]; then
        MAX_RETRIES=3
        COUNT=0
        SUCCESS=false
        
        while [ $COUNT -lt $MAX_RETRIES ]; do
            if echo "$MSG_BLOCK" >> "$LOG_FILE"; then
                SUCCESS=true
                break
            else
                echo "Warning: Failed to write to log file. Retrying ($((COUNT+1))/$MAX_RETRIES)..." >&2
                sleep 15
                COUNT=$((COUNT+1))
            fi
        done
        
        if [ "$SUCCESS" = false ]; then
             echo "Error: Failed to write to log file after $MAX_RETRIES attempts." >&2
        fi
    fi
    
    rm -f "$SPECIFIC_CONFIG"
    
    if [ "$REQUIRE_SHARED_SLEEP" = "true" ]; then
        sleep_time=$(_get_sleep_time)
        sleep "$sleep_time"
    fi
    
    cd "$WORK_ROOT"
}

mark_scenario_completed() {
    if ! _check_stepmode; then
        echo "Error: Manual operations are not allowed in 'complete' execution mode." >&2
        return 1
    fi
    if [ ! -f "$SPECIFIC_CONFIG" ]; then
        echo "Error: No scenario currently running."
        return 1
    fi
    ts=$(_get_ts)
    hn=$(hostname)
    dn=$(basename "$PWD")
    scenario_name=$(python3 -c "import json; print(json.load(open('$SPECIFIC_CONFIG')).get('scenario_name', ''))" 2>/dev/null)
    
    # Log if enabled
    LOG_INFO=$(python3 -c "
import json, os
try:
    enabled = json.load(open('$GLOBAL_CONFIG')).get('logging_enabled', False)
    log_file = json.load(open('$SPECIFIC_CONFIG')).get('log_file_path', '')
    print(f'{enabled}|{log_file}')
except: print('False|')
")
    IFS='|' read -r LOGGING_ENABLED LOG_FILE <<< "$LOG_INFO"
    
    msg="[$ts] \$ Ended scenario: \"$scenario_name\""
    
    # Construct message block
    MSG_BLOCK=$(printf "\n\n/////////////////////////////////////////////////////////////////////////////////////////\n%s\n//////////////////////////////////////////////////////////////////////////////////////////////\n\n\n" "$msg")
    
    # Print to stdout
    echo "$MSG_BLOCK"
    
    if [ "$LOGGING_ENABLED" == "True" ] && [ -n "$LOG_FILE" ]; then
        MAX_RETRIES=3
        COUNT=0
        SUCCESS=false
        
        while [ $COUNT -lt $MAX_RETRIES ]; do
            if echo "$MSG_BLOCK" >> "$LOG_FILE"; then
                SUCCESS=true
                break
            else
                echo "Warning: Failed to write to log file. Retrying ($((COUNT+1))/$MAX_RETRIES)..." >&2
                sleep 15
                COUNT=$((COUNT+1))
            fi
        done
        
        if [ "$SUCCESS" = false ]; then
             echo "Error: Failed to write to log file after $MAX_RETRIES attempts." >&2
        fi
    fi
    
    rm -f "$SPECIFIC_CONFIG"
    sleep_time=$(_get_sleep_time)
    sleep "$sleep_time"
    
    if [ "$REQUIRE_SHARED_SLEEP" = "true" ]; then
        sleep_time=$(_get_sleep_time)
        sleep "$sleep_time"
    fi
    
    cd "$WORK_ROOT"
}

fail_scenario() {
    if ! _check_stepmode; then
        echo "Error: Manual operations are not allowed in 'complete' execution mode." >&2
        return 1
    fi
    if [ ! -f "$SPECIFIC_CONFIG" ]; then
        echo "Error: No scenario currently running."
        return 1
    fi
    ts=$(_get_ts)
    hn=$(hostname)
    dn=$(basename "$PWD")
    scenario_name=$(python3 -c "import json; print(json.load(open('$SPECIFIC_CONFIG')).get('scenario_name', ''))" 2>/dev/null)
    
    # Log if enabled
    LOG_INFO=$(python3 -c "
import json, os
try:
    enabled = json.load(open('$GLOBAL_CONFIG')).get('logging_enabled', False)
    log_file = json.load(open('$SPECIFIC_CONFIG')).get('log_file_path', '')
    print(f'{enabled}|{log_file}')
except: print('False|')
")
    IFS='|' read -r LOGGING_ENABLED LOG_FILE <<< "$LOG_INFO"
    
    msg="[$ts] \$ Failed scenario: \"$scenario_name\""
    
    # Construct message block
    MSG_BLOCK=$(printf "\n\n/////////////////////////////////////////////////////////////////////////////////////////\n%s\n//////////////////////////////////////////////////////////////////////////////////////////////\n\n\n" "$msg")
    
    # Print to stdout
    echo "$MSG_BLOCK"
    
    if [ "$LOGGING_ENABLED" == "True" ] && [ -n "$LOG_FILE" ]; then
        MAX_RETRIES=3
        COUNT=0
        SUCCESS=false
        
        while [ $COUNT -lt $MAX_RETRIES ]; do
            if echo "$MSG_BLOCK" >> "$LOG_FILE"; then
                SUCCESS=true
                break
            else
                echo "Warning: Failed to write to log file. Retrying ($((COUNT+1))/$MAX_RETRIES)..." >&2
                sleep 15
                COUNT=$((COUNT+1))
            fi
        done
        
        if [ "$SUCCESS" = false ]; then
             echo "Error: Failed to write to log file after $MAX_RETRIES attempts." >&2
        fi
    fi
    
    rm -f "$SPECIFIC_CONFIG"
    sleep_time=$(_get_sleep_time)
    sleep "$sleep_time"
    
    if [ "$REQUIRE_SHARED_SLEEP" = "true" ]; then
        sleep_time=$(_get_sleep_time)
        sleep "$sleep_time"
    fi
    
    cd "$WORK_ROOT"
}

alias execute_scenario_complete='execute_scenario --complete'
alias execute_scenario_stepmode='execute_scenario --stepmode'
alias list_scenarios='python3 -m single_node_single_mount.execute_scenarios --list'

alias abort_scenario=abort_current_scenario
alias complete_scenario=mark_scenario_completed
alias complete_scenario=mark_scenario_completed

execution_mode() {
    python3 -c "
import json, os
try:
    if os.path.exists('$SPECIFIC_CONFIG'):
        with open('$SPECIFIC_CONFIG') as f:
            print(json.load(f).get('execution_mode', 'Unknown'))
    else:
        print('No scenario running.')
except:
    print('Error reading config.')
"
}

# Config Toggles

enable_file_cache() { _update_global_config "enable_file_cache" "true"; echo "File Cache: ENABLED (Updated $GLOBAL_CONFIG)"; }
disable_file_cache() { _update_global_config "enable_file_cache" "false"; echo "File Cache: DISABLED (Updated $GLOBAL_CONFIG)"; }

enable_metadata_cache() { _update_global_config "enable_metadata_cache" "true"; echo "Metadata Cache: ENABLED (Updated $GLOBAL_CONFIG)"; }
disable_metadata_cache() { _update_global_config "enable_metadata_cache" "false"; echo "Metadata Cache: DISABLED (Updated $GLOBAL_CONFIG)"; }

enable_logging() { _update_global_config "logging_enabled" "true"; echo "Logging: ENABLED"; }
disable_logging() { _update_global_config "logging_enabled" "false"; echo "Logging: DISABLED"; }

set_sleep_seconds() { _update_global_config "sleep_seconds_after_shared_file_write" "$1"; echo "Sleep time set to $1s"; }

alias enable_md_cache=enable_metadata_cache
alias disable_md_cache=disable_metadata_cache

current_scenario() {
    if [ -f "$SPECIFIC_CONFIG" ]; then
        python3 -c "import json; print(json.load(open('$SPECIFIC_CONFIG')).get('scenario_name', ''))"
    else
        echo "No scenario currently running."
    fi
}

current_logfile() {
    if [ -f "$SPECIFIC_CONFIG" ]; then
        python3 -c "import json; print(json.load(open('$SPECIFIC_CONFIG')).get('log_file_path', ''))"
    else
        echo "No scenario running."
    fi
}

current_mount() {
    # For single mount workflow, there is only one mount
    echo "Current Mount Path: $MOUNT_PATH"
}

current_config() {
    python3 -c "
import json, os
config_path = '$GLOBAL_CONFIG'
defaults = {
    'enable_file_cache': True, 
    'enable_metadata_cache': True, 
    'logging_enabled': False,
    'sleep_seconds_after_shared_file_write': 15
}
data = {}
if os.path.exists(config_path):
    try:
        with open(config_path) as f: data = json.load(f)
    except: pass

merged = {**defaults, **data}
print('Current Configuration:')
for k, v in merged.items():
    print(f'  {k}: {v}')
"
}

show_fsops_help() {
    echo "Aliases loaded. Paths resolved:"
    echo "  - Work Root: $WORK_ROOT"
    echo "  - Shared Root: $SHARED_ROOT"
    echo "  - Mount Path: $MOUNT_PATH"
    echo "  - Config File: $GLOBAL_CONFIG"
    echo "You can now use:"
    echo "  - FS Ops: createfile, listfile, ..."
    echo "  - Nav: root, shared, mountdir"
    echo "  - Flow: execute_scenario_complete <id>, execute_scenario_stepmode <id>, abort_scenario, complete_scenario, fail_scenario"
    echo "  - Manual: log_custom, log_and_execute"
    echo "  - Config: enable/disable_file_cache, enable/disable_metadata_cache, enable/disable_logging, set_sleep_seconds"
    echo "  - Status: current_scenario, current_logfile, tail_logs, current_mount, current_config"
}

if [ -z "$SILENT_LOAD" ]; then
    show_fsops_help
fi

alias workflow_aliases=show_fsops_help