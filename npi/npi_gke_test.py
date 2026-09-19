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


class TestGKEConvergenceFlagPropagation(unittest.TestCase):

    @patch("npi_gke.run_benchmark_job", return_value=True)
    @patch("npi_gke.setup_kubernetes_service_account", return_value=True)
    @patch("npi_gke.subprocess.run")
    def test_main_propagates_convergence_flags_to_fio_and_go_but_not_host_info(
        self, mock_subproc, mock_setup_ksa, mock_run_job
    ):
        mock_subproc.return_value.returncode = 0
        mock_subproc.return_value.stdout = ""
        argv = [
            "npi_gke.py",
            "--cluster-name", "test-cluster",
            "--location", "us-central1-a",
            "--bucket-name", "test-bucket",
            "--project-id", "test-project",
            "--bq-dataset-id", "test_dataset",
            "--benchmarks", "host_info", "read_grpc", "go_read_grpc",
            "--min-iterations", "3",
            "--max-iterations", "8",
            "--convergence-threshold", "0.04",
            "--confidence-level", "0.90",
        ]
        with patch("sys.argv", argv):
            npi_gke.main()

        self.assertEqual(mock_run_job.call_count, 3)
        calls_by_table = {
            call.kwargs["table_id"]: call.kwargs["args_list"]
            for call in mock_run_job.call_args_list
        }

        host_args = calls_by_table["host_info"]
        for flag in ("--iterations", "--min-iterations", "--max-iterations", "--convergence-threshold", "--confidence-level"):
            self.assertFalse(any(a.startswith(flag) for a in host_args), f"Unexpected {flag} in {host_args}")

        fio_args = calls_by_table["fio_read_grpc"]
        self.assertIn("--min-iterations=3", fio_args)
        self.assertIn("--max-iterations=8", fio_args)
        self.assertIn("--convergence-threshold=0.04", fio_args)
        self.assertIn("--confidence-level=0.9", fio_args)

        go_args = calls_by_table["go_client_read_grpc"]
        self.assertIn("--min-iterations=3", go_args)
        self.assertIn("--max-iterations=8", go_args)
        self.assertIn("--convergence-threshold=0.04", go_args)
        self.assertIn("--confidence-level=0.9", go_args)

    @patch("npi_gke.run_benchmark_job", return_value=True)
    @patch("npi_gke.setup_kubernetes_service_account", return_value=True)
    @patch("npi_gke.subprocess.run")
    def test_main_legacy_mode_omits_all_convergence_flags(
        self, mock_subproc, mock_setup_ksa, mock_run_job
    ):
        mock_subproc.return_value.returncode = 0
        mock_subproc.return_value.stdout = ""
        argv = [
            "npi_gke.py",
            "--cluster-name", "test-cluster",
            "--location", "us-central1-a",
            "--bucket-name", "test-bucket",
            "--project-id", "test-project",
            "--bq-dataset-id", "test_dataset",
            "--benchmarks", "read_grpc", "go_read_grpc",
            "--iterations", "5",
        ]
        with patch("sys.argv", argv):
            npi_gke.main()

        for call in mock_run_job.call_args_list:
            args_list = call.kwargs["args_list"]
            self.assertIn("--iterations=5", args_list)
            for flag in ("--min-iterations", "--max-iterations", "--convergence-threshold", "--confidence-level"):
                self.assertFalse(any(a.startswith(flag) for a in args_list), f"Unexpected {flag} in {args_list}")

    @patch("npi_gke.run_benchmark_job", return_value=True)
    @patch("npi_gke.setup_kubernetes_service_account", return_value=True)
    @patch("npi_gke.subprocess.run")
    def test_main_partial_convergence_flags_includes_default_confidence_level(
        self, mock_subproc, mock_setup_ksa, mock_run_job
    ):
        mock_subproc.return_value.returncode = 0
        mock_subproc.return_value.stdout = ""
        argv = [
            "npi_gke.py",
            "--cluster-name", "test-cluster",
            "--location", "us-central1-a",
            "--bucket-name", "test-bucket",
            "--project-id", "test-project",
            "--bq-dataset-id", "test_dataset",
            "--benchmarks", "read_grpc",
            "--min-iterations", "4",
        ]
        with patch("sys.argv", argv):
            npi_gke.main()

        args_list = mock_run_job.call_args.kwargs["args_list"]
        self.assertIn("--min-iterations=4", args_list)
        self.assertIn("--confidence-level=0.95", args_list)
        self.assertFalse(any(a.startswith("--max-iterations") for a in args_list))
        self.assertFalse(any(a.startswith("--convergence-threshold") for a in args_list))

    @patch("npi_gke.run_benchmark_job", return_value=True)
    @patch("npi_gke.setup_kubernetes_service_account", return_value=True)
    @patch("npi_gke.subprocess.run")
    def test_main_active_convergence_with_unconfigured_confidence_level_normalizes_to_095(
        self, mock_subproc, mock_setup_ksa, mock_run_job
    ):
        """When min_iterations=3 is active and args.confidence_level is None, MagicMock, or bool,
        eff_conf normalizes to 0.95 and --confidence-level=0.95 is appended."""
        from unittest.mock import MagicMock
        import argparse

        mock_subproc.return_value.returncode = 0
        mock_subproc.return_value.stdout = ""

        for invalid_conf in (None, MagicMock(), True, False):
            mock_run_job.reset_mock()
            mock_args = argparse.Namespace(
                cluster_name="test-cluster",
                location="us-central1-a",
                bucket_name="test-bucket",
                project_id="test-project",
                bq_dataset_id="test_dataset",
                image_version="latest",
                benchmarks=["read_grpc"],
                iterations=5,
                min_iterations=3,
                max_iterations=None,
                convergence_threshold=None,
                confidence_level=invalid_conf,
                smoke_mode=False,
                numjobs=None,
                is_rapid_bucket=False,
                extra_mount_options=None,
                dry_run=False,
                kubernetes_service_account="default",
                use_memory_volumes=False,
                node_selector=None,
                resources_limits=None,
                run_file_cache_test=False,
                file_cache_size_mb=2097152,
                bq_table_suffix=None,
                gcsfuse_sidecar_image=None,
            )
            with patch("argparse.ArgumentParser.parse_args", return_value=mock_args):
                npi_gke.main()

            args_list = mock_run_job.call_args.kwargs["args_list"]
            self.assertIn("--min-iterations=3", args_list)
            self.assertIn(
                "--confidence-level=0.95",
                args_list,
                f"Expected --confidence-level=0.95 for confidence_level={invalid_conf!r}, got: {args_list}",
            )

    @patch("npi_gke.run_benchmark_job", return_value=True)
    @patch("npi_gke.setup_kubernetes_service_account", return_value=True)
    @patch("npi_gke.subprocess.run")
    def test_main_unconfigured_confidence_level_in_legacy_mode_omits_flag(
        self, mock_subproc, mock_setup_ksa, mock_run_job
    ):
        """When no convergence flags are active and args.confidence_level is None, MagicMock, or bool,
        eff_conf normalizes to 0.95 and --confidence-level is completely omitted."""
        from unittest.mock import MagicMock
        import argparse

        mock_subproc.return_value.returncode = 0
        mock_subproc.return_value.stdout = ""

        for invalid_conf in (None, MagicMock(), True, False):
            mock_run_job.reset_mock()
            mock_args = argparse.Namespace(
                cluster_name="test-cluster",
                location="us-central1-a",
                bucket_name="test-bucket",
                project_id="test-project",
                bq_dataset_id="test_dataset",
                image_version="latest",
                benchmarks=["read_grpc"],
                iterations=5,
                min_iterations=None,
                max_iterations=None,
                convergence_threshold=None,
                confidence_level=invalid_conf,
                smoke_mode=False,
                numjobs=None,
                is_rapid_bucket=False,
                extra_mount_options=None,
                dry_run=False,
                kubernetes_service_account="default",
                use_memory_volumes=False,
                node_selector=None,
                resources_limits=None,
                run_file_cache_test=False,
                file_cache_size_mb=2097152,
                bq_table_suffix=None,
                gcsfuse_sidecar_image=None,
            )
            with patch("argparse.ArgumentParser.parse_args", return_value=mock_args):
                npi_gke.main()

            args_list = mock_run_job.call_args.kwargs["args_list"]
            self.assertFalse(
                any(a.startswith("--confidence-level") for a in args_list),
                f"Unexpected --confidence-level for confidence_level={invalid_conf!r}: {args_list}",
            )

    @patch("npi_gke.run_benchmark_job", return_value=True)
    @patch("npi_gke.setup_kubernetes_service_account", return_value=True)
    @patch("npi_gke.subprocess.run")
    def test_main_non_default_confidence_level_only_emits_flag(
        self, mock_subproc, mock_setup_ksa, mock_run_job
    ):
        """When only --confidence-level 0.99 is passed without other convergence flags,
        --confidence-level=0.99 is emitted for benchmark jobs and omitted for host_info."""
        mock_subproc.return_value.returncode = 0
        mock_subproc.return_value.stdout = ""
        argv = [
            "npi_gke.py",
            "--cluster-name", "test-cluster",
            "--location", "us-central1-a",
            "--bucket-name", "test-bucket",
            "--project-id", "test-project",
            "--bq-dataset-id", "test_dataset",
            "--benchmarks", "host_info", "read_grpc",
            "--confidence-level", "0.99",
        ]
        with patch("sys.argv", argv):
            npi_gke.main()

        calls_by_table = {
            call.kwargs["table_id"]: call.kwargs["args_list"]
            for call in mock_run_job.call_args_list
        }
        self.assertFalse(any(a.startswith("--confidence-level") for a in calls_by_table["host_info"]))
        self.assertIn("--confidence-level=0.99", calls_by_table["fio_read_grpc"])


if __name__ == "__main__":
    unittest.main()

