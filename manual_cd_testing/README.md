# Manual CD Testing

This directory contains scripts and documentation for performing manual Continuous Delivery (CD) testing of GCSFuse.

## `run_cd_script.sh`

This script automates the process of setting up a VM, deploying GCSFuse, and running E2E tests for a specified version and commit hash.

### Prerequisites

Before running this script, ensure you have the following:

1.  **Google Cloud SDK (gcloud)** installed and configured.
2.  Authenticated `gcloud` with a project that has permissions to:
    *   Create and manage GCS buckets.
    *   Create and manage Compute Engine instances.

### Usage

1.  **Configure Parameters:**
    Open `run_cd_script.sh` and modify the variables in the "1. Change the parameters below" section to match your testing requirements.

    *   `gcsfuse_version`: The GCSFuse version to test (e.g., "3.7.101").
    *   `commit_hash`: The specific commit hash of GCSFuse to test.
    *   `bucket_name`: A GCS bucket where `details.txt` to be used by the test and test results will be uploaded. This name must be unique.
    *   `vm_name`: The name for the Compute Engine VM instance.
    *   `location`, `zone`, `machine_type`, `image_project`, `image_family`: VM configuration details.

2.  **Run the Script:**
    Execute the script from your terminal:

    ```bash
    ./run_cd_script.sh
    ```

### Script Actions

The script performs the following steps:

1.  **Creates `details.txt`**: Generates a file containing the specified GCSFuse version and commit hash.
2.  **Uploads `details.txt`**: Copies `details.txt` to `gs://${bucket_name}/version-detail/details.txt`. This file is used by the E2E test script running on the VM to determine which GCSFuse version to install.
3.  **Creates Test Buckets**: Sets up 4 GCS buckets that will be used by the E2E tests on the VM.
4.  **Deletes and Recreates VM**:
    *   Deletes any existing VM with the specified `vm_name`.
    *   Creates a new Compute Engine VM instance with the specified configuration.
    *   The `startup-script-url` metadata points to the `e2e_test.sh` script, which will automatically run on VM startup to install GCSFuse and execute the E2E tests.

### Post-Execution

After the script completes, the VM will be created and the `e2e_test.sh` script will start running. You can monitor the progress of the tests by connecting to the VM via SSH or by viewing its serial console output in the Google Cloud Console.

Once the tests are complete, you can inspect the test results on the VM or in the GCS buckets provided.

### Cleanup

To avoid incurring unnecessary costs, it is important to delete the resources created by the script after you have finished testing.

1.  **Delete the Compute Engine VM:**

    ```bash
    gcloud compute instances delete ${vm_name} --zone=${zone}
    ```

2.  **Delete the GCS Buckets:**

    The script creates a total of 4 GCS buckets for testing. Replace `${bucket_name}` and `${vm_name}`with the name you configured in the script.

    ```bash
    gcloud storage rm -r gs://${vm_name}
    gcloud storage rm -r gs://${vm_name}-hns
    gcloud storage rm -r gs://${vm_name}-parallel
    gcloud storage rm -r gs://${vm_name}-hns-parallel
    ```
