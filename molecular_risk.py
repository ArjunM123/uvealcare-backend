"""
Molecular and risk-score logic for uveal melanoma (pure Python: no database,
no web framework). main.py gathers recorded values; this module interprets them.

Two separate things live here, and they answer two different questions:

  1. TFSOM-DIM  - how likely is a small choroidal NEVUS to grow into melanoma?
     Published 6-factor score (Shields et al.): Thickness >2 mm, subretinal
     Fluid, Symptoms, Orange pigment, Melanoma hollow (ultrasound), and
     DIaMeter >5 mm. It is a different score from TFSOM-UHHD, which stays
     exactly as it was. The two scores share the five factors they have in
     common, so nothing is typed twice.

  2. Molecular profile - for a melanoma that already exists, how do the
     recorded tumor markers line up (GEP class, PRAME, chromosome 3, 8q, 6p,
     BAP1, SF3B1, EIF1AX), and do any of them disagree?

Rules this module follows:
  * Published numbers are used only where a published table exists (TFSOM-DIM).
    PRAME has NO number attached: the test's maker states that specific risk
    estimates for PRAME positivity have not been established, so the effect is
    described in words, never as an invented percentage.
  * The molecular result is a DESCRIPTIVE TALLY of recorded markers, not a
    validated score. It never replaces the treating team's judgment.
  * A marker that was never recorded is never assumed negative.
  * Surveillance intervals are NOT set here; they differ by center.

The wording has not yet been reviewed by a clinician.
"""

# ----------------------------------------------------------------------------
# New fields (all yes/no, entered with the existing Present / Absent buttons)
# ----------------------------------------------------------------------------

NEW_FIELDS = [
    # key, label, category
    ("tfsom_diameter", "TFSOM: Diameter >5mm", "risk_factor"),
    ("prame_positive", "PRAME Positive", "molecular"),
    ("chr3_monosomy", "Chromosome 3: Monosomy (complete loss)", "molecular"),
    ("chr3_partial_loss", "Chromosome 3: Partial Loss", "molecular"),
    ("chr3_isodisomy", "Chromosome 3: Isodisomy (copy-neutral)", "molecular"),
    ("chr8q_gain", "Chromosome 8q Gain", "molecular"),
    ("chr6p_gain", "Chromosome 6p Gain", "molecular"),
    ("bap1_nuclear_loss", "BAP1 Nuclear Loss (IHC)", "molecular"),
    ("bap1_tumor_mutation", "BAP1 Mutation in Tumor", "molecular"),
    ("bap1_germline_variant", "BAP1 Germline Pathogenic Variant", "molecular"),
    ("sf3b1_mutation", "SF3B1 Mutation", "molecular"),
    ("eif1ax_mutation", "EIF1AX Mutation", "molecular"),
]

# ----------------------------------------------------------------------------
# TFSOM-DIM
# ----------------------------------------------------------------------------

DIM_FACTORS = [
    ("tfsom_thickness", "Thickness >2mm"),
    ("tfsom_fluid", "Subretinal fluid"),
    ("tfsom_symptoms", "Symptoms"),
    ("tfsom_orange_pigment", "Orange pigment"),
    ("tfsom_ultrasound_hollow", "Melanoma hollow (ultrasound)"),
    ("tfsom_diameter", "Diameter >5mm"),
]

# Published 5-year risk of a nevus growing into melanoma, by number of factors.
DIM_RISK_PCT = {0: "1.1%", 1: "11%", 2: "22%", 3: "34%", 4: "51%", 5: "55%"}


def _flag(values, key):
    """True / False / None (not recorded or not understood)."""
    raw = values.get(key)
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("true", "yes", "present", "positive"):
        return True
    if s in ("false", "no", "absent", "negative"):
        return False
    return None


def compute_tfsom_dim(values):
    """values: dict key -> text, only for fields recorded as complete.
    Returns None when none of the six factors has been assessed."""
    assessed = [(k, lbl) for k, lbl in DIM_FACTORS if _flag(values, k) is not None]
    if not assessed:
        return None
    present = [lbl for k, lbl in assessed if _flag(values, k)]
    n = len(present)
    total = len(DIM_FACTORS)
    complete = len(assessed) == total
    if n == 0:
        label = "Low"
    elif n == 1:
        label = "Moderate"
    else:
        label = "High"
    if n >= 6:
        pct = "55% or higher (the published table stops at 5 factors)"
    else:
        pct = DIM_RISK_PCT[n]
    if complete:
        estimate = "~%s 5-year risk of growth to melanoma" % pct
    else:
        estimate = ("at least ~%s 5-year risk of growth to melanoma, so far. %d of %d factors have not been "
                    "assessed, and the real figure may be higher" % (pct, total - len(assessed), total))
    return {
        "factors_assessed": len(assessed),
        "factors_total": total,
        "factors_present": present,
        "factor_count": n,
        "risk_label": label,
        "risk_estimate": estimate,
        "provisional": not complete,
        "mnemonic": "TFSOM-DIM",
    }


# ----------------------------------------------------------------------------
# Molecular profile
# ----------------------------------------------------------------------------

HIGHER, LOWER, MIXED = "higher", "lower", "intermediate"

DISCLAIMER = (
    "This is a plain tally of the markers recorded for this tumor, not a validated risk score. "
    "No single marker decides prognosis. Interpretation and surveillance planning belong to the treating team."
)


def _gep_class(values):
    import re
    raw = (values.get("gep_class") or "").lower().replace(" ", "").replace("class", "")
    if raw in ("1a", "1b", "2"):
        return raw
    m = re.search(r"class\s*(1a|1b|2)\b", (values.get("gep_result") or "").lower())
    return m.group(1) if m else None


def _chr3_state(values):
    """Returns 'monosomy' | 'isodisomy' | 'partial' | 'disomy' | None."""
    mono = _flag(values, "chr3_monosomy")
    iso = _flag(values, "chr3_isodisomy")
    part = _flag(values, "chr3_partial_loss")
    if mono:
        return "monosomy"
    if iso:
        return "isodisomy"
    if part:
        return "partial"
    if mono is False and iso is False and part is False:
        return "disomy"
    # fall back to the older free-text field (e.g. "Monosomy 3", "Disomy 3")
    t = (values.get("chromosome_3_status") or "").lower() + " " + (values.get("gep_result") or "").lower()
    if "isodisomy" in t:
        return "isodisomy"
    if "monosomy" in t:
        return "monosomy"
    if "partial" in t:
        return "partial"
    if "disomy" in t:
        return "disomy"
    return None


def compute_molecular_profile(values):
    """Returns None when no molecular marker is recorded."""
    findings = []
    flags = []

    def add(key, label, value, direction, note):
        findings.append(dict(key=key, label=label, value=value, direction=direction, note=note))

    # GEP class ---------------------------------------------------------
    gep = _gep_class(values)
    if gep == "1a":
        add("gep", "Gene expression profile", "Class 1A", LOWER,
            "Lowest-risk group of the test.")
    elif gep == "1b":
        add("gep", "Gene expression profile", "Class 1B", MIXED,
            "Class 1, but in the higher-risk half of Class 1.")
    elif gep == "2":
        add("gep", "Gene expression profile", "Class 2", HIGHER,
            "Highest-risk group of the test.")

    # PRAME -------------------------------------------------------------
    prame = _flag(values, "prame_positive")
    if prame is True:
        if gep in ("1a", "1b"):
            note = ("PRAME-positive within Class 1: the tumor may carry a higher metastatic risk than the class alone "
                    "suggests. The test's maker states that specific risk estimates for PRAME positivity have not been "
                    "established, so no percentage is given here. Consider this when planning surveillance.")
        elif gep == "2":
            note = ("PRAME-positive within Class 2: associated with a shorter time to metastasis. "
                    "No specific risk estimate has been established.")
        else:
            note = ("PRAME-positive. PRAME is interpreted together with the GEP class, which has not been recorded here. "
                    "No specific risk estimate has been established.")
        add("prame", "PRAME", "Positive", HIGHER, note)
    elif prame is False:
        if gep in ("1a", "1b"):
            add("prame", "PRAME", "Negative", LOWER,
                "PRAME-negative: the prognosis indicated by the Class 1 result is not expected to change.")
        else:
            add("prame", "PRAME", "Negative", MIXED,
                "PRAME-negative: the prognosis indicated by the GEP class is not expected to change.")

    # chromosome 3 ------------------------------------------------------
    chr3 = _chr3_state(values)
    if chr3 == "monosomy":
        add("chr3", "Chromosome 3", "Monosomy (complete loss)", HIGHER,
            "Strongly linked to metastasis.")
    elif chr3 == "isodisomy":
        add("chr3", "Chromosome 3", "Isodisomy (copy-neutral loss)", HIGHER,
            "Carries the same prognostic weight as monosomy 3. Only some methods (SNP array) can detect it.")
    elif chr3 == "partial":
        add("chr3", "Chromosome 3", "Partial loss", MIXED,
            "Prognostic meaning is still being studied. In published series, outcomes sit closer to disomy 3 than to complete monosomy 3.")
    elif chr3 == "disomy":
        add("chr3", "Chromosome 3", "Disomy (no loss detected)", LOWER,
            "Metastasis is uncommon with disomy 3, but not absent. Methods that cannot detect isodisomy (FISH, MLPA) "
            "can miss copy-neutral loss, so check which method was used.")

    # 8q / 6p -----------------------------------------------------------
    g8 = _flag(values, "chr8q_gain")
    if g8 is True:
        add("chr8q", "Chromosome 8q", "Gain", HIGHER,
            "Associated with a worse prognosis, most of all when combined with monosomy 3.")
    elif g8 is False:
        add("chr8q", "Chromosome 8q", "No gain", MIXED, "Not a high-risk feature.")
    g6 = _flag(values, "chr6p_gain")
    if g6 is True:
        add("chr6p", "Chromosome 6p", "Gain", LOWER, "Associated with a better prognosis.")
    elif g6 is False:
        add("chr6p", "Chromosome 6p", "No gain", MIXED, "No effect on risk.")

    # BAP1 --------------------------------------------------------------
    b_loss = _flag(values, "bap1_nuclear_loss")
    b_mut = _flag(values, "bap1_tumor_mutation")
    b_germ = _flag(values, "bap1_germline_variant")
    if b_loss is True:
        add("bap1_ihc", "BAP1 (IHC)", "Nuclear loss", HIGHER,
            "Loss of BAP1 protein in the tumor strongly tracks with monosomy 3, metastasis, and earlier metastasis.")
    elif b_loss is False:
        add("bap1_ihc", "BAP1 (IHC)", "Nuclear staining retained", LOWER,
            "BAP1 protein is present, which fits a lower-risk tumor.")
    if b_mut is True:
        add("bap1_mut", "BAP1 mutation (tumor)", "Present", HIGHER,
            "Linked with reduced disease-free survival, especially together with monosomy 3. "
            "A tumor mutation alone does not show whether it is also inherited.")
    elif b_mut is False:
        add("bap1_mut", "BAP1 mutation (tumor)", "Not found", MIXED, "No BAP1 mutation detected in the tumor.")

    # SF3B1 / EIF1AX ------------------------------------------------------
    if _flag(values, "sf3b1_mutation") is True:
        add("sf3b1", "SF3B1 mutation", "Present", MIXED,
            "Usually seen with disomy 3 and intermediate risk. Metastasis can appear late, sometimes more than a decade after "
            "diagnosis, so long-term surveillance still matters.")
    if _flag(values, "eif1ax_mutation") is True:
        add("eif1ax", "EIF1AX mutation", "Present", LOWER,
            "Usually seen with disomy 3 and a low metastatic risk.")

    # inherited BAP1 ------------------------------------------------------
    if b_germ is True:
        add("bap1_germ", "BAP1 germline variant", "Pathogenic variant", MIXED,
            "Inherited BAP1 variant. This changes care for the patient and family members, not only for this tumor.")
        flags.append(dict(level="action", text=(
            "Inherited BAP1 variant recorded: refer to genetic counseling. The patient and blood relatives may need "
            "screening for other BAP1-related cancers (for example mesothelioma, skin melanoma, and kidney cancer), "
            "and other uveal melanoma.")))
    elif b_germ is None and (b_loss is True or b_mut is True):
        flags.append(dict(level="review", text=(
            "BAP1 loss or mutation was found in the tumor and germline BAP1 testing is not recorded. Consider whether germline "
            "testing and genetic counseling are indicated per your center's policy. Only a small minority of patients carry an "
            "inherited variant.")))

    if not findings and not flags:
        return None

    # disagreements between markers -----------------------------------------
    high_markers = (chr3 in ("monosomy", "isodisomy")) or b_loss is True or b_mut is True
    low_markers = chr3 == "disomy" and b_loss is not True and b_mut is not True
    if gep in ("1a", "1b") and high_markers:
        flags.append(dict(level="review", text=(
            "These results disagree: GEP is Class 1, but a high-risk marker (monosomy/isodisomy 3 or BAP1 loss/mutation) is "
            "also recorded. Consider sampling variation within the tumor, a repeat or additional test, and a review with pathology.")))
    if gep == "2" and low_markers and b_loss is False:
        flags.append(dict(level="review", text=(
            "These results disagree: GEP is Class 2, but chromosome 3 shows disomy and BAP1 staining is retained. "
            "Check how the sample was taken and which method was used (isodisomy can be missed).")))

    higher = sum(1 for f in findings if f["direction"] == HIGHER)
    lower = sum(1 for f in findings if f["direction"] == LOWER)
    if higher == 0 and lower == 0:
        pattern = "No clearly higher- or lower-risk marker recorded yet"
    elif higher > 0 and lower == 0:
        pattern = "Recorded markers point toward higher risk"
    elif lower > 0 and higher == 0:
        pattern = "Recorded markers point toward lower risk"
    else:
        pattern = "Mixed: some markers point higher and some lower"

    return {
        "findings": findings,
        "higher_count": higher,
        "lower_count": lower,
        "pattern": pattern,
        "flags": flags,
        "disclaimer": DISCLAIMER,
    }
