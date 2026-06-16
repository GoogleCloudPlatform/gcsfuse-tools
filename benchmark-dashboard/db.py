import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ui_runs (
        benchmark_id VARCHAR(255) PRIMARY KEY,
        description TEXT,
        username VARCHAR(100),
        status VARCHAR(50),                 -- queued, running, completed, failed, cancelled
        suite VARCHAR(50),                  -- kokoro, published, custom
        io_type VARCHAR(50),                -- read, write, zonal
        
        -- Target Machine
        executor_vm VARCHAR(255),
        zone VARCHAR(100),
        project VARCHAR(255),
        single_thread_vm_type VARCHAR(255),
        multi_thread_vm_type VARCHAR(255),
        
        -- Execution Parameters
        commit_hash VARCHAR(100),
        test_csv_name VARCHAR(255),
        configs_csv_name VARCHAR(255),      -- NULL if single-config run
        fio_job_name VARCHAR(255),
        mount_args TEXT,                    -- single mount config args
        test_data_bucket VARCHAR(255),
        artifacts_bucket VARCHAR(255),
        iterations INTEGER,
        
        -- Timestamps
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        started_at TIMESTAMP,
        completed_at TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

def insert_run(run_data):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO ui_runs (
        benchmark_id, description, username, status,
        suite, io_type, executor_vm, zone, project,
        single_thread_vm_type, multi_thread_vm_type, commit_hash,
        test_csv_name, configs_csv_name, fio_job_name,
        mount_args, test_data_bucket, artifacts_bucket, iterations
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        run_data["benchmark_id"], run_data["description"], run_data["username"], "queued",
        run_data["suite"], run_data["io_type"], run_data["executor_vm"], run_data["zone"],
        run_data["project"], run_data.get("single_thread_vm_type"), run_data.get("multi_thread_vm_type"),
        run_data["commit_hash"], run_data["test_csv_name"], run_data.get("configs_csv_name"),
        run_data["fio_job_name"], run_data.get("mount_args"), run_data["test_data_bucket"],
        run_data["artifacts_bucket"], run_data["iterations"]
    ))
    conn.commit()
    conn.close()

def get_runs_by_status(status):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ui_runs WHERE status = ? ORDER BY created_at ASC", (status,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_active_runs():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ui_runs WHERE status IN ('queued', 'running') ORDER BY created_at ASC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_history_runs():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ui_runs WHERE status NOT IN ('queued', 'running') ORDER BY created_at DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_run(benchmark_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ui_runs WHERE benchmark_id = ?", (benchmark_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_run_status(benchmark_id, status, started_at=None, completed_at=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if started_at:
        cursor.execute("UPDATE ui_runs SET status = ?, started_at = ? WHERE benchmark_id = ?", (status, started_at, benchmark_id))
    elif completed_at:
        cursor.execute("UPDATE ui_runs SET status = ?, completed_at = ? WHERE benchmark_id = ?", (status, completed_at, benchmark_id))
    else:
        cursor.execute("UPDATE ui_runs SET status = ? WHERE benchmark_id = ?", (status, benchmark_id))
    conn.commit()
    conn.close()
