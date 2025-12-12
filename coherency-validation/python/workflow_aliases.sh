#!/bin/bash

# Workflow Management Script
# Location: shared/coherency-validation/python/workflow_aliases.sh

# Resolve Script Directory
WORKFLOW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_CONFIG_FILE="$WORKFLOW_SCRIPT_DIR/workflow_config"

# --- Helper Functions ---

_get_workflow_sleep() {
    # Default to 15s if config doesn't exist or fails
    python3 -c "
import json, os
try:
    with open('$WORKFLOW_CONFIG_FILE') as f:
        print(json.load(f).get('sleep_seconds_after_shared_file_write', 15))
except:
    print(15)
"
}

_write_workflow_config() {
    name="$1"
    sharing="$2" # "True" or "False"
    
    # Determine the shared state directory based on sharing requirement
    if [ "$sharing" == "True" ]; then
        # Shared: config files go into the workflow's directory within 'shared/python'
        SHARED_STATE_DIR="$WORKFLOW_SCRIPT_DIR/$name"
    else
        # Not shared: config files go into the workflow's directory within 'tasks/python'
        # Assuming WORK_ROOT is correctly set, this needs to point to tasks/coherency-validation/python/<workflow_name>
        # This is a simplification; a more robust solution would read from the workflow's config.py
        # For now, we'll hardcode based on the overall project structure.
        PROJECT_ROOT="$(cd "$WORKFLOW_SCRIPT_DIR/../../.." && pwd)"
        SHARED_STATE_DIR="$PROJECT_ROOT/tasks/coherency-validation/python/$name"
        mkdir -p "$SHARED_STATE_DIR" # Ensure the directory exists
    fi

    # Define config file paths based on the SHARED_STATE_DIR
    WORKFLOW_SCENARIO_CONFIG_FILE="$SHARED_STATE_DIR/scenario_config"
    WORKFLOW_SPECIFIC_CONFIG_FILE="$SHARED_STATE_DIR/scenario_specific_config"
    WORKFLOW_GLOBAL_CONFIG_FILE="$SHARED_STATE_DIR/scenario_config" # Alias for backward compatibility
    
    python3 -c "
import json, os, time
# Load existing global config for sleep_seconds_after_shared_file_write if it exists
sleep_val = 15
config_path = os.environ.get('GLOBAL_CONFIG', '$WORKFLOW_CONFIG_FILE') # Default to workflow_config if not set
if os.path.exists(config_path):
    try:
        with open(config_path, 'r') as f:
            existing_data = json.load(f)
            sleep_val = existing_data.get('sleep_seconds_after_shared_file_write', 15)
    except:
        pass # Ignore errors, use default

data = {
    'workflow_name': '$name',
    'workflow_requires_cross_node_sharing': $sharing,
    'sleep_seconds_after_shared_file_write': sleep_val
}
with open('$WORKFLOW_CONFIG_FILE', 'w') as f:
    json.dump(data, f, indent=4)
"
    # Export new config file locations for the aliases to pick up
    export SCENARIO_FILE="$WORKFLOW_SPECIFIC_CONFIG_FILE"
    export SPECIFIC_CONFIG="$WORKFLOW_SPECIFIC_CONFIG_FILE"
    export GLOBAL_CONFIG="$WORKFLOW_GLOBAL_CONFIG_FILE"

    # Sleep to allow propagation if on shared filesystem
    sleep_time=$(_get_workflow_sleep)
    echo "Workflow config updated. Waiting ${sleep_time}s for consistency..."
    sleep "$sleep_time"
}

_clear_workflow_env() {
    # Unset Aliases
    unalias createfile createfilewith2ndcontent create2ndfile updatefile deletefile delete2ndfile 2>/dev/null
    unalias readfile readfileandfail read2ndfile read2ndfileandfail listfile listfileandfail 2>/dev/null
    unalias list2ndfile list2ndfileandfail createdir listdir listdirandfail deletedir renamedir 2>/dev/null
    unalias list2nddir renamefile createsymlink listsymlink readfromsymlink deletesymlink 2>/dev/null
    unalias listsymlinkandfail readfromsymlinkandfail movesymlink list2ndsymlink readfrom2ndsymlink 2>/dev/null
    unalias checkfilehasupdatedsize checkfilehasoriginalsize checkfilehasoriginalsizeandfail 2>/dev/null
    unalias readdirectfile readdirectfileandfail readdirect2ndfile writedirectfile 2>/dev/null
    unalias writedirectfilewithupdatedcontent writedirectfilewithoutflush writedirectfilewithoutsync 2>/dev/null
    unalias writefilewithoutsync writefilewithoutflush writefile writefilewithoutsyncorflush 2>/dev/null
    unalias abort_scenario complete_scenario enable_md_cache disable_md_cache root shared mount1 mount2 2>/dev/null
    unalias execute_scenario_complete execute_scenario_stepmode list_scenarios execution_mode 2>/dev/null
    
    # Unset Functions
    unset -f execute_scenario _execute_scenario_internal abort_current_scenario mark_scenario_completed fail_scenario _check_stepmode 2>/dev/null
    unset -f log_custom log_and_execute tail_logs enable_file_cache disable_file_cache 2>/dev/null
    unset -f enable_metadata_cache disable_metadata_cache enable_logging disable_logging 2>/dev/null
    unset -f set_sleep_seconds current_scenario current_logfile current_mount current_config 2>/dev/null
    unset -f _get_sleep_time _update_global_config _log_wrapper _interactive_wrapper _get_ts 2>/dev/null
    
    # Unset Environment Variables
    unset WORK_ROOT SHARED_ROOT MOUNT_ROOT BUCKET_NAME SCENARIO_FILE SPECIFIC_CONFIG GLOBAL_CONFIG FSOPS_SCRIPT PYTHON_MODULE_DIR SCRIPT_DIR
}

# --- Public Commands ---

current_workflow() {
    if [ -f "$WORKFLOW_CONFIG_FILE" ]; then
        python3 -c "import json; print(json.load(open('$WORKFLOW_CONFIG_FILE')).get('workflow_name', 'Unknown'))"
    else
        echo "No workflow configured."
    fi
}

set_workflow() {
    local choice="$1"
    
    if [ -z "$choice" ]; then
        echo "Select Workflow:"
        echo "1. dual_node_mounts (Shared Config)"
        echo "2. single_node_dual_mounts (Local Config)"
        echo "3. single_node_single_mount (Local Config)"
        
        read -p "Enter number (1-3): " choice
    fi
    
    local wf_name
    local wf_sharing
    
    case $choice in
        1)
            wf_name="dual_node_mounts"
            wf_sharing="True"
            ;;
        2)
            wf_name="single_node_dual_mounts"
            wf_sharing="False"
            ;;
        3)
            wf_name="single_node_single_mount"
            wf_sharing="False"
            ;;
        *)
            echo "Invalid selection: $choice"
            # If triggered interactively (no args), we could loop, but simply returning error is fine.
            # If arg provided was invalid, failing is correct.
            return 1
            ;;
    esac
    
    CURRENT_WF=$(python3 -c "import json; print(json.load(open('$WORKFLOW_CONFIG_FILE')).get('workflow_name', ''))" 2>/dev/null)
    
    if [ "$CURRENT_WF" == "$wf_name" ]; then
        echo "Already in workflow: $wf_name (Refreshing environment...)"
    else
        echo "Switching to workflow: $wf_name (Sharing: $wf_sharing)"
    fi
    
    # Clear environment before setting new workflow to avoid conflicts
    _clear_workflow_env

    # Write workflow_config (this will also export new SCENARIO_FILE, SPECIFIC_CONFIG, GLOBAL_CONFIG)
    _write_workflow_config "$wf_name" "$wf_sharing"
    
    # Source new environment
    TARGET_ALIASES="$WORKFLOW_SCRIPT_DIR/$wf_name/fsops_aliases.sh"
    if [ -f "$TARGET_ALIASES" ]; then
        source "$TARGET_ALIASES"
        echo "Workflow '$wf_name' activated."
    else
        echo "Error: Alias script not found at $TARGET_ALIASES"
    fi
}

echo "Workflow Management Loaded."
echo "Use 'set_workflow' to select a workflow."

# Auto-source active workflow if configured
if [ -f "$WORKFLOW_CONFIG_FILE" ]; then
    CURRENT_WF=$(python3 -c "import json; print(json.load(open('$WORKFLOW_CONFIG_FILE')).get('workflow_name', ''))" 2>/dev/null)
    if [ -n "$CURRENT_WF" ]; then
        WF_ALIASES="$WORKFLOW_SCRIPT_DIR/$CURRENT_WF/fsops_aliases.sh"
        if [ -f "$WF_ALIASES" ]; then
            export SILENT_LOAD=true
            source "$WF_ALIASES"
            unset SILENT_LOAD
        fi
    fi
fi
