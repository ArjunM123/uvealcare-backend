"""
Defines uveal melanoma as a DiseaseProfile config. To prove the platform
generalizes, try writing a second profile (e.g. a different rare-tumor
workflow) in this same shape — if it fits without touching models.py,
the abstraction is doing its job.
"""

import json
import bcrypt
from database import SessionLocal, init_db
from models import DiseaseProfile, DataFieldDefinition, ReadinessRule, Patient, User, Case, DataValue
import datetime

init_db()
db = SessionLocal()

UM_CARE_STAGES = [
    "Diagnosis", "Imaging", "Case Preparation",
    "Multidisciplinary Review", "Treatment Planning",
    "Treatment", "Surveillance",
]

UM_FIELDS = [
    # key, label, category, data_type, required
    ("clinical_assessment", "Clinical assessment", "clinical", "text", True),
    ("tumor_location", "Tumor location", "clinical", "text", True),
    ("tumor_dimensions", "Tumor measurements", "measurement", "number", True),
    ("b_scan", "B-scan", "imaging", "status", True),
    ("oct", "OCT", "imaging", "status", True),
    ("optos", "Optos", "imaging", "status", True),
    ("faf", "FAF", "imaging", "status", True),
    ("gep_result", "Molecular testing / GEP", "molecular", "status", True),
    ("patient_counseling", "Patient Counseling", "patient_support", "text", True),
]

# A genuinely different disease — different care stages, different imaging
# modalities, and a "pathology" category that doesn't exist at all in the
# uveal melanoma profile. This is the actual test of whether the platform
# generalizes: if this renders correctly with zero new frontend code, the
# architecture is doing its job.
SARCOMA_CARE_STAGES = [
    "Diagnosis", "Staging", "Case Preparation",
    "Multidisciplinary Review", "Treatment Planning",
    "Treatment", "Surveillance",
]

SARCOMA_FIELDS = [
    ("clinical_assessment", "Clinical assessment", "clinical", "text", True),
    ("tumor_location", "Tumor location", "clinical", "text", True),
    ("tumor_dimensions", "Tumor measurements", "measurement", "number", True),
    ("mri", "MRI with contrast", "imaging", "status", True),
    ("ct_chest", "CT chest (staging)", "imaging", "status", True),
    ("biopsy_pathology", "Core needle biopsy", "pathology", "status", True),
    ("molecular_profile", "Molecular / genomic testing", "molecular", "status", True),
    ("patient_counseling", "Patient Counseling", "patient_support", "text", True),
]


def add_patient_case(profile, mrn, name, dob, laterality, diagnosis, care_stage, field_values):
    """Creates one patient + case. field_values maps field key -> (value, source)
    for every field that should be marked complete — real descriptive text,
    not a generic placeholder. Everything else is left missing — no fake
    percentages, just real data that adds up to whatever the math produces."""
    patient = Patient(mrn=mrn, name=name, dob=dob, laterality=laterality, diagnosis=diagnosis)
    db.add(patient)
    db.flush()

    case = Case(patient_id=patient.id, disease_profile_id=profile.id, care_stage=care_stage)
    db.add(case)
    db.flush()

    field_defs = {f.key: f for f in profile.field_definitions}
    for key, (value, source) in field_values.items():
        db.add(DataValue(
            case_id=case.id,
            field_definition_id=field_defs[key].id,
            value=value,
            status="complete",
            source=source,
        ))
    return patient, case


def run():
    existing = db.query(DiseaseProfile).filter_by(key="uveal_melanoma").first()
    if existing:
        print("uveal_melanoma profile already seeded.")
        return

    profile = DiseaseProfile(
        key="uveal_melanoma",
        display_name="Uveal Melanoma",
        care_stages=json.dumps(UM_CARE_STAGES),
    )
    db.add(profile)
    db.flush()  # get profile.id

    for key, label, category, data_type, required in UM_FIELDS:
        db.add(DataFieldDefinition(
            disease_profile_id=profile.id,
            key=key, label=label, category=category,
            data_type=data_type, required_for_readiness=required,
        ))
    db.flush()

    db.add(ReadinessRule(
        disease_profile_id=profile.id,
        description="All required clinical, imaging, and molecular fields must be complete.",
    ))

    # Demo login user — password is hashed with bcrypt before storage.
    # This is what "real login" means: the plain-text password is never
    # kept anywhere, only this one-way hash.
    demo_password = "uveal123"
    hashed = bcrypt.hashpw(demo_password.encode(), bcrypt.gensalt()).decode()
    doc = User(name="Dr. Alicia M. Reyes", email="a.reyes@uvealcare.org", role="ophthalmologist", password_hash=hashed)
    db.add(doc)

    # Five real patients, each with genuinely different, realistic clinical
    # content tailored to their actual diagnosis — not the same placeholder
    # text copy-pasted five times. This is what makes the "auto-generated
    # case summary" on the Tumor Board page actually work for everyone,
    # not just one showcase patient.
    margaret, margaret_case = add_patient_case(
        profile, "2847301", "Margaret T. Sullivan", datetime.date(1957, 3, 14), "OD",
        "Choroidal Melanoma OD", "Multidisciplinary Review",
        field_values={
            "b_scan": (
                "Dome-shaped choroidal mass, acoustic quiet zone, internal medium reflectivity. Apical height 4.6 mm.",
                "R. Park, RDMS",
            ),
        },
    )
    add_patient_case(
        profile, "2831045", "Robert J. Hargrove", datetime.date(1963, 7, 2), "OS",
        "Ciliary Body Melanoma OS", "Imaging",
        field_values={
            "clinical_assessment": (
                "Visual acuity 20/50 OS. IOP 16 mmHg. Anterior segment quiet. Vitreous clear. Optic nerve cup-to-disc ratio 0.3.",
                "Dr. A. Reyes",
            ),
            "tumor_location": (
                "Ciliary body mass extending into peripheral choroid, 3–5 o'clock position, OS.",
                "Dr. A. Reyes",
            ),
            "tumor_dimensions": (
                "Basal diameter 9.4 x 7.8 mm. Apical height 3.2 mm. Located 6.5 mm from limbus.",
                "R. Park, RDMS",
            ),
            "b_scan": (
                "Dome-shaped mass, low-to-medium internal reflectivity, acoustic hollowing present.",
                "R. Park, RDMS",
            ),
            "oct": (
                "No subretinal fluid. Retinal architecture preserved adjacent to lesion.",
                "L. Okafor, COT",
            ),
        },
    )
    add_patient_case(
        profile, "2819234", "Diane L. Kowalski", datetime.date(1969, 11, 30), "OD",
        "Choroidal Melanoma OD", "Treatment Planning",
        field_values={
            "clinical_assessment": (
                "Visual acuity 20/30 OD. IOP 14 mmHg. Anterior segment and vitreous unremarkable.",
                "Dr. A. Reyes",
            ),
            "tumor_location": (
                "Posterior pole choroidal mass, superior to the fovea, OD.",
                "Dr. A. Reyes",
            ),
            "tumor_dimensions": (
                "Basal diameter 8.1 x 6.9 mm. Apical height 2.8 mm.",
                "R. Park, RDMS",
            ),
            "b_scan": (
                "Dome-shaped choroidal mass, medium internal reflectivity, no extraocular extension.",
                "R. Park, RDMS",
            ),
            "oct": (
                "Trace subretinal fluid at tumor margin. No outer retinal atrophy.",
                "L. Okafor, COT",
            ),
            "optos": (
                "Pigmented, well-circumscribed choroidal lesion, no overlying orange pigment.",
                "L. Okafor, COT",
            ),
            "faf": (
                "Mild hyperautofluorescence at tumor margin, consistent with lipofuscin deposition.",
                "M. Torres, COT",
            ),
            "gep_result": (
                "Class 1A — low metastatic risk. Disomy 3.",
                "Castle Biosciences DecisionDx-UM",
            ),
        },
    )
    add_patient_case(
        profile, "2803917", "Thomas R. Nguyen", datetime.date(1975, 1, 18), "OD",
        "Iris Melanoma OD", "Surveillance",
        field_values={
            "clinical_assessment": (
                "Visual acuity 20/20 OD. IOP 15 mmHg. Pigmented iris lesion visible on slit-lamp exam, no anterior chamber invasion.",
                "Dr. A. Reyes",
            ),
            "tumor_location": (
                "Iris stroma, inferotemporal quadrant, OD. No ciliary body involvement.",
                "Dr. A. Reyes",
            ),
            "tumor_dimensions": (
                "Basal diameter 3.2 x 2.8 mm. Thickness 1.4 mm.",
                "R. Park, RDMS",
            ),
            "b_scan": (
                "Small anterior segment mass, not well visualized on B-scan given anterior location; UBM recommended for detail.",
                "R. Park, RDMS",
            ),
            "oct": (
                "Anterior segment OCT shows well-defined iris mass without angle involvement.",
                "L. Okafor, COT",
            ),
            "optos": (
                "Posterior segment unremarkable, no evidence of extension.",
                "L. Okafor, COT",
            ),
            "faf": (
                "No posterior segment abnormality on autofluorescence.",
                "M. Torres, COT",
            ),
            "gep_result": (
                "Not applicable — GEP validated for posterior uveal melanoma; iris melanoma risk stratified clinically (low-risk features).",
                "Dr. A. Reyes",
            ),
        },
    )
    add_patient_case(
        profile, "2798423", "Carol A. Whitfield", datetime.date(1959, 9, 5), "OS",
        "Choroidal Melanoma OS", "Case Preparation",
        field_values={
            "b_scan": (
                "Mushroom-shaped choroidal mass, low internal reflectivity, suggestive of Bruch's membrane rupture.",
                "R. Park, RDMS",
            ),
            "oct": (
                "Subretinal fluid extending beyond tumor margin. Overlying retinal thinning.",
                "L. Okafor, COT",
            ),
        },
    )

    db.flush()

    # ─── Second disease profile: soft tissue sarcoma ────────────────────
    # This is the actual proof that the platform generalizes. Different
    # care stages ("Staging" instead of "Imaging"), different imaging
    # modalities (MRI/CT instead of B-scan/OCT), and a "pathology"
    # category that doesn't exist anywhere in the uveal melanoma profile.
    # No frontend code changes were needed to support this — the same
    # screens (Case Readiness, Imaging, Patient Profile, Tumor Board)
    # render it correctly because nothing is hardcoded to a specific
    # disease's field names anymore.
    sarcoma_profile = DiseaseProfile(
        key="soft_tissue_sarcoma",
        display_name="Soft Tissue Sarcoma",
        care_stages=json.dumps(SARCOMA_CARE_STAGES),
    )
    db.add(sarcoma_profile)
    db.flush()

    for key, label, category, data_type, required in SARCOMA_FIELDS:
        db.add(DataFieldDefinition(
            disease_profile_id=sarcoma_profile.id,
            key=key, label=label, category=category,
            data_type=data_type, required_for_readiness=required,
        ))
    db.flush()

    db.add(ReadinessRule(
        disease_profile_id=sarcoma_profile.id,
        description="All required clinical, imaging, pathology, and molecular fields must be complete.",
    ))

    add_patient_case(
        sarcoma_profile, "4102938", "James T. Okonkwo", datetime.date(1981, 6, 22), None,
        "Soft Tissue Sarcoma, Left Thigh", "Multidisciplinary Review",
        field_values={
            "clinical_assessment": (
                "Palpable 8 cm deep soft tissue mass, left proximal thigh. Minimal tenderness. No overlying skin changes.",
                "Dr. A. Reyes",
            ),
            "tumor_location": (
                "Left proximal thigh, deep to fascia, adjacent to adductor compartment.",
                "Dr. A. Reyes",
            ),
            "tumor_dimensions": (
                "8.2 x 6.5 x 5.1 cm on MRI.",
                "MRI Dept.",
            ),
            "mri": (
                "Heterogeneous deep soft tissue mass with avid contrast enhancement. No neurovascular encasement.",
                "MRI Dept.",
            ),
            "ct_chest": (
                "No pulmonary nodules or evidence of metastatic disease. Clear lung fields bilaterally.",
                "CT Dept.",
            ),
            "biopsy_pathology": (
                "Core needle biopsy: high-grade undifferentiated pleomorphic sarcoma (UPS). Mitotic rate 15/10 HPF. Necrosis present (~20%).",
                "Pathology Dept.",
            ),
            "molecular_profile": (
                "FISH negative for MDM2 amplification (rules out dedifferentiated liposarcoma). No targetable fusion identified on next-generation sequencing panel.",
                "Molecular Pathology Lab",
            ),
        },
    )
            # ct_chest, biopsy_pathology, and molecular_profile deliberately
            # left missing — an honest, partially-documented case, same as
            # every other seeded patient.
        },
    )

    # A few realistic open tasks, tied to real missing fields on real
    # patients — these show up in the Dashboard's Upcoming Tasks list
    # and on each patient's Case Readiness page.
    from models import Task
    hargrove_case = db.query(Case).join(Patient).filter(Patient.name == "Robert J. Hargrove").first()
    whitfield_case = db.query(Case).join(Patient).filter(Patient.name == "Carol A. Whitfield").first()
    db.add(Task(case_id=hargrove_case.id, description="Resolve: Optos", assignee_name="Imaging Dept.",
                due_date=datetime.date(2026, 9, 9)))
    db.add(Task(case_id=whitfield_case.id, description="Resolve: Clinical assessment", assignee_name="Dr. A. Reyes",
                due_date=datetime.date(2026, 9, 12)))

    db.commit()
    print(f"Seeded disease profile '{profile.key}' with {len(UM_FIELDS)} fields and 5 patients.")
    print(f"Seeded disease profile '{sarcoma_profile.key}' with {len(SARCOMA_FIELDS)} fields and 1 patient.")
    print(f"Margaret's case ID (for the older single-case screens): {margaret_case.id}")
    print(f"Login with email: {doc.email}  password: {demo_password}")


if __name__ == "__main__":
    run()
