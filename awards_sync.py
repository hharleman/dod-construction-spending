"""
awards_sync.py
The ONLY place in the DD1391 pipeline allowed to call the live USASpending
API. Run monthly (by hand, or from a Cloud Scheduler + Cloud Function later)
to refresh the `awards` BigQuery table. dd1391_parser.py and
award_matcher.py never touch USASpending directly - they only read `awards`.

Scope (see CRITERIA.md "DD1391 pipeline"):
  - Department of Defense awarding agency only
  - Construction: PSC Y-series (New Construction) OR PSC Z-series
    (Maintenance, Repair & Alteration - major BEQ/barracks renovations
    often fall here, not Y) OR NAICS 236220 (Commercial and Institutional
    Building Construction) - three separate API filters, queried
    separately and merged/deduped, since USASpending only ANDs filters
    within one request.
  - Client-side filtered to Housing/Barracks/BEQ/CDC/Open Bay descriptions
    (reuses project_classifier.classify_project_type - same typology
    keywords as the rest of this repo).
  - One row per award_id. USASpending's spending_by_award endpoint already
    returns the award's current total obligated value (not a per-action
    increment), so no separate "funding action" summing is needed - the
    only summing this script does is a safety net in case the Y-series and
    NAICS queries both surface the same award_id with different amounts,
    which should not normally happen but is asserted against.

Known gaps (documented, not silently guessed):
  - `uic`: USASpending has no native DoD UIC field. Left NULL here; the
    matcher instead uses pop_city/pop_state/pop_zip proximity against
    dim_base_location (whose own `uic` column is backfilled incrementally
    from confirmed DD1391 review in app.py) to find the right base.
  - `parent_idiq_piid`: "Parent Award ID" comes back null for most awards in
    this endpoint (only populated for orders placed against an IDV, and even
    then not always). Kept as-is, no secondary lookup call - that would mean
    one API call per award, defeating the point of a cheap batch sync.

Usage:
  python awards_sync.py                          # default window (see START_DATE)
  python awards_sync.py --start-date 2015-01-01 --end-date 2026-01-01
  python awards_sync.py --dry-run                # fetch + print counts, no BigQuery write
"""

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
from google.cloud import bigquery

from config import GCP_PROJECT_ID, CREDENTIALS_PATH, DATASET_ID
from schema import AWARDS_SCHEMA
from project_classifier import classify_project_type

API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
PAGE_LIMIT = 100
PAGE_CEILING = PAGE_LIMIT * 100  # USASpending's observed max_result_window

CONTRACT_AWARD_TYPE_CODES = ["A", "B", "C", "D"]
CONSTRUCTION_NAICS_CODES = ["236220"]

# Housing/Barracks/BEQ/CDC/Open Bay - matches project_classifier's typology
# labels exactly, so this sync and the rest of the repo agree on scope.
TARGET_TYPOLOGIES = {"CDC", "BEQ", "Open Bay", "Barracks", "Housing"}

# Fields pulled from the search endpoint. "Awarding Office" and
# "Parent Award ID" are valid field names (confirmed against the live API)
# even though they come back null for most rows.
FIELDS = [
    "Award ID", "Recipient Name", "Recipient UEI", "Award Amount", "Description",
    "Awarding Agency", "Awarding Sub Agency", "Awarding Office",
    "Funding Agency", "Funding Sub Agency", "Start Date", "End Date",
    "Base Obligation Date", "Contract Award Type", "Primary Place of Performance",
    "NAICS", "PSC", "Parent Award ID",
]

DEFAULT_START_DATE = "2010-01-01"  # MILCON DD1391 books can be a decade+ old


def _service_branch(awarding_sub_agency: str) -> str:
    if not awarding_sub_agency:
        return ""
    sub = awarding_sub_agency.lower()
    if "marine corps" in sub:
        return "Marines"
    if "army" in sub:
        return "Army"
    if "navy" in sub:
        return "Navy"
    if "air force" in sub:
        return "Air Force"
    return "Other"


def _post_with_retry(payload: dict, max_retries: int = 3) -> dict:
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(API_URL, json=payload, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            if attempt == max_retries:
                raise
            wait = 5 * attempt
            print(f"    retry {attempt}/{max_retries} in {wait}s: {e}")
            time.sleep(wait)


def _paginate(filters: dict) -> list:
    all_results = []
    page = 1
    while True:
        payload = {
            "filters": filters,
            "fields": FIELDS,
            "page": page,
            "limit": PAGE_LIMIT,
            "sort": "Award Amount",
            "order": "desc",
        }
        data = _post_with_retry(payload)
        batch = data.get("results", [])
        all_results.extend(batch)
        if not data.get("page_metadata", {}).get("hasNext", False):
            break
        page += 1
        time.sleep(0.3)
    return all_results


def _fetch_chunked(base_filters: dict, start_date: str, end_date: str, _depth: int = 0) -> list:
    """Same ~10,000-row pagination-ceiling workaround as fetcher.py: split
    the date range in half if a chunk comes back saturated."""
    filters = {**base_filters, "time_period": [{"start_date": start_date, "end_date": end_date}]}
    results = _paginate(filters)

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if len(results) < PAGE_CEILING or _depth >= 12 or (end - start).days < 2:
        return results

    mid = start + (end - start) / 2
    first_half = _fetch_chunked(base_filters, start_date, mid.isoformat(), _depth + 1)
    second_half = _fetch_chunked(base_filters, (mid + timedelta(days=1)).isoformat(), end_date, _depth + 1)
    return first_half + second_half


def fetch_construction_awards(start_date: str, end_date: str) -> list:
    dod_filter = {"type": "awarding", "tier": "toptier", "name": "Department of Defense"}
    base = {"agencies": [dod_filter], "award_type_codes": CONTRACT_AWARD_TYPE_CODES}

    print(f"Fetching PSC Y-series (New Construction) awards {start_date} to {end_date}...")
    y_series = _fetch_chunked({**base, "psc_codes": {"require": [["Product", "Y"]]}}, start_date, end_date)
    print(f"  {len(y_series)} rows")

    print(f"Fetching PSC Z-series (Maintenance, Repair & Alteration) awards {start_date} to {end_date}...")
    z_series = _fetch_chunked({**base, "psc_codes": {"require": [["Product", "Z"]]}}, start_date, end_date)
    print(f"  {len(z_series)} rows")

    print(f"Fetching NAICS {CONSTRUCTION_NAICS_CODES} awards {start_date} to {end_date}...")
    naics = _fetch_chunked({**base, "naics_codes": CONSTRUCTION_NAICS_CODES}, start_date, end_date)
    print(f"  {len(naics)} rows")

    return y_series + z_series + naics


def _flatten(raw: dict) -> dict:
    pop = raw.get("Primary Place of Performance") or {}
    naics = raw.get("NAICS") or {}
    psc = raw.get("PSC") or {}
    return {
        "award_id": raw.get("Award ID"),
        "piid": raw.get("Award ID"),  # PIID and Award ID are the same value for contracts
        "parent_idiq_piid": raw.get("Parent Award ID"),
        "uic": None,  # backfilled by award_matcher.py via uic_crosswalk, see module docstring
        "service_branch": _service_branch(raw.get("Awarding Sub Agency")),
        "funding_agency": raw.get("Funding Agency"),
        "awarding_office": raw.get("Awarding Office"),
        "awardee": raw.get("Recipient Name"),
        "awardee_uei": raw.get("Recipient UEI"),
        "award_amount": raw.get("Award Amount"),
        "award_date": raw.get("Base Obligation Date"),
        "period_of_performance_start": raw.get("Start Date"),
        "period_of_performance_end": raw.get("End Date"),
        "pop_city": pop.get("city_name"),
        "pop_state": pop.get("state_code"),
        "pop_zip": pop.get("zip5"),
        "pop_country": pop.get("country_name"),
        "naics_code": naics.get("code"),
        "psc_code": psc.get("code"),
        "description": raw.get("Description"),
    }


def _collapse_and_filter(raw_results: list) -> pd.DataFrame:
    """De-dupe rows shared between the Y-series and NAICS queries (safety
    net: sum if the API ever reports genuinely different amounts for the
    same award_id, keep the earliest award_date), filter to construction
    typologies, and drop the ~10% of rows with no usable description."""
    if not raw_results:
        return pd.DataFrame()

    df = pd.DataFrame([_flatten(r) for r in raw_results])
    df = df.dropna(subset=["award_id"])

    dupe_mask = df.duplicated(subset="award_id", keep=False)
    if dupe_mask.any():
        agg = {c: "first" for c in df.columns if c not in ("award_amount", "award_date")}
        agg["award_amount"] = "sum"
        agg["award_date"] = "min"
        df = df.groupby("award_id", as_index=False).agg(agg)

    df["typology"] = df["description"].apply(classify_project_type)
    df = df[df["typology"].isin(TARGET_TYPOLOGIES)].drop(columns=["typology"]).reset_index(drop=True)

    df["synced_at"] = datetime.now(timezone.utc).isoformat()
    return df


def sync(start_date: str, end_date: str, dry_run: bool = False) -> pd.DataFrame:
    raw_results = fetch_construction_awards(start_date, end_date)
    df = _collapse_and_filter(raw_results)
    print(f"\n{len(df)} DoD construction awards match Housing/Barracks/BEQ/CDC after filtering "
          f"(from {len(raw_results)} raw Y-series+NAICS rows).")

    if dry_run:
        print("(--dry-run: not writing to BigQuery)")
        return df

    if df.empty:
        print("No rows to load - leaving `awards` table untouched.")
        return df

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
    client = bigquery.Client(project=GCP_PROJECT_ID)
    table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.awards"
    job_config = bigquery.LoadJobConfig(schema=AWARDS_SCHEMA, write_disposition="WRITE_TRUNCATE")
    load_job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    load_job.result()
    print(f"Loaded {len(df)} rows into {table_id}")
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sync(args.start_date, args.end_date, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
