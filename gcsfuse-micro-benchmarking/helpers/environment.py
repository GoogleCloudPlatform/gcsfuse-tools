import subprocess
import shlex
import os
import time
import requests
from .constants import *

def contruct_metadata_string_from_config(metadata_config):
    metadata_items = []
    for key, value in metadata_config.items():
        metadata_items.append(f"{key}={str(value)}")
    return ','.join(metadata_items)

def create_vm_if_not_exists(vm_details, zone, project):
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
        return False
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
            subprocess.run(cmd_list, check=True, capture_output=True, text=True, timeout=360)
            print(f"VM '{vm_name}' creation command finished successfully.")
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
    except Exception:
        print(f"Unexpected error checking VM existence: {e}")
        return None

def is_running_on_gce():
    if os.path.exists("/usr/local/google/home"):
        print("Debug: Detected Cloudtop environment. Forcing External/IAP connection.")
        return False

    try:
        response = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/",
            headers={"Metadata-Flavor": "Google"},
            timeout=1
        )
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False

def wait_for_ssh(vm_name, zone, project, retries=15, delay=20):
    ssh_cmd = [
        'gcloud', 'compute', 'ssh', vm_name,
        f'--zone={zone}', f'--project={project}',
        '--quiet',
    ]
    if is_running_on_gce():
        print("Detected environment: GCE VM. Using internal IP.")
        ssh_cmd.append('--internal-ip')
        ssh_cmd.extend(['--', '-vvv', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', 'echo "SSH ready"'])
    else:
        print("Detected environment: Cloudtop/External. Using default (External IP).")
        ssh_cmd.extend(['--', 'echo "SSH ready"'])

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
    metadata_string = contruct_metadata_string_from_config(metadata_config)
    command = (
        f"gcloud compute instances add-metadata {vm_name} "
        f"--zone={zone} "
        f"--project={project} "
        f"--metadata={metadata_string}"
    )

    try:
        command_list = shlex.split(command)
        subprocess.run(
            command_list,
            check=True,
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

    scp_cmd = gcloud_base + ['scp', startup_script, f'{vm_name}:{remote_script_path}', f'--zone={zone}', f'--project={project}']
    if is_running_on_gce():
        print("Detected GCE environment for SCP. Adding --internal-ip.")
        scp_cmd.append('--internal-ip')
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
                return False
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
        return False
        
    ssh_base = gcloud_base + ['ssh', vm_name, f'--zone={zone}', f'--project={project}']
    if is_running_on_gce():
        print("Detected GCE environment for execution SSH. Adding --internal-ip.")
        ssh_base.append('--internal-ip')
    tmux_session_name = f"startup_{vm_name}"
    tmux_session_name_quoted = shlex.quote(tmux_session_name)

    install_tmux_cmd = (
        "if ! command -v tmux &> /dev/null; then "
        "echo 'tmux not found, attempting to install...'; "
        "sudo DEBIAN_FRONTEND=noninteractive apt-get -yq update && "
        "sudo DEBIAN_FRONTEND=noninteractive apt-get -yq install tmux; "
        "fi"
    )

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

    if creation_status is None:
        print(f"Failed to ensure VM {vm_name} exists.")
        return False
    elif creation_status is True:
        if not wait_for_ssh(vm_name, zone, project):
            return False

    if not update_vm_metadata_parameter(vm_name, zone, project, metadata_config):
        return False
    if not run_script_remotely(vm_name, zone, project, cfg.get('startup_script')):
        return False
    return True


def delete_gce_vm(vm_name, zone, project):
    if not vm_name:
        print("Error: A VM name is required for deletion.")
        return False
    
    cmd_list = ['gcloud', 'compute', 'instances', 'delete', vm_name, f'--zone={zone}', f'--project={project}', '--quiet']
    
    try:
        subprocess.run(cmd_list, check=True, capture_output=True, text=True)
        
        print(f"VM '{vm_name}' deletion command executed successfully.")
        print("Waiting for VM to be fully deleted...")
        check_cmd_string = f'gcloud compute instances describe {vm_name} --format=value(name)'
        
        while True:
            try:
                subprocess.run(shlex.split(check_cmd_string), check=True, capture_output=True, text=True)
                time.sleep(10)
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
