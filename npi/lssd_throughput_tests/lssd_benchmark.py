import os
import sys
import glob
import subprocess
import json
import logging
import argparse
import shutil

# --- BigQuery Import ---
# This expects fio_benchmark_runner.py to be in the same directory (copied via Dockerfile)
try:
    import fio_benchmark_runner
except ImportError:
    fio_benchmark_runner = None
    print("WARNING: fio_benchmark_runner module not found. Results will NOT be uploaded to BigQuery.")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MOUNT_POINT = "/mnt/lssd"
RAID_DEVICE = "/dev/md0"

def check_dependencies():
    """Checks if required system tools are installed."""
    required_tools = ['mdadm', 'fio', 'mkfs.ext4']
    for tool in required_tools:
        if not shutil.which(tool):
            logger.error(f"Required tool '{tool}' is not installed.")
            sys.exit(1)

def get_lssd_devices():
    """Finds Google Local NVMe SSDs."""
    pattern = '/dev/disk/by-id/google-local-nvme-ssd-*'
    devices = glob.glob(pattern)
    
    if not devices:
        logger.warning(f"No devices found matching pattern: {pattern}")
        # Diagnostic check for volume mount
        if not os.path.exists('/dev/disk/by-id'):
            logger.error("CRITICAL ERROR: /dev/disk/by-id does not exist inside the container.")
            logger.error("FIX: Ensure '-v /dev:/dev' is passed in npi.py definitions.")
        print("no lssd")
        sys.exit(0)
            
    return devices

def create_raid_array(devices):
    """Creates a RAID 0 array from the provided devices."""
    num_devices = len(devices)
    logger.info(f"Found {num_devices} local SSDs. Creating RAID 0 array...")

    # Stop array if it already exists to avoid conflicts
    if os.path.exists(RAID_DEVICE):
        logger.warning(f"{RAID_DEVICE} already exists. Attempting to stop and remove...")
        subprocess.run(['mdadm', '--stop', RAID_DEVICE], check=False, stderr=subprocess.DEVNULL)
        subprocess.run(['mdadm', '--remove', RAID_DEVICE], check=False, stderr=subprocess.DEVNULL)

    # Create RAID 0
    # mdadm --create /dev/md0 --level=0 --raid-devices=N /dev/disk/...
    cmd = [
        'mdadm', '--create', RAID_DEVICE, 
        '--level=0', 
        f'--raid-devices={num_devices}', 
        '--force', '--run'
    ] + devices

    try:
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.communicate(input=b'y\n')
        
        if process.returncode != 0:
            logger.error("Failed to create RAID array.")
            sys.exit(1)
        
        logger.info("RAID array created successfully.")

    except Exception as e:
        logger.error(f"Error during RAID creation: {str(e)}")
        sys.exit(1)

def format_and_mount():
    """Formats the RAID device and mounts it."""
    try:
        logger.info("Formatting RAID array with ext4...")
        subprocess.check_call(['mkfs.ext4', '-F', '-q', RAID_DEVICE])

        logger.info(f"Mounting {RAID_DEVICE} to {MOUNT_POINT}...")
        os.makedirs(MOUNT_POINT, exist_ok=True)
        
        subprocess.run(['umount', MOUNT_POINT], check=False, stderr=subprocess.DEVNULL)
        subprocess.check_call(['mount', RAID_DEVICE, MOUNT_POINT])
        subprocess.check_call(['chmod', 'a+w', MOUNT_POINT])
        
        logger.info("Mount successful.")

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed during format/mount: {str(e)}")
        sys.exit(1)

def run_fio_benchmarks(iterations=1, project_id=None, dataset_id=None, table_id=None):
    """Runs FIO tests and uploads results to BigQuery."""
    logger.info("Starting FIO benchmarks for LSSD Throughput...")
    
    # Tests configured for LSSD throughput limits
    tests = [
        {"name": "seq_write_fill", "desc": "Sequential Write", "args": ["--rw=write", "--bs=1M", "--iodepth=64", "--numjobs=16"]},
        {"name": "seq_read_throughput", "desc": "Sequential Read", "args": ["--rw=read", "--bs=1M", "--iodepth=64", "--numjobs=16"]},
        {"name": "rand_read_iops", "desc": "Random Read 4k", "args": ["--rw=randread", "--bs=4k", "--iodepth=64", "--numjobs=16"]}
    ]

    all_results = []

    for i in range(iterations):
        logger.info(f"--- Iteration {i+1}/{iterations} ---")
        iter_results = {"iteration": i + 1}
        
        for test in tests:
            logger.info(f"Running Test: {test['desc']}...")
            fio_file = os.path.join(MOUNT_POINT, "fio_test_file")
            
            # We output to a JSON file so the uploader can read it
            json_output_path = f"/tmp/{test['name']}_iter{i+1}.json"
            
            cmd = [
                'fio',
                '--name=' + test['name'],
                '--filename=' + fio_file,
                '--size=10G',
                '--ioengine=libaio',
                '--direct=1',
                '--group_reporting',
                '--runtime=60',
                '--time_based',
                '--output-format=json',
                f'--output={json_output_path}'
            ] + test['args']

            try:
                # Run FIO
                subprocess.check_output(cmd, stderr=subprocess.STDOUT)
                
                # Parse for local logging
                with open(json_output_path, 'r') as f:
                    data = json.load(f)
                
                job_data = data['jobs'][0]
                if 'read' in job_data:
                    stats = job_data['read']
                else:
                    stats = job_data['write']
                bw = stats['bw_bytes'] / (1024 * 1024)
                iops = stats['iops']

                logger.info(f"Result {test['name']}: BW={bw:.2f} MiB/s, IOPS={iops:.2f}")
                iter_results[test['name']] = {"bw_MiBps": bw, "iops": iops}

                # --- UPLOAD TO BIGQUERY ---
                if fio_benchmark_runner and project_id and dataset_id and table_id:
                    logger.info("Uploading results to BigQuery...")
                    # Using a generic context since this isn't standard GCSFuse
                    fio_env = {
                        "TEST_TYPE": "lssd_raid0",
                        "TEST_NAME": test['name'],
                        "ITERATION": i + 1
                    }
                    
                    try:
                        fio_benchmark_runner.upload_results_to_bq(
                            project_id=project_id,
                            dataset_id=dataset_id,
                            table_id=table_id,
                            fio_json_path=json_output_path,
                            iteration=(i + 1),
                            gcsfuse_flags="LSSD_RAID0_NATIVE",
                            fio_env=fio_env,
                            cpu_limit_list=None
                        )
                    except Exception as e:
                        logger.error(f"BQ Upload failed: {e}")
                # --------------------------

                # Cleanup
                if os.path.exists(fio_file):
                    os.remove(fio_file)
                if os.path.exists(json_output_path):
                    os.remove(json_output_path)

            except subprocess.CalledProcessError as e:
                logger.error(f"FIO Test failed: {e.output.decode() if e.output else 'Unknown error'}")

        all_results.append(iter_results)

    return all_results

if __name__ == "__main__":
    # Parse arguments passed by npi.py
    parser = argparse.ArgumentParser()
    parser.add_argument('--iterations', type=int, default=1)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--bq-dataset-id', required=True)
    parser.add_argument('--bq-table-id', required=True)
    # npi.py passes these, but we don't strictly need them for LSSD logic
    parser.add_argument('--bucket-name', help="Ignored for LSSD test")
    parser.add_argument('--gcsfuse-flags', help="Ignored for LSSD test")
    
    # parse_known_args allows us to ignore other random flags if npi.py adds them
    args, unknown = parser.parse_known_args()
    
    check_dependencies()
    devices = get_lssd_devices()
    
    create_raid_array(devices)
    format_and_mount()
    
    metrics = run_fio_benchmarks(
        iterations=args.iterations,
        project_id=args.project_id,
        dataset_id=args.bq_dataset_id,
        table_id=args.bq_table_id
    )
    
    print("\n--- Final LSSD Performance Report ---")
    print(json.dumps(metrics, indent=2))
    print("-------------------------------------")
