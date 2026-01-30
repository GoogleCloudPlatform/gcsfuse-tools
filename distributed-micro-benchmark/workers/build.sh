#!/bin/bash

# Function to build GCSFuse from a specific commit
build_gcsfuse_for_commit() {
    local COMMIT=$1
    local BUILD_DIR="$WORKSPACE/gcsfuse_${COMMIT}"
    
    # Check if already built
    if [ -f "$BUILD_DIR/gcsfuse" ]; then
        echo "$BUILD_DIR/gcsfuse"
        return 0
    fi
    
    echo "Building GCSFuse from commit: $COMMIT" >&2
    
    # Clone if not exists
    if [ ! -d "$BUILD_DIR" ]; then
        git clone https://github.com/GoogleCloudPlatform/gcsfuse.git "$BUILD_DIR" >&2 2>&1 | sed 's/^/  /' >&2
    fi
    
    cd "$BUILD_DIR"
    
    if ! git checkout "$COMMIT" >&2 2>&1; then
        echo "  ERROR: Failed to checkout commit/branch: $COMMIT" >&2
        cd "$WORKSPACE"
        return 1
    fi
    
    echo "  Running go build..." >&2
    go build -o gcsfuse >&2 2>&1
    
    if [ ! -f "gcsfuse" ]; then
        echo "  ERROR: Build failed - gcsfuse binary not created" >&2
        cd "$WORKSPACE"
        return 1
    fi
    
    cd "$WORKSPACE"
    echo "$BUILD_DIR/gcsfuse"
}