"""
config.py
Shared connection settings, used by anything that talks to BigQuery or
the Anthropic API.
"""

import os

from dotenv import load_dotenv

load_dotenv()

GCP_PROJECT_ID = "dod-budget-test"
CREDENTIALS_PATH = "service-account-key.json"
DATASET_ID = "dod_budget"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Model routing for DD1391 pipeline (see CRITERIA.md cost controls):
# Haiku by default everywhere; only escalate to Sonnet for Phase 2 award
# ranking when Haiku's top candidate confidence comes back below 70%.
CLAUDE_HAIKU_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_SONNET_MODEL = "claude-sonnet-5"

# Portable poppler (pdftoppm) - see tools/fetch_poppler.py.
POPPLER_BIN_DIR = os.path.join(os.path.dirname(__file__), "tools", "poppler", "bin")
