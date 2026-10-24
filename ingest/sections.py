"""Canonical department + section definitions.

The ingestion script uses this to know which source files to look for.
Name each source file <section_key>.docx (or <dept>_<section_key>.docx) and drop
it in:
    data/departments/<dept_key>/source/

Then run:
    python ingest/build_records.py     # source -> records/*.json
    python ingest/embed.py             # records -> vector index

Design: 2 NCT-Delhi departments, 4 subsections each, so a single RTI can plausibly
route across both. A separate exemption-precedent corpus (CIC decisions) lives
OUTSIDE data/departments/ at data/exemption_precedent/ — it feeds exemption
screening, not retrieval, so it is intentionally not a department here.
"""

# dept_key -> {"title", "sections": [(section_key, title, topic)]}
DEPARTMENTS = {
    "mcd_building": {
        "title": "Municipal Corporation of Delhi - Building Approvals",
        "sections": [
            ("plan_sanction",            "Building plan sanction",                    "sanction"),
            ("occupancy_certificate",    "Occupancy / completion certificate",        "occupancy"),
            ("unauthorised_construction","Unauthorised construction & regularisation","enforcement"),
            ("property_tax",             "Property tax",                              "tax"),
        ],
    },
    "delhi_food": {
        "title": "Delhi Dept. of Food, Civil Supplies & Consumer Affairs",
        "sections": [
            ("ration_card",  "Ration card eligibility & issuance", "ration"),
            ("fps_licensing","Fair price shop licensing",          "licensing"),
            ("pds_stock",    "PDS stock allocation & lifting",     "stock"),
            ("grievance",    "Grievance redressal",                "grievance"),
        ],
    },
}
