#!/usr/bin/env python3
"""
Go Storage-Only Isolation Benchmark.
Replicates the logic of the provided bash script but in Python,
and integrates with BigQuery for result reporting.
"""

import argparse
import datetime
import logging
import os
import shutil
import subprocess
import sys
import time
import re
import uuid
import json

from google.cloud import storage
from google.cloud import bigquery
from google.api_core import exceptions

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def run_command(command, cwd=None, env=None, check=True):
    """Runs a shell command."""
    logging.info(f"Executing: {' '.join(command)}")
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=check,
            capture_output=True,
            text=True
        )
        return result
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed: {e.cmd}")
        logging.error(f"STDOUT: {e.stdout}")
        logging.error(f"STDERR: {e.stderr}")
        raise

def setup_go_environment():
    """Ensures Go is available."""
    if os.path.exists("/usr/local/bin/benchmark_tool"):
        return

    if shutil.which("go"):
        return
    
    # In the container, Go should be installed via Dockerfile.
    # If running locally without Go, this might fail or we could try to install it.
    # For now, assume it's present (Docker).
    logging.error("Go is not found in PATH.")
    sys.exit(1)

def prepare_test_data(project_id, bucket_name):
    """Ensures bucket exists and populates it with test data."""
    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)

    # 1. Check for Bucket
    if not bucket.exists():
        logging.error(f"Bucket {bucket_name} does not exist. Please create it before running the benchmark.")
        sys.exit(1)
    
    logging.info(f"Using existing bucket {bucket_name}.")

    # 2. Generate and Upload Data
    # 128 files of 10MB each.
    # To speed up, we can generate one 10MB file and upload it multiple times?
    # Or generate in memory.
    blob_prefix = "10MB/experiment."
    
    # Check if data exists? The original script just overwrites.
    logging.info("Generating and uploading data (128 x 10MB files)...")
    
    # We'll generate one 10MB payload
    payload = os.urandom(10 * 1024 * 1024)
    
    # Upload concurrently? The python client is synchronous. 
    # For 128 files it might take a bit.
    # Let's use a thread pool for upload speed.
    import concurrent.futures
    
    def upload_blob(i):
        blob_name = f"{blob_prefix}{i}.0"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(payload, content_type='application/octet-stream')
        # logging.info(f"Uploaded {blob_name}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(upload_blob, i) for i in range(128)]
        for future in concurrent.futures.as_completed(futures):
            future.result() # Raise exception if any

    logging.info("Data upload complete.")

def build_benchmark_tool(repo_url, work_dir):
    """Clones and builds the Go benchmark tool."""
    repo_name = repo_url.split("/")[-1]
    repo_path = os.path.join(work_dir, repo_name)
    
    if os.path.exists(repo_path):
        shutil.rmtree(repo_path)
    
    logging.info(f"Cloning {repo_url}...")
    run_command(["git", "clone", repo_url, repo_path])
    
    logging.info("Building benchmark tool...")
    run_command(["go", "build", "."], cwd=repo_path)
    
    return repo_path

def run_tests(executable_path, bucket_name, iterations):
    """Runs the benchmark iterations."""
    http_results = []
    grpc_results = []
    
    executable = executable_path
    cwd = os.path.dirname(executable_path)
    # If using absolute path for executable, we should ensure CWD is correct or irrelevant.
    # The original tool assumes it runs from its dir? 
    # The command was `go build .` then `./custom-go-client-benchmark ...`
    # Let's verify if the tool needs CWD. Usually not unless it loads config files.
    
    if not os.path.isabs(executable):
         executable = "./" + os.path.basename(executable_path)

    for i in range(1, iterations + 1):
        logging.info(f"Iteration {i}/{iterations}")
        
        for proto in ["http", "grpc"]:
            logging.info(f"  Running {proto}...")
            # Flags from original script:
            # --warm-up-time 30s --run-time 2m --worker 128 --bucket ...
            cmd = [
                executable,
                "--warm-up-time", "30s",
                "--run-time", "2m",
                "--worker", "128",
                "--bucket", bucket_name,
                "--client-protocol", proto,
                "--obj-prefix", "10MB/experiment.",
                "--obj-suffix", ".0"
            ]
            
            result = run_command(cmd, cwd=cwd, check=False)
            if result.returncode != 0:
                logging.error(f"Benchmark run failed: {result.stderr}")
                bw = 0.0
            else:
                # Parse output for "Bandwidth: X"
                # Output example expected: "Bandwidth: 1234.56"
                match = re.search(r"Bandwidth: ([\d\.]+)", result.stdout)
                if match:
                    bw = float(match.group(1))
                else:
                    logging.warning(f"Could not parse bandwidth from output: {result.stdout}")
                    bw = 0.0
            
            if proto == "http":
                http_results.append(bw)
            else:
                grpc_results.append(bw)
            
            time.sleep(2)
            
    return http_results, grpc_results


def upload_to_bq(project_id, dataset_id, table_id, results):
    """Uploads results to BigQuery."""
    if not project_id or not dataset_id or not table_id:
        logging.info("Skipping BQ upload (missing credentials/config).")
        return

    client = bigquery.Client(project=project_id)
    dataset_ref = client.dataset(dataset_id)
    table_ref = dataset_ref.table(table_id)
    full_table_id = f"{project_id}.{dataset_id}.{table_id}"

    # Create dataset if it doesn't exist
    try:
        client.get_dataset(dataset_ref)
    except exceptions.NotFound:
        logging.info(f"Dataset {dataset_id} not found, creating it.")
        client.create_dataset(bigquery.Dataset(dataset_ref))

    rows = []
    # Use timezone-aware UTC datetime to avoid DeprecationWarning
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    for i, (h_bw, g_bw) in enumerate(zip(results['http'], results['grpc'])):
        row = {
            "run_timestamp": timestamp,
            "iteration": i + 1,
            "http_bandwidth_mibps": h_bw,
            "grpc_bandwidth_mibps": g_bw,
        }
        rows.append(row)

    # Schema definition
    schema = [
        bigquery.SchemaField("run_timestamp", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("iteration", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("http_bandwidth_mibps", "FLOAT"),
        bigquery.SchemaField("grpc_bandwidth_mibps", "FLOAT"),
    ]

    # Create table if it doesn't exist
    try:
        client.get_table(table_ref)
    except exceptions.NotFound:
        logging.info(f"Table {table_id} not found, creating it.")
        table = bigquery.Table(table_ref, schema=schema)
        client.create_table(table)

    errors = client.insert_rows_json(full_table_id, rows)
    if errors:
        logging.error(f"BQ Upload Errors: {errors}")
    else:
        logging.info(f"Uploaded {len(rows)} rows to {full_table_id}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket-name", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--bq-dataset-id", required=True)
    parser.add_argument("--bq-table-id", required=True)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--gcsfuse-flags", help="Ignored, but kept for compatibility")
    parser.add_argument("--cpu-limit-list", help="Ignored, but kept for compatibility")
    parser.add_argument("--bind-fio", action="store_true", help="Ignored")
    
    args = parser.parse_args()

    setup_go_environment()
    
    # Create work dir
    work_dir = "/tmp/go-benchmark"
    os.makedirs(work_dir, exist_ok=True)

    try:
        prepare_test_data(args.project_id, args.bucket_name)
        
        # Check for pre-built binary
        if os.path.exists("/usr/local/bin/benchmark_tool"):
            logging.info("Using pre-built benchmark tool.")
            executable_path = "/usr/local/bin/benchmark_tool"
            # run_tests will handle the path. We just need to make sure permissions are executable.
            # Docker COPY usually preserves permissions or we can chmod.
        else:
            repo_url = "https://github.com/kislaykishore/custom-go-client-benchmark"
            repo_path = build_benchmark_tool(repo_url, work_dir)
            repo_name = repo_url.split("/")[-1]
            executable_path = os.path.join(repo_path, repo_name)
        
        http_results, grpc_results = run_tests(executable_path, args.bucket_name, args.iterations)
        
        logging.info("Results Summary:")
        logging.info(f"HTTP: {http_results}")
        logging.info(f"gRPC: {grpc_results}")
        
        upload_to_bq(
            args.project_id, 
            args.bq_dataset_id, 
            args.bq_table_id, 
            {'http': http_results, 'grpc': grpc_results},
        )

    finally:
        # Cleanup
        if os.path.exists(work_dir):
            logging.info(f"Cleaning up work directory {work_dir}...")
            shutil.rmtree(work_dir)

if __name__ == "__main__":
    main()
