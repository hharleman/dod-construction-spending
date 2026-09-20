"""
piid_reference.py
Static reference data for decoding DoD PIIDs (FAR 4.1603 / DFARS 204.1603):
- ISSUING_OFFICE_LOOKUP: issuing-office AAC/DoDAAC (first 6 chars of a PIID)
  -> service_owner + command that issued the instrument. This identifies who
  ISSUED the contract, not who the project belongs to (an Army office can
  issue a contract supporting an Air Force project).
- INSTRUMENT_TYPE_LOOKUP: PIID position-9 type code -> instrument type.

This is the single source of truth. bigquery_setup.py seeds the
piid_issuing_office / piid_issuing_type BigQuery tables from these same
dicts, and piid_decoder.py decodes against them directly (no BigQuery
round-trip needed on every row).
"""

ISSUING_OFFICE_LOOKUP = {
    # Navy / NAVFAC
    "N00178": {"service_owner": "Navy", "command": "Naval Surface Warfare Center Dahlgren Division"},
    "N33191": {"service_owner": "Navy", "command": "NAVFAC Europe Africa Central"},
    "N39430": {"service_owner": "Navy", "command": "NAVFAC Engineering and Expeditionary Warfare Center"},
    "N40080": {"service_owner": "Navy", "command": "NAVFAC Washington"},
    "N40084": {"service_owner": "Navy", "command": "NAVFAC Northwest"},
    "N40085": {"service_owner": "Navy", "command": "NAVFAC Southwest"},
    "N40119": {"service_owner": "Navy", "command": "NAVFAC Europe Africa Central"},
    "N40192": {"service_owner": "Navy", "command": "NAVFAC Marianas"},
    "N44255": {"service_owner": "Navy", "command": "NAVFAC Northwest"},
    "N62463": {"service_owner": "Navy", "command": "NAVFAC Southwest - legacy office code"},
    "N62470": {"service_owner": "Navy", "command": "NAVFAC Southwest - legacy/previous office code"},
    "N62473": {"service_owner": "Navy", "command": "NAVFAC Southwest"},
    "N62478": {"service_owner": "Navy", "command": "NAVFAC Hawaii"},
    "N62479": {"service_owner": "Navy", "command": "NAVFAC Marianas"},
    "N62742": {"service_owner": "Navy", "command": "NAVFAC Pacific"},
    "N69450": {"service_owner": "Navy", "command": "NAVFAC Southeast"},

    # Air Force / Space Force
    "FA2517": {"service_owner": "Air Force/Space Force", "command": "21st Contracting Squadron, Peterson Space Force Base"},
    "FA4460": {"service_owner": "Air Force", "command": "19th Contracting Squadron, Little Rock AFB"},
    "FA4613": {"service_owner": "Air Force", "command": "90th Contracting Squadron, F.E. Warren AFB"},
    "FA4620": {"service_owner": "Air Force", "command": "92nd Contracting Squadron, Fairchild AFB"},
    "FA4625": {"service_owner": "Air Force", "command": "509th Contracting Squadron, Whiteman AFB"},
    "FA5270": {"service_owner": "Air Force", "command": "18th Contracting Squadron, Kadena Air Base"},
    "FA5575": {"service_owner": "Air Force", "command": "496th Air Base Squadron contracting activity, Moron Air Base"},
    "FA5710": {"service_owner": "Air Force", "command": "Air Force contracting activity in Southwest Asia; verify historical office name in DoDAAD"},
    "FA8751": {"service_owner": "Air Force", "command": "Air Force Research Laboratory, Rome Research Site"},
    "FA8903": {"service_owner": "Air Force", "command": "Air Force Civil Engineer Center contracting activity"},
    "FA9101": {"service_owner": "Air Force", "command": "Arnold Engineering Development Complex contracting activity"},

    # Army Corps of Engineers
    "W911KB": {"service_owner": "Army", "command": "USACE Alaska District"},
    "W91236": {"service_owner": "Army", "command": "USACE Norfolk District"},
    "W91238": {"service_owner": "Army", "command": "USACE Sacramento District"},
    "W9126G": {"service_owner": "Army", "command": "USACE Fort Worth District"},
    "W91278": {"service_owner": "Army", "command": "USACE Mobile District"},
    "W9127S": {"service_owner": "Army", "command": "USACE Little Rock District"},
    "W9128A": {"service_owner": "Army", "command": "USACE Honolulu District"},
    "W9128F": {"service_owner": "Army", "command": "USACE Omaha District"},
    "W912BU": {"service_owner": "Army", "command": "USACE Philadelphia District"},
    "W912BV": {"service_owner": "Army", "command": "USACE Tulsa District"},
    "W912DQ": {"service_owner": "Army", "command": "USACE Kansas City District"},
    "W912DR": {"service_owner": "Army", "command": "USACE Baltimore District"},
    "W912DS": {"service_owner": "Army", "command": "USACE New York District"},
    "W912DW": {"service_owner": "Army", "command": "USACE Seattle District"},
    "W912DY": {"service_owner": "Army", "command": "USACE Engineering and Support Center, Huntsville"},
    "W912EP": {"service_owner": "Army", "command": "USACE Jacksonville District"},
    "W912ER": {"service_owner": "Army", "command": "USACE Middle East District"},
    "W912G0": {"service_owner": "Army", "command": "USACE Transatlantic-area contracting activity; historical code, verify in DoDAAD"},
    "W912GB": {"service_owner": "Army", "command": "USACE Europe District"},
    "W912HN": {"service_owner": "Army", "command": "USACE Savannah District"},
    "W912HP": {"service_owner": "Army", "command": "USACE Charleston District"},
    "W912HV": {"service_owner": "Army", "command": "USACE Japan District"},
    "W912PL": {"service_owner": "Army", "command": "USACE Los Angeles District"},
    "W912PM": {"service_owner": "Army", "command": "USACE Wilmington District"},
    "W912PP": {"service_owner": "Army", "command": "USACE Albuquerque District"},
    "W912QR": {"service_owner": "Army", "command": "USACE Louisville District"},
    "W912UM": {"service_owner": "Army", "command": "USACE Far East District"},
    "W912WJ": {"service_owner": "Army", "command": "USACE New England District"},

    # Army and Army National Guard
    "W59XQ3": {"service_owner": "Army", "command": "Army contracting activity; exact historical organization requires DoDAAD confirmation"},
    "W6982A": {"service_owner": "Army", "command": "Army contracting activity; exact historical organization requires DoDAAD confirmation"},
    "W91242": {"service_owner": "Army", "command": "North Carolina National Guard USPFO"},
    "W91243": {"service_owner": "Army", "command": "Nebraska National Guard USPFO"},
    "W912J2": {"service_owner": "Army", "command": "Wisconsin National Guard USPFO"},
    "W912J6": {"service_owner": "Army", "command": "Hawaii National Guard USPFO"},
    "W912JC": {"service_owner": "Army", "command": "Kansas National Guard USPFO"},
    "W912JF": {"service_owner": "Army", "command": "Arkansas National Guard USPFO"},
    "W912WY": {"service_owner": "Army", "command": "Wyoming National Guard USPFO"},
    "W9133L": {"service_owner": "Army", "command": "National Guard Bureau contracting activity"},
    "W91364": {"service_owner": "Army", "command": "Ohio National Guard USPFO"},
    "W91SMC": {"service_owner": "Army", "command": "Illinois National Guard USPFO"},

    # Air National Guard (issued under Army/NGB procurement hierarchy, but the
    # operational unit is Air National Guard - classify service_owner as Air Force)
    "W50S6M": {"service_owner": "Air Force", "command": "Alabama ANG USPFO activity, 117th Air Refueling Wing"},
    "W50S6N": {"service_owner": "Air Force", "command": "Alabama ANG USPFO activity"},
    "W50S6Q": {"service_owner": "Air Force", "command": "Arkansas ANG USPFO activity"},
    "W50S6Y": {"service_owner": "Air Force", "command": "Florida ANG USPFO activity, 125th Fighter Wing"},
    "W50S73": {"service_owner": "Air Force", "command": "Idaho ANG USPFO activity, 124th Fighter Wing"},
    "W50S78": {"service_owner": "Air Force", "command": "Texas ANG USPFO activity, 149th Fighter Wing"},
    "W50S7H": {"service_owner": "Air Force", "command": "Mississippi ANG USPFO activity"},
    "W50S7J": {"service_owner": "Air Force", "command": "Mississippi ANG USPFO activity"},
    "W50S7W": {"service_owner": "Air Force", "command": "Indiana ANG USPFO activity"},
    "W50S85": {"service_owner": "Air Force", "command": "Michigan ANG USPFO activity"},
    "W50S8E": {"service_owner": "Air Force", "command": "New York ANG USPFO activity"},
    "W50S8F": {"service_owner": "Air Force", "command": "New Jersey ANG USPFO activity"},
    "W50S8J": {"service_owner": "Air Force", "command": "New York ANG USPFO activity"},
    "W50S8W": {"service_owner": "Air Force", "command": "Wyoming ANG USPFO activity"},
    "W50S8Z": {"service_owner": "Air Force", "command": "Oregon ANG USPFO activity"},
    "W50S92": {"service_owner": "Air Force", "command": "Pennsylvania ANG USPFO activity"},
    "W50S95": {"service_owner": "Air Force", "command": "South Carolina ANG USPFO activity"},
    "W50S96": {"service_owner": "Air Force", "command": "South Dakota ANG USPFO activity"},
    "W50S98": {"service_owner": "Air Force", "command": "Tennessee ANG USPFO activity"},
    "W50S9E": {"service_owner": "Air Force", "command": "Washington ANG USPFO activity"},
    "W50S9F": {"service_owner": "Air Force", "command": "Wisconsin ANG USPFO activity"},
    "W50S9G": {"service_owner": "Air Force", "command": "Wisconsin ANG USPFO activity"},
    "W50S9H": {"service_owner": "Air Force", "command": "Wisconsin ANG USPFO activity"},

    # Other DoD
    "HQ0034": {"service_owner": "Other", "command": "Washington Headquarters Services, Acquisition Directorate"},

    # Legacy / malformed / nonstandard - not modern FAR PIID issuing-office codes
    "785220": {"service_owner": "Other", "command": "Legacy/nonstandard contract identifier"},
    "AKOFLD": {"service_owner": "Army", "command": "Alaska National Guard legacy local identifier; not a modern FAR PIID"},
    "C09640": {"service_owner": "Other", "command": "Legacy/nonstandard identifier"},
    "MDISAB": {"service_owner": "Army", "command": "Maryland National Guard legacy/local identifier"},
    "MILF11": {"service_owner": "Army", "command": "National Guard legacy/local identifier"},
    "NGB222": {"service_owner": "Army", "command": "National Guard Bureau legacy identifier associated with Mississippi records"},
    "NGKY20": {"service_owner": "Army", "command": "Kentucky National Guard legacy identifier"},
}

# Only B, C, D, and R are the instrument types we care about for
# projects_awarded (task orders, purchase orders, etc. are out of scope).
INSTRUMENT_TYPE_LOOKUP = {
    "B": "Invitation for bids",
    "C": "Contract other than an indefinite-delivery contract",
    "D": "Indefinite-delivery contract",
    "R": "Request for proposals",
}

# Fallback service classification when an issuing_office_code isn't in
# ISSUING_OFFICE_LOOKUP above - matched by PIID prefix. "OTHER" is the
# catch-all used when no prefix matches. See piid_decoder.py.
SERVICE_CODE_LOOKUP = {
    "FA": "Air Force",
    "N": "Navy",
    "W": "Army",
    "OTHER": "Other",
}
