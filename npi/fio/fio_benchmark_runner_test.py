import json
import os
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add the directory containing fio_benchmark_runner.py to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import fio_benchmark_runner
import run_fio_benchmark
import run_fio_matrix


def _make_fio_json(bw_mibps, lat_ms=10.0, rw="read"):
    io_key = "write" if rw == "write" else "read"
    return json.dumps({
        "fio version": "fio-3.36",
        "global options": {"iodepth": "64", "rw": rw},
        "jobs": [
            {
                "jobname": f"job_{rw}",
                "job options": {
                    "bs": "1M",
                    "filesize": "1G",
                    "nrfiles": "10",
                    "numjobs": "112",
                },
                io_key: {
                    "bw": int(bw_mibps * 1024.0),
                    "iops": float(bw_mibps),
                    "lat_ns": {
                        "mean": int(lat_ms * 1_000_000.0),
                        "percentiles": {"99.000000": int(lat_ms * 1_500_000.0)},
                    },
                },
            }
        ],
    })


class TestClearCacheDir(unittest.TestCase):

    @patch('os.path.exists')
    @patch('shutil.rmtree')
    def test_clear_cache_dir_success(self, mock_rmtree, mock_exists):
        mock_exists.return_value = True
        
        flags = "--cache-dir=/tmp/cache"
        fio_benchmark_runner.clear_cache_dir(flags)
        
        mock_rmtree.assert_called_once_with("/tmp/cache")

    @patch('os.path.exists')
    def test_clear_cache_dir_no_flag(self, mock_exists):
        fio_benchmark_runner.clear_cache_dir("--other-flag=value")
        mock_exists.assert_not_called()

    @patch('os.path.exists')
    def test_clear_cache_dir_not_exists(self, mock_exists):
        mock_exists.return_value = False
        fio_benchmark_runner.clear_cache_dir("--cache-dir=/tmp/cache")
        mock_exists.assert_called_once_with("/tmp/cache")


class TestAdaptiveConvergenceRunner(unittest.TestCase):

    def test_early_stopping_on_low_variance_stream(self):
        stream = [1000.0, 1005.0, 995.0, 1002.0, 998.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_file = os.path.join(tmpdir, "summary.txt")

            def fake_run_fio(fio_config, mount_point, iteration, output_dir, fio_env=None, cpu_limit_list=None):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w") as f:
                    f.write(_make_fio_json(stream[iteration - 1]))

            with (
                patch.object(fio_benchmark_runner, "run_command"),
                patch.object(fio_benchmark_runner, "mount_gcsfuse"),
                patch.object(fio_benchmark_runner, "unmount_gcsfuse"),
                patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio,
            ):
                conv = fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=5,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    summary_file=summary_file,
                    keep_mount=False,
                    min_iterations=3,
                    max_iterations=10,
                    convergence_threshold=0.05,
                    confidence_level=0.95,
                )

            self.assertEqual(mock_fio.call_count, 3)
            self.assertTrue(conv.converged)
            self.assertTrue(os.path.exists(summary_file))
            with open(summary_file, "r") as f:
                summary_text = f.read()
            self.assertIn("1000.00", summary_text)

    def test_keep_mount_warmup_separation(self):
        stream = [250.0, 2000.0, 2010.0, 1990.0, 2005.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_file = os.path.join(tmpdir, "summary.txt")

            def fake_run_fio(fio_config, mount_point, iteration, output_dir, fio_env=None, cpu_limit_list=None):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w") as f:
                    f.write(_make_fio_json(stream[iteration - 1]))

            with (
                patch.object(fio_benchmark_runner, "run_command"),
                patch.object(fio_benchmark_runner, "mount_gcsfuse") as mock_mount,
                patch.object(fio_benchmark_runner, "unmount_gcsfuse") as mock_unmount,
                patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio,
            ):
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=5,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    summary_file=summary_file,
                    keep_mount=True,
                    min_iterations=3,
                    max_iterations=8,
                    convergence_threshold=0.05,
                    confidence_level=0.95,
                )

            self.assertEqual(mock_mount.call_count, 1)
            self.assertEqual(mock_unmount.call_count, 1)
            self.assertEqual(mock_fio.call_count, 4)
            with open(summary_file, "r") as f:
                summary_text = f.read()
            self.assertIn("2000.00", summary_text)
            self.assertNotIn("1562.50", summary_text)

    def test_transient_stall_outlier_filtering(self):
        stream = [1000.0, 1000.0, 200.0, 1000.0, 1000.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_file = os.path.join(tmpdir, "summary.txt")

            def fake_run_fio(fio_config, mount_point, iteration, output_dir, fio_env=None, cpu_limit_list=None):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w") as f:
                    f.write(_make_fio_json(stream[iteration - 1]))

            with (
                patch.object(fio_benchmark_runner, "run_command"),
                patch.object(fio_benchmark_runner, "mount_gcsfuse"),
                patch.object(fio_benchmark_runner, "unmount_gcsfuse"),
                patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio,
            ):
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=5,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    summary_file=summary_file,
                    keep_mount=False,
                    min_iterations=4,
                    max_iterations=8,
                    convergence_threshold=0.05,
                )

            self.assertEqual(mock_fio.call_count, 4)
            with open(summary_file, "r") as f:
                summary_text = f.read()
            self.assertIn("1000.00", summary_text)
            self.assertNotIn("800.00", summary_text)

    def test_high_variance_clean_termination_at_max_iterations(self):
        stream = [500.0, 1500.0, 600.0, 1400.0, 550.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_run_fio(fio_config, mount_point, iteration, output_dir, fio_env=None, cpu_limit_list=None):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w") as f:
                    f.write(_make_fio_json(stream[iteration - 1]))

            with (
                patch.object(fio_benchmark_runner, "run_command"),
                patch.object(fio_benchmark_runner, "mount_gcsfuse"),
                patch.object(fio_benchmark_runner, "unmount_gcsfuse"),
                patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio,
            ):
                conv = fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=5,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    keep_mount=False,
                    min_iterations=3,
                    max_iterations=5,
                    convergence_threshold=0.02,
                )

            self.assertEqual(mock_fio.call_count, 5)
            self.assertFalse(conv.converged)

    def test_legacy_fixed_mode_when_convergence_flags_none(self):
        stream = [1000.0, 1000.0, 1000.0, 1000.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_run_fio(fio_config, mount_point, iteration, output_dir, fio_env=None, cpu_limit_list=None):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w") as f:
                    f.write(_make_fio_json(stream[iteration - 1]))

            with (
                patch.object(fio_benchmark_runner, "run_command"),
                patch.object(fio_benchmark_runner, "mount_gcsfuse"),
                patch.object(fio_benchmark_runner, "unmount_gcsfuse"),
                patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio,
            ):
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=4,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    min_iterations=None,
                    max_iterations=None,
                    convergence_threshold=None,
                )

            self.assertEqual(mock_fio.call_count, 4)

    def test_min_greater_than_max_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=3,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    min_iterations=8,
                    max_iterations=3,
                    convergence_threshold=0.05,
                )

    def test_cli_flag_forwarding_benchmark_and_matrix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w") as f:
                f.write("FILE_SIZE,BLOCK_SIZE,NR_FILES\n1G,1M,10\n")

            with (
                patch.object(fio_benchmark_runner, "run_benchmark") as mock_run,
                patch.object(fio_benchmark_runner, "truncate_bq_table"),
            ):
                with patch.object(
                    sys,
                    "argv",
                    [
                        "run_fio_benchmark.py",
                        "--bucket-name", "test-b",
                        "--fio-config", "read.fio",
                        "--project-id", "test-p",
                        "--min-iterations", "3",
                        "--max-iterations", "7",
                        "--convergence-threshold", "0.04",
                        "--confidence-level", "0.90",
                    ],
                ):
                    run_fio_benchmark.main()

                kwargs1 = mock_run.call_args.kwargs
                self.assertEqual(kwargs1.get("min_iterations"), 3)
                self.assertEqual(kwargs1.get("max_iterations"), 7)
                self.assertEqual(kwargs1.get("convergence_threshold"), 0.04)
                self.assertEqual(kwargs1.get("confidence_level"), 0.90)

                mock_run.reset_mock()
                with patch.object(
                    sys,
                    "argv",
                    [
                        "run_fio_matrix.py",
                        "--bucket-name", "test-b",
                        "--matrix-config", csv_path,
                        "--fio-template", "read.fio",
                        "--project-id", "test-p",
                        "--work-dir", tmpdir,
                        "--output-dir", tmpdir,
                        "--min-iterations", "3",
                        "--max-iterations", "7",
                        "--convergence-threshold", "0.04",
                        "--confidence-level", "0.90",
                    ],
                ):
                    run_fio_matrix.main()

                kwargs2 = mock_run.call_args.kwargs
                self.assertEqual(kwargs2.get("min_iterations"), 3)
                self.assertEqual(kwargs2.get("max_iterations"), 7)
                self.assertEqual(kwargs2.get("convergence_threshold"), 0.04)
                self.assertEqual(kwargs2.get("confidence_level"), 0.90)


if __name__ == '__main__':
    unittest.main()
