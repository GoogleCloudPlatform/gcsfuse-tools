#!/bin/bash
set -e

# Ensure Go and Go-installed binaries are in PATH
export PATH=/usr/local/go/bin:$HOME/go/bin:$PATH

# 1. Self-healing: Check and install Golang if missing
if ! command -v go &>/dev/null && [ ! -x /usr/local/go/bin/go ]; then
  echo "Golang not found. Installing stable Golang (1.24.0)..."
  GO_VERSION="1.24.0"
  curl -sLO "https://dl.google.com/go/go${GO_VERSION}.linux-amd64.tar.gz"
  sudo rm -rf /usr/local/go
  sudo tar -C /usr/local -xzf "go${GO_VERSION}.linux-amd64.tar.gz"
  rm "go${GO_VERSION}.linux-amd64.tar.gz"
  echo "Golang ${GO_VERSION} installed successfully!"
fi

# 2. Self-healing: Check and install goimports if missing
if ! command -v goimports &>/dev/null && [ ! -x $HOME/go/bin/goimports ]; then
  echo "goimports not found. Installing goimports..."
  go install golang.org/x/tools/cmd/goimports@latest
  echo "goimports installed successfully!"
fi

# Dynamic branch selection (defaults to read-ahead-support)
BRANCH="${BRANCH:-read-ahead-support}"

echo "Starting GCSFuse NPI Conformance Suite on branch ${BRANCH}..."
cd ~/gcsfuse

# Force hard reset to match remote branch exactly, bypassing merge conflicts
git fetch origin
git checkout "${BRANCH}"
git reset --hard "origin/${BRANCH}"

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
      kill -9 "$MAKE_PID" 2>/dev/null || true
      pkill -9 -f 'go test' 2>/dev/null || true
      pkill -9 -f 'gcsfuse' 2>/dev/null || true
      fusermount -u /tmp/gcsfuse* 2>/dev/null || umount -l /tmp/gcsfuse* 2>/dev/null || true
      fusermount -u /mnt/gcsfuse* 2>/dev/null || umount -l /mnt/gcsfuse* 2>/dev/null || true
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

