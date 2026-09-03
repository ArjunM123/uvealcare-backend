"""
Adds a "Patient Counseling" field to every existing disease profile.

This exists because a real vitreoretinal surgeon, when asked what part of
case preparation is most time-consuming, said patient counseling — not
data collection. That's a real workflow step this platform never
tracked at all. This script adds it as a required-for-readiness field,
the same way any other clinical field is tracked, so an un-counseled
patient genuinely shows up as a readiness gap.

Safe to run more than once — skips any profile that already has this
field defined.

Run with:  python3 add_patient_counseling_field.py
"""

from database import SessionLocal
from models import DiseaseProfile, DataFieldDefinition

db = SessionLocal()


def run():
    profiles = db.query(DiseaseProfile).all()
    for profile in profiles:
        existing = (
            db.query(DataFieldDefinition)
            .filter_by(disease_profile_id=profile.id, key="patient_counseling")
            .first()
        )
        if existing:
            print(f"  {profile.key}: patient_counseling already exists — skipping.")
            continue

        db.add(DataFieldDefinition(
            disease_profile_id=profile.id,
            key="patient_counseling",
            label="Patient Counseling",
            category="patient_support",
            data_type="text",
            required_for_readiness=True,
        ))
        print(f"  {profile.key}: added Patient Counseling field.")

    db.commit()
    print("Done.")


if __name__ == "__main__":
    run()
