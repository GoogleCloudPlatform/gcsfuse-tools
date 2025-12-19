import random
import string
import os
import yaml
import csv
import shutil
import warnings
import re
import subprocess
import shlex
import time
from datetime import datetime, timedelta
from .constants import *
from . import environment
from . import bucket


def generate_random_string(length):
    """Generates a random string of fixed length."""
    characters = string.ascii_lowercase + string.digits
    return ''.join(random.choice(characters) for i in range(length))


def generate_artifacts_dir(benchmark_id: str) -> str | None:
    """
    Creates a directory named benchmark_id inside /tmp.

    Args:
        benchmark_id: The name of the directory to create.

    Returns:
        The full path to the created directory, or None if creation failed.
    """
    if not benchmark_id:
        print("Error: benchmark_id cannot be empty.")
        return None

    base_dir = '/tmp'
    # Sanitize benchmark_id to avoid path traversal issues, though less critical in /tmp
    # For example, ensure benchmark_id doesn't contain '..'
    safe_benchmark_id = os.path.basename(benchmark_id)
    if safe_benchmark_id != benchmark_id:
        print(f"Warning: benchmark_id '{benchmark_id}' was sanitized to '{safe_benchmark_id}'")
        # Depending on requirements, you might want to raise an error here

    path = os.path.join(base_dir, safe_benchmark_id)

    try:
        os.makedirs(path, exist_ok=True)
        print(f"Artifacts directory path: '{path}'")
        return path
    except OSError as e:
        print(f"Error creating directory '{path}': {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None


def copy_to_artifacts_dir(artifacts_dir, oldpath, filename):
    # Move file from oldpath to artifacts dir under new name
    try:
        shutil.copy(oldpath, os.path.join(artifacts_dir, filename))
    except FileNotFoundError :
        print(f"Error: The file '{oldpath}' was not found.")
    except Exception as e :
        print(f"Error while moving the file: {e}")
    return os.path.join(artifacts_dir, filename)


def parse_bench_config(config_filepath):
    with open(config_filepath, 'r') as file:
        config = yaml.safe_load(file) 
    return config  


def generate_fio_job_file(job_details):
    """Generates a FIO job file based on the provided job details."""
    # Extract job details, checking for empty lists and falling back to defaults
    bs_values = job_details.get('bs')
    file_size_values = job_details.get('file_size')
    iodepth_values = job_details.get('iodepth')
    iotype_values = job_details.get('iotype')
    threads_values = job_details.get('threads')
    nrfiles_values = job_details.get('nrfiles')


    # Generate combinations of parameters
    job_configs = []
    for bs in bs_values:
        for file_size in file_size_values:
            for iodepth in iodepth_values:
                for iotype in iotype_values:
                    for threads in threads_values:
                        for nrfiles in nrfiles_values:
                            job_configs.append({
                                'bs': bs,
                                'file_size': file_size,
                                'iodepth': iodepth,
                                'iotype': iotype,
                                'threads': threads,
                                'nrfiles': nrfiles,
                            })
    
    filepath = os.path.join("/tmp/fio_job_" + generate_random_string(10) + ".csv")

    with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=['bs', 'file_size', 'iodepth', 'iotype', 'threads','nrfiles'], quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for case in job_configs:
            writer.writerow({
                'bs': case['bs'],
                'file_size': case['file_size'],
                'iodepth': case['iodepth'],
                'iotype': case['iotype'],
                'threads': case['threads'],
                'nrfiles': case['nrfiles'],
            })
            
    return filepath

  
def get_jobcases_file(artifacts_dir, config):
    filepath = default_fio_jobcases_file
    if config.get('job_details') :
        if config.get('job_details').get('file_path') and os.path.exists(config.get('job_details').get('file_path')):
            filepath = config.get('job_details').get('file_path')
        else:
            filepath= generate_fio_job_file(config.get('job_details'))
    filepath = copy_to_artifacts_dir(artifacts_dir, filepath, "fio_job_cases.csv")
    return filepath


def get_job_template(artifacts_dir, config):
    config_path=config.get('fio_jobfile_template')
    if not os.path.exists(config_path):
            print("The specified fio jobfile template does not exist. Proceeding with default")
            config_path = "./resources/jobfile.fio"
    # Move to the artifacts_dir
    new_filepath = copy_to_artifacts_dir(artifacts_dir, config_path, "jobfile.fio")
    return new_filepath
        

def get_gcsfuse_mount_config(artifacts_dir, config):
    config_path=config.get('mount_config_file')
    if not os.path.exists(config_path):
        print("The specified mount config file does not exist. Proceeding with default")
        config_path = "./resources/mount_config.yml"
    # Move to the artifacts_dir
    new_filepath = copy_to_artifacts_dir(artifacts_dir, config_path, "mount_config.yml")
    return new_filepath
        

def get_version_details(artifacts_dir, config):
    filepath="/tmp/version_details.yml"
    version_details = config.get('version_details')
    with open(filepath, 'w') as file:
            file.write(f"go_version: {version_details.get('go_version')}\n")            
            file.write(f"fio_version: {version_details.get('fio_version')}\n")            
            file.write(f"gcsfuse_version_or_commit: {version_details.get('gcsfuse_version_or_commit')}\n") 
     # Move to the artifacts_dir
    new_filepath = copy_to_artifacts_dir(artifacts_dir, filepath, "version_details.yml")
    return new_filepath


def generate_benchmarking_resources(artifacts_dir, cfg):
    fio_jobcases_filepath= get_jobcases_file(artifacts_dir, cfg)
    print(f"Generated testcases for benchmarking at : {fio_jobcases_filepath}")

    fio_job_template = get_job_template(artifacts_dir, cfg)
    print(f"Generated job template for benchmarking at : {fio_job_template}")

    mount_config = get_gcsfuse_mount_config(artifacts_dir, cfg)
    print(f"Generated mount config for benchmarking at : {mount_config}")

    version_details = get_version_details(artifacts_dir, cfg)
    print(f"Generated version details for benchmarking at : {version_details}")


def create_benchmark_vm(cfg):
    """
    Creates the GCE VM for benchmarking based on the provided configuration.

    Args:
        cfg (dict): The benchmark configuration dictionary.

    Returns:
        bool: True if the VM was created successfully, False otherwise.
    """
    vm_details = cfg.get('bench_env').get('gce_env')
    print("--- Creating GCE VM for benchmarking ---")
    success = environment.create_and_run_on_gce_vm(vm_details)
    if not success:
        print("--- Failed to create GCE VM. ---")
    return success
  

def copy_directory_to_bucket(local_dir, bucket_name):
    """
    Copies a local directory to a GCS bucket using the gcloud CLI.

    Args:
        local_dir (str): The path to the local directory.
        bucket_name (str): The name of the GCS bucket.
    """
    if not os.path.isdir(local_dir):
        print(f"Error: Local directory '{local_dir}' not found.")
        return

    try:
        # Construct the gcloud command. The --recursive flag is essential
        # for copying entire directories. The destination is gs://bucket_name/local_dir_name.
        command = f"gcloud storage cp --recursive {local_dir} gs://{bucket_name}/"
        
        # Use shlex.split to safely parse the command string into a list of arguments.
        command_list = shlex.split(command)
        
        # Run the command and wait for it to complete.
        # check=True will raise an exception if the command returns a non-zero exit code.
        subprocess.run(
            command_list,
            check=True,
            capture_output=True,
            text=True
        )
        
        print(f"Directory '{local_dir}' copied successfully to gs://{bucket_name}/")

    except subprocess.CalledProcessError as e:
        print(f"Error copying directory '{local_dir}':")
        print("Error Output:", e.stderr)
        print("Return Code:", e.returncode)
    except FileNotFoundError:
        print("Error: The 'gcloud' command was not found. Please ensure the gcloud CLI is installed and in your system's PATH.")


def construct_gcloud_path(bucket_name, bench_id):
    return f'gs://{bucket_name}/{bench_id}/'


def wait_for_benchmark_to_complete(bucket_name, filepath, timeout=timeout, poll_interval=poll_interval):
    """
    Waits for a benchmark to complete by polling for a success or failure file 
    using the gcloud CLI.

    Args:
        bucket_name (str): The name of the GCS bucket to monitor.
        timeout (int): The maximum time in seconds to wait.
        poll_interval (int): The interval in seconds between each check.

    Returns:
        int: 0 if a 'success.txt' file is found.
        int: 1 if a 'failure.txt' file is found or the timeout is reached.
    """
    print(f"Monitoring bucket '{bucket_name}' for benchmark completion...")
    
    deadline = datetime.now() + timedelta(seconds=timeout)
    
    while datetime.now() < deadline:
        print(f"Polling for completion files at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...")
        
        # Construct the gcloud command to list files in the bucket
        command = f"gcloud storage ls {filepath}"
        command_list = shlex.split(command)
        
        try:
            # Run the command and capture the output
            result = subprocess.run(
                command_list,
                check=True,
                capture_output=True,
                text=True
            )

            # Check the command's standard output for the file names
            if 'success.txt' in result.stdout:
                print(f"Success! Found 'success.txt'. Benchmark completed successfully.")
                return True
            
            if 'failure.txt' in result.stdout:
                print(f"Failure! Found 'failure.txt'. Benchmark failed.")
                return False

        except subprocess.CalledProcessError as e:
            # This handles cases where the gcloud command itself fails
            print(f"Error during gcloud command: {e.stderr}")
            # We can choose to exit or continue based on the error
            # For this scenario, we'll continue, as the error might be
            # due to an empty bucket, which is a valid state to be in
            # while waiting.
            pass
        except FileNotFoundError:
            print("Error: The 'gcloud' command was not found.")
            return False # Exit with an error if gcloud isn't installed
        
        time.sleep(poll_interval)

    # If the loop completes, the timeout was reached
    print("Timeout reached. Neither success nor failure file was found.")
    return False
