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
/* footer brand */
.brand-foot { text-align:center; color:#6b7588; font-size:12px; padding:10px 0 4px;
    border-top:1px solid var(--line); margin-top:14px; }
.brand-foot b { color:#8fa6c8; }

/* ───────── Mobile responsive ───────── */
@media (max-width: 680px){
    .block-container { padding:1rem .6rem 3rem; }
    h1 { font-size:1.45rem; } h2 { font-size:1.2rem; } h3 { font-size:1.05rem; }
    div[data-testid="stMetric"] { padding:10px 12px; border-radius:12px; }
    div[data-testid="stMetricValue"] { font-size:1.15rem; }
    div[data-testid="stMetricLabel"] { font-size:.72rem; }
    /* columns -> wrap (2 per row) instead of squeezing side-by-side */
    div[data-testid="stHorizontalBlock"] { flex-wrap:wrap; gap:.45rem; }
    div[data-testid="stHorizontalBlock"]>div[data-testid="column"] {
        min-width:47% !important; flex:1 1 47% !important;
    }
    button[data-baseweb="tab"] { font-size:.8rem; padding:0 8px; }
}
</style>
""", unsafe_allow_html=True)

# ── version check: stale calc.py/schema.py partial-deploy එකකදී පැහැදිලි message ──
_REQUIRED_CALC = [
    "unic_serial", "fmt_date", "fmt_datetime", "excel_serial", "team_user_ids",
    "compute_attendance", "cost_revenue_report", "audit_monthly_ot",
    "validate_attendance_upload", "site_volume_month", "top_users_volume",
    "top_users_revenue", "ot_report", "recompute_attendance_df",
    "recompute_transaction_df", "bulk_attendance_rows", "date_range_list",
    "data_audit_attendance", "fix_attendance_df", "data_audit_transaction",
]
_missing = [f for f in _REQUIRED_CALC if not hasattr(calc, f)]
if not hasattr(gsheets, "upsert_rows"):
    _missing.append("gsheets.upsert_rows")
if _missing:
    st.error(
        "⚠️ An old file version is deployed. Push **calc.py "
        "(and schema.py) latest version**, then Streamlit Cloud → Manage app "
        "→ click **Reboot**.\n\n"
        f"Missing functions in calc.py: `{', '.join(_missing)}`"
    )
    st.stop()

TIME_OPTIONS = [schema.TIME_NORMAL, schema.TIME_OT_N, schema.TIME_OT_D]


# ───────────────────────── helpers ─────────────────────────
def unic(date_val, user_id: str) -> str:
    """UNIC CODE = Excel serial + USER ID — same in both ATTANDANCE and TRANSACTION,
    so a day's attendance and transactions can be matched by UNIC CODE."""
    return calc.unic_serial(date_val, user_id)


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
    """Highlights an audit dataframe — background + dark text (readable)."""
    if df is None or df.empty:
        return df
    css = f"background-color:{color};color:#1f1f1f"
    return df.style.apply(lambda _: [css] * len(df.columns), axis=1)


def _clear_audit_rows(unics, status, note):
    """Updates APPROVAL STATUS + NOTE for the matching UNIC CODE rows to clear them."""
    df = gsheets.get_df("ATTANDANCE")
    if df.empty or "UNIC CODE" not in df.columns:
        return 0
    df = df.astype(object)
    m = df["UNIC CODE"].astype(str).str.strip().isin([str(u).strip() for u in unics])
    df.loc[m, "APPROVAL STATUS"] = status
    df.loc[m, "APPROVAL NOTE"] = note
    gsheets.overwrite("ATTANDANCE", df)
    return int(m.sum())


def gauge(value, max_value, title, color="#4da3ff", suffix=""):
    """Analog meter (gauge) — dark theme. Render with st.plotly_chart."""
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
        st.warning("USER-M is empty. Create sheets from Setup first.")
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
    """Admin -> all. Leader -> team. User -> own only. (by USER ID)"""
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
    st.error("Cannot connect to Google Sheet — check secrets.toml.")
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
            st.warning("USER-M is empty. Login as admin and run Setup → Auto-Create.")
        else:
            opts = {f'{r["USER ID"]} — {r["USER NAME"]}': (str(r["USER ID"]).strip(),
                    r.get("USER NAME", ""), str(r.get("PASSWORD", "")).strip())
                    for _, r in udf.iterrows() if str(r.get("USER ID", "")).strip()}
            sel = st.selectbox("USER ID", list(opts.keys()), key="login_uid")
            uid, uname, pw = opts[sel]
            entered = st.text_input("Password", type="password", key="login_upw",
                                    help="If no admin password is set, leave blank and Login.")
            if st.button("Login", type="primary", key="login_ubtn"):
                if pw and entered != pw:
                    st.error("Incorrect password.")
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
                st.warning("Add an [app] admin_pin in secrets.toml.")
            else:
                st.error("Incorrect PIN.")


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
        "🏠 Dashboard", "🎛️ Meters", "⚙️ Setup", "📝 Transaction", "🕐 Attendance",
        "⏱️ OT Approval", "📋 Complaint", "✅ KPI Update", "💰 Incentive",
        "💵 Cost/Revenue", "🕒 OT Report", "🔍 Audit", "🧪 Data Audit",
        "📥 Export", "📤 Upload", "🛡️ Admin", "🗂️ Data Manager",
    ]
else:
    PAGES = ["🏠 Dashboard", "🎛️ Meters", "📝 Transaction", "🕐 Attendance",
             "💰 Incentive", "🔍 Audit", "🗂️ Data Manager", "📤 Upload"]

page = st.sidebar.radio("Menu", PAGES, label_visibility="collapsed")

# ── Data Audit notification: user who entered wrong data gets notified ──
MY_DATA_ISSUES = None
if not IS_ADMIN:
    try:
        _ia = calc.data_audit_attendance(scope_df(gsheets.get_df("ATTANDANCE")),
                                         _holidays_set(), gsheets.get_df("TRANSACTION"))
        _it = calc.data_audit_transaction(scope_df(gsheets.get_df("TRANSACTION")),
                                          calc.build_tcode_lookup(_tcodes()))
        MY_DATA_ISSUES = (_ia, _it)
        _ni = len(_ia) + len(_it)
        if _ni:
            st.sidebar.warning(f"⚠️ Data Audit: {_ni} issue(s) in your records — "
                               "see 🏠 Dashboard.")
    except Exception:
        MY_DATA_ISSUES = None

# ── footer brand ──
st.sidebar.markdown(
    "<div class='brand-foot'>Development by <b>Ishanka Madusanka</b></div>",
    unsafe_allow_html=True)


# ═══════════════════════════ SETUP ═══════════════════════════
if page == "⚙️ Setup":
    st.header("⚙️ Setup — Google Sheet Auto-Create")
    st.write(
        "Click the button below and **every tab in the schema is created in the Google Sheet "
        "are **auto-created**, headers added, and master sheets "
        "(USER-M, TCODE-M, SITE-M, CUSTOMMER-M, TIME-M, LOCATION-M) "
        "seeds data from the original Excel."
    )

    col1, col2 = st.columns(2)
    seed = col1.checkbox("Seed masters (T-codes, Users, Sites…)", value=True)
    if col2.button("🚀 Sheets Auto-Create / Sync", type="primary"):
        with st.spinner("Creating sheets…"):
            created = gsheets.ensure_all(seed_masters=seed)
        if created:
            st.success(f"Newly created sheets: {', '.join(created)}")
        else:
            st.info("All sheets already exist ✅")
        st.cache_data.clear()

    st.divider()
    st.subheader("📋 Current status")
    try:
        st.dataframe(gsheets.sheet_status(), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning("Create sheets before checking status.")
        st.caption(str(e))


# ═══════════════════════════ DASHBOARD ═══════════════════════════
elif page == "🏠 Dashboard":
    st.header("🏠 Dashboard" + ("" if IS_ADMIN else f" — {CURRENT_UNAME}"))
    try:
        txn = scope_df(gsheets.get_df("TRANSACTION"))
        att = scope_df(gsheets.get_df("ATTANDANCE"))
    except Exception:
        st.info("Create sheets from Setup first.")
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

    # ── 🧪 Data Audit notification — "you entered wrong data" ──
    if not IS_ADMIN and MY_DATA_ISSUES is not None:
        _ia, _it = MY_DATA_ISSUES
        _ni = len(_ia) + len(_it)
        if _ni:
            st.error(f"🧪 **Data Audit notice:** You entered some incorrect data — "
                     f"{_ni} issue(s) found in your records. Please review and correct "
                     "them (or contact your admin).")
            with st.expander(f"See the {_ni} data issue(s)", expanded=False):
                if not _ia.empty:
                    st.markdown("**Attendance**")
                    st.dataframe(style_flag(_ia[["DATE", "FIELD", "CURRENT", "EXPECTED", "ISSUE"]], "#ffe0e0"),
                                 use_container_width=True, hide_index=True)
                if not _it.empty:
                    st.markdown("**Transaction**")
                    st.dataframe(style_flag(_it[["DATE", "T-CODE", "FIELD", "CURRENT", "EXPECTED", "ISSUE"]], "#ffe0e0"),
                                 use_container_width=True, hide_index=True)

    # ── 🔍 Audit panel (මාසේ 1 → today, scoped) — violations තියෙනවා නම් විතරක් ──
    _hol = _holidays_set()
    _t = dt.date.today(); _ms = _t.replace(day=1)
    _att = calc.filter_by_range(att, schema.A_DATE, _ms, _t)
    _txn = calc.filter_by_range(txn, schema.T_DATE, _ms, _t)
    _checks = {
        "20hr+ Cap": calc.audit_working_hours_cap(_att),
        "Holiday/Sunday": calc.audit_holiday_attendance(_att, _hol),
        "OT w/o Txn": calc.audit_ot_without_transaction(_att, _txn, _hol),
        "Weekly OT 15+": calc.audit_weekly_ot(_att),
        "Monthly OT 60+": calc.audit_monthly_ot(_att),
    }
    _counts = {k: len(v) for k, v in _checks.items() if not v.empty}
    _total = sum(_counts.values())
    if _total:
        with st.expander(f"🔍 Audit — Rule Violations ({_total}) · {calc.fmt_date(_ms)}–{calc.fmt_date(_t)}",
                         expanded=True):
            st.caption("1st of month to today — details in the 🔍 Audit page.")
            _bc = st.columns(len(_counts))
            for _i, (_k, _n) in enumerate(_counts.items()):
                _bc[_i].metric(_k, _n)
            for _k, _v in _checks.items():
                if not _v.empty:
                    st.markdown(f"**{_k}** ({len(_v)})")
                    st.dataframe(style_flag(_v.head(20)), use_container_width=True, hide_index=True)
    else:
        st.success("🔍 Audit — No Rule Violations ✅ (1st → today)")

    st.divider()
    st.subheader("📅 Monthly — User level (OT / Revenue / Cost / Incentive)")
    summ_all = calc.monthly_user_summary(txn, att)
    if summ_all.empty:
        st.info("No data yet. Add via 📝 Transaction / 🕐 Attendance.")
    else:
        months = sorted(summ_all["MONTH"].unique(), reverse=True)
        cur = dt.date.today().strftime("%Y-%m")
        idx = months.index(cur) if cur in months else 0   # current month default
        msel = st.selectbox("Month", months, index=idx)
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


# ═══════════════════════════ METERS (analog) ═══════════════════════════
elif page == "🎛️ Meters":
    st.header("🎛️ Analog Meters")
    this_month = dt.date.today().strftime("%Y-%m")
    st.caption(f"Company-wide · {this_month} · for everyone")
    full_txn = gsheets.get_df("TRANSACTION")   # unscoped: company-wide

    st.subheader("🏢 SITE level — Transaction Volume")
    sv = calc.site_volume_month(full_txn, this_month)
    if sv.empty:
        st.info("No transactions this month.")
    elif HAS_PLOTLY:
        mx = float(sv["VOLUME"].max())
        cols = st.columns(min(len(sv), 4))
        for i, (_, r) in enumerate(sv.iterrows()):
            with cols[i % len(cols)]:
                st.plotly_chart(gauge(r["VOLUME"], mx, r["SITE"], "#4da3ff"),
                                use_container_width=True, key=f"sv_{i}")
    else:
        st.bar_chart(sv.set_index("SITE")["VOLUME"], color="#4da3ff")

    st.divider()
    st.subheader("🏆 Top 5 by Transactions")
    top5 = calc.top_users_volume(full_txn, this_month, 5)
    if top5.empty:
        st.info("No data this month.")
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

    st.divider()
    st.subheader("💰 Top 5 by Revenue")
    topr = calc.top_users_revenue(full_txn, this_month, 5)
    if topr.empty:
        st.info("No data this month.")
    elif HAS_PLOTLY:
        mx = float(topr["REVENUE"].max())
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        gcolors = ["#ffd700", "#c0c0c0", "#cd7f32", "#46d39a", "#4da3ff"]
        cols = st.columns(min(len(topr), 5))
        for i, (_, r) in enumerate(topr.iterrows()):
            with cols[i % len(cols)]:
                st.plotly_chart(
                    gauge(r["REVENUE"], mx, f"{medals[i]} {r['USER']}", gcolors[i]),
                    use_container_width=True, key=f"topr_{i}")
    else:
        st.bar_chart(topr.set_index("USER")["REVENUE"], color="#46d39a")


# ═══════════════════════════ TRANSACTION ═══════════════════════════
elif page == "📝 Transaction":
    st.header("📝 Transaction Entry")
    tdf = _tcodes()
    if tdf.empty:
        st.warning("TCODE-M is empty. Seed it from Setup.")
        st.stop()
    lut = calc.build_tcode_lookup(tdf)

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

    submitted = st.button("➕ Add Transaction", type="primary")

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
    st.subheader("📊 Transaction Summary")
    _tx = scope_df(gsheets.get_df("TRANSACTION"))
    _tt = dt.date.today()
    _tx = calc.filter_by_range(_tx, schema.T_DATE, _tt.replace(day=1), _tt)
    if _tx.empty:
        st.info("No transactions this month.")
    else:
        _rev = sum(_tx[c].apply(calc._f).sum() for c in
                   (schema.T_REV_N, schema.T_REV_OTN, schema.T_REV_OTD) if c in _tx)
        _inc = _tx[schema.T_INCENTIVE].apply(calc._f).sum() if schema.T_INCENTIVE in _tx else 0
        _qty = _tx[schema.T_QTY].apply(calc._f).sum() if schema.T_QTY in _tx else 0
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Transactions", f"{len(_tx):,}")
        s2.metric("Total Volume", f"{_qty:,.0f}")
        s3.metric("Total Revenue", f"{_rev:,.2f}")
        s4.metric("Incentive", f"{_inc:,.2f}")
        st.caption(f"This month ({calc.fmt_date(_tt.replace(day=1))} – {calc.fmt_date(_tt)})")

        # by TIME (Normal/OT-N/OT-D)
        if schema.T_TIME in _tx:
            _g = _tx.copy()
            _g["_rev"] = sum(_g[c].apply(calc._f) for c in
                             (schema.T_REV_N, schema.T_REV_OTN, schema.T_REV_OTD) if c in _g)
            _by = _g.groupby(_g[schema.T_TIME].astype(str)).agg(
                Transactions=(schema.T_TIME, "size"), Revenue=("_rev", "sum")).reset_index()
            _by.columns = ["TIME", "Transactions", "Revenue"]
            st.dataframe(_by, use_container_width=True, hide_index=True)


# ═══════════════════════════ ATTENDANCE ═══════════════════════════
elif page == "🕐 Attendance":
    st.header("🕐 Attendance Entry")
    locs = gsheets.get_df("LOCATION-M")
    loc_opts = locs["LOCATION"].tolist() if "LOCATION" in locs else ["EGF"]
    holidays = _holidays_set()

    entry_type = st.radio("Entry type", ["💼 Work", "🌴 Leave"],
                          horizontal=True, key="att_entry_type")

    if entry_type == "🌴 Leave":
        with st.form("att_leave"):
            lc1, lc2 = st.columns(2)
            with lc1:
                date_v = st.date_input("DATE", dt.date.today(), key="lv_date")
                uid, uname = user_picker("USER", key="lv_user")
            with lc2:
                leave_type = st.selectbox("Leave Type", [
                    "Annual Leave", "Casual Leave", "Medical Leave",
                    "No-Pay Leave", "Half Day", "Off"])
                lv_remark = st.text_input("REMARK", "", key="lv_remark")
            sub_lv = st.form_submit_button("➕ Add Leave", type="primary")
        if sub_lv and uid:
            udf = _users(); urow = udf[udf["USER ID"] == uid]
            dept = urow["DEPARTMENT"].iloc[0] if not urow.empty else ""
            subdept = urow["SUB DEPARTMENT"].iloc[0] if not urow.empty and "SUB DEPARTMENT" in urow else ""
            sched = calc.scheduled_hours(date_v, holidays)
            rmk = leave_type + (f" — {lv_remark}" if lv_remark else "")
            row = [calc.unic_serial(date_v, uid), calc.fmt_date(date_v), uid, uname,
                   dept, subdept, "", "", 0, "LEAVE", "", 0, 0, "", "", "", 0, 0,
                   date_v.strftime("%a").upper(), rmk, "", sched, schema.APPR_OK, "Leave"]
            added, updated = gsheets.upsert_rows("ATTANDANCE", [row], "UNIC CODE")
            st.success(f"🌴 Leave {'updated' if updated else 'added'} ✅ — {leave_type} ({calc.fmt_date(date_v)})")
            st.cache_data.clear()

    else:
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
        # WORK LOCATION = LEAVE/OFF -> working day නෙවෙයි
        if calc.is_non_work_location(loc):
            wh, ot = 0.0, 0.0
            st.info(f"ℹ️ '{loc}' is a non-working location — working/OT counted as 0.")

        i1, i2, i3, i4 = st.columns(4)
        i1.metric("Working HRS", f"{wh:.2f}", help="(OUT − IN) − LUNCH & TEA")
        i2.metric("OT HRS", f"{ot:.2f}")
        i3.metric("Scheduled", f"{sched:.0f}")
        i4.metric("Day", date_v.strftime("%a"))
        remark = st.text_input("REMARK", "")

        needs_appr, reason = calc.attendance_needs_approval(wh, date_v, holidays)
        if needs_appr:
            st.warning(f"⚠️ Needs approval: {reason}. "
                       + ("Will be approved as admin." if IS_ADMIN
                          else "Will be saved as PENDING."))
        submitted = st.button("➕ Add Attendance", type="primary")

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
                calc.unic_serial(date_v, uid), calc.fmt_date(date_v), uid, uname, dept, subdept,
                calc.fmt_datetime(in_dt), calc.fmt_datetime(out_dt),
                lunch, loc, "", round(wh, 2), round(ot, 2), "", "", "",
                round(util_hrs, 2), util, date_v.strftime("%a").upper(),
                remark, "", sched, status, note,
            ]
            added, updated = gsheets.upsert_rows("ATTANDANCE", [row], "UNIC CODE")
            _verb = "Updated" if updated else "Added"
            if status == schema.APPR_PENDING:
                st.warning(f"{_verb} as PENDING ⏳ ({reason})")
            else:
                st.success(f"{_verb} ✅  Working {wh:.2f}h · OT {ot:.2f}h · Util {util:.1%}"
                           + (" (updated existing row)" if updated else ""))
            st.cache_data.clear()

    st.divider()
    st.subheader("📊 Attendance Summary")
    _at = scope_df(gsheets.get_df("ATTANDANCE"))
    _t = dt.date.today()
    _at = calc.filter_by_range(_at, schema.A_DATE, _t.replace(day=1), _t)
    if _at.empty:
        st.info("No attendance this month.")
    else:
        _wh = _at["# OF WORKING HRS"].apply(calc._f).sum() if "# OF WORKING HRS" in _at else 0
        _ot = _at["# OF OT HRS"].apply(calc._f).sum() if "# OF OT HRS" in _at else 0
        _leave = int((_at.get("WORCK LOCATION", pd.Series(dtype=str)).astype(str).str.upper() == "LEAVE").sum())
        _pend = int((_at.get("APPROVAL STATUS", pd.Series(dtype=str)).astype(str).str.upper() == schema.APPR_PENDING).sum())
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Records", f"{len(_at):,}")
        s2.metric("Working Hrs", f"{_wh:,.1f}")
        s3.metric("OT Hrs", f"{_ot:,.1f}")
        s4.metric("Pending", f"{_pend}")
        st.caption(f"This month ({calc.fmt_date(_t.replace(day=1))} – {calc.fmt_date(_t)}) · "
                   f"Leave days: {_leave}")

        # per-user working/OT summary
        if "USER ID" in _at:
            _g = _at.copy()
            _g["_w"] = _g["# OF WORKING HRS"].apply(calc._f)
            _g["_o"] = _g["# OF OT HRS"].apply(calc._f)
            _by = _g.groupby(["USER ID", "USER NAME"]).agg(
                Days=("USER ID", "size"), Working=("_w", "sum"), OT=("_o", "sum")
            ).reset_index().sort_values("OT", ascending=False)
            st.dataframe(_by, use_container_width=True, hide_index=True)


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
            st.info("No incentive data yet.")
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
            with st.spinner("Calculating…"):
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
            if st.button("💾 Save to INSENTIVE sheet"):
                gsheets.overwrite("INSENTIVE", inc)
                st.success("INSENTIVE sheet updated ✅")
                st.cache_data.clear()


# ═══════════════════════════ COST / REVENUE ═══════════════════════════
elif page == "💵 Cost/Revenue":
    st.header("💵 Cost & Revenue — User-wise")
    if not IS_ADMIN:
        st.warning("Admins only.")
        st.stop()
    st.caption("Cost = Basic + OT(N/D) + Fixed Incentive + EPF(12%) + ETF(3%) + Contractor Fee · "
               "Revenue = transactions · Margin = Revenue − Cost. "
               "Enter salary data via 🗂️ Data Manager → SALARY-M (BASIC SALARY is enough — OT rates auto).")

    months_default = dt.date.today().strftime("%Y-%m")
    month = st.text_input("Month (YYYY-MM)", months_default)

    if st.button("🧮 Generate report", type="primary"):
        rep = calc.cost_revenue_report(
            gsheets.get_df("ATTANDANCE"), gsheets.get_df("TRANSACTION"),
            gsheets.get_df("SALARY-M"), gsheets.get_df("USER-M"),
            _holidays_set(), month)
        st.session_state["cr_rep"] = rep
        st.session_state["cr_month"] = month

    if "cr_rep" in st.session_state:
        rep = st.session_state["cr_rep"]
        if rep.empty:
            st.info("No data this month.")
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
            _money = ["BASIC SALARY", "OT-N AMOUNT", "OT-D AMOUNT", "FIXED INCENTIVE",
                      "TOTAL GROSS", "EPF", "ETF", "CONTRACTOR FEE", "COST TO COMPANY",
                      "REVENUE NORMAL", "REVENUE OT-N", "REVENUE OT-D", "OT-N VARIANCE",
                      "OT-D VARIANCE", "TOTAL REVENUE", "MARGIN", "OT-N HRS", "OT-D HRS"]
            _fmt = {c: "{:.3f}" for c in _money if c in rep.columns}
            st.dataframe(rep.style.apply(_mcolor, axis=1).format(_fmt),
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


# ═══════════════════════════ OT REPORT ═══════════════════════════
elif page == "🕒 OT Report":
    st.header("🕒 User-wise Total OT Report")
    if not IS_ADMIN:
        st.warning("Admins only.")
        st.stop()
    _today = dt.date.today()
    _mstart = _today.replace(day=1)
    c1, c2 = st.columns(2)
    d_from = c1.date_input("From", _mstart, key="otr_from")
    d_to = c2.date_input("To", _today, key="otr_to")
    st.caption(f"{calc.fmt_date(d_from)} – {calc.fmt_date(d_to)} · "
               "OT-N = normal-day OT · OT-D = holiday/Sunday work")

    rep = calc.ot_report(gsheets.get_df("ATTANDANCE"), _holidays_set(), d_from, d_to)
    if rep.empty:
        st.info("No OT data for this range.")
    else:
        t1, t2, t3 = st.columns(3)
        t1.metric("Total OT Hrs", f'{rep["TOTAL OT HRS"].sum():,.1f}')
        t2.metric("OT-N", f'{rep["OT-N HRS"].sum():,.1f}')
        t3.metric("OT-D", f'{rep["OT-D HRS"].sum():,.1f}')

        # monthly cap 60 ඉක්මෙව්ව ඒවා රතුවෙන්
        def _c(row):
            over = calc._f(row["TOTAL OT HRS"]) > schema.MONTHLY_OT_CAP
            bg = "#3a1d1d" if over else "transparent"
            return [f"background-color:{bg};color:#e8eaed"] * len(row)
        st.dataframe(rep.style.apply(_c, axis=1), use_container_width=True, hide_index=True)
        st.caption(f"🔴 Red = exceeded monthly OT cap ({schema.MONTHLY_OT_CAP}h).")

        st.bar_chart(rep.set_index("USER NAME")["TOTAL OT HRS"], color="#ffb454")

        import io
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            rep.to_excel(xw, sheet_name="OT Report", index=False)
        st.download_button("⬇️ Excel download", buf.getvalue(),
                           file_name=f"OT_Report_{d_from}_{d_to}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ═══════════════════════════ DATA AUDIT (integrity) ═══════════════════════════
elif page == "🧪 Data Audit":
    st.header("🧪 Data Audit — Integrity & Calculation Check")
    if not IS_ADMIN:
        st.warning("Admins only.")
        st.stop()
    st.caption("Checks for data errors (not rule violations): LEAVE/OFF rows with "
               "IN/OUT or OT, working/OT/scheduled miscalculations, wrong UNIC CODE, "
               "and transaction SMV/revenue/incentive mismatches. Fix updates the sheets.")

    holidays = _holidays_set()
    tab_a, tab_t = st.tabs(["🕐 Attendance integrity", "📝 Transaction integrity"])

    # ── Attendance ──
    with tab_a:
        att = gsheets.get_df("ATTANDANCE")
        txn = gsheets.get_df("TRANSACTION")
        issues = calc.data_audit_attendance(att, holidays, txn)
        if issues.empty:
            st.success("✅ No attendance data issues — all calculations correct.")
        else:
            _byissue = issues.groupby("ISSUE").size().reset_index(name="Count")
            st.error(f"⚠️ {len(issues)} data issues across {issues['UNIC CODE'].nunique()} rows.")
            bc = st.columns(min(len(_byissue), 4) or 1)
            for i, (_, rr) in enumerate(_byissue.iterrows()):
                bc[i % len(bc)].metric(rr["ISSUE"][:22], rr["Count"])
            st.dataframe(style_flag(issues, "#ffe0e0"), use_container_width=True, hide_index=True)

            import io
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as xw:
                issues.to_excel(xw, sheet_name="Attendance Issues", index=False)
            st.download_button("⬇️ Issues report (Excel)", buf.getvalue(),
                               file_name="data_audit_attendance.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            st.warning("🔧 Fix will recompute working/OT/scheduled, clear IN/OUT & zero "
                       "LEAVE/OFF rows, and correct UNIC CODE / date format.")
            if st.button("🔧 Fix all attendance data", type="primary"):
                fixed = calc.fix_attendance_df(att, holidays, txn)
                gsheets.overwrite("ATTANDANCE", fixed)
                st.success(f"✅ Fixed — {len(issues)} issues resolved.")
                st.cache_data.clear()
                st.rerun()

    # ── Transaction ──
    with tab_t:
        txn = gsheets.get_df("TRANSACTION")
        lut = calc.build_tcode_lookup(_tcodes())
        t_issues = calc.data_audit_transaction(txn, lut)
        if t_issues.empty:
            st.success("✅ No transaction calculation issues.")
        else:
            st.error(f"⚠️ {len(t_issues)} calculation issues across "
                     f"{t_issues['UNIC CODE'].nunique()} rows.")
            st.dataframe(style_flag(t_issues, "#ffe0e0"), use_container_width=True, hide_index=True)
            if st.button("🔧 Fix all transaction calculations", type="primary"):
                fixed = calc.recompute_transaction_df(txn, lut)
                gsheets.overwrite("TRANSACTION", fixed)
                st.success(f"✅ Fixed — {len(t_issues)} issues resolved.")
                st.cache_data.clear()
                st.rerun()


# ═══════════════════════════ AUDIT ═══════════════════════════
elif page == "🔍 Audit":
    _scope_lbl = "" if IS_ADMIN else (" (your team)" if IS_LEADER else " (yours)")
    st.header("🔍 Audit — Rule Violations" + _scope_lbl)
    try:
        att = scope_df(gsheets.get_df("ATTANDANCE"))   # leader -> team scope
        txn = scope_df(gsheets.get_df("TRANSACTION"))
        users = scope_df(_users())
    except Exception:
        st.info("Create sheets from Setup first.")
        st.stop()
    holidays = _holidays_set()

    # ── මාසේ 1 සිට today දක්වා range (default) ──
    _today = dt.date.today()
    _mstart = _today.replace(day=1)
    cf1, cf2 = st.columns(2)
    d_from = cf1.date_input("From", _mstart, key="audit_from")
    d_to = cf2.date_input("To", _today, key="audit_to")
    st.caption(f"Rule violations are checked for **{calc.fmt_date(d_from)} – {calc.fmt_date(d_to)}**.")
    att = calc.filter_by_range(att, schema.A_DATE, d_from, d_to)
    txn = calc.filter_by_range(txn, schema.T_DATE, d_from, d_to)

    tabs = st.tabs([
        "🚫 20hr+ Cap", "📅 Holiday/Sunday", "⏱️ OT w/o Txn",
        "📈 Weekly OT 15+", "📊 Monthly OT 60+", "❓ Missing Txn",
    ])

    # 1) Working hours > 20 without approval
    with tabs[0]:
        st.caption(f"# OF WORKING HRS > {schema.WORKING_HRS_CAP}, not yet approved.")
        d1 = calc.audit_working_hours_cap(att)
        if d1.empty:
            st.success("✅ No violations.")
        else:
            st.error(f"⚠️ {len(d1)} rows — need admin approval.")
            st.dataframe(style_flag(d1), use_container_width=True, hide_index=True)

    # 2) Holiday / Sunday attendance
    with tabs[1]:
        st.caption("Attendance on Sundays / admin holidays (not approved).")
        d2 = calc.audit_holiday_attendance(att, holidays)
        if d2.empty:
            st.success("✅ No violations.")
        else:
            st.error(f"⚠️ {len(d2)} rows.")
            st.dataframe(style_flag(d2, "#ffe9c7"), use_container_width=True, hide_index=True)
            if IS_ADMIN and "UNIC CODE" in d2.columns:
                st.markdown("**🟢 Mark OFF and clear (admin)**")
                _o2 = {f'{r["UNIC CODE"]} — {calc.fmt_date(r.get("DATE"))} · {r.get("USER NAME","")}': str(r["UNIC CODE"]).strip()
                       for _, r in d2.iterrows()}
                _s2 = st.multiselect("Rows", list(_o2), key="hol_clear_sel")
                _n2 = st.text_input("Remark", "OFF day approved", key="hol_clear_note")
                if st.button("🟢 OFF mark · Clear", type="primary", key="hol_clear_btn"):
                    if not _s2:
                        st.warning("Select rows.")
                    else:
                        n = _clear_audit_rows([_o2[s] for s in _s2], schema.APPR_OFF, _n2 or "OFF")
                        st.success(f"{n} rows marked OFF and cleared ✅")
                        st.cache_data.clear()
                        st.rerun()

    # 3) OT worked but no OT transaction
    with tabs[2]:
        st.caption("Worked beyond scheduled time, but no OT-N/OT-D transaction that day "
                   "(not even one line).")
        d3 = calc.audit_ot_without_transaction(att, txn, holidays)
        if d3.empty:
            st.success("✅ Every OT has a transaction.")
        else:
            st.error(f"⚠️ {len(d3)} rows — OT transaction missing.")
            cols = [c for c in ["DATE", "USER ID", "USER NAME", "# OF WORKING HRS",
                                "SCHEDULED HRS", "# OF OT HRS", "EXTRA HRS", "ISSUE"]
                    if c in d3.columns]
            st.dataframe(style_flag(d3[cols]), use_container_width=True, hide_index=True)
            if IS_ADMIN and "UNIC CODE" in d3.columns:
                st.markdown("**📝 Clear with remark (admin approval)**")
                _o3 = {f'{r["UNIC CODE"]} — {calc.fmt_date(r.get("DATE"))} · {r.get("USER NAME","")} · OT {calc._f(r.get("# OF OT HRS")):.1f}h': str(r["UNIC CODE"]).strip()
                       for _, r in d3.iterrows()}
                _s3 = st.multiselect("Rows", list(_o3), key="ot_clear_sel")
                _n3 = st.text_input("Remark (required)", "", key="ot_clear_note",
                                    placeholder="e.g. manual OT approved by manager")
                if st.button("✅ Clear (OT justified)", type="primary", key="ot_clear_btn"):
                    if not _s3:
                        st.warning("Select rows.")
                    elif not _n3.strip():
                        st.warning("A remark is required.")
                    else:
                        n = _clear_audit_rows([_o3[s] for s in _s3],
                                              schema.APPR_OT_CLEARED, _n3.strip())
                        st.success(f"{n} rows cleared ✅ (with remark)")
                        st.cache_data.clear()
                        st.rerun()

    # 4) Weekly OT > 15
    with tabs[3]:
        st.caption(f"Weekly # OF OT HRS > {schema.WEEKLY_OT_CAP}.")
        d4 = calc.audit_weekly_ot(att)
        if d4.empty:
            st.success("✅ Weekly OT cap not exceeded.")
        else:
            st.error(f"⚠️ {len(d4)} user-weeks.")
            st.dataframe(style_flag(d4, "#ffd6d6"), use_container_width=True, hide_index=True)

    # 5) Monthly OT > 60
    with tabs[4]:
        st.caption(f"Monthly # OF OT HRS > {schema.MONTHLY_OT_CAP}.")
        d6 = calc.audit_monthly_ot(att)
        if d6.empty:
            st.success("✅ Monthly OT cap not exceeded.")
        else:
            st.error(f"⚠️ {len(d6)} user-months — exceeded monthly OT 60+.")
            st.dataframe(style_flag(d6, "#ffccd5"), use_container_width=True, hide_index=True)

    # 6) Missing transactions for a date
    with tabs[5]:
        adate = st.date_input("Date", dt.date.today(), key="audit_missing_date")
        d5 = calc.audit_missing_transactions(users, txn, adate)
        if d5.empty:
            st.success("✅ Every active user has transactions.")
        else:
            st.error(f"⚠️ {len(d5)} users — no transaction on {adate.isoformat()}.")
            st.dataframe(style_flag(d5, "#e0e0ff"), use_container_width=True, hide_index=True)


# ═══════════════════════════ EXPORT ═══════════════════════════
elif page == "📥 Export":
    st.header("📥 Export — ATTANDANCE / TRANSACTION")
    st.caption("Filter by date range + user and download Excel/CSV. "
               "Format matches the original Excel.")

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
    user_map = {"ALL — all users": "ALL"}
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
            "⬇️ Excel (.xlsx) — All", buf.getvalue(),
            file_name=f"KPI_export_{d_from}_{d_to}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ═══════════════════════════ UPLOAD ═══════════════════════════
elif page == "📤 Upload":
    st.header("📤 Bulk Upload — ATTANDANCE / TRANSACTION")
    st.caption("Add data from an Excel (.xlsx) or CSV file. **Rules are checked "
               "are added** — violations are blocked / need approval.")

    target = st.selectbox("Sheet", ["TRANSACTION", "ATTANDANCE"])
    headers = schema.SHEETS[target]["headers"]
    holidays = _holidays_set()

    tmpl = pd.DataFrame(columns=headers)
    st.download_button("⬇️ Template (.csv)",
                       tmpl.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{target}_template.csv", mime="text/csv")

    up = st.file_uploader("File", type=["xlsx", "xls", "csv"])
    if up is not None:
        try:
            raw = pd.read_csv(up, dtype=str) if up.name.lower().endswith("csv") \
                else pd.read_excel(up, dtype=str)
        except Exception as e:
            st.error(f"Cannot read file: {e}")
            st.stop()
        raw = raw.fillna("")
        st.write(f"Read: {len(raw)} rows")

        # Scope: normal user -> තමන්, leader -> team, admin -> ඕනෑම
        if not IS_ADMIN and ALLOWED_UIDS is not None:
            if "USER ID" not in raw.columns:
                raw["USER ID"] = CURRENT_UID
            else:
                raw["USER ID"] = raw["USER ID"].apply(
                    lambda x: CURRENT_UID if not str(x).strip() else str(x).strip())
            other = raw[~raw["USER ID"].isin(ALLOWED_UIDS)]
            if len(other):
                st.warning(f"⚠️ Skipping {len(other)} rows with USER IDs outside your scope — "
                           + ("you can only upload for users in your team."
                              if IS_LEADER else "you can only upload your own data."))
            raw = raw[raw["USER ID"].isin(ALLOWED_UIDS)]
            if raw.empty:
                st.info("No rows to upload.")
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
                        aligned.at[i, "UNIC CODE"] = calc.unic_serial(d, uid)

            save_df, disp, errmask = calc.validate_transaction_upload(aligned, lut)
            n_err = int(errmask.sum())
            n_ok = len(save_df) - n_err
            c1, c2 = st.columns(2)
            c1.metric("✅ OK rows", n_ok)
            c2.metric("🚫 Error rows (block)", n_err)

            if n_err:
                st.error("Rule/validation errors in the rows below — these are not added:")
                st.dataframe(style_flag(disp[errmask]), use_container_width=True, hide_index=True)

            clean = save_df[~errmask]
            st.subheader(f"{len(clean)} rows to add (preview)")
            st.dataframe(clean.head(50), use_container_width=True, hide_index=True)

            if len(clean) and st.button(f"⬆️ Add {len(clean)} clean rows", type="primary"):
                gsheets.append_rows(target, clean.fillna("").astype(str).values.tolist())
                st.success(f"{len(clean)} rows added ✅ (skipped {n_err} error rows)")
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
            c3.metric("⏳ Needs approval", n_pending)

            if viol_mask.any():
                st.warning("⚠️ Rule violations — these are added as **PENDING** "
                           "(needs Admin approval via Approvals):")
                st.dataframe(
                    style_flag(disp[viol_mask][[
                        "DATE", "USER ID", "# OF WORKING HRS", "SCHEDULED HRS",
                        "# OF OT HRS", "APPROVAL STATUS", "⚠ VIOLATION"]]),
                    use_container_width=True, hide_index=True)

            mode = st.radio("Add mode", [
                "Clean rows only (skip violations)",
                "Add all — violations as PENDING",
            ], index=1)

            to_add = save_df if mode.startswith("All") else save_df[~pending_mask]

            st.info("ℹ️ If a UNIC CODE already exists that row is **updated** "
                    "(no duplicates).")
            st.subheader(f"{len(to_add)} rows (preview)")
            st.dataframe(to_add.head(50), use_container_width=True, hide_index=True)

            if len(to_add) and st.button(f"⬆️ Upsert {len(to_add)} rows", type="primary"):
                added, updated = gsheets.upsert_rows(
                    "ATTANDANCE", to_add.fillna("").astype(str).values.tolist(), "UNIC CODE")
                st.success(f"✅ {added} add · {updated} update"
                           + (f" · {n_pending} PENDING" if mode.startswith("All") and n_pending else ""))
                st.cache_data.clear()


# ═══════════════════════════ ADMIN ═══════════════════════════
elif page == "🛡️ Admin":
    st.header("🛡️ Admin")
    if not IS_ADMIN:
        st.warning("This page is admins only. Login with the PIN via 🔑 Admin login in the sidebar.")
        st.stop()

    a1, a2, a3 = st.tabs(["✅ Attendance Approvals", "👥 Bulk Mark", "📅 Holiday Setup"])

    # ── Pending attendance approvals ──
    with a1:
        st.subheader("PENDING attendance approvals")
        att = gsheets.get_df("ATTANDANCE")
        if "APPROVAL STATUS" not in att or att.empty:
            st.info("No attendance data.")
        else:
            pend = att[att["APPROVAL STATUS"].astype(str).str.upper() == schema.APPR_PENDING]
            if pend.empty:
                st.success("✅ No pending approvals.")
            else:
                st.error(f"{len(pend)} pending.")
                show_cols = [c for c in ["UNIC CODE", "DATE", "USER ID", "USER NAME",
                                         "# OF WORKING HRS", "APPROVAL NOTE"] if c in pend.columns]
                st.dataframe(pend[show_cols], use_container_width=True, hide_index=True)

                _multi = st.checkbox("Approve / reject multiple at once", value=True)
                if _multi:
                    picks = st.multiselect("Select UNIC CODEs", pend["UNIC CODE"].tolist())
                else:
                    picks = [st.selectbox("Select UNIC CODE", pend["UNIC CODE"].tolist())]
                c1, c2 = st.columns(2)
                if c1.button("✅ Approve", type="primary"):
                    att.loc[att["UNIC CODE"].isin(picks), "APPROVAL STATUS"] = schema.APPR_APPROVED
                    gsheets.overwrite("ATTANDANCE", att)
                    st.success(f"{len(picks)} approved ✅")
                    st.cache_data.clear()
                    st.rerun()
                if c2.button("❌ Reject"):
                    att.loc[att["UNIC CODE"].isin(picks), "APPROVAL STATUS"] = schema.APPR_REJECTED
                    gsheets.overwrite("ATTANDANCE", att)
                    st.warning(f"{len(picks)} rejected")
                    st.cache_data.clear()
                    st.rerun()

    # ── Bulk attendance mark for all users ──
    with a2:
        st.subheader("Bulk attendance mark — all USER-M users")
        st.caption("Default times: Weekday 8:00–17:00 (lunch 1h) · Saturday 8:00–13:00 · "
                   "Sunday 8:00–13:00. Rest-day (Sunday/holiday) rows need admin approval.")
        udf = _users()
        active = udf[udf["ACTIVE"].astype(str).str.upper() != "N"] if "ACTIVE" in udf else udf
        b1, b2 = st.columns(2)
        bfrom = b1.date_input("From", dt.date.today(), key="bulk_from")
        bto = b2.date_input("To", dt.date.today(), key="bulk_to")
        b3, b4 = st.columns(2)
        bloc = b3.text_input("Work location", "EGF", key="bulk_loc")
        approve_rest = b4.checkbox("Auto-approve rest-day rows (admin)", value=False,
                                   help="On = Sunday/holiday rows APPROVED · Off = PENDING")
        ndays = (bto - bfrom).days + 1
        st.caption(f"{len(active)} active users × {max(ndays,0)} day(s) = "
                   f"up to {len(active) * max(ndays,0)} rows")

        if st.button("👥 Generate attendance", type="primary"):
            dates = calc.date_range_list(bfrom, bto)
            rows = calc.bulk_attendance_rows(
                active, dates, _holidays_set(), txn_df=gsheets.get_df("TRANSACTION"),
                weekday_lunch=1.0, weekend_lunch=0.0, location=bloc or "EGF",
                admin=approve_rest)
            if not rows:
                st.info("No rows generated (no active users / invalid range).")
            else:
                added, updated = gsheets.upsert_rows("ATTANDANCE", rows, "UNIC CODE")
                _pend_n = sum(1 for r in rows if r[22] == schema.APPR_PENDING)
                st.success(f"✅ {added} added · {updated} updated"
                           + (f" · {_pend_n} rest-day rows PENDING approval" if _pend_n else ""))
                st.cache_data.clear()

    # ── Holiday setup ──
    with a3:
        st.subheader("Holiday setup")
        st.caption("Dates added here have scheduled hours = 0. Attendance on those days "
                   "needs admin approval.")
        hdf = gsheets.get_df("HOLIDAY-M")

        # ── calendar date picker එකෙන් add ──
        st.markdown("**📅 Add a new holiday**")
        h1, h2, h3, h4 = st.columns([2, 3, 2, 1])
        hdate = h1.date_input("Date", dt.date.today(), key="hol_date")
        hdesc = h2.text_input("Description", key="hol_desc")
        htype = h3.selectbox("TYPE", ["Public", "Mercantile", "Special", "Bank"],
                             key="hol_type")
        h4.markdown("<br>", unsafe_allow_html=True)
        if h4.button("➕ Add"):
            iso = hdate.isoformat()
            existing_dates = set(hdf["DATE"].apply(lambda x: (calc._to_date(x) or "")
                                 and calc._to_date(x).isoformat()) ) if not hdf.empty and "DATE" in hdf else set()
            if iso in existing_dates:
                st.warning("That date already exists.")
            else:
                gsheets.append_rows("HOLIDAY-M", [[iso, hdesc, htype]])
                st.success(f"Holiday {iso} added ✅")
                st.cache_data.clear()
                st.rerun()

        st.divider()
        st.markdown("**Existing holidays** (you can edit / delete in the table too)")
        edited = st.data_editor(hdf, num_rows="dynamic", use_container_width=True,
                                hide_index=True, key="hol_ed")
        if st.button("💾 Holidays save", type="primary"):
            gsheets.overwrite("HOLIDAY-M", edited)
            st.success("HOLIDAY-M updated ✅")
            st.cache_data.clear()

        st.divider()
        st.caption(f"⚙️ Rules: daily cap {schema.WORKING_HRS_CAP}h · weekly OT cap "
                   f"{schema.WEEKLY_OT_CAP}h · complaint penalty {schema.COMPLAINT_PENALTY} · "
                   f"schedule (Mon–Fri 8h, Sat 5h, Sun 0h). Change these in schema.py.")


# ═══════════════════════════ DATA MANAGER ═══════════════════════════
elif page == "🗂️ Data Manager":
    st.header("🗂️ Data Manager — Add / Update / Delete"
              + ("" if IS_ADMIN else (" (your team)" if IS_LEADER else " (yours)")))

    if IS_ADMIN:
        # Admin -> හැම sheet එකම
        editable = ["USER-M", "CUSTOMMER-M", "TCODE-M", "CUSTOMMER COMPLAINT"]
        editable += [s for s in (schema.MASTER_SHEETS + schema.TXN_SHEETS)
                     if s not in editable and s != "INSENTIVE"]
    else:
        # User/Leader -> තමන්ගේ/team එකේ ATTANDANCE + TRANSACTION විතරක්
        editable = ["ATTANDANCE", "TRANSACTION"]
        st.caption("You can manage your"
                   + (" team's" if IS_LEADER else "")
                   + " you can edit/delete ATTANDANCE and TRANSACTION records.")

    mkey = st.selectbox("Sheet", editable)
    full_df = gsheets.get_df(mkey)
    # non-admin -> තමන්ගේ/team scope එකට විතරක්
    view = scope_df(full_df) if not IS_ADMIN else full_df

    # ── date filter (ATTANDANCE/TRANSACTION වැනි date column තියෙන sheets) ──
    date_col = "Date" if "Date" in full_df.columns else ("DATE" if "DATE" in full_df.columns else None)
    use_dt = False
    if date_col and not view.empty:
        use_dt = st.checkbox(f"📅 Filter by {date_col}", value=True)
        if use_dt:
            _t = dt.date.today()
            fc1, fc2 = st.columns(2)
            d_from = fc1.date_input("From", _t.replace(day=1), key=f"dm_from_{mkey}")
            d_to = fc2.date_input("To", _t, key=f"dm_to_{mkey}")
            _dmask = view[date_col].apply(
                lambda x: (lambda d: d is not None and d_from <= d <= d_to)(calc._to_date(x)))
            view = view[_dmask]
            st.caption(f"{len(view)} records in {calc.fmt_date(d_from)} – {calc.fmt_date(d_to)}")

    # optional search filter
    q = st.text_input("🔎 Search (optional)", "")
    if q.strip():
        mask = view.apply(lambda r: r.astype(str).str.contains(q, case=False, na=False).any(), axis=1)
        view = view[mask]

    # non-admin නම් හැම විටම partial-save (අනිත් users ගේ rows preserve කරන්න)
    _partial = bool(use_dt or q.strip() or not IS_ADMIN)
    st.caption(f"showing {len(view)} records • **double-click a cell → edit** · "
               "**bottom ➕ row → add** · **tick 🗑️ Delete? → delete** · then 💾 Save.")

    work = view.copy()
    work.insert(0, "🗑️ Delete?", False)
    edited = st.data_editor(
        work, use_container_width=True, num_rows="dynamic",
        hide_index=True, key=f"ed_{mkey}_{q}_{use_dt}",
        column_config={"🗑️ Delete?": st.column_config.CheckboxColumn(
            "🗑️ Delete?", help="Tick to delete this row", default=False)},
    )

    ndel = int(edited["🗑️ Delete?"].fillna(False).sum())
    recalc = False
    if mkey in ("ATTANDANCE", "TRANSACTION"):
        recalc = st.checkbox(
            "🔄 Auto-recalculate from IN/OUT (or T-CODE/qty)", value=True,
            help="ATTANDANCE: working/OT/scheduled · TRANSACTION: recompute SMV/revenue/In")
    c1, c2 = st.columns([1, 4])
    if c1.button(f"💾 Save ({ndel} delete)" if ndel else "💾 Save", type="primary"):
        kept = edited[~edited["🗑️ Delete?"].fillna(False)].drop(columns=["🗑️ Delete?"])

        # non-admin -> තමන්ගේ/team USER ID rows විතරක් (පිට users ට save කරන්න බෑ)
        if not IS_ADMIN and "USER ID" in kept.columns and ALLOWED_UIDS is not None:
            kept["USER ID"] = kept["USER ID"].apply(
                lambda x: CURRENT_UID if not str(x).strip() else str(x).strip())
            bad = kept[~kept["USER ID"].isin(ALLOWED_UIDS)]
            if len(bad):
                st.warning(f"⚠️ Skipping {len(bad)} rows with USER IDs outside your scope.")
            kept = kept[kept["USER ID"].isin(ALLOWED_UIDS)]

        if _partial:
            hidden = full_df[~full_df.index.isin(view.index)]
            final = pd.concat([hidden, kept], ignore_index=True)
        else:
            final = kept.reset_index(drop=True)

        # 🔄 auto recalculate
        if recalc and mkey == "ATTANDANCE":
            final = calc.recompute_attendance_df(final, _holidays_set(),
                                                 gsheets.get_df("TRANSACTION"))
        elif recalc and mkey == "TRANSACTION":
            final = calc.recompute_transaction_df(final, calc.build_tcode_lookup(_tcodes()))

        # ATTANDANCE -> UNIC CODE duplicate වළක්වනවා (අන්තිම row තියාගන්නවා)
        if mkey == "ATTANDANCE" and "UNIC CODE" in final.columns:
            final = final[final["UNIC CODE"].astype(str).str.strip() != ""].drop_duplicates(
                subset="UNIC CODE", keep="last").reset_index(drop=True)

        gsheets.overwrite(mkey, final)
        st.success(f"{mkey} updated ✅ — {len(final)} rows ({ndel} deleted)"
                   + (" · recalculated 🔄" if recalc else ""))
        st.cache_data.clear()
        st.rerun()
    c2.caption("⚠️ Saving applies add + edit + delete together. "
               "Other rows stay safe even when you save with a filter/scope.")

    if mkey == "TCODE-M":
        st.info("ℹ️ TCODE-M = the original Excel *Master sheet - Finalized* (SMV/rate engine). "
                "changing a rate column applies that value to subsequent transactions.")


# ───────────────────────── footer brand (every page) ─────────────────────────
st.markdown(
    "<div class='brand-foot'>Development by <b>Ishanka Madusanka</b></div>",
    unsafe_allow_html=True)
