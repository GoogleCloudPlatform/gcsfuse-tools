import argparse
import os
import time
import numpy as np
import pandas as pd
import polars as pl
import concurrent.futures
import sys

def _generate_dummy_dataframe(num_rows: int) -> pd.DataFrame:
    """Helper function to generate a Pandas DataFrame with random data."""
    return pd.DataFrame({
        "int_col": np.random.randint(0, num_rows, size=num_rows, dtype=np.int32),
        "float_col": np.random.random(size=num_rows),
        "str_col": np.random.choice(['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta', 'theta'], size=num_rows)
    })

def _write_single_file(df: pl.DataFrame, file_path: str):
    """Helper to write a single parquet file."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    df.write_parquet(file_path)

def run_write_benchmark(df: pl.DataFrame, base_file_path: str, nr_files: int, num_runs: int = 5) -> list[float]:
    """Runs the write benchmark and returns a list of run times."""
    timings = []
    print(f"Writing dataframe with {len(df)} rows to {base_file_path}")
    if nr_files > 1:
        print(f"Writing to {nr_files} files concurrently.")

    for i in range(num_runs):
        start_time = time.time()
        if nr_files == 1:
            print(f"Writing to {base_file_path}")
            _write_single_file(df, base_file_path)
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
        description="Benchmark Polars write performance with a local file path (e.g., GCSFuse) and/or a direct GCS path.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--gcs-path", type=str, default=None, help="GCS path for the output file (e.g., gs://bucket/file.parquet).\nUsed for direct GCS benchmarks.")
    parser.add_argument("--local-path", type=str, default=None, help="Local path for the output file on the GCSFuse mount.\nUsed for GCSFuse benchmarks.")
    parser.add_argument("--threads", type=int, default=None, help="Number of threads for Polars to use.")
    parser.add_argument("--nr-files", type=int, default=1, help="Number of files to write concurrently.")
    args = parser.parse_args()

    if not args.gcs_path and not args.local_path:
        parser.error("At least one of --gcs-path or --local-path must be specified.")

    if args.threads:
        os.environ["POLARS_MAX_THREADS"] = str(args.threads)
        print(f"Polars max threads set to: {os.environ['POLARS_MAX_THREADS']}")

    if args.gcs_path:
        if not args.gcs_path.startswith("gs://"):
            parser.error(f"Invalid GCS path: {args.gcs_path}. Must start with gs://")
        try:
            bucket, blob = args.gcs_path[5:].split('/', 1)
            if not bucket or not blob:
                raise ValueError
        except ValueError:
            parser.error(f"Invalid GCS path: {args.gcs_path}. Must be in format gs://bucket/object")

    gcsfuse_results = {}
    direct_gcs_results = {}

    print("\nGenerating a 400_000_000 row dataframe to use for the write benchmark...")
    pd_df_to_write = _generate_dummy_dataframe(400_000_000)
    df_to_write = pl.from_pandas(pd_df_to_write)

    if args.local_path:
        print("\n--- Benchmarking GCSFuse Write Performance ---")
        gcsfuse_write_timings = run_write_benchmark(df_to_write, args.local_path, args.nr_files)
        gcsfuse_write_avg = sum(gcsfuse_write_timings) / len(gcsfuse_write_timings)
        gcsfuse_results['write_avg'] = gcsfuse_write_avg
        print(f"GCSFuse Write - Min: {min(gcsfuse_write_timings):.2f}s, Max: {max(gcsfuse_write_timings):.2f}s, Avg: {gcsfuse_write_avg:.2f}s")

    if args.gcs_path:
        print("\n--- Benchmarking Direct GCS Write Performance ---")
        direct_gcs_write_timings = run_write_benchmark(df_to_write, args.gcs_path, args.nr_files)
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
