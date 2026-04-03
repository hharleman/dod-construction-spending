"""
DoD Construction Spending - USASpending.gov API
Fetches contracts with PSC code "Y" (Construction of Structures and Facilities)
for the Department of Defense, FY2020 onward.

PSC "Y" Code Reference (DoD Construction):
  Y1** = Buildings & Structures
  Y2** = Infrastructure & Utilities
"""

import requests
import csv
import time
import os

API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
OUTPUT_FILE = "dod_construction_spending.csv"

# ------------------------------------------------------------------
# PSC Sub-Code Reference Map
# Format: { "PSC_prefix": ("Facility Category", "Facility Type") }
# ------------------------------------------------------------------
PSC_SUBCODES = {
    # --- Buildings & Structures (Y1**) ---
    "Y1AA": ("Buildings",      "Barracks / Dormitories"),
    "Y1AB": ("Buildings",      "Barracks / Dormitories - Repair/Alteration"),
    "Y1BA": ("Buildings",      "Child Development Center (CDC)"),
    "Y1BB": ("Buildings",      "Child Development Center - Repair/Alteration"),
    "Y1CA": ("Buildings",      "Chapel / Religious Facility"),
    "Y1CB": ("Buildings",      "Chapel - Repair/Alteration"),
    "Y1DA": ("Buildings",      "Hospital / Medical Treatment Facility"),
    "Y1DB": ("Buildings",      "Hospital - Repair/Alteration"),
    "Y1EA": ("Buildings",      "Administrative / Office Building"),
    "Y1EB": ("Buildings",      "Administrative Building - Repair/Alteration"),
    "Y1FA": ("Buildings",      "Industrial / Maintenance / Production Facility"),
    "Y1FB": ("Buildings",      "Industrial Facility - Repair/Alteration"),
    "Y1GA": ("Buildings",      "Recreational Facility (Gym, Pool, Club)"),
    "Y1GB": ("Buildings",      "Recreational Facility - Repair/Alteration"),
    "Y1HA": ("Buildings",      "Research & Development Facility"),
    "Y1HB": ("Buildings",      "R&D Facility - Repair/Alteration"),
    "Y1JA": ("Buildings",      "School / Training Facility"),
    "Y1JB": ("Buildings",      "School / Training Facility - Repair/Alteration"),
    "Y1KA": ("Buildings",      "Warehouse / Storage Facility"),
    "Y1KB": ("Buildings",      "Warehouse - Repair/Alteration"),
    "Y1LA": ("Buildings",      "Family Housing"),
    "Y1LB": ("Buildings",      "Family Housing - Repair/Alteration"),
    "Y1MA": ("Buildings",      "Missile / Space / Command Facility"),
    "Y1MB": ("Buildings",      "Missile / Space Facility - Repair/Alteration"),
    "Y1NA": ("Buildings",      "Other Building / Structure"),
    "Y1NB": ("Buildings",      "Other Building - Repair/Alteration"),
    "Y1ZA": ("Buildings",      "Miscellaneous Buildings"),
    "Y1ZZ": ("Buildings",      "Other / Unclassified Building"),
    # --- Infrastructure & Utilities (Y2**) ---
    "Y2AA": ("Infrastructure", "Airfield / Air Navigation Facility"),
    "Y2AB": ("Infrastructure", "Airfield - Repair/Alteration"),
    "Y2BA": ("Infrastructure", "Dam / Waterway / Levee"),
    "Y2BB": ("Infrastructure", "Dam / Waterway - Repair/Alteration"),
    "Y2CA": ("Infrastructure", "Highway / Road / Bridge / Railroad"),
    "Y2CB": ("Infrastructure", "Road / Bridge - Repair/Alteration"),
    "Y2DA": ("Infrastructure", "Electrical Utility System"),
    "Y2DB": ("Infrastructure", "Electrical Utility - Repair/Alteration"),
    "Y2EA": ("Infrastructure", "Fuel / Gas Distribution System"),
    "Y2EB": ("Infrastructure", "Fuel / Gas System - Repair/Alteration"),
    "Y2FA": ("Infrastructure", "Water Supply / Treatment System"),
    "Y2FB": ("Infrastructure", "Water System - Repair/Alteration"),
    "Y2GA": ("Infrastructure", "Sewage / Waste Treatment System"),
    "Y2GB": ("Infrastructure", "Sewage System - Repair/Alteration"),
    "Y2HA": ("Infrastructure", "Heating / Cooling Plant"),
    "Y2HB": ("Infrastructure", "Heating / Cooling - Repair/Alteration"),
    "Y2ZA": ("Infrastructure", "Miscellaneous Infrastructure"),
    "Y2ZZ": ("Infrastructure", "Other / Unclassified Infrastructure"),
}

# Keywords in description that suggest renovation vs new construction
RENOVATION_KEYWORDS = [
    "renovate", "renovation", "repair", "restore", "restoration",
    "alteration", "alter", "upgrade", "retrofit", "rehab",
    "replacement", "replace", "modernize", "modernization",
    "refurbish", "improvement", "improve",
]

FIELDS = [
    "Award ID",
    "Mod",
    "Recipient Name",
    "recipient_doing_business_as_name",
    "recipient_uei",
    "cage_code",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Funding Agency",
    "Funding Sub Agency",
    "funding_office_code",
    "funding_office_name",
    "Place of Performance State Code",
    "Place of Performance State Name",
    "Place of Performance Zip Code",
    "Place of Performance Country Code",
    "recipient_address_line_1",
    "recipient_address_line_2",
    "recipient_city_name",
    "recipient_state_code",
    "recipient_state_name",
    "recipient_country_name",
    "NAICS Code",
    "NAICS Description",
    "PSC Code",
    "PSC Description",
    "Contract Award Type",
    "Type of Contract Pricing",
    "Action Type",
    "action_type_code",
    "Description",
    "Start Date",
    "Last Modified Date",
    "Base Obligation Date",
    "Award Amount",
    "Total Outlays",
    "usaspending_permalink",
]

PAYLOAD = {
    "filters": {
        "agencies": [
            {
                "type": "awarding",
                "tier": "toptier",
                "name": "Department of Defense"
            }
        ],
        "time_period": [
            {"start_date": "2020-01-01", "end_date": "2026-12-31"}
        ],
        "psc_codes": {
            "require": [["Product Service Code", "Y"]]  # All Y* = Construction
        },
        "award_type_codes": ["A", "B", "C", "D"]  # Contracts only
    },
    "fields": FIELDS,
    "page": 1,
    "limit": 100,
    "sort": "Start Date",
    "order": "desc",
}


def classify_psc(psc_code: str) -> tuple:
    """Return (facility_category, facility_type) from PSC sub-code lookup."""
    if not psc_code:
        return ("Unknown", "Unknown")
    # Exact 4-char match first
    if psc_code in PSC_SUBCODES:
        return PSC_SUBCODES[psc_code]
    # Fall back to 2-char prefix
    prefix = psc_code[:2].upper()
    if prefix == "Y1":
        return ("Buildings", f"Y1 Building ({psc_code})")
    if prefix == "Y2":
        return ("Infrastructure", f"Y2 Infrastructure ({psc_code})")
    return ("Unknown", psc_code)


def classify_work_type(psc_code: str, description: str) -> str:
    """Classify as New Construction, Renovation/Repair, or Unknown."""
    psc = (psc_code or "").upper()
    desc = (description or "").lower()

    # PSC codes ending in 'B' are explicitly Repair/Alteration in the Y series
    if len(psc) >= 4 and psc[3] == "B":
        return "Renovation / Repair"

    # Scan description for renovation keywords
    if any(kw in desc for kw in RENOVATION_KEYWORDS):
        return "Renovation / Repair"

    # PSC codes ending in 'A' are typically new construction
    if len(psc) >= 4 and psc[3] == "A":
        return "New Construction"

    return "Unknown"


def flatten_record(record: dict) -> dict:
    psc = record.get("PSC Code") or ""
    desc = record.get("Description") or ""
    facility_category, facility_type = classify_psc(psc)
    work_type = classify_work_type(psc, desc)

    zip_raw = str(record.get("Place of Performance Zip Code") or "")
    zip5 = zip_raw[:5] if zip_raw else ""

    return {
        "award_id_piid":                    record.get("Award ID"),
        "modification_number":              record.get("Mod"),
        "recipient_name":                   record.get("Recipient Name"),
        "recipient_doing_business_as_name": record.get("recipient_doing_business_as_name"),
        "recipient_uei":                    record.get("recipient_uei"),
        "cage_code":                        record.get("cage_code"),
        "awarding_agency_name":             record.get("Awarding Agency"),
        "awarding_sub_agency_name":         record.get("Awarding Sub Agency"),
        "funding_agency_name":              record.get("Funding Agency"),
        "funding_sub_agency_name":          record.get("Funding Sub Agency"),
        "funding_office_code":              record.get("funding_office_code"),
        "funding_office_name":              record.get("funding_office_name"),
        "recipient_address_line_1":         record.get("recipient_address_line_1"),
        "recipient_address_line_2":         record.get("recipient_address_line_2"),
        "recipient_city_name":              record.get("recipient_city_name"),
        "recipient_state_code":             record.get("recipient_state_code"),
        "recipient_state_name":             record.get("recipient_state_name"),
        "recipient_country_name":           record.get("recipient_country_name"),
        "place_of_performance_state":       record.get("Place of Performance State Code"),
        "place_of_performance_state_name":  record.get("Place of Performance State Name"),
        "place_of_performance_zip":         zip5,
        "place_of_performance_country":     record.get("Place of Performance Country Code"),
        "naics_code":                       record.get("NAICS Code"),
        "naics_description":                record.get("NAICS Description"),
        "psc_code":                         psc,
        "psc_description":                  record.get("PSC Description"),
        "facility_category":                facility_category,   # Buildings vs Infrastructure
        "facility_type":                    facility_type,       # Barracks, CDC, Hospital, etc.
        "work_type":                        work_type,           # New Construction vs Renovation/Repair
        "award_type":                       record.get("Contract Award Type"),
        "type_of_contract_pricing":         record.get("Type of Contract Pricing"),
        "action_type":                      record.get("Action Type"),
        "action_type_code":                 record.get("action_type_code"),
        "transaction_description":          desc,
        "start_date":                       record.get("Start Date"),
        "last_modified_date":               record.get("Last Modified Date"),
        "base_obligation_date":             record.get("Base Obligation Date"),
        "award_amount":                     record.get("Award Amount"),
        "total_outlays":                    record.get("Total Outlays"),
        "usaspending_permalink":            record.get("usaspending_permalink"),
    }


def fetch_page(page: int) -> dict:
    payload = {**PAYLOAD, "page": page}
    response = requests.post(API_URL, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


def main():
    print("Fetching DoD construction contracts from USASpending.gov...")
    print("Filters: PSC='Y*' (Construction), Agency=DoD, Date>=2020-01-01\n")

    all_records = []
    page = 1

    data = fetch_page(page)
    total = data["page_metadata"]["total"]
    pages = (total // 100) + (1 if total % 100 else 0)
    print(f"Total records found: {total:,} across {pages} pages\n")

    all_records.extend([flatten_record(r) for r in data["results"]])
    print(f"Page {page}/{pages} — {len(all_records):,} records loaded")

    for page in range(2, pages + 1):
        time.sleep(0.3)
        data = fetch_page(page)
        all_records.extend([flatten_record(r) for r in data["results"]])
        print(f"Page {page}/{pages} — {len(all_records):,} records loaded")
        if not data["results"]:
            break

    if all_records:
        cols = list(all_records[0].keys())
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            writer.writerows(all_records)

        # Summary breakdown
        from collections import Counter
        work_counts = Counter(r["work_type"] for r in all_records)
        facility_counts = Counter(r["facility_type"] for r in all_records)

        print(f"\nDone! {len(all_records):,} records saved to: {os.path.abspath(OUTPUT_FILE)}")
        print("\n--- Work Type Breakdown ---")
        for k, v in work_counts.most_common():
            print(f"  {k}: {v:,}")
        print("\n--- Top 15 Facility Types ---")
        for k, v in facility_counts.most_common(15):
            print(f"  {k}: {v:,}")
    else:
        print("No records found.")


if __name__ == "__main__":
    main()
