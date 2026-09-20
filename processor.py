"""
processor.py
Purpose: Take raw API results and prepare them for CSV/BigQuery export
- Flatten nested fields (location, NAICS, PSC objects)
- Convert to a pandas DataFrame
- Both project_preaward and projects_awarded use the same criteria (PSC
  families, instrument types) - see CRITERIA.md - so they represent the
  same population of projects at different points in time.
"""

from datetime import datetime, timedelta

import pandas as pd

from fetcher import USASpendingFetcher, PSC_FAMILIES
from piid_decoder import decode_piid
from piid_lookup import load_issuing_office_lookup, load_instrument_type_lookup, load_service_code_lookup
from project_classifier import extract_project_id, classify_project_type

# PIID position-9 instrument type codes kept in both tables: B (invitation
# for bids), C (contract other than an IDC), D (indefinite-delivery
# contract), R (request for proposals). See CRITERIA.md.
INSTRUMENT_TYPE_CODES = {"B", "C", "D", "R"}


def _flatten_award(raw: dict) -> dict:
    """Flatten the nested location/NAICS/PSC objects into plain columns."""
    recipient_loc = raw.get("Recipient Location") or {}
    pop = raw.get("Primary Place of Performance") or {}
    naics = raw.get("NAICS") or {}
    psc = raw.get("PSC") or {}

    return {
        "award_id": raw.get("Award ID"),
        "recipient_name": raw.get("Recipient Name"),
        "award_amount": raw.get("Award Amount"),
        "total_outlays": raw.get("Total Outlays"),
        "description": raw.get("Description"),
        "awarding_agency": raw.get("Awarding Agency"),
        "awarding_sub_agency": raw.get("Awarding Sub Agency"),
        "funding_agency": raw.get("Funding Agency"),
        "funding_sub_agency": raw.get("Funding Sub Agency"),
        "start_date": raw.get("Start Date"),
        "end_date": raw.get("End Date"),
        "base_obligation_date": raw.get("Base Obligation Date"),
        "contract_award_type": raw.get("Contract Award Type"),
        "recipient_state": recipient_loc.get("state_code"),
        "recipient_city": recipient_loc.get("city_name"),
        "pop_state": pop.get("state_code"),
        "pop_city": pop.get("city_name"),
        "pop_county": pop.get("county_name"),
        "naics_code": naics.get("code"),
        "naics_description": naics.get("description"),
        "psc_code": psc.get("code"),
        "psc_description": psc.get("description"),
        "last_modified_date": raw.get("Last Modified Date"),
        "issued_date": raw.get("Issued Date"),
    }


def _add_piid_and_project_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Adds project_id, project_type, and PIID-decoded columns (service,
    issuing_office_code, type_code, type). Applied to every award, in both
    project_preaward and projects_awarded. award_id itself is left
    untouched - the decode is joined on, not derived from renaming/replacing it."""
    if df.empty:
        return df

    df["project_id"] = df["description"].apply(extract_project_id)
    df["project_type"] = df["description"].apply(classify_project_type)

    # Pulled live from the piid_issuing_office / piid_issuing_type /
    # piid_service_code BigQuery tables (see piid_lookup.py) - editing a row
    # there is picked up on the next run with no code change needed.
    office_lookup = load_issuing_office_lookup()
    type_lookup = load_instrument_type_lookup()
    service_code_lookup = load_service_code_lookup()

    decoded = pd.DataFrame(
        df["award_id"].apply(lambda piid: decode_piid(piid, office_lookup, type_lookup, service_code_lookup)).tolist(),
        index=df.index,
    )
    return pd.concat([df, decoded], axis=1)


def _fetch_psc_families(fetcher: USASpendingFetcher, start_date: str, end_date: str, max_pages: int = None) -> pd.DataFrame:
    """
    Shared pull used by both project_preaward and projects_awarded: fetch
    every PSC_FAMILIES family for the given date range, tag psc_category,
    dedupe, decode PIIDs, and filter to INSTRUMENT_TYPE_CODES. See CRITERIA.md.
    """
    frames = []
    for letter, psc_path, category in PSC_FAMILIES:
        print(f"  PSC-{letter} ({category}) ...", end=" ", flush=True)
        if max_pages:
            # Quick-test mode: single capped call, no chunking.
            raw_results = fetcher.fetch_by_psc_family(start_date, end_date, psc_path, max_pages=max_pages)
        else:
            # Full run: split the date range as needed to get past the
            # API's ~10,000-row-per-query pagination ceiling.
            raw_results = fetcher.fetch_by_psc_family_chunked(start_date, end_date, psc_path)
        rows = [_flatten_award(r) for r in raw_results]
        for row in rows:
            row["psc_category"] = category
        print(f"{len(rows)} rows")
        if rows:
            frames.append(pd.DataFrame(rows))

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset="award_id").reset_index(drop=True)
    df = _add_piid_and_project_columns(df)

    if not df.empty:
        df = df[df["type_code"].isin(INSTRUMENT_TYPE_CODES)].reset_index(drop=True)

    return df


class DataProcessor:

    @staticmethod
    def prepare_new_opportunities(days_back: int = 180, max_pages: int = None) -> pd.DataFrame:
        """
        Fetch DoD awards over the last N days across all three PSC families
        (Y/Z/C) - same criteria as prepare_historical_projects, just a
        shorter, more recent window. See CRITERIA.md.
        """
        fetcher = USASpendingFetcher()

        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        print(f"Fetching new opportunities (last {days_back} days): {start_date} to {end_date}")

        df = _fetch_psc_families(fetcher, start_date, end_date, max_pages)

        print(f"  Total: {len(df)} rows across {len(PSC_FAMILIES)} PSC families")
        return df

    @staticmethod
    def prepare_historical_projects(years_back: int = 5, max_pages: int = None) -> pd.DataFrame:
        """
        Fetch DoD awards over the last N years across all three PSC families
        (Y = New Construction, Z = Maintenance/Repair/Alteration,
        C = Architect & Engineer Services). See CRITERIA.md.
        """
        fetcher = USASpendingFetcher()

        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=365 * years_back)).strftime("%Y-%m-%d")

        print(f"Fetching historical projects (last {years_back} years): {start_date} to {end_date}")

        df = _fetch_psc_families(fetcher, start_date, end_date, max_pages)

        print(f"  Total: {len(df)} rows across {len(PSC_FAMILIES)} PSC families")
        return df
