# Copyright 2026 Google LLC
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

import os
import sys
import json
import logging
import datetime
import argparse
import csv
import tempfile
import re
from google.cloud import bigquery, exceptions

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Extracted schema definition to clean up function bodies
BQ_SCHEMA_FIELDS = [
    # Metadata
    ("run_timestamp", "TIMESTAMP"), ("benchmark_id", "STRING"), 
    ("matrix_id", "INTEGER"), ("test_id", "STRING"),
    ("config", "STRING"), ("commit", "STRING"), ("mount_args", "STRING"),
    ("iter", "INTEGER"),
    
    # Parsed IO Params
    ("io_type", "STRING"), ("num_jobs", "INTEGER"), ("file_size", "STRING"),
    ("block_size", "STRING"), ("io_depth", "INTEGER"), ("num_files", "INTEGER"),

    # Metrics
    ("read_bw_mbs", "FLOAT"), ("write_bw_mbs", "FLOAT"), ("read_min_ms", "FLOAT"),
    ("read_max_ms", "FLOAT"), ("read_avg_ms", "FLOAT"), ("read_stddev_ms", "FLOAT"),
    ("read_p50_ms", "FLOAT"), ("read_p90_ms", "FLOAT"), ("read_p99_ms", "FLOAT"),
    
    # Resources & Network
    ("avg_cpu_percent", "FLOAT"), ("peak_cpu_percent", "FLOAT"),
    ("avg_mem_mb", "FLOAT"), ("peak_mem_mb", "FLOAT"),
    ("avg_pgcache_gb", "FLOAT"), ("peak_pgcache_gb", "FLOAT"),
    ("avg_sys_cpu_percent", "FLOAT"), ("peak_sys_cpu_percent", "FLOAT"),
    ("avg_net_rx_mbs", "FLOAT"), ("peak_net_rx_mbs", "FLOAT"),
    ("avg_net_tx_mbs", "FLOAT"), ("peak_net_tx_mbs", "FLOAT"),
]

def normalize_header(header):
    """Converts CSV headers to BigQuery-friendly snake_case."""
    header = header.replace('(MB/s)', '_mbs').replace('(ms)', '_ms').replace('(%)', '_percent')
    header = header.replace('(MB)', '_mb').replace('(GB)', '_gb')
    header = re.sub(r'\s*\(.*?\)', '', header)
    # Replace spaces and special chars with underscores
    header = re.sub(r'[^a-zA-Z0-9_]', '_', header)
    # Collapse multiple underscores
    header = re.sub(r'_+', '_', header)
    return header.strip('_').lower()

def parse_io_params(io_params_str):
    """Parses 'IOType|Jobs|FSize|BS|IOD|NrFiles' into a dict."""
    try:
        parts = io_params_str.split('|')
        if len(parts) >= 6:
            return {
                "io_type": parts[0], "num_jobs": int(parts[1]),
                "file_size": parts[2], "block_size": parts[3],
                "io_depth": int(parts[4]), "num_files": int(parts[5])
            }
    except (ValueError, IndexError) as e:
        logging.warning(f"Failed to parse IO params: {e}")
    return {}

def load_mount_configs_map(results_dir):
    """Loads configs.csv to create a mapping: label -> {mount_args, commit}."""
    config_path = os.path.join(results_dir, "configs.csv")
    config_map = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                for row in csv.DictReader(f):
                    label = row.get('label', '').strip()
                    if label:
                        config_map[label] = {
                            'mount_args': row.get('mount_args', ''),
                            'commit': row.get('commit', '')
                        }
        except Exception as e:
            logging.warning(f"Could not load configs.csv: {e}")
    return config_map

def process_and_write_temp_csv(report_path, config_map, bench_id):
    """Processes report CSV, joins with configs map, formats data, and writes to temp CSV."""
    csv_headers = [f[0] for f in BQ_SCHEMA_FIELDS]
    temp_csv = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', newline='')
    rows_processed = 0

    with open(report_path, 'r') as infile, open(temp_csv.name, 'w', newline='') as outfile:
        reader = csv.DictReader(infile)
        writer = csv.DictWriter(outfile, fieldnames=csv_headers, extrasaction='ignore')
        writer.writeheader()

        header_map = {orig: normalize_header(orig) for orig in (reader.fieldnames or [])}
        current_time = datetime.datetime.utcnow().isoformat()

        for line in reader:
            row_data = {'run_timestamp': current_time, 'benchmark_id': bench_id}

            # Map inputs to normalized headers
            for orig_key, value in line.items():
                if norm_key := header_map.get(orig_key):
                    row_data[norm_key] = value

            # Parse IO Params
            if comp_key := next((k for k in line.keys() if 'IOType' in k), None):
                row_data.update(parse_io_params(line[comp_key]))

            # Cross-product mapping: Link run results to mount configurations
            config_label = row_data.get('config')
            if config_label and config_label in config_map:
                row_data['mount_args'] = config_map[config_label]['mount_args']
                if config_map[config_label]['commit']:
                    row_data['commit'] = config_map[config_label]['commit']
            
            # Format empty numbers to proper SQL nulls
            for field, dtype in BQ_SCHEMA_FIELDS:
                if dtype in ("INTEGER", "FLOAT") and row_data.get(field) in ('', '-'):
                    row_data[field] = None

            writer.writerow(row_data)
            rows_processed += 1

    return temp_csv.name, rows_processed

def upload_results_to_bq(results_dir, project_id, dataset_id, table_prefix):
    """Main execution block combining config reading, processing, and BQ upload."""
    report_path = os.path.join(results_dir, "combined_report.csv")
    if not os.path.exists(report_path):
        logging.error(f"Report file not found: {report_path}")
        sys.exit(1)

    # Load Metadata
    bench_id = 'unknown'
    run_config_path = os.path.join(results_dir, "run-config.json")
    if os.path.exists(run_config_path):
        with open(run_config_path, 'r') as f:
            bench_id = json.load(f).get('benchmark_id', 'unknown')

    config_map = load_mount_configs_map(results_dir)
    
    # Process Data
    temp_csv_path, num_rows = process_and_write_temp_csv(report_path, config_map, bench_id)
    if num_rows == 0:
        logging.warning("No data found in report CSV.")
        os.remove(temp_csv_path)
        return

    # Upload Data
    try:
        client = bigquery.Client(project=project_id)
        dataset_ref = client.dataset(dataset_id)
        
        try:
            client.get_dataset(dataset_ref)
        except exceptions.NotFound:
            logging.info(f"Creating dataset {dataset_id}")
            client.create_dataset(bigquery.Dataset(dataset_ref))

        table_id = f"{table_prefix}_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        full_table_id = f"{project_id}.{dataset_id}.{table_id}"
        
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV,
            skip_leading_rows=1,
            schema=[bigquery.SchemaField(name, dtype) for name, dtype in BQ_SCHEMA_FIELDS],
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION]
        )

        with open(temp_csv_path, "rb") as source_file:
            job = client.load_table_from_file(source_file, full_table_id, job_config=job_config)

        job.result()
        logging.info(f"Uploaded {num_rows} rows to BigQuery table: {full_table_id}")

    except Exception as e:
        logging.error(f"Failed to upload to BQ: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(temp_csv_path):
            os.remove(temp_csv_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--project-id", default="gcs-fuse-test-ml")
    parser.add_argument("--is-kokoro", action="store_true")
    args = parser.parse_args()

    dataset = "periodic_benchmarks" if args.is_kokoro else "adhoc_benchmarks"
    prefix = "kokoro_run" if args.is_kokoro else "local_run"

    upload_results_to_bq(args.results_dir, args.project_id, dataset, prefix)
