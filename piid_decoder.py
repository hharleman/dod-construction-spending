"""
piid_decoder.py
Decodes a DoD PIID (the Award ID) per FAR 4.1603 / DFARS 204.1603:
  positions 1-6  = issuing-office AAC/DoDAAC
  position  9    = instrument-type code

Important: the issuing office identifies who ISSUED the contract, not who
the project belongs to. Do not infer project owner/base/UIC from it.

office_lookup/type_lookup/service_code_lookup are passed in (see
piid_lookup.py) rather than imported statically, so the decode always
reflects whatever is currently in the piid_issuing_office /
piid_issuing_type / piid_service_code BigQuery tables.
"""

import re


def _fallback_service(office_code: str, service_code_lookup: dict) -> str:
    """When office_code isn't a known issuing office, classify by PIID
    prefix (longest prefix first, so e.g. "FA" is checked before a bare "F"
    would be) using the piid_service_code table; "OTHER" is the catch-all."""
    if office_code:
        prefixes = sorted((p for p in service_code_lookup if p != "OTHER"), key=len, reverse=True)
        for prefix in prefixes:
            if office_code.startswith(prefix):
                return service_code_lookup[prefix]
    return service_code_lookup.get("OTHER", "Other")


def decode_piid(raw_piid, office_lookup: dict, type_lookup: dict, service_code_lookup: dict) -> dict:
    """office_lookup: issuing_office_code -> service_owner (string)."""
    raw = "" if raw_piid is None else str(raw_piid)
    piid = re.sub(r"[-\s]", "", raw.upper().strip())

    office_code = piid[0:6] if len(piid) >= 6 else None
    type_code = piid[8] if len(piid) >= 9 else None

    service_owner = office_lookup.get(office_code) or _fallback_service(office_code, service_code_lookup)

    return {
        "service": service_owner,
        "type_code": type_code,
        "type": type_lookup.get(type_code),
    }
