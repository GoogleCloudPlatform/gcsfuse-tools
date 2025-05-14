# Script to generate/read the parquet file

## Setup
### Create virtual environment
1. To create `python3 -m venv .venv`
2. To activate `source .venv/bin/activate`
3. To deactivate `deactivate`

### Install the requirement file
1. To ensure the latest pip `pip install --upgrade pip`
2. To install the requirements.txt `pip install --root-user-action ignore -r requirements.txt && pip cache purge`

### Run the script
`python3 load_parquet.py --file-path  ~/gcs/b.parquet --target-size-mb 100`

The above scripts first create a parquet file of 100mb if not already exist and then read.

### Output
Prints the time taken to read the parquet file.
Output for the above command:
`Parquet file read of 100 MB took 0.15 seconds`
