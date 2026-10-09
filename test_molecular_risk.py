"""Run with:  python3 test_molecular_risk.py   (prints ALL PASS or the first failures)"""
import json
import molecular_risk as mr

fails = []
def check(name, cond, extra=""):
    if not cond:
        fails.append(name); print("FAIL:", name, extra)

T, F = "true", "false"
def dim(**kw):
    return mr.compute_tfsom_dim(kw)

# --- TFSOM-DIM ---------------------------------------------------------------
check("nothing assessed -> None", dim() is None)
d = dim(tfsom_thickness=F, tfsom_fluid=F, tfsom_symptoms=F, tfsom_orange_pigment=F, tfsom_ultrasound_hollow=F, tfsom_diameter=F)
check("0 factors complete", d["factor_count"] == 0 and d["risk_label"] == "Low" and "1.1%" in d["risk_estimate"] and not d["provisional"], d)
cases = {1: ("Moderate", "11%"), 2: ("High", "22%"), 3: ("High", "34%"), 4: ("High", "51%"), 5: ("High", "55%")}
keys = ["tfsom_thickness", "tfsom_fluid", "tfsom_symptoms", "tfsom_orange_pigment", "tfsom_ultrasound_hollow", "tfsom_diameter"]
for n, (lab, pct) in cases.items():
    vals = {k: (T if i < n else F) for i, k in enumerate(keys)}
    d = mr.compute_tfsom_dim(vals)
    check("%d factors" % n, d["factor_count"] == n and d["risk_label"] == lab and ("~" + pct) in d["risk_estimate"], d)
vals = {k: T for k in keys}
d = mr.compute_tfsom_dim(vals)
check("6 factors says table stops", d["factor_count"] == 6 and "stops at 5" in d["risk_estimate"], d)
d = dim(tfsom_diameter=T)
check("diameter alone, provisional", d["factor_count"] == 1 and d["provisional"] and "at least" in d["risk_estimate"] and d["factors_assessed"] == 1, d)
d = dim(tfsom_thickness=F)
check("one negative assessed is Low but provisional", d["risk_label"] == "Low" and d["provisional"])
check("UHHD-only factors are ignored by DIM", dim(tfsom_margin=T, tfsom_no_halo=T, tfsom_no_drusen=T) is None)
check("unrecognized text is not assumed", dim(tfsom_diameter="maybe") is None)

# --- molecular profile -------------------------------------------------------
check("empty -> None", mr.compute_molecular_profile({}) is None)
def mp(**kw): return mr.compute_molecular_profile(kw)
def f(p, key): return next((x for x in p["findings"] if x["key"] == key), None)

p = mp(gep_class="1A", prame_positive=F, chr3_monosomy=F, chr3_partial_loss=F, chr3_isodisomy=F, bap1_nuclear_loss=F)
check("low-risk profile pattern", p["pattern"].startswith("Recorded markers point toward lower"), p["pattern"])
check("no flags on concordant low profile", p["flags"] == [])
check("disomy note warns about FISH/MLPA", "isodisomy" in f(p, "chr3")["note"])

p = mp(gep_class="1B", prame_positive=T)
check("prame+ class1 higher", f(p, "prame")["direction"] == "higher" and "higher metastatic risk than the class alone" in f(p, "prame")["note"])
check("prame has no invented percentage", "%" not in f(p, "prame")["note"])
p = mp(gep_class="2", prame_positive=T)
check("prame+ class2 shorter time", "shorter time to metastasis" in f(p, "prame")["note"])
p = mp(prame_positive=T)
check("prame+ without class explained", "interpreted together" in f(p, "prame")["note"])
p = mp(gep_class="1A", prame_positive=F)
check("prame- class1 unchanged", f(p, "prame")["direction"] == "lower" and "not expected to change" in f(p, "prame")["note"])
p = mp(gep_result="Class 1A - low metastatic risk. Disomy 3.")
check("class parsed from result text", f(p, "gep")["value"] == "Class 1A")
check("disomy parsed from text", f(p, "chr3")["value"].startswith("Disomy"))
p = mp(chromosome_3_status="Monosomy 3")
check("monosomy from older text field", f(p, "chr3")["direction"] == "higher")

p = mp(chr3_monosomy=T, chr8q_gain=T, bap1_nuclear_loss=T, gep_class="2")
check("high-risk pattern", p["pattern"].startswith("Recorded markers point toward higher") and p["higher_count"] >= 4, p["pattern"])
check("concordant high has no disagreement flag", not any("disagree" in x["text"] for x in p["flags"]))
check("bap1 loss without germline -> review flag", any(x["level"] == "review" and "germline" in x["text"] for x in p["flags"]))

p = mp(chr3_isodisomy=T)
check("isodisomy higher", f(p, "chr3")["direction"] == "higher")
p = mp(chr3_partial_loss=T)
check("partial loss intermediate", f(p, "chr3")["direction"] == "intermediate")
p = mp(chr6p_gain=T, eif1ax_mutation=T)
check("6p and EIF1AX lower", f(p, "chr6p")["direction"] == "lower" and f(p, "eif1ax")["direction"] == "lower")
p = mp(sf3b1_mutation=T)
check("SF3B1 warns about late metastasis", "late" in f(p, "sf3b1")["note"].lower() and f(p, "sf3b1")["direction"] == "intermediate")

# disagreements
p = mp(gep_class="1A", chr3_monosomy=T)
check("class1 + monosomy disagree", any("GEP is Class 1" in x["text"] for x in p["flags"]))
p = mp(gep_class="1B", bap1_nuclear_loss=T)
check("class1 + BAP1 loss disagree", any("GEP is Class 1" in x["text"] for x in p["flags"]))
p = mp(gep_class="2", chr3_monosomy=F, chr3_partial_loss=F, chr3_isodisomy=F, bap1_nuclear_loss=F)
check("class2 + disomy + BAP1 retained disagree", any("GEP is Class 2" in x["text"] for x in p["flags"]))
p = mp(gep_class="2", chr3_monosomy=F, chr3_partial_loss=F, chr3_isodisomy=F)
check("class2 + disomy alone (BAP1 unknown) does NOT flag", not any("GEP is Class 2" in x["text"] for x in p["flags"]))

# germline
p = mp(bap1_germline_variant=T)
check("germline -> action flag, genetic counseling", any(x["level"] == "action" and "genetic counseling" in x["text"] for x in p["flags"]))
p = mp(bap1_germline_variant=F, bap1_nuclear_loss=T)
check("germline negative -> no germline review flag", not any("germline BAP1 testing is not recorded" in x["text"] for x in p["flags"]))

# robustness
p = mp(prame_positive="maybe", chr8q_gain="unknown")
check("unrecognized values are ignored", p is None)
check("disclaimer present and says not a validated score", "not a validated risk score" in mp(gep_class="1A")["disclaimer"])
check("json serializable", json.dumps(mp(gep_class="1A", prame_positive=T)))
check("no surveillance interval hardcoded", "months" not in json.dumps(mp(gep_class="2", prame_positive=T, chr3_monosomy=T)).lower())
check("new field list unique keys", len({k for k, _l, _c in mr.NEW_FIELDS}) == len(mr.NEW_FIELDS) == 12)

if fails:
    print("\n%d FAILED" % len(fails)); raise SystemExit(1)
print("ALL PASS")
