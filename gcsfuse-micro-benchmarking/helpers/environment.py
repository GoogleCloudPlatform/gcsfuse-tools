import subprocess
import shlex
import os
import time
import json
from .constants import *


def contruct_metadata_string_from_config(metadata_config):
    """
    Constructs a metadata string from a dictionary for gcloud.

    Args:
        metadata_config (dict): A dictionary of key-value pairs.

    Returns:
        str: A comma-separated string of key-value pairs.
    """
    metadata_items = []
    for key, value in metadata_config.items():
        # Ensure the value is a string, which is required for gcloud metadata
        metadata_items.append(f"{key}={str(value)}")
    return ','.join(metadata_items)


def create_vm_if_not_exists(vm_details, zone, project):
    vm_name = vm_details.get('vm_name')
    startup_script = vm_details.get('startup_script')

    if not vm_name:
        print("Error: 'vm_name' is a required key in vm_details.")
        return False
    
    # Check if the VM already exists
    vm_exists = False
    check_cmd = [
            'gcloud', 'compute', 'instances', 'list',
            '--filter=name="{}"'.format(vm_name),
            '--project={}'.format(project),
            '--format=json'
        ]
    try:
        result=subprocess.run(check_cmd, check=True, capture_output=True, text=True)
        if json.loads(result.stdout):
            vm_exists = True
            print(f"VM '{vm_name}' already exists")
        else:
            raise subprocess.CalledProcessError(
            returncode=1,
            cmd="{check_cmd}",
            output="The VM was not found.. hence proceeding to create the VM with same name"
            )
    except subprocess.CalledProcessError:
        print(f"VM '{vm_name}' does not exist. Creating VM now...")
        
        # Build and run the VM creation command
        cmd_list = [
            'gcloud', 'compute', 'instances', 'create', vm_name,
            f'--zone={zone}',
            f'--project={project}',
            f'--machine-type={vm_details.get("machine_type")}',
            f'--create-disk=name={vm_name}-disk,size={vm_details.get("disk_size")}',
            f'--image-family={vm_details.get("image_family")}',
            f'--image-project={vm_details.get("image_project")}',
            f'--scopes=https://www.googleapis.com/auth/cloud-platform' \
        ]
        
        try:
            print(f"Executing creation command: {' '.join(cmd_list)}")
            subprocess.run(cmd_list, check=True, capture_output=True, text=True)
            print(f"VM '{vm_name}' created successfully.")
        except subprocess.CalledProcessError as e:
            print(f"Error creating VM: {e.stderr}")
            return False
    return True


def update_vm_metadata_parameter(vm_name, zone, project,metadata_config):
    """
    Updates the metadata of a Google Cloud VM instance.

    Args:
        vm_name (str): The name of the VM instance.
        zone (str): The zone where the VM instance is located.
        metadata_config (dict): A dictionary of key-value pairs to set as metadata.
    """
    # First, construct the metadata string
    metadata_string = contruct_metadata_string_from_config(metadata_config)
    
    # Construct the gcloud command
    command = (
        f"gcloud compute instances add-metadata {vm_name} "
        f"--zone={zone} "
        f"--project={project} "
        f"--metadata={metadata_string}"
    )

    try:
        # Use shlex.split to safely parse the command
        command_list = shlex.split(command)
        
        # Run the command
        subprocess.run(
            command_list,
            check=True,  # Raises an exception on command failure
            capture_output=True,
            text=True
        )
        print(f"Successfully updated metadata for VM '{vm_name}' in zone '{zone}'.")
        
    except subprocess.CalledProcessError as e:
        print(f"Error updating metadata for VM '{vm_name}':")
        print(e.stderr)
        return False
    except FileNotFoundError:
        print("Error: The 'gcloud' command was not found. Please ensure it's installed and in your PATH.")
        return False
        
    return True


def run_script_remotely(vm_name, zone, project,startup_script,max_retries=max_ssh_retries, retry_delay=retry_delay):
    if startup_script and os.path.exists(startup_script):
        # The SSH command to execute the script remotely
        ssh_cmd_base = ['gcloud', 'compute', 'ssh', vm_name, f'--zone={zone}', f'--project={project}',  '--', 'sudo', 'bash']
        
        # We need to retry the SSH connection as the VM might not be ready immediately after creation
        for i in range(max_retries):
            print(f"Attempt {i+1}/{max_retries}: Executing remote script...")
            try:
                with open(startup_script, 'r') as script_file:
                    subprocess.run(ssh_cmd_base, stdin=script_file, check=True, text=True)
                print(f"Script executed successfully on '{vm_name}'.")
                return True
            except subprocess.CalledProcessError as e:
                print(f"SSH failed. Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            except FileNotFoundError:
                print(f"Error: Local script file '{startup_script}' not found.")
                return False
    else:
        print("No valid startup_script provided. No remote execution will take place.")
        return True # The VM was created, so this is a successful operation
        
    print(f"Failed to execute script on '{vm_name}' after {max_retries} retries.")
    return False


def startup_benchmark_vm(cfg, zone, project,metadata_config):
    if not create_vm_if_not_exists(cfg, zone, project):
        return False
    if not update_vm_metadata_parameter(cfg.get('vm_name'), zone, project, metadata_config):
        return False
    if not run_script_remotely(cfg.get('vm_name'), zone, project, cfg.get('startup_script')):
        return False
    return True


def delete_gce_vm(vm_name, zone, project):
    """
    Deletes a GCE VM without a confirmation prompt.

    Args:
        vm_name (str): The name of the VM to delete.
    
    Returns:
        bool: True if the deletion was successful, False otherwise.
    """
    if not vm_name:
        print("Error: A VM name is required for deletion.")
        return False
    
    cmd_list = ['gcloud', 'compute', 'instances', 'delete', vm_name, f'--zone={zone}', f'--project={project}', '--quiet']
    
    try:
        print(f"Executing command: {' '.join(shlex.quote(arg) for arg in cmd_list)}")
        
        # Use subprocess.run to execute the command
        subprocess.run(cmd_list, check=True, capture_output=True, text=True)
        
        print(f"VM '{vm_name}' deletion command executed successfully.")
        
        # Poll for deletion to confirm it's complete
        print("Waiting for VM to be fully deleted...")
        check_cmd_string = f'gcloud compute instances describe {vm_name} --format=value(name)'
        
        while True:
            try:
                subprocess.run(shlex.split(check_cmd_string), check=True, capture_output=True, text=True)
                time.sleep(10)  # Wait for 10 seconds before checking again
            except subprocess.CalledProcessError:
                print(f"VM '{vm_name}' has been successfully deleted.")
                return True

    except subprocess.CalledProcessError as e:
        print(f"Error executing deletion command. Return code: {e.returncode}")
        print("Standard Error:")
        print(e.stderr)
        return False
    except FileNotFoundError:
        print("Error: 'gcloud' command not found. Please ensure Google Cloud SDK is installed and in your PATH.")
        return False


if __name__ == '__main__':
    vm_config = {
        'vm_name': 'my-test-vm',
        'machine_type': 'e2-micro',
        'image_family': 'debian-11',
        'image_project': 'debian-cloud',
        'disk_size': '10GB',
        'startup_script': 'test_script.sh'
    }
    
    # Create a dummy script for demonstration
    with open('test_script.sh', 'w') as f:
        f.write("#!/bin/bash\n")
        f.write("echo 'Hello from the script!' > /tmp/hello.txt\n")
        f.write("echo 'Script executed successfully.'\n")
        f.write("touch /tmp/script_status.txt")
        
    delete_gce_vm(vm_config.get('vm_name'), default_zone, default_project)
