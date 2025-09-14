#!/bin/bash

# --- Configuration ---
# Set the directory where your fio JSON files are located.
# IMPORTANT: Change this to the actual path of your results directory.
FIO_RESULTS_DIR="$1" 

# The name of the Python script to execute.
PYTHON_SCRIPT="create_fio_graph.py"

# The name of the virtual environment directory.
VENV_DIR=".venv"

# --- Pre-flight Checks ---
# Exit immediately if a command exits with a non-zero status.
set -e

# Check if the results directory exists
if [ ! -d "$FIO_RESULTS_DIR" ]; then
    echo "Error: FIO results directory not found at '$FIO_RESULTS_DIR'."
    echo "Please create it and place your JSON files inside, or update the FIO_RESULTS_DIR variable in this script."
    exit 1
fi

# Check if the Python script exists
if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo "Error: Python script '$PYTHON_SCRIPT' not found in the current directory."
    echo "Please make sure both scripts are in the same folder."
    exit 1
fi

# Check if python3 is available
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed or not in your PATH. Please install Python 3."
    exit 1
fi

# --- Main Execution ---
echo ">>> Starting FIO Performance Analysis <<<"

# Create a temporary virtual environment
echo "--> Step 1: Creating a temporary Python virtual environment in '$VENV_DIR'..."
python3 -m venv "$VENV_DIR"

# Activate the virtual environment
# The 'source' command is used to run the script in the current shell context
source "$VENV_DIR/bin/activate"
echo "--> Step 2: Virtual environment activated."

# Install required Python packages
# Using -q for a quieter installation. Added 'kaleido' for static image export.
echo "--> Step 3: Installing required Python libraries (pandas, plotly, kaleido)..."
pip install -q pandas plotly kaleido

echo "--> Step 4: Running the Python analysis script..."
echo "----------------------------------------------------"
# Execute the python script, passing the results directory as an argument
python3 "$PYTHON_SCRIPT" "$FIO_RESULTS_DIR"
echo "----------------------------------------------------"


# --- Cleanup ---
echo "--> Step 5: Deactivating and cleaning up the virtual environment..."
# Deactivate the virtual environment
deactivate

# Remove the virtual environment directory
# rm -rf "$VENV_DIR"

echo ">>> Analysis Complete. <<<"
echo "A static image file should now be in this directory."

