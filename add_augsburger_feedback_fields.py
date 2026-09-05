"""
Adds four fields directly requested by a real ocular oncologist's
critique of what's needed before uveal melanoma treatment planning:
fellow eye visual status, baseline metastatic workup, prior treatment
history, and psychosocial/treatment-acceptance factors.

fellow_eye_status is added ONLY to uveal_melanoma, since "fellow eye"
is specific to an eye disease. The other three are general oncology
concepts and apply to every disease profile.

Safe to run more than once — skips any field that already exists.

Run with:  python3 add_augsburger_feedback_fields.py
"""

from database import SessionLocal
from models import DiseaseProfile, DataFieldDefinition

db = SessionLocal()

# key, label, category, data_type, only_for_profile_key (None = all profiles)
NEW_FIELDS = [
    ("fellow_eye_status", "Fellow Eye Visual Status", "clinical", "text", "uveal_melanoma"),
    ("metastatic_workup", "Baseline Metastatic Workup", "systemic_workup", "text", None),
    ("prior_treatment", "Prior Treatment History", "clinical", "text", None),
    ("psychosocial_factors", "Psychosocial / Treatment Acceptance Factors", "patient_support", "text", None),
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
                required_for_readiness=True,
            ))
            print(f"  {profile.key}: added {label}.")

    db.commit()
    print("Done.")


if __name__ == "__main__":
    run()
