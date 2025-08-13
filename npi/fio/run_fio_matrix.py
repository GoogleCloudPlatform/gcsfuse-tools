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
import csv
import logging
import os
import sys

import fio_benchmark_runner

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def main():
  """Parses args and runs the benchmark matrix."""
  parser = argparse.ArgumentParser(
      description="Run a matrix of GCSFuse FIO benchmarks from a config file."
  )
  parser.add_argument(
      "--gcsfuse-flags",
      default="",
      help="Flags for GCSFuse, as a single quoted string.",
  )
  parser.add_argument(
      "--bucket-name", required=True, help="Name of the GCS bucket."
  )
  parser.add_argument(
      "--iterations",
      type=int,
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
  args = parser.parse_args()

  try:
    with open(args.matrix_config, "r", newline="") as f:
      reader = csv.DictReader(f)
      configs = list(reader)
  except FileNotFoundError:
    logging.error("Matrix config file not found: %s", args.matrix_config)
    sys.exit(1)
  except Exception as e:
    logging.error("Error reading matrix config file: %s", e)
    sys.exit(1)

  logging.info(
      "Found %d configurations to run from %s", len(configs), args.matrix_config
  )

  for i, config in enumerate(configs):
    # Create a string representation of the configuration for logging.
    config_str = ", ".join([f"{k}={v}" for k, v in sorted(config.items())])

    logging.info("--- Starting Matrix Run %d/%d ---", i + 1, len(configs))
    logging.info("Configuration: %s", config_str)

    # All columns from the CSV are passed as environment variables to FIO.
    fio_env = config

    # Create a unique subdirectory for this configuration's results.
    # The name is generated from the config parameters to be unique and
    # descriptive.
    config_name_parts = [
        f"{k}_{v}" for k, v in sorted(config.items())
    ]
    config_name = "_".join(config_name_parts)
    config_output_dir = os.path.join(args.output_dir, config_name)

    try:
      fio_benchmark_runner.run_benchmark(
          gcsfuse_flags=args.gcsfuse_flags, bucket_name=args.bucket_name,
          iterations=args.iterations, fio_config=args.fio_template,
          work_dir=args.work_dir, output_dir=config_output_dir, fio_env=fio_env)
    except Exception as e:
      logging.error("Benchmark run failed for configuration %s: %s", config, e)
      # Continue to the next configuration
      continue

  logging.info("--- All benchmark matrix runs complete. ---")


if __name__ == "__main__":
  main()
