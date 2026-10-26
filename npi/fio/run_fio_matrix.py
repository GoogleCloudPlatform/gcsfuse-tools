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

"""A script to run a matrix of GCSFuse FIO benchmarks from a config file."""

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
    """Parses args and runs the benchmark matrix."""
    parser = argparse.ArgumentParser(
        description="Run a matrix of GCSFuse FIO benchmarks from a config file.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--gcsfuse-flags",
        default="",
        help="Flags for GCSFuse, as a single quoted string.",
    )
    parser.add_argument(
        "--cpu-limit-list",
        default=None,
        help=(
            "Comma-separated list of CPUs to restrict GCSFuse to, e.g., '0-3,7'. "
            "This is applied to all runs in the matrix."
        ),
    )
    parser.add_argument(
        "--bind-fio",
        action="store_true",
        help=(
            "If set, bind the FIO process to the CPUs specified in --cpu-limit-list. "
            "This is applied to all runs in the matrix."
        ),
    )
    parser.add_argument(
        "--keep-mount",
        action="store_true",
        help="If set, mount GCSFuse once before iterations and keep it mounted. Used for file-cache tests.",
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
        help="Number of FIO test iterations per configuration.",
    )
    parser.add_argument(
        "--fio-template",
        required=True,
        help="Path to the FIO config template file.",
    )
    parser.add_argument(
        "--matrix-config",
        required=True,
        help="Path to the CSV file with benchmark parameters.",
    )
    parser.add_argument(
        "--work-dir",
        default="/tmp/gcsfuse_benchmark",
        help="Working directory for clones and builds.",
    )
    parser.add_argument(
        "--output-dir",
        default="./fio_results_matrix",
        help="Directory to save FIO JSON results.",
    )
    parser.add_argument(
        "--summary-file-name",
        default=None,
        help="File name for the summary of results. It will be created in each configuration's output directory.",
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
        "--project-id",
        default=None,
        help="Project ID to upload results. If provided, --bq-dataset-id and --bq-table-id must also be provided.",
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
    args = parser.parse_args()

    if not args.bucket_name and not args.mount_path:
        parser.error("Either --bucket-name or --mount-path must be provided.")
    if args.bind_fio and not args.cpu_limit_list:
        parser.error("--bind-fio requires --cpu-limit-list to be provided.")

    mount_path = os.path.abspath(args.mount_path) if args.mount_path else None

    required_columns = (
        npi_common.MATRIX_READ_REQUIRED_COLUMNS
        if npi_common.fio_template_requires_read_type(args.fio_template)
        else npi_common.MATRIX_REQUIRED_BASE_COLUMNS
    )
    try:
        configs = npi_common.load_matrix_csv(
            args.matrix_config, required_columns=required_columns
        )
    except FileNotFoundError:
        logging.error("Matrix config file not found: %s", args.matrix_config)
        sys.exit(1)
    except Exception as e:
        logging.error("Error reading matrix config file %s: %s", args.matrix_config, e)
        sys.exit(1)

    logging.info(
        "Found %d configurations to run from %s", len(configs), args.matrix_config
    )

    cli_overrides = {}
    overridden_keys = set()
    if args.numjobs is not None:
        cli_overrides["NUMJOBS"] = str(args.numjobs)
        overridden_keys.add("NUMJOBS")
    if args.direct is not None:
        cli_overrides[npi_common.DIRECT_ENV_KEY] = args.direct
        overridden_keys.add(npi_common.DIRECT_ENV_KEY)

    if not configs:
        try:
            fio_benchmark_runner.resolve_fio_params(cli_overrides, os.environ)
        except ValueError as e:
            logging.error("Invalid FIO parameter configuration: %s", e)
            sys.exit(1)
        logging.warning(
            "Matrix config %s contains 0 rows; skipping BigQuery truncation and benchmark runs.",
            args.matrix_config,
        )
        return

    resolved_rows = []
    for idx, clean_row in enumerate(configs, start=1):
        row_env = dict(clean_row)
        if "NUMJOBS" in cli_overrides:
            row_env.pop("NUM_JOBS", None)
        row_env.update(cli_overrides)
        try:
            direct_val, numjobs_val = fio_benchmark_runner.resolve_fio_params(
                row_env, os.environ
            )
        except ValueError as e:
            logging.error(
                "Invalid FIO parameter in matrix row %d (%s): %s",
                idx,
                clean_row,
                e,
            )
            sys.exit(1)
        row_env.pop("NUM_JOBS", None)
        row_env[npi_common.DIRECT_ENV_KEY] = direct_val
        row_env["NUMJOBS"] = numjobs_val
        resolved_rows.append((clean_row, row_env))

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

    has_failures = False

    for i, (clean_row, fio_env) in enumerate(resolved_rows):
        config_str, config_name = npi_common.format_matrix_config(
            clean_row,
            effective_params={
                "NUMJOBS": fio_env["NUMJOBS"],
                npi_common.DIRECT_ENV_KEY: fio_env[npi_common.DIRECT_ENV_KEY],
            },
            overridden_keys=overridden_keys,
        )

        logging.info("--- Starting Matrix Run %d/%d ---", i + 1, len(resolved_rows))
        logging.info("Configuration: %s", config_str)

        config_output_dir = os.path.join(args.output_dir, config_name)
        summary_file_path = (
            os.path.join(config_output_dir, args.summary_file_name)
            if args.summary_file_name
            else None
        )

        try:
            fio_benchmark_runner.run_benchmark(
                gcsfuse_flags=args.gcsfuse_flags,
                bucket_name=args.bucket_name,
                iterations=args.iterations,
                fio_config=args.fio_template,
                work_dir=args.work_dir,
                output_dir=config_output_dir,
                fio_env=fio_env,
                summary_file=summary_file_path,
                cpu_limit_list=args.cpu_limit_list,
                bind_fio=args.bind_fio,
                project_id=args.project_id,
                bq_dataset_id=args.bq_dataset_id,
                bq_table_id=args.bq_table_id,
                mount_path=mount_path,
                keep_mount=args.keep_mount,
            )
        except Exception as e:
            logging.error("Benchmark run failed for configuration %s: %s", config_str, e)
            has_failures = True
            continue

    logging.info("--- All benchmark matrix runs complete. ---")

    if has_failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
