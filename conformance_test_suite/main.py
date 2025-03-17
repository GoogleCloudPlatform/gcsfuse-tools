import argparse
from test_runner import TestRunner


def main():
  """Main function to run the test suite."""
  parser = argparse.ArgumentParser(
      description="Run environment setup and tests."
  )
  parser.add_argument(
      "--config",
      type=str,
      default="config.yaml",
      help="Path to the configuration file.",
  )
  args = parser.parse_args()

  config_file = args.config

  try:
    runner = TestRunner(config_file)
    runner.run()
  except Exception as e:
    print(f"An error occurred: {e}")


if __name__ == "__main__":
  main()
