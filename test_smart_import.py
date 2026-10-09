"""Run:  python3 test_smart_import.py   (expect: ALL PASS)
Uses the example files in example-imports/ if they sit next to this file or in
../example-imports; otherwise builds tiny test files itself."""
import io, os, zipfile
import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
import smart_import as si

fails = []
def check(name, cond):
    if not cond:
        fails.append(name); print("FAIL:", name)

AVAIL = ["b_scan", "oct", "faf", "optos"]

def make_dcm(modality="OP", series="", pid="EXAMPLE-0001", pname="SYNTHETIC^EXAMPLE", lat="R",
             rgb=False, frames=1, inst=1, manu=""):
    meta = FileMetaDataset(); meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.77.1.5.1"; meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = FileDataset("x.dcm", {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = meta.MediaStorageSOPClassUID; ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.Modality = modality; ds.SeriesDescription = series; ds.PatientID = pid; ds.PatientName = pname
    ds.Laterality = lat; ds.InstanceNumber = inst; ds.Manufacturer = manu; ds.SeriesInstanceUID = "1.2.3"
    if rgb:
        ds.PhotometricInterpretation = "RGB"; ds.SamplesPerPixel = 3; ds.PlanarConfiguration = 0
        arr = np.zeros((40, 50, 3), dtype=np.uint8); arr[..., 0] = 200; arr[..., 1] = 100
    else:
        ds.PhotometricInterpretation = "MONOCHROME2"; ds.SamplesPerPixel = 1
        arr = np.tile(np.arange(50, dtype=np.uint8), (frames, 40, 1)) if frames > 1 else np.tile(np.arange(50, dtype=np.uint8), (40, 1))
    if frames > 1: ds.NumberOfFrames = frames
    ds.BitsAllocated = 8; ds.BitsStored = 8; ds.HighBit = 7; ds.PixelRepresentation = 0
    ds.Rows, ds.Columns = 40, 50; ds.PixelData = arr.tobytes()
    b = io.BytesIO(); pydicom.dcmwrite(b, ds); return b.getvalue()

# kind detection
check("kind pdf", si.kind_of("a.pdf", b"%PDF-1.4") == "pdf")
check("kind dicom by magic", si.kind_of("noext", b"\0" * 128 + b"DICM") == "dicom")
check("kind png", si.kind_of("a.png", b"\x89PNG\r\n\x1a\n") == "image")
check("kind weird", si.kind_of("notes.txt", b"hello") == "unknown")

# meta + guess
m = si.read_dicom_meta(make_dcm(modality="US", series="OPHTHALMIC B-SCAN"))
check("meta read", m and m["modality"] == "US" and m["patient_id"] == "EXAMPLE-0001")
check("guess US -> b_scan", si.guess_study(m, "x.dcm", "", AVAIL)[0] == "b_scan")
m = si.read_dicom_meta(make_dcm(series="FUNDUS AUTOFLUORESCENCE"))
check("guess faf", si.guess_study(m, "x.dcm", "", AVAIL)[0] == "faf")
m = si.read_dicom_meta(make_dcm(series="WIDEFIELD PSEUDOCOLOR"))
check("guess optos", si.guess_study(m, "x.dcm", "", AVAIL)[0] == "optos")
m = si.read_dicom_meta(make_dcm(modality="OPT", series="Volume 19 lines"))
check("guess OPT -> oct", si.guess_study(m, "x.dcm", "", AVAIL)[0] == "oct")
m = si.read_dicom_meta(make_dcm(series="mystery"))
check("unknown -> None", si.guess_study(m, "x.dcm", "", AVAIL)[0] is None)
check("only offers existing studies", si.guess_study(si.read_dicom_meta(make_dcm(series="B-SCAN")), "x", "", ["oct"])[0] is None)
check("pdf text guess", si.guess_study(None, "report.pdf", "Macular OCT Report", AVAIL)[0] == "oct")
check("faf wins over oct words", si.guess_study(si.read_dicom_meta(make_dcm(series="Spectralis FAF")), "x", "", AVAIL)[0] == "faf")

# identity
m = si.read_dicom_meta(make_dcm(pid="123", pname="HARGROVE^MARGARET", lat="R"))
check("id match by name", si.identity_check(m, "999", "Margaret Hargrove", "OD") == [])
check("id match by id", si.identity_check(si.read_dicom_meta(make_dcm(pid="MRN-55", pname="X^Y")), "mrn55", "Someone Else", "OD") == [])
p = si.identity_check(si.read_dicom_meta(make_dcm(pid="4821", pname="DOE^JANE")), "999", "Margaret Hargrove", "OD")
check("wrong patient flagged", len(p) == 1)
check("wrong patient not printed in full", p and "Doe" not in p[0] and "DOE" not in p[0] and "4821" in p[0])
p = si.identity_check(si.read_dicom_meta(make_dcm(pid="999", pname="HARGROVE^MARGARET", lat="L")), "999", "Margaret Hargrove", "OD")
check("wrong eye flagged", len(p) == 1 and "OS" in p[0])
check("OU case accepts either eye", si.identity_check(si.read_dicom_meta(make_dcm(pid="999", pname="HARGROVE^MARGARET", lat="L")), "999", "Margaret Hargrove", "OU") == [])
check("no identity = no complaint", si.identity_check(si.read_dicom_meta(make_dcm(pid="", pname="", lat="")), "999", "Margaret Hargrove", "OD") == [])
check("no meta (pdf) passes", si.identity_check(None, "999", "x", "OD") == [])
check("norm eye words", si.norm_eye("Right") == "OD" and si.norm_eye("OS") == "OS" and si.norm_eye("zzz") is None)

# conversion
pngs = si.dicom_to_pngs(make_dcm(frames=4))
check("multi-frame -> 4 pngs", len(pngs) == 4)
pngs = si.dicom_to_pngs(make_dcm(rgb=True))
from PIL import Image
im = Image.open(io.BytesIO(pngs[0]))
check("rgb stays colour", im.mode == "RGB" and im.getpixel((5, 5))[:2] == (200, 100))

# zip handling
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("exam/a.dcm", make_dcm()); z.writestr("exam/b.dcm", make_dcm(inst=2))
    z.writestr("__MACOSX/exam/._a.dcm", b"junk"); z.writestr(".DS_Store", b"junk"); z.writestr("exam/", b"")
    z.writestr("exam/DICOMDIR", b"x")
srcs, probs = si.expand([("export.zip", buf.getvalue())])
check("zip junk removed", sorted(s.name for s in srcs) == ["exam/a.dcm", "exam/b.dcm"])
check("zip entry readable", si.kind_of(srcs[0].name, srcs[0].read()[:200]) == "dicom")
srcs, probs = si.expand([("bad.zip", b"PK\x03\x04garbage")])
check("bad zip reported, not raised", srcs == [] and probs and probs[0]["status"] == "error")
srcs, probs = si.expand([("one.dcm", make_dcm()), (".DS_Store", b"x")])
check("loose files kept, junk dropped", len(srcs) == 1)

# examples, if present
here = os.path.dirname(os.path.abspath(__file__))
for d in (os.path.join(here, "example-imports"), os.path.join(here, "..", "example-imports")):
    if os.path.isdir(d):
        want = {"01_bscan_ultrasound_6frames.dcm": "b_scan", "02_widefield_fundus_pseudocolor.dcm": "optos",
                "03_fundus_autofluorescence_8bit.dcm": "faf", "04_oct_macula_report_style.pdf": "oct"}
        for fn, key in want.items():
            raw = open(os.path.join(d, fn), "rb").read()
            if fn.endswith(".pdf"):
                got = si.guess_study(None, fn, si.pdf_text_only(raw), AVAIL)[0]
            else:
                got = si.guess_study(si.read_dicom_meta(raw), fn, "", AVAIL)[0]
            check(f"example {fn} -> {key}", got == key)
        raw = open(os.path.join(d, "05_molecular_lab_report_style.pdf"), "rb").read()
        check("lab report PDF not forced into an imaging study", si.guess_study(None, "05_molecular_lab_report_style.pdf", si.pdf_text_only(raw), AVAIL)[0] is None)
        break

print("ALL PASS" if not fails else f"{len(fails)} FAILED")
raise SystemExit(1 if fails else 0)
