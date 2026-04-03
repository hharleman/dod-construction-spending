"""
DoD Military Construction Spending - USASpending.gov API
One row per contract modification. Shows initial award + every change order.
"""

import requests
import csv
import time
import os
from datetime import date
from collections import Counter

API_BASE   = "https://api.usaspending.gov/api/v2"
TODAY      = date.today().isoformat()
OUTPUT_FILE = f"dod_construction_spending_{date.today().strftime('%Y%m%d')}.csv"

# ── Exact field names accepted by spending_by_transaction ─────────────────────
FIELDS = [
    "Award ID",
    "Mod",
    "Recipient Name",
    "Recipient UEI",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Funding Agency",
    "Funding Sub Agency",
    "pop_state_code",
    "pop_country_name",
    "Award Type",
    "Action Type",
    "Transaction Description",
    "product_or_service_code",
    "product_or_service_description",
    "naics_code",
    "naics_description",
    "Action Date",
    "Transaction Amount",
]

ACTION_TYPE_LABELS = {
    "A": "Initial Award",
    "B": "Supplemental Agreement",
    "C": "Funding Only Modification",
    "D": "Change Order",
    "E": "Terminate for Default",
    "F": "Terminate for Convenience",
    "G": "Exercise an Option",
    "H": "Novation Agreement",
    "K": "Close Out",
    "M": "Other Administrative Action",
    "R": "Rerepresentation",
    "T": "Transfer Action",
    "X": "Terminate for Cause",
}

MAINTENANCE_KEYWORDS = [
    "maintenance", "preventive", "preventative", "inspection",
    "service contract", "O&M", "operations and maintenance",
    "pest control", "janitorial", "grounds", "mowing", "snow removal",
]

RENOVATION_KEYWORDS = [
    "renovate", "renovation", "repair", "restore", "restoration",
    "alteration", "alter", "upgrade", "retrofit", "rehab", "rehabilitation",
    "replacement", "replace", "modernize", "modernization",
    "refurbish", "improvement", "improve", "convert", "conversion",
]

PSC_BUILDING_TYPE = {
    "Y1AA": "Barracks / BEQ",                  "Y1AB": "Barracks / BEQ",
    "Y1BA": "Child Development Center (CDC)",   "Y1BB": "Child Development Center (CDC)",
    "Y1CA": "Chapel / Religious Facility",      "Y1CB": "Chapel / Religious Facility",
    "Y1DA": "Hospital / Medical Facility",      "Y1DB": "Hospital / Medical Facility",
    "Y1EA": "Administrative / Office",          "Y1EB": "Administrative / Office",
    "Y1FA": "Industrial / Maintenance Shop",    "Y1FB": "Industrial / Maintenance Shop",
    "Y1GA": "Recreation / Fitness / Pool",      "Y1GB": "Recreation / Fitness / Pool",
    "Y1HA": "R&D / Laboratory Facility",        "Y1HB": "R&D / Laboratory Facility",
    "Y1JA": "School / Training Facility",       "Y1JB": "School / Training Facility",
    "Y1KA": "Warehouse / Storage",              "Y1KB": "Warehouse / Storage",
    "Y1LA": "Family Housing",                   "Y1LB": "Family Housing",
    "Y1MA": "Missile / Space / Command",        "Y1MB": "Missile / Space / Command",
    "Y1NA": "Other Building",                   "Y1NB": "Other Building",
    "Y1ZA": "Other Building",                   "Y1ZZ": "Other Building",
    "Y2AA": "Airfield / Runway / Taxiway",      "Y2AB": "Airfield / Runway / Taxiway",
    "Y2BA": "Dam / Waterway / Levee",           "Y2BB": "Dam / Waterway / Levee",
    "Y2CA": "Road / Bridge / Pavement",         "Y2CB": "Road / Bridge / Pavement",
    "Y2DA": "Electrical Utility",               "Y2DB": "Electrical Utility",
    "Y2EA": "Fuel / Gas System",                "Y2EB": "Fuel / Gas System",
    "Y2FA": "Water Supply / Treatment",         "Y2FB": "Water Supply / Treatment",
    "Y2GA": "Sewage / Waste Treatment",         "Y2GB": "Sewage / Waste Treatment",
    "Y2HA": "Heating / Cooling (HVAC)",         "Y2HB": "Heating / Cooling (HVAC)",
    "Y2ZA": "Other Infrastructure",             "Y2ZZ": "Other Infrastructure",
}

DESC_BUILDING_KEYWORDS = [
    (["barrack", "beq", "bachelor enlisted", "dormitor", "billet"],           "Barracks / BEQ"),
    (["boq", "bachelor officer"],                                              "Bachelor Officer Quarters (BOQ)"),
    (["child development", "cdc", "daycare", "day care"],                     "Child Development Center (CDC)"),
    (["chapel", "religious", "church"],                                        "Chapel / Religious Facility"),
    (["hospital", "medical", "dental", "clinic", "health"],                   "Hospital / Medical Facility"),
    (["administrative", "headquarters", "hq", "office building"],             "Administrative / Office"),
    (["hangar", "aircraft maintenance"],                                       "Aircraft Maintenance Hangar"),
    (["dining", "dfac", "galley", "mess hall", "food service"],               "Dining Facility (DFAC)"),
    (["fitness", "gymnasium", "gym", "swimming pool", "aquatic", "mwr"],      "Recreation / Fitness / Pool"),
    (["school", "education", "classroom", "academic", "training facilit"],    "School / Training Facility"),
    (["warehouse", "storage facility", "supply depot"],                        "Warehouse / Storage"),
    (["family housing", "general officer quarter"],                            "Family Housing"),
    (["airfield", "runway", "taxiway", "apron", "air traffic"],               "Airfield / Runway / Taxiway"),
    (["road", "pavement", "parking lot", "bridge", "sidewalk"],               "Road / Bridge / Pavement"),
    (["electrical", "power plant", "substation", "generator"],                "Electrical Utility"),
    (["water treatment", "water supply", "potable water"],                    "Water Supply / Treatment"),
    (["sewer", "sewage", "wastewater", "sanitary"],                           "Sewage / Waste Treatment"),
    (["heating", "cooling", "hvac", "boiler", "chiller"],                     "Heating / Cooling (HVAC)"),
    (["fuel", "petroleum", "gas distribution", "tank farm"],                  "Fuel / Gas System"),
    (["missile", "silo", "launch facilit", "command center"],                 "Missile / Space / Command"),
    (["laboratory", "research facilit", "test facilit"],                      "R&D / Laboratory Facility"),
    (["vehicle maintenance", "motor pool", "auto shop"],                      "Vehicle Maintenance Shop"),
    (["ammunition", "ammo storage", "magazine"],                              "Ammunition Storage"),
    (["port", "pier", "shipyard", "dry dock", "wharf", "marina"],            "Port / Shipyard / Pier"),
    (["firing range", "weapons range", "range facilit"],                      "Weapons / Firing Range"),
    (["security", "guard house", "entry control", "gate house"],              "Security / Guard Facility"),
]


def classify_work_type(naics, psc, desc):
    naics = (naics or "").strip()
    psc   = (psc or "").upper().strip()
    desc  = (desc or "").lower()
    if naics == "236220":
        return "New Construction"
    if any(kw in desc for kw in MAINTENANCE_KEYWORDS):
        return "Regular Maintenance"
    if any(kw in desc for kw in RENOVATION_KEYWORDS):
        return "Renovation & Repairs"
    if len(psc) >= 4 and psc[3] == "B":
        return "Renovation & Repairs"
    return "Renovation & Repairs"


def classify_building_type(psc, desc):
    psc  = (psc or "").upper().strip()
    desc = (desc or "").lower()
    if psc in PSC_BUILDING_TYPE:
        return PSC_BUILDING_TYPE[psc]
    for keywords, label in DESC_BUILDING_KEYWORDS:
        if any(kw in desc for kw in keywords):
            return label
    return "Other / Unclassified"


def flatten(record):
    desc        = record.get("Transaction Description") or ""
    psc         = (record.get("product_or_service_code") or "").upper().strip()
    naics       = str(record.get("naics_code") or "").strip()
    action_type = (record.get("Action Type") or "").strip()
    action_code = next((k for k, v in ACTION_TYPE_LABELS.items() if v == action_type), "")
    mod_label   = ACTION_TYPE_LABELS.get(action_code, action_type or "Modification")

    action_date  = record.get("Action Date") or ""
    fiscal_year  = ""
    if len(action_date) >= 7:
        yr = int(action_date[:4])
        mo = int(action_date[5:7])
        fiscal_year = str(yr + 1) if mo >= 10 else str(yr)

    return {
        "fiscal_year":                  fiscal_year,
        "award_id_piid":                record.get("Award ID"),
        "modification_number":          record.get("Mod"),
        "mod_type":                     mod_label,
        "awarding_agency_name":         record.get("Awarding Agency"),
        "awarding_sub_agency_name":     record.get("Awarding Sub Agency"),
        "funding_agency_name":          record.get("Funding Agency"),
        "funding_sub_agency_name":      record.get("Funding Sub Agency"),
        "recipient_uei":                record.get("Recipient UEI"),
        "recipient_name":               record.get("Recipient Name"),
        "place_of_performance_state":   record.get("pop_state_code"),
        "place_of_performance_country": record.get("pop_country_name"),
        "award_type":                   record.get("Award Type"),
        "transaction_description":      desc,
        "psc_code":                     psc,
        "psc_description":              record.get("product_or_service_description"),
        "naics_code":                   naics,
        "naics_description":            record.get("naics_description"),
        "work_type":                    classify_work_type(naics, psc, desc),
        "building_type":                classify_building_type(psc, desc),
        "action_date":                  action_date,
        "this_mod_obligated_amount":    record.get("Transaction Amount"),
    }


def fetch_page(page):
    payload = {
        "filters": {
            "agencies": [
                {"type": "awarding", "tier": "toptier", "name": "Department of Defense"}
            ],
            "time_period": [
                {"start_date": "2020-01-01", "end_date": TODAY}
            ],
            "award_type_codes": ["D"],
            "psc_codes": {"require": [["Product", "Y"]]},
            "naics_codes": ["23"],
        },
        "fields": FIELDS,
        "page": page,
        "limit": 100,
        "sort": "Action Date",
        "order": "desc",
    }
    resp = requests.post(f"{API_BASE}/search/spending_by_transaction/", json=payload, timeout=60)
    if not resp.ok:
        print(f"  API error {resp.status_code}: {resp.text}")
        resp.raise_for_status()
    return resp.json()


def main():
    print("=" * 60)
    print("DoD Military Construction Spending — USASpending.gov")
    print(f"Date range: 2020-01-01 to {TODAY}")
    print("=" * 60 + "\n")

    print("Fetching page 1 to get total record count...")
    data  = fetch_page(1)
    meta  = data.get("page_metadata", {})
    total = meta.get("total") or meta.get("count") or 0
    pages = (total + 99) // 100 if total else 9999

    print(f"Total records: {total:,}  |  Pages: {pages if total else 'unknown'}\n")

    all_rows = [flatten(r) for r in data.get("results", [])]
    print(f"  Page 1 — {len(all_rows):,} records")

    page = 2
    while True:
        if total and page > pages:
            break
        time.sleep(0.25)
        data    = fetch_page(page)
        results = data.get("results", [])
        if not results:
            break
        all_rows.extend(flatten(r) for r in results)
        print(f"  Page {page}/{pages if total else '?'} — {len(all_rows):,} records loaded")
        page += 1

    if not all_rows:
        print("\nNo records returned.")
        return

    cols = list(all_rows[0].keys())
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(all_rows)

    work_counts     = Counter(r["work_type"]    for r in all_rows)
    building_counts = Counter(r["building_type"] for r in all_rows)

    print(f"\n{'=' * 60}")
    print(f"Done! {len(all_rows):,} records → {os.path.abspath(OUTPUT_FILE)}")
    print("\n--- Work Type Breakdown ---")
    for k, v in work_counts.most_common():
        print(f"  {k}: {v:,}")
    print("\n--- Top 20 Building Types ---")
    for k, v in building_counts.most_common(20):
        print(f"  {k}: {v:,}")


if __name__ == "__main__":
    main()
