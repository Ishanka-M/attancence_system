"""
app.py — EFL CSS KPI & Incentive System
========================================
Streamlit + Python + Google Sheets backend.

Pages:
  🏠 Dashboard | ⚙️ Setup | 📝 Transaction | 🕐 Attendance | ⏱️ OT Approval
  📋 Complaint | ✅ KPI Update | 💰 Incentive | 👥 Masters
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

import calc
import gsheets
import schema

st.set_page_config(page_title="EFL KPI System", page_icon="📊", layout="wide")

TIME_OPTIONS = [schema.TIME_NORMAL, schema.TIME_OT_N, schema.TIME_OT_D]


# ───────────────────────── helpers ─────────────────────────
def unic(date_val, user_id: str) -> str:
    """UNIC CODE = DATE + USER ID (original Excel pattern එකම)."""
    d = date_val.strftime("%Y%m%d") if hasattr(date_val, "strftime") else str(date_val)
    return f"{d}{user_id}"


@st.cache_data(ttl=60, show_spinner=False)
def _users():
    return gsheets.get_df("USER-M")


@st.cache_data(ttl=60, show_spinner=False)
def _tcodes():
    return gsheets.get_df("TCODE-M")


def user_picker(label="USER", key=None):
    df = _users()
    if df.empty:
        st.warning("USER-M හිස්. මුලින් Setup එකෙන් sheets create කරන්න.")
        return None, None
    opts = {f'{r["USER ID"]} — {r["USER NAME"]}': (r["USER ID"], r["USER NAME"])
            for _, r in df.iterrows() if str(r.get("USER ID", "")).strip()}
    sel = st.selectbox(label, list(opts.keys()), key=key)
    return opts[sel]


def df_show(df: pd.DataFrame, n=200):
    st.dataframe(df.tail(n), use_container_width=True, hide_index=True)


# ───────────────────────── sidebar ─────────────────────────
st.sidebar.title("📊 EFL KPI System")
st.sidebar.caption("CSS • Streamlit + Google Sheets")

PAGES = [
    "🏠 Dashboard", "⚙️ Setup", "📝 Transaction", "🕐 Attendance",
    "⏱️ OT Approval", "📋 Complaint", "✅ KPI Update", "💰 Incentive",
    "👥 Masters",
]
page = st.sidebar.radio("Menu", PAGES, label_visibility="collapsed")

# connection check
try:
    gsheets.get_spreadsheet()
    st.sidebar.success("Google Sheet සම්බන්ධයි ✅")
except Exception as e:
    st.sidebar.error("Sheet සම්බන්ධ වෙන්නෑ. secrets.toml බලන්න.")
    st.sidebar.exception(e)


# ═══════════════════════════ SETUP ═══════════════════════════
if page == "⚙️ Setup":
    st.header("⚙️ Setup — Google Sheet Auto-Create")
    st.write(
        "පහත button එක click කළාම schema එකේ තියෙන **හැම tab එකක්ම Google Sheet "
        "එකේ auto-create** වෙනවා, headers දැම්මෙයි, master sheets වලට "
        "(USER-M, TCODE-M, SITE-M, CUSTOMMER-M, TIME-M, LOCATION-M) "
        "මුල් Excel එකේ data seed වෙනවා."
    )

    col1, col2 = st.columns(2)
    seed = col1.checkbox("Masters seed කරන්න (T-codes, Users, Sites…)", value=True)
    if col2.button("🚀 Sheets Auto-Create / Sync", type="primary"):
        with st.spinner("Sheets හදනවා…"):
            created = gsheets.ensure_all(seed_masters=seed)
        if created:
            st.success(f"අලුතෙන් හදපු sheets: {', '.join(created)}")
        else:
            st.info("සියලුම sheets දැනටමත් තියෙනවා ✅")
        st.cache_data.clear()

    st.divider()
    st.subheader("📋 වර්තමාන තත්ත්වය")
    try:
        st.dataframe(gsheets.sheet_status(), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning("Status බලන්න කලින් sheets create කරන්න.")
        st.caption(str(e))


# ═══════════════════════════ DASHBOARD ═══════════════════════════
elif page == "🏠 Dashboard":
    st.header("🏠 Dashboard")
    try:
        txn = gsheets.get_df("TRANSACTION")
        att = gsheets.get_df("ATTANDANCE")
    except Exception:
        st.info("මුලින් Setup එකෙන් sheets create කරන්න.")
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    total_rev = txn["TOTAL REVANUE"].apply(calc._f).sum() if "TOTAL REVANUE" in txn else 0
    total_inc = txn["TXN INCENTIVE"].apply(calc._f).sum() if "TXN INCENTIVE" in txn else 0
    c1.metric("Transactions", f"{len(txn):,}")
    c2.metric("Total Revenue", f"{total_rev:,.0f}")
    c3.metric("Txn Incentive", f"{total_inc:,.0f}")
    c4.metric("Attendance rows", f"{len(att):,}")

    if not txn.empty and "USER NAME" in txn:
        st.subheader("👤 User එක අනුව Revenue")
        g = txn.copy()
        g["TOTAL REVANUE"] = g["TOTAL REVANUE"].apply(calc._f) if "TOTAL REVANUE" in g else 0
        top = g.groupby("USER NAME")["TOTAL REVANUE"].sum().sort_values(ascending=False).head(15)
        st.bar_chart(top)

        st.subheader("📅 දිනපතා Revenue")
        if "DATE" in g:
            daily = g.groupby("DATE")["TOTAL REVANUE"].sum()
            st.line_chart(daily)
    else:
        st.info("තවම transactions නෑ. 📝 Transaction page එකෙන් දාන්න.")


# ═══════════════════════════ TRANSACTION ═══════════════════════════
elif page == "📝 Transaction":
    st.header("📝 Transaction Entry")
    tdf = _tcodes()
    if tdf.empty:
        st.warning("TCODE-M හිස්. Setup එකෙන් seed කරන්න.")
        st.stop()
    lut = calc.build_tcode_lookup(tdf)

    with st.form("txn"):
        c1, c2, c3 = st.columns(3)
        with c1:
            date_v = st.date_input("DATE", dt.date.today())
            uid, uname = user_picker("USER", key="txn_user")
        with c2:
            site = st.text_input("SITE", "EGF")
            cust = st.text_input("CUSTOMMER", "")
        with c3:
            code = st.selectbox("T-CODE", sorted(lut.keys()),
                                format_func=lambda c: f'{c} — {lut[c]["desc"][:30]}')
            time_t = st.selectbox("TIME", TIME_OPTIONS)

        qty = st.number_input("# OF TRANSACTION", min_value=0.0, step=1.0)

        # live preview
        info = lut.get(code, {})
        prev = calc.calc_transaction(info, time_t, qty)
        p1, p2, p3 = st.columns(3)
        p1.metric("Utilize Hours", f'{prev["utilize_hours"]:.3f}')
        p2.metric("Total Revenue", f'{prev["total_rev"]:,.2f}')
        p3.metric("Txn Incentive", f'{prev["txn_incentive"]:,.2f}')

        submitted = st.form_submit_button("➕ Add Transaction", type="primary")

    if submitted and uid:
        r = calc.calc_transaction(lut.get(code, {}), time_t, qty)
        row = [
            unic(date_v, uid), date_v.isoformat(), uid, uname, site, cust,
            code, info.get("desc", ""), time_t, info.get("uom", ""), qty,
            r["smv"], r["utilize_hours"], r["rev_normal"], r["rev_otn"],
            r["rev_otd"], r["total_rev"], r["txn_incentive"],
        ]
        gsheets.append_rows("TRANSACTION", [row])
        st.success(f"Added ✅  Revenue {r['total_rev']:,.2f} | Incentive {r['txn_incentive']:,.2f}")
        st.cache_data.clear()

    st.divider()
    st.subheader("📄 මෑත transactions")
    df_show(gsheets.get_df("TRANSACTION"))


# ═══════════════════════════ ATTENDANCE ═══════════════════════════
elif page == "🕐 Attendance":
    st.header("🕐 Attendance Entry")
    locs = gsheets.get_df("LOCATION-M")
    loc_opts = locs["LOCATION"].tolist() if "LOCATION" in locs else ["EGF"]

    with st.form("att"):
        c1, c2, c3 = st.columns(3)
        with c1:
            date_v = st.date_input("DATE", dt.date.today())
            uid, uname = user_picker("USER", key="att_user")
        with c2:
            in_t = st.time_input("IN TIME", dt.time(8, 0))
            out_t = st.time_input("OUT TIME", dt.time(17, 0))
        with c3:
            loc = st.selectbox("WORK LOCATION", loc_opts)
            lunch = st.number_input("LUNCH & TEA (hrs)", 0.0, 5.0, 1.0, 0.25)

        c4, c5, c6 = st.columns(3)
        wh = c4.number_input("# OF WORKING HRS", 0.0, step=0.5)
        ot = c5.number_input("# OF OT HRS", 0.0, step=0.5)
        util_hrs = c6.number_input("UTILIZED HOURS", 0.0, step=0.5,
                                   help="TRANSACTION වලින් එන utilize hours")
        remark = st.text_input("REMARK", "")
        submitted = st.form_submit_button("➕ Add Attendance", type="primary")

    if submitted and uid:
        util = calc.calc_attendance_utilization(util_hrs, wh)
        udf = _users()
        urow = udf[udf["USER ID"] == uid]
        dept = urow["DEPARTMENT"].iloc[0] if not urow.empty else ""
        subdept = urow["SUB DEPARTMENT"].iloc[0] if not urow.empty and "SUB DEPARTMENT" in urow else ""
        row = [
            unic(date_v, uid), date_v.isoformat(), uid, uname, dept, subdept,
            in_t.strftime("%H:%M"), out_t.strftime("%H:%M"), lunch, loc, "",
            wh, ot, util_hrs, util, date_v.strftime("%a").upper(), remark,
        ]
        gsheets.append_rows("ATTANDANCE", [row])
        st.success(f"Added ✅  Utilization {util:.1%}")
        st.cache_data.clear()

    st.divider()
    df_show(gsheets.get_df("ATTANDANCE"))


# ═══════════════════════════ OT APPROVAL ═══════════════════════════
elif page == "⏱️ OT Approval":
    st.header("⏱️ OT Approval")
    with st.form("ot"):
        c1, c2, c3 = st.columns(3)
        with c1:
            rdate = st.date_input("REQUEST DATE", dt.date.today())
            pdate = st.date_input("OT PLANNED DATE", dt.date.today())
            uid, uname = user_picker("USER", key="ot_user")
        with c2:
            site = st.text_input("SITE", "EGF")
            client = st.text_input("CLIENT", "")
            op = st.text_input("OPERATION", "")
        with c3:
            req_h = st.number_input("REQUEST OT HOURS", 0.0, step=0.5)
            app_h = st.number_input("APPROVED OT HOURS", 0.0, step=0.5)
            person = st.text_input("APPROVED PERSON", "")
        reason = st.text_input("REASON FOR OT", "")
        status = st.selectbox("STATUS", ["Pending", "Approved", "Rejected"])
        submitted = st.form_submit_button("➕ Add OT", type="primary")

    if submitted and uid:
        row = [unic(rdate, uid), rdate.isoformat(), pdate.isoformat(), site,
               client, op, uid, uname, req_h, app_h, person, reason, status]
        gsheets.append_rows("OT APPROVAL", [row])
        st.success("Added ✅")
        st.cache_data.clear()
    st.divider()
    df_show(gsheets.get_df("OT APPROVAL"))


# ═══════════════════════════ COMPLAINT ═══════════════════════════
elif page == "📋 Complaint":
    st.header("📋 Customer Complaint")
    with st.form("comp"):
        c1, c2 = st.columns(2)
        with c1:
            date_v = st.date_input("DATE", dt.date.today())
            uid, uname = user_picker("USER", key="comp_user")
            cust = st.text_input("CUSTOMMER", "")
        with c2:
            tl_id = st.text_input("TEAM LEADER ID", "")
            tl_name = st.text_input("TEAM LEADER NAME", "")
            imp = st.date_input("IMPLIMENT DATE", dt.date.today())
        complaint = st.text_area("COMPLAINT", "")
        c3, c4 = st.columns(2)
        ca = c3.text_input("CA (Corrective Action)", "")
        pa = c4.text_input("PA (Preventive Action)", "")
        submitted = st.form_submit_button("➕ Add Complaint", type="primary")

    if submitted and uid:
        row = [date_v.isoformat(), uid, uname, tl_id, tl_name, cust,
               complaint, ca, pa, imp.isoformat()]
        gsheets.append_rows("CUSTOMMER COMPLAINT", [row])
        st.success("Added ✅")
        st.cache_data.clear()
    st.divider()
    df_show(gsheets.get_df("CUSTOMMER COMPLAINT"))


# ═══════════════════════════ KPI UPDATE ═══════════════════════════
elif page == "✅ KPI Update":
    st.header("✅ KPI Update")
    with st.form("kpi"):
        c1, c2 = st.columns(2)
        with c1:
            date_v = st.date_input("DATE", dt.date.today())
            uid, uname = user_picker("USER", key="kpi_user")
        with c2:
            ontime = st.selectbox("ON TIME UPDATE", ["Y", "N"])
            desc = st.text_input("DESCRIPTION", "")
        submitted = st.form_submit_button("➕ Add KPI Update", type="primary")

    if submitted and uid:
        score = 1 if ontime == "Y" else 0
        row = [date_v.isoformat(), uid, uname, ontime, desc, score]
        gsheets.append_rows("KPI UPDATE", [row])
        st.success("Added ✅")
        st.cache_data.clear()
    st.divider()
    df_show(gsheets.get_df("KPI UPDATE"))


# ═══════════════════════════ INCENTIVE ═══════════════════════════
elif page == "💰 Incentive":
    st.header("💰 Incentive Calculation")
    st.caption(
        f"TXN Incentive = Revenue ÷ {schema.TXN_INCENTIVE_DIVISOR} • "
        f"0-Complaint bonus = {schema.ZERO_COMPLAINT_BONUS} • "
        f"On-time KPI = {schema.ONTIME_KPI_BONUS} • "
        f"100% OT recovery = {schema.FULL_OT_RECOVERY_BONUS} • "
        f"Target = {schema.DEFAULT_TARGET}"
    )
    period = st.text_input("PERIOD label", dt.date.today().strftime("%Y-%m"))

    if st.button("🧮 Calculate Incentive", type="primary"):
        with st.spinner("ගණනය කරනවා…"):
            inc = calc.compute_incentive(
                gsheets.get_df("TRANSACTION"),
                gsheets.get_df("CUSTOMMER COMPLAINT"),
                gsheets.get_df("KPI UPDATE"),
                gsheets.get_df("USER-M"),
                period,
            )
        st.session_state["inc_df"] = inc

    if "inc_df" in st.session_state:
        inc = st.session_state["inc_df"]
        st.dataframe(inc, use_container_width=True, hide_index=True)
        c1, c2 = st.columns(2)
        c1.metric("Total Incentive Payout", f'{inc["TOTAL INSENTIVE"].apply(calc._f).sum():,.0f}')
        c2.metric("Employees", len(inc))
        if st.button("💾 INSENTIVE sheet එකට save කරන්න"):
            gsheets.overwrite("INSENTIVE", inc)
            st.success("INSENTIVE sheet update කළා ✅")
            st.cache_data.clear()


# ═══════════════════════════ MASTERS ═══════════════════════════
elif page == "👥 Masters":
    st.header("👥 Master Data")
    mkey = st.selectbox("Master sheet", schema.MASTER_SHEETS)
    df = gsheets.get_df(mkey)
    st.caption(f"{len(df)} records • cell එකක් double-click කරලා edit කරන්න, "
               "පහල row එකකින් අලුත් record එකක් දාන්න.")
    edited = st.data_editor(df, use_container_width=True, num_rows="dynamic",
                            hide_index=True, key=f"ed_{mkey}")
    if st.button("💾 Save changes", type="primary"):
        gsheets.overwrite(mkey, edited)
        st.success(f"{mkey} update කළා ✅")
        st.cache_data.clear()
