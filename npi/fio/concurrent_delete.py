#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys
import shutil
import logging
from concurrent.futures import ThreadPoolExecutor

# Import the Google Cloud Storage client
try:
    from google.cloud import storage
    GCS_SDK_AVAILABLE = True
except ImportError:
    GCS_SDK_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def resolve_bucket_and_prefix(target_path):
    """Parses /proc/mounts to find the GCS bucket name and relative prefix for the target path."""
    target_path = os.path.realpath(target_path)
    mount_point = None
    bucket_name = None
    
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    dev, mnt, fstype = parts[0], parts[1], parts[2]
                    # GCSFuse mounts are identified by fuse.gcsfuse fstype or gcsfuse in dev name
                    if "gcsfuse" in fstype or "gcsfuse" in dev:
                        real_mnt = os.path.realpath(mnt)
                        # Check if the target path is inside this GCSFuse mount point
                        if target_path == real_mnt or target_path.startswith(real_mnt + "/"):
                            mount_point = real_mnt
                            # Extract bucket name (strip any prefix like gcsfuse# if present)
                            bucket_name = dev.split('#')[-1]
                            break
    except Exception as e:
        logging.warning(f"Failed to parse /proc/mounts: {e}")
        
    if not mount_point or not bucket_name:
        return None, None
        
    # Calculate relative GCS prefix from mount point
    rel_path = os.path.relpath(target_path, mount_point)
    if rel_path == "." or rel_path == "..":
        prefix = ""
    else:
        prefix = rel_path.rstrip('/') + '/'
        
    return bucket_name, prefix

def main():
    # Usage: concurrent_delete.py <target_directory> [<file_size> <block_size> <nr_files>]
    if len(sys.argv) < 2:
        logging.error("Usage: concurrent_delete.py <target_directory> [<file_size> <block_size> <nr_files>]")
        sys.exit(1)

    target_dir = sys.argv[1]
    
    # 1. Clean up old files via high-speed GCS Batch API if SDK is available
    bucket_name, prefix = resolve_bucket_and_prefix(target_dir)
    
    if GCS_SDK_AVAILABLE and bucket_name and prefix:
        logging.info(f"Resolved GCS Bucket: {bucket_name}, Prefix: {prefix}")
        try:
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            # List all blobs under the prefix (including empty directory objects)
            blobs = list(bucket.list_blobs(prefix=prefix))
            
            if blobs:
                logging.info(f"Deleting {len(blobs)} GCS objects concurrently via GCS API...")
                # Chunk into batches of 100 for GCS batch delete API limit
                chunks = [blobs[i:i + 100] for i in range(0, len(blobs), 100)]
                
                def delete_chunk(chunk):
                    try:
                        bucket.delete_blobs(chunk)
                        return True
                    except Exception as e:
                        logging.error(f"Failed to delete batch: {e}")
                        return False
                        
                # Delete concurrently using a ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=16) as executor:
                    results = list(executor.map(delete_chunk, chunks))
                
                if not all(results):
                    logging.error("Failed to delete one or more batches via GCS API. Exiting with error.")
                    sys.exit(1)
                    
                logging.info("Successfully deleted all GCS objects via API.")
            else:
                logging.info("No GCS objects found to delete under prefix.")
        except Exception as e:
            logging.error(f"Failed to delete via GCS API: {e}. Falling back to FUSE deletion...")
            if os.path.exists(target_dir):
                try:
                    shutil.rmtree(target_dir)
                except Exception as ex:
                    logging.error(f"FUSE recursive deletion failed: {ex}")
                    sys.exit(1)
    else:
        # Fallback to local FUSE deletion if GCS API/SDK is not available
        logging.warning(f"GCS SDK not available or bucket could not be resolved for {target_dir}. Falling back to FUSE deletion...")
        if os.path.exists(target_dir):
            logging.info(f"Starting legacy FUSE recursive deletion under: {target_dir}")
            try:
                shutil.rmtree(target_dir)
            except Exception as ex:
                logging.error(f"FUSE recursive deletion failed: {ex}")
                sys.exit(1)

    # 2. Pre-create the 112 job directories for the current run configuration (via FUSE)
    if len(sys.argv) >= 5:
        file_size = sys.argv[2]
        block_size = sys.argv[3]
        nr_files = sys.argv[4]
        
        # Path: target_dir/FILE_SIZE/BLOCK_SIZE/NR_FILES/
        current_conf_path = os.path.join(target_dir, file_size, block_size, nr_files)
        logging.info(f"Pre-creating 112 job subdirectories under: {current_conf_path}")
        
        try:
            for i in range(112):
                os.makedirs(os.path.join(current_conf_path, f"job_{i}"), exist_ok=True)
            logging.info("Successfully pre-created all 112 job subdirectories.")
        except Exception as e:
            logging.error(f"Failed to pre-create job subdirectories: {e}")
            sys.exit(1)

    logging.info("Cleanup and pre-creation completed successfully.")
    sys.exit(0)

if __name__ == "__main__":
    main()
