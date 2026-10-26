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
import json
import logging
import os
import subprocess
import sys

try:
    import npi_common
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import npi_common

bigquery = npi_common.bigquery
exceptions = npi_common.exceptions
_BQ_SUPPORTED = npi_common.BQ_SUPPORTED

positive_int = npi_common.positive_int
direct_flag = npi_common.direct_flag
resolve_numjobs = npi_common.resolve_numjobs

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GO_BIN_PATH = os.path.join(SCRIPT_DIR, "go-benchmark-client")

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def run_command(command, check=True, cwd=None):
    """Runs a command and logs its output."""
    logging.info("Running command: %s", " ".join(command))
    try:
        result = subprocess.run(
            command, check=check, capture_output=True, text=True, cwd=cwd
        )
        return result
    except subprocess.CalledProcessError as e:
        logging.error("Command failed with exit code %s", e.returncode)
        logging.error("STDOUT: %s", e.stdout.strip() if e.stdout else "N/A")
        logging.error("STDERR: %s", e.stderr.strip() if e.stderr else "N/A")
        raise


def _ensure_go_binary():
    """Returns the script-relative Go benchmark binary path, compiling if absent."""
    if not os.path.exists(GO_BIN_PATH):
        logging.info("Compiling go-benchmark-client...")
        run_command(
            ["go", "build", "-o", GO_BIN_PATH, "main.go"], cwd=SCRIPT_DIR
        )
    return GO_BIN_PATH


def truncate_bq_table(client, project_id, dataset_id, table_id):
    """Erases existing data in the specified BigQuery table."""
    return npi_common.truncate_bq_table(
        client, project_id, dataset_id, table_id, exceptions_module=exceptions
    )


def upload_results_to_bq(
    client,
    project_id,
    dataset_id,
    table_id,
    json_output_content,
    iteration,
    client_protocol,
    env_params,
    cpu_limit_list,
):
    """Uploads the benchmark results JSON to a BigQuery table using the FIO schema."""
    npi_common.insert_benchmark_row_to_bq(
        client=client,
        project_id=project_id,
        dataset_id=dataset_id,
        table_id=table_id,
        iteration=iteration,
        gcsfuse_flags=f"--client-protocol={client_protocol}",
        env_params=env_params,
        cpu_limit_list=cpu_limit_list,
        json_output_content=json_output_content,
        bq_module=bigquery,
        exceptions_module=exceptions,
    )


def print_summary(all_results, summary_file=None):
    """Prints a summary of all benchmark iterations."""
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
    summary_lines.append(separator)
    output = "\n".join(summary_lines)
    print(output)

    if summary_file:
        try:
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write(output)
            logging.info("Summary also written to %s", summary_file)
        except IOError as e:
            logging.error("Failed to write summary to %s: %s", summary_file, e)


def main():
    parser = argparse.ArgumentParser(
        description="Run a matrix of Go-client read benchmarks from a config file.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--bucket-name", required=True, help="Name of the GCS bucket."
    )
    parser.add_argument(
        "--client-protocol",
        default="http1",
        choices=["http1", "grpc"],
        help="Network protocol to use: http1 or grpc.",
    )
    parser.add_argument(
        "--numjobs",
        type=positive_int,
        default=None,
        help="Degrees of concurrency (number of workers). Defaults to NUMJOBS env var or 112.",
    )
    parser.add_argument(
        "--iterations",
        type=positive_int,
        default=1,
        help="Number of iterations per benchmark run.",
    )
    parser.add_argument(
        "--matrix-config",
        required=True,
        help="Path to the CSV file with benchmark parameters (e.g. read_matrix.csv).",
    )
    parser.add_argument(
        "--output-dir",
        default="./go_results_matrix",
        help="Directory to save JSON result files.",
    )
    parser.add_argument(
        "--summary-file-name",
        default=None,
        help="File name to write the results summary table.",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="Project ID to upload results to BigQuery.",
    )
    parser.add_argument(
        "--bq-dataset-id",
        default=None,
        help="BigQuery dataset ID.",
    )
    parser.add_argument(
        "--bq-table-id",
        default=None,
        help="BigQuery table ID.",
    )
    parser.add_argument(
        "--cpu-limit-list",
        default=None,
        help="List of CPUs to restrict the benchmark run to (via taskset).",
    )
    # GCSFuse-specific parameters to ignore (passed by the orchestration framework)
    parser.add_argument("--gcsfuse-flags", default=None, help="Ignored.")
    parser.add_argument("--mount-path", default=None, help="Ignored.")
    parser.add_argument("--bind-fio", action="store_true", help="Ignored.")
    parser.add_argument(
        "--direct", type=direct_flag, default=None, help="Ignored."
    )
    args = parser.parse_args()

    try:
        configs = npi_common.load_matrix_csv(
            args.matrix_config,
            required_columns=npi_common.MATRIX_REQUIRED_BASE_COLUMNS,
        )
    except Exception as e:
        logging.error(
            "Error reading matrix config file %s: %s", args.matrix_config, e
        )
        sys.exit(1)

    logging.info(
        "Found %d configurations to run from %s", len(configs), args.matrix_config
    )

    if not configs:
        try:
            resolve_numjobs(args.numjobs, {}, os.environ)
        except ValueError as e:
            logging.error("Invalid numjobs configuration: %s", e)
            sys.exit(1)
        logging.warning(
            "Matrix config %s contains 0 rows; skipping BigQuery truncation and benchmark runs.",
            args.matrix_config,
        )
        return

    resolved_configs = []
    for idx, clean_row in enumerate(configs, start=1):
        try:
            num_jobs = resolve_numjobs(args.numjobs, clean_row, os.environ)
        except ValueError as e:
            logging.error(
                "Invalid numjobs in matrix row %d (%s): %s", idx, clean_row, e
            )
            sys.exit(1)
        resolved_configs.append((clean_row, num_jobs))

    bq_client = None
    if args.project_id and args.bq_dataset_id and args.bq_table_id:
        if not _BQ_SUPPORTED:
            logging.error(
                "BigQuery is requested, but google-cloud-bigquery package is not installed."
            )
            sys.exit(1)
        bq_client = bigquery.Client(project=args.project_id)
        truncate_bq_table(
            bq_client, args.project_id, args.bq_dataset_id, args.bq_table_id
        )

    os.makedirs(args.output_dir, exist_ok=True)
    all_results = [[] for _ in range(args.iterations)]

    try:
        go_bin_path = _ensure_go_binary()
    except Exception as e:
        logging.error("Failed to prepare Go benchmark binary: %s", e)
        sys.exit(1)

    overridden_keys = {"NUMJOBS"} if args.numjobs is not None else ()
    has_failures = False

    for iteration in range(1, args.iterations + 1):
        logging.info("--- Starting Iteration %d/%d ---", iteration, args.iterations)

        for idx, (clean_row, num_jobs) in enumerate(resolved_configs):
            config_str, config_name = npi_common.format_matrix_config(
                clean_row,
                effective_params={"NUMJOBS": str(num_jobs)},
                overridden_keys=overridden_keys,
            )
            logging.info(
                "Matrix config %d/%d — Configuration: %s",
                idx + 1,
                len(resolved_configs),
                config_str,
            )

            file_size = clean_row["FILE_SIZE"]
            block_size = clean_row["BLOCK_SIZE"]
            nr_files = clean_row["NR_FILES"]

            config_output_dir = os.path.join(args.output_dir, config_name)
            os.makedirs(config_output_dir, exist_ok=True)

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
                logging.info("Clearing page cache...")
                run_command(
                    ["sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"],
                    check=False,
                )

                result = run_command(cmd, cwd=SCRIPT_DIR)
                json_output = result.stdout.strip()

                with open(output_file, "w", encoding="utf-8") as out_f:
                    out_f.write(json_output)

                # Parse output to add to summary
                data = json.loads(json_output)
                for job in data.get("jobs", []):
                    stats = job.get("read", {})
                    options = job.get("job options", {})
                    all_results[iteration - 1].append({
                        "job_name": job.get("jobname", "unnamed"),
                        "client_protocol": options.get(
                            "client_protocol", args.client_protocol
                        ),
                        "block_size": options.get("bs", block_size),
                        "file_size": options.get("filesize", file_size),
                        "nr_files": options.get("nrfiles", nr_files),
                        "num_jobs": options.get("numjobs", str(num_jobs)),
                        "bw_mibps": stats.get("bw", 0.0) / 1024.0,
                        "iops": stats.get("iops", 0.0),
                        "mean_lat_ms": (
                            stats.get("lat_ns", {}).get("mean", 0.0) / 1_000_000.0
                        ),
                    })

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
                logging.error(
                    "Benchmark failed for configuration %s: %s", config_str, e
                )
                has_failures = True

    summary_file_path = (
        os.path.join(args.output_dir, args.summary_file_name)
        if args.summary_file_name
        else None
    )
    print_summary(all_results, summary_file=summary_file_path)

    if has_failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
