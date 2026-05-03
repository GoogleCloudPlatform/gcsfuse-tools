#!/bin/bash

# Update the package list and install mdadm if it's not already installed.
sudo apt update
sudo apt install mdadm --no-install-recommends

# Find all local SSD devices and store their paths in an array.
# This command looks for all devices that match the pattern 'google-local-nvme-ssd-*'.
# If your SSDs have a different naming pattern, you can adjust it here.
DEVICES=(/dev/disk/by-id/google-local-nvme-ssd-*)

# Get the total number of SSDs found.
NUM_DEVICES=${#DEVICES[@]}

# Check if any SSDs were found. If not, exit the script.
if [ $NUM_DEVICES -eq 0 ]; then
    echo "No local SSDs found matching the pattern '/dev/disk/by-id/google-local-nvme-ssd-*'."
    echo "Please run 'ls -l /dev/disk/by-id/' to verify the device names."
    exit 1
fi

echo "Found $NUM_DEVICES local SSDs. Creating RAID 0 array..."

# Create the RAID 0 array using all discovered devices.
sudo mdadm --create /dev/md0 --level=0 --raid-devices=$NUM_DEVICES "${DEVICES[@]}"

echo "Formatting the RAID array..."

# Format the newly created array with the ext4 filesystem.
sudo mkfs.ext4 -F /dev/md0

echo "Mounting the RAID array..."

# Create a directory to mount the array.
sudo mkdir -p /mnt/lssd

# Mount the array to the created directory.
sudo mount /dev/md0 /mnt/lssd

# Set write permissions for all users.
sudo chmod a+w /mnt/lssd

echo "RAID 0 array created and mounted successfully at /mnt/lssd."

# Display the filesystem information to verify the setup.
df -h /mnt/lssd
