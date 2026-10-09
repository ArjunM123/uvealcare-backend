"""
Patches main.py in place to add:

  * TFSOM-DIM (diameter) risk score
  * PRAME, chromosome 3 (monosomy / partial / isodisomy), 8q, 6p, BAP1
    (IHC loss, tumor mutation, germline), SF3B1, EIF1AX markers
  * a molecular profile summary that flags results that disagree

Run from inside your backend folder (same folder as main.py and
molecular_risk.py):

    python3 patch_molecular_backend.py

NO separate database migration is needed. The 12 new yes/no fields are added to
the uveal melanoma profile automatically the first time the server starts
(skipped if they already exist). Independent of the AJCC, GEP and patient
journey patches: apply in any order. Safe to run twice.
"""
import os
import sys

if not os.path.exists("molecular_risk.py"):
    print("FAIL  molecular_risk.py is not in this folder. Put it next to main.py first.")
    sys.exit(1)

content = open("main.py", "r", encoding="utf-8").read()
original = content
MARKER = "# uvealcare: molecular biomarkers"

BLOCK = '''# uvealcare: molecular biomarkers
# TFSOM-DIM and the molecular profile (see molecular_risk.py for the rules and
# their sources). The 12 new yes/no fields are created automatically at start-up
# if they are missing, so no separate migration step is needed.
def _ensure_molecular_fields():
    from database import SessionLocal
    s = SessionLocal()
    try:
        profile = s.query(DiseaseProfile).filter_by(key="uveal_melanoma").first()
        if not profile:
            return
        existing = {
            k for (k,) in s.query(DataFieldDefinition.key).filter_by(disease_profile_id=profile.id).all()
        }
        added = 0
        for key, label, category in molecular_risk.NEW_FIELDS:
            if key in existing:
                continue
            s.add(DataFieldDefinition(
                disease_profile_id=profile.id, key=key, label=label,
                category=category, data_type="boolean", required_for_readiness=False,
            ))
            added += 1
        if added:
            s.commit()
            print("molecular biomarkers: added %d field(s)" % added)
    except Exception as exc:  # never stop the server from starting over this
        s.rollback()
        print("molecular biomarkers: could not add fields:", exc)
    finally:
        s.close()


_ensure_molecular_fields()


def _molecular_values(case_id: str, db: Session):
    rows = (
        db.query(DataValue, DataFieldDefinition.key)
        .join(DataFieldDefinition, DataValue.field_definition_id == DataFieldDefinition.id)
        .filter(DataValue.case_id == case_id, DataValue.status == "complete")
        .all()
    )
    return {key: dv.value.strip() for dv, key in rows if dv.value and dv.value.strip()}


def _is_uveal(case_id: str, db: Session):
    case = db.query(Case).filter_by(id=case_id).first()
    return bool(case and case.disease_profile.key == "uveal_melanoma")


def _compute_tfsom_dim(case_id: str, db: Session):
    if not _is_uveal(case_id, db):
        return None
    return molecular_risk.compute_tfsom_dim(_molecular_values(case_id, db))


def _compute_molecular_profile(case_id: str, db: Session):
    if not _is_uveal(case_id, db):
        return None
    return molecular_risk.compute_molecular_profile(_molecular_values(case_id, db))


@app.get("/cases/{case_id}/molecular-profile")
def get_molecular_profile(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")
    return {
        "case_id": case_id,
        "tfsom_dim": _compute_tfsom_dim(case_id, db),
        "molecular_profile": _compute_molecular_profile(case_id, db),
    }


'''


def fail(msg):
    print("  FAIL  " + msg)
    print("        Your main.py differs from what this patch expects.")
    sys.exit(1)


if MARKER in content:
    print("  SKIP  molecular endpoints (already applied)")
else:
    anchor = '@app.get("/disease-profiles")'
    if content.count(anchor) != 1:
        fail("endpoints: expected exactly 1 insertion point, found %d" % content.count(anchor))
    content = content.replace(anchor, BLOCK + anchor, 1)
    print("  OK    molecular endpoints + start-up field creation")

    packet_old = '        "tfsom_risk": _compute_tfsom_risk(case_id, db),\n'
    if content.count(packet_old) != 1:
        fail("case packet: expected exactly 1 match, found %d" % content.count(packet_old))
    content = content.replace(
        packet_old,
        packet_old
        + '        "tfsom_dim": _compute_tfsom_dim(case_id, db),\n'
        + '        "molecular_profile": _compute_molecular_profile(case_id, db),\n',
        1,
    )
    print("  OK    case packet: tfsom_dim + molecular_profile")

if "import molecular_risk" not in content:
    first_import = "import datetime as dt"
    if content.count(first_import) < 1:
        fail("import anchor 'import datetime as dt' not found")
    content = content.replace(first_import, first_import + "\nimport molecular_risk", 1)
    print("  OK    import molecular_risk")
else:
    print("  SKIP  import (already applied)")

if content == original:
    print("\nNothing changed.")
    sys.exit(0)
open("main.py", "w", encoding="utf-8").write(content)
print("\nDone - main.py patched.")
