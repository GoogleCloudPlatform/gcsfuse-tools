import getpass
import subprocess
import json
from datetime import datetime


def record_benchmark_id_for_user(benchmark_id, bench_type, artifacts_bucket):
    user=getpass.getuser()
    content={
        'user': user,
        'benchmark_id': benchmark_id,
        'type': bench_type,
        'end_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S IST'),
    }

    # Define the path to the runs.json file in the GCS bucket
    gcs_runs_file = f"gs://{artifacts_bucket}/{user}/runs.json"
    local_temp_file = "/tmp/runs.json"

    # 1. Try to download the existing runs.json file
    try:
        download_command = ["gcloud", "storage", "cp", gcs_runs_file, local_temp_file]
        subprocess.run(download_command, check=True, capture_output=True, text=True)
        print(f"Downloaded existing {gcs_runs_file}")
    except subprocess.CalledProcessError as e:
        # If the file doesn't exist, gcloud storage cp will return an error.
        # We'll assume it's a File Not Found error and proceed to create a new file.
        print(f"Could not download {gcs_runs_file}, assuming it does not exist. Error: {e.stderr.strip()}")
        with open(local_temp_file, 'w') as f:
            json.dump([], f) # Initialize with an empty list if file doesn't exist

    # 2. Append the new content to the local JSON file
    try:
        with open(local_temp_file, 'r+') as f:
            file_content = f.read()
            if not file_content:
                data = []
            else:
                data = json.loads(file_content)
            data.append(content)
            f.seek(0) # Rewind to the beginning
            f.truncate() # Clear existing content
            json.dump(data, f, indent=4)
        print(f"Appended new benchmark record to {local_temp_file}")
    except Exception as e:
        print(f"Error appending to local JSON file: {e}")
        return

    # 3. Upload the updated local JSON file back to GCS
    try:
        upload_command = ["gcloud", "storage", "cp", local_temp_file, gcs_runs_file]
        subprocess.run(upload_command, check=True, capture_output=True, text=True)
        print(f"Uploaded updated runs.json to {gcs_runs_file}")
    except Exception as e:
        print(f"Error uploading updated JSON file to GCS: {e}")


if __name__ == '__main__':
    record_benchmark_id_for_user("test-benchmark-123", "feature", "random-non-existent-bucket")
