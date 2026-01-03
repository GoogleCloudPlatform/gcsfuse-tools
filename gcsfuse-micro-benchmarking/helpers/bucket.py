import subprocess
import shlex
import sys
import os

def check_bucket_exists(bucket_name, project):
    """
    Checks if a GCS bucket exists.
    
    Args:
        bucket_name (str): Name of the bucket to check
        project (str): GCP project
        
    Returns:
        bool: True if bucket exists, False otherwise
    """
    try:
        cmd = ['gcloud', 'storage', 'buckets', 'describe', f'gs://{bucket_name}', f'--project={project}']
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError:
        return False


def create_gcs_bucket(location, project,config):
    """
    Creates a GCS bucket using the gcloud storage CLI based on a configuration dictionary.
    
    Args:
        config (dict): A dictionary with bucket configuration.
                      e.g., {'bucket_name': 'my-bucket', 'location': 'us-central1', ...}
    
    Raises:
        ValueError: If a required configuration field is missing.
        RuntimeError: If the gcloud command fails to execute.
    """
    # 1. Validate required fields
    bucket_name = config.get('bucket_name')

    if not bucket_name:
        raise ValueError("Bucket configuration failed: 'bucket_name' is missing.")
    if not location:
        raise ValueError("Bucket configuration failed: 'location' is missing.")

    # 2. Build the base gcloud command
    cmd = [
        'gcloud', 'storage', 'buckets', 'create', f'gs://{bucket_name}', f'--project={project}', '--quiet'
    ]

    # Add optional flags if they are present in the config
    # GCS uses 'location' for region, multi-region, or dual-region.
    cmd.append(f'--location={shlex.quote(location)}')

    if config.get('storage_class'):
        cmd.append(f'--default-storage-class={shlex.quote(config["storage_class"])}')

    if config.get('enable_hns') is True:
        cmd.append('--enable-hierarchical-namespace')
        cmd.append('--uniform-bucket-level-access')
    
    if config.get('placement'):
        cmd.append(f'--placement={shlex.quote(config["placement"])}')
     
    try:
        # 3. Execute the command
        # print(f"Executing command: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"Bucket '{bucket_name}' created successfully.")
        print(result.stdout)
        
    except FileNotFoundError:
        raise RuntimeError("The 'gcloud' command was not found. Please ensure Google Cloud SDK is installed and configured in your system's PATH.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create bucket: {e.stderr}")
    except Exception as e:
        raise RuntimeError(f"An unexpected error occurred: {e}")
    return True


def delete_gcs_bucket(bucket_name, project):
    """
    Deletes a Google Cloud Storage bucket using the gcloud CLI.

    This function requires the gcloud CLI to be installed and authenticated.
    
    Args:
        bucket_name (str): The name of the bucket to delete.
    """
    try:
        # Construct the gcloud command. The --quiet flag suppresses
        # the confirmation prompt.
        command = [
            'gcloud', '--quiet', 'storage', 'rm',
            '--recursive',
            f'gs://{bucket_name}',
            f'--project={project}'
        ]
        # Run the command and capture the output
        result = subprocess.run(
            command,
            check=True,  # This will raise an exception if the command fails
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


# Example Usage:
if __name__ == '__main__':
    # Assuming 'is_zonal' and 'benchmark_id' are defined elsewhere
    is_zonal = False
    benchmark_id = 'my-benchmark-id'
    location = 'us-central1'
    default_zone = 'us-central1-a'
    default_enable_hns = False
    project='my-project'

    config = {
         'bucket_name': f"{benchmark_id}-bucket",
         'placement': default_zone if is_zonal else "",
         'storage_class': "RAPID" if is_zonal else "",
         'enable_hns': True if is_zonal else default_enable_hns,
    }
    
    try:
        create_gcs_bucket(location, project, config)
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}")

    try: 
        delete_gcs_bucket(config.get('bucket_name'), project)
    except Exception as e:
        print(f"Could not delete the GCS bucket, Error: {e}")