"""
Patient journey: turns a uveal melanoma case's recorded findings into plain
language, step by step.

Design rules (deliberate, and the same as the existing Care Summary):

  * RULE-BASED, NOT GENERATED. Every sentence a patient can see is either
    fixed text written here once, or filled from a small whitelist of
    patterns (a GEP class, a measurement in mm, a few exact phrases). Free
    text typed by clinicians is NEVER echoed to the patient. Anything that is
    not recognised falls back to "your doctor will explain this".
  * NOTHING IS GUESSED. If a value is missing the step says so; if a value is
    ambiguous the translation is the cautious one.
  * RESULTS ARE HELD UNTIL THE CLINICIAN RELEASES THEM. Until the care team
    marks results as shared, each step shows only what the step is and
    whether it is done. The interpretive "what we found" lines appear after
    release (or in staff preview). This protects a patient from reading a
    risk class alone before anyone has talked it through.
  * This module is pure logic: no database, no web framework. The endpoint in
    main.py gathers the inputs; test_patient_journey.py checks the logic.

The wording has NOT been reviewed by a clinician yet. Dr. Ebrahimiadib should
read every string in this file before patients see it.
"""

import datetime as _dt
import re

DISCLAIMER = (
    "This page explains your care in everyday language. It does not replace "
    "a conversation with your doctors, and it cannot tell you what will "
    "happen to you. Percentages and risk groups describe large groups of "
    "people, not any one person. Your physicians make all medical decisions "
    "with you."
)

# Care stage -> the first journey step (1-based) that can still be "current".
# Earlier steps are treated as finished once the care team has moved the case
# to these stages. Early stages (Diagnosis / Imaging / Case Preparation) imply
# nothing, so those steps are judged only by what is actually recorded.
STAGE_FLOOR = {
    "Diagnosis": 2,
    "Imaging": 2,
    "Case Preparation": 2,
    "Multidisciplinary Review": 6,
    "Treatment Planning": 7,
    "Treatment": 8,
    "Surveillance": 9,
}

IMAGING_TESTS = [
    ("b_scan", "Ultrasound (B-scan)",
     "uses sound waves to measure how thick the tumor is and to look inside it"),
    ("oct", "OCT scan",
     "takes a very detailed cross-section picture of the retina, a bit like an ultrasound that uses light"),
    ("optos", "Wide-angle eye photos (Optos)",
     "photographs the back of your eye, including the edges"),
    ("faf", "Autofluorescence photos (FAF)",
     "uses a safe blue light to show how the tissue at the back of your eye is behaving"),
]

TREATMENT_PLAIN = {
    "Iodine-125 (I-125) plaque brachytherapy": {
        "summary": (
            "A small radioactive disc (a \"plaque\") will be stitched onto the outside wall of your eye, "
            "next to the tumor, for several days and then removed. It delivers radiation straight to the "
            "tumor while sparing as much of your eye and vision as possible."),
        "what_to_expect": (
            "Two short operations under anesthesia: one to attach the plaque and one, a few days later, to "
            "take it out. You will be given safety instructions while the plaque is in place. Vision can "
            "change slowly over months to years after radiation, so your eye doctor will check you regularly."),
    },
    "Proton beam radiation therapy": {
        "summary": (
            "A focused beam of radiation particles will be aimed precisely at the tumor over a series of "
            "short outpatient sessions."),
        "what_to_expect": (
            "First, a short operation places tiny metal markers on the eye so the beam can be aimed exactly. "
            "Then you come for a few short, painless sessions, usually within about a week. Vision can change "
            "slowly over months to years after radiation, so your eye doctor will check you regularly."),
    },
    "Enucleation": {
        "summary": (
            "Surgery to remove the affected eye. This is usually recommended when the tumor is too large for "
            "treatments that keep the eye."),
        "what_to_expect": (
            "You will have the surgery under anesthesia. After the area heals, a custom artificial eye is made "
            "to match your other eye. Your team can connect you with others who have been through this and "
            "with support for adjusting to vision in one eye."),
    },
    "Active surveillance (observation)": {
        "summary": (
            "No treatment right now. Your care team will watch the tumor closely with regular exams and "
            "imaging, and recommend treatment only if it shows signs of growth."),
        "what_to_expect": (
            "You will not need an operation or radiation at this time. What matters is keeping every "
            "follow-up appointment so that any change is noticed early."),
    },
}

SURVEILLANCE_PLAIN = {
    "Liver MRI every 6 months": (
        "An MRI of your liver every 6 months, because the liver is the most common place this cancer can "
        "spread to. Catching any change early gives more treatment options."),
    "Liver MRI every 12 months": "An MRI of your liver once a year to check for any spread.",
    "Annual LFTs + imaging": (
        "A yearly blood test that checks how your liver is working, together with imaging, to watch for any "
        "signs of spread."),
}

GLOSSARY = [
    ("Uveal melanoma", "A cancer that starts in the pigment cells of the middle layer of the eye (the uvea). It is rare, and doctors who treat it are called ocular oncologists."),
    ("Ocular oncologist", "An eye doctor who specializes in tumors of the eye."),
    ("Choroid", "A layer of blood vessels and pigment cells in the back wall of the eye. Most uveal melanomas start here."),
    ("Ciliary body", "A ring of tissue behind the colored part of the eye that helps the eye focus."),
    ("Iris", "The colored part of the eye."),
    ("Basal diameter", "How wide the tumor is where it sits on the eye wall, measured in millimeters (mm)."),
    ("Apical height (thickness)", "How tall the tumor is, from the eye wall to its top, measured in millimeters (mm)."),
    ("B-scan", "An ultrasound of the eye. It uses sound waves, so there is no radiation."),
    ("OCT", "Optical coherence tomography: a quick, painless scan that takes a detailed cross-section picture of the retina."),
    ("Subretinal fluid", "A small amount of fluid that collects under the retina, sometimes next to a tumor."),
    ("Metastasis", "Cancer that has spread from where it started to another part of the body. For uveal melanoma, the liver is the most common place."),
    ("Gene expression profile (GEP)", "A test on a small sample of the tumor that looks at which genes are active. It estimates how the tumor is likely to behave in the future. It does not show whether cancer has already spread."),
    ("Chromosome 3", "Tumor cells that are missing one copy of chromosome 3 (\"monosomy 3\") are linked with a higher chance of spread; cells with both copies (\"disomy 3\") are linked with a lower chance."),
    ("Tumor board", "A meeting where specialists from several fields review your results together and agree on a recommended plan."),
    ("Plaque brachytherapy", "Radiation given from a tiny radioactive disc stitched to the outside of the eye for a few days."),
    ("Proton beam therapy", "Radiation given from outside the body with a precisely aimed beam of particles."),
    ("Enucleation", "Surgery to remove the eye."),
    ("Surveillance", "Regular checkups and scans after (or instead of) treatment so that any change is found early."),
]

# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


def _clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _has(values, key):
    return _clean(values.get(key)) is not None


def _is_not_applicable(text):
    return bool(text) and text.strip().lower().startswith("not applicable")


def _fmt_mm(x):
    s = ("%.1f" % x)
    return s[:-2] if s.endswith(".0") else s


def _parse_measurements(values, basal_mm, apical_mm):
    """Returns (largest_basal_mm, thickness_mm). Structured numbers win; the
    free-text description is only parsed for two exact, standard phrases."""
    basal = basal_mm if isinstance(basal_mm, (int, float)) and basal_mm > 0 else None
    apical = apical_mm if isinstance(apical_mm, (int, float)) and apical_mm > 0 else None
    text = (_clean(values.get("tumor_dimensions")) or "").lower()
    if basal is None:
        m = re.search(r"basal diameter\s*([0-9]+(?:\.[0-9]+)?)(?:\s*(?:x|×)\s*([0-9]+(?:\.[0-9]+)?))?", text)
        if m:
            nums = [float(g) for g in m.groups() if g]
            basal = max(nums)
    if apical is None:
        m = re.search(r"(?:apical height|thickness)\s*([0-9]+(?:\.[0-9]+)?)", text)
        if m:
            apical = float(m.group(1))
    return basal, apical


def _fmt_date(raw):
    s = _clean(raw)
    if not s:
        return None
    try:
        d = _dt.date.fromisoformat(s[:10])
        return d.strftime("%B %d, %Y").replace(" 0", " ")
    except ValueError:
        return None


def diagnosis_sentence(diagnosis, laterality):
    side = {"OD": "right eye", "OS": "left eye", "OU": "both eyes"}.get(laterality or "", "eye")
    d = (diagnosis or "").lower()
    if "melanoma" in d:
        if "choroidal" in d:
            where = "the choroid, a layer of blood vessels and pigment cells in the back wall of your %s" % side
        elif "ciliary" in d:
            where = "the ciliary body, a ring of tissue just behind the colored part of your %s" % side
        elif "iris" in d:
            where = "the iris, the colored part of your %s" % side
        else:
            where = "the middle layer of your %s" % side
        return ("You have a tumor in %s. It is called uveal melanoma. It is a rare cancer, "
                "and your team has experience treating it." % where)
    return "Your care team has been evaluating a condition in your %s." % side


# ----------------------------------------------------------------------------
# interpretive finding translators (whitelisted; each returns [] when unsure)
# ----------------------------------------------------------------------------


def _imaging_findings(values):
    out = []
    text = " ".join((_clean(values.get(k)) or "") for k, _n, _d in IMAGING_TESTS).lower()
    if re.search(r"\bno (?:evidence of )?(?:extraocular )?extension\b", text):
        out.append("The scans did not show the tumor growing outside the eye.")
    if re.search(r"\bno (?:significant |obvious )?subretinal fluid\b", text):
        out.append("No fluid was seen under the retina.")
    elif "subretinal fluid" in text:
        out.append("Some fluid was seen under the retina near the tumor. Your doctor will explain what this means for you.")
    return out


def _measurement_findings(values, basal, apical, diagnosis):
    out = []
    if basal and apical:
        out.append("Your tumor measures about %s mm across at its widest point and about %s mm thick. "
                   "For comparison, a US dime is about 18 mm across." % (_fmt_mm(basal), _fmt_mm(apical)))
    elif basal:
        out.append("Your tumor measures about %s mm across at its widest point. "
                   "For comparison, a US dime is about 18 mm across." % _fmt_mm(basal))
    elif apical:
        out.append("Your tumor is about %s mm thick." % _fmt_mm(apical))
    elif _has(values, "tumor_dimensions"):
        out.append("Your tumor's measurements have been recorded. Your doctor will go over the numbers with you.")
    if _has(values, "tumor_location"):
        out.append("Your doctor can show you exactly where the tumor sits on your own scan images.")
    return out


_CONCERN = re.compile(r"suspicious|lesion|mass|nodule|positive|indeterminate|concern|metasta(?:sis|tic) (?:disease|lesion)|abnormal")
_CLEAR = re.compile(r"\bno (?:radiographic |imaging |clinical )?(?:evidence|signs?) of (?:metast\w*|distant|spread)|\bno metast\w* (?:disease|lesions?) (?:seen|identified|detected)\b")


def _body_findings(values):
    text = (_clean(values.get("metastatic_workup")) or "").lower()
    if not text:
        return []
    residual = _CLEAR.sub("", text)
    if _CONCERN.search(residual):
        return ["Your results from this check have been recorded, and some items need a closer look. "
                "Your doctor will go through them with you in person."]
    if _CLEAR.search(text):
        return ["No signs that the cancer has spread were found in these tests. "
                "Because it can still appear later, you will have regular checkups (see \"Checkups and monitoring\")."]
    return ["Your results from this check have been recorded. Your doctor will explain them to you."]


GEP_PLAIN = {
    "1a": ("Class 1A", "Your tumor falls in Class 1A, the lowest-risk group of this test."),
    "1b": ("Class 1B", "Your tumor falls in Class 1B, the middle group of this test."),
    "2": ("Class 2", "Your tumor falls in Class 2, the higher-risk group of this test."),
}


def _gep_findings(values):
    cls_raw = _clean(values.get("gep_class")) or ""
    result_raw = _clean(values.get("gep_result")) or ""
    out = []
    key = cls_raw.lower().replace(" ", "").replace("class", "")
    if key not in GEP_PLAIN:
        m = re.search(r"class\s*(1a|1b|2)\b", result_raw.lower())
        key = m.group(1) if m else None
    if key in GEP_PLAIN:
        out.append(GEP_PLAIN[key][1])
        out.append("This describes groups of people with similar tumors, not what will definitely happen to you. "
                   "It does not mean the cancer has spread. Your team uses it to decide how closely to monitor you.")
    elif cls_raw or result_raw:
        out.append("Your genetic test result has been recorded. Your doctor will explain what it means.")
    chr3 = ((_clean(values.get("chromosome_3_status")) or "") + " " + result_raw).lower()
    if "monosomy" in chr3:
        out.append("The tumor cells are missing one copy of chromosome 3. Doctors use this as one more clue when planning monitoring.")
    elif "disomy" in chr3:
        out.append("The tumor cells have both copies of chromosome 3, which is linked with a lower chance of spread.")
    return out


# ----------------------------------------------------------------------------
# main entry
# ----------------------------------------------------------------------------


def build_journey(*, disease_key, diagnosis, laterality, care_stage, values,
                  basal_mm=None, apical_mm=None, decision=None,
                  released=False, preview=False):
    """
    values:   dict of field key -> text, containing only COMPLETE values.
    decision: None or dict(recommendation, surveillance_protocol, follow_up_date)
    Returns a dict, or None when this disease has no patient journey yet.
    """
    if disease_key != "uveal_melanoma":
        return None

    show_findings = bool(released or preview)
    floor = STAGE_FLOOR.get(care_stage, 2)
    rec = _clean(decision.get("recommendation")) if decision else None
    surv = _clean(decision.get("surveillance_protocol")) if decision else None
    follow_up = _fmt_date(decision.get("follow_up_date")) if decision else None
    basal, apical = _parse_measurements(values, basal_mm, apical_mm)

    steps = []

    def add(key, title, plain, done, **kw):
        steps.append(dict(key=key, title=title, plain=plain, done=done, **kw))

    # 1 ----------------------------------------------------------------
    add("first_visit", "Something was noticed",
        "Uveal melanoma is often found during a routine eye exam, or after symptoms such as blurry vision, "
        "flashes of light, floaters, or a shadow in your vision. Many people have no symptoms at all. Your eye "
        "doctor referred you to a specialist who treats tumors of the eye (an ocular oncologist).",
        True,
        checklist=[], findings=[],
        next_up="A close look at your eye with special scans.",
        questions=["Can you show me on a picture where the tumor is?",
                   "Is there anything I should avoid doing right now?"])

    # 2 ----------------------------------------------------------------
    recorded = [(k, n, d) for k, n, d in IMAGING_TESTS if _has(values, k)]
    add("imaging", "A close look at your eye",
        "Several painless tests photograph and scan the inside of your eye. Together they show how big the "
        "tumor is, how thick it is, and what is going on around it. You may have drops to widen your pupils, "
        "which can make things blurry and sensitive to light for a few hours.",
        len(recorded) == len(IMAGING_TESTS),
        checklist=[dict(label=n, explain=d, done=_has(values, k)) for k, n, d in IMAGING_TESTS],
        findings=_imaging_findings(values),
        progress_note="%d of %d eye tests recorded" % (len(recorded), len(IMAGING_TESTS)),
        next_up="Measuring the tumor precisely.",
        questions=["Are any more scans needed before decisions are made?",
                   "Can I see my own scan images?"])

    # 3 ----------------------------------------------------------------
    measured = bool(basal or apical or _has(values, "tumor_dimensions"))
    add("measuring", "Measuring the tumor",
        "The size and exact position of a tumor are two of the main things that guide treatment. Size is "
        "measured in millimeters (mm), using two numbers: how wide the tumor is at its base and how thick "
        "it is. Where it sits in the eye (the choroid, the ciliary body, or the iris) also matters.",
        measured,
        checklist=[dict(label="Tumor measurements", explain=None, done=measured),
                   dict(label="Tumor location", explain=None, done=_has(values, "tumor_location"))],
        findings=_measurement_findings(values, basal, apical, diagnosis),
        next_up="Checking the rest of your body.",
        questions=["How do my measurements compare with the usual treatment cut-offs?",
                   "Will the tumor be measured again, and how often?"])

    # 4 ----------------------------------------------------------------
    body_done = _has(values, "metastatic_workup")
    add("body_check", "Checking the rest of your body",
        "When uveal melanoma spreads, it most often goes to the liver. At the time of diagnosis most people "
        "have no spread, but your team checks to be sure and to create a starting point to compare against "
        "later. This usually means blood tests that show how your liver is working, plus a scan of the liver "
        "and of the chest. Your other eye is also examined.",
        body_done,
        checklist=[dict(label="Baseline check for spread (blood tests and scans)", explain=None, done=body_done),
                   dict(label="Exam of your other eye", explain=None, done=_has(values, "fellow_eye_status"))],
        findings=_body_findings(values),
        sensitive=True,
        next_up="A genetic test on the tumor, if it applies to you.",
        questions=["Which scans did I have, and where can I see the results?",
                   "How often will my liver be checked from now on?"])

    # 5 ----------------------------------------------------------------
    gep_text = _clean(values.get("gep_result")) or ""
    gep_na = _is_not_applicable(gep_text)
    gep_done = _has(values, "gep_class") or _has(values, "gep_result")
    add("genetic_test", "Testing the tumor's genes",
        "A tiny sample of the tumor (taken with a very fine needle, or from tissue removed during treatment) "
        "can be tested to see which genes are active. This is called a gene expression profile. It estimates "
        "how the tumor is likely to behave over the coming years. It does NOT show whether cancer has "
        "already spread, and it is not a prediction for any one person.",
        gep_done,
        checklist=[dict(label="Genetic (gene expression) test", explain=None, done=gep_done and not gep_na)],
        findings=([] if gep_na else _gep_findings(values)),
        sensitive=True,
        not_needed=gep_na,
        not_needed_note=("This test is not used for the type or location of tumor you have, so it is not needed. "
                         "Your team uses other features of the tumor to judge risk." if gep_na else None),
        next_up="Your specialists meet to review everything together.",
        questions=["Is this test right for my type of tumor?",
                   "How will the result change how closely I am monitored?"])

    # 6 ----------------------------------------------------------------
    add("team_review", "Your specialists review your case together",
        "Eye cancer surgeons, radiation doctors, retina specialists, radiologists, cancer doctors and others "
        "look at all of your results at one meeting, called a tumor board. You do not need to attend. Their job "
        "is to agree on the plan they believe is best for you, which your own doctor then discusses with you.",
        bool(decision),
        checklist=[dict(label="Team recommendation recorded", explain=None, done=bool(decision))],
        findings=[],
        next_up="Choosing your treatment together with your doctor.",
        questions=["Which specialists reviewed my case?",
                   "Was there any disagreement about the best plan?"])

    # 7 ----------------------------------------------------------------
    t = TREATMENT_PLAIN.get(rec) if rec else None
    t_findings = []
    if rec:
        t_findings.append(("The team's recommendation: " + t["summary"]) if t else
                          "Your care team has recommended a plan. Please ask them to walk you through the details.")
    add("treatment_choice", "Choosing your treatment",
        "Treatment depends on the size and position of the tumor, the health of your eye, and your own "
        "priorities. The main options are: a radioactive disc stitched to the eye for a few days (plaque "
        "therapy); a precisely aimed beam of radiation (proton beam therapy); surgery to remove the eye "
        "(enucleation), usually only for larger tumors; or, for very small tumors, close watching first. "
        "The decision is made with you, not for you.",
        bool(rec),
        checklist=[dict(label="Treatment plan decided", explain=None, done=bool(rec))],
        findings=t_findings,
        next_up="Your treatment.",
        questions=["What are the pros and cons of each option for my vision?",
                   "How much time will I need off work or away from driving?",
                   "Are there any clinical trials I could join?"])

    # 8 ----------------------------------------------------------------
    watch = rec == "Active surveillance (observation)"
    treated = _has(values, "date_of_radiation") or _has(values, "date_of_surgery")
    tx_findings = []
    if rec and t:
        tx_findings.append("What to expect: " + t["what_to_expect"])
    for k, label in (("date_of_radiation", "Radiation date"), ("date_of_surgery", "Surgery date")):
        f = _fmt_date(values.get(k))
        if f:
            tx_findings.append("%s: %s." % (label, f))
    add("treatment", "Treatment",
        "Your treatment is carried out by a team that does this regularly. Before it starts you will "
        "get written instructions, and your team will tell you what to expect on the day and in the weeks "
        "after.",
        treated or floor >= 9,
        checklist=[dict(label="Treatment completed", explain=None, done=treated or floor >= 9)],
        findings=tx_findings,
        not_needed=watch,
        not_needed_note=("Your team recommended watching closely instead of treating right now." if watch else None),
        next_up="Regular checkups and monitoring.",
        questions=["What should I expect on the day of treatment?",
                   "Which symptoms mean I should call right away?"])

    # 9 ----------------------------------------------------------------
    m_findings = []
    if surv:
        m_findings.append("Your follow-up plan: " + SURVEILLANCE_PLAIN.get(
            surv, "A follow-up plan has been set. Ask your care team for the details."))
    add("monitoring", "Checkups and monitoring",
        "Even after successful treatment, you will have regular checkups for many years. They have two "
        "purposes: eye exams to check the tumor and to catch and treat side effects of radiation early, and "
        "body checks (usually liver scans and blood tests) because, in some people, the cancer can spread "
        "later, sometimes years afterward. Finding any change early gives you the most options. Your team "
        "sets the schedule that fits you.",
        False,
        checklist=[dict(label="Follow-up plan set", explain=None, done=bool(surv))],
        findings=m_findings,
        ongoing=True,
        next_up="Keep every appointment. Tell your team about any change in your vision or health.",
        questions=["How often will I have eye exams and liver scans?",
                   "Who do I call if I notice a change between visits?"])

    # status assignment --------------------------------------------------
    # A step is finished when its data is recorded, when the care team has
    # already moved the case past it (STAGE_FLOOR), or when it does not apply.
    # The first unfinished step is "current"; the rest are "upcoming".
    # Monitoring never finishes: it becomes current once everything before it
    # is finished.
    current_idx = None
    for i, s in enumerate(steps):
        s["number"] = i + 1
        finished = bool(s["done"] or s["number"] < floor or s.get("not_needed"))
        if s.get("ongoing"):
            continue
        if s.get("not_needed"):
            s["status"] = "not_needed"
        elif finished:
            s["status"] = "done"
        elif current_idx is None:
            s["status"] = "current"
            current_idx = i
        else:
            s["status"] = "upcoming"
    mon = steps[-1]
    if current_idx is None:
        mon["status"] = "current"
        current_idx = len(steps) - 1
    else:
        mon["status"] = "upcoming"

    # gating -------------------------------------------------------------
    out_steps = []
    for s in steps:
        sensitive_or_interpretive = s["findings"]
        held = bool(sensitive_or_interpretive) and not show_findings and s["key"] != "first_visit"
        out_steps.append(dict(
            key=s["key"], number=s["number"], title=s["title"], status=s["status"],
            what_is_this=s["plain"],
            checklist=s["checklist"],
            progress_note=s.get("progress_note"),
            findings=([] if held else s["findings"]),
            findings_held_back=held,
            not_needed_note=s.get("not_needed_note"),
            ongoing=bool(s.get("ongoing")),
            next_up=s["next_up"],
            questions=s["questions"],
        ))

    cur = out_steps[current_idx]
    return dict(
        version=1,
        diagnosis_plain=diagnosis_sentence(diagnosis, laterality),
        total_steps=len(out_steps),
        current_step=cur["number"],
        current_title=cur["title"],
        headline="You are at step %d of %d: %s" % (cur["number"], len(out_steps), cur["title"]),
        results_shared=bool(released),
        preview=bool(preview and not released),
        next_follow_up=follow_up,
        steps=out_steps,
        support=dict(
            title="Support for you along the way",
            plain=("Hearing that you have a cancer, and going through tests and treatment, is stressful for "
                   "almost everyone. Feeling worried, tired, or overwhelmed is normal, and support is part of "
                   "good care. Counselors, social workers, and patient support groups for eye cancer exist, "
                   "and your team can connect you. Please tell them if you are struggling, or if cost, "
                   "travel, or work is getting in the way of your care."),
            counseling_documented=_has(values, "patient_counseling"),
        ),
        glossary=[dict(term=t, meaning=m) for t, m in GLOSSARY],
        disclaimer=DISCLAIMER,
    )
