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

"""Configuration resolution and validation for the GKE Cluster Scaler."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, List, Optional, Set


DEFAULT_IDLE_DAYS_THRESHOLD = 7
DEFAULT_MAX_WORKERS = 10
DEFAULT_LOCATION = "-"

DEFAULT_IGNORED_NAMESPACES: Set[str] = {
    "kube-system",
    "gke-managed-system",
    "gke-managed-cim",
    "gke-gmp-system",
}

# Known system prefixes and add-on namespaces
SYSTEM_NAMESPACE_PREFIXES = (
    "kube-",
    "gke-",
)

KNOWN_ADDON_NAMESPACES = {
    "gcs-fuse-csi-driver",
    "gmp-system",
    "jobset-system",
    "kueue-system",
    "istio-system",
    "gatekeeper-system",
    "config-management-system",
    "asm-system",
}


DEFAULT_EXCLUDE_LABEL_KEYS: Set[str] = {
    "keep-alive",
    "keep_alive",
    "do-not-scale",
    "do_not_scale",
    "do-not-stop",
    "do_not_stop",
    "do-not-delete",
    "do_not_delete",
    "dont-scale",
    "dont-stop",
    "dont-delete",
    "no-auto-scale",
    "no_auto_scale",
    "no-auto-stop",
    "no_auto_stop",
    "permanent",
    "whitelisted",
    "protected",
    "skip-lifecycle",
    "skip_lifecycle",
}


def _parse_bool(val: Any, default: bool = False) -> bool:
    """Safely parse boolean values from strings, ints, or bools."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        normalized = val.strip().lower()
        if normalized in ("true", "1", "t", "yes", "y"):
            return True
        if normalized in ("false", "0", "f", "no", "n"):
            return False
    return default


def _parse_int(val: Any, default: int) -> int:
    """Safely parse integer values."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _parse_str_list(val: Any) -> Optional[List[str]]:
    """Parse list of strings from list, set, or comma-separated string."""
    if val is None:
        return None
    if isinstance(val, (list, tuple, set)):
        return [str(item).strip() for item in val if str(item).strip()]
    if isinstance(val, str):
        items = [s.strip() for s in val.split(",") if s.strip()]
        return items if items else None
    return None


@dataclass
class ScalerConfig:
    """Runtime configuration for GKE Cluster Scaler execution."""

    project_id: str = ""
    location: str = DEFAULT_LOCATION
    idle_days_threshold: int = DEFAULT_IDLE_DAYS_THRESHOLD
    ignored_namespaces: Set[str] = field(default_factory=lambda: set(DEFAULT_IGNORED_NAMESPACES))
    dry_run: bool = False
    max_workers: int = DEFAULT_MAX_WORKERS
    cluster_names: Optional[List[str]] = None
    exclude_label_keys: Set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_LABEL_KEYS))
    exclude_label_values: dict[str, str] = field(default_factory=dict)
    whitelist_tags: Set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_LABEL_KEYS))
    activity_lookback_hours: float = 24.0
    check_audit_logs: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.ignored_namespaces, set):
            self.ignored_namespaces = set(self.ignored_namespaces or [])
        if not isinstance(self.exclude_label_keys, set):
            self.exclude_label_keys = set(self.exclude_label_keys or [])
        if not isinstance(self.whitelist_tags, set):
            self.whitelist_tags = set(self.whitelist_tags or [])
        if not isinstance(self.exclude_label_values, dict):
            self.exclude_label_values = dict(self.exclude_label_values or {})
        if self.idle_days_threshold < 0:
            raise ValueError(f"idle_days_threshold must be non-negative, got {self.idle_days_threshold}")
        if self.activity_lookback_hours < 0:
            raise ValueError(f"activity_lookback_hours must be non-negative, got {self.activity_lookback_hours}")
        if self.max_workers < 1:
            raise ValueError(f"max_workers must be at least 1, got {self.max_workers}")

    def is_system_namespace(self, namespace: str) -> bool:
        """Determines if a namespace is considered system/managed."""
        if not namespace:
            return False
        ns = namespace.strip()
        if ns in self.ignored_namespaces:
            return True
        if ns in KNOWN_ADDON_NAMESPACES:
            return True
        for prefix in SYSTEM_NAMESPACE_PREFIXES:
            if ns.startswith(prefix):
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to a serializable dictionary."""
        return {
            "project_id": self.project_id,
            "location": self.location,
            "idle_days_threshold": self.idle_days_threshold,
            "ignored_namespaces": sorted(list(self.ignored_namespaces)),
            "dry_run": self.dry_run,
            "max_workers": self.max_workers,
            "cluster_names": self.cluster_names,
        }

    @classmethod
    def from_request(cls, request: Any = None) -> ScalerConfig:
        """Extracts and resolves configuration from HTTP request and environment.

        Resolution hierarchy:
        1. JSON request body
        2. Query parameters
        3. Environment variables
        4. Application Default Credentials (ADC) project fallback
        """
        payload: dict[str, Any] = {}
        args: dict[str, Any] = {}

        if request is not None:
            # Handle Flask request or custom request wrapper
            if hasattr(request, "get_json"):
                try:
                    payload = request.get_json(silent=True) or {}
                except Exception:
                    payload = {}
            elif isinstance(request, dict):
                payload = request

            if hasattr(request, "args") and request.args is not None:
                try:
                    args = dict(request.args)
                except Exception:
                    args = {}

        # 1. Project ID resolution
        project_id = (
            payload.get("project")
            or payload.get("project_id")
            or args.get("project")
            or args.get("project_id")
            or os.environ.get("PROJECT_ID")
            or os.environ.get("GCP_PROJECT")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or ""
        )

        if not project_id:
            try:
                import google.auth
                _, default_project = google.auth.default()
                if default_project:
                    project_id = default_project
            except Exception:
                pass

        # 2. Location resolution
        location = (
            payload.get("location")
            or payload.get("region")
            or args.get("location")
            or args.get("region")
            or os.environ.get("LOCATION")
            or os.environ.get("REGION")
            or DEFAULT_LOCATION
        )

        # 3. Idle days threshold resolution
        idle_days_val = (
            payload.get("idle_days_threshold")
            if "idle_days_threshold" in payload
            else payload.get("days_threshold",
            args.get("idle_days_threshold",
            args.get("days_threshold",
            os.environ.get("IDLE_DAYS_THRESHOLD",
            os.environ.get("DAYS_THRESHOLD")))))
        )
        idle_days_threshold = _parse_int(idle_days_val, DEFAULT_IDLE_DAYS_THRESHOLD)

        # 4. Ignored namespaces resolution
        ignored_ns_input = (
            payload.get("ignored_namespaces")
            or args.get("ignored_namespaces")
            or os.environ.get("IGNORED_NAMESPACES")
        )
        ignored_namespaces = set(DEFAULT_IGNORED_NAMESPACES)
        if ignored_ns_input is not None:
            parsed_ns = _parse_str_list(ignored_ns_input)
            if parsed_ns:
                ignored_namespaces = set(parsed_ns)

        # 5. Dry run resolution
        dry_run_val = (
            payload.get("dry_run")
            if "dry_run" in payload
            else args.get("dry_run", os.environ.get("DRY_RUN"))
        )
        dry_run = _parse_bool(dry_run_val, default=False)

        # 6. Max workers resolution
        max_workers_val = (
            payload.get("max_workers")
            if "max_workers" in payload
            else args.get("max_workers", os.environ.get("MAX_WORKERS"))
        )
        max_workers = _parse_int(max_workers_val, DEFAULT_MAX_WORKERS)

        # 7. Cluster names filter resolution
        cluster_names_input = (
            payload.get("cluster_names")
            or payload.get("clusters")
            or args.get("cluster_names")
            or args.get("clusters")
            or os.environ.get("CLUSTER_NAMES")
        )
        cluster_names = _parse_str_list(cluster_names_input)

        # 8. Protection label keys and tags resolution
        excl_keys_input = (
            payload.get("exclude_label_keys")
            or payload.get("exclude_labels")
            or payload.get("whitelist_labels")
            or args.get("exclude_label_keys")
            or os.environ.get("EXCLUDE_LABEL_KEYS")
        )
        exclude_label_keys = set(DEFAULT_EXCLUDE_LABEL_KEYS)
        if excl_keys_input is not None:
            parsed_keys = _parse_str_list(excl_keys_input)
            if parsed_keys:
                exclude_label_keys = set(parsed_keys)

        whitelist_tags_input = (
            payload.get("whitelist_tags")
            or payload.get("tags")
            or args.get("whitelist_tags")
            or os.environ.get("WHITELIST_TAGS")
        )
        whitelist_tags = set(DEFAULT_EXCLUDE_LABEL_KEYS)
        if whitelist_tags_input is not None:
            parsed_tags = _parse_str_list(whitelist_tags_input)
            if parsed_tags:
                whitelist_tags = set(parsed_tags)

        exclude_label_values = payload.get("exclude_label_values") or {}
        if not isinstance(exclude_label_values, dict):
            exclude_label_values = {}

        # 9. Activity lookback and audit logs configuration
        lookback_val = (
            payload.get("activity_lookback_hours")
            or payload.get("lookback_hours")
            or args.get("activity_lookback_hours")
            or args.get("lookback_hours")
            or os.environ.get("ACTIVITY_LOOKBACK_HOURS")
        )
        try:
            activity_lookback_hours = float(lookback_val) if lookback_val is not None else 24.0
        except (ValueError, TypeError):
            activity_lookback_hours = 24.0

        check_audit_val = (
            payload.get("check_audit_logs")
            if "check_audit_logs" in payload
            else args.get("check_audit_logs", os.environ.get("CHECK_AUDIT_LOGS"))
        )
        check_audit_logs = _parse_bool(check_audit_val, default=True)

        return cls(
            project_id=str(project_id).strip(),
            location=str(location).strip(),
            idle_days_threshold=idle_days_threshold,
            ignored_namespaces=ignored_namespaces,
            dry_run=dry_run,
            max_workers=max_workers,
            cluster_names=cluster_names,
            exclude_label_keys=exclude_label_keys,
            exclude_label_values=exclude_label_values,
            whitelist_tags=whitelist_tags,
            activity_lookback_hours=activity_lookback_hours,
            check_audit_logs=check_audit_logs,
        )
