import logging
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time

# Import config from the subdirectory package based on workflow_config
try:
  import importlib
  # Try to locate and read workflow_config
  config_path = os.path.join(os.path.dirname(__file__), "workflow_config")
  workflow_package = "dual_node_mounts"  # Default fallback

  if os.path.exists(config_path):
    import json

    try:
      with open(config_path, "r") as f:
        data = json.load(f)
        workflow_package = data.get("workflow_name", "dual_node_mounts")
    except Exception as e:
      logger.warning(
          f"Failed to read workflow_config: {e}. Using default:"
          f" {workflow_package}"
      )

  # Add the current directory to path to allow importing the package
  sys.path.append(os.path.dirname(__file__))

  # Dynamic import using importlib
  config = importlib.import_module(f"{workflow_package}.config")

except ImportError as e:
  logger.error(
      f"Failed to import config from package '{workflow_package}': {e}"
  )
  sys.exit(1)

# Configure Logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Global to override CWD for CLI usage
CLI_MODE = False

# Track background processes
BACKGROUND_PIDS = []

def _run_command(
    cmd,
    shell=False,
    cwd=None,
    expect_fail=False,
    log_cwd=None,
    allow_interrupt=False,
    wait=True,
):
  """Helper to run a command and check its result.

  Args:
      wait: If False, starts the process in background and returns True immediately.
  """
  cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)

  timestamp = "{:.9f}".format(time.time())
  hostname = socket.gethostname()

  # Determine execution directory context
  target_cwd = cwd
  if target_cwd is None:
    if CLI_MODE:
      try:
        target_cwd = os.getcwd()
      except FileNotFoundError:
        # Fallback to configured mount path if CWD is invalid (e.g. stale mount)
        try:
          if os.path.exists(config.MOUNT_PATH):
            logger.warning(
                "Current directory invalid. Falling back to mount path:"
                f" {config.MOUNT_PATH}"
            )
            target_cwd = config.MOUNT_PATH
          else:
            raise
        except:
          logger.error("Current directory invalid and fallback failed.")
          sys.exit(1)
    else:
      try:
        target_cwd = config.MOUNT_PATH
      except NameError:
        target_cwd = os.getcwd()

  # Determine display directory context
  display_cwd = log_cwd if log_cwd else target_cwd
  cwd_name = os.path.basename(display_cwd)

  formatted_msg = f"[{timestamp}] [{hostname}] [{cwd_name}] $ {cmd_str}"
  if not wait:
      formatted_msg += " (Background)"

  logger.info(formatted_msg)

  try:
    if allow_interrupt:
        # Interactive mode
        process = subprocess.Popen(cmd, shell=shell, cwd=target_cwd, text=True)
        if wait:
            try:
                process.wait()
            except KeyboardInterrupt:
                # Allow the interrupt to be handled by the child, wait for it to exit
                process.wait()
        else:
            BACKGROUND_PIDS.append(process)
            return True
    else:
        # Capture output mode
        stdout_target = subprocess.PIPE if wait else None
        stderr_target = subprocess.STDOUT if wait else None
        
        process = subprocess.Popen(
          cmd,
          shell=shell,
          cwd=target_cwd,
          stdout=stdout_target,
          stderr=stderr_target,
          text=True,
        )

        if not wait:
            BACKGROUND_PIDS.append(process)
            return True

        # Stream output
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                logger.info(line.rstrip())
        
        process.wait()

    # Only reached if wait=True
    try:
      if CLI_MODE and allow_interrupt:
        print()

      exit_code = process.returncode

      if allow_interrupt and exit_code != 0:
        status_msg = (
            f"--- (Exit Status: {exit_code} -> Ignored/Interrupted) ---"
        )
      else:
        status_msg = f"--- (Exit Status: {exit_code}) ---"

      logger.info(status_msg)
    except BrokenPipeError:
        pass

    # CRITICAL: Return True if interrupt allowed, regardless of exit code (unless we want to check signals)
    # Assuming ANY exit code is fine if we interrupted it.
    if allow_interrupt:
      return True

    if expect_fail:
      if exit_code == 0:
        logger.error(f"[FAILURE]: Command expected to fail but succeeded.")
        return False
      return True
    else:
      if exit_code != 0:
        logger.error(f"[FAILURE]: Command failed unexpectedly.")
        return False
      return True

  except Exception as e:
    logger.error(f"[FAILURE]: Exception running command {cmd_str}: {e}")
    return False

def wait_for_background_jobs():
    if not BACKGROUND_PIDS:
        logger.info("No background jobs to wait for.")
        return True
        
    logger.info(f"Waiting for {len(BACKGROUND_PIDS)} background jobs...")
    for i, p in enumerate(BACKGROUND_PIDS):
        p.wait()
        logger.info(f"Background job {i+1} (PID {p.pid}) finished with exit code {p.returncode}")
    
    BACKGROUND_PIDS.clear()
    return True


# --- File Operations ---


def create_file(filename="sample.txt", content="sample_content"):
  return _run_command(f"echo '{content}' > {filename}", shell=True)


def create_file_with_2nd_content(filename="sample.txt"):
  return create_file(filename, "sample_content2")


def update_file(filename="sample.txt", content="sample_content2"):
  return create_file(filename, content)


def delete_file(filename="sample.txt"):
  return _run_command(["rm", "-v", filename])


def read_file(filename="sample.txt", expected_content=None):
  return _run_command(["cat", filename])


def read_file_and_fail(filename="sample.txt"):
  return _run_command(["cat", filename], expect_fail=True)


def rename_file(src="sample.txt", dest="sample2.txt"):
  return _run_command(["mv", "-v", src, dest])


def list_file(filename="sample.txt"):
  # Check if file exists using both stat and ls
  cmd = (
      f"if [ -f {filename} ] && ( ls -A | grep -x -q {filename} ); then exit 0;"
      " else exit 1; fi"
  )
  return _run_command(cmd, shell=True)


def list_file_and_fail(filename="sample.txt"):
  # Check if file does NOT exist (expect failure to find it)
  cmd = (
      f"if [ -f {filename} ] || ( ls -A | grep -x -q {filename} ); then exit 1;"
      " else exit 0; fi"
  )
  return _run_command(cmd, shell=True)


# --- Directory Operations ---


def create_dir(dirname="sample_dir"):
  return _run_command(["mkdir", "-pv", dirname])


def list_dir(dirname="sample_dir"):
  return _run_command(f"[ -d {dirname} ]", shell=True)


def list_dir_and_fail(dirname="sample_dir"):
  return _run_command(f"[ ! -d {dirname} ]", shell=True)


def delete_dir(dirname="sample_dir"):
  return _run_command(["rm", "-rfv", dirname])


def rename_dir(src="sample_dir", dest="sample_dir2"):
  return _run_command(["mv", "-v", src, dest])


# --- Symlink Operations ---


def create_symlink(target="sample.txt", link="sample.lnk"):
  create_file(target)
  return _run_command(["ln", "-sfv", target, link])


def delete_symlink(link="sample.lnk"):
  return _run_command(["rm", "-fv", link])


def list_symlink(link="sample.lnk"):
  return _run_command(f"[ -L {link} ]", shell=True)


def list_symlink_and_fail(link="sample.lnk"):
  return _run_command(f"[ ! -L {link} ]", shell=True)


def read_from_symlink(link="sample.lnk"):
  return _run_command(["cat", link])


def read_from_symlink_and_fail(link="sample.lnk"):
  return _run_command(["cat", link], expect_fail=True)


def move_symlink(src="sample.lnk", dest="sample2.lnk"):
  return _run_command(["mv", "-v", src, dest])


# --- Go Program Wrappers ---


def run_go_read(filename, direct=False, expect_fail=False):
  # Determine the directory from which the Go program should be run (parent of the mount)
  safe_cwd = os.path.dirname(config.MOUNT_PATH)

  # Determine the absolute path to the read.go program
  go_program_dir = os.path.dirname(os.path.abspath(__file__))
  full_go_read_path = os.path.join(go_program_dir, "read.go")

  # Calculate the relative path from safe_cwd to the read.go program
  relative_go_read_path = os.path.relpath(full_go_read_path, start=safe_cwd)

  # Get the absolute path of the file inside the mount
  # This uses the current working directory, which is expected to be the mount path
  abs_file_path = os.path.abspath(filename)

  # Log that we are in the current dir (mount), even though we execute from safe_cwd
  current_dir = os.getcwd()

  cmd = ["go", "run", relative_go_read_path]
  if direct:
    cmd.append("--direct")
  cmd.append(abs_file_path)

  return _run_command(
      cmd, cwd=safe_cwd, log_cwd=current_dir, expect_fail=expect_fail
  )


def run_go_write(
    filename,
    content=None,
    size=None,
    direct=False,
    no_sync=False,
    no_flush=False,
    duplicate_writes=1,
    expect_fail=False,
    wait=True,
):
  # Determine the directory from which the Go program should be run (parent of the mount)
  safe_cwd = os.path.dirname(config.MOUNT_PATH)

  # Determine the absolute path to the write.go program
  go_program_dir = os.path.dirname(os.path.abspath(__file__))
  full_go_write_path = os.path.join(go_program_dir, "write.go")

  # Calculate the relative path from safe_cwd to the write.go program
  relative_go_write_path = os.path.relpath(full_go_write_path, start=safe_cwd)

  # Get the absolute path of the file inside the mount
  abs_file_path = os.path.abspath(filename)

  # Log that we are in the current dir (mount), even though we execute from safe_cwd
  current_dir = os.getcwd()

  cmd = ["go", "run", relative_go_write_path]
  if direct:
    cmd.append("--direct")
  if no_sync:
    cmd.append("--no-sync")
  if no_flush:
    cmd.append("--no-flush")
  if content:
    cmd.append(f"--content={content}")
  if size:
    cmd.append(f"--size={size}")
  if duplicate_writes > 1:
    cmd.append(f"--duplicate-writes={duplicate_writes}")

  cmd.append(abs_file_path)

  return _run_command(
      cmd,
      cwd=safe_cwd,
      log_cwd=current_dir,
      expect_fail=expect_fail,
      allow_interrupt=no_flush,
      wait=wait,
  )


def run_go_read_concurrently(
    filename="sample.txt",
    size="1M",
    threads=4,
    verify=True,
    min_read_size=None,
    direct=False,
    expect_fail=False,
):
  # Determine the directory from which the Go program should be run (parent of the mount)
  safe_cwd = os.path.dirname(config.MOUNT_PATH)

  # Determine the absolute path to the read_concurrently.go program
  go_program_dir = os.path.dirname(os.path.abspath(__file__))
  full_go_prog_path = os.path.join(go_program_dir, "read_concurrently.go")

  # Calculate the relative path from safe_cwd
  relative_go_prog_path = os.path.relpath(full_go_prog_path, start=safe_cwd)

  # Get the absolute path of the file inside the mount
  abs_file_path = os.path.abspath(filename)

  current_dir = os.getcwd()

  cmd = ["go", "run", relative_go_prog_path]

  if size:
    cmd.append(f"--size={size}")
  if threads:
    cmd.append(f"--threads={threads}")
  if verify:
    cmd.append("--verify")
  if direct:
    cmd.append("--direct")
  if min_read_size:
    cmd.append(f"--min-read-size={min_read_size}")

  cmd.append(abs_file_path)

  return _run_command(
      cmd, cwd=safe_cwd, log_cwd=current_dir, expect_fail=expect_fail
  )


# --- Check Size Operations ---


def check_file_size(filename, size):
  cmd = (
      f"find . -maxdepth 1 -type f -name {filename} -size {size}c -print -quit"
      " | grep -q ."
  )
  return _run_command(cmd, shell=True)


def check_file_size_and_fail(filename, size):
  cmd = (
      f"find . -maxdepth 1 -type f -name {filename} -size {size}c -print -quit"
      " | grep -q ."
  )
  return _run_command(cmd, shell=True, expect_fail=True)


# --- Content Check Operations ---


def check_file_content(
    filename="sample.txt", expected_content="sample_content2"
):
  return _run_command(f"grep -x '{expected_content}' {filename}", shell=True)


def check_direct_file_content(
    filename="sample.txt", expected_content="sample_content2"
):
  safe_cwd = os.path.dirname(config.MOUNT_PATH)
  go_program_dir = os.path.dirname(os.path.abspath(__file__))
  full_go_read_path = os.path.join(go_program_dir, "read.go")
  relative_go_read_path = os.path.relpath(full_go_read_path, start=safe_cwd)
  abs_file_path = os.path.abspath(filename)
  current_dir = os.getcwd()
  cmd = (
      f"go run {relative_go_read_path} --direct {abs_file_path} | grep -x"
      f" '{expected_content}'"
  )
  return _run_command(cmd, shell=True, cwd=safe_cwd, log_cwd=current_dir)


# --- Aliases mapped to functions ---
OPS_MAP = {
    "createfile": create_file,
    "createfilewith2ndcontent": create_file_with_2nd_content,
    "create2ndfile": lambda: create_file("sample2.txt", "sample_content2"),
    "updatefile": update_file,
    "deletefile": delete_file,
    "delete2ndfile": lambda: delete_file("sample2.txt"),
    "readfile": read_file,
    "readfileandfail": read_file_and_fail,
    "readfilehasupdatedcontent": check_file_content,
    "readfilehasoriginalcontent": lambda: check_file_content(
        "sample.txt", "sample_content"
    ),
    "read2ndfile": lambda: read_file("sample2.txt"),
    "read2ndfilehas1storiginalcontent": lambda: check_file_content(
        "sample2.txt", "sample_content"
    ),
    "read2ndfileandfail": lambda: read_file_and_fail("sample2.txt"),
    "listfile": list_file,
    "listfileandfail": list_file_and_fail,
    "list2ndfile": lambda: list_file("sample2.txt"),
    "list2ndfileandfail": lambda: list_file_and_fail("sample2.txt"),
    "createdir": create_dir,
    "listdir": list_dir,
    "listdirandfail": list_dir_and_fail,
    "deletedir": delete_dir,
    "renamedir": rename_dir,
    "list2nddir": lambda: list_dir("sample_dir2"),
    "renamefile": rename_file,
    "createsymlink": create_symlink,
    "listsymlink": list_symlink,
    "readfromsymlink": read_from_symlink,
    "deletesymlink": delete_symlink,
    "listsymlinkandfail": list_symlink_and_fail,
    "readfromsymlinkandfail": read_from_symlink_and_fail,
    "movesymlink": move_symlink,
    "list2ndsymlink": lambda: list_symlink("sample2.lnk"),
    "readfrom2ndsymlink": lambda: read_from_symlink("sample2.lnk"),
    "checkfilehasupdatedsize": lambda: check_file_size("sample.txt", 16),
    "checkfilehasoriginalsize": lambda: check_file_size("sample.txt", 15),
    "checkfilehasoriginalsizeandfail": lambda: check_file_size_and_fail(
        "sample.txt", 15
    ),
    "readdirectfile": lambda: run_go_read("sample.txt", direct=True),
    "readdirectfileandfail": lambda: run_go_read(
        "sample.txt", direct=True, expect_fail=True
    ),
    "readdirectfilehasupdatedcontent": check_direct_file_content,
    "readdirectfilehasoriginalcontent": lambda: check_direct_file_content(
        "sample.txt", "sample_content"
    ),
    "readdirect2ndfile": lambda: run_go_read("sample2.txt", direct=True),
    "readdirect2ndfilehas1storiginalcontent": lambda: check_direct_file_content(
        "sample2.txt", "sample_content"
    ),
    "writedirectfile": lambda: run_go_write(
        "sample.txt", direct=True, content="sample_content"
    ),
    "writedirectfilewithupdatedcontent": lambda: run_go_write(
        "sample.txt", content="sample_content2", direct=True
    ),
    "writedirectfilewithoutflush": lambda: run_go_write(
        "sample.txt", direct=True, no_flush=True, content="sample_content"
    ),
    "writedirectfilewithoutsync": lambda: run_go_write(
        "sample.txt", direct=True, no_sync=True, content="sample_content"
    ),
    "writedirectbigfile": lambda: run_go_write(
        "sample_2G.txt", size="2G", direct=True
    ),
    "writedirectbigfileandfail": lambda: run_go_write(
        "sample_2G.txt", size="2G", direct=True, expect_fail=True
    ),
    "writefilewithoutsync": lambda: run_go_write(
        "sample.txt", no_sync=True, content="sample_content"
    ),
    "writefilewithoutflush": lambda: run_go_write(
        "sample.txt", no_flush=True, content="sample_content"
    ),
    "writefile": lambda: run_go_write("sample.txt", content="sample_content"),
    "writebigfile": lambda: run_go_write("sample_2G.txt", size="2G"),
    "writebigfileandfail": lambda: run_go_write(
        "sample_2G.txt", size="2G", expect_fail=True
    ),
    "writefilewithoutsyncorflush": lambda: run_go_write(
        "sample.txt", no_sync=True, no_flush=True, content="sample_content"
    ),
    "readbigfileconcurrently": lambda: run_go_read_concurrently(
        "sample_2G.txt", size="2G", threads=10, min_read_size="10M", verify=True
    ),
    "readbigfileconcurrentlyandfail": lambda: run_go_read_concurrently(
        "sample_2G.txt",
        size="2G",
        threads=10,
        min_read_size="10M",
        verify=True,
        expect_fail=True,
    ),
    "readdirectbigfileconcurrently": lambda: run_go_read_concurrently(
        "sample_2G.txt",
        size="2G",
        threads=10,
        min_read_size="10M",
        verify=True,
        direct=True,
    ),
    "readdirectbigfileconcurrentlyandfail": lambda: run_go_read_concurrently(
        "sample_2G.txt",
        size="2G",
        threads=10,
        min_read_size="10M",
        verify=True,
        direct=True,
        expect_fail=True,
    ),
    "writebigfileasync": lambda: run_go_write("sample_2G.txt", size="2G", wait=False),
    "writedirectbigfileasync": lambda: run_go_write("sample_2G.txt", size="2G", direct=True, wait=False),
    "writebigfileconcurrently": lambda: run_go_write("sample_2G.txt", size="2G", duplicate_writes=2),
    "writedirectbigfileconcurrently": lambda: run_go_write(
        "sample_2G.txt", size="2G", direct=True, duplicate_writes=2
    ),
    "waitforbackgroundjobs": wait_for_background_jobs,
}

if __name__ == "__main__":
  if not os.path.exists(config.SHARED_SCENARIO_FILE):
    print(
        "Error: No scenario currently running. Checked:"
        f" {config.SHARED_SCENARIO_FILE} (Workflow: {workflow_package})",
        file=sys.stderr,
    )
    sys.exit(1)

  if len(sys.argv) < 2:
    print("Usage: python fsops.py <command>")
    print("Available commands:", ", ".join(OPS_MAP.keys()))
    sys.exit(1)
  cmd_name = sys.argv[1]
  if cmd_name not in OPS_MAP:
    print(f"Error: Unknown command '{cmd_name}'")
    sys.exit(1)
  CLI_MODE = True
  success = OPS_MAP[cmd_name]()
  if not success:
    sys.exit(1)
