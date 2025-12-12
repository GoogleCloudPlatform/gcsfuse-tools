#!/bin/bash
set -e

sudo apt install -y python3.13-venv

# Locate the script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Use a stable, local cache directory for the VENV
VENV_DIR="$HOME/.cache/coherency-validation/.venv"

echo "Script directory: $SCRIPT_DIR"
echo "Target Virtual Environment: $VENV_DIR"

# Ensure parent directory exists
mkdir -p "$(dirname "$VENV_DIR")"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
    echo "Virtual environment successfully created."
else
    echo "Virtual environment already exists."
fi

source "$VENV_DIR/bin/activate"
echo "Activated virtual environment: $VIRTUAL_ENV"

echo "Upgrading pip..."
pip install --upgrade pip

if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    echo "Installing requirements from: $SCRIPT_DIR/requirements.txt"
    pip install -r "$SCRIPT_DIR/requirements.txt"
else
    echo "No requirements.txt found at $SCRIPT_DIR/requirements.txt, skipping dependency installation."
fi

echo "Setup complete. Activate with: source $VENV_DIR/bin/activate"
