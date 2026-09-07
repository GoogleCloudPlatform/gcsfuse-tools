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
import unittest.mock
from unittest.mock import MagicMock, Mock, patch
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
        with self.assertRaises(ValueError):
            CleanerConfig(project_id="test", pool_maxsize=0)
        with self.assertRaises(ValueError):
            CleanerConfig(project_id="test", pool_maxsize=-3)

    def test_config_pool_maxsize_resolution(self):
        # Default effective pool size: max(10, max_workers)
        cfg_default = CleanerConfig(project_id="test", max_workers=4)
        self.assertIsNone(cfg_default.pool_maxsize)
        self.assertEqual(cfg_default.effective_pool_maxsize, 10)

        # Scales up with max_workers if greater than 10
        cfg_high_workers = CleanerConfig(project_id="test", max_workers=25)
        self.assertEqual(cfg_high_workers.effective_pool_maxsize, 25)

        # Explicit pool_maxsize override takes precedence
        cfg_override = CleanerConfig(project_id="test", max_workers=5, pool_maxsize=50)
        self.assertEqual(cfg_override.pool_maxsize, 50)
        self.assertEqual(cfg_override.effective_pool_maxsize, 50)

        # Resolution via dict and environment variables
        cfg_from_dict = CleanerConfig.from_dict({"project_id": "test", "pool_maxsize": 20})
        self.assertEqual(cfg_from_dict.pool_maxsize, 20)

        with patch.dict(os.environ, {"POOL_MAXSIZE": "35"}):
            cfg_from_env = CleanerConfig.from_dict({"project_id": "test"})
            self.assertEqual(cfg_from_env.pool_maxsize, 35)
            self.assertEqual(cfg_from_env.effective_pool_maxsize, 35)

        # Resolution of float strings e.g. "10.0" in CleanerConfig
        cfg_float_str = CleanerConfig.from_dict({"project_id": "test", "pool_maxsize": "10.0"})
        self.assertEqual(cfg_float_str.pool_maxsize, 10)
        self.assertIsInstance(cfg_float_str.pool_maxsize, int)

        with patch.dict(os.environ, {"POOL_MAXSIZE": "15.0"}):
            cfg_env_float_str = CleanerConfig.from_dict({"project_id": "test"})
            self.assertEqual(cfg_env_float_str.pool_maxsize, 15)
            self.assertIsInstance(cfg_env_float_str.pool_maxsize, int)

        # Invalid pool_maxsize string raises ValueError
        with self.assertRaises(ValueError):
            CleanerConfig.from_dict({"project_id": "test", "pool_maxsize": "invalid"})

    def test_config_pool_maxsize_non_positive_raises(self):
        # pool_maxsize <= 0 (e.g. 0, -1, "-5", "0.0") raises ValueError via direct constructor and from_dict
        invalid_values = [0, -1, -5, "-5", "0", "0.0", "-1.0"]
        for val in invalid_values:
            with self.subTest(val=val):
                with self.assertRaises(ValueError):
                    CleanerConfig(project_id="test", pool_maxsize=val)
                with self.assertRaises(ValueError):
                    CleanerConfig.from_dict({"project_id": "test", "pool_maxsize": val})
                with patch.dict(os.environ, {"POOL_MAXSIZE": str(val)}):
                    with self.assertRaises(ValueError):
                        CleanerConfig.from_dict({"project_id": "test"})

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

    def test_reservation_client_keyword_only_parameters(self):
        # http_pool and maxsize are keyword-only arguments to prevent positional binding bugs
        with self.assertRaises(TypeError):
            ReservationClient(None, self.mock_http)
        with self.assertRaises(TypeError):
            ReservationClient(self.mock_creds, self.mock_http)
        with self.assertRaises(TypeError):
            ReservationClient(self.mock_creds, self.mock_http, 10)
        with self.assertRaises(TypeError):
            ReservationClient(None, 10)
        with self.assertRaises(TypeError):
            ReservationClient(self.mock_creds, 10)

    def test_reservation_client_non_positive_maxsize_raises(self):
        # maxsize <= 0 (e.g., 0, -1, "-5", "0.0") raises ValueError
        invalid_values = [0, -1, -10, "-5", "0", "0.0", "-1.0"]
        for val in invalid_values:
            with self.subTest(val=val):
                with self.assertRaises(ValueError):
                    ReservationClient(credentials=self.mock_creds, maxsize=val)
                with self.assertRaises(ValueError):
                    ReservationClient(credentials=self.mock_creds, http_pool=self.mock_http, maxsize=val)

    def test_reservation_client_default_pool_maxsize(self):
        # Default maxsize must be >= 10 to support concurrent workers
        client_default = ReservationClient(credentials=self.mock_creds)
        self.assertEqual(client_default.maxsize, 10)
        self.assertIsInstance(client_default.http_pool, urllib3.PoolManager)
        self.assertEqual(client_default.http_pool.connection_pool_kw.get("maxsize"), 10)

    def test_reservation_client_custom_pool_maxsize(self):
        # Custom maxsize parameter sets connection_pool_kw maxsize
        client_custom = ReservationClient(credentials=self.mock_creds, maxsize=32)
        self.assertEqual(client_custom.maxsize, 32)
        self.assertEqual(client_custom.http_pool.connection_pool_kw.get("maxsize"), 32)

    def test_reservation_client_maxsize_type_coercion(self):
        # Verifies string and float maxsize parameters are safely cast to int
        client_str = ReservationClient(credentials=self.mock_creds, maxsize="25")
        self.assertEqual(client_str.maxsize, 25)
        self.assertIsInstance(client_str.maxsize, int)
        self.assertEqual(client_str.http_pool.connection_pool_kw.get("maxsize"), 25)

        client_float = ReservationClient(credentials=self.mock_creds, maxsize=15.0)
        self.assertEqual(client_float.maxsize, 15)
        self.assertIsInstance(client_float.maxsize, int)
        self.assertEqual(client_float.http_pool.connection_pool_kw.get("maxsize"), 15)

        # Also verify type coercion when custom pool fallback occurs
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        client_pool_str = ReservationClient(
            credentials=self.mock_creds, http_pool=mock_pool, maxsize="25"
        )
        self.assertEqual(client_pool_str.maxsize, 25)
        self.assertIsInstance(client_pool_str.maxsize, int)

        client_pool_float = ReservationClient(
            credentials=self.mock_creds, http_pool=mock_pool, maxsize=15.0
        )
        self.assertEqual(client_pool_float.maxsize, 15)
        self.assertIsInstance(client_pool_float.maxsize, int)

        # Verifies float-like string parameters (e.g. "15.0", "10.0") are parsed correctly
        client_float_str = ReservationClient(credentials=self.mock_creds, maxsize="15.0")
        self.assertEqual(client_float_str.maxsize, 15)
        self.assertIsInstance(client_float_str.maxsize, int)
        self.assertEqual(client_float_str.http_pool.connection_pool_kw.get("maxsize"), 15)

        client_float_str2 = ReservationClient(credentials=self.mock_creds, maxsize="10.0")
        self.assertEqual(client_float_str2.maxsize, 10)
        self.assertIsInstance(client_float_str2.maxsize, int)
        self.assertEqual(client_float_str2.http_pool.connection_pool_kw.get("maxsize"), 10)

        # Verifies None falls back to DEFAULT_POOL_SIZE (10)
        client_none = ReservationClient(credentials=self.mock_creds, maxsize=None)
        self.assertEqual(client_none.maxsize, 10)
        self.assertEqual(client_none.http_pool.connection_pool_kw.get("maxsize"), 10)

        # Verifies invalid non-numeric string raises ValueError rather than silent fallback
        with self.assertRaises(ValueError):
            ReservationClient(credentials=self.mock_creds, maxsize="invalid")

        # Fallback to DEFAULT_POOL_SIZE when custom pool fallback occurs
        client_pool_none = ReservationClient(
            credentials=self.mock_creds, http_pool=mock_pool, maxsize=None
        )
        self.assertEqual(client_pool_none.maxsize, 10)

        client_pool_float_str = ReservationClient(
            credentials=self.mock_creds, http_pool=mock_pool, maxsize="15.0"
        )
        self.assertEqual(client_pool_float_str.maxsize, 15)
        self.assertIsInstance(client_pool_float_str.maxsize, int)

        with self.assertRaises(ValueError):
            ReservationClient(credentials=self.mock_creds, http_pool=mock_pool, maxsize="invalid")

    def test_reservation_client_custom_http_pool_maxsize_extraction(self):
        # Verify that when a custom urllib3.PoolManager(maxsize=42) is passed as http_pool,
        # client.maxsize returns 42
        custom_pool = urllib3.PoolManager(maxsize=42)
        client = ReservationClient(credentials=self.mock_creds, http_pool=custom_pool)
        self.assertEqual(client.maxsize, 42)

        # Verify fallback to maxsize parameter when mock or pool without connection_pool_kw is passed
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        client_mock = ReservationClient(credentials=self.mock_creds, http_pool=mock_pool, maxsize=20)
        self.assertEqual(client_mock.maxsize, 20)

        # Verify fallback when pool object has no connection_pool_kw attribute
        class CustomPoolWithoutKw:
            pass

        client_no_kw = ReservationClient(
            credentials=self.mock_creds, http_pool=CustomPoolWithoutKw(), maxsize=15
        )
        self.assertEqual(client_no_kw.maxsize, 15)

        # Verify fallback when connection_pool_kw has maxsize set to None (no TypeError)
        none_pool = MagicMock()
        none_pool.connection_pool_kw = {"maxsize": None}
        client_none = ReservationClient(
            credentials=self.mock_creds, http_pool=none_pool, maxsize=16
        )
        self.assertEqual(client_none.maxsize, 16)

        # Verify fallback when connection_pool_kw has non-int maxsize (no TypeError)
        str_pool = MagicMock()
        str_pool.connection_pool_kw = {"maxsize": "invalid"}
        client_str = ReservationClient(
            credentials=self.mock_creds, http_pool=str_pool, maxsize=18
        )
        self.assertEqual(client_str.maxsize, 18)

        # Verify fallback when connection_pool_kw has float/list maxsize (no TypeError)
        float_pool = MagicMock()
        float_pool.connection_pool_kw = {"maxsize": 12.5}
        client_float = ReservationClient(
            credentials=self.mock_creds, http_pool=float_pool, maxsize=22
        )
        self.assertEqual(client_float.maxsize, 22)

    def test_reservation_client_warns_on_default_poolmanager_without_maxsize(self):
        # A default urllib3.PoolManager has no explicit maxsize in connection_pool_kw (urllib3 defaults to maxsize=1)
        pool = urllib3.PoolManager()
        self.assertNotIn("maxsize", pool.connection_pool_kw)
        with self.assertLogs("cleaner.reservation_client", level="WARNING") as cm:
            client = ReservationClient(credentials=self.mock_creds, http_pool=pool)
        self.assertTrue(
            any(
                "does not have an explicit maxsize configured" in msg
                for msg in cm.output
            )
        )
        self.assertEqual(client.maxsize, 1)

        # When pool has explicit maxsize configured, no warning is logged
        pool_with_maxsize = urllib3.PoolManager(maxsize=10)
        with self.assertNoLogs("cleaner.reservation_client", level="WARNING"):
            ReservationClient(credentials=self.mock_creds, http_pool=pool_with_maxsize)

        # Subclass of urllib3.PoolManager without explicit maxsize also triggers warning (PEP 8 isinstance check)
        class CustomPoolManager(urllib3.PoolManager):
            pass

        custom_pool = CustomPoolManager()
        with self.assertLogs("cleaner.reservation_client", level="WARNING") as cm_subclass:
            subclass_client = ReservationClient(credentials=self.mock_creds, http_pool=custom_pool)
        self.assertTrue(
            any(
                "does not have an explicit maxsize configured" in msg
                for msg in cm_subclass.output
            )
        )
        self.assertEqual(subclass_client.maxsize, 1)

        # Mock / MagicMock with spec=urllib3.PoolManager does NOT trigger warning
        for mock_obj in (
            MagicMock(spec=urllib3.PoolManager),
            Mock(spec=urllib3.PoolManager),
            unittest.mock.create_autospec(urllib3.PoolManager),
        ):
            with self.subTest(mock_type=type(mock_obj).__name__):
                with self.assertNoLogs("cleaner.reservation_client", level="WARNING"):
                    mock_client = ReservationClient(
                        credentials=self.mock_creds, http_pool=mock_obj, maxsize=15
                    )
                    self.assertEqual(mock_client.maxsize, 15)

    def test_reservation_client_autospec_poolmanager_detected_as_mock(self):
        # When a test uses unittest.mock.create_autospec(urllib3.PoolManager),
        # hasattr(http_pool, "mock_add_spec") detects it as a mock, no warning is logged,
        # and client.maxsize does not get forced to 1.
        autospec_pool = unittest.mock.create_autospec(urllib3.PoolManager)
        self.assertTrue(hasattr(autospec_pool, "mock_add_spec"))
        with self.assertNoLogs("cleaner.reservation_client", level="WARNING"):
            client = ReservationClient(
                credentials=self.mock_creds, http_pool=autospec_pool, maxsize=20
            )
        self.assertEqual(client.maxsize, 20)

        # Also verify when maxsize is not explicitly passed (falls back to DEFAULT_POOL_SIZE)
        with self.assertNoLogs("cleaner.reservation_client", level="WARNING"):
            client_default = ReservationClient(
                credentials=self.mock_creds, http_pool=autospec_pool
            )
        self.assertEqual(client_default.maxsize, 10)

        # Also verify with instance=True (which produces NonCallableMagicMock)
        autospec_instance = unittest.mock.create_autospec(
            urllib3.PoolManager, instance=True
        )
        self.assertTrue(hasattr(autospec_instance, "mock_add_spec"))
        with self.assertNoLogs("cleaner.reservation_client", level="WARNING"):
            client_inst = ReservationClient(
                credentials=self.mock_creds, http_pool=autospec_instance, maxsize=15
            )
        self.assertEqual(client_inst.maxsize, 15)

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

    def test_protected_reservation_via_name_keyword(self):
        """Reservations with keep-alive, do-not-delete, or permanent in name are retained."""
        res_name_protected = {
            "id": "1005b",
            "name": "res-keep-alive-team",
            "zone": "us-central1-a",
            "specificReservation": {"count": "2", "inUseCount": "0"},
        }
        eval_res = self.processor.evaluate_reservation(res_name_protected, now=self.ref_now)
        self.assertEqual(eval_res["status"], "Protected")
        self.assertFalse(eval_res["is_candidate"])
        self.assertEqual(eval_res["action"], "retained_protected")
        self.assertIn("in reservation name", eval_res["reason"])
        self.mock_client.query_reservation_usage.assert_not_called()
        self.mock_client.delete_reservation.assert_not_called()

    def test_protected_reservation_via_whitelist_names(self):
        """Reservations matching whitelist_names config are retained."""
        self.config.whitelist_names = ["custom-static-res"]
        res_whitelist = {
            "id": "1005c",
            "name": "custom-static-res",
            "zone": "us-central1-a",
            "specificReservation": {"count": "2", "inUseCount": "0"},
        }
        eval_res = self.processor.evaluate_reservation(res_whitelist, now=self.ref_now)
        self.assertEqual(eval_res["status"], "Protected")
        self.assertFalse(eval_res["is_candidate"])
        self.assertEqual(eval_res["action"], "retained_protected")
        self.assertIn("whitelist name match", eval_res["reason"])
        self.mock_client.query_reservation_usage.assert_not_called()
        self.mock_client.delete_reservation.assert_not_called()

    def test_protected_reservation_via_description_keyword_and_flag(self):
        """Reservations with description containing protection keywords or auto-delete=false are retained."""
        res_desc_1 = {
            "id": "1005d",
            "name": "my-compute-res",
            "zone": "us-central1-a",
            "description": "Production reservation - do-not-delete under any circumstances",
            "specificReservation": {"count": "2", "inUseCount": "0"},
        }
        res_desc_2 = {
            "id": "1005e",
            "name": "staging-res",
            "zone": "us-central1-a",
            "description": "auto-delete=false for QA environment",
            "specificReservation": {"count": "2", "inUseCount": "0"},
        }
        eval1 = self.processor.evaluate_reservation(res_desc_1, now=self.ref_now)
        eval2 = self.processor.evaluate_reservation(res_desc_2, now=self.ref_now)
        self.assertEqual(eval1["status"], "Protected")
        self.assertEqual(eval1["action"], "retained_protected")
        self.assertEqual(eval2["status"], "Protected")
        self.assertEqual(eval2["action"], "retained_protected")
        self.mock_client.query_reservation_usage.assert_not_called()
        self.mock_client.delete_reservation.assert_not_called()

    def test_protected_reservation_via_resource_manager_tags(self):
        """Reservations with params.resource_manager_tags containing protection keywords are retained."""
        res_rm_tags = {
            "id": "1005f",
            "name": "untagged-name-res",
            "zone": "us-central1-a",
            "params": {
                "resourceManagerTags": {
                    "tagKeys/12345": "permanent",
                }
            },
            "specificReservation": {"count": "2", "inUseCount": "0"},
        }
        eval_res = self.processor.evaluate_reservation(res_rm_tags, now=self.ref_now)
        self.assertEqual(eval_res["status"], "Protected")
        self.assertFalse(eval_res["is_candidate"])
        self.assertEqual(eval_res["action"], "retained_protected")
        self.assertIn("resource manager tag", eval_res["reason"])
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
        self.mock_client.maxsize = 10
        self.service = ReservationCleanerService(self.config, client=self.mock_client)
        self.ref_now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)

    def test_service_client_initialization_with_effective_pool_size(self):
        # Service instantiates client with effective_pool_maxsize matching workers
        cfg_workers = CleanerConfig(project_id="test-fleet-project", max_workers=16)
        service = ReservationCleanerService(cfg_workers)
        self.assertEqual(service.client.maxsize, 16)
        self.assertEqual(service.client.http_pool.connection_pool_kw.get("maxsize"), 16)

    def test_service_warns_when_client_maxsize_less_than_max_workers(self):
        # Warning is logged when client.maxsize < config.max_workers
        client = ReservationClient(credentials=MagicMock(), maxsize=2)
        self.assertLess(client.maxsize, self.config.max_workers)
        with self.assertLogs("cleaner.service", level="WARNING") as cm:
            ReservationCleanerService(self.config, client=client)
        self.assertTrue(
            any(
                "Provided ReservationClient maxsize (2) is less than max_workers (4)" in msg
                for msg in cm.output
            )
        )

    def test_service_warns_when_client_none_and_pool_maxsize_less_than_max_workers(self):
        # When client is None and config.pool_maxsize < config.max_workers,
        # ReservationCleanerService initializes ReservationClient with effective_pool_maxsize
        # and logs a warning about connection pool overflow.
        cfg = CleanerConfig(
            project_id="test-fleet-project",
            max_workers=6,
            pool_maxsize=2,
        )
        with self.assertLogs("cleaner.service", level="WARNING") as cm:
            service = ReservationCleanerService(cfg, client=None)
        self.assertEqual(service.client.maxsize, 2)
        self.assertTrue(
            any(
                "Provided ReservationClient maxsize (2) is less than max_workers (6)" in msg
                for msg in cm.output
            )
        )

        # Also verify when client argument is omitted entirely (defaults to None)
        with self.assertLogs("cleaner.service", level="WARNING") as cm2:
            service_default = ReservationCleanerService(cfg)
        self.assertEqual(service_default.client.maxsize, 2)
        self.assertTrue(
            any(
                "Provided ReservationClient maxsize (2) is less than max_workers (6)" in msg
                for msg in cm2.output
            )
        )

    def test_service_warns_when_default_poolmanager_client_passed_with_concurrent_workers(self):
        # A default urllib3.PoolManager without explicit maxsize sets client.maxsize to 1.
        # Passing this client to ReservationCleanerService when max_workers > 1 triggers a warning.
        self.assertGreater(self.config.max_workers, 1)
        default_pool = urllib3.PoolManager()
        with self.assertLogs("cleaner.reservation_client", level="WARNING"):
            client = ReservationClient(credentials=MagicMock(), http_pool=default_pool)
        self.assertEqual(client.maxsize, 1)
        with self.assertLogs("cleaner.service", level="WARNING") as cm:
            ReservationCleanerService(self.config, client=client)
        self.assertTrue(
            any(
                f"Provided ReservationClient maxsize (1) is less than max_workers ({self.config.max_workers})" in msg
                for msg in cm.output
            )
        )

    def test_service_no_warning_when_client_maxsize_greater_or_equal_to_max_workers(self):
        # Warning is NOT logged when maxsize >= config.max_workers
        # 1. client.maxsize == config.max_workers (4 == 4)
        client_equal = ReservationClient(credentials=MagicMock(), maxsize=self.config.max_workers)
        with self.assertNoLogs("cleaner.service", level="WARNING"):
            ReservationCleanerService(self.config, client=client_equal)

        # 2. client.maxsize > config.max_workers (8 > 4)
        client_larger = ReservationClient(credentials=MagicMock(), maxsize=self.config.max_workers + 4)
        with self.assertNoLogs("cleaner.service", level="WARNING"):
            ReservationCleanerService(self.config, client=client_larger)

        # Also verify via assertLogs that no WARNING logs are triggered
        with self.assertRaises(AssertionError):
            with self.assertLogs("cleaner.service", level="WARNING"):
                ReservationCleanerService(self.config, client=client_equal)

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

    def test_service_run_partial_error_status(self):
        mock_reservations = [
            {
                "id": "1",
                "name": "res-ok",
                "zone": "us-central1-a",
                "specificReservation": {"count": "1", "inUseCount": "1"},
            },
            {
                "id": "2",
                "name": "res-fail",
                "zone": "us-central1-a",
                "specificReservation": {"count": "1", "inUseCount": "0"},
            },
        ]
        self.mock_client.list_aggregated_reservations.return_value = mock_reservations
        self.mock_client.query_reservation_usage.side_effect = RuntimeError("Quota exceeded")

        result = self.service.run(reference_time=self.ref_now)
        self.assertEqual(result["status"], "partial_error")
        self.assertGreater(len(result["errors"]), 0)

    def test_service_run_raise_on_error_consolidated_exception(self):
        mock_reservations = [
            {
                "id": "1",
                "name": "res-fail",
                "zone": "us-central1-a",
                "specificReservation": {"count": "1", "inUseCount": "0"},
            },
        ]
        self.mock_client.list_aggregated_reservations.return_value = mock_reservations
        self.mock_client.query_reservation_usage.side_effect = RuntimeError("Backend failure")

        with self.assertRaises(RuntimeError) as ctx:
            self.service.run(reference_time=self.ref_now, raise_on_error=True)
        self.assertIn("Consolidated cleanup failures", str(ctx.exception))


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
