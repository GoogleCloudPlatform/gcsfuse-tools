#!/usr/bin/env python3
# Copyright 2024 Google LLC
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

"""A script to automate GCSFuse performance benchmarking with FIO."""

import argparse
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def run_command(command, check=True, cwd=None):
    """Runs a command and logs its output."""
    logging.info(f"Running command: {' '.join(command)}")
    try:
        result = subprocess.run(
            command, check=check, capture_output=True, text=True, cwd=cwd, env=os.environ
        )
        if result.stdout:
            logging.info(f"STDOUT: {result.stdout.strip()}")
        if result.stderr:
            # Use warning for stderr as some tools write info there
            logging.info(f"STDERR: {result.stderr.strip()}")
        return result
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed with exit code {e.returncode}")
        logging.error(f"STDOUT: {e.stdout.strip() if e.stdout else 'N/A'}")
        logging.error(f"STDERR: {e.stderr.strip() if e.stderr else 'N/A'}")
        raise

def mount_gcsfuse(gcsfuse_bin, flags, bucket_name, mount_point):
    """Mounts the GCS bucket using GCSFuse."""
    os.makedirs(mount_point, exist_ok=True)
    logging.info(f"Mounting gs://{bucket_name} to {mount_point}")
    cmd = [gcsfuse_bin] + shlex.split(flags) + [bucket_name, mount_point]
    run_command(cmd)
    time.sleep(2)  # Give a moment for the mount to register
    if not os.path.ismount(mount_point):
        logging.error("Mounting failed. Check GCSFuse logs (e.g., in /var/log/syslog).")
        sys.exit(1)
    logging.info("Mount successful.")


def unmount_gcsfuse(mount_point):
    """Unmounts the GCSFuse file system."""
    logging.info(f"Unmounting {mount_point}")
    try:
        run_command(["fusermount", "-u", mount_point])
    except (FileNotFoundError, subprocess.CalledProcessError):
        logging.warning("`fusermount -u` failed. Retrying with `sudo umount`.")
        time.sleep(2)
        run_command(["umount", "-l", mount_point], check=False)


def run_fio_test(fio_config, mount_point, iteration, output_dir):
    """Runs a single FIO test iteration."""
    logging.info(f"Starting FIO test iteration {iteration}...")
    output_filename = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
    cmd = [
        "fio", fio_config, "--output-format=json", f"--output={output_filename}",
        f"--directory={mount_point}"
    ]
    run_command(cmd)
    logging.info(f"FIO test iteration {iteration} complete. Results: {output_filename}")


def main():
    parser = argparse.ArgumentParser(description="Run GCSFuse FIO benchmarks.")
    parser.add_argument("--gcsfuse-flags", default="", help="Flags for GCSFuse, as a single quoted string.")
    parser.add_argument("--bucket-name", required=True, help="Name of the GCS bucket.")
    parser.add_argument("--iterations", type=int, default=1, help="Number of FIO test iterations.")
    parser.add_argument("--fio-config", required=True, help="Path to the FIO config file.")
    parser.add_argument("--work-dir", default="/tmp/gcsfuse_benchmark", help="Working directory for clones and builds.")
    parser.add_argument("--output-dir", default="./fio_results", help="Directory to save FIO JSON results.")
    args = parser.parse_args()

    os.makedirs(args.work_dir, exist_ok=True)
    os.makedirs(args.output_dir, exist_ok=True)

    gcsfuse_bin = "/gcsfuse/gcsfuse"
    bucket_name = args.bucket_name
    mount_point = os.path.join(args.work_dir, "mount_point")
    os.environ["DIR"] = mount_point

    for i in range(1, args.iterations + 1):
        logging.info(f"--- Starting Iteration {i}/{args.iterations} ---")
        try:
            logging.info("Clearing page cache...")
            run_command(["sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"])

            mount_gcsfuse(gcsfuse_bin, args.gcsfuse_flags, bucket_name, mount_point)
            run_fio_test(args.fio_config, mount_point, i, args.output_dir)
        finally:
            if os.path.ismount(mount_point):
                unmount_gcsfuse(mount_point)
        logging.info(f"--- Finished Iteration {i}/{args.iterations} ---")


if __name__ == "__main__":
    main()

