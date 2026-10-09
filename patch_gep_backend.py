"""
Patches main.py in place to add GEP/molecular metastatic-risk tracking
(DecisionDx-UM Class 1A/1B/2 + chromosome 3 status), on top of the
TFSOM-UHHD patch already applied.

Run this from inside your uvealcare-backend repo folder (same folder as
main.py):

    python3 patch_gep_backend.py

Safe to run only once; running it twice will fail cleanly (it checks
each change isn't already applied). Requires patch_tfsom_backend.py to
have already been run — it anchors onto the TFSOM endpoint it added.
"""
import sys

with open("main.py", "r", encoding="utf-8") as f:
    content = f.read()

original = content


def apply(label, old, new, content):
    if new in content:
        print(f"  SKIP  {label} (already applied)")
        return content
    count = content.count(old)
    if count != 1:
        print(f"  FAIL  {label}: expected exactly 1 match, found {count}")
        print("        Your main.py may differ from what this patch expects")
        print("        (did patch_tfsom_backend.py run successfully first?).")
        sys.exit(1)
    print(f"  OK    {label}")
    return content.replace(old, new, 1)


GEP_BLOCK = '''


# Gene expression profiling (DecisionDx-UM is the test in near-universal
# use) classifies a tumor's METASTATIC risk — a completely different axis
# from TFSOM (which estimates a NEVUS's risk of becoming melanoma in the
# first place). AJCC now recommends GEP testing for essentially all uveal
# melanoma patients. Class 1A = low risk, Class 1B = intermediate/
# long-term risk, Class 2 = high, near-term risk. This mapping is the
# test's own published classification, not a derived clinical judgment —
# unlike surveillance-interval recommendations (which vary by center and
# aren't hardcoded here).
GEP_RISK_LABELS = {
    "1a": "Low",
    "1b": "Intermediate",
    "2": "High",
}


def _compute_gep_risk(case_id: str, db: Session):
    """
    Reads the recorded GEP class (if any) for this case and maps it to
    its published metastatic-risk tier. Returns None if GEP hasn't been
    recorded yet — most cases won't have this until tissue is obtained,
    so an absent result is the normal state, not a gap to flag the way
    missing imaging is.
    """
    gep_value = (
        db.query(DataValue)
        .join(DataFieldDefinition, DataValue.field_definition_id == DataFieldDefinition.id)
        .filter(
            DataValue.case_id == case_id,
            DataFieldDefinition.key == "gep_class",
            DataValue.status == "complete",
        )
        .first()
    )
    if not gep_value or not gep_value.value:
        return None

    raw = gep_value.value.strip()
    normalized = raw.lower().replace(" ", "").replace("class", "")
    risk_label = GEP_RISK_LABELS.get(normalized)

    chr3_value = (
        db.query(DataValue)
        .join(DataFieldDefinition, DataValue.field_definition_id == DataFieldDefinition.id)
        .filter(
            DataValue.case_id == case_id,
            DataFieldDefinition.key == "chromosome_3_status",
            DataValue.status == "complete",
        )
        .first()
    )

    return {
        "gep_class": raw,
        "risk_label": risk_label,  # None if the recorded value doesn't match a known class
        "chromosome_3_status": chr3_value.value if chr3_value else None,
        "test_name": "DecisionDx-UM",
    }


@app.get("/cases/{case_id}/gep-risk")
def get_gep_risk(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Standalone fetch of the recorded GEP/molecular result, so a screen
    can show the metastatic-risk badge without pulling the whole packet."""
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")
    return {"case_id": case_id, "gep_risk": _compute_gep_risk(case_id, db)}'''

content = apply(
    "GEP helper + endpoint",
    old='''@app.get("/cases/{case_id}/tfsom-risk")
def get_tfsom_risk(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Standalone fetch of the TFSOM-UHHD growth-risk score, so a screen
    can show the risk badge without pulling the whole case packet."""
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")
    return {"case_id": case_id, "tfsom_risk": _compute_tfsom_risk(case_id, db)}''',
    new='''@app.get("/cases/{case_id}/tfsom-risk")
def get_tfsom_risk(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Standalone fetch of the TFSOM-UHHD growth-risk score, so a screen
    can show the risk badge without pulling the whole case packet."""
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")
    return {"case_id": case_id, "tfsom_risk": _compute_tfsom_risk(case_id, db)}''' + GEP_BLOCK,
    content=content,
)

# Add gep_risk to the case packet response.
content = apply(
    "packet gep_risk",
    old='        "tfsom_risk": _compute_tfsom_risk(case_id, db),\n    }',
    new='        "tfsom_risk": _compute_tfsom_risk(case_id, db),\n        "gep_risk": _compute_gep_risk(case_id, db),\n    }',
    content=content,
)

# Compute the GEP risk flag in list_cases (population view).
content = apply(
    "population view GEP risk computation",
    old='        tfsom_risk = _compute_tfsom_risk(case.id, db) if case.disease_profile.key == "uveal_melanoma" else None\n\n        results.append({',
    new=(
        '        tfsom_risk = _compute_tfsom_risk(case.id, db) if case.disease_profile.key == "uveal_melanoma" else None\n\n'
        '        # GEP class is a different axis from TFSOM — metastatic risk of an\n'
        '        # already-diagnosed melanoma, not a nevus\'s risk of becoming one —\n'
        '        # so it\'s surfaced as its own population-view flag.\n'
        '        gep_risk = _compute_gep_risk(case.id, db) if case.disease_profile.key == "uveal_melanoma" else None\n\n'
        '        results.append({'
    ),
    content=content,
)

# Add the computed label to the returned dict.
content = apply(
    "population view GEP risk label field",
    old='            "tfsom_risk_label": tfsom_risk["risk_label"] if tfsom_risk else None,\n        })',
    new=(
        '            "tfsom_risk_label": tfsom_risk["risk_label"] if tfsom_risk else None,\n'
        '            "gep_risk_label": gep_risk["risk_label"] if gep_risk else None,\n'
        '        })'
    ),
    content=content,
)

if content == original:
    print("\nNothing changed (everything already applied?). main.py left untouched.")
    sys.exit(0)

with open("main.py", "w", encoding="utf-8") as f:
    f.write(content)

print("\nDone — main.py patched successfully.")
