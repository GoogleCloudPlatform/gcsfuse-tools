#!/usr/bin/env python3
import argparse
import jax
import jax.numpy as jnp
import numpy as np
import orbax.checkpoint as ocp
from jax.sharding import Mesh, NamedSharding, PartitionSpec as P
from functools import partial
import time
import os
import sys

# --- 1. Llama 3.1 70B Configuration ---
LLAMA_70B_CONFIG = {
    "dim": 8192,
    "n_layers": 80,
    "ffn_dim": 28672,
    "vocab_size": 128256,
}

# --- 2. Shared Utilities ---

def setup_environment():
    """Initializes JAX distributed system for TPU Pods."""
    try:
        jax.distributed.initialize()
        print(f"JAX Distributed initialized. Process ID: {jax.process_index()}")
    except Exception as e:
        # It's okay if it fails on a single host/local setup, but warn the user.
        print(f"Notice: JAX distributed init skipped (Single host?): {e}")

    print(f"Devices found: {jax.device_count()}")
    return jax.devices()

def get_sharding_spec():
    """Defines how the Llama 70B weights are split across the mesh."""
    # We split large weight matrices across the 'model' axis
    return {
        'attention': {
            'wq': P('model', None), 
            'wk': P('model', None),
            'wv': P('model', None), 
            'wo': P('model', None),
        },
        'feed_forward': {
            'w1': P('model', None),
            'w2': P('model', None),
            'w3': P('model', None),
        }
    }

def get_abstract_tree():
    """Returns the ShapeDtypeStruct tree (no memory allocated)."""
    def layer_struct():
        return {
            'attention': {
                'wq': jax.ShapeDtypeStruct((LLAMA_70B_CONFIG['dim'], LLAMA_70B_CONFIG['dim']), jnp.bfloat16),
                'wk': jax.ShapeDtypeStruct((LLAMA_70B_CONFIG['dim'], LLAMA_70B_CONFIG['dim'] // 8), jnp.bfloat16),
                'wv': jax.ShapeDtypeStruct((LLAMA_70B_CONFIG['dim'], LLAMA_70B_CONFIG['dim'] // 8), jnp.bfloat16),
                'wo': jax.ShapeDtypeStruct((LLAMA_70B_CONFIG['dim'], LLAMA_70B_CONFIG['dim']), jnp.bfloat16),
            },
            'feed_forward': {
                'w1': jax.ShapeDtypeStruct((LLAMA_70B_CONFIG['dim'], LLAMA_70B_CONFIG['ffn_dim']), jnp.bfloat16),
                'w2': jax.ShapeDtypeStruct((LLAMA_70B_CONFIG['ffn_dim'], LLAMA_70B_CONFIG['dim']), jnp.bfloat16),
                'w3': jax.ShapeDtypeStruct((LLAMA_70B_CONFIG['dim'], LLAMA_70B_CONFIG['ffn_dim']), jnp.bfloat16),
            }
        }
    
    return {
        'params': {
            'layers': {str(i): layer_struct() for i in range(LLAMA_70B_CONFIG['n_layers'])}
        }
    }

def get_checkpointer(path):
    """Returns the AsyncCheckpointer configured for OCDBT."""
    checkpointer = ocp.AsyncCheckpointer(
        ocp.PyTreeCheckpointHandler(use_ocdbt=True, ocdbt_target_data_file_size=200*1024*1024)
    )
    # CheckpointManager manages the folder structure
    return ocp.CheckpointManager(
        os.path.abspath(path) if "gs://" not in path else path,
        checkpointer,
        options=ocp.CheckpointManagerOptions(create=True, max_to_keep=2)
    )

# --- 3. Mode: Create ---

def run_create(args):
    print(f"=== MODE: CREATE CHECKPOINT (Target: {args.path}) ===")
    devices = setup_environment()
    mesh = Mesh(np.array(devices).reshape(1, -1), ('data', 'model'))
    
    # Define sharding
    sharding_spec = get_sharding_spec()
    sharding_tree = {'params': {'layers': {str(i): sharding_spec for i in range(LLAMA_70B_CONFIG['n_layers'])}}}
    target_sharding = jax.tree_util.tree_map(lambda s: NamedSharding(mesh, s), sharding_tree)

    print("Generating random weights directly on devices...")
    
    # Helper to init one layer
    def init_layer(key):
        k1, k2, k3, k4 = jax.random.split(key, 4)
        return {
            'attention': {
                'wq': jax.random.normal(k1, (LLAMA_70B_CONFIG['dim'], LLAMA_70B_CONFIG['dim']), dtype=jnp.bfloat16),
                'wk': jax.random.normal(k2, (LLAMA_70B_CONFIG['dim'], LLAMA_70B_CONFIG['dim'] // 8), dtype=jnp.bfloat16),
                'wv': jax.random.normal(k3, (LLAMA_70B_CONFIG['dim'], LLAMA_70B_CONFIG['dim'] // 8), dtype=jnp.bfloat16),
                'wo': jax.random.normal(k4, (LLAMA_70B_CONFIG['dim'], LLAMA_70B_CONFIG['dim']), dtype=jnp.bfloat16),
            },
            'feed_forward': {
                'w1': jax.random.normal(k1, (LLAMA_70B_CONFIG['dim'], LLAMA_70B_CONFIG['ffn_dim']), dtype=jnp.bfloat16),
                'w2': jax.random.normal(k2, (LLAMA_70B_CONFIG['ffn_dim'], LLAMA_70B_CONFIG['dim']), dtype=jnp.bfloat16),
                'w3': jax.random.normal(k3, (LLAMA_70B_CONFIG['dim'], LLAMA_70B_CONFIG['ffn_dim']), dtype=jnp.bfloat16),
            }
        }

    @jax.jit
    def create_weights():
        layers = {}
        for i in range(LLAMA_70B_CONFIG['n_layers']):
            layers[str(i)] = init_layer(jax.random.key(i))
        return {'params': {'layers': layers}}

    # Force creation into the mesh layout
    with mesh:
        create_fn = jax.jit(create_weights, out_shardings=target_sharding)
        model_state = create_fn()
        # Block until generated
        jax.block_until_ready(model_state)

    # Save
    manager = get_checkpointer(args.path)
    
    print(f"Saving step {args.step} with 200MiB target data file size...")
    manager.save(args.step, model_state)
    manager.wait_until_finished()
    print("Checkpoint creation successful.")

# --- 4. Mode: Restore ---

def run_restore(args):
    print(f"=== MODE: RESTORE CHECKPOINT (Source: {args.path}) ===")
    t0_total = time.perf_counter()
    
    devices = setup_environment()
    mesh = Mesh(np.array(devices).reshape(1, -1), ('data', 'model'))

    # Setup Abstract & Sharding
    abstract_model = get_abstract_tree()
    sharding_spec = get_sharding_spec()
    sharding_tree = {'params': {'layers': {str(i): sharding_spec for i in range(LLAMA_70B_CONFIG['n_layers'])}}}
    target_sharding = jax.tree_util.tree_map(lambda s: NamedSharding(mesh, s), sharding_tree)

    # Restore Args
    restore_args = jax.tree_util.tree_map(
        lambda s: ocp.ArrayRestoreArgs(sharding=s),
        target_sharding
    )

    manager = get_checkpointer(args.path)
    
    print(f"Starting restore of step {args.step}...")
    t0_restore = time.perf_counter()

    restored_state = manager.restore(
        args.step,
        items=abstract_model,
        restore_kwargs={'restore_args': restore_args}
    )
    manager.wait_until_finished()
    
    t1_restore = time.perf_counter()
    restore_duration = t1_restore - t0_restore

    # Calculate Throughput
    total_bytes = jax.tree_util.tree_reduce(lambda x, y: x + y.nbytes, restored_state, initializer=0)
    total_gb = total_bytes / (1024**3)
    throughput = total_gb / restore_duration

    print("-" * 50)
    print(f"PERFORMANCE REPORT")
    print(f"  Model Size:      {total_gb:.2f} GiB")
    print(f"  Restore Time:    {restore_duration:.2f} s")
    print(f"  Throughput:      {throughput:.2f} GiB/s")
    print("-" * 50)
    
    # Verify a leaf
    try:
        leaf = restored_state['params']['layers']['0']['feed_forward']['w1']
        print(f"Verification - Leaf shape: {leaf.shape} | Sharding: {leaf.sharding}")
    except Exception as e:
        print(f"Verification failed: {e}")

# --- 5. Main Entry Point ---

def main():
    parser = argparse.ArgumentParser(description="Llama 3.1 70B Orbax Checkpoint Manager")
    subparsers = parser.add_subparsers(dest='command', required=True, help='Action to perform')

    # Create Command
    parser_create = subparsers.add_parser('create', help='Create a new random 70B checkpoint')
    parser_create.add_argument('--path', type=str, required=True, help='GCS bucket or local path')
    parser_create.add_argument('--step', type=int, default=1, help='Step number')

    # Restore Command
    parser_restore = subparsers.add_parser('restore', help='Restore an existing checkpoint')
    parser_restore.add_argument('--path', type=str, required=True, help='GCS bucket or local path')
    parser_restore.add_argument('--step', type=int, default=1, help='Step number')

    args = parser.parse_args()

    if args.command == 'create':
        run_create(args)
    elif args.command == 'restore':
        run_restore(args)

if __name__ == "__main__":
    main()