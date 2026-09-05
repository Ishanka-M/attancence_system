"""
storage.py
==========
Cloudflare R2 attachments for ASN documents (images / PDFs).

R2 is Cloudflare's S3-compatible object storage, so we talk to it with
the plain `boto3` S3 client pointed at R2's endpoint - no Cloudflare-
specific SDK needed. Every uploaded file gets a public URL that is
saved as a row in the ATTACHMENTS sheet (see schema.py), keyed by
ASN No and, optionally, Invoice Number - so AX GRN can look files back
up by either one.

Nothing here talks to Google Sheets directly; app.py wires the two
together (upload -> get_url -> gsheets.upsert("ATTACHMENTS", ...)).
"""
from __future__ import annotations

import gzip
import mimetypes
import uuid

import streamlit as st

try:
    import boto3
    from botocore.config import Config
    _BOTO_OK = True
except Exception:                                  # pragma: no cover
    _BOTO_OK = False


def _cfg() -> dict:
    try:
        return dict(st.secrets.get("cloudflare_r2", {}))
    except Exception:
        return {}


def enabled() -> bool:
    """True once every value R2 needs is present in secrets.toml."""
    if not _BOTO_OK:
        return False
    c = _cfg()
    return bool(c.get("account_id") and c.get("access_key_id")
                and c.get("secret_access_key") and c.get("bucket_name")
                and c.get("public_base_url"))


@st.cache_resource(show_spinner=False)
def _client():
    c = _cfg()
    endpoint = f"https://{c['account_id']}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=c["access_key_id"],
        aws_secret_access_key=c["secret_access_key"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def _safe_name(name: str) -> str:
    keep = "-_.() "
    return "".join(ch if ch.isalnum() or ch in keep else "_" for ch in name).strip()


def object_key(asn_no: str, filename: str) -> str:
    """asn/<ASN NO>/<uid>_<original name> - the uid stops two people's
    same-named file from overwriting each other."""
    asn = _safe_name(str(asn_no or "misc").strip()) or "misc"
    fn = _safe_name(filename) or "file"
    return f"asn/{asn}/{uuid.uuid4().hex[:8]}_{fn}"


def upload(file_bytes: bytes, key: str, content_type: str = "") -> str:
    """Upload one file to the bucket and return its public URL."""
    if not enabled():
        raise RuntimeError("Cloudflare R2 is not configured - see "
                            ".streamlit/secrets.toml.example")
    c = _cfg()
    ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
    _client().put_object(Bucket=c["bucket_name"], Key=key, Body=file_bytes,
                          ContentType=ct)
    base = c["public_base_url"].rstrip("/")
    return f"{base}/{key}"


def upload_compressed(file_bytes: bytes, key: str, content_type: str = "") -> tuple[str, int]:
    """
    Gzip-compress a file losslessly and upload it under `key + '.gz'`.

    Gzip is lossless - decompressing gives back the exact original bytes,
    so this only saves storage/transfer for documents (PDF, Excel); images
    are already compressed formats and barely shrink further, and would
    stop rendering as thumbnails via their direct URL, so callers should
    use plain upload() for those instead.

    Returns (public_url, compressed_size_bytes).
    """
    if not enabled():
        raise RuntimeError("Cloudflare R2 is not configured - see "
                            ".streamlit/secrets.toml.example")
    c = _cfg()
    ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
    packed = gzip.compress(file_bytes, compresslevel=6)
    gz_key = f"{key}.gz"
    _client().put_object(Bucket=c["bucket_name"], Key=gz_key, Body=packed,
                          ContentType="application/gzip",
                          Metadata={"original-type": ct})
    base = c["public_base_url"].rstrip("/")
    return f"{base}/{gz_key}", len(packed)


def fetch_bytes(url: str) -> bytes:
    """Read an object straight back out of the bucket, given its public URL."""
    if not enabled():
        raise RuntimeError("Cloudflare R2 is not configured - see "
                            ".streamlit/secrets.toml.example")
    c = _cfg()
    obj = _client().get_object(Bucket=c["bucket_name"], Key=url_to_key(url))
    return obj["Body"].read()


def download_decompressed(url: str) -> bytes:
    """The inverse of upload_compressed() - fetch the .gz object and gunzip
    it back to the exact original bytes. Lossless, so what comes out is
    bit-for-bit identical to what was uploaded, at full original quality."""
    return gzip.decompress(fetch_bytes(url))


def delete(key: str) -> None:
    if not enabled():
        return
    c = _cfg()
    _client().delete_object(Bucket=c["bucket_name"], Key=key)


def url_to_key(url: str) -> str:
    """Reverse of upload()'s URL, for delete-by-url convenience."""
    base = _cfg().get("public_base_url", "").rstrip("/")
    return url[len(base) + 1:] if base and url.startswith(base) else url
