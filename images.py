"""
images.py
=========
Saves and loads ASN attachments - both photos and PDF documents.

Primary storage is the Google Drive folder configured in SETTINGS
(IMAGE_STORAGE = DRIVE). Images are resized and JPEG compressed first;
PDFs are stored untouched.

If Drive is unavailable - a service account has no storage quota of its
own, so an unshared folder fails - the file falls back to the Google
Sheet itself: base64 in chunks in IMAGE_DATA, metadata in ASN_IMAGES.
An attachment is therefore never lost.
"""
from __future__ import annotations

import base64
import io
import uuid

import pandas as pd

import drive
import gsheets
import schema
from matching import now_str, nkey

# Google Sheets caps a cell at 50,000 characters; 40,000 leaves headroom.
CHUNK = 40_000
# Largest payload accepted for sheet storage
MAX_BYTES = 3_000_000


# ───────────────────────── compression ─────────────────────────
def compress(data: bytes, mime: str, max_px: int = 1400,
             quality: int = 78) -> tuple[bytes, str]:
    """Shrink an image. Returns the original bytes if Pillow is unavailable."""
    try:
        from PIL import Image
    except Exception:
        return data, mime or "image/png"

    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        has_alpha = im.mode in ("RGBA", "LA", "P")
        if has_alpha:
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")

        w, h = im.size
        if max(w, h) > max_px:
            scale = max_px / float(max(w, h))
            im = im.resize((max(int(w * scale), 1), max(int(h * scale), 1)),
                           Image.LANCZOS)

        out = io.BytesIO()
        im.save(out, format="JPEG", quality=int(quality), optimize=True)
        small = out.getvalue()
        return (small, "image/jpeg") if len(small) < len(data) else (data, mime or "image/png")
    except Exception:
        return data, mime or "image/png"


def _opts() -> tuple[str, int, int, bool]:
    s = gsheets.settings_dict()
    mode = str(s.get("IMAGE_STORAGE", "DRIVE")).strip().upper() or "DRIVE"
    px = int(gsheets.setting_float(s, "IMAGE_MAX_PX", 2200) or 2200)
    q = int(gsheets.setting_float(s, "IMAGE_QUALITY", 92) or 92)
    keep = str(s.get("KEEP_ORIGINAL", "Y")).strip().upper() in ("Y", "YES", "TRUE", "1")
    return mode, px, q, keep


# ───────────────────────── save ─────────────────────────
def save_image(asn: str, filename: str, data: bytes, mime: str,
               source: str = "MANUAL UPLOAD", user: str = "",
               note: str = "", kind: str = "") -> tuple[bool, str]:
    """
    Save an image or PDF. Returns (ok, message).
    With IMAGE_STORAGE=DRIVE the file goes to Drive first and falls back
    to the sheet if that fails.
    """
    if not data:
        return False, f"{filename}: the file is empty."

    mode, px, q, keep_original = _opts()
    is_pdf = (str(mime).lower() == "application/pdf"
              or str(filename).lower().endswith(".pdf"))
    kind = kind or ("PDF" if is_pdf else "IMAGE")

    img_id = uuid.uuid4().hex[:10].upper()
    ts = now_str()
    storage, link, file_id = "SHEET", "", ""
    small, mime2 = data, (mime or "application/octet-stream")
    quality = "original"
    msg = ""

    # ── 1. Drive: room enough to keep the file exactly as uploaded ──
    if mode == "DRIVE":
        if is_pdf or keep_original:
            payload, pmime, pq = data, mime2, "original"
        else:
            payload, pmime = compress(data, mime, px, q)
            pq = "original" if payload is data else f"{px}px q{q}"
        folder = gsheets.settings_dict().get("DRIVE_FOLDER_ID", "")
        ok, res = drive.upload_image(payload, f"{nkey(asn)}_{filename}",
                                     pmime, folder)
        if ok:
            storage, link, file_id = "DRIVE", res["link"], res["id"]
            small, mime2, quality = payload, pmime, pq
        else:
            msg = (f"{filename}: Drive upload failed, saved to the sheet "
                   f"instead ({str(res)[:140]})")

    # ── 2. sheet fallback: a cell has a hard size limit, so compress ──
    if storage == "SHEET":
        if is_pdf:
            small, mime2, quality = data, "application/pdf", "original"
        else:
            small, mime2 = compress(data, mime, px, q)
            quality = "original" if small is data else f"{px}px q{q}"
        if len(small) > MAX_BYTES:
            return False, (f"{filename}: too large for sheet storage "
                           f"({len(small) // 1024} KB). Fix the Drive folder "
                           f"or upload a smaller file.")
        b64 = base64.b64encode(small).decode("ascii")
        chunks = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
        gsheets.append_rows("IMAGE_DATA",
                            [[img_id, i + 1, c] for i, c in enumerate(chunks)])

    gsheets.upsert("ASN_IMAGES", [{
        "IMAGE ID": img_id,
        "ASN NO": asn,
        "FILE NAME": filename,
        "KIND": kind,
        "SOURCE": source,
        "MIME": mime2,
        "SIZE KB": round(len(small) / 1024, 1),
        "QUALITY": quality,
        "STORAGE": storage,
        "DRIVE FILE ID": file_id,
        "LINK": link,
        "UPLOADED AT": ts,
        "UPLOADED BY": user or "unknown",
        "NOTE": note,
    }])
    return True, msg or (f"{filename} saved — {round(len(small) / 1024, 1)} KB, "
                         f"{quality.lower()}, {storage.lower()}")


# ───────────────────────── load ─────────────────────────
def load_bytes(row) -> bytes | None:
    """
    Return the stored file at the quality it was saved with.

    Drive-stored files are fetched back from Drive, so a download gives the
    original upload rather than a preview-sized copy. `row` is an ASN_IMAGES
    record (Series or dict); a bare id also works for sheet storage.
    """
    if isinstance(row, str):
        return load_image(row)
    get = row.get
    if str(get("STORAGE", "") or "").upper() == "DRIVE":
        data = drive.download_file(get("DRIVE FILE ID", ""))
        if data:
            return data
    return load_image(get("IMAGE ID", ""))


def load_image(img_id: str) -> bytes | None:
    """Reassemble the chunks in IMAGE_DATA back into bytes."""
    df = gsheets.get_df("IMAGE_DATA")
    if df.empty:
        return None
    g = df[df["IMAGE ID"].astype(str).str.strip() == str(img_id).strip()].copy()
    if g.empty:
        return None
    g["_s"] = pd.to_numeric(g["SEQ"], errors="coerce").fillna(0)
    b64 = "".join(g.sort_values("_s")["CHUNK"].astype(str))
    try:
        return base64.b64decode(b64)
    except Exception:
        return None


# ───────────────────────── delete ─────────────────────────
def delete_images(img_ids: list[str]) -> int:
    """Remove both the metadata row and the stored chunks."""
    ids = {str(i).strip() for i in img_ids if str(i).strip()}
    if not ids:
        return 0
    meta = gsheets.get_df("ASN_IMAGES")
    if not meta.empty:
        for _, r in meta[meta["IMAGE ID"].astype(str).isin(ids)].iterrows():
            if str(r.get("STORAGE", "")).upper() == "DRIVE":
                drive.delete_file(r.get("DRIVE FILE ID", ""))
    n = gsheets.delete_where("ASN_IMAGES", "IMAGE ID", ids)
    gsheets.delete_where("IMAGE_DATA", "IMAGE ID", ids)
    return n


def delete_for_asn(asns: list[str]) -> int:
    """Remove every attachment belonging to the given ASNs."""
    meta = gsheets.get_df("ASN_IMAGES")
    if meta.empty:
        return 0
    keys = {str(a).strip() for a in asns}
    ids = list(meta.loc[meta["ASN NO"].astype(str).str.strip().isin(keys), "IMAGE ID"])
    return delete_images(ids)
