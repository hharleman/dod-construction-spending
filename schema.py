"""
schema.py
Purpose: Single source of truth for BigQuery table structure.
Used by bigquery_setup.py (creates/resets tables - run manually) and by
bigquery_loader.py (loads data against these fixed schemas - no autodetect,
so a daily run can never silently change table structure).
"""

from google.cloud import bigquery

# Both award tables share the same flattened shape - see processor.py's
# _flatten_award(). Dates are kept as STRING because the API mixes plain
# dates ("2026-06-18") with datetime strings ("2026-06-18 13:01:01") and
# sometimes nulls; forcing DATE/TIMESTAMP here would break loads.
AWARD_FIELDS = [
    bigquery.SchemaField("award_id", "STRING"),
    bigquery.SchemaField("recipient_name", "STRING"),
    bigquery.SchemaField("award_amount", "FLOAT64"),
    bigquery.SchemaField("total_outlays", "FLOAT64"),
    bigquery.SchemaField("description", "STRING"),
    bigquery.SchemaField("awarding_agency", "STRING"),
    bigquery.SchemaField("awarding_sub_agency", "STRING"),
    bigquery.SchemaField("funding_agency", "STRING"),
    bigquery.SchemaField("funding_sub_agency", "STRING"),
    bigquery.SchemaField("start_date", "STRING"),
    bigquery.SchemaField("end_date", "STRING"),
    bigquery.SchemaField("base_obligation_date", "STRING"),
    bigquery.SchemaField("contract_award_type", "STRING"),
    bigquery.SchemaField("recipient_state", "STRING"),
    bigquery.SchemaField("recipient_city", "STRING"),
    bigquery.SchemaField("pop_state", "STRING"),
    bigquery.SchemaField("pop_city", "STRING"),
    bigquery.SchemaField("pop_county", "STRING"),
    bigquery.SchemaField("naics_code", "STRING"),
    bigquery.SchemaField("naics_description", "STRING"),
    bigquery.SchemaField("psc_code", "STRING"),
    bigquery.SchemaField("psc_description", "STRING"),
    bigquery.SchemaField("last_modified_date", "STRING"),
    bigquery.SchemaField("issued_date", "STRING"),
]

# PIID decode (see piid_decoder.py, backed by piid_issuing_office /
# piid_issuing_type) - which service issued it and its instrument type.
# Applied to every award in both tables. issuing_office_code itself is used
# internally to resolve "service" but not kept as an output column.
PIID_DECODE_FIELDS = [
    bigquery.SchemaField("service", "STRING"),
    bigquery.SchemaField("type_code", "STRING"),
    bigquery.SchemaField("type", "STRING"),
]

# Best-effort P-number / building-type tag pulled from the description (see
# project_classifier.py). Applied to both tables.
PROJECT_CLASSIFICATION_FIELDS = [
    bigquery.SchemaField("project_id", "STRING"),
    bigquery.SchemaField("project_type", "STRING"),
]

NEW_OPPORTUNITIES_SCHEMA = AWARD_FIELDS + PROJECT_CLASSIFICATION_FIELDS + PIID_DECODE_FIELDS

# projects_awarded additionally tags which PSC family (Y/Z/C) each row came from.
HISTORICAL_PROJECTS_SCHEMA = (
    AWARD_FIELDS
    + [bigquery.SchemaField("psc_category", "STRING")]
    + PROJECT_CLASSIFICATION_FIELDS
    + PIID_DECODE_FIELDS
)

# Lookup tables mirroring piid_reference.py, so the decode can also be
# audited/joined directly in BigQuery. Just the code -> service owner - no
# command/office-name detail, since nothing downstream displays it.
PIID_ISSUING_OFFICE_SCHEMA = [
    bigquery.SchemaField("issuing_office_code", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("service_owner", "STRING"),
]

PIID_ISSUING_TYPE_SCHEMA = [
    bigquery.SchemaField("type_code", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("type", "STRING"),
]

# Fallback service classification by PIID prefix, used when
# issuing_office_code isn't in piid_issuing_office. See piid_decoder.py.
PIID_SERVICE_CODE_SCHEMA = [
    bigquery.SchemaField("service_code", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("service", "STRING"),
]

# One row per pipeline stage per run - see logger.py. file_name identifies
# which script ran (historical_run / new_opp_run); stage is one of:
# run_start, api_start, api_end, load_start, load_end, run_end.
RUN_LOG_SCHEMA = [
    bigquery.SchemaField("file_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("stage", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("row_count", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("error_message", "STRING"),
    bigquery.SchemaField("logged_at", "TIMESTAMP", mode="REQUIRED"),
]

TABLES = {
    "project_preaward": NEW_OPPORTUNITIES_SCHEMA,
    "projects_awarded": HISTORICAL_PROJECTS_SCHEMA,
    "run_log": RUN_LOG_SCHEMA,
    "piid_issuing_office": PIID_ISSUING_OFFICE_SCHEMA,
    "piid_issuing_type": PIID_ISSUING_TYPE_SCHEMA,
    "piid_service_code": PIID_SERVICE_CODE_SCHEMA,
}
