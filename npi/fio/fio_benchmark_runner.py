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

"""Core logic for GCSFuse performance benchmarking with FIO."""

import json
import logging
import os
import shlex
import subprocess
import sys
import time

try:
    from google.cloud import bigquery
    from google.api_core import exceptions
    import datetime
    _BQ_SUPPORTED = True
except ImportError:
    _BQ_SUPPORTED = False


def run_command(command, check=True, cwd=None, extra_env=None):
    """Runs a command and logs its output."""
    logging.info(f"Running command: {' '.join(command)}")

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    try:
        result = subprocess.run(
            command, check=check, capture_output=True, text=True, cwd=cwd, env=env
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

def mount_gcsfuse(gcsfuse_bin, flags, bucket_name, mount_point, cpu_limit_list=None):
    """Mounts the GCS bucket using GCSFuse."""
    os.makedirs(mount_point, exist_ok=True)
    logging.info(f"Mounting gs://{bucket_name} to {mount_point}")
    cmd = [gcsfuse_bin] + shlex.split(flags) + [bucket_name, mount_point]
    if cpu_limit_list:
        cmd = ["taskset", "-c", cpu_limit_list] + cmd
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


def run_fio_test(fio_config, mount_point, iteration, output_dir, fio_env=None, cpu_limit_list=None):
    """Runs a single FIO test iteration."""
    logging.info(f"Starting FIO test iteration {iteration}...")
    output_filename = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
    cmd = [
        "fio", fio_config, "--output-format=json", f"--output={output_filename}",
        f"--directory={mount_point}"
    ]
    if cpu_limit_list:
        logging.info(f"Binding FIO to CPUs: {cpu_limit_list}")
        cmd = ["taskset", "-c", cpu_limit_list] + cmd
    run_command(cmd, extra_env=fio_env)
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
                options = job.get("job options", {})
                # Bandwidth is in KiB/s, convert to MiB/s
                bw_mibps = stats.get("bw", 0) / 1024.0
                if bw_mibps == 0:
                    continue
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
                    "block_size": options.get("bs", 0),
                    "file_size": options.get("filesize", 0),
                    "nr_files": options.get("nrfiles", 0),
                    "queue_depth": data["global options"].get("iodepth", 0),
                    "num_jobs": options.get("numjobs", 0),
                    "operation": data["global options"].get("rw", "unknown"),
                    "bw_mibps": bw_mibps,
                    "iops": iops,
                    "mean_lat_ms": mean_lat_ms,
                    "p99_lat_ms": p99_lat_ms,
                })
    return results


def print_summary(all_results, summary_file=None):
    """Prints a summary of all FIO iterations and optionally writes to a file."""
    if not all_results:
        logging.warning("No results to summarize.")
        return

    print("\n")
    summary_lines = []

    summary_lines.append("--- FIO Benchmark Summary ---")

    header = (f"{'Iter':<5} {'Job Name':<20} {'Op':<8} {'Block Size':<10} {'File Size':<10} {'NR_Files':<3} {'Queue Depth':<12} {'Num Jobs':<8} "
              f"{'Bandwidth (MiB/s)':<20} "
              f"{'IOPS':<12} {'Mean Latency (ms)':<20}")
    separator = "-" * len(header)
    summary_lines.append(header)
    summary_lines.append(separator)

    for i, iteration_results in enumerate(all_results, 1):
        if not iteration_results:
            line = f"{i:<5} No results for this iteration."
            summary_lines.append(line)
            continue
        for result in iteration_results:
            line = (f"{i:<5} {result['job_name']:<20} {result['operation']:<8} {result['block_size']:<10} {result['file_size']:<10} {result['nr_files']:<8} {result['queue_depth']:<12} {result['num_jobs']:<8}"
                    f"{result['bw_mibps']:<20.2f} {result['iops']:<12.2f} "
                    f"{result['mean_lat_ms']:<20.4f}")
            summary_lines.append(line)
    summary_lines.append(separator)
    output = "\n".join(summary_lines)
    print(output)

    if summary_file:
        try:
            with open(summary_file, "w") as f:
                f.write(output)
            logging.info(f"Summary also written to {summary_file}")
        except IOError as e:
            logging.error(f"Failed to write summary to {summary_file}: {e}")


def upload_results_to_bq(
    project_id, dataset_id, table_id, fio_json_path, iteration,
    gcsfuse_flags, fio_env, cpu_limit_list
):
    """Uploads the full FIO JSON output to a BigQuery table."""
    if not _BQ_SUPPORTED:
        logging.error(
            "BigQuery upload requested, but 'google-cloud-bigquery' is not "
            "installed. Please run 'pip3 install google-cloud-bigquery'."
        )
        return

    try:
        with open(fio_json_path, "r") as f:
            fio_json_content = f.read()
    except (IOError, FileNotFoundError) as e:
        logging.error(f"Could not read FIO JSON file {fio_json_path}: {e}")
        return

    try:
        client = bigquery.Client(project=project_id)
        full_table_id = f"{project_id}.{dataset_id}.{table_id}"
        dataset_ref = client.dataset(dataset_id)
        table_ref = dataset_ref.table(table_id)

        # Create dataset if it doesn't exist
        try:
            client.get_dataset(dataset_ref)
        except exceptions.NotFound:
            logging.info(f"Dataset {dataset_id} not found, creating it.")
            client.create_dataset(bigquery.Dataset(dataset_ref))

        # Define schema
        schema = [
            bigquery.SchemaField("run_timestamp", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("iteration", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("gcsfuse_flags", "STRING"),
            bigquery.SchemaField("fio_env", "STRING"),
            bigquery.SchemaField("cpu_limit_list", "STRING"),
            bigquery.SchemaField("fio_json_output", "JSON"),
        ]

        # Create table if it doesn't exist
        try:
            client.get_table(table_ref)
        except exceptions.NotFound:
            logging.info(f"Table {table_id} not found, creating it with a new schema.")
            table = bigquery.Table(table_ref, schema=schema)
            client.create_table(table)

        row_to_insert = {
            "run_timestamp": datetime.datetime.utcnow().isoformat(),
            "iteration": iteration,
            "gcsfuse_flags": gcsfuse_flags,
            "fio_env": json.dumps(fio_env) if fio_env else None,
            "cpu_limit_list": cpu_limit_list,
            "fio_json_output": fio_json_content,
        }

        errors = client.insert_rows_json(full_table_id, [row_to_insert])
        if errors:
            logging.error(f"Errors inserting rows into BigQuery: {errors}")
        else:
            logging.info(
                f"Successfully inserted FIO JSON for iteration {iteration} into {full_table_id}"
            )

    except Exception as e:
        logging.error(f"Failed to upload results to BigQuery: {e}")
        logging.error("Please ensure you have run 'gcloud auth application-default login' and have the correct permissions.")


def run_benchmark(
    gcsfuse_flags, bucket_name, iterations, fio_config, work_dir, output_dir,
    fio_env=None, summary_file=None, cpu_limit_list=None, bind_fio=False,
    project_id=None, bq_dataset_id=None, bq_table_id=None
):
    """Runs the full FIO benchmark suite."""
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    gcsfuse_bin = "/gcsfuse/gcsfuse"
    mount_point = os.path.join(work_dir, "mount_point")

    if bind_fio and not cpu_limit_list:
        logging.error("--bind-fio is set to true, but --cpu-limit-list is not provided.")
        sys.exit(1)

    # Prepare environment for FIO
    fio_run_env = {"DIR": mount_point}
    if fio_env:
        fio_run_env.update(fio_env)

    all_results = []

    for i in range(1, iterations + 1):
        logging.info(f"--- Starting Iteration {i}/{iterations} ---")
        output_filename = os.path.join(output_dir,
                                       f"fio_results_iter_{i}.json")
        if os.path.exists(output_filename):
            os.remove(output_filename)
        try:
            logging.info("Clearing page cache...")
            run_command(["sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"])

            mount_gcsfuse(gcsfuse_bin, gcsfuse_flags, bucket_name, mount_point, cpu_limit_list=cpu_limit_list)

            fio_cpu_list = cpu_limit_list if bind_fio else None

            run_fio_test(fio_config, mount_point, i, output_dir,
                         fio_env=fio_run_env, cpu_limit_list=fio_cpu_list)

            iteration_results = parse_fio_output(output_filename)
            all_results.append(iteration_results)

            if project_id and bq_dataset_id and bq_table_id:
                upload_results_to_bq(
                    project_id=project_id,
                    dataset_id=bq_dataset_id,
                    table_id=bq_table_id,
                    fio_json_path=output_filename,
                    iteration=i,
                    gcsfuse_flags=gcsfuse_flags,
                    fio_env=fio_run_env,
                    cpu_limit_list=cpu_limit_list)
        finally:
            if os.path.ismount(mount_point):
                unmount_gcsfuse(mount_point)
        logging.info(f"--- Finished Iteration {i}/{iterations} ---")

    print_summary(all_results, summary_file=summary_file)
