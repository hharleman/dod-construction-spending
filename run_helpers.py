"""
run_helpers.py
Purpose: Shared plumbing for historical_run.py / new_opp_run.py so each of
those files stays a thin definition of "what to fetch and where it goes",
while the fetch -> log -> load -> log stage sequence lives in one place.
"""

from datetime import datetime

import pandas as pd

from bigquery_loader import BigQueryLoader
from logger import RunLogger
from config import GCP_PROJECT_ID, CREDENTIALS_PATH


def save_to_csv(df: pd.DataFrame, filename_prefix: str):
    if df.empty:
        print(f"  No data to save for {filename_prefix}")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    full_name = f"{filename_prefix}_{timestamp}.csv"
    df.to_csv(full_name, index=False)
    print(f"  Saved: {full_name}")
    return full_name


def print_table_preview(df: pd.DataFrame, title: str, rows: int = 5):
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)
    print(f"Total Rows: {len(df)}")
    if df.empty:
        print("No data returned")
        return
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 150)
    pd.set_option("display.max_colwidth", 40)
    print(df.head(rows).to_string())
    print("-" * 100)


def run_pull(file_name: str, fetch_fn, table_name: str, schema, csv_prefix: str) -> int:
    """
    Runs one full pull for a single table, writing a run_log row at every
    stage:
      run_start (0) -> api_start (0) -> api_end (rows fetched)
      -> load_start (0) -> load_end (rows loaded) -> run_end (0)
    Any exception is logged with status=ERROR and error_message, then
    re-raised so the calling script exits non-zero.

    Returns the exit code (0 success, 1 error).
    """
    loader = BigQueryLoader(project_id=GCP_PROJECT_ID, credentials_path=CREDENTIALS_PATH)
    logger = RunLogger(client=loader.client, project_id=GCP_PROJECT_ID)

    logger.log(file_name, "run_start", 0)

    try:
        logger.log(file_name, "api_start", 0)
        df = fetch_fn()
        logger.log(file_name, "api_end", len(df))
    except Exception as e:
        logger.log(file_name, "api_end", 0, status="ERROR", error_message=str(e))
        logger.log(file_name, "run_end", 0, status="ERROR", error_message=str(e))
        print(f"\nERROR during fetch: {e}")
        return 1

    print_table_preview(df, f"{file_name.upper()} RESULTS")
    save_to_csv(df, csv_prefix)

    logger.log(file_name, "load_start", 0)
    try:
        loaded = loader.load_dataframe_to_table(df, table_name, schema)
        logger.log(file_name, "load_end", loaded)
    except Exception as e:
        logger.log(file_name, "load_end", 0, status="ERROR", error_message=str(e))
        logger.log(file_name, "run_end", 0, status="ERROR", error_message=str(e))
        print(f"\nERROR during BigQuery load: {e}")
        return 1

    logger.log(file_name, "run_end", 0)
    print(f"\n{file_name}: fetched {len(df)} rows, loaded {loaded} rows into {table_name}")
    return 0
