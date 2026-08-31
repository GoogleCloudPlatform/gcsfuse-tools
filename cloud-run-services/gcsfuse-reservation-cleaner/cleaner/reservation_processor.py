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

"""Evaluation and processing logic for GCE Compute Reservations."""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import dateutil.parser

from cleaner.config import CleanerConfig
from cleaner.pricing import (
    calculate_annual_cost,
    calculate_monthly_cost,
    estimate_hourly_rate,
)
from cleaner.reservation_client import ReservationClient

logger = logging.getLogger(__name__)


class ReservationProcessor:
    """Evaluates reservation metrics, computes costs, and processes safe cleanups."""

    def __init__(self, config: CleanerConfig, client: ReservationClient):
        self.config = config
        self.client = client

    def _parse_timestamp(self, ts_str: Optional[str]) -> Optional[datetime]:
        """Safely parse ISO / RFC 3339 timestamp into timezone-aware UTC datetime."""
        if not ts_str:
            return None
        try:
            dt = dateutil.parser.parse(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception as e:
            logger.warning("Failed to parse timestamp '%s': %s", ts_str, e)
            return None

    def evaluate_reservation(
        self,
        reservation: Dict[str, Any],
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Evaluate a single reservation against age, usage history, and safety policies."""
        reference_time = now or datetime.now(timezone.utc)
        res_id = str(reservation.get("id", ""))
        name = reservation.get("name", "unknown")
        zone = reservation.get("zone", "unknown")

        specific_res = reservation.get("specificReservation", {})
        instance_props = specific_res.get("instanceProperties", {})
        machine_type = instance_props.get("machineType", "")
        accelerators = instance_props.get("guestAccelerators", [])
        capacity = int(specific_res.get("count", 1))
        in_use_now = int(specific_res.get("inUseCount", 0))

        # Financial pricing calculation
        hourly_rate = estimate_hourly_rate(machine_type, zone, accelerators)
        hourly_cost = round(hourly_rate * capacity, 4)
        monthly_cost = calculate_monthly_cost(hourly_rate, capacity)
        annual_cost = calculate_annual_cost(monthly_cost)

        # Creation timestamp and age
        creation_ts_str = reservation.get("creationTimestamp")
        creation_dt = self._parse_timestamp(creation_ts_str)
        age_days = (
            round((reference_time - creation_dt).total_seconds() / 86400.0, 2)
            if creation_dt
            else None
        )

        res_record: Dict[str, Any] = {
            "id": res_id,
            "name": name,
            "zone": zone,
            "machine_type": machine_type,
            "capacity": capacity,
            "in_use_now": in_use_now,
            "creation_timestamp": creation_ts_str,
            "age_days": age_days,
            "hourly_cost_usd": hourly_cost,
            "monthly_cost_usd": monthly_cost,
            "annual_cost_usd": annual_cost,
            "status": "Unknown",
            "is_candidate": False,
            "action": "none",
            "reason": "",
            "usage_metrics": {},
        }

        # -------------------------------------------------------------
        # STRICT SAFETY RULE: If in_use_now > 0, reservation is active.
        # It is NEVER eligible for deletion under any circumstances.
        # -------------------------------------------------------------
        if in_use_now > 0:
            res_record["status"] = "Active Now"
            res_record["is_candidate"] = False
            res_record["action"] = "retained_active"
            res_record["reason"] = f"Reservation currently has {in_use_now} instance(s) in use."
            return res_record

        # Check protection labels / tags
        labels = dict(reservation.get("resourceLabels") or reservation.get("labels") or {})
        norm_excl_keys = {k.lower() for k in self.config.exclude_label_keys}
        norm_tags = {t.lower() for t in self.config.whitelist_tags}

        for label_k, label_v in labels.items():
            k_lower = str(label_k).lower()
            v_lower = str(label_v).lower()
            if k_lower in norm_excl_keys or k_lower in norm_tags:
                res_record["status"] = "Protected"
                res_record["is_candidate"] = False
                res_record["action"] = "retained_protected"
                res_record["reason"] = f"Protected by label/tag key '{label_k}'."
                return res_record
            if k_lower in ("auto-delete", "auto_delete", "autodelete", "auto-clean", "auto_clean") and v_lower in ("false", "0", "no", "off"):
                res_record["status"] = "Protected"
                res_record["is_candidate"] = False
                res_record["action"] = "retained_protected"
                res_record["reason"] = f"Protected by label '{label_k}={label_v}'."
                return res_record

        for excl_k, excl_v in self.config.exclude_label_values.items():
            if labels.get(excl_k) == excl_v:
                res_record["status"] = "Protected"
                res_record["is_candidate"] = False
                res_record["action"] = "retained_protected"
                res_record["reason"] = f"Protected by label '{excl_k}={excl_v}'."
                return res_record

        # Query Cloud Monitoring usage metrics
        try:
            usage_info = self.client.query_reservation_usage(
                project_id=self.config.project_id,
                reservation_id=res_id,
                lookback_days=self.config.lookback_days,
                reference_time=reference_time,
            )
            res_record["usage_metrics"] = usage_info
        except Exception as e:
            logger.error("Error querying monitoring for reservation '%s' (%s): %s", name, res_id, e)
            res_record["status"] = "Query Error"
            res_record["is_candidate"] = False
            res_record["action"] = "retained_error"
            res_record["reason"] = f"Cloud Monitoring query failed: {e}"
            return res_record

        if usage_info.get("error"):
            res_record["status"] = "Query Error"
            res_record["is_candidate"] = False
            res_record["action"] = "retained_error"
            res_record["reason"] = f"Cloud Monitoring query error: {usage_info.get('error')}"
            return res_record

        is_never_used = usage_info.get("is_never_used", False)
        last_used_str = usage_info.get("last_used_timestamp")

        if is_never_used:
            res_record["status"] = "Never Used"
            # Check deletion policy for never-used reservations
            if self.config.delete_never_used:
                res_record["is_candidate"] = True
                res_record["reason"] = "Never used in monitored historical window (0 active hours)."
            elif (
                self.config.max_age_days is not None
                and age_days is not None
                and age_days >= self.config.max_age_days
            ):
                res_record["is_candidate"] = True
                res_record["reason"] = (
                    f"Never used and exceeds max age threshold ({age_days}d >= {self.config.max_age_days}d)."
                )
            else:
                res_record["is_candidate"] = False
                res_record["action"] = "retained_never_used"
                res_record["reason"] = "Never used, but delete_never_used policy is disabled."

        elif last_used_str:
            last_used_dt = self._parse_timestamp(last_used_str)
            if last_used_dt:
                idle_days = round((reference_time - last_used_dt).total_seconds() / 86400.0, 2)
                res_record["days_since_last_used"] = idle_days

                if idle_days >= self.config.delete_idle_days:
                    res_record["status"] = "Idle"
                    res_record["is_candidate"] = True
                    res_record["reason"] = (
                        f"Idle for {idle_days} days (threshold: {self.config.delete_idle_days} days)."
                    )
                else:
                    res_record["status"] = "Recently Used"
                    res_record["is_candidate"] = False
                    res_record["action"] = "retained_recent"
                    res_record["reason"] = (
                        f"Recently used {idle_days} days ago (under threshold {self.config.delete_idle_days} days)."
                    )
            else:
                res_record["status"] = "Timestamp Error"
                res_record["is_candidate"] = False
                res_record["action"] = "retained_error"
                res_record["reason"] = f"Invalid last_used timestamp: {last_used_str}"
        else:
            # Fallback if metric returned no error and not marked never used
            res_record["status"] = "Never Used"
            if self.config.delete_never_used:
                res_record["is_candidate"] = True
                res_record["reason"] = "No active usage recorded."
            else:
                res_record["is_candidate"] = False
                res_record["action"] = "retained_never_used"
                res_record["reason"] = "No active usage recorded, delete_never_used is disabled."

        return res_record

    def process_reservation(
        self,
        evaluated_res: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute deletion or dry-run simulation for an evaluated candidate."""
        if not evaluated_res.get("is_candidate"):
            return evaluated_res

        name = evaluated_res["name"]
        zone = evaluated_res["zone"]

        if self.config.dry_run:
            evaluated_res["action"] = "dry_run_candidate"
            evaluated_res["message"] = (
                f"[DRY-RUN] Simulated deletion for stale reservation '{name}' in '{zone}'. "
                f"Estimated savings: ${evaluated_res['monthly_cost_usd']}/month."
            )
            logger.info(evaluated_res["message"])
            return evaluated_res

        # Live execution: invoke deletion API
        try:
            self.client.delete_reservation(
                project_id=self.config.project_id,
                zone=zone,
                reservation_name=name,
            )
            evaluated_res["action"] = "deleted"
            evaluated_res["message"] = (
                f"Successfully deleted stale reservation '{name}' in '{zone}'. "
                f"Realized savings: ${evaluated_res['monthly_cost_usd']}/month."
            )
            logger.info(evaluated_res["message"])
        except Exception as e:
            error_msg = f"Failed to delete reservation '{name}' in '{zone}': {e}"
            logger.error(error_msg)
            evaluated_res["action"] = "deletion_failed"
            evaluated_res["error"] = str(e)
            evaluated_res["message"] = error_msg

        return evaluated_res
