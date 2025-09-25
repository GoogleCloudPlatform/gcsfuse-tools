#!/usr/bin/env python3
"""A script for running GCSfuse performance benchmarks.

This script orchestrates running various GCSFuse performance benchmarks in Docker
containers. It allows specifying which benchmarks to run, the GCS bucket to use,
and where to store the results in BigQuery.

The script supports different configurations for benchmarks, such as running with
HTTP/1.1 or gRPC, and pinning to specific NUMA nodes.

Usage:
  python3 npi.py --benchmarks <benchmark_names> --bucket-name <bucket> \\
    --bq-project-id <project> --bq-dataset-id <dataset> --gcsfuse-version <version>

Example:
  python3 npi.py --benchmarks read_http1 write_grpc --bucket-name my-bucket \\
    --bq-project-id my-bq-project --bq-dataset-id my_bq_dataset --gcsfuse-version v1.2.0
"""

import argparse
import json
import functools
import shlex
import logging
import subprocess
import sys
import tempfile
import shutil

class BenchmarkFactory:
    """A factory for creating benchmark commands.

    This class is responsible for generating the Docker commands needed to run
    the various GCSfuse performance benchmarks. It takes into account the
    benchmark type (e.g., read, write), configuration (e.g., HTTP/1.1, gRPC),
    and other parameters like NUMA node pinning.

    Attributes:
        bucket_name (str): The GCS bucket to use for the benchmarks.
        bq_project_id (str): The BigQuery project ID for storing results.
        bq_dataset_id (str): The BigQuery dataset ID for storing results.
        gcsfuse_version (str): The GCSfuse version to use.
        iterations (int): The number of iterations for each benchmark.
        temp_dir (str): The type of temporary directory to use ('memory' or
            'boot-disk').
    """

    def __init__(self, bucket_name, bq_project_id, bq_dataset_id, gcsfuse_version, iterations, temp_dir):
        """Initializes the BenchmarkFactory.

        Args:
            bucket_name (str): The GCS bucket name.
            bq_project_id (str): The BigQuery project ID.
            bq_dataset_id (str): The BigQuery dataset ID.
            gcsfuse_version (str): The GCSfuse version.
            iterations (int): The number of benchmark iterations.
            temp_dir (str): The temporary directory type.
        """
        self.bucket_name = bucket_name
        self.bq_project_id = bq_project_id
        self.bq_dataset_id = bq_dataset_id
        self.gcsfuse_version = gcsfuse_version
        self.iterations = iterations
        self.temp_dir = temp_dir
        self._benchmark_definitions = self._get_benchmark_definitions()

    def get_benchmark_command(self, name):
        """Generates the command for a given benchmark name.

        Args:
            name (str): The name of the benchmark to generate the command for.

        Returns:
            str: The full Docker command to run the benchmark.

        Raises:
            ValueError: If the benchmark name is not defined.
        """
        if name not in self._benchmark_definitions:
            raise ValueError(f"Benchmark '{name}' not defined.")

        command_func = self._benchmark_definitions[name]
        return command_func(
            bucket_name=self.bucket_name,
            bq_project_id=self.bq_project_id,
            bq_dataset_id=self.bq_dataset_id
        )

    def get_available_benchmarks(self):
        """Returns a list of available benchmark names.

        Returns:
            list[str]: A list of all defined benchmark names.
        """
        return list(self._benchmark_definitions.keys())

    def _create_docker_command(self, benchmark_image_suffix, bq_table_id,
                               bucket_name, bq_project_id, bq_dataset_id,
                               gcsfuse_flags=None, cpu_list=None, bind_fio=None):
        """Helper to construct the full docker run command.

        This method assembles the final `docker run` command string with all
        the necessary flags and parameters.

        Args:
            benchmark_image_suffix (str): The suffix for the benchmark Docker image.
            bq_table_id (str): The BigQuery table ID for the results.
            bucket_name (str): The GCS bucket name.
            bq_project_id (str): The BigQuery project ID.
            bq_dataset_id (str): The BigQuery dataset ID.
            gcsfuse_flags (str, optional): Additional flags for GCSfuse.
            cpu_list (str, optional): The list of CPUs to pin the container to.
            bind_fio (bool, optional): Whether to bind FIO to the same CPUs.

        Returns:
            str: The complete Docker command.
        """
        container_temp_dir = "/gcsfuse-temp"
        volume_mount = ""
        if self.temp_dir == "memory":
            volume_mount = f"--mount type=tmpfs,destination={container_temp_dir}"
        elif self.temp_dir == "boot-disk":
            volume_mount = f"-v <temp_dir_path>:{container_temp_dir}"

        if gcsfuse_flags:
            gcsfuse_flags += f" --temp-dir={container_temp_dir}"
        else:
            gcsfuse_flags = f"--temp-dir={container_temp_dir}"

        base_cmd = (
            "docker run --pull=always --network=host --privileged --rm "
            f"{volume_mount} "
            f"us-docker.pkg.dev/gcs-fuse-test/gcsfuse-benchmarks/{benchmark_image_suffix}-{self.gcsfuse_version}:latest "
            f"--iterations={self.iterations} "
            f"--bucket-name={bucket_name} "
            f"--bq-project-id={bq_project_id} "
            f"--bq-dataset-id={bq_dataset_id} "
            f"--bq-table-id={bq_table_id}"
        )
        if gcsfuse_flags:
            base_cmd += f" --gcsfuse-flags='{gcsfuse_flags}'"
        if cpu_list:
            base_cmd += f" --cpu-limit-list={cpu_list}"
        if bind_fio:
            base_cmd += " --bind-fio"
        return base_cmd

    def _get_cpu_list_for_numa_node(self, node_id):
        """Gets the CPU list for a given NUMA node by parsing `lscpu --json`.

        Args:
            node_id (int): The NUMA node ID (e.g., 0 or 1).

        Returns:
            str | None: A string containing the comma-separated list of CPUs for
                the given NUMA node, or None if the information cannot be
                retrieved.
        """
        try:
            result = subprocess.run(
                ["lscpu", "--json"],
                capture_output=True,
                text=True,
                check=True,
                encoding='utf-8',
            )
            data = json.loads(result.stdout)
            search_field = f"NUMA node{node_id} CPU(s):"
            for item in data.get("lscpu", []):
                if item.get("field") == search_field:
                    return item.get("data")
        except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
            logging.warning(f"Could not determine CPUs for NUMA node {node_id}: {e}. NUMA-pinned benchmarks for this node will be skipped.")
        return None

    def _get_benchmark_definitions(self):
        """Returns a dictionary of benchmark names to command-generating functions.

        This method defines all the available benchmarks and their configurations.
        It constructs a dictionary where keys are benchmark names (e.g.,
        'read_http1', 'write_grpc_numa0_fio_bound') and values are partial
        functions that, when called, generate the full Docker command for that
        benchmark.

        Returns:
            dict[str, callable]: A dictionary mapping benchmark names to functions
                that generate the benchmark command string.
        """
        # Define benchmark configurations
        # Each benchmark has an image suffix and an optional BQ table name override.
        benchmarks = {
            "orbax_read": {
                "image_suffix": "orbax-emulated-benchmark",
                "bq_table_id_override": "orbax_read_{config_name}"
            },
            "read": {"image_suffix": "fio-read-benchmark"},
            "write": {"image_suffix": "fio-write-benchmark"},
            #"full_sweep": {"image_suffix": "fio-fullsweep-benchmark"}, # Comment out full_sweep for now since it takes a long long time.
        }

        # Define test configurations (protocol, cpu pinning, etc.)
        configs = {
            "http1": {},
            "grpc": {"gcsfuse_flags": "--client-protocol=grpc"},
        }

        # Dynamically add NUMA configurations if possible.
        for node_id in [0, 1]:
            cpu_list = self._get_cpu_list_for_numa_node(node_id)
            if cpu_list:
                numa_name = f"numa{node_id}"
                # For NUMA nodes, create 4 configs: http1/grpc with and without binding fio
                configs[f"http1_{numa_name}_fio_notbound"] = {"cpu_list": cpu_list, "bind_fio": False}
                configs[f"http1_{numa_name}_fio_bound"] = {"cpu_list": cpu_list, "bind_fio": True}
                configs[f"grpc_{numa_name}_fio_notbound"] = {"cpu_list": cpu_list, "gcsfuse_flags": "--client-protocol=grpc", "bind_fio": False}
                configs[f"grpc_{numa_name}_fio_bound"] = {"cpu_list": cpu_list, "gcsfuse_flags": "--client-protocol=grpc", "bind_fio": True}




        definitions = {}
        for bench_name, bench_config in benchmarks.items():
            for config_name, config_params in configs.items():
                # Construct the full benchmark name and BQ table ID
                full_bench_name = f"{bench_name}_{config_name}"

                if "bq_table_id_override" in bench_config:
                    bq_table_id = bench_config["bq_table_id_override"].format(config_name=config_name)
                else:
                    bq_table_id = f"fio_{full_bench_name}"

                # Use functools.partial to create a command function with pre-filled arguments
                definitions[full_bench_name] = functools.partial(
                    self._create_docker_command,
                    benchmark_image_suffix=bench_config["image_suffix"],
                    bq_table_id=bq_table_id,
                    **config_params
                )
        return definitions


def run_benchmark(benchmark_name, command_str, temp_dir_type):
    """Runs a single benchmark command locally.

    This function executes a benchmark command using `subprocess.run`. It handles
    the creation and cleanup of a temporary directory on the host if the
    'boot-disk' temp_dir_type is used.

    Args:
        benchmark_name (str): The name of the benchmark being run.
        command_str (str): The Docker command string to execute.
        temp_dir_type (str): The type of temporary directory ('memory' or
            'boot-disk').

    Returns:
        bool: True if the benchmark ran successfully, False otherwise.
    """
    print(f"--- Running benchmark: {benchmark_name} on localhost ---")

    host_temp_dir = None
    if temp_dir_type == "boot-disk":
        host_temp_dir = tempfile.mkdtemp(prefix="gcsfuse-npi-")
        print(f"Created temporary directory on host: {host_temp_dir}")
        command_str = command_str.replace("<temp_dir_path>", host_temp_dir)

    command = shlex.split(command_str)
    print(f"Command: {' '.join(command)}")

    try:
        subprocess.run(command, check=True)
        print(f"--- Benchmark {benchmark_name} on localhost finished successfully ---")
        success = True
    except FileNotFoundError:
        print("Error: Command not found. Ensure docker is in your PATH.", file=sys.stderr)
        success = False
    except subprocess.CalledProcessError as e:
        print(f"--- Benchmark {benchmark_name} on localhost FAILED ---", file=sys.stderr)
        print(f"Return code: {e.returncode}", file=sys.stderr)
        success = False
    finally:
        if host_temp_dir:
            print(f"Cleaning up temporary directory: {host_temp_dir}")
            shutil.rmtree(host_temp_dir)

    return success

def main():
    """Parses command-line arguments and orchestrates benchmark runs.

    This is the main entry point of the script. It parses arguments, creates a
    BenchmarkFactory, determines which benchmarks to run, and then executes them
    sequentially.
    """
    parser = argparse.ArgumentParser(
        description="A benchmark runner.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "-b", "--benchmarks",
        nargs="+",
        default=["all"],
        help="Space-separated list of benchmarks to run. Use 'all' to run all available benchmarks."
    )
    parser.add_argument("--bucket-name", required=True, help="Name of the GCS bucket to use.")
    parser.add_argument("--bq-project-id", required=True, help="BigQuery project ID for results.")
    parser.add_argument("--bq-dataset-id", required=True, help="BigQuery dataset ID for results.")
    parser.add_argument("--gcsfuse-version", required=True, help="GCSFuse version to use for benchmark images (e.g., 'master', 'v1.2.0').")
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Number of FIO test iterations per benchmark. Default: 5."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the benchmark commands that would be executed without running them."
    )
    parser.add_argument(
        "--temp-dir",
        choices=["memory", "boot-disk"],
        default="boot-disk",
        help="The temporary directory type to use for benchmark artifacts. 'memory' uses a tmpfs mount, 'boot-disk' uses the host's disk. Default: boot-disk."
    )

    args = parser.parse_args()

    factory = BenchmarkFactory(
        bucket_name=args.bucket_name,
        bq_project_id=args.bq_project_id,
        bq_dataset_id=args.bq_dataset_id,
        gcsfuse_version=args.gcsfuse_version,
        iterations=args.iterations,
        temp_dir=args.temp_dir
    )

    available_benchmarks = factory.get_available_benchmarks()
    benchmarks_to_run = available_benchmarks if "all" in args.benchmarks else args.benchmarks

    # Validate benchmark names
    for b in benchmarks_to_run:
        if b not in available_benchmarks:
            print(f"Error: Benchmark '{b}' not found.", file=sys.stderr)
            sys.exit(1)

    print(f"Starting benchmark orchestration...")
    print(f"Benchmarks to run: {', '.join(benchmarks_to_run)}")
    print(f"BigQuery Target: {args.bq_project_id}.{args.bq_dataset_id}")

    # Run benchmarks sequentially on the local machine.
    failed_benchmarks = []
    for benchmark_name in benchmarks_to_run:
        # For boot-disk, we pass a placeholder that will be replaced in run_benchmark
        command_str = factory.get_benchmark_command(benchmark_name)

        if args.dry_run:
            print(f"--- [DRY RUN] Benchmark: {benchmark_name} ---")
            print(f"Command: {command_str}\n")
        else:
            success = run_benchmark(benchmark_name, command_str, args.temp_dir)
            if not success:
                failed_benchmarks.append(benchmark_name)

    if failed_benchmarks:
        print(f"\n--- Some benchmarks failed: {', '.join(failed_benchmarks)} ---", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
