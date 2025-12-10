import os
import socket
import sys

# Constants and Configuration
HOME = os.environ.get("HOME", "")
USER = os.environ.get("USER", "")
HOSTNAME = socket.gethostname().lower()

# --- Path Detection Logic ---
# Current Script: .../python/single_node_single_mount/config.py
# PKG_ROOT: .../python
PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Detect if we are in the "Shared" structure
grandparent_dir = os.path.dirname(os.path.dirname(PKG_ROOT))

# Default fallback roots (Production style)
DEFAULT_SHARED_ROOT_BASE = os.path.join(HOME, "work", "shared")
DEFAULT_WORK_ROOT_BASE = os.path.join(HOME, "work", "tasks")
DEFAULT_MOUNT_ROOT_BASE = os.path.join(HOME, "work", "test_buckets")

if os.path.basename(grandparent_dir) == "shared":
    # We are likely in /tmp/coherency-validation/shared/coherency-validation/python...
    # PROJECT_ROOT = /tmp/coherency-validation
    PROJECT_ROOT = os.path.dirname(grandparent_dir)
    
    # Set defaults relative to this PROJECT_ROOT
    DEFAULT_SHARED_ROOT_BASE = os.path.join(PROJECT_ROOT, "shared")
    DEFAULT_WORK_ROOT_BASE = os.path.join(PROJECT_ROOT, "tasks")
    DEFAULT_MOUNT_ROOT_BASE = os.path.join(PROJECT_ROOT, "test_buckets")

# --- Configurable Paths ---
SHARED_ROOT_BASE = os.environ.get("COHERENCY_SHARED_ROOT", DEFAULT_SHARED_ROOT_BASE)
WORK_ROOT_BASE = os.environ.get("COHERENCY_WORK_ROOT", DEFAULT_WORK_ROOT_BASE)
MOUNT_ROOT = os.environ.get("COHERENCY_MOUNT_ROOT", DEFAULT_MOUNT_ROOT_BASE)

# ... (Previous imports and setup)

# --- Derived Specific Paths ---
# Tasks/Logs: .../tasks/coherency-validation/python/single_node_single_mount
WORK_DIR = os.path.join(WORK_ROOT_BASE, "coherency-validation", "python", "single_node_single_mount")

# Workflow Configuration
REQUIRES_SHARING = False

if REQUIRES_SHARING:
    # Use the directory this file is in (Shared volume)
    SHARED_STATE_DIR = os.path.dirname(os.path.abspath(__file__))
else:
    # Use the local tasks directory
    SHARED_STATE_DIR = WORK_DIR

# New Config Files
SHARED_SPECIFIC_CONFIG_FILE = os.path.join(SHARED_STATE_DIR, "scenario_specific_config")
SHARED_GLOBAL_CONFIG_FILE = os.path.join(SHARED_STATE_DIR, "scenario_config")

# Backwards compatibility alias
SHARED_SCENARIO_FILE = SHARED_SPECIFIC_CONFIG_FILE

# ... (Rest of file)

# --- Mount Number Logic ---
env_mount = os.environ.get("MOUNT_NUMBER")
MOUNT_NUMBER = 0

if env_mount:
    try:
        MOUNT_NUMBER = int(env_mount)
        # Warnings suppressed
    except ValueError:
        print(f"Error: Invalid MOUNT_NUMBER environment variable '{env_mount}'.", file=sys.stderr)
else:
    if "gargnitin-ubuntu2504-e2std8-asiase1b" in HOSTNAME:
        MOUNT_NUMBER = 1
    elif "gargnitin-ubuntu2504-e2std8-asiase1c" in HOSTNAME:
        MOUNT_NUMBER = 2

# Bucket Info
BUCKET_NAME = "gargnitin-test-hns-asiase1"
MOUNT_PATH_TEMPLATE = os.path.join(MOUNT_ROOT, f"{BUCKET_NAME}-mount")
MOUNT_PATH = MOUNT_PATH_TEMPLATE

# Go Programs
GO_READ_PROGRAM = os.path.join(PKG_ROOT, "read.go")
GO_WRITE_PROGRAM = os.path.join(PKG_ROOT, "write.go")

# Ensure directories exist
os.makedirs(SHARED_STATE_DIR, exist_ok=True)
os.makedirs(WORK_DIR, exist_ok=True)
os.makedirs(MOUNT_ROOT, exist_ok=True)
