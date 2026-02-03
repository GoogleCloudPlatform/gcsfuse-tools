import datetime
from datetime import timezone
from google.cloud import storage

PROJECT_ID = "gcs-fuse-test-ml"
BUCKET_PREFIX = "gcsfuse-e2e"
RETENTION_DAYS = 5
DRY_RUN = False


def cleanup_buckets():
  storage_client = storage.Client(project=PROJECT_ID)
  now = datetime.datetime.now(timezone.utc)
  cutoff_time = now - datetime.timedelta(days=RETENTION_DAYS)

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
  for bucket in buckets:
    if bucket.time_created < cutoff_time:
      print(f"Deleting bucket: {bucket.name} (Created: {bucket.time_created})")
      try:
        if not DRY_RUN:
            bucket.delete(force=True)
            print(f"Successfully deleted {bucket.name}")
        else:
            print(f"DRY RUN: Would have deleted {bucket.name}")
        count += 1
      except Exception as e:
        print(f"Failed to delete {bucket.name}: {e}")
    else:
      print(f"Skipping bucket: {bucket.name} (Created: {bucket.time_created} > {cutoff_time})")

  print(f"Cleanup complete. Deleted {count} buckets.")


if __name__ == "__main__":
  cleanup_buckets()
