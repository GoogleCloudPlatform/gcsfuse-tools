import csv
import os
from .reporter import Reporter


class CSVReporter(Reporter):
  """A concrete reporter class that stores test results in a CSV file."""

  def __init__(self, config: dict):
    """Initializes the CSVReporter with the output file path.

    Args:
        config (dict): A dictionary containing configuration parameters for the
          reporter.
    """
    super().__init__(config)
    self.output_file = self.config.get("output_file", "test_results.csv")
    self.report_data_list = []

  def process(self, report_data: dict) -> None:
    """Processes the test report data and adds it to the list of reports.

    Args:
        report_data (dict): A dictionary containing the test report data.
    """
    self.report_data_list.append(report_data)

  def store(self) -> None:
    """Stores the processed test report data in a CSV file."""
    if not self.report_data_list:
      print("No test results to store.")
      return

    # Extract all unique keys from all dictionaries
    all_keys = set()
    for report_data in self.report_data_list:
      all_keys.update(report_data.keys())
    fieldnames = sorted(list(all_keys))

    # Check if file exists and if it is empty
    file_exists = os.path.exists(self.output_file)
    file_empty = not file_exists or os.stat(self.output_file).st_size == 0

    with open(self.output_file, "a", newline="") as csvfile:
      writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
      if file_empty:
        writer.writeheader()
      for report_data in self.report_data_list:
        writer.writerow(report_data)
    print(f"Test results stored in: {self.output_file}")
