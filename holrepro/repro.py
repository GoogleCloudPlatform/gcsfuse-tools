# Write a program which opens n number of handles for a given file, takes num_handles and file-path as an argument
import argparse
import os
import time

def open_handles_and_read(file_path, num_handles):
    """Opens a specified number of handles to a given file.

    Args:
        file_path: The path to the file.
        num_handles: The number of handles to open.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    handles = []
    try:
        for _ in range(num_handles):
            handle = os.open(file_path, os.O_RDONLY | os.O_DIRECT)
            handles.append(handle)
            chunk_size = 1
            os.read(handle, chunk_size)
        print(f"Successfully opened {num_handles} handles to {file_path}")

        time.sleep(60)
    except Exception as e:
        print(f"Error opening handles: {e}")
    finally:
        for handle in handles:
            os.close(handle)
        print(f"Closed {len(handles)} handles to {file_path}")

def main():
    parser = argparse.ArgumentParser(description="Open multiple handles to a file.")
    parser.add_argument("--file_path", help="Path to the file")
    parser.add_argument("--num_handles", type=int, help="Number of handles to open")
    args = parser.parse_args()

    try:
        open_handles_and_read(args.file_path, args.num_handles)
    except FileNotFoundError as e:
        print(e)

if __name__ == "__main__":
    main()
