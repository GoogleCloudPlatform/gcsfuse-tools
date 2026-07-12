import lmdb
import numpy as np
import time
import os
import random

DB_DIR = '/home/princer_google_com/gcs/lmdb_test_env'
full_path = os.path.abspath(DB_DIR)
NUM_SAMPLES = 1000
SAMPLE_SIZE = 128 * 1024 # 128KB data block

def create_lmdb_db():
    """Creates an LMDB database, storing data as key-value pairs."""
    if os.path.exists(DB_DIR):
        # Clean up previous run
        import shutil
        # shutil.rmtree(DB_DIR)

    print(f"Creating LMDB database with {NUM_SAMPLES} samples...")
    # map_size is crucial, must be large enough for all data
    env = lmdb.open(DB_DIR, map_size=NUM_SAMPLES * SAMPLE_SIZE * 2) 
    
    with env.begin(write=True) as txn:
        for i in range(NUM_SAMPLES):
            # Key is the index (needs to be bytes)
            key = str(i).encode('ascii')
            
            # Value is the image/feature data + label (serialized)
            label = str(i % 10).encode('ascii')
            data_block = os.urandom(SAMPLE_SIZE)
            value = label + b'_' + data_block
            
            txn.put(key, value)
    
    env.close()
    print(f"Database created: {DB_DIR}")

def run_random_read():
    """Simulates a DataLoader requesting a batch of random indices."""
    print("\n--- Running LMDB Random Read Pattern (simulate batching) ---")
    env = lmdb.open(DB_DIR, readonly=True, lock=False)
    
    # Simulate reading a batch of 100 random samples 100 times (10,0 lookups total)
    NUM_BATCHES = 10
    BATCH_SIZE = 5
    
    start_time = time.time()
    
    with env.begin() as txn:
        for _ in range(NUM_BATCHES):
            # --- The LMDBDataset pattern: Random access by key (index) ---
            random_indices = random.sample(range(NUM_SAMPLES), BATCH_SIZE)
            
            batch_data = []
            for idx in random_indices:
                key = str(idx).encode('ascii')
                value = txn.get(key)
                
                # Simulate deserialization (getting the actual data)
                label, data = value.split(b'_', 1)
                batch_data.append((label, data))
            
            # The model consumes the batch_data here
            pass

    end_time = time.time()
    read_duration = end_time - start_time
    print(f"Total read time (10,000 random samples): {read_duration:.4f} seconds")

    env.close()
    
def run_sequential_read():
    """Reads all samples sequentially using an LMDB cursor."""
    if not os.path.exists(DB_DIR):
        print(f"Error: LMDB environment directory '{DB_DIR}' not found. Please run the creation script first.")
        return

    print("\n--- Running LMDB Sequential Read Pattern (Cursor) ---")
    env = lmdb.open(DB_DIR, readonly=True, lock=False)
    
    count = 0
    start_time = time.time()
    
    with env.begin() as txn:
        # Create a cursor to iterate through the database
        cursor = txn.cursor()
        
        # Iterate over all key-value pairs sequentially
        # The .iternext() method is highly efficient
        for key, value in cursor:
            # Simulate processing the data (e.g., deserializing an image)
            # label, data = value.split(b'_', 1) 
            count += 1
            if count >= NUM_SAMPLES:
                 break # Ensure we don't go past the expected number of samples
    
    end_time = time.time()
    read_duration = end_time - start_time
    
    print(f"Total read time ({count} samples sequentially): {read_duration:.4f} seconds")

    env.close()

if __name__ == '__main__':
    # create_lmdb_db()
    # run_random_read()
    run_sequential_read()
    
    # # Cleanup
    # import shutil
    # shutil.rmtree(DB_DIR)