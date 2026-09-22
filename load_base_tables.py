"""
load_base_tables.py
One-time (or re-run-as-needed) loader: reads temp_tables.xlsx (reference
data maintained outside this repo) and loads its sheets into BigQuery:
  dim_base_location    <- 'dim_base_location' sheet
  dim_location_crosswalk <- 'dim_location_crosswalk' sheet
  estimate_crx         <- 'estiamte_crx' sheet (sic - source file's typo)
  suppl_crx             <- 'suppl_crx' sheet
Run bigquery_setup.py for these tables first if they don't exist yet.

dim_base_location.uic is NOT in the source file - it starts NULL and is
backfilled over time from confirmed DD1391 review (see app.py's Review
tab), so this loader only ever touches the other columns; it never wipes
a uic value that's already been filled in.

Usage:
  python load_base_tables.py [path/to/temp_tables.xlsx]
"""

import os
import sys

import pandas as pd
from google.cloud import bigquery

from config import GCP_PROJECT_ID, CREDENTIALS_PATH, DATASET_ID
from schema import (
    DIM_BASE_LOCATION_SCHEMA, DIM_LOCATION_CROSSWALK_SCHEMA,
    ESTIMATE_CRX_SCHEMA, SUPPL_CRX_SCHEMA,
)

DEFAULT_PATH = "temp_tables.xlsx"


def _load_base_location(client: bigquery.Client, xls: pd.ExcelFile):
    table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dim_base_location"

    df = pd.read_excel(xls, sheet_name="dim_base_location")
    df.columns = [c.strip() for c in df.columns]
    for col in ("zip", "gisjoin"):
        df[col] = df[col].apply(lambda v: None if pd.isna(v) else str(v))

    # Preserve any uic values already backfilled by the review UI - only
    # overwrite the columns that come from the source file.
    existing_uics = {}
    try:
        existing = client.query(
            f"SELECT base_id, uic FROM `{table_id}` WHERE uic IS NOT NULL"
        ).result()
        existing_uics = {row.base_id: row.uic for row in existing}
    except Exception:
        pass  # table doesn't exist yet or is empty - nothing to preserve

    df["uic"] = df["base_id"].map(existing_uics)

    job_config = bigquery.LoadJobConfig(schema=DIM_BASE_LOCATION_SCHEMA, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_dataframe(df, table_id, job_config=job_config).result()
    print(f"Loaded {len(df)} rows into dim_base_location ({len(existing_uics)} existing UIC(s) preserved)")


def _load_location_crosswalk(client: bigquery.Client, xls: pd.ExcelFile):
    table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dim_location_crosswalk"
    df = pd.read_excel(xls, sheet_name="dim_location_crosswalk")
    df.columns = [c.strip() for c in df.columns]

    job_config = bigquery.LoadJobConfig(schema=DIM_LOCATION_CROSSWALK_SCHEMA, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_dataframe(df, table_id, job_config=job_config).result()
    print(f"Loaded {len(df)} rows into dim_location_crosswalk")


def _load_estimate_crx(client: bigquery.Client, xls: pd.ExcelFile):
    table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.estimate_crx"
    df = pd.read_excel(xls, sheet_name="estiamte_crx")
    df.columns = [c.strip() for c in df.columns]

    job_config = bigquery.LoadJobConfig(schema=ESTIMATE_CRX_SCHEMA, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_dataframe(df, table_id, job_config=job_config).result()
    print(f"Loaded {len(df)} rows into estimate_crx")


def _load_suppl_crx(client: bigquery.Client, xls: pd.ExcelFile):
    table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.suppl_crx"
    df = pd.read_excel(xls, sheet_name="suppl_crx")
    df.columns = [c.strip() for c in df.columns]
    df["desc_code"] = df["desc_code"].apply(lambda v: None if pd.isna(v) else str(v))

    job_config = bigquery.LoadJobConfig(schema=SUPPL_CRX_SCHEMA, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_dataframe(df, table_id, job_config=job_config).result()
    print(f"Loaded {len(df)} rows into suppl_crx")


def main(path: str):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
    client = bigquery.Client(project=GCP_PROJECT_ID)
    xls = pd.ExcelFile(path)
    _load_base_location(client, xls)
    _load_location_crosswalk(client, xls)
    _load_estimate_crx(client, xls)
    _load_suppl_crx(client, xls)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH))
