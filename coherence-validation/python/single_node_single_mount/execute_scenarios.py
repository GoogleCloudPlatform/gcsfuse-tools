import importlib
import json
import logging
import os
import sys
import time

# Configure Logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Dynamic Workflow Module Loading
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_PKG_ROOT = os.path.dirname(MODULE_DIR)
WORKFLOW_CONFIG_FILE = os.path.join(PYTHON_PKG_ROOT, "workflow_config")


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

  if PYTHON_PKG_ROOT not in sys.path:
    sys.path.insert(0, PYTHON_PKG_ROOT)

  try:
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
    # 12 Not Applicable
    13: "Write large file concurrently from multiple threads",
    14: "Symlink create and list it, read from it",
    15: "Symlink delete and list it, read from it",
    16: "Symlink move and list it, read from it",
    18: "Write without sync and test from other mount",
    20: "Write with sync with flush",
    21: "Create file and read it with odirect",
    22: "Delete file and read with odirect",
    23: "Update file and read it with odirect",
    24: "Rename file and read it with odirect",
    25: "Read concurrently from 2 threads",
    26: "Read odirect concurrently from 2 threads",
    27: "Write large file concurrently with odirect from multiple threads",
}

# Single list of commands per scenario for single_node_single_mount
SCENARIO_NAME_TO_COMMANDS = {
    "Create File and list it": ["createfile", "listfile"],
    "Create File and Read it": ["createfile", "readfilehasoriginalcontent"],
    "Create Directory and list it": ["createdir", "listdir"],
    "Delete file and list it": [
        "createfile",
        "listfile",
        "deletefile",
        "listfileandfail",
    ],
    "Delete file and Read it": [
        "createfile",
        "readfilehasoriginalcontent",
        "deletefile",
        "readfileandfail",
    ],
    "Delete Folder and list it": [
        "createdir",
        "listdir",
        "deletedir",
        "listdirandfail",
    ],
    "Update file and list it": [
        "createfile",
        "listfile",
        "updatefile",
        "checkfilehasupdatedsize",
    ],
    "Update file and Read it": [
        "createfile",
        "readfilehasoriginalcontent",
        "updatefile",
        "readfilehasupdatedcontent",
    ],
    "Rename file and list it": [
        "createfile",
        "listfile",
        "renamefile",
        "listfileandfail",
        "list2ndfile",
    ],
    "Rename file and Read it": [
        "createfile",
        "readfilehasoriginalcontent",
        "renamefile",
        "readfileandfail",
        "read2ndfile",
    ],
    "Rename folder and list it": [
        "createdir",
        "listdir",
        "renamedir",
        "listdirandfail",
        "list2nddir",
    ],
    "Write large file concurrently from multiple threads": [
        "writebigfileconcurrently",
    ],
    "Symlink create and list it, read from it": [
        "createsymlink",
        "listsymlink",
        "readfromsymlink",
    ],
    "Symlink delete and list it, read from it": [
        "createsymlink",
        "listsymlink",
        "readfromsymlink",
        "deletesymlink",
        "listsymlinkandfail",
        "readfromsymlinkandfail",
    ],
    "Symlink move and list it, read from it": [
        "createsymlink",
        "listsymlink",
        "readfromsymlink",
        "movesymlink",
        "listsymlinkandfail",
        "readfromsymlinkandfail",
        "list2ndsymlink",
        "readfrom2ndsymlink",
    ],
    "Write without sync and test from other mount": [
        "writefilewithoutsync",
        "listfile",
        "readfilehasoriginalcontent",
    ],
    "Write with sync with flush": [
        "writefile",
        "listfile",
        "readfilehasoriginalcontent",
    ],
    "Create file and read it with odirect": ["writedirectfile", "readdirectfilehasoriginalcontent"],
    "Delete file and read with odirect": ["writedirectfile", "readdirectfilehasoriginalcontent", "deletefile", "readdirectfileandfail"],
    "Update file and read it with odirect": ["writedirectfile", "readdirectfilehasoriginalcontent", "writedirectfilewithupdatedcontent", "readdirectfilehasupdatedcontent"],
    "Rename file and read it with odirect": ["writedirectfile", "readdirectfilehasoriginalcontent", "renamefile", "readdirectfileandfail", "readdirect2ndfile"],
    "Read concurrently from 2 threads": ["writebigfile", "readbigfileconcurrently"],
    "Read odirect concurrently frm 2 threads": ["writedirectbigfile", "readdirectbigfileconcurrently"],
    "Write large file concurrently with odirect from multiple threads": [
        "writedirectbigfileconcurrently",
    ],
}

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
  if _get_config_value("logging_enabled", False):
    try:
      # Clear existing handlers to avoid duplicate logs when running multiple scenarios
      root_logger = logging.getLogger()
      for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.FileHandler):
          root_logger.removeHandler(handler)

      file_handler = logging.FileHandler(log_path)
      file_handler.setFormatter(logging.Formatter("%(message)s"))
      root_logger.addHandler(file_handler)
    except Exception as e:
      print(f"Failed to open log file: {e}", file=sys.stderr)


def log_print(msg):
  logging.info(msg)


def epoch():
  return "{:.9f}".format(time.time())


def print_banner(msg):
  line = "/" * 88
  log_print("\n\n" + line)
  log_print(f"///////// {msg}")
  log_print(line + "\n\n")


def print_header(title):
  line = "/" * 88
  log_print("\n" + line)
  log_print(f"///////// {title}")
  log_print(line + "\n")


def determine_log_filename(commands_list):
  mc = "on" if _get_config_value("enable_metadata_cache", True) else "off"
  fc = "on" if _get_config_value("enable_file_cache", True) else "off"
  is_direct = any("direct" in cmd for cmd in commands_list)
  pc = "off" if is_direct else "on"
  return f"exec_log_file_with_mc_{mc}_fc_{fc}_pc_{pc}.log"


def reset_mount():
  print_header("Mount Operations")
  try:
    # Switch to a safe directory before unmounting to avoid "getcwd() failed" errors
    if os.path.exists(config.WORK_DIR):
      os.chdir(config.WORK_DIR)
    else:
      os.chdir("/tmp")

    log_print("Unmounting/Cleaning...")
    unmount.unmount()
    cleanup.cleanup()

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

    log_print("Mounting...")
    mount.mount()

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

    # Default cwd to mount
    os.chdir(config.MOUNT_PATH)
    config.MOUNT_PATH = config.MOUNT_PATH  # Consistency

  except Exception as e:
    msg = f"Failed to reset mount: {e}"
    logger.error(msg)
    sys.exit(1)


def list_all_scenarios():
  print(f"Scenarios for single_node_single_mount:")
  sorted_ids = sorted(SCENARIO_ID_TO_NAME.keys())
  for sid in sorted_ids:
    name = SCENARIO_ID_TO_NAME[sid]
    if name in SCENARIO_NAME_TO_COMMANDS:
      cmds = SCENARIO_NAME_TO_COMMANDS[name]
      full_cmd_str = " && ".join(cmds)
      print(f'[{sid}] "{name}" : {full_cmd_str}')


def list_all_scenarios_and_prompt():
  list_all_scenarios()
  print("\nPlease enter the scenario number you wish to execute:")
  try:
    choice = input("> ")
    return choice.strip()
  except (KeyboardInterrupt, EOFError):
    sys.exit(0)


def run_stepmode_scenario(scenario_name, commands_list):
  log_filename = determine_log_filename(commands_list)
  log_path = os.path.join(config.SHARED_STATE_DIR, log_filename)
  _setup_file_logging(log_path)
  
  enabled = _get_config_value("logging_enabled", False)
  status = "ENABLED" if enabled else "DISABLED"
  print(f"Logging execution to: {log_path} [{status}]")
  
  print_banner(f"[{epoch()}] Executing scenario: \"{scenario_name}\"")

  try:
    data = {
        "scenario_name": scenario_name,
        "log_file_path": log_path,
        "execution_mode": "stepmode",
    }
    with open(config.SHARED_SPECIFIC_CONFIG_FILE, "w") as f:
      json.dump(data, f, indent=4)
  except IOError as e:
    logger.error(f"Failed to write specific config: {e}")
    sys.exit(1)

  reset_mount()

  line = "/" * 88
  print("\n" + line)
  print("///////// Stepmode Instructions")
  print(line + "\n")

  cmd_str = " ; ".join(commands_list)

  print(f"Run: {cmd_str}")
  print("\nUse 'complete_scenario' or 'abort_scenario' when done.")

  print_header("FS Operations")


def run_complete_scenario(scenario_name, commands_list):
  log_filename = determine_log_filename(commands_list)
  log_path = os.path.join(config.SHARED_STATE_DIR, log_filename)
  _setup_file_logging(log_path)
  
  enabled = _get_config_value("logging_enabled", False)
  status = "ENABLED" if enabled else "DISABLED"
  print(f"Logging execution to: {log_path} [{status}]")

  print_banner(f'[{epoch()}] Executing Scenario: "{scenario_name}"')

  try:
    data = {
        "scenario_name": scenario_name,
        "log_file_path": log_path,
        "execution_mode": "complete",
    }
    with open(config.SHARED_SPECIFIC_CONFIG_FILE, "w") as f:
      json.dump(data, f, indent=4)
  except IOError as e:
    logger.error(f"Failed to write specific config: {e}")
    sys.exit(1)

  reset_mount()

  print_header("FS Operations")

  os.chdir(config.MOUNT_PATH)

  for cmd in commands_list:
    if cmd == "Ctrl-C":
      log_print(f"WARNING: Skipping manual step '{cmd}' in complete mode.")
      continue

    log_print(f"Executing: {cmd}")
    if cmd in fsops.OPS_MAP:
      if not fsops.OPS_MAP[cmd]():
        log_print(f"FAILURE: Command '{cmd}' failed.")
        print_banner(f'[{epoch()}] Failed Scenario: "{scenario_name}"')
        sys.exit(1)
    elif cmd.startswith("echo"):
      print(cmd)
    else:
      log_print(f"WARNING: Unknown command '{cmd}'")

  print_banner(f'[{epoch()}] Passed Scenario: "{scenario_name}"')

  if os.path.exists(config.SHARED_SPECIFIC_CONFIG_FILE):
    os.remove(config.SHARED_SPECIFIC_CONFIG_FILE)


def main():
  if len(sys.argv) > 1 and sys.argv[1] == "--list":
    list_all_scenarios()
    return

  args = sys.argv[1:]
  mode = "stepmode"

  clean_args = []
  for arg in args:
    if arg == "--complete":
      mode = "complete"
    elif arg == "--stepmode":
      mode = "stepmode"
    else:
      clean_args.append(arg)

  if not clean_args:
    arg_scen = list_all_scenarios_and_prompt()
    if not arg_scen:
      sys.exit(1)
    clean_args = [arg_scen]

  if mode == "stepmode" and len(clean_args) > 1:
    print("Error: Step-mode supports only one scenario at a time.")
    sys.exit(1)

  for arg_scen in clean_args:
    scenario_name = arg_scen
    scen_id = -1

    if arg_scen.isdigit():
      scen_id = int(arg_scen)
      if scen_id in SCENARIO_ID_TO_NAME:
        scenario_name = SCENARIO_ID_TO_NAME[scen_id]
      else:
        logger.error(f"Scenario ID {scen_id} not found.")
        sys.exit(1)

    if scenario_name not in SCENARIO_NAME_TO_COMMANDS:
      logger.error(f'Scenario "{scenario_name}" not found/supported.')
      sys.exit(1)

    # Check restricted scenarios
    restricted_complete_ids = [17, 19]
    if mode == "complete":
      # If user passed name, we need ID. Reverse lookup.
      if scen_id == -1:
        for sid, sname in SCENARIO_ID_TO_NAME.items():
          if sname == scenario_name:
            scen_id = sid
            break

      if scen_id in restricted_complete_ids:
        print(f"Error: Scenario {scen_id} can be run as step-mode only!!!")
        sys.exit(1)

    commands_list = SCENARIO_NAME_TO_COMMANDS[scenario_name]

    if mode == "complete":
      run_complete_scenario(scenario_name, commands_list)
    else:
      run_stepmode_scenario(scenario_name, commands_list)


if __name__ == "__main__":
  main()
