"""
new_opp_run.py
Pulls the last 180 days of DoD awards across the same three PSC families
and instrument types as historical_run.py (see CRITERIA.md) and loads them
into dod_budget.project_preaward. Same criteria as projects_awarded, just a
shorter/more recent window - so the two tables track the same population of
projects at different points in their lifecycle.

180 days, not 90: USASpending's awarding-agency filter already covers every
DoD branch (Army/Navy/Air Force/etc. are all subtier agencies under the one
"Department of Defense" toptier, so a single toptier filter includes all of
them) - but the Air Force's own data feed into USASpending lags real time by
90-120+ days. A 90-day window returns ~0 Air Force awards not because
they're filtered out, but because their data for that window hasn't been
reported yet. 180 days reliably captures all branches.

Usage: python new_opp_run.py
"""

import sys

from processor import DataProcessor
from schema import NEW_OPPORTUNITIES_SCHEMA
from run_helpers import run_pull

FILE_NAME = "new_opp_run"


def run() -> int:
    return run_pull(
        file_name=FILE_NAME,
        fetch_fn=lambda: DataProcessor.prepare_new_opportunities(days_back=180),
        table_name="project_preaward",
        schema=NEW_OPPORTUNITIES_SCHEMA,
        csv_prefix="new_dod_opportunities",
    )


if __name__ == "__main__":
    sys.exit(run())
