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
import re

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


def _status(err) -> int:
    for attr in ("resp", "response"):
        r = getattr(err, attr, None)
        code = getattr(r, "status", None) or getattr(r, "status_code", None)
        if code:
            try:
                return int(code)
            except Exception:
                pass
    m = re.search(r"HttpError (\d{3})", str(err))
    return int(m.group(1)) if m else 0


def explain(err, fid: str = "") -> str:
    """Turn a Drive API error into something a warehouse user can act on."""
    code = _status(err)
    sa = service_email() or "the service account"
    if code == 404:
        return (f"Drive folder not found (404). The service account cannot see "
                f"folder `{fid or '?'}`. Open the folder in Drive, press Share, "
                f"and add {sa} as an Editor. If the id came from a link, check "
                f"you copied the whole link.")
    if code == 403:
        txt = str(err)
        if "storageQuota" in txt or "quota" in txt.lower():
            return (f"The service account has no Drive storage of its own (403). "
                    f"Move the folder into a Shared Drive and add {sa} as a "
                    f"member, or set Storage to SHEET in Setup.")
        return (f"Permission denied (403). {sa} can see the folder but cannot "
                f"add files to it - share it as an **Editor**, not a Viewer.")
    if code == 401:
        return ("Authentication failed (401). Check the service account key in "
                "secrets, and that the Drive API is enabled for the project.")
    return f"{type(err).__name__}: {str(err)[:200]}"


def diagnose(folder_value: str) -> list[dict]:
    """
    Walk the whole Drive path step by step and report where it breaks.
    Returns [{step, ok, detail}] - the first failure explains the fix.
    """
    steps: list[dict] = []

    def add(step, ok, detail=""):
        steps.append({"step": step, "ok": ok, "detail": detail})
        return ok

    if not add("Drive client installed", available(),
               "" if available() else "pip install google-api-python-client"):
        return steps

    sa = service_email()
    if not add("Service account found", bool(sa), sa or "missing from secrets"):
        return steps

    fid = folder_id(folder_value)
    if not add("Folder id parsed", bool(fid),
               fid or "no folder link or id configured in Setup"):
        return steps

    try:
        svc = _service()
        f = api(svc.files().get(fileId=fid,
                                fields="id,name,mimeType,driveId,capabilities",
                                supportsAllDrives=True).execute)
    except Exception as e:
        add("Folder visible to the service account", False, explain(e, fid))
        return steps

    is_folder = f.get("mimeType") == "application/vnd.google-apps.folder"
    kind = "Shared Drive" if f.get("driveId") else "My Drive"
    add("Folder visible to the service account", True,
        f"{f.get('name')} ({kind})")
    if not add("Target is a folder", is_folder,
               "" if is_folder else "That id points at a file, not a folder"):
        return steps

    can_add = bool(f.get("capabilities", {}).get("canAddChildren", True))
    add("Editor access", can_add,
        "" if can_add else f"Share the folder with {sa} as an Editor")

    # a real upload is the only honest test of write access and quota
    try:
        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(io.BytesIO(b"asn-grn-connection-test"),
                                  mimetype="text/plain", resumable=False)
        t = api(_service().files().create(
            body={"name": "_asn_grn_test.txt", "parents": [fid]},
            media_body=media, fields="id",
            supportsAllDrives=True).execute)
        add("Test file uploaded", True, "write access confirmed")
        try:
            api(_service().files().delete(fileId=t["id"],
                                          supportsAllDrives=True).execute)
            add("Test file removed", True)
        except Exception:
            add("Test file removed", False,
                "Upload worked but the test file could not be deleted - "
                "remove _asn_grn_test.txt manually")
    except Exception as e:
        add("Test file uploaded", False, explain(e, fid))

    return steps


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
        return False, explain(e, fid)


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
        return False, explain(e, folder_id(folder))


def download_file(file_id: str) -> bytes | None:
    """Fetch a file back from Drive at full, original quality."""
    if not available() or not str(file_id).strip():
        return None
    try:
        import io as _io
        from googleapiclient.http import MediaIoBaseDownload
        req = _service().files().get_media(fileId=file_id,
                                           supportsAllDrives=True)
        buf = _io.BytesIO()
        dl = MediaIoBaseDownload(buf, req, chunksize=2 * 1024 * 1024)
        done = False
        while not done:
            _, done = api(dl.next_chunk)
        return buf.getvalue()
    except Exception:
        return None


def delete_file(file_id: str) -> bool:
    if not available() or not str(file_id).strip():
        return False
    try:
        api(_service().files().delete(fileId=file_id,
                                      supportsAllDrives=True).execute)
        return True
    except Exception:
        return False
