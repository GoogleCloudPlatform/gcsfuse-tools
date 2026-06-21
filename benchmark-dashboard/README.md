# GCSFuse Distributed Benchmark Dashboard: Design & Architecture Document

This document outlines the design, architecture, and features implemented for the **GCSFuse Distributed Benchmark Dashboard**—a robust, secure, and collaborative web platform that enables the GCSFuse and ML performance teams to orchestrate, monitor, and compare distributed FIO micro-benchmarks across Compute Engine VMs.

---

## 1. System Architecture

The dashboard is built on a decoupled, resilient architecture consisting of a **FastAPI Web Server**, a **SQLite Database with GCS Write-Through Sync**, and a **Compute Engine Distributed Worker Pool** coordinated via Google Cloud Storage (GCS).

```
[Teammate Browser] (HTTPS / Port 8080)
       │
       ▼
[FastAPI Web Server (main.py)] ◄──► [Local SQLite DB (dashboard.db)]
       │                                     ▲
       ├──► [Orchestrator (orchestrator.py)]  │ (Write-Through Sync)
       │                                     ▼
       └──► [GCS Run Folder] ◄────────► [GCS Database (dmb-db/dashboard/dashboard.db)]
                 │
                 ▼
     [GCE Worker VMs (runner.sh)]
```

---

## 2. Implemented Features & Capabilities

### 🔒 Resilient Team Security & Authentication
To restrict access to GCSFuse team members without introducing the complexity of full OAuth2 integration, we implemented a custom token-based security layer:
*   **Selective Global Middleware**: Added a custom FastAPI dependency in `main.py` that intercepts all `/api/` requests and verifies a Bearer token. Static assets (`/static/`), index (`/`), and `/api/login` are automatically exempted.
*   **Shared Password Authentication**: Verifies against a `DASHBOARD_PASSWORD` environment variable, returning an expiring, cryptographically signed **JSON Web Token (JWT)** on success via `PyJWT`.
*   **Transparent Client-Side Interceptor**: Overrode the global `window.fetch` function in `app.js`. It automatically injects the session token into the `Authorization` header and handles `401 Unauthorized` responses by immediately logging the user out and showing the Sign In modal.

### ☁️ GCS-Persistent SQLite Database
To protect the dashboard data against GCE VM deletions, force checkouts, or restarts, the database is hosted entirely in the cloud:
*   **GCS Source of Truth**: The SQLite file (`dashboard.db`) is fetched from GCS on server boot by `db.py`.
*   **Write-Through Synchronization**: Every single database write operation (inserting runs, deleting, starring, or updating status) automatically triggers an immediate write-through sync back to `gs://<DASHBOARD_BUCKET>/dashboard/dashboard.db` in `db.py`.

### 📋 Collaborative Config Preset Manager
To prevent teammates from having to manually upload or copy-paste CSV configuration files and FIO templates every time they want to launch a run, we built a shared preset manager:
*   **Unified Grouped Dropdowns**: Integrated repository files and database presets into the same selectors in `app.js`. Options are grouped under:
    1.  `Shared Presets (Common)`: General team baselines.
    2.  `Your Custom Presets`: Presets created by the signed-in user.
    3.  `Teammates' Presets`: Presets created by other team members (offering cross-user access).
    4.  `Repository Files`: Static templates checked into the repo.
*   **Interactive Cloud Saving**: Added **Save as Preset** (`☁ arrow-up`) buttons next to each previewer textarea in `index.html`. Clicking it reads the current textarea editor content, prompts for a name, asks if it should be public (Common) or private, and saves it instantly to the cloud database.

### 📈 Live Execution Monitor & Graphing
The dashboard provides deep, real-time insight into benchmark runs:
*   **Subprocess Orchestration**: Launches the multi-VM Python orchestrator in a background process, streaming its output log in real-time to the browser via the **Active Monitor** tab.
*   **Granular Worker Metrics**: Polls GCS manifests to display exactly how many test matrix cells have completed (e.g. *Progress: 21/54*).
*   **Live Resource Graphing**: Charts CPU utilization, RSS memory usage, Page Cache size, and Network Throughput (RX/TX) dynamically during execution using Chart.js.

### 📊 Comparative Analysis Workspace
Teammates can select multiple historical runs from the **Run History** tab and compare them side-by-side:
*   **User-Filtered History**: Includes toggles for **All Users**, **My Runs Only**, and a **Live LDAP Search Bar** in `index.html` to quickly filter historical runs.
*   **Side-by-Side Comparison Graphs**: Generates comparative bar/line charts charting:
    *   Average & Peak Throughput
    *   Average Latency
    *   Average & Peak CPU Utilization
    *   Resident Set Memory (RSS)

### 🔄 Resilient Resume & Re-attachment
*   **Server Resiliency**: If the web server process (`uvicorn`) is terminated, restarted, or updated mid-run, the server automatically re-attaches to all active runs in `--resume` mode upon boot, preserving run logs.
*   **Manual Re-attach**: Added an emerald **Play/Resume** (`▶`) action button next to failed/cancelled runs in the history table. Clicking it tells the backend to re-attach to the worker VMs and resume monitoring immediately.

---

## 3. Database Schema Design

The synced SQLite database has two primary tables:

### Table 1: `ui_runs` (Benchmark Executions)
Stores the metadata, execution parameters, and status of all launched runs.
```sql
CREATE TABLE IF NOT EXISTS ui_runs (
    benchmark_id VARCHAR(255) PRIMARY KEY,
    description TEXT,
    username VARCHAR(100),
    status VARCHAR(50),                 -- queued, running, completed, failed, cancelled
    suite VARCHAR(50),                  -- kokoro, published, custom
    io_type VARCHAR(50),                -- read, write, zonal
    executor_vm VARCHAR(255),
    zone VARCHAR(100),
    project VARCHAR(255),
    single_thread_vm_type VARCHAR(255),
    multi_thread_vm_type VARCHAR(255),
    commit_hash VARCHAR(100),
    test_csv_name VARCHAR(255),
    configs_csv_name VARCHAR(255),      -- NULL if single-config run
    fio_job_name VARCHAR(255),
    mount_args TEXT,
    test_data_bucket VARCHAR(255),
    artifacts_bucket VARCHAR(255),
    iterations INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    is_starred INTEGER DEFAULT 0
);
```

### Table 2: `ui_presets` (Configuration Presets)
Stores the reusable test configurations, FIO jobs, and mount arguments.
```sql
CREATE TABLE IF NOT EXISTS ui_presets (
    preset_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(255) NOT NULL,
    owner VARCHAR(100) NOT NULL,        -- LDAP username or 'system'
    category VARCHAR(50) NOT NULL,      -- 'test_cases', 'fio_job', 'mount_configs'
    filename VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. Frontend Tech Stack & Plotting Engines

The GCSFuse Benchmark Dashboard uses a high-performance, lightweight frontend stack coupled with powerful charting libraries to enable responsive, in-browser data analysis and static image generation for reports.

### 1. Frontend Technologies & Libraries
To keep the dashboard fast and simple to host on a lightweight VM without the complexity of Node.js build pipelines, we built a modern Single Page Application (SPA) using:
*   **Tailwind CSS**: A utility-first CSS framework used for responsive layout grids, clean cards, modal transitions, and custom scrollbars.
*   **FontAwesome**: Standard premium icons representing tabs, active status, star, delete, and comparison operations.
*   **Chart.js**: A high-performance canvas-based graphing library used to render all comparison graphs in the browser.
*   **DataTables**: A jQuery plugin providing advanced client-side column sorting, pagination, and dynamic child rows for the complex Run History tables.
*   **Toastify JS**: A lightweight, non-blocking notification library for smooth, premium UI alerts and error handling.

### 2. Interactive Charting Engine (Browser-side)
All interactive plotting is handled dynamically in `app.js`:
*   **Dynamic Pivot & X-Axis Alignment**: Allows teammates to toggle the graph's X-axis between a concatenated string of all run parameters (BS, Threads, File Size, direct mode) or individual attributes.
*   **Chart Lifecycle Management**: Before rendering a new graph, the frontend explicitly calls `chart.destroy()` on the existing instance to prevent canvas overlaps and memory leaks.

### 3. Static Report Plotting (Backend-side)
In addition to browser charts, the coordinator automatically creates a high-resolution static image (`plots.png`) at the end of each run for easy sharing in emails or chat:
*   **Libraries**: Uses **Pandas** for metrics pivoting/grouping and **Matplotlib** for rendering.
*   **Automation**: At the end of the run, the orchestrator invokes the helper script `plot_reports.py` which reads the run's compiled CSV, renders a multi-graph layout, and uploads the final `plots.png` directly to your GCS artifacts folder.

---

## 5. Production Deployment & Verification

To host the dashboard permanently on your dedicated GCE VM so that it is always running and accessible to your team members, execute the following commands inside the VM terminal:

### 1. Verification & Installation Commands
```bash
# 1. Clone the repository and switch to our dashboard branch
git clone https://github.com/GoogleCloudPlatform/gcsfuse-tools.git
cd gcsfuse-tools
git checkout add-benchmark-dashboard

# 2. Setup the python virtual environment & install libraries
python3 -m venv venv
source venv/bin/activate
pip3 install --upgrade pip
pip3 install -r benchmark-dashboard/requirements.txt

# 3. Configure the Team Password and GCS Database Bucket
export DASHBOARD_BUCKET="dmb-db"
export DASHBOARD_PASSWORD="YourSecureTeamPasswordHere"

# 4. Make scripts executable and install the systemd daemon
chmod +x benchmark-dashboard/start_server.sh
chmod +x benchmark-dashboard/install_service.sh
./benchmark-dashboard/install_service.sh
```

### 3. Monitoring & Managing the Live Service
*   To check if the service is active: `sudo systemctl status benchmark-dashboard`
*   To restart the service (e.g., after pulling new code): `sudo systemctl restart benchmark-dashboard`
*   To watch live server logs or debug crashes: `sudo journalctl -u benchmark-dashboard -n 50 -f`
*   **Team URL**: Teammates can now open Chrome and go directly to:
    ```
    http://<YOUR_VM_NAME>:8080/
    ```
