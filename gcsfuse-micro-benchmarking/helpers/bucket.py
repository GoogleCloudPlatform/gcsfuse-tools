import subprocess
import shlex

def create_gcs_bucket(location, project, config):
    bucket_name = config.get('bucket_name')
    if not bucket_name:
        raise ValueError("Bucket configuration failed: 'bucket_name' is missing.")
    if not location:
        raise ValueError("Bucket configuration failed: 'location' is missing.")

    cmd = [
        'gcloud', 'storage', 'buckets', 'create', f'gs://{bucket_name}', f'--project={project}', '--quiet'
    ]
    cmd.append(f'--location={shlex.quote(location)}')

    if config.get('storage_class'):
        cmd.append(f'--default-storage-class={shlex.quote(config["storage_class"])}')

    if config.get('enable_hns') is True:
        cmd.append('--enable-hierarchical-namespace')
        cmd.append('--uniform-bucket-level-access')
    
    if config.get('placement'):
        cmd.append(f'--placement={shlex.quote(config["placement"])}')
     
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"Bucket '{bucket_name}' created successfully.")
        print(result.stdout)
    except FileNotFoundError:
        raise RuntimeError("The 'gcloud' command was not found. Please ensure Google Cloud SDK is installed and configured in your system's PATH.")
    except Exception as e:
        raise RuntimeError(f"Failed to create bucket. An unexpected error occurred: {e}")
    return True


def delete_gcs_bucket(bucket_name, project):
    try:
        command = [
            'gcloud', '--quiet', 'storage', 'rm',
            '--recursive',
            f'gs://{bucket_name}',
            f'--project={project}'
        ]
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True
        )
        print(f"Bucket '{bucket_name}' deleted successfully.")

    except subprocess.CalledProcessError as e:
        print(f"Error deleting bucket '{bucket_name}':")
        print("Error Output:", e.stderr)
        print("Return Code:", e.returncode)
    except FileNotFoundError:
        print("Error: The 'gcloud' command was not found. Please ensure the gcloud CLI is installed and in your system's PATH.")