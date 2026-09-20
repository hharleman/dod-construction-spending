"""
historical_run.py
Pulls the last 5 years of DoD construction-PSC awards (Y/Z/C families) and
loads them into dod_budget.projects_awarded.

Usage: python historical_run.py
"""

import sys

from processor import DataProcessor
from schema import HISTORICAL_PROJECTS_SCHEMA
from run_helpers import run_pull

FILE_NAME = "historical_run"


def run() -> int:
    return run_pull(
        file_name=FILE_NAME,
        fetch_fn=lambda: DataProcessor.prepare_historical_projects(years_back=5),
        table_name="projects_awarded",
        schema=HISTORICAL_PROJECTS_SCHEMA,
        csv_prefix="historical_dod_projects",
    )


if __name__ == "__main__":
    sys.exit(run())
