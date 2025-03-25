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
import fsspec
import gcsfs
import argparse
import logging
import os
import psutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from typing import Tuple, List, Optional
import pathlib

# Initialize the global logger with basic INFO level log.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
logger = logging.getLogger(__name__)

def convert_bytes_to_mib(bytes: int) -> float:
    """Converts bytes to MiB."""
    return bytes / (1024 ** 2)

def get_system_memory() -> Tuple[float, float, float]:
    """Retrieves total, used, and free system memory in MiB."""
    mem = psutil.virtual_memory()
    return convert_bytes_to_mib(mem.total), convert_bytes_to_mib(mem.used), convert_bytes_to_mib(mem.free)

def get_memory_usage() -> float:
    """Retrieves memory usage of the current process in MiB."""
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    return convert_bytes_to_mib(mem_info.rss)

def process_csv(file: str, fs) -> Tuple[Optional[str], Optional[str], pd.DataFrame]:
    """Processes a single CSV file and extracts timestamps and data."""
    try:
        with fs.open(file, 'r') as f:
            df = pd.read_csv(f)
            if not df.empty:
                return df['Timestamp'].iloc[0], df['Timestamp'].iloc[-1], df
            else:
                return None, None, pd.DataFrame()
    except KeyError:
        logger.error(f"Error processing file {file}: Required columns 'Timestamp' not found.")
        return None, None, pd.DataFrame()
    except pd.errors.EmptyDataError:
        logger.warning(f"Empty data in file {file}.")
        return None, None, pd.DataFrame()
    except Exception as e:
        logger.error(f"Error processing file {file}: {e}")
        return None, None, pd.DataFrame()

def analyze_metrics(path: str, timestamp_filter: bool = True) -> Optional[pd.DataFrame]:
    """Analyzes metrics from CSV files in a GCS bucket or local filesystem."""
    try:
        if path.startswith("gs://"):
            fs = gcsfs.GCSFileSystem()
        else:
            fs = fsspec.filesystem("local")

        csv_files = list(fs.glob(path))
        if not csv_files:
            logger.warning(f"No CSV files found at {path}")
            return None

        logger.info(f"Total number of CSV files: {len(csv_files)}")
        total_mem, used_mem, free_mem = get_system_memory()
        logger.info(f"Total system memory: {total_mem:.2f} MiB, Used: {used_mem:.2f} MiB, Free: {free_mem:.2f} MiB")
        logger.info(f"Memory usage by process before loading CSV files: {get_memory_usage():.2f} MiB")

        results = []
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            futures = [executor.submit(process_csv, file, fs) for file in csv_files]
            for future in tqdm(as_completed(futures), total=len(csv_files)):
                results.append(future.result())

        start_timestamps = []
        end_timestamps = []
        all_data = []
        for start, end, df in results:
            if start is not None and end is not None:
                start_timestamps.append(start)
                end_timestamps.append(end)
            all_data.append(df)

        combined_df = pd.concat(all_data)
        logger.info(f"Memory usage by process after loading CSV files: {get_memory_usage():.2f} MiB")

        if not start_timestamps or not end_timestamps:
            logger.warning("No valid timestamps found.")
            return None

        min_timestamp = max(start_timestamps)
        max_timestamp = min(end_timestamps)

        if timestamp_filter:
            combined_df['Timestamp'] = pd.to_datetime(combined_df['Timestamp'], unit='s')
            combined_df = combined_df[
                (combined_df['Timestamp'] >= pd.to_datetime(min_timestamp, unit='s')) &
                (combined_df['Timestamp'] <= pd.to_datetime(max_timestamp, unit='s'))
            ]

        if combined_df.empty:
            logger.warning("No data remains after timestamp filtering.")
            return None

        return combined_df

    except Exception as e:
        logger.error(f"Error in analyze_metrics: {e}")
        return None

def parse_args():
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(description="Analyze metrics from GCS or local files.")
    parser.add_argument(
        "--metrics-path",
        type=str,
        default="gs://vipin-metrics/go-sdk/*.csv",
        help="GCS or local path to metrics CSV files."
    )
    parser.add_argument(
        "--timestamp-filter",
        action="store_true",
        help="Filter data by common timestamps across files."
    )
    return parser.parse_args()

def main():
    """Main function to execute the script."""
    args = parse_args()
    result_df = analyze_metrics(args.metrics_path, args.timestamp_filter)
    if result_df is not None:
        print(result_df['Overall Latency'].describe(percentiles=[0.05, 0.1, 0.25, 0.5, 0.9, 0.99, 0.999, 0.9999, 0.99999, 0.999999, 0.9999999]))

if __name__ == "__main__":
    main()
    