# Periodic GCS Bucket Cleanup Job

This project contains a Cloud Run job that runs periodically to delete expired GCS buckets used in e2e tests.

## Logic
The job deletes buckets in the target project that match a prefix and are older than a specific retention period.

Behavior is controlled by environment variables (configurable in Cloud Run Job settings):
*   `PROJECT_ID`: The Google Cloud Project ID (Default: `gcs-fuse-test-ml`).
*   `BUCKET_PREFIX`: Prefix of buckets to scan (Default: `gcsfuse-e2e`).
*   `RETENTION_DAYS`: Number of days to keep buckets (Default: 10).
*   `DRY_RUN`: If `True` (or "1", "yes"), only logs what would be deleted. If `False`, performs actual deletion (Default: `False`).
*   `QUIET`: If `True`, suppresses informational logs (e.g., "Deleting bucket...", "Successfully deleted..."). Only errors are printed. (Default: `True`).

## Prerequisites
*   Google Cloud SDK (gcloud) installed and authenticated.
*   Access to the `gcs-fuse-test-ml` project (or your target project).

## Directory Structure
*   `run.sh`: **Primary deployment script.** Automates build, SA creation, and deployment.
*   `cleanup_buckets.py`: The Python script performing the cleanup.
*   `Dockerfile`: Configuration to containerize the script.
*   `requirements.txt`: Python dependencies.

## Deployment

### Single-Click Deployment (Recommended)
Use the `run.sh` script. It handles:
1.  Checking prerequisites and enabling required APIs (`run`, `cloudscheduler`, `cloudbuild`, `artifactregistry`).
2.  Creating/Configuring the Service Account (`[USER]-e2e-cleanup-sa`) with necessary roles (`Storage Admin`, `Cloud Run Invoker`).
3.  Building and pushing the Docker image.
4.  Creating/Updating the Cloud Run Job (configured with 1 hour timeout).
5.  Creating/Updating the Cloud Scheduler trigger (runs daily at 2 AM).

```bash
./run.sh
```

Run `./run.sh --help` for usage information.

### Manual Deployment
If you prefer to deploy manually, refer to the logic in `run.sh`.
Key steps involve:
1.  `gcloud builds submit ...`
2.  `gcloud run jobs create ...`
3.  `gcloud scheduler jobs create http ...`

## Access and Monitoring
To monitor the job execution and view logs:

1.  **Cloud Run Jobs Console:** Go to [Cloud Run Jobs](https://console.cloud.google.com/run/jobs?project=gcs-fuse-test-ml).
    *   Find the job named `gcsfuse-e2e-buckets-cleanup-job`.
    *   Click on the job to see execution history, status, and logs.
2.  **Cloud Scheduler Console:** Go to [Cloud Scheduler](https://console.cloud.google.com/cloudscheduler?project=gcs-fuse-test-ml).
    *   Find the schedule `gcsfuse-e2e-buckets-cleanup-schedule`.
    *   Check the "Last run" status and logs for the trigger invocation.

## Local Development
1.  Create virtual env: `python3 -m venv .venv && source .venv/bin/activate`
2.  Install deps: `pip install -r requirements.txt`
3.  Run: `python cleanup_buckets.py`
