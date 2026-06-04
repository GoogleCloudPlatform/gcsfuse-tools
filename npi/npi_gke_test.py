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

if __name__ == "__main__":
    unittest.main()
