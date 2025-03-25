#!/usr/bin/env python3

# Copyright 2025 Google LLC
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

import pandas as pd
import fsspec
import gcsfs
import argparse
import logging
import os
import psutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import numpy as np
from collections import defaultdict
from typing import List, Dict, Optional

# Constants
DEFAULT_PERCENTILES = [10, 50, 90, 99, 99.9, 99.99, 99.999, 99.9999, 99.99999]
DEFAULT_TIME_GAP = 5

# Initialize the global logger with basic INFO level log.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
logger = logging.getLogger(__name__)

def convert_bytes_to_mib(bytes: int) -> float:
    return bytes / (1024 ** 2)

def get_system_memory() -> tuple[float, float, float]:
    mem = psutil.virtual_memory()
    return convert_bytes_to_mib(mem.total), convert_bytes_to_mib(mem.used), convert_bytes_to_mib(mem.free)

def get_memory_usage() -> float:
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    return convert_bytes_to_mib(mem_info.rss)

def calculate_percentiles(latencies: List[float], percentiles_to_calculate: List[float]) -> Dict[str, float]:
    percentiles = {
        'min': np.min(latencies),
        'max': np.max(latencies)
    }
    for p in percentiles_to_calculate:
        percentiles[f'p{p}'] = np.percentile(latencies, p)
    return percentiles

def process_csv(file: str, fs) -> pd.DataFrame:
    try:
        with fs.open(file, 'r') as f:
            df = pd.read_csv(f)
            if 'Overall Latency' not in df.columns:
                logger.warning(f"File {file} does not contain 'Overall Latency' column. Skipping file.")
                return pd.DataFrame()
            if not df.empty:
                df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='s')
                return df
            else:
                return pd.DataFrame()
    except Exception as e:
        logger.error(f"Error processing file {file}: {e}")
        return pd.DataFrame()

def analyze_metrics(path: str, percentiles_to_calculate: List[float], time_gap_minutes: int) -> Optional[pd.DataFrame]:
    try:
        if path.startswith("gs://"):
            fs = gcsfs.GCSFileSystem()
        else:
            fs = fsspec.filesystem("local")

        csv_files = list(fs.glob(path))
        if not csv_files:
            logger.warning(f"No files found at {path}")
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

        time_gap_str = f'{time_gap_minutes}T'
        time_data = defaultdict(list)

        for df in results:
            if not df.empty:
                df['time_gap'] = df['Timestamp'].dt.floor(time_gap_str)
                for gap, group in df.groupby('time_gap'):
                    time_data[gap].extend(group['Overall Latency'].values)

        processed_metrics = []
        for gap, latencies in time_data.items():
            if latencies:
                percentiles = calculate_percentiles(latencies, percentiles_to_calculate)
                metric_row = {'time': gap.strftime('%H:%M'), 'min': percentiles['min']}
                metric_row.update({f'p{p}': percentiles[f'p{p}'] for p in percentiles_to_calculate})
                metric_row['max'] = percentiles['max']
                processed_metrics.append(metric_row)

        result_df = pd.DataFrame(processed_metrics)

        logger.info(f"Memory usage by process after loading CSV files: {get_memory_usage():.2f} MiB")

        if result_df.empty:
            logger.warning("No data to return.")
            return None

        return result_df

    except Exception as e:
        logger.error(f"Error in analyze_metrics: {e}")
        return None

def parse_args():
    parser = argparse.ArgumentParser(description="Analyze metrics from GCS with configurable time gaps.")
    parser.add_argument(
        "--metrics-path",
        type=str,
        default="gs://vipinydv-metrics/slowenvironment-readstall-genericread-1byte/*.csv",
        help="GCS or local path to metrics files."
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default="latency_per_timegap.csv",
        help="Path to save the processed CSV output."
    )
    parser.add_argument(
        "--percentiles",
        type=str,
        default=",".join(map(str, DEFAULT_PERCENTILES)),
        help="Comma-separated list of percentiles to calculate."
    )
    parser.add_argument(
        "--time-gap",
        type=int,
        default=DEFAULT_TIME_GAP,
        help="Time gap in minutes for aggregation."
    )
    return parser.parse_args()

def main():
    args = parse_args()
    percentiles_list = [float(p) for p in args.percentiles.split(',')]
    result_df = analyze_metrics(args.metrics_path, percentiles_list, args.time_gap)

    if result_df is not None:
        output_file = args.output_file
        result_df.to_csv(output_file, index=False)
        logger.info(f"Results have been saved to {output_file}")

if __name__ == "__main__":
    main()
