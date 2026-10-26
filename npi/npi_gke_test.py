import io
import unittest
from unittest.mock import patch, mock_open
import yaml
import npi_gke

class TestCreateJobSpec(unittest.TestCase):

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_no_resources_in_template(self, mock_file, mock_yaml_load):
        # Template container has no "resources" key at all
        template_spec = {
            "metadata": {"name": "template"},
            "spec": {
                "template": {
                    "metadata": {"labels": {"app": "test"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "benchmark",
                                "image": "placeholder",
                                "args": []
                            }
                        ],
                        "volumes": []
                    }
                }
            }
        }
        mock_yaml_load.return_value = template_spec

        res = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--arg1"],
            bucket_name="test-bucket",
            service_account="test-sa",
            resources_limits={"cpu": "4", "memory": "8Gi"}
        )

        container = res["spec"]["template"]["spec"]["containers"][0]
        self.assertIn("resources", container)
        self.assertIn("limits", container["resources"])
        self.assertEqual(container["resources"]["limits"]["cpu"], "4")
        self.assertEqual(container["resources"]["limits"]["memory"], "8Gi")

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_resources_null(self, mock_file, mock_yaml_load):
        # Template container has "resources": None (yaml parsed null)
        template_spec = {
            "metadata": {"name": "template"},
            "spec": {
                "template": {
                    "metadata": {"labels": {"app": "test"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "benchmark",
                                "image": "placeholder",
                                "args": [],
                                "resources": None
                            }
                        ],
                        "volumes": []
                    }
                }
            }
        }
        mock_yaml_load.return_value = template_spec

        res = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--arg1"],
            bucket_name="test-bucket",
            service_account="test-sa",
            resources_limits={"cpu": "4"}
        )

        container = res["spec"]["template"]["spec"]["containers"][0]
        self.assertIn("resources", container)
        self.assertIn("limits", container["resources"])
        self.assertEqual(container["resources"]["limits"]["cpu"], "4")

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_limits_null(self, mock_file, mock_yaml_load):
        # Template container has "resources": {"limits": None}
        template_spec = {
            "metadata": {"name": "template"},
            "spec": {
                "template": {
                    "metadata": {"labels": {"app": "test"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "benchmark",
                                "image": "placeholder",
                                "args": [],
                                "resources": {
                                    "limits": None
                                }
                            }
                        ],
                        "volumes": []
                    }
                }
            }
        }
        mock_yaml_load.return_value = template_spec

        res = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--arg1"],
            bucket_name="test-bucket",
            service_account="test-sa",
            resources_limits={"cpu": "4"}
        )

        container = res["spec"]["template"]["spec"]["containers"][0]
        self.assertIn("resources", container)
        self.assertIn("limits", container["resources"])
        self.assertEqual(container["resources"]["limits"]["cpu"], "4")

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_numjobs_rapid_bucket(self, mock_file, mock_yaml_load):
        template_spec = {
            "metadata": {"name": "template"},
            "spec": {
                "template": {
                    "metadata": {"labels": {"app": "test"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "benchmark",
                                "image": "placeholder",
                                "args": []
                            }
                        ],
                        "volumes": []
                    }
                }
            }
        }
        mock_yaml_load.return_value = template_spec

        res = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--iterations=1"],
            bucket_name="test-bucket",
            service_account="test-sa",
            is_rapid_bucket=True
        )

        container = res["spec"]["template"]["spec"]["containers"][0]
        env_vars = {e["name"]: e["value"] for e in container.get("env", [])}
        self.assertEqual(env_vars.get("NUMJOBS"), "112")
        self.assertEqual(env_vars.get("DIRECT"), "0")

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_numjobs_standard(self, mock_file, mock_yaml_load):
        template_spec = {
            "metadata": {"name": "template"},
            "spec": {
                "template": {
                    "metadata": {"labels": {"app": "test"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "benchmark",
                                "image": "placeholder",
                                "args": []
                            }
                        ],
                        "volumes": []
                    }
                }
            }
        }
        mock_yaml_load.return_value = template_spec

        res = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--iterations=1"],
            bucket_name="test-bucket",
            service_account="test-sa",
            is_rapid_bucket=False
        )

        container = res["spec"]["template"]["spec"]["containers"][0]
        env_vars = {e["name"]: e["value"] for e in container.get("env", [])}
        self.assertEqual(env_vars.get("NUMJOBS"), "112")
        self.assertEqual(env_vars.get("DIRECT"), "1")

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_numjobs_smoke_mode(self, mock_file, mock_yaml_load):
        template_spec = {
            "metadata": {"name": "template"},
            "spec": {
                "template": {
                    "metadata": {"labels": {"app": "test"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "benchmark",
                                "image": "placeholder",
                                "args": []
                            }
                        ],
                        "volumes": []
                    }
                }
            }
        }
        mock_yaml_load.return_value = template_spec

        res = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--numjobs=2"],
            bucket_name="test-bucket",
            service_account="test-sa",
            is_rapid_bucket=True
        )

        container = res["spec"]["template"]["spec"]["containers"][0]
        env_vars = {e["name"]: e["value"] for e in container.get("env", [])}
        self.assertEqual(env_vars.get("NUMJOBS"), "2")

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_numjobs_explicit_override(self, mock_file, mock_yaml_load):
        template_spec = {
            "metadata": {"name": "template"},
            "spec": {
                "template": {
                    "metadata": {"labels": {"app": "test"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "benchmark",
                                "image": "placeholder",
                                "args": []
                            }
                        ],
                        "volumes": []
                    }
                }
            }
        }
        mock_yaml_load.return_value = template_spec

        res = npi_gke.create_job_spec(
            job_name="test-job",
            image="test-image",
            args=["--numjobs=64"],
            bucket_name="test-bucket",
            service_account="test-sa",
            is_rapid_bucket=True
        )

        container = res["spec"]["template"]["spec"]["containers"][0]
        env_vars = {e["name"]: e["value"] for e in container.get("env", [])}
        self.assertEqual(env_vars.get("NUMJOBS"), "64")

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_direct_and_two_token_overrides(self, mock_file, mock_yaml_load):
        template_spec = {
            "metadata": {"name": "template"},
            "spec": {
                "template": {
                    "metadata": {"labels": {"app": "test"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "benchmark",
                                "image": "placeholder",
                                "args": [],
                                "env": [
                                    {"name": "EXISTING", "value": "ok"},
                                    {"name": "NUMJOBS", "value": "48"},
                                    {"name": "DIRECT", "value": "1"},
                                ],
                            }
                        ],
                        "volumes": []
                    }
                }
            }
        }
        mock_yaml_load.return_value = template_spec

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
        self.assertEqual(env_vars.get("EXISTING"), "ok")
        self.assertEqual(env_vars.get("NUMJOBS"), "32")
        self.assertEqual(env_vars.get("DIRECT"), "0")

    @patch("npi_gke.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open)
    def test_create_job_spec_invalid_numjobs_or_direct_raises(self, mock_file, mock_yaml_load):
        def make_template():
            return {
                "metadata": {"name": "template"},
                "spec": {
                    "template": {
                        "metadata": {"labels": {"app": "test"}},
                        "spec": {
                            "containers": [{"name": "benchmark", "image": "placeholder", "args": []}],
                            "volumes": [],
                        },
                    }
                },
            }

        for bad_args in (["--numjobs=0"], ["--numjobs=-4"], ["--direct=2"], ["--direct="]):
            mock_yaml_load.return_value = make_template()
            with self.assertRaises(ValueError):
                npi_gke.create_job_spec(
                    job_name="test-job",
                    image="test-image",
                    args=bad_args,
                    bucket_name="test-bucket",
                    service_account="test-sa",
                )


class TestGKEMainAndValidators(unittest.TestCase):

    def test_positive_int_validator(self):
        import argparse
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
        with patch("sys.argv", base_argv), \
             patch("npi_gke.subprocess.run", return_value=mock_cred_res), \
             patch("npi_gke.setup_kubernetes_service_account", return_value=True), \
             patch("npi_gke.create_job_spec", side_effect=spy_create), \
             patch("sys.stdout", new_callable=io.StringIO):
            npi_gke.main()
        return captured

    def test_main_default_standard_and_rapid_omit_cli_numjobs_and_direct(self):
        # Standard bucket
        std_specs = self._run_dry_run_and_capture_specs(["--benchmarks", "read_grpc", "go_read_grpc"])
        self.assertEqual(len(std_specs), 2)
        for (_, _, spec) in std_specs:
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
        for (_, _, spec) in rapid_specs:
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
            ["--smoke-mode", "--is-rapid-bucket", "--benchmarks", "read_grpc", "go_read_grpc"]
        )
        self.assertEqual(len(smoke_specs), 2)
        for (_, _, spec) in smoke_specs:
            container = spec["spec"]["template"]["spec"]["containers"][0]
            self.assertIn("--numjobs=2", container["args"])
            env_vars = {e["name"]: e["value"] for e in container["env"]}
            self.assertEqual(env_vars["NUMJOBS"], "2")
            self.assertEqual(env_vars["DIRECT"], "0")

        # Smoke mode + explicit --numjobs 64 -> --numjobs=64 wins
        override_specs = self._run_dry_run_and_capture_specs(
            ["--smoke-mode", "--numjobs", "64", "--is-rapid-bucket", "--benchmarks", "read_grpc", "go_read_grpc"]
        )
        self.assertEqual(len(override_specs), 2)
        for (_, _, spec) in override_specs:
            container = spec["spec"]["template"]["spec"]["containers"][0]
            self.assertIn("--numjobs=64", container["args"])
            self.assertNotIn("--numjobs=2", container["args"])
            env_vars = {e["name"]: e["value"] for e in container["env"]}
            self.assertEqual(env_vars["NUMJOBS"], "64")
            self.assertEqual(env_vars["DIRECT"], "0")

class TestGKEUtils(unittest.TestCase):

    @patch("builtins.open", new_callable=mock_open, read_data="MemTotal:        65536000 kB\n")
    def test_get_host_total_ram_mb(self, mock_file):
        ram_mb = npi_gke.get_host_total_ram_mb()
        self.assertEqual(ram_mb, 64000)

    def test_parse_key_value_pairs(self):
        kv_dict = npi_gke.parse_key_value_pairs("cloud.google.com/gke-nodepool=npi-pool,env=prod")
        self.assertEqual(kv_dict, {"cloud.google.com/gke-nodepool": "npi-pool", "env": "prod"})
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
            dry_run=False
        )
        self.assertTrue(ok)
        storage_calls = [
            c.args[0] for c in mock_run.call_args_list
            if c.args[0][:4] == ["gcloud", "storage", "buckets", "add-iam-policy-binding"]
        ]
        self.assertEqual(len(storage_calls), 2)
        members = [arg.split("=", 1)[1] for call in storage_calls for arg in call if arg.startswith("--member=")]
        self.assertIn(
            "principal://iam.googleapis.com/projects/123456789/locations/global/workloadIdentityPools/my-proj.svc.id.goog/subject/ns/default/sa/gcsfuse-npi-ksa",
            members
        )
        self.assertIn("serviceAccount:my-gsa@my-proj.iam.gserviceaccount.com", members)

    @patch("sys.stderr", new_callable=io.StringIO)
    @patch("npi_gke.subprocess.run")
    def test_setup_kubernetes_service_account_get_sa_failure(self, mock_run, mock_stderr):
        def side_effect(cmd, **kwargs):
            res = unittest.mock.MagicMock()
            res.returncode = 0
            res.stderr = ""
            res.stdout = ""
            if cmd[:3] == ["gcloud", "projects", "describe"]:
                res.stdout = "123456789\n"
            elif cmd[:3] == ["kubectl", "get", "serviceaccount"]:
                res.returncode = 1
                res.stderr = "Error from server (Forbidden): serviceaccounts is forbidden\n"
            return res

        mock_run.side_effect = side_effect
        ok = npi_gke.setup_kubernetes_service_account(
            project_id="my-proj",
            ksa_name="gcsfuse-npi-ksa",
            namespace="default",
            buckets=["my-bucket"],
            dry_run=False
        )
        self.assertTrue(ok)
        self.assertIn(
            "Warning: Failed to retrieve serviceaccount gcsfuse-npi-ksa: Error from server (Forbidden): serviceaccounts is forbidden",
            mock_stderr.getvalue()
        )
        storage_calls = [
            c.args[0] for c in mock_run.call_args_list
            if c.args[0][:4] == ["gcloud", "storage", "buckets", "add-iam-policy-binding"]
        ]
        self.assertEqual(len(storage_calls), 1)
        members = [arg.split("=", 1)[1] for call in storage_calls for arg in call if arg.startswith("--member=")]
        self.assertEqual(
            members,
            ["principal://iam.googleapis.com/projects/123456789/locations/global/workloadIdentityPools/my-proj.svc.id.goog/subject/ns/default/sa/gcsfuse-npi-ksa"]
        )


if __name__ == "__main__":
    unittest.main()


