"""
drive.py
========
Google Drive upload helper.

A Google service account has no Drive storage quota of its own, so:
  * a folder in your own Drive must be shared with the service account
    email as an Editor;
  * under some Google policies an upload into a My Drive folder still
    returns storageQuotaExceeded - use a Shared Drive in that case.

If the upload fails, images.py falls back to the Google Sheet, so an
attachment is never lost.
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
    Turn a Drive folder link or raw id into a clean folder id.

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
    """Check whether the folder is reachable. Returns (ok, message)."""
    if not available():
        return False, "google-api-python-client is not installed."
    fid = folder_id(fid)
    if not fid:
        return False, "No folder id or link has been configured."
    try:
        svc = _service()
        f = api(svc.files().get(fileId=fid, fields="id,name,mimeType,driveId",
                                supportsAllDrives=True).execute)
        kind = "Shared Drive" if f.get("driveId") else "My Drive"
        return True, f"Connected to `{f.get('name')}` ({kind})."
    except Exception as e:
        return False, (f"No access to that folder ({type(e).__name__}). "
                       f"Share it as an Editor with `{service_email()}`.")


def upload_image(data: bytes, filename: str, mime: str,
                 folder: str = "") -> tuple[bool, dict | str]:
    """Returns (True, {"id","link","name"}) or (False, "error message")."""
    if not available():
        return False, ("google-api-python-client is not installed. "
                       "Run `pip install google-api-python-client`.")
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
            pass                      # the file exists even if link sharing fails
        return True, {
            "id": f["id"],
            "name": f.get("name", filename),
            "link": f.get("webViewLink",
                          f"https://drive.google.com/file/d/{f['id']}/view"),
        }
    except Exception as e:
        msg = str(e)
        if "storageQuota" in msg or "quota" in msg.lower():
            msg = ("The service account has no Drive storage quota. Move the "
                   "folder into a Shared Drive, or set Storage = SHEET in Setup.")
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
