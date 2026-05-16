#!/usr/bin/env python3
import os
import platform
import subprocess
import json
import sys
import datetime
import glob

try:
    from google.cloud import bigquery
    from google.api_core import exceptions
    _BQ_SUPPORTED = True
except ImportError:
    _BQ_SUPPORTED = False

def get_cpu_arch():
    return platform.machine()

def get_num_cpus():
    return os.cpu_count()

def get_num_numa_nodes():
    try:
        # Count directories matching /sys/devices/system/node/node*
        nodes = glob.glob('/sys/devices/system/node/node*')
        if nodes:
            return len(nodes)
        # Fallback to lscpu if sysfs not accessible or empty
        result = subprocess.run(['lscpu'], capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            if "NUMA node(s):" in line:
                return int(line.split()[-1])
    except Exception as e:
        print(f"Warning: Could not determine NUMA nodes: {e}")
    return 1 # Default to 1 if unknown

def get_kernel_version():
    return platform.release()

def get_page_size():
    try:
        import resource
        return resource.getpagesize()
    except Exception:
        try:
            result = subprocess.run(['getconf', 'PAGESIZE'], capture_output=True, text=True, check=True)
            return int(result.stdout.strip())
        except Exception as e:
            print(f"Warning: Could not determine page size: {e}")
            return 4096 # Default typical page size

def get_ram_bytes():
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    return int(line.split()[1]) * 1024 # Convert KiB to bytes
    except Exception as e:
        print(f"Warning: Could not determine RAM: {e}")
    return 0

def get_num_local_ssds():
    try:
        # Pattern from raid0-script.sh
        devices = glob.glob('/dev/disk/by-id/google-local-nvme-ssd-*')
        return len(devices)
    except Exception as e:
        print(f"Warning: Could not determine local SSDs: {e}")
    return 0

def collect_info():
    return {
        "cpu_arch": get_cpu_arch(),
        "num_cpus": get_num_cpus(),
        "num_numa_nodes": get_num_numa_nodes(),
        "kernel_version": get_kernel_version(),
        "page_size": get_page_size(),
        "ram_bytes": get_ram_bytes(),
        "num_local_ssds": get_num_local_ssds(),
    }

def upload_to_bq(info, project_id, dataset_id, table_id):
    if not _BQ_SUPPORTED:
        print("BigQuery client not available. Skipping upload.")
        return

    client = bigquery.Client(project=project_id)
    full_table_id = f"{project_id}.{dataset_id}.{table_id}"
    dataset_ref = client.dataset(dataset_id)
    table_ref = dataset_ref.table(table_id)

    # Create dataset if it doesn't exist
    try:
        client.get_dataset(dataset_ref)
    except exceptions.NotFound:
        print(f"Dataset {dataset_id} not found, creating it.")
        client.create_dataset(bigquery.Dataset(dataset_ref))

    # Define schema
    schema = [
        bigquery.SchemaField("run_timestamp", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("cpu_arch", "STRING"),
        bigquery.SchemaField("num_cpus", "INTEGER"),
        bigquery.SchemaField("num_numa_nodes", "INTEGER"),
        bigquery.SchemaField("kernel_version", "STRING"),
        bigquery.SchemaField("page_size", "INTEGER"),
        bigquery.SchemaField("ram_bytes", "INTEGER"),
        bigquery.SchemaField("num_local_ssds", "INTEGER"),
    ]

    # Create table if it doesn't exist
    try:
        client.get_table(table_ref)
    except exceptions.NotFound:
        print(f"Table {table_id} not found, creating it.")
        table = bigquery.Table(table_ref, schema=schema)
        client.create_table(table)

    row_to_insert = {
        "run_timestamp": datetime.datetime.utcnow().isoformat(),
        "cpu_arch": info["cpu_arch"],
        "num_cpus": info["num_cpus"],
        "num_numa_nodes": info["num_numa_nodes"],
        "kernel_version": info["kernel_version"],
        "page_size": info["page_size"],
        "ram_bytes": info["ram_bytes"],
        "num_local_ssds": info["num_local_ssds"],
    }

    errors = client.insert_rows_json(full_table_id, [row_to_insert])
    if errors:
        print(f"Errors inserting rows into BigQuery: {errors}", file=sys.stderr)
    else:
        print(f"Successfully inserted host info into {full_table_id}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Record host information.")
    parser.add_argument("--project-id", help="BigQuery project ID.")
    parser.add_argument("--bq-dataset-id", help="BigQuery dataset ID.")
    parser.add_argument("--bq-table-id", default="host_info", help="BigQuery table ID.")
    
    args, _ = parser.parse_known_args()

    info = collect_info()
    print("Collected Host Info:")
    print(json.dumps(info, indent=2))

    if args.project_id and args.bq_dataset_id:
        upload_to_bq(info, args.project_id, args.bq_dataset_id, args.bq_table_id)
    else:
        print("BigQuery project-id and bq-dataset-id not provided. Skipping upload.")

if __name__ == "__main__":
    main()
