from abc import ABC, abstractmethod
from typing import Any


class Test(ABC):
  """Abstract base class for defining tests."""

  def __init__(self, config: dict, environment: Any):
    """Initializes the Test object with a configuration dictionary and an environment object.

    Args:
        config (dict): A dictionary containing configuration parameters for the
          test.
        environment (Any): The environment object to run the test against.
    """
    self.config = config
    self.environment = environment
    self.test_status = (  # Possible values: NOT_RUN, PASSED, FAILED, SKIPPED
        "NOT_RUN"
    )
    self.report_data = {}

  @abstractmethod
  def setup(self) -> None:
    """Sets up the test environment (e.g., creates files, configures settings)."""
    pass

  @abstractmethod
  def teardown(self) -> None:
    """Cleans up the test environment (e.g., deletes files, resets settings)."""
    pass

  @abstractmethod
  def run(self) -> None:
    """Runs the test against the given environment."""
    pass

  def status(self) -> str:
    """Returns the current status of the test.

    Returns:
        str: The status of the test (e.g., "NOT_RUN", "PASSED", "FAILED",
        "SKIPPED").
    """
    return self.test_status

  def report(self) -> dict:
    """Returns a report of the test results.

    Returns:
        dict: A dictionary containing the test report data.
    """
    return self.report_data
