#!/bin/bash

install_dependencies() {
  echo "Checking and installing dependencies..."
  
  local MISSING_PACKAGES=""
  
  # Check for basic tools
  if ! command -v git &> /dev/null; then MISSING_PACKAGES="$MISSING_PACKAGES git"; fi
  if ! command -v bc &> /dev/null; then MISSING_PACKAGES="$MISSING_PACKAGES bc"; fi
  if ! command -v jq &> /dev/null; then MISSING_PACKAGES="$MISSING_PACKAGES jq"; fi
  if ! command -v envsubst &> /dev/null; then MISSING_PACKAGES="$MISSING_PACKAGES gettext-base"; fi
  
  # Check for build tools
  if ! command -v gcc &> /dev/null; then MISSING_PACKAGES="$MISSING_PACKAGES build-essential"; fi
  if ! dpkg -s libaio-dev &> /dev/null; then MISSING_PACKAGES="$MISSING_PACKAGES libaio-dev"; fi
  
  # FIXED: Add fuse to provide fusermount
  if ! command -v fusermount &> /dev/null; then MISSING_PACKAGES="$MISSING_PACKAGES fuse"; fi

  # Install missing packages if any
  if [ -n "$MISSING_PACKAGES" ]; then
    echo " Installing missing system packages: $MISSING_PACKAGES"
    sudo apt-get update -qq
    sudo apt-get install -y -qq $MISSING_PACKAGES
  fi

  # Install Go manually (Fixed for 1.25.0)
  if ! command -v go &> /dev/null || [[ "$(go version | awk '{print $3}' | sed 's/go//')" < "1.25" ]]; then
    echo " Installing/Updating Go to 1.25.0..."
    cd /tmp
    # Download the 1.25.0 release
    wget -q https://go.dev/dl/go1.25.0.linux-amd64.tar.gz
    sudo rm -rf /usr/local/go
    sudo tar -C /usr/local -xzf go1.25.0.linux-amd64.tar.gz
    
    # CRITICAL: Prepend to PATH so it overrides system Go
    export PATH=/usr/local/go/bin:$PATH
    
    cd "$WORKSPACE"
    echo " Go version: $(go version)"
  fi
  
  # Install FIO from source if not present
  if ! command -v fio &> /dev/null; then
    echo " Installing FIO from latest master..."
    FIO_SRC_DIR="/tmp/fio"
    sudo rm -rf "$FIO_SRC_DIR"
    git clone https://github.com/axboe/fio.git "$FIO_SRC_DIR"
    cd "$FIO_SRC_DIR"
    # Increase latency buckets
    sed -i 's/define \+FIO_IO_U_PLAT_GROUP_NR \+\([0-9]\+\)/define FIO_IO_U_PLAT_GROUP_NR 32/g' stat.h
    ./configure && make && sudo make install
    cd "$WORKSPACE"
    echo " FIO version: $(fio --version)"
  fi

  echo "✓ Dependencies ready"
}
