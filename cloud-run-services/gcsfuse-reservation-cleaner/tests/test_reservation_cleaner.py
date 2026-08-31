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

"""Offline unit test suite for GCE Compute Reservation Cleaner."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import types
from typing import Any
import unittest
from unittest.mock import MagicMock, patch
import urllib3


# Ensure third-party modules can be imported/mocked offline
def _setup_offline_mock_modules() -> None:
    if "google" not in sys.modules:
        try:
            import google
        except ImportError:
            sys.modules["google"] = types.ModuleType("google")

    if "google.auth" not in sys.modules or not hasattr(sys.modules.get("google", None), "auth"):
        try:
            import google.auth
        except ImportError:
            auth_mod = types.ModuleType("google.auth")
            auth_mod.default = MagicMock(return_value=(MagicMock(), None))
            sys.modules["google.auth"] = auth_mod
            if "google" in sys.modules:
                setattr(sys.modules["google"], "auth", auth_mod)

    if "google.auth.credentials" not in sys.modules or not hasattr(sys.modules.get("google.auth", None), "credentials"):
        try:
            import google.auth.credentials
        except ImportError:
            cred_mod = types.ModuleType("google.auth.credentials")
            cred_mod.Credentials = MagicMock
            sys.modules["google.auth.credentials"] = cred_mod
            if "google.auth" in sys.modules:
                setattr(sys.modules["google.auth"], "credentials", cred_mod)

    if "google.auth.transport" not in sys.modules or not hasattr(sys.modules.get("google.auth", None), "transport"):
        try:
            import google.auth.transport
        except ImportError:
            trans_mod = types.ModuleType("google.auth.transport")
            sys.modules["google.auth.transport"] = trans_mod
            if "google.auth" in sys.modules:
                setattr(sys.modules["google.auth"], "transport", trans_mod)

    if "google.auth.transport.requests" not in sys.modules or not hasattr(sys.modules.get("google.auth.transport", None), "requests"):
        try:
            import google.auth.transport.requests
        except ImportError:
            req_mod = types.ModuleType("google.auth.transport.requests")
            req_mod.Request = MagicMock
            sys.modules["google.auth.transport.requests"] = req_mod
            if "google.auth.transport" in sys.modules:
                setattr(sys.modules["google.auth.transport"], "requests", req_mod)

    if "functions_framework" not in sys.modules:
        try:
            import functions_framework
        except ImportError:
            ff_mock = types.ModuleType("functions_framework")
            ff_mock.http = lambda f: f
            sys.modules["functions_framework"] = ff_mock

    if "flask" not in sys.modules:
        try:
            import flask
        except ImportError:
            flask_mock = MagicMock()

            class MockFlask:

                def __init__(self, name: str):
                    self.name = name
                    self.routes = {}

                def route(self, rule: str, **options: Any):

                    def decorator(f: Any) -> Any:
                        self.routes[rule] = f
                        return f

                    return decorator

                def test_client(self):
                    return MagicMock()

            flask_mock.Flask = MockFlask
            flask_mock.jsonify = lambda d: d
            flask_mock.request = MagicMock()
            sys.modules["flask"] = flask_mock


_setup_offline_mock_modules()

# Add service directory to Python path
SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

from cleaner.config import CleanerConfig, _parse_bool, _parse_list
from cleaner.pricing import (
    calculate_annual_cost,
    calculate_monthly_cost,
    estimate_hourly_rate,
    format_currency,
)
from cleaner.reservation_client import ReservationClient
from cleaner.reservation_processor import ReservationProcessor
from cleaner.service import ReservationCleanerService
import main


class TestCleanerConfig(unittest.TestCase):
    """Tests for dynamic configuration parsing and validation."""

    def test_parse_bool_variants(self):
        self.assertTrue(_parse_bool(True))
        self.assertTrue(_parse_bool("True"))
        self.assertTrue(_parse_bool("true"))
        self.assertTrue(_parse_bool("1"))
        self.assertTrue(_parse_bool("yes"))
        self.assertTrue(_parse_bool("Y"))
        self.assertFalse(_parse_bool(False))
        self.assertFalse(_parse_bool("False"))
        self.assertFalse(_parse_bool("false"))
        self.assertFalse(_parse_bool("0"))
        self.assertFalse(_parse_bool("no"))
        self.assertFalse(_parse_bool(None, default=False))
        self.assertTrue(_parse_bool(None, default=True))

    def test_parse_list_variants(self):
        self.assertIsNone(_parse_list(None))
        self.assertEqual(_parse_list("zone-a, zone-b"), ["zone-a", "zone-b"])
        self.assertEqual(_parse_list(["zone-a", "zone-b"]), ["zone-a", "zone-b"])
        self.assertIsNone(_parse_list(""))

    def test_config_from_dict_full(self):
        data = {
            "project_id": "test-project-123",
            "delete_idle_days": 45.0,
            "delete_never_used": False,
            "max_age_days": 120.0,
            "lookback_days": 365,
            "dry_run": True,
            "max_workers": 5,
            "zones": ["us-central1-a", "europe-west4-b"],
            "reservation_names": ["res-1", "res-2"],
        }
        cfg = CleanerConfig.from_dict(data)
        self.assertEqual(cfg.project_id, "test-project-123")
        self.assertEqual(cfg.delete_idle_days, 45.0)
        self.assertFalse(cfg.delete_never_used)
        self.assertEqual(cfg.max_age_days, 120.0)
        self.assertEqual(cfg.lookback_days, 365)
        self.assertTrue(cfg.dry_run)
        self.assertEqual(cfg.max_workers, 5)
        self.assertEqual(cfg.zones, ["us-central1-a", "europe-west4-b"])
        self.assertEqual(cfg.reservation_names, ["res-1", "res-2"])

    @patch.dict(os.environ, {"PROJECT_ID": "env-proj-456", "DELETE_IDLE_DAYS": "30", "DRY_RUN": "true"})
    def test_config_from_env_vars(self):
        cfg = CleanerConfig.from_dict({})
        self.assertEqual(cfg.project_id, "env-proj-456")
        self.assertEqual(cfg.delete_idle_days, 30.0)
        self.assertTrue(cfg.dry_run)

    @patch("google.auth.default", return_value=(MagicMock(), "adc-project-789"))
    def test_config_from_adc_fallback(self, mock_adc):
        with patch.dict(os.environ, {}, clear=True):
            cfg = CleanerConfig.from_dict({})
            self.assertEqual(cfg.project_id, "adc-project-789")

    def test_config_validation_missing_project_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("google.auth.default", return_value=(MagicMock(), None)):
                with self.assertRaises(ValueError):
                    CleanerConfig.from_dict({})

    def test_config_validation_invalid_parameters_raises(self):
        with self.assertRaises(ValueError):
            CleanerConfig(project_id="test", delete_idle_days=-5)
        with self.assertRaises(ValueError):
            CleanerConfig(project_id="test", max_workers=0)
        with self.assertRaises(ValueError):
            CleanerConfig(project_id="test", lookback_days=-1)

    def test_config_from_flask_request(self):
        mock_req = MagicMock()
        mock_req.args = {"project": "query-proj", "delete_idle_days": "15"}
        mock_req.is_json = True
        mock_req.get_json.return_value = {"project": "payload-proj", "dry_run": True}

        cfg = CleanerConfig.from_request(mock_req)
        # Payload takes precedence over query args
        self.assertEqual(cfg.project_id, "payload-proj")
        self.assertEqual(cfg.delete_idle_days, 15.0)
        self.assertTrue(cfg.dry_run)


class TestPricingCalculations(unittest.TestCase):
    """Tests for pricing estimates and monthly/annual cost models."""

    def test_standard_n2_pricing(self):
        # Default region rate for n2-standard-4 is 0.1942
        rate_default = estimate_hourly_rate("n2-standard-4", zone="us-central1-a")
        self.assertEqual(rate_default, 0.1942)

        # Regional rate in europe-west4 is 0.2136
        rate_eu = estimate_hourly_rate("n2-standard-4", zone="europe-west4-b")
        self.assertEqual(rate_eu, 0.2136)

    def test_accelerator_pricing(self):
        # n1-standard-8 base (0.3800) + 1x nvidia-tesla-t4 (0.3500) = 0.7300
        accelerators = [{"acceleratorType": "nvidia-tesla-t4", "acceleratorCount": 1}]
        rate = estimate_hourly_rate("n1-standard-8", zone="us-central1-a", accelerators=accelerators)
        self.assertAlmostEqual(rate, 0.7300, places=4)

    def test_fallback_custom_machine_pricing(self):
        # Custom machine with 16 vCPUs -> 16 * 0.0485 = 0.7760
        rate = estimate_hourly_rate("custom-16-65536", zone="us-central1-a")
        self.assertAlmostEqual(rate, 0.7760, places=4)

    def test_monthly_and_annual_cost(self):
        hourly_rate = 0.50
        capacity = 4
        # 0.50 * 4 * 730 = 1460.0
        monthly = calculate_monthly_cost(hourly_rate, count=capacity)
        self.assertEqual(monthly, 1460.0)

        # 1460 * 12 = 17520.0
        annual = calculate_annual_cost(monthly)
        self.assertEqual(annual, 17520.0)

    def test_format_currency(self):
        self.assertEqual(format_currency(1234.56), "$1,234.56")
        self.assertEqual(format_currency(0.0), "$0.00")


class TestReservationClient(unittest.TestCase):
    """Tests for GCE Compute and Cloud Monitoring REST client."""

    def setUp(self):
        self.mock_creds = MagicMock()
        self.mock_creds.valid = True
        self.mock_creds.token = "mock-bearer-token"
        self.mock_http = MagicMock(spec=urllib3.PoolManager)
        self.client = ReservationClient(credentials=self.mock_creds, http_pool=self.mock_http)

    def test_list_aggregated_reservations_single_page(self):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps(
            {
                "items": {
                    "zones/us-central1-a": {
                        "reservations": [
                            {
                                "id": "1001",
                                "name": "res-active-1",
                                "zone": "zones/us-central1-a",
                                "specificReservation": {
                                    "count": "2",
                                    "inUseCount": "2",
                                    "instanceProperties": {"machineType": "n2-standard-4"},
                                },
                            }
                        ]
                    }
                }
            }
        ).encode("utf-8")
        self.mock_http.request.return_value = mock_response

        res_list = self.client.list_aggregated_reservations("my-project")
        self.assertEqual(len(res_list), 1)
        self.assertEqual(res_list[0]["id"], "1001")
        self.assertEqual(res_list[0]["zone"], "us-central1-a")

    def test_list_aggregated_reservations_multi_page(self):
        page_1 = MagicMock()
        page_1.status = 200
        page_1.data = json.dumps(
            {
                "nextPageToken": "token-page-2",
                "items": {
                    "zones/us-central1-a": {
                        "reservations": [{"id": "1001", "name": "res-1", "zone": "us-central1-a"}]
                    }
                },
            }
        ).encode("utf-8")

        page_2 = MagicMock()
        page_2.status = 200
        page_2.data = json.dumps(
            {
                "items": {
                    "zones/europe-west4-a": {
                        "reservations": [{"id": "1002", "name": "res-2", "zone": "europe-west4-a"}]
                    }
                }
            }
        ).encode("utf-8")

        self.mock_http.request.side_effect = [page_1, page_2]

        res_list = self.client.list_aggregated_reservations("my-project")
        self.assertEqual(len(res_list), 2)
        self.assertEqual(res_list[0]["id"], "1001")
        self.assertEqual(res_list[1]["id"], "1002")

    def test_list_aggregated_reservations_http_error(self):
        err_response = MagicMock()
        err_response.status = 403
        err_response.data = b'{"error": {"message": "Permission denied"}}'
        self.mock_http.request.return_value = err_response

        with self.assertRaises(RuntimeError):
            self.client.list_aggregated_reservations("my-project")

    def test_query_reservation_usage_active_points(self):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps(
            {
                "timeSeries": [
                    {
                        "points": [
                            {
                                "interval": {"startTime": "2026-06-01T00:00:00Z", "endTime": "2026-06-01T01:00:00Z"},
                                "value": {"int64Value": "2"},
                            },
                            {
                                "interval": {"startTime": "2026-07-01T00:00:00Z", "endTime": "2026-07-01T01:00:00Z"},
                                "value": {"int64Value": "1"},
                            },
                        ]
                    }
                ]
            }
        ).encode("utf-8")
        self.mock_http.request.return_value = mock_response

        usage = self.client.query_reservation_usage("my-project", "1001", lookback_days=90)
        self.assertFalse(usage["is_never_used"])
        self.assertEqual(usage["first_used_timestamp"], "2026-06-01T01:00:00Z")
        self.assertEqual(usage["last_used_timestamp"], "2026-07-01T01:00:00Z")
        self.assertEqual(usage["total_active_hours"], 2)
        self.assertEqual(usage["max_usage_count"], 2)
        self.assertIsNone(usage["error"])

    def test_query_reservation_usage_never_used(self):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps({"timeSeries": []}).encode("utf-8")
        self.mock_http.request.return_value = mock_response

        usage = self.client.query_reservation_usage("my-project", "1002")
        self.assertTrue(usage["is_never_used"])
        self.assertIsNone(usage["last_used_timestamp"])
        self.assertEqual(usage["total_active_hours"], 0)

    def test_query_reservation_usage_http_error(self):
        err_response = MagicMock()
        err_response.status = 500
        err_response.data = b"Internal Server Error"
        self.mock_http.request.return_value = err_response

        usage = self.client.query_reservation_usage("my-project", "1003")
        self.assertIsNotNone(usage["error"])
        self.assertFalse(usage["is_never_used"])

    def test_delete_reservation_success(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.data = b'{"status": "DONE"}'
        self.mock_http.request.return_value = mock_resp

        result = self.client.delete_reservation("my-project", "us-central1-a", "res-to-delete")
        self.assertTrue(result)

    def test_delete_reservation_failure(self):
        mock_resp = MagicMock()
        mock_resp.status = 404
        mock_resp.data = b'{"error": "Not Found"}'
        self.mock_http.request.return_value = mock_resp

        with self.assertRaises(RuntimeError):
            self.client.delete_reservation("my-project", "us-central1-a", "res-nonexistent")


class TestReservationProcessor(unittest.TestCase):
    """Tests for stale evaluation, safety checks, and deletion lifecycle."""

    def setUp(self):
        self.config = CleanerConfig(
            project_id="test-proj",
            delete_idle_days=60.0,
            delete_never_used=True,
            max_age_days=180.0,
            dry_run=False,
        )
        self.mock_client = MagicMock(spec=ReservationClient)
        self.processor = ReservationProcessor(self.config, self.mock_client)
        self.ref_now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)

    def test_safety_check_active_reservation_never_deleted(self):
        """STRICT SAFETY RULE: inUseCount > 0 must NEVER be candidate or deleted."""
        active_res = {
            "id": "1001",
            "name": "prod-active-res",
            "zone": "us-central1-a",
            "creationTimestamp": "2025-01-01T00:00:00Z",
            "specificReservation": {
                "count": "10",
                "inUseCount": "5",  # In use!
                "instanceProperties": {"machineType": "n2-standard-8"},
            },
        }

        evaluated = self.processor.evaluate_reservation(active_res, now=self.ref_now)
        self.assertEqual(evaluated["status"], "Active Now")
        self.assertFalse(evaluated["is_candidate"])
        self.assertEqual(evaluated["action"], "retained_active")

        # Client monitoring query should NOT even need to be called
        self.mock_client.query_reservation_usage.assert_not_called()

        # Process should retain
        processed = self.processor.process_reservation(evaluated)
        self.mock_client.delete_reservation.assert_not_called()
        self.assertEqual(processed["action"], "retained_active")

    def test_stale_idle_reservation_marked_and_deleted(self):
        """Idle reservation exceeding delete_idle_days threshold."""
        idle_res = {
            "id": "1002",
            "name": "stale-dev-res",
            "zone": "europe-west4-a",
            "creationTimestamp": "2025-01-01T00:00:00Z",
            "specificReservation": {
                "count": "2",
                "inUseCount": "0",
                "instanceProperties": {"machineType": "n2-standard-4"},
            },
        }

        # Last used 90 days ago (> 60 day threshold)
        self.mock_client.query_reservation_usage.return_value = {
            "is_never_used": False,
            "last_used_timestamp": "2026-06-02T12:00:00Z",  # 90 days before 2026-08-31
            "first_used_timestamp": "2025-02-01T00:00:00Z",
            "total_active_hours": 100,
            "max_usage_count": 2,
            "error": None,
        }

        evaluated = self.processor.evaluate_reservation(idle_res, now=self.ref_now)
        self.assertEqual(evaluated["status"], "Idle")
        self.assertTrue(evaluated["is_candidate"])
        self.assertAlmostEqual(evaluated["days_since_last_used"], 90.0, places=0)

        # Process deletion
        self.mock_client.delete_reservation.return_value = True
        processed = self.processor.process_reservation(evaluated)

        self.mock_client.delete_reservation.assert_called_once_with(
            project_id="test-proj",
            zone="europe-west4-a",
            reservation_name="stale-dev-res",
        )
        self.assertEqual(processed["action"], "deleted")

    def test_recently_used_reservation_retained(self):
        """Idle reservation last used within delete_idle_days threshold."""
        recent_res = {
            "id": "1003",
            "name": "recent-test-res",
            "zone": "us-central1-b",
            "creationTimestamp": "2026-01-01T00:00:00Z",
            "specificReservation": {
                "count": "1",
                "inUseCount": "0",
                "instanceProperties": {"machineType": "e2-standard-4"},
            },
        }

        # Last used 10 days ago (< 60 day threshold)
        self.mock_client.query_reservation_usage.return_value = {
            "is_never_used": False,
            "last_used_timestamp": "2026-08-21T12:00:00Z",
            "first_used_timestamp": "2026-01-15T00:00:00Z",
            "total_active_hours": 50,
            "max_usage_count": 1,
            "error": None,
        }

        evaluated = self.processor.evaluate_reservation(recent_res, now=self.ref_now)
        self.assertEqual(evaluated["status"], "Recently Used")
        self.assertFalse(evaluated["is_candidate"])
        self.assertEqual(evaluated["action"], "retained_recent")

        processed = self.processor.process_reservation(evaluated)
        self.mock_client.delete_reservation.assert_not_called()

    def test_never_used_reservation_with_policy_enabled(self):
        """Never used reservation with delete_never_used=True."""
        never_used_res = {
            "id": "1004",
            "name": "unused-res",
            "zone": "us-central1-a",
            "creationTimestamp": "2026-03-01T00:00:00Z",
            "specificReservation": {
                "count": "4",
                "inUseCount": "0",
                "instanceProperties": {"machineType": "n1-standard-4"},
            },
        }

        self.mock_client.query_reservation_usage.return_value = {
            "is_never_used": True,
            "last_used_timestamp": None,
            "first_used_timestamp": None,
            "total_active_hours": 0,
            "max_usage_count": 0,
            "error": None,
        }

        evaluated = self.processor.evaluate_reservation(never_used_res, now=self.ref_now)
        self.assertEqual(evaluated["status"], "Never Used")
        self.assertTrue(evaluated["is_candidate"])

        self.processor.process_reservation(evaluated)
        self.mock_client.delete_reservation.assert_called_once_with(
            project_id="test-proj",
            zone="us-central1-a",
            reservation_name="unused-res",
        )

    def test_protected_reservation_with_labels_is_retained(self):
        """Reservations with keep-alive, do-not-delete, or auto-delete=false labels are retained."""
        res_protected_1 = {
            "id": "1005",
            "name": "protected-res-1",
            "zone": "us-central1-a",
            "labels": {"keep-alive": "true"},
            "specificReservation": {"count": "2", "inUseCount": "0"},
        }
        res_protected_2 = {
            "id": "1006",
            "name": "protected-res-2",
            "zone": "us-central1-a",
            "labels": {"auto-delete": "false"},
            "specificReservation": {"count": "2", "inUseCount": "0"},
        }

        eval1 = self.processor.evaluate_reservation(res_protected_1, now=self.ref_now)
        eval2 = self.processor.evaluate_reservation(res_protected_2, now=self.ref_now)

        self.assertEqual(eval1["status"], "Protected")
        self.assertFalse(eval1["is_candidate"])
        self.assertEqual(eval1["action"], "retained_protected")

        self.assertEqual(eval2["status"], "Protected")
        self.assertFalse(eval2["is_candidate"])
        self.assertEqual(eval2["action"], "retained_protected")

        self.mock_client.query_reservation_usage.assert_not_called()
        self.mock_client.delete_reservation.assert_not_called()

    def test_never_used_reservation_with_policy_disabled(self):
        """Never used reservation with delete_never_used=False and young age."""
        self.config.delete_never_used = False
        self.config.max_age_days = 180.0

        never_used_young = {
            "id": "1005",
            "name": "young-unused-res",
            "zone": "us-central1-a",
            "creationTimestamp": "2026-08-01T00:00:00Z",  # 30 days old
            "specificReservation": {
                "count": "1",
                "inUseCount": "0",
                "instanceProperties": {"machineType": "n2-standard-2"},
            },
        }

        self.mock_client.query_reservation_usage.return_value = {
            "is_never_used": True,
            "last_used_timestamp": None,
            "first_used_timestamp": None,
            "total_active_hours": 0,
            "max_usage_count": 0,
            "error": None,
        }

        evaluated = self.processor.evaluate_reservation(never_used_young, now=self.ref_now)
        self.assertEqual(evaluated["status"], "Never Used")
        self.assertFalse(evaluated["is_candidate"])
        self.assertEqual(evaluated["action"], "retained_never_used")

    def test_dry_run_mode_never_deletes(self):
        """Dry-run mode records candidate and savings without calling delete API."""
        self.config.dry_run = True

        candidate_res = {
            "id": "1006",
            "name": "dry-run-target",
            "zone": "us-central1-a",
            "creationTimestamp": "2025-01-01T00:00:00Z",
            "specificReservation": {
                "count": "2",
                "inUseCount": "0",
                "instanceProperties": {"machineType": "n2-standard-4"},
            },
        }

        self.mock_client.query_reservation_usage.return_value = {
            "is_never_used": True,
            "last_used_timestamp": None,
            "first_used_timestamp": None,
            "total_active_hours": 0,
            "max_usage_count": 0,
            "error": None,
        }

        evaluated = self.processor.evaluate_reservation(candidate_res, now=self.ref_now)
        self.assertTrue(evaluated["is_candidate"])

        processed = self.processor.process_reservation(evaluated)
        self.mock_client.delete_reservation.assert_not_called()
        self.assertEqual(processed["action"], "dry_run_candidate")
        self.assertIn("[DRY-RUN]", processed["message"])

    def test_monitoring_error_retains_reservation_safely(self):
        """When Cloud Monitoring fails, reservation is marked as error and retained."""
        err_res = {
            "id": "1007",
            "name": "error-res",
            "zone": "us-central1-a",
            "specificReservation": {
                "count": "1",
                "inUseCount": "0",
                "instanceProperties": {"machineType": "n1-standard-1"},
            },
        }

        self.mock_client.query_reservation_usage.return_value = {
            "is_never_used": False,
            "last_used_timestamp": None,
            "first_used_timestamp": None,
            "total_active_hours": 0,
            "max_usage_count": 0,
            "error": "500 Internal Error",
        }

        evaluated = self.processor.evaluate_reservation(err_res, now=self.ref_now)
        self.assertEqual(evaluated["status"], "Query Error")
        self.assertFalse(evaluated["is_candidate"])
        self.assertEqual(evaluated["action"], "retained_error")


class TestReservationCleanerService(unittest.TestCase):
    """Tests for full sweep coordination and aggregate financial reporting."""

    def setUp(self):
        self.config = CleanerConfig(
            project_id="test-fleet-project",
            delete_idle_days=60.0,
            delete_never_used=True,
            dry_run=False,
            max_workers=4,
        )
        self.mock_client = MagicMock(spec=ReservationClient)
        self.service = ReservationCleanerService(self.config, client=self.mock_client)
        self.ref_now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)

    def test_full_sweep_mixed_fleet(self):
        # 1 active, 1 idle, 1 never-used, 1 recently-used
        mock_reservations = [
            {
                "id": "101",
                "name": "active-res",
                "zone": "us-central1-a",
                "specificReservation": {
                    "count": "2",
                    "inUseCount": "2",
                    "instanceProperties": {"machineType": "n2-standard-4"},
                },
            },
            {
                "id": "102",
                "name": "idle-res",
                "zone": "us-central1-b",
                "specificReservation": {
                    "count": "1",
                    "inUseCount": "0",
                    "instanceProperties": {"machineType": "n2-standard-8"},
                },
            },
            {
                "id": "103",
                "name": "never-used-res",
                "zone": "europe-west4-a",
                "specificReservation": {
                    "count": "1",
                    "inUseCount": "0",
                    "instanceProperties": {"machineType": "e2-standard-4"},
                },
            },
            {
                "id": "104",
                "name": "recently-used-res",
                "zone": "asia-northeast1-a",
                "specificReservation": {
                    "count": "1",
                    "inUseCount": "0",
                    "instanceProperties": {"machineType": "n1-standard-4"},
                },
            },
        ]
        self.mock_client.list_aggregated_reservations.return_value = mock_reservations

        def mock_query(project_id, reservation_id, lookback_days, reference_time):
            if reservation_id == "102":
                return {
                    "is_never_used": False,
                    "last_used_timestamp": "2026-05-01T00:00:00Z",  # 122 days ago
                    "total_active_hours": 20,
                    "max_usage_count": 1,
                    "error": None,
                }
            elif reservation_id == "103":
                return {
                    "is_never_used": True,
                    "last_used_timestamp": None,
                    "total_active_hours": 0,
                    "max_usage_count": 0,
                    "error": None,
                }
            elif reservation_id == "104":
                return {
                    "is_never_used": False,
                    "last_used_timestamp": "2026-08-25T00:00:00Z",  # 6 days ago
                    "total_active_hours": 10,
                    "max_usage_count": 1,
                    "error": None,
                }
            return {"is_never_used": False, "error": "unknown"}

        self.mock_client.query_reservation_usage.side_effect = mock_query
        self.mock_client.delete_reservation.return_value = True

        result = self.service.run(reference_time=self.ref_now)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["service"], "gcsfuse-reservation-cleaner")
        summary = result["summary"]
        self.assertEqual(summary["total_reservations"], 4)
        self.assertEqual(summary["active_now"], 1)
        self.assertEqual(summary["idle"], 1)
        self.assertEqual(summary["never_used"], 1)
        self.assertEqual(summary["recently_used"], 1)
        self.assertEqual(summary["candidates_for_deletion"], 2)
        self.assertEqual(summary["deleted"], 2)
        self.assertGreater(summary["realized_monthly_savings_usd"], 0)

        # Confirm delete_reservation called exactly twice (for 102 and 103)
        self.assertEqual(self.mock_client.delete_reservation.call_count, 2)

    def test_zone_filtering(self):
        self.config.zones = ["us-central1-a"]
        mock_reservations = [
            {
                "id": "1",
                "name": "res-in-zone",
                "zone": "us-central1-a",
                "specificReservation": {"count": "1", "inUseCount": "1"},
            },
            {
                "id": "2",
                "name": "res-other-zone",
                "zone": "europe-west4-a",
                "specificReservation": {"count": "1", "inUseCount": "1"},
            },
        ]
        self.mock_client.list_aggregated_reservations.return_value = mock_reservations

        result = self.service.run(reference_time=self.ref_now)
        self.assertEqual(len(result["reservations"]), 1)
        self.assertEqual(result["reservations"][0]["name"], "res-in-zone")

    def test_list_reservations_failure_handled(self):
        self.mock_client.list_aggregated_reservations.side_effect = RuntimeError("API unreachable")
        result = self.service.run(reference_time=self.ref_now)
        self.assertEqual(result["status"], "error")
        self.assertEqual(len(result["errors"]), 1)


class TestHttpEndpoints(unittest.TestCase):
    """Tests for Flask routing and Functions Framework handler."""

    def setUp(self):
        self.app = main.app.test_client()
        self.app.testing = True

    def test_health_endpoint(self):
        response = self.app.get("/health")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data.decode("utf-8"))
        self.assertEqual(data["status"], "ok")

    @patch("cleaner.service.ReservationCleanerService.run")
    def test_post_root_success(self, mock_run):
        mock_run.return_value = {
            "status": "success",
            "service": "gcsfuse-reservation-cleaner",
            "project_id": "req-proj",
            "dry_run": True,
            "summary": {"total_reservations": 1, "candidates_for_deletion": 0},
            "actions_taken": [],
            "reservations": [],
            "errors": [],
        }

        payload = {"project": "req-proj", "dry_run": True}
        response = self.app.post("/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data.decode("utf-8"))
        self.assertEqual(data["project_id"], "req-proj")
        self.assertTrue(data["dry_run"])

    def test_post_root_missing_project_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("google.auth.default", return_value=(MagicMock(), None)):
                response = self.app.post("/", data=json.dumps({}), content_type="application/json")
                self.assertEqual(response.status_code, 400)
                data = json.loads(response.data.decode("utf-8"))
                self.assertEqual(data["status"], "error")
                self.assertIn("project_id", data["error"])


class TestDeploymentScriptSyntax(unittest.TestCase):
    """Tests to verify deployment script syntax and argument validation."""

    def test_deploy_script_syntax(self):
        script_path = Path(__file__).parent.parent / "deploy.sh"
        self.assertTrue(script_path.exists(), f"deploy.sh not found at {script_path}")

        # Run bash -n syntax check
        result = subprocess.run(
            ["bash", "-n", str(script_path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"bash -n failed on deploy.sh:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )

    def test_deploy_script_help_flag(self):
        script_path = Path(__file__).parent.parent / "deploy.sh"
        result = subprocess.run(
            ["bash", str(script_path), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("--project", result.stdout)
        self.assertIn("--dry-run", result.stdout)


if __name__ == "__main__":
    unittest.main()
