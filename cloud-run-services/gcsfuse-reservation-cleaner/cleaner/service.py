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

"""Service coordinator for GCE Compute Reservation Cleaner."""

import concurrent.futures
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from cleaner.config import CleanerConfig
from cleaner.reservation_client import ReservationClient
from cleaner.reservation_processor import ReservationProcessor

logger = logging.getLogger(__name__)


class ReservationCleanerService:
    """Coordinates reservation discovery, evaluation, pricing analysis, and deletion."""

    def __init__(
        self,
        config: CleanerConfig,
        client: Optional[ReservationClient] = None,
    ):
        self.config = config
        self.client = client or ReservationClient()
        self.processor = ReservationProcessor(self.config, self.client)

    def run(self, reference_time: Optional[datetime] = None) -> Dict[str, Any]:
        """Execute full reservation cleanup sweep."""
        now = reference_time or datetime.now(timezone.utc)
        logger.info(
            "Starting Reservation Cleaner for project '%s' (dry_run=%s, idle_days=%.1f, delete_never_used=%s)",
            self.config.project_id,
            self.config.dry_run,
            self.config.delete_idle_days,
            self.config.delete_never_used,
        )

        actions_taken: List[str] = []
        errors: List[str] = []

        # 1. Discover all reservations across zones
        try:
            raw_reservations = self.client.list_aggregated_reservations(self.config.project_id)
        except Exception as e:
            error_msg = f"Failed to list aggregated reservations for project '{self.config.project_id}': {e}"
            logger.error(error_msg)
            return {
                "status": "error",
                "service": "gcsfuse-reservation-cleaner",
                "project_id": self.config.project_id,
                "dry_run": self.config.dry_run,
                "summary": {"total_reservations": 0, "errors": 1},
                "actions_taken": [],
                "reservations": [],
                "errors": [error_msg],
            }

        # Apply optional zone / reservation name filtering
        filtered_reservations: List[Dict[str, Any]] = []
        for res in raw_reservations:
            zone = res.get("zone", "")
            name = res.get("name", "")

            if self.config.zones and zone not in self.config.zones:
                continue
            if self.config.reservation_names and name not in self.config.reservation_names:
                continue
            filtered_reservations.append(res)

        # 2. Concurrently evaluate each reservation
        evaluated_reservations: List[Dict[str, Any]] = []
        if filtered_reservations:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
                future_to_res = {
                    executor.submit(self.processor.evaluate_reservation, res, now): res
                    for res in filtered_reservations
                }
                for future in concurrent.futures.as_completed(future_to_res):
                    try:
                        res_result = future.result()
                        evaluated_reservations.append(res_result)
                    except Exception as e:
                        orig_res = future_to_res[future]
                        res_name = orig_res.get("name", "unknown")
                        err_str = f"Evaluation error on reservation '{res_name}': {e}"
                        logger.error(err_str)
                        errors.append(err_str)
                        evaluated_reservations.append(
                            {
                                "id": str(orig_res.get("id", "")),
                                "name": res_name,
                                "zone": orig_res.get("zone", "unknown"),
                                "status": "Evaluation Error",
                                "is_candidate": False,
                                "action": "error",
                                "reason": str(e),
                            }
                        )

        # 3. Concurrently process candidates (deletion or dry-run)
        final_reservations: List[Dict[str, Any]] = []
        if evaluated_reservations:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
                future_to_eval = {
                    executor.submit(self.processor.process_reservation, item): item
                    for item in evaluated_reservations
                }
                for future in concurrent.futures.as_completed(future_to_eval):
                    try:
                        processed_item = future.result()
                        final_reservations.append(processed_item)
                        msg = processed_item.get("message")
                        if msg:
                            actions_taken.append(msg)
                        if processed_item.get("action") == "deletion_failed":
                            err_msg = processed_item.get("error") or "Unknown deletion failure"
                            errors.append(f"Reservation '{processed_item.get('name')}': {err_msg}")
                    except Exception as e:
                        item = future_to_eval[future]
                        err_str = f"Processing error on reservation '{item.get('name')}': {e}"
                        logger.error(err_str)
                        errors.append(err_str)
                        final_reservations.append(item)

        # Sort reservations for stable reporting
        final_reservations.sort(key=lambda r: (r.get("zone", ""), r.get("name", "")))

        # 4. Compute financial summaries
        total_monthly_cost = round(sum(r.get("monthly_cost_usd", 0.0) for r in final_reservations), 2)
        active_now_count = sum(1 for r in final_reservations if r.get("status") == "Active Now")
        never_used_count = sum(1 for r in final_reservations if r.get("status") == "Never Used")
        idle_count = sum(1 for r in final_reservations if r.get("status") == "Idle")
        recently_used_count = sum(1 for r in final_reservations if r.get("status") == "Recently Used")
        error_count = len(errors) + sum(
            1 for r in final_reservations if r.get("status") in ("Query Error", "Evaluation Error", "Timestamp Error")
        )

        candidates = [r for r in final_reservations if r.get("is_candidate")]
        candidate_monthly_savings = round(sum(r.get("monthly_cost_usd", 0.0) for r in candidates), 2)
        candidate_annual_savings = round(candidate_monthly_savings * 12.0, 2)

        deleted_items = [r for r in final_reservations if r.get("action") == "deleted"]
        deleted_count = len(deleted_items)
        realized_monthly_savings = round(sum(r.get("monthly_cost_usd", 0.0) for r in deleted_items), 2)
        realized_annual_savings = round(realized_monthly_savings * 12.0, 2)

        dry_run_items = [r for r in final_reservations if r.get("action") == "dry_run_candidate"]
        dry_run_count = len(dry_run_items)

        summary = {
            "total_reservations": len(final_reservations),
            "active_now": active_now_count,
            "never_used": never_used_count,
            "idle": idle_count,
            "recently_used": recently_used_count,
            "candidates_for_deletion": len(candidates),
            "deleted": deleted_count,
            "dry_run_candidates": dry_run_count,
            "errors": error_count,
            "total_monthly_cost_usd": total_monthly_cost,
            "candidate_monthly_savings_usd": candidate_monthly_savings,
            "candidate_annual_savings_usd": candidate_annual_savings,
            "realized_monthly_savings_usd": realized_monthly_savings,
            "realized_annual_savings_usd": realized_annual_savings,
        }

        status = "error" if (errors and not final_reservations) else "success"

        return {
            "status": status,
            "service": "gcsfuse-reservation-cleaner",
            "project_id": self.config.project_id,
            "dry_run": self.config.dry_run,
            "summary": summary,
            "actions_taken": actions_taken,
            "reservations": final_reservations,
            "errors": errors,
        }
