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

"""Dynamic configuration resolution for VM Stopper."""

from dataclasses import dataclass, field
import json
import logging
import os
from typing import Any, Dict, List, Optional
import google.auth

logger = logging.getLogger(__name__)

DEFAULT_EXCLUDE_LABEL_KEYS = [
    "keep-alive",
    "keep_alive",
    "do-not-stop",
    "do_not_stop",
    "do-not-delete",
    "do_not_delete",
    "dont-stop",
    "dont-delete",
    "no-auto-stop",
    "no_auto_stop",
    "no-auto-delete",
    "no_auto_delete",
    "permanent",
    "whitelisted",
    "protected",
    "skip-lifecycle",
    "skip_lifecycle",
]

DEFAULT_WHITELIST_TAGS = [
    "keep-alive",
    "keep_alive",
    "do-not-stop",
    "do_not_stop",
    "do-not-delete",
    "do_not_delete",
    "dont-stop",
    "dont-delete",
    "no-auto-stop",
    "no_auto_stop",
    "no-auto-delete",
    "no_auto_delete",
    "permanent",
    "whitelisted",
    "protected",
    "skip-lifecycle",
    "skip_lifecycle",
]


def _parse_bool(val: Any, default: bool = False) -> bool:
    """Parse boolean value from various representations."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        val_lower = val.strip().lower()
        if val_lower in ("true", "1", "yes", "t", "y", "on"):
            return True
        if val_lower in ("false", "0", "no", "f", "n", "off"):
            return False
    return default


def _parse_int(val: Any, default: int, min_val: int = 1) -> int:
    """Parse integer value with minimum boundary check."""
    if val is None:
        return default
    try:
        parsed = int(val)
        return max(parsed, min_val)
    except (ValueError, TypeError):
        logger.warning("Failed to parse integer from '%s', falling back to default %d", val, default)
        return default


def _parse_list(val: Any, default: Optional[List[str]] = None) -> List[str]:
    """Parse list of strings from list, JSON string, or comma-separated string."""
    if default is None:
        default = []
    if val is None:
        return list(default)
    if isinstance(val, list):
        return [str(item).strip() for item in val if str(item).strip()]
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return list(default)
        if val.startswith("[") and val.endswith("]"):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except json.JSONDecodeError:
                pass
        return [part.strip() for part in val.split(",") if part.strip()]
    return list(default)


def _parse_dict(val: Any, default: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Parse dictionary from dict or JSON string."""
    if default is None:
        default = {}
    if val is None:
        return dict(default)
    if isinstance(val, dict):
        return {str(k).strip(): str(v).strip() for k, v in val.items()}
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return dict(default)
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return {str(k).strip(): str(v).strip() for k, v in parsed.items()}
        except json.JSONDecodeError:
            pass
    return dict(default)


def _resolve_adc_project() -> Optional[str]:
    """Attempt to resolve project ID from Google Application Default Credentials."""
    try:
        _, project_id = google.auth.default()
        if project_id and project_id != "(unset)":
            return project_id
    except Exception as e:
        logger.debug("Could not resolve project from ADC: %s", e)
    return None


@dataclass
class StopperConfig:
    """Configuration parameters for a VM Stopper execution sweep."""

    project_id: str
    idle_days_threshold: int = 7
    stopped_days_threshold: int = 90
    delete_stopped_vms: bool = False
    dry_run: bool = False
    max_workers: int = 20
    exclude_label_keys: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_LABEL_KEYS))
    exclude_label_values: Dict[str, str] = field(default_factory=dict)
    whitelist_names: List[str] = field(default_factory=list)
    whitelist_tags: List[str] = field(default_factory=lambda: list(DEFAULT_WHITELIST_TAGS))

    def validate(self) -> None:
        """Validate configuration integrity."""
        if not self.project_id or not self.project_id.strip():
            raise ValueError(
                "Missing target GCP Project ID. Provide 'project' / 'project_id' in "
                "request payload, query parameter, PROJECT_ID env var, or configure ADC."
            )
        if self.idle_days_threshold <= 0:
            raise ValueError(f"idle_days_threshold must be > 0, got {self.idle_days_threshold}")
        if self.stopped_days_threshold <= 0:
            raise ValueError(f"stopped_days_threshold must be > 0, got {self.stopped_days_threshold}")
        if self.max_workers <= 0:
            raise ValueError(f"max_workers must be > 0, got {self.max_workers}")

    @classmethod
    def from_request(
        cls,
        request_data: Optional[Dict[str, Any]] = None,
        query_args: Optional[Dict[str, Any]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> "StopperConfig":
        """Resolve configuration hierarchically from JSON body, query params, env, and ADC.

        Hierarchy:
        1. JSON request body
        2. HTTP query parameters
        3. Environment variables
        4. Application Default Credentials (ADC) for project ID
        """
        req = request_data or {}
        args = query_args or {}
        environ = env if env is not None else os.environ

        def _get_val(key_names: List[str], env_names: Optional[List[str]] = None) -> Any:
            for k in key_names:
                if k in req and req[k] is not None:
                    return req[k]
            for k in key_names:
                if k in args and args[k] is not None:
                    return args[k]
            if env_names:
                for ek in env_names:
                    if ek in environ and environ[ek] is not None and environ[ek].strip():
                        return environ[ek]
            return None

        # 1. Project ID
        project_id = _get_val(
            ["project", "project_id", "projectId"],
            ["PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCP_PROJECT"],
        )
        if not project_id or not str(project_id).strip():
            project_id = _resolve_adc_project()
        project_id = str(project_id).strip() if project_id else ""

        # 2. Thresholds
        raw_idle_days = _get_val(
            ["idle_days_threshold", "idle_days", "idleDaysThreshold", "days_threshold"],
            ["IDLE_DAYS_THRESHOLD", "IDLE_DAYS", "DAYS_THRESHOLD"],
        )
        idle_days_threshold = _parse_int(raw_idle_days, default=7, min_val=1)

        raw_stopped_days = _get_val(
            ["stopped_days_threshold", "stopped_days", "stoppedDaysThreshold", "delete_days_threshold"],
            ["STOPPED_DAYS_THRESHOLD", "STOPPED_DAYS", "DELETE_DAYS_THRESHOLD"],
        )
        stopped_days_threshold = _parse_int(raw_stopped_days, default=90, min_val=1)

        # 3. Flags
        raw_delete_stopped = _get_val(
            ["delete_stopped_vms", "delete_stopped", "deleteStoppedVms"],
            ["DELETE_STOPPED_VMS", "DELETE_STOPPED"],
        )
        delete_stopped_vms = _parse_bool(raw_delete_stopped, default=False)

        raw_dry_run = _get_val(
            ["dry_run", "dryRun", "simulate"],
            ["DRY_RUN"],
        )
        dry_run = _parse_bool(raw_dry_run, default=False)

        raw_max_workers = _get_val(
            ["max_workers", "maxWorkers", "concurrency"],
            ["MAX_WORKERS"],
        )
        max_workers = _parse_int(raw_max_workers, default=20, min_val=1)

        # 4. Exclusions / Whitelists
        raw_exclude_keys = _get_val(
            ["exclude_label_keys", "excludeLabelKeys", "ignored_label_keys"],
            ["EXCLUDE_LABEL_KEYS", "IGNORED_LABEL_KEYS"],
        )
        exclude_label_keys = _parse_list(raw_exclude_keys, default=DEFAULT_EXCLUDE_LABEL_KEYS)

        raw_exclude_values = _get_val(
            ["exclude_label_values", "excludeLabelValues"],
            ["EXCLUDE_LABEL_VALUES"],
        )
        exclude_label_values = _parse_dict(raw_exclude_values, default={})

        raw_whitelist_names = _get_val(
            ["whitelist_names", "whitelistNames", "exempt_vm_names"],
            ["WHITELIST_NAMES", "EXEMPT_VM_NAMES"],
        )
        whitelist_names = _parse_list(raw_whitelist_names, default=[])

        raw_whitelist_tags = _get_val(
            ["whitelist_tags", "whitelistTags", "exempt_tags"],
            ["WHITELIST_TAGS", "EXEMPT_TAGS"],
        )
        whitelist_tags = _parse_list(raw_whitelist_tags, default=DEFAULT_WHITELIST_TAGS)

        config = cls(
            project_id=project_id,
            idle_days_threshold=idle_days_threshold,
            stopped_days_threshold=stopped_days_threshold,
            delete_stopped_vms=delete_stopped_vms,
            dry_run=dry_run,
            max_workers=max_workers,
            exclude_label_keys=exclude_label_keys,
            exclude_label_values=exclude_label_values,
            whitelist_names=whitelist_names,
            whitelist_tags=whitelist_tags,
        )
        config.validate()
        return config
