import subprocess
import shlex
import json

def extract_region_from_zone(zone):
    """
    Given a GCS zone string, extracts and returns the region.
    
    Args:
        zone (str): The zone string (e.g., 'eu-west4-a').
        
    Returns:
        str: The corresponding region (e.g., 'eu-west4').
    """
    # Split the string by the hyphen and rejoin all parts except the last one
    return '-'.join(zone.split('-')[:-1])


def _get_vm_zone(vm_name, project):
    """
    Retrieves the zone of a GCE VM.
    
    Returns:
        str: The VM's zone (e.g., 'us-central1-a') or None if not found.
    """
    try:
        # Construct the gcloud command to list instances and filter by name.
        # The --format flag is used to get the output as a JSON list,
        # which is easier to parse. We specifically select the 'zone' and 'name' fields.
        command = [
            'gcloud', 'compute', 'instances', 'list',
            '--project={}'.format(project),
            '--filter=name="{}"'.format(vm_name),
            '--format=json'
        ]

        # Run the command and capture the output.
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        
        # Parse the JSON output.
        vm_list = json.loads(result.stdout)

        # If a VM with the given name is found, return its zone.
        if vm_list:
            # The 'zone' field from gcloud is a full URL, e.g., '.../zones/us-central1-a'.
            # We split the string by '/' and take the last part to get the zone name.
            zone_url = vm_list[0]['zone']
            return zone_url.split('/')[-1]
        else:
            return None

    except subprocess.CalledProcessError as e:
        print(f"Error executing gcloud command: {e.stderr}")
        return None
    except json.JSONDecodeError:
        print("Error parsing JSON output from gcloud.")
        return None


def _get_bucket_location(bucket_name, project):
    """
    Retrieves the location of a GCS bucket.
    
    Returns:
        str: The bucket's location (e.g., 'US-CENTRAL1') or None if not found.
    """
    try:
        command_string = f"gcloud storage buckets describe gs://{bucket_name} --project={project} --format='value(location)'"
        result = subprocess.run(shlex.split(command_string), check=True, capture_output=True, text=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        print(f"Error: Bucket '{bucket_name}' not found.")
        return None


def validate_if_vm_and_bucket_colocated(zonal, env_cfg,vm_name, bucket_name):
    """
    Validates if a GCE VM and a GCS bucket are in the same region.
    
    Args:
        vm_name (str): The name of the GCE VM.
        bucket_name (str): The name of the GCS bucket.

    Returns:
        bool: True if they are colocated, False otherwise.
    """
    does_vm_exist=True
    does_bkt_exist=True
    
    vm_zone = _get_vm_zone(vm_name, env_cfg.get('project')) 
    if not vm_zone:
        does_vm_exist=False
        vm_zone = env_cfg.get('zone')
        
    bucket_location = _get_bucket_location(bucket_name, env_cfg.get('project'))
    if not bucket_location:
        does_bkt_exist=False
        bucket_location = env_cfg.get('zone') if zonal else extract_region_from_zone(env_cfg.get('zone'))

    is_colocated=False
    if zonal and bucket_location == vm_zone:
        is_colocated=True
    if not zonal and vm_zone.startswith(bucket_location):
        is_colocated=True
    
    if is_colocated:
        print(f"Success: VM '{vm_name}' in region '{vm_zone}' and bucket '{bucket_name}' in location '{bucket_location}' are colocated.")
        return does_vm_exist, does_bkt_exist
    else:
        print(f"Warning: VM '{vm_name}' in region '{vm_zone}' and bucket '{bucket_name}' in location '{bucket_location}' are NOT colocated. Benchmark numbers might be impacted")
        return does_vm_exist, does_bkt_exist


def validate_existing_resources_if_any(zonal, env_cfg):
    vm_name= env_cfg.get("gce_env").get("vm_name")
    bucket_name=env_cfg.get("gcs_bucket").get("bucket_name")
    if vm_name == "" and bucket_name == "":
        print("Both VM and GCS bucket do not exist. Newly created resources will be colocated")
        return False, False
    
    return validate_if_vm_and_bucket_colocated(zonal, env_cfg,vm_name, bucket_name)


# Example Usage
if __name__ == '__main__':
    # Replace with your actual VM and bucket names
    zonal=False
    env_cfg={
        'zone': 'us-central1-a',
        'gce_env': {
            'vm_name': 'my-non-existent-vm',
        },
        'gcs_bucket':{
            'bucket_name': 'my-non-existent-bkt',
        },
    }

    is_vm_exists,is_bkt_exists= validate_existing_resources_if_any(zonal, env_cfg)
    print(is_vm_exists,is_bkt_exists)