import json
import datetime
import argparse
from google.cloud import bigquery
import requests

def parse_size_to_bytes(size_str):
    """Converts a FIO size string (e.g., '1M', '128KB') to bytes."""
    if isinstance(size_str, (int, float)):
        return int(size_str)
    size_str = str(size_str).strip().upper()
    if not size_str:
        return 0

    try:
        # Handle two-letter suffixes first (KB, MB, GB, TB)
        if size_str.endswith('KB'):
            return int(size_str[:-2]) * 1024
        elif size_str.endswith('MB'):
            return int(size_str[:-2]) * 1024**2
        elif size_str.endswith('GB'):
            return int(size_str[:-2]) * 1024**3
        elif size_str.endswith('TB'):
            return int(size_str[:-2]) * 1024**4
        # Handle single-letter suffixes
        elif size_str.endswith('K'):
            return int(size_str[:-1]) * 1024
        elif size_str.endswith('M'):
            return int(size_str[:-1]) * 1024**2
        elif size_str.endswith('G'):
            return int(size_str[:-1]) * 1024**3
        elif size_str.endswith('T'):
            return int(size_str[:-1]) * 1024**4
        else:
            # No suffix, assume it's just a number
            return int(size_str)
    except (ValueError, TypeError):
        return 0

def fetch_metadata(attribute):
    url = f"http://metadata.google.internal/computeMetadata/v1/instance/{attribute}"
    headers = {"Metadata-Flavor": "Google"}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"Failed to fetch metadata attribute '{attribute}': {e}")
        return "unknown"

# Set up command-line argument parsing
parser = argparse.ArgumentParser(description="Insert FIO benchmark results into BigQuery.")
parser.add_argument("--result-file", required=True, help="Path to the results.json file")
parser.add_argument("--fio-job-file", required=True, help="Path to the temporary single-job FIO file")
parser.add_argument("--master-fio-file", required=True, help="Path to the original master FIO job file")
parser.add_argument("--project-id", default="gcs-fuse-test-ml", help="GCP project ID")
parser.add_argument("--dataset-id", default="gke_test_tool_outputs", help="BigQuery dataset ID")
parser.add_argument("--table-id", default="vipinydv_fio_outputs", help="BigQuery table ID")
parser.add_argument("--lowest-cpu", required=True, type=float, help="Lowest CPU usage")
parser.add_argument("--highest-cpu", required=True, type=float, help="Highest CPU usage")
parser.add_argument("--lowest-mem", required=True, type=float, help="Lowest Memory usage")
parser.add_argument("--highest-mem", required=True, type=float, help="Highest Memory usage")
parser.add_argument("--gcsfuse-mount-options", required=True, help="GCS Fuse Mount Options")

args = parser.parse_args()

machine_type = fetch_metadata("attributes/MACHINE-TYPE")
vm_name = fetch_metadata("hostname")
unique_id = fetch_metadata("attributes/UNIQUE_ID")

# Load the results file
with open(args.result_file) as f:
    try:
        data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON: {e}")
        exit(1)

# Compose full table ID
full_table_id = f"{args.project_id}.{args.dataset_id}.{args.table_id}"

# Prepare BigQuery client
client = bigquery.Client(project=args.project_id)

# Create dataset if it doesn't exist
dataset_ref = client.dataset(args.dataset_id)
try:
    client.get_dataset(dataset_ref)
except Exception:
    client.create_dataset(bigquery.Dataset(dataset_ref))

# Create table if it doesn't exist
schema = [
    bigquery.SchemaField("fio_workload_id", "STRING"),
    bigquery.SchemaField("experiment_id", "STRING"),
    bigquery.SchemaField("epoch", "INTEGER"),
    bigquery.SchemaField("operation", "STRING"),
    bigquery.SchemaField("file_size", "STRING"),
    bigquery.SchemaField("file_size_in_bytes", "INTEGER"),
    bigquery.SchemaField("block_size", "STRING"),
    bigquery.SchemaField("block_size_in_bytes", "INTEGER"),
    bigquery.SchemaField("num_threads", "INTEGER"),
    bigquery.SchemaField("files_per_thread", "INTEGER"),
    bigquery.SchemaField("bucket_name", "STRING"),
    bigquery.SchemaField("machine_type", "STRING"),
    bigquery.SchemaField("gcsfuse_mount_options", "STRING"),
    bigquery.SchemaField("start_time", "TIMESTAMP"),
    bigquery.SchemaField("end_time", "TIMESTAMP"),
    bigquery.SchemaField("start_epoch", "INTEGER"),
    bigquery.SchemaField("end_epoch", "INTEGER"),
    bigquery.SchemaField("duration_in_seconds", "INTEGER"),
    bigquery.SchemaField("lowest_cpu_usage", "FLOAT"),
    bigquery.SchemaField("highest_cpu_usage", "FLOAT"),
    bigquery.SchemaField("lowest_memory_usage", "FLOAT"),
    bigquery.SchemaField("highest_memory_usage", "FLOAT"),
    bigquery.SchemaField("pod_name", "STRING"),
    bigquery.SchemaField("scenario", "STRING"),
    bigquery.SchemaField("e2e_latency_ns_max", "FLOAT"),
    bigquery.SchemaField("e2e_latency_ns_p50", "FLOAT"),
    bigquery.SchemaField("e2e_latency_ns_p90", "FLOAT"),
    bigquery.SchemaField("e2e_latency_ns_p99", "FLOAT"),
    bigquery.SchemaField("e2e_latency_ns_p99_9", "FLOAT"),
    bigquery.SchemaField("iops", "FLOAT"),
    bigquery.SchemaField("throughput_in_mbps", "FLOAT"),
]

try:
    client.get_table(full_table_id)
except Exception:
    table = bigquery.Table(full_table_id, schema=schema)
    client.create_table(table)

# The JSON file will contain only one aggregated job result
for job in data.get("jobs", []):
    jobname = job.get("jobname")
    job_options = job.get("job options", {})
    
    # Use the master fio file for the ID
    master_fio_basename = args.master_fio_file.split("/")[-1].replace(".fio", "")
    WORKLOAD_ID = f"{master_fio_basename}-{unique_id}"
    EXPERIMENT_ID = f"{master_fio_basename}-{jobname}-{unique_id}"

    file_size_str = job_options.get("filesize", data.get("global options", {}).get("filesize", "unknown"))
    block_size_str = job_options.get("bs", data.get("global options", {}).get("bs", "unknown"))
    
    nrfiles_str = job_options.get("nrfiles", data.get("global options", {}).get("nrfiles"))
    nrfiles = int(nrfiles_str) if nrfiles_str and isinstance(nrfiles_str, str) and nrfiles_str.isdigit() else 0
    num_threads = int(job_options.get("numjobs", data.get("global options", {}).get("numjobs", 0)))
    operation = job_options.get("rw", data.get("global options", {}).get("rw", "unknown"))
    
    read = job.get("read", {})
    write = job.get("write", {})

    read_bw = read.get("bw_bytes", 0) / (1000 * 1000)
    write_bw = write.get("bw_bytes", 0) / (1000 * 1000)
    throughput_in_mbps = read_bw + write_bw
    iops = read.get("iops", 0.0) + write.get("iops", 0.0)
    
    end_epoch_ms = int(data.get("timestamp_ms", data.get("timestamp", 0) * 1000))
    job_runtime_us = int(job.get("job_runtime", 0))
    start_epoch_ms = end_epoch_ms - (job_runtime_us // 1000)
    duration_s = job_runtime_us // 1000000

    clat_ns = read.get("clat_ns", {})
    percentiles = clat_ns.get("percentile", {})
    
    row_to_insert = {
        "fio_workload_id": WORKLOAD_ID,
        "experiment_id": EXPERIMENT_ID,
        "epoch": 1,
        "operation": operation,
        "file_size": file_size_str,
        "file_size_in_bytes": parse_size_to_bytes(job_options.get("size", file_size_str)),
        "block_size": block_size_str,
        "block_size_in_bytes": parse_size_to_bytes(block_size_str),
        "num_threads": num_threads,
        "files_per_thread": nrfiles,
        "bucket_name": fetch_metadata("attributes/GCS_BUCKET_WITH_FIO_TEST_DATA"),
        "machine_type": machine_type,
        "gcsfuse_mount_options": args.gcsfuse_mount_options,
        "start_time": datetime.datetime.utcfromtimestamp(start_epoch_ms / 1000).isoformat(),
        "end_time": datetime.datetime.utcfromtimestamp(end_epoch_ms / 1000).isoformat(),
        "start_epoch": start_epoch_ms,
        "end_epoch": end_epoch_ms,
        "duration_in_seconds": duration_s,
        "lowest_cpu_usage": args.lowest_cpu,
        "highest_cpu_usage": args.highest_cpu,
        "lowest_memory_usage": args.lowest_mem,
        "highest_memory_usage": args.highest_mem,
        "pod_name": vm_name,
        "scenario": "gcsfuse",
        "e2e_latency_ns_max": clat_ns.get("max", 0.0),
        "e2e_latency_ns_p50": percentiles.get("50.000000", 0.0),
        "e2e_latency_ns_p90": percentiles.get("90.000000", 0.0),
        "e2e_latency_ns_p99": percentiles.get("99.000000", 0.0),
        "e2e_latency_ns_p99_9": percentiles.get("99.900000", 0.0),
        "iops": iops,
        "throughput_in_mbps": throughput_in_mbps,
    }

    # Insert row
    errors = client.insert_rows_json(full_table_id, [row_to_insert])
    if errors:
        print("Errors inserting rows:", errors)
    else:
        print(f"Inserted 1 row for job '{jobname}' into {full_table_id}")
