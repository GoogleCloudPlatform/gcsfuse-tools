import argparse
import datetime
import os
import sys
from datetime import timezone
from google.cloud import storage

# --- Configuration & Constants ---
# Default values are sourced from environment variables to support Cloud Run configuration.
# Defaults can be overridden by command-line arguments (handled in main).

DEFAULT_PROJECT_ID = os.environ.get("PROJECT_ID", "gcs-fuse-test-ml")
DEFAULT_BUCKET_PREFIX = os.environ.get("BUCKET_PREFIX", "gcsfuse-e2e")
DEFAULT_RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 10))
# Parse DRY_RUN: "True", "true", "1" -> True
DEFAULT_DRY_RUN = os.environ.get("DRY_RUN", "False").lower() in ("true", "1", "yes")
# Parse QUIET: Default True
DEFAULT_QUIET = os.environ.get("QUIET", "True").lower() in ("true", "1", "yes")

class CleanupConfig:
    def __init__(self, project_id, bucket_prefix, retention_days, dry_run, quiet):
        self.project_id = project_id
        self.bucket_prefix = bucket_prefix
        self.retention_days = retention_days
        self.dry_run = dry_run
        self.quiet = quiet

def log(msg, config):
    """
    Logs a message to stdout if QUIET mode is disabled.
    Errors should be printed using print() directly to ensure they appear.
    """
    if not config.quiet:
        print(msg)


def cleanup_buckets(config):
    """
    Main cleanup logic:
    1. Lists buckets in the project matching the prefix.
    2. Checks creation time against the retention period.
    3. Deletes expired buckets (and their objects) if not in Dry Run mode.
    """
    # Initialize Storage Client
    storage_client = storage.Client(project=config.project_id)
    
    # Calculate cutoff time
    now = datetime.datetime.now(timezone.utc)
    cutoff_time = now - datetime.timedelta(days=config.retention_days)

    # Always print startup info to verify configuration in logs
    print(f"Starting cleanup for project: {config.project_id}")
    print(f"Configuration: Prefix='{config.bucket_prefix}', Retention={config.retention_days} days, DryRun={config.dry_run}, Quiet={config.quiet}")
    
    if config.dry_run:
        print("DRY RUN MODE ENABLED: No buckets will be deleted.")

    print(
        f"Deleting buckets starting with '{config.bucket_prefix}' created before"
        f" {cutoff_time}"
    )

    # List buckets
    buckets = list(storage_client.list_buckets(prefix=config.bucket_prefix))
    print(f"Found {len(buckets)} buckets with prefix '{config.bucket_prefix}'.")

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
            log(f"Deleting bucket: {bucket.name} (Created: {bucket.time_created})", config)
            try:
                if not config.dry_run:
                    # Explicitly delete blobs first to avoid "too many objects" errors
                    # or timeouts when deleting the bucket directly.
                    # Use a generator to avoid loading all blobs into memory at once.
                    blobs_generator = storage_client.list_blobs(bucket)
                    
                    batch = []
                    total_blobs_deleted = 0
                    
                    for blob in blobs_generator:
                        batch.append(blob)
                        if len(batch) >= 100:
                            bucket.delete_blobs(batch)
                            total_blobs_deleted += len(batch)
                            log(f"  Deleted batch of {len(batch)} objects...", config)
                            batch = []
                    
                    # Delete remaining blobs
                    if batch:
                        bucket.delete_blobs(batch)
                        total_blobs_deleted += len(batch)
                        log(f"  Deleted final batch of {len(batch)} objects...", config)

                    if total_blobs_deleted > 0:
                        log(f"  Total objects deleted: {total_blobs_deleted}", config)

                    # Delete the bucket itself
                    bucket.delete(force=True)
                    log(f"Successfully deleted {bucket.name}", config)
                else:
                    log(f"DRY RUN: Would have deleted {bucket.name}", config)
                
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
                f" {cutoff_time})", config
            )

    print(f"Cleanup complete. Deleted {count} buckets.")


if __name__ == "__main__":
    # Argument Parsing
    parser = argparse.ArgumentParser(
        description="Clean up expired GCS buckets based on a prefix and retention period."
    )
    
    parser.add_argument(
        "--project-id",
        default=DEFAULT_PROJECT_ID,
        help=f"Google Cloud Project ID (default: {DEFAULT_PROJECT_ID})"
    )
    parser.add_argument(
        "--bucket-prefix",
        default=DEFAULT_BUCKET_PREFIX,
        help=f"Prefix for bucket names to scan (default: {DEFAULT_BUCKET_PREFIX})"
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help=f"Retention period in days (default: {DEFAULT_RETENTION_DAYS})"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=DEFAULT_DRY_RUN,
        help="Enable Dry Run mode (no deletions). Overrides env var if set."
    )
    
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=DEFAULT_QUIET,
        help="Enable Quiet mode (suppress info logs)."
    )

    args = parser.parse_args()

    # Create config object
    config = CleanupConfig(
        project_id=args.project_id,
        bucket_prefix=args.bucket_prefix,
        retention_days=args.retention_days,
        dry_run=args.dry_run,
        quiet=args.quiet
    )

    cleanup_buckets(config)
