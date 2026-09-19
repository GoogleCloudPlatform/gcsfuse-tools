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

"""A script to run a matrix of Go-client read benchmarks from a config file."""

import argparse
import csv
import json
import logging
import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import convergence

try:
    from google.cloud import bigquery
    from google.api_core import exceptions
    import datetime
    _BQ_SUPPORTED = True
except ImportError:
    _BQ_SUPPORTED = False

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def run_command(command, check=True, cwd=None):
    """Runs a command and logs its output."""
    logging.info(f"Running command: {' '.join(command)}")
    try:
        result = subprocess.run(
            command, check=check, capture_output=True, text=True, cwd=cwd
        )
        return result
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed with exit code {e.returncode}")
        logging.error(f"STDOUT: {e.stdout.strip() if e.stdout else 'N/A'}")
        logging.error(f"STDERR: {e.stderr.strip() if e.stderr else 'N/A'}")
        raise


def truncate_bq_table(client, project_id, dataset_id, table_id):
    """Erases existing data in the specified BigQuery table."""
    try:
        full_table_id = f"{project_id}.{dataset_id}.{table_id}"
        try:
            client.get_table(full_table_id)
        except exceptions.NotFound:
            logging.info(f"Table {full_table_id} not found, skipping truncation.")
            return

        logging.info(f"--- Erasing data in BigQuery table: {full_table_id} ---")
        query = f"TRUNCATE TABLE `{full_table_id}`"
        query_job = client.query(query)
        query_job.result()
        logging.info(f"Successfully truncated table {full_table_id}")
    except Exception as e:
        logging.error(f"Failed to truncate BigQuery table: {e}")
        raise


def upload_results_to_bq(
    client, project_id, dataset_id, table_id, json_output_content, iteration,
    client_protocol, env_params, cpu_limit_list
):
    """Uploads the benchmark results JSON to a BigQuery table using the FIO schema."""
    try:
        full_table_id = f"{project_id}.{dataset_id}.{table_id}"
        if hasattr(client, "dataset"):
            dataset_ref = client.dataset(dataset_id)
            table_ref = dataset_ref.table(table_id)

            # Create dataset if it doesn't exist
            try:
                client.get_dataset(dataset_ref)
            except exceptions.NotFound:
                logging.info(f"Dataset {dataset_id} not found, creating it.")
                client.create_dataset(bigquery.Dataset(dataset_ref))
        else:
            table_ref = full_table_id

        # Define schema (same as FIO schema to keep database consistent)
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
            logging.info(f"Table {table_id} not found, creating it.")
            table = bigquery.Table(table_ref, schema=schema)
            client.create_table(table)

        row_to_insert = {
            "run_timestamp": datetime.datetime.utcnow().isoformat(),
            "iteration": iteration,
            "gcsfuse_flags": f"--client-protocol={client_protocol}",
            "fio_env": json.dumps(env_params) if env_params else None,
            "cpu_limit_list": cpu_limit_list,
            "fio_json_output": json_output_content,
        }

        errors = client.insert_rows_json(full_table_id, [row_to_insert])
        if errors:
            logging.error(f"Errors inserting rows into BigQuery: {errors}")
        else:
            logging.info(
                f"Successfully inserted results for iteration {iteration} into {full_table_id}"
            )
    except Exception as e:
        logging.error(f"Failed to upload results to BigQuery: {e}")


def print_summary(
    all_results,
    summary_file=None,
    min_iterations=3,
    convergence_threshold=0.05,
    confidence_level=0.95,
):
    """Prints a summary of all benchmark iterations and canonical convergence statistics."""
    if not all_results:
        logging.warning("No results to summarize.")
        return

    print("\n")
    summary_lines = []
    summary_lines.append("--- Go Client Read Benchmark Summary ---")
    header = (
        f"{'Iter':<5} {'Job Name':<20} {'Protocol':<10} {'Block Size':<10} "
        f"{'File Size':<10} {'NR_Files':<8} {'Num Jobs':<8} "
        f"{'Bandwidth (MiB/s)':<20} "
        f"{'IOPS':<12} {'Mean Latency (ms)':<20}"
    )
    separator = "-" * len(header)
    summary_lines.append(header)
    summary_lines.append(separator)

    grouped = {}
    for i, iteration_results in enumerate(all_results, 1):
        # iteration_results is a list of results (one per matrix config) for iteration i
        for result in iteration_results:
            line = (
                f"{i:<5} {result['job_name']:<20} {result['client_protocol']:<10} "
                f"{result['block_size']:<10} {result['file_size']:<10} "
                f"{result['nr_files']:<8} {result['num_jobs']:<8}"
                f"{result['bw_mibps']:<20.2f} {result['iops']:<12.2f} "
                f"{result['mean_lat_ms']:<20.4f}"
            )
            summary_lines.append(line)
            key = (
                result["job_name"],
                result["client_protocol"],
                result["block_size"],
                result["file_size"],
                result["nr_files"],
                result["num_jobs"],
            )
            grouped.setdefault(key, []).append(result)
    summary_lines.append(separator)

    if grouped:
        summary_lines.append("")
        summary_lines.append("--- Statistical Convergence Summary ---")
        stat_header = (
            f"{'Job Name':<20} {'Protocol':<10} {'Block Size':<10} {'File Size':<10} "
            f"{'Rep BW (MiB/s)':<16} {'Median BW':<14} {'95% CI (±)':<14} "
            f"{'RMoE (%)':<10} {'Rep Lat (ms)':<14} {'Converged':<10} {'Samples':<10}"
        )
        stat_sep = "-" * len(stat_header)
        summary_lines.append(stat_header)
        summary_lines.append(stat_sep)
        for key, entries in grouped.items():
            job_name, protocol, bs, fs, _, _ = key
            bw_vals = [e["bw_mibps"] for e in entries]
            lat_vals = [e["mean_lat_ms"] for e in entries]
            bw_conv = convergence.evaluate_convergence(
                bw_vals,
                min_iterations=min_iterations,
                convergence_threshold=convergence_threshold,
                confidence_level=confidence_level,
            )
            lat_conv = convergence.evaluate_convergence(
                lat_vals,
                min_iterations=min_iterations,
                convergence_threshold=convergence_threshold,
                confidence_level=confidence_level,
            )
            ci_str = (
                f"{bw_conv.ci_half_width:.2f}"
                if bw_conv.ci_half_width != float("inf")
                else "inf"
            )
            rmoe_str = (
                f"{bw_conv.relative_margin_of_error * 100.0:.2f}%"
                if bw_conv.relative_margin_of_error != float("inf")
                else "inf"
            )
            conv_str = "YES" if bw_conv.converged else "NO"
            samples_str = f"{bw_conv.n_inliers}/{bw_conv.n_total}"
            stat_line = (
                f"{job_name:<20} {protocol:<10} {bs:<10} {fs:<10} "
                f"{bw_conv.representative_value:<16.2f} {bw_conv.median:<14.2f} "
                f"{ci_str:<14} {rmoe_str:<10} "
                f"{lat_conv.representative_value:<14.4f} {conv_str:<10} {samples_str:<10}"
            )
            summary_lines.append(stat_line)
        summary_lines.append(stat_sep)

    output = "\n".join(summary_lines)
    print(output)

    if summary_file:
        try:
            with open(summary_file, "w") as f:
                f.write(output)
            logging.info(f"Summary also written to {summary_file}")
        except IOError as e:
            logging.error(f"Failed to write summary to {summary_file}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Run a matrix of Go-client read benchmarks from a config file."
    )
    parser.add_argument(
        "--bucket-name", required=True, help="Name of the GCS bucket."
    )
    parser.add_argument(
        "--client-protocol", default="http1", choices=["http1", "grpc"],
        help="Network protocol to use: http1 or grpc."
    )
    parser.add_argument(
        "--numjobs", type=int, default=128,
        help="Degrees of concurrency (number of workers)."
    )
    parser.add_argument(
        "--iterations", type=int, default=1,
        help="Number of iterations per benchmark run."
    )
    parser.add_argument(
        "--min-iterations",
        type=int,
        default=None,
        help="Minimum steady-state iterations before early convergence stopping is permitted.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum iterations to execute per matrix configuration.",
    )
    parser.add_argument(
        "--convergence-threshold",
        type=float,
        default=None,
        help="Target relative margin of error threshold for early stopping (e.g., 0.05 for 5%%).",
    )
    parser.add_argument(
        "--confidence-level",
        type=float,
        default=0.95,
        help="Confidence level for Student's t interval estimation (default: 0.95).",
    )
    parser.add_argument(
        "--matrix-config", required=True,
        help="Path to the CSV file with benchmark parameters (e.g. read_matrix.csv)."
    )
    parser.add_argument(
        "--output-dir", default="./go_results_matrix",
        help="Directory to save JSON result files."
    )
    parser.add_argument(
        "--summary-file-name", default=None,
        help="File name to write the results summary table."
    )
    parser.add_argument(
        "--project-id", default=None,
        help="Project ID to upload results to BigQuery."
    )
    parser.add_argument(
        "--bq-dataset-id", default=None,
        help="BigQuery dataset ID."
    )
    parser.add_argument(
        "--bq-table-id", default=None,
        help="BigQuery table ID."
    )
    parser.add_argument(
        "--cpu-limit-list", default=None,
        help="List of CPUs to restrict the benchmark run to (via taskset)."
    )
    # GCSFuse-specific parameters to ignore (passed by the orchestration framework)
    parser.add_argument(
        "--gcsfuse-flags", default=None, help="Ignored."
    )
    parser.add_argument(
        "--mount-path", default=None, help="Ignored."
    )
    parser.add_argument(
        "--bind-fio", action="store_true", help="Ignored."
    )
    args = parser.parse_args()

    adaptive_mode = any(
        x is not None
        for x in (
            args.min_iterations,
            args.max_iterations,
            args.convergence_threshold,
        )
    )

    if adaptive_mode:
        if args.min_iterations is not None:
            min_iter = args.min_iterations
        else:
            min_iter = max(
                3,
                min(
                    args.iterations,
                    args.max_iterations
                    if args.max_iterations is not None
                    else args.iterations,
                ),
            )
        if args.max_iterations is not None:
            max_iter = args.max_iterations
        else:
            max_iter = max(args.iterations, min_iter)
        threshold = (
            args.convergence_threshold
            if args.convergence_threshold is not None
            else 0.05
        )
        if min_iter < 1 or max_iter < min_iter:
            raise ValueError(
                f"Invalid iteration bounds: min_iterations={min_iter}, max_iterations={max_iter}. "
                f"Requires 1 <= min_iterations <= max_iterations."
            )
    else:
        min_iter = args.iterations
        max_iter = args.iterations
        threshold = 0.05

    confidence_level = (
        args.confidence_level if args.confidence_level is not None else 0.95
    )

    try:
        with open(args.matrix_config, "r", newline="") as f:
            reader = csv.DictReader(f)
            configs = list(reader)
    except Exception as e:
        logging.error("Error reading matrix config file %s: %s", args.matrix_config, e)
        sys.exit(1)

    logging.info("Found %d configurations to run from %s", len(configs), args.matrix_config)

    bq_client = None
    if args.project_id and args.bq_dataset_id and args.bq_table_id:
        if not _BQ_SUPPORTED:
            logging.error("BigQuery is requested, but google-cloud-bigquery package is not installed.")
            sys.exit(1)
        bq_client = bigquery.Client(project=args.project_id)
        truncate_bq_table(bq_client, args.project_id, args.bq_dataset_id, args.bq_table_id)

    os.makedirs(args.output_dir, exist_ok=True)
    all_results = []

    # Build binary path (should be pre-compiled, or compiled on the fly)
    go_bin_path = "./go-benchmark-client"
    if not os.path.exists(go_bin_path):
        # compile on the fly if needed
        logging.info("Compiling go-benchmark-client...")
        run_command(
            ["go", "build", "-o", "go-benchmark-client", "main.go"],
            cwd=os.path.dirname(__file__) or ".",
        )

    for idx, config in enumerate(configs):
        config_str = ", ".join([f"{k}={v}" for k, v in sorted(config.items())])
        logging.info("Matrix config %d/%d: %s", idx + 1, len(configs), config_str)

        file_size = config.get("FILE_SIZE")
        block_size = config.get("BLOCK_SIZE")
        nr_files = config.get("NR_FILES")
        num_jobs_val = config.get("NUMJOBS")
        num_jobs = int(num_jobs_val) if num_jobs_val else args.numjobs

        # Generate unique config name
        config_name_parts = [f"{k}_{v}" for k, v in sorted(config.items())]
        config_name = "_".join(config_name_parts)
        config_output_dir = os.path.join(args.output_dir, config_name)
        os.makedirs(config_output_dir, exist_ok=True)

        bw_samples = []
        last_conv = None

        for iteration in range(1, max_iter + 1):
            logging.info(
                f"--- Starting Config {idx + 1}/{len(configs)} Iteration {iteration}/{max_iter} ---"
            )

            output_file = os.path.join(
                config_output_dir, f"go_results_iter_{iteration}.json"
            )

            cmd = [
                go_bin_path,
                f"--bucket={args.bucket_name}",
                f"--client-protocol={args.client_protocol}",
                f"--bs={block_size}",
                f"--filesize={file_size}",
                f"--numjobs={num_jobs}",
                f"--nrfiles={nr_files}",
            ]
            if args.cpu_limit_list:
                cmd = ["taskset", "-c", args.cpu_limit_list] + cmd

            try:
                # Clear system page cache before each matrix config run
                logging.info("Clearing page cache...")
                subprocess.run(
                    ["sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"], check=False
                )

                result = run_command(cmd, cwd=os.path.dirname(go_bin_path) or ".")
                json_output = result.stdout.strip()

                with open(output_file, "w") as out_f:
                    out_f.write(json_output)

                # Parse output to add to summary
                try:
                    data = json.loads(json_output)
                except json.JSONDecodeError:
                    try:
                        data, _ = json.JSONDecoder().raw_decode(json_output)
                    except json.JSONDecodeError:
                        data = {}
                if not isinstance(data, dict):
                    data = {}
                iter_bw_total = 0.0
                has_job = False
                for job in (data.get("jobs") or []):
                    if not isinstance(job, dict):
                        continue
                    stats = job.get("read") or {}
                    options = job.get("job options") or {}
                    lat_ns = stats.get("lat_ns") or {}
                    percentiles = lat_ns.get("percentiles") or {}
                    p99_lat_ns = (
                        percentiles.get("99.000000", lat_ns.get("99.000000", 0.0))
                        or 0.0
                    )
                    bw_mibps = (stats.get("bw") or 0.0) / 1024.0  # convert KiB/s to MiB/s
                    iter_bw_total += bw_mibps
                    has_job = True
                    while len(all_results) < iteration:
                        all_results.append([])
                    all_results[iteration - 1].append({
                        "job_name": job.get("jobname", "unnamed"),
                        "client_protocol": options.get(
                            "client_protocol", args.client_protocol
                        ),
                        "block_size": options.get("bs", block_size),
                        "file_size": options.get("filesize", file_size),
                        "nr_files": options.get("nrfiles", nr_files),
                        "num_jobs": options.get("numjobs", str(num_jobs)),
                        "bw_mibps": bw_mibps,
                        "iops": stats.get("iops", 0.0),
                        "mean_lat_ms": lat_ns.get("mean", 0.0) / 1_000_000.0,
                        "p99_lat_ms": p99_lat_ns / 1_000_000.0,
                    })

                if has_job:
                    bw_samples.append(iter_bw_total)

                if bq_client:
                    env_params = {
                        "FILE_SIZE": file_size,
                        "BLOCK_SIZE": block_size,
                        "NR_FILES": nr_files,
                        "NUMJOBS": str(num_jobs),
                        "client_protocol": args.client_protocol,
                    }
                    upload_results_to_bq(
                        client=bq_client,
                        project_id=args.project_id,
                        dataset_id=args.bq_dataset_id,
                        table_id=args.bq_table_id,
                        json_output_content=json_output,
                        iteration=iteration,
                        client_protocol=args.client_protocol,
                        env_params=env_params,
                        cpu_limit_list=args.cpu_limit_list,
                    )

            except Exception as e:
                logging.error(f"Benchmark failed for configuration {config}: {e}")

            if adaptive_mode and bw_samples:
                last_conv = convergence.evaluate_convergence(
                    bw_samples,
                    min_iterations=min_iter,
                    convergence_threshold=threshold,
                    confidence_level=confidence_level,
                )
                if last_conv.converged:
                    logging.info(
                        "Config %s converged after %d iterations "
                        "(representative BW: %.2f MiB/s, RMoE: %.4f <= %.4f).",
                        config_name,
                        iteration,
                        last_conv.representative_value,
                        last_conv.relative_margin_of_error,
                        threshold,
                    )
                    break
        else:
            if adaptive_mode and last_conv is not None and not last_conv.converged:
                logging.info(
                    "Config %s reached max_iterations=%d without meeting convergence threshold "
                    "(representative BW: %.2f MiB/s, RMoE: %.4f > %.4f).",
                    config_name,
                    max_iter,
                    last_conv.representative_value,
                    last_conv.relative_margin_of_error,
                    threshold,
                )

    summary_file_path = None
    if args.summary_file_name:
        summary_file_path = os.path.join(args.output_dir, args.summary_file_name)
    print_summary(
        all_results,
        summary_file=summary_file_path,
        min_iterations=min_iter,
        convergence_threshold=threshold,
        confidence_level=confidence_level,
    )


if __name__ == "__main__":
    main()
