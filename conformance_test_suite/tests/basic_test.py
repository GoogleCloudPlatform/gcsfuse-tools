import os
from .test import Test  # Assuming test.py is in the same directory


class BasicTest(Test):
  """A concrete test class that performs basic file operations on a mounted GCSFuse directory."""

  def __init__(self, config, environment):
    super().__init__(config, environment)
    self.test_name = "BasicFileOperationsTest"

  def setup(self):
    """No specific setup needed for this basic test."""
    print(f"Setting up {self.test_name}...")
    self.report_data["setup_status"] = "SUCCESS"
    # Example of environment-dependent setup (if needed):
    # if self.environment.type == "GCE":
    #     # Do GCE-specific setup
    #     pass

  def teardown(self):
    """No specific teardown needed for this basic test."""
    print(f"Tearing down {self.test_name}...")
    self.report_data["teardown_status"] = "SUCCESS"
    # Example of environment-dependent teardown (if needed):
    # if self.environment.type == "GCE":
    #     # Do GCE-specific teardown
    #     pass

  def run(self):
    """Runs the basic file operations test against the given environment."""
    print(f"Running {self.test_name}...")
    self.report_data["test_name"] = self.test_name
    try:
      mount_directory = self.environment.mount_dir()
      self.report_data["mount_directory"] = mount_directory

      # Example 1: List files in the mount directory
      output = self.environment.execute(f"ls -l {mount_directory}")
      self.report_data["ls_output"] = output
      print(f"Output of 'ls -l {mount_directory}':\n{output}")

      # Example 2: Create a file in the mount directory
      output = self.environment.execute(
          f"echo 'Hello, GCSFuse!' > {mount_directory}/hello.txt"
      )
      self.report_data["create_file_output"] = output
      print(f"Output of creating file:\n{output}")

      # Example 3: Read the file
      output = self.environment.execute(f"cat {mount_directory}/hello.txt")
      self.report_data["read_file_output"] = output
      print(f"Output of reading file:\n{output}")

      # Example 4: Run a command that fails
      try:
        output = self.environment.execute("this_command_does_not_exist")
        self.report_data["non_existing_command_output"] = output
        print(f"Output of non existing command:\n{output}")
      except Exception as e:
        self.report_data["non_existing_command_error"] = str(e)
        print(f"Caught expected error: {e}")

      self.test_status = "PASSED"
      self.report_data["test_status"] = self.test_status
    except Exception as e:
      self.test_status = "FAILED"
      self.report_data["test_status"] = self.test_status
      self.report_data["error"] = str(e)
      print(f"Test failed: {e}")
