"This class should be implementation of the environment for GCE so it should inherit from the environment class and implenent the abstract menthods"

import os
import random
import string
import subprocess
import time
import google.auth
from google.oauth2.service_account import Credentials
from .environment import environment


class gce_environment(environment):
  "Implementation of the environment for GCE"

  def __init__(self, config):
    super().__init__(config)
    self.project_id = config.get("project_id")
    self.zone = config.get("zone")
    self.instance_name = config.get("instance_name")
    self.machine_type = config.get("machine_type")
    self.use_existing = config.get("use_existing", "no")
    self.bucket_name = config.get("bucket_name")
    self.image_family = config.get("image_family", "debian-11")
    self.image_project = config.get("image_project", "debian-cloud")
    self.boot_disk_size = config.get("boot_disk_size", "20GB")
    self.boot_disk_type = config.get("boot_disk_type", "pd-standard")
    self.ssh_user = config.get("ssh_user", self._get_default_ssh_user())
    self._mount_dir = None  # Initialize mount_dir to None

  def _get_default_ssh_user(self):
    """Gets the default SSH user from ADC (Application Default Credentials)."""
    try:
      credentials, project = google.auth.default()
      print("dir(credentials)", credentials.account)
      if isinstance(credentials, Credentials):
        # Handle service account credentials
        if credentials.service_account_email:
          return credentials.service_account_email.split("@")[0]
        else:
          print(
              "Warning: Could not determine default SSH user from service"
              " account email. Using 'root' as fallback."
          )
          return "root"
      elif credentials.principal_email:
        # Handle user account credentials
        return credentials.principal_email.split("@")[0]
      else:
        print(
            "Warning: Could not determine default SSH user from ADC. Using"
            " 'root' as fallback."
        )
        return "root"
    except google.auth.exceptions.DefaultCredentialsError:
      print(
          "Warning: Could not determine default SSH user from ADC. Using 'root'"
          " as fallback."
      )
      return "root"

  def _run_remote_command(self, command, check=True):
    """Runs a command on the remote GCE instance via SSH."""
    ssh_command = [
        "gcloud",
        "compute",
        "ssh",
        f"{self.ssh_user}@{self.instance_name}",
        "--project",
        self.project_id,
        "--zone",
        self.zone,
        "--command",
        command,
        "--",  # This is important to separate gcloud options from ssh options
        "-o",
        "ProxyCommand=corp-ssh-helper %h %p",
    ]
    print(f"Running remote command: {' '.join(ssh_command)}")
    try:
      result = subprocess.run(
          ssh_command, check=check, capture_output=True, text=True
      )
      print(f"Remote command output:\n{result.stdout}")
      if result.stderr:
        print(f"Remote command error:\n{result.stderr}")
      return result
    except subprocess.CalledProcessError as e:
      print(f"Error running remote command: {e}")
      print(f"Stdout: {e.stdout}")
      print(f"Stderr: {e.stderr}")
      raise

  def _install_gcsfuse(self):
    """Installs GCSFuse on the remote GCE instance."""
    # 2. Remove existing gcsfuse installation (if any)
    print("Removing existing gcsfuse installation...")
    remote_remove_command = "sudo apt-get remove -y gcsfuse"
    try:
      self._run_remote_command(remote_remove_command)
      print("Existing gcsfuse installation removed.")
    except subprocess.CalledProcessError as e:
      # It's okay if gcsfuse wasn't installed
      if "E: Unable to locate package gcsfuse" not in e.stderr:
        print(f"Error removing existing gcsfuse installation: {e}")
        raise
      else:
        print("gcsfuse was not installed")

    # 3. Install gcsfuse
    print("Installing gcsfuse...")
    # Add the GCSFuse repository and key
    add_repo_command = """
        export GCSFUSE_REPO=gcsfuse-`lsb_release -c -s`
        curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo apt-key add -
        echo "deb http://packages.cloud.google.com/apt $GCSFUSE_REPO main" | sudo tee /etc/apt/sources.list.d/gcsfuse.list
        """
    try:
      self._run_remote_command(add_repo_command)
      print("GCSFuse repository added.")
    except subprocess.CalledProcessError as e:
      print(f"Error adding GCSFuse repository: {e}")
      raise

    # Update apt-get
    try:
      self._run_remote_command("sudo apt-get update")
      print("apt-get updated.")
    except subprocess.CalledProcessError as e:
      print(f"Error updating apt-get: {e}")
      raise

    # Install gcsfuse
    remote_install_command = "sudo apt-get install -y gcsfuse"
    try:
      self._run_remote_command(remote_install_command)
      print("gcsfuse installed successfully.")
    except subprocess.CalledProcessError as e:
      print(f"Error installing gcsfuse: {e}")
      raise

  def setup(self):
    """Setup function is supposed to do the following 1.

    If use existing is false then create a GCS instance else do not create 2.
    Remove GCSFuse instance from the system if already present 3. Install
    GCSFuse 4. Create a mount using GCSFuse. The name of the directory should be
    random. 5. The name of the mount directory should be stored in the class
    member
    """
    if not self.use_existing:
      # 1. Create a GCE instance
      print("Creating GCE instance...")
      try:
        subprocess.run(
            [
                "gcloud",
                "compute",
                "instances",
                "create",
                self.instance_name,
                "--project",
                self.project_id,
                "--zone",
                self.zone,
                "--machine-type",
                self.machine_type,
                "--image-family",
                self.image_family,
                "--image-project",
                self.image_project,
                "--boot-disk-size",
                self.boot_disk_size,
                "--boot-disk-type",
                self.boot_disk_type,
                "--scopes",
                "https://www.googleapis.com/auth/cloud-platform",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"GCE instance '{self.instance_name}' created successfully.")
      except subprocess.CalledProcessError as e:
        print(f"Error creating GCE instance: {e}")
        print(f"Stdout: {e.stdout}")
        print(f"Stderr: {e.stderr}")
        raise
    # sleep for some time
    time.sleep(30)

    # 3. Install gcsfuse
    self._install_gcsfuse()

    # 4. Create a random mount directory
    print("Creating mount directory...")
    random_dir_name = "".join(random.choices(string.ascii_lowercase, k=10))
    self._mount_dir = os.path.join(f"/home/{self.ssh_user}", random_dir_name)
    remote_mkdir_command = f"mkdir -p {self._mount_dir}"
    try:
      self._run_remote_command(remote_mkdir_command)
      print(f"Mount directory created at: {self._mount_dir}")
    except subprocess.CalledProcessError as e:
      print(f"Error creating mount directory: {e}")
      raise

    # 5. Create a mount using gcsfuse
    print("Mounting with gcsfuse...")
    remote_mount_command = f"gcsfuse {self.bucket_name} {self._mount_dir}"
    try:
      self._run_remote_command(remote_mount_command)
      print(
          f"Mounted '{self.bucket_name}' to '{self._mount_dir}' successfully."
      )
    except subprocess.CalledProcessError as e:
      print(f"Error mounting with gcsfuse: {e}")
      raise

  def teardown(self):
    """Teardown function is supposed to do the following 1.

    Unmount the GCSFuse 2. Remove the mount directory 3. Delete the GCE instance
    if it was created 4. Remove GCSFuse installation
    """
    print("Tearing down the environment...")
    # 1. Unmount the GCSFuse
    print("Unmounting gcsfuse...")
    remote_unmount_command = f"sudo fusermount -u {self._mount_dir}"
    try:
      self._run_remote_command(remote_unmount_command)
      print(f"Unmounted '{self._mount_dir}' successfully.")
    except subprocess.CalledProcessError as e:
      print(f"Error unmounting with gcsfuse: {e}")
      raise

    # 2. Remove the mount directory
    print("Removing mount directory...")
    remote_rmdir_command = f"sudo rm -rf {self._mount_dir}"
    try:
      self._run_remote_command(remote_rmdir_command)
      print(f"Removed mount directory: {self._mount_dir}")
    except subprocess.CalledProcessError as e:
      print(f"Error removing mount directory: {e}")
      raise

    # 3. Remove gcsfuse installation
    print("Removing gcsfuse installation...")
    remote_remove_command = "sudo apt-get remove -y gcsfuse"
    try:
      self._run_remote_command(remote_remove_command)
      print("gcsfuse installation removed.")
    except subprocess.CalledProcessError as e:
      print(f"Error removing gcsfuse installation: {e}")
      raise

    # 4. Delete the GCE instance if it was created
    if not self.use_existing:
      print("Deleting GCE instance...")
      try:
        subprocess.run(
            [
                "gcloud",
                "compute",
                "instances",
                "delete",
                self.instance_name,
                "--project",
                self.project_id,
                "--zone",
                self.zone,
                "--quiet",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"GCE instance '{self.instance_name}' deleted successfully.")
      except subprocess.CalledProcessError as e:
        print(f"Error deleting GCE instance: {e}")
        raise

  def execute(self, task):
    """Execute the command on the remote GCE instance and return the output."""
    print(f"Executing task: {task}")
    try:
      result = self._run_remote_command(task)
      print(f"Task: {task} executed successfully")
      return result.stdout
    except subprocess.CalledProcessError as e:
      print(f"Error executing task: {e}")
      raise

  def mount_dir(self):
    """Returns the mount directory."""
    return self._mount_dir
