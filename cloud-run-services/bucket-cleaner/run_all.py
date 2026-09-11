import os
import sys
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("cleaner_run.log", mode="w"),
        logging.StreamHandler(sys.stdout)
    ]
)
sys.path.insert(0, ".")

from cleaner.config import CleanerConfig
from cleaner.gcs_client import GCSClient
from cleaner.bucket_processor import BucketProcessor

def main():
    concurrency = int(os.environ.get("CONCURRENCY", "16"))
    config = CleanerConfig(
        projects=["gcs-fuse-test", "gcs-fuse-test-ml"],
        bucket_prefix="gcsfuse-e2e-",
        age_days=3,
        max_delete=None,
        dry_run=False,
        concurrency=concurrency,
        batch_size=50
    )

    logging.info("Starting background cleanup across projects: %s (concurrency=%d)", config.projects, concurrency)
    processor = BucketProcessor(config=config, gcs_client=GCSClient())
    result = processor.process_all_projects()

    logging.info("=" * 50)
    logging.info("FINAL CLEANUP SUMMARY:")
    for proj, stats in result.get("projects", {}).items():
        logging.info("  Project %s: scanned=%d, found=%d, deleted=%d, olm=%d, failed=%d", proj, stats["scanned"], stats.get("eligible", 0), stats.get("deleted_count", 0), stats.get("olm_count", 0), stats.get("failed_count", 0))
    logging.info("Overall: total_found=%d, total_deleted=%d, total_olm=%d, total_failed=%d", result["summary"].get("total_eligible", 0), result["summary"].get("deleted_count", 0), result["summary"].get("olm_count", 0), result["summary"].get("failed_count", 0))
    logging.info("=" * 50)

if __name__ == "__main__":
    main()
