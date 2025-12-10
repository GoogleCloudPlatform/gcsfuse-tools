import os
import subprocess
import logging
import tempfile
import json
from . import config

logger = logging.getLogger(__name__)

def read_scenario_config():
    """Reads cache settings from the shared config file."""
    config_file = os.path.join(os.path.dirname(config.SHARED_SCENARIO_FILE), "scenario_config")
    defaults = {
        "enable_file_cache": True,
        "enable_metadata_cache": True
    }
    
    if not os.path.exists(config_file):
        return defaults
        
    try:
        with open(config_file, "r") as f:
            data = json.load(f)
            # Merge with defaults just in case
            return {**defaults, **data}
    except Exception as e:
        logger.warning(f"Failed to read scenario_config: {e}. Using defaults.")
        return defaults

def mount(mount_number):
    bucket = config.BUCKET_NAME
    mount_path = config.MOUNT_PATH_TEMPLATE.format(mount_number)
    log_file = f"{mount_path}.log"
    
    # Ensure mount path exists
    os.makedirs(mount_path, exist_ok=True)
    
    # Create log file if not exists
    if not os.path.exists(log_file):
        with open(log_file, "w"): pass

    # Read dynamic config
    scenario_cfg = read_scenario_config()
    enable_file_cache = scenario_cfg.get("enable_file_cache", True)
    enable_md_cache = scenario_cfg.get("enable_metadata_cache", True)

    params = ["--implicit-dirs"]
    
    if enable_file_cache:
        cache_dir = tempfile.mkdtemp(prefix="gcsfuse-cache-dir-")
        params.extend(["--file-cache-max-size-mb", "-1"])
        params.extend(["--cache-dir", cache_dir])
    else:
        params.extend(["--file-cache-max-size-mb", "0"])
        # Pass empty string as cache dir
        params.extend(["--cache-dir", ""])

    if enable_md_cache:
        params.extend(["--metadata-cache-ttl-secs", "-1"])
        params.extend(["--type-cache-max-size-mb", "-1"])
        params.extend(["--stat-cache-max-size-mb", "-1"])
    else:
        params.extend(["--metadata-cache-ttl-secs", "0"])
        params.extend(["--type-cache-max-size-mb", "0"])
        params.extend(["--stat-cache-max-size-mb", "0"])

    params.extend(["--kernel-list-cache-ttl-secs", "0"])
    params.append("--metadata-cache-negative-ttl-secs=0")
    params.append(f"--log-file={log_file}")
    params.append("--log-severity=trace")
    params.append("--log-format=text")

    cmd = ["gcsfuse"] + params + [bucket, mount_path]
    
    cmd_str = f"Command: {' '.join(cmd)}"
    # Quiet Mode: Only printing the command as requested
    logger.info(cmd_str)
    
    # Log the command to the scenario log file if logging is enabled
    scenario_cfg = read_scenario_config()
    if scenario_cfg.get("logging_enabled", False):
        try:
            # We need to find where the specific config file is to get the log path
            # Using the same logic as execute_scenarios or fsops_aliases would be best,
            # but here we might just read the specific config file directly if we can locate it.
            specific_config_path = config.SHARED_SPECIFIC_CONFIG_FILE
            if os.path.exists(specific_config_path):
                with open(specific_config_path, "r") as f:
                    specific_data = json.load(f)
                    scenario_log_path = specific_data.get("log_file_path")
                    
                if scenario_log_path:
                    with open(scenario_log_path, "a") as scen_log:
                         scen_log.write(f"[{config.HOSTNAME}] {cmd_str}\n")
        except Exception as e:
            logger.warning(f"Failed to log mount command to scenario log: {e}")

    with open(log_file, "a") as log:
        try:
            subprocess.run(cmd, stdout=log, stderr=log, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Mount failed: {e}")
            raise

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python mount.py <mount_number>")
        sys.exit(1)
    mount(int(sys.argv[1]))
