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

"""VM evaluation, GKE/MIG filtering, lifecycle analysis, and stop/delete execution."""

import concurrent.futures
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple
from dateutil import parser as dateutil_parser

from stopper.config import StopperConfig
from stopper.gce_client import GCEClient

logger = logging.getLogger(__name__)

GKE_METADATA_KEYS = {
    "cluster-name",
    "cluster-location",
    "cluster-uid",
    "gke-nodepool",
    "kube-env",
    "kube-labels",
    "kubeconfig",
    "instance-template",
}

GKE_LABEL_PREFIXES = (
    "goog-k8s-",
    "goog-gke-",
)

GKE_NAME_PREFIXES = (
    "gke-",
    "gk3-",
)

MIG_CREATED_BY_KEYWORDS = (
    "instancegroupmanagers",
    "regioninstancegroupmanagers",
    "instancegroups",
)


def _get_metadata_dict(instance: Any) -> Dict[str, str]:
    """Extract metadata items from a GCE instance object as a key-value dict."""
    metadata = getattr(instance, "metadata", None)
    if not metadata:
        return {}
    items = getattr(metadata, "items", None)
    if not items:
        return {}

    result = {}
    if isinstance(items, dict):
        return {str(k): str(v) for k, v in items.items()}

    for item in items:
        key = getattr(item, "key", None)
        val = getattr(item, "value", "")
        if key is not None:
            result[str(key)] = str(val) if val is not None else ""
    return result


def _get_labels_dict(instance: Any) -> Dict[str, str]:
    """Extract labels dict from a GCE instance object."""
    labels = getattr(instance, "labels", None)
    if not labels:
        return {}
    if isinstance(labels, dict):
        return {str(k): str(v) for k, v in labels.items()}
    # protobuf MapComposite or similar
    try:
        return {str(k): str(v) for k, v in dict(labels).items()}
    except Exception:
        return {}


def _get_tags_list(instance: Any) -> List[str]:
    """Extract network tags list from a GCE instance object."""
    tags = getattr(instance, "tags", None)
    if not tags:
        return []
    items = getattr(tags, "items", None)
    if items:
        return [str(t) for t in items]
    if isinstance(tags, list):
        return [str(t) for t in tags]
    return []


def is_part_of_gke_or_mig(instance: Any) -> bool:
    """Check if instance belongs to a GKE cluster or Managed Instance Group.

    Exclusion Rules:
    1. GKE Name Prefix: instance name starts with 'gke-' or 'gk3-'
    2. GKE Labels: label keys starting with 'goog-k8s-' or 'goog-gke-'
    3. GKE/MIG Metadata:
       - metadata key matches any known GKE cluster keys
       - metadata key 'created-by' contains 'instanceGroupManagers' or 'instanceGroups'
       - metadata key 'instance-template' exists
    4. Network Tags: tags containing 'gke-' or 'k8s-' or 'mig-'
    """
    name = str(getattr(instance, "name", "") or "").lower()
    if any(name.startswith(p) for p in GKE_NAME_PREFIXES):
        return True

    labels = _get_labels_dict(instance)
    for label_key in labels:
        label_lower = label_key.lower()
        if any(label_lower.startswith(p) for p in GKE_LABEL_PREFIXES):
            return True
        if "gke" in label_lower or "k8s" in label_lower:
            return True

    metadata = _get_metadata_dict(instance)
    for key, val in metadata.items():
        key_lower = key.lower()
        if key_lower in GKE_METADATA_KEYS:
            return True
        if key_lower == "created-by":
            val_lower = val.lower()
            if any(kw in val_lower for kw in MIG_CREATED_BY_KEYWORDS):
                return True

    tags = _get_tags_list(instance)
    for tag in tags:
        tag_lower = tag.lower()
        if any(kw in tag_lower for kw in ("gke-", "k8s-", "mig-")):
            return True

    return False


def is_whitelisted(instance: Any, config: StopperConfig) -> Tuple[bool, str]:
    """Evaluate whether an instance is exempt from stopping based on user whitelist rules.

    Checks:
    1. Instance name pattern matching
    2. Label keys and key=value pairs (case-insensitive)
    3. Network tags (case-insensitive)
    4. Custom instance metadata keys and key=value pairs
    5. Specific disable flags (e.g. auto-stop=false, auto-delete=false)

    Returns:
        (True, reason) if whitelisted, (False, "") otherwise.
    """
    name = str(getattr(instance, "name", "") or "")
    for pattern in config.whitelist_names:
        if pattern and (pattern == name or pattern in name):
            return True, f"Name matches whitelist pattern '{pattern}'"

    # Normalize configured rules
    norm_excl_keys = {k.lower() for k in config.exclude_label_keys}
    norm_tags = {t.lower() for t in config.whitelist_tags}

    # 2. Check Labels
    labels = _get_labels_dict(instance)
    for label_k, label_v in labels.items():
        k_lower = str(label_k).lower()
        v_lower = str(label_v).lower()
        if k_lower in norm_excl_keys:
            return True, f"Excluded by label key '{label_k}'"
        if k_lower in ("auto-stop", "auto_stop", "autostop") and v_lower in ("false", "0", "no", "off"):
            return True, f"Excluded by label '{label_k}={label_v}'"
        if k_lower in ("auto-delete", "auto_delete", "autodelete") and v_lower in ("false", "0", "no", "off"):
            return True, f"Excluded by label '{label_k}={label_v}'"

    for excl_k, excl_v in config.exclude_label_values.items():
        if labels.get(excl_k) == excl_v:
            return True, f"Excluded by label '{excl_k}={excl_v}'"

    # 3. Check Network Tags
    tags = _get_tags_list(instance)
    for tag in tags:
        t_lower = str(tag).lower()
        if t_lower in norm_tags:
            return True, f"Excluded by network tag '{tag}'"

    # 4. Check Metadata
    metadata = _get_metadata_dict(instance)
    for meta_k, meta_v in metadata.items():
        mk_lower = str(meta_k).lower()
        mv_lower = str(meta_v).lower()
        if mk_lower in norm_excl_keys or mk_lower in norm_tags:
            return True, f"Excluded by metadata key '{meta_k}'"
        if mk_lower in ("auto-stop", "auto_stop", "autostop") and mv_lower in ("false", "0", "no", "off"):
            return True, f"Excluded by metadata '{meta_k}={meta_v}'"
        if mk_lower in ("auto-delete", "auto_delete", "autodelete") and mv_lower in ("false", "0", "no", "off"):
            return True, f"Excluded by metadata '{meta_k}={meta_v}'"

    return False, ""


def parse_timestamp(ts_val: Any) -> Optional[datetime]:
    """Parse RFC3339 / ISO timestamp string into UTC datetime object."""
    if not ts_val:
        return None
    if isinstance(ts_val, datetime):
        if ts_val.tzinfo is None:
            return ts_val.replace(tzinfo=timezone.utc)
        return ts_val.astimezone(timezone.utc)
    try:
        dt = dateutil_parser.isoparse(str(ts_val))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as e:
        logger.warning("Failed to parse timestamp '%s': %s", ts_val, e)
        return None


class VMProcessor:
    """Evaluates and processes GCE instances for idle stopping and lifecycle cleanup."""

    def __init__(self, config: StopperConfig, gce_client: Optional[GCEClient] = None):
        self.config = config
        self.client = gce_client or GCEClient()

    def process_single_instance(
        self,
        zone: str,
        instance: Any,
        now_utc: datetime,
    ) -> Dict[str, Any]:
        """Evaluate a single instance and execute stop/delete actions if applicable.

        Returns:
            Dictionary describing the evaluation outcome and action taken.
        """
        name = str(getattr(instance, "name", "unknown"))
        inst_id = str(getattr(instance, "id", "") or "")
        status = str(getattr(instance, "status", "UNKNOWN")).upper()

        result: Dict[str, Any] = {
            "name": name,
            "id": inst_id,
            "zone": zone,
            "status": status,
            "action": "none",
            "category": "skipped",
            "reason": "",
            "error": None,
        }

        # 1. GKE / MIG Exemption Check
        if is_part_of_gke_or_mig(instance):
            result["category"] = "skipped_gke_mig"
            result["reason"] = "Instance is part of GKE cluster or Managed Instance Group"
            return result

        # 2. Whitelist / User Exemption Check
        whitelisted, whitelist_reason = is_whitelisted(instance, self.config)
        if whitelisted:
            result["category"] = "skipped_whitelisted"
            result["reason"] = whitelist_reason
            return result

        # 3. Running VM Evaluation
        if status == "RUNNING":
            creation_ts = parse_timestamp(getattr(instance, "creation_timestamp", None))
            idle_cutoff = now_utc - timedelta(days=self.config.idle_days_threshold)

            # Check if VM is too young
            if creation_ts and creation_ts > idle_cutoff:
                result["category"] = "skipped_recently_created"
                result["reason"] = (
                    f"VM created recently at {creation_ts.isoformat()} "
                    f"(< {self.config.idle_days_threshold} days old)"
                )
                return result

            # Check Cloud Logging for recent login/SSH/metadata activity
            has_activity = self.client.has_recent_activity(
                project_id=self.config.project_id,
                zone=zone,
                instance_name=name,
                instance_id=inst_id,
                since_timestamp=idle_cutoff,
            )

            if has_activity:
                result["category"] = "skipped_active"
                result["reason"] = (
                    f"Active login or instance activity detected in the last "
                    f"{self.config.idle_days_threshold} days"
                )
                return result

            # Confirmed Idle -> STOP VM
            if self.config.dry_run:
                result["action"] = "dry_run_stop"
                result["category"] = "dry_run_stops"
                result["reason"] = (
                    f"[DRY RUN] Would stop idle running VM '{name}' in zone '{zone}' "
                    f"(no login/activity in >= {self.config.idle_days_threshold} days)"
                )
            else:
                try:
                    self.client.stop_instance(self.config.project_id, zone, name)
                    result["action"] = "stopped"
                    result["category"] = "stopped"
                    result["reason"] = (
                        f"Stopped idle running VM '{name}' in zone '{zone}' "
                        f"(no login/activity in >= {self.config.idle_days_threshold} days)"
                    )
                except Exception as exc:
                    logger.error("Failed to stop instance %s in zone %s: %s", name, zone, exc)
                    result["action"] = "error"
                    result["category"] = "errors_count"
                    result["error"] = str(exc)
                    result["reason"] = f"Failed to stop instance: {exc}"
            return result

        # 4. Stopped / Terminated / Suspended VM Evaluation
        if status in ("TERMINATED", "STOPPED", "SUSPENDED"):
            if not self.config.delete_stopped_vms:
                result["category"] = "skipped_stopped"
                result["reason"] = "Instance is stopped; delete_stopped_vms is disabled"
                return result

            stop_ts = (
                parse_timestamp(getattr(instance, "last_stop_timestamp", None))
                or parse_timestamp(getattr(instance, "last_suspended_timestamp", None))
                or parse_timestamp(getattr(instance, "creation_timestamp", None))
            )
            stopped_cutoff = now_utc - timedelta(days=self.config.stopped_days_threshold)

            if stop_ts and stop_ts > stopped_cutoff:
                result["category"] = "skipped_stopped"
                result["reason"] = (
                    f"Instance stopped recently at {stop_ts.isoformat()} "
                    f"(< {self.config.stopped_days_threshold} days stopped)"
                )
                return result

            # Long-stopped VM -> DELETE
            if self.config.dry_run:
                result["action"] = "dry_run_delete"
                result["category"] = "dry_run_deletions"
                result["reason"] = (
                    f"[DRY RUN] Would delete stopped VM '{name}' in zone '{zone}' "
                    f"(stopped >= {self.config.stopped_days_threshold} days)"
                )
            else:
                try:
                    self.client.delete_instance(self.config.project_id, zone, name)
                    result["action"] = "deleted"
                    result["category"] = "deleted"
                    result["reason"] = (
                        f"Deleted long-stopped VM '{name}' in zone '{zone}' "
                        f"(stopped >= {self.config.stopped_days_threshold} days)"
                    )
                except Exception as exc:
                    logger.error("Failed to delete instance %s in zone %s: %s", name, zone, exc)
                    result["action"] = "error"
                    result["category"] = "errors_count"
                    result["error"] = str(exc)
                    result["reason"] = f"Failed to delete instance: {exc}"
            return result

        # 5. Transitional / Other statuses
        result["category"] = "skipped_other"
        result["reason"] = f"Instance in non-actionable status '{status}'"
        return result

    def sweep(self) -> Dict[str, Any]:
        """Execute a complete scan and processing sweep across all instances.

        Returns:
            Structured summary response dictionary.
        """
        now_utc = datetime.now(timezone.utc)
        logger.info(
            "Starting VM Stopper sweep for project '%s' (dry_run=%s, idle_threshold=%dd, "
            "delete_stopped=%s, stopped_threshold=%dd, max_workers=%d)...",
            self.config.project_id,
            self.config.dry_run,
            self.config.idle_days_threshold,
            self.config.delete_stopped_vms,
            self.config.stopped_days_threshold,
            self.config.max_workers,
        )

        all_instances = self.client.list_instances(self.config.project_id)

        summary = {
            "total_scanned": len(all_instances),
            "stopped": 0,
            "dry_run_stops": 0,
            "deleted": 0,
            "dry_run_deletions": 0,
            "skipped_gke_mig": 0,
            "skipped_whitelisted": 0,
            "skipped_active": 0,
            "skipped_recently_created": 0,
            "skipped_stopped": 0,
            "skipped_other": 0,
            "errors_count": 0,
        }
        actions_taken: List[str] = []
        errors: List[str] = []
        details: List[Dict[str, Any]] = []

        # Multi-threaded concurrent evaluation
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            future_to_inst = {
                executor.submit(self.process_single_instance, zone, inst, now_utc): (zone, inst)
                for zone, inst in all_instances
            }

            for future in concurrent.futures.as_completed(future_to_inst):
                zone, inst = future_to_inst[future]
                inst_name = getattr(inst, "name", "unknown")
                try:
                    res = future.result()
                    details.append(res)
                    category = res.get("category", "skipped_other")
                    if category in summary:
                        summary[category] += 1
                    else:
                        summary["skipped_other"] += 1

                    if res.get("action") in ("stopped", "deleted", "dry_run_stop", "dry_run_delete"):
                        actions_taken.append(res.get("reason", ""))
                    if res.get("error"):
                        errors.append(f"{inst_name} ({zone}): {res['error']}")
                except Exception as exc:
                    logger.error("Unexpected error processing instance %s in zone %s: %s", inst_name, zone, exc)
                    summary["errors_count"] += 1
                    errors.append(f"{inst_name} ({zone}): {exc}")

        overall_status = "success"
        if summary["errors_count"] > 0:
            overall_status = "partial_error" if (summary["stopped"] > 0 or summary["deleted"] > 0) else "error"

        response = {
            "status": overall_status,
            "service": "vm-stopper",
            "project_id": self.config.project_id,
            "dry_run": self.config.dry_run,
            "summary": summary,
            "actions_taken": actions_taken,
            "errors": errors,
            "details": details,
        }

        logger.info(
            "Completed VM Stopper sweep: scanned=%d, stopped=%d, deleted=%d, errors=%d",
            summary["total_scanned"],
            summary["stopped"] + summary["dry_run_stops"],
            summary["deleted"] + summary["dry_run_deletions"],
            summary["errors_count"],
        )
        return response
