"""
DoD Construction Spending - USASpending.gov
Usage: python dod_construction_spending.py
"""

import requests
import csv
import time
from datetime import date, datetime

API_URL   = "https://api.usaspending.gov/api/v2/search/spending_by_transaction/"
TODAY     = date.today().isoformat()
PULLED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

PSC_FAMILIES = [
    ("Y", ["Product", "Y"], "New Construction"),
    ("Z", ["Product", "Z"], "Maintenance, Repair & Alteration"),
    ("C", ["Service",  "C"], "Architect & Engineer (A&E) Services"),
]

API_FIELDS = [
    "Award ID", "Mod", "Recipient Name", "Recipient UEI",
    "Awarding Agency", "Awarding Sub Agency", "Funding Agency", "Funding Sub Agency",
    "pop_state_code", "pop_country_name", "Award Type", "Action Type",
    "Transaction Description", "product_or_service_code", "product_or_service_description",
    "naics_code", "naics_description", "Action Date", "Transaction Amount",
]

OUTPUT_COLS = [
    "fiscal_year", "award_id_piid", "modification_number", "action_type",
    "awarding_agency_name", "awarding_sub_agency_name", "funding_agency_name", "funding_sub_agency_name",
    "recipient_uei", "recipient_name", "place_of_performance_state", "place_of_performance_country",
    "award_type", "transaction_description", "psc_category", "psc_code", "psc_description",
    "naics_code", "naics_description", "action_date", "transaction_amount", "last_updated_date",
]


def fy_dates(fy):
    start = f"{fy - 1}-10-01"
    end   = f"{fy}-09-30"
    return start, min(end, TODAY)


def to_row(r, psc_category):
    d = r.get("Action Date") or ""
    fy = ""
    if len(d) >= 7:
        yr, mo = int(d[:4]), int(d[5:7])
        fy = str(yr + 1) if mo >= 10 else str(yr)
    return {
        "fiscal_year":                  fy,
        "award_id_piid":                r.get("Award ID"),
        "modification_number":          r.get("Mod"),
        "action_type":                  r.get("Action Type"),
        "awarding_agency_name":         r.get("Awarding Agency"),
        "awarding_sub_agency_name":     r.get("Awarding Sub Agency"),
        "funding_agency_name":          r.get("Funding Agency"),
        "funding_sub_agency_name":      r.get("Funding Sub Agency"),
        "recipient_uei":                r.get("Recipient UEI"),
        "recipient_name":               r.get("Recipient Name"),
        "place_of_performance_state":   r.get("pop_state_code"),
        "place_of_performance_country": r.get("pop_country_name"),
        "award_type":                   r.get("Award Type"),
        "transaction_description":      r.get("Transaction Description") or "",
        "psc_category":                 psc_category,
        "psc_code":                     (r.get("product_or_service_code") or "").upper().strip(),
        "psc_description":              r.get("product_or_service_description"),
        "naics_code":                   str(r.get("naics_code") or "").strip(),
        "naics_description":            r.get("naics_description"),
        "action_date":                  d,
        "transaction_amount":           r.get("Transaction Amount"),
        "last_updated_date":            PULLED_AT,
    }


def fetch(fy, psc_path, page):
    start, end = fy_dates(fy)
    payload = {
        "filters": {
            "agencies":         [{"type": "awarding", "tier": "toptier", "name": "Department of Defense"}],
            "time_period":      [{"start_date": start, "end_date": end}],
            "award_type_codes": ["A", "B", "C", "D"],
            "psc_codes":        {"require": [psc_path]},
        },
        "fields": API_FIELDS,
        "page": page, "limit": 100, "sort": "Action Date", "order": "desc",
    }
    for attempt in range(1, 6):
        try:
            r = requests.post(API_URL, json=payload, timeout=120)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 5:
                raise
            wait = 15 * attempt
            print(f"  retry {attempt}/5 in {wait}s: {e}")
            time.sleep(wait)


def pull_psc(fy, letter, psc_path, category):
    rows, page = [], 1
    print(f"  Fetching PSC-{letter} ...", end=" ", flush=True)
    while True:
        data  = fetch(fy, psc_path, page)
        batch = data.get("results", [])
        rows.extend(to_row(r, category) for r in batch)
        if len(batch) < 100:
            break
        page += 1
        time.sleep(0.5)
    print(f"{len(rows):,} rows ({page} pages)")

    filename = f"dod_spending_{letter}_{fy}_{TODAY}.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=OUTPUT_COLS).writeheader()
        csv.DictWriter(f, fieldnames=OUTPUT_COLS).writerows(rows)
    print(f"  Saved: {filename}")
    return len(rows)


def main():
    fy = input("Enter fiscal year (e.g. 2024): ").strip()
    if not fy.isdigit():
        print("Invalid year.")
        return
    fy = int(fy)
    start, end = fy_dates(fy)

    print(f"\nFY{fy}  |  {start} to {end}  |  Agency: Department of Defense\n")

    total = 0
    for i, (letter, psc_path, category) in enumerate(PSC_FAMILIES):
        if i > 0:
            time.sleep(5)
        total += pull_psc(fy, letter, psc_path, category)

    print(f"\nDone. {total:,} total rows across 3 files.")


if __name__ == "__main__":
    main()
