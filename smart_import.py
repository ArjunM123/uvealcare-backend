"""
Smarter imaging import. Pure helpers (no web server, no database) so each
rule can be tested alone (see test_smart_import.py).

What it adds over the one-file-at-a-time import:
  * Accepts many files at once, including whole .zip exports (a folder of
    DICOMs from an Optos, Heidelberg or Visage export).
  * Reads each DICOM's header to work out which study it belongs to
    (B-scan, OCT, FAF, widefield) instead of asking you to pick one.
  * Checks that the file is for THIS patient and THIS eye before saving it.
  * Replaces filenames (which often contain patient names) with neutral labels.

Only the picture is ever saved. The patient name, ID and date of birth inside
a DICOM header are read for the safety check and then thrown away.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Callable, Optional

MAX_FILES = 400
MAX_BATCH_BYTES = 250 * 1024 * 1024        # total uncompressed size of one batch
MAX_FILE_BYTES = 25 * 1024 * 1024          # per file, same as the single-file import
PDF_DPI = 200

IMAGE_EXT = (".png", ".jpg", ".jpeg")
SKIP_NAMES = {".ds_store", "thumbs.db", "dicomdir", "desktop.ini"}


# ── Sources: a file (or a zip entry) that is read only when needed ────────
class Source:
    def __init__(self, name: str, reader: Callable[[], bytes], size: int):
        self.name = name
        self._reader = reader
        self.size = size

    def read(self) -> bytes:
        return self._reader()


def _skip(name: str) -> bool:
    base = name.replace("\\", "/").split("/")[-1].lower()
    parts = name.replace("\\", "/").split("/")
    if not base or base in SKIP_NAMES or base.startswith("._") or base.startswith("."):
        return True
    if "__macosx" in [p.lower() for p in parts]:
        return True
    return False


def expand(uploads: list[tuple[str, bytes]]) -> tuple[list[Source], list[dict]]:
    """Turns uploaded files into a flat list of Sources, opening zips.
    Returns (sources, problems). Never raises for one bad file."""
    sources: list[Source] = []
    problems: list[dict] = []
    total = 0
    for name, raw in uploads:
        if name.lower().endswith(".zip") or raw[:4] == b"PK\x03\x04":
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
            except Exception:
                problems.append({"name": name, "status": "error", "reason": "That zip couldn't be opened."})
                continue
            for info in zf.infolist():
                if info.is_dir() or _skip(info.filename):
                    continue
                if info.file_size > MAX_FILE_BYTES:
                    problems.append({"name": info.filename, "status": "skipped",
                                     "reason": f"Over {MAX_FILE_BYTES // (1024*1024)} MB."})
                    continue
                total += info.file_size
                if total > MAX_BATCH_BYTES or len(sources) >= MAX_FILES:
                    problems.append({"name": info.filename, "status": "skipped",
                                     "reason": "Batch limit reached. Import the rest in a second batch."})
                    continue
                sources.append(Source(info.filename, (lambda z=zf, i=info: z.read(i)), info.file_size))
        else:
            if _skip(name):
                continue
            if len(raw) > MAX_FILE_BYTES:
                problems.append({"name": name, "status": "skipped",
                                 "reason": f"Over {MAX_FILE_BYTES // (1024*1024)} MB."})
                continue
            total += len(raw)
            if total > MAX_BATCH_BYTES or len(sources) >= MAX_FILES:
                problems.append({"name": name, "status": "skipped", "reason": "Batch limit reached."})
                continue
            sources.append(Source(name, (lambda r=raw: r), len(raw)))
    return sources, problems


def kind_of(name: str, head: bytes) -> str:
    low = name.lower()
    if head[:5] == b"%PDF-" or low.endswith(".pdf"):
        return "pdf"
    if low.endswith(IMAGE_EXT) or head[:8] == b"\x89PNG\r\n\x1a\n" or head[:3] == b"\xff\xd8\xff":
        return "image"
    if len(head) >= 132 and head[128:132] == b"DICM":
        return "dicom"
    if low.endswith((".dcm", ".dicom", ".ima")) or "." not in low.split("/")[-1]:
        return "dicom"      # many exports have no extension at all
    return "unknown"


# ── DICOM header ──────────────────────────────────────────────────────────
def _s(v) -> str:
    return "" if v is None else str(v).strip()


def read_dicom_meta(raw: bytes) -> Optional[dict]:
    """Header only (fast, no pixels). None if it isn't DICOM."""
    try:
        import pydicom
        ds = pydicom.dcmread(io.BytesIO(raw), force=True, stop_before_pixels=True)
    except Exception:
        return None
    if not (hasattr(ds, "Modality") or hasattr(ds, "SOPClassUID") or hasattr(ds, "PatientID")):
        return None
    lat = _s(getattr(ds, "ImageLaterality", "")) or _s(getattr(ds, "Laterality", ""))
    try:
        inst = int(getattr(ds, "InstanceNumber", 0) or 0)
    except Exception:
        inst = 0
    return {
        "modality": _s(getattr(ds, "Modality", "")).upper(),
        "series_desc": _s(getattr(ds, "SeriesDescription", "")),
        "study_desc": _s(getattr(ds, "StudyDescription", "")),
        "image_type": " ".join(map(str, getattr(ds, "ImageType", []) or [])),
        "manufacturer": _s(getattr(ds, "Manufacturer", "")),
        "model": _s(getattr(ds, "ManufacturerModelName", "")),
        "laterality": lat.upper(),
        "patient_id": _s(getattr(ds, "PatientID", "")),
        "patient_name": _s(getattr(ds, "PatientName", "")),
        "series_uid": _s(getattr(ds, "SeriesInstanceUID", "")),
        "instance": inst,
        "frames": int(getattr(ds, "NumberOfFrames", 1) or 1),
    }


# ── Which study is this? ──────────────────────────────────────────────────
_RULES = [
    # (study key we look for, words that point to it). Checked in this order.
    ("faf", ["autofluor", " faf", "faf ", "fundus af", "blue-fa"]),
    ("b_scan", ["b-scan", "bscan", "b scan", "ultrasound", "sonograph", "echograph"]),
    ("optos", ["optos", "widefield", "wide-field", "wide field", "pseudocolor", "pseudocolour",
               "ultra-widefield", "uwf", "california", "daytona", "silverstone", "monaco"]),
    ("oct", ["oct", "tomograph", "spectralis", "heyex", "cirrus", "macular cube", "cross-section", "cross section"]),
]


def guess_study(meta: Optional[dict], filename: str, text: str, available: list[str]) -> tuple[Optional[str], str]:
    """Returns (study key or None, plain-language reason). Only returns a key
    that exists for this disease profile."""
    parts = []
    if meta:
        parts += [meta["series_desc"], meta["study_desc"], meta["image_type"], meta["manufacturer"], meta["model"]]
    parts += [filename.replace("\\", "/").split("/")[-1], text[:1500]]
    hay = " " + " ".join(parts).lower() + " "
    if meta and meta["modality"] == "US" and "b_scan" in available:
        return "b_scan", "DICOM modality is ultrasound"
    for key, words in _RULES:
        if key not in available:
            continue
        for w in words:
            ww = w.strip()
            # Whole-word match for short terms ("oct" must not match "october",
            # "b-scan" must not match "b-scans"); longer terms may be prefixes.
            pat = r"(?<![a-z])" + re.escape(ww) + (r"(?![a-z])" if len(ww) <= 6 else "")
            if re.search(pat, hay):
                return key, f'the file mentions "{ww}"'
    if meta and meta["modality"] in ("OPT", "OCT") and "oct" in available:
        return "oct", f"DICOM modality is {meta['modality']}"
    return None, "couldn't tell which study this belongs to"


# ── Is this the right patient and the right eye? ──────────────────────────
def _tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", (name or "").lower()) if len(t) >= 2}


def _norm_id(v: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (v or "").lower())


def _mask(meta: dict) -> str:
    """Never print another patient's identity back out. Initials + last 4."""
    toks = [t for t in re.split(r"[\^\s,]+", meta.get("patient_name", "")) if t]
    initials = ".".join(t[0].upper() for t in toks[:3]) + ("." if toks else "")
    pid = meta.get("patient_id", "")
    tail = f"ID ends {pid[-4:]}" if pid else ""
    return ", ".join(x for x in (initials, tail) if x) or "a different patient"


def norm_eye(v: Optional[str]) -> Optional[str]:
    t = (v or "").strip().upper()
    if t in ("R", "OD", "RIGHT") or "RIGHT" in t:
        return "OD"
    if t in ("L", "OS", "LEFT") or "LEFT" in t:
        return "OS"
    if t in ("B", "OU", "BOTH"):
        return "OU"
    return None


def identity_check(meta: Optional[dict], case_mrn: str, case_name: str, case_laterality: Optional[str]) -> list[str]:
    """List of problems (empty = fine). Files with no identity in them
    (PDF reports, plain images) can't be checked and pass."""
    if not meta:
        return []
    problems: list[str] = []
    has_identity = bool(meta.get("patient_id") or meta.get("patient_name"))
    if has_identity:
        id_ok = bool(meta["patient_id"]) and _norm_id(meta["patient_id"]) == _norm_id(case_mrn)
        a, b = _tokens(meta["patient_name"]), _tokens(case_name)
        name_ok = bool(a) and bool(b) and (a <= b or b <= a)
        if not (id_ok or name_ok):
            problems.append(f"This file looks like it belongs to someone else ({_mask(meta)}).")
    f_eye, c_eye = norm_eye(meta.get("laterality")), norm_eye(case_laterality)
    if f_eye and c_eye and "OU" not in (f_eye, c_eye) and f_eye != c_eye:
        problems.append(f"This file is for the {f_eye} eye but this case is {c_eye}.")
    return problems


# ── Converting to pictures ────────────────────────────────────────────────
def dicom_to_pngs(raw: bytes) -> list[bytes]:
    import numpy as np
    import pydicom
    from PIL import Image
    ds = pydicom.dcmread(io.BytesIO(raw), force=True)
    arr = ds.pixel_array
    photometric = str(getattr(ds, "PhotometricInterpretation", ""))
    samples = int(getattr(ds, "SamplesPerPixel", 1) or 1)
    nframes = int(getattr(ds, "NumberOfFrames", 1) or 1)

    if samples == 3:
        if photometric.startswith("YBR"):
            from pydicom.pixels import convert_color_space
            arr = convert_color_space(arr, photometric, "RGB")
        frames = list(arr) if (nframes > 1 and arr.ndim == 4) else [arr]
        out = []
        for f in frames:
            if f.dtype != np.uint8:
                f = f.astype("float64")
                lo, hi = float(f.min()), float(f.max())
                f = ((f - lo) / (hi - lo) * 255.0) if hi > lo else f * 0
                f = f.astype("uint8")
            buf = io.BytesIO()
            Image.fromarray(f, "RGB").save(buf, format="PNG")
            out.append(buf.getvalue())
        return out

    try:
        from pydicom.pixels import apply_voi_lut
        arr = apply_voi_lut(arr, ds)
    except Exception:
        pass
    frames = list(arr) if (nframes > 1 and arr.ndim >= 3) else [arr]
    lo = min(float(f.min()) for f in frames)
    hi = max(float(f.max()) for f in frames)
    out = []
    for f in frames:
        f = f.astype("float64")
        f = ((f - lo) / (hi - lo) * 255.0) if hi > lo else f * 0
        f = f.astype("uint8")
        if photometric == "MONOCHROME1":
            f = 255 - f
        buf = io.BytesIO()
        Image.fromarray(f).save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def _fitz():
    # PyMuPDF is importable as "pymupdf" in new versions and "fitz" in all of them.
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        import fitz
        return fitz


def pdf_text_and_pages(raw: bytes) -> tuple[str, list[bytes]]:
    fitz = _fitz()
    doc = fitz.open(stream=raw, filetype="pdf")
    try:
        text = " ".join(p.get_text() for p in list(doc)[:2])
        pages = [p.get_pixmap(dpi=PDF_DPI).tobytes("png") for p in doc]
    finally:
        doc.close()
    return text, pages


def pdf_text_only(raw: bytes) -> str:
    fitz = _fitz()
    doc = fitz.open(stream=raw, filetype="pdf")
    try:
        return " ".join(p.get_text() for p in list(doc)[:2])
    finally:
        doc.close()


def image_to_png(raw: bytes) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    im.load()
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()
