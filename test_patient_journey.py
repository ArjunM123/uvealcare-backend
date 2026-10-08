"""Run with:  python3 test_patient_journey.py   (prints ALL PASS or the first failure)"""
import json
import patient_journey as pj

fails = []
def check(name, cond, extra=""):
    if not cond:
        fails.append(name)
        print("FAIL:", name, extra)

def J(**kw):
    base = dict(disease_profile=None, disease_key="uveal_melanoma", diagnosis="Choroidal Melanoma OD",
                laterality="OD", care_stage="Diagnosis", values={}, released=False, preview=False)
    base.update(kw)
    base.pop("disease_profile")
    return pj.build_journey(**base)

def step(j, key):
    return next(s for s in j["steps"] if s["key"] == key)

# 1. other diseases have no journey
check("sarcoma none", pj.build_journey(disease_key="soft_tissue_sarcoma", diagnosis="x", laterality=None,
                                       care_stage="Diagnosis", values={}) is None)

# 2. brand-new case: imaging is current, nothing else done
j = J()
check("new: 10 steps? (9 + support separate)", j["total_steps"] == 9)
check("new: current is imaging", j["current_step"] == 2, j["headline"])
check("new: first_visit done", step(j, "first_visit")["status"] == "done")
check("new: later steps upcoming", all(s["status"] == "upcoming" for s in j["steps"][2:]))

# 3. Carol: case prep, 2 of 4 imaging tests
j = J(care_stage="Case Preparation", values={"b_scan": "Mushroom-shaped ... UNIQUE_TOKEN_A", "oct": "Subretinal fluid extending beyond margin"})
check("carol: imaging current", step(j, "imaging")["status"] == "current")
check("carol: note 2 of 4", step(j, "imaging")["progress_note"] == "2 of 4 eye tests recorded")
check("carol: held back before release", step(j, "imaging")["findings"] == [] and step(j, "imaging")["findings_held_back"])
jp = J(care_stage="Case Preparation", values={"b_scan": "x", "oct": "Subretinal fluid extending beyond margin"}, preview=True)
check("carol: preview shows fluid", any("fluid" in f for f in step(jp, "imaging")["findings"]))
check("carol: preview flag", jp["preview"] is True and jp["results_shared"] is False)

# 4. free text never echoed
j = J(care_stage="Case Preparation", released=True,
      values={"b_scan": "UNIQUE_TOKEN_A", "metastatic_workup": "UNIQUE_TOKEN_B all clear", "tumor_location": "UNIQUE_TOKEN_C"})
blob = json.dumps(j)
check("no raw text leaks", not any(t in blob for t in ("UNIQUE_TOKEN_A", "UNIQUE_TOKEN_B", "UNIQUE_TOKEN_C")))

# 5. measurements: structured wins; text parsed
j = J(released=True, values={"tumor_dimensions": "Basal diameter 9.4 x 7.8 mm. Apical height 3.2 mm."})
f = step(j, "measuring")["findings"][0]
check("text parse 9.4/3.2", "9.4 mm across" in f and "3.2 mm thick" in f, f)
j = J(released=True, basal_mm=12.0, apical_mm=4.55, values={"tumor_dimensions": "Basal diameter 9.4"})
f = step(j, "measuring")["findings"][0]
check("structured wins", "12 mm across" in f, f)
j = J(released=True, values={"tumor_dimensions": "11 by 9"})
check("unparseable falls back", "recorded" in step(j, "measuring")["findings"][0])

# 6. imaging translations
v = {"b_scan": "no extraocular extension", "oct": "No subretinal fluid.", "optos": "x", "faf": "x"}
j = J(released=True, values=v)
fs = step(j, "imaging")["findings"]
check("extension negative", any("outside the eye" in x for x in fs))
check("no fluid", any(x.startswith("No fluid") for x in fs))
check("imaging done", step(j, "imaging")["status"] == "done")
j = J(released=True, values={"oct": "Trace subretinal fluid at tumor margin."})
check("trace fluid -> some fluid", any("Some fluid" in x for x in step(j, "imaging")["findings"]))
j = J(released=True, values={"optos": "Posterior segment unremarkable, no evidence of extension."})
check("no evidence of extension", any("outside the eye" in x for x in step(j, "imaging")["findings"]))

# 7. body check: conservative
cases = {
    "No evidence of metastatic disease on MRI liver and CT chest.": "No signs",
    "Liver MRI: suspicious lesion segment 6": "closer look",
    "Negative": "recorded",
    "Liver lesion indeterminate, otherwise no evidence of metastatic disease": "closer look",
}
for text, expect in cases.items():
    j = J(released=True, values={"metastatic_workup": text})
    out = step(j, "body_check")["findings"][0]
    check("body: %s" % text, expect in out, out)

# 8. GEP
j = J(released=True, values={"gep_result": "Class 1A — low metastatic risk. Disomy 3.", "b_scan": "x"})
g = step(j, "genetic_test")
check("gep 1A", any("Class 1A" in x for x in g["findings"]) and any("both copies" in x for x in g["findings"]), g["findings"])
check("gep done", g["status"] in ("done", "current", "upcoming") and g["checklist"][0]["done"])
j = J(released=True, values={"gep_class": "2", "chromosome_3_status": "Monosomy 3"})
g = step(j, "genetic_test")
check("gep 2 + monosomy", any("higher-risk" in x for x in g["findings"]) and any("missing one copy" in x for x in g["findings"]))
check("gep 2 reassurance", any("does not mean the cancer has spread" in x for x in g["findings"]))
j = J(released=True, values={"gep_result": "Pending lab"})
check("gep unknown cautious", step(j, "genetic_test")["findings"][0].startswith("Your genetic test result has been recorded"))
j = J(care_stage="Surveillance", released=True,
      values={"gep_result": "Not applicable — GEP validated for posterior uveal melanoma"}, diagnosis="Iris Melanoma OD")
g = step(j, "genetic_test")
check("gep n/a", g["status"] == "not_needed" and g["not_needed_note"] and g["findings"] == [])
check("iris sentence", "iris" in j["diagnosis_plain"])

# 9. stage logic
for stage, cur in (("Multidisciplinary Review", 6), ("Treatment Planning", 7), ("Treatment", 8), ("Surveillance", 9)):
    j = J(care_stage=stage)
    check("stage %s -> step %d" % (stage, cur), j["current_step"] == cur, j["headline"])
# decision recorded moves past 6 and 7 even if stage is behind
dec = dict(recommendation="Proton beam radiation therapy", surveillance_protocol="Liver MRI every 6 months", follow_up_date="2026-12-03")
j = J(care_stage="Treatment Planning", decision=dec, released=True)
check("decision -> treatment current", j["current_step"] == 8, j["headline"])
check("treatment text proton", "particles" in step(j, "treatment_choice")["findings"][0])
check("expect markers", "metal markers" in step(j, "treatment")["findings"][0])
check("surveillance text", "every 6 months" in step(j, "monitoring")["findings"][0])
check("follow-up date", j["next_follow_up"] == "December 3, 2026", j["next_follow_up"])
check("monitoring upcoming", step(j, "monitoring")["status"] == "upcoming")
# observation
j = J(care_stage="Treatment Planning", decision=dict(recommendation="Active surveillance (observation)"), released=True)
check("observation: treatment not needed", step(j, "treatment")["status"] == "not_needed")
check("observation: monitoring current", j["current_step"] == 9, j["headline"])
# unknown recommendation text -> cautious
j = J(care_stage="Treatment Planning", decision=dict(recommendation="Something new"), released=True)
check("unknown rec cautious", "walk you through" in step(j, "treatment_choice")["findings"][0])
# treatment dates
j = J(care_stage="Surveillance", released=True, values={"date_of_radiation": "2026-08-20"})
check("treatment date", any("August 20, 2026" in x for x in step(j, "treatment")["findings"]))
check("bad date ignored", not any("Radiation date" in x for x in step(J(released=True, values={"date_of_radiation": "last summer"}), "treatment")["findings"]))

# 10. gating details
j = J(care_stage="Surveillance", values={"gep_result": "Class 2"}, decision=dec)
check("gating: findings hidden everywhere except step 1",
      all(s["findings"] == [] for s in j["steps"] if s["key"] != "first_visit"))
check("diagnosis sentence always shown", "uveal melanoma" in j["diagnosis_plain"])
check("gating: follow-up still shown", j["next_follow_up"] == "December 3, 2026")
check("gating: flag", all(s["findings_held_back"] for s in j["steps"] if s["key"] in ("genetic_test", "treatment_choice")))

# 11. every step has plain text, questions, next_up; support + glossary + disclaimer present
j = J()
check("all steps populated", all(s["what_is_this"] and s["questions"] and s["next_up"] for s in j["steps"]))
check("support/glossary/disclaimer", j["support"]["plain"] and len(j["glossary"]) >= 15 and "does not replace" in j["disclaimer"])
check("json serializable", json.dumps(j))
check("never says 'cure'/'prognosis' guarantees", "guarantee" not in json.dumps(j).lower())

if fails:
    print("\n%d FAILED" % len(fails)); raise SystemExit(1)
print("ALL PASS")
