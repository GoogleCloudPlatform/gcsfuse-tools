#!/usr/bin/env python3

# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import csv
import io
import argparse
import logging
from google.cloud import storage
from concurrent.futures import ThreadPoolExecutor, as_completed

# Initialize logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def process_csv_blob(blob):
    """Processes a single CSV blob and finds the maximum latency."""
    try:
        content = blob.download_as_text()
        reader = csv.DictReader(io.StringIO(content))
        max_latency = 0
        max_latency_object = None
        for row in reader:
            try:
                latency = float(row.get("Overall Latency", 0))
                if latency > max_latency:
                    max_latency = latency
                    max_latency_object = row.get("Object Name")
            except ValueError:
                logger.warning(f"Invalid latency value in {blob.name}")
        return max_latency, max_latency_object, blob.name
    except Exception as e:
        logger.error(f"Error processing {blob.name}: {e}")
        return None, None, blob.name

def process_files(path):
    """Processes CSV files in a GCS directory and calculates max overall latency."""
    try:
        client = storage.Client()
        bucket_name, blob_prefix = path.replace("gs://", "").split("/", 1)
        bucket = client.bucket(bucket_name)

        blobs = bucket.list_blobs(prefix=blob_prefix, delimiter='/')

        global_max_latency = 0
        global_max_latency_file = None
        global_max_latency_object = None

        futures = []
        with ThreadPoolExecutor() as executor:
            for blob in blobs:
                if blob.name.endswith('.csv') and '/' not in blob.name[len(blob_prefix):]:
                    logger.info(f"Processing file: {blob.name}")
                    futures.append(executor.submit(process_csv_blob, blob))

            for future in as_completed(futures):
                max_latency, max_latency_object, file_name = future.result()
                if max_latency is not None:
                    logger.info(f"  Max Overall Latency in {file_name}: {max_latency}, Object Name: {max_latency_object}")
                    if max_latency > global_max_latency:
                        global_max_latency = max_latency
                        global_max_latency_file = file_name
                        global_max_latency_object = max_latency_object

        if global_max_latency > 0:
            logger.info(f"\nGlobal Max Overall Latency: {global_max_latency}")
            logger.info(f"File Name: {global_max_latency_file}")
            logger.info(f"Object Name: {global_max_latency_object}")
        else:
            logger.info("\nNo valid latency values found.")

    except Exception as e:
        logger.error(f"Error processing GCS files: {e}")

def main():
    """Main function to execute the script."""
    parser = argparse.ArgumentParser(description="Calculate max Overall Latency from CSV files in GCS.")
    parser.add_argument("--path", required=True, help="GCS path to the directory containing CSV files (e.g., gs://bucket/path/to/directory/)")
    args = parser.parse_args()

    if not args.path.endswith('/'):
        args.path = args.path + '/'

    process_files(args.path)

if __name__ == "__main__":
    main()
    