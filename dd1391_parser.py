"""
dd1391_parser.py
Phase 1: parse DD Form 1391 MILCON project PDFs (single or multi-project
"books", up to 200+ pages) into dod_budget.dd1391_project_overview and
dod_budget.dd1391_cost_rows. Terminal-testable, no UI, no live USASpending
calls (that's awards_sync.py's job only).

Pipeline per file:
  1. pypdf text extraction, page by page.
  2. Split into projects by detecting repeated "MILITARY CONSTRUCTION
     PROGRAM" + project-number headers (see _split_into_projects).
  3. Per page, validate the extracted text isn't scrambled (some DD1391s
     use custom font encodings pypdf can't map) - see _looks_garbled.
  4. Any garbled pages are rendered to PNG (tools/poppler pdftoppm) and
     handed to Claude as images instead of text - logged in import_notes.
  5. One Claude tool-call per project extracts all Table 1 fields + Table
     2 cost rows in a single structured response (mixing clean-page text
     and garbled-page images in the same message, so a project split
     across a corrupted page still costs one call, not two).
  6. Duplicate project_ids already in BigQuery are skipped, never
     overwritten (see CRITERIA.md-equivalent note in schema.py).

Usage:
  python dd1391_parser.py path/to/book1.pdf path/to/book2.pdf
  python dd1391_parser.py *.pdf --budget 6.00      # stop before exceeding $6
  python dd1391_parser.py book.pdf --dry-run       # parse + print, no BigQuery write

Requires: tools/poppler/bin/pdftoppm.exe (run `python tools/fetch_poppler.py`
once per machine) and ANTHROPIC_API_KEY in .env.
"""

import argparse
import glob
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import pandas as pd
from google.cloud import bigquery
from pypdf import PdfReader

from claude_client import CostTracker, call_tool
from config import GCP_PROJECT_ID, CREDENTIALS_PATH, DATASET_ID, CLAUDE_HAIKU_MODEL, POPPLER_BIN_DIR
from schema import DD1391_PROJECT_OVERVIEW_SCHEMA, DD1391_COST_ROWS_SCHEMA

PDFTOPPM = os.path.join(POPPLER_BIN_DIR, "pdftoppm.exe")

# A fresh DD1391 project record starts on a page carrying both of these
# anchors near its top. Tune against real samples in Phase 3 - service
# branches and fiscal years format the header block differently.
_PROJECT_HEADER_RE = re.compile(
    r"MILITARY\s+CONSTRUCTION\s+PROGRAM", re.IGNORECASE)
_PROJECT_NUMBER_FIELD_RE = re.compile(
    r"(?:7\.\s*)?PROJECT\s+NUMBER", re.IGNORECASE)
_COMPONENT_FIELD_RE = re.compile(r"1\.\s*COMPONENT", re.IGNORECASE)
_PROJECT_NUMBER_VALUE_RE = re.compile(
    r"PROJECT\s+NUMBER\D{0,20}?([A-Z]{0,2}-?\d{3,6}[A-Z]?)", re.IGNORECASE)

def _build_extract_tool(estimate_crx: pd.DataFrame, suppl_crx: pd.DataFrame) -> dict:
    """Builds EXTRACT_TOOL with the estimate_crx/suppl_crx code lists
    embedded as enums (with descriptions) so Claude assigns the right code
    directly during extraction, rather than us fuzzy-matching subsection
    text after the fact - see CRITERIA.md for why (reliability, and the
    token cost is negligible: ~500-700 tokens/call)."""
    estimate_codes = [int(c) for c in estimate_crx["code"]]
    estimate_desc = "; ".join(f"{r.code}={r.section}/{r.item}" for r in estimate_crx.itertuples())
    suppl_codes = [int(c) for c in suppl_crx["code"]]
    suppl_desc = "; ".join(
        f"{r.code}=block 12 ({r.code_matrix}) {r.subsection}"
        + (f" - {r.desc_code}: {r.item}" if r.desc_code else f": {r.item}")
        for r in suppl_crx.itertuples()
    )

    return {
        "name": "extract_dd1391_project",
        "description": (
            "Extract structured MILCON project data from a DD Form 1391 project "
            "record. Only report values explicitly written on the form. If a "
            "field is missing, illegible, or you are not confident, OMIT that "
            "key entirely rather than guessing - never invent a value."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "e.g. ARMY, NAVY, AIR FORCE, MARINE CORPS"},
                "fy_year": {"type": "string", "description": "Fiscal year of the MILCON program, e.g. FY2024"},
                "preparation_date": {
                    "type": "string",
                    "description": (
                        "Date the DD1391 was prepared, as ISO YYYY-MM-DD. If the form "
                        "only gives a month and year, use the 1st of that month "
                        "(YYYY-MM-01). If it only gives a year, use YYYY-01-01."
                    ),
                },
                "uic": {"type": "string", "description": "Unit Identification Code, if shown"},
                "base_name": {"type": "string", "description": "Installation/base name"},
                "city": {"type": "string"},
                "state": {"type": "string"},
                "country": {"type": "string"},
                "typology": {"type": "string", "enum": ["BEQ", "CDC"], "description": "Only set if the project is clearly a BEQ or CDC; omit otherwise"},
                "program_element": {"type": "string"},
                "category_code": {"type": "string"},
                "project_id": {"type": "string", "description": "The project number exactly as printed, e.g. P-209"},
                "project_cost_thousands": {"type": "number", "description": "Total project cost in $000, as printed in block 8/9"},
                "description_construction": {"type": "string", "description": "Full text of the 'Description of Proposed Construction' section (block 10), concatenated across all its pages"},
                "project_description": {"type": "string", "description": "Full text of the requirement/justification narrative (block 11)"},
                "unit_type": {"type": "string", "description": "e.g. 1+1 unaccompanied, 2-story BEQ module, etc, if stated"},
                "personnel": {
                    "type": "string",
                    "description": (
                        "The personnel/population count supporting the requirement, as a "
                        "PLAIN WHOLE NUMBER ONLY (e.g. '150') - no qualifier words like "
                        "'approximately', 'about', 'up to', no commas, no units. If the "
                        "form gives a range, use the single figure it identifies as the "
                        "requirement (not a range)."
                    ),
                },
                "number_of_units": {"type": "integer", "description": "A whole number only - no qualifier text."},
                "number_of_buildings": {"type": "integer", "description": "A whole number only - no qualifier text."},
                "cost_rows": {
                    "type": "array",
                    "description": (
                        "One row per line item from the cost estimate table(s): "
                        "Primary Facilities, Supporting Facilities, and Project "
                        "Summary (Subtotal/Contingency/SIOH/Total). IMPORTANT: every "
                        "Primary/Supporting Facilities group needs a row labeled "
                        "subsection='TOTAL' representing that group's total - this "
                        "includes a group heading line that itself functions as the "
                        "total (e.g. a line reading 'BEQ - Courthouse Bay' that sits "
                        "above a list of individual buildings IS the group total: "
                        "extract it as subsection='TOTAL', not as its own named item). "
                        "If individual buildings are listed separately underneath a "
                        "group heading (e.g. 'Building 1400', 'Building 1401'), give "
                        "each its own row too, all sharing the same 'code' as the "
                        "group - do not merge them into one row or omit them."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "section": {"type": "string", "enum": ["Primary Facilities", "Supporting Facilities", "Project Summary"]},
                            "code": {
                                "type": "integer",
                                "enum": estimate_codes,
                                "description": (
                                    "The facility-type code that best matches this line item, "
                                    "from this list (code=section/item): " + estimate_desc + ". "
                                    "Omit for Project Summary rows (Subtotal/Contingency/SIOH/"
                                    "Total/Escalation) - those aren't individual facility items."
                                ),
                            },
                            "subsection": {
                                "type": "string",
                                "description": (
                                    "The clean line-item label, with any '(...)' quantity/unit "
                                    "or percentage stripped out into value/unit instead. E.g. "
                                    "'BEQ, 2+2 (45,000 SF; 4,181 m2)' -> subsection='BEQ, 2+2'. "
                                    "'Contingency (5%)' -> subsection='Contingency'. Use "
                                    "subsection='TOTAL' for a facility group's total row (see "
                                    "the cost_rows description above)."
                                ),
                            },
                            "value": {
                                "type": "string",
                                "description": (
                                    "For a facility line: the SF square footage ONLY (ignore any "
                                    "m2 figure), as a plain number string e.g. '45000'. For "
                                    "SIOH/Contingency: the percentage as a plain number string "
                                    "e.g. '5'. Omit for dollar-only summary rows (Subtotal, "
                                    "Total, Escalation) that have neither an SF quantity nor a "
                                    "percentage."
                                ),
                            },
                            "unit": {
                                "type": "string",
                                "enum": ["SF", "PCT", ""],
                                "description": "SF for a facility square-footage row, PCT for SIOH/Contingency, '' (blank) for everything else.",
                            },
                            "cost": {
                                "type": "number",
                                "description": (
                                    "The dollar figure printed for this row EXACTLY as shown on "
                                    "the cost table - these tables are headed '($000)', so report "
                                    "the raw printed number (e.g. a printed '43,600' -> 43600), "
                                    "do NOT multiply it yourself, that conversion happens in code. "
                                    "Never calculate, sum, or re-derive this value (this matters "
                                    "especially for TOTAL/Subtotal rows: report the printed "
                                    "figure, never a computed sum of other rows)."
                                ),
                            },
                        },
                        "required": ["section", "subsection"],
                    },
                },
                "supplemental_data": {
                    "type": "array",
                    "description": (
                        "One row per lettered/numbered item found under block 12 "
                        "(Supplemental Data) that's actually filled in on the form. "
                        "Only include items you can find a matching code for below; "
                        "skip anything blank on the form. Codes (code=block 12 item): "
                        + suppl_desc
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "enum": suppl_codes, "description": "The matching block-12 item code from the list above"},
                            "value": {
                                "type": "string",
                                "description": (
                                    "The value as printed. Dates MUST be ISO YYYY-MM-DD - if "
                                    "only a month/year is given use YYYY-MM-01, if only a year "
                                    "use YYYY-01-01."
                                ),
                            },
                            "unit": {"type": "string", "enum": ["date", "pct", "$", "text"], "description": "What kind of value this is"},
                        },
                        "required": ["code", "value", "unit"],
                    },
                },
                "extraction_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "notes": {"type": "string", "description": "Anything ambiguous, illegible, or that a human reviewer should double check"},
            },
            "required": ["component", "project_id", "extraction_confidence"],
        },
    }


def _looks_garbled(text: str) -> bool:
    """Heuristic for pypdf output scrambled by a custom/embedded font
    encoding: too little text, or too few normal printable characters."""
    stripped = text.strip()
    if len(stripped) < 40:
        return True
    printable = sum(1 for c in stripped if c.isalnum() or c.isspace() or c in ".,:;()$%-/")
    if printable / len(stripped) < 0.85:
        return True
    # A clean DD1391 page should have real words - a wall of glued-together
    # symbols with almost no spaces is a strong garbling signal.
    if stripped.count(" ") / len(stripped) < 0.05:
        return True
    return False


def _extract_pages(pdf_path: str):
    """Returns list of (page_num (1-indexed), text, is_garbled)."""
    reader = PdfReader(pdf_path)
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append((i, text, _looks_garbled(text)))
    return pages


def _extract_project_number(text: str):
    m = _PROJECT_NUMBER_VALUE_RE.search(text)
    return re.sub(r"\s+", "", m.group(1)).upper() if m else None


def _split_into_projects(pages) -> list:
    """Returns list of (start_page, end_page) 1-indexed inclusive ranges.
    A candidate project start is a page matching both the 'MILITARY
    CONSTRUCTION PROGRAM' banner and a project-number/component field
    header near its top - but DD1391s commonly repeat that banner on
    EVERY page of the same project, so a candidate only starts a NEW
    project when its printed project number actually differs from the
    previous one. If no project number can be read anywhere in the file,
    splitting isn't safe to attempt - the whole file is treated as one
    project rather than risk over-splitting a single project's pages."""
    candidates = []
    for page_num, text, garbled in pages:
        if garbled:
            continue
        head = text[:1500]
        if _PROJECT_HEADER_RE.search(head) and (
            _PROJECT_NUMBER_FIELD_RE.search(head) or _COMPONENT_FIELD_RE.search(head)
        ):
            candidates.append((page_num, _extract_project_number(head)))

    if not candidates or not any(number for _, number in candidates):
        return [(1, len(pages))]

    starts = []
    last_number = None
    for page_num, number in candidates:
        if number and number != last_number:
            starts.append(page_num)
            last_number = number

    if not starts or starts[0] != 1:
        starts = [1] + starts

    ranges = []
    for i, start in enumerate(starts):
        end = starts[i + 1] - 1 if i + 1 < len(starts) else len(pages)
        ranges.append((start, end))
    return ranges


def _render_page_png(pdf_path: str, page_num: int, out_dir: str) -> bytes:
    prefix = os.path.join(out_dir, f"page_{page_num}")
    subprocess.run(
        [PDFTOPPM, "-f", str(page_num), "-l", str(page_num), "-r", "200", "-png", pdf_path, prefix],
        check=True, capture_output=True,
    )
    matches = glob.glob(prefix + "*.png")
    if not matches:
        raise RuntimeError(f"pdftoppm produced no output for page {page_num}")
    with open(matches[0], "rb") as f:
        return f.read()


def _build_content(pdf_path: str, project_pages: list, tmp_dir: str) -> tuple:
    """Builds the Anthropic message content list for one project: text
    blocks for clean pages, image blocks for garbled ones. Returns
    (content_list, extraction_method, garbled_page_numbers)."""
    import base64

    content = []
    garbled_pages = []
    for page_num, text, garbled in project_pages:
        if garbled:
            garbled_pages.append(page_num)
            png_bytes = _render_page_png(pdf_path, page_num, tmp_dir)
            content.append({"type": "text", "text": f"--- Page {page_num} (image, text extraction failed) ---"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png_bytes).decode()},
            })
        else:
            content.append({"type": "text", "text": f"--- Page {page_num} ---\n{text}"})

    method = "vision" if garbled_pages else "text"
    return content, method, garbled_pages


def load_crosswalks(client: bigquery.Client = None):
    """Loads estimate_crx (facility-type codes) and suppl_crx (block 12
    codes) - read once per parser run (not per project) and embedded into
    the extraction tool's enums. $0 BigQuery reads, ~60 rows total."""
    if client is None:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
        client = bigquery.Client(project=GCP_PROJECT_ID)
    estimate_crx = client.query(f"SELECT * FROM `{GCP_PROJECT_ID}.{DATASET_ID}.estimate_crx`").result().to_dataframe()
    suppl_crx = client.query(f"SELECT * FROM `{GCP_PROJECT_ID}.{DATASET_ID}.suppl_crx`").result().to_dataframe()
    return estimate_crx, suppl_crx


def parse_pdf(pdf_path: str, tracker: CostTracker, extract_tool: dict, suppl_crx: pd.DataFrame, progress_cb=None) -> list:
    """Returns a list of dicts: {"overview": {...}, "cost_rows": [...],
    "supplemental_data": [...]}. extract_tool/suppl_crx come from
    _build_extract_tool(*load_crosswalks()) / load_crosswalks() - built
    once per run and passed in, not rebuilt per project/per file.
    supplemental_data rows are denormalized against suppl_crx here (not
    left as a bare code for a downstream join) so the table is directly
    readable.
    progress_cb(done, total, message), if given, is called after each
    project finishes (and once before any work starts, done=0) so a caller
    (e.g. the Streamlit app) can render a live progress bar."""
    print(f"\n=== {pdf_path} ===")
    pages = _extract_pages(pdf_path)
    ranges = _split_into_projects(pages)
    print(f"  {len(pages)} pages, {len(ranges)} project(s) detected")
    suppl_by_code = suppl_crx.set_index("code").to_dict("index")

    if progress_cb:
        progress_cb(0, len(ranges), f"{os.path.basename(pdf_path)}: {len(pages)} pages, {len(ranges)} project(s) detected")

    results = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        for i, (start, end) in enumerate(ranges):
            project_pages = [p for p in pages if start <= p[0] <= end]
            content, method, garbled_pages = _build_content(pdf_path, project_pages, tmp_dir)

            system = (
                "You are extracting data from a U.S. DoD DD Form 1391 (Military "
                "Construction Project Data) record. Only use what is explicitly "
                "printed on the form pages given. Do not infer or guess any value "
                "that isn't stated."
            )
            print(f"  Pages {start}-{end}: extraction_method={method}" +
                  (f" (garbled pages: {garbled_pages})" if garbled_pages else ""))
            extracted = call_tool(CLAUDE_HAIKU_MODEL, system, content, extract_tool, tracker)

            import_notes = []
            if garbled_pages:
                import_notes.append(
                    f"Text extraction failed (corrupted font encoding) - used image "
                    f"fallback for pages {', '.join(map(str, garbled_pages))}."
                )
            if len(ranges) == 1 and not _split_boundary_confident(pages):
                import_notes.append(
                    "Could not auto-detect project boundaries from headers - treated "
                    "the whole file as a single project. Verify page range manually."
                )
            if extracted.get("notes"):
                import_notes.append(extracted["notes"])

            project_id = extracted.get("project_id") or f"{os.path.basename(pdf_path)}_p{start}"
            if not extracted.get("project_id"):
                import_notes.append("No project number found on the form - using file+page synthetic ID; verify manually.")

            overview = {
                "project_id": project_id,
                "component": extracted.get("component"),
                "fy_year": extracted.get("fy_year"),
                "preparation_date": extracted.get("preparation_date"),
                "uic": extracted.get("uic"),
                "base_name": extracted.get("base_name"),
                "city": extracted.get("city"),
                "state": extracted.get("state"),
                "country": extracted.get("country"),
                "typology": extracted.get("typology"),
                "program_element": extracted.get("program_element"),
                "category_code": extracted.get("category_code"),
                "project_cost_thousands": extracted.get("project_cost_thousands"),
                "description_construction": extracted.get("description_construction"),
                "project_description": extracted.get("project_description"),
                "unit_type": extracted.get("unit_type"),
                "personnel": extracted.get("personnel"),
                "number_of_units": extracted.get("number_of_units"),
                "number_of_buildings": extracted.get("number_of_buildings"),
                "source_milcon_book_fy": extracted.get("fy_year"),
                "source_file": os.path.basename(pdf_path),
                "source_pages": f"{start}-{end}",
                "extraction_method": method,
                "extraction_confidence": extracted.get("extraction_confidence"),
                "import_notes": " ".join(import_notes) if import_notes else None,
                "confirmed_award_id": None,
                "confirmed_piid": None,
                "confirmed_solicitation_id": None,
                "match_status": "unmatched",
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "write_timestamp": None,
            }
            cost_rows = []
            for r in extracted.get("cost_rows", []):
                cost = r.get("cost")
                # DD1391 cost tables are printed in $000.
                if cost is not None:
                    cost = cost * 1000
                cost_rows.append({
                    "project_id": project_id, "section": r.get("section"), "code": r.get("code"),
                    "subsection": r.get("subsection"), "value": r.get("value"),
                    "unit": r.get("unit"), "cost": cost,
                })
            supplemental_data = []
            for r in extracted.get("supplemental_data", []):
                code = r.get("code")
                crx_row = suppl_by_code.get(code, {})
                supplemental_data.append({
                    "project_id": project_id, "code": code,
                    "subsection_code": crx_row.get("subsection_code"),
                    "subsection": crx_row.get("subsection"),
                    "desc_code": crx_row.get("desc_code"),
                    "item": crx_row.get("item"),
                    "unit": r.get("unit"), "value": r.get("value"),
                })
            results.append({"overview": overview, "cost_rows": cost_rows, "supplemental_data": supplemental_data})
            if progress_cb:
                progress_cb(i + 1, len(ranges), f"{os.path.basename(pdf_path)}: project {project_id} done ({i + 1}/{len(ranges)})")

    return results


def _split_boundary_confident(pages) -> bool:
    """True only if at least one page yielded an actual project number -
    matches the requirement in _split_into_projects for a confident split."""
    for _, text, garbled in pages:
        if garbled:
            continue
        head = text[:1500]
        if _PROJECT_HEADER_RE.search(head) and (
            _PROJECT_NUMBER_FIELD_RE.search(head) or _COMPONENT_FIELD_RE.search(head)
        ) and _extract_project_number(head):
            return True
    return False


def _existing_project_ids(client: bigquery.Client, project_ids: list) -> set:
    if not project_ids:
        return set()
    table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dd1391_project_overview"
    query = f"SELECT project_id FROM `{table_id}` WHERE project_id IN UNNEST(@ids)"
    job = client.query(query, job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", project_ids)]
    ))
    try:
        return {row.project_id for row in job.result()}
    except Exception as e:
        print(f"  [WARN] Could not check for existing project_ids (has bigquery_setup.py been run?): {e}")
        return set()


def load_results(all_results: list, dry_run: bool):
    overview_rows = [r["overview"] for r in all_results]
    cost_rows = [row for r in all_results for row in r["cost_rows"]]
    supplemental_rows = [row for r in all_results for row in r.get("supplemental_data", [])]

    if dry_run:
        print("\n--dry-run: not writing to BigQuery. Overview rows:")
        for row in overview_rows:
            print(f"  {row['project_id']}: {row.get('base_name')} / {row.get('typology')} "
                  f"/ ${row.get('project_cost_thousands')}k [{row['extraction_method']}, "
                  f"{row['extraction_confidence']}]")
        return

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
    client = bigquery.Client(project=GCP_PROJECT_ID)

    existing = _existing_project_ids(client, [r["project_id"] for r in overview_rows])
    new_overview = []
    new_project_ids = set()
    for row in overview_rows:
        if row["project_id"] in existing:
            print(f"Project {row['project_id']} already exists in final table - skipped.")
            continue
        new_overview.append(row)
        new_project_ids.add(row["project_id"])

    new_cost_rows = [r for r in cost_rows if r["project_id"] in new_project_ids]
    new_supplemental_rows = [r for r in supplemental_rows if r["project_id"] in new_project_ids]

    if new_overview:
        table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dd1391_project_overview"
        errors = client.insert_rows_json(table_id, new_overview)
        if errors:
            print(f"  [ERROR] dd1391_project_overview insert errors: {errors}")
        else:
            print(f"Inserted {len(new_overview)} project(s) into dd1391_project_overview")

    if new_cost_rows:
        table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dd1391_cost_rows"
        errors = client.insert_rows_json(table_id, new_cost_rows)
        if errors:
            print(f"  [ERROR] dd1391_cost_rows insert errors: {errors}")
        else:
            print(f"Inserted {len(new_cost_rows)} cost row(s) into dd1391_cost_rows")

    if new_supplemental_rows:
        table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dd1391_supplemental_data"
        errors = client.insert_rows_json(table_id, new_supplemental_rows)
        if errors:
            print(f"  [ERROR] dd1391_supplemental_data insert errors: {errors}")
        else:
            print(f"Inserted {len(new_supplemental_rows)} row(s) into dd1391_supplemental_data")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdfs", nargs="+")
    parser.add_argument("--budget", type=float, default=6.00, help="Stop before exceeding this much USD (default $6)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(PDFTOPPM):
        print(f"[WARN] {PDFTOPPM} not found - run `python tools/fetch_poppler.py` first. "
              f"Image fallback will fail if any page needs it.")

    tracker = CostTracker(budget_usd=args.budget)
    estimate_crx, suppl_crx = load_crosswalks()
    extract_tool = _build_extract_tool(estimate_crx, suppl_crx)
    all_results = []
    try:
        for pdf_path in args.pdfs:
            all_results.extend(parse_pdf(pdf_path, tracker, extract_tool, suppl_crx))
    except RuntimeError as e:
        print(f"\n[STOPPED] {e}")
        print(f"Partial results from {len(all_results)} project(s) parsed before stopping will still be loaded.")

    load_results(all_results, args.dry_run)
    print(f"\nTotal estimated cost: ${tracker.total_usd:.4f} across {tracker.calls} Claude call(s)")


if __name__ == "__main__":
    sys.exit(main())
