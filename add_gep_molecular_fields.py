"""
Adds two molecular/prognostic fields to uveal_melanoma: gep_class (the
DecisionDx-UM gene expression profile result — Class 1A, 1B, or 2) and
chromosome_3_status (disomy/monosomy 3). AJCC now recommends GEP testing
for essentially all uveal melanoma patients, and it's the single number
that most drives how aggressively a patient gets followed for metastasis
afterward — a completely different axis from TFSOM-UHHD, which estimates
a NEVUS's risk of becoming melanoma in the first place, not an already-
diagnosed melanoma's risk of spreading.

Stored as plain text fields (not constrained dropdowns) so you can record
whatever the lab report actually says — main.py's _compute_gep_risk()
normalizes common spellings ("Class 1A", "1a", "1A") when mapping to the
test's published risk tier.

required_for_readiness=False: most cases won't have this until tissue is
obtained (biopsy, or after enucleation/plaque placement), so an absent
result is the normal state, not something that should block tumor board
review the way missing imaging does.

Added ONLY to uveal_melanoma — this is melanoma-specific molecular
testing, not relevant to sarcoma.

Safe to run more than once — skips any field that already exists.

Run with:  python3 add_gep_molecular_fields.py
"""

from database import SessionLocal
from models import DiseaseProfile, DataFieldDefinition

db = SessionLocal()

# key, label
GEP_FIELDS = [
    ("gep_class", "GEP Class (DecisionDx-UM)"),
    ("chromosome_3_status", "Chromosome 3 Status"),
]


def run():
    profile = db.query(DiseaseProfile).filter_by(key="uveal_melanoma").first()
    if not profile:
        print("No uveal_melanoma disease profile found — nothing to do.")
        return

    for key, label in GEP_FIELDS:
        existing = (
            db.query(DataFieldDefinition)
            .filter_by(disease_profile_id=profile.id, key=key)
            .first()
        )
        if existing:
            print(f"  uveal_melanoma: {key} already exists — skipping.")
            continue

        db.add(DataFieldDefinition(
            disease_profile_id=profile.id,
            key=key,
            label=label,
            category="molecular",
            data_type="text",
            required_for_readiness=False,
        ))
        print(f"  uveal_melanoma: added {label}.")

    db.commit()
    print("Done.")


if __name__ == "__main__":
    run()
