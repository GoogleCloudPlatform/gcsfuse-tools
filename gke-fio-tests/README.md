# FIO Tests for GKE

This directory contains FIO job files designed for high scale performance benchmarking of GCSfuse on Google Kubernetes Engine (GKE). The tests are categorized into subdirectories based on the I/O pattern (e.g., `random_read`, `sequential_write`).

## How to Use

The FIO job files in this directory are templates that expect the target directory to be provided via an environment variable named `DIR`.

To run these tests, you must set the `DIR` environment variable to the path of your GCSfuse mount point before invoking the `fio` command.

### Example

To run a specific FIO job file (e.g., `sequential_read/job.fio`) against a GCSfuse mount at `/my/gcs/mount`, you would use the following command:

```bash
DIR=/my/gcs/mount/fio_sequential_read fio sequential_read/job.fio
```

## Important: Pre-populating Data for Read Tests

The scripts that run the scalability tests expect pre-existing data for read tests. Therefore, the FIO read-test job files in this directory are designed to run against pre-populated data. The GKE testgrid infrastructure uses the GCS bucket `gs://gcsfusecsi-list-storm-hns-bucket` for this purpose.

**If you modify any of the read FIO job files** (e.g., `random_read/*.fio`), you **must** update the contents of the `gs://gcsfusecsi-list-storm-hns-bucket` to match the new test configuration (e.g., `filesize`, `nrfiles`).

Failing to pre-populate the bucket with the correct data will cause the read tests to fail or produce invalid results.

### Directory Structure for Pre-populated Data

The test scripts expect the pre-populated data for read tests to follow a specific directory structure within the GCS bucket. For each read test suite located in a directory (e.g., `random_read`), the corresponding data must be placed in a directory named `fio_<test_suite_name>` at the root of the bucket.

For example:

*   For tests in `random_read/`, the data should be in `gs://gcsfusecsi-list-storm-hns-bucket/fio_random_read/`.
*   For tests in `sequential_read/`, the data should be in `gs://gcsfusecsi-list-storm-hns-bucket/fio_sequential_read/`.

You can use FIO to generate the necessary files locally and then upload them to the correct directory in the GCS bucket.

**Example of generating and uploading data:**

1.  Create a local directory: `mkdir -p /tmp/fio_random_read`
2.  Run FIO to generate files based on the job file: `DIR=/tmp/fio_random_read fio gke-fio-tests/random_read/your_test_file.fio`
3.  Upload the generated files to GCS: `gcloud storage cp -r /tmp/fio_random_read/* gs://gcsfusecsi-list-storm-hns-bucket/fio_random_read/`
