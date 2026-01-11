"""GCS operations for distributed benchmarking"""

import json
import subprocess
import tempfile
import time


def upload_json(data, gcs_path):
    """Upload JSON data to GCS with retry on failure"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(data, f, indent=2)
        f.flush()
        
        cmd = ['gcloud', 'storage', 'cp', f.name, gcs_path]
        for attempt in range(3):
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return
            if attempt < 2:
                print(f"Warning: Upload to {gcs_path} failed (attempt {attempt+1}/3), retrying...")
                time.sleep(2)
        
        raise Exception(f"Failed to upload to {gcs_path} after 3 attempts: {result.stderr}")


def download_json(gcs_path):
    """Download and parse JSON from GCS"""
    with tempfile.NamedTemporaryFile(mode='r', suffix='.json', delete=False) as f:
        cmd = ['gcloud', 'storage', 'cp', gcs_path, f.name]
        result = subprocess.run(cmd, capture_output=True)
        
        if result.returncode != 0:
            return None
        
        with open(f.name, 'r') as rf:
            return json.load(rf)


def upload_test_cases(csv_path, base_path):
    """Upload test cases CSV to GCS"""
    import os
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Test cases file not found: {csv_path}")
    
    dest = f"{base_path}/test-cases.csv"
    cmd = ['gcloud', 'storage', 'cp', csv_path, dest]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"Failed to upload {csv_path}: {result.stderr}")


def upload_fio_job_file(fio_path, base_path):
    """Upload FIO job template to GCS"""
    import os
    if not os.path.exists(fio_path):
        raise FileNotFoundError(f"FIO job file not found: {fio_path}")
    
    dest = f"{base_path}/jobfile.fio"
    cmd = ['gcloud', 'storage', 'cp', fio_path, dest]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"Failed to upload {fio_path}: {result.stderr}")


def list_manifests(benchmark_id, artifacts_bucket):
    """List all manifest files for a benchmark"""
    pattern = f"gs://{artifacts_bucket}/{benchmark_id}/results/*/manifest.json"
    cmd = ['gcloud', 'storage', 'ls', pattern]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        return []
    
    return [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]


def download_directory(gcs_path, local_path):
    """Download a directory from GCS"""
    cmd = ['gcloud', 'storage', 'cp', '-r', gcs_path, local_path]
    subprocess.run(cmd, check=True, capture_output=True)


def check_cancellation(benchmark_id, artifacts_bucket):
    """Check if cancellation flag exists in GCS"""
    cancel_path = f"gs://{artifacts_bucket}/{benchmark_id}/cancel"
    cmd = ['gcloud', 'storage', 'ls', cancel_path]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0
