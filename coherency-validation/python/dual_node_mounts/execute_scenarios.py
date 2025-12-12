import json
import logging
import os
import sys
import time

# Configure Logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Dynamic Workflow Module Loading
# This module's directory (e.g., .../python/dual_node_mounts)
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))

# The parent python directory (e.g., .../python)
PYTHON_PKG_ROOT = os.path.dirname(MODULE_DIR)

# Workflow config file is in the python pkg root
WORKFLOW_CONFIG_FILE = os.path.join(PYTHON_PKG_ROOT, "workflow_config")

import importlib


def _load_workflow_modules():
  workflow_name = "dual_node_mounts"  # Default
  if os.path.exists(WORKFLOW_CONFIG_FILE):
    try:
      with open(WORKFLOW_CONFIG_FILE, "r") as f:
        wf_config = json.load(f)
        workflow_name = wf_config.get("workflow_name", workflow_name)
    except Exception as e:
      logger.warning(
          f"Failed to read workflow_config for dynamic module loading: {e}."
          f" Using default workflow '{workflow_name}'."
      )

  # Add python package root to sys.path if not already present
  if PYTHON_PKG_ROOT not in sys.path:
    sys.path.insert(0, PYTHON_PKG_ROOT)

  try:
    # Dynamically import using importlib
    config_mod = importlib.import_module(f"{workflow_name}.config")
    mount_mod = importlib.import_module(f"{workflow_name}.mount")
    unmount_mod = importlib.import_module(f"{workflow_name}.unmount")
    cleanup_mod = importlib.import_module(f"{workflow_name}.cleanup")
    fsops_module = importlib.import_module("fsops")

    return config_mod, mount_mod, unmount_mod, cleanup_mod, fsops_module
  except ImportError as e:
    logger.error(
        "Failed to dynamically load workflow modules for"
        f" '{workflow_name}': {e}"
    )
    sys.exit(1)


config, mount, unmount, cleanup, fsops = _load_workflow_modules()

# (Keeping definitions concise for update)
SCENARIO_ID_TO_NAME = {
    1: "Create File and list it",
    2: "Create File and Read it",
    3: "Create Directory and list it",
    4: "Delete file and list it",
    5: "Delete file and Read it",
    6: "Delete Folder and list it",
    7: "Update file and list it",
    8: "Update file and Read it",
    9: "Rename file and list it",
    10: "Rename file and Read it",
    11: "Rename folder and list it",
    12: (
        "Write file before being listed, when it’s been created by another"
        " mount"
    ),
    # 13: "Write the same file from two mounts at the same time",
    13: "Write large file concurrently from multiple threads/mounts",
    14: "Symlink create and list it, read from it",
    15: "Symlink delete and list it, read from it",
    16: "Symlink move and list it, read from it",
    17: "Write without sync/flush and test from other mount",
    18: "Write without sync and test from other mount",
    19: "Write with sync w/o flush and test from other mount",
    20: "Write with sync with flush",
    21: "Create file and read it with odirect",
    22: "Delete file and read with odirect",
    23: "Update file and read it with odirect",
    24: "Rename file and read it with odirect",
    27: (
        "Write large file concurrently from multiple threads/mounts with"
        " odirect"
    ),
}

SCENARIO_NAME_TO_COMMANDS = {
    "Create File and list it": (["createfile"], ["listfile"]),
    "Create File and Read it": (["createfile"], ["readfile"]),
    "Create Directory and list it": (["createdir"], ["listdir"]),
    "Delete file and list it": (
        ["createfile", "deletefile"],
        ["listfile", "listfileandfail"],
    ),
    "Delete file and Read it": (
        ["createfile", "deletefile"],
        ["readfile", "readfileandfail"],
    ),
    "Delete Folder and list it": (
        ["createdir", "deletedir"],
        ["listdir", "listdirandfail"],
    ),
    "Update file and list it": (
        ["createfile", "updatefile"],
        ["listfile", "checkfilehasupdatedsize"],
    ),
    "Update file and Read it": (
        ["createfile", "updatefile"],
        ["readfile", "readfile"],
    ),
    "Rename file and list it": (
        ["createfile", "renamefile"],
        ["listfile", "listfileandfail", "list2ndfile"],
    ),
    "Rename file and Read it": (
        ["createfile", "renamefile"],
        ["readfile", "readfileandfail", "read2ndfile"],
    ),
    "Rename folder and list it": (
        ["createdir", "renamedir"],
        ["listdir", "listdirandfail", "list2nddir"],
    ),
    "Write file before being listed, when it’s been created by another mount": (
        ["createfile", "readfile"],
        ["createfilewith2ndcontent", "readfile"],
    ),
    "Write the same file from two mounts at the same time": (
        ["echo Scenario 13 Manual"],
        ["echo Scenario 13 Manual"],
    ),
    "Write large file concurrently from multiple threads/mounts": (
        ["writebigfile_nolog"],
        ["writebigfile_nolog"],
    ),
    "Symlink create and list it, read from it": (
        ["createsymlink"],
        ["listsymlink", "readfromsymlink"],
    ),
    "Symlink delete and list it, read from it": (
        ["createsymlink", "deletesymlink"],
        ["listsymlinkandfail", "readfromsymlinkandfail"],
    ),
    "Symlink move and list it, read from it": (
        ["createsymlink", "movesymlink"],
        [
            "listsymlinkandfail",
            "readfromsymlinkandfail",
            "list2ndsymlink",
            "readfrom2ndsymlink",
        ],
    ),
    "Write without sync/flush and test from other mount": (
        ["writefilewithoutsyncorflush"],
        ["listfileandfail", "listfile", "readfile"],
    ),
    "Write without sync and test from other mount": (
        ["writefilewithoutsync"],
        ["listfile", "readfile"],
    ),
    "Write with sync w/o flush and test from other mount": (
        ["writefilewithoutflush"],
        ["listfile", "readfile"],
    ),
    "Write with sync with flush": (["writefile"], ["listfile", "readfile"]),
    "Create file and read it with odirect": (
        ["writedirectfile"],
        ["readdirectfile"],
    ),
    "Delete file and read with odirect": (
        ["writedirectfile", "deletefile"],
        ["readdirectfile", "readdirectfileandfail"],
    ),
    "Update file and read it with odirect": (
        ["writedirectfile", "writedirectfilewithupdatedcontent"],
        ["readdirectfile", "readdirectfile"],
    ),
    "Rename file and read it with odirect": (
        ["writedirectfile", "renamefile"],
        ["readdirectfile", "readdirectfileandfail", "readdirect2ndfile"],
    ),
    "Write large file concurrently from multiple threads/mounts with odirect": (
        ["writedirectbigfile_nolog"],
        ["writedirectbigfile_nolog"],
    ),
}

# --- Globals for Logging ---
CURRENT_LOG_FILE = None


def _get_config_value(key, default=True):
  if os.path.exists(config.SHARED_GLOBAL_CONFIG_FILE):
    try:
      with open(config.SHARED_GLOBAL_CONFIG_FILE, "r") as f:
        data = json.load(f)
        return data.get(key, default)
    except:
      return default
  return default


def _setup_file_logging(log_path):
  """Enables file logging if configured."""
  global CURRENT_LOG_FILE
  if _get_config_value("logging_enabled", False):
    try:
      CURRENT_LOG_FILE = open(log_path, "a")
    except Exception as e:
      print(f"Failed to open log file: {e}", file=sys.stderr)


def log_print(msg):
  """Prints to stdout and appends to log file if enabled."""
  print(msg)
  if CURRENT_LOG_FILE:
    try:
      CURRENT_LOG_FILE.write(msg + "\n")
      CURRENT_LOG_FILE.flush()
    except Exception:
      pass


def epoch():
  return "{:.9f}".format(time.time())


def print_banner(msg):
  """Prints a prominent banner message."""
  line = "/" * 88
  log_print("\n\n" + line)
  log_print(f"///////// {msg}")
  log_print(line + "\n\n")


def print_header(title):
  line = "/" * 88
  log_print("\n" + line)
  log_print(f"///////// {title}")
  log_print(line + "\n")


def print_instruction(commands_list, mount_num):
  cmd_string = " ; ".join(commands_list)
  cur_dir_name = os.path.basename(os.getcwd())
  hostname = config.HOSTNAME

  if mount_num == 1:
    msg = (
        "Run following fs commands after both mounts completed from 1st mount:"
        f' "{cmd_string}"'
    )
  else:
    msg = f'Run following fs commands from 2nd mount: "{cmd_string}"'

  log_print(f"[{epoch()}] [{hostname}] [{cur_dir_name}] $ {msg} \n")


def determine_log_filename(commands_tuple):
  mc = "on" if _get_config_value("enable_metadata_cache", True) else "off"
  fc = "on" if _get_config_value("enable_file_cache", True) else "off"

  all_cmds = commands_tuple[0] + commands_tuple[1]
  is_direct = any("direct" in cmd for cmd in all_cmds)
  pc = "off" if is_direct else "on"

  return f"exec_log_file_with_mc_{mc}_fc_{fc}_pc_{pc}.log"


def reset_mount(silent_header=False):
  if not silent_header:
    print_header("Mount Operations")
  try:
    unmount.unmount(config.MOUNT_NUMBER)
    cleanup.cleanup()
    mount.mount(config.MOUNT_NUMBER)
    os.chdir(config.MOUNT_PATH)

    import subprocess

    can_sudo = False
    try:
      subprocess.run(
          ["sudo", "-n", "true"],
          check=True,
          stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL,
      )
      can_sudo = True
    except (subprocess.CalledProcessError, FileNotFoundError):
      pass

    if can_sudo:
      log_print("Clearing page cache...")
      try:
        subprocess.run(
            "sync && sudo sysctl -w vm.drop_caches=3", shell=True, check=True
        )
        log_print("Page cache cleared.")
      except subprocess.CalledProcessError as e:
        log_print(f"Warning: Failed to clear page cache: {e}")
    else:
      log_print(
          "Skipping page cache clear (sudo requires password or is"
          " unavailable)."
      )

  except Exception as e:
    msg = f"Failed to reset mount: {e}"
    logger.error(msg)
    if CURRENT_LOG_FILE:
      CURRENT_LOG_FILE.write(f"[ERROR] {msg}\n")
    sys.exit(1)


def execute_scenario_by_name(scenario_name):
  global CURRENT_LOG_FILE
  mount_num = config.MOUNT_NUMBER
  if mount_num == 0:
    logger.error("Invalid mount number (0). check hostname config.")
    sys.exit(1)

  if scenario_name not in SCENARIO_NAME_TO_COMMANDS:
    logger.error(f'Scenario "{scenario_name}" not found.')
    sys.exit(1)

  commands_tuple = SCENARIO_NAME_TO_COMMANDS[scenario_name]

  # Determine log path early to enable logging for this run
  log_filename = determine_log_filename(commands_tuple)
  log_path = os.path.join(config.SHARED_STATE_DIR, log_filename)

  # Initialize logging for ALL scenarios to ensure headers/banners are logged
  _setup_file_logging(log_path)

  if _get_config_value("logging_enabled", False):
    print(f"Logging execution to: {log_path}")

  # Special handling for latency-sensitive scenarios:
  # We want headers/banners in the log, but we might want to stop logging
  # before the actual instruction/execution to avoid interfering with the test.
  disable_logging_later = False
  if scenario_name in [
      "Write large file concurrently from multiple threads/mounts",
      "Write large file concurrently (Direct) from multiple threads/mounts",
      "Write large file concurrently from multiple threads/mounts with odirect",
  ]:
    disable_logging_later = True

  if mount_num == 1:
    if os.path.exists(config.SHARED_SPECIFIC_CONFIG_FILE):
      logger.error(f"Scenario config exists. Scenario is already running.")
      sys.exit(1)

    print_banner(f'[{epoch()}] Executing scenario: "{scenario_name}"')

    try:
      data = {"scenario_name": scenario_name, "log_file_path": log_path}
      with open(config.SHARED_SPECIFIC_CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)
    except IOError as e:
      logger.error(f"Failed to write shared specific config: {e}")
      sys.exit(1)

    sleep_sec = _get_config_value("sleep_seconds_after_shared_file_write", 15)
    time.sleep(sleep_sec)

    reset_mount(silent_header=False)

    if disable_logging_later:
      if CURRENT_LOG_FILE:
        try:
          CURRENT_LOG_FILE.close()
        except Exception:
          pass
        CURRENT_LOG_FILE = None
      print_banner(
          "WARNING: Auto-logging disabled for the rest of this scenario to"
          " ensure concurrency."
      )
      print(
          "Please manually log any important observations using 'log_custom'."
      )

    print_instruction(commands_tuple[0], 1)

  elif mount_num == 2:
    if not os.path.exists(config.SHARED_SPECIFIC_CONFIG_FILE):
      logger.error(
          "No scenario running (Specific config not found). Run Mount 1 first."
      )
      sys.exit(1)

    try:
      with open(config.SHARED_SPECIFIC_CONFIG_FILE, "r") as f:
        data = json.load(f)
        running_scenario = data["scenario_name"]
        # VM2 should ideally use the same log file as VM1 defined
        # Override local calculation with the authoritative one
        if "log_file_path" in data:
          log_path = data["log_file_path"]
          # Re-init logging with correct path
          if CURRENT_LOG_FILE:
            CURRENT_LOG_FILE.close()
          _setup_file_logging(log_path)

    except (IOError, KeyError) as e:
      logger.error(f"Failed to read shared specific config: {e}")
      sys.exit(1)

    if scenario_name and running_scenario != scenario_name:
      logger.warning(
          f'Running scenario is "{running_scenario}", but argument was'
          f' "{scenario_name}". Using running scenario.'
      )

    if running_scenario not in SCENARIO_NAME_TO_COMMANDS:
      logger.error(f"Unknown scenario: {running_scenario}")
      sys.exit(1)

    commands_tuple = SCENARIO_NAME_TO_COMMANDS[running_scenario]

    reset_mount(silent_header=True)

    print_header("FS Operations")

    if disable_logging_later:
      if CURRENT_LOG_FILE:
        try:
          CURRENT_LOG_FILE.close()
        except Exception:
          pass
        CURRENT_LOG_FILE = None
      print_banner(
          "WARNING: Auto-logging disabled for the rest of this scenario to"
          " ensure concurrency."
      )
      print(
          "Please manually log any important observations using 'log_custom'."
      )

    print_instruction(commands_tuple[1], 2)


def list_all_scenarios():
  print(f"Scenarios for dual_node_mounts:")
  sorted_ids = sorted(SCENARIO_ID_TO_NAME.keys())
  for sid in sorted_ids:
    name = SCENARIO_ID_TO_NAME[sid]
    if name in SCENARIO_NAME_TO_COMMANDS:
      cmds = SCENARIO_NAME_TO_COMMANDS[name]
      cmd_str_1 = " && ".join(cmds[0])
      cmd_str_2 = " && ".join(cmds[1])
      print(f'[{sid}] "{name}" : VM1[{cmd_str_1}] ; VM2[{cmd_str_2}]')


def main():
  if len(sys.argv) > 1 and sys.argv[1] == "--list":
    list_all_scenarios()
    return

  if len(sys.argv) < 2:
    if config.MOUNT_NUMBER == 2:
      if os.path.exists(config.SHARED_SPECIFIC_CONFIG_FILE):
        with open(config.SHARED_SPECIFIC_CONFIG_FILE, "r") as f:
          data = json.load(f)
          name = data["scenario_name"]
        execute_scenario_by_name(name)
        return
      else:
        print(
            "Usage: python -m dual_node_mounts.execute_scenarios"
            " <scenario_id_or_name>"
        )
        sys.exit(1)
    else:
      print(
          "Usage: python -m dual_node_mounts.execute_scenarios"
          " <scenario_id_or_name>"
      )
      sys.exit(1)

  arg = sys.argv[1]
  if arg.isdigit():
    scen_id = int(arg)
    if scen_id in SCENARIO_ID_TO_NAME:
      execute_scenario_by_name(SCENARIO_ID_TO_NAME[scen_id])
    else:
      logger.error(f"Scenario ID {scen_id} not found.")
      sys.exit(1)
  else:
    execute_scenario_by_name(arg)


if __name__ == "__main__":
  main()
