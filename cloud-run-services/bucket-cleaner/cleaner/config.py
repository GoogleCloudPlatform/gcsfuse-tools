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

"""Hierarchical configuration resolution and validation for Bucket Cleaner."""

from dataclasses import dataclass, field
import os
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CleanerConfig:
    """Immutable runtime configuration parameters for Bucket Cleaner."""

    projects: List[str] = field(default_factory=lambda: ["gcs-fuse-test", "gcs-fuse-test-ml"])
    bucket_prefix: str = "gcsfuse-e2e-"
    age_days: int = 3
    dry_run: bool = False
    concurrency: int = 32
    batch_size: int = 50
    apply_olm_fallback: bool = True
    max_delete: Optional[int] = None

    @property
    def age_hours(self) -> int:
        """Return the age threshold in hours."""
        return self.age_days * 24

    @classmethod
    def from_request(
        cls,
        request_data: Optional[Dict[str, Any]] = None,
        query_args: Optional[Dict[str, Any]] = None,
    ) -> "CleanerConfig":
        """Resolve configuration hierarchically:

        Request Body JSON -> Query Args -> Environment Variables -> Defaults.
        """
        data: Dict[str, Any] = {}

        # 1. Environment variables
        env_projects = os.environ.get("PROJECTS") or os.environ.get("PROJECT_ID")
        if env_projects:
            data["projects"] = [p.strip() for p in env_projects.split(",") if p.strip()]

        if "BUCKET_PREFIX" in os.environ:
            data["bucket_prefix"] = os.environ["BUCKET_PREFIX"]

        if "AGE_DAYS" in os.environ:
            try:
                data["age_days"] = int(os.environ["AGE_DAYS"])
            except ValueError:
                pass

        if "DRY_RUN" in os.environ:
            data["dry_run"] = os.environ["DRY_RUN"].strip().lower() in ("true", "1", "yes")

        if "CONCURRENCY" in os.environ:
            try:
                data["concurrency"] = int(os.environ["CONCURRENCY"])
            except ValueError:
                pass

        if "BATCH_SIZE" in os.environ:
            try:
                data["batch_size"] = int(os.environ["BATCH_SIZE"])
            except ValueError:
                pass

        if "MAX_DELETE" in os.environ:
            try:
                data["max_delete"] = int(os.environ["MAX_DELETE"])
            except ValueError:
                pass

        # 2. Query Arguments
        if query_args:
            if "project" in query_args:
                data["projects"] = [p.strip() for p in str(query_args["project"]).split(",") if p.strip()]
            elif "projects" in query_args:
                data["projects"] = [p.strip() for p in str(query_args["projects"]).split(",") if p.strip()]

            if "bucket_prefix" in query_args:
                data["bucket_prefix"] = str(query_args["bucket_prefix"])
            elif "prefix" in query_args:
                data["bucket_prefix"] = str(query_args["prefix"])

            if "age_days" in query_args:
                data["age_days"] = int(query_args["age_days"])
            elif "age_hours" in query_args:
                data["age_days"] = max(1, int(int(query_args["age_hours"]) / 24))

            if "dry_run" in query_args:
                data["dry_run"] = str(query_args["dry_run"]).strip().lower() in ("true", "1", "yes")

            if "concurrency" in query_args:
                data["concurrency"] = int(query_args["concurrency"])

            if "batch_size" in query_args:
                data["batch_size"] = int(query_args["batch_size"])

            if "max_delete" in query_args:
                data["max_delete"] = int(query_args["max_delete"])
            elif "max_buckets" in query_args:
                data["max_delete"] = int(query_args["max_buckets"])

        # 3. Request Body JSON (Highest priority)
        if request_data:
            if "project" in request_data:
                p_val = request_data["project"]
                if isinstance(p_val, list):
                    data["projects"] = [str(p).strip() for p in p_val if str(p).strip()]
                else:
                    data["projects"] = [p.strip() for p in str(p_val).split(",") if p.strip()]
            elif "projects" in request_data:
                p_val = request_data["projects"]
                if isinstance(p_val, list):
                    data["projects"] = [str(p).strip() for p in p_val if str(p).strip()]
                else:
                    data["projects"] = [p.strip() for p in str(p_val).split(",") if p.strip()]

            if "bucket_prefix" in request_data:
                data["bucket_prefix"] = str(request_data["bucket_prefix"])
            elif "prefix" in request_data:
                data["bucket_prefix"] = str(request_data["prefix"])

            if "age_days" in request_data:
                data["age_days"] = int(request_data["age_days"])
            elif "age_hours" in request_data:
                data["age_days"] = max(1, int(int(request_data["age_hours"]) / 24))

            if "dry_run" in request_data:
                d_val = request_data["dry_run"]
                if isinstance(d_val, bool):
                    data["dry_run"] = d_val
                else:
                    data["dry_run"] = str(d_val).strip().lower() in ("true", "1", "yes")

            if "concurrency" in request_data:
                data["concurrency"] = int(request_data["concurrency"])

            if "batch_size" in request_data:
                data["batch_size"] = int(request_data["batch_size"])

            if "apply_olm_fallback" in request_data:
                data["apply_olm_fallback"] = bool(request_data["apply_olm_fallback"])

            if "max_delete" in request_data:
                val = request_data["max_delete"]
                data["max_delete"] = int(val) if val is not None else None
            elif "max_buckets" in request_data:
                val = request_data["max_buckets"]
                data["max_delete"] = int(val) if val is not None else None

        # Construct and validate
        config = cls(**data)
        config.validate()
        return config

    def validate(self) -> None:
        """Validate parameter ranges and preconditions."""
        if not self.projects:
            raise ValueError("Target 'projects' list cannot be empty.")
        for p in self.projects:
            if not p or not isinstance(p, str):
                raise ValueError(f"Invalid project ID in projects list: {p}")

        if not self.bucket_prefix:
            raise ValueError("Bucket prefix cannot be empty.")

        if self.age_days < 0:
            raise ValueError(f"age_days must be >= 0, got {self.age_days}")

        if self.concurrency < 1:
            raise ValueError(f"concurrency must be at least 1, got {self.concurrency}")

        if self.batch_size < 1:
            raise ValueError(f"batch_size must be at least 1, got {self.batch_size}")

        if self.max_delete is not None and self.max_delete < 1:
            raise ValueError(f"max_delete must be at least 1 if specified, got {self.max_delete}")
