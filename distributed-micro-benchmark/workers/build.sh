#!/bin/bash

# Function to build GCSFuse from a specific commit
build_gcsfuse_for_commit() {
    local COMMIT=$1
    local BUILD_DIR="$WORKSPACE/gcsfuse_${COMMIT}"
    
    # Check if already built
    if [ -f "$BUILD_DIR/bin/gcsfuse" ]; then
        echo "$BUILD_DIR/bin/gcsfuse"
        return 0
    fi
    
    echo "Building GCSFuse from commit: $COMMIT" >&2
    
    # Clone if not exists
    if [ ! -d "$BUILD_DIR" ]; then
        git clone https://github.com/GoogleCloudPlatform/gcsfuse.git "$BUILD_DIR" >&2
    fi
    
    cd "$BUILD_DIR"
    
    if ! git checkout "$COMMIT" >&2; then
        echo "  ERROR: Failed to checkout commit/branch: $COMMIT" >&2
        cd "$WORKSPACE"
        return 1
    fi
    
    # Environment setup for a static build
    export CGO_ENABLED=0
    export GO111MODULE=auto
    mkdir -p "$BUILD_DIR/bin" "$BUILD_DIR/sbin"
    # Read the module path (.../gcsfuse/v3, /v4, ...) from go.mod so any version builds.
    local MODULE
    MODULE=$(awk '$1 == "module" {print $2; exit}' "$BUILD_DIR/go.mod")
    
    # Build binaries using -C and -o
    # -C tells Go to run the build inside the source directory
    # -o specifies the exact path for the resulting binary
    go build -C "$BUILD_DIR" -o "$BUILD_DIR/bin/gcsfuse" \
        -ldflags "-X $MODULE/common.gcsfuseVersion=$COMMIT" \
        "$MODULE" >&2
        
    go build -C "$BUILD_DIR" -o "$BUILD_DIR/sbin/mount.gcsfuse" \
        "$MODULE/tools/mount_gcsfuse" >&2
    
    # Output the final path
    echo "$BUILD_DIR/bin/gcsfuse"
}
