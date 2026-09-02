"""
gsheets.py
==========
Google Sheets backend.

Responsibilities:
  1. Build a gspread client from service account credentials (st.secrets).
  2. Open or create the spreadsheet.
  3. ensure_all() - AUTO-CREATE every missing tab in schema.SHEETS, write
     headers and seed the master sheets (USER-M / SETTINGS).
  4. read / append / overwrite / upsert helpers.
  5. An API manager that rate limits and retries Google API calls.
"""
from __future__ import annotations

import random
import threading
import time
from collections import deque

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

import schema

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ═══════════════════════════════════════════════════════════════════
#  API MANAGER
#  The Sheets API allows about 60 requests per minute per user. Going
#  over that returns 429. This layer:
#     * tracks calls per minute and waits when the limit is close
#     * retries 429 / 500 / 503 with exponential backoff and jitter
#     * keeps call, retry and error counters for the Setup page
# ═══════════════════════════════════════════════════════════════════
API_WINDOW = 60.0           # seconds
API_MAX_PER_WINDOW = 55     # stay a little under the Google limit
API_MAX_RETRIES = 5

_lock = threading.Lock()
_calls: deque[float] = deque()          # recent call timestamps
_stats = {"calls": 0, "retries": 0, "errors": 0, "throttled": 0,
          "last_error": "", "last_call": ""}

RETRY_CODES = {429, 500, 502, 503, 504}


def _status_of(err) -> int:
    """Extract the HTTP status code from a gspread or googleapiclient error."""
    for attr in ("response", "resp"):
        r = getattr(err, attr, None)
        code = getattr(r, "status_code", None) or getattr(r, "status", None)
        if code:
            try:
                return int(code)
            except Exception:
                pass
    txt = str(err)
    for c in RETRY_CODES:
        if f"[{c}]" in txt or f" {c} " in txt:
            return c
    return 0


def _throttle():
    """Wait if the per-minute call limit has been reached."""
    with _lock:
        now = time.time()
        while _calls and now - _calls[0] > API_WINDOW:
            _calls.popleft()
        if len(_calls) >= API_MAX_PER_WINDOW:
            wait = API_WINDOW - (now - _calls[0]) + 0.1
            _stats["throttled"] += 1
        else:
            wait = 0
    if wait > 0:
        time.sleep(min(wait, API_WINDOW))
        with _lock:
            now = time.time()
            while _calls and now - _calls[0] > API_WINDOW:
                _calls.popleft()
    with _lock:
        _calls.append(time.time())
        _stats["calls"] += 1
        _stats["last_call"] = time.strftime("%H:%M:%S")


def api(fn, *args, **kwargs):
    """
    Run a Google API call safely.
    Quota and server errors are retried; anything else is raised as-is.
    """
    delay = 1.0
    last = None
    for attempt in range(API_MAX_RETRIES):
        _throttle()
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            code = _status_of(e)
            if code not in RETRY_CODES or attempt == API_MAX_RETRIES - 1:
                with _lock:
                    _stats["errors"] += 1
                    _stats["last_error"] = f"{type(e).__name__}: {str(e)[:180]}"
                raise
            with _lock:
                _stats["retries"] += 1
            time.sleep(delay + random.uniform(0, 0.4))
            delay = min(delay * 2, 16.0)
    raise last                                             # pragma: no cover


def api_stats() -> dict:
    with _lock:
        now = time.time()
        recent = sum(1 for t in _calls if now - t <= API_WINDOW)
        d = dict(_stats)
    d["last_minute"] = recent
    d["limit"] = API_MAX_PER_WINDOW
    d["headroom"] = max(API_MAX_PER_WINDOW - recent, 0)
    return d


def api_reset_stats():
    with _lock:
        for k in ("calls", "retries", "errors", "throttled"):
            _stats[k] = 0
        _stats["last_error"] = ""


def apply_api_settings(d: dict | None = None):
    """Apply API_RATE_LIMIT / CACHE_TTL from the SETTINGS sheet."""
    global API_MAX_PER_WINDOW
    try:
        d = d if d is not None else settings_dict()
        r = int(setting_float(d, "API_RATE_LIMIT", 55))
        API_MAX_PER_WINDOW = max(10, min(r, 60))
    except Exception:
        pass


# ───────────────────────── client / spreadsheet ─────────────────────────
@st.cache_resource(show_spinner=False)
def get_credentials():
    info = dict(st.secrets["gcp_service_account"])
    return Credentials.from_service_account_info(info, scopes=SCOPES)


@st.cache_resource(show_spinner=False)
def get_client() -> gspread.Client:
    return gspread.authorize(get_credentials())


@st.cache_resource(show_spinner=False)
def get_spreadsheet():
    """Open the spreadsheet named in secrets, creating it if needed."""
    client = get_client()
    cfg = st.secrets.get("app", {})
    sid = str(cfg.get("spreadsheet_id", "")).strip()
    name = str(cfg.get("spreadsheet_name", "EFL ASN GRN System")).strip()
    sa_email = dict(st.secrets["gcp_service_account"]).get("client_email", "?")

    # accept a full spreadsheet URL and pull the id out of it
    if "docs.google.com" in sid and "/d/" in sid:
        sid = sid.split("/d/")[1].split("/")[0]

    if sid:
        try:
            return api(client.open_by_key, sid)
        except gspread.exceptions.APIError as e:
            raise RuntimeError(
                f"Cannot open the spreadsheet (id='{sid}').\n\n"
                f"1) Check spreadsheet_id - it is only the part of the URL "
                f"between /d/ and /edit.\n"
                f"2) Share the sheet with this service account as an Editor:\n"
                f"   {sa_email}\n\n"
                f"Or leave spreadsheet_id empty and let the app create a new one."
            ) from e

    try:
        return api(client.open, name)
    except gspread.SpreadsheetNotFound:
        sh = api(client.create, name)
        share = str(cfg.get("share_email", "")).strip()
        if share:
            api(sh.share, share, perm_type="user", role="writer")
        return sh


def spreadsheet_url() -> str:
    try:
        return get_spreadsheet().url
    except Exception:
        return ""


def _update(ws, values, rng="A1"):
    """Update wrapper that works with both gspread 5 and 6."""
    try:
        api(ws.update, values=values, range_name=rng, value_input_option="USER_ENTERED")
    except TypeError:                                     # pragma: no cover
        api(ws.update, rng, values, value_input_option="USER_ENTERED")


def _ws(sheet_key: str):
    """
    Return the worksheet, creating it on the spot (with headers and seed
    rows) if it does not exist yet, so a write never fails with
    WorksheetNotFound.
    """
    sh = get_spreadsheet()
    cfg = schema.SHEETS[sheet_key]
    title, headers = cfg["title"], cfg["headers"]
    try:
        ws = api(sh.worksheet, title)
    except gspread.WorksheetNotFound:
        ws = api(sh.add_worksheet, title=title, rows=2000, cols=max(len(headers), 12))
        rows = [headers]
        if cfg.get("seed"):
            rows += [list(r) for r in cfg["seed"]]
        _update(ws, rows)
        get_df.clear()
        return ws

    # fill in the header row if the existing sheet is blank
    try:
        if not any(str(c).strip() for c in api(ws.row_values, 1)):
            _update(ws, [headers])
            get_df.clear()
    except Exception:
        pass
    return ws


# ───────────────────────── AUTO-CREATE sheets ─────────────────────────
def ensure_all(seed_masters: bool = True) -> tuple[list[str], list[str]]:
    """
    Create every sheet in schema.SHEETS that does not exist yet. If a new
    column was added to the schema, append that header to the existing
    sheet without losing data.

    Returns (created_titles, patched_titles).
    """
    sh = get_spreadsheet()
    existing = {ws.title: ws for ws in api(sh.worksheets)}
    created, patched = [], []

    for key, cfg in schema.SHEETS.items():
        title, headers = cfg["title"], cfg["headers"]

        if title in existing:
            ws = existing[title]
            first = [str(c).strip() for c in api(ws.row_values, 1)]
            if not any(first):
                _update(ws, [headers])
                patched.append(title)
                continue
            missing = [h for h in headers if h not in first]
            if missing:
                new_header = first + missing
                if ws.col_count < len(new_header):
                    api(ws.add_cols, len(new_header) - ws.col_count)
                _update(ws, [new_header])
                patched.append(title)
            continue

        # ── create the missing tab ──
        ws = api(sh.add_worksheet, title=title, rows=2000, cols=max(len(headers), 12))
        rows = [headers]
        if seed_masters and cfg.get("seed"):
            rows += [list(r) for r in cfg["seed"]]
        _update(ws, rows)
        created.append(title)
        existing[title] = ws
        time.sleep(0.2)          # be gentle with the API quota

    # remove the default empty "Sheet1" created with a new spreadsheet
    try:
        titles = {w.title for w in api(sh.worksheets)}
        if len(titles) > 1 and "Sheet1" in titles:
            api(sh.del_worksheet, api(sh.worksheet, "Sheet1"))
    except Exception:
        pass

    get_df.clear()
    return created, patched


@st.cache_resource(show_spinner=False)
def ensure_missing_once() -> list[str]:
    """
    Run once at start-up and create only the missing tabs. Costs one
    worksheets() call plus a create per missing tab, so it is much
    lighter than ensure_all().
    """
    created = []
    try:
        sh = get_spreadsheet()
        apply_api_settings()
        existing = {ws.title for ws in api(sh.worksheets)}
        for key, cfg in schema.SHEETS.items():
            if cfg["title"] in existing:
                continue
            headers = cfg["headers"]
            ws = api(sh.add_worksheet, title=cfg["title"], rows=2000,
                     cols=max(len(headers), 12))
            rows = [headers]
            if cfg.get("seed"):
                rows += [list(r) for r in cfg["seed"]]
            _update(ws, rows)
            created.append(cfg["title"])
            time.sleep(0.2)
        if created:
            get_df.clear()
    except Exception:
        pass                      # never block start-up; _ws() creates on demand
    return created


def sheet_status() -> pd.DataFrame:
    sh = get_spreadsheet()
    existing = {ws.title: ws for ws in api(sh.worksheets)}
    out = []
    for key, cfg in schema.SHEETS.items():
        t = cfg["title"]
        ws = existing.get(t)
        rows = 0
        if ws:
            try:
                rows = max(len(api(ws.get_all_values)) - 1, 0)
            except Exception:
                rows = 0
        out.append({
            "Sheet": t,
            "Type": cfg["kind"],
            "Exists": "✅" if ws else "❌",
            "Columns": len(cfg["headers"]),
            "Data rows": rows,
        })
    return pd.DataFrame(out)


# ───────────────────────── read / write ─────────────────────────
@st.cache_data(ttl=90, show_spinner=False)
def get_df(sheet_key: str) -> pd.DataFrame:
    """Read a worksheet into a DataFrame (cached)."""
    sh = get_spreadsheet()
    cfg = schema.SHEETS[sheet_key]
    try:
        ws = api(sh.worksheet, cfg["title"])
    except gspread.WorksheetNotFound:
        return pd.DataFrame(columns=cfg["headers"])
    values = api(ws.get_all_values)
    if not values:
        return pd.DataFrame(columns=cfg["headers"])
    header, *data = values
    df = pd.DataFrame(data, columns=header)
    df = df.loc[:, [c for c in df.columns if str(c).strip() != ""]]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    for h in cfg["headers"]:
        if h not in df.columns:
            df[h] = ""
    return df.reindex(columns=cfg["headers"])


def refresh():
    get_df.clear()


def append_rows(sheet_key: str, rows: list[list]):
    if not rows:
        return
    ws = _ws(sheet_key)
    api(ws.append_rows,
        [["" if v is None else str(v) for v in r] for r in rows],
        value_input_option="USER_ENTERED")
    get_df.clear()


def overwrite(sheet_key: str, df: pd.DataFrame):
    """Clear the sheet and rewrite it from the DataFrame."""
    cfg = schema.SHEETS[sheet_key]
    ws = _ws(sheet_key)
    df = df.reindex(columns=cfg["headers"]).fillna("")
    body = [cfg["headers"]] + df.astype(str).values.tolist()
    need_rows, need_cols = len(body) + 50, len(cfg["headers"])
    if ws.row_count < need_rows:
        api(ws.add_rows, need_rows - ws.row_count)
    if ws.col_count < need_cols:
        api(ws.add_cols, need_cols - ws.col_count)
    api(ws.clear)
    _update(ws, body)
    get_df.clear()


def upsert(sheet_key: str, rows: list[dict]) -> tuple[int, int]:
    """
    Upsert by the schema key column: update when the key already exists,
    otherwise append. Returns (added, updated).
    """
    cfg = schema.SHEETS[sheet_key]
    headers, key = cfg["headers"], cfg.get("key")
    if not key:
        append_rows(sheet_key, [[r.get(h, "") for h in headers] for r in rows])
        return len(rows), 0

    df = get_df(sheet_key)
    cur = df.fillna("").astype(str).values.tolist() if not df.empty else []
    ki = headers.index(key)
    index = {str(r[ki]).strip(): i for i, r in enumerate(cur)}

    added = updated = 0
    for r in rows:
        srow = ["" if r.get(h) is None else str(r.get(h, "")) for h in headers]
        k = srow[ki].strip()
        if k and k in index:
            # do not let blank incoming values wipe existing data
            old = cur[index[k]]
            merged = [new if str(new).strip() != "" else old[i] for i, new in enumerate(srow)]
            cur[index[k]] = merged
            updated += 1
        else:
            cur.append(srow)
            if k:
                index[k] = len(cur) - 1
            added += 1

    overwrite(sheet_key, pd.DataFrame(cur, columns=headers))
    return added, updated


def replace_rows(sheet_key: str, new_df: pd.DataFrame, key_values: list[str]):
    """Drop rows whose key is in key_values, then append new_df."""
    cfg = schema.SHEETS[sheet_key]
    key = cfg["key"]
    cur = get_df(sheet_key)
    if not cur.empty and key:
        keep = ~cur[key].astype(str).str.strip().isin([str(k).strip() for k in key_values])
        cur = cur[keep]
    out = pd.concat([cur, new_df.reindex(columns=cfg["headers"])], ignore_index=True)
    overwrite(sheet_key, out)


def upsert_by(sheet_key: str, rows: list[dict], key_cols: list[str],
              fallback_cols: list[str] | None = None) -> tuple[int, int]:
    """
    Upsert using a composite key (e.g. Invoice Number + Pallet).

    Rows whose key already exists are REPLACED; new keys are appended.
    If every key column is blank for a row, `fallback_cols` is used
    instead (e.g. Pallet on its own when the invoice number is missing).

    Returns (added, replaced).
    """
    cfg = schema.SHEETS[sheet_key]
    headers = cfg["headers"]

    def key_of(get) -> str:
        parts = [str(get(c) or "").strip().upper() for c in key_cols]
        if not any(parts) and fallback_cols:
            parts = [str(get(c) or "").strip().upper() for c in fallback_cols]
        return "|".join(parts)

    cur = get_df(sheet_key)
    cur_rows = cur.fillna("").astype(str).values.tolist() if not cur.empty else []
    idx = {}
    for i, r in enumerate(cur_rows):
        d = dict(zip(headers, r))
        k = key_of(d.get)
        if k.strip("|"):
            idx[k] = i

    added = replaced = 0
    for r in rows:
        srow = ["" if r.get(h) is None else str(r.get(h, "")) for h in headers]
        k = key_of(r.get)
        if k.strip("|") and k in idx:
            cur_rows[idx[k]] = srow          # full replace, not a merge
            replaced += 1
        else:
            cur_rows.append(srow)
            if k.strip("|"):
                idx[k] = len(cur_rows) - 1
            added += 1

    overwrite(sheet_key, pd.DataFrame(cur_rows, columns=headers))
    return added, replaced


# ───────────────────────── delete / reset ─────────────────────────
def delete_where(sheet_key: str, column: str, values) -> int:
    """Delete every row whose `column` value is in `values`. Returns the count."""
    df = get_df(sheet_key)
    if df.empty or column not in df.columns:
        return 0
    keys = {str(v).strip() for v in values if str(v).strip()}
    if not keys:
        return 0
    mask = df[column].astype(str).str.strip().isin(keys)
    n = int(mask.sum())
    if n:
        overwrite(sheet_key, df[~mask].reset_index(drop=True))
    return n


def clear_sheet(sheet_key: str) -> int:
    """Remove all data rows, keeping only the header row."""
    df = get_df(sheet_key)
    n = len(df)
    overwrite(sheet_key, pd.DataFrame(columns=schema.SHEETS[sheet_key]["headers"]))
    return n


def reset_database(keys: list[str]) -> dict[str, int]:
    """Clear every listed sheet. Returns {sheet: deleted_rows}."""
    out = {}
    for k in keys:
        if k in schema.SHEETS:
            try:
                out[k] = clear_sheet(k)
            except Exception as e:                       # pragma: no cover
                out[k] = -1
                st.warning(f"{k}: {e}")
    return out


# ───────────────────────── settings ─────────────────────────
def settings_dict() -> dict:
    df = get_df("SETTINGS")
    d = {r["KEY"]: r["VALUE"] for _, r in df.iterrows() if str(r["KEY"]).strip()}
    for k, v, _desc in schema.DEFAULT_SETTINGS:
        d.setdefault(k, v)
    return d


def save_settings(d: dict):
    df = get_df("SETTINGS")
    desc = {r["KEY"]: r["DESCRIPTION"] for _, r in df.iterrows()}
    for k, _v, dsc in schema.DEFAULT_SETTINGS:
        desc.setdefault(k, dsc)
    rows = [{"KEY": k, "VALUE": v, "DESCRIPTION": desc.get(k, "")} for k, v in d.items()]
    overwrite("SETTINGS", pd.DataFrame(rows))


def setting_bool(d: dict, key: str, default: bool = True) -> bool:
    return str(d.get(key, "Y" if default else "N")).strip().upper() in ("Y", "YES", "TRUE", "1")


def setting_float(d: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(str(d.get(key, default)).strip() or default)
    except Exception:
        return default
