import os
import sys
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def parallel_delete_recursive(root_path):
    """Recursively deletes all files and folders under root_path in parallel via GCSFuse."""
    if not root_path:
        raise ValueError("Safe guard: root_path cannot be empty.")

    root_path = os.path.realpath(root_path)
    # Prevent deletion of system directories, user home directories, or root
    if (
        root_path in ("/", "/root", "/home", "/boot", "/dev", "/etc", "/lib", "/lib64", "/media", "/mnt", "/opt", "/proc", "/run", "/srv", "/sys", "/usr", "/var", "/tmp")
        or (root_path.startswith(("/home/", "/root/")) and len(root_path.split(os.sep)) <= 3)
    ):
        raise ValueError(f"Safe guard: Deletion of root_path '{root_path}' is not allowed.")

    if not os.path.isdir(root_path):
        logging.info(f"Target directory {root_path} does not exist or is not a directory. Skipping deletion.")
        return

    logging.info(f"Scanning directory tree under: {root_path}")
    all_files = []
    all_dirs = []

    # Walk the directory tree to gather all files and directories
    for root, dirs, files in os.walk(root_path):
        for f in files:
            all_files.append(os.path.normpath(os.path.join(root, f)))
        for d in dirs:
            all_dirs.append(os.path.normpath(os.path.join(root, d)))

    # Sort directories by depth in descending order so that leaf directories
    # are deleted first (preventing "Directory not empty" errors)
    all_dirs.sort(key=lambda x: x.count(os.sep), reverse=True)

    failed_files_count = 0
    failed_dirs_count = 0

    if all_files:
        logging.info(f"Deleting {len(all_files)} files concurrently via GCSFuse...")
        start_time = time.time()
        with ThreadPoolExecutor(max_workers=64) as executor:
            futures = {executor.submit(os.remove, f): f for f in all_files}
            for future in as_completed(futures):
                f_path = futures[future]
                try:
                    future.result()
                except FileNotFoundError:
                    pass
                except Exception as e:
                    logging.error(f"Failed to delete file {f_path} via GCSFuse: {e}")
                    failed_files_count += 1
        logging.info(f"Deleted files in {time.time() - start_time:.2f} seconds (failures: {failed_files_count}).")

    if all_dirs:
        logging.info(f"Deleting {len(all_dirs)} empty directories sequentially via GCSFuse...")
        for d in all_dirs:
            try:
                os.rmdir(d)
            except FileNotFoundError:
                pass
            except Exception as e:
                logging.error(f"Failed to delete directory {d} via GCSFuse: {e}")
                failed_dirs_count += 1

    # Finally, delete the root path itself
    root_deleted = False
    if os.path.ismount(root_path):
        logging.info(f"Root path {root_path} is a mount point. Skipping deletion of the root directory itself.")
    else:
        try:
            os.rmdir(root_path)
            logging.info(f"Deleted root directory: {root_path}")
            root_deleted = True
        except FileNotFoundError:
            root_deleted = True
        except Exception as e:
            logging.error(f"Failed to delete root directory {root_path} via GCSFuse: {e}")

    # Check if there were any failures, and raise a single consolidated exception at the end
    if failed_files_count > 0 or failed_dirs_count > 0 or (not os.path.ismount(root_path) and not root_deleted):
        raise RuntimeError(
            f"Parallel GCSFuse deletion completed with errors. "
            f"Failed files: {failed_files_count}, failed directories: {failed_dirs_count}, "
            f"root directory deleted: {root_deleted}"
        )


def main():
    # Usage: concurrent_delete.py <target_directory>
    if len(sys.argv) < 2:
        logging.error("Usage: concurrent_delete.py <target_directory>")
        sys.exit(1)

    target_dir = sys.argv[1]

    # 1. Clean up old files via parallel GCSFuse deletion
    try:
        parallel_delete_recursive(target_dir)
    except Exception as e:
        logging.error(f"Parallel GCSFuse deletion failed: {e}")
        sys.exit(1)




if __name__ == "__main__":
    main()
