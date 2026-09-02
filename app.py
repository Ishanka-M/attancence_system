"""
app.py
======
ASN ↔ Korber GRN Control System  (Streamlit + Google Sheets)

Flow:
    📤 ASN Upload  →  📦 Inventory  →  🔄 Reconciliation
        → tally වෙන ඒවා  KORBER GRN DONE
        → ✅ AX GRN Pending → AX GRN DONE → FULLY COMPLETE
        → ⚠️ Discrepancy report (Summary + Details)  →  ✉️ Markdown email
"""
from __future__ import annotations

import uuid
from datetime import datetime, date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import drive
import gsheets
import images
import matching
import parsing
import reporting
import schema
from matching import nkey, now_str, run_id
from parsing import clean, to_num, fmt_num

st.set_page_config(page_title="ASN ↔ GRN Control System",
                   page_icon="📦", layout="wide")

# ───────────────────────────── design system ─────────────────────────────
INK = "#101720"
MUTED = "#67717e"
LINE = "#e4e8ed"
ACCENT = "#0d6e63"
OK = "#17794a"
WARN = "#a5670c"
DANGER = "#b3261e"
INFO = "#2d5f9a"

st.markdown(f"""
<style>
  .block-container {{padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1440px;}}
  html, body, [class*="css"] {{
    font-feature-settings: "tnum" 1, "cv05" 1;
  }}
  h1,h2,h3,h4 {{color:{INK}; letter-spacing:-.015em; font-weight:650;}}

  /* page header */
  .ph {{margin:0 0 1.4rem 0; padding-bottom:.85rem; border-bottom:1px solid {LINE};}}
  .ph .t {{font-size:1.42rem; font-weight:650; color:{INK}; line-height:1.2;}}
  .ph .s {{font-size:.86rem; color:{MUTED}; margin-top:.28rem;}}

  /* metric card */
  .kc {{background:#fff; border:1px solid {LINE}; border-radius:10px;
        padding:.95rem 1.05rem; height:100%;}}
  .kc .v {{font-size:1.72rem; font-weight:650; line-height:1.1; color:{INK};
           font-variant-numeric:tabular-nums;}}
  .kc .l {{font-size:.79rem; color:{MUTED}; margin-top:.3rem;}}
  .kc .n {{font-size:.72rem; color:{MUTED}; margin-top:.45rem; opacity:.85;}}

  /* status pill — tinted, not saturated */
  .pill {{display:inline-block; padding:.16rem .58rem; border-radius:6px;
          font-size:.755rem; font-weight:600; border:1px solid;}}

  /* pipeline strip */
  .pipe {{display:flex; gap:6px; margin:.2rem 0 1rem 0;}}
  .pipe .seg {{flex:1; background:#fff; border:1px solid {LINE};
               border-radius:8px; padding:.7rem .85rem;}}
  .pipe .seg .n {{font-size:1.35rem; font-weight:650; color:{INK};
                  font-variant-numeric:tabular-nums;}}
  .pipe .seg .c {{font-size:.75rem; color:{MUTED}; margin-top:.15rem;}}
  .pipe .seg .bar {{height:3px; border-radius:2px; margin-top:.6rem;}}

  /* tables */
  .stDataFrame {{font-size:.83rem;}}
  div[data-testid="stDataFrame"] {{border:1px solid {LINE}; border-radius:9px;}}

  /* sidebar */
  section[data-testid="stSidebar"] {{background:#0f1720; border-right:1px solid #1c2531;}}
  section[data-testid="stSidebar"] * {{color:#d5dbe2;}}
  section[data-testid="stSidebar"] h3 {{color:#fff; font-size:1rem;}}

  /* buttons */
  .stButton>button {{border-radius:8px; font-weight:550;}}
  .stDownloadButton>button {{border-radius:8px;}}

  /* tabs */
  button[data-baseweb="tab"] {{font-size:.87rem;}}

  .hint {{font-size:.79rem; color:{MUTED};}}
  hr {{border-color:{LINE};}}
</style>
""", unsafe_allow_html=True)


def hero(title: str, sub: str = ""):
    st.markdown(f'<div class="ph"><div class="t">{title}</div>'
                f'<div class="s">{sub}</div></div>', unsafe_allow_html=True)


def kpi(col, value, label, color=INK, note=""):
    col.markdown(
        f'<div class="kc"><div class="v" style="color:{color}">{value}</div>'
        f'<div class="l">{label}</div>'
        + (f'<div class="n">{note}</div>' if note else "")
        + '</div>', unsafe_allow_html=True)


def _tint(hexcol: str, alpha: str = "14") -> str:
    return hexcol + alpha


def pill(text: str) -> str:
    c = schema.STATUS_COLORS.get(text, MUTED)
    return (f'<span class="pill" style="color:{c};border-color:{c}55;'
            f'background:{_tint(c)}">{text}</span>')


def pipeline(stages: list[tuple[str, int, str]]):
    """[(label, value, color)] -> horizontal stage strip."""
    segs = "".join(
        f'<div class="seg"><div class="n">{v}</div><div class="c">{lab}</div>'
        f'<div class="bar" style="background:{c}"></div></div>'
        for lab, v, c in stages)
    st.markdown(f'<div class="pipe">{segs}</div>', unsafe_allow_html=True)


def fig_style(fig, height=300, legend=False):
    fig.update_layout(
        template="simple_white", height=height,
        margin=dict(t=8, b=8, l=8, r=8),
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif",
                  size=12, color=INK),
        showlegend=legend,
        legend=dict(orientation="h", y=1.12, x=0, title_text=""),
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor=LINE, zeroline=False),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# ───────────────────────────── session ─────────────────────────────
SS = st.session_state
SS.setdefault("user", "")
SS.setdefault("role", "user")
SS.setdefault("parsed_asn", {})       # filename -> {df, meta, images}
SS.setdefault("inv_df", None)         # canonical inventory (session)
SS.setdefault("inv_note", "")
SS.setdefault("recon", None)          # last recon result
SS.setdefault("backup", None)


def cfg_recon() -> dict:
    s = gsheets.settings_dict()
    return {
        "client": s.get("CLIENT_CODE", ""),
        "strip_prefix": gsheets.setting_bool(s, "STRIP_CLIENT_PREFIX"),
        "qty_tolerance": gsheets.setting_float(s, "QTY_TOLERANCE", 0.0),
        "check_item": gsheets.setting_bool(s, "CHECK_ITEM"),
        "check_lot": gsheets.setting_bool(s, "CHECK_LOT"),
        "check_asn": gsheets.setting_bool(s, "CHECK_ASN_NO"),
        "flag_extra": gsheets.setting_bool(s, "FLAG_EXTRA"),
    }


def show(df: pd.DataFrame, height: int | None = None, n: int = 3000):
    if df is None or df.empty:
        st.info("Records නෑ.")
        return
    st.dataframe(df.head(n), width="stretch", hide_index=True,
                 height=height or min(60 + 34 * min(len(df), 15), 560))


# ───────────────────────────── sidebar ─────────────────────────────
st.sidebar.markdown("### 📦 ASN ↔ GRN Control")
st.sidebar.caption("Korber One · AX · EFL Warehouse")

try:
    _new_tabs = gsheets.ensure_missing_once()
    _users = gsheets.get_df("USER-M")
except Exception as e:
    st.sidebar.error("Google Sheets connect කරන්න බැරි උනා.")
    st.error(f"**Connection error**\n\n```\n{e}\n```\n\n"
             "`.streamlit/secrets.toml` එකේ `gcp_service_account` සහ "
             "`app.spreadsheet_id` හරියටම දාලා තියෙනවද බලන්න.")
    st.stop()

names = [n for n in _users["USER NAME"].astype(str) if n.strip()] if not _users.empty else []
who = st.sidebar.selectbox("👤 Operator", ["— තෝරන්න —"] + names + ["+ අලුත් නමක්"], index=0)
if who == "+ අලුත් නමක්":
    who = st.sidebar.text_input("නම", value=SS.get("user", ""))
SS["user"] = "" if who.startswith("—") else who

with st.sidebar.expander("🔑 Admin"):
    pin = st.text_input("Admin PIN", type="password", key="pin_in")
    if st.button("Login", key="pin_btn"):
        s = gsheets.settings_dict()
        SS["role"] = "admin" if pin == str(s.get("ADMIN_PIN", "1234")) else "user"
        st.success("Admin ✅") if SS["role"] == "admin" else st.error("PIN වැරදියි")
if SS["role"] == "admin":
    st.sidebar.success("🛡️ Admin mode")

if _new_tabs:
    st.sidebar.info("🏗️ අලුතෙන් හැදුවා: " + ", ".join(_new_tabs))

PAGES = ["📊 Dashboard", "📤 ASN Upload", "📦 Inventory", "🔄 Reconciliation",
         "🧾 ASN Register", "🔍 Search", "⚠️ Discrepancy", "✉️ Email", "✅ AX GRN",
         "🖼️ Attachments", "⚙️ Setup", "🗂️ Data Manager", "🧹 Maintenance"]
page = st.sidebar.radio("Menu", PAGES, label_visibility="collapsed")

st.sidebar.markdown("---")
_st = gsheets.api_stats()
_bar = "#b3261e" if _st["last_minute"] > _st["limit"] * .8 else "#4bb3a2"
st.sidebar.markdown(
    f"<div style='font-size:.75rem;opacity:.75'>API "
    f"<span style='color:{_bar}'>{_st['last_minute']}/{_st['limit']}</span> per min"
    + (f" · {_st['retries']} retries" if _st["retries"] else "")
    + (f" · {_st['errors']} errors" if _st["errors"] else "")
    + "</div>", unsafe_allow_html=True)

if st.sidebar.button("Refresh data"):
    gsheets.refresh()
    st.rerun()
_url = gsheets.spreadsheet_url()
if _url:
    st.sidebar.markdown(
        f"<div style='font-size:.75rem;margin-top:.4rem'>"
        f"<a href='{_url}' target='_blank'>Google Sheet විවෘත කරන්න</a></div>",
        unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
#  ⚙️ SETUP
# ═══════════════════════════════════════════════════════════════════
if page == "⚙️ Setup":
    hero("Setup", "Sheets · matching rules · attachments · API")

    t_sheets, t_rules, t_files, t_api = st.tabs(
        ["Sheets", "Matching rules", "Attachments", "API & quota"])

    # ───────────── sheets ─────────────
    with t_sheets:
        c1, c2 = st.columns([1, 2])
        with c1:
            if st.button("හැම sheet එකක්ම හදන්න / update", type="primary"):
                with st.spinner("Sheets හදනවා..."):
                    created, patched = gsheets.ensure_all()
                if created:
                    st.success("අලුතෙන් හැදුවා: " + ", ".join(created))
                if patched:
                    st.info("Header update කළා: " + ", ".join(patched))
                if not created and not patched:
                    st.success("ඔක්කොම sheets දැනටමත් හරි")
        with c2:
            st.caption("`ASN_SUMMARY`, `ASN_DETAIL`, `INVENTORY`, `DISCREPANCY`, "
                       "`AX_GRN`, `ASN_IMAGES`, `IMAGE_DATA`, `RECON_LOG`, "
                       "`EMAIL_LOG`, `USER-M`, `SETTINGS` — ඔක්කොම auto-create වෙනවා. "
                       "ලියද්දී නැති sheet එකකුත් තනියම හැදෙනවා.")
        show(gsheets.sheet_status())

    s = gsheets.settings_dict()

    # ───────────── matching rules ─────────────
    with t_rules:
        with st.form("settings_rules"):
            a, b, c = st.columns(3)
            s["CLIENT_CODE"] = a.text_input("Client code", s.get("CLIENT_CODE", "HIES"))
            s["SITE"] = b.text_input("Site / warehouse", s.get("SITE", "EGDC"))
            s["COMPANY"] = c.text_input("Company", s.get("COMPANY", "EFL"))

            a, b, c, d = st.columns(4)
            s["QTY_TOLERANCE"] = str(a.number_input(
                "Qty tolerance", value=gsheets.setting_float(s, "QTY_TOLERANCE"),
                step=1.0))
            s["STRIP_CLIENT_PREFIX"] = "Y" if b.checkbox(
                "Client prefix ඉවත්", gsheets.setting_bool(s, "STRIP_CLIENT_PREFIX")) else "N"
            s["CHECK_ITEM"] = "Y" if c.checkbox(
                "Item check", gsheets.setting_bool(s, "CHECK_ITEM")) else "N"
            s["CHECK_LOT"] = "Y" if d.checkbox(
                "Lot check", gsheets.setting_bool(s, "CHECK_LOT")) else "N"

            a, b = st.columns(2)
            s["CHECK_ASN_NO"] = "Y" if a.checkbox(
                "ASN number check", gsheets.setting_bool(s, "CHECK_ASN_NO")) else "N"
            s["FLAG_EXTRA"] = "Y" if b.checkbox(
                "Extra HU flag", gsheets.setting_bool(s, "FLAG_EXTRA")) else "N"

            a, b = st.columns(2)
            s["EMAIL_TO"] = a.text_input("Email To", s.get("EMAIL_TO", ""))
            s["EMAIL_CC"] = b.text_input("Email Cc", s.get("EMAIL_CC", ""))
            s["ADMIN_PIN"] = st.text_input("Admin PIN", s.get("ADMIN_PIN", "1234"))

            if st.form_submit_button("Save", type="primary"):
                gsheets.save_settings(s)
                st.success("Save කළා")

    # ───────────── attachments ─────────────
    with t_files:
        st.markdown("###### Images සහ PDF කොහෙද save වෙනවද")
        a, b, c = st.columns(3)
        s["IMAGE_STORAGE"] = a.selectbox(
            "Storage", ["DRIVE", "SHEET"],
            index=0 if str(s.get("IMAGE_STORAGE", "DRIVE")).upper() == "DRIVE" else 1,
            help="DRIVE = Drive folder එකට (fail වුණොත් automatic ව Sheet එකට). "
                 "SHEET = Google Sheet එකේම, quota ප්‍රශ්න නෑ.")
        s["IMAGE_MAX_PX"] = str(int(b.number_input(
            "Max px", value=gsheets.setting_float(s, "IMAGE_MAX_PX", 1400),
            min_value=400.0, max_value=4000.0, step=100.0)))
        s["IMAGE_QUALITY"] = str(int(c.number_input(
            "JPEG quality", value=gsheets.setting_float(s, "IMAGE_QUALITY", 78),
            min_value=40.0, max_value=95.0, step=1.0)))

        s["DRIVE_FOLDER_ID"] = st.text_input(
            "Drive folder — link එකක් හෝ ID එකක්", s.get("DRIVE_FOLDER_ID", ""))
        fid = drive.folder_id(s["DRIVE_FOLDER_ID"])
        if fid:
            st.caption(f"Folder ID: `{fid}`")

        c1, c2 = st.columns([1, 3])
        if c1.button("Save", type="primary", key="save_files"):
            gsheets.save_settings(s)
            st.success("Save කළා")
        if c2.button("Drive connection test"):
            ok, msg = drive.check_folder(s["DRIVE_FOLDER_ID"])
            st.success(msg) if ok else st.error(msg)

        st.markdown("---")
        st.markdown("###### Service account")
        sa = drive.service_email()
        st.code(sa or "(secrets එකේ නෑ)", language="text")
        st.caption("Drive folder එකට **Editor** විදිහට මේ email එක share කරන්න ඕනේ. "
                   "Service account එකකට තමන්ගේ storage quota නෑ — folder එක "
                   "**Shared Drive** එකක තියෙනවා නම් හොඳම. Upload fail වුණොත් "
                   "attachment එක automatic ව Google Sheet එකට යනවා, නැති වෙන්නේ නෑ.")
        st.caption(f"Drive API: {'ready' if drive.available() else 'google-api-python-client නෑ'}"
                   f" · PDF: {'ready' if parsing.pdf_available() else 'pdfplumber නෑ'}")

    # ───────────── API ─────────────
    with t_api:
        st.markdown("###### Google API usage")
        st.caption("Google Sheets API එකේ සීමාව විනාඩියකට request 60ක්. මේ system එක "
                   "ඒක track කරලා, ළං වුණාම තනියම රැඳිලා, quota error එකක් ආවොත් "
                   "exponential backoff එකෙන් retry කරනවා.")

        stt = gsheets.api_stats()
        a, b, c, d = st.columns(4)
        used = stt["last_minute"]
        kpi(a, f'{used}/{stt["limit"]}', "Calls last minute",
            DANGER if used > stt["limit"] * .8 else ACCENT,
            f'headroom {stt["headroom"]}')
        kpi(b, stt["calls"], "Total calls", note=f'last {stt["last_call"] or "—"}')
        kpi(c, stt["retries"], "Retries", WARN if stt["retries"] else INK,
            f'throttled {stt["throttled"]}')
        kpi(d, stt["errors"], "Errors", DANGER if stt["errors"] else OK)

        if stt["last_error"]:
            st.error(f"Last error — {stt['last_error']}")

        st.markdown("###### Tuning")
        with st.form("api_form"):
            a, b = st.columns(2)
            rate = a.number_input(
                "Rate limit — calls / minute",
                value=gsheets.setting_float(s, "API_RATE_LIMIT", 55),
                min_value=10.0, max_value=60.0, step=5.0,
                help="Google limit 60. පහළින් තියාගන්න එක ආරක්ෂිතයි.")
            ttl = b.number_input(
                "Cache TTL — තත්පර",
                value=gsheets.setting_float(s, "CACHE_TTL", 90),
                min_value=10.0, max_value=600.0, step=10.0,
                help="ලොකු නම් API calls අඩුයි, ඒත් data ටිකක් පරණයි.")
            if st.form_submit_button("Save", type="primary"):
                s["API_RATE_LIMIT"] = str(int(rate))
                s["CACHE_TTL"] = str(int(ttl))
                gsheets.save_settings(s)
                gsheets.apply_api_settings(s)
                st.success("Save කළා")

        c1, c2 = st.columns(2)
        if c1.button("Cache clear"):
            gsheets.refresh()
            st.success("Cache clear කළා")
        if c2.button("Counters reset"):
            gsheets.api_reset_stats()
            st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  📤 ASN UPLOAD
# ═══════════════════════════════════════════════════════════════════
elif page == "📤 ASN Upload":
    hero("ASN Document Upload",
         "Excel හෝ PDF → source එක තෝරලා confirm → Summary + Details save")

    if not SS["user"]:
        st.warning("Sidebar එකෙන් Operator නම තෝරන්න.")

    if not parsing.pdf_available():
        st.caption("ℹ️ PDF support එකට `pdfplumber` install කරන්න ඕනේ "
                   "(`requirements.txt` එකේ තියෙනවා).")

    files = st.file_uploader("ASN file(s) — Excel හෝ PDF",
                             type=["xlsx", "xlsm", "xls", "pdf"],
                             accept_multiple_files=True, key="asn_up")

    if files:
        st.markdown("##### 1 · Source එක තෝරන්න")
        st.caption("Excel නම් sheet එක, PDF නම් table එක තෝරලා parse කරන්න.")

        choices = {}
        for f in files:
            b = f.getvalue()
            if parsing.is_pdf(f.name, b):
                tables = parsing.list_pdf_tables(b)
                c1, c2 = st.columns([2, 3])
                c1.markdown(f"**📕 {f.name}**")
                c1.caption(f"PDF · pages {parsing.pdf_page_count(b)}")
                if not tables:
                    c2.error("Table එකක් හම්බුණේ නෑ — scan කරපු PDF එකක් වෙන්න ඇති. "
                             "Excel එකක් දාන්න, නැත්නම් 🖼️ ASN Images page එකෙන් "
                             "මේ PDF එක document එකක් විදිහට attach කරන්න.")
                    continue
                labels = [t["label"] for t in tables]
                sel = c2.selectbox(f"Table — {f.name}", labels, key=f"pt_{f.name}",
                                   label_visibility="collapsed")
                keys = [t["key"] for t in tables if t["label"] == sel]
                choices[f.name] = ("pdf", b, keys)
            else:
                sheets = parsing.list_sheets(b)
                if not sheets:
                    st.error(f"`{f.name}` — Excel sheet කියවන්න බැරි උනා.")
                    continue
                c1, c2 = st.columns([2, 3])
                c1.markdown(f"**📗 {f.name}**")
                c1.caption(f"Excel · sheets {len(sheets)}")
                sel = c2.selectbox(f"Sheet — {f.name}", sheets, key=f"sh_{f.name}",
                                   label_visibility="collapsed")
                choices[f.name] = ("xlsx", b, sel)

        if st.button("Parse & preview", type="primary", disabled=not choices):
            SS["parsed_asn"] = {}
            for fname, (kind, b, sel) in choices.items():
                if kind == "pdf":
                    df, meta = parsing.parse_asn_pdf(b, sel)
                    imgs = parsing.extract_pdf_images(b)
                    src = meta.get("sheet", "PDF")
                else:
                    df, meta = parsing.parse_asn(b, sel)
                    imgs = parsing.extract_images(b)
                    src = sel
                SS["parsed_asn"][fname] = {"df": df, "meta": meta, "images": imgs,
                                           "sheet": src, "kind": kind,
                                           "raw": b if kind == "pdf" else None}
            st.rerun()

    # ── preview + confirm ──
    if SS["parsed_asn"]:
        st.markdown("---")
        st.markdown("##### 2 · Preview & confirm")

        total_rows = 0
        all_ok = True
        for fname, p in SS["parsed_asn"].items():
            df, meta = p["df"], p["meta"]
            icon = "📕" if p.get("kind") == "pdf" else "📗"
            with st.expander(f"{icon} {fname}  ·  {p['sheet']}  ·  "
                             f"{len(df)} lines  ·  {len(p['images'])} image(s)",
                             expanded=True):
                if meta.get("error"):
                    st.error(meta["error"])
                    all_ok = False
                    continue
                total_rows += len(df)
                asns = sorted({clean(a) for a in df["ASN_NO"] if clean(a)})
                a, b, c, d = st.columns(4)
                kpi(a, len(df), "ASN lines")
                kpi(b, df["HU_ID"].astype(str).str.strip().nunique(), "HU / Pallet")
                kpi(c, fmt_num(df["QTY"].map(to_num).sum()), "Total qty")
                kpi(d, len(asns), "ASN number(s)")
                st.caption("ASN: " + ", ".join(f"`{x}`" for x in asns[:8]))
                st.caption(f"Header at {meta['header_row']} · "
                           f"mapped columns {len(meta['mapped'])}")
                if meta["unmapped"]:
                    st.caption("Map නොවුණ columns (skip වෙනවා): " +
                               ", ".join(meta["unmapped"][:12]))
                show(df.head(50))
                if p["images"]:
                    st.caption(f"File එක ඇතුළේ images {len(p['images'])}ක්:")
                    cols = st.columns(min(5, len(p["images"])))
                    for i, im in enumerate(p["images"][:5]):
                        cols[i].image(im["data"],
                                      caption=f"{im['name']} ({im['size_kb']}KB)",
                                      width="stretch")

        st.markdown("##### 3 · Extra photos (optional)")
        extra_imgs = st.file_uploader("ASN එකට අදාළ photos (GRN sheet, damage, seal...)",
                                      type=["png", "jpg", "jpeg", "webp"],
                                      accept_multiple_files=True, key="extra_img")
        all_asn = sorted({clean(a)
                          for p in SS["parsed_asn"].values()
                          for a in p["df"].get("ASN_NO", []) if clean(a)})
        img_asn = st.selectbox("මේ photos අදාළ ASN එක", all_asn or ["—"], key="img_asn") \
            if extra_imgs else None

        st.markdown("##### 4 · Save")
        c1, c2, c3 = st.columns([2, 1, 1])
        targets = c1.multiselect("Save කරන්න ඕන sheets",
                                 ["ASN_SUMMARY", "ASN_DETAIL"],
                                 default=["ASN_SUMMARY", "ASN_DETAIL"])
        up_img = c2.checkbox("Images save", value=True)
        keep_pdf = c3.checkbox("PDF attach", value=True,
                               help="Upload කරපු PDF එකම ASN එකට document එකක් "
                                    "විදිහට save කරනවා.")

        confirm = st.checkbox(
            f"මම confirm කරනවා — ලයින් {total_rows}ක් "
            f"{', '.join(targets) or '—'} sheet(s) වලට save කරන්න.")

        cA, cB = st.columns([1, 1])
        if cA.button("Save to Google Sheet", type="primary",
                     disabled=not (confirm and targets and all_ok)):
            ts = now_str()
            user = SS["user"] or "unknown"
            det_rows, summ_rows, img_rows = [], [], []

            for fname, p in SS["parsed_asn"].items():
                df, sheet = p["df"], p["sheet"]
                if df.empty:
                    continue
                for _, r in df.iterrows():
                    asn = clean(r["ASN_NO"])
                    hu = clean(r["HU_ID"])
                    uid = f"{nkey(asn)}|{nkey(hu) or 'L' + clean(r['ASN_LINE'])}"
                    det_rows.append({
                        "LINE UID": uid,
                        "ASN NO": asn, "ASN LINE": clean(r["ASN_LINE"]),
                        "CLIENT CODE": clean(r["CLIENT_CODE"]),
                        "ITEM NUMBER": clean(r["ITEM_NUMBER"]), "HU ID": hu,
                        "SUPPLIER HU": clean(r["SUPPLIER_HU"]),
                        "LOT NUMBER": clean(r["LOT_NUMBER"]),
                        "QTY": clean(r["QTY"]), "UOM": clean(r["UOM"]),
                        "S UOM": clean(r["S_UOM"]), "S QTY": clean(r["S_QTY"]),
                        "PO NUMBER": clean(r["PO_NUMBER"]), "PO LINE": clean(r["PO_LINE"]),
                        "PACKAGE TYPE": clean(r["PACKAGE_TYPE"]),
                        "VENDOR CODE": clean(r["VENDOR_CODE"]),
                        "GROSS WEIGHT": clean(r["GROSS_WEIGHT"]),
                        "NET WEIGHT": clean(r["NET_WEIGHT"]),
                        "COLOR": clean(r["COLOR"]), "TYPE QC": clean(r["TYPE_QC"]),
                        "SUPPLIER DESC": clean(r["SUPPLIER_DESC"]),
                        "UPLOAD DATE": ts, "UPLOADED BY": user,
                        "SOURCE FILE": fname, "SOURCE SHEET": sheet,
                        "MATCH STATUS": "", "KORBER GRN": schema.K_PENDING,
                        "AX GRN": schema.AX_NA, "REMARK": "",
                    })

                # images from the workbook / pdf
                if up_img:
                    file_asn = clean(df["ASN_NO"].iloc[0]) if len(df) else ""
                    for im in p["images"]:
                        img_rows.append((file_asn, im["name"],
                                         "PDF EMBEDDED" if p.get("kind") == "pdf"
                                         else "EXCEL EMBEDDED",
                                         im["mime"], im["size_kb"], im["data"]))

                # original PDF එකම document එකක් විදිහට
                if keep_pdf and p.get("kind") == "pdf" and p.get("raw"):
                    file_asn = clean(df["ASN_NO"].iloc[0]) if len(df) else ""
                    img_rows.append((file_asn, fname, "ASN DOCUMENT",
                                     "application/pdf",
                                     round(len(p["raw"]) / 1024, 1), p["raw"]))

            if extra_imgs and up_img and img_asn and img_asn != "—":
                for uf in extra_imgs:
                    d = uf.getvalue()
                    img_rows.append((img_asn, uf.name, "MANUAL UPLOAD",
                                     uf.type or "image/png", round(len(d) / 1024, 1), d))

            det = pd.DataFrame(det_rows).reindex(columns=schema.ASN_DETAIL_HEADERS).fillna("")

            with st.spinner("Google Sheet එකට ලියනවා..."):
                gsheets.ensure_all()
                if "ASN_DETAIL" in targets and not det.empty:
                    a, u = gsheets.upsert("ASN_DETAIL", det.to_dict("records"))
                    st.success(f"ASN_DETAIL — අලුත් {a} · update {u}")

                if "ASN_SUMMARY" in targets and not det.empty:
                    summ = matching.summarise_asn(det)
                    summ["AX GRN"] = schema.AX_NA
                    summ["OVERALL"] = schema.S_GRN_PENDING
                    summ["STATUS"] = schema.S_NEW
                    a, u = gsheets.upsert("ASN_SUMMARY", summ.to_dict("records"))
                    st.success(f"ASN_SUMMARY — අලුත් {a} · update {u}")

                # images
                if img_rows:
                    ok_n, errs = 0, []
                    prog = st.progress(0.0, text="Attachments save වෙනවා...")
                    for i, (asn, nm, src, mime, kb, data) in enumerate(img_rows):
                        ok, msg = images.save_image(asn, nm, data, mime,
                                                    source=src, user=user)
                        if ok:
                            ok_n += 1
                            if msg and "fail" in msg.lower():
                                errs.append(msg)
                        else:
                            errs.append(msg)
                        prog.progress((i + 1) / len(img_rows),
                                      text=f"Attachment {i + 1}/{len(img_rows)}")
                    prog.empty()
                    if ok_n:
                        st.success(f"Attachments {ok_n}ක් save කළා")
                    for e_ in errs[:6]:
                        st.warning(e_)

            SS["parsed_asn"] = {}
            st.balloons()
            st.rerun()

        if cB.button("🗑️ Clear"):
            SS["parsed_asn"] = {}
            st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  📦 INVENTORY
# ═══════════════════════════════════════════════════════════════════
elif page == "📦 Inventory":
    hero("Korber Inventory", "Inventory report එක upload කරලා snapshot එකක් තියාගන්න")

    f = st.file_uploader("Inventory Excel", type=["xlsx", "xlsm", "xls"], key="inv_up")
    if f:
        b = f.getvalue()
        sheets = parsing.list_sheets(b)
        sheet = st.selectbox("Sheet එක තෝරන්න", sheets, key="inv_sheet")
        if st.button("🔍 Parse", type="primary"):
            df, meta = parsing.parse_inventory(b, sheet)
            if meta.get("error"):
                st.error(meta["error"])
            else:
                SS["inv_df"] = df
                SS["inv_note"] = f"{f.name} · {sheet} · {len(df)} rows · {now_str()}"
                st.rerun()

    inv = SS.get("inv_df")
    if inv is not None and not inv.empty:
        st.success(f"Session inventory: {SS['inv_note']}")
        a, b_, c, d = st.columns(4)
        kpi(a, len(inv), "Inventory rows")
        kpi(b_, inv["PALLET"].nunique(), "Unique HU")
        kpi(c, inv["ASN_NUMBER"].nunique(), "ASN numbers")
        kpi(d, fmt_num(inv["ACTUAL_QTY"].map(to_num).sum()), "Total Qty")
        show(inv.head(200))

        c1, c2 = st.columns([1, 3])
        mode = c2.radio("Save mode", ["Replace (recommended)", "Append"],
                        horizontal=True, key="inv_mode")
        if c1.button("💾 INVENTORY sheet එකට save"):
            gsheets.ensure_all()
            ts = now_str()
            out = pd.DataFrame({
                "SNAPSHOT AT": ts, "WH ID": inv["WH_ID"], "CLIENT CODE": inv["CLIENT_CODE"],
                "PALLET": inv["PALLET"], "LOCATION ID": inv["LOCATION_ID"],
                "ITEM NUMBER": inv["ITEM_NUMBER"],
                "DISPLAY ITEM NUMBER": inv["DISPLAY_ITEM_NUMBER"],
                "DESCRIPTION": inv["DESCRIPTION"], "LOT NUMBER": inv["LOT_NUMBER"],
                "ACTUAL QTY": inv["ACTUAL_QTY"], "UNAVAILABLE QTY": inv["UNAVAILABLE_QTY"],
                "UOM": inv["UOM"], "STATUS": inv["STATUS"], "GRN NUMBER": inv["GRN_NUMBER"],
                "ASN NUMBER": inv["ASN_NUMBER"], "ASN LINE NUMBER": inv["ASN_LINE_NUMBER"],
                "SUPPLIER HU": inv["SUPPLIER_HU"], "PO NUMBER": inv["PO_NUMBER"],
                "INVOICE NUMBER": inv["INVOICE_NUMBER"], "VENDOR NAME": inv["VENDOR_NAME"],
                "INVENTORY TYPE": inv["INVENTORY_TYPE"], "SUPPLIER DESC": inv["SUPPLIER_DESC"],
                "S UOM": inv["S_UOM"], "S QTY": inv["S_QTY"],
            })
            with st.spinner("Save වෙනවා..."):
                if mode.startswith("Replace"):
                    gsheets.overwrite("INVENTORY", out)
                else:
                    gsheets.append_rows(
                        "INVENTORY",
                        out.reindex(columns=schema.INVENTORY_HEADERS).values.tolist())
            st.success(f"INVENTORY sheet එකට rows {len(out)}ක් save කළා ✅")

    st.markdown("---")
    st.markdown("#### Sheet එකේ තියෙන latest snapshot")
    show(gsheets.get_df("INVENTORY").head(200))


# ═══════════════════════════════════════════════════════════════════
#  🔄 RECONCILIATION
# ═══════════════════════════════════════════════════════════════════
elif page == "🔄 Reconciliation":
    hero("Reconciliation", "ASN ↔ Korber inventory — GRN complete උනේ මොනාද, විෂමතා මොනාද")

    det_all = gsheets.get_df("ASN_DETAIL")
    if det_all.empty:
        st.warning("ASN_DETAIL එකේ data නෑ. මුලින්ම 📤 ASN Upload කරන්න.")
        st.stop()

    # inventory source
    src = st.radio("Inventory source",
                   ["📤 මේ session එකේ upload කරපු එක", "📗 INVENTORY sheet එක"],
                   horizontal=True)
    if src.startswith("📤"):
        inv = SS.get("inv_df")
        note = SS.get("inv_note", "")
        if inv is None or inv.empty:
            st.warning("Session එකේ inventory නෑ — 📦 Inventory page එකෙන් upload කරන්න.")
            st.stop()
    else:
        raw = gsheets.get_df("INVENTORY")
        if raw.empty:
            st.warning("INVENTORY sheet එක හිස්.")
            st.stop()
        inv = pd.DataFrame({
            "WH_ID": raw["WH ID"], "CLIENT_CODE": raw["CLIENT CODE"], "PALLET": raw["PALLET"],
            "LOCATION_ID": raw["LOCATION ID"], "ITEM_NUMBER": raw["ITEM NUMBER"],
            "DISPLAY_ITEM_NUMBER": raw["DISPLAY ITEM NUMBER"], "DESCRIPTION": raw["DESCRIPTION"],
            "LOT_NUMBER": raw["LOT NUMBER"], "ACTUAL_QTY": raw["ACTUAL QTY"],
            "UNAVAILABLE_QTY": raw["UNAVAILABLE QTY"], "UOM": raw["UOM"],
            "STATUS": raw["STATUS"], "GRN_NUMBER": raw["GRN NUMBER"],
            "ASN_NUMBER": raw["ASN NUMBER"], "ASN_LINE_NUMBER": raw["ASN LINE NUMBER"],
            "SUPPLIER_HU": raw["SUPPLIER HU"], "PO_NUMBER": raw["PO NUMBER"],
            "INVOICE_NUMBER": raw["INVOICE NUMBER"], "VENDOR_NAME": raw["VENDOR NAME"],
            "INVENTORY_TYPE": raw["INVENTORY TYPE"], "SUPPLIER_DESC": raw["SUPPLIER DESC"],
            "S_UOM": raw["S UOM"], "S_QTY": raw["S QTY"],
        })
        note = f"INVENTORY sheet · {len(inv)} rows"
    st.caption(f"📦 {note}")

    summ_all = gsheets.get_df("ASN_SUMMARY")
    done = set(summ_all.loc[summ_all["OVERALL"] == schema.S_COMPLETE, "ASN NO"]) \
        if not summ_all.empty else set()
    asn_opts = sorted({clean(a) for a in det_all["ASN NO"] if clean(a)})
    default = [a for a in asn_opts if a not in done]

    picked = st.multiselect("Check කරන්න ඕන ASN", asn_opts, default=default)
    if st.button("▶️ Reconcile", type="primary", disabled=not picked):
        sub = det_all[det_all["ASN NO"].astype(str).isin(picked)].copy()
        rid = run_id()
        upd, extra, stats = matching.reconcile(sub, inv, cfg_recon(), rid)
        SS["recon"] = {"rid": rid, "detail": upd, "extra": extra, "stats": stats,
                       "note": note, "asns": picked}
        st.rerun()

    R = SS.get("recon")
    if R:
        s = R["stats"]
        st.markdown("---")
        st.markdown(f"### ප්‍රතිඵල · `{R['rid']}`")
        a, b, c, d, e = st.columns(5)
        kpi(a, s["lines"], "Lines checked")
        kpi(b, s["matched"], "✅ Tally (Korber GRN Done)", "#2f8f83")
        kpi(c, s["missing"], "⛔ GRN නෑ", "#c9782a")
        kpi(d, s["mismatch"], "⚠️ Mismatch", "#c4453f")
        kpi(e, s["extra"], "➕ Extra in inventory", "#3a6ea5")

        det = R["detail"]
        tabs = st.tabs(["✅ Tally", "⛔ Missing", "⚠️ Mismatch", "➕ Extra", "📋 සියල්ල"])
        cols = ["ASN NO", "ASN LINE", "HU ID", "ITEM NUMBER", "LOT NUMBER", "QTY",
                "INV QTY", "QTY DIFF", "MATCH STATUS", "INV GRN NO", "INV LOCATION",
                "DISCREPANCY"]
        st_col = det["MATCH STATUS"].astype(str)
        with tabs[0]:
            show(det[st_col == schema.M_MATCHED][cols])
        with tabs[1]:
            show(det[st_col == schema.M_MISSING][cols])
        with tabs[2]:
            show(det[~st_col.isin([schema.M_MATCHED, schema.M_MISSING])][cols])
        with tabs[3]:
            show(R["extra"][cols] if not R["extra"].empty else R["extra"])
        with tabs[4]:
            show(det[cols])

        # ASN level
        st.markdown("#### ASN level")
        summ = matching.summarise_asn(det, R["extra"])
        scols = ["ASN NO", "TOTAL LINES", "TOTAL QTY", "MATCHED LINES", "MISSING LINES",
                 "MISMATCH LINES", "EXTRA LINES", "RECEIVED QTY", "QTY DIFF", "STATUS",
                 "KORBER GRN", "KORBER GRN NO"]
        show(summ[scols])

        st.markdown("---")
        c1, c2 = st.columns([1, 2])
        push = c2.checkbox("Korber GRN Done වුණ ASN, AX GRN Pending එකට යවන්න",
                           value=True)
        if c1.button("💾 ප්‍රතිඵල save කරන්න", type="primary"):
            ts, user = now_str(), SS["user"] or "unknown"
            with st.spinner("Save වෙනවා..."):
                # 1) detail update (+ extra rows)
                rows = det.to_dict("records")
                if not R["extra"].empty:
                    rows += R["extra"].to_dict("records")
                gsheets.upsert("ASN_DETAIL", rows)

                # 2) summary update
                keep = ["ASN NO", "TOTAL LINES", "TOTAL HU", "TOTAL QTY", "ITEM COUNT",
                        "MATCHED LINES", "MISSING LINES", "MISMATCH LINES", "EXTRA LINES",
                        "MATCHED QTY", "RECEIVED QTY", "QTY DIFF", "STATUS",
                        "KORBER GRN", "KORBER GRN NO", "LAST RECON"]
                sm = summ[keep].copy()
                sm["KORBER GRN DATE"] = [ts if k == schema.K_DONE else ""
                                         for k in sm["KORBER GRN"]]
                sm["AX GRN"] = [schema.AX_PENDING if k == schema.K_DONE else schema.AX_NA
                                for k in sm["KORBER GRN"]]
                sm["OVERALL"] = [schema.S_AX_PENDING if k == schema.K_DONE
                                 else schema.S_GRN_PENDING for k in sm["KORBER GRN"]]
                gsheets.upsert("ASN_SUMMARY", sm.to_dict("records"))

                # 3) discrepancies
                disc = matching.discrepancy_rows(det, R["extra"], R["rid"])
                if not disc.empty:
                    gsheets.upsert("DISCREPANCY", disc.to_dict("records"))

                # 4) recon log
                gsheets.append_rows("RECON_LOG", [[
                    R["rid"], ts, user, len(R["asns"]), ", ".join(R["asns"][:20]),
                    s["inventory_rows"], s["lines"], s["matched"], s["missing"],
                    s["mismatch"], s["extra"], R["note"],
                ]])

                # 5) push to AX queue
                if push:
                    ready = summ[summ["KORBER GRN"] == schema.K_DONE]
                    ax = [{
                        "ASN NO": r["ASN NO"], "CLIENT CODE": r["CLIENT CODE"],
                        "KORBER GRN NO": r["KORBER GRN NO"], "KORBER GRN DATE": ts,
                        "TOTAL LINES": r["TOTAL LINES"], "TOTAL QTY": r["TOTAL QTY"],
                        "PUSHED AT": ts, "PUSHED BY": user,
                        "AX GRN": schema.AX_PENDING, "AX GRN NO": "", "AX GRN DATE": "",
                        "AX GRN BY": "", "OVERALL": schema.S_AX_PENDING, "REMARK": "",
                    } for _, r in ready.iterrows()]
                    if ax:
                        gsheets.upsert("AX_GRN", ax)
                        st.success(f"➡️ AX GRN Pending එකට ASN {len(ax)}ක් යැව්වා")

            st.success(f"Save කළා ✅  ·  discrepancy {len(disc)} line(s)")


# ═══════════════════════════════════════════════════════════════════
#  🧾 ASN REGISTER
# ═══════════════════════════════════════════════════════════════════
elif page == "🧾 ASN Register":
    hero("ASN Register", "Summary සහ Details — filter කරලා බලන්න / download කරන්න")

    summ = gsheets.get_df("ASN_SUMMARY")
    det = gsheets.get_df("ASN_DETAIL")
    if summ.empty:
        st.info("ASN records නෑ.")
        st.stop()

    c1, c2, c3 = st.columns(3)
    f_status = c1.multiselect("Status", sorted({s for s in summ["STATUS"] if s}))
    f_korber = c2.multiselect("Korber GRN", sorted({s for s in summ["KORBER GRN"] if s}))
    f_asn = c3.text_input("ASN search")

    v = summ.copy()
    if f_status:
        v = v[v["STATUS"].isin(f_status)]
    if f_korber:
        v = v[v["KORBER GRN"].isin(f_korber)]
    if f_asn.strip():
        v = v[v["ASN NO"].astype(str).str.contains(f_asn.strip(), case=False, na=False)]

    a, b, c, d = st.columns(4)
    kpi(a, len(v), "ASN")
    kpi(b, fmt_num(v["TOTAL QTY"].map(to_num).sum()), "ASN Qty")
    kpi(c, int(v["MATCHED LINES"].map(lambda x: to_num(x)).sum()), "Tally lines", "#2f8f83")
    kpi(d, int(v["MISSING LINES"].map(to_num).sum() + v["MISMATCH LINES"].map(to_num).sum()),
        "Issue lines", "#c4453f")

    st.markdown("#### Summary")
    show(v)

    st.markdown("#### Details")
    pick = st.selectbox("ASN එකක් තෝරන්න (details බලන්න)",
                        ["— සියල්ල —"] + list(v["ASN NO"].astype(str)))
    d2 = det if pick.startswith("—") else det[det["ASN NO"].astype(str) == pick]
    if not pick.startswith("—"):
        row = v[v["ASN NO"].astype(str) == pick]
        if not row.empty:
            r = row.iloc[0]
            st.markdown(
                f"**{pick}** &nbsp; {pill(r['STATUS'] or schema.S_NEW)} &nbsp; "
                f"Korber GRN: `{r['KORBER GRN']}` &nbsp; AX GRN: `{r['AX GRN']}` &nbsp; "
                f"Overall: {pill(r['OVERALL'] or schema.S_GRN_PENDING)}",
                unsafe_allow_html=True)
    show(d2)

    st.download_button(
        "📥 ASN Register (Summary + Details) Excel",
        reporting.build_excel({"Summary": v, "Details": d2}),
        file_name=f"ASN_Register_{date.today():%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    if not pick.startswith("—"):
        with st.expander(f"🗑️ `{pick}` delete කරන්න"):
            if SS["role"] != "admin":
                st.caption("Admin විතරයි — Sidebar → 🔑 Admin. "
                           "(සම්පූර්ණ options 🧹 Maintenance page එකේ.)")
            else:
                st.caption("Summary, Details, Discrepancy, AX GRN සහ Images ඔක්කොම අයින් වෙනවා.")
                t = st.text_input("තහවුරු කරන්න `DELETE` කියලා type කරන්න", key="reg_del")
                if st.button("🗑️ Delete", disabled=t.strip().upper() != "DELETE"):
                    res = {}
                    for k in ("ASN_SUMMARY", "ASN_DETAIL", "DISCREPANCY", "AX_GRN"):
                        res[k] = gsheets.delete_where(k, "ASN NO", [pick])
                    res["ASN_IMAGES"] = images.delete_for_asn([pick])
                    st.success("Delete කළා ✅ — " +
                               " · ".join(f"{k}: {n}" for k, n in res.items()))
                    st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  🔍 SEARCH
# ═══════════════════════════════════════════════════════════════════
elif page == "🔍 Search":
    hero("Search", "HU, ASN, item, lot, PO, GRN, vendor — ඕනෑම එකක් හොයන්න")

    SEARCHABLE = ["ASN_DETAIL", "ASN_SUMMARY", "INVENTORY", "DISCREPANCY",
                  "AX_GRN", "ASN_IMAGES", "RECON_LOG", "EMAIL_LOG"]

    c1, c2 = st.columns([3, 2])
    term = c1.text_input("🔎 Search", placeholder="උදා: ETHT0726 · 26AUG_UPPD_40659 · GRN-40196",
                         key="q_term")
    where = c2.multiselect("Sheets", SEARCHABLE,
                           default=["ASN_DETAIL", "ASN_SUMMARY", "INVENTORY"])

    c1, c2, c3 = st.columns(3)
    exact = c1.checkbox("Exact match", value=False)
    case = c2.checkbox("Case sensitive", value=False)
    limit = int(c3.number_input("Sheet එකකට උපරිම results", value=300.0,
                                min_value=20.0, max_value=3000.0, step=50.0))

    with st.expander("➕ Advanced — column එකකින් filter"):
        adv_sheet = st.selectbox("Sheet", ["—"] + SEARCHABLE, key="adv_sh")
        adv_col, adv_val = "—", ""
        if adv_sheet != "—":
            adv_col = st.selectbox("Column", ["—"] + schema.SHEETS[adv_sheet]["headers"],
                                   key="adv_col")
            adv_val = st.text_input("Value", key="adv_val")

    if not term.strip() and adv_sheet == "—":
        st.info("සෙවීමට වචනයක් type කරන්න. HU ID, ASN number, item, lot, GRN number, "
                "vendor — ඕනෑම එකක් වැඩ කරනවා.")
    else:
        q = term.strip()
        total, tabs_data = 0, []

        targets = where if q else []
        if adv_sheet != "—" and adv_sheet not in targets:
            targets = targets + [adv_sheet]

        for key in targets:
            df = gsheets.get_df(key)
            if df.empty:
                continue
            v = df
            if q:
                s = v.astype(str)
                if exact:
                    m = s.apply(lambda col: col.str.strip().str.lower() == q.lower()
                                if not case else col.str.strip() == q)
                else:
                    m = s.apply(lambda col: col.str.contains(q, case=case,
                                                             regex=False, na=False))
                v = v[m.any(axis=1)]
            if adv_sheet == key and adv_col != "—" and adv_val.strip():
                v = v[v[adv_col].astype(str).str.contains(adv_val.strip(), case=case,
                                                          regex=False, na=False)]
            if not v.empty:
                total += len(v)
                tabs_data.append((key, v.head(limit)))

        if not tabs_data:
            st.warning(f"`{q or adv_val}` — කිසිම තැනක හම්බුණේ නෑ.")
        else:
            st.success(f"Results {total}ක් · sheets {len(tabs_data)}ක")
            tabs = st.tabs([f"{k} ({len(v)})" for k, v in tabs_data])
            for t, (k, v) in zip(tabs, tabs_data):
                with t:
                    # හම්බුණේ මොන columns වලද කියලා
                    if q:
                        hit_cols = [c for c in v.columns
                                    if v[c].astype(str).str.contains(
                                        q, case=case, regex=False, na=False).any()]
                        if hit_cols:
                            st.caption("Match වුණ columns: " +
                                       ", ".join(f"`{c}`" for c in hit_cols[:10]))
                    show(v, height=460)

            st.download_button(
                "📥 Search results Excel",
                reporting.build_excel({k[:31]: v for k, v in tabs_data}),
                file_name=f"Search_{date.today():%Y%m%d}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            # ASN එකක් හම්බුණා නම් shortcut
            asn_hits = set()
            for k, v in tabs_data:
                if "ASN NO" in v.columns:
                    asn_hits |= {clean(a) for a in v["ASN NO"] if clean(a)}
            if asn_hits:
                st.caption("හම්බුණු ASN: " + ", ".join(f"`{a}`" for a in sorted(asn_hits)[:12]))


# ═══════════════════════════════════════════════════════════════════
#  ⚠️ DISCREPANCY
# ═══════════════════════════════════════════════════════════════════
elif page == "⚠️ Discrepancy":
    hero("Discrepancy Report", "Tally නොවුණ ඒවා — summary සහ line level details")

    disc = gsheets.get_df("DISCREPANCY")
    if disc.empty:
        st.success("🎉 විෂමතා නෑ.")
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    f_run = c1.selectbox("Recon run", ["— සියල්ල —"] +
                         sorted({r for r in disc["RUN ID"] if r}, reverse=True))
    f_type = c2.multiselect("Type", sorted({t for t in disc["DISCREPANCY TYPE"] if t}))
    f_sev = c3.multiselect("Severity", sorted({s for s in disc["SEVERITY"] if s}))
    f_st = c4.multiselect("Status", sorted({s for s in disc["STATUS"] if s}),
                          default=["OPEN"] if "OPEN" in set(disc["STATUS"]) else [])

    v = disc.copy()
    if not f_run.startswith("—"):
        v = v[v["RUN ID"] == f_run]
    if f_type:
        v = v[v["DISCREPANCY TYPE"].isin(f_type)]
    if f_sev:
        v = v[v["SEVERITY"].isin(f_sev)]
    if f_st:
        v = v[v["STATUS"].isin(f_st)]

    a, b, c, d = st.columns(4)
    kpi(a, len(v), "Discrepancy lines", "#c4453f")
    kpi(b, v["ASN NO"].nunique(), "ASN affected")
    kpi(c, int((v["SEVERITY"] == "HIGH").sum()), "High severity", "#c4453f")
    kpi(d, fmt_num(v["QTY DIFF"].map(to_num).sum()), "Net qty diff")

    # ── SUMMARY view ──
    st.markdown("#### 📊 Summary")
    g = (v.assign(_a=v["ASN QTY"].map(to_num), _i=v["INV QTY"].map(to_num))
           .groupby(["ASN NO", "DISCREPANCY TYPE"])
           .agg(Lines=("DISC ID", "count"), ASN_Qty=("_a", "sum"),
                INV_Qty=("_i", "sum")).reset_index())
    g["Qty_Diff"] = g["INV_Qty"] - g["ASN_Qty"]
    show(g)

    st.markdown("#### 📋 Details")
    dcols = ["ASN NO", "ASN LINE", "HU ID", "ITEM NUMBER", "LOT NUMBER", "ASN QTY",
             "INV QTY", "QTY DIFF", "DISCREPANCY TYPE", "SEVERITY", "DETAIL",
             "STATUS", "GENERATED AT", "RUN ID"]
    show(v[dcols])

    st.download_button(
        "📥 Discrepancy Report (Summary + Details) Excel",
        reporting.build_excel({"Summary": g, "Details": v[dcols]}),
        file_name=f"Discrepancy_{date.today():%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary")

    st.markdown("---")
    with st.expander("✅ Discrepancy close කරන්න"):
        ids = st.multiselect("DISC ID", list(v["DISC ID"]))
        note = st.text_input("Note")
        if st.button("Close", disabled=not ids):
            full = gsheets.get_df("DISCREPANCY")
            m = full["DISC ID"].isin(ids)
            full.loc[m, "STATUS"] = "CLOSED"
            full.loc[m, "ACTION BY"] = SS["user"] or "unknown"
            full.loc[m, "CLOSED AT"] = now_str()
            full.loc[m, "NOTE"] = note
            gsheets.overwrite("DISCREPANCY", full)
            st.success(f"{len(ids)} close කළා ✅")
            st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  ✉️ EMAIL
# ═══════════════════════════════════════════════════════════════════
elif page == "✉️ Email":
    hero("Discrepancy Email", "විස්තර සහිත Markdown email එකක් auto-generate")

    disc = gsheets.get_df("DISCREPANCY")
    summ = gsheets.get_df("ASN_SUMMARY")
    s = gsheets.settings_dict()

    if disc.empty:
        st.info("Discrepancy නෑ — email එකක් අවශ්‍ය නෑ.")
        st.stop()

    c1, c2 = st.columns(2)
    runs = ["— සියල්ල —"] + sorted({r for r in disc["RUN ID"] if r}, reverse=True)
    f_run = c1.selectbox("Recon run", runs)
    only_open = c2.checkbox("OPEN විතරක්", value=True)

    v = disc.copy()
    if not f_run.startswith("—"):
        v = v[v["RUN ID"] == f_run]
    if only_open:
        v = v[v["STATUS"] == "OPEN"]

    asn_opts = sorted({a for a in v["ASN NO"] if a})
    picked = st.multiselect("ASN", asn_opts, default=asn_opts)
    v = v[v["ASN NO"].isin(picked)]

    c1, c2 = st.columns(2)
    to = c1.text_input("To", s.get("EMAIL_TO", ""))
    cc = c2.text_input("Cc", s.get("EMAIL_CC", ""))

    if st.button("📝 Email generate", type="primary", disabled=v.empty):
        sm = summ[summ["ASN NO"].isin(picked)] if not summ.empty else pd.DataFrame()
        subject, md = reporting.discrepancy_email(
            sm, v,
            company=s.get("COMPANY", "EFL"), site=s.get("SITE", ""),
            client=s.get("CLIENT_CODE", ""), prepared_by=SS["user"] or "",
            to=to, cc=cc,
            run_id="" if f_run.startswith("—") else f_run,
            inventory_note=SS.get("inv_note", ""))
        SS["email"] = {"subject": subject, "md": md, "asns": picked, "to": to, "cc": cc}

    E = SS.get("email")
    if E:
        st.markdown("#### Subject")
        st.code(E["subject"], language="text")
        st.markdown("#### Markdown body — copy කරගන්න")
        st.code(E["md"], language="markdown")

        c1, c2, c3 = st.columns(3)
        c1.download_button("📥 .md download", E["md"].encode("utf-8"),
                           file_name=f"discrepancy_email_{date.today():%Y%m%d}.md",
                           mime="text/markdown")
        if c2.button("💾 EMAIL_LOG එකට save"):
            gsheets.append_rows("EMAIL_LOG", [[
                uuid.uuid4().hex[:10].upper(), now_str(), SS["user"] or "unknown",
                ", ".join(E["asns"][:20]), E["subject"], E["to"], E["cc"],
                E["md"][:45000],
            ]])
            st.success("Save කළා ✅")
        with c3:
            st.caption("Render preview පහළින්")

        with st.expander("👁️ Rendered preview"):
            st.markdown(E["md"])


# ═══════════════════════════════════════════════════════════════════
#  ✅ AX GRN
# ═══════════════════════════════════════════════════════════════════
elif page == "✅ AX GRN":
    hero("AX GRN", "Korber GRN done → AX GRN pending → AX GRN done → fully complete")

    ax = gsheets.get_df("AX_GRN")
    if ax.empty:
        st.info("AX queue එක හිස්. 🔄 Reconciliation එකේදී Korber GRN Done වුණ ASN මෙතනට එනවා.")
        st.stop()

    pend = ax[ax["AX GRN"] != schema.AX_DONE]
    done = ax[ax["AX GRN"] == schema.AX_DONE]

    a, b, c = st.columns(3)
    kpi(a, len(pend), "AX GRN Pending", "#3a6ea5")
    kpi(b, len(done), "AX GRN Done", "#2f7a45")
    kpi(c, fmt_num(pend["TOTAL QTY"].map(to_num).sum()), "Pending Qty")

    st.markdown("### 🕓 AX GRN Pending")
    show(pend)

    if not pend.empty:
        st.markdown("#### AX GRN Done කරන්න")
        c1, c2, c3 = st.columns([2, 1, 1])
        sel = c1.multiselect("ASN", list(pend["ASN NO"].astype(str)))
        ax_no = c2.text_input("AX GRN No", "")
        ax_dt = c3.date_input("AX GRN Date", value=date.today())

        if st.button("✅ Mark AX GRN Done", type="primary", disabled=not sel):
            ts, user = now_str(), SS["user"] or "unknown"
            m = ax["ASN NO"].astype(str).isin(sel)
            ax.loc[m, "AX GRN"] = schema.AX_DONE
            ax.loc[m, "AX GRN NO"] = ax_no
            ax.loc[m, "AX GRN DATE"] = str(ax_dt)
            ax.loc[m, "AX GRN BY"] = user
            ax.loc[m, "OVERALL"] = schema.S_COMPLETE
            gsheets.overwrite("AX_GRN", ax)

            # ASN_SUMMARY
            summ = gsheets.get_df("ASN_SUMMARY")
            ms = summ["ASN NO"].astype(str).isin(sel)
            summ.loc[ms, "AX GRN"] = schema.AX_DONE
            summ.loc[ms, "AX GRN NO"] = ax_no
            summ.loc[ms, "AX GRN DATE"] = str(ax_dt)
            summ.loc[ms, "AX GRN BY"] = user
            summ.loc[ms, "OVERALL"] = schema.S_COMPLETE
            summ.loc[ms, "STATUS"] = schema.S_COMPLETE
            gsheets.overwrite("ASN_SUMMARY", summ)

            # ASN_DETAIL
            det = gsheets.get_df("ASN_DETAIL")
            md = det["ASN NO"].astype(str).isin(sel)
            det.loc[md, "AX GRN"] = schema.AX_DONE
            det.loc[md, "REMARK"] = f"AX GRN Done {ts}"
            gsheets.overwrite("ASN_DETAIL", det)

            st.success(f"🎉 ASN {len(sel)}ක් FULLY COMPLETE!")
            st.balloons()
            st.rerun()

    st.markdown("### ✅ Fully Complete")
    show(done)

    st.download_button("📥 AX GRN Excel",
                       reporting.build_excel({"Pending": pend, "Completed": done}),
                       file_name=f"AX_GRN_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ═══════════════════════════════════════════════════════════════════
#  🖼️ ASN IMAGES
# ═══════════════════════════════════════════════════════════════════
elif page == "🖼️ Attachments":
    hero("Attachments", "ASN එකට අදාළ photos සහ PDF documents")

    meta = gsheets.get_df("ASN_IMAGES")
    summ = gsheets.get_df("ASN_SUMMARY")
    asn_opts = sorted({a for a in summ["ASN NO"] if a}) if not summ.empty else []
    if not asn_opts and not meta.empty:
        asn_opts = sorted({a for a in meta["ASN NO"] if a})

    mode = str(gsheets.settings_dict().get("IMAGE_STORAGE", "DRIVE")).upper()
    st.caption(f"Storage: **{mode}** — Setup → Attachments එකෙන් වෙනස් කරන්න පුළුවන්.")

    with st.expander("අලුත් attachment එකක්", expanded=meta.empty):
        c1, c2 = st.columns([1, 2])
        asn = c1.selectbox("ASN", asn_opts or ["—"])
        note = c2.text_input("Note", "")
        ups = st.file_uploader("Images හෝ PDF",
                               type=["png", "jpg", "jpeg", "webp", "bmp", "pdf"],
                               accept_multiple_files=True, key="img_only")
        if st.button("Upload", disabled=not ups or asn == "—", type="primary"):
            ok_n, errs = 0, []
            for uf in ups:
                d = uf.getvalue()
                is_pdf = parsing.is_pdf(uf.name, d)
                ok, msg = images.save_image(
                    asn, uf.name, d,
                    "application/pdf" if is_pdf else (uf.type or "image/png"),
                    source="ASN DOCUMENT" if is_pdf else "MANUAL UPLOAD",
                    user=SS["user"] or "unknown", note=note)
                if ok:
                    ok_n += 1
                    if msg and "fail" in msg.lower():
                        errs.append(msg)
                else:
                    errs.append(msg)
            if ok_n:
                st.success(f"{ok_n} save කළා")
            for e_ in errs:
                st.warning(e_)
            if ok_n:
                st.rerun()

    if meta.empty:
        st.info("Attachments නෑ.")
    else:
        c1, c2 = st.columns([2, 1])
        f = c1.selectbox("ASN filter", ["සියල්ල"] +
                         sorted({a for a in meta["ASN NO"] if a}))
        kinds = sorted({k for k in meta["KIND"] if str(k).strip()}) or ["IMAGE"]
        kf = c2.multiselect("Kind", kinds, default=kinds)

        v = meta if f == "සියල්ල" else meta[meta["ASN NO"] == f]
        if kf:
            v = v[v["KIND"].isin(kf) | (v["KIND"].astype(str).str.strip() == "")]

        a, b, c = st.columns(3)
        kpi(a, len(v), "Attachments")
        kpi(b, v["ASN NO"].nunique(), "ASN covered")
        kpi(c, f'{fmt_num(v["SIZE KB"].map(to_num).sum())} KB', "Total size")

        show(v[["IMAGE ID", "ASN NO", "FILE NAME", "KIND", "SOURCE", "SIZE KB",
                "STORAGE", "UPLOADED AT", "UPLOADED BY", "NOTE"]])

        st.markdown("###### Preview")
        cols = st.columns(4)
        for i, (_, r) in enumerate(v.head(16).iterrows()):
            with cols[i % 4]:
                is_pdf = str(r.get("KIND", "")).upper() == "PDF" or \
                    str(r.get("MIME", "")).lower() == "application/pdf"
                if str(r.get("STORAGE", "")).upper() == "DRIVE" and r.get("LINK"):
                    st.markdown(f"{'📕' if is_pdf else '🖼️'} "
                                f"[{r['FILE NAME']}]({r['LINK']})")
                    st.caption(f"{r['ASN NO']} · {r['SIZE KB']} KB")
                else:
                    data = images.load_image(r["IMAGE ID"])
                    if not data:
                        st.caption(f"{r['FILE NAME']} — data නෑ")
                    elif is_pdf:
                        st.markdown(f"📕 **{r['FILE NAME']}**")
                        st.caption(f"{r['ASN NO']} · {r['SIZE KB']} KB")
                        st.download_button("Download", data,
                                           file_name=r["FILE NAME"],
                                           mime="application/pdf",
                                           key=f"dl_{r['IMAGE ID']}")
                    else:
                        st.image(data, caption=f"{r['ASN NO']} · {r['FILE NAME']}",
                                 width="stretch")
                        st.download_button("Download", data,
                                           file_name=r["FILE NAME"],
                                           mime=r.get("MIME") or "image/jpeg",
                                           key=f"dl_{r['IMAGE ID']}")

        with st.expander("Delete"):
            ids = st.multiselect("IMAGE ID", list(v["IMAGE ID"]))
            if st.button("Delete", disabled=not ids):
                n = images.delete_images(ids)
                st.success(f"{n} delete කළා")
                st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  📊 DASHBOARD
# ═══════════════════════════════════════════════════════════════════
elif page == "📊 Dashboard":
    summ = gsheets.get_df("ASN_SUMMARY")
    det = gsheets.get_df("ASN_DETAIL")
    disc = gsheets.get_df("DISCREPANCY")
    log = gsheets.get_df("RECON_LOG")

    last = clean(log["RUN AT"].iloc[-1]) if not log.empty else "—"
    hero("Dashboard", f"ASN → Korber GRN → AX GRN · last reconciliation {last}")

    if summ.empty:
        st.info("Data නෑ. **📤 ASN Upload** එකෙන් පටන් ගන්න.")
        st.stop()

    n_asn = len(summ)
    n_korber = int((summ["KORBER GRN"] == schema.K_DONE).sum())
    n_axp = int((summ["AX GRN"] == schema.AX_PENDING).sum())
    n_comp = int((summ["OVERALL"] == schema.S_COMPLETE).sum())
    n_open = int((disc["STATUS"] == "OPEN").sum()) if not disc.empty else 0
    asn_qty = summ["TOTAL QTY"].map(to_num).sum()
    rec_qty = summ["RECEIVED QTY"].map(to_num).sum()
    lines = len(det)
    tally = int((det["MATCH STATUS"] == schema.M_MATCHED).sum()) if not det.empty else 0
    rate = (tally / lines * 100) if lines else 0
    pending_asn = n_asn - n_comp

    a, b, c, d = st.columns(4)
    kpi(a, n_asn, "ASN in system", note=f"{lines} lines · {fmt_num(asn_qty)} qty")
    kpi(b, f"{rate:.0f}%", "Lines tallied", ACCENT, f"{tally} of {lines} lines")
    kpi(c, n_open, "Open discrepancies", DANGER if n_open else OK,
        "all clear" if not n_open else "needs action")
    kpi(d, pending_asn, "ASN not yet complete", WARN if pending_asn else OK,
        f"{n_comp} fully complete")

    st.markdown("#### Pipeline")
    pipeline([
        ("ASN uploaded", n_asn, MUTED),
        ("Korber GRN done", n_korber, ACCENT),
        ("AX GRN pending", n_axp, INFO),
        ("Fully complete", n_comp, OK),
    ])

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("###### ASN by status")
        vc = summ["STATUS"].replace("", schema.S_NEW).value_counts().sort_values()
        fig = go.Figure(go.Bar(
            x=vc.values, y=vc.index, orientation="h",
            marker_color=[schema.STATUS_COLORS.get(i, MUTED) for i in vc.index],
            text=vc.values, textposition="outside", cliponaxis=False))
        fig.update_layout(xaxis=dict(showticklabels=False, showgrid=False),
                          yaxis=dict(showgrid=False))
        st.plotly_chart(fig_style(fig, 290), width="stretch")

    with c2:
        st.markdown("###### Line match result")
        if det.empty or det["MATCH STATUS"].astype(str).str.strip().eq("").all():
            st.caption("තාම reconcile කරලා නෑ.")
        else:
            v = det["MATCH STATUS"].replace("", schema.M_PENDING).value_counts().sort_values()
            cmap = {schema.M_MATCHED: ACCENT, schema.M_MISSING: WARN,
                    schema.M_PENDING: MUTED, schema.M_EXTRA: INFO}
            fig = go.Figure(go.Bar(
                x=v.values, y=v.index, orientation="h",
                marker_color=[cmap.get(i, DANGER) for i in v.index],
                text=v.values, textposition="outside", cliponaxis=False))
            fig.update_layout(xaxis=dict(showticklabels=False, showgrid=False),
                              yaxis=dict(showgrid=False))
            st.plotly_chart(fig_style(fig, 290), width="stretch")

    c1, c2 = st.columns([3, 2])

    with c1:
        st.markdown("###### ASN quantity — expected vs received")
        top = summ.copy()
        top["_a"] = top["TOTAL QTY"].map(to_num)
        top["_r"] = top["RECEIVED QTY"].map(to_num)
        top = top.nlargest(12, "_a").sort_values("_a")
        fig = go.Figure()
        fig.add_bar(name="ASN", y=top["ASN NO"], x=top["_a"], orientation="h",
                    marker_color="#cbd3dc")
        fig.add_bar(name="Received", y=top["ASN NO"], x=top["_r"], orientation="h",
                    marker_color=ACCENT)
        fig.update_layout(barmode="group", bargap=.28,
                          yaxis=dict(showgrid=False),
                          xaxis=dict(showgrid=True, gridcolor=LINE))
        st.plotly_chart(fig_style(fig, 340, legend=True), width="stretch")

    with c2:
        st.markdown("###### Discrepancies by type")
        if disc.empty:
            st.success("විෂමතා නෑ.")
        else:
            v = disc["DISCREPANCY TYPE"].value_counts().sort_values()
            fig = go.Figure(go.Bar(
                x=v.values, y=v.index, orientation="h",
                marker_color=DANGER, text=v.values,
                textposition="outside", cliponaxis=False))
            fig.update_layout(xaxis=dict(showticklabels=False, showgrid=False),
                              yaxis=dict(showgrid=False))
            st.plotly_chart(fig_style(fig, 340), width="stretch")

    st.markdown("###### Needs action")
    need = summ[summ["OVERALL"] != schema.S_COMPLETE]
    if need.empty:
        st.success("හැම ASN එකක්ම fully complete 🎉")
    else:
        show(need[["ASN NO", "TOTAL LINES", "MATCHED LINES", "MISSING LINES",
                   "MISMATCH LINES", "EXTRA LINES", "STATUS", "KORBER GRN",
                   "AX GRN", "LAST RECON"]], height=300)

    st.caption(f"Quantity received {fmt_num(rec_qty)} of {fmt_num(asn_qty)} expected "
               f"· variance {fmt_num(rec_qty - asn_qty)}")


# ═══════════════════════════════════════════════════════════════════
#  🗂️ DATA MANAGER
# ═══════════════════════════════════════════════════════════════════
elif page == "🗂️ Data Manager":
    hero("Data Manager", "ඕනෑම sheet එකක records edit / delete කරන්න (admin)")

    if SS["role"] != "admin":
        st.warning("Admin විතරයි. Sidebar → 🔑 Admin → PIN.")
        st.stop()

    key = st.selectbox("Sheet", list(schema.SHEETS))
    df = gsheets.get_df(key)
    st.caption(f"{len(df)} rows · {len(schema.SHEETS[key]['headers'])} columns")

    ed = st.data_editor(df, num_rows="dynamic", width="stretch",
                        height=520, key=f"ed_{key}")

    c1, c2 = st.columns([1, 3])
    if c1.button("💾 Save", type="primary"):
        gsheets.overwrite(key, ed)
        st.success("Save කළා ✅")
        st.rerun()
    c2.caption("⚠️ Save කළාම sheet එකේ තියෙන data මේ table එකෙන් replace වෙනවා.")

    st.download_button(f"📥 {key} Excel",
                       reporting.build_excel({key[:31]: df}),
                       file_name=f"{key}_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ═══════════════════════════════════════════════════════════════════
#  🧹 MAINTENANCE  — ASN delete · database reset
# ═══════════════════════════════════════════════════════════════════
elif page == "🧹 Maintenance":
    hero("Maintenance", "ASN delete · sheet clear · database reset")

    if SS["role"] != "admin":
        st.warning("Admin විතරයි. Sidebar → 🔑 Admin → PIN දාන්න.")
        st.stop()

    ASN_SHEETS = {
        "ASN_SUMMARY": "ASN NO",
        "ASN_DETAIL": "ASN NO",
        "DISCREPANCY": "ASN NO",
        "AX_GRN": "ASN NO",
    }

    t1, t2, t3 = st.tabs(["🗑️ ASN Delete", "🧽 Sheet Clear", "💥 Database Reset"])

    # ───────────── ASN delete ─────────────
    with t1:
        st.markdown("#### ASN එකක් සම්පූර්ණයෙන් delete කරන්න")
        st.caption("තෝරපු ASN එකට අදාළ Summary, Details, Discrepancy, AX GRN සහ "
                   "Images ඔක්කොම අයින් වෙනවා. මේක **ආපහු හදාගන්න බෑ**.")

        summ = gsheets.get_df("ASN_SUMMARY")
        det = gsheets.get_df("ASN_DETAIL")
        opts = sorted({clean(a) for a in summ["ASN NO"] if clean(a)}) if not summ.empty else []
        if not opts and not det.empty:
            opts = sorted({clean(a) for a in det["ASN NO"] if clean(a)})

        if not opts:
            st.info("ASN records නෑ.")
        else:
            sel = st.multiselect("Delete කරන්න ඕන ASN", opts, key="del_asn")

            if sel:
                # මොනවද යන්නේ කියලා පෙන්නනවා
                st.markdown("##### මේවා අයින් වෙනවා")
                counts = {}
                for k, col in ASN_SHEETS.items():
                    d = gsheets.get_df(k)
                    counts[k] = 0 if d.empty or col not in d.columns else int(
                        d[col].astype(str).str.strip().isin(sel).sum())
                imeta = gsheets.get_df("ASN_IMAGES")
                counts["ASN_IMAGES"] = 0 if imeta.empty else int(
                    imeta["ASN NO"].astype(str).str.strip().isin(sel).sum())
                show(pd.DataFrame([{"Sheet": k, "Rows": v} for k, v in counts.items()]))

                prev = det[det["ASN NO"].astype(str).isin(sel)] if not det.empty else pd.DataFrame()
                with st.expander(f"👁️ Delete වෙන lines ({len(prev)})"):
                    show(prev[["ASN NO", "ASN LINE", "HU ID", "ITEM NUMBER", "QTY",
                               "MATCH STATUS", "KORBER GRN", "AX GRN"]]
                         if not prev.empty else prev)

                st.download_button(
                    "📥 Delete කරන්න කලින් backup එකක් ගන්න",
                    reporting.build_excel({
                        "Summary": summ[summ["ASN NO"].astype(str).isin(sel)],
                        "Details": prev}),
                    file_name=f"ASN_backup_{date.today():%Y%m%d}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

                st.markdown("##### තහවුරු කිරීම")
                typed = st.text_input("තහවුරු කරන්න `DELETE` කියලා type කරන්න",
                                      key="del_confirm")
                also_img = st.checkbox("Images ත් අයින් කරන්න", value=True)

                if st.button("🗑️ Delete", type="primary",
                             disabled=typed.strip().upper() != "DELETE"):
                    with st.spinner("Delete වෙනවා..."):
                        res = {}
                        for k, col in ASN_SHEETS.items():
                            res[k] = gsheets.delete_where(k, col, sel)
                        if also_img:
                            res["ASN_IMAGES"] = images.delete_for_asn(sel)
                    st.success("Delete කළා ✅ — " +
                               " · ".join(f"{k}: {v}" for k, v in res.items()))
                    st.rerun()

    # ───────────── single sheet clear ─────────────
    with t2:
        st.markdown("#### Sheet එකක data ඔක්කොම clear කරන්න")
        st.caption("Headers විතරක් ඉතුරු වෙනවා. Sheet එක delete වෙන්නේ නෑ.")

        status = gsheets.sheet_status()
        show(status)

        k = st.selectbox("Sheet", list(schema.SHEETS), key="clr_sheet")
        cur = gsheets.get_df(k)
        st.caption(f"දැන් rows {len(cur)}ක් තියෙනවා.")

        st.download_button(
            f"📥 {k} backup",
            reporting.build_excel({k[:31]: cur}),
            file_name=f"{k}_backup_{date.today():%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        typed2 = st.text_input(f"තහවුරු කරන්න `{k}` කියලා type කරන්න", key="clr_confirm")
        if st.button("🧽 Clear", disabled=typed2.strip() != k, type="primary"):
            n = gsheets.clear_sheet(k)
            st.success(f"{k} — rows {n}ක් clear කළා ✅")
            st.rerun()

    # ───────────── full reset ─────────────
    with t3:
        st.markdown("#### 💥 Database Reset")
        st.error("මේකෙන් තෝරපු sheets වල **හැම record එකක්ම** අයින් වෙනවා. "
                 "ආපහු හදාගන්න බෑ — කලින් backup එකක් ගන්න.")

        DATA_SHEETS = ["ASN_SUMMARY", "ASN_DETAIL", "INVENTORY", "DISCREPANCY",
                       "AX_GRN", "ASN_IMAGES", "IMAGE_DATA", "RECON_LOG", "EMAIL_LOG"]
        MASTERS = ["USER-M", "SETTINGS"]

        scope = st.radio(
            "Reset scope",
            ["🔸 Transaction data විතරක් (masters + settings ඉතුරු වෙනවා)",
             "🔹 Custom — sheets තෝරන්න",
             "🔴 සම්පූර්ණයෙන්ම (masters + settings ඇතුළුව)"],
            key="reset_scope")

        if scope.startswith("🔸"):
            targets = DATA_SHEETS
        elif scope.startswith("🔴"):
            targets = DATA_SHEETS + MASTERS
        else:
            targets = st.multiselect("Sheets", list(schema.SHEETS),
                                     default=DATA_SHEETS, key="reset_pick")

        rows_now = {}
        for k in targets:
            try:
                rows_now[k] = len(gsheets.get_df(k))
            except Exception:
                rows_now[k] = 0
        st.markdown(f"**Sheets {len(targets)}ක · rows {sum(rows_now.values())}ක්** අයින් වෙනවා")
        show(pd.DataFrame([{"Sheet": k, "Rows": v} for k, v in rows_now.items()]))

        st.markdown("##### 📥 Reset කරන්න කලින් backup")
        if st.button("Backup file එකක් හදන්න", key="mk_backup"):
            SS["backup"] = reporting.build_excel(
                {k[:31]: gsheets.get_df(k) for k in targets})
        if SS.get("backup"):
            st.download_button(
                "📥 Full backup download",
                SS["backup"],
                file_name=f"FULL_BACKUP_{datetime.now():%Y%m%d_%H%M}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        st.markdown("##### තහවුරු කිරීම")
        c1, c2 = st.columns(2)
        pin2 = c1.text_input("Admin PIN නැවත", type="password", key="reset_pin")
        typed3 = c2.text_input("`RESET` කියලා type කරන්න", key="reset_confirm")
        ack = st.checkbox("මම තේරුම් අරගෙන ඉන්නවා — මේ data ආපහු ගන්න බෑ.",
                          key="reset_ack")

        good_pin = pin2 == str(gsheets.settings_dict().get("ADMIN_PIN", "1234"))
        ready = bool(targets) and good_pin and typed3.strip().upper() == "RESET" and ack

        if pin2 and not good_pin:
            st.warning("PIN වැරදියි.")

        if st.button("💥 Database Reset", type="primary", disabled=not ready):
            with st.spinner("Reset වෙනවා..."):
                res = gsheets.reset_database(targets)
                if "SETTINGS" in targets or "USER-M" in targets:
                    gsheets.ensure_all()      # masters ආපහු seed වෙනවා
            st.success("Reset කළා ✅ — " +
                       " · ".join(f"{k}: {v}" for k, v in res.items()))
            SS["recon"] = None
            SS["parsed_asn"] = {}
            SS["inv_df"] = None
            SS["backup"] = None
            st.rerun()


# ───────────────────────────── footer ─────────────────────────────
st.markdown(
    f"<div style='text-align:center;color:{MUTED};font-size:.73rem;"
    f"margin-top:2.4rem;padding-top:1rem;border-top:1px solid {LINE}'>"
    "ASN ↔ GRN Control System · Korber One / AX · EFL Warehouse Operations"
    "</div>", unsafe_allow_html=True)
