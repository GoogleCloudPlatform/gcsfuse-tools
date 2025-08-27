import argparse
import os
import time
import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import storage

def _generate_dummy_dataframe(num_rows: int) -> pd.DataFrame:
    """Helper function to generate a Pandas DataFrame with random data."""
    return pd.DataFrame({
        "int_col": np.random.randint(0, 1_000_000, size=num_rows, dtype=np.int32),
        "float_col": np.random.random(size=num_rows),
        "str_col": np.random.choice(['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta', 'theta'], size=num_rows)
    })

def create_parquet_file_gcs(bucket_name: str, blob_name: str, target_size_bytes: int, chunk_rows: int = 1_000_000):
    """Creates a Parquet file in GCS with a target size if it doesn't already exist."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    if blob.exists():
        print(f"Found existing Parquet file at gs://{bucket_name}/{blob_name}. Skipping creation.")
        return

    print(f"Creating Parquet file gs://{bucket_name}/{blob_name} with target size of {target_size_bytes / (1024**3):.2f} GiB...")

    gcs_file = pa.fs.GcsFileSystem()
    total_rows = 0

    schema = pa.schema([
        pa.field("int_col", pa.int32()),
        pa.field("float_col", pa.float64()),
        pa.field("str_col", pa.string())
    ])

    with pq.ParquetWriter(f"gs://{bucket_name}/{blob_name}", schema=schema, filesystem=gcs_file) as writer:
        while True:
            df_chunk = _generate_dummy_dataframe(chunk_rows)
            table = pa.Table.from_pandas(df_chunk, schema=schema)
            writer.write_table(table)
            total_rows += chunk_rows

            # This is an approximation of the size in GCS.
            # For a more accurate size, we would need to check the blob's metadata after each write,
            # but that would slow down the creation process significantly.
            current_size = total_rows * df_chunk.memory_usage(deep=True).sum() / chunk_rows
            print(f"Wrote {total_rows:,} rows, estimated size: {current_size / (1024**2):.2f} MiB")

            if current_size >= target_size_bytes:
                break

    print(f"✅ Done creating: gs://{bucket_name}/{blob_name}")


def run_read_benchmark(file_path: str, num_runs: int = 5) -> list[float]:
    """Runs the read benchmark for a given file path and returns a list of run times."""
    timings = []
    for i in range(num_runs):
        start_time = time.time()
        _ = pl.read_parquet(file_path)
        end_time = time.time()
        run_time = end_time - start_time
        print(f"Run {i+1}/{num_runs}: {run_time:.2f} seconds")
        timings.append(run_time)
    return timings

def run_write_benchmark(df: pl.DataFrame, file_path: str, num_runs: int = 5) -> list[float]:
    """Runs the write benchmark for a given file path and returns a list of run times."""
    timings = []
    for i in range(num_runs):
        start_time = time.time()
        df.write_parquet(file_path)
        end_time = time.time()
        run_time = end_time - start_time
        print(f"Run {i+1}/{num_runs}: {run_time:.2f} seconds")
        timings.append(run_time)
    return timings

def main():
    parser = argparse.ArgumentParser(description="Benchmark Polars with GCSFuse vs. Direct GCS.")
    parser.add_argument("--gcs-bucket", type=str, required=True, help="GCS bucket name.")
    parser.add_argument("--local-path", type=str, required=True, help="Local path to the file on the GCSFuse mount.")
    parser.add_argument("--size-gb", type=float, default=1.0, help="Target size of the Parquet file in GB.")
    parser.add_argument("--benchmark-type", type=str, default="all", choices=["read", "write", "all"], help="Type of benchmark to run.")
    parser.add_argument("--threads", type=int, default=None, help="Number of threads for Polars to use.")
    args = parser.parse_args()

    if args.threads:
        os.environ["POLARS_MAX_THREADS"] = str(args.threads)
        print(f"Polars max threads set to: {os.environ['POLARS_MAX_THREADS']}")


    blob_name = os.path.basename(args.local_path)
    target_size_bytes = int(args.size_gb * 1024**3)
    gcs_path = f"gs://{args.gcs_bucket}/{blob_name}"

    # Create the file in GCS if it doesn't exist
    create_parquet_file_gcs(args.gcs_bucket, blob_name, target_size_bytes)

    if args.benchmark_type in ["read", "all"]:
        # Benchmark GCSFuse Read
        print("\n--- Benchmarking GCSFuse Read Performance ---")
        gcsfuse_timings = run_read_benchmark(args.local_path)
        gcsfuse_min = min(gcsfuse_timings)
        gcsfuse_max = max(gcsfuse_timings)
        gcsfuse_avg = sum(gcsfuse_timings) / len(gcsfuse_timings)
        print(f"GCSFuse Read - Min: {gcsfuse_min:.2f}s, Max: {gcsfuse_max:.2f}s, Avg: {gcsfuse_avg:.2f}s")

        # Benchmark Direct GCS Read
        print("\n--- Benchmarking Direct GCS Read Performance ---")
        direct_gcs_timings = run_read_benchmark(gcs_path)
        direct_gcs_min = min(direct_gcs_timings)
        direct_gcs_max = max(direct_gcs_timings)
        direct_gcs_avg = sum(direct_gcs_timings) / len(direct_gcs_timings)
        print(f"Direct GCS Read - Min: {direct_gcs_min:.2f}s, Max: {direct_gcs_max:.2f}s, Avg: {direct_gcs_avg:.2f}s")

        # Summary
        print("\n--- Read Summary ---")
        print(f"GCSFuse average read time: {gcsfuse_avg:.2f} seconds")
        print(f"Direct GCS average read time: {direct_gcs_avg:.2f} seconds")

    if args.benchmark_type in ["write", "all"]:
        # Prepare a dataframe for writing
        print("\nReading a sample of the data to use for the write benchmark...")
        df_to_write = pl.read_parquet(gcs_path, n_rows=1_000_000)

        # Benchmark GCSFuse Write
        print("\n--- Benchmarking GCSFuse Write Performance ---")
        gcsfuse_write_timings = run_write_benchmark(df_to_write, args.local_path.replace(".parquet", "_write_test.parquet"))
        gcsfuse_write_min = min(gcsfuse_write_timings)
        gcsfuse_write_max = max(gcsfuse_write_timings)
        gcsfuse_write_avg = sum(gcsfuse_write_timings) / len(gcsfuse_write_timings)
        print(f"GCSFuse Write - Min: {gcsfuse_write_min:.2f}s, Max: {gcsfuse_write_max:.2f}s, Avg: {gcsfuse_write_avg:.2f}s")

        # Benchmark Direct GCS Write
        print("\n--- Benchmarking Direct GCS Write Performance ---")
        direct_gcs_write_timings = run_write_benchmark(df_to_write, gcs_path.replace(".parquet", "_write_test.parquet"))
        direct_gcs_write_min = min(direct_gcs_write_timings)
        direct_gcs_write_max = max(direct_gcs_write_timings)
        direct_gcs_write_avg = sum(direct_gcs_write_timings) / len(direct_gcs_write_timings)
        print(f"Direct GCS Write - Min: {direct_gcs_write_min:.2f}s, Max: {direct_gcs_write_max:.2f}s, Avg: {direct_gcs_write_avg:.2f}s")

        # Summary
        print("\n--- Write Summary ---")
        print(f"GCSFuse average write time: {gcsfuse_write_avg:.2f} seconds")
        print(f"Direct GCS average write time: {direct_gcs_write_avg:.2f} seconds")

if __name__ == "__main__":
    main()
