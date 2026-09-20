"""
bigquery_loader.py
Purpose: Load DataFrames into existing BigQuery tables.
Deliberately does NOT create/alter datasets or tables - that is
bigquery_setup.py's job, run manually. This file only ever writes data
against the fixed schema defined in schema.py.
"""

import os

from google.cloud import bigquery


class BigQueryLoader:
    def __init__(self, project_id: str, credentials_path: str, dataset_id: str = "dod_budget"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path

        self.client = bigquery.Client(project=project_id)
        self.project_id = project_id
        self.dataset_id = dataset_id

    def load_dataframe_to_table(self, df, table_name: str, schema) -> int:
        """
        Replace a table's data with df, against the fixed schema (no
        autodetect - table structure is owned by bigquery_setup.py).

        If df is empty, the table is left untouched and 0 is returned -
        these tables re-fetch their full window every run, so an empty
        result almost always means an upstream problem, not "no new data".
        Wiping the table in that case would destroy good data.
        """
        if df.empty:
            return 0

        table_id = f"{self.project_id}.{self.dataset_id}.{table_name}"
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition="WRITE_TRUNCATE",
        )
        load_job = self.client.load_table_from_dataframe(df, table_id, job_config=job_config)
        load_job.result()

        return len(df)
