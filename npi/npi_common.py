#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared constants, parameter validators, CSV loaders, and BigQuery helpers for NPI."""

import argparse
import csv
import datetime
import json
import logging
import os

try:
    from google.api_core import exceptions
    from google.cloud import bigquery
    BQ_SUPPORTED = True
except ImportError:
    bigquery = None
    exceptions = None
    BQ_SUPPORTED = False

DEFAULT_NUMJOBS = 112
SMOKE_NUMJOBS = 2
DEFAULT_ITERATIONS = 5
SMOKE_ITERATIONS = 1
DEFAULT_FILE_CACHE_SIZE_MB = 2097152

DEFAULT_DIRECT_STANDARD = "1"
DEFAULT_DIRECT_RAPID = "0"
VALID_DIRECT_VALUES = (DEFAULT_DIRECT_RAPID, DEFAULT_DIRECT_STANDARD)

NUMJOBS_ENV_KEYS = ("NUMJOBS", "NUM_JOBS")
DIRECT_ENV_KEY = "DIRECT"

MATRIX_REQUIRED_BASE_COLUMNS = ("FILE_SIZE", "BLOCK_SIZE", "NR_FILES")
MATRIX_READ_REQUIRED_COLUMNS = ("READ_TYPE", "FILE_SIZE", "BLOCK_SIZE", "NR_FILES")


def clean_param_str(val, param_name="parameter"):
    """Normalizes an optional str/int parameter to a stripped string.

    Returns '' for None, and rejects booleans, floats, and other non-(int, str)
    values with a descriptive ValueError naming `param_name`.
    """
    if val is None:
        return ""
    if isinstance(val, bool) or not isinstance(val, (int, str)):
        raise ValueError(
            f"Invalid type {type(val).__name__} ({val!r}) for {param_name}"
        )
    return str(val).strip()


def parse_positive_int(val, param_name="numjobs"):
    """Validates and parses `val` as a strictly positive integer (> 0)."""
    cleaned = clean_param_str(val, param_name=param_name)
    if not cleaned:
        raise ValueError(
            f"Invalid {param_name}={val!r}; must be a positive integer"
        )
    try:
        ivalue = int(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"Invalid {param_name}={val!r}; must be a positive integer"
        ) from exc
    if ivalue <= 0:
        raise ValueError(f"Invalid {param_name}={val!r}; must be > 0")
    return ivalue


def positive_int(value):
    """Argparse type validator requiring a positive integer (> 0)."""
    try:
        return parse_positive_int(value, param_name="value")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_direct(val):
    """Validates and normalizes a FIO DIRECT setting to '0' or '1'."""
    cleaned = clean_param_str(val, param_name=DIRECT_ENV_KEY)
    if cleaned not in VALID_DIRECT_VALUES:
        raise ValueError(
            f"Invalid {DIRECT_ENV_KEY}={val!r}; expected '0' or '1'"
        )
    return cleaned


def direct_flag(value):
    """Argparse type validator requiring stripped '0' or '1' for --direct."""
    try:
        return parse_direct(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _first_non_empty_param(mapping, keys):
    """Returns the first non-empty cleaned string among `keys` in `mapping`."""
    for key in keys:
        cleaned = clean_param_str(mapping.get(key), param_name=key)
        if cleaned:
            return cleaned
    return ""


def resolve_fio_params(fio_env=None, environ=None):
    """Resolves and validates (DIRECT, NUMJOBS) strings from overrides and environ."""
    env_map = os.environ if environ is None else environ
    overrides = fio_env or {}

    direct_raw = (
        clean_param_str(overrides.get(DIRECT_ENV_KEY), param_name=DIRECT_ENV_KEY)
        or clean_param_str(env_map.get(DIRECT_ENV_KEY), param_name=DIRECT_ENV_KEY)
        or DEFAULT_DIRECT_STANDARD
    )
    direct_val = parse_direct(direct_raw)

    numjobs_raw = (
        _first_non_empty_param(overrides, NUMJOBS_ENV_KEYS)
        or _first_non_empty_param(env_map, NUMJOBS_ENV_KEYS)
        or str(DEFAULT_NUMJOBS)
    )
    numjobs_int = parse_positive_int(numjobs_raw, param_name="NUMJOBS")

    return direct_val, str(numjobs_int)


def resolve_numjobs(cli_numjobs=None, config=None, environ=None):
    """Resolves and validates integer worker concurrency (> 0) with 4-tier precedence."""
    if cli_numjobs is not None:
        return parse_positive_int(cli_numjobs, param_name="numjobs")

    cfg = config or {}
    env_map = os.environ if environ is None else environ
    raw = (
        _first_non_empty_param(cfg, NUMJOBS_ENV_KEYS)
        or _first_non_empty_param(env_map, NUMJOBS_ENV_KEYS)
    )
    if not raw:
        return DEFAULT_NUMJOBS
    return parse_positive_int(raw, param_name="NUMJOBS")


def resolve_orchestrator_params(numjobs=None, smoke_mode=False, is_rapid_bucket=False):
    """Resolves (explicit_numjobs, env_numjobs, direct_val) for GCE/GKE runners.

    Returns:
        tuple[str | None, str, str]:
          - explicit_numjobs: CLI '--numjobs=...' value when explicitly overridden
            or in smoke mode, else None (preserving CSV override capability).
          - env_numjobs: Container NUMJOBS environment variable string.
          - direct_val: Container DIRECT environment variable string ('0' or '1').
    """
    if numjobs is not None:
        explicit_numjobs = str(parse_positive_int(numjobs, param_name="numjobs"))
    elif smoke_mode:
        explicit_numjobs = str(SMOKE_NUMJOBS)
    else:
        explicit_numjobs = None

    env_numjobs = explicit_numjobs or str(DEFAULT_NUMJOBS)
    direct_val = DEFAULT_DIRECT_RAPID if is_rapid_bucket else DEFAULT_DIRECT_STANDARD
    return explicit_numjobs, env_numjobs, direct_val


def resolve_smoke_iterations(iterations, smoke_mode):
    """Scales default iterations down to SMOKE_ITERATIONS when smoke_mode is enabled."""
    if smoke_mode and iterations == DEFAULT_ITERATIONS:
        return SMOKE_ITERATIONS
    return iterations


def filter_rapid_benchmarks(
    requested_benchmarks, benchmarks_to_run, is_rapid_bucket, name_fn=str
):
    """Validates and filters out HTTP/1.1 benchmarks when targeting a RAPID bucket.

    Raises:
        ValueError: If an explicit HTTP/1.1 benchmark was requested without 'all'.
    """
    if not is_rapid_bucket:
        return benchmarks_to_run
    if "all" not in requested_benchmarks:
        for bench in requested_benchmarks:
            if "http1" in bench:
                raise ValueError(
                    f"Benchmark '{bench}' is not supported for RAPID buckets "
                    "(only gRPC benchmarks are allowed)."
                )
    return [item for item in benchmarks_to_run if "http1" not in name_fn(item)]


def resolve_bq_table_id(benchmark_name, table_suffix=None) -> str:
    """Resolves the canonical BigQuery table ID for a benchmark definition.

    Mappings:
      - 'host_info' -> 'host_info'
      - 'go_read_<cfg>' -> 'go_client_read_<cfg>'
      - 'read_file_cache' or 'read_file_cache_<cfg>' -> 'fio_read_file_cache'
        (with 'grpc'/'http1' stripped from <cfg>, appending remaining suffix if any)
      - '<other>' -> 'fio_<other>'
    Appends '_<clean_suffix>' when `table_suffix` is non-empty.
    """
    bench = clean_param_str(benchmark_name, param_name="benchmark_name")
    if not bench:
        raise ValueError("Invalid benchmark_name: must not be empty.")

    if bench == "host_info":
        table_id = "host_info"
    elif bench == "go_read":
        table_id = "go_client_read"
    elif bench.startswith("go_read_"):
        cfg = bench[len("go_read_") :]
        table_id = f"go_client_read_{cfg}"
    elif bench == "read_file_cache" or bench.startswith("read_file_cache_"):
        cfg = bench[len("read_file_cache") :].lstrip("_")
        suffix = cfg.replace("grpc", "").replace("http1", "").strip("_")
        table_id = (
            f"fio_read_file_cache_{suffix}"
            if suffix
            else "fio_read_file_cache"
        )
    else:
        table_id = f"fio_{bench}"

    clean_suffix = clean_param_str(table_suffix, param_name="table_suffix")
    if clean_suffix:
        table_id = f"{table_id}_{clean_suffix}"
    return table_id



def fio_template_requires_read_type(fio_template_path):
    """Determines whether a FIO template requires the READ_TYPE CSV column."""
    if fio_template_path and os.path.isfile(fio_template_path):
        try:
            with open(
                fio_template_path, "r", encoding="utf-8", errors="ignore"
            ) as f:
                return "${READ_TYPE}" in f.read()
        except OSError:
            pass
    return "read" in os.path.basename(fio_template_path or "").lower()


def load_matrix_csv(csv_path, required_columns=MATRIX_REQUIRED_BASE_COLUMNS):
    """Loads, strips, and validates a benchmark matrix CSV file.

    - Rejects empty/headerless files (`reader.fieldnames is None`).
    - Strips whitespace from column headers and cell values, normalizing missing
      trailing cells (`None`) on short rows to `""` and dropping `None` keys from
      trailing commas.
    - Validates that `required_columns` exist in the header and are non-empty on
      every data row.
    """
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(
                f"Matrix config CSV is empty or missing a header row: {csv_path}"
            )
        normalized_headers = [
            h.strip() for h in reader.fieldnames if h is not None and h.strip()
        ]
        if not normalized_headers:
            raise ValueError(
                f"Matrix config CSV has no valid column headers: {csv_path}"
            )
        missing_headers = [
            col for col in required_columns if col not in normalized_headers
        ]
        if missing_headers:
            raise ValueError(
                f"Matrix config CSV {csv_path} is missing required column(s): "
                f"{', '.join(missing_headers)}"
            )

        rows = []
        for row_idx, raw_row in enumerate(reader, start=1):
            clean_row = {
                k.strip(): (v or "").strip()
                for k, v in raw_row.items()
                if k is not None and k.strip()
            }
            empty_required = [
                col for col in required_columns if not clean_row.get(col, "")
            ]
            if empty_required:
                raise ValueError(
                    f"Matrix row {row_idx} in {csv_path} has empty required "
                    f"column(s): {', '.join(empty_required)}"
                )
            rows.append(clean_row)
    return rows


def format_matrix_config(clean_row, effective_params, overridden_keys=()):
    """Formats (config_str, config_name) for matrix logging and output directories.

    - `config_str` always displays non-empty row fields together with effective
      resolved parameters (e.g., NUMJOBS and DIRECT), normalizing NUM_JOBS -> NUMJOBS.
    - `config_name` preserves legacy directory naming when optional NUMJOBS/DIRECT
      columns are absent or blank and not overridden on the CLI, while using
      effective resolved values whenever specified in the CSV or on the CLI.
    """
    overridden = set(overridden_keys)
    special_keys = set(NUMJOBS_ENV_KEYS) | {DIRECT_ENV_KEY}

    base_items = {
        k: v for k, v in clean_row.items() if k not in special_keys and v != ""
    }

    log_items = dict(base_items)
    for k, v in effective_params.items():
        log_items[k] = str(v)

    dir_items = dict(base_items)
    csv_has_numjobs = any(clean_row.get(k, "") != "" for k in NUMJOBS_ENV_KEYS)
    if "NUMJOBS" in effective_params and (csv_has_numjobs or "NUMJOBS" in overridden):
        dir_items["NUMJOBS"] = str(effective_params["NUMJOBS"])

    csv_has_direct = clean_row.get(DIRECT_ENV_KEY, "") != ""
    if DIRECT_ENV_KEY in effective_params and (
        csv_has_direct or DIRECT_ENV_KEY in overridden
    ):
        dir_items[DIRECT_ENV_KEY] = str(effective_params[DIRECT_ENV_KEY])

    config_str = ", ".join(f"{k}={v}" for k, v in sorted(log_items.items()))
    config_name = "_".join(f"{k}_{v}" for k, v in sorted(dir_items.items()))
    return config_str, config_name


def _is_bq_not_found(exc, exceptions_module=None):
    exc_mod = exceptions if exceptions_module is None else exceptions_module
    return (
        exc_mod is not None
        and getattr(exc_mod, "NotFound", None) is not None
        and isinstance(exc, exc_mod.NotFound)
    )


def validate_bq_args(
    parser, project_id, bq_dataset_id, bq_table_id, allow_project_only=False
):
    """Validates BigQuery CLI arguments and returns True when BQ upload is enabled.

    Treats None or whitespace-only strings as unset. Calls `parser.error(...)`
    on partial flag combinations. When `allow_project_only=True`, passing
    `--project-id` alone without `--bq-dataset-id` or `--bq-table-id` is treated
    as a local non-BQ run (`False`) for backward compatibility.
    """
    has_project = bool(clean_param_str(project_id, param_name="project_id"))
    has_dataset = bool(
        clean_param_str(bq_dataset_id, param_name="bq_dataset_id")
    )
    has_table = bool(clean_param_str(bq_table_id, param_name="bq_table_id"))

    if has_project and has_dataset and has_table:
        return True
    if not has_dataset and not has_table and (
        allow_project_only or not has_project
    ):
        return False
    parser.error(
        "--project-id, --bq-dataset-id, and --bq-table-id must all be "
        "provided together when uploading to BigQuery."
    )


def init_and_truncate_bq_table(
    project_id,
    bq_dataset_id,
    bq_table_id,
    *,
    bq_supported,
    bq_module,
    truncate_fn,
):
    """Initializes a BigQuery client and truncates the target table before a run."""
    if not bq_supported:
        raise RuntimeError(
            "BigQuery operations requested, but 'google-cloud-bigquery' is not "
            "installed. Please run 'pip3 install google-cloud-bigquery'."
        )
    client = bq_module.Client(project=project_id)
    truncate_fn(client, project_id, bq_dataset_id, bq_table_id)
    return client


def build_bq_results_schema(bq_module=None):
    """Builds the 6-column BigQuery schema shared by FIO and Go-client tables."""
    mod = bigquery if bq_module is None else bq_module
    return [
        mod.SchemaField("run_timestamp", "TIMESTAMP", mode="REQUIRED"),
        mod.SchemaField("iteration", "INTEGER", mode="REQUIRED"),
        mod.SchemaField("gcsfuse_flags", "STRING"),
        mod.SchemaField("fio_env", "STRING"),
        mod.SchemaField("cpu_limit_list", "STRING"),
        mod.SchemaField("fio_json_output", "JSON"),
    ]


def truncate_bq_table(
    client, project_id, dataset_id, table_id, exceptions_module=None
):
    """Erases existing data in the specified BigQuery table if it exists."""
    try:
        full_table_id = f"{project_id}.{dataset_id}.{table_id}"
        try:
            client.get_table(full_table_id)
        except Exception as exc:
            if _is_bq_not_found(exc, exceptions_module):
                logging.info("Table %s not found, skipping truncation.", full_table_id)
                return
            raise

        logging.info("--- Erasing data in BigQuery table: %s ---", full_table_id)
        query = f"TRUNCATE TABLE `{full_table_id}`"
        query_job = client.query(query)
        query_job.result()
        logging.info("Successfully truncated table %s", full_table_id)
    except Exception as e:
        logging.error("Failed to truncate BigQuery table: %s", e)
        raise


def insert_benchmark_row_to_bq(
    client,
    project_id,
    dataset_id,
    table_id,
    iteration,
    gcsfuse_flags,
    env_params,
    cpu_limit_list,
    json_output_content,
    bq_module=None,
    exceptions_module=None,
    verified_tables=None,
):
    """Ensures BigQuery dataset/table exist and inserts one benchmark result row."""
    mod = bigquery if bq_module is None else bq_module
    try:
        full_table_id = f"{project_id}.{dataset_id}.{table_id}"
        if verified_tables is None or full_table_id not in verified_tables:
            dataset_ref = client.dataset(dataset_id)
            table_ref = dataset_ref.table(table_id)

            try:
                client.get_dataset(dataset_ref)
            except Exception as exc:
                if _is_bq_not_found(exc, exceptions_module):
                    logging.info("Dataset %s not found, creating it.", dataset_id)
                    client.create_dataset(mod.Dataset(dataset_ref))
                else:
                    raise

            schema = build_bq_results_schema(mod)

            try:
                client.get_table(table_ref)
            except Exception as exc:
                if _is_bq_not_found(exc, exceptions_module):
                    logging.info("Table %s not found, creating it.", table_id)
                    table = mod.Table(table_ref, schema=schema)
                    client.create_table(table)
                else:
                    raise

            if verified_tables is not None:
                verified_tables.add(full_table_id)

        row_to_insert = {
            "run_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "iteration": iteration,
            "gcsfuse_flags": gcsfuse_flags,
            "fio_env": json.dumps(env_params) if env_params else None,
            "cpu_limit_list": cpu_limit_list,
            "fio_json_output": json_output_content,
        }

        errors = client.insert_rows_json(full_table_id, [row_to_insert])
        if errors:
            logging.error("Errors inserting rows into BigQuery: %s", errors)
        else:
            logging.info(
                "Successfully inserted results for iteration %d into %s",
                iteration,
                full_table_id,
            )
    except Exception as e:
        logging.error("Failed to upload results to BigQuery: %s", e)
        logging.error(
            "Please ensure you have run 'gcloud auth application-default login' "
            "and have the correct permissions."
        )
