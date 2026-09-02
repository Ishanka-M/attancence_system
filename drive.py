"""
drive.py
========
ASN images Google Drive එකට upload කරලා link එකක් දෙන helper එක.

  * SETTINGS sheet එකේ DRIVE_FOLDER_ID එක දුන්නොත් ඒ folder එකට යනවා.
  * හිස් නම් — service account එකේ My Drive එකේ "ASN_IMAGES" folder එකක්
    auto-create කරලා ඒකට දානවා.
  * Drive fail වුණොත් app එක crash වෙන්නේ නෑ — (ok=False, error) එනවා.

Note: service account එකට තමන්ගේම storage quota නෑ. හොඳම විදිය —
Google Drive එකේ folder එකක් හදලා service account email එකට **Editor**
විදිහට share කරලා, ඒකේ ID එක SETTINGS -> DRIVE_FOLDER_ID එකට දාන්න.
"""
from __future__ import annotations

import io

import streamlit as st

import gsheets


@st.cache_resource(show_spinner=False)
def _service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=gsheets.get_credentials(),
                 cache_discovery=False)


def _ensure_folder(svc, name: str = "ASN_IMAGES") -> str:
    q = (f"mimeType='application/vnd.google-apps.folder' and name='{name}' "
         f"and trashed=false")
    res = svc.files().list(q=q, fields="files(id,name)", pageSize=1,
                           supportsAllDrives=True).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    f = svc.files().create(body=meta, fields="id", supportsAllDrives=True).execute()
    return f["id"]


def available() -> bool:
    try:
        import googleapiclient  # noqa: F401
        return True
    except Exception:
        return False


def upload_image(data: bytes, filename: str, mime: str,
                 folder_id: str = "") -> tuple[bool, dict | str]:
    """
    return: (True, {"id","link","name"})  |  (False, "error message")
    """
    if not available():
        return False, ("google-api-python-client install වෙලා නෑ. "
                       "`pip install google-api-python-client` කරන්න.")
    try:
        from googleapiclient.http import MediaIoBaseUpload
        svc = _service()
        fid = (folder_id or "").strip()
        if "drive.google.com" in fid and "/folders/" in fid:
            fid = fid.split("/folders/")[1].split("?")[0].split("/")[0]
        if not fid:
            fid = _ensure_folder(svc)

        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime or "image/png",
                                  resumable=False)
        meta = {"name": filename, "parents": [fid]}
        f = svc.files().create(body=meta, media_body=media,
                               fields="id,name,webViewLink",
                               supportsAllDrives=True).execute()
        # link එක share කරන්න (fail වුණත් upload එක හරි)
        try:
            svc.permissions().create(
                fileId=f["id"], body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True).execute()
        except Exception:
            pass
        return True, {
            "id": f["id"],
            "name": f.get("name", filename),
            "link": f.get("webViewLink", f"https://drive.google.com/file/d/{f['id']}/view"),
        }
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
