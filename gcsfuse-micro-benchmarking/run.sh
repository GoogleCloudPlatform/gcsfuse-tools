#!/bin/bash

python3 distributed_main.py \
  --config_filepath resources/simple_reader_bench.yml \
  --instance-group princer-test \
  --benchmark_id_prefix princer-test-1