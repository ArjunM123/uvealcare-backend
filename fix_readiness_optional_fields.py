"""
Fixes a real bug in get_readiness(): it was using the SAME query
(required_for_readiness=True) to build both the checklist display AND
the readiness percentage. That meant any field marked "not required"
(like the four new treatment/follow-up fields) was silently excluded
from the checklist entirely, instead of just being excluded from the
percentage calculation.

This patch separates the two concerns:
  - The CHECKLIST now includes every field for the disease profile.
  - The PERCENTAGE and "missing" list still only ever consider fields
    genuinely marked required_for_readiness=True.

Each checklist item also gains a new "required" boolean, so the
frontend can tell an optional field apart from a required one (e.g. to
avoid showing a red "Missing" badge on something that was never
supposed to count against readiness in the first place).

Safe to run more than once — it checks whether the fix is already
present before making any change.

Run with:  python3 fix_readiness_optional_fields.py
"""

PATH = "main.py"

OLD = '''    required_fields = db.query(DataFieldDefinition).filter_by(
        disease_profile_id=case.disease_profile_id, required_for_readiness=True
    ).all()

    values_by_field = {
        v.field_definition_id: v
        for v in db.query(DataValue).filter_by(case_id=case_id).all()
    }

    checklist = []
    complete_count = 0
    for field in required_fields:
        val = values_by_field.get(field.id)
        status = val.status if val else "missing"
        if status == "complete":
            complete_count += 1
        checklist.append({
            "key": field.key,
            "field": field.label,
            "category": field.category,
            "status": status,
            "value": val.value if val else None,
            "source": val.source if val else None,
            "measurement_method": val.measurement_method if val else None,
            "measurement_precision": val.measurement_precision if val else None,
            "measurement_length_type": val.measurement_length_type if val else None,
            "basal_diameter_mm": val.basal_diameter_mm if val else None,
            "apical_height_mm": val.apical_height_mm if val else None,
        })

    pct = round((complete_count / len(required_fields)) * 100) if required_fields else 0
    missing = [c["field"] for c in checklist if c["status"] != "complete"]'''

NEW = '''    # ALL fields for this disease profile show up on the checklist —
    # not just the ones required for readiness. A field like "Date of
    # Surgery" is genuinely tracked information even though it
    # shouldn't count against (or be excluded from) a case that hasn't
    # reached treatment yet.
    all_fields = db.query(DataFieldDefinition).filter_by(
        disease_profile_id=case.disease_profile_id
    ).all()

    values_by_field = {
        v.field_definition_id: v
        for v in db.query(DataValue).filter_by(case_id=case_id).all()
    }

    checklist = []
    for field in all_fields:
        val = values_by_field.get(field.id)
        status = val.status if val else "missing"
        checklist.append({
            "key": field.key,
            "field": field.label,
            "category": field.category,
            "status": status,
            "required": field.required_for_readiness,
            "value": val.value if val else None,
            "source": val.source if val else None,
            "measurement_method": val.measurement_method if val else None,
            "measurement_precision": val.measurement_precision if val else None,
            "measurement_length_type": val.measurement_length_type if val else None,
            "basal_diameter_mm": val.basal_diameter_mm if val else None,
            "apical_height_mm": val.apical_height_mm if val else None,
        })

    # Percentage and "missing" are only ever based on REQUIRED fields —
    # an optional field like Date of Surgery being empty should never
    # count against readiness or show up as something blocking tumor
    # board presentation.
    required_items = [c for c in checklist if c["required"]]
    complete_count = sum(1 for c in required_items if c["status"] == "complete")
    pct = round((complete_count / len(required_items)) * 100) if required_items else 0
    missing = [c["field"] for c in required_items if c["status"] != "complete"]'''


def run():
    with open(PATH, "r") as f:
        content = f.read()

    if "all_fields = db.query(DataFieldDefinition).filter_by(\n        disease_profile_id=case.disease_profile_id\n    ).all()" in content:
        print("Fix already applied — nothing to do.")
        return

    count = content.count(OLD)
    if count == 0:
        print("Could not find the expected code block to replace.")
        print("This likely means main.py has changed since this script was written.")
        print("No changes were made — send this output back for a manual fix.")
        return
    if count > 1:
        print(f"Found {count} matches, expected exactly 1 — refusing to guess which one.")
        print("No changes were made.")
        return

    content = content.replace(OLD, NEW)
    with open(PATH, "w") as f:
        f.write(content)
    print("Fixed get_readiness() — optional fields now show on the checklist without affecting readiness %.")


if __name__ == "__main__":
    run()
