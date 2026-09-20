"""
piid_lookup.py
Pulls the issuing-office and instrument-type lookup tables LIVE from
BigQuery (piid_issuing_office / piid_issuing_type) so a PIID decode always
reflects whatever is currently in those tables. piid_reference.py is only
the seed data bigquery_setup.py loads into them once; after that, editing a
row directly in BigQuery is picked up by the next run with no code change.
"""

import os

from google.cloud import bigquery

from config import GCP_PROJECT_ID, CREDENTIALS_PATH, DATASET_ID


def _client() -> bigquery.Client:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
    return bigquery.Client(project=GCP_PROJECT_ID)


def load_issuing_office_lookup(client: bigquery.Client = None) -> dict:
    """issuing_office_code (e.g. "W9126G") -> service_owner (e.g. "Army")."""
    client = client or _client()
    query = f"""
        SELECT issuing_office_code, service_owner
        FROM `{GCP_PROJECT_ID}.{DATASET_ID}.piid_issuing_office`
    """
    return {row.issuing_office_code: row.service_owner for row in client.query(query).result()}


def load_instrument_type_lookup(client: bigquery.Client = None) -> dict:
    client = client or _client()
    query = f"""
        SELECT type_code, type
        FROM `{GCP_PROJECT_ID}.{DATASET_ID}.piid_issuing_type`
    """
    return {row.type_code: row.type for row in client.query(query).result()}


def load_service_code_lookup(client: bigquery.Client = None) -> dict:
    client = client or _client()
    query = f"""
        SELECT service_code, service
        FROM `{GCP_PROJECT_ID}.{DATASET_ID}.piid_service_code`
    """
    return {row.service_code: row.service for row in client.query(query).result()}
