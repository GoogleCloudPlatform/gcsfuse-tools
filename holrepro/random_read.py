# Write a program which opens n number of handles for a given file, takes num_handles and file-path as an argument
import argparse
import os
import time

def random_read(file_path):
    """Opens a specified number of handles to a given file.

    Args:
        file_path: The path to the file.
        num_handles: The number of handles to open.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    handles = []
    try:
        handle = os.open(file_path, os.O_RDONLY | os.O_DIRECT)
        chunk_size = 128 * 1024
        os.pread(handle, chunk_size, 234)
        os.pread(handle, chunk_size, 234 + 1024 * 1024 * 2)
        # os.pread(handle, chunk_size, 234 + 1024 * 300)
    except Exception as e:
        print(f"Error opening handles: {e}")
    finally:
        os.close(handle)

def main():
    parser = argparse.ArgumentParser(description="Open multiple handles to a file.")
    parser.add_argument("--file_path", help="Path to the file")
    args = parser.parse_args()

    try:
        random_read(args.file_path)
    except FileNotFoundError as e:
        print(e)

if __name__ == "__main__":
    main()
