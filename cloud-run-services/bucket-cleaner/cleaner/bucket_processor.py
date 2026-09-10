# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Core orchestration logic for identifying, batching, and deleting orphan GCS buckets."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from cleaner.config import CleanerConfig
from cleaner.gcs_client import GCSClient

logger = logging.getLogger(__name__)


class BucketProcessor:
    """Discovers, audits, and purges orphan test buckets across GCP projects."""

    def __init__(self, config: CleanerConfig, gcs_client: GCSClient) -> None:
        self.config = config
        self.client = gcs_client

    def process_all_projects(self) -> Dict[str, Any]:
        """Execute cleanup sweep across all configured target projects."""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        cutoff_time = now_utc - datetime.timedelta(days=self.config.age_days)

        logger.info(
            "Starting Bucket Cleaner sweep: projects=%s, prefix='%s', cutoff=%s (age >= %d days, max_delete=%s, dry_run=%s, concurrency=%d)",
            self.config.projects,
            self.config.bucket_prefix,
            cutoff_time.isoformat(),
            self.config.age_days,
            self.config.max_delete,
            self.config.dry_run,
            self.config.concurrency,
        )

        overall_summary = {
            "total_scanned": 0,
            "total_eligible": 0,
            "deleted_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "max_delete_limit": self.config.max_delete,
            "dry_run": self.config.dry_run,
            "age_days_threshold": self.config.age_days,
            "cutoff_timestamp": cutoff_time.isoformat(),
        }

        project_results: Dict[str, Any] = {}
        total_deleted = 0

        for project_id in self.config.projects:
            remaining_quota = None
            if self.config.max_delete is not None:
                remaining_quota = max(0, self.config.max_delete - total_deleted)
                if remaining_quota == 0:
                    logger.info("Reached maximum delete quota of %d buckets. Skipping project '%s'.", self.config.max_delete, project_id)
                    project_results[project_id] = {
                        "scanned": 0, "eligible": 0, "deleted_count": 0, "failed_count": 0, "skipped_count": 0,
                        "deleted_buckets": [], "failed_buckets": [], "note": "skipped_due_to_max_delete_limit"
                    }
                    continue

            proj_data = self._process_single_project(project_id, cutoff_time, max_to_delete=remaining_quota)
            project_results[project_id] = proj_data

            overall_summary["total_scanned"] += proj_data["scanned"]
            overall_summary["total_eligible"] += proj_data["eligible"]
            overall_summary["deleted_count"] += proj_data["deleted_count"]
            overall_summary["failed_count"] += proj_data["failed_count"]
            overall_summary["skipped_count"] += proj_data["skipped_count"]

            total_deleted += proj_data["deleted_count"]

        status = "success"
        if overall_summary["failed_count"] > 0:
            status = "partial_success" if overall_summary["deleted_count"] > 0 else "error"

        return {
            "status": status,
            "service": "bucket-cleaner",
            "summary": overall_summary,
            "projects": project_results,
        }

    def _process_single_project(
        self, project_id: str, cutoff_time: datetime.datetime, max_to_delete: Optional[int] = None
    ) -> Dict[str, Any]:
        """Discover and delete eligible buckets in a single project with concurrency."""
        try:
            buckets = self.client.list_buckets(project_id, self.config.bucket_prefix)
        except Exception as exc:
            logger.error("Failed to list buckets in project '%s': %s", project_id, exc)
            return {
                "scanned": 0,
                "eligible": 0,
                "deleted_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "error": str(exc),
                "deleted_buckets": [],
                "failed_buckets": [],
            }

        total_scanned = len(buckets)
        eligible_buckets = []
        skipped_count = 0

        for b in buckets:
            c_time = getattr(b, "time_created", None)
            if c_time is None:
                skipped_count += 1
                continue

            if c_time.tzinfo is None:
                c_time = c_time.replace(tzinfo=datetime.timezone.utc)

            if c_time < cutoff_time:
                eligible_buckets.append(b)
            else:
                skipped_count += 1

        total_eligible = len(eligible_buckets)

        if max_to_delete is not None and len(eligible_buckets) > max_to_delete:
            logger.info("Applying max_delete limit: capping eligible buckets for '%s' from %d to %d.", project_id, len(eligible_buckets), max_to_delete)
            eligible_buckets = eligible_buckets[:max_to_delete]

        logger.info(
            "Project '%s': Scanned %d buckets with prefix '%s'. Found %d eligible (older than %d days), targeted for deletion: %d, skipped: %d.",
            project_id,
            total_scanned,
            self.config.bucket_prefix,
            total_eligible,
            self.config.age_days,
            len(eligible_buckets),
            skipped_count,
        )

        if len(eligible_buckets) == 0:
            return {
                "scanned": total_scanned,
                "eligible": total_eligible,
                "deleted_count": 0,
                "failed_count": 0,
                "skipped_count": skipped_count,
                "deleted_buckets": [],
                "failed_buckets": [],
            }

        deleted_buckets: List[str] = []
        failed_buckets: List[Dict[str, str]] = []

        counter_lock = threading.Lock()
        completed_count = 0

        def _delete_worker(bucket: Any) -> Tuple[bool, str, Optional[str]]:
            b_name = bucket.name
            if self.config.dry_run:
                return True, b_name, None

            try:
                self.client.delete_bucket(bucket, force=True)
                return True, b_name, None
            except Exception as del_err:
                err_str = str(del_err)
                logger.warning("Deletion failed for gs://%s: %s", b_name, err_str)
                if self.config.apply_olm_fallback:
                    try:
                        self.client.apply_lifecycle_rule(bucket, age_days=1)
                        err_str += " (Fallback 1-day OLM rule applied)"
                    except Exception as olm_err:
                        err_str += f" (OLM fallback failed: {olm_err})"
                return False, b_name, err_str

        with ThreadPoolExecutor(max_workers=self.config.concurrency) as executor:
            future_to_bucket = {
                executor.submit(_delete_worker, b): b for b in eligible_buckets
            }

            for future in as_completed(future_to_bucket):
                success, name, err = future.result()
                with counter_lock:
                    completed_count += 1
                    if success:
                        deleted_buckets.append(name)
                    else:
                        failed_buckets.append({"bucket": name, "error": err or "unknown error"})

                    if (
                        completed_count % self.config.batch_size == 0
                        or completed_count == len(eligible_buckets)
                    ):
                        logger.info(
                            "[%s Progress] Deleted %d/%d targeted buckets (%d failed)...",
                            project_id,
                            len(deleted_buckets),
                            len(eligible_buckets),
                            len(failed_buckets),
                        )

        return {
            "scanned": total_scanned,
            "eligible": total_eligible,
            "deleted_count": len(deleted_buckets),
            "failed_count": len(failed_buckets),
            "skipped_count": skipped_count,
            "deleted_buckets": deleted_buckets,
            "failed_buckets": failed_buckets,
        }
