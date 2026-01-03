"""GCS operations for distributed benchmarking"""

import json
import subprocess
import tempfile


def upload_json(data, gcs_path):
    """Upload JSON data to GCS"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(data, f, indent=2)
        f.flush()
        
        cmd = ['gcloud', 'storage', 'cp', f.name, gcs_path]
        subprocess.run(cmd, check=True, capture_output=True)


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
    dest = f"{base_path}/test-cases.csv"
    cmd = ['gcloud', 'storage', 'cp', csv_path, dest]
    subprocess.run(cmd, check=True, capture_output=True)


def upload_fio_job_file(fio_path, base_path):
    """Upload FIO job template to GCS"""
    dest = f"{base_path}/jobfile.fio"
    cmd = ['gcloud', 'storage', 'cp', fio_path, dest]
    subprocess.run(cmd, check=True, capture_output=True)


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
