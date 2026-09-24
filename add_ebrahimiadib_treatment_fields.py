"""
Adds four post-treatment/follow-up fields directly requested by a real
vitreoretinal surgeon after reviewing the live platform: date of
surgery, date of radiation, number of injections, and radiation
retinopathy.

These are tracked but NOT required_for_readiness — the app doesn't yet
have stage-aware required fields, so marking these required would
unfairly show early-stage patients (who haven't been treated yet) as
"incomplete" for something that genuinely isn't relevant to them yet.

date_of_surgery and date_of_radiation apply to BOTH disease profiles,
since both uveal melanoma and sarcoma patients can have surgery and
radiation. number_of_injections and radiation_retinopathy are added
ONLY to uveal_melanoma, since radiation retinopathy is a retina-specific
complication that doesn't apply to sarcoma.

Safe to run more than once — skips any field that already exists.

Run with:  python3 add_ebrahimiadib_treatment_fields.py
"""

from database import SessionLocal
from models import DiseaseProfile, DataFieldDefinition

db = SessionLocal()

# key, label, category, data_type, only_for_profile_key (None = all profiles)
NEW_FIELDS = [
    ("date_of_surgery", "Date of Surgery", "treatment", "date", None),
    ("date_of_radiation", "Date of Radiation", "treatment", "date", None),
    ("number_of_injections", "Number of Injections", "treatment", "number", "uveal_melanoma"),
    ("radiation_retinopathy", "Radiation Retinopathy", "treatment", "text", "uveal_melanoma"),
]


def run():
    profiles = db.query(DiseaseProfile).all()
    for key, label, category, data_type, only_for in NEW_FIELDS:
        for profile in profiles:
            if only_for and profile.key != only_for:
                continue

            existing = (
                db.query(DataFieldDefinition)
                .filter_by(disease_profile_id=profile.id, key=key)
                .first()
            )
            if existing:
                print(f"  {profile.key}: {key} already exists — skipping.")
                continue

            db.add(DataFieldDefinition(
                disease_profile_id=profile.id,
                key=key,
                label=label,
                category=category,
                data_type=data_type,
                required_for_readiness=False,  # tracked, but doesn't penalize pre-treatment patients
            ))
            print(f"  {profile.key}: added {label}.")

    db.commit()
    print("Done.")


if __name__ == "__main__":
    run()
