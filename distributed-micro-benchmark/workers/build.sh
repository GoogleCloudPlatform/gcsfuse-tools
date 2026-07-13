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
        GCSFUSE_REPO="${GCSFUSE_REPO:-https://github.com/GoogleCloudPlatform/gcsfuse.git}"
        git clone "$GCSFUSE_REPO" "$BUILD_DIR" >&2
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
    
    local MOD_FLAG=""
    # Sync vendor directory with go mod vendor to prevent Go 1.25 inconsistent vendoring errors across all branches
    if [ -d "vendor" ]; then
        if [ "$SKIP_VENDOR_SYNC" = "true" ]; then
            echo "SKIP_VENDOR_SYNC is set to true. Skipping go mod vendor sync and using existing tracked vendor directory." >&2
        else
            echo "Running go mod tidy..." >&2
            go mod tidy >&2
            echo "Syncing vendor directory with go mod vendor..." >&2
            if ! go mod vendor >&2; then
                echo "WARNING: go mod vendor failed on existing directory. Cleaning vendor/ and retrying..." >&2
                rm -rf vendor
                go mod vendor >&2
            fi
        fi
        MOD_FLAG="-mod=vendor"
    fi
    
    # Build binaries using -C and -o
    # -C tells Go to run the build inside the source directory
    # -o specifies the exact path for the resulting binary
    go build -C "$BUILD_DIR" $MOD_FLAG -o "$BUILD_DIR/bin/gcsfuse" \
        -ldflags "-X github.com/googlecloudplatform/gcsfuse/v3/common.gcsfuseVersion=$COMMIT" \
        github.com/googlecloudplatform/gcsfuse/v3 >&2
        
    go build -C "$BUILD_DIR" $MOD_FLAG -o "$BUILD_DIR/sbin/mount.gcsfuse" \
        github.com/googlecloudplatform/gcsfuse/v3/tools/mount_gcsfuse >&2
    
    # Output the final path
    echo "$BUILD_DIR/bin/gcsfuse"
}
