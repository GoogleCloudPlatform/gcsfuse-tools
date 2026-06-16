#!/bin/bash -e

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

# Get the mount path from first argument, default to /mnt/lssd
MOUNT_PATH=${1:-/mnt/lssd}

# Check if any SSDs were found. If not, fallback to RAM tmpfs if RAM is >= 600GB.
if [ $NUM_DEVICES -eq 0 ]; then
    echo "No local SSDs found matching the pattern '/dev/disk/by-id/google-local-nvme-ssd-*'."
    echo "Checking if host has sufficient RAM (>= 600GB) to mount tmpfs instead..."
    
    TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    if [ -z "$TOTAL_RAM_KB" ] || ! [[ "$TOTAL_RAM_KB" =~ ^[0-9]+$ ]]; then
        echo "Error: Failed to parse MemTotal from /proc/meminfo."
        exit 1
    fi
    TOTAL_RAM_GB=$((TOTAL_RAM_KB / 1024 / 1024))
    
    if [ $TOTAL_RAM_GB -ge 550 ]; then
        echo "Found ${TOTAL_RAM_GB}GB RAM. Creating 500GB tmpfs memory volume to leave OS headroom..."
        sudo mkdir -p "$MOUNT_PATH"
        sudo mount -t tmpfs -o size=500G tmpfs "$MOUNT_PATH"
        sudo chmod a+w "$MOUNT_PATH"
        echo "Memory volume (tmpfs) mounted successfully at $MOUNT_PATH."
        df -h "$MOUNT_PATH"
        exit 0
    else
        echo "Error: Host has no local SSDs, and RAM is only ${TOTAL_RAM_GB}GB (requires a 600GB VM class, minimum 550GB detected)."
        exit 1
    fi
fi

echo "Found $NUM_DEVICES local SSDs. Creating RAID 0 array..."

# Create the RAID 0 array using all discovered devices.
yes | sudo mdadm --create /dev/md0 --level=0 --raid-devices=$NUM_DEVICES "${DEVICES[@]}"

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
