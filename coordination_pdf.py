"""
One-page tumor board summary (PDF), built from the case packet plus the
imaging log and surveillance schedule. Pure layout code: it receives plain
dictionaries and returns PDF bytes, so it can be tested without a database.

Every case is forced onto a single page (the content shrinks to fit rather
than spilling over), so it pastes into Epic as one attachment.
"""

import base64
import datetime as dt
import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepInFrame, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#616e7c")
RULE = colors.HexColor("#cbd2d9")
BAND = colors.HexColor("#e4e7eb")

_ss = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=_ss["BodyText"], fontName="Helvetica", fontSize=8.6, leading=10.6, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.4, leading=9, textColor=MUTED)
H1 = ParagraphStyle("h1", parent=BODY, fontName="Helvetica-Bold", fontSize=15, leading=18)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=9.4, leading=11.5, spaceBefore=5, spaceAfter=1.5)

PAGE_W, PAGE_H = letter
MARGIN = 0.55 * inch
FRAME_W = PAGE_W - 2 * MARGIN
FRAME_H = PAGE_H - 2 * MARGIN - 0.25 * inch


def P(text, style=BODY):
    return Paragraph(escape(str(text)), style)


def PB(markup, style=BODY):
    """Paragraph from text that already contains our own <b> tags (callers escape the rest)."""
    return Paragraph(markup, style)


def _trim(text, n):
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "… (full text in UvealCare)"


def _kv_table(rows, col0=1.45 * inch):
    data = [[PB("<b>%s</b>" % escape(k), SMALL), P(v)] for k, v in rows if v]
    if not data:
        return None
    t = Table(data, colWidths=[col0, FRAME_W - col0])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 1),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _thumb(b64, height):
    try:
        raw = base64.b64decode(b64)
        from PIL import Image as PILImage
        im = PILImage.open(io.BytesIO(raw))
        im.thumbnail((420, 420))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        buf.seek(0)
        w, h = im.size
        return Image(buf, width=height * w / h, height=height)
    except Exception:
        return None


def case_page(packet, studies, surveillance, images, meeting_date=None):
    """Returns a list of flowables for one case (not yet shrunk to a page)."""
    s = []
    eye = {"right": "Right eye", "od": "Right eye (OD)", "left": "Left eye", "os": "Left eye (OS)",
           "both": "Both eyes", "ou": "Both eyes (OU)"}.get(
        str(packet.get("laterality") or "").lower(), packet.get("laterality"))
    s.append(P("%s" % packet.get("patient_name", "Patient"), H1))
    head = ["MRN %s" % packet.get("mrn")] if packet.get("mrn") else []
    if packet.get("dob"):
        head.append("DOB %s" % packet["dob"])
    if eye:
        head.append(str(eye))
    if packet.get("diagnosis"):
        head.append(packet["diagnosis"])
    s.append(P("  ·  ".join(head), SMALL))
    sub = []
    if meeting_date:
        sub.append("Tumor board %s" % meeting_date)
    sub.append("Care stage: %s" % (packet.get("care_stage") or "not set"))
    sub.append("Readiness %s%%" % packet.get("readiness_pct", 0))
    s.append(P("  ·  ".join(sub), SMALL))
    miss = packet.get("missing_information") or []
    if miss:
        s.append(Spacer(1, 2))
        s.append(PB("<b>Still missing:</b> %s" % escape(_trim("; ".join(str(m) for m in miss[:8]), 260))))

    # Tumor
    m = packet.get("measurement")
    if m:
        bits = []
        if m.get("apical_height_mm") is not None:
            bits.append("thickness %s mm" % m["apical_height_mm"])
        if m.get("basal_diameter_mm") is not None:
            bits.append("largest basal diameter %s mm" % m["basal_diameter_mm"])
        txt = ", ".join(bits) or str(m.get("value") or "")
        if m.get("method"):
            txt += " (%s)" % m["method"]
        if m.get("recorded_at"):
            txt += ", recorded %s" % str(m["recorded_at"])[:10]
        trend = m.get("trend") or []
        if len(trend) > 1:
            txt += ". %d measurements on file." % len(trend)
        s.append(P("Tumor", H2))
        s.append(P(txt))

    # Staging and risk
    stg = packet.get("ajcc_staging")
    risk_rows = []
    if stg:
        if stg.get("stage_group"):
            risk_rows.append(("AJCC 8th stage", "%s%s" % (stg["stage_group"], " (T%s N%s M%s)" % (stg.get("t"), stg.get("n"), stg.get("m")) if stg.get("t") else "")))
        elif stg.get("provisional_stage_group"):
            risk_rows.append(("AJCC 8th stage", "Provisional %s, assuming N0 and M0 (not all recorded)" % stg["provisional_stage_group"]))
        else:
            risk_rows.append(("AJCC 8th stage", "Not enough recorded to stage. Missing: %s" % _trim(", ".join(stg.get("missing") or []), 160)))
    dim = packet.get("tfsom_dim")
    if dim:
        risk_rows.append(("TFSOM-DIM", "%d of %d factors assessed, %d present. %s%s" % (
            dim["factors_assessed"], dim["factors_total"], dim["factor_count"], _trim(dim.get("risk_estimate"), 200),
            " (provisional)" if dim.get("provisional") else "")))
    gep = packet.get("gep_risk")
    if gep:
        risk_rows.append(("Gene expression", "%s%s" % (gep.get("gep_class"), " — %s" % gep["risk_label"] if gep.get("risk_label") else "")))
    mol = packet.get("molecular_profile")
    if mol:
        f = "; ".join("%s: %s" % (x["label"], x["value"]) for x in mol.get("findings", []))
        risk_rows.append(("Molecular markers", _trim("%s. %s" % (mol.get("pattern"), f), 330)))
        for fl in (mol.get("flags") or [])[:2]:
            risk_rows.append(("Note", _trim(fl if isinstance(fl, str) else fl.get("text", ""), 200)))
    t = _kv_table(risk_rows)
    if t is not None:
        s.append(P("Staging and risk", H2))
        s.append(t)

    # Imaging log
    lat, wait = studies.get("latest", []), studies.get("waiting", [])
    if lat or wait:
        s.append(P("Imaging", H2))
        rows = []
        for r in lat[:8]:
            rows.append(("%s, %s" % (r["modality_label"], r["eye_label"]),
                         "%s (%d day%s ago)%s%s" % (r["performed_date"], r["days_ago"], "" if r["days_ago"] == 1 else "s",
                                                    (" at " + r["location"]) if r.get("location") else "",
                                                    ", outside study" if r.get("outside") else "")))
        for r in wait[:4]:
            rows.append(("%s, %s" % (r["modality_label"], r["eye_label"]),
                         "Ordered%s, not yet done" % ((" " + r["ordered_date"]) if r.get("ordered_date") else "")))
        s.append(_kv_table(rows, 2.1 * inch))

    # Surveillance
    due = [x for x in surveillance if x.get("status") in ("overdue", "due_soon", "no_date")]
    if surveillance:
        s.append(P("Surveillance", H2))
        rows = [("%s" % x["label"], x["text"]) for x in (due or surveillance)[:6]]
        s.append(_kv_table(rows, 2.1 * inch))

    # Decision
    d = packet.get("decision")
    s.append(P("Board decision", H2))
    if d:
        rows = [("Recommendation", _trim(d.get("recommendation"), 420)),
                ("Rationale", _trim(d.get("rationale"), 520)),
                ("Next step", d.get("next_step")), ("Responsible", d.get("responsible_provider")),
                ("Follow-up date", d.get("follow_up_date")), ("Surveillance", _trim(d.get("surveillance_protocol"), 200))]
        s.append(_kv_table(rows))
    else:
        s.append(P("No decision recorded yet."))

    # Images
    thumbs = []
    for im in images[:4]:
        th = _thumb(im["data_base64"], 0.95 * inch)
        if th:
            thumbs.append([th, P(im.get("label", ""), SMALL)])
    if thumbs:
        s.append(P("Key images", H2))
        cells = [[x[0] for x in thumbs], [x[1] for x in thumbs]]
        tb = Table(cells, colWidths=[x[0].drawWidth + 10 for x in thumbs], hAlign="LEFT")
        tb.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        s.append(tb)
    return s


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, 0.38 * inch,
                      "Contains patient information. Prepared by UvealCare from recorded data; "
                      "interpretation belongs to the treating team.")
    canvas.drawRightString(PAGE_W - MARGIN, 0.38 * inch,
                           "Generated %s  ·  page %d" % (dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), doc.page))
    canvas.restoreState()


def agenda_cover(items, meeting_date):
    s = [P("Tumor board agenda", H1),
         P(("Meeting %s" % meeting_date) if meeting_date else "Meeting date not set", SMALL), Spacer(1, 8)]
    data = [[PB("<b>#</b>", SMALL), PB("<b>Patient</b>", SMALL), PB("<b>MRN</b>", SMALL), PB("<b>Eye</b>", SMALL),
             PB("<b>Ready</b>", SMALL), PB("<b>Note</b>", SMALL)]]
    for i, it in enumerate(items, 1):
        data.append([P(i), P(it["patient_name"]), P(it["mrn"]), P(it.get("laterality") or ""),
                     P("%s%%" % it["readiness_pct"]), P(_trim(it.get("note") or "", 120))])
    if len(data) == 1:
        s.append(P("No cases on the agenda."))
        return s
    t = Table(data, colWidths=[0.3 * inch, 1.9 * inch, 0.9 * inch, 0.7 * inch, 0.6 * inch, FRAME_W - 4.4 * inch], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), BAND), ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    s.append(t)
    return s


def render(pages, cover=None):
    """pages: list of case_page flowable lists. cover: optional flowable list for page 1."""
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
                          bottomMargin=MARGIN + 0.2 * inch, title="UvealCare tumor board summary", author="UvealCare")
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(MARGIN, MARGIN + 0.2 * inch, FRAME_W, FRAME_H, id="f",
                                                             leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)],
                                       onPage=_footer)])
    story = []
    blocks = ([cover] if cover else []) + list(pages)
    for i, flow in enumerate(blocks):
        story.append(KeepInFrame(FRAME_W, FRAME_H - 4, flow, mode="shrink", hAlign="LEFT"))
        if i < len(blocks) - 1:
            story.append(PageBreak())
    if not story:
        story.append(P("Nothing to show."))
    doc.build(story)
    return buf.getvalue()
