"""
Patches main.py in place to add care coordination:

  * imaging study log (ordered / performed / received / read, with repeat warning)
  * surveillance schedule with overdue alerts
  * decision history and outcomes, plus a board review list
  * tumor board agenda and one-page PDF summaries
  * clinical trial matching from recorded findings

Run from inside your backend folder (same folder as main.py), after the
roles patch is already applied:

    python3 patch_coordination_backend.py

Safe to run twice. Nothing is changed unless every check passes.
"""
import os
import sys

for needed in ("coordination.py", "coordination_db.py", "coordination_pdf.py"):
    if not os.path.exists(needed):
        print("FAIL  %s is not in this folder. Put it next to main.py first." % needed)
        sys.exit(1)

content = open("main.py", "r", encoding="utf-8").read()
MARKER = "# uvealcare: care coordination"

BLOCK = r'''# uvealcare: care coordination
# Imaging study log, surveillance schedule, decision history and outcomes,
# tumor board agenda and PDF, clinical trial matching. Rules live in
# coordination.py, tables in coordination_db.py, the PDF layout in
# coordination_pdf.py. Specialist (restricted) roles can read these on cases
# they are on; changes by them are blocked by the roles guard above.
import coordination
import coordination_db
import coordination_pdf
from fastapi import Body as _CoBody
from fastapi.responses import Response as _CoResponse
from models import Base as _CoBase
from database import engine as _co_engine

_CoBase.metadata.create_all(bind=_co_engine)
_CO = coordination_db


def _co_today():
    return dt.date.today()


def _co_fail(errors):
    raise HTTPException(400, " ".join(errors))


def _co_iso(d):
    return d.isoformat() if d else None


def _co_study_rule(r):
    return {"id": r.id, "modality": r.modality, "eye": r.eye, "status": r.status,
            "ordered_date": r.ordered_date, "performed_date": r.performed_date,
            "location": r.location, "outside": bool(r.outside)}


def _co_study_out(r):
    return {
        "id": r.id, "modality": r.modality, "modality_label": coordination.STUDY_LABELS.get(r.modality, r.modality),
        "eye": r.eye, "eye_label": coordination.EYE_LABELS.get(r.eye, r.eye),
        "status": r.status, "status_label": coordination.STATUS_LABELS.get(r.status, r.status),
        "ordered_date": _co_iso(r.ordered_date), "performed_date": _co_iso(r.performed_date),
        "due_next_date": _co_iso(r.due_next_date), "location": r.location, "outside": bool(r.outside),
        "read_by": r.read_by, "result_summary": r.result_summary, "comparison_note": r.comparison_note,
        "created_by": r.created_by,
    }


def _co_studies(case_id, db):
    return db.query(_CO.ImagingStudy).filter_by(case_id=case_id).all()


def _co_item_out(r, today=None):
    st = coordination.surveillance_status(
        {"last_done": r.last_done, "start_date": r.start_date, "interval_months": r.interval_months}, today)
    return {
        "id": r.id, "case_id": r.case_id, "kind": r.kind, "kind_label": coordination.KIND_LABELS.get(r.kind, r.kind),
        "label": r.label, "interval_months": r.interval_months, "start_date": _co_iso(r.start_date),
        "last_done": _co_iso(r.last_done), "study_type": r.study_type,
        "study_type_label": coordination.STUDY_LABELS.get(r.study_type) if r.study_type else None,
        "eye": r.eye, "eye_label": coordination.EYE_LABELS.get(r.eye) if r.eye else None,
        "notes": r.notes, **st,
    }


def _co_items(case_id, db):
    return db.query(_CO.SurveillanceItem).filter_by(case_id=case_id, active=True).all()


def _co_mark_surveillance(case_id, study, db):
    """A completed imaging study satisfies matching surveillance items."""
    if study.status not in coordination.DONE_STATUSES or not study.performed_date:
        return []
    items = _co_items(case_id, db)
    rules = [{"id": i.id, "study_type": i.study_type, "eye": i.eye} for i in items]
    hit = {m["id"] for m in coordination.matching_items(rules, study.modality, study.eye)}
    done = []
    for i in items:
        if i.id in hit and (i.last_done is None or study.performed_date > i.last_done):
            i.last_done = study.performed_date
            done.append(i.label)
    return done


def _co_user_name(u):
    return getattr(u, "name", None) or getattr(u, "email", None) or "unknown"


def _co_audit(user, case_id, action, detail=None):
    """Exports and similar reads are not in the guard's list, so record them here."""
    s = _RolesSession()
    try:
        s.add(roles_db.AuditEntry(user_id=user.id, user_name=user.name, role=_role_of(user), action=action,
                                  method="GET", case_id=case_id, status_code=200, detail=detail))
        s.commit()
    except Exception as exc:
        s.rollback()
        print("audit log error:", exc)
    finally:
        s.close()


# ── Imaging study log ──────────────────────────────────────────────────────
@app.get("/cases/{case_id}/imaging-studies")
def list_imaging_studies(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    rows = _co_studies(case_id, db)
    rows.sort(key=lambda r: (r.performed_date or r.ordered_date or dt.date.min), reverse=True)
    return {
        "studies": [_co_study_out(r) for r in rows],
        "summary": coordination.summarize_studies([_co_study_rule(r) for r in rows], _co_today()),
        "types": [{"key": k, "label": l} for k, l in coordination.STUDY_TYPES],
        "eyes": [{"key": k, "label": l} for k, l in coordination.EYES],
        "statuses": [{"key": k, "label": l} for k, l in coordination.STATUSES],
        "recent_window_days": coordination.RECENT_WINDOW_DAYS,
    }


@app.post("/cases/{case_id}/imaging-studies")
def add_imaging_study(case_id: str, payload: dict = _CoBody(...),
                      current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    clean, errors = coordination.validate_study(payload, _co_today())
    if errors:
        _co_fail(errors)
    if clean["status"] == "ordered" and not payload.get("confirm_duplicate"):
        matches = coordination.find_recent([_co_study_rule(r) for r in _co_studies(case_id, db)],
                                           clean["modality"], clean["eye"], _co_today())
        if matches:
            raise HTTPException(409, detail={"duplicate": True, "matches": matches,
                                             "message": coordination.duplicate_message(matches)})
    row = _CO.ImagingStudy(case_id=case_id, created_by=_co_user_name(current_user), **clean)
    db.add(row)
    updated = _co_mark_surveillance(case_id, row, db)
    db.commit()
    return {"ok": True, "study": _co_study_out(row), "surveillance_updated": updated}


@app.put("/cases/{case_id}/imaging-studies/{study_id}")
def update_imaging_study(case_id: str, study_id: str, payload: dict = _CoBody(...),
                         current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    row = db.query(_CO.ImagingStudy).filter_by(id=study_id, case_id=case_id).first()
    if not row:
        raise HTTPException(404, "Study not found")
    clean, errors = coordination.validate_study(payload, _co_today())
    if errors:
        _co_fail(errors)
    for k, v in clean.items():
        setattr(row, k, v)
    updated = _co_mark_surveillance(case_id, row, db)
    db.commit()
    return {"ok": True, "study": _co_study_out(row), "surveillance_updated": updated}


@app.delete("/cases/{case_id}/imaging-studies/{study_id}")
def delete_imaging_study(case_id: str, study_id: str,
                         current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(_CO.ImagingStudy).filter_by(id=study_id, case_id=case_id).first()
    if not row:
        raise HTTPException(404, "Study not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ── Surveillance schedule ──────────────────────────────────────────────────
@app.get("/cases/{case_id}/surveillance")
def list_surveillance(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    today = _co_today()
    items = [_co_item_out(r, today) for r in _co_items(case_id, db)]
    order = {"overdue": 0, "no_date": 1, "due_soon": 2, "ok": 3}
    items.sort(key=lambda i: (order.get(i["status"], 9), i["due_date"] or "9999"))
    return {
        "items": items,
        "counts": {s: sum(1 for i in items if i["status"] == s) for s in ("overdue", "due_soon", "no_date", "ok")},
        "kinds": [{"key": k, "label": l} for k, l in coordination.SURVEILLANCE_KINDS],
        "types": [{"key": k, "label": l} for k, l in coordination.STUDY_TYPES],
        "eyes": [{"key": k, "label": l} for k, l in coordination.EYES],
    }


@app.post("/cases/{case_id}/surveillance")
def add_surveillance(case_id: str, payload: dict = _CoBody(...),
                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    clean, errors = coordination.validate_surveillance(payload)
    if errors:
        _co_fail(errors)
    row = _CO.SurveillanceItem(case_id=case_id, created_by=_co_user_name(current_user), **clean)
    db.add(row)
    db.commit()
    return {"ok": True, "item": _co_item_out(row)}


@app.put("/cases/{case_id}/surveillance/{item_id}")
def update_surveillance(case_id: str, item_id: str, payload: dict = _CoBody(...),
                        current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(_CO.SurveillanceItem).filter_by(id=item_id, case_id=case_id, active=True).first()
    if not row:
        raise HTTPException(404, "Item not found")
    clean, errors = coordination.validate_surveillance(payload)
    if errors:
        _co_fail(errors)
    for k, v in clean.items():
        setattr(row, k, v)
    db.commit()
    return {"ok": True, "item": _co_item_out(row)}


@app.post("/cases/{case_id}/surveillance/{item_id}/done")
def mark_surveillance_done(case_id: str, item_id: str, payload: dict = _CoBody(default={}),
                           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(_CO.SurveillanceItem).filter_by(id=item_id, case_id=case_id, active=True).first()
    if not row:
        raise HTTPException(404, "Item not found")
    d = coordination.parse_date(payload.get("date")) if payload.get("date") else _co_today()
    if d == "bad" or d > _co_today():
        raise HTTPException(400, "Enter a date that is today or earlier, like 2026-10-31.")
    row.last_done = d
    db.commit()
    return {"ok": True, "item": _co_item_out(row)}


@app.delete("/cases/{case_id}/surveillance/{item_id}")
def delete_surveillance(case_id: str, item_id: str,
                        current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(_CO.SurveillanceItem).filter_by(id=item_id, case_id=case_id).first()
    if not row:
        raise HTTPException(404, "Item not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


@app.get("/surveillance/overdue")
def surveillance_alerts(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    today = _co_today()
    cases = _visible_cases(db.query(Case).all(), current_user, db)
    by_id = {c.id: c for c in cases}
    rows = db.query(_CO.SurveillanceItem).filter(
        _CO.SurveillanceItem.active == True, _CO.SurveillanceItem.case_id.in_(list(by_id.keys()) or [""])  # noqa: E712
    ).all()
    out = []
    for r in rows:
        o = _co_item_out(r, today)
        if o["status"] in ("overdue", "due_soon", "no_date"):
            c = by_id[r.case_id]
            o.update(patient_name=c.patient.name, mrn=c.patient.mrn)
            out.append(o)
    order = {"overdue": 0, "no_date": 1, "due_soon": 2}
    out.sort(key=lambda i: (order[i["status"]], -(i["days_overdue"] or 0), i["due_date"] or "9999"))
    return {"items": out, "overdue": sum(1 for i in out if i["status"] == "overdue"), "total": len(out)}


# ── Decision history and outcomes ──────────────────────────────────────────
@app.get("/cases/{case_id}/decision-history")
def decision_history(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    versions = db.query(_CO.DecisionHistory).filter_by(case_id=case_id).order_by(_CO.DecisionHistory.logged_at.desc()).all()
    outs = db.query(_CO.DecisionOutcome).filter_by(case_id=case_id).order_by(_CO.DecisionOutcome.recorded_at.desc()).all()
    return {
        "versions": [{"id": v.id, "recommendation": v.recommendation, "rationale": v.rationale,
                      "next_step": v.next_step, "responsible_provider": v.responsible_provider,
                      "follow_up_date": _co_iso(v.follow_up_date), "surveillance_protocol": v.surveillance_protocol,
                      "logged_at": _iso(v.logged_at)} for v in versions],
        "outcomes": [{"id": o.id, "status": o.status, "status_label": coordination.OUTCOME_LABELS.get(o.status, o.status),
                      "note": o.note, "event_date": _co_iso(o.event_date), "recommendation_then": o.recommendation_then,
                      "recorded_by": o.recorded_by, "recorded_at": _iso(o.recorded_at)} for o in outs],
        "statuses": [{"key": k, "label": l} for k, l in coordination.OUTCOME_STATUSES],
    }


@app.post("/cases/{case_id}/outcomes")
def add_outcome(case_id: str, payload: dict = _CoBody(...),
                current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    decision = db.query(Decision).filter_by(case_id=case_id).first()
    if not decision:
        raise HTTPException(400, "Record the board decision first, then add what happened.")
    clean, errors = coordination.validate_outcome(payload, _co_today())
    if errors:
        _co_fail(errors)
    db.add(_CO.DecisionOutcome(case_id=case_id, recommendation_then=decision.recommendation,
                               recorded_by=_co_user_name(current_user), **clean))
    db.commit()
    return {"ok": True}


@app.delete("/cases/{case_id}/outcomes/{outcome_id}")
def delete_outcome(case_id: str, outcome_id: str,
                   current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(_CO.DecisionOutcome).filter_by(id=outcome_id, case_id=case_id).first()
    if not row:
        raise HTTPException(404, "Outcome not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


@app.get("/tumor-board/review")
def board_review(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Decided cases with the latest recorded outcome, so the board can
    look back at its own decisions."""
    today = _co_today()
    cases = {c.id: c for c in _visible_cases(db.query(Case).all(), current_user, db)}
    decisions = db.query(Decision).filter(Decision.case_id.in_(list(cases.keys()) or [""])).all()
    outs = db.query(_CO.DecisionOutcome).order_by(_CO.DecisionOutcome.recorded_at.desc()).all()
    latest = {}
    counts = {}
    for o in outs:
        latest.setdefault(o.case_id, o)
        counts[o.case_id] = counts.get(o.case_id, 0) + 1
    rows = []
    for d in decisions:
        c = cases[d.case_id]
        o = latest.get(d.case_id)
        decided = d.recorded_at.date() if d.recorded_at else None
        rows.append({
            "case_id": d.case_id, "patient_name": c.patient.name, "mrn": c.patient.mrn,
            "recommendation": d.recommendation, "decided_on": _co_iso(decided),
            "days_since": (today - decided).days if decided else None,
            "outcome_count": counts.get(d.case_id, 0),
            "latest_outcome": {"status": o.status, "status_label": coordination.OUTCOME_LABELS.get(o.status, o.status),
                               "note": o.note, "event_date": _co_iso(o.event_date)} if o else None,
        })
    rows.sort(key=lambda r: (r["latest_outcome"] is not None, -(r["days_since"] or 0)))
    return {
        "rows": rows,
        "summary": {
            "decided": len(rows),
            "with_outcome": sum(1 for r in rows if r["latest_outcome"]),
            "no_outcome_yet": sum(1 for r in rows if not r["latest_outcome"]),
            "plan_changed": sum(1 for r in rows if r["latest_outcome"] and r["latest_outcome"]["status"] == "changed"),
        },
    }


# ── Tumor board agenda and PDF ─────────────────────────────────────────────
def _co_agenda_row(case, note, added_by, db):
    _cl, pct, ready, _miss = _build_readiness_checklist(case, db)
    return {"case_id": case.id, "patient_name": case.patient.name, "mrn": case.patient.mrn,
            "laterality": case.patient.laterality, "readiness_pct": pct, "ready": ready,
            "has_decision": db.query(Decision.id).filter_by(case_id=case.id).first() is not None,
            "care_stage": case.care_stage, "note": note, "added_by": added_by}


def _co_meeting_date(db):
    m = db.query(TumorBoardMeeting).order_by(TumorBoardMeeting.created_at.desc()).first()
    return m.meeting_date.isoformat() if m else None


@app.get("/tumor-board/agenda")
def get_agenda(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cases = {c.id: c for c in _visible_cases(db.query(Case).all(), current_user, db)}
    on = {a.case_id: a for a in db.query(_CO.BoardAgendaItem).order_by(_CO.BoardAgendaItem.added_at).all()}
    items = [_co_agenda_row(cases[cid], a.note, a.added_by, db) for cid, a in on.items() if cid in cases]
    others = [dict(_co_agenda_row(c, None, None, db), on_agenda=False) for cid, c in cases.items() if cid not in on]
    suggested = coordination.suggest_agenda(
        [dict(o, on_agenda=False) for o in others])
    return {"meeting_date": _co_meeting_date(db), "items": items, "suggested": suggested}


@app.post("/tumor-board/agenda")
def add_to_agenda(payload: dict = _CoBody(...), current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    case = _case_or_404(str(payload.get("case_id") or ""), db)
    note = (payload.get("note") or "").strip()[:300] or None
    row = db.query(_CO.BoardAgendaItem).filter_by(case_id=case.id).first()
    if row:
        row.note = note or row.note
    else:
        db.add(_CO.BoardAgendaItem(case_id=case.id, note=note, added_by=_co_user_name(current_user)))
    db.commit()
    return {"ok": True}


@app.delete("/tumor-board/agenda/{case_id}")
def remove_from_agenda(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(_CO.BoardAgendaItem).filter_by(case_id=case_id).delete()
    db.commit()
    return {"ok": True}


@app.delete("/tumor-board/agenda")
def clear_agenda(confirm: bool = False, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not confirm:
        raise HTTPException(400, "Add ?confirm=true to clear the whole agenda.")
    n = db.query(_CO.BoardAgendaItem).delete()
    db.commit()
    return {"ok": True, "removed": n}


def _co_page(case, current_user, db, meeting_date):
    packet = get_case_packet(case.id, current_user, db)
    rows = _co_studies(case.id, db)
    studies = coordination.summarize_studies([_co_study_rule(r) for r in rows], _co_today())
    today = _co_today()
    surv = [_co_item_out(r, today) for r in _co_items(case.id, db)]
    images = []
    for ki in packet.get("key_images", [])[:4]:
        img = db.query(ImageUpload).filter_by(id=ki["image_id"]).first()
        if img:
            images.append({"data_base64": img.data_base64, "label": ki["field_label"]})
    return coordination_pdf.case_page(packet, studies, surv, images, meeting_date)


@app.get("/cases/{case_id}/summary.pdf")
def case_summary_pdf(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    case = _case_or_404(case_id, db)
    pdf = coordination_pdf.render([_co_page(case, current_user, db, _co_meeting_date(db))])
    _co_audit(current_user, case_id, "exported PDF summary")
    return _CoResponse(pdf, media_type="application/pdf",
                       headers={"Content-Disposition": 'inline; filename="case-summary.pdf"'})


@app.get("/tumor-board/agenda.pdf")
def agenda_pdf(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    meeting = _co_meeting_date(db)
    cases = {c.id: c for c in _visible_cases(db.query(Case).all(), current_user, db)}
    on = [a for a in db.query(_CO.BoardAgendaItem).order_by(_CO.BoardAgendaItem.added_at).all() if a.case_id in cases]
    items = [_co_agenda_row(cases[a.case_id], a.note, a.added_by, db) for a in on]
    pages = [_co_page(cases[a.case_id], current_user, db, meeting) for a in on]
    pdf = coordination_pdf.render(pages, cover=coordination_pdf.agenda_cover(items, meeting))
    for a in on:
        _co_audit(current_user, a.case_id, "exported PDF summary", "tumor board agenda")
    return _CoResponse(pdf, media_type="application/pdf",
                       headers={"Content-Disposition": 'inline; filename="tumor-board-agenda.pdf"'})


# ── Clinical trials ────────────────────────────────────────────────────────
def _co_trial_out(t):
    try:
        crit = json.loads(t.criteria_json or "[]")
    except Exception:
        crit = []
    return {"id": t.id, "name": t.name, "nct_id": t.nct_id, "summary": t.summary, "source_note": t.source_note,
            "last_verified": _co_iso(t.last_verified), "criteria": crit, "active": bool(t.active),
            "created_by": t.created_by}


@app.get("/trials")
def list_trials(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(_CO.Trial).order_by(_CO.Trial.name).all()
    return {"trials": [_co_trial_out(t) for t in rows],
            "ops": [{"key": k, "label": l} for k, l in coordination.OPS], "notice": coordination.MATCH_NOTICE}


@app.get("/trial-fields")
def trial_fields(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    seen, out = set(), []
    for f in db.query(DataFieldDefinition).order_by(DataFieldDefinition.label).all():
        if f.key in seen or f.category in ("imaging", "patient_support"):
            continue
        seen.add(f.key)
        out.append({"key": f.key, "label": f.label, "data_type": f.data_type})
    out.append({"key": "apical_height_mm", "label": "Tumor thickness (mm)", "data_type": "number"})
    out.append({"key": "basal_diameter_mm", "label": "Largest basal diameter (mm)", "data_type": "number"})
    return {"fields": out}


@app.post("/trials")
def create_trial(payload: dict = _CoBody(...), current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    _require_admin(current_user)
    clean, errors = coordination.validate_trial(payload)
    if errors:
        _co_fail(errors)
    crit = clean.pop("criteria")
    t = _CO.Trial(criteria_json=json.dumps(crit), created_by=_co_user_name(current_user), **clean)
    db.add(t)
    db.commit()
    return {"ok": True, "trial": _co_trial_out(t)}


@app.put("/trials/{trial_id}")
def update_trial(trial_id: str, payload: dict = _CoBody(...), current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    _require_admin(current_user)
    t = db.query(_CO.Trial).filter_by(id=trial_id).first()
    if not t:
        raise HTTPException(404, "Trial not found")
    clean, errors = coordination.validate_trial(payload)
    if errors:
        _co_fail(errors)
    t.criteria_json = json.dumps(clean.pop("criteria"))
    for k, v in clean.items():
        setattr(t, k, v)
    db.commit()
    return {"ok": True, "trial": _co_trial_out(t)}


@app.delete("/trials/{trial_id}")
def delete_trial(trial_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    db.query(_CO.Trial).filter_by(id=trial_id).delete()
    db.commit()
    return {"ok": True}


@app.get("/cases/{case_id}/trial-matches")
def trial_matches(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _case_or_404(case_id, db)
    rows = (
        db.query(DataValue, DataFieldDefinition.key)
        .join(DataFieldDefinition, DataValue.field_definition_id == DataFieldDefinition.id)
        .filter(DataValue.case_id == case_id, DataValue.status == "complete")
        .all()
    )
    values = {key: dv.value.strip() for dv, key in rows if dv.value and dv.value.strip()}
    latest, _trend = _latest_measurement_and_trend(case_id, db)
    if latest is not None:
        if latest.apical_height_mm is not None:
            values["apical_height_mm"] = str(latest.apical_height_mm)
        if latest.basal_diameter_mm is not None:
            values["basal_diameter_mm"] = str(latest.basal_diameter_mm)
    out = []
    for t in db.query(_CO.Trial).filter_by(active=True).order_by(_CO.Trial.name).all():
        o = _co_trial_out(t)
        res = coordination.match_trial(o, values)
        out.append({"trial": {k: o[k] for k in ("id", "name", "nct_id", "summary", "last_verified", "source_note")}, **res})
    rank = {"possible_match": 0, "needs_data": 1, "no_criteria": 2, "not_a_match": 3}
    out.sort(key=lambda r: (rank[r["overall"]], r["trial"]["name"]))
    return {"matches": out, "notice": coordination.MATCH_NOTICE}
'''


def fail(msg):
    print("  FAIL  " + msg)
    print("        Your main.py differs from what this patch expects. Nothing was changed.")
    sys.exit(1)


if MARKER in content:
    print("  SKIP  care coordination (already applied)")
    print("\nNothing changed.")
    sys.exit(0)

if "# uvealcare: roles and care teams" not in content:
    fail("the roles patch must be applied first (patch_roles_backend.py)")
for needed in ("def _build_readiness_checklist(", "def get_case_packet(", "def _latest_measurement_and_trend(",
               "def _require_admin(", "def _visible_cases(", "def _case_or_404(", "TumorBoardMeeting", "Decision"):
    if needed not in content:
        fail("this main.py has no '%s'" % needed)
if "import datetime as dt" not in content or "import json" not in content:
    fail("expected 'import datetime as dt' and 'import json' at the top")

if not content.endswith("\n"):
    content += "\n"
content = content + "\n\n" + BLOCK
open("main.py", "w", encoding="utf-8").write(content)
print("  OK    care coordination endpoints")
print("\nDone - main.py patched.")
