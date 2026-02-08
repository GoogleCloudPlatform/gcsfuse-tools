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
    
    echo "Building GCSFuse from commit: $COMMIT"
    
    # Clone if not exists
    if [ ! -d "$BUILD_DIR" ]; then
        git clone https://github.com/GoogleCloudPlatform/gcsfuse.git "$BUILD_DIR"
    fi
    
    cd "$BUILD_DIR"
    
    if ! git checkout "$COMMIT"; then
        echo "  ERROR: Failed to checkout commit/branch: $COMMIT"
        cd "$WORKSPACE"
        return 1
    fi
    
    # Environment setup for a static build
    export CGO_ENABLED=0
    export GO111MODULE=auto
    mkdir -p "$BUILD_DIR/bin" "$BUILD_DIR/sbin"
    
    # Build binaries using -C and -o
    # -C tells Go to run the build inside the source directory
    # -o specifies the exact path for the resulting binary
    go build -C "$BUILD_DIR" -o "$BUILD_DIR/bin/gcsfuse" \
        -ldflags "-X github.com/googlecloudplatform/gcsfuse/v3/common.gcsfuseVersion=$COMMIT" \
        github.com/googlecloudplatform/gcsfuse/v3
        
    go build -C "$BUILD_DIR" -o "$BUILD_DIR/sbin/mount.gcsfuse" \
        github.com/googlecloudplatform/gcsfuse/v3/tools/mount_gcsfuse
    
    # Output the final path
    echo "$BUILD_DIR/bin/gcsfuse"
}
