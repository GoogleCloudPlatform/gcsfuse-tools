#!/usr/bin/env python3
"""A script for running GCSfuse performance benchmarks.

This script orchestrates running various GCSFuse performance benchmarks in Docker
containers. It allows specifying which benchmarks to run, the GCS bucket to use,
and where to store the results in BigQuery.

The script supports different configurations for benchmarks, such as running with
HTTP/1.1 or gRPC, and pinning to specific NUMA nodes.

Usage:
  python3 npi.py --benchmarks <benchmark_names> --bucket-name <bucket> \\
    --project-id <project> --bq-dataset-id <dataset>

  python3 npi.py --benchmarks <benchmark_names> --mount-path <path> \\
    --project-id <project> --bq-dataset-id <dataset>

Example:
  python3 npi.py --benchmarks read_http1 write_grpc --bucket-name my-bucket \\
    --project-id my-project --bq-dataset-id my_bq_dataset
"""

import argparse
import json
import functools
import os
import shlex
import logging
import subprocess
import sys
import tempfile
import shutil
import datetime

class BenchmarkFactory:
    """A factory for creating benchmark commands.

    This class is responsible for generating the Docker commands needed to run
    the various GCSfuse performance benchmarks. It takes into account the
    benchmark type (e.g., read, write), configuration (e.g., HTTP/1.1, gRPC),
    and other parameters like NUMA node pinning.

    Attributes:
        bucket_name (str): The GCS bucket to use for the benchmarks.
        project_id (str): The BigQuery project ID for storing results.
        bq_dataset_id (str): The BigQuery dataset ID for storing results.
        iterations (int): The number of iterations for each benchmark.
        temp_dir (str): The type of temporary directory to use ('memory' or
            'boot-disk').
        mount_path (str): The path to an already mounted GCS bucket.
    """

    def __init__(self, bucket_name, project_id, bq_dataset_id, iterations, mount_path=None, image_version="latest", buffer_mount_path=None, file_cache_size_mb=2097152):
        """Initializes the BenchmarkFactory.

        Args:
            bucket_name (str): The GCS bucket name.
            project_id (str): The BigQuery project ID.
            bq_dataset_id (str): The BigQuery dataset ID.
            iterations (int): The number of benchmark iterations.
            mount_path (str): The path to an already mounted GCS bucket.
            image_version (str): The version of the benchmark Docker images.
        """
        self.bucket_name = bucket_name
        self.project_id = project_id
        self.bq_dataset_id = bq_dataset_id
        self.iterations = iterations
        self.mount_path = mount_path
        self.image_version = image_version
        self.buffer_mount_path = buffer_mount_path
        self.file_cache_size_mb = file_cache_size_mb
        self._benchmark_definitions = self._get_benchmark_definitions()

    def get_benchmark_command(self, name):
        """Generates the command for a given benchmark name.

        Args:
            name (str): The name of the benchmark to generate the command for.

        Returns:
            tuple[str, str]: A tuple containing the full Docker command and the BigQuery table ID.

        Raises:
            ValueError: If the benchmark name is not defined.
        """
        if name not in self._benchmark_definitions:
            raise ValueError(f"Benchmark '{name}' not defined.")

        command_func = self._benchmark_definitions[name]
        return command_func(
            bucket_name=self.bucket_name,
            project_id=self.project_id,
            bq_dataset_id=self.bq_dataset_id,
            mount_path=self.mount_path
        )

    def get_available_benchmarks(self):
        """Returns a list of available benchmark names.

        Returns:
            list[str]: A list of all defined benchmark names.
        """
        return list(self._benchmark_definitions.keys())

    def _create_docker_command(self, benchmark_image_suffix, bq_table_id,
                               bucket_name, project_id, bq_dataset_id,
                               gcsfuse_flags=None, cpu_list=None, bind_fio=None, mount_path=None,
                               docker_args=None, iterations_override=None, runner_args=None):
        """Helper to construct the full docker run command.

        This method assembles the final `docker run` command string with all
        the necessary flags and parameters.

        Args:
            benchmark_image_suffix (str): The suffix for the benchmark Docker image.
            bq_table_id (str): The BigQuery table ID for the results.
            bucket_name (str): The GCS bucket name.
            project_id (str): The BigQuery project ID.
            bq_dataset_id (str): The BigQuery dataset ID.
            gcsfuse_flags (str, optional): Additional flags for GCSfuse.
            cpu_list (str, optional): The list of CPUs to pin the container to.
            bind_fio (bool, optional): Whether to bind FIO to the same CPUs.
            mount_path (str, optional): The path to an already mounted GCS bucket.

        Returns:
            tuple[str, str]: A tuple containing the complete Docker command and the BigQuery table ID.
        """
        container_temp_dir = "/gcsfuse-buffer/write"
        volume_mount = f"-v {shlex.quote(self.buffer_mount_path)}:/gcsfuse-buffer"

        if mount_path:
            volume_mount += f" -v {shlex.quote(mount_path)}:{shlex.quote(mount_path)}"

        default_gcsfuse_flags = f"--temp-dir={container_temp_dir} -o allow_other"

        if gcsfuse_flags:
            # Prepend default flags. This allows user-provided flags to override defaults if needed.
            gcsfuse_flags = f"{default_gcsfuse_flags} {gcsfuse_flags}"
        else:
            gcsfuse_flags = default_gcsfuse_flags

        base_cmd = (
            "docker run --pull=always --network=host --privileged --rm "
            f"{volume_mount} "
        )
        if docker_args:
            base_cmd += f"{docker_args} "

        target_iterations = iterations_override if iterations_override is not None else self.iterations

        base_cmd += (
            f"us-docker.pkg.dev/{project_id}/gcsfuse-benchmarks/{benchmark_image_suffix}:{self.image_version} "
            f"--iterations={target_iterations} "
            f"--project-id={project_id} "
            f"--bq-dataset-id={bq_dataset_id} "
            f"--bq-table-id={bq_table_id}"
        )
        
        if runner_args:
            base_cmd += f" {runner_args}"
        if bucket_name:
            base_cmd += f" --bucket-name={bucket_name}"
        if mount_path:
            base_cmd += f" --mount-path={shlex.quote(mount_path)}"
        if gcsfuse_flags:
            base_cmd += f" --gcsfuse-flags='{gcsfuse_flags}'"
        if cpu_list:
            base_cmd += f" --cpu-limit-list={cpu_list}"
        if bind_fio:
            base_cmd += " --bind-fio"
        return base_cmd, bq_table_id

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
        read_file_cache_config = {
            "image_suffix": "fio-read-benchmark",
            "docker_args": "", # Removed FIO_ITERATIONS
            "iterations_override": 10,
            "runner_args": "--keep-mount"
        }
        read_file_cache_config["gcsfuse_flags_extra"] = f"--metadata-cache-ttl-secs=-1,--file-cache-max-size-mb={self.file_cache_size_mb} --file-cache-dir=/gcsfuse-buffer/file-cache"

        benchmarks = {
            "read": {"image_suffix": "fio-read-benchmark"},
            "write": {"image_suffix": "fio-write-benchmark"},
            "read_file_cache": read_file_cache_config,
        }

        # Define test configurations (protocol, cpu pinning, etc.)
        configs = {
            "http1": {"gcsfuse_flags": "--client-protocol=http1"},
            "grpc": {"gcsfuse_flags": "--client-protocol=grpc"},
        }

        # Dynamically add NUMA configurations if possible.
        for node_id in [0, 1]:
            cpu_list = self._get_cpu_list_for_numa_node(node_id)
            if cpu_list:
                numa_name = f"numa{node_id}"
                # For NUMA nodes, create 4 configs: http1/grpc with and without binding fio
                configs[f"http1_{numa_name}_fio_notbound"] = {"cpu_list": cpu_list, "gcsfuse_flags": "--client-protocol=http1", "bind_fio": False}
                configs[f"http1_{numa_name}_fio_bound"] = {"cpu_list": cpu_list, "gcsfuse_flags": "--client-protocol=http1", "bind_fio": True}
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

                combined_gcsfuse_flags = config_params.get("gcsfuse_flags", "")
                if "gcsfuse_flags_extra" in bench_config:
                    combined_gcsfuse_flags = f"{combined_gcsfuse_flags} {bench_config['gcsfuse_flags_extra']}".strip()
                
                cpu_list = config_params.get("cpu_list")
                bind_fio = config_params.get("bind_fio")
                docker_args = bench_config.get("docker_args")
                iterations_override = bench_config.get("iterations_override")
                runner_args = bench_config.get("runner_args")

                # Use functools.partial to create a command function with pre-filled arguments
                definitions[full_bench_name] = functools.partial(
                    self._create_docker_command,
                    benchmark_image_suffix=bench_config["image_suffix"],
                    bq_table_id=bq_table_id,
                    gcsfuse_flags=combined_gcsfuse_flags if combined_gcsfuse_flags else None,
                    cpu_list=cpu_list,
                    bind_fio=bind_fio,
                    docker_args=docker_args,
                    iterations_override=iterations_override,
                    runner_args=runner_args
                )
        return definitions


def run_benchmark(benchmark_name, command_str, project_id, dataset_id, table_id):
    """Runs a single benchmark command locally.

    This function executes a benchmark command using `subprocess.run`.

    Args:
        benchmark_name (str): The name of the benchmark being run.
        command_str (str): The Docker command string to execute.
        project_id (str): The BigQuery project ID.
        dataset_id (str): The BigQuery dataset ID.
        table_id (str): The BigQuery table ID.

    Returns:
        bool: True if the benchmark ran successfully, False otherwise.
    """
    print(f"--- Running benchmark: {benchmark_name} on localhost ---")

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
    parser.add_argument("--bucket-name", default=None, help="Name of the GCS bucket to use.")
    parser.add_argument("--mount-path", default=None, help="Path to an already mounted GCS bucket. If provided, --bucket-name is ignored and GCSFuse is not mounted.")
    parser.add_argument("--project-id", required=True, help="Project ID for results.")
    parser.add_argument("--bq-dataset-id", required=True, help="BigQuery dataset ID for results.")
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
        "--buffer-mount-path",
        required=True,
        help="The host directory to mount as the storage buffer inside the container (used for both writes and file-cache)."
    )
    parser.add_argument(
        "--image-version",
        default="latest",
        help="The version (tag) of the benchmark Docker images to use. Default: latest."
    )
    parser.add_argument(
        "--file-cache-size-mb",
        type=int,
        default=2097152,
        help="The size of the file cache in MB. Default: 2097152."
    )
    parser.add_argument(
        "--is-rapid-bucket",
        action="store_true",
        help="If set, indicates that the bucket is a RAPID bucket. Only gRPC benchmarks will be run."
    )

    args = parser.parse_args()

    if not args.bucket_name and not args.mount_path:
        parser.error("Either --bucket-name or --mount-path must be provided.")

    mount_path = os.path.abspath(args.mount_path) if args.mount_path else None

    # Clear the buffer-mount-path if it's not empty and it's not a dry run
    if not args.dry_run and os.path.exists(args.buffer_mount_path):
        if os.listdir(args.buffer_mount_path):
            print(f"Buffer mount path {args.buffer_mount_path} is not empty. Clearing its contents...")
            for filename in os.listdir(args.buffer_mount_path):
                file_path = os.path.join(args.buffer_mount_path, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    print(f"Failed to delete {file_path}. Reason: {e}", file=sys.stderr)

    # Ensure subdirectories exist in the buffer mount path to prevent permission issues.
    os.makedirs(os.path.join(args.buffer_mount_path, "write"), exist_ok=True)
    os.makedirs(os.path.join(args.buffer_mount_path, "file-cache"), exist_ok=True)

    factory = BenchmarkFactory(
        bucket_name=args.bucket_name,
        project_id=args.project_id,
        bq_dataset_id=args.bq_dataset_id,
        iterations=args.iterations,
        mount_path=mount_path,
        image_version=args.image_version,
        buffer_mount_path=args.buffer_mount_path,
        file_cache_size_mb=args.file_cache_size_mb
    )

    available_benchmarks = factory.get_available_benchmarks()
    if "all" in args.benchmarks:
        benchmarks_to_run = available_benchmarks
    else:
        # Validate benchmark names
        for b in args.benchmarks:
            if b not in available_benchmarks:
                print(f"Error: Benchmark '{b}' not found.", file=sys.stderr)
                sys.exit(1)
        benchmarks_to_run = args.benchmarks
            
    if args.is_rapid_bucket:
        if "all" not in args.benchmarks:
            for b in args.benchmarks:
                if "http1" in b:
                    parser.error(f"Benchmark '{b}' is not supported for RAPID buckets (only gRPC benchmarks are allowed).")
        benchmarks_to_run = [b for b in benchmarks_to_run if "http1" not in b]
            
    # Validations for missing file-cache requirements are now obsolete.

    print(f"Starting benchmark orchestration...")
    print(f"Benchmarks to run: {', '.join(benchmarks_to_run)}")
    print(f"BigQuery Target: {args.project_id}.{args.bq_dataset_id}")

    start_time = datetime.datetime.now()
    print(f"--- Entire run started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ---")

    # Run benchmarks sequentially on the local machine.
    failed_benchmarks = []
    for benchmark_name in benchmarks_to_run:
        # For boot-disk, we pass a placeholder that will be replaced in run_benchmark
        command_str, bq_table_id = factory.get_benchmark_command(benchmark_name)

        if args.dry_run:
            print(f"--- [DRY RUN] Benchmark: {benchmark_name} ---")
            print(f"Table: {bq_table_id}")
            print(f"Command: {command_str}\n")
        else:
            success = run_benchmark(benchmark_name, command_str, args.project_id, args.bq_dataset_id, bq_table_id)
            if not success:
                failed_benchmarks.append(benchmark_name)

    if failed_benchmarks:
        print(f"\n--- Some benchmarks failed: {', '.join(failed_benchmarks)} ---", file=sys.stderr)
        sys.exit(1)

    end_time = datetime.datetime.now()
    print(f"--- Entire run ended at: {end_time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    print(f"--- Total duration: {end_time - start_time} ---")

if __name__ == "__main__":
    main()
