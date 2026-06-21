import os
import sys
import json
import asyncio
import logging
import subprocess
from collections import defaultdict
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Depends
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path
from google.cloud import bigquery, storage

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import db

# Setup logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("dashboard")

# Shared Password from Environment, default to a secure-looking team password
SHARED_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "gcsfuse-team")

def generate_user_token(username: str) -> str:
    """Generates a secure, signed token for a user using their username and the team password."""
    signature = db.hash_password(username, SHARED_PASSWORD)
    return f"utoken-{username}-{signature}"

def verify_user_token(token: str) -> bool:
    """Verifies a signature-based user token."""
    if not token or not token.startswith("utoken-"):
        return False
    parts = token.split("-")
    if len(parts) < 3:
        return False
    username = parts[1]
    signature = parts[2]
    expected_signature = db.hash_password(username, SHARED_PASSWORD)
    return signature == expected_signature

async def verify_token_selective(request: Request):
    path = request.url.path
    # Allow index, static files, login API, auth status check, and favicon
    if path == "/" or path.startswith("/static/") or path == "/api/login" or path == "/api/auth/me" or path == "/favicon.ico":
        return
    
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning(f"Unauthenticated API access attempt: {path}")
        raise HTTPException(status_code=401, detail="Missing or invalid authentication token")
    
    token = auth_header.split(" ")[1]
    if not verify_user_token(token):
        logger.warning(f"Failed API authentication attempt (invalid token): {path}")
        raise HTTPException(status_code=401, detail="Invalid or expired session token")

# Paths
BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
DMB_DIR = REPO_ROOT / "distributed-micro-benchmark"

app = FastAPI(
    title="GCSFuse Benchmark Dashboard",
    dependencies=[Depends(verify_token_selective)]
)

# Initialize SQLite database
db.init_db()

# Mount static files for UI (created later)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


class LoginRequest(BaseModel):
    username: str
    password: str


class PresetCreateRequest(BaseModel):
    name: str
    owner: str
    category: str
    filename: str
    content: str


class BenchmarkRunRequest(BaseModel):
    username: str
    description: str
    executor_vm: str
    zone: str
    project: str
    test_csv: str
    fio_job: str
    configs_csv: Optional[str] = None
    mount_args: Optional[str] = None
    iterations: int = 2
    poll_interval: int = 30
    timeout: int = 0
    single_thread_vm_type: Optional[str] = None
    multi_thread_vm_type: Optional[str] = None
    artifacts_bucket: str
    test_data_bucket: str
    commit_hash: Optional[str] = None
    test_csv_content: Optional[str] = None
    fio_job_content: Optional[str] = None
    configs_csv_content: Optional[str] = None


class FioJobCreateRequest(BaseModel):
    filename: str
    content: str


class CustomCsvCreateRequest(BaseModel):
    filename: str
    content: str


class StarRequest(BaseModel):
    is_starred: int


# Background Queue Task
active_processes = {}  # benchmark_id -> subprocess instance
queue_task = None

async def run_queue_manager():
    """Background loop that executes queued benchmarks serializing them per target machine."""
    logger.info("Queue manager started.")
    while True:
        try:
            # 1. Fetch active running targets
            active_runs = db.get_active_runs()
            busy_targets = {r["executor_vm"] for r in active_runs if r["status"] == "running"}
            
            # 2. Fetch queued jobs
            queued_runs = [r for r in active_runs if r["status"] == "queued"]
            
            for run in queued_runs:
                target_vm = run["executor_vm"]
                if target_vm not in busy_targets:
                    # Target is free, start the job!
                    logger.info(f"Starting queued job {run['benchmark_id']} on target '{target_vm}'")
                    busy_targets.add(target_vm)
                    
                    # Update status in DB
                    db.update_run_status(run["benchmark_id"], "running", started_at=datetime.utcnow().isoformat())
                    
                    # Trigger orchestrator process in background task
                    task = asyncio.create_task(execute_orchestrator(run))
                    active_processes[run["benchmark_id"]] = task
                    
        except Exception as e:
            logger.error(f"Error in queue manager loop: {e}", exc_info=True)
            
        await asyncio.sleep(10)  # Check every 10 seconds


async def execute_orchestrator(run, resume: bool = False):
    """Launches orchestrator.py in a subprocess, logging output locally."""
    benchmark_id = run["benchmark_id"]
    results_dir = DMB_DIR / "results" / benchmark_id
    results_dir.mkdir(parents=True, exist_ok=True)
    
    log_file_path = results_dir / "orchestrator.log"
    
    # Build arguments list
    args = [
        "orchestrator.py",
        "--benchmark-id", benchmark_id,
        "--executor-vm", run["executor_vm"],
        "--zone", run["zone"],
        "--project", run["project"],
        "--artifacts-bucket", run["artifacts_bucket"],
        "--test-data-bucket", run["test_data_bucket"],
        "--test-csv", str(DMB_DIR / run["test_csv_name"]),
        "--fio-job-file", str(DMB_DIR / run["fio_job_name"]),
        "--iterations", str(run["iterations"]),
        "--poll-interval", "30",
        "--timeout", str(run.get("timeout", 0))
    ]
    
    if run.get("single_thread_vm_type"):
        args.extend(["--single-thread-vm-type", run["single_thread_vm_type"]])
    if run.get("multi_thread_vm_type"):
        args.extend(["--multi-thread-vm-type", run["multi_thread_vm_type"]])
    
    if run.get("configs_csv_name"):
        args.extend(["--configs-csv", str(DMB_DIR / run["configs_csv_name"])])
    else:
        args.extend([
            "--gcsfuse-commit", run["commit_hash"],
            "--gcsfuse-mount-args", run["mount_args"] or ""
        ])
        
    if resume:
        args.append("--resume")
    
    logger.info(f"Executing: python3 {' '.join(args)} in {DMB_DIR}")
    
    try:
        # Open local log file in append mode so resumed logs append
        with open(log_file_path, "a") as log_f:
            process = await asyncio.create_subprocess_exec(
                "python3", "-u", *args,
                cwd=str(DMB_DIR),
                stdout=log_f,
                stderr=subprocess.STDOUT if hasattr(subprocess, 'STDOUT') else log_f
            )
            
            # Wait for execution to finish
            exit_code = await process.wait()
            
            if exit_code == 0:
                logger.info(f"Subprocess finished successfully for {benchmark_id}")
                
                # Automatically upload ad-hoc results to BigQuery
                try:
                    bq_script = DMB_DIR / "helpers" / "upload_to_bq.py"
                    results_dir = DMB_DIR / "results" / benchmark_id
                    
                    if bq_script.exists() and results_dir.exists():
                        logger.info(f"Triggering BigQuery metrics upload for {benchmark_id}...")
                        project_id = run.get("project", "gcs-fuse-test-ml")
                        
                        bq_proc = await asyncio.create_subprocess_exec(
                            sys.executable, str(bq_script),
                            "--results-dir", str(results_dir),
                            "--project-id", project_id,
                            "--report-name", "combined_report.csv"
                        )
                        await bq_proc.wait()
                        logger.info(f"BigQuery metrics upload finished for {benchmark_id}.")
                    else:
                        logger.warning(f"BigQuery upload script or results dir missing for {benchmark_id}")
                except Exception as bq_err:
                    logger.error(f"BigQuery upload failed for {benchmark_id}: {bq_err}", exc_info=True)
                
                db.update_run_status(benchmark_id, "completed", completed_at=datetime.utcnow().isoformat())
            else:
                logger.error(f"Subprocess failed with exit code {exit_code} for {benchmark_id}")
                db.update_run_status(benchmark_id, "failed", completed_at=datetime.utcnow().isoformat())
                
    except Exception as e:
        logger.error(f"Failed to execute orchestrator process for {benchmark_id}: {e}", exc_info=True)
        db.update_run_status(benchmark_id, "failed", completed_at=datetime.utcnow().isoformat())
    finally:
        active_processes.pop(benchmark_id, None)


@app.on_event("startup")
async def startup_event():
    global queue_task
    # Re-attach to any orphaned active running benchmarks on boot
    try:
        db.init_db()
        running_runs = db.get_active_runs() # Fetching runs in active queue state
        for run in running_runs:
            if run["status"] == "running":
                run_dict = dict(run)
                logger.info(f"Re-attaching to active running benchmark {run_dict['benchmark_id']} on server startup.")
                task = asyncio.create_task(execute_orchestrator(run_dict, resume=True))
                active_processes[run_dict["benchmark_id"]] = task
    except Exception as e:
        logger.error(f"Failed to restore active runs on startup: {e}", exc_info=True)
        
    queue_task = asyncio.create_task(run_queue_manager())


@app.on_event("shutdown")
async def shutdown_event():
    if queue_task:
        queue_task.cancel()
    # Terminate any running benchmark tasks
    for run_id, task in active_processes.items():
        task.cancel()


# --- API ENDPOINTS ---

@app.get("/api/auth/me")
async def get_current_user(request: Request):
    """Checks if Google IAP has authenticated the user and returns their LDAP."""
    # This endpoint is exempt from verify_token_selective in verify_token_selective()!
    # Let's verify we added it to the exempt list.
    email_header = request.headers.get("X-Goog-Authenticated-User-Email")
    if email_header:
        email = email_header.split(":")[-1]
        username = email.split("@")[0]
        return {"authenticated": True, "username": username, "source": "google"}
    return {"authenticated": False, "username": None, "source": "none"}

@app.post("/api/login")
async def login_api(req: LoginRequest):
    """Verifies the shared team password and returns a secure signed session token."""
    username = req.username.strip().lower()
    if req.password == SHARED_PASSWORD:
        token = generate_user_token(username)
        logger.info(f"User '{username}' successfully signed in using the team password.")
        return {"status": "success", "token": token, "username": username}
    else:
        logger.warning(f"Failed sign-in attempt for user '{username}' (incorrect team password).")
        raise HTTPException(status_code=401, detail="Invalid team password")

@app.get("/", response_class=HTMLResponse)
async def get_index():
    """Serves the single-page application UI."""
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


def get_gce_project_id():
    """Queries GCE metadata server to resolve the local project ID."""
    try:
        import urllib.request
        req = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/project/project-id")
        req.add_header("Metadata-Flavor", "Google")
        with urllib.request.urlopen(req, timeout=1.0) as response:
            return response.read().decode("utf-8").strip()
    except Exception:
        # Fallback to local environment variable or default project
        return os.getenv("GCP_PROJECT", "gcs-fuse-test")


def GCE_lookup_project_and_zone(resource_name: str):
    """Checks GCE for a VM or MIG matching resource_name in both projects."""
    projects = ["gcs-fuse-test", "gcs-fuse-test-ml"]
    
    # Try looking for an instance first
    for proj in projects:
        try:
            cmd = [
                "gcloud", "compute", "instances", "list",
                "--project", proj,
                "--filter", f"name={resource_name}",
                "--format", "value(zone.basename())"
            ]
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8").strip()
            if output:
                return proj, output
        except Exception:
            pass
            
    # Try looking for an instance group (MIG)
    for proj in projects:
        try:
            cmd = [
                "gcloud", "compute", "instance-groups", "list",
                "--project", proj,
                "--filter", f"name={resource_name}",
                "--format", "value(zone.basename())"
            ]
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8").strip()
            if output:
                return proj, output
        except Exception:
            pass
            
    return None, None


@app.get("/api/configs/project")
def get_local_project():
    """Returns the detected project ID of the hosting VM."""
    return {"project": get_gce_project_id()}


@app.get("/api/configs/detect-project")
def detect_project(name: str):
    """Scans GCE namespaces in both projects to resolve target project & zone."""
    proj, zone = GCE_lookup_project_and_zone(name)
    if proj:
        return {"project": proj, "zone": zone}
    else:
        return {"project": get_gce_project_id(), "zone": None}


@app.get("/api/configs/files")
def get_config_files():
    """Lists files inside the distributed-micro-benchmark/test_suites/ directory."""
    test_suites_dir = DMB_DIR / "test_suites"
    
    def scan_dir(subdir, ext):
        path = test_suites_dir / subdir
        if not path.exists():
            return []
        return [str(p.relative_to(DMB_DIR)) for p in path.glob(f"**/*.{ext}")]

    return {
        "test_cases": scan_dir("", "csv"),
        "fio_jobs": scan_dir("", "fio"),
    }


@app.post("/api/configs/fio-jobs")
def create_fio_job(fio: FioJobCreateRequest):
    """Saves a custom FIO job configuration in the test_suites/custom_fio_configs/ directory."""
    # Sanitize filename (remove path traversals)
    filename = os.path.basename(fio.filename)
    if not filename.endswith(".fio"):
        filename += ".fio"
        
    custom_dir = DMB_DIR / "test_suites" / "custom_fio_configs"
    custom_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = custom_dir / filename
    try:
        with open(file_path, "w") as f:
            f.write(fio.content)
        logger.info(f"Custom FIO job config saved: {file_path}")
        return {"status": "success", "path": str(file_path.relative_to(DMB_DIR))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write FIO config: {e}")


@app.post("/api/configs/test-cases")
def create_test_cases(csv: CustomCsvCreateRequest):
    """Saves a custom test cases CSV file in the test_suites/custom_test_cases/ directory."""
    filename = os.path.basename(csv.filename)
    if not filename.endswith(".csv"):
        filename += ".csv"
        
    custom_dir = DMB_DIR / "test_suites" / "custom_test_cases"
    custom_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = custom_dir / filename
    try:
        with open(file_path, "w") as f:
            f.write(csv.content)
        logger.info(f"Custom test cases CSV saved: {file_path}")
        return {"status": "success", "path": str(file_path.relative_to(DMB_DIR))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write test cases: {e}")


@app.post("/api/configs/mount-configs")
def create_mount_configs(csv: CustomCsvCreateRequest):
    """Saves a custom GCSFuse mount configs CSV in the test_suites/custom_mount_configs/ directory."""
    filename = os.path.basename(csv.filename)
    if not filename.endswith(".csv"):
        filename += ".csv"
        
    custom_dir = DMB_DIR / "test_suites" / "custom_mount_configs"
    custom_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = custom_dir / filename
    try:
        with open(file_path, "w") as f:
            f.write(csv.content)
        logger.info(f"Custom mount configs CSV saved: {file_path}")
        return {"status": "success", "path": str(file_path.relative_to(DMB_DIR))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write mount configs: {e}")


@app.get("/api/configs/preview")
def get_file_preview(path: str):
    """Reads and returns the contents of a config file relative to the benchmark directory."""
    # Sanitize path to prevent traversal
    safe_path = (DMB_DIR / path).resolve()
    if not str(safe_path).startswith(str(DMB_DIR)):
        raise HTTPException(status_code=403, detail="Access denied: Path lies outside benchmark directory")
        
    if not safe_path.exists() or not safe_path.is_file():
        raise HTTPException(status_code=404, detail="Config file not found")
        
    try:
        with open(safe_path, "r") as f:
            lines = f.readlines()
            return {"content": "".join(lines[:1000])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")


# --- CONFIG PRESETS MANAGER ENDPOINTS ---

@app.post("/api/presets")
def create_preset(preset: PresetCreateRequest):
    """Saves a new config preset in the cloud-synced database."""
    try:
        db.insert_preset(
            name=preset.name,
            owner=preset.owner,
            category=preset.category,
            filename=preset.filename,
            content=preset.content
        )
        logger.info(f"Preset '{preset.name}' ({preset.category}) saved successfully by {preset.owner}")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to create preset: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to save preset: {e}")

@app.get("/api/presets")
def list_presets():
    """Lists all saved presets from the database."""
    try:
        return db.get_presets()
    except Exception as e:
        logger.error(f"Failed to list presets: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve presets: {e}")

@app.get("/api/presets/{preset_id}")
def get_preset_detail(preset_id: int):
    """Retrieves the details/content of a specific preset."""
    preset = db.get_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    return preset

@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: int, username: str):
    """Deletes a preset from the database. Only owner can delete."""
    preset = db.get_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    
    if preset["owner"] != username and preset["owner"] != "system":
        raise HTTPException(status_code=403, detail="You are not authorized to delete other users' presets!")
         
    try:
        db.delete_preset(preset_id)
        logger.info(f"Preset '{preset['name']}' deleted by {username}")
        return {"status": "deleted"}
    except Exception as e:
        logger.error(f"Failed to delete preset: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete preset: {e}")


@app.post("/api/runs")
def create_run(run: BenchmarkRunRequest):
    """Creates and enqueues a new benchmark run."""
    # Resolve project context from GCE Metadata
    local_project = get_gce_project_id()

    # Generate benchmark ID
    import random
    timestamp = int(datetime.utcnow().timestamp())
    benchmark_id = f"web-run-{timestamp}-{random.randint(10, 99)}"

    # Setup directories
    results_dir = DMB_DIR / "results" / benchmark_id
    results_dir.mkdir(parents=True, exist_ok=True)

    # Ensure custom suites folders exist
    (DMB_DIR / "test_suites" / "custom_test_cases").mkdir(parents=True, exist_ok=True)
    (DMB_DIR / "test_suites" / "custom_fio_jobs").mkdir(parents=True, exist_ok=True)
    (DMB_DIR / "test_suites" / "custom_mount_configs").mkdir(parents=True, exist_ok=True)

    # 1. Resolve & Write Test CSV
    if run.test_csv_content and run.test_csv_content.strip():
        test_csv_path = f"test_suites/custom_test_cases/ad_hoc_test_cases_{benchmark_id}.csv"
        with open(DMB_DIR / test_csv_path, "w") as f:
            f.write(run.test_csv_content)
    else:
        if not (DMB_DIR / run.test_csv).exists():
            raise HTTPException(status_code=400, detail=f"Test CSV not found: {run.test_csv}")
        test_csv_path = run.test_csv

    # 2. Resolve & Write FIO Job File
    if run.fio_job_content and run.fio_job_content.strip():
        fio_job_path = f"test_suites/custom_fio_jobs/ad_hoc_fio_job_{benchmark_id}.fio"
        with open(DMB_DIR / fio_job_path, "w") as f:
            f.write(run.fio_job_content)
    else:
        if not (DMB_DIR / run.fio_job).exists():
            raise HTTPException(status_code=400, detail=f"FIO Job not found: {run.fio_job}")
        fio_job_path = run.fio_job

    # 3. Resolve & Write Configs CSV
    configs_csv_path = None
    if run.configs_csv_content and run.configs_csv_content.strip():
        configs_csv_path = f"test_suites/custom_mount_configs/ad_hoc_mount_configs_{benchmark_id}.csv"
        with open(DMB_DIR / configs_csv_path, "w") as f:
            f.write(run.configs_csv_content)
    elif run.configs_csv:
        if not (DMB_DIR / run.configs_csv).exists():
            raise HTTPException(status_code=400, detail=f"Configs CSV not found: {run.configs_csv}")
        configs_csv_path = run.configs_csv

    # Bind custom user bucket parameters
    artifacts_bucket = run.artifacts_bucket.strip() or "pranjal-bucket-1"
    test_data_bucket = run.test_data_bucket.strip() or "grpc-metric-dmb-regional"

    # Deduce suite type
    if "kokoro" in test_csv_path.lower():
        suite = "kokoro"
    elif "published" in test_csv_path.lower():
        suite = "published"
    else:
        suite = "custom"

    # Deduce io_type
    if "read" in fio_job_path.lower():
        io_type = "read"
    elif "write" in fio_job_path.lower():
        io_type = "write"
    else:
        io_type = "other"

    run_record = {
        "benchmark_id": benchmark_id,
        "description": run.description,
        "username": run.username,
        "suite": suite,
        "io_type": io_type,
        "executor_vm": run.executor_vm,
        "zone": run.zone,
        "project": run.project,
        "single_thread_vm_type": run.single_thread_vm_type,
        "multi_thread_vm_type": run.multi_thread_vm_type,
        "commit_hash": run.commit_hash or "master",
        "test_csv_name": test_csv_path,
        "configs_csv_name": configs_csv_path,
        "fio_job_name": fio_job_path,
        "mount_args": run.mount_args,
        "test_data_bucket": test_data_bucket,
        "artifacts_bucket": artifacts_bucket,
        "iterations": run.iterations
    }

    db.insert_run(run_record)
    logger.info(f"Enqueued run {benchmark_id} submitted by {run.username}")
    
    return {"benchmark_id": benchmark_id, "status": "queued"}


@app.delete("/api/runs/{run_id}")
def delete_benchmark_run(run_id: str, username: str):
    """Deletes a benchmark run from SQLite. Only creator is permitted to do this."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["username"] != username:
        raise HTTPException(status_code=403, detail="You are not authorized to delete other users' runs!")
    db.delete_run(run_id)
    return {"status": "deleted"}


@app.post("/api/runs/{run_id}/resume")
async def resume_benchmark_run(run_id: str, username: str):
    """Resumes/re-attaches to a cancelled or failed benchmark run that is still executing on CE."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    
    # Verify it is not already running
    if run_id in active_processes:
        raise HTTPException(status_code=400, detail="Run is already actively monitored")
        
    # Update status back to running
    db.update_run_status(run_id, "running")
    
    run_dict = dict(run)
    task = asyncio.create_task(execute_orchestrator(run_dict, resume=True))
    active_processes[run_id] = task
    logger.info(f"Manually resumed/re-attached benchmark {run_id} by request from {username}")
    return {"status": "resumed", "benchmark_id": run_id}


@app.post("/api/runs/{run_id}/star")
def toggle_star_run(run_id: str, req: StarRequest):
    """Stars or unstars a benchmark run."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    db.update_run_starred(run_id, req.is_starred)
    return {"status": "success", "is_starred": req.is_starred}


@app.get("/api/runs/active")
def get_active():
    """Returns active (queued and running) jobs."""
    return db.get_active_runs()


@app.get("/api/runs/history")
def get_history():
    """Returns historical runs started from the UI."""
    return db.get_history_runs()


@app.get("/api/runs/history-bq")
def get_bq_history(project_id: str = "gcs-fuse-test-ml"):
    """Fetches combined run history from BigQuery (includes Kokoro runs)."""
    try:
        client = bigquery.Client(project=project_id)
        
        # Query unique metadata across local and kokoro datasets
        query = """
        SELECT DISTINCT benchmark_id, run_timestamp, commit, mount_args, io_type
        FROM `gcs-fuse-test-ml.periodic_benchmarks.kokoro_run_*`
        UNION DISTINCT
        SELECT DISTINCT benchmark_id, run_timestamp, commit, mount_args, io_type
        FROM `gcs-fuse-test-ml.adhoc_benchmarks.local_run_*`
        ORDER BY run_timestamp DESC
        LIMIT 100
        """
        query_job = client.query(query)
        results = query_job.result()
        
        history = []
        for row in results:
            history.append({
                "benchmark_id": row.benchmark_id,
                "run_timestamp": row.run_timestamp.isoformat() if row.run_timestamp else None,
                "commit": row.commit,
                "mount_args": row.mount_args,
                "io_type": row.io_type
            })
        return history
    except Exception as e:
        logger.error(f"Failed to query BQ: {e}")
        return []


@app.get("/api/runs/{run_id}/logs")
def get_logs(run_id: str):
    """Retrieves current orchestrator log contents."""
    log_path = DMB_DIR / "results" / run_id / "orchestrator.log"
    if not log_path.exists():
        return {"logs": "Logs not yet available."}
        
    try:
        with open(log_path, "r") as f:
            # Return last 200 lines to avoid blowing context
            lines = f.readlines()
            return {"logs": "".join(lines[-200:])}
    except Exception as e:
        return {"logs": f"Failed to read logs: {e}"}


@app.get("/api/runs/{run_id}/progress")
def get_progress(run_id: str):
    """Calculates active benchmark progress by matching job configurations with durations stored in GCS."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found in local cache")

    # If run is queued, it hasn't created GCS objects yet
    if run["status"] == "queued":
        return {
            "status": "queued",
            "total_jobs": 0, "completed_jobs": 0, "failed_jobs": 0, "pending_jobs": 0,
            "vms": {}
        }

    try:
        # 1. Initialize GCS bucket
        client = storage.Client()
        bucket = client.bucket(run["artifacts_bucket"])
        
        # 2. Find and parse all source-of-truth VM job JSON definitions
        blobs = client.list_blobs(bucket, prefix=f"{run_id}/jobs/")
        
        job_data_by_id = {}
        vms = []
        vm_assignments = defaultdict(int)

        for blob in blobs:
            if not blob.name.endswith(".json"):
                continue
            
            vm_full = blob.name.split("/")[-1].replace(".json", "")
            vms.append(vm_full)
            vm_short = "mig-" + vm_full.split("-")[-1] if "-mig-" in vm_full else vm_full

            try:
                content = blob.download_as_text()
                data = json.loads(content)
                entries = data.get("test_entries", [])
                for entry in entries:
                    matrix_id = int(entry.get("matrix_id", 0))
                    
                    # Compute signature
                    sig_parts = (
                        str(entry.get("io_type", "")).strip().lower(),
                        str(entry.get("num_jobs", "")).strip(),
                        str(entry.get("file_size", "")).strip().lower(),
                        str(entry.get("block_size", "")).strip().lower(),
                        str(entry.get("io_depth", "")).strip(),
                        str(entry.get("nr_files", entry.get("nrfiles", ""))).strip(),
                        str(entry.get("direct", "")).strip()
                    )

                    job_data_by_id[matrix_id] = {
                        "vm": vm_short,
                        "vm_full": vm_full,
                        "id": matrix_id,
                        "signature": sig_parts,
                        "status": "PENDING"
                    }
                    vm_assignments[vm_full] += 1
            except Exception as e:
                logger.warning(f"Failed to read/parse GCS job definition {blob.name}: {e}")

        if not job_data_by_id:
            # Jobs have not been uploaded by the orchestrator yet
            return {
                "status": run["status"],
                "total_jobs": 0, "completed_jobs": 0, "failed_jobs": 0, "pending_jobs": 0,
                "vms": {}
            }

        # 3. Fetch status files (manifest.json and fio_durations.csv) from GCS per VM
        vm_progress = {}
        total_completed = 0
        total_failed = 0
        total_pending = 0

        for vm in sorted(vms):
            # Check manifest for finished statuses and completed tests
            manifest_blob = bucket.blob(f"{run_id}/results/{vm}/manifest.json")
            vm_overall_status = "running"
            completed_matrix_ids = set()
            
            if manifest_blob.exists():
                try:
                    manifest_data = json.loads(manifest_blob.download_as_text())
                    vm_overall_status = manifest_data.get("status", "running")
                    
                    # Collect all completed test matrix IDs from manifest
                    for test in manifest_data.get("tests", []):
                        mid = test.get("matrix_id")
                        if mid is not None:
                            completed_matrix_ids.add(int(mid))
                except Exception as e:
                    logger.warning(f"Failed to read/parse manifest.json for {vm}: {e}")

            # Loop over VM assigned jobs and map status
            vm_jobs = [j for j in job_data_by_id.values() if j["vm_full"] == vm]
            vm_completed = 0
            vm_failed = 0
            vm_pending = 0

            for job in vm_jobs:
                if job["id"] in completed_matrix_ids:
                    job["status"] = "SUCCESS"
                    vm_completed += 1
                else:
                    if vm_overall_status in ["completed", "failed", "cancelled"]:
                        job["status"] = "FAILED/TIMEOUT"
                        vm_failed += 1
                    else:
                        job["status"] = "RUNNING/PENDING"
                        vm_pending += 1

            total_completed += vm_completed
            total_failed += vm_failed
            total_pending += vm_pending

            vm_progress[vm] = {
                "total": vm_assignments[vm],
                "completed": vm_completed,
                "failed": vm_failed,
                "pending": vm_pending,
                "status": "completed" if vm_completed == vm_assignments[vm] and vm_assignments[vm] > 0 else vm_overall_status
            }

        return {
            "status": run["status"],
            "total_jobs": len(job_data_by_id),
            "completed_jobs": total_completed,
            "failed_jobs": total_failed,
            "pending_jobs": total_pending,
            "vms": vm_progress
        }

    except Exception as e:
        logger.error(f"Failed to calculate progress for {run_id}: {e}", exc_info=True)
        return {
            "status": run["status"],
            "total_jobs": 0, "completed_jobs": 0, "failed_jobs": 0, "pending_jobs": 0,
            "vms": {}
        }


@app.get("/api/runs/{run_id}/config")
def get_config(run_id: str):
    """Gets the input configuration of a run (for cloning)."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found in SQLite")
    return run


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    """Aborts a run."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
        
    if run["status"] == "queued":
        db.update_run_status(run_id, "cancelled", completed_at=datetime.utcnow().isoformat())
        logger.info(f"Queued job {run_id} cancelled.")
        return {"status": "cancelled"}
        
    if run["status"] == "running":
        # Create cancel flag in GCS for workers to detect
        try:
            import subprocess
            cancel_path = f"gs://{run['artifacts_bucket']}/{run_id}/cancel"
            subprocess.run(['gcloud', 'storage', 'cp', '-', cancel_path], input=b'cancelled', check=True)
            logger.info(f"GCS cancellation flag written for {run_id}")
        except Exception as e:
            logger.error(f"Failed to create cancel flag in GCS: {e}")
            
        db.update_run_status(run_id, "cancelled", completed_at=datetime.utcnow().isoformat())
        return {"status": "cancelling"}
        
    raise HTTPException(status_code=400, detail="Run is not active")


def fetch_metrics_from_gcs(run_id: str, run_config: dict):
    bucket_name = run_config.get("artifacts_bucket")
    if not bucket_name:
        return []
    
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        
        # Find all VM result folders
        blobs = client.list_blobs(bucket, prefix=f"{run_id}/results/")
        vms = set()
        for blob in blobs:
            parts = blob.name.split("/")
            if len(parts) > 2:
                vms.add(parts[2])
                
        rows = []
        for vm in vms:
            manifest_blob = bucket.blob(f"{run_id}/results/{vm}/manifest.json")
            if not manifest_blob.exists():
                continue
            
            try:
                manifest = json.loads(manifest_blob.download_as_text())
                for test in manifest.get("tests", []):
                    test_id = test.get("test_id")
                    matrix_id = test.get("matrix_id", test_id)
                    params = test.get("params", {})
                    
                    # FIO parameters
                    io_type = str(params.get("io_type", "read")).strip().lower()
                    num_jobs = str(params.get("threads", params.get("num_jobs", "1"))).strip()
                    file_size = str(params.get("file_size", "10g")).strip().lower()
                    block_size = str(params.get("bs", params.get("block_size", "1m"))).strip().lower()
                    io_depth = str(params.get("io_depth", "1")).strip()
                    nr_files = str(params.get("nrfiles", params.get("nr_files", "1"))).strip()
                    direct = str(params.get("direct", "1")).strip()
                    
                    param_str = f"{io_type}|{num_jobs}|{file_size}|{block_size}|{io_depth}|{nr_files}|{direct}"
                    config_label = test.get("config_label", params.get("config_label", "default"))
                    
                    # Fetch FIO iteration JSON files to calculate throughput/latency (using matrix_id directory prefix)
                    fio_blobs = client.list_blobs(bucket, prefix=f"{run_id}/results/{vm}/test-{matrix_id}/fio_output_")
                    bw_values = []
                    lat_values = []
                    peak_bw_values = []
                    
                    for f_blob in fio_blobs:
                        if not f_blob.name.endswith(".json"):
                            continue
                        try:
                            fio_data = json.loads(f_blob.download_as_text())
                            jobs = fio_data.get("jobs", [])
                            if not jobs:
                                continue
                            job = jobs[0]
                            
                            is_write = "write" in io_type
                            rw_key = "write" if is_write else "read"
                            
                            stats = job.get(rw_key, {})
                            bw_bytes = stats.get("bw_bytes", 0)
                            bw_mbs = bw_bytes / (1024 * 1024)
                            
                            # FIO reports bw_max in KB/s
                            bw_max_kbs = stats.get("bw_max", 0)
                            bw_max_mbs = bw_max_kbs / 1024.0
                            
                            clat = stats.get("clat_ns", {})
                            mean_ns = clat.get("mean", 0)
                            lat_ms = mean_ns / 1000000.0
                            
                            bw_values.append(bw_mbs)
                            lat_values.append(lat_ms)
                            peak_bw_values.append(bw_max_mbs)
                        except Exception as e:
                            logger.warning(f"Error parsing GCS FIO file {f_blob.name}: {e}")
                    
                    avg_bw = sum(bw_values) / len(bw_values) if bw_values else 0.0
                    avg_lat = sum(lat_values) / len(lat_values) if lat_values else 0.0
                    max_bw = max(peak_bw_values) if peak_bw_values else 0.0
                    
                    rows.append({
                        "param_str": param_str,
                        "config": config_label,
                        "read_bw": avg_bw if "write" not in io_type else 0.0,
                        "write_bw": avg_bw if "write" in io_type else 0.0,
                        "read_lat": avg_lat if "write" not in io_type else 0.0,
                        "write_lat": avg_lat if "write" in io_type else 0.0,
                        "cpu": float(params.get("avg_cpu", 0)),
                        "sys_cpu": float(params.get("avg_sys_cpu", 0)),
                        "pgcache": float(params.get("avg_page_cache_gb", 0)),
                        "peak_pgcache": float(params.get("peak_page_cache_gb", 0)),
                        "mem": float(params.get("avg_mem_mb", 0)),
                        "net_rx": float(params.get("avg_net_rx_mbps", 0)),
                        "peak_net_rx": float(params.get("peak_net_rx_mbps", 0)),
                        "net_tx": float(params.get("avg_net_tx_mbps", 0)),
                        "peak_net_tx": float(params.get("peak_net_tx_mbps", 0)),
                        "peak_bw": max_bw
                    })
            except Exception as e:
                logger.warning(f"Error parsing manifest.json for VM {vm} in GCS metrics retrieval: {e}")
                
        return rows
    except Exception as e:
        logger.error(f"Failed to fetch metrics from GCS for {run_id}: {e}", exc_info=True)
        return []


@app.get("/api/runs/compare")
def compare_runs(ids: str, project_id: str = "gcs-fuse-test-ml"):
    """Fetches and merges metrics for specified benchmark IDs from BigQuery or GCS for plotting."""
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if not id_list:
        raise HTTPException(status_code=400, detail="No run IDs specified")

    try:
        data = {}
        for rid in id_list:
            run_config = db.get_run(rid)
            proj = run_config.get("project", project_id) if run_config else project_id
            
            # 1. Try resolving table in BigQuery first
            client = bigquery.Client(project=proj)
            dataset = "periodic_benchmarks" if "kokoro" in rid else "adhoc_benchmarks"
            
            try:
                tables = client.list_tables(dataset)
                matching_table = next((t.table_id for t in tables if rid in t.table_id), None)
            except Exception as bq_e:
                logger.info(f"BigQuery access failed or table list failed for {rid}: {bq_e}. Falling back to GCS results.")
                matching_table = None
            
            if not matching_table:
                # 2. Fallback: Parse GCS results directly if BigQuery table not found (e.g. adhoc run)
                logger.info(f"BigQuery table not found for {rid}. Parsing GCS folders directly.")
                if run_config:
                    data[rid] = fetch_metrics_from_gcs(rid, run_config)
                else:
                    data[rid] = []
                continue
                
            query = f"""
            SELECT 
                CONCAT(io_type, '|', num_jobs, '|', file_size, '|', block_size, '|', io_depth, '|', num_files, '|', direct) as param_str,
                config,
                read_bw_mbs, write_bw_mbs, read_avg_ms, write_avg_ms, avg_cpu_percent, avg_sys_cpu_percent, avg_pgcache_gb, peak_pgcache_gb, avg_mem_mb,
                avg_net_rx_mbs, peak_net_rx_mbs, avg_net_tx_mbs, peak_net_tx_mbs
            FROM `{proj}.{dataset}.{matching_table}`
            """
            
            results = client.query(query).result()
            
            rows = []
            for row in results:
                rows.append({
                    "param_str": row.param_str,
                    "config": row.config,
                    "read_bw": row.read_bw_mbs,
                    "write_bw": row.write_bw_mbs,
                    "read_lat": row.read_avg_ms,
                    "write_lat": row.write_avg_ms,
                    "cpu": row.avg_cpu_percent,
                    "sys_cpu": row.avg_sys_cpu_percent,
                    "pgcache": row.avg_pgcache_gb,
                    "peak_pgcache": row.peak_pgcache_gb,
                    "mem": row.avg_mem_mb,
                    "net_rx": row.avg_net_rx_mbs,
                    "peak_net_rx": row.peak_net_rx_mbs,
                    "net_tx": row.avg_net_tx_mbs,
                    "peak_net_tx": row.peak_net_tx_mbs,
                    "peak_bw": max(row.read_bw_mbs or 0.0, row.write_bw_mbs or 0.0)
                })
            data[rid] = rows
            
        return data
        
    except Exception as e:
        logger.error(f"Failed to fetch comparison metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


        return {"content": content}
    except Exception as e:
        logger.error(f"Failed to fetch file from GCS: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


from fastapi.responses import HTMLResponse

@app.get("/api/runs/{run_id}/report-view", response_class=HTMLResponse)
def get_report_view(run_id: str):
    """Serves a print-ready HTML report page for the specified benchmark run containing all 9 metrics."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Benchmark Performance Report - {run_id}</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            @media print {{
                body {{ background: white; color: black; }}
                .no-print {{ display: none !important; }}
                .page-break {{ page-break-before: always; }}
            }}
        </style>
    </head>
    <body class="bg-slate-50 text-slate-800 p-8 min-h-screen">
        <div class="max-w-4xl mx-auto bg-white p-8 rounded-xl shadow border border-slate-200">
            <!-- Header -->
            <div class="flex items-center justify-between border-b-2 border-slate-800 pb-6 mb-6">
                <div>
                    <h1 class="text-2xl font-bold text-slate-900">Benchmark Performance Report</h1>
                    <p class="text-xs text-slate-500 font-mono mt-1">ID: {run_id}</p>
                </div>
                <button onclick="window.print()" class="no-print px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-lg text-xs shadow flex items-center transition-colors">
                    <i class="fa-solid fa-print mr-2"></i> Print / Save as PDF
                </button>
            </div>
            
            <!-- Metadata Grid -->
            <div class="grid grid-cols-2 gap-6 text-xs text-slate-600 mb-8 bg-slate-50 p-5 rounded-lg border border-slate-200">
                <div>
                    <p class="mb-1.5"><strong class="text-slate-800 uppercase tracking-wider text-[10px]">Description:</strong> {run.get("description", "N/A")}</p>
                    <p class="mb-1.5"><strong class="text-slate-800 uppercase tracking-wider text-[10px]">Target VM:</strong> {run.get("executor_vm", "N/A")}</p>
                    <p class="mb-1.5"><strong class="text-slate-800 uppercase tracking-wider text-[10px]">Zone / Project:</strong> {run.get("zone", "N/A")} / {run.get("project", "N/A")}</p>
                </div>
                <div>
                    <p class="mb-1.5"><strong class="text-slate-800 uppercase tracking-wider text-[10px]">Created:</strong> {run.get("created_at", "N/A")}</p>
                    <p class="mb-1.5"><strong class="text-slate-800 uppercase tracking-wider text-[10px]">Started:</strong> {run.get("started_at", "N/A")}</p>
                    <p class="mb-1.5"><strong class="text-slate-800 uppercase tracking-wider text-[10px]">Finished:</strong> {run.get("completed_at", "N/A")}</p>
                    <p class="mb-1.5" id="duration-container"><strong class="text-slate-800 uppercase tracking-wider text-[10px]">Total Duration:</strong> Calculating...</p>
                </div>
            </div>
 
            <!-- Mount Options -->
            <div class="mb-8">
                <h3 class="text-sm font-bold text-slate-900 border-b border-slate-200 pb-2 mb-3 uppercase tracking-wide">Mount Options</h3>
                <pre class="bg-slate-50 p-4 rounded border border-slate-200 text-xs font-mono whitespace-pre-wrap leading-relaxed text-slate-700">{run.get("mount_args", "Used mount configs CSV")}</pre>
            </div>
 
            <!-- Performance Table -->
            <div class="mb-8">
                <h3 class="text-sm font-bold text-slate-900 border-b border-slate-200 pb-2 mb-3 uppercase tracking-wide">Test Cases & Performance Outputs</h3>
                <div class="overflow-x-auto border border-slate-200 rounded-lg shadow-sm">
                    <table class="w-full text-left border-collapse text-xs">
                        <thead>
                            <tr class="bg-slate-800 text-white font-bold">
                                <th class="py-2.5 px-3 border border-slate-200">ID</th>
                                <th class="py-2.5 px-3 border border-slate-200">FIO Parameters</th>
                                <th class="py-2.5 px-3 border border-slate-200">Config Label</th>
                                <th class="py-2.5 px-3 border border-slate-200 text-right">Avg Throughput</th>
                                <th class="py-2.5 px-3 border border-slate-200 text-right">Peak Throughput</th>
                                <th class="py-2.5 px-3 border border-slate-200 text-right">Latency</th>
                                <th class="py-2.5 px-3 border border-slate-200 text-right">Avg CPU %</th>
                                <th class="py-2.5 px-3 border border-slate-200 text-right">Memory RSS</th>
                            </tr>
                        </thead>
                        <tbody id="report-table-body">
                            <tr>
                                <td colspan="8" class="py-4 text-center text-slate-400 italic">Loading performance metrics...</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
 
            <!-- Page Break for Charts -->
            <div class="page-break pt-8">
                <h3 class="text-sm font-bold text-slate-900 border-b border-slate-200 pb-2 mb-6 uppercase tracking-wide">Performance Comparison Graphs</h3>
                
                <div id="unified-report-legend" class="flex flex-wrap gap-4 mb-6 bg-slate-50 p-4 rounded-lg border border-slate-200 text-xs font-semibold text-slate-700 justify-center"></div>

                <div class="space-y-8">
                    <div>
                        <h4 id="label-throughput-chart" class="text-xs font-bold text-slate-700 mb-2 uppercase tracking-wide text-center">Average Throughput Comparison (MB/s)</h4>
                        <div class="h-64 w-full relative border border-slate-200 p-4 rounded-lg bg-white">
                            <canvas id="throughput-chart"></canvas>
                        </div>
                    </div>
                    <div>
                        <h4 id="label-peak-bw-chart" class="text-xs font-bold text-slate-700 mb-2 uppercase tracking-wide text-center">Peak Throughput Comparison (MB/s)</h4>
                        <div class="h-64 w-full relative border border-slate-200 p-4 rounded-lg bg-white">
                            <canvas id="peak-bw-chart"></canvas>
                        </div>
                    </div>
                    <div class="page-break pt-8">
                        <h4 id="label-latency-chart" class="text-xs font-bold text-slate-700 mb-2 uppercase tracking-wide text-center">Average Completion Latency Comparison (ms)</h4>
                        <div class="h-64 w-full relative border border-slate-200 p-4 rounded-lg bg-white">
                            <canvas id="latency-chart"></canvas>
                        </div>
                    </div>
                    <div>
                        <h4 class="text-xs font-bold text-slate-700 mb-2 uppercase tracking-wide text-center">GCSFuse CPU Usage Comparison (%)</h4>
                        <div class="h-64 w-full relative border border-slate-200 p-4 rounded-lg bg-white">
                            <canvas id="cpu-chart"></canvas>
                        </div>
                    </div>
                    <div class="page-break pt-8">
                        <h4 class="text-xs font-bold text-slate-700 mb-2 uppercase tracking-wide text-center">GCSFuse Memory RSS Comparison (MB)</h4>
                        <div class="h-64 w-full relative border border-slate-200 p-4 rounded-lg bg-white">
                            <canvas id="mem-chart"></canvas>
                        </div>
                    </div>
                    <div>
                        <h4 class="text-xs font-bold text-slate-700 mb-2 uppercase tracking-wide text-center">OS Page Cache Comparison (GB)</h4>
                        <div class="h-64 w-full relative border border-slate-200 p-4 rounded-lg bg-white">
                            <canvas id="pgcache-chart"></canvas>
                        </div>
                    </div>
                    <div class="page-break pt-8">
                        <h4 class="text-xs font-bold text-slate-700 mb-2 uppercase tracking-wide text-center">Avg Network Ingress (RX) Comparison (MB/s)</h4>
                        <div class="h-64 w-full relative border border-slate-200 p-4 rounded-lg bg-white">
                            <canvas id="net-rx-chart"></canvas>
                        </div>
                    </div>
                    <div>
                        <h4 class="text-xs font-bold text-slate-700 mb-2 uppercase tracking-wide text-center">Peak Network Ingress (RX) Comparison (MB/s)</h4>
                        <div class="h-64 w-full relative border border-slate-200 p-4 rounded-lg bg-white">
                            <canvas id="peak-net-rx-chart"></canvas>
                        </div>
                    </div>
                    <div class="page-break pt-8">
                        <h4 class="text-xs font-bold text-slate-700 mb-2 uppercase tracking-wide text-center">Network Egress (TX) Comparison (MB/s)</h4>
                        <div class="h-64 w-full relative border border-slate-200 p-4 rounded-lg bg-white">
                            <canvas id="net-tx-chart"></canvas>
                        </div>
                    </div>
                </div>
            </div>
        </div>
 
        <script>
            const runId = "{run_id}";
            
            function formatDuration(start, end) {{
                if (!start || !end) return "N/A";
                const sDate = new Date(start);
                const eDate = new Date(end);
                const diffMs = eDate - sDate;
                if (diffMs < 0 || isNaN(diffMs)) return "N/A";
                
                const diffSec = Math.floor(diffMs / 1000);
                const hrs = Math.floor(diffSec / 3600);
                const mins = Math.floor((diffSec % 3600) / 60);
                const secs = diffSec % 60;
                
                let str = "";
                if (hrs > 0) str += hrs + "h ";
                if (mins > 0 || hrs > 0) str += mins + "m ";
                str += secs + "s";
                return str.trim();
            }}
            
            window.onload = async () => {{
                const durationStr = formatDuration("{run.get('started_at', '')}", "{run.get('completed_at', '')}");
                document.getElementById("duration-container").innerHTML = `<strong class="text-slate-800 uppercase tracking-wider text-[10px]">Total Duration:</strong> ` + durationStr;
 
                try {{
                    const res = await fetch(`/api/runs/compare?ids=` + runId);
                    const metrics = await res.json();
                    
                    const runData = metrics[runId] || [];
                    if (runData.length === 0) {{
                        document.getElementById("report-table-body").innerHTML = `
                            <tr>
                                <td colspan="8" class="py-4 text-center text-rose-500 italic font-bold">No performance metrics found. Did the benchmark run fail or cancel early?</td>
                            </tr>
                        `;
                        return;
                    }}
                    
                    let tableHtml = "";
                    runData.forEach((row, index) => {{
                        const paramParts = row.param_str.split('|');
                        const paramFmt = paramParts[0] + " (" + paramParts[3] + ") - depth " + paramParts[4] + " (" + paramParts[1] + " jobs)";
                        const readBw = row.read_bw ? row.read_bw.toFixed(2) + " MB/s" : "-";
                        const writeBw = row.write_bw ? row.write_bw.toFixed(2) + " MB/s" : "-";
                        const avgBw = row.read_bw ? readBw : writeBw;
                        const peakBw = row.peak_bw ? row.peak_bw.toFixed(2) + " MB/s" : "-";
                        const lat = (row.read_lat || row.write_lat) ? (row.read_lat || row.write_lat).toFixed(3) + " ms" : "-";
                        
                        tableHtml += `
                            <tr class="${{index % 2 === 0 ? 'bg-white' : 'bg-slate-50'}} border-b border-slate-200">
                                <td class="py-2 px-3 font-mono font-bold text-slate-800 border-r border-slate-200">${{index + 1}}</td>
                                <td class="py-2 px-3 border-r border-slate-200">${{paramFmt}}</td>
                                <td class="py-2 px-3 font-mono text-slate-700 border-r border-slate-200">${{row.config}}</td>
                                <td class="py-2 px-3 text-right font-mono font-bold text-slate-800 border-r border-slate-200">${{avgBw}}</td>
                                <td class="py-2 px-3 text-right font-mono font-bold text-slate-800 border-r border-slate-200">${{peakBw}}</td>
                                <td class="py-2 px-3 text-right font-mono text-slate-850 border-r border-slate-200">${{lat}}</td>
                                <td class="py-2 px-3 text-right font-mono text-slate-650 border-r border-slate-200">${{row.cpu.toFixed(2)}}%</td>
                                <td class="py-2 px-3 text-right font-mono text-slate-650">${{row.mem.toFixed(2)}} MB</td>
                            </tr>
                        `;
                    }});
                    document.getElementById("report-table-body").innerHTML = tableHtml;
                    
                    renderReportCharts(runData);
                    setTimeout(() => {{ window.print(); }}, 1800);
                    
                }} catch (e) {{
                    document.getElementById("report-table-body").innerHTML = `
                        <tr>
                            <td colspan="8" class="py-4 text-center text-rose-500 font-bold">Failed to load report data: ${{e}}</td>
                        </tr>
                    `;
                }}
            }};
 
            function renderReportCharts(runData) {{
                const allParams = new Set();
                const allConfigs = new Set();
                runData.forEach(row => {{
                    allParams.add(row.param_str);
                    allConfigs.add(row.config);
                }});
                
                const sortedParams = Array.from(allParams).sort();
                const sortedConfigs = Array.from(allConfigs).sort();
                
                const labels = sortedParams.map(p => {{
                    const parts = p.split('|');
                    return parts[0] + " (" + parts[3] + ") - depth " + parts[4] + " (" + parts[1] + " jobs)";
                }});
                
                const datasetsBw = [];
                const datasetsLat = [];
                const datasetsPeakBw = [];
                const datasetsCpu = [];
                const datasetsMem = [];
                const datasetsPgCache = [];
                const datasetsNetRx = [];
                const datasetsPeakNetRx = [];
                const datasetsNetTx = [];
                const colors = ['#1a73e8', '#1e8e3e', '#d93025', '#f97316', '#8b5cf6', '#ec4899', '#f59e0b', '#06b6d4'];
                
                // Detect dynamic read/write labeling
                let hasRead = false;
                let hasWrite = false;
                runData.forEach(row => {{
                    if (row.param_str.toLowerCase().includes('write')) {{
                        hasWrite = true;
                    }} else {{
                        hasRead = true;
                    }}
                }});

                let bwLabel = 'Throughput (MB/s)';
                let latLabel = 'Latency (ms)';
                if (hasRead && !hasWrite) {{
                    bwLabel = 'Read Throughput (MB/s)';
                    latLabel = 'Read Latency (ms)';
                }} else if (hasWrite && !hasRead) {{
                    bwLabel = 'Write Throughput (MB/s)';
                    latLabel = 'Write Latency (ms)';
                }}

                // Update headers in page text
                document.getElementById('label-throughput-chart').innerText = 'Average ' + bwLabel;
                document.getElementById('label-peak-bw-chart').innerText = 'Peak ' + bwLabel;
                document.getElementById('label-latency-chart').innerText = 'Average ' + latLabel;

                let seriesIdx = 0;
                sortedConfigs.forEach(conf => {{
                    const bwData = [];
                    const latData = [];
                    const peakBwData = [];
                    const cpuData = [];
                    const memData = [];
                    const pgCacheData = [];
                    const netRxData = [];
                    const peakNetRxData = [];
                    const netTxData = [];
                    let hasData = false;
                    
                    sortedParams.forEach(param => {{
                        const match = runData.find(r => r.param_str === param && r.config === conf);
                        if (match) {{
                            hasData = true;
                            bwData.push(match.read_bw || match.write_bw || 0);
                            latData.push(match.read_lat || match.write_lat || 0);
                            peakBwData.push(match.peak_bw || match.read_bw || match.write_bw || 0);
                            cpuData.push(match.cpu || 0);
                            memData.push(match.mem || 0);
                            pgCacheData.push(match.pgcache || 0);
                            netRxData.push(match.net_rx || 0);
                            peakNetRxData.push(match.peak_net_rx || 0);
                            netTxData.push(match.net_tx || 0);
                        }} else {{
                            bwData.push(0);
                            latData.push(0);
                            peakBwData.push(0);
                            cpuData.push(0);
                            memData.push(0);
                            pgCacheData.push(0);
                            netRxData.push(0);
                            peakNetRxData.push(0);
                            netTxData.push(0);
                        }}
                    }});
                    
                    if (hasData) {{
                        const color = colors[seriesIdx % colors.length];
                        datasetsBw.push({{
                            label: conf,
                            data: bwData,
                            backgroundColor: color + 'bf',
                            borderColor: color,
                            borderWidth: 1
                        }});
                        
                        datasetsLat.push({{
                            label: conf,
                            data: latData,
                            fill: false,
                            borderColor: color,
                            tension: 0.15,
                            pointRadius: 4,
                            borderWidth: 2
                        }});
 
                        datasetsPeakBw.push({{
                            label: conf,
                            data: peakBwData,
                            backgroundColor: color + 'bf',
                            borderColor: color,
                            borderWidth: 1
                        }});
 
                        datasetsCpu.push({{
                            label: conf,
                            data: cpuData,
                            fill: false,
                            borderColor: color,
                            tension: 0.15,
                            pointRadius: 4,
                            borderWidth: 2
                        }});
 
                        datasetsMem.push({{
                            label: conf,
                            data: memData,
                            backgroundColor: color + 'bf',
                            borderColor: color,
                            borderWidth: 1
                        }});

                        datasetsPgCache.push({{
                            label: conf,
                            data: pgCacheData,
                            backgroundColor: color + 'bf',
                            borderColor: color,
                            borderWidth: 1
                        }});

                        datasetsNetRx.push({{
                            label: conf,
                            data: netRxData,
                            backgroundColor: color + 'bf',
                            borderColor: color,
                            borderWidth: 1
                        }});

                        datasetsPeakNetRx.push({{
                            label: conf,
                            data: peakNetRxData,
                            backgroundColor: color + 'bf',
                            borderColor: color,
                            borderWidth: 1
                        }});

                        datasetsNetTx.push({{
                            label: conf,
                            data: netTxData,
                            backgroundColor: color + 'bf',
                            borderColor: color,
                            borderWidth: 1
                        }});
 
                        seriesIdx++;
                    }}
                }});
                
                // Rebuild report unified legend
                    const legendEl = document.getElementById('unified-report-legend');
                    if (legendEl && datasetsBw.length > 0) {{
                        legendEl.innerHTML = datasetsBw.map(ds => {{
                            return `
                                <div class="flex items-center space-x-2 bg-white px-3 py-1.5 rounded-lg border border-slate-200 shadow-sm">
                                    <span class="w-3.5 h-3.5 rounded-sm" style="background-color: ${{ds.borderColor}}; border: 1px solid ${{ds.borderColor}};"></span>
                                    <span class="text-slate-700 font-mono text-xs font-semibold">${{ds.label}}</span>
                                </div>
                            `;
                        }}).join('');
                    }}

                    drawChart('throughput-chart', 'bar', labels, datasetsBw, bwLabel, false);
                    drawChart('latency-chart', 'line', labels, datasetsLat, latLabel, false);
                    drawChart('peak-bw-chart', 'bar', labels, datasetsPeakBw, 'Peak ' + bwLabel, false);
                    drawChart('cpu-chart', 'line', labels, datasetsCpu, 'CPU Usage (%)', false);
                    drawChart('mem-chart', 'bar', labels, datasetsMem, 'RSS Memory (MB)', false);
                    drawChart('pgcache-chart', 'bar', labels, datasetsPgCache, 'Page Cache (GB)', false);
                    drawChart('net-rx-chart', 'bar', labels, datasetsNetRx, 'Avg Net Ingress (RX) (MB/s)', false);
                    drawChart('peak-net-rx-chart', 'bar', labels, datasetsPeakNetRx, 'Peak Net Ingress (RX) (MB/s)', false);
                    drawChart('net-tx-chart', 'bar', labels, datasetsNetTx, 'Net Egress (TX) (MB/s)', false);
                }}
     
                function drawChart(canvasId, type, labels, datasets, yLabel, showLegend = true) {{
                    const ctx = document.getElementById(canvasId).getContext('2d');
                    new Chart(ctx, {{
                        type: type,
                        data: {{ labels: labels, datasets: datasets }},
                        plugins: [ChartDataLabels],
                        options: {{
                            responsive: true,
                            maintainAspectRatio: false,
                            animation: false,
                            layout: {{
                                padding: {{
                                    top: 15
                                }}
                            }},
                            scales: {{
                                x: {{ grid: {{ color: '#e2e8f0' }}, ticks: {{ font: {{ size: 8 }} }} }},
                                y: {{ grid: {{ color: '#e2e8f0' }}, title: {{ display: true, text: yLabel }} }}
                            }},
                            plugins: {{
                                legend: {{ display: showLegend, position: 'bottom' }},
                                datalabels: {{
                                    display: 'auto',
                                    anchor: 'end',
                                    align: 'top',
                                    offset: 1,
                                    formatter: (value) => {{
                                        if (!value || value === 0) return '';
                                        if (value < 0.001) return value.toFixed(4);
                                        if (value < 0.01) return value.toFixed(3);
                                        if (value < 1.0) return value.toFixed(2);
                                        if (value >= 100) return Math.round(value);
                                        return value.toFixed(1);
                                    }},
                                    font: {{
                                        weight: 'bold',
                                        size: 8
                                    }},
                                    color: '#64748b'
                                }}
                            }}
                        }}
                    }});
                }}
        </script>
    </body>
    </html>
    """
    return html_content
