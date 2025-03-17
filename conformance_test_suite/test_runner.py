import argparse
import os
import sys
import uuid

from config_manager import ConfigManager
from environments.gce_environment import gce_environment
from reporters.csv_reporter import CSVReporter
from reporters.reporter import Reporter
from tests.basic_test import BasicTest  # Import the concrete test class


class TestRunner:
  """A class to manage the execution of tests in different environments."""

  def __init__(self, config_file):
    """Initializes the TestRunner with the path to the configuration file.

    Args:
        config_file (str): The path to the YAML configuration file.
    """
    self.config_file = config_file
    self.config_manager = ConfigManager(self.config_file)
    self.environments = {}
    self.test_objects = []
    self.reporters = {}

  def create_environment_object(self, env_config):
    """Creates an environment object based on the environment configuration.

    Args:
        env_config (dict): The environment configuration.

    Returns:
        environment: An instance of the environment class.
    """
    env_type = env_config.get("type")
    if env_type == "GCE":
      uud = str(uuid.uuid4())
      gce_config = {
          "project_id": env_config.get("project_id", "gcs-fuse-test"),
          "zone": env_config.get("zone", "us-central1-a"),
          "instance_name": env_config.get(
              "instance_name", f"conformance-test-instance-{uud}"
          ),
          "machine_type": env_config.get("machine_type", "e2-medium"),
          "use_existing": env_config.get("use_existing", "No").lower() == "yes",
          "bucket_name": env_config.get("bucket_name", "test_bucket"),
      }
      return gce_environment(gce_config)
    elif env_type == "GKE":
      # Add GKE environment creation logic here
      print("GKE environment creation not yet implemented.")
      return None
    else:
      raise ValueError(f"Unknown environment type: {env_type}")

  def create_test_object(self, test_config, environment):
    """Creates a test object based on the test configuration and the environment.

    Args:
        test_config (dict): The test configuration.
        environment (Any): The environment object.

    Returns:
        Test: An instance of the test class.
    """
    test_type = test_config.get("type")
    if test_type == "BasicTest":
      return BasicTest(test_config, environment)
    else:
      raise ValueError(f"Unknown test type: {test_type}")

  def initialize_environments(self):
    """Initializes all environments defined in the configuration file."""
    environment_configs = self.config_manager.get_environment_configs()
    for env_config in environment_configs:
      env_name = env_config.get("name")
      print(f"Creating environment: {env_name}")
      env = self.create_environment_object(env_config)
      if env:
        env.setup()
        self.environments[env_name] = env
      else:
        print(f"Skipping environment {env_name} due to creation failure.")

  def create_reporters(self):
    """Creates reporter objects based on the configuration."""
    reporter_configs = self.config_manager.get_reporter_configs()
    for reporter_config in reporter_configs:
      reporter_name = reporter_config.get("name")
      reporter_type = reporter_config.get("type")
      if reporter_type == "CSVReporter":
        self.reporters[reporter_name] = CSVReporter(reporter_config)
      else:
        raise ValueError(f"Unknown reporter type: {reporter_type}")

  def create_tests(self):
    """Creates test objects for each test and environment combination."""
    test_configs = self.config_manager.get_test_configs()
    for test_config in test_configs:
      test_name = test_config.get("name")
      test_environments = test_config.get("environments", [])
      test_reporters = test_config.get("reporters", [])
      for env_name in test_environments:
        if env_name in self.environments:
          print(f"Creating test: {test_name} in environment {env_name}")
          env = self.environments[env_name]
          test = self.create_test_object(test_config, env)
          test.reporters = [
              self.reporters[reporter_name]
              for reporter_name in test_reporters
              if reporter_name in self.reporters
          ]
          self.test_objects.append(test)
        else:
          print(
              f"Skipping test: {test_name} in environment {env_name} because"
              " environment is not initialized."
          )

  def run_tests(self):
    """Runs all created test objects."""
    for test in self.test_objects:
      test.setup()
      test.run()
      test.teardown()
      print(f"Test status: {test.status()}")
      report = test.report()
      print(f"Test report: {report}")
      for reporter in test.reporters:
        reporter.process(report)

  def teardown_environments(self):
    """Tears down all initialized environments."""
    for env_name, env in self.environments.items():
      print(f"Tearing down environment: {env_name}")
      env.teardown()

  def store_reports(self):
    """Stores all reports."""
    for reporter in self.reporters.values():
      reporter.store()

  def run(self):
    """Runs the entire test suite."""
    self.initialize_environments()
    self.create_reporters()
    self.create_tests()
    self.run_tests()
    self.teardown_environments()
    self.store_reports()
