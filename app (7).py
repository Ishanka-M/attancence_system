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

try:
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False   # plotly නැත්නම් gauges වෙනුවට bar chart fall back

import calc
import gsheets
import schema

st.set_page_config(page_title="Central System Support Team KPI System", page_icon="📊", layout="wide")

# ───────────────────────── UI polish (dark theme) ─────────────────────────
st.markdown("""
<style>
:root { --accent:#4da3ff; --accent2:#7c5cff; --card:#161b26; --line:#262e3f; }
.block-container { padding-top: 2.2rem; max-width: 1300px; }
h1, h2, h3 { letter-spacing:.2px; }
h1 { background: linear-gradient(90deg,#4da3ff,#7c5cff);
     -webkit-background-clip:text; -webkit-text-fill-color:transparent;
     font-weight:800; }
/* metric cards */
div[data-testid="stMetric"] {
    background: linear-gradient(160deg,#1a2030,#12161f);
    border:1px solid var(--line); border-radius:16px;
    padding:16px 18px; box-shadow:0 4px 18px rgba(0,0,0,.35);
}
div[data-testid="stMetric"]:hover { border-color:var(--accent); transition:.2s; }
div[data-testid="stMetricValue"] { font-weight:700; }
/* buttons */
.stButton>button, .stDownloadButton>button {
    border-radius:10px; border:1px solid var(--line); font-weight:600;
    transition:.15s;
}
.stButton>button[kind="primary"] {
    background:linear-gradient(90deg,var(--accent),var(--accent2));
    border:none;
}
.stButton>button:hover { transform:translateY(-1px); border-color:var(--accent); }
/* tabs */
button[data-baseweb="tab"] { font-weight:600; }
/* sidebar */
section[data-testid="stSidebar"] {
    background:linear-gradient(180deg,#12161f,#0e1117);
    border-right:1px solid var(--line);
}
section[data-testid="stSidebar"] .stRadio label { padding:3px 0; }
/* dataframes */
div[data-testid="stDataFrame"] { border-radius:12px; overflow:hidden;
    border:1px solid var(--line); }
/* inputs */
div[data-baseweb="select"]>div, .stTextInput input, .stNumberInput input,
.stDateInput input { border-radius:9px !important; }
hr { border-color:var(--line); }
</style>
""", unsafe_allow_html=True)

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
    """Audit dataframe එකක් highlight කරනවා — background + dark text (readable)."""
    if df is None or df.empty:
        return df
    css = f"background-color:{color};color:#1f1f1f"
    return df.style.apply(lambda _: [css] * len(df.columns), axis=1)


def gauge(value, max_value, title, color="#4da3ff", suffix=""):
    """Analog meter (gauge) — dark theme. st.plotly_chart වලින් render කරන්න."""
    mx = max_value if max_value and max_value > 0 else 1
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=float(value or 0),
        number={"suffix": suffix, "font": {"color": "#e8eaed", "size": 28}},
        title={"text": title, "font": {"color": "#aab4c5", "size": 14}},
        gauge={
            "axis": {"range": [0, mx], "tickcolor": "#55607a",
                     "tickfont": {"color": "#8893a8", "size": 9}},
            "bar": {"color": color, "thickness": 0.28},
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 1, "bordercolor": "#262e3f",
            "steps": [
                {"range": [0, mx * 0.5], "color": "#1a2030"},
                {"range": [mx * 0.5, mx * 0.8], "color": "#222a3a"},
                {"range": [mx * 0.8, mx], "color": "#2b3447"},
            ],
            "threshold": {"line": {"color": color, "width": 3},
                          "thickness": 0.8, "value": float(value or 0)},
        },
    ))
    fig.update_layout(height=210, margin=dict(l=15, r=15, t=45, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", font={"color": "#e8eaed"})
    return fig


def user_picker(label="USER", key=None):
    # Normal user නම් තමන්ටම lock. Admin / Leader නම් (team එකෙන්) තෝරන්න පුළුවන්.
    df = _users()
    if not IS_ADMIN and not IS_LEADER and CURRENT_UID:
        st.caption(f"👤 {CURRENT_UID} — {CURRENT_UNAME}")
        return CURRENT_UID, CURRENT_UNAME
    if df.empty:
        st.warning("USER-M හිස්. මුලින් Setup එකෙන් sheets create කරන්න.")
        return None, None
    if not IS_ADMIN and ALLOWED_UIDS is not None:  # leader -> team විතරක්
        df = df[df["USER ID"].astype(str).str.strip().isin(ALLOWED_UIDS)]
    opts = {f'{r["USER ID"]} — {r["USER NAME"]}': (r["USER ID"], r["USER NAME"])
            for _, r in df.iterrows() if str(r.get("USER ID", "")).strip()}
    if not opts:
        st.caption(f"👤 {CURRENT_UID} — {CURRENT_UNAME}")
        return CURRENT_UID, CURRENT_UNAME
    sel = st.selectbox(label, list(opts.keys()), key=key)
    return opts[sel]


def scope_df(df: pd.DataFrame) -> pd.DataFrame:
    """Admin -> සියල්ල. Leader -> team. User -> තමන් විතරක්. (USER ID අනුව)"""
    if IS_ADMIN or ALLOWED_UIDS is None or df is None or df.empty or "USER ID" not in df:
        return df
    return df[df["USER ID"].astype(str).str.strip().isin(ALLOWED_UIDS)]


def df_show(df: pd.DataFrame, n=200, scope=True):
    if scope:
        df = scope_df(df)
    st.dataframe(df.tail(n), use_container_width=True, hide_index=True)


# ───────────────────────── connect ─────────────────────────
st.sidebar.title("📊 Central System Support Team")
st.sidebar.caption("KPI System")

try:
    gsheets.get_spreadsheet()
except Exception as e:
    st.error("Google Sheet සම්බන්ධ වෙන්නෑ — secrets.toml බලන්න.")
    st.exception(e)
    st.stop()

# ───────────────────────── LOGIN ─────────────────────────
ss = st.session_state
ss.setdefault("role", None)      # None | "user" | "admin"
ss.setdefault("uid", "")
ss.setdefault("uname", "")
_admin_pin = str(st.secrets.get("app", {}).get("admin_pin", "")).strip()


def _login_screen():
    st.header("🔐 Login")
    tab_u, tab_a = st.tabs(["👤 User", "🛡️ Admin"])

    with tab_u:
        udf = _users()
        if udf.empty:
            st.warning("USER-M හිස්. Admin login වෙලා Setup → Auto-Create කරන්න.")
        else:
            opts = {f'{r["USER ID"]} — {r["USER NAME"]}': (str(r["USER ID"]).strip(),
                    r.get("USER NAME", ""), str(r.get("PASSWORD", "")).strip())
                    for _, r in udf.iterrows() if str(r.get("USER ID", "")).strip()}
            sel = st.selectbox("USER ID", list(opts.keys()), key="login_uid")
            uid, uname, pw = opts[sel]
            entered = st.text_input("Password", type="password", key="login_upw",
                                    help="Admin password set කරලා නැත්නම් හිස්ව තියලා Login කරන්න.")
            if st.button("Login", type="primary", key="login_ubtn"):
                if pw and entered != pw:
                    st.error("Password වැරදියි.")
                else:
                    ss.role, ss.uid, ss.uname = "user", uid, uname
                    st.rerun()

    with tab_a:
        pin = st.text_input("Admin PIN", type="password", key="login_pin")
        if st.button("Admin Login", type="primary", key="login_abtn"):
            if _admin_pin and pin == _admin_pin:
                ss.role, ss.uid, ss.uname = "admin", "ADMIN", "Administrator"
                st.rerun()
            elif not _admin_pin:
                st.warning("secrets.toml එකේ [app] admin_pin එකක් දාන්න.")
            else:
                st.error("PIN වැරදියි.")


if ss.role is None:
    _login_screen()
    st.stop()

IS_ADMIN = ss.role == "admin"
CURRENT_UID = ss.uid
CURRENT_UNAME = ss.uname

# ── Leader scope: තමන් + assign කරපු team (SUPERVISOR ID අනුව) ──
ALLOWED_UIDS = None  # None = සියල්ල (admin)
IS_LEADER = False
if not IS_ADMIN:
    try:
        ALLOWED_UIDS = calc.team_user_ids(_users(), CURRENT_UID)
    except AttributeError:
        # calc.py පරණ version එකක් නම් — normal user විදිහට degrade
        ALLOWED_UIDS = {CURRENT_UID}
    IS_LEADER = len(ALLOWED_UIDS) > 1

# ── sidebar: who + logout ──
_who = "🛡️ Admin" if IS_ADMIN else (f"👔 Leader ({len(ALLOWED_UIDS)} team)"
                                     if IS_LEADER else f"👤 {CURRENT_UID}")
st.sidebar.success(_who + " logged in")
if st.sidebar.button("Logout"):
    ss.role, ss.uid, ss.uname = None, "", ""
    st.rerun()

# ── role අනුව pages ──
if IS_ADMIN:
    PAGES = [
        "🏠 Dashboard", "⚙️ Setup", "📝 Transaction", "🕐 Attendance",
        "⏱️ OT Approval", "📋 Complaint", "✅ KPI Update", "💰 Incentive",
        "💵 Cost/Revenue", "🔍 Audit", "📥 Export", "📤 Upload", "🛡️ Admin",
        "🗂️ Data Manager",
    ]
else:
    PAGES = ["🏠 Dashboard", "📝 Transaction", "🕐 Attendance", "💰 Incentive", "📤 Upload"]
    if IS_LEADER:
        PAGES.insert(4, "🔍 Audit")   # leader -> team Audit

page = st.sidebar.radio("Menu", PAGES, label_visibility="collapsed")


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
    st.header("🏠 Dashboard" + ("" if IS_ADMIN else f" — {CURRENT_UNAME}"))
    try:
        txn = scope_df(gsheets.get_df("TRANSACTION"))
        att = scope_df(gsheets.get_df("ATTANDANCE"))
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
        cur = dt.date.today().strftime("%Y-%m")
        idx = months.index(cur) if cur in months else 0   # current month default
        msel = st.selectbox("මාසය", months, index=idx)
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

        st.subheader("📈 මාසික Revenue trend")
        trend = summ_all.groupby("MONTH")["TOTAL REV"].sum()
        st.line_chart(trend)

    # ── Company-wide meters (current month) — හැම user කෙනෙක්ටම ──
    st.divider()
    this_month = dt.date.today().strftime("%Y-%m")
    full_txn = gsheets.get_df("TRANSACTION")   # unscoped: company-wide

    st.subheader(f"🏢 SITE level — Transaction Volume ({this_month})")
    sv = calc.site_volume_month(full_txn, this_month)
    if sv.empty:
        st.info("මේ මාසෙට transactions නෑ.")
    elif HAS_PLOTLY:
        mx = float(sv["VOLUME"].max())
        cols = st.columns(min(len(sv), 4))
        for i, (_, r) in enumerate(sv.iterrows()):
            with cols[i % len(cols)]:
                st.plotly_chart(gauge(r["VOLUME"], mx, r["SITE"], "#4da3ff"),
                                use_container_width=True, key=f"sv_{i}")
    else:
        st.bar_chart(sv.set_index("SITE")["VOLUME"], color="#4da3ff")

    st.subheader(f"🏆 වැඩිම Transaction කරපු Top 5 ({this_month})")
    top5 = calc.top_users_volume(full_txn, this_month, 5)
    if top5.empty:
        st.info("මේ මාසෙට data නෑ.")
    elif HAS_PLOTLY:
        mx = float(top5["VOLUME"].max())
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        gcolors = ["#ffd700", "#c0c0c0", "#cd7f32", "#4da3ff", "#7c5cff"]
        cols = st.columns(min(len(top5), 5))
        for i, (_, r) in enumerate(top5.iterrows()):
            with cols[i % len(cols)]:
                st.plotly_chart(
                    gauge(r["VOLUME"], mx, f"{medals[i]} {r['USER']}", gcolors[i]),
                    use_container_width=True, key=f"top_{i}")
    else:
        st.bar_chart(top5.set_index("USER")["VOLUME"], color="#ffb454")


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
            lunch = st.number_input("LUNCH & TEA (hrs)", 0.0, 5.0,
                                    float(schema.LUNCH_TEA_HOURS), 0.25)

        # ── auto-compute: WORKING = (OUT−IN) − LUNCH, OT = WORKING − SCHEDULED ──
        in_dt = dt.datetime.combine(date_v, in_t)
        out_dt = dt.datetime.combine(date_v, out_t)
        if out_dt < in_dt:
            out_dt += dt.timedelta(days=1)   # රෑ පහුවෙනවා නම්
        res = calc.compute_attendance(date_v, in_dt.isoformat(" "),
                                      out_dt.isoformat(" "), lunch, 0, holidays)
        wh, ot, sched = res["working"], res["ot"], res["sched"]

        i1, i2, i3, i4 = st.columns(4)
        i1.metric("Working HRS", f"{wh:.2f}", help="(OUT − IN) − LUNCH & TEA")
        i2.metric("OT HRS", f"{ot:.2f}")
        i3.metric("Scheduled", f"{sched:.0f}")
        i4.metric("Day", date_v.strftime("%a"))
        remark = st.text_input("REMARK", "")

        needs_appr, reason = calc.attendance_needs_approval(wh, date_v, holidays)
        if needs_appr:
            st.warning(f"⚠️ Approval ඕනේ: {reason}. "
                       + ("Admin නිසා approve වෙයි." if IS_ADMIN
                          else "PENDING විදිහට save වෙයි."))
        submitted = st.form_submit_button("➕ Add Attendance", type="primary")

    if submitted and uid:
        # UTILIZED HOURS = ඒ user+date එකට TRANSACTION වල utilize එකතුව
        tdf = gsheets.get_df("TRANSACTION")
        util_hrs = 0.0
        if not tdf.empty and {"USER ID", "Date", "UTILIZE HOURS"} <= set(tdf.columns):
            for _, t in tdf.iterrows():
                td = calc._to_date(t.get("Date"))
                if td == date_v and str(t.get("USER ID", "")).strip() == uid:
                    util_hrs += calc._f(t.get("UTILIZE HOURS"))
        util = calc.calc_attendance_utilization(util_hrs, wh)
        udf = _users()
        urow = udf[udf["USER ID"] == uid]
        dept = urow["DEPARTMENT"].iloc[0] if not urow.empty else ""
        subdept = urow["SUB DEPARTMENT"].iloc[0] if not urow.empty and "SUB DEPARTMENT" in urow else ""
        if not needs_appr:
            status, note = schema.APPR_OK, ""
        elif IS_ADMIN:
            status, note = schema.APPR_APPROVED, f"Admin approved: {reason}"
        else:
            status, note = schema.APPR_PENDING, reason
        row = [
            unic(date_v, uid), date_v.isoformat(), uid, uname, dept, subdept,
            in_dt.strftime("%Y-%m-%d %H:%M"), out_dt.strftime("%Y-%m-%d %H:%M"),
            lunch, loc, "", round(wh, 2), round(ot, 2), "", "", "",
            round(util_hrs, 2), util, date_v.strftime("%a").upper(),
            remark, "", sched, status, note,
        ]
        gsheets.append_rows("ATTANDANCE", [row])
        if status == schema.APPR_PENDING:
            st.warning(f"PENDING විදිහට save කළා ⏳ ({reason})")
        else:
            st.success(f"Added ✅  Working {wh:.2f}h · OT {ot:.2f}h · Util {util:.1%}")
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
    st.header("💰 Incentive")
    st.caption(
        f"TXN Incentive = Revenue ÷ {schema.TXN_INCENTIVE_DIVISOR} • "
        f"0-Complaint bonus = {schema.ZERO_COMPLAINT_BONUS} • "
        f"On-time KPI = {schema.ONTIME_KPI_BONUS} • Complaint penalty = "
        f"{schema.COMPLAINT_PENALTY} • Target = {schema.DEFAULT_TARGET}"
    )

    if not IS_ADMIN:
        # User: තමන්ගේ incentive එක විතරක් (live ගණනය)
        inc = calc.compute_incentive(
            scope_df(gsheets.get_df("TRANSACTION")),
            scope_df(gsheets.get_df("CUSTOMMER COMPLAINT")),
            scope_df(gsheets.get_df("KPI UPDATE")),
            _users()[_users()["USER ID"].astype(str).str.strip() == CURRENT_UID],
            dt.date.today().strftime("%Y-%m"),
        )
        if inc.empty:
            st.info("තවම incentive data නෑ.")
        else:
            row = inc.iloc[0]
            c1, c2, c3 = st.columns(3)
            c1.metric("TXN Incentive", f'{calc._f(row["TXN INCENTIVE"]):,.0f}')
            c2.metric("Penalty", f'{calc._f(row["COMPLAINT PENALTY"]):,.0f}')
            c3.metric("TOTAL", f'{calc._f(row["TOTAL INSENTIVE"]):,.0f}')
            st.dataframe(inc, use_container_width=True, hide_index=True)
    else:
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


# ═══════════════════════════ COST / REVENUE ═══════════════════════════
elif page == "💵 Cost/Revenue":
    st.header("💵 Cost & Revenue — User-wise")
    if not IS_ADMIN:
        st.warning("Admin ලට පමණයි.")
        st.stop()
    st.caption("Cost = Basic + OT(N/D) + Fixed Incentive + EPF(12%) + ETF(3%) + Contractor Fee · "
               "Revenue = transactions · Margin = Revenue − Cost. "
               "Salary data 🗂️ Data Manager → SALARY-M එකෙන් දාන්න (BASIC SALARY විතරක් ඇති — OT rates auto).")

    months_default = dt.date.today().strftime("%Y-%m")
    month = st.text_input("Month (YYYY-MM)", months_default)

    if st.button("🧮 Report generate කරන්න", type="primary"):
        rep = calc.cost_revenue_report(
            gsheets.get_df("ATTANDANCE"), gsheets.get_df("TRANSACTION"),
            gsheets.get_df("SALARY-M"), gsheets.get_df("USER-M"),
            _holidays_set(), month)
        st.session_state["cr_rep"] = rep
        st.session_state["cr_month"] = month

    if "cr_rep" in st.session_state:
        rep = st.session_state["cr_rep"]
        if rep.empty:
            st.info("මේ මාසෙට data නෑ.")
        else:
            tot_cost = rep["COST TO COMPANY"].apply(calc._f).sum()
            tot_rev = rep["TOTAL REVENUE"].apply(calc._f).sum()
            tot_margin = rep["MARGIN"].apply(calc._f).sum()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Cost", f"{tot_cost:,.0f}")
            c2.metric("Total Revenue", f"{tot_rev:,.0f}")
            c3.metric("Total Margin", f"{tot_margin:,.0f}",
                      delta=f"{(tot_margin/tot_cost*100 if tot_cost else 0):.0f}%")
            c4.metric("Employees", len(rep))

            # margin <0 highlight (red), >=0 light green
            def _mcolor(row):
                m = calc._f(row["MARGIN"])
                bg = "#3a1d1d" if m < 0 else "#1d3a24"
                return [f"background-color:{bg};color:#e8eaed"] * len(row)
            st.dataframe(rep.style.apply(_mcolor, axis=1),
                         use_container_width=True, hide_index=True)

            import io
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as xw:
                rep.to_excel(xw, sheet_name="Cost-Revenue", index=False)
            st.download_button("⬇️ Excel download", buf.getvalue(),
                               file_name=f"Cost_Revenue_{st.session_state['cr_month']}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            st.subheader("📊 Cost vs Revenue (user)")
            chart = rep.set_index("CSS USER NAME")[["COST TO COMPANY", "TOTAL REVENUE"]]
            st.bar_chart(chart)


# ═══════════════════════════ AUDIT ═══════════════════════════
elif page == "🔍 Audit":
    st.header("🔍 Audit — Rule Violations"
              + ("" if IS_ADMIN else " (ඔයාගේ team)"))
    try:
        att = scope_df(gsheets.get_df("ATTANDANCE"))   # leader -> team scope
        txn = scope_df(gsheets.get_df("TRANSACTION"))
        users = scope_df(_users())
    except Exception:
        st.info("මුලින් Setup එකෙන් sheets create කරන්න.")
        st.stop()
    holidays = _holidays_set()

    tabs = st.tabs([
        "🚫 20hr+ Cap", "📅 Holiday/Sunday", "⏱️ OT w/o Txn",
        "📈 Weekly OT 15+", "📊 Monthly OT 60+", "❓ Missing Txn",
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
        st.caption("Scheduled time එකට වඩා වැඩ කරලා, ඒ දවසට OT-N/OT-D transaction "
                   "(එක line එකක් හරි) නැති.")
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

    # 5) Monthly OT > 60
    with tabs[4]:
        st.caption(f"මාසෙකට # OF OT HRS > {schema.MONTHLY_OT_CAP}.")
        d6 = calc.audit_monthly_ot(att)
        if d6.empty:
            st.success("✅ මාසික OT cap එක ඉක්මවලා නෑ.")
        else:
            st.error(f"⚠️ {len(d6)} user-months — මාසික OT 60+ ඉක්මවලා.")
            st.dataframe(style_flag(d6, "#ffccd5"), use_container_width=True, hide_index=True)

    # 6) Missing transactions for a date
    with tabs[5]:
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


# ═══════════════════════════ UPLOAD ═══════════════════════════
elif page == "📤 Upload":
    st.header("📤 Bulk Upload — ATTANDANCE / TRANSACTION")
    st.caption("Excel (.xlsx) හෝ CSV එකකින් data add කරන්න. **Rules check කරලා "
               "තමයි add කරන්නේ** — violations block / approval වෙයි.")

    target = st.selectbox("Sheet", ["TRANSACTION", "ATTANDANCE"])
    headers = schema.SHEETS[target]["headers"]
    holidays = _holidays_set()

    tmpl = pd.DataFrame(columns=headers)
    st.download_button("⬇️ Template (.csv)",
                       tmpl.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{target}_template.csv", mime="text/csv")

    up = st.file_uploader("File එක", type=["xlsx", "xls", "csv"])
    if up is not None:
        try:
            raw = pd.read_csv(up, dtype=str) if up.name.lower().endswith("csv") \
                else pd.read_excel(up, dtype=str)
        except Exception as e:
            st.error(f"File කියවන්න බෑ: {e}")
            st.stop()
        raw = raw.fillna("")
        st.write(f"කියෙව්වා: {len(raw)} rows")

        # Scope: normal user -> තමන්, leader -> team, admin -> ඕනෑම
        if not IS_ADMIN and ALLOWED_UIDS is not None:
            if "USER ID" not in raw.columns:
                raw["USER ID"] = CURRENT_UID
            else:
                raw["USER ID"] = raw["USER ID"].apply(
                    lambda x: CURRENT_UID if not str(x).strip() else str(x).strip())
            other = raw[~raw["USER ID"].isin(ALLOWED_UIDS)]
            if len(other):
                st.warning(f"⚠️ ඔයාට අදාළ නොවන USER ID rows {len(other)}ක් skip කරනවා — "
                           + ("team එකේ users ට විතරක් upload කරන්න පුළුවන්."
                              if IS_LEADER else "තමන්ගේ data විතරක් upload කරන්න පුළුවන්."))
            raw = raw[raw["USER ID"].isin(ALLOWED_UIDS)]
            if raw.empty:
                st.info("Upload කරන්න rows නෑ.")
                st.stop()

        # ─────────────── TRANSACTION ───────────────
        if target == "TRANSACTION":
            lut = calc.build_tcode_lookup(_tcodes())
            udf = _users()
            uname_lut = {str(r["USER ID"]).strip(): r.get("USER NAME", "")
                         for _, r in udf.iterrows()} if not udf.empty else {}
            aligned = pd.DataFrame({h: (raw[h] if h in raw.columns else "") for h in headers}).astype(object)
            # calculated fields recompute
            for i in aligned.index:
                code = str(aligned.at[i, "T-CODE"]).strip()
                info = lut.get(code, {})
                res = calc.calc_transaction(info, aligned.at[i, "TIME"], aligned.at[i, "# OF TRANSACTION"])
                uid = str(aligned.at[i, "USER ID"]).strip()
                aligned.at[i, "USER NAME"] = aligned.at[i, "USER NAME"] or uname_lut.get(uid, "")
                aligned.at[i, "CSSTR00"] = aligned.at[i, "CSSTR00"] or info.get("desc", "")
                aligned.at[i, "UOM"] = aligned.at[i, "UOM"] or info.get("uom", "")
                aligned.at[i, "SMV"] = res["smv"]
                aligned.at[i, "UTILIZE HOURS"] = res["utilize_hours"]
                aligned.at[i, "REVANUE-NORMAL"] = res["rev_normal"]
                aligned.at[i, "REVANUE-OT -N"] = res["rev_otn"]
                aligned.at[i, "REVANUE-OT -D"] = res["rev_otd"]
                aligned.at[i, "In"] = res["txn_incentive"]
                if not str(aligned.at[i, "UNIC CODE"]).strip():
                    d = calc._to_date(aligned.at[i, "Date"]); uid = str(aligned.at[i, "USER ID"]).strip()
                    if d and uid:
                        aligned.at[i, "UNIC CODE"] = d.strftime("%Y%m%d") + uid

            save_df, disp, errmask = calc.validate_transaction_upload(aligned, lut)
            n_err = int(errmask.sum())
            n_ok = len(save_df) - n_err
            c1, c2 = st.columns(2)
            c1.metric("✅ OK rows", n_ok)
            c2.metric("🚫 Error rows (block)", n_err)

            if n_err:
                st.error("පහත rows වල rule/validation errors — මේවා add වෙන්නේ නෑ:")
                st.dataframe(style_flag(disp[errmask]), use_container_width=True, hide_index=True)

            clean = save_df[~errmask]
            st.subheader(f"Add වෙන {len(clean)} rows (preview)")
            st.dataframe(clean.head(50), use_container_width=True, hide_index=True)

            if len(clean) and st.button(f"⬆️ {len(clean)} clean rows add කරන්න", type="primary"):
                gsheets.append_rows(target, clean.fillna("").astype(str).values.tolist())
                st.success(f"{len(clean)} rows add කළා ✅ ({n_err} error rows skip කළා)")
                st.cache_data.clear()

        # ─────────────── ATTANDANCE ───────────────
        else:
            existing = gsheets.get_df("ATTANDANCE")
            save_df, disp = calc.validate_attendance_upload(
                raw, existing, holidays,
                txn_df=gsheets.get_df("TRANSACTION"), user_df=_users())
            viol_mask = disp["⚠ VIOLATION"].astype(str).str.len() > 0
            pending_mask = save_df["APPROVAL STATUS"] == schema.APPR_PENDING
            n_pending = int(pending_mask.sum())
            c1, c2, c3 = st.columns(3)
            c1.metric("Total", len(save_df))
            c2.metric("✅ OK", int((~pending_mask).sum()))
            c3.metric("⏳ Approval ඕනේ", n_pending)

            if viol_mask.any():
                st.warning("⚠️ Rule violations — මේවා **PENDING** විදිහට add වෙයි "
                           "(Admin → Approvals වලින් approve කරන්න ඕනේ):")
                st.dataframe(
                    style_flag(disp[viol_mask][[
                        "DATE", "USER ID", "# OF WORKING HRS", "SCHEDULED HRS",
                        "# OF OT HRS", "APPROVAL STATUS", "⚠ VIOLATION"]]),
                    use_container_width=True, hide_index=True)

            mode = st.radio("Add mode", [
                "Clean rows විතරක් (violations skip)",
                "ඔක්කොම add — violations PENDING විදිහට",
            ], index=1)

            to_add = save_df if mode.startswith("ඔක්කොම") else save_df[~pending_mask]
            st.subheader(f"Add වෙන {len(to_add)} rows (preview)")
            st.dataframe(to_add.head(50), use_container_width=True, hide_index=True)

            if len(to_add) and st.button(f"⬆️ {len(to_add)} rows add කරන්න", type="primary"):
                gsheets.append_rows("ATTANDANCE", to_add.fillna("").astype(str).values.tolist())
                st.success(f"{len(to_add)} rows add කළා ✅"
                           + (f" ({n_pending} PENDING — approve කරන්න)" if mode.startswith("ඔක්කොම") and n_pending else ""))
                st.cache_data.clear()


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

        # ── calendar date picker එකෙන් add ──
        st.markdown("**📅 අලුත් නිවාඩුවක් add කරන්න**")
        h1, h2, h3, h4 = st.columns([2, 3, 2, 1])
        hdate = h1.date_input("දිනය", dt.date.today(), key="hol_date")
        hdesc = h2.text_input("Description", key="hol_desc")
        htype = h3.selectbox("TYPE", ["Public", "Mercantile", "Special", "Bank"],
                             key="hol_type")
        h4.markdown("<br>", unsafe_allow_html=True)
        if h4.button("➕ Add"):
            iso = hdate.isoformat()
            existing_dates = set(hdf["DATE"].apply(lambda x: (calc._to_date(x) or "")
                                 and calc._to_date(x).isoformat()) ) if not hdf.empty and "DATE" in hdf else set()
            if iso in existing_dates:
                st.warning("ඒ දිනය දැනටමත් තියෙනවා.")
            else:
                gsheets.append_rows("HOLIDAY-M", [[iso, hdesc, htype]])
                st.success(f"{iso} නිවාඩුව add කළා ✅")
                st.cache_data.clear()
                st.rerun()

        st.divider()
        st.markdown("**දැනට තියෙන නිවාඩු දවස්** (table එකේ edit / delete කරන්නත් පුළුවන්)")
        edited = st.data_editor(hdf, num_rows="dynamic", use_container_width=True,
                                hide_index=True, key="hol_ed")
        if st.button("💾 Holidays save", type="primary"):
            gsheets.overwrite("HOLIDAY-M", edited)
            st.success("HOLIDAY-M update කළා ✅")
            st.cache_data.clear()

        st.divider()
        st.caption(f"⚙️ Rules: දවසකට cap {schema.WORKING_HRS_CAP}h · සතියට OT cap "
                   f"{schema.WEEKLY_OT_CAP}h · complaint penalty {schema.COMPLAINT_PENALTY} · "
                   f"schedule (Mon–Fri 8h, Sat 5h, Sun 0h). මේවා schema.py එකේ වෙනස් කරන්න.")


# ═══════════════════════════ DATA MANAGER ═══════════════════════════
elif page == "🗂️ Data Manager":
    st.header("🗂️ Data Manager — Add / Update / Delete")
    if not IS_ADMIN:
        st.warning("Admin ලට පමණයි.")
        st.stop()

    # Admin CRUD කරන්න පුළුවන් sheets — user ඉල්ලපු ඒවා මුලින්
    editable = ["USER-M", "CUSTOMMER-M", "TCODE-M", "CUSTOMMER COMPLAINT"]
    editable += [s for s in (schema.MASTER_SHEETS + schema.TXN_SHEETS)
                 if s not in editable and s != "INSENTIVE"]

    mkey = st.selectbox("Sheet", editable)
    df = gsheets.get_df(mkey)

    # optional search filter (loud datasets වල පහසුවට)
    q = st.text_input("🔎 Search (optional)", "")
    view = df
    if q.strip():
        mask = df.apply(lambda r: r.astype(str).str.contains(q, case=False, na=False).any(), axis=1)
        view = df[mask]

    st.caption(f"{len(df)} records • **cell double-click → edit** · "
               "**පහළ ➕ row → add** · **🗑️ Delete? tick කරලා → delete** · "
               "අන්තිමට 💾 Save.")

    # 🗑️ Delete? column එකක් මුලට දානවා — delete එක පැහැදිලියි
    work = view.copy()
    work.insert(0, "🗑️ Delete?", False)
    edited = st.data_editor(
        work, use_container_width=True, num_rows="dynamic",
        hide_index=True, key=f"ed_{mkey}_{q}",
        column_config={"🗑️ Delete?": st.column_config.CheckboxColumn(
            "🗑️ Delete?", help="මේ row එක delete කරන්න tick කරන්න", default=False)},
    )

    ndel = int(edited["🗑️ Delete?"].fillna(False).sum())
    c1, c2 = st.columns([1, 4])
    if c1.button(f"💾 Save ({ndel} delete)" if ndel else "💾 Save", type="primary"):
        kept = edited[~edited["🗑️ Delete?"].fillna(False)].drop(columns=["🗑️ Delete?"])
        if q.strip():
            # search filter එකක් තිබ්බොත් — පෙන්නපු rows විතරක් replace, ඉතුරු තියාගන්නවා
            hidden = df[~df.index.isin(view.index)]
            final = pd.concat([hidden, kept], ignore_index=True)
        else:
            final = kept.reset_index(drop=True)
        gsheets.overwrite(mkey, final)
        st.success(f"{mkey} update කළා ✅ — {len(final)} rows ({ndel} deleted)")
        st.cache_data.clear()
        st.rerun()
    c2.caption("⚠️ Save කළාම add + edit + delete (ticked rows) එකවර apply වෙනවා.")

    if mkey == "TCODE-M":
        st.info("ℹ️ TCODE-M = මුල් Excel එකේ *Master sheet - Finalized* (SMV/rate engine). "
                "rate column වෙනස් කළාම ඊට පස්සේ එන transactions වලට ඒ අගයම apply වෙනවා.")
