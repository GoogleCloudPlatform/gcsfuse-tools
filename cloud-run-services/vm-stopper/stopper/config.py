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


def _parse_float(val: Any, default: float, min_val: float = 0.0) -> float:
    """Parse float value with minimum boundary check."""
    if val is None:
        return default
    try:
        parsed = float(val)
        return max(parsed, min_val)
    except (ValueError, TypeError):
        logger.warning("Failed to parse float from '%s', falling back to default %f", val, default)
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
    discard_local_ssd: bool = True
    exclude_label_keys: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_LABEL_KEYS))
    exclude_label_values: Dict[str, str] = field(default_factory=dict)
    whitelist_names: List[str] = field(default_factory=list)
    whitelist_tags: List[str] = field(default_factory=lambda: list(DEFAULT_WHITELIST_TAGS))
    cloud_logging_batch_size: int = 25
    cloud_logging_rate_limit: int = 40
    cloud_logging_max_retries: int = 4
    cloud_logging_retry_backoff: float = 2.0

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
        if self.cloud_logging_batch_size <= 0:
            raise ValueError(f"cloud_logging_batch_size must be > 0, got {self.cloud_logging_batch_size}")
        if self.cloud_logging_rate_limit <= 0:
            raise ValueError(f"cloud_logging_rate_limit must be > 0, got {self.cloud_logging_rate_limit}")
        if self.cloud_logging_max_retries < 0:
            raise ValueError(f"cloud_logging_max_retries must be >= 0, got {self.cloud_logging_max_retries}")
        if self.cloud_logging_retry_backoff < 0:
            raise ValueError(f"cloud_logging_retry_backoff must be >= 0, got {self.cloud_logging_retry_backoff}")

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

        raw_discard_local_ssd = _get_val(
            ["discard_local_ssd", "discardLocalSsd", "discard_local_ssds"],
            ["DISCARD_LOCAL_SSD"],
        )
        discard_local_ssd = _parse_bool(raw_discard_local_ssd, default=True)

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

        # 5. Cloud Logging Quota & Rate Limit Settings
        raw_batch_size = _get_val(
            ["cloud_logging_batch_size", "logging_batch_size", "batch_size"],
            ["CLOUD_LOGGING_BATCH_SIZE", "LOGGING_BATCH_SIZE"],
        )
        cloud_logging_batch_size = _parse_int(raw_batch_size, default=25, min_val=1)

        raw_rate_limit = _get_val(
            ["cloud_logging_rate_limit", "logging_rate_limit", "rate_limit"],
            ["CLOUD_LOGGING_RATE_LIMIT", "LOGGING_RATE_LIMIT"],
        )
        cloud_logging_rate_limit = _parse_int(raw_rate_limit, default=40, min_val=1)

        raw_max_retries = _get_val(
            ["cloud_logging_max_retries", "logging_max_retries", "max_retries"],
            ["CLOUD_LOGGING_MAX_RETRIES", "LOGGING_MAX_RETRIES"],
        )
        cloud_logging_max_retries = _parse_int(raw_max_retries, default=4, min_val=0)

        raw_retry_backoff = _get_val(
            ["cloud_logging_retry_backoff", "logging_retry_backoff", "retry_backoff"],
            ["CLOUD_LOGGING_RETRY_BACKOFF", "LOGGING_RETRY_BACKOFF"],
        )
        cloud_logging_retry_backoff = _parse_float(raw_retry_backoff, default=2.0, min_val=0.1)

        config = cls(
            project_id=project_id,
            idle_days_threshold=idle_days_threshold,
            stopped_days_threshold=stopped_days_threshold,
            delete_stopped_vms=delete_stopped_vms,
            dry_run=dry_run,
            max_workers=max_workers,
            discard_local_ssd=discard_local_ssd,
            exclude_label_keys=exclude_label_keys,
            exclude_label_values=exclude_label_values,
            whitelist_names=whitelist_names,
            whitelist_tags=whitelist_tags,
            cloud_logging_batch_size=cloud_logging_batch_size,
            cloud_logging_rate_limit=cloud_logging_rate_limit,
            cloud_logging_max_retries=cloud_logging_max_retries,
            cloud_logging_retry_backoff=cloud_logging_retry_backoff,
        )
        config.validate()
        return config
