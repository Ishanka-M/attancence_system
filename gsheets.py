"""
gsheets.py
==========
Google Sheets backend.

ප්‍රධාන වැඩ 3යි:
  1. Service account credentials වලින් gspread client එකක් හදනවා (st.secrets).
  2. Spreadsheet එක open/create කරනවා.
  3. ensure_all()  ->  schema.SHEETS එකේ නැති හැම tab එකක්ම AUTO-CREATE කරලා,
     headers දාලා, master sheets වලට seeds.json එකෙන් data seed කරනවා.

මේකයි "Google sheet එකේ sheet auto create වෙන්න" කියන requirement එක
implement කරන තැන.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

import schema

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_HERE = os.path.dirname(os.path.abspath(__file__))


# ───────────────────────── seeds ─────────────────────────
@lru_cache(maxsize=1)
def _load_seeds() -> dict:
    path = os.path.join(_HERE, "seeds.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _seed_rows(sheet_key: str) -> list[list]:
    """seeds.json එකේ raw data -> sheet headers වලට align කරපු rows."""
    cfg = schema.SHEETS[sheet_key]
    seed_key = cfg.get("seed")
    if not seed_key:
        return []
    data = _load_seeds().get(seed_key, [])
    if not data:
        return []

    if seed_key == "USER":
        return [[
            d.get("USER_ID", ""), d.get("COMPANY", ""), d.get("DEPARTMENT", ""),
            d.get("SUB_DEPARTMENT", ""), d.get("USER_NAME", ""),
            d.get("SUPERVISOR_ID", ""), d.get("SUPERVISOR", ""), "Y", "",
        ] for d in data]

    if seed_key == "TCODE":
        return [[
            d.get("System", ""), d.get("T_CODE", ""), d.get("Description", ""),
            d.get("UOM", ""), d.get("Volume", ""), d.get("CSS_SMV", ""),
            d.get("SMV_M", ""), d.get("NORMAL_rate", ""), d.get("OTN_rate", ""),
            d.get("OTD_rate", ""),
        ] for d in data]

    if seed_key in ("TIME", "LOCATION"):
        return [[x] for x in data]

    # SITE / CUSTOMMER => already list-of-lists
    return [list(r) for r in data]


# ───────────────────── client / spreadsheet ──────────────────────
@st.cache_resource(show_spinner=False)
def get_client() -> gspread.Client:
    """st.secrets["gcp_service_account"] වලින් authorize කරනවා."""
    info = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


@st.cache_resource(show_spinner=False)
def get_spreadsheet():
    """
    secrets එකේ spreadsheet_id හෝ spreadsheet_name අනුව open කරනවා.
    නැත්නම් අලුතින් create කරලා, share_email එකට share කරනවා.
    """
    client = get_client()
    cfg = st.secrets.get("app", {})
    sid = cfg.get("spreadsheet_id", "").strip()
    name = cfg.get("spreadsheet_name", "EFL KPI System").strip()
    sa_email = dict(st.secrets["gcp_service_account"]).get("client_email", "?")

    # URL එකක් අතුළත් කරලා නම් ID එක extract කරනවා
    if "docs.google.com" in sid and "/d/" in sid:
        sid = sid.split("/d/")[1].split("/")[0]

    if sid:
        try:
            return client.open_by_key(sid)
        except gspread.exceptions.APIError as e:
            raise RuntimeError(
                f"Sheet එක open කරන්න බෑ (id='{sid}').\n\n"
                f"1) spreadsheet_id එක හරිද බලන්න — URL එකේ /d/ සහ /edit අතර කොටස විතරයි.\n"
                f"2) Sheet එක මේ service account එකට share කරන්න ඕනේ (Editor):\n"
                f"   👉 {sa_email}\n"
                f"   (Google Sheet → Share → මේ email එක දාලා Editor → Send)\n\n"
                f"නැත්නම් spreadsheet_id හිස් තියලා app එකට අලුත් එකක් හදන්න දෙන්න."
            ) from e

    try:
        return client.open(name)
    except gspread.SpreadsheetNotFound:
        sh = client.create(name)
        share = cfg.get("share_email", "").strip()
        if share:
            sh.share(share, perm_type="user", role="writer")
        return sh


# ───────────────────── AUTO-CREATE sheets ──────────────────────
def ensure_all(seed_masters: bool = True) -> list[str]:
    """
    schema.SHEETS එකේ හැම sheet එකක්ම Google Sheet එකේ තියෙනවද බලනවා.
    නැති ඒවා auto-create කරනවා + headers දානවා + masters seed කරනවා.
    Default 'Sheet1' එක (create වෙද්දි එන empty එක) අයින් කරනවා.
    return: අලුතෙන් create වුණ sheet නම් list එක.
    """
    sh = get_spreadsheet()
    existing = {ws.title: ws for ws in sh.worksheets()}
    created = []

    for key, cfg in schema.SHEETS.items():
        title = cfg["title"]
        headers = cfg["headers"]
        if title in existing:
            ws = existing[title]
            # header row එක හිස්නම් දාගන්නවා
            first = ws.row_values(1)
            if not any(first):
                ws.update("A1", [headers])
            continue

        # ── අලුත් tab එක auto-create ──
        ws = sh.add_worksheet(title=title, rows=2000, cols=max(len(headers), 12))
        rows = [headers]
        if seed_masters and cfg["kind"] == "master":
            rows += _seed_rows(key)
        ws.update("A1", rows)
        created.append(title)
        existing[title] = ws

    # create වෙද්දි ආපු default empty "Sheet1" එක අයින් කරනවා
    try:
        if len(sh.worksheets()) > 1 and "Sheet1" in {w.title for w in sh.worksheets()}:
            sh.del_worksheet(sh.worksheet("Sheet1"))
    except Exception:
        pass

    get_df.clear()  # cache invalidate
    return created


def sheet_status() -> pd.DataFrame:
    """හැම schema sheet එකකම තත්ත්වය (exists? rows?) පෙන්නන්න."""
    sh = get_spreadsheet()
    existing = {ws.title: ws for ws in sh.worksheets()}
    out = []
    for key, cfg in schema.SHEETS.items():
        t = cfg["title"]
        ws = existing.get(t)
        out.append({
            "Sheet": t,
            "Type": cfg["kind"],
            "Exists": "✅" if ws else "❌",
            "Data rows": (max(ws.row_count, 0) and len(ws.get_all_values()) - 1) if ws else 0,
        })
    return pd.DataFrame(out)


# ───────────────────── read / write helpers ──────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def get_df(sheet_key: str) -> pd.DataFrame:
    """Worksheet එකක් DataFrame විදිහට කියවනවා (60s cache)."""
    sh = get_spreadsheet()
    title = schema.SHEETS[sheet_key]["title"]
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return pd.DataFrame(columns=schema.SHEETS[sheet_key]["headers"])
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame(columns=schema.SHEETS[sheet_key]["headers"])
    header, *data = values
    df = pd.DataFrame(data, columns=header)
    return df.loc[:, [c for c in df.columns if c != ""]]


def append_rows(sheet_key: str, rows: list[list]):
    sh = get_spreadsheet()
    ws = sh.worksheet(schema.SHEETS[sheet_key]["title"])
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    get_df.clear()


def overwrite(sheet_key: str, df: pd.DataFrame):
    """Sheet එක clear කරලා DataFrame එකම නැවත ලියනවා (masters edit කරද්දි)."""
    sh = get_spreadsheet()
    ws = sh.worksheet(schema.SHEETS[sheet_key]["title"])
    ws.clear()
    body = [list(df.columns)] + df.fillna("").astype(str).values.tolist()
    ws.update("A1", body, value_input_option="USER_ENTERED")
    get_df.clear()
