import json
import datetime
import argparse
from google.cloud import bigquery

# Set up command-line argument parsing
parser = argparse.ArgumentParser(description="Insert FIO benchmark results into BigQuery.")
parser.add_argument("--result-file", required=True, help="Path to the results.json file")
parser.add_argument("--project-id", default="gcs-fuse-test-ml", help="GCP project ID")
parser.add_argument("--dataset-id", default="benchmark_results", help="BigQuery dataset ID")
parser.add_argument("--table-id", default="fio_benchmarks", help="BigQuery table ID")

args = parser.parse_args()

import requests

def fetch_metadata(attribute):
    url = f"http://metadata.google.internal/computeMetadata/v1/instance/attributes/{attribute}"
    headers = {"Metadata-Flavor": "Google"}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"Failed to fetch metadata attribute '{attribute}': {e}")
        return "unknown"

machine_type = fetch_metadata("MACHINE_TYPE")
gcsfuse_version = fetch_metadata("GCSFUSE_VERSION")

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
    bigquery.SchemaField("job_name", "STRING"),
    bigquery.SchemaField("gcsfuse_version", "STRING"),
    bigquery.SchemaField("machine_type", "STRING"),
    bigquery.SchemaField("start_time", "TIMESTAMP"),
    bigquery.SchemaField("file_size", "STRING"),
    bigquery.SchemaField("block_size", "STRING"),
    bigquery.SchemaField("nrfiles", "INTEGER"),
    bigquery.SchemaField("read_bandwidth_MiBps", "FLOAT"),
    bigquery.SchemaField("write_bandwidth_MiBps", "FLOAT"),
    bigquery.SchemaField("IOPS", "FLOAT"),
    bigquery.SchemaField("avg_latency_ms", "FLOAT"),
]

try:
    client.get_table(full_table_id)
except Exception:
    table = bigquery.Table(full_table_id, schema=schema)
    client.create_table(table)

# Convert timestamp to ISO
start_time = datetime.datetime.utcfromtimestamp(data.get("timestamp", 0)).isoformat()

# Prepare rows for insertion
rows = []
for job in data.get("jobs", []):
    jobname = job.get("jobname")
    job_options = job.get("job options", {})

    file_size = job_options.get("filesize", data.get("global options", {}).get("filesize", "unknown"))
    block_size = job_options.get("bs", data.get("global options", {}).get("bs", "unknown"))

    nrfiles_str = job_options.get("nrfiles", data.get("global options", {}).get("nrfiles"))
    nrfiles = int(nrfiles_str) if nrfiles_str and isinstance(nrfiles_str, str) and nrfiles_str.isdigit() else 0

    read = job.get("read", {})
    write = job.get("write", {})

    read_bw = read.get("bw_bytes", 0) / (1024 * 1024)
    write_bw = write.get("bw_bytes", 0) / (1024 * 1024)
    iops = read.get("iops", 0.0) + write.get("iops", 0.0)

    read_lat_ns = read.get("lat_ns", {}).get("mean")
    write_lat_ns = write.get("lat_ns", {}).get("mean")

    if read_lat_ns is not None and write_lat_ns is not None:
        avg_latency_ms = ((read_lat_ns + write_lat_ns) / 2) / 1_000_000
    elif read_lat_ns is not None:
        avg_latency_ms = read_lat_ns / 1_000_000
    elif write_lat_ns is not None:
        avg_latency_ms = write_lat_ns / 1_000_000
    else:
        avg_latency_ms = 0.0

    rows.append({
        "job_name": jobname,
        "gcsfuse_version": gcsfuse_version,
        "machine_type": machine_type,
        "start_time": start_time,
        "file_size": file_size,
        "block_size": block_size,
        "nrfiles": nrfiles,
        "read_bandwidth_MiBps": read_bw,
        "write_bandwidth_MiBps": write_bw,
        "IOPS": iops,
        "avg_latency_ms": avg_latency_ms,
    })

# Insert rows
errors = client.insert_rows_json(full_table_id, rows)
if errors:
    print("Errors inserting rows:", errors)
else:
    print(f"Inserted {len(rows)} row(s) into {full_table_id}")
