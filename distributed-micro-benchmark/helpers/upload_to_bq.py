import os
import sys
import json
import logging
import datetime
import argparse
import csv
import tempfile
from google.cloud import bigquery, exceptions

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def upload_results_to_bq(results_dir, project_id, dataset_id, table_prefix):
    # 1. Paths
    report_path = os.path.join(results_dir, "combined_report.csv")
    config_path = os.path.join(results_dir, "run-config.json")

    if not os.path.exists(report_path):
        logging.error(f"Report file not found: {report_path}")
        sys.exit(1)

    # 2. Load Run Config for Metadata
    run_config = {}
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            run_config = json.load(f)

    # 3. Setup BigQuery Client
    table_id = f"{table_prefix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    client = bigquery.Client(project=project_id)
    full_table_id = f"{project_id}.{dataset_id}.{table_id}"

    # 4. Ensure Dataset exists
    dataset_ref = client.dataset(dataset_id)
    try:
        client.get_dataset(dataset_ref)
    except exceptions.NotFound:
        logging.info(f"Creating dataset {dataset_id}")
        client.create_dataset(bigquery.Dataset(dataset_ref))

    # 5. Read CSV Data, inject metadata, and write to temp file
    rows = []
    temp_csv = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', newline='')
    temp_csv_path = temp_csv.name

    try:
        with open(report_path, 'r') as csvfile:
            reader = csv.DictReader(csvfile)
            # Ensure we have fieldnames and add metadata columns
            fieldnames = (reader.fieldnames or []) + ['run_timestamp', 'benchmark_id']
            
            writer = csv.DictWriter(temp_csv, fieldnames=fieldnames)
            writer.writeheader()

            for line in reader:
                row = dict(line)
                row['run_timestamp'] = datetime.datetime.utcnow().isoformat()
                row['benchmark_id'] = run_config.get('benchmark_id', 'unknown')
                writer.writerow(row)
                rows.append(row)
        temp_csv.close()

        if not rows:
            logging.warning("No data found in report CSV.")
            return

        # 6. Define Schema based on expected columns in combined_report.csv
        # We assume common metrics from worker.sh + metadata
        schema = [
            bigquery.SchemaField("run_timestamp", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("benchmark_id", "STRING", mode="REQUIRED"),
            # Test Parameters
            bigquery.SchemaField("test_id", "STRING"),
            bigquery.SchemaField("io_type", "STRING"),
            bigquery.SchemaField("file_size", "STRING"),
            bigquery.SchemaField("block_size", "STRING"), # Note: CSV header might be 'bs'
            bigquery.SchemaField("threads", "INTEGER"),
            bigquery.SchemaField("io_depth", "INTEGER"),
            # Configs
            bigquery.SchemaField("config_label", "STRING"),
            bigquery.SchemaField("commit", "STRING"),
            bigquery.SchemaField("mount_args", "STRING"),
            # Metrics (Float for stats)
            bigquery.SchemaField("read_bw", "FLOAT"), # MB/s
            bigquery.SchemaField("write_bw", "FLOAT"), # MB/s
            bigquery.SchemaField("avg_cpu", "FLOAT"),
            bigquery.SchemaField("peak_cpu", "FLOAT"),
            bigquery.SchemaField("avg_mem_mb", "FLOAT"),
            bigquery.SchemaField("avg_net_rx_mbps", "FLOAT"),
            bigquery.SchemaField("avg_net_tx_mbps", "FLOAT"),
        ]

        # Auto-detect other columns or fallback to STRING for unknown columns
        # To make this robust, we can use autodetect=True in load_job_config 
        # instead of hardcoding schema, which is often safer for changing CSVs.

        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV,
            skip_leading_rows=1,
            autodetect=True,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE
        )

        with open(temp_csv_path, "rb") as source_file:
            job = client.load_table_from_file(source_file, full_table_id, job_config=job_config)

        job.result()  # Waits for the job to complete.

        print(f"Uploaded {len(rows)} rows to BigQuery.")
        print(f"RESULT_TABLE_ID={table_id}")
        print(f"Full Table: {full_table_id}")

    except Exception as e:
        logging.error(f"Failed to upload to BQ: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(temp_csv_path):
            os.remove(temp_csv_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True, help="Directory containing combined_report.csv")
    parser.add_argument("--project-id", default="gcs-fuse-test-ml", help="GCP Project ID")
    parser.add_argument("--is-kokoro", action="store_true", help="Set flag for Kokoro runs")
    args = parser.parse_args()

    # Logic from your original script
    if args.is_kokoro:
        dataset = "periodic_benchmarks_trial"
        prefix = "kokoro_run"
    else:
        dataset = "adhoc_benchmarks_trial"
        prefix = "local_run"

    upload_results_to_bq(args.results_dir, args.project_id, dataset, prefix)
