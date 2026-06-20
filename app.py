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


@st.cache_data(ttl=60, show_spinner=False)
def _holidays_set():
    try:
        return calc.holiday_set(gsheets.get_df("HOLIDAY-M"))
    except Exception:
        return set()


def style_flag(df: pd.DataFrame, color="#ffd6d6"):
    """Audit dataframe එකක් මුළුමනින්ම highlight (warning) කරනවා."""
    if df is None or df.empty:
        return df
    return df.style.apply(lambda _: [f"background-color:{color}"] * len(df.columns), axis=1)


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
    "🔍 Audit", "📥 Export", "🛡️ Admin", "👥 Masters",
]
page = st.sidebar.radio("Menu", PAGES, label_visibility="collapsed")

# ── Admin gate (PIN) ──────────────────────────────────────
st.sidebar.divider()
_admin_pin = str(st.secrets.get("app", {}).get("admin_pin", "")).strip()
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False
with st.sidebar.expander("🔑 Admin login", expanded=False):
    if st.session_state.is_admin:
        st.success("Admin mode ON")
        if st.button("Logout"):
            st.session_state.is_admin = False
            st.rerun()
    else:
        pin = st.text_input("Admin PIN", type="password", key="pin_in")
        if st.button("Login"):
            if _admin_pin and pin == _admin_pin:
                st.session_state.is_admin = True
                st.rerun()
            elif not _admin_pin:
                st.warning("secrets.toml එකේ [app] admin_pin එකක් දාන්න.")
            else:
                st.error("PIN වැරදියි.")

IS_ADMIN = st.session_state.is_admin

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

    # computed totals (TOTAL REVANUE column එකක් save කරන්නේ නෑ — මෙතන ගණනය)
    def _rev_total(df):
        if df.empty:
            return 0.0
        s = 0.0
        for c in (schema.T_REV_N, schema.T_REV_OTN, schema.T_REV_OTD):
            if c in df:
                s += df[c].apply(calc._f).sum()
        return s

    total_rev = _rev_total(txn)
    total_inc = txn.get(schema.T_INCENTIVE, pd.Series(dtype=float)).apply(calc._f).sum() if not txn.empty else 0
    total_ot = att.get(schema.A_OT, pd.Series(dtype=float)).apply(calc._f).sum() if not att.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Transactions", f"{len(txn):,}")
    c2.metric("Total Revenue", f"{total_rev:,.0f}")
    c3.metric("Total Incentive", f"{total_inc:,.0f}")
    c4.metric("Total OT Hrs", f"{total_ot:,.1f}")

    st.divider()
    st.subheader("📅 Monthly — User level (OT / Revenue / Cost / Incentive)")
    summ_all = calc.monthly_user_summary(txn, att)
    if summ_all.empty:
        st.info("තවම data නෑ. 📝 Transaction / 🕐 Attendance වලින් දාන්න.")
    else:
        months = sorted(summ_all["MONTH"].unique(), reverse=True)
        msel = st.selectbox("මාසය", months)
        summ = summ_all[summ_all["MONTH"] == msel].drop(columns=["MONTH"])

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("OT Hrs", f'{summ["OT HRS"].sum():,.1f}')
        m2.metric("Revenue", f'{summ["TOTAL REV"].sum():,.0f}')
        m3.metric("Cost (Incentive)", f'{summ["COST"].sum():,.0f}')
        m4.metric("Incentive", f'{summ["INCENTIVE"].sum():,.0f}')

        st.dataframe(
            summ[["USER ID", "USER NAME", "OT HRS", "NORMAL REV", "OT REV",
                  "TOTAL REV", "INCENTIVE", "COST"]],
            use_container_width=True, hide_index=True,
        )
        st.caption("ℹ️ **Cost** = incentive payout (company එකට යන වියදම) කියලා "
                   "assume කරලා. වෙනත් cost basis එකක් (උදා: OT wage) තියෙනවා නම් කියන්න.")

        st.subheader("👤 User එක අනුව Revenue")
        st.bar_chart(summ.set_index("USER NAME")["TOTAL REV"].sort_values(ascending=False).head(15))

        st.subheader("📈 මාසික Revenue trend")
        trend = summ_all.groupby("MONTH")["TOTAL REV"].sum()
        st.line_chart(trend)


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
        # මුල් Excel order: ... CSSTR00(desc) ... In(incentive), Column18, OT -N
        row = [
            unic(date_v, uid), date_v.isoformat(), uid, uname, site, cust,
            code, info.get("desc", ""), time_t, info.get("uom", ""), qty,
            r["smv"], r["utilize_hours"], r["rev_normal"], r["rev_otn"],
            r["rev_otd"], r["txn_incentive"], "", "",
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
    holidays = _holidays_set()

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

        # ── live schedule / rule info ──
        sched = calc.scheduled_hours(date_v, holidays)
        needs_appr, reason = calc.attendance_needs_approval(wh, date_v, holidays)
        i1, i2, i3 = st.columns(3)
        i1.metric("Scheduled HRS", f"{sched:.0f}")
        i2.metric("Day", date_v.strftime("%a"))
        i3.metric("Cap", f"{schema.WORKING_HRS_CAP}")
        if needs_appr:
            st.warning(f"⚠️ Approval ඕනේ: {reason}. "
                       + ("Admin නිසා approve කරලා save කරයි." if IS_ADMIN
                          else "PENDING විදිහට save වෙයි — admin approve කරන්න ඕනේ."))
        submitted = st.form_submit_button("➕ Add Attendance", type="primary")

    if submitted and uid:
        util = calc.calc_attendance_utilization(util_hrs, wh)
        udf = _users()
        urow = udf[udf["USER ID"] == uid]
        dept = urow["DEPARTMENT"].iloc[0] if not urow.empty else ""
        subdept = urow["SUB DEPARTMENT"].iloc[0] if not urow.empty and "SUB DEPARTMENT" in urow else ""
        needs_appr, reason = calc.attendance_needs_approval(wh, date_v, holidays)
        if not needs_appr:
            status, note = schema.APPR_OK, ""
        elif IS_ADMIN:
            status, note = schema.APPR_APPROVED, f"Admin approved: {reason}"
        else:
            status, note = schema.APPR_PENDING, reason
        row = [
            unic(date_v, uid), date_v.isoformat(), uid, uname, dept, subdept,
            in_t.strftime("%H:%M"), out_t.strftime("%H:%M"), lunch, loc, "",
            wh, ot, "", "", "", util_hrs, util, date_v.strftime("%a").upper(),
            remark, "", sched, status, note,
        ]
        gsheets.append_rows("ATTANDANCE", [row])
        if status == schema.APPR_PENDING:
            st.warning(f"PENDING විදිහට save කළා ⏳ — Admin approve කරන තුරු valid නෑ. ({reason})")
        else:
            st.success(f"Added ✅  Utilization {util:.1%}  ·  Status: {status}")
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
        # මුල් Excel 23 columns — capture නොකරන ඒවා හිස්ව තියනවා
        row = [
            unic(rdate, uid), rdate.isoformat(), pdate.isoformat(), site,
            client, op, uid, uname, "", person, req_h, app_h, person, "", "",
            reason, status, app_h, person, "", "", app_h, "",
        ]
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
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Incentive Payout", f'{inc["TOTAL INSENTIVE"].apply(calc._f).sum():,.0f}')
        c2.metric("Total Complaint Penalty", f'{inc["COMPLAINT PENALTY"].apply(calc._f).sum():,.0f}')
        c3.metric("Employees", len(inc))
        if st.button("💾 INSENTIVE sheet එකට save කරන්න"):
            gsheets.overwrite("INSENTIVE", inc)
            st.success("INSENTIVE sheet update කළා ✅")
            st.cache_data.clear()


# ═══════════════════════════ AUDIT ═══════════════════════════
elif page == "🔍 Audit":
    st.header("🔍 Audit — Rule Violations")
    try:
        att = gsheets.get_df("ATTANDANCE")
        txn = gsheets.get_df("TRANSACTION")
        users = _users()
    except Exception:
        st.info("මුලින් Setup එකෙන් sheets create කරන්න.")
        st.stop()
    holidays = _holidays_set()

    tabs = st.tabs([
        "🚫 20hr+ Cap", "📅 Holiday/Sunday", "⏱️ OT w/o Txn",
        "📈 Weekly OT 15+", "❓ Missing Txn",
    ])

    # 1) Working hours > 20 without approval
    with tabs[0]:
        st.caption(f"# OF WORKING HRS > {schema.WORKING_HRS_CAP}, approve කරලා නැති rows.")
        d1 = calc.audit_working_hours_cap(att)
        if d1.empty:
            st.success("✅ Violations නෑ.")
        else:
            st.error(f"⚠️ {len(d1)} rows — admin approval ඕනේ.")
            st.dataframe(style_flag(d1), use_container_width=True, hide_index=True)

    # 2) Holiday / Sunday attendance
    with tabs[1]:
        st.caption("ඉරිදා / admin නිවාඩු දවස් වලට attendance (approve කරලා නැති).")
        d2 = calc.audit_holiday_attendance(att, holidays)
        if d2.empty:
            st.success("✅ Violations නෑ.")
        else:
            st.error(f"⚠️ {len(d2)} rows.")
            st.dataframe(style_flag(d2, "#ffe9c7"), use_container_width=True, hide_index=True)

    # 3) OT worked but no OT transaction
    with tabs[2]:
        st.caption("Scheduled time එකට වඩා වැඩ කරලා, ඒ දවසට OT-N/OT-D transaction නැති.")
        d3 = calc.audit_ot_without_transaction(att, txn, holidays)
        if d3.empty:
            st.success("✅ හැම OT එකකටම transaction තියෙනවා.")
        else:
            st.error(f"⚠️ {len(d3)} rows — OT transaction missing.")
            cols = [c for c in ["DATE", "USER ID", "USER NAME", "# OF WORKING HRS",
                                "SCHEDULED HRS", "# OF OT HRS", "EXTRA HRS", "ISSUE"]
                    if c in d3.columns]
            st.dataframe(style_flag(d3[cols]), use_container_width=True, hide_index=True)

    # 4) Weekly OT > 15
    with tabs[3]:
        st.caption(f"සතියකට # OF OT HRS > {schema.WEEKLY_OT_CAP}.")
        d4 = calc.audit_weekly_ot(att)
        if d4.empty:
            st.success("✅ සතියේ OT cap එක ඉක්මවලා නෑ.")
        else:
            st.error(f"⚠️ {len(d4)} user-weeks.")
            st.dataframe(style_flag(d4, "#ffd6d6"), use_container_width=True, hide_index=True)

    # 5) Missing transactions for a date
    with tabs[4]:
        adate = st.date_input("දිනය", dt.date.today(), key="audit_missing_date")
        d5 = calc.audit_missing_transactions(users, txn, adate)
        if d5.empty:
            st.success("✅ හැම active user කෙනෙක්ම transaction දාලා.")
        else:
            st.error(f"⚠️ {len(d5)} users — {adate.isoformat()} දිනට transaction නෑ.")
            st.dataframe(style_flag(d5, "#e0e0ff"), use_container_width=True, hide_index=True)


# ═══════════════════════════ EXPORT ═══════════════════════════
elif page == "📥 Export":
    st.header("📥 Export — ATTANDANCE / TRANSACTION")
    st.caption("Date range + user අනුව filter කරලා Excel/CSV download කරන්න. "
               "Format එක මුල් Excel එකේ විදිහටමයි.")

    c1, c2, c3 = st.columns(3)
    with c1:
        which = st.multiselect("Sheets", ["TRANSACTION", "ATTANDANCE"],
                               default=["TRANSACTION", "ATTANDANCE"])
    with c2:
        d_from = st.date_input("From", dt.date.today().replace(day=1))
    with c3:
        d_to = st.date_input("To", dt.date.today())

    # user level filter
    udf = _users()
    user_map = {"ALL — සියලුම users": "ALL"}
    if not udf.empty:
        for _, r in udf.iterrows():
            uid = str(r.get("USER ID", "")).strip()
            if uid:
                user_map[f'{uid} — {r.get("USER NAME","")}'] = uid
    usel = st.selectbox("User level", list(user_map.keys()))
    uid_filter = user_map[usel]

    if st.button("🔎 Filter", type="primary"):
        import io
        result = {}
        for key in which:
            date_col = schema.T_DATE if key == "TRANSACTION" else schema.A_DATE
            df = gsheets.get_df(key)
            result[key] = calc.filter_by_range(df, date_col, d_from, d_to, uid_filter)
        st.session_state["export"] = result

    if "export" in st.session_state:
        result = st.session_state["export"]
        import io
        for key, df in result.items():
            st.subheader(f"{key} — {len(df)} rows")
            st.dataframe(df.head(100), use_container_width=True, hide_index=True)
            st.download_button(
                f"⬇️ {key} CSV", df.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{key}_{d_from}_{d_to}.csv", mime="text/csv",
                key=f"csv_{key}")

        # combined Excel (original format, sheet per tab)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            for key, df in result.items():
                df.to_excel(xw, sheet_name=key[:31], index=False)
        st.download_button(
            "⬇️ Excel (.xlsx) — ඔක්කොම", buf.getvalue(),
            file_name=f"KPI_export_{d_from}_{d_to}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ═══════════════════════════ ADMIN ═══════════════════════════
elif page == "🛡️ Admin":
    st.header("🛡️ Admin")
    if not IS_ADMIN:
        st.warning("මේ page එක admin ලට පමණයි. Sidebar එකේ 🔑 Admin login එකෙන් PIN දාන්න.")
        st.stop()

    a1, a2 = st.tabs(["✅ Attendance Approvals", "📅 Holiday Setup"])

    # ── Pending attendance approvals ──
    with a1:
        st.subheader("PENDING attendance approvals")
        att = gsheets.get_df("ATTANDANCE")
        if "APPROVAL STATUS" not in att or att.empty:
            st.info("Attendance data නෑ.")
        else:
            pend = att[att["APPROVAL STATUS"].astype(str).str.upper() == schema.APPR_PENDING]
            if pend.empty:
                st.success("✅ Pending approvals නෑ.")
            else:
                st.error(f"{len(pend)} pending.")
                show_cols = [c for c in ["UNIC CODE", "DATE", "USER ID", "USER NAME",
                                         "# OF WORKING HRS", "APPROVAL NOTE"] if c in pend.columns]
                st.dataframe(pend[show_cols], use_container_width=True, hide_index=True)

                pick = st.selectbox("UNIC CODE තෝරන්න", pend["UNIC CODE"].tolist())
                c1, c2 = st.columns(2)
                if c1.button("✅ Approve", type="primary"):
                    att.loc[att["UNIC CODE"] == pick, "APPROVAL STATUS"] = schema.APPR_APPROVED
                    gsheets.overwrite("ATTANDANCE", att)
                    st.success(f"{pick} approved ✅")
                    st.cache_data.clear()
                    st.rerun()
                if c2.button("❌ Reject"):
                    att.loc[att["UNIC CODE"] == pick, "APPROVAL STATUS"] = schema.APPR_REJECTED
                    gsheets.overwrite("ATTANDANCE", att)
                    st.warning(f"{pick} rejected")
                    st.cache_data.clear()
                    st.rerun()

    # ── Holiday setup ──
    with a2:
        st.subheader("නිවාඩු දවස් setup")
        st.caption("මෙතන දාන දවස් වලට scheduled hours = 0. ඒ දවස් වලට attendance "
                   "දාන්න admin approval ඕනේ වෙයි.")
        hdf = gsheets.get_df("HOLIDAY-M")
        edited = st.data_editor(hdf, num_rows="dynamic", use_container_width=True,
                                hide_index=True, key="hol_ed",
                                column_config={"DATE": st.column_config.TextColumn(
                                    "DATE (YYYY-MM-DD)")})
        if st.button("💾 Holidays save", type="primary"):
            gsheets.overwrite("HOLIDAY-M", edited)
            st.success("HOLIDAY-M update කළා ✅")
            st.cache_data.clear()

        st.divider()
        st.caption(f"⚙️ Rules: දවසකට cap {schema.WORKING_HRS_CAP}h · සතියට OT cap "
                   f"{schema.WEEKLY_OT_CAP}h · complaint penalty {schema.COMPLAINT_PENALTY} · "
                   f"schedule (Mon–Fri 8h, Sat 5h, Sun 0h). මේවා schema.py එකේ වෙනස් කරන්න.")


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
