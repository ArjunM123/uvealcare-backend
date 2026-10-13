import datetime as dt
import coordination as c

T = dt.date(2026, 10, 13)
fails = []
def check(name, cond):
    if not cond:
        fails.append(name); print("FAIL", name)

D = dt.date
# validate_study
cl, er = c.validate_study({"modality": "oct", "eye": "right", "status": "performed", "performed_date": "2026-10-01"}, T)
check("valid study", not er and cl["performed_date"] == D(2026, 10, 1))
_, er = c.validate_study({"modality": "oct", "eye": "right", "status": "performed"}, T)
check("performed needs date", any("date" in e.lower() for e in er))
_, er = c.validate_study({"modality": "oct", "eye": "right", "status": "performed", "performed_date": "2026-12-01"}, T)
check("future performed rejected", any("future" in e for e in er))
_, er = c.validate_study({"modality": "oct", "eye": "right", "status": "performed", "performed_date": "2026-09-01", "ordered_date": "2026-09-05"}, T)
check("performed before ordered rejected", bool(er))
_, er = c.validate_study({"modality": "zzz", "eye": "", "status": "x"}, T)
check("bad choices rejected", len(er) == 3)
cl, er = c.validate_study({"modality": "liver_imaging", "status": "ordered"}, T)
check("liver defaults to not eye-specific", not er and cl["eye"] == "na")
_, er = c.validate_study({"modality": "oct", "eye": "left", "status": "ordered", "ordered_date": "13/10/2026"}, T)
check("bad date text rejected", bool(er))

# duplicates
S = lambda **k: dict(dict(id="1", modality="oct", eye="right", status="performed", performed_date=D(2026, 10, 1), ordered_date=None, location="UF"), **k)
m = c.find_recent([S()], "oct", "right", T)
check("recent found", len(m) == 1 and m[0]["days_ago"] == 12 and m[0]["kind"] == "recent")
check("other eye not flagged", c.find_recent([S()], "oct", "left", T) == [])
check("both overlaps right", len(c.find_recent([S()], "oct", "both", T)) == 1)
check("other modality not flagged", c.find_recent([S()], "b_scan", "right", T) == [])
check("old study not flagged", c.find_recent([S(performed_date=D(2026, 6, 1))], "oct", "right", T) == [])
check("na only matches na", c.find_recent([S(modality="liver_imaging", eye="na")], "liver_imaging", "right", T) == [])
p = c.find_recent([S(status="ordered", performed_date=None, ordered_date=D(2026, 10, 5))], "oct", "right", T)
check("pending order flagged", len(p) == 1 and p[0]["kind"] == "pending" and p[0]["days_ago"] == 8)
check("exclude self", c.find_recent([S()], "oct", "right", T, exclude_id="1") == [])
check("message mentions place", "at UF" in c.duplicate_message(m) and "12 days ago" in c.duplicate_message(m))
check("no message when none", c.duplicate_message([]) is None)

# summary
sm = c.summarize_studies([S(id="a", performed_date=D(2026, 9, 1)), S(id="b", performed_date=D(2026, 10, 1)),
                          S(id="c", status="ordered", performed_date=None, ordered_date=D(2026, 10, 3), modality="b_scan")], T)
check("latest keeps newest", len(sm["latest"]) == 1 and sm["latest"][0]["performed_date"] == "2026-10-01")
check("waiting listed with days", sm["waiting"][0]["days_waiting"] == 10)

# add_months
check("add months", c.add_months(D(2026, 8, 31), 6) == D(2027, 2, 28))
check("add months leap", c.add_months(D(2024, 8, 31), 6) == D(2025, 2, 28) and c.add_months(D(2023, 8, 31), 6) == D(2024, 2, 29))
check("add months year roll", c.add_months(D(2026, 11, 15), 3) == D(2027, 2, 15))

# surveillance
st = c.surveillance_status({"last_done": D(2026, 3, 1), "interval_months": 6}, T)
check("overdue", st["status"] == "overdue" and st["due_date"] == "2026-09-01" and st["days_overdue"] == 42)
st = c.surveillance_status({"last_done": D(2026, 5, 1), "interval_months": 6}, T)
check("due soon", st["status"] == "due_soon" and st["days_until"] == 19)
st = c.surveillance_status({"last_done": D(2026, 10, 1), "interval_months": 6}, T)
check("ok", st["status"] == "ok")
st = c.surveillance_status({"last_done": None, "start_date": D(2026, 10, 1), "interval_months": 6}, T)
check("never done past start is overdue", st["status"] == "overdue" and st["never_done"])
st = c.surveillance_status({"last_done": None, "start_date": None, "interval_months": 6}, T)
check("no date is not 'ok'", st["status"] == "no_date")
_, er = c.validate_surveillance({"kind": "liver", "label": "Liver MRI"})
check("interval required, no default", any("how often" in e for e in er))
_, er = c.validate_surveillance({"kind": "liver", "label": "Liver MRI", "interval_months": 0})
check("interval 0 rejected", bool(er))
cl, er = c.validate_surveillance({"kind": "liver", "label": " Liver MRI ", "interval_months": "6", "study_type": "liver_imaging"})
check("valid surveillance", not er and cl["label"] == "Liver MRI" and cl["interval_months"] == 6)
items = [{"id": 1, "study_type": "oct", "eye": "right"}, {"id": 2, "study_type": "oct", "eye": None}, {"id": 3, "study_type": "oct", "eye": "left"}, {"id": 4}]
check("matching items", [i["id"] for i in c.matching_items(items, "oct", "right")] == [1, 2])

# outcomes
cl, er = c.validate_outcome({"status": "followed", "note": "Plaque placed.", "event_date": "2026-10-01"}, T)
check("valid outcome", not er)
_, er = c.validate_outcome({"status": "followed", "note": ""}, T)
check("outcome needs note", bool(er))
_, er = c.validate_outcome({"status": "nope", "note": "x"}, T)
check("outcome needs status", bool(er))

# agenda
cs = [dict(case_id="a", has_decision=False, ready=True, readiness_pct=90, on_agenda=False),
      dict(case_id="b", has_decision=True, ready=True, readiness_pct=100, on_agenda=False),
      dict(case_id="c", has_decision=False, ready=False, readiness_pct=40, on_agenda=False),
      dict(case_id="d", has_decision=False, ready=True, readiness_pct=100, on_agenda=True),
      dict(case_id="e", has_decision=False, ready=True, readiness_pct=100, on_agenda=False)]
check("agenda suggestions", [x["case_id"] for x in c.suggest_agenda(cs)] == ["e", "a"])

# trials
trial, er = c.validate_trial({"name": "Test trial", "nct_id": "nct12345678", "criteria": [
    {"kind": "inclusion", "field_key": "apical_height_mm", "op": "at_least", "value": "2.5"},
    {"kind": "inclusion", "field_key": "chr3_monosomy", "op": "is", "value": "present"},
    {"kind": "exclusion", "field_key": "bap1_germline_variant", "op": "is", "value": "present"}]})
check("trial valid", not er and trial["nct_id"] == "NCT12345678")
_, er = c.validate_trial({"name": "x", "nct_id": "12345"})
check("bad NCT", bool(er))
_, er = c.validate_trial({"name": "x", "criteria": [{"kind": "inclusion", "field_key": "a", "op": "at_least", "value": "big"}]})
check("numeric needs number", bool(er))
_, er = c.validate_trial({"name": "", "criteria": []})
check("name needed", bool(er))

r = c.match_trial(trial, {"apical_height_mm": "3.1", "chr3_monosomy": "true", "bap1_germline_variant": "false"})
check("possible match", r["overall"] == "possible_match")
r = c.match_trial(trial, {"apical_height_mm": "3.1", "chr3_monosomy": "true"})
check("unrecorded exclusion -> needs data, not match", r["overall"] == "needs_data" and r["unknown_count"] == 1)
r = c.match_trial(trial, {"apical_height_mm": "1.9", "chr3_monosomy": "true", "bap1_germline_variant": "false"})
check("too thin fails", r["overall"] == "not_a_match")
r = c.match_trial(trial, {"apical_height_mm": "3.1", "chr3_monosomy": "true", "bap1_germline_variant": "true"})
check("exclusion present fails", r["overall"] == "not_a_match" and r["fail_count"] == 1)
r = c.match_trial(trial, {})
check("nothing recorded -> needs data", r["overall"] == "needs_data" and r["unknown_count"] == 3)
r = c.match_trial(trial, {"apical_height_mm": "3.1 mm", "chr3_monosomy": "false"})
check("units tolerated; absent monosomy fails inclusion", r["overall"] == "not_a_match")
t2, _ = c.validate_trial({"name": "t", "criteria": [{"kind": "inclusion", "field_key": "gep_class", "op": "one_of", "value": "2, Class 2"}]})
check("one_of", c.match_trial(t2, {"gep_class": "Class 2"})["overall"] == "possible_match"
      and c.match_trial(t2, {"gep_class": "1A"})["overall"] == "not_a_match")
check("no criteria", c.match_trial({"criteria": []}, {})["overall"] == "no_criteria")
check("blank value is unknown", c.match_trial(trial, {"apical_height_mm": "  "})["criteria"][0]["state"] == "unknown")
check("unparseable number is unknown", c.match_trial(trial, {"apical_height_mm": "thick"})["criteria"][0]["state"] == "unknown")

print("ALL PASS" if not fails else "%d FAILED" % len(fails))
raise SystemExit(1 if fails else 0)
