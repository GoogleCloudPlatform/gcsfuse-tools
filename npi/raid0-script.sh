#!/bin/bash -e

MOUNT_POINT="${MOUNT_POINT:-${1:-/mnt/disks/gcsfuse-buffer}}"
if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    echo "Buffer mountpoint $MOUNT_POINT is already mounted. Nothing to do."
    exit 0
fi

# Update the package list and install mdadm if it's not already installed.
sudo apt-get update
sudo apt-get install -y mdadm --no-install-recommends

# Find all local SSD devices and store their paths in an array.
# This command looks for all devices that match the pattern 'google-local-nvme-ssd-*'.
# If your SSDs have a different naming pattern, you can adjust it here.
shopt -s nullglob
DEVICES=(/dev/disk/by-id/google-local-nvme-ssd-*)

# Get the total number of SSDs found.
NUM_DEVICES=${#DEVICES[@]}

# Get the mount path from MOUNT_POINT
MOUNT_PATH="$MOUNT_POINT"

# Check if mount path is already mounted
if mountpoint -q "$MOUNT_PATH"; then
    echo "Storage buffer is already mounted at $MOUNT_PATH."
    df -h "$MOUNT_PATH"
    exit 0
fi

# Check if any SSDs were found. If not, fallback to RAM tmpfs.
if [ $NUM_DEVICES -eq 0 ]; then
    echo "No local SSDs found matching the pattern '/dev/disk/by-id/google-local-nvme-ssd-*'."
    echo "Fallback to tmpfs RAM disk buffer..."
    
    TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    if [ -z "$TOTAL_RAM_KB" ] || ! [[ "$TOTAL_RAM_KB" =~ ^[0-9]+$ ]]; then
        echo "Error: Failed to parse MemTotal from /proc/meminfo."
        exit 1
    fi
    TOTAL_RAM_GB=$((TOTAL_RAM_KB / 1024 / 1024))
    
    if [ $TOTAL_RAM_GB -ge 550 ]; then
        TMPFS_SIZE_GB=500
    else
        TMPFS_SIZE_GB=$((TOTAL_RAM_GB / 2))
        if [ $TMPFS_SIZE_GB -lt 1 ]; then
            TMPFS_SIZE_GB=1
        fi
    fi
    
    echo "Found ${TOTAL_RAM_GB}GB RAM. Creating ${TMPFS_SIZE_GB}G tmpfs memory volume..."
    sudo mkdir -p "$MOUNT_PATH"
    sudo mount -t tmpfs -o size=${TMPFS_SIZE_GB}G tmpfs "$MOUNT_PATH"
    sudo chmod a+w "$MOUNT_PATH"
    echo "Memory volume (tmpfs) mounted successfully at $MOUNT_PATH."
    df -h "$MOUNT_PATH"
    exit 0
fi

if [ $NUM_DEVICES -eq 1 ]; then
    echo "Found 1 local SSD (${DEVICES[0]}). Creating 1-drive RAID 0 array with --force..."
else
    echo "Found $NUM_DEVICES local SSDs. Creating RAID 0 array..."
fi

# Create the RAID 0 array using all discovered devices.
# Note: mdadm --create requires --force when creating array with a single device (NUM_DEVICES=1).
yes | sudo mdadm --create --force --verbose /dev/md0 --level=0 --raid-devices=$NUM_DEVICES "${DEVICES[@]}"

echo "Formatting the RAID array..."

# Format the newly created array with the ext4 filesystem.
sudo mkfs.ext4 -F /dev/md0

echo "Mounting the RAID array..."

# Create a directory to mount the array.
sudo mkdir -p "$MOUNT_PATH"

# Mount the array to the created directory.
sudo mount /dev/md0 "$MOUNT_PATH"

# Set write permissions for all users.
sudo chmod a+w "$MOUNT_PATH"

echo "RAID 0 array created and mounted successfully at $MOUNT_PATH."

# Display the filesystem information to verify the setup.
df -h "$MOUNT_PATH"
