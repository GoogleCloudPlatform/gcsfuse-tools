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

"""HTTP Entrypoint for GCE Compute Reservation Cleaner (Cloud Run / Cloud Functions)."""

import logging
import os
import sys
from flask import Flask, jsonify, request as flask_request
import functions_framework

from cleaner.config import CleanerConfig
from cleaner.service import ReservationCleanerService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("gcsfuse-reservation-cleaner")

app = Flask(__name__)


@functions_framework.http
def cleanup_reservations(request):
    """Entry point for Cloud Functions Gen 2 / HTTP invocations."""
    try:
        config = CleanerConfig.from_request(request)
    except ValueError as e:
        logger.error("Configuration validation error: %s", e)
        return (
            jsonify(
                {
                    "status": "error",
                    "service": "gcsfuse-reservation-cleaner",
                    "error": str(e),
                }
            ),
            400,
        )
    except Exception as e:
        logger.error("Unexpected error parsing configuration: %s", e)
        return (
            jsonify(
                {
                    "status": "error",
                    "service": "gcsfuse-reservation-cleaner",
                    "error": f"Invalid request configuration: {e}",
                }
            ),
            400,
        )

    try:
        service = ReservationCleanerService(config)
        result = service.run()
        status_code = 200 if result.get("status") == "success" else 200
        return jsonify(result), status_code
    except Exception as e:
        logger.exception("Fatal unhandled error during reservation cleanup: %s", e)
        return (
            jsonify(
                {
                    "status": "error",
                    "service": "gcsfuse-reservation-cleaner",
                    "error": str(e),
                }
            ),
            500,
        )


@app.route("/", methods=["GET", "POST"])
def index():
    """WSGI routing for Cloud Run container hosting."""
    return cleanup_reservations(flask_request)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint for container liveness/readiness probes."""
    return jsonify({"status": "ok", "service": "gcsfuse-reservation-cleaner"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info("Starting GCE Reservation Cleaner HTTP server on port %d...", port)
    app.run(host="0.0.0.0", port=port)
