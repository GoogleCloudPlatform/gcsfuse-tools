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
        fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as log:
            subprocess.run(cmd, stdout=log, stderr=log, check=False)
    except Exception as e:
        logger.warning(f"Unmount failed (might not be mounted) or log open error: {e}")

if __name__ == "__main__":
    unmount()
