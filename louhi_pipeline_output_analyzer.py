import argparse
import concurrent.futures
from datetime import datetime
import logging
import re
import sys
from typing import Any, Dict, List

from google.auth import default
from google.cloud import storage
import pandas as pd

# --- CONFIGURATION ---
BUCKET_NAME = 'gcsfuse-release-packages'
GCS_AUTH_BASE_URL = 'https://storage.mtls.cloud.google.com'
MAX_WORKERS = 20

# Suppress "Connection pool is full" warnings from parallel requests
logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)

TEST_TYPES = {
    '': 'flat',
    'hns': 'hns',
    'emulator': 'emulator',
    'zonal': 'zonal',
}


def normalize_log_line(line: str) -> str:
  """Normalizes a log line by replacing dynamic elements with placeholders.

  This helps in identifying 'fuzzy' duplicates.
  """
  # Normalize /tmp/gcsfuse_readwrite_test_XXXXXXXXXX paths
  line = re.sub(
      r'/tmp/gcsfuse_readwrite_test_\d+/',
      '/tmp/gcsfuse_readwrite_test_XXXXXX/',
      line,
  )
  # Normalize timestamps in JSON objects
  line = re.sub(
      r'"timestamp":\{"seconds":\d+,"nanos":\d+\}',
      '"timestamp":{"seconds":XXXXXXXXXX,"nanos":YYYYYYYYY}',
      line,
  )
  # Normalize IP addresses and port numbers (e.g., 10.128.0.190:59014)
  line = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+\b', 'IP:PORT', line)
  # Normalize memory addresses (e.g., 0x40005f5a40)
  line = re.sub(r'0x[0-9a-fA-F]+', '0xADDR', line)
  # Normalize goroutine IDs (e.g., goroutine 14)
  line = re.sub(r'goroutine \d+', 'goroutine X', line)
  # Normalize retry failed after N attempts
  line = re.sub(
      r'retry failed after \d+ attempts', 'retry failed after N attempts', line
  )
  # Normalize temp policy JSON file names (e.g., /tmp/iam-policy-217733623.json)
  line = re.sub(
      r'/tmp/iam-policy-\d+\.json', '/tmp/iam-policy-XXXXXX.json', line
  )
  # Normalize timeSeries ranges (e.g., timeSeries[0-7])
  line = re.sub(r'timeSeries\[\d+-\d+\]', 'timeSeries[X-Y]', line)
  # Normalize point counts in error details
  line = re.sub(r'point_count:\d+', 'point_count:N', line)
  return line


def parse_failing_log_content(log_content: str) -> List[Dict[str, Any]]:
  """Parses the log content and returns a list of failing tests."""

  def classify_line(line):
    line = line.strip()
    if line.startswith('=== RUN'):
      return 'test_case_start'
    if line.startswith('--- FAIL:'):
      return 'test_case_end_failed'
    if line.startswith('--- PASS:'):
      return 'test_case_end_successful'
    if line.startswith('--- SKIP:') or line.startswith('SKIP:'):
      return 'test_case_end_skipped'
    if line.startswith('=== Log for'):
      return 'package_start'
    if 'Running static mounting tests with flags:' in line:
      return 'flag_options_setting'
    if line in ['PASS', 'FAIL']:
      return 'keyword_only'
    return 'test_logs'

  lines = log_content.splitlines()
  test_map = {}
  active_tests_stack = []
  current_flags = 'Default/Unknown'
  current_package_name = 'Unknown'
  test_counter = 0

  for line in lines:
    stripped = line.strip()

    # Ignore specific harmless error logs
    if (
        'ERROR: [otel-plugin] ctx passed into client side stats handler metrics'
        ' event handling has no client attempt data present'
        in stripped
    ):
      continue

    classification = classify_line(stripped)

    if classification == 'package_start':
      match = re.search(r'/tmp/(.*?)_release-test', stripped)
      if match:
        current_package_name = match.group(1)

    elif classification == 'flag_options_setting':
      match = re.search(r'\[(.*?)\]', stripped)
      if match:
        current_flags = match.group(1).strip()

    elif classification == 'test_case_start':
      parts = stripped.split()
      if len(parts) >= 3:
        test_name = parts[2]
        test_counter += 1
        if '/' in test_name:
          parent = test_name.rsplit('/', 1)[0]
          if parent in test_map:
            test_map[parent]['has_subtests'] = True

        test_map[test_name] = {
            'test_number': test_counter,
            'package': current_package_name,
            'name': test_name,
            'status': 'RUNNING',
            'flags': current_flags,
            'logs': [stripped],
            'has_subtests': False,
        }
        active_tests_stack.append(test_name)

    elif classification in [
        'test_case_end_successful',
        'test_case_end_failed',
        'test_case_end_skipped',
    ]:
      parts = stripped.split()
      test_name = None
      if classification == 'test_case_end_skipped' and stripped.startswith(
          'SKIP:'
      ):
        if len(parts) >= 2:
          test_name = parts[1]
      elif len(parts) >= 3:
        test_name = parts[2]

      if test_name:
        test_name = test_name.rstrip(':')
        if test_name in test_map:
          test_map[test_name]['status'] = (
              'FAIL' if classification == 'test_case_end_failed' else 'PASS'
          )
          test_map[test_name]['logs'].append(stripped)
          if test_name in active_tests_stack:
            active_tests_stack.remove(test_name)

    elif classification == 'test_logs':
      if active_tests_stack:
        test_map[active_tests_stack[-1]]['logs'].append(stripped)

  failed_leaf_tests = [
      t
      for t in test_map.values()
      if not t['has_subtests'] and t['status'] == 'FAIL'
  ]

  # Apply log compression for each failing test
  for test in failed_leaf_tests:
    original_logs = test['logs']
    compressed_logs = []
    if not original_logs:
      test['logs'] = compressed_logs
      continue

    i = 0
    while i < len(original_logs):
      current_line = original_logs[i]
      current_norm = normalize_log_line(current_line)

      # 1. Check for Single Line Repetition
      # Look ahead for fuzzy duplicates of the current line
      repeat_count = 0
      j = i + 1
      while j < len(original_logs):
        if normalize_log_line(original_logs[j]) == current_norm:
          repeat_count += 1
          j += 1
        else:
          break

      if repeat_count > 0:
        compressed_logs.append(current_line)
        compressed_logs.append(
            f'[repeat of previous line {repeat_count} times]'
        )
        i = j  # Skip the repetitions
        continue

      # 2. Check for 2-Line Block Repetition
      # Only possible if we have at least 4 lines remaining from i (i, i+1, i+2, i+3)
      # Pattern: A, B, A', B'
      if i + 3 < len(original_logs):
        next_line = original_logs[i + 1]
        next_norm = normalize_log_line(next_line)

        # We define the "block" as (current_line, next_line)
        # We check if the *immediately following* 2 lines match this block

        block_repeats = 0
        k = i + 2

        while k + 1 < len(original_logs):
          cand_1 = original_logs[k]
          cand_2 = original_logs[k + 1]
          if (
              normalize_log_line(cand_1) == current_norm
              and normalize_log_line(cand_2) == next_norm
          ):
            block_repeats += 1
            k += 2
          else:
            break

        if block_repeats > 0:
          compressed_logs.append(current_line)
          compressed_logs.append(next_line)
          compressed_logs.append(
              f'[repeat of previous 2 lines {block_repeats} times]'
          )
          i = k  # Skip the block (2 lines) + all repeats (2 * count)
          continue

      # No compression found
      compressed_logs.append(current_line)
      i += 1

    test['logs'] = compressed_logs

  failed_leaf_tests.sort(key=lambda x: x['test_number'])

  return [
      {
          'failing_package': t['package'],
          'failing_test_name': t['name'],
          'relevant_logs': '<br>'.join(t['logs']),
          'gcsfuse_flags': t['flags'],
      }
      for t in failed_leaf_tests
  ]


def process_vm_target(
    bucket, vm_dir: str, suffix_key: str, bucket_type: str
) -> List[Dict[str, str]]:
  """Checks a single VM directory and test type for failures."""
  suffix = f'-{suffix_key}' if suffix_key else ''
  log_file = f'logs{suffix}.txt'
  success_file = f'success{suffix}.txt'

  log_blob = bucket.blob(f'{vm_dir}/{log_file}')
  success_blob = bucket.blob(f'{vm_dir}/{success_file}')

  # If log doesn't exist, test didn't run. If success exists, test passed.
  if not log_blob.exists() or success_blob.exists():
    return []

  print(f'  -> FAILURE: {vm_dir}, Type: {bucket_type} (Missing {success_file})')

  try:
    version, vm_subpath = vm_dir.split('/', 1)
    vm_name = vm_subpath.replace('release-test-', '')
    raw_log_url = (
        f'{GCS_AUTH_BASE_URL}/{BUCKET_NAME}/{version}/{vm_subpath}/{log_file}'
    )

    log_content = log_blob.download_as_text()
    failures = parse_failing_log_content(log_content)

    results = []
    for f in failures:
      results.append({
          'dir_name': vm_name,
          'bucket_type': bucket_type,
          **f,
          'logfile_path': f'[`{log_file}`]({raw_log_url})',
      })
    return results

  except Exception as e:
    print(f'    ERROR processing {vm_dir}/{log_file}: {e}', file=sys.stderr)
    return []


def analyze_failures(version: str):
  try:
    # Explicitly use default credentials to avoid RefreshError
    creds, _ = default()
    client = storage.Client(credentials=creds)
    bucket = client.bucket(BUCKET_NAME)
  except Exception as e:
    print(f'FATAL ERROR: GCS Client init failed: {e}')
    print(
        'Ensure you have run `auth.authenticate_user()` or have correct'
        ' credentials.'
    )
    return pd.DataFrame()

  print(f"Analyzing {BUCKET_NAME} for version '{version}'...")
  prefix = f'{version}/release-test-'

  # List all VM directories first
  iterator = bucket.list_blobs(prefix=prefix, delimiter='/')
  _ = list(iterator)  # Consume iterator to populate prefixes
  vm_dirs = [p.rstrip('/') for p in iterator.prefixes]

  if not vm_dirs:
    print(f'No test directories found for {prefix}')
    return pd.DataFrame()

  print(
      f'Found {len(vm_dirs)} VM directories. Checking for failures'
      ' (Parallel)...'
  )

  all_failures = []
  # Use ThreadPoolExecutor for parallel GCS requests
  with concurrent.futures.ThreadPoolExecutor(MAX_WORKERS) as executor:
    futures = []
    for vm_dir in vm_dirs:
      for suffix_key, bucket_type in TEST_TYPES.items():
        futures.append(
            executor.submit(
                process_vm_target, bucket, vm_dir, suffix_key, bucket_type
            )
        )

    for future in concurrent.futures.as_completed(futures):
      all_failures.extend(future.result())

  if not all_failures:
    print('\n🎉 All checks completed. No failures found.')
    return pd.DataFrame()

  # Create and sort DataFrame
  df = pd.DataFrame(all_failures)
  cols = [
      'dir_name',
      'bucket_type',
      'failing_package',
      'gcsfuse_flags',
      'failing_test_name',
      'logfile_path',
      'relevant_logs',
  ]

  print(
      '\n'
      + '=' * 80
      + '\n                      GCSFuse RELEASE FAILURE SUMMARY\n'
      + '=' * 80
  )
  return df[cols]


def save_markdown(df, filename):
  try:
    # to_markdown requires 'tabulate'
    markdown_content = df.to_markdown(index=False)
  except ImportError:
    print("Warning: 'tabulate' not installed. Using simple string format.")
    markdown_content = df.to_string(index=False)

  with open(filename, 'w') as f:
    f.write(markdown_content)
  print(f'Saved Markdown: {filename}')


def _unique_list_str(x):
  """Helper for aggregations to create a sorted, unique, comma-separated string."""
  return ', '.join(sorted(set(str(i) for i in x if i)))


def generate_detailed_report(df, version_number, timestamp):
  """Generates and saves the main detailed failure report."""
  # --- 1. Main Detailed Report ---
  md_file_main = f'gcsfuse_failed_tests_{version_number}_{timestamp}.md'

  df_md = df.copy().fillna('')
  df_md['logfile_path'] = df_md['logfile_path'].str.replace(
      '`', ''
  )  # Clean links

  # Rename columns
  df_md.columns = [
      'VM',
      'Bucket-type',
      'Failing Package',
      'GCSFuse Flags',
      'Failing Test',
      'Logfile Path',
      'Relevant Logs',
  ]

  # Reorder columns: Swap 'Failing Test' and 'GCSFuse Flags'
  df_md = df_md[[
      'VM',
      'Bucket-type',
      'Failing Package',
      'Failing Test',
      'GCSFuse Flags',
      'Logfile Path',
      'Relevant Logs',
  ]]

  # Sort
  df_md = df_md.sort_values(
      by=['VM', 'Bucket-type', 'Failing Package', 'Failing Test']
  )

  save_markdown(df_md, md_file_main)


def generate_package_summary(df, version_number, timestamp):
  """Generates and saves the package summary report."""
  # --- 2. Package Summary Report ---
  md_file_pkg = f'gcsfuse_package_summary_{version_number}_{timestamp}.md'

  pkg_agg = (
      df.groupby('failing_package')
      .agg(
          num_tests=('failing_test_name', 'count'),
          unique_tests=('failing_test_name', _unique_list_str),
          unique_vms=('dir_name', _unique_list_str),
          unique_buckets=('bucket_type', _unique_list_str),
      )
      .reset_index()
  )

  pkg_agg.columns = [
      'Failing package',
      'Number of failing tests',
      'Unique list of failing Tests',
      'Unique list of VMs',
      'Unique list of bucket-types',
  ]
  pkg_agg = pkg_agg.sort_values(by='Number of failing tests', ascending=False)

  # Add Total Row
  total_row_pkg = pd.DataFrame([{
      'Failing package': 'TOTAL',
      'Number of failing tests': pkg_agg['Number of failing tests'].sum(),
      'Unique list of failing Tests': '',
      'Unique list of VMs': '',
      'Unique list of bucket-types': '',
  }])
  pkg_agg = pd.concat([pkg_agg, total_row_pkg], ignore_index=True)

  save_markdown(pkg_agg, md_file_pkg)


def generate_vm_summary(df, version_number, timestamp):
  """Generates and saves the VM summary report."""
  # --- 3. VM Summary Report ---
  md_file_vm = f'gcsfuse_vm_summary_{version_number}_{timestamp}.md'

  vm_agg = (
      df.groupby('dir_name')
      .agg(
          num_tests=('failing_test_name', 'count'),
          unique_pkgs=('failing_package', _unique_list_str),
          unique_tests=('failing_test_name', _unique_list_str),
          unique_buckets=('bucket_type', _unique_list_str),
      )
      .reset_index()
  )

  vm_agg.rename(
      columns={
          'dir_name': 'Failing VM',
          'num_tests': 'Number of failing tests',
          'unique_pkgs': 'Unique list of failing test packages',
          'unique_tests': 'Unique list of failing Tests',
          'unique_buckets': 'Unique list of bucket-types',
      },
      inplace=True,
  )

  # Reorder columns as requested
  vm_agg = vm_agg[[
      'Failing VM',
      'Number of failing tests',
      'Unique list of failing test packages',
      'Unique list of failing Tests',
      'Unique list of bucket-types',
  ]]
  vm_agg = vm_agg.sort_values(by='Number of failing tests', ascending=False)

  # Add Total Row
  total_row_vm = pd.DataFrame([{
      'Failing VM': 'TOTAL',
      'Number of failing tests': vm_agg['Number of failing tests'].sum(),
      'Unique list of failing test packages': '',
      'Unique list of failing Tests': '',
      'Unique list of bucket-types': '',
  }])
  vm_agg = pd.concat([vm_agg, total_row_vm], ignore_index=True)

  save_markdown(vm_agg, md_file_vm)


def post_process(df, version_number, timestamp):
  """Processes DataFrame and saves reports as Markdown."""
  if df.empty:
    return

  generate_detailed_report(df, version_number, timestamp)
  generate_package_summary(df, version_number, timestamp)
  generate_vm_summary(df, version_number, timestamp)


def main():
  parser = argparse.ArgumentParser(
      description='Analyze GCSFuse release test failures.'
  )
  parser.add_argument(
      'version', nargs='?', help='GCSFuse version (e.g., v3.5.0)'
  )
  args = parser.parse_args()

  version_input = args.version
  if not version_input:
    try:
      version_input = input('Enter GCSFuse version (e.g., v3.4.0): ').strip()
    except EOFError:
      print('Error: No version provided and input stream closed.')
      return

  version_number = version_input.replace('v', '')

  if not re.match(r'^\d+\.\d+\.\d+(\.\d+)?$', version_number):
    print("Invalid version format. Use 'vX.Y.Z'.")
    return

  timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

  # Run Analysis
  results_df = analyze_failures(version_input)

  if not results_df.empty:
    print('\\nAnalysis Results:')
    post_process(results_df, version_number, timestamp)
  else:
    print('No failures found or analysis failed.')


if __name__ == '__main__':
  main()
