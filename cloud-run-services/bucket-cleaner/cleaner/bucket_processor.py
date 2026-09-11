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
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from cleaner.config import CleanerConfig
from cleaner.gcs_client import GCSClient, storage

try:
    from google.cloud import bigquery
except ImportError:
    bigquery = None

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
            "Starting Bucket Cleaner sweep: projects=%s, prefix='%s', cutoff=%s (age >= %s days, max_delete=%s, dry_run=%s, concurrency=%d)",
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
            "total_found": 0,
            "deleted_count": 0,
            "olm_count": 0,
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
                        "scanned": 0, "eligible": 0, "found": 0, "deleted_count": 0, "olm_count": 0, "failed_count": 0, "skipped_count": 0,
                        "deleted_buckets": [], "olm_buckets": [], "failed_buckets": [], "note": "skipped_due_to_max_delete_limit"
                    }
                    continue

            proj_data = self._process_single_project(project_id, cutoff_time, max_to_delete=remaining_quota)
            project_results[project_id] = proj_data

            overall_summary["total_scanned"] += proj_data["scanned"]
            overall_summary["total_eligible"] += proj_data["eligible"]
            overall_summary["total_found"] += proj_data.get("found", proj_data["eligible"])
            overall_summary["deleted_count"] += proj_data["deleted_count"]
            overall_summary["olm_count"] += proj_data.get("olm_count", 0)
            overall_summary["failed_count"] += proj_data["failed_count"]
            overall_summary["skipped_count"] += proj_data["skipped_count"]

            total_deleted += proj_data["deleted_count"]

        status = "success"
        if overall_summary["failed_count"] > 0:
            status = "partial_success" if (overall_summary["deleted_count"] > 0 or overall_summary["olm_count"] > 0) else "error"

        # Generate simple daily CSV metrics
        today_str = now_utc.strftime("%Y-%m-%d")
        csv_header = "date,project,found,deleted,olm,failed"
        csv_lines = [csv_header]
        csv_rows = []

        for project_id, proj_data in project_results.items():
            row_str = f"{today_str},{project_id},{proj_data.get('eligible', 0)},{proj_data.get('deleted_count', 0)},{proj_data.get('olm_count', 0)},{proj_data.get('failed_count', 0)}"
            csv_lines.append(row_str)
            csv_rows.append(row_str)

        total_row = f"{today_str},total,{overall_summary['total_eligible']},{overall_summary['deleted_count']},{overall_summary['olm_count']},{overall_summary['failed_count']}"
        csv_lines.append(total_row)
        csv_rows.append(total_row)

        csv_content = "\n".join(csv_lines)

        logger.info("=== DAILY BUCKET CLEANER METRICS (CSV) ===")
        for line in csv_lines:
            logger.info(line)
        logger.info("==========================================")

        if self.config.enable_bq_logging:
            self._record_metrics_to_bigquery(now_utc, project_results)

        return {
            "status": status,
            "service": "bucket-cleaner",
            "summary": overall_summary,
            "projects": project_results,
            "csv_metrics": csv_content,
        }

    def _record_metrics_to_bigquery(
        self, now_utc: datetime.datetime, project_results: Dict[str, Any]
    ) -> None:
        """Insert exactly 1 row per target project into BigQuery daily_metrics table."""
        today_str = now_utc.strftime("%Y-%m-%d")
        timestamp_str = now_utc.strftime("%Y-%m-%d %H:%M:%S")

        rows_to_insert = []
        for project_id, proj_data in project_results.items():
            rows_to_insert.append({
                "date": today_str,
                "project": project_id,
                "found": int(proj_data.get("eligible", 0)),
                "deleted": int(proj_data.get("deleted_count", 0)),
                "olm": int(proj_data.get("olm_count", 0)),
                "failed": int(proj_data.get("failed_count", 0)),
                "timestamp": timestamp_str,
            })

        if not rows_to_insert:
            return

        table_id = f"{self.config.bq_project}.{self.config.bq_dataset}.{self.config.bq_table}"

        if not bigquery:
            logger.warning("google-cloud-bigquery library is not installed; skipping BigQuery metrics logging.")
            return

        try:
            client = bigquery.Client(project=self.config.bq_project)
            errors = client.insert_rows_json(table_id, rows_to_insert)
            if not errors:
                logger.info("Successfully recorded %d metrics rows to BigQuery table: %s", len(rows_to_insert), table_id)
            else:
                logger.error("BigQuery SDK insert_rows_json returned errors: %s", errors)
        except Exception as bq_err:
            logger.error("Failed to record metrics to BigQuery table '%s': %s", table_id, bq_err)

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
                "eligible": 0,
                "found": 0,
                "deleted_count": 0,
                "olm_count": 0,
                "failed_count": 0,
                "skipped_count": skipped_count,
                "deleted_buckets": [],
                "olm_buckets": [],
                "failed_buckets": [],
            }

        deleted_buckets: List[str] = []
        olm_buckets: List[str] = []
        failed_buckets: List[Dict[str, str]] = []

        counter_lock = threading.Lock()
        completed_count = 0

        # Dedicated executor for bucket deletion calls to enforce per-bucket timeout
        deleter_executor = ThreadPoolExecutor(max_workers=self.config.concurrency)

        def _delete_worker(bucket: Any) -> Tuple[str, str, Optional[str]]:
            """Returns (status, bucket_name, error_message).

            status is one of:
            - 'deleted': Bucket was successfully deleted directly.
            - 'olm': Deletion failed/timed out, but 1-day OLM lifecycle rule was applied.
            - 'failed': Both deletion and OLM fallback failed.
            """
            b_name = bucket.name
            if self.config.dry_run:
                return "deleted", b_name, None

            try:
                delete_future = deleter_executor.submit(self.client.delete_bucket, bucket, force=True)
                delete_future.result(timeout=self.config.bucket_delete_timeout)
                return "deleted", b_name, None
            except Exception as del_err:
                if isinstance(del_err, (TimeoutError, TimeoutError.__class__)) or type(del_err).__name__ == "TimeoutError":
                    err_str = (
                        f"Deletion timed out after {self.config.bucket_delete_timeout}s on gs://{b_name} "
                        "(large bucket with many objects); applying OLM fallback."
                    )
                    logger.warning(err_str)
                else:
                    err_str = str(del_err)
                    logger.warning("Deletion failed for gs://%s: %s", b_name, err_str)

                if self.config.apply_olm_fallback:
                    try:
                        self.client.apply_lifecycle_rule(bucket, age_days=1)
                        return "olm", b_name, f"{err_str} (Fallback 1-day OLM rule applied)"
                    except Exception as olm_err:
                        err_str += f" (OLM fallback failed: {olm_err})"
                return "failed", b_name, err_str

        try:
            with ThreadPoolExecutor(max_workers=self.config.concurrency) as executor:
                future_to_bucket = {
                    executor.submit(_delete_worker, b): b for b in eligible_buckets
                }

                for future in as_completed(future_to_bucket):
                    status, name, err = future.result()
                    with counter_lock:
                        completed_count += 1
                        if status == "deleted":
                            deleted_buckets.append(name)
                        elif status == "olm":
                            olm_buckets.append(name)
                        else:
                            failed_buckets.append({"bucket": name, "error": err or "unknown error"})

                        if (
                            completed_count % self.config.batch_size == 0
                            or completed_count == len(eligible_buckets)
                        ):
                            logger.info(
                                "[%s Progress] Processed %d/%d targeted buckets (deleted=%d, olm=%d, failed=%d)...",
                                project_id,
                                completed_count,
                                len(eligible_buckets),
                                len(deleted_buckets),
                                len(olm_buckets),
                                len(failed_buckets),
                            )
        finally:
            deleter_executor.shutdown(wait=False, cancel_futures=True)

        return {
            "scanned": total_scanned,
            "eligible": total_eligible,
            "found": total_eligible,
            "deleted_count": len(deleted_buckets),
            "olm_count": len(olm_buckets),
            "failed_count": len(failed_buckets),
            "skipped_count": skipped_count,
            "deleted_buckets": deleted_buckets,
            "olm_buckets": olm_buckets,
            "failed_buckets": failed_buckets,
        }
