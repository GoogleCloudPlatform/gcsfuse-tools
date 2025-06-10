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

r"""
This script analyzes GCSFuse stalled read-retry logs for a given job.

It counts the number of retries per request (identified by UUIDs)
and prints a summary table showing how many requests experienced
how many retries.

The input log file is expected to be at '/tmp/<job_name>-logs.csv'.

Input Format:
- CSV file (path derived from job_name: /tmp/<job_name>-logs.csv)
- Each row contains:
    timestamp,textPayload
    2025-05-14T16:31:41.718553833Z,[<uuid>] stalled read-req cancelled after ...

Example usage:

```bash
python retries_by_request.py with-retry-64-nodes-1mb-io

The output will be something like:

Processing file: /tmp/with-retry-64-nodes-1mb-io-logs.csv

Retries    | Requests
-----------+----------
1          | 248
2          | 32
3          | 10

Each row in the table shows how many requests had a specific number of retries.
"""

import pandas as pd
import re
import sys
import argparse
from collections import defaultdict

LOG_PATTERN = re.compile(r'\[(.*?)\] stalled read-req cancelled after')

def main():
    parser = argparse.ArgumentParser(
        description="Analyzes GCSFuse stalled read-retry logs and counts retries per request. "
                    "The log file is expected at /tmp/<job_name>-logs.csv.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "job_name",
        help="Name of the job. Used for log file name construction: "
             "'/tmp/<job_name>-logs.csv'."
    )

    args = parser.parse_args()

    job_name = args.job_name
    # Construct log_file path from job_name
    log_file = f"/tmp/{job_name}-logs.csv"

    print(f"Processing file: {log_file}")

    try:
        df = pd.read_csv(log_file)
    except FileNotFoundError:
        print(f"Log file '{log_file}' not found.")
        print(f"Please ensure the file exists at '/tmp/{job_name}-logs.csv'.")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading log file '{log_file}': {e}")
        sys.exit(1)

    if 'textPayload' not in df.columns:
        print(f"Log file '{log_file}' missing required column 'textPayload'.")
        sys.exit(1)

    retry_counts = defaultdict(int)

    for text in df['textPayload']:
        # Ensure text is a string before searching
        if not isinstance(text, str):
            continue
        match = LOG_PATTERN.search(text)
        if match:
            uuid = match.group(1)
            retry_counts[uuid] += 1

    if not retry_counts:
        print("No retries found in log file.")
        sys.exit(0)

    frequency_counts = defaultdict(int)
    for count in retry_counts.values():
        frequency_counts[count] += 1

    # Print formatted table
    print(f"\n{'Retries':<10} | {'Requests':<10}")
    print(f"{'-'*10}-+-{'-'*10}")

    for retries_count in sorted(frequency_counts.keys()):
        requests_count = frequency_counts[retries_count]
        print(f"{retries_count:<10} | {requests_count:<10}")

if __name__ == "__main__":
    main()
