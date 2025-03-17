from typing import Dict, List
import yaml


class ConfigManager:
  """A class to parse configuration from a YAML file and return environment configurations."""

  def __init__(self, config_file):
    """Initializes the ConfigParser with the path to the configuration file.

    Args:
        config_file (str): The path to the YAML configuration file.
    """
    self.config_file = config_file
    self.config = self._load_config()

  def _load_config(self):
    """Loads the configuration from the YAML file.

    Returns:
        dict: The configuration data as a dictionary.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
        yaml.YAMLError: If there is an error parsing the YAML file.
    """
    try:
      with open(self.config_file, "r") as f:
        return yaml.safe_load(f)
    except FileNotFoundError:
      raise FileNotFoundError(
          f"Configuration file not found: {self.config_file}"
      )
    except yaml.YAMLError as e:
      raise yaml.YAMLError(f"Error parsing YAML file: {self.config_file}\n{e}")

  def get_environment_configs(self) -> List[Dict]:
    """Returns a list of environment configurations.

    Returns:
        List[Dict]: A list of dictionaries, where each dictionary represents an
        environment configuration.

    Raises:
        KeyError: If the 'Environments' key is missing in the configuration.
    """
    try:
      environments = self.config.get("Environments")
      if not environments:
        raise KeyError("Environments section not found in config")
      return environments
    except KeyError as e:
      raise KeyError(f"Missing required configuration key: {e}")

  def get_test_configs(self) -> List[Dict]:
    """Returns a list of test configurations.

    Returns:
        List[Dict]: A list of dictionaries, where each dictionary represents a
        test configuration.

    Raises:
        KeyError: If the 'Tests' key is missing in the configuration.
    """
    try:
      tests = self.config.get("Tests")
      if not tests:
        raise KeyError("Tests section not found in config")
      return tests
    except KeyError as e:
      raise KeyError(f"Missing required configuration key: {e}")

  def get_reporter_configs(self) -> List[Dict]:
    """Returns a list of reporter configurations.

    Returns:
        List[Dict]: A list of dictionaries, where each dictionary represents a
        reporter configuration.

    Raises:
        KeyError: If the 'Reporters' key is missing in the configuration.
    """
    try:
      reporters = self.config.get("Reporters")
      if not reporters:
        raise KeyError("Reporters section not found in config")
      return reporters
    except KeyError as e:
      raise KeyError(f"Missing required configuration key: {e}")
