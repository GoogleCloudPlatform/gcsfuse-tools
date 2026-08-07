#!/bin/bash
set -e

# Ensure Go and Go-installed binaries are in PATH
export PATH=/usr/local/go/bin:$HOME/go/bin:$PATH

TARGET_TYPE="${1:-gce}"
MOUNT_PATH="${2:-/mnt/lssd}"

echo "== [Self-Healing Setup] Target Type: ${TARGET_TYPE} =="

# a) & f) docker.io, docker group membership, and Artifact Registry auth
NEED_DOCKER_INSTALL=false
if ! command -v docker >/dev/null 2>&1; then
  NEED_DOCKER_INSTALL=true
fi

if [ "$NEED_DOCKER_INSTALL" = true ]; then
  echo "Installing docker.io..."
  sudo apt-get update && sudo apt-get install -y docker.io
  sudo systemctl enable --now docker || true
fi

if ! groups "${USER:-$(whoami)}" 2>/dev/null | grep -q -w 'docker'; then
  echo "Adding ${USER:-$(whoami)} to docker group..."
  sudo usermod -aG docker "${USER:-$(whoami)}" || true
fi

DOCKER_CONFIG="${HOME}/.docker/config.json"
if [ ! -f "$DOCKER_CONFIG" ] || ! grep -q "us-docker.pkg.dev" "$DOCKER_CONFIG" 2>/dev/null; then
  echo "Configuring Artifact Registry docker auth..."
  gcloud auth configure-docker us-docker.pkg.dev -q || true
fi

# b) kubectl & google-cloud-cli-gke-gcloud-auth-plugin (if target is GKE or running on GKE runner)
if [ "$TARGET_TYPE" = "gke" ]; then
  NEED_KUBECTL=false
  if ! command -v kubectl >/dev/null 2>&1; then
    NEED_KUBECTL=true
  fi

  NEED_GKE_AUTH_PLUGIN=false
  if ! command -v gke-gcloud-auth-plugin >/dev/null 2>&1 && ! command -v google-cloud-cli-gke-gcloud-auth-plugin >/dev/null 2>&1; then
    NEED_GKE_AUTH_PLUGIN=true
  fi

  if [ "$NEED_KUBECTL" = true ] || [ "$NEED_GKE_AUTH_PLUGIN" = true ]; then
    echo "Installing GKE tools (kubectl, google-cloud-cli-gke-gcloud-auth-plugin)..."
    sudo apt-get update 2>/dev/null || true
    sudo apt-get install -y kubectl google-cloud-cli-gke-gcloud-auth-plugin 2>/dev/null || \
    sudo apt-get install -y kubectl google-cloud-sdk-gke-gcloud-auth-plugin 2>/dev/null || true

    if [ "$NEED_KUBECTL" = true ] && ! command -v kubectl >/dev/null 2>&1; then
      echo "Installing kubectl fallback binary..."
      KUBECTL_VER=$(curl -L -s https://dl.k8s.io/release/stable.txt 2>/dev/null || echo "v1.30.0")
      curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VER}/bin/linux/amd64/kubectl" -o /tmp/kubectl || { echo "kubectl download failed" >&2; }
      if [ -s /tmp/kubectl ] && ! grep -q -i '<html' /tmp/kubectl 2>/dev/null; then
        sudo install -m 0755 /tmp/kubectl /usr/local/bin/kubectl
        rm -f /tmp/kubectl
      else
        echo "ERROR: Failed to download or verify kubectl binary." >&2
        rm -f /tmp/kubectl
      fi
    fi
    if [ "$NEED_GKE_AUTH_PLUGIN" = true ] && ! command -v gke-gcloud-auth-plugin >/dev/null 2>&1 && ! command -v google-cloud-cli-gke-gcloud-auth-plugin >/dev/null 2>&1; then
      echo "Attempting gcloud component installation for gke-gcloud-auth-plugin..."
      gcloud components install gke-gcloud-auth-plugin -q || true
    fi
  fi
fi

# c) make & build-essential
if ! command -v make >/dev/null 2>&1 || ! command -v gcc >/dev/null 2>&1; then
  echo "Installing build-essential and make..."
  sudo apt-get update && sudo apt-get install -y build-essential make
fi

# d) Stable Go 1.24+ toolchain in /usr/local/go and goimports
NEED_GO_INSTALL=true

if [ -x /usr/local/go/bin/go ]; then
  GO_VER_STR=$(/usr/local/go/bin/go version | awk '{print $3}' | sed 's/go//')
  MAJOR=$(echo "$GO_VER_STR" | cut -d. -f1 | sed 's/[^0-9].*//')
  MINOR_NUM=$(echo "$GO_VER_STR" | cut -d. -f2 | sed 's/[^0-9].*//')
  if [ "$MAJOR" -gt 1 ] 2>/dev/null || { [ "$MAJOR" -eq 1 ] 2>/dev/null && [ "$MINOR_NUM" -ge 26 ] 2>/dev/null; }; then
    NEED_GO_INSTALL=false
  fi
fi

if [ "$NEED_GO_INSTALL" = true ]; then
  echo "Installing stable Golang (1.26.5) into /usr/local/go..."
  GO_VERSION="1.26.5"
  curl -fsSL "https://dl.google.com/go/go${GO_VERSION}.linux-amd64.tar.gz" -o /tmp/go.tar.gz || { echo "ERROR: Failed to download Go tarball" >&2; exit 1; }
  if [ -s /tmp/go.tar.gz ] && tar -tzf /tmp/go.tar.gz >/dev/null 2>&1; then
    sudo rm -rf /usr/local/go
    sudo tar -C /usr/local -xzf /tmp/go.tar.gz
    rm -f /tmp/go.tar.gz
    echo "Golang ${GO_VERSION} installed successfully!"
  else
    echo "ERROR: Failed to download or verify Go tarball integrity." >&2
    rm -f /tmp/go.tar.gz
    exit 1
  fi
fi

if ! command -v goimports >/dev/null 2>&1 && [ ! -x "$HOME/go/bin/goimports" ]; then
  echo "Installing goimports..."
  go install golang.org/x/tools/cmd/goimports@latest || true
fi

# e) mdadm
if ! command -v mdadm >/dev/null 2>&1; then
  echo "Installing mdadm..."
  sudo apt-get update && sudo apt-get install -y mdadm --no-install-recommends
fi

echo "== [Self-Healing Setup] Completed successfully =="
