#!/bin/bash
set -e

EXIT_CODE=0

# Ensure Go and Go-installed binaries are in PATH
export PATH=/usr/local/go/bin:$HOME/go/bin:$PATH

kill_tree() {
    local pid=$1
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        for child in $(pgrep -P "$pid" 2>/dev/null); do
            kill_tree "$child"
        done
        kill -9 "$pid" 2>/dev/null || true
    fi
}

cleanup() {
  echo "Executing conformance cleanup..."
  if [ -n "$MAKE_PID" ]; then
      kill_tree "$MAKE_PID"
  fi
  pkill -9 -P $$ 2>/dev/null || true
  pkill -9 -f 'go test' 2>/dev/null || true
  REAL_PWD=$(pwd -P 2>/dev/null || pwd)
  for m in $(awk '{print $2}' /proc/mounts 2>/dev/null | grep -E '^/(tmp|mnt)/gcsfuse' | sort -r); do
      m_decoded=$(printf '%b\n' "$m")
      if [ -e "$m_decoded" ] || mountpoint -q "$m_decoded" 2>/dev/null; then
          if [ "$m_decoded" = "$REAL_PWD" ] || [[ "$REAL_PWD" == "$m_decoded"/* ]]; then
              continue
          fi
          fusermount -u "$m_decoded" 2>/dev/null || umount -l "$m_decoded" 2>/dev/null || true
      fi
  done
  pkill -9 -x gcsfuse 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 1' INT TERM

# 1. Pre-flight check: Ensure make and build-essential (gcc) are installed
if ! command -v make >/dev/null 2>&1 || ! command -v gcc >/dev/null 2>&1; then
  echo "Build tools missing. Installing build-essential and make..."
  sudo apt-get update && sudo apt-get install -y build-essential make
fi

# 2. Pre-flight check: Version-aware check ensuring go version (PATH or /usr/local/go/bin/go) is >= 1.24
GO_BIN=""
if command -v go >/dev/null 2>&1; then
  GO_BIN=$(command -v go)
elif [ -x /usr/local/go/bin/go ]; then
  GO_BIN="/usr/local/go/bin/go"
fi

NEED_GO_INSTALL=true
if [ -n "$GO_BIN" ]; then
  GO_VER_STR=$("$GO_BIN" version | awk '{print $3}' | sed 's/go//')
  MAJOR=$(echo "$GO_VER_STR" | cut -d. -f1 | sed 's/[^0-9].*//')
  MINOR_NUM=$(echo "$GO_VER_STR" | cut -d. -f2 | sed 's/[^0-9].*//')
  if [ "$MAJOR" -gt 1 ] 2>/dev/null || { [ "$MAJOR" -eq 1 ] 2>/dev/null && [ "$MINOR_NUM" -ge 24 ] 2>/dev/null; }; then
    NEED_GO_INSTALL=false
  fi
fi

if [ "$NEED_GO_INSTALL" = true ]; then
  echo "Go 1.24+ not found in /usr/local/go. Installing stable Golang (1.24.0)..."
  GO_VERSION="1.24.0"
  TARBALL="go${GO_VERSION}.linux-amd64.tar.gz"
  curl -fsSL "https://dl.google.com/go/${TARBALL}" -o "${TARBALL}" || { echo "ERROR: Failed to download Go tarball" >&2; exit 1; }
  if [ -s "${TARBALL}" ] && tar -tzf "${TARBALL}" >/dev/null 2>&1; then
    sudo rm -rf /usr/local/go
    sudo tar -C /usr/local -xzf "${TARBALL}"
    rm -f "${TARBALL}"
    echo "Golang ${GO_VERSION} installed successfully!"
  else
    echo "ERROR: Failed to download or verify Go tarball integrity." >&2
    rm -f "${TARBALL}"
    exit 1
  fi
fi

# 3. Self-healing: Check and install goimports if missing
if ! command -v goimports >/dev/null 2>&1 && [ ! -x "$HOME/go/bin/goimports" ]; then
  echo "goimports not found. Installing goimports..."
  go install golang.org/x/tools/cmd/goimports@latest
  echo "goimports installed successfully!"
fi

# Dynamic branch selection (defaults to master)
BRANCH="${BRANCH:-master}"

echo "Starting GCSFuse NPI Conformance Suite on branch ${BRANCH}..."
TARGET_DIR="${GCSFUSE_DIR:-$HOME/gcsfuse}"
if ! cd "$TARGET_DIR"; then
  echo "ERROR: Failed to change directory to ${TARGET_DIR}" >&2
  echo 1 > ~/conformance.exit
  exit 1
fi

# Force hard reset to match remote branch exactly, bypassing merge conflicts
git fetch origin
git checkout "${BRANCH}"
git reset --hard "origin/${BRANCH}"

if ! grep -q 'npi-conformance:' Makefile; then
  printf '\nnpi-conformance:\n\tbash ./tools/integration_tests/improved_run_e2e_tests.sh --project-id $(PROJECT) --bucket-location $(BUCKET_LOCATION) --skip-non-essential-tests\n' >> Makefile
fi

# Forward orchestrator environment variables to Makefile if specified
MAKE_ARGS=()
if [ -n "${PROJECT}" ]; then
  MAKE_ARGS+=("PROJECT=${PROJECT}")
fi
if [ -n "${BUCKET_LOCATION}" ]; then
  MAKE_ARGS+=("BUCKET_LOCATION=${BUCKET_LOCATION}")
fi
if [ -n "${READ_AHEAD_KB}" ]; then
  MAKE_ARGS+=("READ_AHEAD_KB=${READ_AHEAD_KB}")
fi

# Execute GCSFuse Makefile target with watchdog monitoring for 5-minute inactivity stalls
make npi-conformance "${MAKE_ARGS[@]}" > ~/integration_tests.log 2>&1 &
MAKE_PID=$!

LAST_SIZE=-1
LAST_CHANGE_TIME=$(date +%s)
STALL_TIMEOUT=300

while kill -0 "$MAKE_PID" 2>/dev/null; do
  CURRENT_SIZE=$(stat -c %s ~/integration_tests.log 2>/dev/null || echo 0)
  CURRENT_TIME=$(date +%s)
  
  if [ "$CURRENT_SIZE" -ne "$LAST_SIZE" ]; then
    LAST_SIZE=$CURRENT_SIZE
    LAST_CHANGE_TIME=$CURRENT_TIME
  else
    ELAPSED=$((CURRENT_TIME - LAST_CHANGE_TIME))
    if [ "$ELAPSED" -ge "$STALL_TIMEOUT" ]; then
      echo "ERROR: Conformance test stalled for ${STALL_TIMEOUT}s without log output. Terminating..." >> ~/integration_tests.log
      echo 124 > ~/conformance.exit
      echo "NPI Conformance Suite aborted due to 5-minute stall."
      exit 124
    fi
  fi
  sleep 10
done

wait "$MAKE_PID" || EXIT_CODE=$?
EXIT_CODE=${EXIT_CODE:-0}
echo "$EXIT_CODE" > ~/conformance.exit
echo "NPI Conformance Suite finished with exit code ${EXIT_CODE}"

