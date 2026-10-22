#!/bin/bash
# Startup script for GCSFuse Benchmark Dashboard managed by Systemd
cd "$(dirname "$0")"

# Load user profile to inherit env variables (like gcloud paths, etc.)
if [ -f "$HOME/.bashrc" ]; then
    source "$HOME/.bashrc"
fi
if [ -f "$HOME/.profile" ]; then
    source "$HOME/.profile"
fi

# Activate virtualenv if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d "../venv" ]; then
    source ../venv/bin/activate
fi

# Run uvicorn using exec so systemd can track the PID and manage lifecycle
exec python3 -m uvicorn main:app --host 0.0.0.0 --port 8080
