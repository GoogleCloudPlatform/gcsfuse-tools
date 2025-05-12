import polars as pl
import time
import pandas as pd
import pyarrow.parquet as pq
import pyarrow as pa
import numpy as np
import os
import argparse
import sys
import re


def _generate_dummy_dataframe(num_rows: int) -> pd.DataFrame:
    """Helper function to generate a Pandas DataFrame with random data."""
    return pd.DataFrame({
        "int_col": np.random.randint(0, 1_000_000, size=num_rows, dtype=np.int32),
        "float_col": np.random.random(size=num_rows),
        "str_col": np.random.choice(['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta', 'theta'], size=num_rows)
    })


def create_parquet_file_if_not_exists(file_path: str, target_size_bytes: int, chunk_rows: int = 1_000_000):
    """
    Creates a Parquet file with a target size if it doesn't already exist.
    The file_path is expected to be an absolute, user-expanded path.

    Args:
        file_path (str): The absolute path to the Parquet file.
        target_size_bytes (int): The desired target size of the file in bytes.
        chunk_rows (int): Number of rows to generate per chunk.
    """
    if os.path.exists(file_path):
        print(f"File '{file_path}' already exists. Skipping creation.")
        return

    print(f"File '{file_path}' not found. Creating it to approximate target size of {target_size_bytes / (1024**3):.2f} GiB...")

    dir_name = os.path.dirname(file_path)
    if dir_name: # Ensure dirname is not empty (e.g. for relative paths in CWD that become absolute)
        os.makedirs(dir_name, exist_ok=True)

    writer = None
    total_rows = 0
    current_size = 0

    try:
        while True:
            df_chunk = _generate_dummy_dataframe(chunk_rows)
            table = pa.Table.from_pandas(df_chunk)

            if writer is None:
                writer = pq.ParquetWriter(file_path, table.schema)

            writer.write_table(table)
            total_rows += chunk_rows

            if not os.path.exists(file_path): # Should not happen if permissions are correct
                print(f"Warning: File '{file_path}' not created after writing a chunk. Aborting creation.")
                if writer: writer.close() # Attempt to close writer
                return # Exit creation process

            current_size = os.path.getsize(file_path)
            print(f"Wrote {total_rows:,} rows, current file size: {current_size / (1024**2):.2f} MiB")

            if current_size >= target_size_bytes:
                break
    finally:
        if writer:
            writer.close()

    if os.path.exists(file_path):
        final_size_gib = os.path.getsize(file_path) / (1024**3)
        print(f"✅ Done creating: '{file_path}' is ~{final_size_gib:.2f} GiB")
    else:
        print(f"❌ Failed to create file: '{file_path}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read a Parquet file, creating it with dummy data if it doesn't exist.")
    parser.add_argument("--file-path", type=str, help="Path to the Parquet file (e.g., ~/data/my_file.parquet, data/file.parquet).")
    parser.add_argument("--target-size-mb", type=int, help="target size in MB if creation required")

    args = parser.parse_args()

    # Resolve path (handles ~ and makes it absolute)
    resolved_file_path = os.path.abspath(os.path.expanduser(args.file_path))

    create_parquet_file_if_not_exists(resolved_file_path, args.target_size_mb * 1024 * 1024)

    if not os.path.exists(resolved_file_path):
        print(f"❌ Parquet file '{resolved_file_path}' not found and could not be created. Exiting.", file=sys.stderr)
        sys.exit(1)

    print(f"\nAttempting to read Parquet file: '{resolved_file_path}' with Polars...")
    try:
        start_read = time.time()
        df = pl.read_parquet(resolved_file_path)
        end_read = time.time()
        print(f"✅ Parquet file read of {args.target_size_mb} MB took {end_read - start_read:.2f} seconds")

        print("\nDataFrame Head:")
        print(df.head())
        print(f"\nShape: {df.shape}")

    except Exception as e:
        print(f"❌ Error reading Parquet file '{resolved_file_path}' with Polars: {e}", file=sys.stderr)
        sys.exit(1)
