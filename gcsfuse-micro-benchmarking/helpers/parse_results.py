import csv
import subprocess
import os
import tempfile
import shlex
import shutil
import json
import fnmatch
import statistics
import datetime
from datetime import timezone
from .vm_metrics import get_vm_cpu_utilization_points


def calculate_stats(data):
    """Calculates mean and standard deviation, handling single-element lists."""
    if not data:
        return None, None
    if len(data) == 1:
        return data[0], 0.0
    return statistics.fmean(data), statistics.stdev(data)


def process_fio_metrics_and_vm_metrics(fio_metrics, timestamps, vm_cfg, fetch_vm_metrics=True):
    """
    Processes FIO and VM metrics to generate a combined performance report.

    Args:
        fio_metrics (list): A list of JSON objects, where each object is the result of one FIO iteration.
        timestamps (list): A list of tuples, where each tuple contains the (start_time, end_time) for each FIO iteration.
        vm_cfg (dict): The configuration dictionary for the VM.
        fetch_vm_metrics (bool): Whether to fetch VM CPU/memory metrics. Default True.
    """
    if len(fio_metrics) != len(timestamps):
        print(f"Warning: Mismatch in the number of records - fio_metrics: {len(fio_metrics)}, timestamps: {len(timestamps)}")
        print("Skipping VM metrics for this test case due to mismatch")
        fetch_vm_metrics = False

    # --- 1. Process FIO Metrics ---
    # FIO throughput (bw) is in KiB/s. We will collect both read and write values.
    read_bws = [
        job['read']['bw']
        for metrics in fio_metrics
        for job in metrics['jobs']
        if 'read' in job and 'bw' in job['read']
    ]

    write_bws = [
        job['write']['bw']
        for metrics in fio_metrics
        for job in metrics['jobs']
        if 'write' in job and 'bw' in job['write']
    ]

    # FIO latency (lat_ns) is in milliseconds.
    read_lats = [
        job['read']['lat_ns']['mean']/1000000.0
        for metrics in fio_metrics
        for job in metrics['jobs']
        if 'read' in job and 'lat_ns' in job['read']
    ]

    write_lats = [
        job['write']['lat_ns']['mean']/1000000.0
        for metrics in fio_metrics
        for job in metrics['jobs']
        if 'write' in job and 'lat_ns' in job['write']
    ]

    read_iops = [
        job['read']['iops']
        for metrics in fio_metrics
        for job in metrics['jobs']
        if 'read' in job and 'iops' in job['read']
    ]

    write_iops = [
        job['write']['iops']
        for metrics in fio_metrics
        for job in metrics['jobs']
        if 'write' in job and 'iops' in job['write']
    ]

    # Calculate average and standard deviation for FIO metrics.
    # Note: statistics.fmean and statistics.stdev are used for floating point data.
    fio_report = {}
    avg_read_bw, stdev_read_bw = calculate_stats(read_bws)
    if avg_read_bw is not None:
        # 1 MiB/s = 1024 KiB/s
        fio_report['avg_read_throughput_mbps'] = avg_read_bw / 1000.0
        fio_report['stdev_read_throughput_mbps'] = stdev_read_bw / 1000.0

    avg_write_bw, stdev_write_bw = calculate_stats(write_bws)
    if avg_write_bw is not None:
        # 1 MiB/s = 1024 KiB/s
        fio_report['avg_write_throughput_mbps'] = avg_write_bw / 1000.0
        fio_report['stdev_write_throughput_mbps'] = stdev_write_bw / 1000.0

    avg_read_lat, stdev_read_lat = calculate_stats(read_lats)
    if avg_read_lat is not None:
        fio_report['avg_read_latency_ms'] = avg_read_lat
        fio_report['stdev_read_latency_ms'] = stdev_read_lat

    avg_write_lat, stdev_write_lat = calculate_stats(write_lats)
    if avg_write_lat is not None:
        fio_report['avg_write_latency_ms'] = avg_write_lat
        fio_report['stdev_write_latency_ms'] = stdev_write_lat
    
    avg_read_iops, stdev_read_iops = calculate_stats(read_iops)
    if avg_read_iops is not None:
        fio_report['avg_read_iops'] = avg_read_iops
        fio_report['stdev_read_iops'] = stdev_read_iops
    avg_write_iops, stdev_write_iops = calculate_stats(write_iops)
    if avg_write_iops is not None:
        fio_report['avg_write_iops'] = avg_write_iops
        fio_report['stdev_write_iops'] = stdev_write_iops

    # --- 2. Process VM Metrics ---
    if fetch_vm_metrics:
        vm_name = vm_cfg['instance_name']
        project = vm_cfg['project']
        zone = vm_cfg['zone']

        all_cpu_utilizations = []

        for i, ts in enumerate(timestamps):
            start_time = datetime.datetime.strptime(ts['start_time'], "%Y-%m-%dT%H:%M:%S%z")
            end_time = datetime.datetime.strptime(ts['end_time'], "%Y-%m-%dT%H:%M:%S%z")
            try:
                print(f"Fetching CPU for interval {i+1}: {start_time} to {end_time}")
                cpu_points = get_vm_cpu_utilization_points(vm_name, project, zone, start_time, end_time)
                if cpu_points:
                    avg_cpu_for_interval = max(cpu_points)
                    all_cpu_utilizations.append(avg_cpu_for_interval)
                    # print(f"  Interval {i+1}: Found {len(cpu_points)} CPU points, Avg: {avg_cpu_for_interval:.4f}")
                else:
                    print(f"  Interval {i+1}: No CPU data points found.")
            except Exception as e:
                print(f"Error fetching VM metrics for interval {start_time} to {end_time}: {e}")
                continue

        vm_report = {}
        avg_cpu, stdev_cpu = calculate_stats(all_cpu_utilizations)
        if avg_cpu is not None:
            vm_report['avg_cpu_utilization_percent'] = avg_cpu * 100
            vm_report['stdev_cpu_utilization_percent'] = stdev_cpu * 100
            vm_report['cpu_data_point_count'] = len(all_cpu_utilizations)
        else:
            vm_report['avg_cpu_utilization_percent'] = None
            vm_report['stdev_cpu_utilization_percent'] = None
            vm_report['cpu_data_point_count'] = 0
    else:
        # Skip VM metrics fetching
        vm_report = {
            'avg_cpu_utilization_percent': None,
            'stdev_cpu_utilization_percent': None,
            'cpu_data_point_count': 0
        }

    # --- 3. Generate Final Combined Report ---
    final_report = {
        'fio_metrics': fio_report,
        'vm_metrics': vm_report
    }
    
    # Calculate the CPU% per GB/s metric.
    # We combine read and write throughput and convert from KiB/s to GB/s.
    total_avg_throughput_mbps = (
        fio_report.get('avg_read_throughput_mbps', 0) +
        fio_report.get('avg_write_throughput_mbps', 0)
    )

    # Get the average CPU percent, will be None if not calculated
    avg_cpu_percent = vm_report.get('avg_cpu_utilization_percent')

    # Check if both throughput and avg_cpu_percent are valid for calculation
    if total_avg_throughput_mbps > 0 and avg_cpu_percent is not None:
        # Convert MiB/s to decimal GB/s (Gigabytes per second)
        # 1 MiB = 1024 * 1024 Bytes
        # 1 GB = 1000 * 1000 * 1000 Bytes
        bytes_per_mb = 1000.0 * 1000.0
        bytes_per_gb = 1000.0 * 1000.0 * 1000.0
        
        total_avg_throughput_gbps = total_avg_throughput_mbps * bytes_per_mb / bytes_per_gb

        if total_avg_throughput_gbps > 1e-9:  # Avoid division by zero or near-zero
            final_report['cpu_percent_per_gbps'] = avg_cpu_percent / total_avg_throughput_gbps
        else:
            # Handle cases with very low or zero throughput
            final_report['cpu_percent_per_gbps'] = float('inf')
    else:
        final_report['cpu_percent_per_gbps'] = None
        if avg_cpu_percent is None:
            print("Cannot calculate cpu_percent_per_gbps: Average CPU utilization is not available.")
        if total_avg_throughput_mbps <= 0:
             print("Cannot calculate cpu_percent_per_gbps: Total average throughput is zero or less.")

    return final_report


def download_artifacts_from_bucket(benchmark_id: str, artifacts_bucket: str):
    """
    Downloads artifacts for a given benchmark_id from a GCS bucket.

    Uses 'gcloud storage cp -r' to copy the folder named benchmark_id
    from gs://{artifacts_bucket}/ to a local temporary directory.

    Args:
        benchmark_id: The name of the folder within the bucket to download.
        artifacts_bucket: The GCS bucket name.

    Returns:
        The absolute local file path to the downloaded folder
        (e.g., /tmp/tmpxyz/{benchmark_id}).
        The caller is responsible for cleaning up this temporary directory
        when no longer needed (e.g., using shutil.rmtree()).

    Raises:
        subprocess.CalledProcessError: If the gcloud command fails.
        FileNotFoundError: If the downloaded folder is not found after copy.
    """
    if not benchmark_id:
        raise ValueError("benchmark_id cannot be empty")
    if not artifacts_bucket:
        raise ValueError("artifacts_bucket cannot be empty")

    # Create a unique temporary directory
    # tempfile.mkdtemp() creates a new directory with a unique name.
    local_temp_base_dir = tempfile.mkdtemp()

    source_uri = f"gs://{artifacts_bucket}/{benchmark_id}"

    # Command to recursively copy the GCS folder to the local temp directory.
    # The benchmark_id folder itself will be created inside local_temp_base_dir.
    command = [
        "gcloud", "storage", "cp",
        "-r",  # Recursive
        source_uri,
        local_temp_base_dir
    ]

    # print(f"Running command: {' '.join(shlex.quote(arg) for arg in command)}")

    try:
        result = subprocess.run(
            command,
            check=True,  # Raise an exception for non-zero exit codes
            capture_output=True,
            text=True
        )
        # print("gcloud stdout:", result.stdout)
        # print("gcloud stderr:", result.stderr)

        # The downloaded folder will be at local_temp_base_dir/benchmark_id
        downloaded_folder_path = os.path.join(local_temp_base_dir, benchmark_id)

        if not os.path.isdir(downloaded_folder_path):
            # Cleanup the base temp dir if the expected subfolder wasn't created.
            shutil.rmtree(local_temp_base_dir)
            raise FileNotFoundError(
                f"Expected downloaded folder not found at: {downloaded_folder_path}"
                f" after running gcloud command. Check stderr for details."
            )

        print(f"Artifacts downloaded to: {downloaded_folder_path}")
        return downloaded_folder_path

    except subprocess.CalledProcessError as e:
        print(f"gcloud storage cp command failed:")
        print(f"  Return Code: {e.returncode}")
        print(f"  Stderr: {e.stderr}")
        print(f"  Stdout: {e.stdout}")
        # Clean up the potentially partially created temp directory
        shutil.rmtree(local_temp_base_dir)
        raise
    except Exception as e:
        # Clean up in case of other errors
        shutil.rmtree(local_temp_base_dir)
        raise


def load_csv_to_object(filepath):
    """Loads a CSV file into a list of dictionaries."""
    data = []
    with open(filepath, 'r', newline='') as csvfile:
        # Use csv.DictReader to automatically map rows to dictionaries
        reader = csv.DictReader(csvfile)
        for row in reader:
            data.append(row)
    return data


def clean_load_json_to_object(filepath: str):
    """
    Loads a JSON file into a Python object, skipping any leading
    lines that are not part of the JSON content.

    It looks for the first line that, after stripping leading whitespace,
    starts with '{' or '['.

    Args:
        filepath: The full path to the JSON file.

    Returns:
        A Python object (dict, list, etc.) representing the JSON content.
        Returns None if the file is not found, no valid JSON start
        is found, or if JSON decoding fails.
    """
    if not os.path.exists(filepath):
        print(f"Error: File not found: {filepath}")
        return None

    try:
        with open(filepath, 'r') as jsonfile:
            lines = jsonfile.readlines()

        json_start_line_index = -1
        for i, line in enumerate(lines):
            stripped_line = line.lstrip()
            if stripped_line.startswith('{') or stripped_line.startswith('['):
                json_start_line_index = i
                break

        if json_start_line_index != -1:
            # Join the lines from the start of the JSON content to the end
            json_string = "".join(lines[json_start_line_index:])
            try:
                data = json.loads(json_string)
                return data
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON from {filepath} starting from line {json_start_line_index + 1}: {e}")
                # Optionally print the snippet that failed to parse
                # print(f"Attempted to parse:\n{json_string[:500]}...")
                return None
        else:
            print(f"Error: No JSON start character ('{{' or '[') found in {filepath}")
            return None

    except Exception as e:
        print(f"An unexpected error occurred while processing {filepath}: {e}")
        return None


def process_fio_output_files(file_pattern, directory_path: str):
    """
    Finds all files matching 'zyx*.json' in the given directory,
    loads them using load_json_to_object, and collects the results.

    Args:
        directory_path: The path to the directory to search in.

    Returns:
        A list of objects, where each object is the result of
        loading a matching JSON file. Contains None for files
        that failed to load.
    """
    if not os.path.isdir(directory_path):
        print(f"Error: Directory not found: {directory_path}")
        return []

    loaded_objects = []

    # print(f"Searching for files matching '{file_pattern}' in '{directory_path}'...")

    for filename in os.listdir(directory_path):
        if fnmatch.fnmatch(filename, file_pattern):
            full_filepath = os.path.join(directory_path, filename)
            if os.path.isfile(full_filepath):
                loaded_object = clean_load_json_to_object(full_filepath)
                loaded_objects.append(loaded_object)
            else:
                print(f"Skipping non-file entry: {filename}")

    return loaded_objects


def get_avg_perf_metrics_for_job(case, artifacts_dir, vm_cfg, fetch_vm_metrics=True):
    bs=case['bs']
    file_size=case['file_size']
    iodepth=case['iodepth']
    iotype=case['iotype']
    threads=case['threads']
    nrfiles=case['nrfiles']

    # Construct relevant filepaths
    testcase=f'fio_output_{bs}_{file_size}_{iodepth}_{iotype}_{threads}_{nrfiles}'
    raw_data_path=f'{artifacts_dir}/raw-results/{testcase}/'
    fio_output_path="fio_output_iter"
    timestamps_file=raw_data_path+"timestamps.csv"

    # Get the fio metrics
    fio_metrics= process_fio_output_files(f'{fio_output_path}*.json', raw_data_path)
    
    # Get the VM metrics
    timestamps= load_csv_to_object(timestamps_file)
    # print(timestamps)

    metrics = process_fio_metrics_and_vm_metrics(fio_metrics, timestamps, vm_cfg, fetch_vm_metrics)
    
    metrics['bs']=bs
    metrics['file_size']=file_size
    metrics['iodepth']=iodepth
    metrics['iotype']=iotype
    metrics['threads']=threads
    metrics['nrfiles']=nrfiles

    return metrics


def load_testcases_from_csv(filepath):
    data = load_csv_to_object(filepath)
    return data


def parse_benchmark_results(benchmark_id, ARTIFACTS_BUCKET, cfg, fetch_vm_metrics=True):
    vm_cfg={
        'instance_name': cfg.get('bench_env').get('gce_env').get('vm_name'),
        'zone': cfg.get('bench_env').get('zone'),
        'project': cfg.get('bench_env').get('project'),
    }

    artifacts = download_artifacts_from_bucket(benchmark_id, ARTIFACTS_BUCKET)
    
    testcases = load_testcases_from_csv(f'{artifacts}/fio_job_cases.csv')
    metrics={}
    for tc in testcases:
        tc_metrics=get_avg_perf_metrics_for_job(tc, artifacts, vm_cfg, fetch_vm_metrics)
        key = f"{tc['bs']}_{tc['file_size']}_{tc['iodepth']}_{tc['iotype']}_{tc['threads']}_{tc['nrfiles']}"
        metrics[key] = tc_metrics     
    return artifacts, metrics


# Example Usage:
if __name__ == '__main__':
    try:
        # Replace with your bucket and a folder you want to test with
        bucket = "non-existent-bucket "  # Example bucket
        benchmark = "randomid" # Example folder

        print(f"Attempting to download gs://{bucket}/{benchmark}")

        # Create a dummy folder and file in the bucket for testing
        # You would need to do this manually or via another script setup
        # Example commands to set up for test:
        # echo "hello world" > /tmp/dummy.txt
        # gcloud storage cp /tmp/dummy.txt gs://gcs-fuse-test/test-artifact-folder/dummy.txt
        cfg={
            'bench_env': {
                'gce_env': {
                    'vm_name': 'non-existent-vm',
                },
                'zone': 'us-central1-a',
                'project': 'non-existent-project',
            },
        }

        downloaded_path, metrics = parse_benchmark_results(benchmark, bucket, cfg)
        # --- Pretty Print the metrics dictionary ---
        print("\n--- Pretty Printed Metrics ---")
        pretty_metrics = json.dumps(metrics, indent=4, sort_keys=True)
        print(pretty_metrics)


        # IMPORTANT: Clean up the temporary directory
        print(f"\nCleaning up temporary directory: {os.path.dirname(downloaded_path)}")
        shutil.rmtree(os.path.dirname(downloaded_path))
        print("Cleanup complete.")

    except ValueError as e:
        print(f"Input Error: {e}")
    except FileNotFoundError as e:
        print(f"Error: {e}")
    except subprocess.CalledProcessError:
        print("Download failed, check logs above.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")