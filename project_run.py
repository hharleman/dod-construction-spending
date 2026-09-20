"""
project_run.py
The ONLY file meant to run on a daily schedule. It runs the two data pulls
and nothing else - it never creates or alters BigQuery datasets/tables.
Table structure lives in bigquery_setup.py, which is run manually and only
when you actually want to change the schema.

Usage: python project_run.py
"""

import sys

import historical_run
import new_opp_run


def main() -> int:
    print("=" * 100)
    print("PROJECT RUN - daily data pull (no schema changes)")
    print("=" * 100)

    print("\n--- HISTORICAL PROJECTS ---")
    historical_status = historical_run.run()

    print("\n--- NEW OPPORTUNITIES ---")
    new_opp_status = new_opp_run.run()

    if historical_status != 0 or new_opp_status != 0:
        print("\nOne or more pulls failed - check run_log for details.")
        return 1

    print("\nBoth pulls completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
