"""
Care-coordination rules: imaging study log, surveillance schedule, board
agenda suggestions, decision outcomes, and clinical-trial matching.

Pure Python (no database, no web framework) so every rule can be tested on
its own. coordination_db.py holds the tables; the endpoints live in main.py.

Design rules, kept deliberately:
  * Nothing here decides a clinical interval. Surveillance intervals are
    typed in by the clinician for each patient; there are no defaults.
  * A value nobody recorded is "unknown", never "no" and never "ok".
  * Trial matching never says "eligible". The most it says is "possible
    match", and every result tells the coordinator to check the protocol.
  * Two thresholds below are display conveniences, not clinical rules, and
    are named constants so a clinician can change them in one place.
"""

import datetime as dt
import re

# ── Imaging study log ──────────────────────────────────────────────────────
STUDY_TYPES = [
    ("b_scan", "B-scan ultrasound"),
    ("oct", "OCT"),
    ("optos", "Widefield (Optos)"),
    ("faf", "Fundus autofluorescence"),
    ("fundus_photo", "Fundus photograph"),
    ("liver_imaging", "Liver imaging (MRI, CT or ultrasound)"),
    ("other", "Other"),
]
STUDY_LABELS = dict(STUDY_TYPES)

EYES = [("right", "Right eye"), ("left", "Left eye"), ("both", "Both eyes"), ("na", "Not eye-specific")]
EYE_LABELS = dict(EYES)

STATUSES = [("ordered", "Ordered"), ("performed", "Performed"), ("received", "Images received"), ("read", "Read")]
STATUS_LABELS = dict(STATUSES)
DONE_STATUSES = {"performed", "received", "read"}

# A study of the same type on the same eye within this many days is shown as
# "recent" when someone orders or logs another. It only warns; it never blocks.
# Display convenience, not a clinical rule. Confirm the number with the clinic.
RECENT_WINDOW_DAYS = 30

# Surveillance items due within this many days show as "due soon".
DUE_SOON_DAYS = 30


def parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return "bad"


def _eyes_overlap(a, b):
    if a == "na" or b == "na":
        return a == b
    if a == "both" or b == "both":
        return True
    return a == b


def _clean_text(value, limit):
    s = (value or "").strip()
    return s[:limit] if s else None


def validate_study(data, today=None):
    """Returns (clean, errors). `data` is the request body as a dict."""
    today = today or dt.date.today()
    errors = []
    modality = (data.get("modality") or "").strip()
    if modality not in STUDY_LABELS:
        errors.append("Choose which study this is.")
    eye = (data.get("eye") or "").strip() or ("na" if modality in ("liver_imaging", "other") else "")
    if eye not in EYE_LABELS:
        errors.append("Choose which eye (or 'Not eye-specific').")
    status = (data.get("status") or "").strip()
    if status not in STATUS_LABELS:
        errors.append("Choose a status.")

    dates = {}
    for key, label in (("ordered_date", "Ordered date"), ("performed_date", "Date performed"),
                       ("due_next_date", "Next due date")):
        d = parse_date(data.get(key))
        if d == "bad":
            errors.append("%s must look like 2026-10-31." % label)
            d = None
        dates[key] = d
    if status in DONE_STATUSES and not dates["performed_date"]:
        errors.append("Enter the date the study was performed.")
    if dates["performed_date"] and dates["performed_date"] > today:
        errors.append("The date performed can't be in the future.")
    if dates["ordered_date"] and dates["performed_date"] and dates["performed_date"] < dates["ordered_date"]:
        errors.append("The study can't be performed before it was ordered.")

    clean = {
        "modality": modality, "eye": eye, "status": status,
        "ordered_date": dates["ordered_date"], "performed_date": dates["performed_date"],
        "due_next_date": dates["due_next_date"],
        "location": _clean_text(data.get("location"), 200),
        "outside": bool(data.get("outside")),
        "read_by": _clean_text(data.get("read_by"), 200),
        "result_summary": _clean_text(data.get("result_summary"), 2000),
        "comparison_note": _clean_text(data.get("comparison_note"), 1000),
    }
    return clean, errors


def find_recent(studies, modality, eye, today=None, window_days=RECENT_WINDOW_DAYS, exclude_id=None):
    """Earlier studies of the same type on an overlapping eye that were done
    inside the window, or that are still waiting to be done."""
    today = today or dt.date.today()
    out = []
    for s in studies:
        if exclude_id and s.get("id") == exclude_id:
            continue
        if s["modality"] != modality or not _eyes_overlap(s["eye"], eye):
            continue
        if s["status"] in DONE_STATUSES and s.get("performed_date"):
            age = (today - s["performed_date"]).days
            if 0 <= age <= window_days:
                out.append({"id": s.get("id"), "kind": "recent", "status": s["status"],
                            "date": s["performed_date"].isoformat(), "days_ago": age,
                            "location": s.get("location"), "outside": bool(s.get("outside"))})
        elif s["status"] == "ordered":
            out.append({"id": s.get("id"), "kind": "pending", "status": "ordered",
                        "date": s["ordered_date"].isoformat() if s.get("ordered_date") else None,
                        "days_ago": (today - s["ordered_date"]).days if s.get("ordered_date") else None,
                        "location": s.get("location"), "outside": bool(s.get("outside"))})
    return out


def duplicate_message(matches):
    if not matches:
        return None
    parts = []
    for m in matches:
        where = (" at " + m["location"]) if m.get("location") else ""
        if m["kind"] == "recent":
            parts.append("done %d day%s ago%s" % (m["days_ago"], "" if m["days_ago"] == 1 else "s", where))
        else:
            parts.append("already ordered%s" % where)
    return "The same study on this eye was %s. Check it isn't a repeat before ordering." % "; ".join(parts)


def summarize_studies(studies, today=None):
    """What a clinician wants at a glance: the latest completed study for
    each type and eye, and orders still waiting."""
    today = today or dt.date.today()
    latest = {}
    waiting = []
    for s in studies:
        if s["status"] in DONE_STATUSES and s.get("performed_date"):
            k = (s["modality"], s["eye"])
            if k not in latest or s["performed_date"] > latest[k]["performed_date"]:
                latest[k] = s
        elif s["status"] == "ordered":
            waiting.append(s)
    latest_rows = [{
        "modality": k[0], "modality_label": STUDY_LABELS.get(k[0], k[0]),
        "eye": k[1], "eye_label": EYE_LABELS.get(k[1], k[1]),
        "performed_date": s["performed_date"].isoformat(),
        "days_ago": (today - s["performed_date"]).days,
        "status": s["status"], "location": s.get("location"), "outside": bool(s.get("outside")),
    } for k, s in sorted(latest.items(), key=lambda kv: kv[1]["performed_date"], reverse=True)]
    waiting_rows = [{
        "id": s.get("id"), "modality_label": STUDY_LABELS.get(s["modality"], s["modality"]),
        "eye_label": EYE_LABELS.get(s["eye"], s["eye"]),
        "ordered_date": s["ordered_date"].isoformat() if s.get("ordered_date") else None,
        "days_waiting": (today - s["ordered_date"]).days if s.get("ordered_date") else None,
    } for s in waiting]
    return {"latest": latest_rows, "waiting": waiting_rows}


# ── Surveillance schedule ──────────────────────────────────────────────────
SURVEILLANCE_KINDS = [
    ("imaging", "Eye imaging"), ("liver", "Liver test or imaging"),
    ("systemic", "Systemic work-up"), ("clinic", "Clinic visit"), ("other", "Other"),
]
KIND_LABELS = dict(SURVEILLANCE_KINDS)


def add_months(d, n):
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    last = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(d.day, last))


def validate_surveillance(data):
    errors = []
    kind = (data.get("kind") or "").strip()
    if kind not in KIND_LABELS:
        errors.append("Choose what kind of item this is.")
    label = _clean_text(data.get("label"), 200)
    if not label:
        errors.append("Give the item a name, for example 'Liver MRI'.")
    try:
        interval = int(data.get("interval_months"))
    except (TypeError, ValueError):
        interval = None
    if interval is None or not (1 <= interval <= 60):
        errors.append("Enter how often, in months (1 to 60). The clinician decides this; there is no default.")
    dates = {}
    for key, nice in (("start_date", "Start date"), ("last_done", "Last done")):
        d = parse_date(data.get(key))
        if d == "bad":
            errors.append("%s must look like 2026-10-31." % nice)
            d = None
        dates[key] = d
    study_type = (data.get("study_type") or "").strip() or None
    if study_type and study_type not in STUDY_LABELS:
        errors.append("Unknown study type.")
    eye = (data.get("eye") or "").strip() or None
    if eye and eye not in EYE_LABELS:
        errors.append("Unknown eye.")
    clean = {
        "kind": kind, "label": label, "interval_months": interval,
        "start_date": dates["start_date"], "last_done": dates["last_done"],
        "study_type": study_type, "eye": eye,
        "notes": _clean_text(data.get("notes"), 1000),
    }
    return clean, errors


def surveillance_status(item, today=None):
    """item: dict with interval_months, last_done, start_date (dates or None)."""
    today = today or dt.date.today()
    last, start, interval = item.get("last_done"), item.get("start_date"), item.get("interval_months")
    never = last is None
    if last and interval:
        due = add_months(last, interval)
    else:
        due = start
    if due is None:
        return {"status": "no_date", "due_date": None, "days_overdue": None, "days_until": None,
                "never_done": never, "text": "Needs a start date or a date last done"}
    delta = (due - today).days
    if delta < 0:
        status, text = "overdue", "Overdue by %d day%s" % (-delta, "" if delta == -1 else "s")
    elif delta <= DUE_SOON_DAYS:
        status, text = "due_soon", "Due in %d day%s" % (delta, "" if delta == 1 else "s")
    else:
        status, text = "ok", "Next due %s" % due.isoformat()
    return {"status": status, "due_date": due.isoformat(),
            "days_overdue": -delta if delta < 0 else 0, "days_until": delta,
            "never_done": never, "text": text}


def matching_items(items, modality, eye):
    """Surveillance items that a completed imaging study satisfies."""
    out = []
    for it in items:
        if it.get("study_type") != modality:
            continue
        ie = it.get("eye")
        if ie and not _eyes_overlap(ie, eye):
            continue
        out.append(it)
    return out


# ── Decision outcomes ──────────────────────────────────────────────────────
OUTCOME_STATUSES = [("followed", "Plan followed"), ("changed", "Plan changed"), ("unknown", "Not yet known")]
OUTCOME_LABELS = dict(OUTCOME_STATUSES)


def validate_outcome(data, today=None):
    today = today or dt.date.today()
    errors = []
    status = (data.get("status") or "").strip()
    if status not in OUTCOME_LABELS:
        errors.append("Choose what happened to the plan.")
    note = _clean_text(data.get("note"), 3000)
    if not note:
        errors.append("Write a short note on what happened.")
    d = parse_date(data.get("event_date"))
    if d == "bad":
        errors.append("The date must look like 2026-10-31.")
        d = None
    if d and d > today:
        errors.append("The date can't be in the future.")
    return {"status": status, "note": note, "event_date": d}, errors


def suggest_agenda(cases):
    """cases: dicts with case_id, has_decision, ready, readiness_pct, on_agenda.
    Suggests cases that are ready, undecided and not yet on the agenda."""
    pick = [c for c in cases if c["ready"] and not c["has_decision"] and not c["on_agenda"]]
    return sorted(pick, key=lambda c: (-c["readiness_pct"], c["case_id"]))


# ── Clinical trial matching ────────────────────────────────────────────────
OPS = [
    ("is", "is"), ("is_not", "is not"), ("at_least", "is at least"), ("at_most", "is at most"),
    ("greater", "is greater than"), ("less", "is less than"), ("one_of", "is one of"),
]
OP_LABELS = dict(OPS)
NUMERIC_OPS = {"at_least", "at_most", "greater", "less"}
NCT_RE = re.compile(r"^NCT\d{8}$")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def norm_value(raw):
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    return {"true": "present", "false": "absent", "yes": "present", "no": "absent"}.get(s, s)


def _number(raw):
    m = _NUM.search(str(raw)) if raw is not None else None
    return float(m.group()) if m else None


def condition_state(crit, values):
    """Is the written condition true for this patient? 'true' / 'false' / 'unknown'."""
    have = norm_value(values.get(crit["field_key"]))
    if have is None:
        return "unknown"
    want_raw = crit["value"]
    op = crit["op"]
    if op in NUMERIC_OPS:
        a, b = _number(have), _number(want_raw)
        if a is None or b is None:
            return "unknown"
        ok = {"at_least": a >= b, "at_most": a <= b, "greater": a > b, "less": a < b}[op]
        return "true" if ok else "false"
    if op == "one_of":
        wants = {norm_value(x) for x in str(want_raw).split(",") if norm_value(x)}
        return "true" if have in wants else "false"
    want = norm_value(want_raw)
    if op == "is":
        return "true" if have == want else "false"
    if op == "is_not":
        return "true" if have != want else "false"
    return "unknown"


def validate_trial(data):
    errors = []
    name = _clean_text(data.get("name"), 200)
    if not name:
        errors.append("Give the trial a name.")
    nct = (data.get("nct_id") or "").strip().upper()
    if nct and not NCT_RE.match(nct):
        errors.append("The trial number looks like NCT01234567.")
    verified = parse_date(data.get("last_verified"))
    if verified == "bad":
        errors.append("The date verified must look like 2026-10-31.")
        verified = None
    crits_in = data.get("criteria") or []
    crits = []
    if len(crits_in) > 40:
        errors.append("Too many criteria (40 at most).")
    for i, c in enumerate(crits_in[:40], 1):
        kind = (c.get("kind") or "").strip()
        key = (c.get("field_key") or "").strip()
        op = (c.get("op") or "").strip()
        val = (str(c.get("value")) if c.get("value") is not None else "").strip()
        if kind not in ("inclusion", "exclusion") or not key or op not in OP_LABELS or not val:
            errors.append("Criterion %d is incomplete." % i)
            continue
        if op in NUMERIC_OPS and _number(val) is None:
            errors.append("Criterion %d needs a number." % i)
            continue
        crits.append({"kind": kind, "field_key": key, "field_label": _clean_text(c.get("field_label"), 120) or key,
                      "op": op, "value": val[:200]})
    clean = {
        "name": name, "nct_id": nct or None,
        "summary": _clean_text(data.get("summary"), 2000),
        "source_note": _clean_text(data.get("source_note"), 500),
        "last_verified": verified, "criteria": crits,
        "active": bool(data.get("active", True)),
    }
    return clean, errors


MATCH_NOTICE = ("This compares recorded findings with criteria someone typed in. It is not an eligibility "
                "decision. Check the full protocol and the current trial listing before contacting a patient.")


def match_trial(trial, values):
    rows = []
    for c in trial["criteria"]:
        cond = condition_state(c, values)
        if cond == "unknown":
            state = "unknown"
        elif c["kind"] == "inclusion":
            state = "ok" if cond == "true" else "fails"
        else:
            state = "ok" if cond == "false" else "fails"
        rows.append({
            "kind": c["kind"], "label": c.get("field_label") or c["field_key"],
            "text": "%s %s" % (OP_LABELS[c["op"]], c["value"]), "state": state,
            "have": values.get(c["field_key"]),
        })
    if not rows:
        overall = "no_criteria"
    elif any(r["state"] == "fails" for r in rows):
        overall = "not_a_match"
    elif any(r["state"] == "unknown" for r in rows):
        overall = "needs_data"
    else:
        overall = "possible_match"
    return {"overall": overall, "criteria": rows,
            "unknown_count": sum(1 for r in rows if r["state"] == "unknown"),
            "fail_count": sum(1 for r in rows if r["state"] == "fails")}
