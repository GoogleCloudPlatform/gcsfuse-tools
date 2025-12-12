import os
import subprocess
import logging
import tempfile
import json
import time
import socket
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
    
    timestamp = "{:.9f}".format(time.time())
    hostname = socket.gethostname()
    
    cmd_str = f"[{timestamp}] [{hostname}] $ {' '.join(cmd)}"
    logger.info(cmd_str)

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
