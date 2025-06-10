GCSFuse Stalled Read Retry Analysis Toolkit
============================================

This directory contains a set of scripts designed to fetch and analyze Google Cloud Storage FUSE (GCSFuse) logs from Google Cloud Logging. The tools help in identifying and visualizing the frequency and distribution of retries caused by stalled read requests.

The workflow is designed to be simple: fetch_logs.sh downloads the necessary logs to a pre-defined location (/tmp/), and the Python analysis scripts automatically find and process those logs.


Scripts Overview
----------------

1. fetch_logs.sh: A bash script that queries and downloads relevant GCSFuse logs into a file at /tmp/<job_name>-logs.csv.
2. retries_per_interval.py: A Python script that reads /tmp/<job_name>-logs.csv, aggregates retry counts into time-based intervals, and generates a CSV file and a bar chart visualization.
3. requests_per_retry_count.py: A Python script that reads /tmp/<job_name>-logs.csv, counts how many times each unique request was retried, and provides a summary table.


Prerequisites
-------------

Before you begin, ensure you have the following installed and configured:

* Google Cloud SDK (gcloud): Required by fetch_logs.sh to query logs from Google Cloud Logging. You must be authenticated (gcloud auth login) and have the necessary permissions.
  - Installation Guide (https://cloud.google.com/sdk/docs/install)

* Python 3: Required to run the analysis scripts.

* Python Libraries: The analysis scripts depend on pandas and matplotlib. You can install them using the provided requirements.txt file:
    
    pip install -r requirements.txt


Usage Workflow
--------------

The intended workflow is a 3-step process:

Step 1: Fetch Logs

First, use the fetch_logs.sh script to download the GCSFuse logs. The script requires you to provide a job_name, which will be used to create the log file in the /tmp directory.

Syntax:

    ./fetch_logs.sh <cluster_name> <job_name> <start_time> <end_time>

Example:

    ./fetch_logs.sh xpk-large-scale-usc1f-a sample-job "2025-02-04T18:00:00+05:30" "2025-02-05T10:00:00+05:30"

Output:

* A file named /tmp/sample-job-logs.csv will be created. The Python scripts below depend on this exact path and naming convention.


Step 2: Analyze and Visualize Retries Over Time

`retries_per_interval.py` is used to see how the frequency of retries changes over time. Provide the same job_name you used in Step 1. **You must run `fetch_logs.sh` successfully before using this script**, as it depends on the log file created in the first step.

Syntax:

    ./retries_per_interval.py <job_name> [OPTIONS]

Example:

    # This script will automatically read from /tmp/sample-job-logs.csv
    ./retries_per_interval.py sample-job --interval 5m

Output:

1. sample-job-retries.csv: A CSV file in your current directory.
2. sample-job-retries.png: A bar chart visualizing the retries, saved in your current directory.


Step 3: Analyze Unique Requests per Retry Count

`requests_per_retry_count.py` analyzes the logs to determine how many unique requests were retried a specific number of times. It generates a summary table showing, for example, how many requests were retried exactly once, exactly twice, and so on. Provide the same job_name you used in Step 1. **You must run `fetch_logs.sh` successfully before using this script**.

Syntax:

    ./requests_per_retry_count.py <job_name>

Example:

    # This script will also read from /tmp/sample-job-logs.csv
    ./requests_per_retry_count.py sample-job

Output:

* A summary table printed to the console:

    Processing file: /tmp/sample-job-logs.csv
```
    Retries    | Requests
    -----------+----------
    1          | 248
    2          | 32
    3          | 10
```
