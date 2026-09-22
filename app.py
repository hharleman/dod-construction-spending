"""
app.py
Local web app (Streamlit). Home page links to four pipelines - only
"DD1391 Pipeline" is built; Awards Pipeline / Monthly Status Reports /
Delay Reports are placeholders for future work.

DD1391 Pipeline: four steps, run in order: Parse -> Review -> Match ->
Final Review. Nothing is written to BigQuery until you click Approve in
step 4, except dim_base_location.uic backfills in step 2 (explicit
button per project, not automatic).

Run:
  streamlit run app.py

Requires: ANTHROPIC_API_KEY in .env, tools/poppler/bin/pdftoppm.exe
(python tools/fetch_poppler.py), and the DD1391 + base tables already
created (python bigquery_setup.py awards dd1391_project_overview
dd1391_cost_rows award_candidates dim_base_location dim_location_crosswalk
then python load_base_tables.py).
"""

import os
import tempfile
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from google.cloud import bigquery

from claude_client import CostTracker, call_tool
from config import GCP_PROJECT_ID, CREDENTIALS_PATH, DATASET_ID, CLAUDE_HAIKU_MODEL
from dd1391_parser import parse_pdf, _existing_project_ids, _build_extract_tool, load_crosswalks
from award_matcher import match_project, _load_candidate_awards, _component_to_service_branch

st.set_page_config(page_title="DD1391 Pipeline", layout="wide")

STEPS = ["1. DD1391 Parser", "2. DD1391 Review", "3. Award Match", "4. Final Review"]

TYPOLOGY_OPTIONS = ["", "BEQ", "CDC"]
UNIT_TYPE_OPTIONS = ["", "2+0", "2+2", "Open Bay"]
OVERVIEW_DISPLAY_COLS = [
    "project_id", "component", "fy_year", "preparation_date", "uic", "base_name", "city",
    "state", "country", "typology", "program_element", "category_code",
    "project_cost_thousands", "unit_type", "personnel", "number_of_units",
    "number_of_buildings", "description_construction", "project_description",
    "source_file", "source_pages", "extraction_method", "extraction_confidence",
    "import_notes", "match_status",
]

BASE_MATCH_TOOL = {
    "name": "match_base_location",
    "description": (
        "Match a DoD installation name from a DD1391 (possibly a nickname, "
        "abbreviation, or slightly different spelling) to the single closest "
        "entry in a reference list of bases. ALWAYS pick the closest one, "
        "even if the match is imperfect - there must always be a pick. Use "
        "'confidence' to say how sure you are, rather than leaving base_id blank."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "base_id": {"type": "string", "description": "The best-matching base_id - required, never omit"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "reasoning": {"type": "string", "description": "One sentence."},
        },
        "required": ["base_id", "confidence", "reasoning"],
    },
}


def records_with_clean_ints(df: pd.DataFrame, int_cols: list) -> list:
    """data_editor's NumberColumn hands back NaN for a blank cell, which
    isn't valid JSON - BigQuery INTEGER columns need a real int or None."""
    records = df.to_dict("records")
    for r in records:
        for c in int_cols:
            if c in r and (r[c] is None or pd.isna(r[c])):
                r[c] = None
            elif c in r:
                r[c] = int(r[c])
    return records


@st.cache_resource
def get_bq_client():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH
    return bigquery.Client(project=GCP_PROJECT_ID)


@st.cache_data(ttl=300)
def load_base_locations() -> pd.DataFrame:
    client = get_bq_client()
    table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dim_base_location"
    return client.query(f"SELECT * FROM `{table_id}`").result().to_dataframe()


@st.cache_resource
def get_crosswalks():
    return load_crosswalks(get_bq_client())


@st.cache_resource
def get_extract_tool():
    return _build_extract_tool(*get_crosswalks())


def match_base(base_name: str, bases: pd.DataFrame):
    if not base_name or bases.empty:
        return None
    name = base_name.strip().lower()
    exact = bases[bases["base_name"].str.strip().str.lower() == name]
    if not exact.empty:
        return exact.iloc[0]
    contains = bases[bases["base_name"].str.lower().str.contains(name, na=False, regex=False)]
    if not contains.empty:
        return contains.iloc[0]
    return None


def ai_match_base(base_name: str, city: str, state: str, uic: str, bases: pd.DataFrame, tracker: CostTracker):
    """Exact/substring match_base() already ran and failed - hand this off
    to Claude. Narrow the candidate list to the DD1391's own state first
    (per user instruction: search within-state before going wider), and
    surface the UIC so Claude can cross-check against any base that
    already has one recorded. Always returns a best pick - see
    BASE_MATCH_TOOL's schema (base_id is required, never blank)."""
    pool = bases
    if state:
        state_pool = bases[bases["state"].fillna("").str.strip().str.upper() == state.strip().upper()]
        if not state_pool.empty:
            pool = state_pool

    listing = "\n".join(
        f"{r.base_id}: {r.base_name} ({r.city}, {r.state}) - uic={r.uic or 'none recorded'}"
        for r in pool.itertuples()
    )
    content = (
        f"DD1391 installation name: '{base_name}' (city={city}, state={state}, uic={uic or 'none'})\n\n"
        f"Candidate bases{' (filtered to state=' + state + ')' if state and pool is not bases else ''}:\n{listing}\n\n"
        "Pick the single closest match."
    )
    result = call_tool(CLAUDE_HAIKU_MODEL,
                        "You match a DoD installation name to the closest entry in a reference base list.",
                        content, BASE_MATCH_TOOL, tracker)
    base_id = result.get("base_id")
    found = bases[bases["base_id"] == base_id] if base_id else pd.DataFrame()
    if not found.empty:
        return found.iloc[0], result.get("confidence"), result.get("reasoning", "")
    return None, result.get("confidence"), result.get("reasoning", "")


def backfill_uic(base_id: str, uic: str):
    client = get_bq_client()
    table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dim_base_location"
    query = f"UPDATE `{table_id}` SET uic = @uic WHERE base_id = @base_id"
    client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uic", "STRING", uic),
        bigquery.ScalarQueryParameter("base_id", "STRING", base_id),
    ])).result()
    load_base_locations.clear()


def init_state():
    defaults = {
        "page": "home",            # home | dd1391 | awards | monthly | delay
        "projects": [],           # list of {"overview": dict, "cost_rows": list}
        "match": {},               # project_id -> {"rows": [...], "match_status": str, "note": str, "candidates_df": df}
        "selection": {},           # project_id -> {"type": "candidate"|status, "award": dict|None} - set by the radio in Step 3
        "manual_overrides": {},    # project_id -> same shape, set by manual award-ID entry; takes precedence over "selection"
        "reviewed": set(),         # project_ids marked "Add to Review" in Step 2
        "written": set(),          # project_ids already committed to BigQuery this session
        "ai_base_matches": {},     # project_id -> base dict, confirmed via AI match
        "review_idx": 0,
        "parsing": False,
        "matching": False,
        "step": 0,
        "tracker": CostTracker(budget_usd=6.00),
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_state()


def goto(step_idx):
    st.session_state.step = step_idx
    st.rerun()


def goto_page(page_name):
    st.session_state.page = page_name
    st.rerun()


def get_selection(pid):
    """Manual award-ID entry (Step 3) always wins over the ranked-candidate
    radio, since it's an explicit human override."""
    return st.session_state.manual_overrides.get(pid) or st.session_state.selection.get(pid)


# ---------------------------------------------------------------------------
# Home page - the front door. DD1391 Pipeline is built; the other three are
# placeholders for future pipelines.
# ---------------------------------------------------------------------------
if st.session_state.page == "home":
    st.title("DoD Construction Spending")
    st.caption("Pick a pipeline.")
    cols = st.columns(4)
    if cols[0].button("DD1391 Pipeline", use_container_width=True, type="primary"):
        goto_page("dd1391")
    if cols[1].button("Awards Pipeline", use_container_width=True):
        goto_page("awards")
    if cols[2].button("Monthly Status Reports", use_container_width=True):
        goto_page("monthly")
    if cols[3].button("Delay Reports", use_container_width=True):
        goto_page("delay")
    st.stop()

if st.session_state.page in ("awards", "monthly", "delay"):
    labels = {"awards": "Awards Pipeline", "monthly": "Monthly Status Reports", "delay": "Delay Reports"}
    if st.button("< Back to Home"):
        goto_page("home")
    st.title(labels[st.session_state.page])
    st.info("Coming soon.")
    st.stop()

# From here on, st.session_state.page == "dd1391".
if st.button("< Back to Home"):
    goto_page("home")

st.title("DD1391 MILCON Pipeline")

with st.sidebar:
    st.subheader("Budget")
    budget = st.number_input("Stop before spending more than ($)", value=6.00, step=1.00)
    st.session_state.tracker.budget_usd = budget
    st.metric("Spent so far", f"${st.session_state.tracker.total_usd:.4f}")
    st.caption(f"{st.session_state.tracker.calls} Claude call(s) this session")
    if st.button("Reset everything"):
        for k in ["projects", "match", "selection", "manual_overrides", "reviewed", "written", "ai_base_matches"]:
            st.session_state[k] = [] if k == "projects" else set() if k in ("reviewed", "written") else {}
        st.session_state.review_idx = 0
        st.session_state.step = 0
        st.session_state.tracker = CostTracker(budget_usd=budget)
        st.rerun()


# A dynamic key (tied to the current step) forces Streamlit to treat the
# radio as a fresh widget whenever `goto()` changes step, so its displayed
# selection always reflects st.session_state.step - writing directly to a
# widget's own key after it's rendered raises StreamlitWidgetAlreadyInstantiatedError.
selected = st.radio("Step", STEPS, index=st.session_state.step, horizontal=True,
                     label_visibility="collapsed", key=f"step_radio_{st.session_state.step}")
if STEPS.index(selected) != st.session_state.step:
    st.session_state.step = STEPS.index(selected)
    st.rerun()

step = st.session_state.step


st.divider()

# ---------------------------------------------------------------------------
# Step 1: Parser
# ---------------------------------------------------------------------------
if step == 0:
    st.caption("Upload one or more DD1391 PDFs and parse them. This calls Claude "
               "(Haiku, escalating to image reading only where a page's text is "
               "corrupted) - nothing is written to BigQuery yet.")

    uploaded = st.file_uploader("DD1391 PDF(s)", type="pdf", accept_multiple_files=True)

    if st.button("Run Parser", disabled=not uploaded or st.session_state.parsing):
        st.session_state.parsing = True
        st.rerun()

    if st.session_state.parsing:
        st.info("Parsing... this may take a few minutes.")
        progress = st.progress(0.0)
        status = st.empty()
        st.session_state.projects = []

        def on_progress(done, total, message, file_idx=0, file_count=1):
            overall = (file_idx + (done / total if total else 1)) / file_count
            progress.progress(min(overall, 1.0))
            status.caption(message)

        extract_tool = get_extract_tool()
        _, suppl_crx = get_crosswalks()
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                for i, f in enumerate(uploaded):
                    pdf_path = os.path.join(tmp_dir, f.name)
                    with open(pdf_path, "wb") as out:
                        out.write(f.getbuffer())
                    results = parse_pdf(
                        pdf_path, st.session_state.tracker, extract_tool, suppl_crx,
                        progress_cb=lambda d, t, m, i=i: on_progress(d, t, m, i, len(uploaded)),
                    )
                    st.session_state.projects.extend(results)
            except RuntimeError as e:
                st.error(f"Stopped: {e}")

        st.session_state.parsing = False
        progress.progress(1.0)
        st.success(f"Parsed {len(st.session_state.projects)} project(s) from {len(uploaded)} file(s). "
                   f"Estimated cost: ${st.session_state.tracker.total_usd:.4f}.")
        st.rerun()

    if st.session_state.projects:
        st.subheader("Parsed projects")
        summary = pd.DataFrame([
            {
                "project_id": p["overview"]["project_id"],
                "base_name": p["overview"].get("base_name"),
                "typology": p["overview"].get("typology"),
                "cost_$k": p["overview"].get("project_cost_thousands"),
                "extraction_method": p["overview"].get("extraction_method"),
                "confidence": p["overview"].get("extraction_confidence"),
                "import_notes": p["overview"].get("import_notes"),
            }
            for p in st.session_state.projects
        ])
        st.dataframe(summary, use_container_width=True)
        dupes = summary["project_id"][summary["project_id"].duplicated()].unique()
        if len(dupes):
            st.warning(f"Duplicate project_id(s) found: {list(dupes)} - these came from different "
                       f"files/pages but share a project number. Check them in Step 2.")

        if st.button("Next: Review >", type="primary"):
            goto(1)

# ---------------------------------------------------------------------------
# Step 2: Review - spreadsheet-style, one project at a time
# ---------------------------------------------------------------------------
elif step == 1:
    st.caption("Click through each project. Scroll right to see every dd1391_project_overview "
               "field, scroll down to see its dd1391_cost_rows. Click 'Add to Review' when it's correct.")

    if not st.session_state.projects:
        st.info("Run the parser in Step 1 first.")
    else:
        project_ids = [p["overview"]["project_id"] for p in st.session_state.projects]
        st.session_state.review_idx = min(st.session_state.review_idx, len(project_ids) - 1)

        nav = st.columns([1, 4, 1])
        if nav[0].button("< Prev", disabled=st.session_state.review_idx == 0):
            st.session_state.review_idx -= 1
            st.rerun()
        current_pid = nav[1].selectbox(
            "Project", project_ids, index=st.session_state.review_idx,
            format_func=lambda pid: f"{'OK  ' if pid in st.session_state.reviewed else '...  '}{pid}",
        )
        st.session_state.review_idx = project_ids.index(current_pid)
        if nav[2].button("Next >", disabled=st.session_state.review_idx == len(project_ids) - 1):
            st.session_state.review_idx += 1
            st.rerun()

        idx = st.session_state.review_idx
        p = st.session_state.projects[idx]
        overview = p["overview"]
        pid = overview["project_id"]

        st.caption(f"extraction: {overview.get('extraction_method')} / "
                   f"confidence: {overview.get('extraction_confidence')} / "
                   f"source: {overview.get('source_file')} pp.{overview.get('source_pages')}")

        bases = load_base_locations()
        matched_base = st.session_state.ai_base_matches.get(pid) or match_base(overview.get("base_name"), bases)
        if matched_base is not None:
            if isinstance(matched_base, dict):
                matched_base = pd.Series(matched_base)
            overview["base_name"] = matched_base["base_name"]  # canonicalize to dim_base_location's name
            base_col, uic_col = st.columns([3, 1])
            base_col.info(f"Matched base: **{matched_base['base_name']}** "
                          f"(base_id={matched_base['base_id']}, {matched_base.get('city')}, {matched_base.get('state')}) - "
                          f"stored UIC: {matched_base.get('uic') or 'none yet'}")
            if overview.get("uic") and overview["uic"] != matched_base.get("uic"):
                if uic_col.button(f"Save UIC '{overview['uic']}' to this base", key=f"backfill_{pid}"):
                    backfill_uic(matched_base["base_id"], overview["uic"])
                    st.success(f"Saved UIC {overview['uic']} to {matched_base['base_id']}")
                    st.rerun()
        else:
            st.warning(f"No exact/substring match in dim_base_location for base_name='{overview.get('base_name')}' - "
                       f"asking Claude to pick the closest base (state-filtered).")
            if st.button("AI: find best base match (1 Claude call)", key=f"ai_match_{pid}"):
                try:
                    found, confidence, reasoning = ai_match_base(
                        overview.get("base_name"), overview.get("city"), overview.get("state"),
                        overview.get("uic"), bases, st.session_state.tracker)
                    if found is not None:
                        st.session_state.ai_base_matches[pid] = found.to_dict()
                        st.success(f"AI matched to {found['base_name']} ({found['base_id']}, confidence={confidence}): {reasoning}")
                        st.rerun()
                    else:
                        st.error(f"Claude's pick wasn't a valid base_id ({reasoning}) - check dim_base_location.")
                except RuntimeError as e:
                    st.error(str(e))

        overview_df = pd.DataFrame([{c: overview.get(c) for c in OVERVIEW_DISPLAY_COLS}])
        edited = st.data_editor(
            overview_df, key=f"overview_{idx}_{pid}", use_container_width=True, num_rows="fixed",
            column_config={
                "typology": st.column_config.SelectboxColumn(options=TYPOLOGY_OPTIONS),
                "unit_type": st.column_config.SelectboxColumn(options=UNIT_TYPE_OPTIONS),
                "description_construction": st.column_config.TextColumn(width="large"),
                "project_description": st.column_config.TextColumn(width="large"),
            },
        )
        cleaned = records_with_clean_ints(edited, ["number_of_units", "number_of_buildings"])[0]
        overview.update(cleaned)

        st.caption("Cost rows (dd1391_cost_rows)")
        cost_df = pd.DataFrame(p["cost_rows"]) if p["cost_rows"] else pd.DataFrame(
            columns=["project_id", "section", "code", "subsection", "value", "unit", "cost"])
        edited_costs = st.data_editor(
            cost_df, key=f"costs_{idx}_{pid}", use_container_width=True, num_rows="dynamic",
            column_config={
                "section": st.column_config.SelectboxColumn(
                    options=["Primary Facilities", "Supporting Facilities", "Project Summary"]),
                "code": st.column_config.NumberColumn(help="FK -> estimate_crx.code"),
                "unit": st.column_config.SelectboxColumn(options=["", "SF", "PCT"]),
            },
        )
        p["cost_rows"] = records_with_clean_ints(edited_costs, ["code"])

        st.caption("Supplemental Data (dd1391_supplemental_data) - block 12, denormalized against suppl_crx")
        suppl_cols = ["project_id", "code", "subsection_code", "subsection", "desc_code", "item", "unit", "value"]
        suppl_df = pd.DataFrame(p.get("supplemental_data", []), columns=suppl_cols) if p.get("supplemental_data") \
            else pd.DataFrame(columns=suppl_cols)
        edited_suppl = st.data_editor(
            suppl_df, key=f"suppl_{idx}_{pid}", use_container_width=True, num_rows="dynamic",
            column_config={
                "code": st.column_config.NumberColumn(help="FK -> suppl_crx.code (901-918)"),
                "subsection_code": st.column_config.NumberColumn(),
                "unit": st.column_config.SelectboxColumn(options=["date", "pct", "$", "text"]),
            },
        )
        p["supplemental_data"] = records_with_clean_ints(edited_suppl, ["code", "subsection_code"])

        if st.button("Add + to Review", key=f"add_review_{pid}", type="primary"):
            st.session_state.reviewed.add(pid)
            remaining = [i for i, p_id in enumerate(project_ids) if p_id not in st.session_state.reviewed]
            if not remaining:
                # Whole queue is done - move straight to Award Match, no extra click.
                st.toast("All projects reviewed - moving to Award Match.")
                goto(2)
            else:
                # Jump to the next un-reviewed project in the queue (not just
                # idx+1), so re-visiting an already-done one doesn't stall it.
                after = [i for i in remaining if i > idx]
                st.session_state.review_idx = after[0] if after else remaining[0]
                st.rerun()

        st.caption(f"{len(st.session_state.reviewed)}/{len(project_ids)} reviewed")
        st.divider()
        if st.button("Next: Award Match >"):
            goto(2)

# ---------------------------------------------------------------------------
# Step 3: Award Match
# ---------------------------------------------------------------------------
elif step == 2:
    st.caption("Matches each project against the cached `awards` table (no live USASpending "
               "calls). Ranking only calls Claude when the hard filter returns 2-10 candidates.")

    if not st.session_state.projects:
        st.info("Run the parser in Step 1 first.")
    else:
        if st.button("Run Matching", disabled=st.session_state.matching):
            st.session_state.matching = True
            st.rerun()

        if st.session_state.matching:
            st.info("Matching... this may take a minute.")
            progress = st.progress(0.0)
            status = st.empty()
            client = get_bq_client()
            bases = load_base_locations()
            awards_cache = {}
            candidate_rows_to_write = []
            projects = st.session_state.projects
            try:
                for i, p in enumerate(projects):
                    overview = p["overview"]
                    pid = overview["project_id"]
                    branch = _component_to_service_branch(overview.get("component"))
                    if branch not in awards_cache:
                        awards_cache[branch] = _load_candidate_awards(client, branch)
                    matched_base = st.session_state.ai_base_matches.get(pid) or match_base(overview.get("base_name"), bases)
                    base_zip = matched_base.get("zip") if isinstance(matched_base, dict) else (
                        matched_base["zip"] if matched_base is not None else None)
                    result = match_project(pd.Series(overview), awards_cache, st.session_state.tracker, base_zip=base_zip)
                    st.session_state.match[pid] = result
                    candidate_rows_to_write.extend(result["rows"])
                    status.caption(f"{pid} ({i + 1}/{len(projects)}): {len(result['rows'])} candidate(s), "
                                   f"match_status={result['match_status']}")
                    progress.progress((i + 1) / len(projects))
            except RuntimeError as e:
                st.error(f"Stopped: {e}")

            if candidate_rows_to_write:
                table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.award_candidates"
                errors = client.insert_rows_json(table_id, candidate_rows_to_write)
                if errors:
                    st.error(f"award_candidates insert errors: {errors}")
            st.session_state.matching = False
            st.success(f"Matched {len(st.session_state.match)} project(s). "
                       f"Estimated cost: ${st.session_state.tracker.total_usd:.4f}.")
            st.rerun()

        def _lookup_award(client, award_id):
            table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.awards"
            rows = list(client.query(
                f"SELECT * FROM `{table_id}` WHERE award_id = @id",
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", award_id)]),
            ).result())
            if rows:
                d = dict(rows[0].items())
                d["confidence_score"] = 100.0
                d["match_reasoning"] = "Manually entered by reviewer."
                return d
            return {"award_id": award_id, "piid": award_id, "solicitation_id": None,
                    "confidence_score": 100.0, "match_reasoning": "Manually entered by reviewer (not found in cached awards table)."}

        for p in st.session_state.projects:
            pid = p["overview"]["project_id"]
            result = st.session_state.match.get(pid)

            st.subheader(pid)
            override = st.session_state.manual_overrides.get(pid)
            with st.expander("Manually enter an award ID instead" + (" (active)" if override else "")):
                if override:
                    st.info(f"Manual override active: {override['award']['award_id']} - takes precedence over the radio below.")
                    if st.button("Clear manual override", key=f"clear_manual_{pid}"):
                        del st.session_state.manual_overrides[pid]
                        st.rerun()
                manual_id = st.text_input("Award ID / PIID", key=f"manual_award_{pid}")
                if st.button("Use this award ID", key=f"manual_award_btn_{pid}") and manual_id:
                    award = _lookup_award(get_bq_client(), manual_id.strip())
                    st.session_state.manual_overrides[pid] = {"type": "candidate", "award": award}
                    st.success(f"Set {pid}'s match to {manual_id.strip()} (manual entry).")
                    st.rerun()

            if not result:
                st.caption("Run Matching above, or use the manual entry option, to set a match for this project.")
                continue
            if result["rows"]:
                cand_df = pd.DataFrame(result["rows"])
                extra = result.get("candidates_df")
                if extra is not None and not extra.empty and "award_id" in extra.columns:
                    extra_cols = extra.set_index("award_id")[
                        [c for c in ["awardee", "awardee_uei", "service_branch", "psc_code"] if c in extra.columns]]
                    cand_df = cand_df.join(extra_cols, on="award_id")
                display_cols = ["rank", "award_id", "piid", "awardee", "awardee_uei", "service_branch",
                                 "psc_code", "confidence_score", "match_reasoning", "ranked_by"]
                st.dataframe(cand_df[[c for c in display_cols if c in cand_df.columns]], use_container_width=True)

                options = {}
                for _, r in cand_df.iterrows():
                    label = f"rank {r['rank']}: {r['award_id']} - {r.get('awardee', '')} (conf {r['confidence_score']})"
                    options[label] = r.to_dict()
                options["No award found"] = {"__status__": "no_award_found"}
                options["Non-standard authority (OTA)"] = {"__status__": "non_standard_authority"}
                options["Project not awarded"] = {"__status__": "project_not_awarded"}
                choice = st.radio("Select the correct match", list(options.keys()), key=f"choice_{pid}")
                picked = options[choice]
                if "__status__" in picked:
                    st.session_state.selection[pid] = {"type": picked["__status__"], "award": None}
                else:
                    st.session_state.selection[pid] = {"type": "candidate", "award": picked}
            else:
                st.write(f"match_status: **{result['match_status']}**")
                st.caption(result.get("note") or "")
                notice_id = result.get("notice_id")
                if result["match_status"] == "non_standard_authority":
                    notice_id = st.text_input("notice_id (OTA agreement/notice number)",
                                               value=notice_id or "", key=f"notice_{pid}") or None
                st.session_state.selection[pid] = {"type": result["match_status"], "award": None, "notice_id": notice_id}

        st.divider()
        if st.button("Next: Final Review >"):
            goto(3)

# ---------------------------------------------------------------------------
# Step 4: Final Review
# ---------------------------------------------------------------------------
else:
    st.caption("Approve each project to write it to BigQuery. Projects whose project_id "
               "already exists in dd1391_project_overview are skipped, never overwritten.")

    if not st.session_state.projects:
        st.info("Run the parser in Step 1 first.")
    else:
        client = get_bq_client()
        for p in st.session_state.projects:
            pid = p["overview"]["project_id"]
            selection = get_selection(pid)
            written = pid in st.session_state.written

            status_bit = "Written" if written else (
                f"Match: {selection['award']['award_id']}" if selection and selection["type"] == "candidate"
                else (f"Status: {selection['type']}" if selection else "Not matched yet")
            )
            with st.expander(f"{pid} - {p['overview'].get('base_name')} ({status_bit})"):
                st.caption("Review one more time before approving.")
                overview_view = pd.DataFrame([{c: p["overview"].get(c) for c in OVERVIEW_DISPLAY_COLS}])
                st.dataframe(overview_view, use_container_width=True)
                if p["cost_rows"]:
                    st.caption("Cost rows")
                    st.dataframe(pd.DataFrame(p["cost_rows"]), use_container_width=True)
                if p.get("supplemental_data"):
                    st.caption("Supplemental data")
                    st.dataframe(pd.DataFrame(p["supplemental_data"]), use_container_width=True)

                if written:
                    st.success("Written")
                    continue
                if not selection:
                    st.warning("Run Step 3 first (or set a match there before coming back here).")
                    continue

                cols = st.columns([3, 2])
                if selection["type"] == "candidate":
                    award = selection["award"]
                    cols[0].write(f"Match: {award['award_id']} (conf {award['confidence_score']})")
                else:
                    cols[0].write(f"Status: {selection['type']}")

                if cols[1].button("Approve & Write", key=f"approve_{pid}", type="primary"):
                    existing = _existing_project_ids(client, [pid])
                    if pid in existing:
                        st.warning(f"Project {pid} already exists in final table - skipped.")
                        st.session_state.written.add(pid)
                    else:
                        overview = dict(p["overview"])
                        if selection["type"] == "candidate":
                            award = selection["award"]
                            overview["confirmed_award_id"] = award["award_id"]
                            overview["confirmed_piid"] = award["piid"]
                            overview["confirmed_solicitation_id"] = award.get("solicitation_id")
                            overview["match_status"] = "matched"
                        else:
                            overview["match_status"] = selection["type"]
                            if selection["type"] == "non_standard_authority":
                                overview["notice_id"] = selection.get("notice_id")
                        overview["write_timestamp"] = datetime.now(timezone.utc).isoformat()
                        overview = {k: v for k, v in overview.items() if not k.startswith("_")}

                        table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dd1391_project_overview"
                        errors = client.insert_rows_json(table_id, [overview])
                        if errors:
                            st.error(f"Insert errors: {errors}")
                        else:
                            if p["cost_rows"]:
                                cost_table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dd1391_cost_rows"
                                client.insert_rows_json(cost_table_id, p["cost_rows"])
                            if p.get("supplemental_data"):
                                suppl_table_id = f"{GCP_PROJECT_ID}.{DATASET_ID}.dd1391_supplemental_data"
                                client.insert_rows_json(suppl_table_id, p["supplemental_data"])
                            st.session_state.written.add(pid)
                            st.success(f"Wrote {pid} to BigQuery.")
                            st.rerun()
