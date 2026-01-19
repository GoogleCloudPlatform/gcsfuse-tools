import random
import string
import os
import yaml
import csv
import shutil
import subprocess
import time
import itertools
from datetime import datetime, timedelta
from .constants import *
from . import environment

def generate_random_string(length):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def generate_artifacts_dir(benchmark_id: str) -> str | None:
    if not benchmark_id: return None
    path = os.path.join('/tmp', os.path.basename(benchmark_id))
    try:
        os.makedirs(path, exist_ok=True)
        print(f"Artifacts directory path: '{path}'")
        return path
    except Exception as e:
        print(f"Error creating directory '{path}': {e}")
        return None


def copy_to_artifacts_dir(artifacts_dir, oldpath, filename):
    try:
        shutil.copy(oldpath, os.path.join(artifacts_dir, filename))
    except Exception as e:
        print(f"Error moving file {oldpath}: {e}")
    return os.path.join(artifacts_dir, filename)


def parse_bench_config(config_filepath):
    with open(config_filepath, 'r') as f: return yaml.safe_load(f)


def generate_fio_job_file(job_details):
    keys = ['bs', 'file_size', 'iodepth', 'iotype', 'threads', 'nrfiles']
    vals = [job_details.get(k, []) for k in keys]
    filepath = f"/tmp/fio_job_{generate_random_string(10)}.csv"
    with open(filepath, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(keys)
        w.writerows(itertools.product(*vals))
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

def _get_config_file(artifacts_dir, config, key, default, dest):
    path = config.get(key)
    if not path or not os.path.exists(path): path = default
    return copy_to_artifacts_dir(artifacts_dir, path, dest)

def get_job_template(artifacts_dir, config):
    return _get_config_file(artifacts_dir, config, 'fio_jobfile_template', "./resources/jobfile.fio", "jobfile.fio")

def get_gcsfuse_mount_config(artifacts_dir, config):
    return _get_config_file(artifacts_dir, config, 'mount_config_file', "./resources/mount_config.yml", "mount_config.yml")

def get_version_details(artifacts_dir, config):
    filepath = "/tmp/version_details.yml"
    version_details = config.get('version_details', {})
    with open(filepath, 'w') as f:
        for k in ['go_version', 'fio_version', 'gcsfuse_version_or_commit']:
            f.write(f"{k}: {version_details.get(k)}\n")
    return copy_to_artifacts_dir(artifacts_dir, filepath, "version_details.yml")


def generate_benchmarking_resources(artifacts_dir, cfg):
    print(f"Testcases: {get_jobcases_file(artifacts_dir, cfg)}")
    print(f"Job template: {get_job_template(artifacts_dir, cfg)}")
    print(f"Mount config: {get_gcsfuse_mount_config(artifacts_dir, cfg)}")
    print(f"Version details: {get_version_details(artifacts_dir, cfg)}")


def create_benchmark_vm(cfg):
    print("--- Creating GCE VM for benchmarking ---")
    if not environment.create_and_run_on_gce_vm(cfg.get('bench_env', {}).get('gce_env')):
        print("--- Failed to create GCE VM. ---")
        return False
    return True
  

def copy_directory_to_bucket(local_dir, bucket_name):
    if not os.path.isdir(local_dir): return
    try:
        subprocess.run(['gcloud', 'storage', 'cp', '--recursive', local_dir, f'gs://{bucket_name}/'], check=True, capture_output=True, text=True)
        print(f"Directory '{local_dir}' copied successfully to gs://{bucket_name}/")
    except subprocess.CalledProcessError as e:
        print(f"Error copying directory: {e.stderr}")


def construct_gcloud_path(bucket_name, bench_id):
    return f'gs://{bucket_name}/{bench_id}/'


def wait_for_benchmark_to_complete(bucket_name, filepath, timeout=timeout, poll_interval=poll_interval):
    print(f"Monitoring bucket '{bucket_name}'...")
    deadline = datetime.now() + timedelta(seconds=timeout)
    while datetime.now() < deadline:
        try:
            res = subprocess.run(['gcloud', 'storage', 'ls', filepath], check=True, capture_output=True, text=True)
            if 'success.txt' in res.stdout:
                print("Benchmark completed successfully.")
                return True
            
            if 'failure.txt' in res.stdout:
                print(f"Failure! Found 'failure.txt'. Benchmark failed.")
                return False
        except subprocess.CalledProcessError: pass
        except FileNotFoundError: return False
        time.sleep(poll_interval)

    print("Timeout reached. Neither success nor failure file was found.")
    return False
