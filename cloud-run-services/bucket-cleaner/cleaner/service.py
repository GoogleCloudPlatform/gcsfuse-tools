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

"""Service orchestration layer for Bucket Cleaner HTTP invocations."""

import logging
from typing import Any, Dict, Optional, Tuple

from cleaner.config import CleanerConfig
from cleaner.gcs_client import GCSClient
from cleaner.bucket_processor import BucketProcessor

logger = logging.getLogger(__name__)


def process_request(
    request_data: Optional[Dict[str, Any]] = None,
    query_args: Optional[Dict[str, Any]] = None,
    gcs_client: Optional[GCSClient] = None,
) -> Tuple[Dict[str, Any], int]:
    """Orchestrate configuration resolution and bucket cleaning sweep.

    Args:
        request_data: Optional dictionary from JSON request body.
        query_args: Optional dictionary from URL query parameters.
        gcs_client: Optional pre-configured GCSClient (e.g. for mock testing).

    Returns:
        Tuple containing the response dictionary and HTTP status code.
    """
    try:
        config = CleanerConfig.from_request(
            request_data=request_data,
            query_args=query_args,
        )
    except ValueError as val_err:
        logger.warning("Configuration validation failed: %s", val_err)
        return {
            "status": "error",
            "service": "bucket-cleaner",
            "error": str(val_err),
            "summary": {
                "total_scanned": 0,
                "total_eligible": 0,
                "deleted_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
            },
        }, 400
    except Exception as exc:
        logger.exception("Unexpected error during configuration parsing: %s", exc)
        return {
            "status": "error",
            "service": "bucket-cleaner",
            "error": f"Internal configuration error: {exc}",
        }, 500

    client = gcs_client or GCSClient()
    processor = BucketProcessor(config=config, gcs_client=client)

    try:
        result = processor.process_all_projects()
        status_code = 200
        if result.get("status") == "error":
            status_code = 502
        return result, status_code
    except Exception as exc:
        logger.exception("Unexpected failure during bucket cleanup processing: %s", exc)
        return {
            "status": "error",
            "service": "bucket-cleaner",
            "error": f"Execution failure: {exc}",
        }, 500
