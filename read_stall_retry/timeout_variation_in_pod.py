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

import os
import csv
import re
import argparse
import logging
import fsspec
from typing import List, Tuple
import pathlib

# Initialize logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Precompile the regex
LOG_PATTERN = re.compile(r'(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}).*cancelled after (\d+\.\d+)s')

def parse_log_line(line: str) -> Tuple[str, str] | None:
    """Parses a single log line and returns timestamp and time_taken."""
    match = LOG_PATTERN.match(line)
    if match:
        timestamp = match.group(1).split(" ")[1]  # Extracting HH:MM:SS
        time_taken = match.group(2)
        return timestamp, time_taken
    return None

def extract_data(logs: List[str]) -> List[List[str]]:
    """Extracts timestamp and time_taken from log lines."""
    processed_data = []
    for line in logs:
        result = parse_log_line(line)
        if result:
            processed_data.append(list(result))
    return processed_data

def process_logs(file_path: str, output_file: str):
    """Processes log files from local or GCS."""
    try:
        with fsspec.open(file_path, 'r') as infile:
            logs = infile.readlines()
            processed_data = extract_data(logs)
            write_to_csv(processed_data, output_file)
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
    except Exception as e:
        logger.error(f"Error processing file {file_path}: {e}")

def write_to_csv(processed_data: List[List[str]], output_file: str):
    """Writes processed data to a CSV file."""
    try:
        with open(output_file, 'w', newline='') as outfile:
            writer = csv.writer(outfile)
            writer.writerow(['timestamp', 'time_taken_for_retry'])
            writer.writerows(processed_data)
        logger.info(f"Processed data saved to {output_file}")
    except Exception as e:
        logger.error(f"Error writing to CSV {output_file}: {e}")

def parse_args():
    parser = argparse.ArgumentParser(description="Process timeout variation logs.")
    parser.add_argument("--logs-path", type=str, required=True, help="Path to the log file (local or GCS, e.g., gs://bucket/logs.txt).")
    parser.add_argument("--output-file", type=str, default="pod_timeout_variation.csv", help="Output CSV file name.")
    return parser.parse_args()

def main():
    args = parse_args()
    process_logs(args.logs_path, args.output_file)

if __name__ == "__main__":
    main()
    