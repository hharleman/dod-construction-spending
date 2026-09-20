"""
bigquery_setup.py
INACTIVE BY DEFAULT - this is the only file allowed to create or change
BigQuery structure (dataset + tables). It is never imported or called by
project_run.py / historical_run.py / new_opp_run.py, so running the daily
pipeline can never accidentally alter table structure.

Run this by hand only when you need to:
  - set up the dataset/tables for the first time, or
  - deliberately reset one or more tables' schema (this DROPS and recreates
    them, which deletes any data currently in those tables)

Usage:
  python bigquery_setup.py                 # all tables
  python bigquery_setup.py project_preaward piid_issuing_type   # just these
"""

import os
import sys

from google.cloud import bigquery

from schema import TABLES
from piid_reference import ISSUING_OFFICE_LOOKUP, INSTRUMENT_TYPE_LOOKUP, SERVICE_CODE_LOOKUP
from config import GCP_PROJECT_ID, CREDENTIALS_PATH, DATASET_ID

REFERENCE_TABLES = {"piid_issuing_office", "piid_issuing_type", "piid_service_code"}


def main(table_names):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
    client = bigquery.Client(project=GCP_PROJECT_ID)

    dataset_ref = f"{GCP_PROJECT_ID}.{DATASET_ID}"
    try:
        client.get_dataset(dataset_ref)
        print(f"Dataset exists: {DATASET_ID}")
    except Exception:
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = "US"
        client.create_dataset(dataset)
        print(f"Created dataset: {DATASET_ID}")

    for table_name in table_names:
        schema = TABLES[table_name]
        table_id = f"{dataset_ref}.{table_name}"
        client.delete_table(table_id, not_found_ok=True)
        table = bigquery.Table(table_id, schema=schema)
        client.create_table(table)
        print(f"Created/reset table: {table_id} ({len(schema)} columns)")

    seed_targets = REFERENCE_TABLES & set(table_names)
    if seed_targets:
        print("\nSeeding reference/lookup tables...")
        if "piid_issuing_office" in seed_targets:
            office_rows = [
                {"issuing_office_code": code, "service_owner": v["service_owner"]}
                for code, v in ISSUING_OFFICE_LOOKUP.items()
            ]
            errors = client.insert_rows_json(f"{dataset_ref}.piid_issuing_office", office_rows)
            if errors:
                print(f"  [WARN] piid_issuing_office seed errors: {errors}")
            else:
                print(f"  Seeded piid_issuing_office ({len(office_rows)} rows)")

        if "piid_issuing_type" in seed_targets:
            type_rows = [{"type_code": code, "type": t} for code, t in INSTRUMENT_TYPE_LOOKUP.items()]
            errors = client.insert_rows_json(f"{dataset_ref}.piid_issuing_type", type_rows)
            if errors:
                print(f"  [WARN] piid_issuing_type seed errors: {errors}")
            else:
                print(f"  Seeded piid_issuing_type ({len(type_rows)} rows)")

        if "piid_service_code" in seed_targets:
            service_rows = [{"service_code": code, "service": s} for code, s in SERVICE_CODE_LOOKUP.items()]
            errors = client.insert_rows_json(f"{dataset_ref}.piid_service_code", service_rows)
            if errors:
                print(f"  [WARN] piid_service_code seed errors: {errors}")
            else:
                print(f"  Seeded piid_service_code ({len(service_rows)} rows)")

    print("\nSchema setup complete.")


if __name__ == "__main__":
    requested = sys.argv[1:] or list(TABLES.keys())
    unknown = [t for t in requested if t not in TABLES]
    if unknown:
        print(f"Unknown table(s): {unknown}. Known tables: {list(TABLES.keys())}")
        sys.exit(1)

    confirm = input(
        f"This will DROP AND RECREATE: {', '.join(requested)} "
        "(all data in these tables will be lost). Type 'yes' to continue: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted - no changes made.")
        sys.exit(1)
    main(requested)
