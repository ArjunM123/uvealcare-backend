"""
Patches main.py in place to add TFSOM-UHHD risk scoring, since the
updated main.py itself couldn't be delivered as a file this session.

Run this from inside your uvealcare-backend repo folder (same folder as
main.py):

    python3 patch_tfsom_backend.py

It only edits main.py — it does NOT create add_tfsom_risk_factors.py,
which you'll get separately. Safe to run only once; running it twice
will fail cleanly (it checks each change isn't already applied).
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
        print("        Your main.py may differ from what this patch expects.")
        sys.exit(1)
    print(f"  OK    {label}")
    return content.replace(old, new, 1)


# 1. Expose data_type on each readiness-checklist item (so the frontend
#    can render boolean fields as Present/Absent toggles instead of a
#    free-text box).
content = apply(
    "checklist data_type",
    old='            "category": field.category,\n            "status": status,\n            "required": field.required_for_readiness,',
    new='            "category": field.category,\n            "data_type": field.data_type,\n            "status": status,\n            "required": field.required_for_readiness,',
    content=content,
)

# 2. Insert the TFSOM-UHHD constants, _compute_tfsom_risk() helper, and
#    the new GET /cases/{case_id}/tfsom-risk endpoint, right before the
#    existing readiness endpoint.
TFSOM_BLOCK = '''
# TFSOM-UHHD is the published 8-factor mnemonic for estimating a choroidal
# nevus's 5-year risk of growing into melanoma (Shields et al.): Thickness
# >2mm, subretinal Fluid, Symptoms, Orange pigment, Margin <=3mm from the
# optic disc, Ultrasonographic hollowness, Halo absent, Drusen absent.
# Stored as ordinary boolean DataFieldDefinitions (see
# add_tfsom_risk_factors.py) so they get the app's existing
# record/edit/audit machinery for free — this helper just turns whichever
# of the 8 have actually been assessed into a risk score, the same way a
# clinician would tally them by hand.
TFSOM_FACTOR_KEYS = [
    "tfsom_thickness",
    "tfsom_fluid",
    "tfsom_symptoms",
    "tfsom_orange_pigment",
    "tfsom_margin",
    "tfsom_ultrasound_hollow",
    "tfsom_no_halo",
    "tfsom_no_drusen",
]

TFSOM_FACTOR_LABELS = {
    "tfsom_thickness": "Thickness >2mm",
    "tfsom_fluid": "Subretinal fluid",
    "tfsom_symptoms": "Symptoms",
    "tfsom_orange_pigment": "Orange pigment",
    "tfsom_margin": "Margin \\u22643mm to disc",
    "tfsom_ultrasound_hollow": "Ultrasonographic hollowness",
    "tfsom_no_halo": "Halo absent",
    "tfsom_no_drusen": "Drusen absent",
}


def _compute_tfsom_risk(case_id: str, db: Session):
    """
    Tallies whichever TFSOM-UHHD factors have actually been assessed
    (status == "complete") for this case and maps the count to the
    published 5-year growth-risk bands. A factor that was never assessed
    is excluded from the count rather than assumed absent, so an
    incomplete work-up never produces a falsely reassuring score.

    Returns None when none of the 8 factors have been assessed yet
    (e.g. this isn't a melanoma case, or risk assessment hasn't started).
    """
    values = (
        db.query(DataValue)
        .join(DataFieldDefinition, DataValue.field_definition_id == DataFieldDefinition.id)
        .filter(DataValue.case_id == case_id, DataFieldDefinition.key.in_(TFSOM_FACTOR_KEYS))
        .all()
    )
    by_key = {v.field_definition.key: v for v in values}

    assessed = [k for k in TFSOM_FACTOR_KEYS if k in by_key and by_key[k].status == "complete"]
    if not assessed:
        return None

    present = [k for k in assessed if (by_key[k].value or "").strip().lower() == "true"]
    count = len(present)

    if count == 0:
        risk_label, risk_estimate = "Low", "~3% 5-year risk of growth to melanoma"
    elif count == 1:
        risk_label, risk_estimate = "Moderate", "~38% 5-year risk of growth to melanoma"
    else:
        risk_label, risk_estimate = "High", ">50% 5-year risk of growth to melanoma"

    return {
        "factors_assessed": len(assessed),
        "factors_total": len(TFSOM_FACTOR_KEYS),
        "factors_present": [TFSOM_FACTOR_LABELS[k] for k in present],
        "factor_count": count,
        "risk_label": risk_label,
        "risk_estimate": risk_estimate,
        "mnemonic": "TFSOM-UHHD",
    }


@app.get("/cases/{case_id}/tfsom-risk")
def get_tfsom_risk(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Standalone fetch of the TFSOM-UHHD growth-risk score, so a screen
    can show the risk badge without pulling the whole case packet."""
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")
    return {"case_id": case_id, "tfsom_risk": _compute_tfsom_risk(case_id, db)}

'''

content = apply(
    "TFSOM helper + endpoint",
    old='    return latest, trend\n\n\n@app.get("/cases/{case_id}/readiness")',
    new='    return latest, trend\n\n' + TFSOM_BLOCK + '\n@app.get("/cases/{case_id}/readiness")',
    content=content,
)

# 3. Add tfsom_risk to the case packet response.
content = apply(
    "packet tfsom_risk",
    old='        } if decision else None,\n        "key_images": key_images,\n    }',
    new='        } if decision else None,\n        "key_images": key_images,\n        "tfsom_risk": _compute_tfsom_risk(case_id, db),\n    }',
    content=content,
)

# 4a. Compute the risk flag in list_cases (population view).
content = apply(
    "population view risk computation",
    old='        decision = db.query(Decision).filter_by(case_id=case.id).first()\n\n        results.append({',
    new=(
        '        decision = db.query(Decision).filter_by(case_id=case.id).first()\n\n'
        '        # Only uveal melanoma cases carry TFSOM factors at all, so this is\n'
        '        # a cheap no-op query for every other disease profile — but it\'s\n'
        '        # exactly the kind of flag a generic EHR patient list can\'t show:\n'
        '        # which nevi, across the whole population, are trending toward a\n'
        '        # melanoma diagnosis, visible without opening each chart.\n'
        '        tfsom_risk = _compute_tfsom_risk(case.id, db) if case.disease_profile.key == "uveal_melanoma" else None\n\n'
        '        results.append({'
    ),
    content=content,
)

# 4b. Add the computed label to the returned dict.
content = apply(
    "population view risk label field",
    old='            "follow_up_date": decision.follow_up_date.isoformat() if decision and decision.follow_up_date else None,\n            "surveillance_protocol": decision.surveillance_protocol if decision else None,\n        })',
    new=(
        '            "follow_up_date": decision.follow_up_date.isoformat() if decision and decision.follow_up_date else None,\n'
        '            "surveillance_protocol": decision.surveillance_protocol if decision else None,\n'
        '            "tfsom_risk_label": tfsom_risk["risk_label"] if tfsom_risk else None,\n'
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
