# Polars GCS Benchmark

This script benchmarks the performance of writing Parquet files with Polars using two different methods:
1.  **GCSFuse:** Writing to a GCSFuse-mounted directory.
2.  **Direct GCS:** Writing directly to a GCS path (`gs://...`).

## Setup

### 1. Create a virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Mount your GCS bucket with GCSFuse
Create a directory to mount your bucket:
```bash
mkdir -p /path/to/local/mount
```

Mount the bucket using GCSFuse. Replace `your-bucket-name` with your GCS bucket name and `/path/to/local/mount` with the path you just created.
```bash
gcsfuse your-bucket-name /path/to/local/mount
```

## Running the Benchmark

The script can run write benchmarks against a GCSFuse mount path, a direct GCS path, or both.

### Example: Benchmarking both GCSFuse and Direct GCS
```bash
python3 benchmark.py \
  --gcs-path gs://your-bucket-name/data/direct/test.parquet \
  --local-path /path/to/local/mount/data/fuse/test.parquet \
  --approx-file-size-mb 1000
```

### Example: Benchmarking only GCSFuse
```bash
python3 benchmark.py --local-path /path/to/local/mount/test.parquet
```

### Example: Benchmarking only Direct GCS
```bash
python3 benchmark.py --gcs-path gs://your-bucket-name/test.parquet --size-gb 4
```

```bash
### All Arguments
*   `--gcs-path`: The GCS path to the file (e.g., `gs://bucket/file.parquet`). Used for direct GCS benchmarks.
*   `--local-path`: The full path to the test file on the GCSFuse mount. Used for GCSFuse benchmarks.
*   `--size-gb`: The target size of the Parquet file in gigabytes (GB).
*   `--benchmark-type`: The type of benchmark to run. Choices are `read`, `write`, or `all`. Defaults to `all`.
*   `--threads`: The number of threads for Polars to use. Defaults to the Polars default.

## Example Output
```
Found existing Parquet file at gs://your-bucket-name/test.parquet. Skipping creation.

--- Benchmarking GCSFuse Read Performance ---
Run 1/5: 10.52 seconds
Run 2/5: 10.48 seconds
Run 3/5: 10.55 seconds
Run 4/5: 10.51 seconds
Run 5/5: 10.53 seconds
GCSFuse Read - Min: 10.48s, Max: 10.55s, Avg: 10.52s

--- Benchmarking Direct GCS Read Performance ---
Run 1/5: 2.34 seconds
Run 2/5: 2.32 seconds
Run 3/5: 2.35 seconds
Run 4/5: 2.33 seconds
Run 5/5: 2.36 seconds
Direct GCS Read - Min: 2.32s, Max: 2.36s, Avg: 2.34s

--- Read Summary ---
GCSFuse average read time: 10.52 seconds
Direct GCS average read time: 2.34 seconds

Reading a sample of the data to use for the write benchmark...

--- Benchmarking GCSFuse Write Performance ---
Run 1/5: 5.12 seconds
Run 2/5: 5.09 seconds
Run 3/5: 5.15 seconds
Run 4/5: 5.11 seconds
Run 5/5: 5.13 seconds
GCSFuse Write - Min: 5.09s, Max: 5.15s, Avg: 5.12s

--- Benchmarking Direct GCS Write Performance ---
Run 1/5: 1.45 seconds
Run 2/5: 1.43 seconds
Run 3/5: 1.46 seconds
Run 4/5: 1.44 seconds
Run 5/5: 1.47 seconds
Direct GCS Write - Min: 1.43s, Max: 1.47s, Avg: 1.45s

--- Write Summary ---
GCSFuse average write time: 5.12 seconds
Direct GCS average write time: 1.45 seconds
```

## Running with Docker

You can also run the benchmark in a Docker container. This is useful for ensuring a consistent environment.

### 1. Build the Docker image
From the `polars_benchmark` directory, run:
```bash
docker build -t polars-gcs-benchmark .
```

### 2. Run the benchmark in a container

To run the benchmark, you need to provide your GCS credentials to the container. The easiest way to do this is by mounting your gcloud configuration directory.

**Note on GCSFuse in Docker:** Running GCSFuse inside a container requires the container to have special privileges. You need to run the container with the `--privileged` flag.

First, create a local directory for the GCSFuse mount:
```bash
mkdir -p /tmp/gcs-mount
```

Then, run the container. Replace `your-bucket-name` with your GCS bucket name.

```bash
docker run --rm -it --privileged \
  -v ~/.config/gcloud:/root/.config/gcloud \
  -v /tmp/gcs-mount:/gcs \
  polars-gcs-benchmark \
  --gcs-path gs://your-bucket-name/test.parquet \
  --local-path /gcs/test.parquet \
  --size-gb 1
```

This command does the following:
*   `--rm`: Removes the container when it exits.
*   `-it`: Runs the container in interactive mode.
*   `--privileged`: Gives the container the necessary permissions to run GCSFuse.
*   `-v ~/.config/gcloud:/root/.config/gcloud`: Mounts your local gcloud configuration, which contains your GCS credentials.
*   `-v /tmp/gcs-mount:/gcs`: Mounts a local directory that will be used by GCSFuse inside the container.
*   The rest of the arguments are passed to the `benchmark.py` script.
