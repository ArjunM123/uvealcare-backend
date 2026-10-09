"""
Patches main.py in place to add roles, care teams and an access log:

  * Three specialist roles (ocular, radiation, systemic oncologist) plus admin
  * A "care team" on every case; specialists only see cases they are on
  * Per-role limits on what each specialist may edit
  * An access/audit log (who opened or changed what, and when)
  * Consult requests (become tasks), and per-role sign-offs on a case
  * Sign-up can no longer be used to make yourself an administrator

Run from inside your backend folder (same folder as main.py, roles.py and
roles_db.py):

    python3 patch_roles_backend.py

No manual database step. The three new tables are created when the server
starts. Independent of the other patches: apply in any order. Safe to run twice.
"""
import os
import sys

for needed in ("roles.py", "roles_db.py"):
    if not os.path.exists(needed):
        print("FAIL  %s is not in this folder. Put it next to main.py first." % needed)
        sys.exit(1)

content = open("main.py", "r", encoding="utf-8").read()
original = content
MARKER = "# uvealcare: roles and care teams"

BLOCK = r'''# uvealcare: roles and care teams
# Rules live in roles.py; tables in roles_db.py. One guard (the middleware
# below) sees every request, so a specialist cannot reach a case they are not
# on even through an endpoint added later.
import roles
import roles_db
from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse as _JSONResponse
from models import Base as _RolesBase
from database import SessionLocal as _RolesSession, engine as _roles_engine

_RolesBase.metadata.create_all(bind=_roles_engine)

# Accounts listed here are administrators. Set ADMIN_EMAILS on Render
# (comma-separated) to change it. The default keeps the demo account in charge.
ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "a.reyes@uvealcare.org").split(",")
    if e.strip()
}


def _is_admin(user) -> bool:
    return (user.role or "").strip().lower() == "admin" or (user.email or "").strip().lower() in ADMIN_EMAILS


def _role_of(user) -> str:
    return "admin" if _is_admin(user) else (user.role or "")


def _iso(dtval):
    return (dtval.isoformat() + "Z") if dtval else None


def _guard_decision(auth_header, method, path):
    # Returns (user_dict_or_None, reason_to_block_or_None). Runs in a thread.
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None, None
    try:
        payload = jwt.decode(auth_header[7:].strip(), JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None, None  # the endpoint itself will answer 401
    s = _RolesSession()
    try:
        u = s.query(User).filter_by(id=payload.get("sub")).first()
        if not u:
            return None, None
        info = {"id": u.id, "name": u.name, "role": _role_of(u)}

        def on_team(case_id):
            return s.query(roles_db.CareTeamMember.id).filter_by(case_id=case_id, user_id=u.id).first() is not None

        return info, roles.check_request(info["role"], method, path, on_team)
    except Exception as exc:  # never lock everyone out because of a bug here
        print("roles guard error:", exc)
        return None, None
    finally:
        s.close()


def _write_audit(user, method, path, status, detail):
    action = roles.audit_action(method, path)
    if status == 403:
        action = "blocked: " + (action or ("%s %s" % (method.lower(), path)))
    if not action:
        return
    parsed = roles.parse_case_path(path)
    s = _RolesSession()
    try:
        s.add(roles_db.AuditEntry(
            user_id=user["id"], user_name=user["name"], role=user["role"],
            action=action, method=method, path=path[:300],
            case_id=parsed[0] if parsed else None, status_code=status, detail=detail,
        ))
        s.commit()
    except Exception as exc:
        s.rollback()
        print("audit log error:", exc)
    finally:
        s.close()


@app.middleware("http")
async def _roles_guard(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    user, deny = await run_in_threadpool(
        _guard_decision, request.headers.get("authorization"), request.method, request.url.path
    )
    if deny:
        await run_in_threadpool(_write_audit, user, request.method, request.url.path, 403, deny)
        return _JSONResponse({"detail": deny}, status_code=403, headers={"Access-Control-Allow-Origin": "*"})
    response = await call_next(request)
    if user:
        await run_in_threadpool(
            _write_audit, user, request.method, request.url.path, response.status_code,
            getattr(request.state, "audit_detail", None),
        )
    return response


def _team_case_ids(user, db):
    return {m.case_id for m in db.query(roles_db.CareTeamMember).filter_by(user_id=user.id).all()}


def _visible_cases(cases, user, db):
    if not roles.is_restricted(_role_of(user)):
        return cases
    ids = _team_case_ids(user, db)
    return [c for c in cases if c.id in ids]


def _visible_tasks(tasks, user, db):
    if not roles.is_restricted(_role_of(user)):
        return tasks
    ids = _team_case_ids(user, db)
    return [t for t in tasks if t.case_id in ids or t.assignee_id == user.id]


def _enforce_field_write(user, field_def):
    if not roles.can_write_category(_role_of(user), field_def.category):
        raise HTTPException(
            403,
            "Your role (%s) can't edit %s fields. Ask the ocular oncologist, or send a consult request."
            % (roles.label(_role_of(user)), field_def.category.replace("_", " ")),
        )


def _require_admin(user):
    if not _is_admin(user):
        raise HTTPException(403, "Only an administrator can do that.")


def _case_or_404(case_id, db):
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")
    return case


@app.get("/me/permissions")
def my_permissions(current_user: User = Depends(get_current_user)):
    return roles.permissions_summary(current_user.role, _is_admin(current_user))


@app.get("/roles")
def list_roles(current_user: User = Depends(get_current_user)):
    return {
        "roles": [{"key": k, "label": v["label"], "summary": v["summary"]} for k, v in roles.ROLE_INFO.items()],
        "consult_roles": roles.CONSULT_ROLES,
    }


@app.get("/admin/users")
def admin_list_users(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    out = []
    for u in db.query(User).order_by(User.name).all():
        n = db.query(func.count(roles_db.CareTeamMember.id)).filter_by(user_id=u.id).scalar() or 0
        out.append({
            "id": u.id, "name": u.name, "email": u.email, "role": u.role,
            "label": roles.label(u.role), "is_admin": _is_admin(u),
            "restricted": roles.is_restricted(_role_of(u)), "case_count": n,
        })
    return out


class RoleChangeIn(BaseModel):
    role: str


@app.put("/users/{user_id}/role")
def change_user_role(user_id: str, payload: RoleChangeIn, request: Request,
                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    if payload.role not in roles.ASSIGNABLE_ROLES:
        raise HTTPException(400, "Unknown role.")
    target = db.query(User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(404, "User not found")
    old = target.role
    target.role = payload.role
    db.commit()
    request.state.audit_detail = "Changed %s from %s to %s" % (target.name, roles.label(old), roles.label(payload.role))
    return {"ok": True, "id": target.id, "role": target.role, "label": roles.label(target.role)}


@app.post("/users/{user_id}/team-all")
def add_user_to_all_cases(user_id: str, request: Request,
                          current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    target = db.query(User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(404, "User not found")
    have = {m.case_id for m in db.query(roles_db.CareTeamMember).filter_by(user_id=user_id).all()}
    added = 0
    for c in db.query(Case).all():
        if c.id not in have:
            db.add(roles_db.CareTeamMember(case_id=c.id, user_id=user_id,
                                           team_role=roles.normalize(target.role), added_by=current_user.name))
            added += 1
    db.commit()
    request.state.audit_detail = "Added %s to %d case(s)" % (target.name, added)
    return {"ok": True, "added": added}


def _team_payload(case_id, db):
    rows = db.query(roles_db.CareTeamMember).filter_by(case_id=case_id).all()
    users = {u.id: u for u in db.query(User).filter(User.id.in_([r.user_id for r in rows] or [""])).all()}
    members = []
    for r in rows:
        u = users.get(r.user_id)
        if not u:
            continue
        members.append({
            "user_id": r.user_id, "name": u.name, "team_role": r.team_role,
            "label": roles.label(r.team_role), "added_by": r.added_by, "added_at": _iso(r.created_at),
        })
    members.sort(key=lambda m: (m["label"], m["name"]))
    return members


@app.get("/cases/{case_id}/team")
def get_case_team(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    return {"members": _team_payload(case_id, db),
            "can_manage": roles.can_manage_team(_role_of(current_user))}


class TeamAddIn(BaseModel):
    user_id: str
    team_role: Optional[str] = None


@app.post("/cases/{case_id}/team")
def add_team_member(case_id: str, payload: TeamAddIn, request: Request,
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    target = db.query(User).filter_by(id=payload.user_id).first()
    if not target:
        raise HTTPException(404, "User not found")
    team_role = roles.normalize(payload.team_role or target.role)
    if team_role not in roles.ROLE_INFO:
        team_role = roles.normalize(target.role)
    row = db.query(roles_db.CareTeamMember).filter_by(case_id=case_id, user_id=target.id).first()
    if row:
        row.team_role = team_role
    else:
        db.add(roles_db.CareTeamMember(case_id=case_id, user_id=target.id, team_role=team_role,
                                       added_by=current_user.name))
    db.commit()
    request.state.audit_detail = "Added %s to the care team as %s" % (target.name, roles.label(team_role))
    return {"ok": True, "members": _team_payload(case_id, db)}


@app.delete("/cases/{case_id}/team/{user_id}")
def remove_team_member(case_id: str, user_id: str, request: Request,
                       current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    row = db.query(roles_db.CareTeamMember).filter_by(case_id=case_id, user_id=user_id).first()
    if not row:
        raise HTTPException(404, "That person isn't on this case's team.")
    u = db.query(User).filter_by(id=user_id).first()
    db.delete(row)
    db.commit()
    request.state.audit_detail = "Removed %s from the care team" % (u.name if u else user_id)
    return {"ok": True, "members": _team_payload(case_id, db)}


class ConsultIn(BaseModel):
    to_role: str
    note: Optional[str] = None


@app.post("/cases/{case_id}/consult-request")
def request_consult(case_id: str, payload: ConsultIn, request: Request,
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    to_role = roles.normalize(payload.to_role)
    if to_role not in roles.CONSULT_ROLES:
        raise HTTPException(400, "Choose ocular, radiation or systemic oncology.")
    team = db.query(roles_db.CareTeamMember).filter_by(case_id=case_id, team_role=to_role).first()
    assignee = db.query(User).filter_by(id=team.user_id).first() if team else None
    note = (payload.note or "").strip()
    desc = "%s review requested by %s" % (roles.label(to_role), current_user.name)
    if note:
        desc += ": " + note[:500]
    if not assignee:
        desc += " (no one with this role is on the care team yet)"
    task = Task(case_id=case_id, assignee_id=assignee.id if assignee else None,
                assignee_name=None if assignee else roles.label(to_role), description=desc, status="open")
    db.add(task)
    db.commit()
    request.state.audit_detail = "Requested %s review" % roles.label(to_role)
    return {"ok": True, "task_id": task.id, "assigned_to": assignee.name if assignee else None}


@app.get("/cases/{case_id}/signoffs")
def list_signoffs(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    rows = db.query(roles_db.CaseSignoff).filter_by(case_id=case_id).order_by(roles_db.CaseSignoff.at.desc()).all()
    latest = {}
    for r in rows:
        latest.setdefault(r.role, r)
    return [{"role": r.role, "label": roles.label(r.role), "by": r.user_name, "note": r.note, "at": _iso(r.at)}
            for r in latest.values()]


class SignoffIn(BaseModel):
    note: Optional[str] = None


@app.post("/cases/{case_id}/signoff")
def sign_off(case_id: str, payload: SignoffIn, request: Request,
             current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    role_key = roles.normalize(_role_of(current_user)) or "clinician"
    db.add(roles_db.CaseSignoff(case_id=case_id, role=role_key, user_id=current_user.id,
                                user_name=current_user.name, note=(payload.note or "").strip()[:500] or None))
    db.commit()
    request.state.audit_detail = "Signed off as %s" % roles.label(role_key)
    return {"ok": True}


def _audit_rows(q, limit):
    return [{
        "at": _iso(r.at), "user": r.user_name, "role": roles.label(r.role), "action": r.action,
        "status": r.status_code, "detail": r.detail, "case_id": r.case_id,
    } for r in q.order_by(roles_db.AuditEntry.at.desc()).limit(limit).all()]


@app.get("/cases/{case_id}/audit")
def case_audit(case_id: str, limit: int = 100, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if roles.is_restricted(_role_of(current_user)):
        raise HTTPException(403, "The access log is available to the ocular oncologist and administrators.")
    _case_or_404(case_id, db)
    return _audit_rows(db.query(roles_db.AuditEntry).filter_by(case_id=case_id), min(max(limit, 1), 500))


@app.get("/audit")
def full_audit(limit: int = 200, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    return _audit_rows(db.query(roles_db.AuditEntry), min(max(limit, 1), 1000))


'''


def fail(msg):
    print("  FAIL  " + msg)
    print("        Your main.py differs from what this patch expects. Nothing was changed.")
    sys.exit(1)


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        fail("%s: expected exactly 1 match, found %d" % (label, text.count(old)))
    return text.replace(old, new, 1)


if MARKER in content:
    print("  SKIP  roles block (already applied)")
else:
    anchor = '@app.get("/disease-profiles")'
    content = replace_once(content, anchor, BLOCK + anchor, "endpoints insertion point")
    print("  OK    roles, care team, audit log, consult and sign-off endpoints")

# 1. editing a field value respects the role
old = '        raise HTTPException(400, f"Unknown field \'{payload.field_key}\' for this disease profile")\n'
new = old + "    _enforce_field_write(current_user, field_def)\n"
if "_enforce_field_write(current_user, field_def)\n\n    existing = db.query(DataValue).filter_by(\n        case_id=case_id, field_definition_id=field_def.id\n    ).first()\n\n    if existing:\n        existing.value" in content:
    print("  SKIP  field-edit check (already applied)")
else:
    content = replace_once(content, old, new, "record_value anchor")
    print("  OK    field edits check the role")

# 2. deleting a field value respects the role
old = '        raise HTTPException(404, "Unknown field for this disease profile")\n'
new = old + "    _enforce_field_write(current_user, field_def)\n"
if old + "    _enforce_field_write(current_user, field_def)\n" in content:
    print("  SKIP  field-delete check (already applied)")
else:
    content = replace_once(content, old, new, "delete_value anchor")
    print("  OK    field deletes check the role")

# 3. the case list shows only cases you may open
old = "    cases = db.query(Case).all()\n    results = []\n"
new = "    cases = db.query(Case).all()\n    cases = _visible_cases(cases, current_user, db)\n    results = []\n"
if "cases = _visible_cases(cases, current_user, db)" in content:
    print("  SKIP  case list filter (already applied)")
else:
    content = replace_once(content, old, new, "list_cases anchor")
    print("  OK    case list is filtered by care team")

# 4. the dashboard task list
old = ("        .order_by(Task.due_date.is_(None), Task.due_date.asc())\n"
       "        .limit(10)\n        .all()\n    )\n")
new = ("        .order_by(Task.due_date.is_(None), Task.due_date.asc())\n"
       "        .limit(200)\n        .all()\n    )\n"
       "    tasks = _visible_tasks(tasks, current_user, db)[:10]\n")
if "tasks = _visible_tasks(tasks, current_user, db)[:10]" in content:
    print("  SKIP  task list filter (already applied)")
else:
    content = replace_once(content, old, new, "list_all_open_tasks anchor")
    print("  OK    dashboard tasks are filtered by care team")

# 5. sign-up cannot create administrators
old = '        raise HTTPException(400, "Password must be at least 8 characters.")\n'
new = old + '\n    if (payload.role or "").strip().lower() == "admin":\n        payload.role = "clinician"  # only an administrator can grant admin\n'
if 'payload.role = "clinician"  # only an administrator can grant admin' in content:
    print("  SKIP  sign-up guard (already applied)")
else:
    content = replace_once(content, old, new, "signup anchor")
    print("  OK    sign-up cannot make an administrator")

if content == original:
    print("\nNothing changed.")
    sys.exit(0)
open("main.py", "w", encoding="utf-8").write(content)
print("\nDone - main.py patched.")
