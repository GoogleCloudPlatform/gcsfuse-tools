#!/usr/bin/env python3
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import time
import torch

MODEL_DIR = "/home/princer_google_com/bucket/llama2-70b-hf"

# hf_weights_files = ['pytorch_model-00001-of-00015.bin', 'pytorch_model-00002-of-00015.bin', 'pytorch_model-00003-of-00015.bin', 'pytorch_model-00004-of-00015.bin', 'pytorch_model-00005-of-00015.bin', 'pytorch_model-00006-of-00015.bin', 'pytorch_model-00007-of-00015.bin', 'pytorch_model-00008-of-00015.bin', 'pytorch_model-00009-of-00015.bin', 'pytorch_model-00010-of-00015.bin', 'pytorch_model-00011-of-00015.bin', 'pytorch_model-00012-of-00015.bin', 'pytorch_model-00013-of-00015.bin', 'pytorch_model-00014-of-00015.bin', 'pytorch_model-00015-of-00015.bin']
hf_weights_files = ['pytorch_model-00003-of-00015.bin']

very_beginning = time.time()
total_size = 0
print(f"Starting workload at {time.time()}")

for hf_weight_file in hf_weights_files:
    local_file = os.path.join(MODEL_DIR, hf_weight_file)

    with open(local_file, 'rb') as file2:
        file_size = os.path.getsize(local_file)
        total_size += file_size
        print(f"Starting file {hf_weight_file} at {time.time()} with size {file_size / 1024 / 1024 / 1024} GiB.")
        state = torch.load(file2, map_location="cpu")
        del state
        torch.cuda.empty_cache()
        print(f"Finished file {hf_weight_file} at {time.time()}")

very_end = time.time()
print(f"Ending workload at {time.time()}")

print(f"Emulator workflow took {very_end - very_beginning}")
print(f"Average throughput: {total_size / (very_end - very_beginning)/ 1024 / 1024} MiB per second")
