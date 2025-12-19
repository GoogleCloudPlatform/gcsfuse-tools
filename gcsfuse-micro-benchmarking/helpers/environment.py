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
    """
    Creates a VM if it does not already exist.

    Returns:
        True: If the VM was newly created in this call.
        False: If the VM already existed.
        None: If an error occurred.
    """
    vm_name = vm_details.get('vm_name')
    if not vm_name:
        print("Error: 'vm_name' is a required key in vm_details.")
        return None

    check_cmd = [
        'gcloud', 'compute', 'instances', 'describe', vm_name,
        f'--zone={zone}',
        f'--project={project}',
        '--format=value(name)'
    ]
    try:
        subprocess.run(check_cmd, check=True, capture_output=True, text=True, timeout=30)
        print(f"VM '{vm_name}' already exists in zone '{zone}'.")
        return False # Already exists
    except subprocess.CalledProcessError:
        print(f"VM '{vm_name}' does not exist in zone '{zone}'. Attempting to create...")

        cmd_list = [
            'gcloud', 'compute', 'instances', 'create', vm_name,
            f'--zone={zone}',
            f'--project={project}',
            f'--machine-type={vm_details.get("machine_type", "e2-micro")}',
            f'--boot-disk-size={vm_details.get("disk_size", "10GB")}',
            f'--image-family={vm_details.get("image_family", "debian-11")}',
            f'--image-project={vm_details.get("image_project", "debian-cloud")}',
            '--scopes=https://www.googleapis.com/auth/cloud-platform'
        ]
        if vm_details.get('service_account'):
            cmd_list.append(f'--service-account={vm_details["service_account"]}')

        try:
            # print(f"Executing creation command: {' '.join(shlex.quote(arg) for arg in cmd_list)}")
            subprocess.run(cmd_list, check=True, capture_output=True, text=True, timeout=360) # Increased timeout
            print(f"VM '{vm_name}' creation command finished successfully.")
            return True # Newly Created
        except subprocess.CalledProcessError as e:
            print(f"Error creating VM {vm_name}: {e}")
            print(f"STDERR: {e.stderr}")
            return None # Error
        except subprocess.TimeoutExpired:
            print(f"Timeout creating VM {vm_name}.")
            return None # Error
    except Exception as e:
        print(f"Unexpected error checking VM existence: {e}")
        return None


def wait_for_ssh(vm_name, zone, project, retries=15, delay=20):
    """Tries to SSH into the VM until it succeeds or retries are exhausted."""
    ssh_cmd = [
        'gcloud', 'compute', 'ssh', vm_name,
        f'--zone={zone}', f'--project={project}',
        '--quiet',  # Suppress interactive prompts
        '--', 'echo "SSH ready"'
    ]
    print(f"Waiting for VM '{vm_name}' to become SSH-ready...")
    for i in range(retries):
        try:
            print(f"  Attempt {i+1}/{retries} to SSH into {vm_name}...")
            result = subprocess.run(ssh_cmd, check=True, capture_output=True, text=True, timeout=45)
            if "SSH ready" in result.stdout:
                print(f"  SSH connection to {vm_name} successful.")
                return True
        except subprocess.CalledProcessError as e:
            print(f"  SSH check {i+1} failed (code {e.returncode}). Retrying in {delay}s...")
        except subprocess.TimeoutExpired:
            print(f"  SSH check {i+1} timed out. Retrying in {delay}s...")
        time.sleep(delay)
    print(f"Failed to connect to VM {vm_name} via SSH after {retries} attempts.")
    return False

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

def run_script_remotely(vm_name, zone, project, startup_script, max_retries=max_ssh_retries, retry_delay=retry_delay):
    if not (startup_script and os.path.exists(startup_script)):
        print("No valid startup_script provided or file not found. No remote execution will take place.")
        return True

    gcloud_base = ['gcloud', 'compute']
    script_filename = os.path.basename(startup_script)
    remote_script_path = f"/tmp/startup_script_{script_filename}"
    remote_script_path_quoted = shlex.quote(remote_script_path)

    # Step 1: Upload the script
    scp_cmd = gcloud_base + ['scp', startup_script, f'{vm_name}:{remote_script_path}', f'--zone={zone}', f'--project={project}']
    print(f"Uploading {startup_script} to {vm_name}:{remote_script_path}...")
    for i in range(max_retries):
        try:
            print(f"  SCP attempt {i+1}/{max_retries}...")
            subprocess.run(scp_cmd, check=True, text=True, capture_output=True, timeout=180)
            print("Upload successful.")
            break # Exit the loop on success
        except subprocess.CalledProcessError as e:
            print(f"Failed to upload script on attempt {i+1}: {e}")
            print("STDOUT:", e.stdout)
            print("STDERR:", e.stderr)
            if i < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print(f"Failed to upload script after {max_retries} retries.")
                return False # Return False only after all retries are exhausted
        except subprocess.TimeoutExpired as e:
            print(f"Timeout expired while uploading script on attempt {i+1}: {e}")
            if i < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                return False
        except Exception as e:
            print(f"An unexpected error occurred during upload: {e}")
            return False
    else:
        # This else block runs only if the for loop completes without a 'break'
        # which means all retries failed.
        return False
        
    # Step 2: Execute the script inside a detached tmux session
    ssh_base = gcloud_base + ['ssh', vm_name, f'--zone={zone}', f'--project={project}']
    tmux_session_name = f"startup_{vm_name}"
    tmux_session_name_quoted = shlex.quote(tmux_session_name)

    # Command to install tmux non-interactively if not present (for Debian/Ubuntu)
    install_tmux_cmd = (
        "if ! command -v tmux &> /dev/null; then "
        "echo 'tmux not found, attempting to install...'; "
        "sudo DEBIAN_FRONTEND=noninteractive apt-get -yq update && "
        "sudo DEBIAN_FRONTEND=noninteractive apt-get -yq install tmux; "
        "fi"
    )

    # Command to kill existing session if it exists, then start a new one
    # Export DEBIAN_FRONTEND to affect all commands in the shell
    remote_exec_cmd = (
        f"export DEBIAN_FRONTEND=noninteractive && "
        f"{install_tmux_cmd} && "
        f"chmod +x {remote_script_path_quoted} && "
        f"if tmux has-session -t {tmux_session_name_quoted} 2>/dev/null; then "
          f"echo 'Killing existing tmux session {tmux_session_name_quoted}...'; "
          f"tmux kill-session -t {tmux_session_name_quoted}; "
        f"fi && "
        f"echo 'Starting new tmux session {tmux_session_name_quoted}...'; "
        f"tmux new-session -d -s {tmux_session_name_quoted} "
          f"'sudo DEBIAN_FRONTEND=noninteractive bash {remote_script_path_quoted} > {remote_script_path_quoted}.out 2> {remote_script_path_quoted}.err'"
    )

    ssh_cmd = ssh_base + ['--', remote_exec_cmd]

    print(f"Attempting to start script in tmux session '{tmux_session_name}' on '{vm_name}'...")
    for i in range(max_retries):
        print(f"Attempt {i+1}/{max_retries}...")
        try:
            result = subprocess.run(ssh_cmd, check=True, text=True, capture_output=True, timeout=120)
            print(f"Script launched in tmux session '{tmux_session_name}' on '{vm_name}'.")
            print(f"Output/errors will be logged to {remote_script_path}.out and {remote_script_path}.err on the VM.")
            # print("SSH STDOUT:", result.stdout) # Optional: Uncomment for debugging
            # print("SSH STDERR:", result.stderr) # Optional: Uncomment for debugging
            return True
        except subprocess.CalledProcessError as e:
            print(f"SSH command failed on attempt {i+1}: {e}")
            print("STDOUT:", e.stdout)
            print("STDERR:", e.stderr)
            if i < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print(f"Failed to launch script in tmux on '{vm_name}' after {max_retries} retries.")
                return False
        except subprocess.TimeoutExpired as e:
             print(f"Timeout expired on attempt {i+1} for SSH command: {e}")
             if i < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
             else:
                return False
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return False
    return False

def startup_benchmark_vm(cfg, zone, project,metadata_config):
    vm_name = cfg.get('vm_name')
    if not vm_name:
        print("Error: vm_name missing from config")
        return False

    creation_status = create_vm_if_not_exists(cfg, zone, project)

    if creation_status is None: # Error during check/create
        print(f"Failed to ensure VM {vm_name} exists.")
        return False
    elif creation_status is True: # Newly created
        if not wait_for_ssh(vm_name, zone, project):
            return False
    # If False, VM already existed, proceed.

    if not update_vm_metadata_parameter(vm_name, zone, project, metadata_config):
        return False
    if not run_script_remotely(vm_name, zone, project, cfg.get('startup_script')):
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
        # print(f"Executing command: {' '.join(shlex.quote(arg) for arg in cmd_list)}")
        
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
