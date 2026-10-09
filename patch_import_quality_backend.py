"""
Improves how DICOM and PDF files are converted when they are imported
into a study. Run from your uvealcare-backend folder (next to main.py):

    python3 patch_import_quality_backend.py

What changes (only for files imported AFTER this is deployed; images that
were already imported are not touched):

  * Colour DICOMs (for example fundus photos) keep their real colours.
    Before, colour images were contrast-stretched as if they were grey,
    which shifted the colours.
  * 8-bit DICOMs are used as they are. Before, every image was stretched
    to full brightness range, which changed the look of the scan.
  * Multi-frame series use ONE brightness scale for the whole series. Before,
    each slice was stretched on its own, so brightness jumped from slice to
    slice and slices could not be compared.
  * DICOM rescale (modality LUT) and window settings are applied before
    scaling, and MONOCHROME1 images are still inverted correctly.
  * PDF pages render at 200 dpi instead of 150, so small print and fine
    detail on vendor reports stay readable when zoomed.

Safe to run twice. Fails without changing anything if the import code is
not where it expects.
"""
import sys

content = open("main.py", "r", encoding="utf-8").read()

if "_dicom_to_png_pages" in content:
    print("Already applied - nothing to do.")
    sys.exit(0)

HELPER = '''def _dicom_to_png_pages(ds, arr):
    """
    Converts a decoded DICOM pixel array into a list of 8-bit PNGs (one per
    frame) without changing how the image looks:
      - colour stays colour (YBR is converted to RGB when the data is stored
        uncompressed)
      - 8-bit grey images pass through unchanged
      - anything deeper than 8 bits is mapped to 8 bits with ONE scale shared
        by every frame in the file, after the DICOM rescale and window are
        applied, so brightness is comparable from slice to slice
    """
    import numpy as np
    from PIL import Image

    photometric = str(getattr(ds, "PhotometricInterpretation", ""))
    samples = int(getattr(ds, "SamplesPerPixel", 1) or 1)
    num_frames = int(getattr(ds, "NumberOfFrames", 1) or 1)

    def to_png(frame_u8):
        buf = io.BytesIO()
        Image.fromarray(frame_u8).save(buf, format="PNG")
        return buf.getvalue()

    def shared_scale_to_u8(stack):
        stack = np.asarray(stack, dtype="float64")
        lo, hi = float(stack.min()), float(stack.max())
        if hi <= lo:
            return np.zeros(stack.shape, dtype="uint8")
        return ((stack - lo) / (hi - lo) * 255.0).round().astype("uint8")

    # ---- colour ----
    if samples >= 3:
        data = np.asarray(arr)
        if photometric.startswith("YBR"):
            try:
                compressed = bool(ds.file_meta.TransferSyntaxUID.is_compressed)
            except Exception:
                compressed = True  # the decoder has already returned RGB
            if not compressed:
                try:
                    from pydicom.pixels import convert_color_space
                except ImportError:
                    from pydicom.pixel_data_handlers.util import convert_color_space
                data = convert_color_space(data, photometric, "RGB")
        frames = list(data) if (num_frames > 1 and data.ndim == 4) else [data]
        if data.dtype != np.uint8:
            scaled = shared_scale_to_u8(np.stack(frames))
            frames = list(scaled)
        return [to_png(f[..., :3]) for f in frames]

    # ---- grey ----
    data = np.asarray(arr)
    try:
        try:
            from pydicom.pixels import apply_modality_lut
        except ImportError:
            from pydicom.pixel_data_handlers.util import apply_modality_lut
        data = apply_modality_lut(data, ds)
    except Exception:
        pass

    has_window = hasattr(ds, "WindowCenter") or hasattr(ds, "VOILUTSequence")
    if has_window:
        try:
            try:
                from pydicom.pixels import apply_voi_lut
            except ImportError:
                from pydicom.pixel_data_handlers.util import apply_voi_lut
            data = apply_voi_lut(data, ds)
        except Exception:
            has_window = False

    data = np.asarray(data)
    if data.dtype == np.uint8 and not has_window:
        u8 = data
    else:
        u8 = shared_scale_to_u8(data)

    # MONOCHROME1 means the lowest raw values should display brightest.
    if photometric == "MONOCHROME1":
        u8 = 255 - u8

    frames = list(u8) if (num_frames > 1 and u8.ndim >= 3) else [u8]
    return [to_png(f) for f in frames]


'''

ROUTE = '@app.post("/cases/{case_id}/images/import")'
if content.count(ROUTE) != 1:
    print("FAIL  import route: expected 1 match, found " + str(content.count(ROUTE)))
    sys.exit(1)
content = content.replace(ROUTE, HELPER + ROUTE, 1)
print("  OK    conversion helper added")

START = "        try:\n            windowed = apply_voi_lut(arr, ds)\n"
END = "            pages.append(buf.getvalue())\n"
if content.count(START) != 1 or content.count(END) != 1:
    print("FAIL  DICOM conversion block not found exactly once. main.py differs from what this patch expects.")
    sys.exit(1)
a = content.index(START)
b = content.index(END) + len(END)
if b < a:
    print("FAIL  DICOM conversion block markers out of order")
    sys.exit(1)
REPL = '''        try:
            pages.extend(_dicom_to_png_pages(ds, arr))
        except Exception:
            raise HTTPException(400, f"\\"{name}\\" was read as DICOM but its image data couldn't be converted.")
'''
content = content[:a] + REPL + content[b:]
print("  OK    DICOM conversion replaced")

OLD_DPI = "pix = page.get_pixmap(dpi=150)"
if content.count(OLD_DPI) != 1:
    print("FAIL  PDF render line not found exactly once")
    sys.exit(1)
content = content.replace(OLD_DPI, "pix = page.get_pixmap(dpi=200)", 1)
print("  OK    PDF pages render at 200 dpi")

open("main.py", "w", encoding="utf-8").write(content)
print("\nDone - main.py patched.")
