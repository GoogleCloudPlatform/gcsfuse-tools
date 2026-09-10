# Google Cloud Storage Orphan Bucket Cleaner (`bucket-cleaner`)

An enterprise-grade Cloud Run / Cloud Functions (Gen 2) microservice designed to sweep and delete orphaned `gcsfuse-e2e-*` test buckets across Google Cloud Storage projects (`gcs-fuse-test` and `gcs-fuse-test-ml`).

Orphan test buckets accumulate when Kokoro CI runs or developer presubmits are interrupted, cancelled, or aborted (such as when new commits supersede previous presubmit jobs with `SIGKILL`). `bucket-cleaner` runs on a scheduled cadence (Daily at 00:00 UTC) to identify buckets older than the retention threshold (default: 3 days) and destroy them.

---

## Key Features

1. **Multi-Project Scanning**: Audits both `gcs-fuse-test` and `gcs-fuse-test-ml` in a single coordinated run.
2. **Server-Side Prefix Filtering**: Queries Cloud Storage directly with prefix `gcsfuse-e2e-` to eliminate unnecessary listing overhead.
3. **Age-Based Retention Policy**: Safely checks bucket `time_created`. Only buckets older than `age_days` (default: 3 days / 72 hours) are targeted, strictly protecting currently running CI and nightly jobs.
4. **Concurrent High-Throughput Deletion**: Uses a configurable worker pool (`concurrency=16`) to delete buckets in parallel, capable of deleting thousands of buckets in minutes.
5. **Dual-Layer Safety & OLM Fallback**: Uses `bucket.delete(force=True)` to wipe objects and buckets. If an individual bucket cannot be immediately purged (due to rate limits, Managed Folders, or locks), it dynamically applies a 1-day **Object Lifecycle Management (OLM)** expiration rule so Cloud Storage server-side cleans objects before the next sweep.
6. **Progress Logging**: Emits structured progress logs (e.g. `Processed X/Y eligible buckets...`) at configurable batch intervals (`batch_size=50`).
7. **Zero-Trust Security**: Cloud Run endpoint requires strict OIDC bearer token authentication via Cloud Scheduler.

---

## Architecture & Invocation Flow

```
+-----------------------------------------------------------------------------------------+
|                                  gcs-fuse-test                                          |
|                                                                                         |
|  +------------------------+      OIDC Bearer Token       +---------------------------+  |
|  |    Cloud Scheduler     | ---------------------------> |     Cloud Run Service     |  |
|  | (bucket-cleaner-sched) |    (Audience: Cloud Run)     |    (bucket-cleaner-sa)    |  |
|  |     [0 0 * * *]        |                              |   [concurrency: 16]       |  |
|  +------------------------+                              +-------------+-------------+  |
|                                                                        |                |
|                                           +----------------------------+------------+   |
|                                           |                                         |   |
|                                           v                                         v   |
|                                +---------------------+                   +--------------------+
|                                |    gcs-fuse-test    |                   |  gcs-fuse-test-ml  |
|                                | (gcsfuse-e2e-* >3d) |                   | (gcsfuse-e2e-* >3d)|
|                                +---------------------+                   +--------------------+
+-----------------------------------------------------------------------------------------+
```

---

## IAM Roles & Permissions

| Identity | Recommended Service Account | Roles Required | Purpose |
| :--- | :--- | :--- | :--- |
| **Cloud Run Runner** | `bucket-cleaner-sa@gcs-fuse-test.iam.gserviceaccount.com` | `roles/storage.admin`<br/>`roles/logging.logWriter` | List, inspect, and delete buckets across target projects; write structured logs. |
| **Cloud Scheduler Invoker** | `bucket-cleaner-sched@gcs-fuse-test.iam.gserviceaccount.com` | `roles/run.invoker` | Generate OIDC token to invoke Cloud Run endpoint. |

> [!NOTE]
> To allow the runner SA to clean `gcs-fuse-test-ml`, grant `roles/storage.admin` on `gcs-fuse-test-ml`:
> ```bash
> gcloud projects add-iam-policy-binding gcs-fuse-test-ml \
>     --member="serviceAccount:bucket-cleaner-sa@gcs-fuse-test.iam.gserviceaccount.com" \
>     --role="roles/storage.admin"
> ```

---

## Deployment Quickstart

### Automated Deployment via `deploy.sh`

```bash
cd cloud-run-services/bucket-cleaner

# Deploy to gcs-fuse-test with automatic IAM role assignment
./deploy.sh --project gcs-fuse-test -y
```

### Options Supported by `deploy.sh`
* `-p, --project`: Target host project ID (default: `gcs-fuse-test`).
* `-r, --region`: GCP region (default: `us-central1`).
* `-s, --schedule`: Cron schedule expression (default: `0 0 * * *` — daily at midnight UTC).
* `--age-days`: Retention threshold in days (default: `3`).
* `-d, --dry-run`: Deploy with default invocation in simulation mode.

---

## Configuration & API Reference

### Request Payload Schema

```json
{
  "projects": ["gcs-fuse-test", "gcs-fuse-test-ml"],
  "bucket_prefix": "gcsfuse-e2e-",
  "age_days": 3,
  "dry_run": false,
  "concurrency": 16,
  "batch_size": 50,
  "apply_olm_fallback": true
}
```

### Manual Triggering

#### 1. Via Cloud Scheduler (On-Demand Sweep)
```bash
gcloud scheduler jobs run bucket-cleaner-scheduler \
    --project gcs-fuse-test \
    --location us-central1
```

#### 2. Via Authenticated Cloud Run Proxy (with Dry-Run)
```bash
gcloud run services proxy bucket-cleaner \
    --project gcs-fuse-test \
    --region us-central1 \
    -- http://localhost:8080/ \
    -H "Content-Type: application/json" \
    -d '{"dry_run": true, "age_days": 3}'
```

---

## Local Development & Testing

Run unit tests 100% offline (no cloud dependencies required):

```bash
cd cloud-run-services/bucket-cleaner
python3 -m unittest discover -s tests -v
```
