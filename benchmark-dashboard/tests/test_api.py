import os
os.environ["GCP_PROJECT"] = "test-project"
import sys
import tempfile
import pytest
from unittest.mock import patch, MagicMock

# Create a temporary file for the database and override db.DB_PATH before importing main
db_fd, temp_db_path = tempfile.mkstemp()

# Add parent directory and module directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
db.DB_PATH = temp_db_path
db.init_db()

from fastapi.testclient import TestClient
from main import app, DMB_DIR

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def mock_project_id():
    """Mock GCE metadata query to consistently return 'test-project' for tests."""
    with patch("main.get_gce_project_id", return_value="test-project") as m:
        yield m


@pytest.fixture(scope="session", autouse=True)
def cleanup_database():
    """Session teardown to clean up temporary database file and custom test files."""
    yield
    # Close and remove temp database
    os.close(db_fd)
    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)
    
    # Remove any test files created during tests
    test_fio = DMB_DIR / "test_suites" / "custom_fio_configs" / "test_temp_api_fio.fio"
    if test_fio.exists():
        test_fio.unlink()


def test_get_files():
    """Verify that available config files are successfully listed."""
    res = client.get("/api/configs/files")
    assert res.status_code == 200
    data = res.json()
    assert "test_cases" in data
    assert "fio_jobs" in data
    assert len(data["fio_jobs"]) > 0


def test_get_preview_valid():
    """Verify preview returns content for a valid, safe path."""
    # Find a valid file from test_suites
    res_files = client.get("/api/configs/files")
    valid_fio_file = res_files.json()["fio_jobs"][0]
    
    res = client.get(f"/api/configs/preview?path={valid_fio_file}")
    assert res.status_code == 200
    assert "content" in res.json()
    assert len(res.json()["content"]) > 0


def test_get_preview_security_traversal():
    """Verify path traversal attempts are blocked with 403 Forbidden."""
    # Attempt parent path traversal
    res = client.get("/api/configs/preview?path=../../main.py")
    assert res.status_code == 403
    assert "Access denied" in res.json()["detail"]


def test_create_custom_fio_job():
    """Verify that posting a custom FIO template successfully saves the file."""
    payload = {
        "filename": "test_temp_api_fio.fio",
        "content": "[global]\nioengine=libaio\ndirect=1\n[job1]\nsize=5m"
    }
    res = client.post("/api/configs/fio-jobs", json=payload)
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert "test_suites/custom_fio_configs/test_temp_api_fio.fio" in res.json()["path"]
    
    # Verify it is listed in the files endpoint
    res_files = client.get("/api/configs/files")
    assert any("test_temp_api_fio.fio" in f for f in res_files.json()["fio_jobs"])


@patch("main.bigquery.Client")
@patch("main.storage.Client")
def test_enqueue_run(mock_gcs, mock_bq):
    """Verify that enqueuing a run caches it in the database and returns status queued."""
    # Find valid files
    res_files = client.get("/api/configs/files")
    test_csv = res_files.json()["test_cases"][0]
    fio_job = res_files.json()["fio_jobs"][0]
    
    payload = {
        "username": "test_ldap",
        "description": "API Test Run",
        "executor_vm": "test-vm",
        "zone": "us-central1-c",
        "project": "test-project",
        "artifacts_bucket": "test-artifacts-bucket",
        "test_data_bucket": "test-data-bucket",
        "test_csv": test_csv,
        "fio_job": fio_job,
        "iterations": 1
    }
    
    res = client.post("/api/runs", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "benchmark_id" in data
    assert data["status"] == "queued"
    
    # Check that it exists in the active queue
    res_active = client.get("/api/runs/active")
    assert res_active.status_code == 200
    active_runs = res_active.json()
    assert len(active_runs) > 0
    assert active_runs[0]["benchmark_id"] == data["benchmark_id"]
    assert active_runs[0]["username"] == "test_ldap"
    assert active_runs[0]["status"] == "queued"


def test_get_progress_for_queued_run():
    """Verify progress endpoint returns a safe default for a queued run (no GCS assets yet)."""
    # Enqueue a run first
    res_files = client.get("/api/configs/files")
    payload = {
        "username": "test_ldap",
        "description": "Progress Test Run",
        "executor_vm": "progress-vm",
        "zone": "us-central1-c",
        "project": "test-project",
        "artifacts_bucket": "test-artifacts-bucket",
        "test_data_bucket": "test-data-bucket",
        "test_csv": res_files.json()["test_cases"][0],
        "fio_job": res_files.json()["fio_jobs"][0],
        "iterations": 1
    }
    res_enqueue = client.post("/api/runs", json=payload)
    benchmark_id = res_enqueue.json()["benchmark_id"]
    
    # Query progress
    res_progress = client.get(f"/api/runs/{benchmark_id}/progress")
    assert res_progress.status_code == 200
    prog_data = res_progress.json()
    assert prog_data["status"] == "queued"
    assert prog_data["total_jobs"] == 0
    assert prog_data["completed_jobs"] == 0
    assert prog_data["vms"] == {}
