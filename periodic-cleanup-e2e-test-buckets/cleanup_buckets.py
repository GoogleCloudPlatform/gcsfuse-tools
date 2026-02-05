import argparse
import datetime
from datetime import timezone
import os
import sys
from google.cloud import storage

# --- Configuration & Constants ---
# Default values are sourced from environment variables to support Cloud Run configuration.
# Defaults can be overridden by command-line arguments (handled in main).

DEFAULT_PROJECT_ID = os.environ.get("PROJECT_ID", "gcs-fuse-test-ml")
DEFAULT_BUCKET_PREFIX = os.environ.get("BUCKET_PREFIX", "gcsfuse-e2e")
DEFAULT_RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 10))
# Parse DRY_RUN: "True", "true", "1" -> True
DEFAULT_DRY_RUN = os.environ.get("DRY_RUN", "False").lower() in (
    "true",
    "1",
    "yes",
)
# Parse QUIET: Default True
DEFAULT_QUIET = os.environ.get("QUIET", "True").lower() in ("true", "1", "yes")

# Global config variables (will be set in main)
PROJECT_ID = DEFAULT_PROJECT_ID
BUCKET_PREFIX = DEFAULT_BUCKET_PREFIX
RETENTION_DAYS = DEFAULT_RETENTION_DAYS
DRY_RUN = DEFAULT_DRY_RUN
QUIET = DEFAULT_QUIET


def log(msg):
  """Logs a message to stdout if QUIET mode is disabled.

  Errors should be printed using print() directly to ensure they appear.
  """
  if not QUIET:
    print(msg)


def cleanup_buckets():
  """Main cleanup logic: 1.

  Lists buckets in the project matching the prefix. 2. Checks creation time
  against the retention period. 3. Deletes expired buckets (and their objects)
  if not in Dry Run mode.
  """
  # Initialize Storage Client
  storage_client = storage.Client(project=PROJECT_ID)

  # Calculate cutoff time
  now = datetime.datetime.now(timezone.utc)
  cutoff_time = now - datetime.timedelta(days=RETENTION_DAYS)

  # Always print startup info to verify configuration in logs
  print(f"Starting cleanup for project: {PROJECT_ID}")
  print(
      f"Configuration: Prefix='{BUCKET_PREFIX}', Retention={RETENTION_DAYS}"
      f" days, DryRun={DRY_RUN}, Quiet={QUIET}"
  )

  if DRY_RUN:
    print("DRY RUN MODE ENABLED: No buckets will be deleted.")

  print(
      f"Deleting buckets starting with '{BUCKET_PREFIX}' created before"
      f" {cutoff_time}"
  )

  # List buckets
  buckets = list(storage_client.list_buckets(prefix=BUCKET_PREFIX))
  print(f"Found {len(buckets)} buckets with prefix '{BUCKET_PREFIX}'.")

  count = 0
  processed = 0

  # Iterate and process buckets
  for bucket in buckets:
    # Periodic progress log
    if processed > 0 and processed % 100 == 0:
      print(f"Processed {processed} buckets so far...")
    processed += 1

    # Check for expiration
    if bucket.time_created < cutoff_time:
      log(f"Deleting bucket: {bucket.name} (Created: {bucket.time_created})")
      try:
        if not DRY_RUN:
          # Explicitly delete blobs first to avoid "too many objects" errors
          # or timeouts when deleting the bucket directly.
          blobs = list(storage_client.list_blobs(bucket))
          if blobs:
            log(f"  Found {len(blobs)} objects. Deleting in batches...")
            # Delete in batches of 100 (API limit/safe chunk size)
            batch_size = 100
            for i in range(0, len(blobs), batch_size):
              batch = blobs[i : i + batch_size]
              bucket.delete_blobs(batch)

          # Delete the bucket itself
          bucket.delete(force=True)
          log(f"Successfully deleted {bucket.name}")
        else:
          log(f"DRY RUN: Would have deleted {bucket.name}")

        count += 1
        # Milestone log for successful deletions
        if count % 100 == 0:
          print(f"Milestone: Successfully deleted {count} buckets so far.")
      except Exception as e:
        # Always print errors
        print(f"Failed to delete {bucket.name}: {e}")
    else:
      log(
          f"Skipping bucket: {bucket.name} (Created: {bucket.time_created} >"
          f" {cutoff_time})"
      )

  print(f"Cleanup complete. Deleted {count} buckets.")


if __name__ == "__main__":
  # Argument Parsing
  parser = argparse.ArgumentParser(
      description=(
          "Clean up expired GCS buckets based on a prefix and retention period."
      )
  )

  parser.add_argument(
      "--project-id",
      default=DEFAULT_PROJECT_ID,
      help=f"Google Cloud Project ID (default: {DEFAULT_PROJECT_ID})",
  )
  parser.add_argument(
      "--bucket-prefix",
      default=DEFAULT_BUCKET_PREFIX,
      help=(
          f"Prefix for bucket names to scan (default: {DEFAULT_BUCKET_PREFIX})"
      ),
  )
  parser.add_argument(
      "--retention-days",
      type=int,
      default=DEFAULT_RETENTION_DAYS,
      help=f"Retention period in days (default: {DEFAULT_RETENTION_DAYS})",
  )
  parser.add_argument(
      "--dry-run",
      action="store_true",
      default=DEFAULT_DRY_RUN,
      help="Enable Dry Run mode (no deletions). Overrides env var if set.",
  )
  # Handle the 'False' default correctly with store_true logic
  # If env var is True, default is True. If user passes --no-dry-run?
  # Simple store_true is additive. Let's trust env vars + explicit flags.

  parser.add_argument(
      "--quiet",
      action="store_true",
      default=DEFAULT_QUIET,
      help="Enable Quiet mode (suppress info logs).",
  )

  args = parser.parse_args()

  # Apply configuration
  PROJECT_ID = args.project_id
  BUCKET_PREFIX = args.bucket_prefix
  RETENTION_DAYS = args.retention_days
  # If arg was not passed, use default. If passed (True), use True.
  # Note: If env was True, default is True.
  DRY_RUN = args.dry_run
  QUIET = args.quiet

  cleanup_buckets()
