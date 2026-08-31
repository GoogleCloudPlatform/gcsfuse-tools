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

"""Multi-threaded fleet orchestration service for GKE Cluster Scaler."""

from __future__ import annotations

import concurrent.futures
import logging
from typing import Any, Dict, List, Optional

from scaler.cluster_processor import ClusterProcessor
from scaler.config import ScalerConfig
from scaler.gke_client import GKEClient

logger = logging.getLogger(__name__)


class ClusterScalerService:
    """Coordinates cluster discovery and parallel idle evaluation across a GCP project."""

    def __init__(
        self,
        config: Optional[ScalerConfig] = None,
        gke_client: Optional[GKEClient] = None,
    ) -> None:
        self.config = config
        self.gke_client = gke_client or GKEClient()

    def run(self, config: Optional[ScalerConfig] = None) -> Dict[str, Any]:
        """Executes full cluster discovery and evaluation sweep.

        Returns structured summary and detailed findings.
        """
        cfg = config or self.config
        if cfg is None:
            cfg = ScalerConfig.from_request()

        if not cfg.project_id:
            logger.error("No project ID specified or discoverable via ADC.")
            return {
                "status": "error",
                "service": "cluster-scaler",
                "message": "GCP Project ID is required. Pass in JSON payload, query args, or set PROJECT_ID env var.",
                "errors": ["Missing required project_id"],
            }

        logger.info(
            "Starting GKE cluster sweep for project '%s' (location='%s', threshold=%dd, dry_run=%s, max_workers=%d)",
            cfg.project_id,
            cfg.location,
            cfg.idle_days_threshold,
            cfg.dry_run,
            cfg.max_workers,
        )

        try:
            clusters = self.gke_client.list_clusters(
                project_id=cfg.project_id,
                location=cfg.location,
            )
        except Exception as e:
            logger.error("Fatal error discovering clusters in project '%s': %s", cfg.project_id, e, exc_info=True)
            return {
                "status": "error",
                "service": "cluster-scaler",
                "project_id": cfg.project_id,
                "message": f"Failed to list clusters: {e}",
                "errors": [str(e)],
            }

        processor = ClusterProcessor(config=cfg, gke_client=self.gke_client)

        active_clusters: List[Dict[str, Any]] = []
        idle_marked_clusters: List[Dict[str, Any]] = []
        idle_pending_threshold: List[Dict[str, Any]] = []
        scaled_down_clusters: List[Dict[str, Any]] = []
        skipped_clusters: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        actions_taken: List[Dict[str, Any]] = []

        if clusters:
            worker_count = min(cfg.max_workers, len(clusters))
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_to_cluster = {
                    executor.submit(processor.process_cluster, cluster): cluster
                    for cluster in clusters
                }

                for future in concurrent.futures.as_completed(future_to_cluster):
                    cluster = future_to_cluster[future]
                    cluster_name = getattr(cluster, "name", "unknown")
                    try:
                        category, details = future.result()
                        if category == "active_clusters":
                            active_clusters.append(details)
                            if details.get("idle_label_cleared"):
                                actions_taken.append({
                                    "action": "clear_idle_label",
                                    "cluster": cluster_name,
                                    "dry_run": cfg.dry_run,
                                })
                        elif category == "idle_marked_clusters":
                            idle_marked_clusters.append(details)
                            actions_taken.append({
                                "action": "stamp_idle_label",
                                "cluster": cluster_name,
                                "idle_since": details.get("idle_since"),
                                "dry_run": cfg.dry_run,
                            })
                        elif category == "idle_pending_threshold":
                            idle_pending_threshold.append(details)
                        elif category == "scaled_down_clusters":
                            scaled_down_clusters.append(details)
                            actions_taken.append({
                                "action": "scale_down_nodes",
                                "cluster": cluster_name,
                                "idle_days": details.get("idle_days"),
                                "node_pools_scaled": details.get("node_pools_scaled", []),
                                "dry_run": cfg.dry_run,
                            })
                        elif category == "skipped_clusters":
                            skipped_clusters.append(details)
                        elif category == "errors":
                            errors.append(details)
                    except Exception as e:
                        logger.error("Unhandled exception processing cluster '%s': %s", cluster_name, e, exc_info=True)
                        errors.append({
                            "cluster": cluster_name,
                            "error": str(e),
                            "phase": "executor_thread",
                        })

        summary = {
            "total_clusters_found": len(clusters),
            "active_clusters": len(active_clusters),
            "idle_marked": len(idle_marked_clusters),
            "idle_pending": len(idle_pending_threshold),
            "scaled_down": len(scaled_down_clusters),
            "skipped": len(skipped_clusters),
            "errors": len(errors),
        }

        logger.info(
            "Cluster sweep finished for '%s'. Summary: %s",
            cfg.project_id,
            summary,
        )

        return {
            "status": "success" if not (errors and len(errors) == len(clusters) and len(clusters) > 0) else "partial_error",
            "service": "cluster-scaler",
            "project_id": cfg.project_id,
            "location": cfg.location,
            "dry_run": cfg.dry_run,
            "summary": summary,
            "actions_taken": actions_taken,
            "results": {
                "active_clusters": active_clusters,
                "idle_marked_clusters": idle_marked_clusters,
                "idle_pending_threshold": idle_pending_threshold,
                "scaled_down_clusters": scaled_down_clusters,
                "skipped_clusters": skipped_clusters,
                "errors": errors,
            },
        }
