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


# ---------------------------------------------------------------------------
# DD1391 MILCON ingestion + award-matching pipeline (see CRITERIA.md)
# ---------------------------------------------------------------------------

# Cached USASpending award data - DoD construction only (Y-series PSC /
# NAICS 236220-family, Housing/Barracks/BEQ/CDC descriptions). Populated
# ONLY by awards_sync.py (monthly, or run by hand); all matching logic in
# award_matcher.py reads this table and never calls the live API.
# One row per award_id: if USASpending reports multiple funding actions
# against the same award, they are summed into award_amount and the
# earliest is kept as award_date - see awards_sync.py's _collapse_award().
AWARDS_SCHEMA = [
    bigquery.SchemaField("award_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("piid", "STRING"),
    bigquery.SchemaField("parent_idiq_piid", "STRING"),
    bigquery.SchemaField("uic", "STRING"),
    bigquery.SchemaField("service_branch", "STRING"),  # Navy | Army | Air Force | Marines
    bigquery.SchemaField("funding_agency", "STRING"),
    bigquery.SchemaField("awarding_office", "STRING"),
    bigquery.SchemaField("awardee", "STRING"),
    bigquery.SchemaField("awardee_uei", "STRING"),
    bigquery.SchemaField("award_amount", "FLOAT64"),  # summed total, not incremental lines
    bigquery.SchemaField("award_date", "STRING"),  # earliest funding action date
    bigquery.SchemaField("period_of_performance_start", "STRING"),
    bigquery.SchemaField("period_of_performance_end", "STRING"),
    bigquery.SchemaField("pop_city", "STRING"),
    bigquery.SchemaField("pop_state", "STRING"),
    bigquery.SchemaField("pop_zip", "STRING"),  # used for base proximity matching, see award_matcher.py
    bigquery.SchemaField("pop_country", "STRING"),
    bigquery.SchemaField("naics_code", "STRING"),
    bigquery.SchemaField("psc_code", "STRING"),
    bigquery.SchemaField("description", "STRING"),
    bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
]

# Table 1 (Phase 1) - one row per MILCON project parsed out of a DD1391.
# See dd1391_parser.py. confirmed_award_id/confirmed_piid/
# confirmed_solicitation_id/match_status are left blank at parse time and
# only populated after a human approves a match in award_candidates.
DD1391_PROJECT_OVERVIEW_SCHEMA = [
    bigquery.SchemaField("project_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("component", "STRING"),
    bigquery.SchemaField("fy_year", "STRING"),
    bigquery.SchemaField("preparation_date", "STRING"),
    bigquery.SchemaField("uic", "STRING"),
    bigquery.SchemaField("base_name", "STRING"),
    bigquery.SchemaField("city", "STRING"),
    bigquery.SchemaField("state", "STRING"),
    bigquery.SchemaField("country", "STRING"),
    bigquery.SchemaField("typology", "STRING"),  # BEQ | CDC
    bigquery.SchemaField("program_element", "STRING"),
    bigquery.SchemaField("category_code", "STRING"),
    bigquery.SchemaField("project_cost_thousands", "FLOAT64"),
    bigquery.SchemaField("description_construction", "STRING"),  # full Section 10
    bigquery.SchemaField("project_description", "STRING"),  # Section 11
    bigquery.SchemaField("unit_type", "STRING"),
    bigquery.SchemaField("personnel", "STRING"),
    bigquery.SchemaField("number_of_units", "INTEGER"),
    bigquery.SchemaField("number_of_buildings", "INTEGER"),
    bigquery.SchemaField("source_milcon_book_fy", "STRING"),
    bigquery.SchemaField("source_file", "STRING"),
    bigquery.SchemaField("source_pages", "STRING"),
    bigquery.SchemaField("extraction_method", "STRING"),  # text | vision
    bigquery.SchemaField("extraction_confidence", "STRING"),
    bigquery.SchemaField("import_notes", "STRING"),
    # Populated after manual approval of an award match (Phase 4 step 4):
    bigquery.SchemaField("confirmed_award_id", "STRING"),
    bigquery.SchemaField("confirmed_piid", "STRING"),
    bigquery.SchemaField("confirmed_solicitation_id", "STRING"),
    # For match_status='non_standard_authority' (e.g. a Defense Innovation
    # Unit OTA): these awards have no PIID, so whatever identifier does
    # exist (a notice ID, OTA agreement number, etc) goes here instead of
    # being forced into confirmed_piid, which is the wrong shape for it.
    bigquery.SchemaField("notice_id", "STRING"),
    bigquery.SchemaField("match_status", "STRING"),  # see MATCH_STATUS_VALUES below
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("write_timestamp", "TIMESTAMP"),  # set on Phase 4 approval write
]

MATCH_STATUS_VALUES = [
    "unmatched", "pending_review", "matched", "no_award_found",
    "project_not_awarded", "non_standard_authority",
]

# Table 2 (Phase 1) - Primary/Supporting Facilities + Project Summary cost
# breakdown rows, many per project_id. `cost` is always the real dollar
# amount (DD1391 cost tables are printed in $000 - dd1391_parser.py
# multiplies the printed figure by 1000 before storing it here). `value`
# holds the SF quantity or the SIOH/Contingency percentage (plain number,
# string-typed); `unit` says which: SF | PCT | "" (blank for dollar-only
# summary rows). `code` is a FK into estimate_crx.code (101-131 Primary,
# 201-213 Supporting), assigned by Claude during extraction - NULL for
# Project Summary rows (Subtotal/Contingency/SIOH/Total), which aren't
# individual facility items. Section 12 (Supplemental Data) lives in its
# own table, dd1391_supplemental_data - not mixed in here.
DD1391_COST_ROWS_SCHEMA = [
    bigquery.SchemaField("project_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("section", "STRING"),  # Primary Facilities | Supporting Facilities | Project Summary
    bigquery.SchemaField("code", "INTEGER"),  # FK -> estimate_crx.code
    bigquery.SchemaField("subsection", "STRING"),
    bigquery.SchemaField("value", "STRING"),
    bigquery.SchemaField("unit", "STRING"),  # SF | PCT | ""
    bigquery.SchemaField("cost", "FLOAT64"),
]

# Reference table for the `code` column above - seeded from
# temp_tables.xlsx's 'estiamte_crx' sheet by load_base_tables.py.
ESTIMATE_CRX_SCHEMA = [
    bigquery.SchemaField("code", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("section", "STRING"),
    bigquery.SchemaField("item", "STRING"),
]

# Section 12 (Supplemental Data), one row per block-12 item found on the
# form. Denormalized against suppl_crx at write time (dd1391_parser.py) so
# the descriptive columns (subsection_code/subsection/desc_code/item) are
# readable directly in this table without a join. `unit` documents what
# kind of value it is (date | pct | $ | text) and is placed before `value`.
DD1391_SUPPLEMENTAL_DATA_SCHEMA = [
    bigquery.SchemaField("project_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("code", "INTEGER", mode="REQUIRED"),  # FK -> suppl_crx.code (901-918)
    bigquery.SchemaField("subsection_code", "INTEGER"),
    bigquery.SchemaField("subsection", "STRING"),  # e.g. "Status", "Basis", "Total Cost"
    bigquery.SchemaField("desc_code", "STRING"),  # A-H, blank for single-value items
    bigquery.SchemaField("item", "STRING"),
    bigquery.SchemaField("unit", "STRING"),  # date | pct | $ | text
    bigquery.SchemaField("value", "STRING"),
]

# Reference table for dd1391_supplemental_data.code - seeded from
# temp_tables.xlsx's 'suppl_crx' sheet by load_base_tables.py.
SUPPL_CRX_SCHEMA = [
    bigquery.SchemaField("code", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("code_matrix", "STRING"),  # e.g. "12.1.A"
    bigquery.SchemaField("section", "STRING"),  # always "Supplemental Data"
    bigquery.SchemaField("subsection_code", "INTEGER"),
    bigquery.SchemaField("subsection", "STRING"),  # e.g. "Status", "Basis", "Total Cost"
    bigquery.SchemaField("desc_code", "STRING"),  # A-H, blank for single-value items
    bigquery.SchemaField("item", "STRING"),
]

# Table 3 (Phase 2) - ranked award candidates per project, written by
# award_matcher.py. status is set by the human reviewer in Phase 4.
AWARD_CANDIDATES_SCHEMA = [
    bigquery.SchemaField("project_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("rank", "INTEGER"),
    bigquery.SchemaField("piid", "STRING"),
    bigquery.SchemaField("solicitation_id", "STRING"),
    bigquery.SchemaField("award_id", "STRING"),
    bigquery.SchemaField("confidence_score", "FLOAT64"),
    bigquery.SchemaField("match_reasoning", "STRING"),
    bigquery.SchemaField("uic", "STRING"),
    bigquery.SchemaField("status", "STRING"),  # pending | approved | rejected
    bigquery.SchemaField("ranked_by", "STRING"),  # rule | haiku | sonnet
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
]

# Base/location reference tables, seeded from temp_tables.xlsx (see
# load_base_tables.py) - dim_base_location.base_id/base_name/location
# fields come straight from that file; `uic` is NOT in the source file
# (DoD has no downloadable UIC master list) and is instead backfilled
# incrementally as UICs are read off DD1391s during Review and confirmed
# via award matching.
DIM_BASE_LOCATION_SCHEMA = [
    bigquery.SchemaField("base_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("base_name", "STRING"),
    bigquery.SchemaField("conus_oconus", "STRING"),
    bigquery.SchemaField("country", "STRING"),
    bigquery.SchemaField("state", "STRING"),
    bigquery.SchemaField("county", "STRING"),
    bigquery.SchemaField("city", "STRING"),
    bigquery.SchemaField("zip", "STRING"),
    bigquery.SchemaField("gisjoin", "STRING"),
    bigquery.SchemaField("latitude", "FLOAT64"),
    bigquery.SchemaField("longitude", "FLOAT64"),
    bigquery.SchemaField("uic", "STRING"),
]

DIM_LOCATION_CROSSWALK_SCHEMA = [
    bigquery.SchemaField("state_abbrv", "STRING"),
    bigquery.SchemaField("state", "STRING"),
    bigquery.SchemaField("region_1", "STRING"),
    bigquery.SchemaField("region_2", "STRING"),
    bigquery.SchemaField("navfac_command", "STRING"),
    bigquery.SchemaField("navfac_subcommand", "STRING"),
    bigquery.SchemaField("type", "STRING"),
]

TABLES = {
    "project_preaward": NEW_OPPORTUNITIES_SCHEMA,
    "projects_awarded": HISTORICAL_PROJECTS_SCHEMA,
    "run_log": RUN_LOG_SCHEMA,
    "piid_issuing_office": PIID_ISSUING_OFFICE_SCHEMA,
    "piid_issuing_type": PIID_ISSUING_TYPE_SCHEMA,
    "piid_service_code": PIID_SERVICE_CODE_SCHEMA,
    "awards": AWARDS_SCHEMA,
    "dd1391_project_overview": DD1391_PROJECT_OVERVIEW_SCHEMA,
    "dd1391_cost_rows": DD1391_COST_ROWS_SCHEMA,
    "award_candidates": AWARD_CANDIDATES_SCHEMA,
    "dim_base_location": DIM_BASE_LOCATION_SCHEMA,
    "dim_location_crosswalk": DIM_LOCATION_CROSSWALK_SCHEMA,
    "estimate_crx": ESTIMATE_CRX_SCHEMA,
    "dd1391_supplemental_data": DD1391_SUPPLEMENTAL_DATA_SCHEMA,
    "suppl_crx": SUPPL_CRX_SCHEMA,
}
