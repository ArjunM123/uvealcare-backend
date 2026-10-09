"""Run:  python3 test_roles.py     (expect: ALL PASS)"""
import roles

fails = []
def check(name, cond):
    if not cond:
        fails.append(name)
        print("FAIL:", name)

on = lambda ids: (lambda cid: cid in ids)

# normalize / alias
check("alias medical -> systemic", roles.normalize("medical_oncologist") == "systemic_oncologist")
check("medical is restricted", roles.is_restricted("medical_oncologist"))
check("ophthalmologist not restricted", not roles.is_restricted("ophthalmologist"))
check("ocular not restricted", not roles.is_restricted("ocular_oncologist"))
check("unknown role not restricted (keeps old access)", not roles.is_restricted("something_old"))

# field rules
check("rad edits treatment", roles.can_write_category("radiation_oncologist", "treatment"))
check("rad cannot edit molecular", not roles.can_write_category("radiation_oncologist", "molecular"))
check("rad cannot edit risk_factor", not roles.can_write_category("radiation_oncologist", "risk_factor"))
check("systemic edits molecular", roles.can_write_category("systemic_oncologist", "molecular"))
check("systemic edits workup", roles.can_write_category("systemic_oncologist", "systemic_workup"))
check("systemic cannot edit imaging", not roles.can_write_category("systemic_oncologist", "imaging"))
check("systemic cannot edit treatment", not roles.can_write_category("systemic_oncologist", "treatment"))
check("ocular edits anything", roles.can_write_category("ocular_oncologist", "imaging"))

# images
check("rad may upload images", roles.can_write_images("radiation_oncologist"))
check("systemic may not upload images", not roles.can_write_images("systemic_oncologist"))
check("ocular may upload images", roles.can_write_images("ocular_oncologist"))

# request rules
t = on({"c1"})
check("ocular anything allowed", roles.check_request("ocular_oncologist", "POST", "/cases/zzz/decision", t) is None)
check("rad off-team blocked read", roles.check_request("radiation_oncologist", "GET", "/cases/c2/packet", t) is not None)
check("rad on-team read ok", roles.check_request("radiation_oncologist", "GET", "/cases/c1/packet", t) is None)
check("rad on-team values write ok", roles.check_request("radiation_oncologist", "POST", "/cases/c1/values", t) is None)
check("rad on-team image import ok", roles.check_request("radiation_oncologist", "POST", "/cases/c1/images/import", t) is None)
check("rad on-team smart import ok", roles.check_request("radiation_oncologist", "POST", "/cases/c1/images/import-smart", t) is None)
check("rad cannot record decision", roles.check_request("radiation_oncologist", "POST", "/cases/c1/decision", t) is not None)
check("rad cannot release journey", roles.check_request("radiation_oncologist", "POST", "/cases/c1/patient-journey/release", t) is not None)
check("rad cannot edit patient", roles.check_request("radiation_oncologist", "PUT", "/cases/c1/patient", t) is not None)
check("rad cannot manage team", roles.check_request("radiation_oncologist", "POST", "/cases/c1/team", t) is not None)
check("rad can consult", roles.check_request("radiation_oncologist", "POST", "/cases/c1/consult-request", t) is None)
check("rad can sign off", roles.check_request("radiation_oncologist", "POST", "/cases/c1/signoff", t) is None)
check("systemic cannot upload images", roles.check_request("systemic_oncologist", "POST", "/cases/c1/images/import", t) is not None)
check("systemic cannot delete images", roles.check_request("systemic_oncologist", "DELETE", "/cases/c1/images/by-id/x", t) is not None)
check("systemic can read imaging", roles.check_request("systemic_oncologist", "GET", "/cases/c1/images", t) is None)
check("systemic cannot create patient", roles.check_request("systemic_oncologist", "POST", "/patients", t) is not None)
check("systemic can list cases path", roles.check_request("systemic_oncologist", "GET", "/cases", t) is None)
check("systemic can complete task", roles.check_request("systemic_oncologist", "PATCH", "/tasks/abc/complete", t) is None)
check("systemic cannot set tumor board", roles.check_request("systemic_oncologist", "POST", "/tumor-board/next", t) is not None)
check("medical_oncologist alias blocked off-team", roles.check_request("medical_oncologist", "GET", "/cases/c9", t) is not None)
check("case id with similar prefix not confused", roles.check_request("radiation_oncologist", "GET", "/cases/c10/packet", t) is not None)

# permissions summary
p = roles.permissions_summary("radiation_oncologist", False)
check("summary restricted", p["restricted"] and p["write_categories"] == ["clinical", "treatment"])
p = roles.permissions_summary("radiation_oncologist", True)
check("admin overrides restriction", p["is_admin"] and not p["restricted"] and p["can_manage_team"])
check("every consult role is a known role", all(r in roles.ROLE_INFO for r in roles.CONSULT_ROLES))

# audit labels
check("audit chart open", roles.audit_action("GET", "/cases/c1") == "viewed chart")
check("audit packet", roles.audit_action("GET", "/cases/c1/packet") == "viewed packet")
check("audit skip image bytes", roles.audit_action("GET", "/cases/c1/images/by-id/x") is None)
check("audit write", roles.audit_action("POST", "/cases/c1/values") == "post values")
check("audit skip login", roles.audit_action("POST", "/login") is None)

print("ALL PASS" if not fails else f"{len(fails)} FAILED")
raise SystemExit(1 if fails else 0)
