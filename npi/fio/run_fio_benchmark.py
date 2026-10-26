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
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

try:
    import npi_common
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(_SCRIPT_DIR))
    import npi_common

import fio_benchmark_runner

positive_int = npi_common.positive_int
direct_flag = npi_common.direct_flag

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def main():
    parser = argparse.ArgumentParser(
        description="Run GCSFuse FIO benchmarks.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--gcsfuse-flags",
        default="",
        help="Flags for GCSFuse, as a single quoted string.",
    )
    parser.add_argument(
        "--bucket-name", default=None, help="Name of the GCS bucket."
    )
    parser.add_argument(
        "--mount-path",
        default=None,
        help="Path to an already mounted GCS bucket. If provided, --bucket-name is ignored and GCSFuse is not mounted.",
    )
    parser.add_argument(
        "--iterations",
        type=positive_int,
        default=1,
        help="Number of FIO test iterations.",
    )
    parser.add_argument(
        "--fio-config", required=True, help="Path to the FIO config file."
    )
    parser.add_argument(
        "--work-dir",
        default="/tmp/gcsfuse_benchmark",
        help="Working directory for clones and builds.",
    )
    parser.add_argument(
        "--output-dir",
        default="./fio_results",
        help="Directory to save FIO JSON results.",
    )
    parser.add_argument(
        "--summary-file", default=None, help="Path to save the summary of results."
    )
    parser.add_argument(
        "--cpu-limit-list",
        default=None,
        help="Comma-separated list of CPUs to restrict GCSFuse to, e.g., '0-3,7'.",
    )
    parser.add_argument(
        "--bind-fio",
        action="store_true",
        help="If set, bind the FIO process to the CPUs specified in --cpu-limit-list.",
    )
    parser.add_argument(
        "--keep-mount",
        action="store_true",
        help="If set, mount GCSFuse once before iterations and keep it mounted. Used for file-cache tests.",
    )
    parser.add_argument(
        "--numjobs",
        type=positive_int,
        default=None,
        help="Override FIO numjobs count.",
    )
    parser.add_argument(
        "--direct",
        type=direct_flag,
        default=None,
        help="Override FIO direct I/O setting (0 or 1).",
    )
    parser.add_argument(
        "--project-id", required=True, default=None, help="Project ID to upload results."
    )
    parser.add_argument(
        "--bq-dataset-id", default=None, help="BigQuery dataset ID."
    )
    parser.add_argument(
        "--bq-table-id", default=None, help="BigQuery table ID."
    )
    args = parser.parse_args()

    if not args.bucket_name and not args.mount_path:
        parser.error("Either --bucket-name or --mount-path must be provided.")
    if args.bind_fio and not args.cpu_limit_list:
        parser.error("--bind-fio requires --cpu-limit-list to be provided.")

    mount_path = os.path.abspath(args.mount_path) if args.mount_path else None

    cli_fio_env = {}
    if args.numjobs is not None:
        cli_fio_env["NUMJOBS"] = str(args.numjobs)
    if args.direct is not None:
        cli_fio_env[npi_common.DIRECT_ENV_KEY] = args.direct

    try:
        fio_benchmark_runner.resolve_fio_params(cli_fio_env, os.environ)
    except ValueError as e:
        logging.error("Invalid FIO parameter configuration: %s", e)
        sys.exit(1)

    if args.project_id and args.bq_dataset_id and args.bq_table_id:
        if not fio_benchmark_runner._BQ_SUPPORTED:
            logging.error(
                "BigQuery operations requested, but 'google-cloud-bigquery' is not installed."
            )
            sys.exit(1)
        bq_client = fio_benchmark_runner.bigquery.Client(project=args.project_id)
        fio_benchmark_runner.truncate_bq_table(
            bq_client, args.project_id, args.bq_dataset_id, args.bq_table_id
        )

    try:
        fio_benchmark_runner.run_benchmark(
            gcsfuse_flags=args.gcsfuse_flags,
            bucket_name=args.bucket_name,
            iterations=args.iterations,
            fio_config=args.fio_config,
            work_dir=args.work_dir,
            output_dir=args.output_dir,
            fio_env=cli_fio_env or None,
            summary_file=args.summary_file,
            cpu_limit_list=args.cpu_limit_list,
            bind_fio=args.bind_fio,
            project_id=args.project_id,
            bq_dataset_id=args.bq_dataset_id,
            bq_table_id=args.bq_table_id,
            mount_path=mount_path,
            keep_mount=args.keep_mount,
        )
    except Exception as e:
        logging.error("Benchmark run failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
