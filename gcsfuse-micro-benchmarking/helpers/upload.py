import json
import subprocess
import tempfile
import os

def store_metrics_in_artifacts_bucket(metrics, benchmark_id, artifacts_bucket_name, project_id):
    if not all([benchmark_id, artifacts_bucket_name, project_id]):
        print("Input error: Missing parameters"); return

    tmp = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".json")
    try:
        with tmp: json.dump(metrics, tmp, indent=4)
        dest = f"gs://{artifacts_bucket_name}/{benchmark_id}/result.json"
        subprocess.run(['gcloud', '--project', project_id, 'storage', 'cp', tmp.name, dest], check=True, capture_output=True, text=True)
        print(f"Stored metrics in {dest}")
    except subprocess.CalledProcessError as e: print(f"Gcloud error: {e.stderr}")
    except Exception as e: print(f"Error: {e}")
    finally:
        # Clean up the temporary file
        if local_file_path and os.path.exists(local_file_path):
            os.remove(local_file_path)
            print(f"Removed temporary file: {local_file_path}")
