"""
Patches main.py in place to add the patient journey endpoints:

    GET  /cases/{id}/patient-journey            (add ?preview=true for staff preview)
    POST /cases/{id}/patient-journey/release    {"released": true|false}

Run from inside your uvealcare-backend folder (same folder as main.py and
patient_journey.py):

    python3 patch_patient_journey_backend.py

Needs NO database migration: the "Results Shared With Patient" field is
created automatically the first time a clinician presses Share.
Independent of the AJCC and GEP patches: apply in any order. Safe to run twice.
"""
import os
import sys

if not os.path.exists("patient_journey.py"):
    print("FAIL  patient_journey.py is not in this folder. Put it next to main.py first.")
    sys.exit(1)

content = open("main.py", "r", encoding="utf-8").read()
original = content

MARKER = "# uvealcare: patient journey"

BLOCK = '''# uvealcare: patient journey
# Plain-language walk through the whole diagnosis-to-surveillance path, built
# from the findings already recorded on the case (see patient_journey.py for
# the rules). Interpretive "what we found" lines are held back until a
# clinician presses Share; staff can preview them with ?preview=true.
RELEASE_FIELD_KEY = "patient_results_released"


def _journey_inputs(case: Case, db: Session):
    rows = (
        db.query(DataValue, DataFieldDefinition)
        .join(DataFieldDefinition, DataValue.field_definition_id == DataFieldDefinition.id)
        .filter(DataValue.case_id == case.id, DataValue.status == "complete")
        .all()
    )
    values = {}
    basal = apical = None
    newest = None
    for dv, fd in rows:
        if dv.value and dv.value.strip():
            values[fd.key] = dv.value.strip()
        if fd.category == "measurement" and (dv.basal_diameter_mm or dv.apical_height_mm):
            if newest is None or (dv.recorded_at and newest.recorded_at and dv.recorded_at > newest.recorded_at):
                newest = dv
    if newest is not None:
        basal, apical = newest.basal_diameter_mm, newest.apical_height_mm
    decision = db.query(Decision).filter_by(case_id=case.id).first()
    decision_dict = None
    if decision:
        decision_dict = {
            "recommendation": decision.recommendation,
            "surveillance_protocol": decision.surveillance_protocol,
            "follow_up_date": decision.follow_up_date.isoformat() if decision.follow_up_date else None,
        }
    released = values.get(RELEASE_FIELD_KEY, "").strip().lower() in ("yes", "true")
    return values, basal, apical, decision_dict, released


@app.get("/cases/{case_id}/patient-journey")
def get_patient_journey(case_id: str, preview: bool = False, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")
    values, basal, apical, decision_dict, released = _journey_inputs(case, db)
    journey = patient_journey.build_journey(
        disease_key=case.disease_profile.key,
        diagnosis=case.patient.diagnosis,
        laterality=case.patient.laterality,
        care_stage=case.care_stage,
        values=values,
        basal_mm=basal,
        apical_mm=apical,
        decision=decision_dict,
        released=released,
        preview=preview,
    )
    return {"case_id": case_id, "journey": journey}


class JourneyReleaseIn(BaseModel):
    released: bool


@app.post("/cases/{case_id}/patient-journey/release")
def set_patient_journey_release(case_id: str, payload: JourneyReleaseIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Clinician switch: share (or stop sharing) the detailed plain-language
    findings with the patient. Creates the tracking field on first use."""
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")
    field_def = db.query(DataFieldDefinition).filter_by(
        disease_profile_id=case.disease_profile_id, key=RELEASE_FIELD_KEY
    ).first()
    if not field_def:
        field_def = DataFieldDefinition(
            disease_profile_id=case.disease_profile_id,
            key=RELEASE_FIELD_KEY,
            label="Results Shared With Patient",
            category="patient_support",
            data_type="text",
            required_for_readiness=False,
        )
        db.add(field_def)
        db.flush()
    value = db.query(DataValue).filter_by(case_id=case_id, field_definition_id=field_def.id).first()
    text = "Yes" if payload.released else "No"
    if value:
        value.value = text
        value.status = "complete"
        value.source = current_user.name
    else:
        db.add(DataValue(case_id=case_id, field_definition_id=field_def.id,
                         value=text, status="complete", source=current_user.name))
    db.commit()
    return {"ok": True, "case_id": case_id, "released": payload.released}


'''

if MARKER in content:
    print("  SKIP  patient journey endpoints (already applied)")
else:
    anchor = '@app.get("/disease-profiles")'
    n = content.count(anchor)
    if n != 1:
        print(f"  FAIL  endpoints: expected exactly 1 match for the insertion point, found {n}")
        print("        Your main.py differs from what this patch expects.")
        sys.exit(1)
    content = content.replace(anchor, BLOCK + anchor, 1)
    print("  OK    patient journey endpoints")

if "import patient_journey" not in content:
    first_import = "import datetime as dt"
    if content.count(first_import) < 1:
        print("  FAIL  import anchor 'import datetime as dt' not found")
        sys.exit(1)
    content = content.replace(first_import, first_import + "\nimport patient_journey", 1)
    print("  OK    import patient_journey")
else:
    print("  SKIP  import (already applied)")

if content == original:
    print("\nNothing changed.")
    sys.exit(0)
open("main.py", "w", encoding="utf-8").write(content)
print("\nDone - main.py patched.")
