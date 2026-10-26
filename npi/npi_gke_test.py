import argparse
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import mock_open, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import npi_gke

_OMIT = object()


def _make_job_template(*, env=_OMIT, resources=_OMIT):
    """Builds a minimal Kubernetes Job template dict for create_job_spec tests."""
    container = {
        "name": "benchmark",
        "image": "placeholder",
        "args": [],
    }
    if env is not _OMIT:
        container["env"] = env
    if resources is not _OMIT:
        container["resources"] = resources
    return {
        "metadata": {"name": "template"},
        "spec": {
            "template": {
                "metadata": {"labels": {"app": "test"}},
                "spec": {
                    "containers": [container],
                    "volumes": [],
                },
            }
        },
    }


class TestCreateJobSpec(unittest.TestCase):

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_no_resources_in_template(self, mock_file, mock_yaml_load):
        mock_yaml_load.return_value = _make_job_template()

        res = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--arg1"],
            bucket_name="test-bucket",
            service_account="test-sa",
            resources_limits={"cpu": "4", "memory": "8Gi"},
        )

        container = res["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(
            container["resources"]["limits"], {"cpu": "4", "memory": "8Gi"}
        )

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_resources_null(self, mock_file, mock_yaml_load):
        mock_yaml_load.return_value = _make_job_template(resources=None)

        res = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--arg1"],
            bucket_name="test-bucket",
            service_account="test-sa",
            resources_limits={"cpu": "4"},
        )

        container = res["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["resources"]["limits"]["cpu"], "4")

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_limits_null(self, mock_file, mock_yaml_load):
        mock_yaml_load.return_value = _make_job_template(
            resources={"limits": None}
        )

        res = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--arg1"],
            bucket_name="test-bucket",
            service_account="test-sa",
            resources_limits={"cpu": "4"},
        )

        container = res["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["resources"]["limits"]["cpu"], "4")

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_defaults_for_rapid_and_standard_buckets(
        self, mock_file, mock_yaml_load
    ):
        for is_rapid, expected_direct in ((True, "0"), (False, "1")):
            mock_yaml_load.return_value = _make_job_template()
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                res = npi_gke.create_job_spec(
                    job_name="test-job",
                    image="test-image",
                    args=["--iterations=1"],
                    bucket_name="test-bucket",
                    service_account="test-sa",
                    node_selector={"pool": "npi"},
                    is_rapid_bucket=is_rapid,
                )
            self.assertEqual(mock_stdout.getvalue(), "")
            container = res["spec"]["template"]["spec"]["containers"][0]
            env_vars = {e["name"]: e["value"] for e in container.get("env", [])}
            self.assertEqual(env_vars.get("NUMJOBS"), "112")
            self.assertEqual(env_vars.get("DIRECT"), expected_direct)
            self.assertEqual(
                res["spec"]["template"]["spec"]["nodeSelector"], {"pool": "npi"}
            )

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_explicit_kwargs_and_args_precedence(
        self, mock_file, mock_yaml_load
    ):
        # 1. Explicit numjobs/direct kwargs override is_rapid_bucket defaults
        mock_yaml_load.return_value = _make_job_template()
        res_kw = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--iterations=1"],
            bucket_name="test-bucket",
            service_account="test-sa",
            is_rapid_bucket=True,
            numjobs=48,
            direct="1",
        )
        env_kw = {
            e["name"]: e["value"]
            for e in res_kw["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(env_kw["NUMJOBS"], "48")
        self.assertEqual(env_kw["DIRECT"], "1")

        # 2. Explicit CLI flags in args override numjobs/direct kwargs
        mock_yaml_load.return_value = _make_job_template()
        res_cli = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--numjobs=64", "--direct=0"],
            bucket_name="test-bucket",
            service_account="test-sa",
            is_rapid_bucket=False,
            numjobs=48,
            direct="1",
        )
        env_cli = {
            e["name"]: e["value"]
            for e in res_cli["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(env_cli["NUMJOBS"], "64")
        self.assertEqual(env_cli["DIRECT"], "0")

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_strips_legacy_num_jobs_and_deduplicates_env(
        self, mock_file, mock_yaml_load
    ):
        mock_yaml_load.return_value = _make_job_template(
            env=[
                {"name": "EXISTING", "value": "ok"},
                {"name": "NUMJOBS", "value": "48"},
                {"name": "NUM_JOBS", "value": "96"},
                {"name": "DIRECT", "value": "1"},
            ]
        )

        res = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--numjobs", "2", "--numjobs", "32", "--direct=0"],
            bucket_name="test-bucket",
            service_account="test-sa",
            is_rapid_bucket=False,
        )

        container = res["spec"]["template"]["spec"]["containers"][0]
        env_list = container.get("env", [])
        self.assertEqual([e["name"] for e in env_list].count("NUMJOBS"), 1)
        self.assertEqual([e["name"] for e in env_list].count("DIRECT"), 1)
        env_vars = {e["name"]: e["value"] for e in env_list}
        self.assertNotIn("NUM_JOBS", env_vars)
        self.assertEqual(env_vars.get("EXISTING"), "ok")
        self.assertEqual(env_vars.get("NUMJOBS"), "32")
        self.assertEqual(env_vars.get("DIRECT"), "0")

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_option_value_arity_and_invalid_inputs(
        self, mock_file, mock_yaml_load
    ):
        for bad_args in (
            ["--numjobs=0"],
            ["--numjobs=-4"],
            ["--direct=2"],
            ["--direct="],
            ["--numjobs"],
            ["--iterations=1", "--numjobs"],
            ["--numjobs=32", "--numjobs"],
            ["--direct"],
            ["--direct=0", "--direct"],
            ["--numjobs=0", "--numjobs=32"],
            ["--direct=2", "--direct=0"],
            ["--numjobs", "--numjobs", "32"],
            ["--numjobs", "--numjobs=32"],
            ["--direct", "--numjobs=32", "--direct=0"],
            ["--gcsfuse-flags"],
            ["--num=32"],  # unknown/abbreviated flags are not treated as "--numjobs"
            [123],
        ):
            mock_yaml_load.return_value = _make_job_template()
            if bad_args == ["--num=32"]:
                res_unknown = npi_gke.create_job_spec(
                    job_name="test-job",
                    image="test-image",
                    args=bad_args,
                    bucket_name="test-bucket",
                    service_account="test-sa",
                )
                env_unknown = {
                    e["name"]: e["value"]
                    for e in res_unknown["spec"]["template"]["spec"][
                        "containers"
                    ][0]["env"]
                }
                self.assertEqual(env_unknown["NUMJOBS"], "112")
                continue

            with self.assertRaises(ValueError, msg=f"bad_args={bad_args!r}"):
                npi_gke.create_job_spec(
                    job_name="test-job",
                    image="test-image",
                    args=bad_args,
                    bucket_name="test-bucket",
                    service_account="test-sa",
                )

        # Invalid explicit numjobs / direct kwargs also raise ValueError
        for bad_kw in (
            {"numjobs": 0},
            {"numjobs": True},
            {"direct": "2"},
            {"direct": False},
        ):
            mock_yaml_load.return_value = _make_job_template()
            with self.assertRaises(ValueError):
                npi_gke.create_job_spec(
                    job_name="test-job",
                    image="test-image",
                    args=[],
                    bucket_name="test-bucket",
                    service_account="test-sa",
                    **bad_kw,
                )

        # Container value-taking options (e.g. --gcsfuse-flags) consume dash-prefixed values cleanly
        for flag_val in ("--direct=0", "--client-protocol=grpc"):
            mock_yaml_load.return_value = _make_job_template()
            res_arity = npi_gke.create_job_spec(
                job_name="test-job",
                image="test-image",
                args=["--gcsfuse-flags", flag_val],
                bucket_name="test-bucket",
                service_account="test-sa",
                is_rapid_bucket=False,
            )
            env_arity = {
                e["name"]: e["value"]
                for e in res_arity["spec"]["template"]["spec"]["containers"][0][
                    "env"
                ]
            }
            self.assertEqual(env_arity["DIRECT"], "1")
            self.assertEqual(env_arity["NUMJOBS"], "112")

        # "--client-protocol" is registered only when is_go_client=True
        self.assertEqual(
            npi_gke._resolve_job_env(
                ["--client-protocol", "grpc", "--numjobs=16"],
                is_go_client=True,
            ),
            ("16", "1"),
        )
        self.assertEqual(
            npi_gke._resolve_job_env(
                ["--client-protocol", "--numjobs=16"],
                is_go_client=False,
            ),
            ("16", "1"),
        )

        # "--" stops option scanning just like argparse
        mock_yaml_load.return_value = _make_job_template()
        res = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--", "--numjobs=0"],
            bucket_name="test-bucket",
            service_account="test-sa",
            is_rapid_bucket=True,
        )
        env_vars = {
            e["name"]: e["value"]
            for e in res["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(env_vars["NUMJOBS"], "112")
        self.assertEqual(env_vars["DIRECT"], "0")


class TestGKEMainAndValidators(unittest.TestCase):

    def test_positive_int_validator(self):
        self.assertEqual(npi_gke.positive_int("64"), 64)
        self.assertEqual(npi_gke.positive_int(112), 112)
        for bad in ("0", "-4", "abc", "", "1.5", 0, -1, False, True, 1.5):
            with self.assertRaises(argparse.ArgumentTypeError):
                npi_gke.positive_int(bad)

    def _run_dry_run_and_capture_specs(self, extra_cli_args):
        captured = []
        real_create = npi_gke.create_job_spec

        def spy_create(*args, **kwargs):
            spec = real_create(*args, **kwargs)
            captured.append((args, kwargs, spec))
            return spec

        base_argv = [
            "npi_gke.py",
            "--cluster-name=test-cluster",
            "--location=us-central1-a",
            "--bucket-name=test-bucket",
            "--project-id=test-proj",
            "--bq-dataset-id=test_ds",
            "--dry-run",
        ] + extra_cli_args

        mock_cred_res = unittest.mock.MagicMock(returncode=0, stdout="ok", stderr="")
        with contextlib.ExitStack() as stack:
            if not npi_gke.YAML_SUPPORTED:
                stack.enter_context(patch("builtins.open", mock_open()))
                stack.enter_context(
                    patch(
                        "npi_gke.yaml.safe_load",
                        side_effect=lambda *_: _make_job_template(),
                    )
                )
            stack.enter_context(patch("sys.argv", base_argv))
            stack.enter_context(
                patch("npi_gke.subprocess.run", return_value=mock_cred_res)
            )
            stack.enter_context(
                patch(
                    "npi_gke.setup_kubernetes_service_account",
                    return_value=True,
                )
            )
            stack.enter_context(
                patch("npi_gke.create_job_spec", side_effect=spy_create)
            )
            stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
            npi_gke.main()
        return captured

    def test_main_rejects_invalid_benchmarks_and_rapid_http1_before_cluster_calls(
        self,
    ):
        for bad_cli, expected_code in (
            (["--is-rapid-bucket", "--benchmarks", "read_http1"], 2),
            (["--benchmarks", "bogus"], 1),
            (["--benchmarks", "read_file_cache_grpc"], 2),
        ):
            argv = [
                "npi_gke.py",
                "--cluster-name=test-cluster",
                "--location=us-central1-a",
                "--bucket-name=test-bucket",
                "--project-id=test-proj",
                "--bq-dataset-id=test_ds",
            ] + bad_cli
            with patch("sys.argv", argv), patch(
                "npi_gke.subprocess.run"
            ) as mock_subproc, patch(
                "npi_gke.setup_kubernetes_service_account"
            ) as mock_setup_ksa, patch(
                "sys.stderr", new_callable=io.StringIO
            ):
                with self.assertRaises(SystemExit) as cm:
                    npi_gke.main()
                self.assertEqual(cm.exception.code, expected_code)
                mock_subproc.assert_not_called()
                mock_setup_ksa.assert_not_called()

    def test_main_default_standard_and_rapid_omit_cli_numjobs_and_direct(self):
        # Standard bucket
        std_specs = self._run_dry_run_and_capture_specs(
            ["--benchmarks", "read_grpc", "go_read_grpc"]
        )
        self.assertEqual(len(std_specs), 2)
        for (_, kwargs, spec) in std_specs:
            self.assertEqual(kwargs["numjobs"], "112")
            self.assertEqual(kwargs["direct"], "1")
            container = spec["spec"]["template"]["spec"]["containers"][0]
            for a in container["args"]:
                self.assertFalse(a.startswith("--numjobs"))
                self.assertFalse(a.startswith("--direct"))
            env_vars = {e["name"]: e["value"] for e in container["env"]}
            self.assertEqual(env_vars["NUMJOBS"], "112")
            self.assertEqual(env_vars["DIRECT"], "1")

        # RAPID bucket
        rapid_specs = self._run_dry_run_and_capture_specs(
            ["--is-rapid-bucket", "--benchmarks", "read_grpc", "go_read_grpc"]
        )
        self.assertEqual(len(rapid_specs), 2)
        for (_, kwargs, spec) in rapid_specs:
            self.assertEqual(kwargs["numjobs"], "112")
            self.assertEqual(kwargs["direct"], "0")
            container = spec["spec"]["template"]["spec"]["containers"][0]
            for a in container["args"]:
                self.assertFalse(a.startswith("--numjobs"))
                self.assertFalse(a.startswith("--direct"))
            env_vars = {e["name"]: e["value"] for e in container["env"]}
            self.assertEqual(env_vars["NUMJOBS"], "112")
            self.assertEqual(env_vars["DIRECT"], "0")

    def test_main_smoke_mode_vs_explicit_numjobs_precedence(self):
        # Smoke mode alone -> --numjobs=2
        smoke_specs = self._run_dry_run_and_capture_specs(
            [
                "--smoke-mode",
                "--is-rapid-bucket",
                "--benchmarks",
                "read_grpc",
                "go_read_grpc",
            ]
        )
        self.assertEqual(len(smoke_specs), 2)
        for (_, kwargs, spec) in smoke_specs:
            self.assertEqual(kwargs["numjobs"], "2")
            self.assertEqual(kwargs["direct"], "0")
            container = spec["spec"]["template"]["spec"]["containers"][0]
            self.assertIn("--numjobs=2", container["args"])
            env_vars = {e["name"]: e["value"] for e in container["env"]}
            self.assertEqual(env_vars["NUMJOBS"], "2")
            self.assertEqual(env_vars["DIRECT"], "0")

        # Smoke mode + explicit --numjobs 64 -> --numjobs=64 wins
        override_specs = self._run_dry_run_and_capture_specs(
            [
                "--smoke-mode",
                "--numjobs",
                "64",
                "--is-rapid-bucket",
                "--benchmarks",
                "read_grpc",
                "go_read_grpc",
            ]
        )
        self.assertEqual(len(override_specs), 2)
        for (_, kwargs, spec) in override_specs:
            self.assertEqual(kwargs["numjobs"], "64")
            self.assertEqual(kwargs["direct"], "0")
            container = spec["spec"]["template"]["spec"]["containers"][0]
            self.assertIn("--numjobs=64", container["args"])
            self.assertNotIn("--numjobs=2", container["args"])
            env_vars = {e["name"]: e["value"] for e in container["env"]}
            self.assertEqual(env_vars["NUMJOBS"], "64")
            self.assertEqual(env_vars["DIRECT"], "0")


class TestGKEUtils(unittest.TestCase):

    def test_missing_yaml_stub_raises_runtime_error_when_unmocked(self):
        stub = npi_gke._MissingYaml()
        with self.assertRaises(RuntimeError):
            stub.safe_load("apiVersion: v1")
        with self.assertRaises(RuntimeError):
            stub.dump({"kind": "Job"})

    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data="MemTotal:        65536000 kB\n",
    )
    def test_get_host_total_ram_mb(self, mock_file):
        ram_mb = npi_gke.get_host_total_ram_mb()
        self.assertEqual(ram_mb, 64000)

    def test_parse_key_value_pairs(self):
        kv_dict = npi_gke.parse_key_value_pairs(
            "cloud.google.com/gke-nodepool=npi-pool,env=prod"
        )
        self.assertEqual(
            kv_dict,
            {"cloud.google.com/gke-nodepool": "npi-pool", "env": "prod"},
        )
        self.assertIsNone(npi_gke.parse_key_value_pairs(None))

    @patch("npi_gke.subprocess.run")
    def test_setup_kubernetes_service_account_with_annotated_gsa(self, mock_run):
        def side_effect(cmd, **kwargs):
            res = unittest.mock.MagicMock()
            res.returncode = 0
            res.stderr = ""
            res.stdout = ""
            if cmd[:3] == ["gcloud", "projects", "describe"]:
                res.stdout = "123456789\n"
            elif cmd[:3] == ["kubectl", "get", "serviceaccount"]:
                res.stdout = '{"metadata": {"annotations": {"iam.gke.io/gcp-service-account": "my-gsa@my-proj.iam.gserviceaccount.com"}}}'
            return res

        mock_run.side_effect = side_effect
        ok = npi_gke.setup_kubernetes_service_account(
            project_id="my-proj",
            ksa_name="gcsfuse-npi-ksa",
            namespace="default",
            buckets=["my-bucket"],
            dry_run=False,
        )
        self.assertTrue(ok)
        storage_calls = [
            c.args[0]
            for c in mock_run.call_args_list
            if c.args[0][:4]
            == ["gcloud", "storage", "buckets", "add-iam-policy-binding"]
        ]
        self.assertEqual(len(storage_calls), 2)
        members = [
            arg.split("=", 1)[1]
            for call in storage_calls
            for arg in call
            if arg.startswith("--member=")
        ]
        self.assertIn(
            "principal://iam.googleapis.com/projects/123456789/locations/global/workloadIdentityPools/my-proj.svc.id.goog/subject/ns/default/sa/gcsfuse-npi-ksa",
            members,
        )
        self.assertIn(
            "serviceAccount:my-gsa@my-proj.iam.gserviceaccount.com", members
        )

    @patch("sys.stderr", new_callable=io.StringIO)
    @patch("npi_gke.subprocess.run")
    def test_setup_kubernetes_service_account_get_sa_failure(
        self, mock_run, mock_stderr
    ):
        def side_effect(cmd, **kwargs):
            res = unittest.mock.MagicMock()
            res.returncode = 0
            res.stderr = ""
            res.stdout = ""
            if cmd[:3] == ["gcloud", "projects", "describe"]:
                res.stdout = "123456789\n"
            elif cmd[:3] == ["kubectl", "get", "serviceaccount"]:
                res.returncode = 1
                res.stderr = (
                    "Error from server (Forbidden): serviceaccounts is forbidden\n"
                )
            return res

        mock_run.side_effect = side_effect
        ok = npi_gke.setup_kubernetes_service_account(
            project_id="my-proj",
            ksa_name="gcsfuse-npi-ksa",
            namespace="default",
            buckets=["my-bucket"],
            dry_run=False,
        )
        self.assertTrue(ok)
        self.assertIn(
            "Warning: Failed to retrieve serviceaccount gcsfuse-npi-ksa: Error from server (Forbidden): serviceaccounts is forbidden",
            mock_stderr.getvalue(),
        )
        storage_calls = [
            c.args[0]
            for c in mock_run.call_args_list
            if c.args[0][:4]
            == ["gcloud", "storage", "buckets", "add-iam-policy-binding"]
        ]
        self.assertEqual(len(storage_calls), 1)
        members = [
            arg.split("=", 1)[1]
            for call in storage_calls
            for arg in call
            if arg.startswith("--member=")
        ]
        self.assertEqual(
            members,
            [
                "principal://iam.googleapis.com/projects/123456789/locations/global/workloadIdentityPools/my-proj.svc.id.goog/subject/ns/default/sa/gcsfuse-npi-ksa"
            ],
        )


if __name__ == "__main__":
    unittest.main()
