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
        self.assertEqual(env_vars.get("NUMJOBS"), "48")

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
                res.stdout = "my-gsa@my-proj.iam.gserviceaccount.com"
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


if __name__ == "__main__":
    unittest.main()


