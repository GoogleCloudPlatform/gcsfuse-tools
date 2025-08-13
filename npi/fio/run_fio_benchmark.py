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
import subprocess
import sys
import time
import json

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


def parse_fio_output(filename):
    """Parses FIO JSON output to extract key metrics."""
    try:
        with open(filename, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logging.error(f"Could not read or parse FIO output {filename}: {e}")
        return []

    results = []
    for job in data.get("jobs", []):
        job_name = job.get("jobname", "unnamed_job")
        for op in ["read", "write"]:
            if op in job:
                stats = job[op]
                # Bandwidth is in KiB/s, convert to MiB/s
                bw_mibps = stats.get("bw", 0) / 1024.0
                iops = stats.get("iops", 0)

                # Latency can be under 'lat_ns', 'clat_ns', etc.
                lat_stats = stats.get("lat_ns") or {}

                # Convert from ns to ms
                mean_lat_ms = lat_stats.get("mean", 0) / 1_000_000.0

                # Percentiles are in a sub-dict with string keys
                percentiles = lat_stats.get("percentiles", {})  # FIO 3.x
                
                p99_key = next((k for k in percentiles if k.startswith("99.00")), None)
                p99_lat_ms = (
                    percentiles.get(p99_key, 0) / 1_000_000.0 if p99_key else 0
                )

                results.append({
                    "job_name": job_name,
                    "operation": op,
                    "bw_mibps": bw_mibps,
                    "iops": iops,
                    "mean_lat_ms": mean_lat_ms,
                    "p99_lat_ms": p99_lat_ms,
                })
    return results


def print_summary(all_results):
    """Prints a summary of all FIO iterations."""
    if not all_results:
        logging.warning("No results to summarize.")
        return

    logging.info("\n--- FIO Benchmark Summary ---")
    header = (f"{'Iter':<5} {'Job Name':<20} {'Op':<6} {'Bandwidth (MiB/s)':<20} "
              f"{'IOPS':<12} {'Mean Latency (ms)':<20} {'P99 Latency (ms)':<20}")
    print(header)
    print("-" * len(header))
    for i, iteration_results in enumerate(all_results, 1):
        if not iteration_results:
            print(f"{i:<5} No results for this iteration.")
            continue
        for result in iteration_results:
            print(f"{i:<5} {result['job_name']:<20} {result['operation']:<6} "
                  f"{result['bw_mibps']:<20.2f} {result['iops']:<12.2f} "
                  f"{result['mean_lat_ms']:<20.4f} {result['p99_lat_ms']:<20.4f}")
    print("-" * len(header))


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
    all_results = []

    for i in range(1, args.iterations + 1):
        logging.info(f"--- Starting Iteration {i}/{args.iterations} ---")
        output_filename = os.path.join(args.output_dir,
                                       f"fio_results_iter_{i}.json")
        if os.path.exists(output_filename):
            os.remove(output_filename)
        try:
            logging.info("Clearing page cache...")
            run_command(["sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"])

            mount_gcsfuse(gcsfuse_bin, args.gcsfuse_flags, bucket_name, mount_point)
            run_fio_test(args.fio_config, mount_point, i, args.output_dir)

            iteration_results = parse_fio_output(output_filename)
            all_results.append(iteration_results)
        finally:
            if os.path.ismount(mount_point):
                unmount_gcsfuse(mount_point)
        logging.info(f"--- Finished Iteration {i}/{args.iterations} ---")

    print_summary(all_results)


if __name__ == "__main__":
    main()
