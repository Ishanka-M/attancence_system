"""
gsheets.py
==========
Google Sheets backend.

වැඩ:
  1. Service account credentials වලින් gspread client එකක් (st.secrets).
  2. Spreadsheet එක open / create.
  3. ensure_all() -> schema.SHEETS එකේ **නැති හැම tab එකක්ම AUTO-CREATE**,
     headers දාලා, masters (USER-M / SETTINGS) seed කරලා.
  4. read / append / overwrite / upsert helpers.

"ඕනේ කරන හැම Sheet එකක්ම Auto හදාගන්න ඕනේ" කියන requirement එක
implement වෙන්නේ ensure_all() එකෙන්.
"""
from __future__ import annotations

import time

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

import schema

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


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
    """secrets එකේ spreadsheet_id / spreadsheet_name අනුව open. නැත්නම් create."""
    client = get_client()
    cfg = st.secrets.get("app", {})
    sid = str(cfg.get("spreadsheet_id", "")).strip()
    name = str(cfg.get("spreadsheet_name", "EFL ASN GRN System")).strip()
    sa_email = dict(st.secrets["gcp_service_account"]).get("client_email", "?")

    # මුළු URL එකක් දාලා නම් ID එක extract කරනවා
    if "docs.google.com" in sid and "/d/" in sid:
        sid = sid.split("/d/")[1].split("/")[0]

    if sid:
        try:
            return client.open_by_key(sid)
        except gspread.exceptions.APIError as e:
            raise RuntimeError(
                f"Sheet එක open කරන්න බෑ (id='{sid}').\n\n"
                f"1) spreadsheet_id එක හරිද බලන්න — URL එකේ /d/ සහ /edit අතර කොටස විතරයි.\n"
                f"2) Sheet එක මේ service account එකට Editor විදිහට share කරන්න:\n"
                f"   👉 {sa_email}\n\n"
                f"නැත්නම් spreadsheet_id හිස් තියලා app එකට අලුත් එකක් හදන්න දෙන්න."
            ) from e

    try:
        return client.open(name)
    except gspread.SpreadsheetNotFound:
        sh = client.create(name)
        share = str(cfg.get("share_email", "")).strip()
        if share:
            sh.share(share, perm_type="user", role="writer")
        return sh


def spreadsheet_url() -> str:
    try:
        return get_spreadsheet().url
    except Exception:
        return ""


def _update(ws, values, rng="A1"):
    """gspread 5/6 දෙකේම වැඩ කරන update wrapper."""
    try:
        ws.update(values=values, range_name=rng, value_input_option="USER_ENTERED")
    except TypeError:                                     # pragma: no cover
        ws.update(rng, values, value_input_option="USER_ENTERED")


# ───────────────────────── AUTO-CREATE sheets ─────────────────────────
def ensure_all(seed_masters: bool = True) -> tuple[list[str], list[str]]:
    """
    schema.SHEETS එකේ හැම sheet එකක්ම තියෙනවද බලලා නැති ඒවා auto-create කරනවා.
    දැනටමත් තියෙන sheet එකක අලුත් column එකක් schema එකට එකතු වෙලා නම්,
    ඒ header එකත් auto-add කරනවා (data නැති නොවී).

    return: (created_titles, patched_titles)
    """
    sh = get_spreadsheet()
    existing = {ws.title: ws for ws in sh.worksheets()}
    created, patched = [], []

    for key, cfg in schema.SHEETS.items():
        title, headers = cfg["title"], cfg["headers"]

        if title in existing:
            ws = existing[title]
            first = [str(c).strip() for c in ws.row_values(1)]
            if not any(first):
                _update(ws, [headers])
                patched.append(title)
                continue
            missing = [h for h in headers if h not in first]
            if missing:
                new_header = first + missing
                if ws.col_count < len(new_header):
                    ws.add_cols(len(new_header) - ws.col_count)
                _update(ws, [new_header])
                patched.append(title)
            continue

        # ── අලුත් tab එක auto-create ──
        ws = sh.add_worksheet(title=title, rows=2000, cols=max(len(headers), 12))
        rows = [headers]
        if seed_masters and cfg.get("seed"):
            rows += [list(r) for r in cfg["seed"]]
        _update(ws, rows)
        created.append(title)
        existing[title] = ws
        time.sleep(0.2)          # API quota එකට ගරු කරනවා

    # create වෙද්දි ආපු default හිස් "Sheet1" එක අයින්
    try:
        titles = {w.title for w in sh.worksheets()}
        if len(titles) > 1 and "Sheet1" in titles:
            sh.del_worksheet(sh.worksheet("Sheet1"))
    except Exception:
        pass

    get_df.clear()
    return created, patched


def sheet_status() -> pd.DataFrame:
    sh = get_spreadsheet()
    existing = {ws.title: ws for ws in sh.worksheets()}
    out = []
    for key, cfg in schema.SHEETS.items():
        t = cfg["title"]
        ws = existing.get(t)
        rows = 0
        if ws:
            try:
                rows = max(len(ws.get_all_values()) - 1, 0)
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
    """Worksheet එකක් DataFrame විදිහට කියවනවා (90s cache)."""
    sh = get_spreadsheet()
    cfg = schema.SHEETS[sheet_key]
    try:
        ws = sh.worksheet(cfg["title"])
    except gspread.WorksheetNotFound:
        return pd.DataFrame(columns=cfg["headers"])
    values = ws.get_all_values()
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
    sh = get_spreadsheet()
    ws = sh.worksheet(schema.SHEETS[sheet_key]["title"])
    ws.append_rows(
        [["" if v is None else str(v) for v in r] for r in rows],
        value_input_option="USER_ENTERED",
    )
    get_df.clear()


def overwrite(sheet_key: str, df: pd.DataFrame):
    """Sheet එක clear කරලා DataFrame එකම නැවත ලියනවා."""
    sh = get_spreadsheet()
    cfg = schema.SHEETS[sheet_key]
    ws = sh.worksheet(cfg["title"])
    df = df.reindex(columns=cfg["headers"]).fillna("")
    body = [cfg["headers"]] + df.astype(str).values.tolist()
    need_rows, need_cols = len(body) + 50, len(cfg["headers"])
    if ws.row_count < need_rows:
        ws.add_rows(need_rows - ws.row_count)
    if ws.col_count < need_cols:
        ws.add_cols(need_cols - ws.col_count)
    ws.clear()
    _update(ws, body)
    get_df.clear()


def upsert(sheet_key: str, rows: list[dict]) -> tuple[int, int]:
    """
    schema එකේ key column එක අනුව upsert.
    key තියෙනවා නම් UPDATE, නැත්නම් ADD.  return (added, updated)
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
            # හිස් අගයන් වලින් තියෙන data overwrite නොවෙන්න
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
    """key column එකේ දී ඇති values තියෙන rows අයින් කරලා new_df එක දානවා."""
    cfg = schema.SHEETS[sheet_key]
    key = cfg["key"]
    cur = get_df(sheet_key)
    if not cur.empty and key:
        keep = ~cur[key].astype(str).str.strip().isin([str(k).strip() for k in key_values])
        cur = cur[keep]
    out = pd.concat([cur, new_df.reindex(columns=cfg["headers"])], ignore_index=True)
    overwrite(sheet_key, out)


# ───────────────────────── delete / reset ─────────────────────────
def delete_where(sheet_key: str, column: str, values) -> int:
    """
    column එකේ අගය `values` ඇතුළේ තියෙන හැම row එකක්ම අයින් කරනවා.
    return: අයින් වුණ row ගණන.
    """
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
    """Sheet එකේ data ඔක්කොම අයින් — headers විතරක් තියෙනවා."""
    df = get_df(sheet_key)
    n = len(df)
    overwrite(sheet_key, pd.DataFrame(columns=schema.SHEETS[sheet_key]["headers"]))
    return n


def reset_database(keys: list[str]) -> dict[str, int]:
    """දුන්න sheets ඔක්කොම clear කරනවා. return: {sheet: deleted_rows}"""
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
