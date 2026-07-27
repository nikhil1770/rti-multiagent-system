"""Canonical department + section definitions.

The ingestion script uses this to know which DOCX files to look for.
Name each DOCX <section_key>.docx and drop it in:
    data/departments/<dept_key>/source/

No department numbering — departments are keyed by name.
"""

# dept_key -> {"title", "sections": [(section_key, title, topic)]}
DEPARTMENTS = {
    "election_commission": {
        "title": "Election Commission of India - Political Finance Compliance Cell",
        "sections": [
            ("donation_reports",       "Donation / contribution reports",   "donations"),
            ("cpio_appellate_notices", "CPIO & appellate authority notices", "rti_designation"),
            ("disclosure_guidelines",  "Disclosure guidelines",             "guidelines"),
        ],
    },
    "supreme_court": {
        "title": "Supreme Court of India - CPIO Office",
        "sections": [
            ("judicial_admin_resolutions", "Judicial administrative resolutions", "resolutions"),
            ("compliance_status_notes",    "Compliance status notes",             "compliance"),
            ("rti_applicability_guidance", "RTI applicability guidance",          "guidelines"),
        ],
    },
    "mha_pmo": {
        "title": "Ministry of Home Affairs / PMO - Records & Archives",
        "sections": [
            ("classified_file_indexes",       "Classified file indexes",              "file_index"),
            ("intergovt_correspondence_notes","Inter-governmental correspondence notes","correspondence"),
            ("exemption_guidelines_sec8",     "Exemption guidelines (Section 8)",     "guidelines"),
        ],
    },
    "food_civil_supplies": {
        "title": "Delhi Dept. of Food, Civil Supplies & Consumer Affairs",
        "sections": [
            ("stock_received_records", "Stock received records", "stock"),
            ("distribution_records",   "Distribution records",   "distribution"),
            ("cardholder_lists",       "Cardholder lists",        "cardholders"),
            ("pds_guidelines",         "General PDS guidelines",  "guidelines"),
        ],
    },
    "ministry_defence": {
        "title": "Ministry of Defence - Land Records",
        "sections": [
            ("land_use_allotment_orders", "Land use / allotment orders",       "land_use"),
            ("noc_construction_clearance","NOC / construction clearance records","clearance"),
            ("land_allotment_policy",     "Land allotment policy",             "guidelines"),
        ],
    },
    "municipal_corporation": {
        "title": "Municipal Corporation - Building Approvals",
        "sections": [
            ("original_sanction_records", "Original sanction records", "sanction"),
            ("revised_sanction_records",  "Revised sanction records",  "sanction"),
            ("building_bylaws",           "Building bylaws",           "guidelines"),
        ],
    },
    "state_environment": {
        "title": "State Environment Department",
        "sections": [
            ("clearance_applications",     "Clearance applications",       "applications"),
            ("clearance_status_decisions", "Clearance status / decisions", "decisions"),
            ("crz_general_guidelines",     "CRZ / general guidelines",     "guidelines"),
        ],
    },
}
