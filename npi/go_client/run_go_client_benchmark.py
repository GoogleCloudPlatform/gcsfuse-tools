#!/usr/bin/env python3
# Copyright 2026 Google LLC
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

"""A script to run the custom Go GCS client benchmark and upload results to BigQuery."""

import argparse
import datetime
import json
import logging
import os
import re
import subprocess
import sys
import time

try:
    from google.cloud import bigquery
    from google.api_core import exceptions
    _BQ_SUPPORTED = True
except ImportError:
    _BQ_SUPPORTED = False

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

def run_command(command, check=True):
    """Runs a command and logs its output."""
    logging.info(f"Running command: {' '.join(command)}")
    try:
        result = subprocess.run(
            command, check=check, capture_output=True, text=True
        )
        if result.stdout:
            logging.info(f"STDOUT:\n{result.stdout.strip()}")
        if result.stderr:
            logging.info(f"STDERR:\n{result.stderr.strip()}")
        return result
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed with exit code {e.returncode}")
        logging.error(f"STDOUT:\n{e.stdout.strip() if e.stdout else 'N/A'}")
        logging.error(f"STDERR:\n{e.stderr.strip() if e.stderr else 'N/A'}")
        raise

def clear_page_cache():
    """Clears system page caches if privileged."""
    logging.info("Clearing page cache...")
    try:
        # Needs privileged container permissions
        subprocess.run(
            ["sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"],
            check=True, capture_output=True
        )
        logging.info("Page cache cleared successfully.")
    except subprocess.CalledProcessError as e:
        logging.warning(f"Failed to clear page cache (probably not running in a privileged container): {e.stderr.strip()}")

def truncate_bq_table(client, project_id, dataset_id, table_id):
    """Erases existing data in the specified BigQuery table."""
    try:
        full_table_id = f"{project_id}.{dataset_id}.{table_id}"
        
        # Check if table exists before truncating
        try:
            client.get_table(full_table_id)
        except exceptions.NotFound:
            logging.info(f"Table {full_table_id} not found, skipping truncation.")
            return

        logging.info(f"--- Erasing data in BigQuery table: {full_table_id} ---")
        query = f"TRUNCATE TABLE `{full_table_id}`"
        query_job = client.query(query)
        query_job.result()
        logging.info(f"Successfully truncated table {full_table_id}")
    except Exception as e:
        logging.error(f"Failed to truncate BigQuery table: {e}")
        raise

def upload_results_to_bq(
    client, project_id, dataset_id, table_id, iteration,
    client_protocol, bandwidth_mibps, num_workers, warm_up_time,
    run_time, grpc_conn_pool_size, bucket_name, object_prefix,
    object_suffix, raw_output
):
    """Uploads iteration benchmark results to BigQuery."""
    try:
        full_table_id = f"{project_id}.{dataset_id}.{table_id}"
        dataset_ref = client.dataset(dataset_id)
        table_ref = dataset_ref.table(table_id)

        # Create dataset if it doesn't exist
        try:
            client.get_dataset(dataset_ref)
        except exceptions.NotFound:
            logging.info(f"Dataset {dataset_id} not found, creating it.")
            client.create_dataset(bigquery.Dataset(dataset_ref))

        # Define schema
        schema = [
            bigquery.SchemaField("run_timestamp", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("iteration", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("client_protocol", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("bandwidth_mibps", "FLOAT", mode="REQUIRED"),
            bigquery.SchemaField("num_workers", "INTEGER"),
            bigquery.SchemaField("warm_up_time", "STRING"),
            bigquery.SchemaField("run_time", "STRING"),
            bigquery.SchemaField("grpc_conn_pool_size", "INTEGER"),
            bigquery.SchemaField("bucket_name", "STRING"),
            bigquery.SchemaField("object_prefix", "STRING"),
            bigquery.SchemaField("object_suffix", "STRING"),
            bigquery.SchemaField("raw_output", "STRING"),
        ]

        # Create table if it doesn't exist
        try:
            client.get_table(table_ref)
        except exceptions.NotFound:
            logging.info(f"Table {table_id} not found, creating it with a new schema.")
            table = bigquery.Table(table_ref, schema=schema)
            client.create_table(table)

        row_to_insert = {
            "run_timestamp": datetime.datetime.utcnow().isoformat(),
            "iteration": iteration,
            "client_protocol": client_protocol,
            "bandwidth_mibps": float(bandwidth_mibps),
            "num_workers": int(num_workers),
            "warm_up_time": warm_up_time,
            "run_time": run_time,
            "grpc_conn_pool_size": int(grpc_conn_pool_size),
            "bucket_name": bucket_name,
            "object_prefix": object_prefix,
            "object_suffix": object_suffix,
            "raw_output": raw_output,
        }

        errors = client.insert_rows_json(full_table_id, [row_to_insert])
        if errors:
            logging.error(f"Errors inserting rows into BigQuery: {errors}")
        else:
            logging.info(
                f"Successfully inserted Go client benchmark results for iteration {iteration} into {full_table_id}"
            )

    except Exception as e:
        logging.error(f"Failed to upload results to BigQuery: {e}")
        logging.error("Please ensure you have run 'gcloud auth application-default login' and have the correct permissions.")

def parse_bandwidth(output_text):
    """Parses the bandwidth from the benchmark command output.
    
    Example: Protocol: http, Bandwidth: 16500 MiB/s
    """
    pattern = r"Bandwidth:\s*(\d+)\s*MiB/s"
    match = re.search(pattern, output_text)
    if match:
        return int(match.group(1))
    return None

def main():
    parser = argparse.ArgumentParser(description="Run Go Storage client benchmark suite.")
    parser.add_argument("--iterations", type=int, default=5, help="Number of test iterations.")
    parser.add_argument("--bucket-name", required=True, help="Name of the GCS bucket.")
    parser.add_argument("--project-id", default=None, help="Project ID for BigQuery results.")
    parser.add_argument("--bq-dataset-id", default=None, help="BigQuery dataset ID.")
    parser.add_argument("--bq-table-id", default="go_client_benchmark", help="BigQuery table ID.")
    parser.add_argument("--client-protocol", required=True, choices=["grpc", "http"], help="Protocol to use.")
    parser.add_argument("--worker", type=int, default=128, help="Number of workers.")
    parser.add_argument("--run-time", default="5m", help="Actual run time.")
    parser.add_argument("--warm-up-time", default="2m", help="Warmup run time.")
    parser.add_argument("--grpc-conn-pool-size", type=int, default=1, help="gRPC connection pool size.")
    parser.add_argument("--obj-prefix", default="1GB/experiment.", help="GCS object name prefix.")
    parser.add_argument("--obj-suffix", default=".0", help="GCS object name suffix.")

    args = parser.parse_args()

    bq_client = None
    if args.project_id and args.bq_dataset_id and args.bq_table_id:
        if not _BQ_SUPPORTED:
            error_msg = (
                "BigQuery operations requested, but 'google-cloud-bigquery' is not "
                "installed. Please run 'pip3 install google-cloud-bigquery'."
            )
            logging.error(error_msg)
            raise RuntimeError(error_msg)
        bq_client = bigquery.Client(project=args.project_id)
        truncate_bq_table(bq_client, args.project_id, args.bq_dataset_id, args.bq_table_id)

    benchmark_bin = "/benchmark"
    if not os.path.exists(benchmark_bin):
        # Try local directory if /benchmark doesn't exist
        benchmark_bin = "./benchmark"
        if not os.path.exists(benchmark_bin):
            logging.error("Benchmark binary not found at /benchmark or ./benchmark.")
            sys.exit(1)

    cmd_args = [
        benchmark_bin,
        f"--bucket={args.bucket_name}",
        f"--client-protocol={args.client_protocol}",
        f"--worker={args.worker}",
        f"--run-time={args.run_time}",
        f"--warm-up-time={args.warm_up_time}",
        f"--grpc-conn-pool-size={args.grpc_conn_pool_size}",
        f"--obj-prefix={args.obj_prefix}",
        f"--obj-suffix={args.obj_suffix}"
    ]

    failed_iterations = []

    for i in range(1, args.iterations + 1):
        logging.info(f"--- Starting Go Client Benchmark Iteration {i}/{args.iterations} ---")
        clear_page_cache()
        
        try:
            result = run_command(cmd_args)
            bw = parse_bandwidth(result.stdout)
            if bw is None:
                logging.error(f"Could not parse bandwidth from iteration {i} output.")
                failed_iterations.append(i)
                continue
            
            logging.info(f"Iteration {i} parsed bandwidth: {bw} MiB/s")

            if bq_client:
                upload_results_to_bq(
                    client=bq_client,
                    project_id=args.project_id,
                    dataset_id=args.bq_dataset_id,
                    table_id=args.bq_table_id,
                    iteration=i,
                    client_protocol=args.client_protocol,
                    bandwidth_mibps=bw,
                    num_workers=args.worker,
                    warm_up_time=args.warm_up_time,
                    run_time=args.run_time,
                    grpc_conn_pool_size=args.grpc_conn_pool_size,
                    bucket_name=args.bucket_name,
                    object_prefix=args.obj_prefix,
                    object_suffix=args.obj_suffix,
                    raw_output=result.stdout
                )
        except Exception as e:
            logging.error(f"Go client benchmark run failed for iteration {i}: {e}")
            failed_iterations.append(i)

        logging.info(f"--- Finished Go Client Benchmark Iteration {i}/{args.iterations} ---")

    if failed_iterations:
        logging.error(f"Some iterations failed: {failed_iterations}")
        sys.exit(1)

    logging.info("--- All Go Client benchmark iterations completed successfully. ---")

if __name__ == "__main__":
    main()
