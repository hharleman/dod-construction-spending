"""
award_matcher.py
Phase 2: match dd1391_project_overview rows (match_status = 'unmatched')
against the cached `awards` BigQuery table (populated by awards_sync.py -
this script NEVER calls USASpending live) and write ranked candidates to
award_candidates.

Cheapest-first, per CRITERIA.md:
  1. Hard filter in pandas (after one $0 BigQuery read of `awards`,
     narrowed server-side to service_branch): UIC (or base/city/state
     fallback - see _uic_or_location_match) + service branch + typology
     + award_date within [prep_date, prep_date+36mo] + cost within ±40%.
  2. Exactly 1 candidate -> rule-based, no AI call, still queued for
     manual review (status=pending).
  3. 2-10 candidates -> ranked by Haiku (structured fields only).
     If Haiku's top confidence < 70, re-rank the same candidates with
     Sonnet instead (escalation, not in addition to).
  4. 0 candidates -> match_status=no_award_found, note exactly what was
     searched so a human can judge whether to widen it manually.
  5. >10 candidates -> narrow to cost within ±15% before ranking.
  6. Still 0 after the hard filter -> exactly one targeted web search
     (DIU/OTA pilot check) before giving up.
  7. That search surfacing "not constructed"/"descoped"/"cancelled" ->
     match_status=project_not_awarded.

Usage:
  python award_matcher.py                    # all unmatched projects
  python award_matcher.py --project-id P209
  python award_matcher.py --dry-run --budget 6.00
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
from google.cloud import bigquery

from claude_client import CostTracker, call_tool, web_research_award
from config import GCP_PROJECT_ID, CREDENTIALS_PATH, DATASET_ID, CLAUDE_HAIKU_MODEL, CLAUDE_SONNET_MODEL
from project_classifier import classify_project_type
import os

RANK_TOOL = {
    "name": "rank_award_candidates",
    "description": (
        "Rank candidate USASpending awards by how likely each is to be the "
        "actual award for the given MILCON project. Return at most 5, "
        "highest confidence first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ranked": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "award_id": {"type": "string"},
                        "confidence_score": {"type": "number", "description": "0-100"},
                        "reasoning": {"type": "string", "description": "One sentence."},
                    },
                    "required": ["award_id", "confidence_score", "reasoning"],
                },
            }
        },
        "required": ["ranked"],
    },
}

COST_TOLERANCE_WIDE = 0.40
COST_TOLERANCE_NARROW = 0.15
DATE_WINDOW_MONTHS = 36
CONFIDENCE_ESCALATION_THRESHOLD = 70


def _get_client() -> bigquery.Client:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
    return bigquery.Client(project=GCP_PROJECT_ID)


def _load_unmatched_projects(client: bigquery.Client, project_id: str = None) -> pd.DataFrame:
    table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dd1391_project_overview"
    where = "WHERE match_status = 'unmatched'"
    params = []
    if project_id:
        where = "WHERE project_id = @pid"
        params = [bigquery.ScalarQueryParameter("pid", "STRING", project_id)]
    query = f"SELECT * FROM `{table_id}` {where}"
    return client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result().to_dataframe()


def _load_candidate_awards(client: bigquery.Client, service_branch: str) -> pd.DataFrame:
    """One $0 BigQuery read per service branch, cached per matcher run -
    all further narrowing happens in pandas, not repeated SQL round-trips."""
    table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.awards"
    query = f"SELECT * FROM `{table_id}` WHERE service_branch = @branch"
    job = client.query(query, job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("branch", "STRING", service_branch)]
    ))
    return job.result().to_dataframe()


def _component_to_service_branch(component: str) -> str:
    if not component:
        return ""
    c = component.upper()
    if "MARINE" in c:
        return "Marines"
    if "ARMY" in c:
        return "Army"
    if "NAVY" in c:
        return "Navy"
    if "AIR FORCE" in c:
        return "Air Force"
    return component


def _uic_or_location_match(project: pd.Series, awards: pd.DataFrame, base_zip: str = None) -> pd.Series:
    if project.get("uic"):
        uic_mask = awards["uic"] == project["uic"]
        if uic_mask.any():
            return uic_mask

    # Fallback: proximity, since USASpending has no native UIC field (see
    # awards_sync.py). Any one of zip3 proximity / city / state counts as a
    # location match (OR'd) - cost/date/typology do the rest of the
    # narrowing, so this stays permissive to avoid missing a true match
    # over a spelling difference (e.g. "KANEOHE BAY" vs "Kaneohe Bay MCBH").
    signals = []
    if base_zip and "pop_zip" in awards.columns:
        zip3 = str(base_zip)[:3]
        if zip3:
            signals.append(awards["pop_zip"].fillna("").str[:3] == zip3)

    city = (project.get("city") or "").strip().upper()
    if city:
        pop_city = awards["pop_city"].fillna("").str.upper()
        signals.append(pop_city.str.contains(city, regex=False) | pop_city.apply(lambda c: c in city if c else False))

    state = (project.get("state") or "").strip().upper()
    if state:
        signals.append(awards["pop_state"].fillna("").str.upper() == state)

    if not signals:
        return pd.Series(True, index=awards.index)

    combined = signals[0]
    for s in signals[1:]:
        combined |= s
    return combined


def _construction_code_mask(awards: pd.DataFrame) -> pd.Series:
    """Defensive check that a candidate is actually construction-coded
    (NAICS 236220 or PSC Y/Z) - normally guaranteed already by
    awards_sync.py's scope, kept here in case `awards` ever gets a wider
    feed."""
    naics_ok = awards["naics_code"].fillna("") == "236220"
    psc_ok = awards["psc_code"].fillna("").str.upper().str.startswith(("Y", "Z"))
    return naics_ok | psc_ok


def _cost_tolerance_mask(awards: pd.DataFrame, project_cost: float, tolerance: float) -> pd.Series:
    if not project_cost:
        return pd.Series(True, index=awards.index)
    target = project_cost * 1000  # project_cost_thousands -> dollars
    low, high = target * (1 - tolerance), target * (1 + tolerance)
    return awards["award_amount"].between(low, high)


def _date_window_mask(awards: pd.DataFrame, prep_date) -> pd.Series:
    if pd.isna(prep_date):
        return pd.Series(True, index=awards.index)
    start = pd.to_datetime(prep_date, errors="coerce")
    if pd.isna(start):
        return pd.Series(True, index=awards.index)
    end = start + pd.DateOffset(months=DATE_WINDOW_MONTHS)
    dates = pd.to_datetime(awards["award_date"], errors="coerce")
    return dates.between(start, end)


def hard_filter(project: pd.Series, awards: pd.DataFrame, base_zip: str = None) -> pd.DataFrame:
    if awards.empty:
        return awards

    mask = _uic_or_location_match(project, awards, base_zip)
    mask &= _date_window_mask(awards, project.get("preparation_date"))
    mask &= _cost_tolerance_mask(awards, project.get("project_cost_thousands"), COST_TOLERANCE_WIDE)
    mask &= _construction_code_mask(awards)

    typology = project.get("typology")
    if typology:
        mask &= awards["description"].apply(classify_project_type) == typology

    return awards[mask]


def _candidate_row(project_id, rank, award, confidence, reasoning, ranked_by):
    return {
        "project_id": project_id,
        "rank": rank,
        "piid": award.get("piid"),
        "solicitation_id": None,  # not available from USASpending search endpoint
        "award_id": award.get("award_id"),
        "confidence_score": confidence,
        "match_reasoning": reasoning,
        "uic": award.get("uic"),
        "status": "pending",
        "ranked_by": ranked_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _rank_with_model(model, project: pd.Series, candidates: pd.DataFrame, tracker: CostTracker):
    project_fields = {
        "project_id": project["project_id"], "base_name": project.get("base_name"),
        "city": project.get("city"), "state": project.get("state"), "uic": project.get("uic"),
        "component": project.get("component"), "typology": project.get("typology"),
        "project_cost_thousands": project.get("project_cost_thousands"),
        "preparation_date": str(project.get("preparation_date")),
    }
    candidate_fields = [
        {"award_id": r["award_id"], "piid": r.get("piid"), "awardee": r.get("awardee"),
         "awardee_uei": r.get("awardee_uei"), "service_branch": r.get("service_branch"),
         "psc_code": r.get("psc_code"), "naics_code": r.get("naics_code"),
         "award_amount": r.get("award_amount"), "award_date": str(r.get("award_date")),
         "pop_city": r.get("pop_city"), "pop_state": r.get("pop_state"), "pop_zip": r.get("pop_zip"),
         "description": (r.get("description") or "")[:200]}
        for _, r in candidates.iterrows()
    ]
    content = (
        f"Project:\n{project_fields}\n\nCandidate awards:\n{candidate_fields}\n\n"
        "Rank these candidates for how likely each is to be the award for this project."
    )
    result = call_tool(model, "You rank USASpending award records against a MILCON project record.",
                        content, RANK_TOOL, tracker)
    return result.get("ranked", [])


def match_project(project: pd.Series, awards_by_branch: dict, tracker: CostTracker, base_zip: str = None) -> list:
    service_branch = _component_to_service_branch(project.get("component"))
    awards = awards_by_branch.get(service_branch, pd.DataFrame())
    candidates = hard_filter(project, awards, base_zip)
    n = len(candidates)

    if n == 0:
        return _handle_no_candidates(project, tracker)

    if n > 10:
        narrowed = candidates[_cost_tolerance_mask(candidates, project.get("project_cost_thousands"), COST_TOLERANCE_NARROW)]
        if not narrowed.empty:
            candidates = narrowed
            n = len(candidates)

    if n == 1:
        award = candidates.iloc[0]
        return {
            "rows": [_candidate_row(project["project_id"], 1, award, 100.0,
                                     "Unique match after hard filter (UIC/location + service + typology + date + cost).",
                                     "rule")],
            "match_status": "pending_review",
            "candidates_df": candidates,
        }

    ranked = _rank_with_model(CLAUDE_HAIKU_MODEL, project, candidates.head(10), tracker)
    ranked_by = "haiku"
    if ranked and ranked[0].get("confidence_score", 0) < CONFIDENCE_ESCALATION_THRESHOLD:
        ranked = _rank_with_model(CLAUDE_SONNET_MODEL, project, candidates.head(10), tracker)
        ranked_by = "sonnet"

    by_award_id = {r["award_id"]: r for _, r in candidates.iterrows()}
    rows = []
    for i, r in enumerate(ranked[:5], start=1):
        award = by_award_id.get(r["award_id"])
        if award is None:
            continue
        rows.append(_candidate_row(project["project_id"], i, award, r.get("confidence_score"),
                                    r.get("reasoning"), ranked_by))
    return {"rows": rows, "match_status": "pending_review", "candidates_df": candidates}


def _handle_no_candidates(project: pd.Series, tracker: CostTracker) -> dict:
    search_desc = (
        f"UIC={project.get('uic')}, city={project.get('city')}, state={project.get('state')}, "
        f"date_window={project.get('preparation_date')}..+{DATE_WINDOW_MONTHS}mo, "
        f"cost_range=+/-{int(COST_TOLERANCE_WIDE*100)}% of ${project.get('project_cost_thousands')}k"
    )
    note = f"No award found in cached awards table - searched {search_desc}, 0 results."
    empty = pd.DataFrame()

    query = (f"{project['project_id']} {project.get('base_name') or ''} "
             f"{project.get('typology') or ''} MILCON construction award contract")
    try:
        research = web_research_award(query, tracker)
    except Exception as e:
        return {"rows": [], "match_status": "no_award_found",
                "note": f"{note} (web research fallback failed: {e})", "candidates_df": empty}

    reasoning = research.get("reasoning", "")
    if research.get("status_note"):
        return {"rows": [], "match_status": "project_not_awarded",
                "note": f"{note} Web research: {research['status_note']} - {reasoning}", "candidates_df": empty}

    if not research.get("found"):
        return {"rows": [], "match_status": "no_award_found",
                "note": f"{note} One-time web research was inconclusive: {reasoning}", "candidates_df": empty}

    if research.get("mechanism") == "ota":
        return {
            "rows": [], "match_status": "non_standard_authority", "candidates_df": empty,
            "notice_id": research.get("notice_id"),
            "note": f"{note} Web research: OTA award, notice_id={research.get('notice_id')}, "
                    f"awardee={research.get('awardee')} - {reasoning}",
        }

    if research.get("mechanism") == "standard_contract" and research.get("piid"):
        return {
            "rows": [_candidate_row(project["project_id"], 1, {"award_id": research["piid"], "piid": research["piid"], "uic": None},
                                     70.0, f"Found via web+SAM.gov research, not in cached awards table yet: {reasoning}", "sonnet_research")],
            "match_status": "pending_review", "candidates_df": empty,
            "note": f"{note} Web research found a likely PIID not yet in the awards cache: {reasoning}",
        }

    return {"rows": [], "match_status": "no_award_found",
            "note": f"{note} Web research found something but not a usable PIID/notice_id: {reasoning}", "candidates_df": empty}


def _update_project_status(client: bigquery.Client, project_id: str, match_status: str, extra_note: str = None):
    table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dd1391_project_overview"
    set_clause = "match_status = @status"
    params = [
        bigquery.ScalarQueryParameter("status", "STRING", match_status),
        bigquery.ScalarQueryParameter("pid", "STRING", project_id),
    ]
    if extra_note:
        set_clause += ", import_notes = CONCAT(IFNULL(import_notes, ''), ' ', @note)"
        params.append(bigquery.ScalarQueryParameter("note", "STRING", extra_note))
    query = f"UPDATE `{table_id}` SET {set_clause} WHERE project_id = @pid"
    client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()


def run(project_id: str, dry_run: bool, budget: float):
    client = _get_client()
    projects = _load_unmatched_projects(client, project_id)
    if projects.empty:
        print("No unmatched projects found.")
        return

    print(f"{len(projects)} unmatched project(s) to match.")
    tracker = CostTracker(budget_usd=budget)
    awards_by_branch = {}
    all_candidate_rows = []
    status_updates = []

    for _, project in projects.iterrows():
        branch = _component_to_service_branch(project.get("component"))
        if branch not in awards_by_branch:
            awards_by_branch[branch] = _load_candidate_awards(client, branch)

        print(f"\n{project['project_id']} ({project.get('base_name')}, {branch}):")
        try:
            result = match_project(project, awards_by_branch, tracker)
        except RuntimeError as e:
            print(f"  [STOPPED] {e}")
            break

        rows = result["rows"]
        print(f"  {len(rows)} candidate(s) -> match_status={result['match_status']}")
        for r in rows:
            print(f"    rank {r['rank']}: {r['award_id']} conf={r['confidence_score']} - {r['match_reasoning']}")
        all_candidate_rows.extend(rows)
        status_updates.append((project["project_id"], result["match_status"], result.get("note")))

    if dry_run:
        print(f"\n--dry-run: not writing to BigQuery. {len(all_candidate_rows)} candidate row(s) would be written.")
    else:
        if all_candidate_rows:
            table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.award_candidates"
            errors = client.insert_rows_json(table_id, all_candidate_rows)
            if errors:
                print(f"  [ERROR] award_candidates insert errors: {errors}")
            else:
                print(f"\nInserted {len(all_candidate_rows)} row(s) into award_candidates")
        for pid, status, note in status_updates:
            _update_project_status(client, pid, status, note)

    print(f"\nTotal estimated cost: ${tracker.total_usd:.4f} across {tracker.calls} Claude call(s)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--budget", type=float, default=6.00)
    args = parser.parse_args()
    run(args.project_id, args.dry_run, args.budget)


if __name__ == "__main__":
    sys.exit(main())
