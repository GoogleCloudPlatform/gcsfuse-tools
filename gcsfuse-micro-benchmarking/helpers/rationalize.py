# No more notorious config objects!
import sys
import subprocess
import json
from .constants import *

def rationalize_fio_job_template(fio_tmpl_config):
    if not fio_tmpl_config or fio_tmpl_config == "":
        return default_fio_job_template
    return fio_tmpl_config


def rationalize_mount_config_file(mount_config_file):
    if not mount_config_file or mount_config_file == "":
        return default_mount_config_file
    return mount_config_file


def rationalize_version_details(version_details):
    config ={
        'go_version': default_go_version,
        'fio_version': default_fio_version,
        'gcsfuse_version_or_commit': default_gcsfuse_version
    }

    if version_details:
        if version_details.get("go_version") and version_details.get("go_version") != "":
            config["go_version"]= version_details.get("go_version")
        if version_details.get("fio_version") and version_details.get("fio_version") != "":
            config["fio_version"]= version_details.get("fio_version")
        if version_details.get("gcsfuse_version_or_commit") and version_details.get("gcsfuse_version_or_commit") != "":
            config["gcsfuse_version_or_commit"]= version_details.get("gcsfuse_version_or_commit")    
        
    return config


def rationalize_job_details(job_details):
    config = {
        'bs': [default_bs],
        'file_size': [default_file_size],
        'iotype': [default_iotype],
        'iodepth': [default_iodepth],
        'threads': [default_threads],
        'nrfiles': [default_nrfiles],
    }

    if job_details:
        if job_details.get('file_path') and job_details.get('file_path') != "":
            config['file_path']=job_details.get('file_path')
        if job_details.get('bs') and job_details.get('bs') != "":
            config['bs']=job_details.get('bs')
        if job_details.get('file_size') and job_details.get('file_size') != "":
            config['file_size']=job_details.get('file_size')
        if job_details.get('iotype') and job_details.get('iotype') != "":
            config['iotype']=job_details.get('iotype')
        if job_details.get('iodepth') and job_details.get('iodepth') != "":
            config['iodepth']=job_details.get('iodepth')
        if job_details.get('threads') and job_details.get('threads') != "":
            config['threads']=job_details.get('threads')
        if job_details.get('nrfiles') and job_details.get('nrfiles') != "":
            config['nrfiles']=job_details.get('nrfiles')  

    return config


def rationalize_gce_vm_config(default_cfg, new_cfg):
    if new_cfg.get('vm_name') and new_cfg.get('vm_name') != "":
        default_cfg['vm_name'] = new_cfg['vm_name']
    if new_cfg.get('machine_type') and new_cfg.get('machine_type') != "":
        default_cfg['machine_type'] = new_cfg['machine_type']
    if new_cfg.get('image_family') and new_cfg.get('image_family') != "":
        default_cfg['image_family'] = new_cfg['image_family']
    if new_cfg.get('image_project') and new_cfg.get('image_project') != "":
        default_cfg['image_project'] = new_cfg['image_project']
    if new_cfg.get('disk_size') and new_cfg.get('disk_size') != "":
        default_cfg['disk_size'] = new_cfg['disk_size']
    if new_cfg.get('startup_script') and new_cfg.get('startup_script') != "":
        default_cfg['startup_script'] = new_cfg['startup_script']
    return default_cfg


def check_bucket_storage_class(bucket_name: str):
    """
    Checks a GCS bucket's default storage class using 'gcloud storage buckets list'.

    - If the bucket does not exist, this function silently returns.
    - If the bucket exists and its default storage class is "RAPID",
      it raises a BucketStorageClassError.
    - Otherwise (bucket exists with a different storage class), it prints a
      success message and returns peacefully.

    Args:
        bucket_name: The name of the GCS bucket to check.

    Raises:
        BucketStorageClassError: If the bucket's default storage class is "RAPID".
        RuntimeError: If the 'gcloud' command-line tool is not found.
    """
    try:
        # Construct the gcloud command to list bucket details, filtering by the exact bucket name.
        # We request JSON output containing only the name and storageClass.
        cmd = [
            "gcloud",
            "storage",
            "buckets",
            "list",
            f"--filter=name={bucket_name}",  # Filter for the specific bucket URI
            "--format=json"  # Output format
        ]

        # Execute the gcloud command.
        # capture_output=True: Captures stdout and stderr.
        # text=True: Decodes stdout and stderr as strings.
        # check=False: Prevents raising an exception for non-zero gcloud exit codes.
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            # Log gcloud command errors to stderr for debugging.
            # These could be permission issues, gcloud internal errors, etc.
            # The function should still exit peacefully in these cases as per requirements.
            print(
                f"Warning: gcloud command exited with code {result.returncode} for bucket '{bucket_name}'.\n"
                f"Stderr: {result.stderr.strip()}",
                file=sys.stderr
            )
            return "invalid"

        try:
            # The expected output is a JSON array.
            bucket_list = json.loads(result.stdout)
        except json.JSONDecodeError:
            # Handle cases where gcloud output isn't valid JSON.
            print(
                f"Warning: Failed to parse JSON output from gcloud for bucket '{bucket_name}'.\n"
                f"Stdout: {result.stdout.strip()}",
                file=sys.stderr
            )
            # Exit peacefully.
            return "invalid"

        if not bucket_list:
            # The filter returned an empty list, meaning the bucket does not exist.
            # Silently ignore and return as per requirements.
            return "dne"

        # Since we filtered by exact name, we expect at most one item.
        bucket_info = bucket_list[0]
        storage_class = bucket_info.get("default_storage_class")
        return storage_class

    except FileNotFoundError:
        # This error occurs if the gcloud binary is not in the system's PATH.
        raise RuntimeError("gcloud command not found. Ensure Google Cloud SDK is installed and in your PATH.") from None
    except Exception as e:
        # Catch any other unexpected exceptions.
        print(f"An unexpected error occurred while checking bucket '{bucket_name}': {e}", file=sys.stderr)
        # Exit peacefully for other issues.
        return


def check_if_bucket_is_existing_and_zonal(bucket_name):
    storage_class = check_bucket_storage_class(bucket_name)
    if storage_class == "invalid" :
        raise RuntimeError(f"Error while fetching the bucket attributes. Exiting...")
    elif storage_class == "dne":
        print(f"Zonal Bucket {bucket_name} does not exist but a replacement will be created later (benchmark_id-bkt)...")
    elif storage_class != "RAPID":
        raise RuntimeError(f"Expected zonal bucket , but found non-zonal {bucket_name}")
    else:
        print("Valid bucket for the benchmarking...")


def check_if_bucket_is_existing_and_regional(bucket_name):
    storage_class = check_bucket_storage_class(bucket_name)
    if storage_class == "invalid" :
        raise RuntimeError(f"Error while fetching the bucket attributes. Exiting...")
    elif storage_class == "dne":
        print(f"Regional Bucket {bucket_name} does not exist but a replacement will be created later (benchmark_id-bkt)...")
    elif storage_class == "RAPID":
        raise RuntimeError(f"Expected regional bucket , but found zonal {bucket_name}")
    else:
        print("Valid bucket for the benchmarking...")
    

def rationalize_zonal_gcs_bucket(default_cfg, new_cfg):
    if new_cfg.get('bucket_name') and new_cfg.get('bucket_name') != '':
        default_cfg['bucket_name'] = new_cfg.get('bucket_name')
        # if it does not exist, then validate.py takes care of generating the correct bucket
        try :
            check_if_bucket_is_existing_and_zonal(new_cfg.get('bucket_name'))
        except RuntimeError as e:
            print("Warning! The provided bucket is not compatible with the rest of the config")
            default_cfg['bucket_name']=""
           
    if new_cfg.get('placement') and new_cfg.get('placement') != '' and new_cfg.get('placement') != default_cfg.get('placement'):
        print(f"Warning! The placement for the zonal bucket is different from the benchmarking zone passed. Falling back to benchmarking zone {default_cfg.get('placement')}")
    if new_cfg.get('storage_class') and new_cfg.get('storage_class') != '' and new_cfg.get('storage_class') != default_cfg.get('storage_class'):
        print(f"Warning! The storage class passed for zonal bucket ({new_cfg.get('storage_class')}) is invalid. Falling to RAPID")
    if new_cfg.get('enable_hns') and new_cfg.get('enable_hns') == False:
        print(f"Warning! Explicitly passing enable_hns false for zonal bucket, is invalid")
    return default_cfg


def rationalize_regional_gcs_bucket(default_cfg, new_cfg):
    if new_cfg.get('bucket_name') and new_cfg.get('bucket_name') != '':
        default_cfg['bucket_name'] = new_cfg.get('bucket_name')
        # if it does not exist, then validate.py takes care of generating the correct bucket
        try :
            check_if_bucket_is_existing_and_regional(new_cfg.get('bucket_name'))
        except RuntimeError as e:
            print("Warning! The provided bucket is not compatible with the rest of the config")
            default_cfg['bucket_name']=""
    if new_cfg.get('placement') and new_cfg.get('placement') != '' :
        print(f"Warning! The placement for regional bucket must be empty. Passed {new_cfg.get('placement')}")
    if new_cfg.get('storage_class') and new_cfg.get('storage_class') == 'RAPID' :
        print(f"Warning! The storage class passed for regional bucket ({new_cfg.get('storage_class')}) is invalid. Falling to default {default_cfg.get('storage_class')}")
    if new_cfg.get('enable_hns') and new_cfg.get('enable_hns') != '':
        default_cfg['enable_hns']=new_cfg.get('enable_hns')
    return default_cfg


def rationalize_gcs_bucket(zonal, default_cfg, new_cfg):
    if zonal:
        return rationalize_zonal_gcs_bucket(default_cfg, new_cfg)
    return rationalize_regional_gcs_bucket(default_cfg, new_cfg)


def rationalize_bench_env(zonal ,bench_env):
    gce_env_cfg = {
        'vm_name': "",
        'machine_type': default_machine_type,
        'image_family': default_image_family,
        'image_project': default_image_project,
        'disk_size': default_disk_size,
        'startup_script': default_startup_script,
    }

    gcs_bucket_cfg={
        'bucket_name': "",
        'placement': default_zone if zonal else "",
        'storage_class': "RAPID" if zonal else "",
        'enable_hns': True if zonal else False,
    }

    cfg = {
        "delete_after_use": default_delete_after_use,
        "zone": default_zone,
        "project": default_project,
        "gce_env": gce_env_cfg,
        "gcs_bucket": gcs_bucket_cfg,
    }

    if bench_env:
        if bench_env.get('delete_after_use') and bench_env.get('delete_after_use') != "":
            cfg['delete_after_use']=bench_env.get('delete_after_use')
            
        if bench_env.get('project') and bench_env.get('project') != "":
            cfg['project']=bench_env.get('project')
        
        if bench_env.get('zone') and bench_env.get('zone') != "":
            zone= bench_env.get('zone')
            cfg['zone']=zone
            gcs_bucket_cfg['placement']=zone if zonal else ""

        if bench_env.get("gce_env") and bench_env.get("gce_env") != "":
            cfg["gce_env"]=rationalize_gce_vm_config(gce_env_cfg, bench_env.get("gce_env"))
        
        if bench_env.get("gcs_bucket") and bench_env.get("gcs_bucket") != "":
            cfg["gcs_bucket"]=rationalize_gcs_bucket(zonal,gcs_bucket_cfg, bench_env.get("gcs_bucket"))        


    return cfg


def rationalize_config(cfg):
    if not cfg.get('zonal_benchmarking') or cfg.get('zonal_benchmarking') == "":
        cfg['zonal_benchmarking']=False
    if not cfg.get('reuse_same_mount') or cfg.get('reuse_same_mount') == "":
        cfg['reuse_same_mount']=False
    if not cfg.get('iterations') or cfg.get('iterations') == "":
        cfg['iterations']=default_iterations
    cfg['fio_jobfile_template']= rationalize_fio_job_template(cfg.get('fio_jobfile_template'))
    cfg['mount_config_file'] = rationalize_mount_config_file(cfg.get('mount_config_file'))
    cfg['version_details']= rationalize_version_details(cfg.get('version_details'))
    cfg['job_details']=rationalize_job_details(cfg.get('job_details')) 
    cfg['bench_env']=rationalize_bench_env(cfg.get('zonal_benchmarking'),cfg.get('bench_env'))
    return cfg


if __name__ == '__main__':
    cfg = {
        'zonal_benchmarking':False,
        'fio_jobfile_template': '/path/default',
        'mount_config_file': 'lambda',
        'version_details': {
            'gcsfuse_version_or_commit': 'beta',
        },
        'job_details':{
            'bs': ["1KB", "4KB"],
        },
        'bench_env':{
            'gce_env':{
                'machine_type': "linda",
            },
        }
    }
    print(rationalize_config(cfg))