import subprocess
import logging
import glob
import shutil
from . import config

logger = logging.getLogger(__name__)

def empty_bucket(bucket_name):
    # gcloud storage ls gs://$bucket/
    check_cmd = ["gcloud", "--no-user-output-enabled", "-q", "storage", "ls", f"gs://{bucket_name}/"]
    try:
        subprocess.run(check_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # If successful, bucket exists/has items
        rm_cmd = ["gcloud", "--no-user-output-enabled", "-q", "storage", "rm", "-r", f"gs://{bucket_name}/*"]
        # logger.info(f"Emptying bucket {bucket_name}...")
        subprocess.run(rm_cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        # ls failed, maybe empty or permission issue
        pass

def cleanup():
    empty_bucket(config.BUCKET_NAME)
    
    # rm -rf /tmp/gcsfuse-cache-dir-*
    # Using glob to find matching directories
    cache_dirs = glob.glob("/tmp/gcsfuse-cache-dir-*")
    for d in cache_dirs:
        try:
            shutil.rmtree(d)
            # logger.info(f"Removed cache dir: {d}")
        except OSError as e:
            logger.warning(f"Failed to remove {d}: {e}")

if __name__ == "__main__":
    cleanup()
