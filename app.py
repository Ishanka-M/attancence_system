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

# ───────────────────────────── styling ─────────────────────────────
st.markdown("""
<style>
  .block-container {padding-top: 1.6rem; max-width: 1500px;}
  h1, h2, h3 {letter-spacing: -.01em;}
  .hero {background:#16232e; color:#e8eef3; padding:16px 20px; border-radius:10px;
         margin-bottom:14px; border-left:5px solid #4bb3a2;}
  .hero .t {font-size:1.15rem; font-weight:700;}
  .hero .s {font-size:.82rem; opacity:.72; margin-top:2px;}
  .pill {display:inline-block; padding:2px 10px; border-radius:11px;
         font-size:.74rem; font-weight:600; color:#fff;}
  .kpi {background:#f6f8fa; border:1px solid #e3e8ee; border-radius:9px;
        padding:12px 14px;}
  .kpi .v {font-size:1.55rem; font-weight:700; line-height:1.1;}
  .kpi .l {font-size:.74rem; text-transform:none; color:#5d6b7a; margin-top:2px;}
  .stDataFrame {font-size:.84rem;}
  section[data-testid="stSidebar"] {background:#111c25;}
  section[data-testid="stSidebar"] * {color:#dbe4ea;}
  div[data-testid="stMetricValue"] {font-size:1.5rem;}
</style>
""", unsafe_allow_html=True)


def hero(title: str, sub: str = ""):
    st.markdown(f'<div class="hero"><div class="t">{title}</div>'
                f'<div class="s">{sub}</div></div>', unsafe_allow_html=True)


def kpi(col, value, label, color="#16232e"):
    col.markdown(f'<div class="kpi"><div class="v" style="color:{color}">{value}</div>'
                 f'<div class="l">{label}</div></div>', unsafe_allow_html=True)


def pill(text: str) -> str:
    c = schema.STATUS_COLORS.get(text, "#6b7785")
    return f'<span class="pill" style="background:{c}">{text}</span>'


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

PAGES = ["📊 Dashboard", "📤 ASN Upload", "📦 Inventory", "🔄 Reconciliation",
         "🧾 ASN Register", "🔍 Search", "⚠️ Discrepancy", "✉️ Email", "✅ AX GRN",
         "🖼️ ASN Images", "⚙️ Setup", "🗂️ Data Manager", "🧹 Maintenance"]
page = st.sidebar.radio("Menu", PAGES, label_visibility="collapsed")

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Cache clear"):
    gsheets.refresh()
    st.rerun()
_url = gsheets.spreadsheet_url()
if _url:
    st.sidebar.markdown(f"[📗 Google Sheet විවෘත කරන්න]({_url})")


# ═══════════════════════════════════════════════════════════════════
#  ⚙️ SETUP
# ═══════════════════════════════════════════════════════════════════
if page == "⚙️ Setup":
    hero("⚙️ Setup", "අවශ්‍ය හැම Google Sheet tab එකක්ම auto-create + system settings")

    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("🏗️ හැම Sheet එකක්ම හදන්න / update කරන්න", type="primary"):
            with st.spinner("Sheets හදනවා..."):
                created, patched = gsheets.ensure_all()
            if created:
                st.success("අලුතෙන් හැදුවා: " + ", ".join(created))
            if patched:
                st.info("Header update කළා: " + ", ".join(patched))
            if not created and not patched:
                st.success("ඔක්කොම sheets දැනටමත් හරි ✅")
    with c2:
        st.caption("මේක ඔබාම `ASN_SUMMARY`, `ASN_DETAIL`, `INVENTORY`, `DISCREPANCY`, "
                   "`AX_GRN`, `ASN_IMAGES`, `RECON_LOG`, `EMAIL_LOG`, `USER-M`, "
                   "`SETTINGS` කියන tabs ඔක්කොම හැදෙනවා.")

    st.markdown("#### Sheet status")
    show(gsheets.sheet_status())

    st.markdown("---")
    st.markdown("#### ⚙️ Matching settings")
    s = gsheets.settings_dict()
    with st.form("settings"):
        a, b, c = st.columns(3)
        s["CLIENT_CODE"] = a.text_input("Client code", s.get("CLIENT_CODE", "HIES"))
        s["SITE"] = b.text_input("Site / Warehouse", s.get("SITE", "EGDC"))
        s["COMPANY"] = c.text_input("Company", s.get("COMPANY", "EFL"))

        a, b, c, d = st.columns(4)
        s["QTY_TOLERANCE"] = str(a.number_input(
            "Qty tolerance", value=gsheets.setting_float(s, "QTY_TOLERANCE"), step=1.0))
        s["STRIP_CLIENT_PREFIX"] = "Y" if b.checkbox(
            "Client prefix ඉවත් කරන්න", gsheets.setting_bool(s, "STRIP_CLIENT_PREFIX")) else "N"
        s["CHECK_ITEM"] = "Y" if c.checkbox(
            "Item check", gsheets.setting_bool(s, "CHECK_ITEM")) else "N"
        s["CHECK_LOT"] = "Y" if d.checkbox(
            "Lot check", gsheets.setting_bool(s, "CHECK_LOT")) else "N"

        a, b = st.columns(2)
        s["CHECK_ASN_NO"] = "Y" if a.checkbox(
            "ASN number check", gsheets.setting_bool(s, "CHECK_ASN_NO")) else "N"
        s["FLAG_EXTRA"] = "Y" if b.checkbox(
            "Extra HU flag කරන්න", gsheets.setting_bool(s, "FLAG_EXTRA")) else "N"

        a, b = st.columns(2)
        s["EMAIL_TO"] = a.text_input("Email To", s.get("EMAIL_TO", ""))
        s["EMAIL_CC"] = b.text_input("Email Cc", s.get("EMAIL_CC", ""))

        st.markdown("**🖼️ Image storage**")
        a, b, c = st.columns(3)
        s["IMAGE_STORAGE"] = a.selectbox(
            "Save කරන තැන", ["SHEET", "DRIVE"],
            index=0 if str(s.get("IMAGE_STORAGE", "SHEET")).upper() != "DRIVE" else 1,
            help="SHEET = Google Sheet එකේම (safe, quota ප්‍රශ්න නෑ). "
                 "DRIVE = Drive folder එකට (folder එක share කරලා තියෙන්න ඕනේ).")
        s["IMAGE_MAX_PX"] = str(int(b.number_input(
            "Max px", value=gsheets.setting_float(s, "IMAGE_MAX_PX", 1400),
            min_value=400.0, max_value=4000.0, step=100.0)))
        s["IMAGE_QUALITY"] = str(int(c.number_input(
            "JPEG quality", value=gsheets.setting_float(s, "IMAGE_QUALITY", 78),
            min_value=40.0, max_value=95.0, step=1.0)))
        s["DRIVE_FOLDER_ID"] = st.text_input(
            "Drive folder ID (DRIVE mode එකට විතරයි)", s.get("DRIVE_FOLDER_ID", ""),
            help="Drive folder එකක් හදලා service account එකට Editor විදිහට share කරලා ID එක දාන්න.")
        s["ADMIN_PIN"] = st.text_input("Admin PIN", s.get("ADMIN_PIN", "1234"))

        if st.form_submit_button("💾 Settings save", type="primary"):
            gsheets.save_settings(s)
            st.success("Save කළා ✅")

    st.markdown("---")
    _mode = str(gsheets.settings_dict().get("IMAGE_STORAGE", "SHEET")).upper()
    if _mode == "DRIVE":
        st.caption(f"🖼️ Image storage: **DRIVE** · Drive API: "
                   f"{'✅ ready' if drive.available() else '⚠️ google-api-python-client නෑ'} "
                   f"· fail වුණොත් automatic ව Sheet එකට save වෙනවා.")
    else:
        st.caption("🖼️ Image storage: **SHEET** — images compress කරලා `IMAGE_DATA` "
                   "sheet එකේම save වෙනවා. Drive quota ප්‍රශ්න නෑ.")


# ═══════════════════════════════════════════════════════════════════
#  📤 ASN UPLOAD
# ═══════════════════════════════════════════════════════════════════
elif page == "📤 ASN Upload":
    hero("📤 ASN Document Upload",
         "Excel එක upload කරලා → Sheet එක තෝරලා → confirm කරලා → Summary + Details save")

    if not SS["user"]:
        st.warning("Sidebar එකෙන් Operator නම තෝරන්න.")

    files = st.file_uploader("ASN Excel file(s)", type=["xlsx", "xlsm", "xls"],
                             accept_multiple_files=True, key="asn_up")

    if files:
        st.markdown("### 1️⃣ Sheet එක තෝරන්න")
        st.caption("File එකේ තියෙන sheets වලින් ASN data තියෙන එක තෝරලා parse කරන්න.")

        choices = {}
        for f in files:
            b = f.getvalue()
            sheets = parsing.list_sheets(b)
            if not sheets:
                st.error(f"`{f.name}` — Excel sheet කියවන්න බැරි උනා.")
                continue
            c1, c2 = st.columns([2, 3])
            c1.markdown(f"**📄 {f.name}**")
            sel = c2.selectbox(f"Sheet — {f.name}", sheets, key=f"sh_{f.name}",
                               label_visibility="collapsed")
            choices[f.name] = (b, sel)

        if st.button("🔍 Parse & Preview", type="primary"):
            SS["parsed_asn"] = {}
            for fname, (b, sheet) in choices.items():
                df, meta = parsing.parse_asn(b, sheet)
                imgs = parsing.extract_images(b)
                SS["parsed_asn"][fname] = {"df": df, "meta": meta, "images": imgs,
                                           "sheet": sheet}
            st.rerun()

    # ── preview + confirm ──
    if SS["parsed_asn"]:
        st.markdown("---")
        st.markdown("### 2️⃣ Preview & Confirm")

        total_rows = 0
        all_ok = True
        for fname, p in SS["parsed_asn"].items():
            df, meta = p["df"], p["meta"]
            with st.expander(f"📄 {fname}  ·  sheet: `{p['sheet']}`  ·  "
                             f"{len(df)} lines  ·  🖼️ {len(p['images'])} image(s)",
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
                kpi(c, fmt_num(df["QTY"].map(to_num).sum()), "Total Qty")
                kpi(d, len(asns), "ASN number(s)")
                st.caption("ASN: " + ", ".join(f"`{x}`" for x in asns[:8]))
                st.caption(f"Header row: {meta['header_row']}  ·  "
                           f"Mapped columns: {len(meta['mapped'])}")
                if meta["unmapped"]:
                    st.caption("⚠️ Map නොවුණ columns (skip වෙනවා): " +
                               ", ".join(meta["unmapped"][:12]))
                show(df.head(50))
                if p["images"]:
                    st.caption(f"Excel එක ඇතුළේ images {len(p['images'])}ක් හම්බුණා:")
                    cols = st.columns(min(5, len(p["images"])))
                    for i, im in enumerate(p["images"][:5]):
                        cols[i].image(im["data"], caption=f"{im['name']} ({im['size_kb']}KB)",
                                      width="stretch")

        st.markdown("### 3️⃣ Extra photos (optional)")
        extra_imgs = st.file_uploader("ASN එකට අදාළ photos (GRN sheet, damage, seal...)",
                                      type=["png", "jpg", "jpeg", "webp"],
                                      accept_multiple_files=True, key="extra_img")
        all_asn = sorted({clean(a)
                          for p in SS["parsed_asn"].values()
                          for a in p["df"].get("ASN_NO", []) if clean(a)})
        img_asn = st.selectbox("මේ photos අදාළ ASN එක", all_asn or ["—"], key="img_asn") \
            if extra_imgs else None

        st.markdown("### 4️⃣ Save")
        c1, c2 = st.columns([2, 1])
        targets = c1.multiselect("Save කරන්න ඕන sheets",
                                 ["ASN_SUMMARY", "ASN_DETAIL"],
                                 default=["ASN_SUMMARY", "ASN_DETAIL"])
        up_img = c2.checkbox("Images Drive එකට upload", value=True)

        confirm = st.checkbox(
            f"✅ මම confirm කරනවා — ලයින් {total_rows}ක් {', '.join(targets) or '—'} "
            f"sheet(s) වලට save කරන්න.")

        cA, cB = st.columns([1, 1])
        if cA.button("💾 Save to Google Sheet", type="primary",
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

                # images from the workbook
                if up_img:
                    file_asn = clean(df["ASN_NO"].iloc[0]) if len(df) else ""
                    for im in p["images"]:
                        img_rows.append((file_asn, im["name"], "EXCEL EMBEDDED",
                                         im["mime"], im["size_kb"], im["data"]))

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
                    prog = st.progress(0.0, text="Images save වෙනවා...")
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
                                      text=f"Images {i + 1}/{len(img_rows)}")
                    prog.empty()
                    if ok_n:
                        st.success(f"🖼️ Images {ok_n}ක් save කළා")
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
    hero("📦 Korber Inventory", "Inventory report එක upload කරලා snapshot එකක් තියාගන්න")

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
    hero("🔄 ASN ↔ Inventory Reconciliation",
         "GRN complete උනේ මොනාද, නැත්තේ මොනාද, විෂමතා මොනාද")

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
    hero("🧾 ASN Register", "Summary සහ Details — filter කරලා බලන්න / download කරන්න")

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
    hero("🔍 Search", "ඕනෑම data එකක් — HU, ASN, item, lot, PO, GRN, vendor — හොයන්න")

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
    hero("⚠️ Discrepancy Report", "Tally නොවුණ ඒවා — Summary සහ Details")

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
    hero("✉️ Discrepancy Email", "විස්තර සහිත Markdown email එකක් auto-generate")

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
    hero("✅ AX GRN", "Korber GRN Done → AX GRN Pending → AX GRN Done → Fully Complete")

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
elif page == "🖼️ ASN Images":
    hero("🖼️ ASN Images", "ASN එකට අදාළ photos / Excel එකේ embed වුණ images")

    meta = gsheets.get_df("ASN_IMAGES")
    summ = gsheets.get_df("ASN_SUMMARY")
    asn_opts = sorted({a for a in summ["ASN NO"] if a}) if not summ.empty else []
    if not asn_opts and not meta.empty:
        asn_opts = sorted({a for a in meta["ASN NO"] if a})

    mode = str(gsheets.settings_dict().get("IMAGE_STORAGE", "SHEET")).upper()
    st.caption(f"Storage mode: **{mode}** — Setup page එකෙන් වෙනස් කරන්න පුළුවන්.")

    with st.expander("➕ අලුත් image එකක් එකතු කරන්න", expanded=meta.empty):
        c1, c2 = st.columns([1, 2])
        asn = c1.selectbox("ASN", asn_opts or ["—"])
        note = c2.text_input("Note", "")
        ups = st.file_uploader("Images", type=["png", "jpg", "jpeg", "webp", "bmp"],
                               accept_multiple_files=True, key="img_only")
        if st.button("⬆️ Upload", disabled=not ups or asn == "—", type="primary"):
            ok_n, errs = 0, []
            for uf in ups:
                ok, msg = images.save_image(asn, uf.name, uf.getvalue(),
                                            uf.type or "image/png",
                                            source="MANUAL UPLOAD",
                                            user=SS["user"] or "unknown", note=note)
                if ok:
                    ok_n += 1
                    if msg and "fail" in msg.lower():
                        errs.append(msg)
                else:
                    errs.append(msg)
            if ok_n:
                st.success(f"{ok_n} save කළා ✅")
            for e_ in errs:
                st.warning(e_)
            if ok_n:
                st.rerun()

    if meta.empty:
        st.info("Images නෑ.")
    else:
        f = st.selectbox("ASN filter", ["— සියල්ල —"] +
                         sorted({a for a in meta["ASN NO"] if a}))
        v = meta if f.startswith("—") else meta[meta["ASN NO"] == f]

        a, b, c = st.columns(3)
        kpi(a, len(v), "Images")
        kpi(b, v["ASN NO"].nunique(), "ASN")
        kpi(c, f'{fmt_num(v["SIZE KB"].map(to_num).sum())} KB', "Total size")

        show(v[["IMAGE ID", "ASN NO", "FILE NAME", "SOURCE", "SIZE KB", "STORAGE",
                "UPLOADED AT", "UPLOADED BY", "NOTE"]])

        st.markdown("#### 👁️ Preview")
        cols = st.columns(4)
        for i, (_, r) in enumerate(v.head(16).iterrows()):
            with cols[i % 4]:
                if str(r.get("STORAGE", "SHEET")).upper() == "DRIVE" and r.get("LINK"):
                    st.markdown(f"[🔗 {r['FILE NAME']}]({r['LINK']})")
                else:
                    data = images.load_image(r["IMAGE ID"])
                    if data:
                        st.image(data, caption=f"{r['ASN NO']} · {r['FILE NAME']}",
                                 width="stretch")
                        st.download_button("📥", data, file_name=r["FILE NAME"],
                                           mime=r.get("MIME") or "image/jpeg",
                                           key=f"dl_{r['IMAGE ID']}")
                    else:
                        st.caption(f"⚠️ {r['FILE NAME']} — data නෑ")

        with st.expander("🗑️ Image delete"):
            ids = st.multiselect("IMAGE ID", list(v["IMAGE ID"]))
            if st.button("Delete", disabled=not ids):
                n = images.delete_images(ids)
                st.success(f"{n} delete කළා ✅")
                st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  📊 DASHBOARD
# ═══════════════════════════════════════════════════════════════════
elif page == "📊 Dashboard":
    hero("📊 Dashboard", "ASN → Korber GRN → AX GRN pipeline එකේ තත්ත්වය")

    summ = gsheets.get_df("ASN_SUMMARY")
    det = gsheets.get_df("ASN_DETAIL")
    disc = gsheets.get_df("DISCREPANCY")
    ax = gsheets.get_df("AX_GRN")

    if summ.empty:
        st.info("Data නෑ. 📤 ASN Upload එකෙන් පටන් ගන්න.")
        st.stop()

    n_asn = len(summ)
    n_korber = int((summ["KORBER GRN"] == schema.K_DONE).sum())
    n_axp = int((summ["AX GRN"] == schema.AX_PENDING).sum())
    n_comp = int((summ["OVERALL"] == schema.S_COMPLETE).sum())
    n_open = int((disc["STATUS"] == "OPEN").sum()) if not disc.empty else 0

    a, b, c, d, e = st.columns(5)
    kpi(a, n_asn, "Total ASN")
    kpi(b, n_korber, "Korber GRN Done", "#2f8f83")
    kpi(c, n_axp, "AX GRN Pending", "#3a6ea5")
    kpi(d, n_comp, "Fully Complete", "#2f7a45")
    kpi(e, n_open, "Open discrepancies", "#c4453f")

    st.markdown("")
    c1, c2 = st.columns([1, 1])

    with c1:
        st.markdown("##### ASN status")
        vc = summ["STATUS"].replace("", schema.S_NEW).value_counts()
        fig = px.pie(values=vc.values, names=vc.index, hole=.55,
                     color=vc.index, color_discrete_map=schema.STATUS_COLORS)
        fig.update_layout(height=320, margin=dict(t=10, b=10, l=10, r=10),
                          legend=dict(orientation="h", y=-.1))
        st.plotly_chart(fig, width="stretch")

    with c2:
        st.markdown("##### GRN pipeline")
        stages = ["ASN uploaded", "Korber GRN Done", "AX GRN Pending", "Fully Complete"]
        vals = [n_asn, n_korber, n_axp, n_comp]
        fig = go.Figure(go.Funnel(y=stages, x=vals,
                                  marker=dict(color=["#8b93a7", "#2f8f83", "#3a6ea5", "#2f7a45"]),
                                  textinfo="value+percent initial"))
        fig.update_layout(height=320, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, width="stretch")

    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("##### Line level match")
        if not det.empty:
            v = det["MATCH STATUS"].replace("", schema.M_PENDING).value_counts()
            fig = px.bar(x=v.values, y=v.index, orientation="h",
                         labels={"x": "Lines", "y": ""}, text=v.values)
            fig.update_traces(marker_color="#3a6ea5")
            fig.update_layout(height=320, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig, width="stretch")

    with c2:
        st.markdown("##### Discrepancy by type")
        if not disc.empty:
            v = disc["DISCREPANCY TYPE"].value_counts()
            fig = px.bar(x=v.index, y=v.values, text=v.values, labels={"x": "", "y": "Lines"})
            fig.update_traces(marker_color="#c4453f")
            fig.update_layout(height=320, margin=dict(t=10, b=10, l=10, r=10),
                              xaxis_tickangle=-20)
            st.plotly_chart(fig, width="stretch")
        else:
            st.success("විෂමතා නෑ 🎉")

    st.markdown("##### ASN wise Qty — ASN vs Received")
    top = summ.copy()
    top["_a"] = top["TOTAL QTY"].map(to_num)
    top["_r"] = top["RECEIVED QTY"].map(to_num)
    top = top.nlargest(15, "_a")
    fig = go.Figure()
    fig.add_bar(name="ASN Qty", x=top["ASN NO"], y=top["_a"], marker_color="#8b93a7")
    fig.add_bar(name="Received Qty", x=top["ASN NO"], y=top["_r"], marker_color="#2f8f83")
    fig.update_layout(barmode="group", height=350, margin=dict(t=10, b=10, l=10, r=10),
                      xaxis_tickangle=-25, legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, width="stretch")

    st.markdown("##### 🔔 Action required")
    need = summ[summ["OVERALL"] != schema.S_COMPLETE]
    show(need[["ASN NO", "TOTAL LINES", "MATCHED LINES", "MISSING LINES",
               "MISMATCH LINES", "EXTRA LINES", "STATUS", "KORBER GRN", "AX GRN",
               "LAST RECON"]], height=320)


# ═══════════════════════════════════════════════════════════════════
#  🗂️ DATA MANAGER
# ═══════════════════════════════════════════════════════════════════
elif page == "🗂️ Data Manager":
    hero("🗂️ Data Manager", "ඕනෑම sheet එකක records edit / delete කරන්න (admin)")

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
    hero("🧹 Maintenance", "ASN delete · පරණ data clean · database reset")

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
    "<div style='text-align:center;color:#8b93a7;font-size:.75rem;margin-top:28px'>"
    "ASN ↔ GRN Control System · Streamlit + Google Sheets · Korber One / AX"
    "</div>", unsafe_allow_html=True)
