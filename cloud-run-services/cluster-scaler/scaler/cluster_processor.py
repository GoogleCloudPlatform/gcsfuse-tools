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

"""Cluster evaluation, idle state tracking, and node pool scaling engine."""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from scaler.config import ScalerConfig
from scaler.gke_client import GKEClient

logger = logging.getLogger(__name__)


def parse_idle_since(val: Any) -> Optional[datetime.date]:
    """Parses various date and timestamp formats into a datetime.date object."""
    if not val:
        return None
    val_str = str(val).strip()

    # 1. Standard ISO format: YYYY-MM-DD
    try:
        return datetime.datetime.strptime(val_str, "%Y-%m-%d").date()
    except ValueError:
        pass

    # 2. Underscore delimited: YYYY_MM_DD
    try:
        return datetime.datetime.strptime(val_str, "%Y_%m_%d").date()
    except ValueError:
        pass

    # 3. Epoch float/int string
    try:
        epoch_num = float(val_str)
        return datetime.datetime.fromtimestamp(epoch_num, tz=datetime.timezone.utc).date()
    except (ValueError, OSError, OverflowError):
        pass

    # 4. Fallback to python-dateutil parser if available
    try:
        from dateutil import parser
        parsed_dt = parser.parse(val_str)
        return parsed_dt.date()
    except Exception:
        pass

    logger.warning("Unrecognized idle_since date format: '%s'", val_str)
    return None


class ClusterProcessor:
    """Evaluates GKE clusters, inspects workloads, and executes scaling actions."""

    def __init__(
        self,
        config: ScalerConfig,
        gke_client: Optional[GKEClient] = None,
    ) -> None:
        self.config = config
        self.gke_client = gke_client or GKEClient()

    def process_cluster(self, cluster: Any) -> Tuple[str, Dict[str, Any]]:
        """Processes a single GKE cluster through the idle lifecycle state machine.

        Returns:
            Tuple of (category_name, details_dict)
        """
        cluster_name = getattr(cluster, "name", "unknown")
        cluster_status = getattr(cluster, "status", None)

        # Status check: Cluster must be in RUNNING state (Status enum value 2 or name "RUNNING")
        status_name = str(cluster_status)
        if hasattr(cluster_status, "name"):
            status_name = cluster_status.name

        if status_name not in ("RUNNING", "Status.RUNNING", "2"):
            logger.info("Skipping cluster '%s' with non-running status: %s", cluster_name, status_name)
            return "skipped_clusters", {
                "cluster": cluster_name,
                "reason": f"Cluster not running (status: {status_name})",
            }

        # Check cluster names whitelist if provided
        if self.config.cluster_names:
            base_name = cluster_name.split("/")[-1]
            if cluster_name not in self.config.cluster_names and base_name not in self.config.cluster_names:
                logger.info("Skipping cluster '%s' (not in target list)", cluster_name)
                return "skipped_clusters", {
                    "cluster": cluster_name,
                    "reason": "Cluster not in target cluster_names whitelist",
                }

        # Check protection labels / tags
        labels = dict(getattr(cluster, "resource_labels", {}) or {})
        existing_idle_since = labels.get("idle_since")

        norm_excl_keys = {k.lower() for k in self.config.exclude_label_keys}
        norm_tags = {t.lower() for t in self.config.whitelist_tags}
        for label_k, label_v in labels.items():
            k_lower = str(label_k).lower()
            v_lower = str(label_v).lower()
            if k_lower in norm_excl_keys or k_lower in norm_tags:
                logger.info("Skipping cluster '%s' (protected by label key '%s')", cluster_name, label_k)
                if existing_idle_since:
                    try:
                        updated_labels = {k: v for k, v in labels.items() if k != "idle_since"}
                        self.gke_client.set_cluster_labels(cluster=cluster, labels=updated_labels, dry_run=self.config.dry_run)
                    except Exception as e:
                        logger.warning("Could not remove idle_since label from protected cluster '%s': %s", cluster_name, e)
                return "skipped_clusters", {
                    "cluster": cluster_name,
                    "reason": f"Protected by label key '{label_k}'",
                }
            if k_lower in ("auto-scale", "auto_scale", "autoscale", "auto-stop", "auto_stop") and v_lower in ("false", "0", "no", "off"):
                logger.info("Skipping cluster '%s' (protected by label '%s=%s')", cluster_name, label_k, label_v)
                return "skipped_clusters", {
                    "cluster": cluster_name,
                    "reason": f"Protected by label '{label_k}={label_v}'",
                }

        for excl_k, excl_v in self.config.exclude_label_values.items():
            if labels.get(excl_k) == excl_v:
                logger.info("Skipping cluster '%s' (protected by label '%s=%s')", cluster_name, excl_k, excl_v)
                return "skipped_clusters", {
                    "cluster": cluster_name,
                    "reason": f"Protected by label '{excl_k}={excl_v}'",
                }

        now = datetime.datetime.now(datetime.timezone.utc)
        lookback_hrs = getattr(self.config, "activity_lookback_hours", 24.0)
        cutoff_time = now - datetime.timedelta(hours=lookback_hrs)

        try:
            is_active_or_visited = False
            active_pods: List[Dict[str, Any]] = []
            activity_reason = ""

            if hasattr(self.gke_client, "check_cluster_activity"):
                check_result = self.gke_client.check_cluster_activity(
                    cluster=cluster,
                    project_id=self.config.project_id,
                    location=self.config.location,
                    is_system_namespace_fn=self.config.is_system_namespace,
                    cutoff_time=cutoff_time,
                    check_audit_logs=getattr(self.config, "check_audit_logs", True),
                )
                if isinstance(check_result, tuple) and len(check_result) == 3:
                    is_active_or_visited, active_pods, activity_reason = check_result
                else:
                    has_active, active_pods = self.gke_client.get_cluster_active_pods(
                        cluster=cluster,
                        is_system_namespace_fn=self.config.is_system_namespace,
                    )
                    is_active_or_visited = has_active
                    activity_reason = f"{len(active_pods)} active user pod(s)" if has_active else "No active workloads"
            else:
                has_active, active_pods = self.gke_client.get_cluster_active_pods(
                    cluster=cluster,
                    is_system_namespace_fn=self.config.is_system_namespace,
                )
                is_active_or_visited = has_active
                activity_reason = f"{len(active_pods)} active user pod(s)" if has_active else "No active workloads"
        except Exception as e:
            logger.error("Failed to inspect pods/activity for cluster '%s': %s", cluster_name, e, exc_info=True)
            return "errors", {
                "cluster": cluster_name,
                "error": str(e),
                "phase": "pod_inspection",
            }

        labels = dict(getattr(cluster, "resource_labels", {}) or {})
        existing_idle_since = labels.get("idle_since")
        today = now.date()
        today_str = today.strftime("%Y-%m-%d")

        # ------------------------------------------------------------------
        # Branch 1: Cluster has active workloads OR was visited/tinkered with -> Active
        # ------------------------------------------------------------------
        if is_active_or_visited:
            if existing_idle_since:
                logger.info(
                    "Cluster '%s' was visited/tinkered with recently (%s). Removing stale idle_since label '%s'",
                    cluster_name,
                    activity_reason,
                    existing_idle_since,
                )
                updated_labels = {k: v for k, v in labels.items() if k != "idle_since"}
                try:
                    self.gke_client.set_cluster_labels(
                        cluster=cluster,
                        labels=updated_labels,
                        dry_run=self.config.dry_run,
                    )
                except Exception as e:
                    logger.error("Failed to remove idle_since label from cluster '%s': %s", cluster_name, e)
                    return "errors", {
                        "cluster": cluster_name,
                        "error": str(e),
                        "phase": "label_removal",
                    }
                return "active_clusters", {
                    "cluster": cluster_name,
                    "active_pods_count": len(active_pods),
                    "idle_label_cleared": True,
                    "previous_idle_since": existing_idle_since,
                    "reason": activity_reason,
                    "dry_run": self.config.dry_run,
                }
            else:
                logger.info("Cluster '%s' is active / visited recently (%s)", cluster_name, activity_reason)
                return "active_clusters", {
                    "cluster": cluster_name,
                    "active_pods_count": len(active_pods),
                    "idle_label_cleared": False,
                    "reason": activity_reason,
                }

        # ------------------------------------------------------------------
        # Branch 2: Cluster has NO active workloads & NO recent visits -> Idle Lifecycle
        # ------------------------------------------------------------------
        if not existing_idle_since:
            logger.info("Cluster '%s' discovered idle. Stamping idle_since='%s'", cluster_name, today_str)
            updated_labels = dict(labels)
            updated_labels["idle_since"] = today_str
            try:
                self.gke_client.set_cluster_labels(
                    cluster=cluster,
                    labels=updated_labels,
                    dry_run=self.config.dry_run,
                )
            except Exception as e:
                logger.error("Failed to stamp idle_since label on cluster '%s': %s", cluster_name, e)
                return "errors", {
                    "cluster": cluster_name,
                    "error": str(e),
                    "phase": "label_stamping",
                }
            return "idle_marked_clusters", {
                "cluster": cluster_name,
                "idle_since": today_str,
                "dry_run": self.config.dry_run,
            }

        # Existing idle_since label present: calculate idle days
        idle_date = parse_idle_since(existing_idle_since)
        if idle_date is None:
            logger.warning(
                "Cluster '%s' had unparseable idle_since='%s'. Resetting to today: %s",
                cluster_name,
                existing_idle_since,
                today_str,
            )
            updated_labels = dict(labels)
            updated_labels["idle_since"] = today_str
            try:
                self.gke_client.set_cluster_labels(
                    cluster=cluster,
                    labels=updated_labels,
                    dry_run=self.config.dry_run,
                )
            except Exception as e:
                logger.error("Failed to reset invalid idle_since label on cluster '%s': %s", cluster_name, e)
                return "errors", {
                    "cluster": cluster_name,
                    "error": str(e),
                    "phase": "label_reset",
                }
            return "idle_marked_clusters", {
                "cluster": cluster_name,
                "idle_since": today_str,
                "reason": "invalid_date_reset",
                "dry_run": self.config.dry_run,
            }

        idle_days = (today - idle_date).days
        if idle_days < 0:
            # Future timestamp safeguard
            idle_days = 0

        # Sub-branch 2A: Idle duration below threshold -> Pending
        if idle_days < self.config.idle_days_threshold:
            days_remaining = self.config.idle_days_threshold - idle_days
            logger.info(
                "Cluster '%s' is idle for %d day(s) (threshold: %d, %d day(s) remaining)",
                cluster_name,
                idle_days,
                self.config.idle_days_threshold,
                days_remaining,
            )
            return "idle_pending_threshold", {
                "cluster": cluster_name,
                "idle_since": existing_idle_since,
                "idle_days": idle_days,
                "threshold": self.config.idle_days_threshold,
                "days_remaining": days_remaining,
            }

        # Sub-branch 2B: Idle duration meets or exceeds threshold -> Scale down!
        logger.info(
            "Cluster '%s' has been idle for %d day(s) (threshold: %d). Initiating scale-down...",
            cluster_name,
            idle_days,
            self.config.idle_days_threshold,
        )

        autopilot = getattr(cluster, "autopilot", None)
        is_autopilot = bool(autopilot and getattr(autopilot, "enabled", False))

        if is_autopilot:
            logger.info("Cluster '%s' is GKE Autopilot. Node management handled automatically by GKE.", cluster_name)
            return "scaled_down_clusters", {
                "cluster": cluster_name,
                "idle_since": existing_idle_since,
                "idle_days": idle_days,
                "threshold": self.config.idle_days_threshold,
                "cluster_type": "autopilot",
                "actions": ["autopilot_nodes_managed_by_gke"],
                "dry_run": self.config.dry_run,
            }

        # Standard GKE: scale down node pools
        node_pools = getattr(cluster, "node_pools", []) or []
        pool_results: List[Dict[str, Any]] = []

        for pool in node_pools:
            pool_name = getattr(pool, "name", "unknown")
            try:
                pool_res = self.gke_client.scale_node_pool_to_zero(
                    cluster=cluster,
                    node_pool=pool,
                    dry_run=self.config.dry_run,
                )
                pool_results.append(pool_res)
            except Exception as e:
                logger.error(
                    "Failed to scale node pool '%s' on cluster '%s': %s",
                    pool_name,
                    cluster_name,
                    e,
                    exc_info=True,
                )
                pool_results.append({
                    "node_pool": pool_name,
                    "error": str(e),
                    "dry_run": self.config.dry_run,
                })

        return "scaled_down_clusters", {
            "cluster": cluster_name,
            "idle_since": existing_idle_since,
            "idle_days": idle_days,
            "threshold": self.config.idle_days_threshold,
            "cluster_type": "standard",
            "node_pools_scaled": pool_results,
            "dry_run": self.config.dry_run,
        }
