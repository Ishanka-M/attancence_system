"""
drive.py
========
Google Drive upload helper.

වැදගත්: Google **service account** එකකට තමන්ගේම Drive storage quota නෑ.
ඒ නිසා —
  * folder එක ඔයාගේ Drive එකේ එකක් නම්, ඒක service account email එකට
    **Editor** විදිහට share කරලා තියෙන්න ඕනේ;
  * ඒත් සමහර Google policy යටතේ My Drive folder එකකට service account
    එකකින් upload කරද්දී `storageQuotaExceeded` එනවා. එහෙම නම්
    **Shared Drive** එකක් පාවිච්චි කරන්න.

Upload එක fail වුණොත් images.py එකෙන් automatic ව Google Sheet එකට
fallback වෙනවා — ඒ නිසා image එකක් කවදාවත් නැති වෙන්නේ නෑ.
"""
from __future__ import annotations

import io

import streamlit as st

import gsheets
from gsheets import api


def available() -> bool:
    try:
        import googleapiclient  # noqa: F401
        return True
    except Exception:
        return False


@st.cache_resource(show_spinner=False)
def _service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=gsheets.get_credentials(),
                 cache_discovery=False)


def folder_id(value: str) -> str:
    """
    Drive folder link එකක් හෝ ID එකක් -> පිරිසිදු ID එක.

    https://drive.google.com/drive/u/2/folders/14t0fa...Ps7mP?usp=sharing
        -> 14t0fa...Ps7mP
    """
    v = str(value or "").strip()
    if not v:
        return ""
    if "/folders/" in v:
        v = v.split("/folders/")[1]
    elif "id=" in v:
        v = v.split("id=")[1]
    for sep in ("?", "&", "/", "#"):
        v = v.split(sep)[0]
    return v.strip()


def _ensure_folder(svc, name: str = "ASN_IMAGES") -> str:
    q = (f"mimeType='application/vnd.google-apps.folder' and name='{name}' "
         f"and trashed=false")
    res = api(svc.files().list(q=q, fields="files(id,name)", pageSize=1,
                               supportsAllDrives=True).execute)
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    f = api(svc.files().create(body=meta, fields="id",
                               supportsAllDrives=True).execute)
    return f["id"]


def service_email() -> str:
    try:
        return dict(st.secrets["gcp_service_account"]).get("client_email", "")
    except Exception:
        return ""


def check_folder(fid: str) -> tuple[bool, str]:
    """Folder එකට access තියෙනවද කියලා බලනවා. return (ok, message)"""
    if not available():
        return False, "google-api-python-client install වෙලා නෑ."
    fid = folder_id(fid)
    if not fid:
        return False, "Folder ID එකක් දීලා නෑ."
    try:
        svc = _service()
        f = api(svc.files().get(fileId=fid, fields="id,name,mimeType,driveId",
                                supportsAllDrives=True).execute)
        kind = "Shared Drive" if f.get("driveId") else "My Drive"
        return True, f"✅ `{f.get('name')}` ({kind}) — access තියෙනවා."
    except Exception as e:
        return False, (f"❌ Folder එකට access නෑ ({type(e).__name__}). "
                       f"Folder එක මේ email එකට **Editor** විදිහට share කරන්න: "
                       f"`{service_email()}`")


def upload_image(data: bytes, filename: str, mime: str,
                 folder: str = "") -> tuple[bool, dict | str]:
    """return: (True, {"id","link","name"})  |  (False, "error message")"""
    if not available():
        return False, ("google-api-python-client install වෙලා නෑ. "
                       "`pip install google-api-python-client` කරන්න.")
    try:
        from googleapiclient.http import MediaIoBaseUpload
        svc = _service()
        fid = folder_id(folder) or _ensure_folder(svc)

        media = MediaIoBaseUpload(io.BytesIO(data),
                                  mimetype=mime or "application/octet-stream",
                                  resumable=False)
        meta = {"name": filename, "parents": [fid]}
        f = api(svc.files().create(body=meta, media_body=media,
                                   fields="id,name,webViewLink",
                                   supportsAllDrives=True).execute)
        try:
            api(svc.permissions().create(
                fileId=f["id"], body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True).execute)
        except Exception:
            pass                      # link share fail වුණත් file එක තියෙනවා
        return True, {
            "id": f["id"],
            "name": f.get("name", filename),
            "link": f.get("webViewLink",
                          f"https://drive.google.com/file/d/{f['id']}/view"),
        }
    except Exception as e:
        msg = str(e)
        if "storageQuota" in msg or "quota" in msg.lower():
            msg = ("Service account එකට Drive storage quota නෑ. Folder එක "
                   "**Shared Drive** එකක තියෙන එකක් කරන්න, නැත්නම් Setup එකේ "
                   "Image storage = SHEET කරන්න.")
        return False, f"{type(e).__name__}: {msg[:220]}"


def delete_file(file_id: str) -> bool:
    if not available() or not str(file_id).strip():
        return False
    try:
        api(_service().files().delete(fileId=file_id,
                                      supportsAllDrives=True).execute)
        return True
    except Exception:
        return False
