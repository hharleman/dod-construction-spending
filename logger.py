"""
logger.py
Purpose: Write one row per pipeline stage to the run_log table so every
run is auditable - which file ran, how far it got, row counts, and any error.
"""

from datetime import datetime, timezone

from google.cloud import bigquery


class RunLogger:
    def __init__(self, client: bigquery.Client, project_id: str, dataset_id: str = "dod_budget"):
        self.client = client
        self.table_id = f"{project_id}.{dataset_id}.run_log"

    def log(self, file_name: str, stage: str, row_count: int = 0,
             status: str = "SUCCESS", error_message: str = None):
        row = {
            "file_name": file_name,
            "stage": stage,
            "row_count": row_count,
            "status": status,
            "error_message": error_message,
            "logged_at": datetime.now(timezone.utc).isoformat(),
        }
        errors = self.client.insert_rows_json(self.table_id, [row])
        if errors:
            # A logging failure should never take down the actual data pull -
            # surface it in the console instead.
            print(f"  [WARN] failed to write run_log row ({stage}): {errors}")
