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

import fio_benchmark_runner

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def main():
    parser = argparse.ArgumentParser(description="Run GCSFuse FIO benchmarks.")
    parser.add_argument("--gcsfuse-flags", default="", help="Flags for GCSFuse, as a single quoted string.")
    parser.add_argument("--bucket-name", required=True, help="Name of the GCS bucket.")
    parser.add_argument("--iterations", type=int, default=1, help="Number of FIO test iterations.")
    parser.add_argument("--fio-config", required=True, help="Path to the FIO config file.")
    parser.add_argument("--work-dir", default="/tmp/gcsfuse_benchmark", help="Working directory for clones and builds.")
    parser.add_argument("--output-dir", default="./fio_results", help="Directory to save FIO JSON results.")
    parser.add_argument("--summary-file", default=None, help="Path to save the summary of results.")
    parser.add_argument("--cpu-limit-list", default=None, help="Comma-separated list of CPUs to restrict GCSFuse to, e.g., '0-3,7'.")
    parser.add_argument(
        "--bind-fio",
        action="store_true",
        help="If set, bind the FIO process to the CPUs specified in --cpu-limit-list."
    )
    parser.add_argument("--project-id", required=True, default=None, help="Project ID to upload results.")
    parser.add_argument("--bq-dataset-id", default=None, help="BigQuery dataset ID.")
    parser.add_argument("--bq-table-id", default=None, help="BigQuery table ID.")
    args = parser.parse_args()

    fio_benchmark_runner.run_benchmark(
        gcsfuse_flags=args.gcsfuse_flags,
        bucket_name=args.bucket_name,
        iterations=args.iterations,
        fio_config=args.fio_config,
        work_dir=args.work_dir,
        output_dir=args.output_dir,
        summary_file=args.summary_file,
        cpu_limit_list=args.cpu_limit_list,
        bind_fio=args.bind_fio,
        project_id=args.project_id,
        bq_dataset_id=args.bq_dataset_id,
        bq_table_id=args.bq_table_id,
    )


if __name__ == "__main__":
    main()
