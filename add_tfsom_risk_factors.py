"""
Adds the 8 TFSOM-UHHD risk factors Dr. Ebrahimiadib asked to have tracked
per melanoma case: Thickness >2mm, subretinal Fluid, Symptoms, Orange
pigment, Margin <=3mm from the optic disc, Ultrasonographic hollowness,
Halo absent, Drusen absent. These are the published predictors (Shields
et al., cross-checked against EyeWiki and Retina Today) of a choroidal
nevus's 5-year risk of transforming into melanoma — 0 factors is ~3%
risk, 1 factor is ~38%, 2+ factors is >50%.

Stored as ordinary boolean DataFieldDefinitions, which means they get the
app's existing record/edit/delete/audit UI for free (see
CaseReadinessScreen in the frontend) — no new screen required. main.py's
_compute_tfsom_risk() tallies whichever of these have actually been
assessed into a risk score, surfaced on the case packet, the case
readiness checklist, and as a population-view flag.

Added ONLY to uveal_melanoma — TFSOM-UHHD is specific to choroidal nevus/
melanoma risk and has no meaning for sarcoma.

required_for_readiness=False: these are real clinical findings worth
tracking, but a case shouldn't be blocked from tumor board review just
because risk-factor documentation is incomplete, the same reasoning
already used for the post-treatment fields below.

Safe to run more than once — skips any field that already exists.

Run with:  python3 add_tfsom_risk_factors.py
"""

from database import SessionLocal
from models import DiseaseProfile, DataFieldDefinition

db = SessionLocal()

# key, label
TFSOM_FIELDS = [
    ("tfsom_thickness", "TFSOM: Thickness >2mm"),
    ("tfsom_fluid", "TFSOM: Subretinal Fluid"),
    ("tfsom_symptoms", "TFSOM: Symptoms"),
    ("tfsom_orange_pigment", "TFSOM: Orange Pigment"),
    ("tfsom_margin", "TFSOM: Margin \u22643mm to Disc"),
    ("tfsom_ultrasound_hollow", "TFSOM: Ultrasonographic Hollowness"),
    ("tfsom_no_halo", "TFSOM: Halo Absent"),
    ("tfsom_no_drusen", "TFSOM: Drusen Absent"),
]


def run():
    profile = db.query(DiseaseProfile).filter_by(key="uveal_melanoma").first()
    if not profile:
        print("No uveal_melanoma disease profile found — nothing to do.")
        return

    for key, label in TFSOM_FIELDS:
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
            category="risk_factor",
            data_type="boolean",
            required_for_readiness=False,
        ))
        print(f"  uveal_melanoma: added {label}.")

    db.commit()
    print("Done.")


if __name__ == "__main__":
    run()
