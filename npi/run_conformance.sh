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

# Execute GCSFuse Makefile target
make npi-conformance "${MAKE_ARGS[@]}" > ~/integration_tests.log 2>&1
echo $? > ~/conformance.exit
echo "NPI Conformance Suite finished with exit code $(cat ~/conformance.exit)"
