import os
import subprocess
import logging
from . import config

logger = logging.getLogger(__name__)

def unmount(mount_number=None):
    mount_path = config.MOUNT_PATH
    log_file = f"{mount_path}.log"
    
    # logger.info(f"Unmounting {mount_path}...")
    
    cmd = ["fusermount", "-uz", mount_path]
    
    try:
        with open(log_file, "a") as log:
            subprocess.run(cmd, stdout=log, stderr=log, check=False)
    except Exception as e:
        logger.warning(f"Unmount failed (might not be mounted): {e}")

if __name__ == "__main__":
    unmount()
