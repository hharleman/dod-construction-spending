"""
classify_records.py
-------------------
Select multiple DoD construction CSVs, classify each row with AI in parallel,
and produce a single merged output CSV.

Columns replaced:
  - facility_type  : BARRACKS, CHILD DEVELOPMENT CENTER, HOSPITAL, etc.
  - work_type      : NEW CONSTRUCTION, RENOVATION, REPAIR/MAINTENANCE, etc.
"""

import os
import csv
import json
import tkinter as tk
from tkinter import filedialog
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

# ---------- Load API key from .env ----------
def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

load_env()
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# ---------- Classification categories ----------
WORK_TYPES = [
    "NEW CONSTRUCTION",
    "RENOVATION",
    "REPAIR / MAINTENANCE",
    "ADDITION / EXPANSION",
    "DEMOLITION",
    "UNSPECIFIED MINOR CONSTRUCTION",
    "OTHER",
]

FACILITY_TYPES = [
    "ADMINISTRATIVE / HEADQUARTERS",
    "AIRCRAFT MAINTENANCE HANGAR",
    "AIRFIELD / RUNWAY / TAXIWAY",
    "AMMUNITION STORAGE",
    "BARRACKS / BACHELOR ENLISTED QUARTERS (BEQ)",
    "BACHELOR OFFICER QUARTERS (BOQ)",
    "CHAPEL / RELIGIOUS FACILITY",
    "CHILD DEVELOPMENT CENTER (CDC)",
    "DINING FACILITY (DFAC)",
    "EXCHANGE / RETAIL FACILITY",
    "FAMILY HOUSING",
    "FITNESS CENTER / GYM / POOL",
    "FUEL STORAGE / DISTRIBUTION",
    "HOSPITAL / MEDICAL / DENTAL",
    "INDUSTRIAL / PRODUCTION FACILITY",
    "LABORATORY / R&D FACILITY",
    "MISSILE / SPACE / COMMAND FACILITY",
    "MWR / RECREATION / COMMUNITY CENTER",
    "OPERATIONS / TRAINING FACILITY",
    "PARKING STRUCTURE / GARAGE",
    "PORT / SHIPYARD / PIER",
    "RANGE / TRAINING AREA",
    "ROAD / BRIDGE / PAVEMENT",
    "SCHOOL / EDUCATION CENTER",
    "SECURITY / GUARD FACILITY",
    "UTILITIES - ELECTRICAL",
    "UTILITIES - WATER / SEWER",
    "UTILITIES - HVAC / ENERGY",
    "VEHICLE MAINTENANCE SHOP",
    "WAREHOUSE / STORAGE",
    "WEAPONS RANGE / FIRING RANGE",
    "OTHER / UNCLASSIFIED",
]

SYSTEM_PROMPT = f"""You are an expert in DoD MILCON (Military Construction) contracts and the DoD budget process.
Classify each construction contract using the PSC code, NAICS code, and description.

Rules:
- PSC ending in 'B' = Repair/Alteration → work_type is "REPAIR / MAINTENANCE"
- PSC ending in 'A' = typically new construction
- NAICS 236220 = Commercial/Institutional (likely new build)
- NAICS 237xx = Infrastructure
- NAICS 238xx = Specialty trades (often repair/maintenance)
- Cross-reference MILCON budget book categories for facility_type

Return a JSON object with a single key "results" containing an array.
Each element must have exactly:
  "work_type": one of {json.dumps(WORK_TYPES)}
  "facility_type": one of {json.dumps(FACILITY_TYPES)}

Return ONLY valid JSON, no explanation."""

BATCH_SIZE = 50   # records per API call
MAX_WORKERS = 10  # parallel API calls


def classify_batch(batch_with_indices):
    """Classify a batch of (original_index, row) tuples. Returns list of (index, result)."""
    indices = [i for i, _ in batch_with_indices]
    rows = [r for _, r in batch_with_indices]

    payload = [
        {
            "i": idx,
            "psc": r.get("psc_code", ""),
            "naics": r.get("naics_code", ""),
            "naics_desc": r.get("naics_description", "")[:80],
            "desc": (r.get("description") or r.get("transaction_description") or "")[:300],
        }
        for idx, r in zip(indices, rows)
    ]

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    parsed = json.loads(response.choices[0].message.content)
    results = parsed.get("results", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(results, list):
        for v in parsed.values():
            if isinstance(v, list):
                results = v
                break

    return list(zip(indices, results))


def pick_files():
    root = tk.Tk()
    root.withdraw()
    paths = filedialog.askopenfilenames(
        title="Select one or more DoD construction CSVs",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
    )
    root.destroy()
    return list(paths)


def load_csvs(paths):
    all_rows = []
    fieldnames = None
    for path in paths:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            if fieldnames is None:
                fieldnames = list(reader.fieldnames or rows[0].keys())
            all_rows.extend(rows)
        print(f"  Loaded {len(rows):,} rows from {os.path.basename(path)}")
    return all_rows, fieldnames


def main():
    print("Select your DoD construction CSV files (hold Ctrl/Shift to select multiple)...")
    paths = pick_files()
    if not paths:
        print("No files selected. Exiting.")
        return

    print(f"\nLoading {len(paths)} file(s)...")
    rows, fieldnames = load_csvs(paths)
    print(f"Total rows loaded: {len(rows):,}\n")

    # Remove facility_category if present; ensure facility_type and work_type exist
    fieldnames = [f for f in fieldnames if f != "facility_category"]
    for col in ("facility_type", "work_type"):
        if col not in fieldnames:
            fieldnames.append(col)

    # Split all rows into batches
    batches = [
        [(i, rows[i]) for i in range(start, min(start + BATCH_SIZE, len(rows)))]
        for start in range(0, len(rows), BATCH_SIZE)
    ]

    total = len(rows)
    completed = 0
    failed_batches = 0

    print(f"Classifying {total:,} records across {len(batches)} batches "
          f"({MAX_WORKERS} parallel workers)...\n")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(classify_batch, b): b for b in batches}
        for future in as_completed(futures):
            try:
                results = future.result()
                for idx, result in results:
                    rows[idx]["facility_type"] = result.get("facility_type", "OTHER / UNCLASSIFIED")
                    rows[idx]["work_type"] = result.get("work_type", "OTHER")
                completed += len(results)
                print(f"  Classified {completed:,} / {total:,} records...")
            except Exception as e:
                failed_batches += 1
                print(f"  Batch error: {e}")

    # Save merged output next to the first selected file
    out_dir = os.path.dirname(paths[0])
    output_path = os.path.join(out_dir, "dod_construction_classified.csv")

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone! {total:,} records saved to:\n  {output_path}")
    if failed_batches:
        print(f"  ({failed_batches} batches failed — those rows keep original values)")


if __name__ == "__main__":
    main()
