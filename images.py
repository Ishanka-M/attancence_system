"""
images.py
=========
ASN images save/load කරන කොටස.

ප්‍රශ්නය: Google **service account** එකකට තමන්ගේම Drive storage quota නෑ.
ඒ නිසා folder එකක් share කරලා නැත්නම් Drive upload එක `storageQuotaExceeded`
වෙලා fail වෙනවා — ඒකයි images save නොවුණේ.

විසඳුම: default විදිහට images **Google Sheet එකේම** save වෙනවා.
  * Pillow එකෙන් resize + JPEG compress (කුඩා කරලා)
  * base64 කරලා chunks වලට කඩලා `IMAGE_DATA` sheet එකට
  * `ASN_IMAGES` sheet එකේ metadata (ASN, file name, size, storage)

Drive එක ඕන නම් SETTINGS -> IMAGE_STORAGE = DRIVE කරන්න. Drive fail වුණොත්
automatic ව Sheet එකට fallback වෙනවා — image එක **කවදාවත් නැති වෙන්නේ නෑ**.
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

# Google Sheets cell limit = 50,000 chars. ආරක්ෂිතව 40,000.
CHUNK = 40_000
# Sheet එකේ save කරන්න ඉඩ දෙන උපරිම compressed size
MAX_BYTES = 3_000_000


# ───────────────────────── compression ─────────────────────────
def compress(data: bytes, mime: str, max_px: int = 1400,
             quality: int = 78) -> tuple[bytes, str]:
    """
    Image එක කුඩා කරනවා. Pillow නැත්නම් original එකම return කරනවා.
    return: (bytes, mime)
    """
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


def _opts() -> tuple[str, int, int]:
    s = gsheets.settings_dict()
    mode = str(s.get("IMAGE_STORAGE", "SHEET")).strip().upper() or "SHEET"
    px = int(gsheets.setting_float(s, "IMAGE_MAX_PX", 1400) or 1400)
    q = int(gsheets.setting_float(s, "IMAGE_QUALITY", 78) or 78)
    return mode, px, q


# ───────────────────────── save ─────────────────────────
def save_image(asn: str, filename: str, data: bytes, mime: str,
               source: str = "MANUAL UPLOAD", user: str = "",
               note: str = "", kind: str = "") -> tuple[bool, str]:
    """
    Image එකක් හෝ PDF එකක් save කරනවා.  return: (ok, message)
    IMAGE_STORAGE=DRIVE නම් මුලින්ම Drive, fail වුණොත් Sheet එකට fallback.
    """
    if not data:
        return False, f"{filename}: file එක හිස්."

    mode, px, q = _opts()
    is_pdf = (str(mime).lower() == "application/pdf"
              or str(filename).lower().endswith(".pdf"))
    kind = kind or ("PDF" if is_pdf else "IMAGE")

    if is_pdf:
        small, mime2 = data, "application/pdf"      # PDF compress කරන්නේ නෑ
    else:
        small, mime2 = compress(data, mime, px, q)
    img_id = uuid.uuid4().hex[:10].upper()
    ts = now_str()
    storage, link, file_id = "SHEET", "", ""
    msg = ""

    # ── 1. Drive (optional) ──
    if mode == "DRIVE":
        folder = gsheets.settings_dict().get("DRIVE_FOLDER_ID", "")
        ok, res = drive.upload_image(small, f"{nkey(asn)}_{filename}", mime2, folder)
        if ok:
            storage, link, file_id = "DRIVE", res["link"], res["id"]
        else:
            msg = f"Drive fail → Sheet එකට save කළා ({str(res)[:120]})"

    # ── 2. Sheet එකට bytes ──
    if storage == "SHEET":
        if len(small) > MAX_BYTES:
            return False, (f"{filename}: file එක ලොකු වැඩියි "
                           f"({len(small) // 1024} KB) — Sheet එකේ තියාගන්න බෑ. "
                           f"Drive folder එක හදාගන්න, නැත්නම් පොඩි එකක් දාන්න.")
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
        "STORAGE": storage,
        "DRIVE FILE ID": file_id,
        "LINK": link,
        "UPLOADED AT": ts,
        "UPLOADED BY": user or "unknown",
        "NOTE": note,
    }])
    return True, msg or f"{filename} ✅ ({round(len(small) / 1024, 1)} KB · {storage})"


# ───────────────────────── load ─────────────────────────
def load_image(img_id: str) -> bytes | None:
    """IMAGE_DATA sheet එකෙන් chunks එකතු කරලා bytes ආපහු දෙනවා."""
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
    """Metadata + chunks දෙකම අයින් කරනවා."""
    ids = {str(i).strip() for i in img_ids if str(i).strip()}
    if not ids:
        return 0
    n = gsheets.delete_where("ASN_IMAGES", "IMAGE ID", ids)
    gsheets.delete_where("IMAGE_DATA", "IMAGE ID", ids)
    return n


def delete_for_asn(asns: list[str]) -> int:
    """ASN එකකට අදාළ හැම image එකක්ම අයින්."""
    meta = gsheets.get_df("ASN_IMAGES")
    if meta.empty:
        return 0
    keys = {str(a).strip() for a in asns}
    ids = list(meta.loc[meta["ASN NO"].astype(str).str.strip().isin(keys), "IMAGE ID"])
    return delete_images(ids)
