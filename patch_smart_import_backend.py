"""
Patches main.py in place to add a smarter imaging import:

  POST /cases/{case_id}/images/import-smart

  * many files at once, and whole .zip exports (a folder of DICOMs)
  * works out which study each file belongs to (B-scan, OCT, FAF, widefield)
  * refuses files labelled for a different patient or the wrong eye
    (unless you tick "import anyway")
  * saves neutral names instead of the original filenames
  * reports exactly what happened to every file

The existing one-file import is left exactly as it is.

Run from inside your backend folder (same folder as main.py and
smart_import.py):

    python3 patch_smart_import_backend.py

Independent of the other patches: apply in any order. Safe to run twice.
"""
import os
import sys

if not os.path.exists("smart_import.py"):
    print("FAIL  smart_import.py is not in this folder. Put it next to main.py first.")
    sys.exit(1)

content = open("main.py", "r", encoding="utf-8").read()
original = content
MARKER = "# uvealcare: smart import"

BLOCK = r'''# uvealcare: smart import
# Rules live in smart_import.py. This endpoint only wires them to the database.
import smart_import
from fastapi import Request as _SmartRequest


@app.post("/cases/{case_id}/images/import-smart")
async def import_images_smart(
    case_id: str,
    request: _SmartRequest,
    files: list[UploadFile] = File(...),
    field_key: str = Form("auto"),
    neutral_names: bool = Form(True),
    allow_mismatch: bool = Form(False),
    only: str = Form(""),   # JSON list of file names: re-run just these
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")

    imaging = (
        db.query(DataFieldDefinition)
        .filter_by(disease_profile_id=case.disease_profile_id, category="imaging")
        .all()
    )
    labels = {f.key: f.label for f in imaging}
    available = list(labels.keys())
    forced = None
    if field_key and field_key != "auto":
        if field_key not in labels:
            raise HTTPException(400, "Unknown study type for this case.")
        forced = field_key

    uploads = []
    for f in files:
        uploads.append((f.filename or "file", await f.read()))
    if not uploads:
        raise HTTPException(400, "No files were sent.")
    sources, results = smart_import.expand(uploads)
    del uploads
    only_names = None
    if only:
        try:
            only_names = set(json.loads(only))
        except Exception:
            raise HTTPException(400, "Couldn't read the list of files to re-run.")
        sources = [s_ for s_ in sources if s_.name.replace("\\", "/").split("/")[-1] in only_names]
        results = []

    # ── Pass 1: look at each file's header only, decide where it goes ──
    plan = []
    for src in sources:
        shown = src.name.replace("\\", "/").split("/")[-1]
        res = {"name": shown, "status": "pending", "field_key": None, "field_label": None,
               "images": 0, "reason": None, "problems": []}
        try:
            raw = src.read()
            kind = smart_import.kind_of(src.name, raw[:200])
            meta, text = None, ""
            if kind == "dicom":
                meta = smart_import.read_dicom_meta(raw)
                if meta is None:
                    res.update(status="skipped", reason="This isn't a readable DICOM file.")
                    results.append(res)
                    continue
            elif kind == "pdf":
                try:
                    text = smart_import.pdf_text_only(raw)
                except Exception:
                    res.update(status="error", reason="This PDF couldn't be read.")
                    results.append(res)
                    continue
            elif kind == "unknown":
                res.update(status="skipped", reason="Not a DICOM, PDF, PNG or JPEG file.")
                results.append(res)
                continue
            del raw
        except Exception as exc:
            res.update(status="error", reason="Couldn't read this file (%s)." % exc.__class__.__name__)
            results.append(res)
            continue

        key, why = (forced, "you chose this study") if forced else smart_import.guess_study(meta, src.name, text, available)
        if not key:
            res.update(status="needs_study", reason="Couldn't tell which study this is. Choose one and import it again.")
            results.append(res)
            continue
        res["field_key"], res["field_label"], res["detected_by"] = key, labels.get(key, key), why

        problems = smart_import.identity_check(
            meta, case.patient.mrn, case.patient.name, case.patient.laterality
        )
        if problems and not allow_mismatch:
            res.update(status="held", problems=problems,
                       reason="Held back. Tick \"import anyway\" if you're sure it's correct.")
            results.append(res)
            continue
        res["problems"] = problems  # imported anyway, but still shown
        plan.append({"src": src, "kind": kind, "meta": meta, "key": key, "res": res})

    plan.sort(key=lambda p: (
        p["key"],
        (p["meta"] or {}).get("series_uid", ""),
        (p["meta"] or {}).get("instance", 0),
        p["src"].name,
    ))

    # ── Pass 2: convert and save one file at a time ──
    totals = {}
    imported_images = imported_files = 0
    for item in plan:
        res, key, src = item["res"], item["key"], item["src"]
        try:
            raw = src.read()
            if item["kind"] == "dicom":
                pngs = smart_import.dicom_to_pngs(raw)
            elif item["kind"] == "pdf":
                _, pngs = smart_import.pdf_text_and_pages(raw)
            else:
                pngs = [smart_import.image_to_png(raw)]
            del raw
        except Exception as exc:
            msg = str(exc)
            if "pylibjpeg" in msg or "gdcm" in msg.lower() or "transfer syntax" in msg.lower():
                res.update(status="error", reason="This DICOM is compressed and the server can't open that kind yet. "
                                                  "Re-export it uncompressed, or ask for decoder support to be added.")
            else:
                res.update(status="error", reason="Couldn't convert this file.")
            results.append(res)
            continue
        if not pngs:
            res.update(status="skipped", reason="Nothing importable inside.")
            results.append(res)
            continue

        if key not in totals:
            same = (ImageUpload.case_id == case_id) & (ImageUpload.field_key == key)
            totals[key] = [
                db.query(func.count(ImageUpload.id)).filter(same).scalar() or 0,
                db.query(func.coalesce(func.sum(func.length(ImageUpload.data_base64)), 0)).filter(same).scalar() or 0,
            ]
        count, used = totals[key]
        if count + len(pngs) > MAX_IMAGES_PER_STUDY:
            res.update(status="skipped", reason="That would go over the limit of %d images in one study." % MAX_IMAGES_PER_STUDY)
            results.append(res)
            continue

        base = src.name.replace("\\", "/").split("/")[-1]
        base = base.rsplit(".", 1)[0] if "." in base else base
        try:
            for i, png in enumerate(pngs):
                if neutral_names:
                    nm = "%s_%d.png" % (labels.get(key, key).replace(" ", "-"), count + 1)
                else:
                    nm = ("%s.png" % base) if len(pngs) == 1 else ("%s_%d.png" % (base, i + 1))
                _row, count, used = _append_image_row(db, case_id, key, nm, "image/png", png, count, used)
            db.commit()
            totals[key] = [count, used]
            imported_images += len(pngs)
            imported_files += 1
            res.update(status="imported", images=len(pngs))
        except HTTPException as exc:
            db.rollback()
            res.update(status="skipped", reason=str(exc.detail))
        results.append(res)

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    request.state.audit_detail = "Smart import: %d image(s) from %d file(s); %s" % (
        imported_images, imported_files,
        ", ".join("%d %s" % (v, k.replace("_", " ")) for k, v in counts.items() if k != "imported") or "nothing held back",
    )
    return {
        "ok": True,
        "imported_images": imported_images,
        "imported_files": imported_files,
        "counts": counts,
        "results": results,
    }


'''


def fail(msg):
    print("  FAIL  " + msg)
    print("        Your main.py differs from what this patch expects. Nothing was changed.")
    sys.exit(1)


if MARKER in content:
    print("  SKIP  smart import (already applied)")
else:
    anchor = '@app.get("/cases/{case_id}/images")\n'
    if content.count(anchor) != 1:
        fail("insertion point: expected exactly 1 match, found %d" % content.count(anchor))
    for needed in ("def _append_image_row(", "MAX_IMAGES_PER_STUDY =", "ImageUpload"):
        if needed not in content:
            fail("this main.py has no '%s' (the earlier image import patch is needed first)" % needed)
    content = content.replace(anchor, BLOCK + anchor, 1)
    print("  OK    smart import endpoint")

if content == original:
    print("\nNothing changed.")
    sys.exit(0)
open("main.py", "w", encoding="utf-8").write(content)
print("\nDone - main.py patched.")
