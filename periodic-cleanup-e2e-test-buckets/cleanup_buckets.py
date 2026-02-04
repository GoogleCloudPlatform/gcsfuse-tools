import datetime
from datetime import timezone
import os
from google.cloud import storage

PROJECT_ID = os.environ.get("PROJECT_ID", "gcs-fuse-test-ml")
BUCKET_PREFIX = os.environ.get("BUCKET_PREFIX", "gcsfuse-e2e")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 10))
# Parse DRY_RUN: "True", "true", "1" -> True
DRY_RUN = os.environ.get("DRY_RUN", "False").lower() in ("true", "1", "yes")
# Parse QUIET: Default True
QUIET = os.environ.get("QUIET", "True").lower() in ("true", "1", "yes")


def log(msg):
  if not QUIET:
    print(msg)


def cleanup_buckets():
  storage_client = storage.Client(project=PROJECT_ID)
  now = datetime.datetime.now(timezone.utc)
  cutoff_time = now - datetime.timedelta(days=RETENTION_DAYS)

  # Always print startup info
  print(f"Starting cleanup for project {PROJECT_ID}")
  if DRY_RUN:
    print("DRY RUN MODE ENABLED: No buckets will be deleted.")

  print(
      f"Deleting buckets starting with '{BUCKET_PREFIX}' created before"
      f" {cutoff_time}"
  )

  buckets = list(storage_client.list_buckets(prefix=BUCKET_PREFIX))
  print(f"Found {len(buckets)} buckets with prefix '{BUCKET_PREFIX}'.")

  count = 0
  processed = 0
  for bucket in buckets:
    if processed > 0 and processed % 10 == 0:
      print(f"Processed {processed} buckets so far...")
    processed += 1

    if bucket.time_created < cutoff_time:
      log(f"Deleting bucket: {bucket.name} (Created: {bucket.time_created})")
      try:
        if not DRY_RUN:
          # Explicitly delete blobs first to avoid "too many objects" errors
          blobs = list(storage_client.list_blobs(bucket))
          if blobs:
            log(f"  Found {len(blobs)} objects. Deleting in batches...")
            batch_size = 100
            for i in range(0, len(blobs), batch_size):
              batch = blobs[i : i + batch_size]
              bucket.delete_blobs(batch)

          bucket.delete(force=True)
          log(f"Successfully deleted {bucket.name}")
        else:
          log(f"DRY RUN: Would have deleted {bucket.name}")
        count += 1
      except Exception as e:
        print(f"Failed to delete {bucket.name}: {e}")
    else:
      log(
          f"Skipping bucket: {bucket.name} (Created: {bucket.time_created} >"
          f" {cutoff_time})"
      )

  print(f"Cleanup complete. Deleted {count} buckets.")


if __name__ == "__main__":
  cleanup_buckets()
