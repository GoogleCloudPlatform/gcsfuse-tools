import os
import sys
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def parallel_delete_recursive(root_path):
    """Recursively deletes all files and folders under root_path in parallel via GCSFuse."""
    if not os.path.exists(root_path):
        logging.info(f"Target directory {root_path} does not exist. Skipping deletion.")
        return

    logging.info(f"Scanning directory tree under: {root_path}")
    all_files = []
    all_dirs = []

    # Walk the directory tree to gather all files and directories
    for root, dirs, files in os.walk(root_path):
        for f in files:
            all_files.append(os.path.join(root, f))
        for d in dirs:
            all_dirs.append(os.path.join(root, d))

    # Sort directories by depth in descending order so that leaf directories
    # are deleted first (preventing "Directory not empty" errors)
    all_dirs.sort(key=lambda x: x.count(os.sep), reverse=True)

    if all_files:
        logging.info(f"Deleting {len(all_files)} files concurrently via GCSFuse...")
        start_time = time.time()
        with ThreadPoolExecutor(max_workers=64) as executor:
            futures = {executor.submit(os.remove, f): f for f in all_files}
            success = True
            for future in as_completed(futures):
                f_path = futures[future]
                try:
                    future.result()
                except FileNotFoundError:
                    # Ignore files that might have been deleted concurrently
                    pass
                except Exception as e:
                    logging.error(f"Failed to delete file {f_path} via GCSFuse: {e}")
                    success = False
            if not success:
                raise RuntimeError("Failed to delete one or more files via GCSFuse.")
        logging.info(f"Successfully deleted all files in {time.time() - start_time:.2f} seconds.")

    if all_dirs:
        logging.info(f"Deleting {len(all_dirs)} empty directories sequentially via GCSFuse...")
        success = True
        for d in all_dirs:
            try:
                os.rmdir(d)
            except FileNotFoundError:
                pass
            except Exception as e:
                logging.error(f"Failed to delete directory {d} via GCSFuse: {e}")
                success = False
        if not success:
            raise RuntimeError("Failed to delete one or more directories via GCSFuse.")

    # Finally, delete the root path itself
    try:
        os.rmdir(root_path)
        logging.info(f"Deleted root directory: {root_path}")
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.error(f"Failed to delete root directory {root_path} via GCSFuse: {e}")
        raise


def main():
    # Usage: concurrent_delete.py <target_directory> [<file_size> <block_size> <nr_files>]
    if len(sys.argv) < 2:
        logging.error("Usage: concurrent_delete.py <target_directory> [<file_size> <block_size> <nr_files>]")
        sys.exit(1)

    target_dir = sys.argv[1]

    # 1. Clean up old files via parallel GCSFuse deletion
    try:
        parallel_delete_recursive(target_dir)
    except Exception as e:
        logging.error(f"Parallel GCSFuse deletion failed: {e}")
        sys.exit(1)

    # 2. Pre-create the 112 job directories for the upcoming run configuration (via FUSE)
    if len(sys.argv) >= 5:
        file_size = sys.argv[2]
        block_size = sys.argv[3]
        nr_files = sys.argv[4]

        # Path: target_dir/FILE_SIZE/BLOCK_SIZE/NR_FILES/
        current_conf_path = os.path.join(target_dir, file_size, block_size, nr_files)
        logging.info(f"Pre-creating 112 job subdirectories (job_1 to job_112) under: {current_conf_path}")

        try:
            os.makedirs(current_conf_path, exist_ok=True)

            def precreate_dir(i):
                job_dir = os.path.join(current_conf_path, f"job_{i}")
                os.makedirs(job_dir, exist_ok=True)

            with ThreadPoolExecutor(max_workers=32) as executor:
                # Wrap in list() to consume the generator and propagate exceptions immediately
                list(executor.map(precreate_dir, range(1, 113)))
            logging.info("Successfully pre-created all 112 job directories.")
        except Exception as e:
            logging.error(f"Failed to pre-create job directories via GCSFuse: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
