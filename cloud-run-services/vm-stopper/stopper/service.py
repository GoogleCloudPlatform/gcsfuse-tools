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

"""Service orchestration layer for VM Stopper HTTP invocations."""

import logging
from typing import Any, Dict, Optional, Tuple

from stopper.config import StopperConfig
from stopper.gce_client import GCEClient
from stopper.vm_processor import VMProcessor

logger = logging.getLogger(__name__)


def process_request(
    request_data: Optional[Dict[str, Any]] = None,
    query_args: Optional[Dict[str, Any]] = None,
    gce_client: Optional[GCEClient] = None,
) -> Tuple[Dict[str, Any], int]:
    """Orchestrate configuration resolution and VM processing sweep.

    Args:
        request_data: Optional dictionary from JSON request body.
        query_args: Optional dictionary from URL query parameters.
        gce_client: Optional pre-configured GCEClient (e.g. for mock testing).

    Returns:
        Tuple containing the response dictionary and HTTP status code.
    """
    try:
        config = StopperConfig.from_request(
            request_data=request_data,
            query_args=query_args,
        )
    except ValueError as val_err:
        logger.warning("Configuration validation failed: %s", val_err)
        return {
            "status": "error",
            "service": "vm-stopper",
            "error": str(val_err),
            "summary": {
                "total_scanned": 0,
                "stopped": 0,
                "deleted": 0,
                "errors_count": 1,
            },
        }, 400
    except Exception as exc:
        logger.exception("Unexpected error initializing configuration: %s", exc)
        return {
            "status": "error",
            "service": "vm-stopper",
            "error": f"Failed to initialize configuration: {exc}",
        }, 500

    try:
        processor = VMProcessor(config=config, gce_client=gce_client)
        result = processor.sweep()
        return result, 200
    except Exception as exc:
        logger.exception("Fatal error during VM Stopper sweep: %s", exc)
        return {
            "status": "error",
            "service": "vm-stopper",
            "project_id": config.project_id,
            "error": str(exc),
            "summary": {
                "total_scanned": 0,
                "stopped": 0,
                "deleted": 0,
                "errors_count": 1,
            },
        }, 500
