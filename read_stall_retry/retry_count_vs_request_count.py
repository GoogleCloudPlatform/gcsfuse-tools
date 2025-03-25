#!/usr/bin/env python3

# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pandas as pd
import logging
import time
import argparse
import fsspec
import gcsfs
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

# Initialize logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Compile regex
LOG_PATTERN = re.compile(r'\[(.*?)\] stalled read-req for object \((.*?)\) cancelled after')

def process_file(file_path, fs, aggregated_counts):
    """Processes a single log file and aggregates UUID counts."""
    try:
        with fs.open(file_path, 'r') as file:
            for line in file:
                match = LOG_PATTERN.search(line)
                if match:
                    uuid = match.group(1)
                    if uuid:
                        aggregated_counts[uuid] += 1
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
    except Exception as e:
        logger.error(f"Error processing file {file_path}: {e}")

def process_files_optimized(file_pattern, output_file, num_workers=4):
    """Processes log files in parallel and aggregates retry counts."""
    try:
        if file_pattern.startswith("gs://"):
            fs = gcsfs.GCSFileSystem()
        else:
            fs = fsspec.filesystem("local")

        file_list = fs.glob(file_pattern)

        if not file_list:
            logger.warning(f"No files found matching pattern: {file_pattern}")
            return

        aggregated_counts = defaultdict(int)
        futures = []

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            for file_path in file_list:
                futures.append(executor.submit(process_file, file_path, fs, aggregated_counts))

            for future in as_completed(futures):
                try:
                    future.result()  # Check for exceptions in threads
                except Exception as e:
                    logger.error(f"Error in thread: {e}")

        frequency_counts = defaultdict(int)
        for count in aggregated_counts.values():
            frequency_counts[count] += 1

        frequency_counts_df = pd.DataFrame(list(frequency_counts.items()), columns=['retry_count', 'num_requests_with_that_retry_count'])
        frequency_counts_df.to_csv(output_file, index=False)
        logger.info(f"Results saved to '{output_file}'.")

    except Exception as e:
        logger.error(f"Error in processing files: {e}")

def parse_args():
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(description="Analyze logs and count retries per request.")
    parser.add_argument(
        "--logs-path",
        type=str,
        required=True,
        help="Path to the logs (GCS or local path with wildcards) to analyze."
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default="retry_count_vs_request_count.csv",
        help="Output CSV file to save the results."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(32, os.cpu_count() or 1), #Adjusting workers number.
        help="Number of worker threads for parallel processing."
    )
    return parser.parse_args()

def main():
    """Main function to execute the script."""
    args = parse_args()
    main_start = time.time()
    process_files_optimized(args.logs_path, args.output_file, args.workers)
    logger.info(f"Total execution time: {time.time() - main_start:.2f} seconds")

if __name__ == "__main__":
    main()
    