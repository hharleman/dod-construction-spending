# DoD Construction Spending Pipeline — Criteria & Definitions

This file is the single source of truth for **what data this pipeline pulls
and why**. Keep it updated whenever a filter, table, or lookup changes —
code changes without a matching update here are considered incomplete.

## Data source

- API: USASpending.gov `POST /api/v2/search/spending_by_award/` (public, no key required)
- Pagination: 100 rows/page. The API has a hard ceiling of ~10,000 rows per
  query (page × limit ≤ 10,000), sorted by Award Amount descending, silently
  dropping everything past that point. `fetcher.py`'s
  `fetch_by_psc_family_chunked` works around this by recursively splitting
  the date range in half whenever a chunk comes back saturated.

## Agency filter

`agencies: [{"type": "awarding", "tier": "toptier", "name": "Department of Defense"}]`

All DoD service branches (Army, Navy, Air Force, Marines, National Guard,
DISA, etc.) are **subtier** agencies under the single "Department of
Defense" toptier — they are not separate toptier agencies in USASpending.
One toptier filter already includes every branch; there is nothing to add
per-branch.

**Known data-lag caveat:** the Air Force's own data feed into USASpending
lags real time by roughly 90–120 days. A short "recent activity" window
(e.g. 90 days) can show ~0 Air Force awards even though the filter covers
them correctly — their data for that window simply hasn't been reported
yet. This is why `project_preaward` uses a 180-day window, not 90.

## Award type codes (USASpending `award_type_codes`)

`["A", "B", "C", "D"]` — BPA Call, Purchase Order, Delivery Order,
Definitive Contract. This is USASpending's own "contracts" bucket (as
opposed to grants, loans, IDVs, direct payments). **Do not confuse this
with the PIID instrument-type code below** — both use letters A–D but they
are different code systems from different specs.

## PSC families (Product/Service Code)

Both `project_preaward` and `projects_awarded` are filtered to the same
three PSC families (`fetcher.PSC_FAMILIES`):

| Letter | PSC path | Category |
|---|---|---|
| Y | `["Product", "Y"]` | New Construction |
| Z | `["Product", "Z"]` | Maintenance, Repair & Alteration |
| C | `["Service", "C"]` | Architect & Engineer (A&E) Services |

Each award is tagged with `psc_category` = one of the three labels above.
`psc_code` / `psc_description` (the specific sub-code, e.g. `Y1ED` /
"CONSTRUCTION OF SHIP CONSTRUCTION AND REPAIR FACILITIES") are also kept
verbatim from the API.

## Instrument type filter (PIID position 9)

Per FAR 4.1603, a modern DoD PIID's 9th character is an instrument-type
code. Both tables are filtered to keep only:

- **B** — Invitation for bids
- **C** — Contract other than an indefinite-delivery contract
- **D** — Indefinite-delivery contract
- **R** — Request for proposals

This filter, the PSC family filter, and the agency filter are identical
between `project_preaward` and `projects_awarded` — the two tables track
**the same population of projects**, differing only in date window. This
is intentional: it lets you match a pre-award record to its eventual award
record.

## Date windows

- `projects_awarded` (`historical_run.py`): last 5 years
- `project_preaward` (`new_opp_run.py`): last 180 days (see Air Force lag
  caveat above)

## PIID decode (`piid_decoder.py`)

Decodes `award_id` (the PIID) per FAR 4.1603 / DFARS 204.1603:
- Positions 1–6: issuing-office AAC/DoDAAC → looked up in `piid_issuing_office`
- Position 9: instrument-type code → looked up in `piid_issuing_type`

Both lookups are pulled **live from BigQuery** at run time (`piid_lookup.py`),
not hardcoded — editing a row in either table changes the next run's output
with no code deploy needed. `piid_reference.py` is only the one-time seed
data `bigquery_setup.py` loads into these tables.

Decoded columns added to every award row: `service`, `type_code`, `type`.
`issuing_office_code` is parsed and used internally to resolve `service`
but is not kept as an output column. (Earlier iterations also carried
`piid_normalized`/`piid_is_valid`/`piid_validation_status`/`issuing_office`
(command name)/`piid_fiscal_year`/`serial_number` — all removed as unused.)

### Lookup tables

**`piid_issuing_office`** — `issuing_office_code` (e.g. `W9126G`) →
`service_owner` (e.g. `Army`). One row per specific contracting office.

**`piid_issuing_type`** — `type_code` → `type` description. Only contains
the four codes in scope (B/C/D/R), not the full A–Z FAR table.

**`piid_service_code`** — fallback used when `issuing_office_code` isn't a
known office (e.g. legacy/malformed PIIDs). Matches by PIID prefix:

| service_code | service |
|---|---|
| FA | Air Force |
| N | Navy |
| W | Army |
| OTHER | Other (catch-all, no prefix matched) |

Decode order: exact office match in `piid_issuing_office` → else longest
matching prefix in `piid_service_code` → else "Other".

## Project classification (`project_classifier.py`)

Both best-effort, regex/keyword based, applied to `description`:

- **`project_id`**: first P-number found (`P-209`, `P440` → `P209`, `P440`),
  leading zeros stripped, `""` if none found.
- **`project_type`**: first match, checked in this order — CDC → BEQ →
  Open Bay → Barracks → Housing. `""` if nothing matches.

## Table schemas

See `schema.py` for the authoritative field list/types. Summary:

- **`project_preaward`**: award fields + `project_id`/`project_type` +
  `service`/`type_code`/`type`
- **`projects_awarded`**: same as above, plus `psc_category`
- **`run_log`**: one row per pipeline stage per run — `file_name`, `stage`
  (`run_start` → `api_start` → `api_end` → `load_start` → `load_end` →
  `run_end`), `row_count`, `status`, `error_message`, `logged_at`
- **`piid_issuing_office`** / **`piid_issuing_type`** / **`piid_service_code`**:
  reference/lookup tables, seeded by `bigquery_setup.py` from
  `piid_reference.py`, otherwise untouched by daily runs

## Table structure vs. daily runs

`bigquery_setup.py` is the **only** file allowed to create/alter table
structure, and is never imported by the daily pipeline
(`project_run.py` → `historical_run.py` + `new_opp_run.py`). Running the
pipeline daily can never accidentally change a table's schema.

## DD1391 MILCON ingestion + award-matching pipeline

A separate, newer system layered on top of the above (different tables,
same BigQuery dataset/credentials). Architecture principle: **cached
awards, no live API calls during normal use** - only `awards_sync.py` is
allowed to call USASpending live.

### `awards` table (`awards_sync.py`)

Run monthly by hand (or later via Cloud Scheduler + Cloud Function).
Scope: DoD awarding agency, PSC Y-series **or** NAICS 236220 (two
separate queries merged/deduped - USASpending only ANDs filters within
one request), client-side filtered to Housing/Barracks/BEQ/CDC/Open Bay
descriptions via `project_classifier.classify_project_type` (reused as-is
from the existing pipeline). One row per `award_id`; USASpending's
`spending_by_award` endpoint already reports each award's current total
obligated value, so no separate funding-action summing is needed beyond a
safety-net dedupe across the two source queries.

**Known gaps:**
- `uic` is always NULL from the sync (USASpending has no native DoD UIC
  field) - `award_matcher.py` falls back to matching on
  `pop_city`/`pop_state`, and `uic_crosswalk` is filled in incrementally
  as UICs are confirmed during manual review.
- `parent_idiq_piid` (from "Parent Award ID") is null for most awards in
  this endpoint; no per-award detail-endpoint lookup is made (would be one
  API call per award, defeating the point of a cheap batch sync).

### `dd1391_project_overview` / `dd1391_cost_rows` (`dd1391_parser.py`)

Phase 1, terminal-only. pypdf text extraction per page; pages that come
back scrambled (custom font encodings some DD1391s use) are rendered to
PNG via the portable Poppler build in `tools/poppler/` (see
`tools/fetch_poppler.py`) and read by Claude Haiku as images instead -
logged in `import_notes`. One Haiku tool-call per detected project
extracts every Table 1 field plus the Table 2 cost-row table together
(mixing clean-page text and garbled-page images in the same call to avoid
a second round-trip). Multi-project "books" are split by detecting
repeated "MILITARY CONSTRUCTION PROGRAM" + project-number header pages;
this heuristic is unverified against real samples and should be the first
thing tuned once Phase 3 test PDFs are available.

Duplicate `project_id`s already in BigQuery are skipped on insert, never
overwritten - see `dd1391_parser.py::load_results`.

### `award_candidates` (`award_matcher.py`)

Phase 2. Reads only the cached `awards` table (one $0 BigQuery read per
service branch per matcher run, everything else is pandas in memory).
Hard filter (UIC-or-location + service branch + typology + award_date in
`[prep_date, prep_date+36mo]` + cost within ±40%) is free; ranking only
calls Claude when the hard filter returns 2-10 candidates (Haiku first,
escalating to Sonnet only if Haiku's top confidence is below 70). Zero
candidates triggers exactly one Anthropic-hosted web search
(`claude_client.web_search_once`) checking for a Defense Innovation Unit
OTA award or "not constructed"/cancelled language - never looped or
retried automatically.

### `uic_crosswalk`

Populated incrementally as UICs are confirmed during manual review - no
downloadable DoD UIC master list exists (DoDAAD requires restricted
access), so this is never pre-loaded.

### Model routing / cost controls

All Claude calls default to `CLAUDE_HAIKU_MODEL` (see `config.py`); the
only escalation path is Phase 2 ranking to Sonnet on low Haiku confidence,
and the single Phase 2 web search (which needs Sonnet for the hosted
`web_search_20250305` tool). `claude_client.CostTracker` prints a running
`$` estimate to the terminal after every call and raises before exceeding
a `--budget` flag (default $6) passed to `dd1391_parser.py` /
`award_matcher.py`.

## Change log

- Added PSC family filter (Y/Z/C) to `project_preaward` to match `projects_awarded` criteria; removed the old unfiltered "all DoD contracts" pull.
- Widened `project_preaward` window from 90 to 180 days (Air Force data-lag).
- Instrument type filter expanded from {C, D} to {B, C, D, R}.
- Added `piid_service_code` lookup table, replacing a hardcoded prefix fallback in Python.
- Simplified `piid_issuing_office` to 2 columns (dropped unused `command`/office-name text).
- Trimmed PIID decode output to 3 columns (`service`, `type_code`, `type`); `issuing_office_code` is used internally but no longer exposed.
- Renamed `new_opportunities` → `project_preaward`, `historical_projects` → `projects_awarded`.
- Added the DD1391 MILCON ingestion + award-matching pipeline: `awards`, `dd1391_project_overview`, `dd1391_cost_rows`, `award_candidates`, `uic_crosswalk` tables; `awards_sync.py`, `dd1391_parser.py`, `award_matcher.py`, `claude_client.py`.
