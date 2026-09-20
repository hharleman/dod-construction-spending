"""
project_classifier.py
Best-effort extraction from an award description:
- project_id: the P-number (e.g. "P-209", "P440" -> "P209", "P440"),
  leading zeros stripped, "" if none found. When a description lists
  multiple P-numbers (e.g. "P440 / 441"), the first is used.
- project_type: a coarse building-type tag (CDC / BEQ / Open Bay /
  Barracks / Housing), "" if nothing matches.
"""

import re

_P_NUMBER_PATTERN = re.compile(r"\bP-?(\d{3,4})\b", re.IGNORECASE)

# Checked in order - more specific labels (CDC, BEQ, Open Bay) before the
# generic Barracks/Housing catch-alls.
_PROJECT_TYPE_PATTERNS = [
    ("CDC", re.compile(r"child development center|\bcdc\b", re.IGNORECASE)),
    ("BEQ", re.compile(r"\bbeq\b|bachelor enlisted quarters", re.IGNORECASE)),
    ("Open Bay", re.compile(r"open bay", re.IGNORECASE)),
    ("Barracks", re.compile(r"barracks", re.IGNORECASE)),
    ("Housing", re.compile(r"family housing|military housing|unaccompanied housing|\bhousing\b", re.IGNORECASE)),
]


def extract_project_id(description) -> str:
    if not isinstance(description, str) or not description:
        return ""
    match = _P_NUMBER_PATTERN.search(description)
    if not match:
        return ""
    digits = match.group(1).lstrip("0") or "0"
    return f"P{digits}"


def classify_project_type(description) -> str:
    if not isinstance(description, str) or not description:
        return ""
    for label, pattern in _PROJECT_TYPE_PATTERNS:
        if pattern.search(description):
            return label
    return ""
