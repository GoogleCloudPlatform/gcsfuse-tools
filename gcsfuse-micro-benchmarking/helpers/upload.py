import json
import subprocess
import tempfile
import os
import shlex

def store_metrics_in_artifacts_bucket(
    metrics: dict,
    benchmark_id: str,
    artifacts_bucket_name: str,
    project_id: str
):
    """
    Stores a metrics dictionary as a JSON file in a Google Cloud Storage bucket
    using gcloud storage cp, specifying the project ID.

    Args:
        metrics (dict): A dictionary containing the metrics to store.
        benchmark_id (str): The identifier for the benchmark, used as a folder name.
        artifacts_bucket_name (str): The name of the GCS bucket.
        project_id (str): The Google Cloud Project ID to use for the gcloud command.
    """
    local_file_path = None  # Initialize to None
    try:
        # Validate inputs to avoid empty paths or IDs
        if not benchmark_id:
            raise ValueError("benchmark_id cannot be empty")
        if not artifacts_bucket_name:
            raise ValueError("artifacts_bucket_name cannot be empty")
        if not project_id:
            raise ValueError("project_id cannot be empty")

        # Create a temporary file to store the JSON data
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".json") as tmp_file:
            json.dump(metrics, tmp_file, indent=4)
            local_file_path = tmp_file.name

        # Define the destination GCS path
        gcs_dest_path = f"gs://{artifacts_bucket_name}/{benchmark_id}/result.json"

        # Construct the gcloud storage cp command as a list
        command = [
            'gcloud',
            '--project', project_id,
            'storage',
            'cp',
            local_file_path,
            gcs_dest_path
        ]

        # Execute the command
        print(f"Running command: {command}")
        # Explicitly set shell=False for security and clarity
        result = subprocess.run(command, check=True, capture_output=True, text=True, shell=False)

        print(f"Successfully stored metrics in {gcs_dest_path} for project {project_id}")
        if result.stdout:
            print("gcloud stdout:\n", result.stdout)
        if result.stderr:
            # gcloud storage can put progress on stderr
            print("gcloud stderr:\n", result.stderr)

    except subprocess.CalledProcessError as e:
        print(f"Error during gcloud command execution:")
        print(f"Command: {e.cmd}")
        print(f"Return Code: {e.returncode}")
        print(f"Stdout:\n{e.stdout}")
        print(f"Stderr:\n{e.stderr}")
    except ValueError as e:
        print(f"Input error: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        # Clean up the temporary file
        if local_file_path and os.path.exists(local_file_path):
            os.remove(local_file_path)
            print(f"Removed temporary file: {local_file_path}")

# Example Usage:
if __name__ == '__main__':
    # Replace with your actual bucket name and benchmark ID
    MY_ARTIFACTS_BUCKET = ""  # Example: "my-ml-artifacts"
    MY_BENCHMARK_ID = ""
    PROJECT=""

    metrics = {
        '4KB_1MB_1_read_1_1': {
            'fio_metrics': {'avg_read_throughput_kibps': 3172.0, 'stdev_read_throughput_kibps': 63.6396, 'avg_write_throughput_kibps': 0.0, 'stdev_write_throughput_kibps': 0.0, 'avg_read_latency_ns': 2705.68, 'stdev_read_latency_ns': 225.296, 'avg_write_latency_ns': 0.0, 'stdev_write_latency_ns': 0.0},
            'vm_metrics': {}, 'cpu_percent_per_gbps': 0.12345, 'bs': '4KB', 'file_size': '1MB', 'iodepth': '1', 'iotype': 'read', 'threads': '1', 'nrfiles': '1'
        },
        '8KB_2MB_2_write_1_1': {
            'fio_metrics': {'avg_read_throughput_kibps': 0.0, 'stdev_read_throughput_kibps': 0.0, 'avg_write_throughput_kibps': 5200.0, 'stdev_write_throughput_kibps': 150.0, 'avg_read_latency_ns': 0.0, 'stdev_write_latency_ns': 0.0, 'avg_write_latency_ns': 1800.0, 'stdev_write_latency_ns': 100.0},
            'vm_metrics': {}, 'cpu_percent_per_gbps': 0.23456, 'bs': '8KB', 'file_size': '2MB', 'iodepth': '2', 'iotype': 'write', 'threads': '1', 'nrfiles': '1'
        }
    }

    store_metrics_in_artifacts_bucket(metrics, MY_BENCHMARK_ID, MY_ARTIFACTS_BUCKET,PROJECT)

