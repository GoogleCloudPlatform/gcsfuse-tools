import sqlite3
import os
import hashlib
from datetime import datetime
from google.cloud import storage

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.db")
DASHBOARD_BUCKET = os.environ.get("DASHBOARD_BUCKET", "pranjal-bucket-1")
GCS_DB_BLOB = "dashboard/dashboard.db"

def download_db_from_gcs():
    """Download dashboard.db from GCS to local path on startup."""
    try:
        client = storage.Client()
        bucket = client.bucket(DASHBOARD_BUCKET)
        blob = bucket.blob(GCS_DB_BLOB)
        if blob.exists():
            print(f"Downloading database from GCS: gs://{DASHBOARD_BUCKET}/{GCS_DB_BLOB}")
            blob.download_to_filename(DB_PATH)
            return True
        else:
            print(f"No database found on GCS at gs://{DASHBOARD_BUCKET}/{GCS_DB_BLOB}. Initializing a new database.")
            return False
    except Exception as e:
        print(f"Warning: Failed to download database from GCS: {e}. Using local database.")
        return False

def upload_db_to_gcs():
    """Upload local dashboard.db to GCS after write operations."""
    try:
        client = storage.Client()
        bucket = client.bucket(DASHBOARD_BUCKET)
        blob = bucket.blob(GCS_DB_BLOB)
        print(f"Syncing database to GCS: gs://{DASHBOARD_BUCKET}/{GCS_DB_BLOB}")
        blob.upload_from_filename(DB_PATH)
    except Exception as e:
        print(f"Error: Failed to upload database to GCS: {e}")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    # Always try to restore state from cloud on initialization
    download_db_from_gcs()
    
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
    
    # Auto-migrate: Add is_starred column if it doesn't exist
    try:
        cursor.execute("SELECT is_starred FROM ui_runs LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE ui_runs ADD COLUMN is_starred INTEGER DEFAULT 0")
        
    # Create ui_presets table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ui_presets (
        preset_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR(255) NOT NULL,
        owner VARCHAR(100) NOT NULL,
        category VARCHAR(50) NOT NULL,      -- 'test_cases', 'fio_job', 'mount_configs'
        filename VARCHAR(255) NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Create ui_users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ui_users (
        username VARCHAR(100) PRIMARY KEY,
        password_hash VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Seed default user if empty
    cursor.execute("SELECT COUNT(*) as cnt FROM ui_users")
    if cursor.fetchone()["cnt"] == 0:
        default_password = os.environ.get("DASHBOARD_PASSWORD", "gcsfuse-team")
        # Hash default password with salt
        hashed = hashlib.sha256((default_password + "gcsfuse-dashboard-salt").encode('utf-8')).hexdigest()
        cursor.execute("INSERT INTO ui_users (username, password_hash) VALUES (?, ?)", ("admin", hashed))
        
    conn.commit()
    conn.close()
    upload_db_to_gcs()

def delete_run(benchmark_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ui_runs WHERE benchmark_id = ?", (benchmark_id,))
    conn.commit()
    conn.close()
    upload_db_to_gcs()

def update_run_starred(benchmark_id, is_starred):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE ui_runs SET is_starred = ? WHERE benchmark_id = ?", (is_starred, benchmark_id))
    conn.commit()
    conn.close()
    upload_db_to_gcs()

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
    upload_db_to_gcs()

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
    upload_db_to_gcs()

# --- PRESET HELPER METHODS ---

def insert_preset(name, owner, category, filename, content):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO ui_presets (name, owner, category, filename, content)
    VALUES (?, ?, ?, ?, ?)
    """, (name, owner, category, filename, content))
    conn.commit()
    conn.close()
    upload_db_to_gcs()

def get_presets():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ui_presets ORDER BY created_at DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_preset(preset_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ui_presets WHERE preset_id = ?", (preset_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_preset(preset_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ui_presets WHERE preset_id = ?", (preset_id,))
    conn.commit()
    conn.close()
    upload_db_to_gcs()

# --- USER AUTHENTICATION HELPERS ---

def hash_password(password, salt="gcsfuse-dashboard-salt"):
    return hashlib.sha256((password + salt).encode('utf-8')).hexdigest()

def create_user(username, password):
    hashed = hash_password(password)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT OR REPLACE INTO ui_users (username, password_hash)
    VALUES (?, ?)
    """, (username.strip().toLowerCase() if hasattr(username, 'toLowerCase') else username.strip().lower(), hashed))
    conn.commit()
    conn.close()
    upload_db_to_gcs()

def verify_user(username, password):
    clean_username = username.strip().lower()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM ui_users WHERE username = ?", (clean_username,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return False
    return row["password_hash"] == hash_password(password)

def change_user_password(username, old_password, new_password):
    if not verify_user(username, old_password):
        return False
    create_user(username, new_password)
    return True
