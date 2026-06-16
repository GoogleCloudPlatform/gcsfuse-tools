import os
import sys
import json
import asyncio
import logging
import subprocess
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path
from google.cloud import bigquery

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import db

# Setup logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("dashboard")

# Paths
BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
DMB_DIR = REPO_ROOT / "distributed-micro-benchmark"

app = FastAPI(title="GCSFuse Benchmark Dashboard")

# Initialize SQLite database
db.init_db()

# Mount static files for UI (created later)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


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
    timeout: int = 7200
    single_thread_vm_type: Optional[str] = None
    multi_thread_vm_type: Optional[str] = None


class FioJobCreateRequest(BaseModel):
    filename: str
    content: str


class CustomCsvCreateRequest(BaseModel):
    filename: str
    content: str


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


async def execute_orchestrator(run):
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
        "--timeout", "7200"
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
    
    logger.info(f"Executing: python3 {' '.join(args)} in {DMB_DIR}")
    
    try:
        # Open local log file to stream subprocess output
        with open(log_file_path, "w") as log_f:
            process = await asyncio.create_subprocess_exec(
                "python3", *args,
                cwd=str(DMB_DIR),
                stdout=log_f,
                stderr=subprocess.STDOUT if hasattr(subprocess, 'STDOUT') else log_f
            )
            
            # Wait for execution to finish
            exit_code = await process.wait()
            
            if exit_code == 0:
                logger.info(f"Subprocess finished successfully for {benchmark_id}")
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
    queue_task = asyncio.create_task(run_queue_manager())


@app.on_event("shutdown")
async def shutdown_event():
    if queue_task:
        queue_task.cancel()
    # Terminate any running benchmark tasks
    for run_id, task in active_processes.items():
        task.cancel()


# --- API ENDPOINTS ---

@app.get("/", response_class=HTMLResponse)
async def get_index():
    """Serves the single-page application UI."""
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


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
    """Saves a custom FIO job configuration in the test_suites/custom/ directory."""
    # Sanitize filename (remove path traversals)
    filename = os.path.basename(fio.filename)
    if not filename.endswith(".fio"):
        filename += ".fio"
        
    custom_dir = DMB_DIR / "test_suites" / "custom"
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


@app.post("/api/runs")
def create_run(run: BenchmarkRunRequest):
    """Creates and enqueues a new benchmark run."""
    # Validate file existences in test_suites
    if not (DMB_DIR / run.test_csv).exists():
        raise HTTPException(status_code=400, detail=f"Test CSV not found: {run.test_csv}")
    if not (DMB_DIR / run.fio_job).exists():
        raise HTTPException(status_code=400, detail=f"FIO Job not found: {run.fio_job}")
    if run.configs_csv and not (DMB_DIR / run.configs_csv).exists():
        raise HTTPException(status_code=400, detail=f"Configs CSV not found: {run.configs_csv}")

    # Generate benchmark ID
    timestamp = int(datetime.utcnow().timestamp())
    benchmark_id = f"web-run-{timestamp}"

    # Setup directories
    results_dir = DMB_DIR / "results" / benchmark_id
    results_dir.mkdir(parents=True, exist_ok=True)

    # Defaults buckets (similar to run.sh)
    artifacts_bucket = "pranjal-bucket-1"
    test_data_bucket = "grpc-metric-dmb-regional"

    # Deduce suite type
    if "kokoro" in run.test_csv.lower():
        suite = "kokoro"
    elif "published" in run.test_csv.lower():
        suite = "published"
    else:
        suite = "custom"

    # Deduce io_type
    if "read" in run.fio_job.lower():
        io_type = "read"
    elif "write" in run.fio_job.lower():
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
        "test_csv_name": run.test_csv,
        "configs_csv_name": run.configs_csv,
        "fio_job_name": run.fio_job,
        "mount_args": run.mount_args,
        "test_data_bucket": test_data_bucket,
        "artifacts_bucket": artifacts_bucket,
        "iterations": run.iterations
    }

    db.insert_run(run_record)
    logger.info(f"Enqueued run {benchmark_id} submitted by {run.username}")
    
    return {"benchmark_id": benchmark_id, "status": "queued"}


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


@app.get("/api/runs/compare")
def compare_runs(ids: str, project_id: str = "gcs-fuse-test-ml"):
    """Fetches and merges metrics for specified benchmark IDs from BigQuery for plotting."""
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if not id_list:
        raise HTTPException(status_code=400, detail="No run IDs specified")

    try:
        client = bigquery.Client(project=project_id)
        
        # Construct dynamic SQL querying the tables
        # Since BQ creates one table per run, we need to query tables matching the IDs
        data = {}
        for rid in id_list:
            # Safely determine dataset (periodic for kokoro, adhoc for local)
            dataset = "periodic_benchmarks" if "kokoro" in rid else "adhoc_benchmarks"
            
            # Find the exact table name by listing tables with filter
            tables = client.list_tables(dataset)
            matching_table = next((t.table_id for t in tables if rid in t.table_id), None)
            
            if not matching_table:
                logger.warning(f"Table not found for benchmark ID: {rid}")
                continue
                
            query = f"""
            SELECT 
                CONCAT(io_type, '|', num_jobs, '|', file_size, '|', block_size, '|', io_depth, '|', num_files, '|', direct) as param_str,
                config,
                read_bw_mbs, write_bw_mbs, read_avg_ms, write_avg_ms, avg_cpu_percent, avg_sys_cpu_percent, avg_pgcache_gb
            FROM `{project_id}.{dataset}.{matching_table}`
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
                    "pgcache": row.avg_pgcache_gb
                })
            data[rid] = rows
            
        return data
        
    except Exception as e:
        logger.error(f"Failed to fetch comparison metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
