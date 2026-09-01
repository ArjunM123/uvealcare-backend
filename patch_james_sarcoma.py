"""
One-time patch: adds the three missing fields for James T. Okonkwo
(the sarcoma patient) directly to whichever database is currently
configured — respects DATABASE_URL / .env exactly like seed.py and
main.py do, so this safely targets your real hosted Neon database.

Unlike re-running seed.py, this does NOT touch anything else — no other
patient, task, or decision you've created is affected. It only adds
data for fields that are currently missing for this one patient.

Run once with:  python3 patch_james_sarcoma.py
Safe to run again — it skips any field that's already been filled in
(e.g. if you already used "Resolve" in the app for one of these).
"""

from database import SessionLocal
from models import Case, Patient, DataFieldDefinition, DataValue

db = SessionLocal()

NEW_VALUES = {
    "ct_chest": (
        "No pulmonary nodules or evidence of metastatic disease. Clear lung fields bilaterally.",
        "CT Dept.",
    ),
    "biopsy_pathology": (
        "Core needle biopsy: high-grade undifferentiated pleomorphic sarcoma (UPS). "
        "Mitotic rate 15/10 HPF. Necrosis present (~20%).",
        "Pathology Dept.",
    ),
    "molecular_profile": (
        "FISH negative for MDM2 amplification (rules out dedifferentiated liposarcoma). "
        "No targetable fusion identified on next-generation sequencing panel.",
        "Molecular Pathology Lab",
    ),
}


def run():
    case = (
        db.query(Case)
        .join(Patient)
        .filter(Patient.name == "James T. Okonkwo")
        .first()
    )
    if not case:
        print("Couldn't find James T. Okonkwo — has he been seeded yet?")
        return

    field_defs = {
        f.key: f for f in case.disease_profile.field_definitions
    }

    added, skipped = [], []
    for key, (value, source) in NEW_VALUES.items():
        if key not in field_defs:
            print(f"  Warning: field '{key}' isn't defined on this disease profile — skipping.")
            continue

        existing = (
            db.query(DataValue)
            .filter_by(case_id=case.id, field_definition_id=field_defs[key].id)
            .first()
        )
        if existing and existing.status == "complete":
            skipped.append(field_defs[key].label)
            continue

        if existing:
            existing.value = value
            existing.source = source
            existing.status = "complete"
        else:
            db.add(DataValue(
                case_id=case.id,
                field_definition_id=field_defs[key].id,
                value=value,
                status="complete",
                source=source,
            ))
        added.append(field_defs[key].label)

    db.commit()
    print(f"Updated: {', '.join(added) if added else '(none)'}")
    print(f"Already complete, left alone: {', '.join(skipped) if skipped else '(none)'}")


if __name__ == "__main__":
    run()
