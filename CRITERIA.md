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

## Change log

- Added PSC family filter (Y/Z/C) to `project_preaward` to match `projects_awarded` criteria; removed the old unfiltered "all DoD contracts" pull.
- Widened `project_preaward` window from 90 to 180 days (Air Force data-lag).
- Instrument type filter expanded from {C, D} to {B, C, D, R}.
- Added `piid_service_code` lookup table, replacing a hardcoded prefix fallback in Python.
- Simplified `piid_issuing_office` to 2 columns (dropped unused `command`/office-name text).
- Trimmed PIID decode output to 3 columns (`service`, `type_code`, `type`); `issuing_office_code` is used internally but no longer exposed.
- Renamed `new_opportunities` → `project_preaward`, `historical_projects` → `projects_awarded`.
