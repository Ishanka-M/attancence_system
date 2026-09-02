"""
app.py
======
ASN to Korber GRN Control System (Streamlit + Google Sheets).

Flow
    ASN Upload (Excel or PDF)  ->  Inventory upload
        -> reconciliation runs automatically, no further input needed
        -> tallied lines become Korber GRN Done and move to AX GRN Pending
        -> mismatches raise discrepancies and an email is generated
        -> AX GRN Done  ->  Fully Complete
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import drive
import gsheets
import images
import matching
import parsing
import pipeline
import reporting
import schema
from matching import nkey, now_str
from parsing import clean, fmt_num, to_num

st.set_page_config(page_title="ASN / GRN Control System",
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
  h1,h2,h3,h4 {{color:{INK}; letter-spacing:-.015em; font-weight:650;}}

  .ph {{margin:0 0 1.4rem 0; padding-bottom:.85rem; border-bottom:1px solid {LINE};}}
  .ph .t {{font-size:1.42rem; font-weight:650; color:{INK}; line-height:1.2;}}
  .ph .s {{font-size:.86rem; color:{MUTED}; margin-top:.28rem;}}

  .kc {{background:#fff; border:1px solid {LINE}; border-radius:10px;
        padding:.95rem 1.05rem; height:100%;}}
  .kc .v {{font-size:1.72rem; font-weight:650; line-height:1.1; color:{INK};
           font-variant-numeric:tabular-nums;}}
  .kc .l {{font-size:.79rem; color:{MUTED}; margin-top:.3rem;}}
  .kc .n {{font-size:.72rem; color:{MUTED}; margin-top:.45rem; opacity:.85;}}

  .pill {{display:inline-block; padding:.16rem .58rem; border-radius:6px;
          font-size:.755rem; font-weight:600; border:1px solid;}}

  .pipe {{display:flex; gap:6px; margin:.2rem 0 1rem 0;}}
  .pipe .seg {{flex:1; background:#fff; border:1px solid {LINE};
               border-radius:8px; padding:.7rem .85rem;}}
  .pipe .seg .n {{font-size:1.35rem; font-weight:650; color:{INK};
                  font-variant-numeric:tabular-nums;}}
  .pipe .seg .c {{font-size:.75rem; color:{MUTED}; margin-top:.15rem;}}
  .pipe .seg .bar {{height:3px; border-radius:2px; margin-top:.6rem;}}

  .stDataFrame {{font-size:.83rem;}}
  div[data-testid="stDataFrame"] {{border:1px solid {LINE}; border-radius:9px;}}

  section[data-testid="stSidebar"] {{background:#0f1720; border-right:1px solid #1c2531;}}
  section[data-testid="stSidebar"] * {{color:#d5dbe2;}}
  section[data-testid="stSidebar"] h3 {{color:#fff; font-size:1rem;}}

  .stButton>button {{border-radius:8px; font-weight:550;}}
  .stDownloadButton>button {{border-radius:8px;}}
  button[data-baseweb="tab"] {{font-size:.87rem;}}
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


def pill(text: str) -> str:
    c = schema.STATUS_COLORS.get(text, MUTED)
    return (f'<span class="pill" style="color:{c};border-color:{c}55;'
            f'background:{c}14">{text}</span>')


def pipeline_strip(stages: list[tuple[str, int, str]]):
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
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    return fig


def show(df: pd.DataFrame, height: int | None = None, n: int = 3000):
    if df is None or df.empty:
        st.info("No records.")
        return
    st.dataframe(df.head(n), width="stretch", hide_index=True,
                 height=height or min(60 + 34 * min(len(df), 15), 560))


# ───────────────────────────── session ─────────────────────────────
SS = st.session_state
SS.setdefault("user", "")
SS.setdefault("role", "user")
SS.setdefault("parsed_asn", {})
SS.setdefault("inv_df", None)
SS.setdefault("inv_note", "")
SS.setdefault("auto", None)          # last automatic reconciliation result
SS.setdefault("recon", None)         # last manual reconciliation result
SS.setdefault("email", None)
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


def attachment_block(asn: str, key_prefix: str, columns: int = 4):
    """Preview and download every attachment belonging to one ASN."""
    att = pipeline.attachments_for(asn)
    if att.empty:
        st.caption("No attachments for this ASN.")
        return
    cols = st.columns(columns)
    for i, (_, r) in enumerate(att.iterrows()):
        with cols[i % columns]:
            is_pdf = (str(r.get("KIND", "")).upper() == "PDF"
                      or str(r.get("MIME", "")).lower() == "application/pdf")
            label = f"{'PDF' if is_pdf else 'Image'} · {r['FILE NAME']}"
            data = None
            if str(r.get("STORAGE", "")).upper() != "DRIVE":
                data = images.load_image(r["IMAGE ID"])
            if data and not is_pdf:
                st.image(data, caption=r["FILE NAME"], width="stretch")
            else:
                st.markdown(f"**{label}**")
            st.caption(f"{r['SIZE KB']} KB · {r.get('SOURCE', '')}")
            if data:
                st.download_button(
                    "Download", data, file_name=r["FILE NAME"],
                    mime=r.get("MIME") or "application/octet-stream",
                    key=f"{key_prefix}_{r['IMAGE ID']}", width="stretch")
            elif r.get("LINK"):
                st.markdown(f"[Open in Drive]({r['LINK']})")
            else:
                st.caption("File data unavailable.")


# ───────────────────────────── sidebar ─────────────────────────────
st.sidebar.markdown("### ASN / GRN Control")
st.sidebar.caption("Korber One · AX · EFL Warehouse")

try:
    _new_tabs = gsheets.ensure_missing_once()
    _users = gsheets.get_df("USER-M")
except Exception as e:
    st.sidebar.error("Could not connect to Google Sheets.")
    st.error(f"**Connection error**\n\n```\n{e}\n```\n\n"
             "Check that `gcp_service_account` and `app.spreadsheet_id` are set "
             "correctly in `.streamlit/secrets.toml`.")
    st.stop()

names = [n for n in _users["USER NAME"].astype(str) if n.strip()] \
    if not _users.empty else []
who = st.sidebar.selectbox("Operator", ["Select"] + names + ["Add a name"], index=0)
if who == "Add a name":
    who = st.sidebar.text_input("Name", value=SS.get("user", ""))
SS["user"] = "" if who == "Select" else who

with st.sidebar.expander("Admin"):
    pin = st.text_input("Admin PIN", type="password", key="pin_in")
    if st.button("Sign in", key="pin_btn"):
        s = gsheets.settings_dict()
        SS["role"] = "admin" if pin == str(s.get("ADMIN_PIN", "1234")) else "user"
        st.success("Signed in") if SS["role"] == "admin" else st.error("Wrong PIN")
if SS["role"] == "admin":
    st.sidebar.success("Admin mode")

if _new_tabs:
    st.sidebar.info("Created: " + ", ".join(_new_tabs))

PAGES = ["Dashboard", "ASN Upload", "Inventory", "Reconciliation",
         "ASN Register", "Search", "Discrepancies", "Email", "AX GRN",
         "Attachments", "Setup", "Data Manager", "Maintenance"]
page = st.sidebar.radio("Menu", PAGES, label_visibility="collapsed")

st.sidebar.markdown("---")
_st = gsheets.api_stats()
_bar = DANGER if _st["last_minute"] > _st["limit"] * .8 else "#4bb3a2"
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
        f"<a href='{_url}' target='_blank'>Open the Google Sheet</a></div>",
        unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
#  SETUP
# ═══════════════════════════════════════════════════════════════════
if page == "Setup":
    hero("Setup", "Sheets · matching rules · automation · attachments · API")

    t_sheets, t_rules, t_auto, t_files, t_api = st.tabs(
        ["Sheets", "Matching rules", "Automation", "Attachments", "API & quota"])

    with t_sheets:
        c1, c2 = st.columns([1, 2])
        with c1:
            if st.button("Create or update all sheets", type="primary"):
                with st.spinner("Working..."):
                    created, patched = gsheets.ensure_all()
                if created:
                    st.success("Created: " + ", ".join(created))
                if patched:
                    st.info("Headers updated: " + ", ".join(patched))
                if not created and not patched:
                    st.success("All sheets are already in place.")
        with c2:
            st.caption("Every tab is created automatically, including on first "
                       "write, so a missing sheet never causes an error.")
        show(gsheets.sheet_status())

    s = gsheets.settings_dict()

    with t_rules:
        with st.form("settings_rules"):
            a, b, c = st.columns(3)
            s["CLIENT_CODE"] = a.text_input("Client code", s.get("CLIENT_CODE", "HIES"))
            s["SITE"] = b.text_input("Site / warehouse", s.get("SITE", "EGDC"))
            s["COMPANY"] = c.text_input("Company", s.get("COMPANY", "EFL"))

            a, b, c, d = st.columns(4)
            s["QTY_TOLERANCE"] = str(a.number_input(
                "Quantity tolerance",
                value=gsheets.setting_float(s, "QTY_TOLERANCE"), step=1.0))
            s["STRIP_CLIENT_PREFIX"] = "Y" if b.checkbox(
                "Strip client prefix",
                gsheets.setting_bool(s, "STRIP_CLIENT_PREFIX")) else "N"
            s["CHECK_ITEM"] = "Y" if c.checkbox(
                "Check item", gsheets.setting_bool(s, "CHECK_ITEM")) else "N"
            s["CHECK_LOT"] = "Y" if d.checkbox(
                "Check lot", gsheets.setting_bool(s, "CHECK_LOT")) else "N"

            a, b = st.columns(2)
            s["CHECK_ASN_NO"] = "Y" if a.checkbox(
                "Check ASN number", gsheets.setting_bool(s, "CHECK_ASN_NO")) else "N"
            s["FLAG_EXTRA"] = "Y" if b.checkbox(
                "Flag extra HUs", gsheets.setting_bool(s, "FLAG_EXTRA")) else "N"

            a, b = st.columns(2)
            s["EMAIL_TO"] = a.text_input("Email To", s.get("EMAIL_TO", ""))
            s["EMAIL_CC"] = b.text_input("Email Cc", s.get("EMAIL_CC", ""))
            s["ADMIN_PIN"] = st.text_input("Admin PIN", s.get("ADMIN_PIN", "1234"))

            if st.form_submit_button("Save", type="primary"):
                gsheets.save_settings(s)
                st.success("Saved.")

    with t_auto:
        st.markdown("###### What happens when an inventory file is uploaded")
        st.caption("With all three enabled the upload is the only action needed: "
                   "reconciliation runs, tallied ASNs move to AX GRN Pending, and "
                   "the mismatch email is written to EMAIL_LOG.")
        with st.form("settings_auto"):
            s["AUTO_RECON"] = "Y" if st.checkbox(
                "Reconcile automatically on inventory upload",
                gsheets.setting_bool(s, "AUTO_RECON")) else "N"
            s["AUTO_PUSH_AX"] = "Y" if st.checkbox(
                "Move Korber GRN Done straight to AX GRN Pending",
                gsheets.setting_bool(s, "AUTO_PUSH_AX")) else "N"
            s["AUTO_EMAIL"] = "Y" if st.checkbox(
                "Generate the mismatch email automatically",
                gsheets.setting_bool(s, "AUTO_EMAIL")) else "N"
            if st.form_submit_button("Save", type="primary"):
                gsheets.save_settings(s)
                st.success("Saved.")

        st.markdown("###### Inventory merge rule")
        st.caption("Uploaded rows replace existing inventory rows with the same "
                   "**Invoice Number + Pallet**. Anything new is added. Rows not "
                   "present in the uploaded file are left untouched. When a row "
                   "has no invoice number the pallet alone identifies it.")

    with t_files:
        st.markdown("###### Where attachments are stored")
        a, b, c = st.columns(3)
        s["IMAGE_STORAGE"] = a.selectbox(
            "Storage", ["DRIVE", "SHEET"],
            index=0 if str(s.get("IMAGE_STORAGE", "DRIVE")).upper() == "DRIVE" else 1,
            help="DRIVE puts images and PDFs in the Drive folder below and falls "
                 "back to the sheet if that fails.")
        s["IMAGE_MAX_PX"] = str(int(b.number_input(
            "Max image px", value=gsheets.setting_float(s, "IMAGE_MAX_PX", 1400),
            min_value=400.0, max_value=4000.0, step=100.0)))
        s["IMAGE_QUALITY"] = str(int(c.number_input(
            "JPEG quality", value=gsheets.setting_float(s, "IMAGE_QUALITY", 78),
            min_value=40.0, max_value=95.0, step=1.0)))

        s["DRIVE_FOLDER_ID"] = st.text_input(
            "Drive folder — paste a link or an id", s.get("DRIVE_FOLDER_ID", ""))
        fid = drive.folder_id(s["DRIVE_FOLDER_ID"])
        if fid:
            st.caption(f"Folder id: `{fid}`")

        c1, c2 = st.columns([1, 3])
        if c1.button("Save", type="primary", key="save_files"):
            gsheets.save_settings(s)
            st.success("Saved.")
        if c2.button("Test the Drive connection"):
            ok, msg = drive.check_folder(s["DRIVE_FOLDER_ID"])
            st.success(msg) if ok else st.error(msg)

        st.markdown("---")
        st.markdown("###### Service account")
        st.code(drive.service_email() or "(not found in secrets)", language="text")
        st.caption("Share the Drive folder with this address as an **Editor**. A "
                   "service account has no storage quota of its own, so if uploads "
                   "return a quota error move the folder into a Shared Drive or set "
                   "Storage to SHEET. Either way an upload that fails falls back to "
                   "the sheet, so nothing is lost.")
        st.caption(f"Drive API: {'ready' if drive.available() else 'not installed'}"
                   f" · PDF: {'ready' if parsing.pdf_available() else 'not installed'}")

    with t_api:
        st.markdown("###### Google API usage")
        st.caption("The Sheets API allows about 60 requests per minute. The app "
                   "tracks that, waits when the limit is close, and retries quota "
                   "and server errors with exponential backoff.")

        stt = gsheets.api_stats()
        a, b, c, d = st.columns(4)
        used = stt["last_minute"]
        kpi(a, f'{used}/{stt["limit"]}', "Calls in the last minute",
            DANGER if used > stt["limit"] * .8 else ACCENT,
            f'{stt["headroom"]} remaining')
        kpi(b, stt["calls"], "Calls this session",
            note=f'last at {stt["last_call"] or "—"}')
        kpi(c, stt["retries"], "Retries", WARN if stt["retries"] else INK,
            f'{stt["throttled"]} throttled')
        kpi(d, stt["errors"], "Errors", DANGER if stt["errors"] else OK)

        if stt["last_error"]:
            st.error(f"Last error — {stt['last_error']}")

        with st.form("api_form"):
            a, b = st.columns(2)
            rate = a.number_input(
                "Rate limit — calls per minute",
                value=gsheets.setting_float(s, "API_RATE_LIMIT", 55),
                min_value=10.0, max_value=60.0, step=5.0)
            ttl = b.number_input(
                "Cache lifetime — seconds",
                value=gsheets.setting_float(s, "CACHE_TTL", 90),
                min_value=10.0, max_value=600.0, step=10.0)
            if st.form_submit_button("Save", type="primary"):
                s["API_RATE_LIMIT"] = str(int(rate))
                s["CACHE_TTL"] = str(int(ttl))
                gsheets.save_settings(s)
                gsheets.apply_api_settings(s)
                st.success("Saved.")

        c1, c2 = st.columns(2)
        if c1.button("Clear cache"):
            gsheets.refresh()
            st.success("Cache cleared.")
        if c2.button("Reset counters"):
            gsheets.api_reset_stats()
            st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  ASN UPLOAD
# ═══════════════════════════════════════════════════════════════════
elif page == "ASN Upload":
    hero("ASN Upload",
         "Excel or PDF — choose the sheet or table, confirm, then save "
         "summary, details and attachments")

    if not SS["user"]:
        st.warning("Choose an operator in the sidebar.")

    if not parsing.pdf_available():
        st.caption("Install `pdfplumber` to enable PDF support.")

    files = st.file_uploader("ASN files — Excel or PDF",
                             type=["xlsx", "xlsm", "xls", "pdf"],
                             accept_multiple_files=True, key="asn_up")

    if files:
        st.markdown("##### 1 · Choose the source")
        st.caption("Pick the worksheet for an Excel file, or the table for a PDF.")

        choices = {}
        for f in files:
            b = f.getvalue()
            if parsing.is_pdf(f.name, b):
                tables = parsing.list_pdf_tables(b)
                c1, c2 = st.columns([2, 3])
                c1.markdown(f"**{f.name}**")
                c1.caption(f"PDF · {parsing.pdf_page_count(b)} page(s)")
                if not tables:
                    c2.error("No table found. This is probably a scanned PDF with "
                             "no text layer — upload an Excel file instead, or "
                             "attach this PDF from the Attachments page.")
                    continue
                labels = [t["label"] for t in tables]
                sel = c2.selectbox(f"Table — {f.name}", labels, key=f"pt_{f.name}",
                                   label_visibility="collapsed")
                keys = [t["key"] for t in tables if t["label"] == sel]
                choices[f.name] = ("pdf", b, keys)
            else:
                sheets = parsing.list_sheets(b)
                if not sheets:
                    st.error(f"{f.name} — could not read any worksheet.")
                    continue
                c1, c2 = st.columns([2, 3])
                c1.markdown(f"**{f.name}**")
                c1.caption(f"Excel · {len(sheets)} sheet(s)")
                sel = c2.selectbox(f"Sheet — {f.name}", sheets, key=f"sh_{f.name}",
                                   label_visibility="collapsed")
                choices[f.name] = ("xlsx", b, sel)

        if st.button("Parse and preview", type="primary", disabled=not choices):
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

    if SS["parsed_asn"]:
        st.markdown("---")
        st.markdown("##### 2 · Review and confirm")

        total_rows, all_ok = 0, True
        for fname, p in SS["parsed_asn"].items():
            df, meta = p["df"], p["meta"]
            with st.expander(f"{fname}  ·  {p['sheet']}  ·  {len(df)} lines  ·  "
                             f"{len(p['images'])} image(s)", expanded=True):
                if meta.get("error"):
                    st.error(meta["error"])
                    all_ok = False
                    continue
                total_rows += len(df)
                asns = sorted({clean(a) for a in df["ASN_NO"] if clean(a)})
                a, b, c, d = st.columns(4)
                kpi(a, len(df), "ASN lines")
                kpi(b, df["HU_ID"].astype(str).str.strip().nunique(), "HU / pallets")
                kpi(c, fmt_num(df["QTY"].map(to_num).sum()), "Total quantity")
                kpi(d, len(asns), "ASN numbers")
                st.caption("ASN: " + ", ".join(f"`{x}`" for x in asns[:8]))
                st.caption(f"Header at {meta['header_row']} · "
                           f"{len(meta['mapped'])} columns mapped")
                if meta["unmapped"]:
                    st.caption("Unmapped columns (skipped): " +
                               ", ".join(meta["unmapped"][:12]))
                show(df.head(50))
                if p["images"]:
                    st.caption(f"{len(p['images'])} embedded image(s):")
                    cols = st.columns(min(5, len(p["images"])))
                    for i, im in enumerate(p["images"][:5]):
                        cols[i].image(im["data"],
                                      caption=f"{im['name']} ({im['size_kb']} KB)",
                                      width="stretch")

        st.markdown("##### 3 · Extra photos")
        extra_imgs = st.file_uploader(
            "Photos for this ASN — GRN sheet, damage, seal",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True, key="extra_img")
        all_asn = sorted({clean(a) for p in SS["parsed_asn"].values()
                          for a in p["df"].get("ASN_NO", []) if clean(a)})
        img_asn = st.selectbox("Attach these photos to", all_asn or ["—"],
                               key="img_asn") if extra_imgs else None

        st.markdown("##### 4 · Save")
        mode = str(gsheets.settings_dict().get("IMAGE_STORAGE", "DRIVE")).upper()
        c1, c2, c3 = st.columns([2, 1, 1])
        targets = c1.multiselect("Save to", ["ASN_SUMMARY", "ASN_DETAIL"],
                                 default=["ASN_SUMMARY", "ASN_DETAIL"])
        up_img = c2.checkbox("Save images", value=True)
        keep_pdf = c3.checkbox("Attach the PDF", value=True,
                               help="Stores the uploaded PDF itself against the ASN.")
        st.caption(f"Attachments go to **{mode}**"
                   + (" — falling back to the sheet if Drive is unavailable."
                      if mode == "DRIVE" else "."))

        confirm = st.checkbox(
            f"Confirm — save {total_rows} line(s) to "
            f"{', '.join(targets) or 'nothing'}.")

        cA, cB = st.columns([1, 1])
        if cA.button("Save to Google Sheet", type="primary",
                     disabled=not (confirm and targets and all_ok)):
            ts = now_str()
            user = SS["user"] or "unknown"
            det_rows, img_rows = [], []

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
                        "PO NUMBER": clean(r["PO_NUMBER"]),
                        "PO LINE": clean(r["PO_LINE"]),
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

                file_asn = clean(df["ASN_NO"].iloc[0]) if len(df) else ""
                if up_img:
                    for im in p["images"]:
                        img_rows.append((file_asn, im["name"],
                                         "PDF EMBEDDED" if p.get("kind") == "pdf"
                                         else "EXCEL EMBEDDED",
                                         im["mime"], im["data"]))
                if keep_pdf and p.get("kind") == "pdf" and p.get("raw"):
                    img_rows.append((file_asn, fname, "ASN DOCUMENT",
                                     "application/pdf", p["raw"]))

            if extra_imgs and up_img and img_asn and img_asn != "—":
                for uf in extra_imgs:
                    img_rows.append((img_asn, uf.name, "MANUAL UPLOAD",
                                     uf.type or "image/png", uf.getvalue()))

            det = pd.DataFrame(det_rows).reindex(
                columns=schema.ASN_DETAIL_HEADERS).fillna("")

            with st.spinner("Writing to the Google Sheet..."):
                if "ASN_DETAIL" in targets and not det.empty:
                    a, u = gsheets.upsert("ASN_DETAIL", det.to_dict("records"))
                    st.success(f"ASN_DETAIL — {a} added, {u} updated")

                if "ASN_SUMMARY" in targets and not det.empty:
                    summ = matching.summarise_asn(det)
                    summ["AX GRN"] = schema.AX_NA
                    summ["OVERALL"] = schema.S_GRN_PENDING
                    summ["STATUS"] = schema.S_NEW
                    a, u = gsheets.upsert("ASN_SUMMARY", summ.to_dict("records"))
                    st.success(f"ASN_SUMMARY — {a} added, {u} updated")

                if img_rows:
                    ok_n, errs = 0, []
                    prog = st.progress(0.0, text="Saving attachments...")
                    for i, (asn, nm, src, mime, data) in enumerate(img_rows):
                        ok, msg = images.save_image(asn, nm, data, mime,
                                                    source=src, user=user)
                        if ok:
                            ok_n += 1
                            if msg and "failed" in msg.lower():
                                errs.append(msg)
                        else:
                            errs.append(msg)
                        prog.progress((i + 1) / len(img_rows),
                                      text=f"Attachment {i + 1} of {len(img_rows)}")
                    prog.empty()
                    if ok_n:
                        st.success(f"{ok_n} attachment(s) saved")
                    for e_ in errs[:6]:
                        st.warning(e_)

            SS["parsed_asn"] = {}
            st.info("Next: upload the Korber inventory — reconciliation runs "
                    "automatically from there.")
            st.balloons()

        if cB.button("Clear"):
            SS["parsed_asn"] = {}
            st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  INVENTORY  — upload triggers the whole automatic flow
# ═══════════════════════════════════════════════════════════════════
elif page == "Inventory":
    hero("Korber Inventory",
         "Upload the inventory and everything downstream runs on its own")

    s = gsheets.settings_dict()
    auto_recon = gsheets.setting_bool(s, "AUTO_RECON")
    auto_push = gsheets.setting_bool(s, "AUTO_PUSH_AX")
    auto_mail = gsheets.setting_bool(s, "AUTO_EMAIL")

    st.caption(
        f"Merge rule: rows with the same **Invoice Number + Pallet** are "
        f"replaced, new rows are added. "
        f"Automation — reconcile: {'on' if auto_recon else 'off'} · "
        f"AX push: {'on' if auto_push else 'off'} · "
        f"email: {'on' if auto_mail else 'off'} (Setup → Automation).")

    f = st.file_uploader("Inventory file", type=["xlsx", "xlsm", "xls"],
                         key="inv_up")

    if f:
        b = f.getvalue()
        sheets = parsing.list_sheets(b)
        sheet = st.selectbox("Worksheet", sheets, key="inv_sheet")

        if st.button("Upload and reconcile", type="primary"):
            df, meta = parsing.parse_inventory(b, sheet)
            if meta.get("error"):
                st.error(meta["error"])
            else:
                note = f"{f.name} · {sheet} · {len(df)} rows"
                SS["inv_df"] = df
                SS["inv_note"] = f"{note} · {now_str()}"

                with st.spinner("Merging inventory..."):
                    merge = pipeline.merge_inventory(df)
                st.success(
                    f"Inventory merged — {merge['replaced']} row(s) replaced, "
                    f"{merge['added']} added, {merge['total']} in total.")

                if auto_recon:
                    with st.spinner("Reconciling..."):
                        full = pipeline.from_sheet_rows(gsheets.get_df("INVENTORY"))
                        res = pipeline.auto_reconcile(
                            full, cfg_recon(),
                            user=SS["user"] or "auto",
                            note=note, push_ax=auto_push,
                            make_email=auto_mail, settings=s)
                    SS["auto"] = res
                    if res.get("email"):
                        SS["email"] = res["email"]
                else:
                    SS["auto"] = None
                    st.info("Automatic reconciliation is switched off. Run it from "
                            "the Reconciliation page.")
                st.rerun()

    # ── result of the automatic run ──
    R = SS.get("auto")
    if R and not R.get("skipped"):
        stt = R["stats"]
        st.markdown("---")
        st.markdown(f"##### Automatic reconciliation · `{R['run_id']}`")

        a, b, c, d, e = st.columns(5)
        kpi(a, stt["lines"], "Lines checked")
        kpi(b, stt["matched"], "Tallied — Korber GRN Done", ACCENT)
        kpi(c, stt["missing"], "GRN not done", WARN)
        kpi(d, stt["mismatch"], "Mismatched", DANGER)
        kpi(e, stt["extra"], "Extra in inventory", INFO)

        c1, c2, c3 = st.columns(3)
        kpi(c1, len(R["ax_pushed"]), "Moved to AX GRN Pending", INFO,
            ", ".join(R["ax_pushed"][:3]) or "none")
        kpi(c2, len(R["resolved"]), "Discrepancies auto-resolved", OK,
            "previously open, now tallying")
        kpi(c3, len(R["discrepancies"]), "Discrepancies outstanding",
            DANGER if len(R["discrepancies"]) else OK)

        if R["resolved"]:
            with st.expander(f"Auto-resolved ({len(R['resolved'])})"):
                st.write(", ".join(R["resolved"][:80]))

        tabs = st.tabs(["ASN summary", "Mismatches", "Missing", "Tallied"])
        cols = ["ASN NO", "ASN LINE", "HU ID", "ITEM NUMBER", "LOT NUMBER", "QTY",
                "INV QTY", "QTY DIFF", "MATCH STATUS", "INV GRN NO", "DISCREPANCY"]
        det, stc = R["detail"], R["detail"]["MATCH STATUS"].astype(str)
        with tabs[0]:
            show(R["summary"][["ASN NO", "TOTAL LINES", "TOTAL QTY", "MATCHED LINES",
                               "MISSING LINES", "MISMATCH LINES", "EXTRA LINES",
                               "RECEIVED QTY", "QTY DIFF", "STATUS", "KORBER GRN"]])
        with tabs[1]:
            mm = det[~stc.isin([schema.M_MATCHED, schema.M_MISSING])][cols]
            if not R["extra"].empty:
                mm = pd.concat([mm, R["extra"][cols]], ignore_index=True)
            show(mm)
        with tabs[2]:
            show(det[stc == schema.M_MISSING][cols])
        with tabs[3]:
            show(det[stc == schema.M_MATCHED][cols])

        if R.get("email"):
            st.markdown("##### Mismatch email")
            st.caption("Generated automatically and stored in EMAIL_LOG.")
            st.code(R["email"]["subject"], language="text")
            with st.expander("Markdown body", expanded=False):
                st.code(R["email"]["md"], language="markdown")
            st.download_button(
                "Download the email", R["email"]["md"].encode("utf-8"),
                file_name=f"mismatch_email_{date.today():%Y%m%d}.md",
                mime="text/markdown")
        elif not R["discrepancies"].empty:
            st.info("Discrepancies were found but the automatic email is switched "
                    "off. Generate it from the Email page.")
        else:
            st.success("Everything tallied — no mismatch email needed.")

    elif R and R.get("skipped"):
        st.info("Inventory merged. No open ASN lines were available to reconcile.")

    st.markdown("---")
    st.markdown("##### Inventory currently held")
    inv_sheet = gsheets.get_df("INVENTORY")
    if inv_sheet.empty:
        st.info("No inventory stored yet.")
    else:
        a, b, c, d = st.columns(4)
        kpi(a, len(inv_sheet), "Rows")
        kpi(b, inv_sheet["PALLET"].nunique(), "Pallets")
        kpi(c, inv_sheet["INVOICE NUMBER"].nunique(), "Invoices")
        kpi(d, fmt_num(inv_sheet["ACTUAL QTY"].map(to_num).sum()), "Total quantity")
        show(inv_sheet.head(300))


# ═══════════════════════════════════════════════════════════════════
#  RECONCILIATION  (manual re-run)
# ═══════════════════════════════════════════════════════════════════
elif page == "Reconciliation":
    hero("Reconciliation",
         "Re-run the match manually — normally this happens on inventory upload")

    det_all = gsheets.get_df("ASN_DETAIL")
    if det_all.empty:
        st.warning("No ASN lines yet. Start from ASN Upload.")
        st.stop()

    src = st.radio("Inventory source",
                   ["Stored INVENTORY sheet", "The file uploaded in this session"],
                   horizontal=True)
    if src.startswith("Stored"):
        inv = pipeline.from_sheet_rows(gsheets.get_df("INVENTORY"))
        note = f"INVENTORY sheet · {len(inv)} rows"
        if inv.empty:
            st.warning("The INVENTORY sheet is empty.")
            st.stop()
    else:
        inv = SS.get("inv_df")
        note = SS.get("inv_note", "")
        if inv is None or inv.empty:
            st.warning("No inventory uploaded in this session.")
            st.stop()
    st.caption(note)

    summ_all = gsheets.get_df("ASN_SUMMARY")
    done = set(summ_all.loc[summ_all["OVERALL"] == schema.S_COMPLETE, "ASN NO"]) \
        if not summ_all.empty else set()
    asn_opts = sorted({clean(a) for a in det_all["ASN NO"] if clean(a)})
    picked = st.multiselect("ASNs to check", asn_opts,
                            default=[a for a in asn_opts if a not in done])

    c1, c2, c3 = st.columns(3)
    push = c1.checkbox("Move tallied ASNs to AX GRN Pending", value=True)
    mail = c2.checkbox("Generate the mismatch email", value=True)
    c3.caption("Results are written to the sheets straight away.")

    if st.button("Run reconciliation", type="primary", disabled=not picked):
        with st.spinner("Reconciling..."):
            res = pipeline.auto_reconcile(
                inv, cfg_recon(), user=SS["user"] or "manual",
                asns=picked, note=note, push_ax=push, make_email=mail)
        SS["recon"] = res
        if res.get("email"):
            SS["email"] = res["email"]
        st.rerun()

    R = SS.get("recon")
    if R and not R.get("skipped"):
        stt = R["stats"]
        st.markdown("---")
        st.markdown(f"##### Result · `{R['run_id']}`")
        a, b, c, d, e = st.columns(5)
        kpi(a, stt["lines"], "Lines checked")
        kpi(b, stt["matched"], "Tallied", ACCENT)
        kpi(c, stt["missing"], "GRN not done", WARN)
        kpi(d, stt["mismatch"], "Mismatched", DANGER)
        kpi(e, stt["extra"], "Extra in inventory", INFO)

        if R["resolved"]:
            st.success(f"{len(R['resolved'])} previously open discrepancy(ies) "
                       f"now tally and were closed automatically.")
        if R["ax_pushed"]:
            st.info("Moved to AX GRN Pending: " + ", ".join(R["ax_pushed"]))

        det, stc = R["detail"], R["detail"]["MATCH STATUS"].astype(str)
        cols = ["ASN NO", "ASN LINE", "HU ID", "ITEM NUMBER", "LOT NUMBER", "QTY",
                "INV QTY", "QTY DIFF", "MATCH STATUS", "INV GRN NO",
                "INV LOCATION", "DISCREPANCY"]
        tabs = st.tabs(["Tallied", "Missing", "Mismatched", "Extra", "All",
                        "ASN summary"])
        with tabs[0]:
            show(det[stc == schema.M_MATCHED][cols])
        with tabs[1]:
            show(det[stc == schema.M_MISSING][cols])
        with tabs[2]:
            show(det[~stc.isin([schema.M_MATCHED, schema.M_MISSING])][cols])
        with tabs[3]:
            show(R["extra"][cols] if not R["extra"].empty else R["extra"])
        with tabs[4]:
            show(det[cols])
        with tabs[5]:
            show(R["summary"][["ASN NO", "TOTAL LINES", "TOTAL QTY", "MATCHED LINES",
                               "MISSING LINES", "MISMATCH LINES", "EXTRA LINES",
                               "RECEIVED QTY", "QTY DIFF", "STATUS", "KORBER GRN",
                               "KORBER GRN NO"]])


# ═══════════════════════════════════════════════════════════════════
#  ASN REGISTER
# ═══════════════════════════════════════════════════════════════════
elif page == "ASN Register":
    hero("ASN Register", "Summary and line details — filter, inspect, export")

    summ = gsheets.get_df("ASN_SUMMARY")
    det = gsheets.get_df("ASN_DETAIL")
    if summ.empty:
        st.info("No ASN records yet.")
        st.stop()

    c1, c2, c3 = st.columns(3)
    f_status = c1.multiselect("Status", sorted({s for s in summ["STATUS"] if s}))
    f_korber = c2.multiselect("Korber GRN", sorted({s for s in summ["KORBER GRN"] if s}))
    f_asn = c3.text_input("Search an ASN")

    v = summ.copy()
    if f_status:
        v = v[v["STATUS"].isin(f_status)]
    if f_korber:
        v = v[v["KORBER GRN"].isin(f_korber)]
    if f_asn.strip():
        v = v[v["ASN NO"].astype(str).str.contains(f_asn.strip(), case=False, na=False)]

    a, b, c, d = st.columns(4)
    kpi(a, len(v), "ASNs")
    kpi(b, fmt_num(v["TOTAL QTY"].map(to_num).sum()), "Expected quantity")
    kpi(c, int(v["MATCHED LINES"].map(to_num).sum()), "Tallied lines", ACCENT)
    kpi(d, int(v["MISSING LINES"].map(to_num).sum()
               + v["MISMATCH LINES"].map(to_num).sum()), "Lines with issues", DANGER)

    st.markdown("##### Summary")
    show(v)

    st.markdown("##### Details")
    pick = st.selectbox("Select an ASN", ["All ASNs"] + list(v["ASN NO"].astype(str)))
    d2 = det if pick == "All ASNs" else det[det["ASN NO"].astype(str) == pick]

    if pick != "All ASNs":
        row = v[v["ASN NO"].astype(str) == pick]
        if not row.empty:
            r = row.iloc[0]
            st.markdown(
                f"**{pick}** &nbsp; {pill(r['STATUS'] or schema.S_NEW)} &nbsp; "
                f"Korber GRN `{r['KORBER GRN']}` &nbsp; AX GRN `{r['AX GRN']}` &nbsp; "
                f"{pill(r['OVERALL'] or schema.S_GRN_PENDING)}",
                unsafe_allow_html=True)
    show(d2)

    if pick != "All ASNs":
        st.markdown("##### Attachments")
        attachment_block(pick, "reg")

    st.download_button(
        "Download the register",
        reporting.build_excel({"Summary": v, "Details": d2}),
        file_name=f"ASN_Register_{date.today():%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    if pick != "All ASNs":
        with st.expander(f"Delete {pick}"):
            if SS["role"] != "admin":
                st.caption("Admin only — sign in from the sidebar. Full options "
                           "are on the Maintenance page.")
            else:
                st.caption("Removes the summary, details, discrepancies, AX GRN "
                           "entry and attachments.")
                t = st.text_input("Type DELETE to confirm", key="reg_del")
                if st.button("Delete", disabled=t.strip().upper() != "DELETE"):
                    res = {}
                    for k in ("ASN_SUMMARY", "ASN_DETAIL", "DISCREPANCY", "AX_GRN"):
                        res[k] = gsheets.delete_where(k, "ASN NO", [pick])
                    res["ASN_IMAGES"] = images.delete_for_asn([pick])
                    st.success("Deleted — " +
                               ", ".join(f"{k}: {n}" for k, n in res.items()))
                    st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  SEARCH
# ═══════════════════════════════════════════════════════════════════
elif page == "Search":
    hero("Search", "Find any HU, ASN, item, lot, PO, GRN or vendor")

    SEARCHABLE = ["ASN_DETAIL", "ASN_SUMMARY", "INVENTORY", "DISCREPANCY",
                  "AX_GRN", "ASN_IMAGES", "RECON_LOG", "EMAIL_LOG"]

    c1, c2 = st.columns([3, 2])
    term = c1.text_input("Search", placeholder="ETHT0726 · 26AUG_UPPD_40659 · GRN-40196",
                         key="q_term")
    where = c2.multiselect("Sheets", SEARCHABLE,
                           default=["ASN_DETAIL", "ASN_SUMMARY", "INVENTORY"])

    c1, c2, c3 = st.columns(3)
    exact = c1.checkbox("Exact match", value=False)
    case = c2.checkbox("Case sensitive", value=False)
    limit = int(c3.number_input("Max results per sheet", value=300.0,
                                min_value=20.0, max_value=3000.0, step=50.0))

    with st.expander("Filter by a specific column"):
        adv_sheet = st.selectbox("Sheet", ["None"] + SEARCHABLE, key="adv_sh")
        adv_col, adv_val = "None", ""
        if adv_sheet != "None":
            adv_col = st.selectbox("Column",
                                   ["None"] + schema.SHEETS[adv_sheet]["headers"],
                                   key="adv_col")
            adv_val = st.text_input("Value", key="adv_val")

    if not term.strip() and adv_sheet == "None":
        st.info("Type something to search. HU ids, ASN numbers, items, lots, GRN "
                "numbers and vendors all work.")
    else:
        q = term.strip()
        total, tabs_data = 0, []
        targets = list(where) if q else []
        if adv_sheet != "None" and adv_sheet not in targets:
            targets.append(adv_sheet)

        for key in targets:
            df = gsheets.get_df(key)
            if df.empty:
                continue
            v = df
            if q:
                sdf = v.astype(str)
                if exact:
                    m = sdf.apply(lambda col: col.str.strip().str.lower() == q.lower()
                                  if not case else col.str.strip() == q)
                else:
                    m = sdf.apply(lambda col: col.str.contains(q, case=case,
                                                               regex=False, na=False))
                v = v[m.any(axis=1)]
            if adv_sheet == key and adv_col != "None" and adv_val.strip():
                v = v[v[adv_col].astype(str).str.contains(adv_val.strip(), case=case,
                                                          regex=False, na=False)]
            if not v.empty:
                total += len(v)
                tabs_data.append((key, v.head(limit)))

        if not tabs_data:
            st.warning(f"Nothing found for `{q or adv_val}`.")
        else:
            st.success(f"{total} result(s) across {len(tabs_data)} sheet(s)")
            tabs = st.tabs([f"{k} ({len(v)})" for k, v in tabs_data])
            for t, (k, v) in zip(tabs, tabs_data):
                with t:
                    if q:
                        hit_cols = [c for c in v.columns
                                    if v[c].astype(str).str.contains(
                                        q, case=case, regex=False, na=False).any()]
                        if hit_cols:
                            st.caption("Matched in: " +
                                       ", ".join(f"`{c}`" for c in hit_cols[:10]))
                    show(v, height=460)

            st.download_button(
                "Download the results",
                reporting.build_excel({k[:31]: v for k, v in tabs_data}),
                file_name=f"Search_{date.today():%Y%m%d}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            asn_hits = set()
            for k, v in tabs_data:
                if "ASN NO" in v.columns:
                    asn_hits |= {clean(a) for a in v["ASN NO"] if clean(a)}
            if asn_hits:
                st.caption("ASNs found: " +
                           ", ".join(f"`{a}`" for a in sorted(asn_hits)[:12]))


# ═══════════════════════════════════════════════════════════════════
#  DISCREPANCIES
# ═══════════════════════════════════════════════════════════════════
elif page == "Discrepancies":
    hero("Discrepancies", "Everything that did not tally, summarised and by line")

    disc = gsheets.get_df("DISCREPANCY")
    if disc.empty:
        st.success("No discrepancies on record.")
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    f_run = c1.selectbox("Reconciliation run", ["All runs"] +
                         sorted({r for r in disc["RUN ID"] if r}, reverse=True))
    f_type = c2.multiselect("Type", sorted({t for t in disc["DISCREPANCY TYPE"] if t}))
    f_sev = c3.multiselect("Severity", sorted({s for s in disc["SEVERITY"] if s}))
    statuses = sorted({s for s in disc["STATUS"] if s})
    f_st = c4.multiselect("Status", statuses,
                          default=[schema.D_OPEN] if schema.D_OPEN in statuses else [])

    v = disc.copy()
    if f_run != "All runs":
        v = v[v["RUN ID"] == f_run]
    if f_type:
        v = v[v["DISCREPANCY TYPE"].isin(f_type)]
    if f_sev:
        v = v[v["SEVERITY"].isin(f_sev)]
    if f_st:
        v = v[v["STATUS"].isin(f_st)]

    a, b, c, d = st.columns(4)
    kpi(a, len(v), "Discrepancy lines", DANGER)
    kpi(b, v["ASN NO"].nunique(), "ASNs affected")
    kpi(c, int((v["SEVERITY"] == "HIGH").sum()), "High severity", DANGER)
    kpi(d, int((disc["STATUS"] == schema.D_RESOLVED).sum()),
        "Auto-resolved to date", OK)

    st.markdown("##### Summary")
    g = (v.assign(_a=v["ASN QTY"].map(to_num), _i=v["INV QTY"].map(to_num))
           .groupby(["ASN NO", "DISCREPANCY TYPE"])
           .agg(Lines=("DISC ID", "count"), ASN_Qty=("_a", "sum"),
                INV_Qty=("_i", "sum")).reset_index())
    g["Qty_Diff"] = g["INV_Qty"] - g["ASN_Qty"]
    show(g)

    st.markdown("##### Line details")
    dcols = ["ASN NO", "ASN LINE", "HU ID", "ITEM NUMBER", "LOT NUMBER", "ASN QTY",
             "INV QTY", "QTY DIFF", "DISCREPANCY TYPE", "SEVERITY", "DETAIL",
             "STATUS", "GENERATED AT", "RUN ID"]
    show(v[dcols])

    st.download_button(
        "Download the discrepancy report",
        reporting.build_excel({"Summary": g, "Details": v[dcols]}),
        file_name=f"Discrepancy_{date.today():%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary")

    st.caption("Discrepancies close themselves when a later inventory upload makes "
               "the line tally. Close one here only when it is settled another way.")
    with st.expander("Close manually"):
        ids = st.multiselect("Discrepancy id", list(v["DISC ID"]))
        note = st.text_input("Note")
        if st.button("Close", disabled=not ids):
            full = gsheets.get_df("DISCREPANCY")
            m = full["DISC ID"].isin(ids)
            full.loc[m, "STATUS"] = schema.D_CLOSED
            full.loc[m, "ACTION BY"] = SS["user"] or "unknown"
            full.loc[m, "CLOSED AT"] = now_str()
            full.loc[m, "NOTE"] = note
            gsheets.overwrite("DISCREPANCY", full)
            st.success(f"{len(ids)} closed.")
            st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  EMAIL
# ═══════════════════════════════════════════════════════════════════
elif page == "Email":
    hero("Discrepancy Email", "Detailed Markdown, ready to copy into your mail client")

    disc = gsheets.get_df("DISCREPANCY")
    summ = gsheets.get_df("ASN_SUMMARY")
    s = gsheets.settings_dict()

    log = gsheets.get_df("EMAIL_LOG")
    if not log.empty:
        with st.expander(f"Previously generated emails ({len(log)})"):
            show(log[["EMAIL ID", "GENERATED AT", "GENERATED BY", "ASN LIST",
                      "SUBJECT"]])
            pick_id = st.selectbox("Reopen", ["None"] + list(log["EMAIL ID"]))
            if pick_id != "None":
                row = log[log["EMAIL ID"] == pick_id].iloc[0]
                st.code(row["SUBJECT"], language="text")
                st.code(row["BODY MD"], language="markdown")

    if disc.empty:
        st.info("No discrepancies, so there is nothing to report.")
        st.stop()

    c1, c2 = st.columns(2)
    runs = ["All runs"] + sorted({r for r in disc["RUN ID"] if r}, reverse=True)
    f_run = c1.selectbox("Reconciliation run", runs)
    only_open = c2.checkbox("Open items only", value=True)

    v = disc.copy()
    if f_run != "All runs":
        v = v[v["RUN ID"] == f_run]
    if only_open:
        v = v[v["STATUS"] == schema.D_OPEN]

    asn_opts = sorted({a for a in v["ASN NO"] if a})
    picked = st.multiselect("ASNs", asn_opts, default=asn_opts)
    v = v[v["ASN NO"].isin(picked)]

    c1, c2 = st.columns(2)
    to = c1.text_input("To", s.get("EMAIL_TO", ""))
    cc = c2.text_input("Cc", s.get("EMAIL_CC", ""))

    if st.button("Generate", type="primary", disabled=v.empty):
        sm = summ[summ["ASN NO"].isin(picked)] if not summ.empty else pd.DataFrame()
        subject, md = reporting.discrepancy_email(
            sm, v, company=s.get("COMPANY", "EFL"), site=s.get("SITE", ""),
            client=s.get("CLIENT_CODE", ""), prepared_by=SS["user"] or "",
            to=to, cc=cc, run_id="" if f_run == "All runs" else f_run,
            inventory_note=SS.get("inv_note", ""))
        SS["email"] = {"subject": subject, "md": md, "asns": picked,
                       "to": to, "cc": cc}

    E = SS.get("email")
    if E:
        st.markdown("##### Subject")
        st.code(E["subject"], language="text")
        st.markdown("##### Body")
        st.code(E["md"], language="markdown")

        c1, c2 = st.columns(2)
        c1.download_button("Download as .md", E["md"].encode("utf-8"),
                           file_name=f"discrepancy_email_{date.today():%Y%m%d}.md",
                           mime="text/markdown")
        if c2.button("Save to EMAIL_LOG"):
            gsheets.append_rows("EMAIL_LOG", [[
                uuid.uuid4().hex[:10].upper(), now_str(), SS["user"] or "unknown",
                ", ".join(E["asns"][:20]), E["subject"], E.get("to", ""),
                E.get("cc", ""), E["md"][:45000]]])
            st.success("Saved.")

        with st.expander("Rendered preview"):
            st.markdown(E["md"])


# ═══════════════════════════════════════════════════════════════════
#  AX GRN
# ═══════════════════════════════════════════════════════════════════
elif page == "AX GRN":
    hero("AX GRN",
         "Korber GRN done → AX GRN pending → AX GRN done → fully complete")

    ax = gsheets.get_df("AX_GRN")
    if ax.empty:
        st.info("The AX queue is empty. ASNs arrive here automatically once every "
                "line tallies against the Korber inventory.")
        st.stop()

    pend = ax[ax["AX GRN"] != schema.AX_DONE]
    done = ax[ax["AX GRN"] == schema.AX_DONE]
    counts = pipeline.attachment_counts()

    a, b, c = st.columns(3)
    kpi(a, len(pend), "Awaiting AX GRN", INFO)
    kpi(b, len(done), "AX GRN done", OK)
    kpi(c, fmt_num(pend["TOTAL QTY"].map(to_num).sum()), "Quantity pending")

    st.markdown("##### Awaiting AX GRN")
    if pend.empty:
        st.success("Nothing pending.")
    else:
        view = pend.copy()
        view["ATTACHMENTS"] = view["ASN NO"].astype(str).map(
            lambda a_: counts.get(str(a_).strip(), 0))
        show(view[["ASN NO", "CLIENT CODE", "KORBER GRN NO", "KORBER GRN DATE",
                   "TOTAL LINES", "TOTAL QTY", "ATTACHMENTS", "PUSHED AT",
                   "PUSHED BY", "REMARK"]])

        st.markdown("##### Documents for an ASN")
        st.caption("Download the ASN document or the photos before posting the GRN "
                   "into AX.")
        doc_asn = st.selectbox("ASN", list(pend["ASN NO"].astype(str)), key="ax_doc")
        attachment_block(doc_asn, "ax")

        st.markdown("##### Mark as done in AX")
        c1, c2, c3 = st.columns([2, 1, 1])
        sel = c1.multiselect("ASNs", list(pend["ASN NO"].astype(str)))
        ax_no = c2.text_input("AX GRN number", "")
        ax_dt = c3.date_input("AX GRN date", value=date.today())

        if st.button("Mark AX GRN done", type="primary", disabled=not sel):
            ts, user = now_str(), SS["user"] or "unknown"
            m = ax["ASN NO"].astype(str).isin(sel)
            ax.loc[m, "AX GRN"] = schema.AX_DONE
            ax.loc[m, "AX GRN NO"] = ax_no
            ax.loc[m, "AX GRN DATE"] = str(ax_dt)
            ax.loc[m, "AX GRN BY"] = user
            ax.loc[m, "OVERALL"] = schema.S_COMPLETE
            gsheets.overwrite("AX_GRN", ax)

            summ = gsheets.get_df("ASN_SUMMARY")
            ms = summ["ASN NO"].astype(str).isin(sel)
            summ.loc[ms, "AX GRN"] = schema.AX_DONE
            summ.loc[ms, "AX GRN NO"] = ax_no
            summ.loc[ms, "AX GRN DATE"] = str(ax_dt)
            summ.loc[ms, "AX GRN BY"] = user
            summ.loc[ms, "OVERALL"] = schema.S_COMPLETE
            summ.loc[ms, "STATUS"] = schema.S_COMPLETE
            gsheets.overwrite("ASN_SUMMARY", summ)

            det = gsheets.get_df("ASN_DETAIL")
            md = det["ASN NO"].astype(str).isin(sel)
            det.loc[md, "AX GRN"] = schema.AX_DONE
            det.loc[md, "REMARK"] = f"AX GRN done {ts}"
            gsheets.overwrite("ASN_DETAIL", det)

            st.success(f"{len(sel)} ASN(s) are now fully complete.")
            st.balloons()
            st.rerun()

    st.markdown("##### Fully complete")
    show(done)

    st.download_button("Download the AX queue",
                       reporting.build_excel({"Pending": pend, "Completed": done}),
                       file_name=f"AX_GRN_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ═══════════════════════════════════════════════════════════════════
#  ATTACHMENTS
# ═══════════════════════════════════════════════════════════════════
elif page == "Attachments":
    hero("Attachments", "Photos and PDF documents held against each ASN")

    meta = gsheets.get_df("ASN_IMAGES")
    summ = gsheets.get_df("ASN_SUMMARY")
    asn_opts = sorted({a for a in summ["ASN NO"] if a}) if not summ.empty else []
    if not asn_opts and not meta.empty:
        asn_opts = sorted({a for a in meta["ASN NO"] if a})

    mode = str(gsheets.settings_dict().get("IMAGE_STORAGE", "DRIVE")).upper()
    st.caption(f"Storage: **{mode}** — change it in Setup → Attachments.")

    with st.expander("Add an attachment", expanded=meta.empty):
        c1, c2 = st.columns([1, 2])
        asn = c1.selectbox("ASN", asn_opts or ["—"])
        note = c2.text_input("Note", "")
        ups = st.file_uploader("Images or PDF",
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
                    if msg and "failed" in msg.lower():
                        errs.append(msg)
                else:
                    errs.append(msg)
            if ok_n:
                st.success(f"{ok_n} saved.")
            for e_ in errs:
                st.warning(e_)
            if ok_n:
                st.rerun()

    if meta.empty:
        st.info("No attachments yet.")
    else:
        c1, c2 = st.columns([2, 1])
        f = c1.selectbox("ASN filter", ["All ASNs"] +
                         sorted({a for a in meta["ASN NO"] if a}))
        kinds = sorted({k for k in meta["KIND"] if str(k).strip()}) or ["IMAGE"]
        kf = c2.multiselect("Kind", kinds, default=kinds)

        v = meta if f == "All ASNs" else meta[meta["ASN NO"] == f]
        if kf:
            v = v[v["KIND"].isin(kf) | (v["KIND"].astype(str).str.strip() == "")]

        a, b, c = st.columns(3)
        kpi(a, len(v), "Attachments")
        kpi(b, v["ASN NO"].nunique(), "ASNs covered")
        kpi(c, f'{fmt_num(v["SIZE KB"].map(to_num).sum())} KB', "Total size")

        show(v[["IMAGE ID", "ASN NO", "FILE NAME", "KIND", "SOURCE", "SIZE KB",
                "STORAGE", "UPLOADED AT", "UPLOADED BY", "NOTE"]])

        if f != "All ASNs":
            st.markdown("##### Preview")
            attachment_block(f, "att")

        with st.expander("Delete"):
            ids = st.multiselect("Attachment id", list(v["IMAGE ID"]))
            if st.button("Delete", disabled=not ids):
                n = images.delete_images(ids)
                st.success(f"{n} deleted.")
                st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════════════
elif page == "Dashboard":
    summ = gsheets.get_df("ASN_SUMMARY")
    det = gsheets.get_df("ASN_DETAIL")
    disc = gsheets.get_df("DISCREPANCY")
    log = gsheets.get_df("RECON_LOG")

    last = clean(log["RUN AT"].iloc[-1]) if not log.empty else "not yet run"
    hero("Dashboard", f"ASN → Korber GRN → AX GRN · last reconciliation {last}")

    if summ.empty:
        st.info("No data yet. Start from ASN Upload.")
        st.stop()

    n_asn = len(summ)
    n_korber = int((summ["KORBER GRN"] == schema.K_DONE).sum())
    n_axp = int((summ["AX GRN"] == schema.AX_PENDING).sum())
    n_comp = int((summ["OVERALL"] == schema.S_COMPLETE).sum())
    n_open = int((disc["STATUS"] == schema.D_OPEN).sum()) if not disc.empty else 0
    n_res = int((disc["STATUS"] == schema.D_RESOLVED).sum()) if not disc.empty else 0
    asn_qty = summ["TOTAL QTY"].map(to_num).sum()
    rec_qty = summ["RECEIVED QTY"].map(to_num).sum()
    lines = len(det)
    tally = int((det["MATCH STATUS"] == schema.M_MATCHED).sum()) if not det.empty else 0
    rate = (tally / lines * 100) if lines else 0
    pending = n_asn - n_comp

    a, b, c, d = st.columns(4)
    kpi(a, n_asn, "ASNs in the system",
        note=f"{lines} lines · {fmt_num(asn_qty)} expected")
    kpi(b, f"{rate:.0f}%", "Lines tallied", ACCENT, f"{tally} of {lines}")
    kpi(c, n_open, "Open discrepancies", DANGER if n_open else OK,
        f"{n_res} auto-resolved" if n_res else "all clear")
    kpi(d, pending, "ASNs not complete", WARN if pending else OK,
        f"{n_comp} fully complete")

    st.markdown("##### Pipeline")
    pipeline_strip([
        ("ASN uploaded", n_asn, MUTED),
        ("Korber GRN done", n_korber, ACCENT),
        ("AX GRN pending", n_axp, INFO),
        ("Fully complete", n_comp, OK),
    ])

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("###### ASNs by status")
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
            st.caption("Nothing reconciled yet.")
        else:
            v = det["MATCH STATUS"].replace("", schema.M_PENDING) \
                .value_counts().sort_values()
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
        st.markdown("###### Quantity expected against received")
        top = summ.copy()
        top["_a"] = top["TOTAL QTY"].map(to_num)
        top["_r"] = top["RECEIVED QTY"].map(to_num)
        top = top.nlargest(12, "_a").sort_values("_a")
        fig = go.Figure()
        fig.add_bar(name="Expected", y=top["ASN NO"], x=top["_a"],
                    orientation="h", marker_color="#cbd3dc")
        fig.add_bar(name="Received", y=top["ASN NO"], x=top["_r"],
                    orientation="h", marker_color=ACCENT)
        fig.update_layout(barmode="group", bargap=.28,
                          yaxis=dict(showgrid=False),
                          xaxis=dict(showgrid=True, gridcolor=LINE))
        st.plotly_chart(fig_style(fig, 340, legend=True), width="stretch")

    with c2:
        st.markdown("###### Discrepancies by type")
        open_d = disc[disc["STATUS"] == schema.D_OPEN] if not disc.empty else disc
        if open_d.empty:
            st.success("No open discrepancies.")
        else:
            v = open_d["DISCREPANCY TYPE"].value_counts().sort_values()
            fig = go.Figure(go.Bar(
                x=v.values, y=v.index, orientation="h", marker_color=DANGER,
                text=v.values, textposition="outside", cliponaxis=False))
            fig.update_layout(xaxis=dict(showticklabels=False, showgrid=False),
                              yaxis=dict(showgrid=False))
            st.plotly_chart(fig_style(fig, 340), width="stretch")

    st.markdown("###### Needs action")
    need = summ[summ["OVERALL"] != schema.S_COMPLETE]
    if need.empty:
        st.success("Every ASN is fully complete.")
    else:
        show(need[["ASN NO", "TOTAL LINES", "MATCHED LINES", "MISSING LINES",
                   "MISMATCH LINES", "EXTRA LINES", "STATUS", "KORBER GRN",
                   "AX GRN", "LAST RECON"]], height=300)

    st.caption(f"Received {fmt_num(rec_qty)} of {fmt_num(asn_qty)} expected · "
               f"variance {fmt_num(rec_qty - asn_qty)}")


# ═══════════════════════════════════════════════════════════════════
#  DATA MANAGER
# ═══════════════════════════════════════════════════════════════════
elif page == "Data Manager":
    hero("Data Manager", "Edit any sheet directly (admin only)")

    if SS["role"] != "admin":
        st.warning("Admin only. Sign in from the sidebar.")
        st.stop()

    key = st.selectbox("Sheet", list(schema.SHEETS))
    df = gsheets.get_df(key)
    st.caption(f"{len(df)} rows · {len(schema.SHEETS[key]['headers'])} columns")

    ed = st.data_editor(df, num_rows="dynamic", width="stretch",
                        height=520, key=f"ed_{key}")

    c1, c2 = st.columns([1, 3])
    if c1.button("Save", type="primary"):
        gsheets.overwrite(key, ed)
        st.success("Saved.")
        st.rerun()
    c2.caption("Saving replaces the whole sheet with what is shown here.")

    st.download_button(f"Download {key}",
                       reporting.build_excel({key[:31]: df}),
                       file_name=f"{key}_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ═══════════════════════════════════════════════════════════════════
#  MAINTENANCE
# ═══════════════════════════════════════════════════════════════════
elif page == "Maintenance":
    hero("Maintenance", "Delete an ASN · clear a sheet · reset the database")

    if SS["role"] != "admin":
        st.warning("Admin only. Sign in from the sidebar.")
        st.stop()

    ASN_SHEETS = {"ASN_SUMMARY": "ASN NO", "ASN_DETAIL": "ASN NO",
                  "DISCREPANCY": "ASN NO", "AX_GRN": "ASN NO"}

    t1, t2, t3 = st.tabs(["Delete an ASN", "Clear a sheet", "Reset the database"])

    with t1:
        st.caption("Removes the summary, line details, discrepancies, AX GRN entry "
                   "and attachments for the selected ASNs. This cannot be undone.")

        summ = gsheets.get_df("ASN_SUMMARY")
        det = gsheets.get_df("ASN_DETAIL")
        opts = sorted({clean(a) for a in summ["ASN NO"] if clean(a)}) \
            if not summ.empty else []
        if not opts and not det.empty:
            opts = sorted({clean(a) for a in det["ASN NO"] if clean(a)})

        if not opts:
            st.info("No ASN records.")
        else:
            sel = st.multiselect("ASNs to delete", opts, key="del_asn")
            if sel:
                counts = {}
                for k, col in ASN_SHEETS.items():
                    d = gsheets.get_df(k)
                    counts[k] = 0 if d.empty or col not in d.columns else int(
                        d[col].astype(str).str.strip().isin(sel).sum())
                imeta = gsheets.get_df("ASN_IMAGES")
                counts["ASN_IMAGES"] = 0 if imeta.empty else int(
                    imeta["ASN NO"].astype(str).str.strip().isin(sel).sum())

                st.markdown("###### What will be removed")
                show(pd.DataFrame([{"Sheet": k, "Rows": v} for k, v in counts.items()]))

                prev = det[det["ASN NO"].astype(str).isin(sel)] \
                    if not det.empty else pd.DataFrame()
                with st.expander(f"Lines to be deleted ({len(prev)})"):
                    show(prev[["ASN NO", "ASN LINE", "HU ID", "ITEM NUMBER", "QTY",
                               "MATCH STATUS", "KORBER GRN", "AX GRN"]]
                         if not prev.empty else prev)

                st.download_button(
                    "Back these up first",
                    reporting.build_excel({
                        "Summary": summ[summ["ASN NO"].astype(str).isin(sel)],
                        "Details": prev}),
                    file_name=f"ASN_backup_{date.today():%Y%m%d}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

                typed = st.text_input("Type DELETE to confirm", key="del_confirm")
                also_img = st.checkbox("Delete attachments too", value=True)

                if st.button("Delete", type="primary",
                             disabled=typed.strip().upper() != "DELETE"):
                    with st.spinner("Deleting..."):
                        res = {k: gsheets.delete_where(k, col, sel)
                               for k, col in ASN_SHEETS.items()}
                        if also_img:
                            res["ASN_IMAGES"] = images.delete_for_asn(sel)
                    st.success("Deleted — " +
                               ", ".join(f"{k}: {v}" for k, v in res.items()))
                    st.rerun()

    with t2:
        st.caption("Removes every data row and keeps the header. The sheet itself "
                   "stays in place.")
        show(gsheets.sheet_status())

        k = st.selectbox("Sheet", list(schema.SHEETS), key="clr_sheet")
        cur = gsheets.get_df(k)
        st.caption(f"{len(cur)} rows at the moment.")

        st.download_button(
            f"Back up {k}", reporting.build_excel({k[:31]: cur}),
            file_name=f"{k}_backup_{date.today():%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        typed2 = st.text_input(f"Type {k} to confirm", key="clr_confirm")
        if st.button("Clear", disabled=typed2.strip() != k, type="primary"):
            n = gsheets.clear_sheet(k)
            st.success(f"{k} — {n} rows cleared.")
            st.rerun()

    with t3:
        st.error("This removes every record in the selected sheets and cannot be "
                 "undone. Take a backup first.")

        DATA_SHEETS = ["ASN_SUMMARY", "ASN_DETAIL", "INVENTORY", "DISCREPANCY",
                       "AX_GRN", "ASN_IMAGES", "IMAGE_DATA", "RECON_LOG",
                       "EMAIL_LOG"]
        MASTERS = ["USER-M", "SETTINGS"]

        scope = st.radio(
            "Scope",
            ["Transaction data only — keeps users and settings",
             "Choose the sheets myself",
             "Everything, including users and settings"],
            key="reset_scope")

        if scope.startswith("Transaction"):
            targets = DATA_SHEETS
        elif scope.startswith("Everything"):
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
        st.markdown(f"**{len(targets)} sheet(s) · {sum(rows_now.values())} rows** "
                    f"will be removed")
        show(pd.DataFrame([{"Sheet": k, "Rows": v} for k, v in rows_now.items()]))

        if st.button("Build a backup file", key="mk_backup"):
            SS["backup"] = reporting.build_excel(
                {k[:31]: gsheets.get_df(k) for k in targets})
        if SS.get("backup"):
            st.download_button(
                "Download the backup", SS["backup"],
                file_name=f"FULL_BACKUP_{datetime.now():%Y%m%d_%H%M}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        c1, c2 = st.columns(2)
        pin2 = c1.text_input("Admin PIN again", type="password", key="reset_pin")
        typed3 = c2.text_input("Type RESET to confirm", key="reset_confirm")
        ack = st.checkbox("I understand this data cannot be recovered.",
                          key="reset_ack")

        good_pin = pin2 == str(gsheets.settings_dict().get("ADMIN_PIN", "1234"))
        ready = bool(targets) and good_pin and typed3.strip().upper() == "RESET" and ack
        if pin2 and not good_pin:
            st.warning("Wrong PIN.")

        if st.button("Reset the database", type="primary", disabled=not ready):
            with st.spinner("Resetting..."):
                res = gsheets.reset_database(targets)
                if "SETTINGS" in targets or "USER-M" in targets:
                    gsheets.ensure_all()
            st.success("Reset — " + ", ".join(f"{k}: {v}" for k, v in res.items()))
            SS["auto"] = SS["recon"] = SS["email"] = None
            SS["parsed_asn"] = {}
            SS["inv_df"] = None
            SS["backup"] = None
            st.rerun()


# ───────────────────────────── footer ─────────────────────────────
st.markdown(
    f"<div style='text-align:center;color:{MUTED};font-size:.73rem;"
    f"margin-top:2.4rem;padding-top:1rem;border-top:1px solid {LINE}'>"
    "ASN / GRN Control System · Korber One and AX · EFL Warehouse Operations"
    "</div>", unsafe_allow_html=True)
