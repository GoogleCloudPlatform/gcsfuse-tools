import subprocess
import json

def extract_region_from_zone(zone):
    return zone.rsplit('-', 1)[0]

def _get_vm_zone(vm_name, project):
    try:
        cmd = ['gcloud', 'compute', 'instances', 'list', f'--project={project}', f'--filter=name="{vm_name}"', '--format=json(zone)']
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        return data[0]['zone'].split('/')[-1] if data else None
    except (subprocess.CalledProcessError, json.JSONDecodeError, IndexError) as e:
        print(f"Error getting VM zone: {e}")
        return None

def _get_bucket_location(bucket_name, project):
    try:
        cmd = ['gcloud', 'storage', 'buckets', 'describe', f'gs://{bucket_name}', f'--project={project}', "--format=value(location)"]
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
    except subprocess.CalledProcessError:
        print(f"Error: Bucket '{bucket_name}' not found.")
        return None

def validate_if_vm_and_bucket_colocated(zonal, env_cfg, vm_name, bucket_name):
    project = env_cfg.get('project')
    vm_zone = _get_vm_zone(vm_name, project)
    does_vm_exist = bool(vm_zone)
    if not vm_zone:
        vm_zone = env_cfg.get('zone')
        
    bucket_location = _get_bucket_location(bucket_name, project)
    does_bkt_exist = bool(bucket_location)
    if not bucket_location:
        bucket_location = env_cfg.get('zone') if zonal else extract_region_from_zone(env_cfg.get('zone'))

    is_colocated = (zonal and bucket_location == vm_zone) or (not zonal and vm_zone.startswith(bucket_location))
    
    if is_colocated:
        print(f"Success: VM '{vm_name}' in region '{vm_zone}' and bucket '{bucket_name}' in location '{bucket_location}' are colocated.")
    else:
        print(f"Warning: VM '{vm_name}' in region '{vm_zone}' and bucket '{bucket_name}' in location '{bucket_location}' are NOT colocated. Benchmark numbers might be impacted")
    return does_vm_exist, does_bkt_exist

def validate_existing_resources_if_any(zonal, env_cfg):
    vm_name = env_cfg.get("gce_env", {}).get("vm_name", "")
    bucket_name = env_cfg.get("gcs_bucket", {}).get("bucket_name", "")
    if not vm_name and not bucket_name:
        print("Both VM and GCS bucket do not exist. Newly created resources will be colocated")
        return False, False
    return validate_if_vm_and_bucket_colocated(zonal, env_cfg, vm_name, bucket_name)
