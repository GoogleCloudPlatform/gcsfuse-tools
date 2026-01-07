import os
import sys
import glob
import subprocess
import json
import logging
import argparse
import shutil

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
    # Pattern specific to Google Cloud Local SSDs
    devices = glob.glob('/dev/disk/by-id/google-local-nvme-ssd-*')
    return devices

def create_raid_array(devices):
    """Creates a RAID 0 array from the provided devices."""
    num_devices = len(devices)
    logger.info(f"Found {num_devices} local SSDs. Creating RAID 0 array...")

    # Stop array if it already exists to avoid conflicts
    if os.path.exists(RAID_DEVICE):
        logger.warning(f"{RAID_DEVICE} already exists. Attempting to stop and remove...")
        subprocess.run(['mdadm', '--stop', RAID_DEVICE], check=False)
        subprocess.run(['mdadm', '--remove', RAID_DEVICE], check=False)

    # Create RAID 0
    # mdadm --create /dev/md0 --level=0 --raid-devices=N /dev/disk/...
    cmd = [
        'mdadm', '--create', RAID_DEVICE, 
        '--level=0', 
        f'--raid-devices={num_devices}', 
        '--force', '--run'
    ] + devices

    try:
        # piping yes to avoid interactive prompts
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate(input=b'y\n')
        
        if process.returncode != 0:
            logger.error(f"Failed to create RAID: {stderr.decode()}")
            sys.exit(1)
        
        logger.info("RAID array created successfully.")

    except Exception as e:
        logger.error(f"Error during RAID creation: {str(e)}")
        sys.exit(1)

def format_and_mount():
    """Formats the RAID device and mounts it."""
    try:
        logger.info("Formatting RAID array with ext4...")
        subprocess.check_call(['mkfs.ext4', '-F', RAID_DEVICE])

        logger.info(f"Mounting {RAID_DEVICE} to {MOUNT_POINT}...")
        os.makedirs(MOUNT_POINT, exist_ok=True)
        
        # Unmount if something is already there
        subprocess.run(['umount', MOUNT_POINT], check=False, stderr=subprocess.DEVNULL)
        
        subprocess.check_call(['mount', RAID_DEVICE, MOUNT_POINT])
        subprocess.check_call(['chmod', 'a+w', MOUNT_POINT]) # Grant write permissions
        
        logger.info("Mount successful.")
        subprocess.run(['df', '-h', MOUNT_POINT])

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed during format/mount: {str(e)}")
        sys.exit(1)

def run_fio_benchmarks():
    """
    Runs FIO tests specifically tuned to determine 'best case' throughput 
    relevant for file cache systems (like gcsfuse).
    """
    logger.info("Starting FIO benchmarks for LSSD Throughput...")
    
    # Define tests. 
    # GCSfuse cache typically benefits from high sequential throughput.
    tests = [
        {
            "name": "seq_write_fill",
            "desc": "Sequential Write (Cache Population Simulation)",
            "args": ["--rw=write", "--bs=1M", "--iodepth=64", "--numjobs=4"]
        },
        {
            "name": "seq_read_throughput",
            "desc": "Sequential Read (Large File Cache Hit Simulation)",
            "args": ["--rw=read", "--bs=1M", "--iodepth=64", "--numjobs=4"]
        },
        {
            "name": "rand_read_iops",
            "desc": "Random Read 4k (Small File/Metadata Cache Simulation)",
            "args": ["--rw=randread", "--bs=4k", "--iodepth=64", "--numjobs=8"]
        }
    ]

    results = {}

    for test in tests:
        logger.info(f"Running Test: {test['desc']}...")
        fio_file = os.path.join(MOUNT_POINT, "fio_test_file")
        
        # Base FIO command
        cmd = [
            'fio',
            '--name=' + test['name'],
            '--filename=' + fio_file,
            '--size=10G',           # Sufficient size to bypass RAM caching effects
            '--ioengine=libaio',    # Async IO is standard for high performance
            '--direct=1',           # Direct IO to test disk, not OS page cache
            '--group_reporting',
            '--runtime=60',
            '--time_based',
            '--output-format=json'
        ] + test['args']

        try:
            result = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            data = json.loads(result)
            job_data = data['jobs'][0]
            
            # Extract relevant metrics based on RW type
            if 'read' in test['args'][0]:
                bw = job_data['read']['bw_bytes'] / (1024 * 1024) # MB/s
                iops = job_data['read']['iops']
            else:
                bw = job_data['write']['bw_bytes'] / (1024 * 1024)
                iops = job_data['write']['iops']

            logger.info(f"Result {test['name']}: BW={bw:.2f} MB/s, IOPS={iops:.2f}")
            results[test['name']] = {"bw_mbps": bw, "iops": iops}

            # Cleanup file after test
            if os.path.exists(fio_file):
                os.remove(fio_file)

        except subprocess.CalledProcessError as e:
            logger.error(f"FIO Test failed: {e.output.decode()}")

    return results

def run():
    check_dependencies()
    devices = get_lssd_devices()

    if not devices:
        logger.info("No local SSDs found (no lssd). Exiting.")
        # As per request: "stops prints no lssd"
        print("no lssd")
        return

    logger.info(f"Detected devices: {devices}")
    
    create_raid_array(devices)
    format_and_mount()
    
    metrics = run_fio_benchmarks()
    
    print("\n--- Final LSSD Performance Report ---")
    print(json.dumps(metrics, indent=2))
    print("-------------------------------------")

if __name__ == "__main__":
    run()