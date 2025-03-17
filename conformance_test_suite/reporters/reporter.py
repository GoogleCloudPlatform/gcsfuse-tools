from abc import ABC, abstractmethod


class Reporter(ABC):
  """Abstract base class for defining reporters."""

  def __init__(self, config: dict):
    """Initializes the Reporter object with a configuration dictionary.

    Args:
        config (dict): A dictionary containing configuration parameters for the
          reporter.
    """
    self.config = config

  @abstractmethod
  def process(self, report_data: dict) -> None:
    """Processes the test report data.

    Args:
        report_data (dict): A dictionary containing the test report data.
    """
    pass

  @abstractmethod
  def store(self) -> None:
    """Stores the processed test report data."""
    pass
