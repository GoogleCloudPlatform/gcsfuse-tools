import argparse
import os
import time
import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import storage
import concurrent.futures
import sys

def _generate_dummy_dataframe(num_rows: int) -> pd.DataFrame:
    """Helper function to generate a Pandas DataFrame with random data."""
    return pd.DataFrame({
        "int_col": np.random.randint(0, 1_000_000, size=num_rows, dtype=np.int32),
        "float_col": np.random.random(size=num_rows),
        "str_col": np.random.choice(['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta', 'theta'], size=num_rows)
    })

def _create_single_parquet_file_gcs(bucket_name: str, blob_name: str, target_size_bytes: int, chunk_rows: int, is_parallel: bool):
    """Helper to create a single Parquet file in GCS."""
    if not is_parallel:
        print(f"Creating Parquet file gs://{bucket_name}/{blob_name} with target size of {target_size_bytes / (1024**3):.2f} GiB...")
    gcs_file = pa.fs.GcsFileSystem()
    total_rows = 0
    schema = pa.schema([
        pa.field("int_col", pa.int32()),
        pa.field("float_col", pa.float64()),
        pa.field("str_col", pa.string())
    ])
    with pq.ParquetWriter(f"{bucket_name}/{blob_name}", schema=schema, filesystem=gcs_file) as writer:
        while True:
            df_chunk = _generate_dummy_dataframe(chunk_rows)
            table = pa.Table.from_pandas(df_chunk, schema=schema)
            writer.write_table(table)
            total_rows += chunk_rows
            current_size = total_rows * df_chunk.memory_usage(deep=True).sum() / chunk_rows
            if not is_parallel:
                print(f"Wrote {total_rows:,} rows, estimated size: {current_size / (1024**2):.2f} MiB")
            if current_size >= target_size_bytes:
                break
    if not is_parallel:
        print(f"✅ Done creating: gs://{bucket_name}/{blob_name}")

def create_parquet_files_gcs(bucket_name: str, blob_name: str, target_size_bytes: int, nr_files: int, chunk_rows: int = 1_000_000):
    """Creates one or more Parquet files in GCS with a target size if they don't already exist."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    if nr_files == 1:
        files_to_check = [blob_name]
    else:
        path_parts = os.path.splitext(blob_name)
        files_to_check = [f"{path_parts[0]}_{j}{path_parts[1]}" for j in range(nr_files)]

    first_blob = bucket.blob(files_to_check[0])
    if first_blob.exists():
        print(f"Found existing Parquet file at gs://{bucket_name}/{files_to_check[0]}. Assuming all files exist. Skipping creation.")
        return

    if nr_files > 1:
        print(f"Creating {nr_files} Parquet files in parallel in gs://{bucket_name}/{os.path.dirname(blob_name)}/ with target size of {target_size_bytes / (1024**3):.2f} GiB each...")

    if nr_files == 1:
        _create_single_parquet_file_gcs(bucket_name, blob_name, target_size_bytes, chunk_rows, is_parallel=False)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=nr_files) as executor:
            futures = [executor.submit(_create_single_parquet_file_gcs, bucket_name, f, target_size_bytes, chunk_rows, is_parallel=True) for f in files_to_check]
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"Error creating file: {e}", file=sys.stderr)
        print(f"✅ Done creating {nr_files} files.")

def _read_single_file(file_path: str):
    """Helper to read a single parquet file."""
    _ = pl.read_parquet(file_path)

def run_read_benchmark(file_path: str, nr_files: int, num_runs: int = 5) -> list[float]:
    """Runs the read benchmark for a given file path and returns a list of run times."""
    timings = []
    if nr_files > 1:
        print(f"Reading from {nr_files} files concurrently.")

    for i in range(num_runs):
        start_time = time.time()
        if nr_files == 1:
            _ = pl.read_parquet(file_path)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=nr_files) as executor:
                futures = []
                path_parts = os.path.splitext(file_path)
                for j in range(nr_files):
                    parallel_file_path = f"{path_parts[0]}_{j}{path_parts[1]}"
                    futures.append(executor.submit(_read_single_file, parallel_file_path))
                concurrent.futures.wait(futures)

        end_time = time.time()
        run_time = end_time - start_time
        print(f"Run {i+1}/{num_runs}: {run_time:.2f} seconds")
        timings.append(run_time)
    return timings

def _write_single_file(df: pl.DataFrame, file_path: str):
    """Helper to write a single parquet file."""
    df.write_parquet(file_path)

def run_write_benchmark(df: pl.DataFrame, base_file_path: str, nr_files: int, num_runs: int = 5) -> list[float]:
    """Runs the write benchmark and returns a list of run times."""
    timings = []
    if nr_files > 1:
        print(f"Writing to {nr_files} files concurrently.")

    for i in range(num_runs):
        start_time = time.time()
        if nr_files == 1:
            df.write_parquet(base_file_path)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=nr_files) as executor:
                futures = []
                for j in range(nr_files):
                    path_parts = os.path.splitext(base_file_path)
                    file_path = f"{path_parts[0]}_{j}{path_parts[1]}"
                    futures.append(executor.submit(_write_single_file, df, file_path))
                concurrent.futures.wait(futures)

        end_time = time.time()
        run_time = end_time - start_time
        print(f"Run {i+1}/{num_runs}: {run_time:.2f} seconds")
        timings.append(run_time)
    return timings

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark Polars read/write performance with a local file path (e.g., GCSFuse) and/or a direct GCS path.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--gcs-path", type=str, default=None, help="GCS path to the file (e.g., gs://bucket/file.parquet).\nUsed for direct GCS benchmarks.")
    parser.add_argument("--local-path", type=str, default=None, help="Local path to the file on the GCSFuse mount.\nUsed for GCSFuse benchmarks.")
    parser.add_argument("--size-gb", type=float, default=1.0, help="Target size of the Parquet file in GB.")
    parser.add_argument("--benchmark-type", type=str, default="all", choices=["read", "write", "all"], help="Type of benchmark to run.")
    parser.add_argument("--threads", type=int, default=None, help="Number of threads for Polars to use.")
    parser.add_argument("--nr-files", type=int, default=1, help="Number of files to read/write concurrently.")
    args = parser.parse_args()

    if not args.gcs_path and not args.local_path:
        parser.error("At least one of --gcs-path or --local-path must be specified.")

    if args.threads:
        os.environ["POLARS_MAX_THREADS"] = str(args.threads)
        print(f"Polars max threads set to: {os.environ['POLARS_MAX_THREADS']}")

    gcs_bucket = None
    blob_name = None
    if args.gcs_path:
        if not args.gcs_path.startswith("gs://"):
            parser.error(f"Invalid GCS path: {args.gcs_path}. Must start with gs://")
        try:
            gcs_bucket, blob_name = args.gcs_path[5:].split('/', 1)
            if not gcs_bucket or not blob_name:
                raise ValueError
        except ValueError:
            parser.error(f"Invalid GCS path: {args.gcs_path}. Must be in format gs://bucket/object")

    # Create the file in GCS if a GCS path is provided.
    # If only a local path is provided, we assume the file must already exist.
    if gcs_bucket and blob_name:
        target_size_bytes = int(args.size_gb * 1024**3)
        create_parquet_files_gcs(gcs_bucket, blob_name, target_size_bytes, args.nr_files)
    elif args.local_path and not args.gcs_path:
        print("Only --local-path provided. Assuming file exists. Skipping GCS file creation.")
        if not os.path.exists(args.local_path):
            print(f"Error: File not found at --local-path: {args.local_path}", file=sys.stderr)
            sys.exit(1)

    gcsfuse_results = {}
    direct_gcs_results = {}

    if args.benchmark_type in ["read", "all"]:
        if args.local_path:
            print("\n--- Benchmarking GCSFuse Read Performance ---")
            gcsfuse_timings = run_read_benchmark(args.local_path, args.nr_files)
            gcsfuse_avg = sum(gcsfuse_timings) / len(gcsfuse_timings)
            gcsfuse_results['read_avg'] = gcsfuse_avg
            print(f"GCSFuse Read - Min: {min(gcsfuse_timings):.2f}s, Max: {max(gcsfuse_timings):.2f}s, Avg: {gcsfuse_avg:.2f}s")

        if args.gcs_path:
            print("\n--- Benchmarking Direct GCS Read Performance ---")
            direct_gcs_timings = run_read_benchmark(args.gcs_path, args.nr_files)
            direct_gcs_avg = sum(direct_gcs_timings) / len(direct_gcs_timings)
            direct_gcs_results['read_avg'] = direct_gcs_avg
            print(f"Direct GCS Read - Min: {min(direct_gcs_timings):.2f}s, Max: {max(direct_gcs_timings):.2f}s, Avg: {direct_gcs_avg:.2f}s")

        # Summary
        print("\n--- Read Summary ---")
        if 'read_avg' in gcsfuse_results:
            print(f"GCSFuse average read time: {gcsfuse_results['read_avg']:.2f} seconds")
        if 'read_avg' in direct_gcs_results:
            print(f"Direct GCS average read time: {direct_gcs_results['read_avg']:.2f} seconds")

    if args.benchmark_type in ["write", "all"]:
        print("\nGenerating a 1,000,000 row dataframe to use for the write benchmark...")
        pd_df_to_write = _generate_dummy_dataframe(1_000_000)
        df_to_write = pl.from_pandas(pd_df_to_write)

        if args.local_path:
            print("\n--- Benchmarking GCSFuse Write Performance ---")
            local_write_path = args.local_path.replace(".parquet", "_write_test.parquet")
            gcsfuse_write_timings = run_write_benchmark(df_to_write, local_write_path, args.nr_files)
            gcsfuse_write_avg = sum(gcsfuse_write_timings) / len(gcsfuse_write_timings)
            gcsfuse_results['write_avg'] = gcsfuse_write_avg
            print(f"GCSFuse Write - Min: {min(gcsfuse_write_timings):.2f}s, Max: {max(gcsfuse_write_timings):.2f}s, Avg: {gcsfuse_write_avg:.2f}s")

        if args.gcs_path:
            print("\n--- Benchmarking Direct GCS Write Performance ---")
            gcs_write_path = args.gcs_path.replace(".parquet", "_write_test.parquet")
            direct_gcs_write_timings = run_write_benchmark(df_to_write, gcs_write_path, args.nr_files)
            direct_gcs_write_avg = sum(direct_gcs_write_timings) / len(direct_gcs_write_timings)
            direct_gcs_results['write_avg'] = direct_gcs_write_avg
            print(f"Direct GCS Write - Min: {min(direct_gcs_write_timings):.2f}s, Max: {max(direct_gcs_write_timings):.2f}s, Avg: {direct_gcs_write_avg:.2f}s")

        # Summary
        print("\n--- Write Summary ---")
        if 'write_avg' in gcsfuse_results:
            print(f"GCSFuse average write time: {gcsfuse_results['write_avg']:.2f} seconds")
        if 'write_avg' in direct_gcs_results:
            print(f"Direct GCS average write time: {direct_gcs_results['write_avg']:.2f} seconds")

if __name__ == "__main__":
    main()
