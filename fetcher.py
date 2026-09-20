"""
fetcher.py
Purpose: Handle all communication with USASpending.gov API
- Make POST requests to API endpoint
- Handle pagination (API caps results at 100 per page)
- Handle errors/timeouts with retries
- Return raw JSON results as a list of dicts
"""

import time
from datetime import date, timedelta

import requests

# Fields returned for every award. See DELIVERABLES note: these are the
# award-level field names the API validates against - not free-form.
FIELDS = [
    "Award ID", "Recipient Name", "Award Amount", "Total Outlays",
    "Description", "Awarding Agency", "Awarding Sub Agency",
    "Funding Agency", "Funding Sub Agency", "Start Date", "End Date",
    "Base Obligation Date", "Contract Award Type", "Recipient Location",
    "Primary Place of Performance", "NAICS", "PSC", "Last Modified Date",
    "Issued Date",
]

# PSC families pulled for the historical construction data set:
# (letter, psc_path, category label)
PSC_FAMILIES = [
    ("Y", ["Product", "Y"], "New Construction"),
    ("Z", ["Product", "Z"], "Maintenance, Repair & Alteration"),
    ("C", ["Service", "C"], "Architect & Engineer (A&E) Services"),
]

# Contract award type codes: A/B/C/D cover BPA calls, purchase orders,
# delivery orders, and definitive contracts (i.e. "contracts", not grants/loans).
CONTRACT_AWARD_TYPE_CODES = ["A", "B", "C", "D"]


class USASpendingFetcher:
    def __init__(self, timeout: int = 30, page_limit: int = 100):
        self.api_url = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
        self.timeout = timeout
        self.page_limit = page_limit  # USASpending caps this endpoint at 100/page

    def _post_with_retry(self, payload: dict, max_retries: int = 3) -> dict:
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(self.api_url, json=payload, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                if attempt == max_retries:
                    raise
                wait = 5 * attempt
                print(f"    retry {attempt}/{max_retries} in {wait}s: {e}")
                time.sleep(wait)

    def _paginate(self, filters: dict, max_pages: int = None) -> list:
        """Fetch every page for the given filters and return combined results."""
        all_results = []
        page = 1
        while True:
            payload = {
                "filters": filters,
                "fields": FIELDS,
                "page": page,
                "limit": self.page_limit,
                "sort": "Award Amount",
                "order": "desc",
            }
            data = self._post_with_retry(payload)
            batch = data.get("results", [])
            all_results.extend(batch)

            has_next = data.get("page_metadata", {}).get("hasNext", False)
            if not has_next or (max_pages and page >= max_pages):
                break
            page += 1
            time.sleep(0.3)  # be polite to the API

        return all_results

    def fetch_by_psc_family(self, start_date: str, end_date: str, psc_path: list, max_pages: int = None) -> list:
        """
        Fetch DoD contract awards restricted to one PSC family (see PSC_FAMILIES).
        Note: spending_by_award only returns prime awards; subawards live on a
        separate endpoint, so no extra "prime only" filter is needed.

        Caution: a single call to this is capped at ~10,000 rows (see
        fetch_by_psc_family_chunked) - use that for a complete pull.
        """
        filters = {
            "agencies": [{"type": "awarding", "tier": "toptier", "name": "Department of Defense"}],
            "award_type_codes": CONTRACT_AWARD_TYPE_CODES,
            "time_period": [{"start_date": start_date, "end_date": end_date}],
            "psc_codes": {"require": [psc_path]},
        }
        return self._paginate(filters, max_pages=max_pages)

    def fetch_by_psc_family_chunked(self, start_date: str, end_date: str, psc_path: list,
                                      _depth: int = 0, _max_depth: int = 10) -> list:
        """
        Same as fetch_by_psc_family, but works around USASpending's hard
        ~10,000-row pagination ceiling per query (page * limit <= 10,000;
        it silently stops returning new rows past that point, sorted by
        Award Amount desc, meaning the low-dollar tail gets dropped).

        If a date range comes back saturated (== the ceiling), it is split
        in half and each half is fetched recursively, so the amount of
        data returned per single API call always stays under the ceiling.
        """
        results = self.fetch_by_psc_family(start_date, end_date, psc_path)

        ceiling = self.page_limit * 100  # USASpending's observed max_result_window
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        if len(results) < ceiling or _depth >= _max_depth or (end - start).days < 2:
            return results

        mid = start + (end - start) / 2
        first_half = self.fetch_by_psc_family_chunked(
            start_date, mid.isoformat(), psc_path, _depth + 1, _max_depth)
        second_half = self.fetch_by_psc_family_chunked(
            (mid + timedelta(days=1)).isoformat(), end_date, psc_path, _depth + 1, _max_depth)
        return first_half + second_half
