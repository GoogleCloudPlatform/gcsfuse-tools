# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""GCS operations for distributed benchmarking"""

import os
import json
import tempfile
from . import gcloud_utils


def upload_json(data, gcs_path):
    """Upload JSON data to GCS with retry on failure"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=True) as f:
        json.dump(data, f, indent=2)
        f.flush()
        
        try:
            gcloud_utils.gcloud_storage_cp(f.name, gcs_path, retries=3, check=True)
        except Exception as e:
            raise RuntimeError(f"Failed to upload to {gcs_path} after 3 attempts: {e}")


def download_json(gcs_path):
    """Download and parse JSON from GCS"""
    with tempfile.NamedTemporaryFile(mode='r', suffix='.json', delete=True) as f:
        result = gcloud_utils.gcloud_storage_cp(gcs_path, f.name, retries=1, check=False)
        
        if result.returncode != 0:
            return None
        
        with open(f.name, 'r') as rf:
            return json.load(rf)


def upload_test_cases(csv_path, base_path):
    """Upload test cases CSV to GCS"""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Test cases file not found: {csv_path}")
    
    dest = f"{base_path}/test-cases.csv"
    gcloud_utils.gcloud_storage_cp(csv_path, dest, retries=1, check=True)


def upload_fio_job_file(fio_path, base_path):
    """Upload FIO job template to GCS"""
    if not os.path.exists(fio_path):
        raise FileNotFoundError(f"FIO job file not found: {fio_path}")
    
    dest = f"{base_path}/jobfile.fio"
    gcloud_utils.gcloud_storage_cp(fio_path, dest, retries=1, check=True)


def list_manifests(benchmark_id, artifacts_bucket):
    """List all manifest files for a benchmark"""
    pattern = f"gs://{artifacts_bucket}/{benchmark_id}/results/*/manifest.json"
    result = gcloud_utils.gcloud_storage_ls(pattern, check=False)
    
    if result.returncode != 0:
        return []
    
    return [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]


def download_directory(gcs_path, local_path):
    """Download a directory from GCS"""
    gcloud_utils.gcloud_storage_cp(gcs_path, local_path, recursive=True, retries=1, check=True)


def check_cancellation(benchmark_id, artifacts_bucket):
    """Check if cancellation flag exists in GCS"""
    cancel_path = f"gs://{artifacts_bucket}/{benchmark_id}/cancel"
    result = gcloud_utils.gcloud_storage_ls(cancel_path, check=False)
    return result.returncode == 0
