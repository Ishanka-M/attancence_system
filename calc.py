"""
calc.py
=======
Calculation engine. Excel එකේ formulas මෙතන Python වලට convert කරලා තියෙනවා.

Reverse-engineered logic (TRANSACTION sheet එකෙන් verify කරපු):
  UTILIZE HOURS  = # OF TRANSACTION × SMV(M) ÷ 60
  REVANUE-NORMAL = # OF TRANSACTION × NORMAL rate     (TIME = NORMAL නම්)
  REVANUE-OT-N   = # OF TRANSACTION × OT-N rate        (TIME = OT -N නම්)
  REVANUE-OT-D   = # OF TRANSACTION × OT-D rate        (TIME = OT -D නම්)
  TXN INCENTIVE  = TOTAL REVANUE ÷ 10

  UTILIZATION    = UTILIZED HOURS ÷ # OF WORKING HRS   (attendance)
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

import schema


def _f(x, default=0.0) -> float:
    """ඕනෑම cell value එකක් safe float එකකට."""
    try:
        if x is None or x == "":
            return default
        return float(str(x).replace(",", ""))
    except (ValueError, TypeError):
        return default


def _to_date(x):
    """ISO string / date / Excel-serial -> datetime.date (බැරිනම් None)."""
    if isinstance(x, dt.date):
        return x
    s = str(x).strip()
    if not s:
        return None
    # Excel serial number?
    try:
        if s.replace(".", "", 1).isdigit() and float(s) > 30000:
            return (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(s))))
    except (ValueError, TypeError):
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _week_key(d: dt.date) -> str:
    """ISO week label, උදා '2026-W25'."""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def scheduled_hours(date_val, holidays: set | None = None) -> float:
    """
    දවසකට නියමිත working hours.
      සතියේ දවස් 8h · සෙනසුරාදා 5h · ඉරිදා 0h · admin නිවාඩු 0h
    """
    d = _to_date(date_val)
    if d is None:
        return 8.0
    if holidays and d.isoformat() in holidays:
        return 0.0
    return float(schema.WORKDAY_HOURS.get(d.weekday(), 8))


def is_rest_day(date_val, holidays: set | None = None) -> bool:
    """ඉරිදා හෝ admin නිවාඩු දවසක්ද?"""
    return scheduled_hours(date_val, holidays) <= 0


def holiday_set(holiday_df: pd.DataFrame) -> set:
    """HOLIDAY-M -> {ISO date strings}."""
    out = set()
    if holiday_df is None or holiday_df.empty or "DATE" not in holiday_df:
        return out
    for v in holiday_df["DATE"]:
        d = _to_date(v)
        if d:
            out.add(d.isoformat())
    return out


def attendance_needs_approval(working_hrs, date_val, holidays: set | None = None):
    """
    Attendance entry එකකට approval ඕනේද? (reason එකත් එක්ක)
    return: (needs: bool, reason: str)
    """
    reasons = []
    if _f(working_hrs) > schema.WORKING_HRS_CAP:
        reasons.append(f"WORKING HRS {_f(working_hrs):.1f} > {schema.WORKING_HRS_CAP} cap")
    if is_rest_day(date_val, holidays):
        reasons.append("නිවාඩු/ඉරිදා දවසට attendance")
    return (len(reasons) > 0, " ; ".join(reasons))



def build_tcode_lookup(tcode_df: pd.DataFrame) -> dict:
    """TCODE-M -> {T-CODE: {smv_m, normal, otn, otd, desc, uom, css_smv}}"""
    lut = {}
    for _, r in tcode_df.iterrows():
        code = str(r.get("T-CODE", "")).strip()
        if not code:
            continue
        lut[code] = {
            "desc": r.get("Description", ""),
            "uom": r.get("UOM", ""),
            "css_smv": _f(r.get("CSS SMV")),
            "smv_m": _f(r.get("SMV (M)")),
            "normal": _f(r.get("NORMAL rate")),
            "otn": _f(r.get("OT-N rate")),
            "otd": _f(r.get("OT-D rate")),
        }
    return lut


def calc_transaction(tcode_info: dict, time_type: str, qty: float) -> dict:
    """එක transaction එකක utilize hours + revenue + incentive."""
    qty = _f(qty)
    smv_m = tcode_info.get("smv_m", 0.0)
    utilize = qty * smv_m / 60.0

    rev_n = rev_otn = rev_otd = 0.0
    t = (time_type or "").strip().upper().replace(" ", "")
    if t == "NORMAL":
        rev_n = qty * tcode_info.get("normal", 0.0)
    elif t in ("OT-N", "OTN"):
        rev_otn = qty * tcode_info.get("otn", 0.0)
    elif t in ("OT-D", "OTD"):
        rev_otd = qty * tcode_info.get("otd", 0.0)

    total_rev = rev_n + rev_otn + rev_otd
    return {
        "smv": tcode_info.get("css_smv", 0.0),
        "utilize_hours": round(utilize, 6),
        "rev_normal": round(rev_n, 4),
        "rev_otn": round(rev_otn, 4),
        "rev_otd": round(rev_otd, 4),
        "total_rev": round(total_rev, 4),
        "txn_incentive": round(total_rev / schema.TXN_INCENTIVE_DIVISOR, 4),
    }


def calc_attendance_utilization(utilized_hours: float, working_hours: float) -> float:
    wh = _f(working_hours)
    if wh <= 0:
        return 0.0
    return round(_f(utilized_hours) / wh, 4)


# ───────────────────── INCENTIVE aggregation ──────────────────────
def compute_incentive(
    txn_df: pd.DataFrame,
    complaint_df: pd.DataFrame,
    kpi_df: pd.DataFrame,
    user_df: pd.DataFrame,
    period_label: str,
    ot_recovery: dict | None = None,
) -> pd.DataFrame:
    """
    User එක එකකට incentive එකතුව ගණනය කරනවා.

      TXN INCENTIVE        = period එකේ txn incentive එකතුව
      ZERO-COMPLAINT BONUS = complaints නැත්නම්  -> 3000
      ON-TIME KPI BONUS    = on-time updates තිබ්බොත්  -> 4000
      100% OT RECOVERY     = ot_recovery[user] >= 100% නම් -> 3000
      TOTAL                = ඉහත සියල්ල
      BALANCE              = TARGET - TOTAL
    """
    ot_recovery = ot_recovery or {}

    # txn incentive per user
    txn_inc = {}
    if not txn_df.empty and schema.T_USER in txn_df:
        col = schema.T_INCENTIVE if schema.T_INCENTIVE in txn_df else None
        for _, r in txn_df.iterrows():
            uid = str(r.get(schema.T_USER, "")).strip()
            if not uid:
                continue
            txn_inc[uid] = txn_inc.get(uid, 0.0) + (_f(r.get(col)) if col else 0.0)

    # complaints per user
    complaints = {}
    if not complaint_df.empty and "USER ID" in complaint_df:
        for _, r in complaint_df.iterrows():
            uid = str(r.get("USER ID", "")).strip()
            if uid:
                complaints[uid] = complaints.get(uid, 0) + 1

    # on-time KPI updates per user
    ontime = {}
    if not kpi_df.empty and "USER ID" in kpi_df:
        for _, r in kpi_df.iterrows():
            uid = str(r.get("USER ID", "")).strip()
            if not uid:
                continue
            score = _f(r.get("SCORE")) or (1 if str(r.get("ON TIME UPDATE", "")).strip().upper() in ("Y", "YES", "1", "TRUE") else 0)
            ontime[uid] = ontime.get(uid, 0) + score

    rows = []
    for _, u in user_df.iterrows():
        uid = str(u.get("USER ID", "")).strip()
        if not uid:
            continue
        name = u.get("USER NAME", "")
        ti = round(txn_inc.get(uid, 0.0), 2)
        ncomp = complaints.get(uid, 0)
        penalty = ncomp * schema.COMPLAINT_PENALTY          # complaint -> අඩු කරනවා
        zero_bonus = schema.ZERO_COMPLAINT_BONUS if ncomp == 0 else 0
        kpi_bonus = schema.ONTIME_KPI_BONUS if ontime.get(uid, 0) > 0 else 0
        otr = _f(ot_recovery.get(uid))
        ot_bonus = schema.FULL_OT_RECOVERY_BONUS if otr >= 100 else 0
        total = round(ti + zero_bonus + kpi_bonus + ot_bonus - penalty, 2)
        total = max(total, 0.0)                             # 0 ට වඩා අඩු වෙන්නෑ
        rows.append([
            period_label, uid, name, ti, ncomp, penalty, zero_bonus, kpi_bonus,
            otr, ot_bonus, total, schema.DEFAULT_TARGET,
            round(schema.DEFAULT_TARGET - total, 2), "",
        ])

    return pd.DataFrame(rows, columns=schema.SHEETS["INSENTIVE"]["headers"])


# ═══════════════════════════ AUDIT engine ═══════════════════════════
def audit_working_hours_cap(att_df: pd.DataFrame) -> pd.DataFrame:
    """පැය 20+ වැඩ කරලා, approval නැති (OK/Pending/Rejected) attendance rows."""
    if att_df.empty or "# OF WORKING HRS" not in att_df:
        return pd.DataFrame()
    df = att_df.copy()
    df["_wh"] = df["# OF WORKING HRS"].apply(_f)
    status = df.get("APPROVAL STATUS", pd.Series([""] * len(df)))
    mask = (df["_wh"] > schema.WORKING_HRS_CAP) & \
           (status.astype(str).str.upper() != schema.APPR_APPROVED)
    out = df[mask].drop(columns=["_wh"])
    return out


def audit_holiday_attendance(att_df: pd.DataFrame, holidays: set) -> pd.DataFrame:
    """නිවාඩු/ඉරිදා දවසට attendance තියෙන, approve කරලා නැති rows."""
    if att_df.empty or "DATE" not in att_df:
        return pd.DataFrame()
    df = att_df.copy()
    df["_rest"] = df["DATE"].apply(lambda d: is_rest_day(d, holidays))
    status = df.get("APPROVAL STATUS", pd.Series([""] * len(df)))
    mask = df["_rest"] & (status.astype(str).str.upper() != schema.APPR_APPROVED)
    return df[mask].drop(columns=["_rest"])


def audit_ot_without_transaction(att_df: pd.DataFrame, txn_df: pd.DataFrame,
                                 holidays: set) -> pd.DataFrame:
    """
    Scheduled time එකට වඩා වැඩ කරලා (OT), නමුත් ඒ user+date එකට
    TRANSACTION එකේ OT-N/OT-D එකක් නැති rows.
    """
    if att_df.empty:
        return pd.DataFrame()

    # OT transactions තියෙන (user, date) keys
    ot_keys = set()
    if not txn_df.empty and {schema.T_USER, schema.T_DATE, schema.T_TIME} <= set(txn_df.columns):
        for _, t in txn_df.iterrows():
            tt = str(t.get(schema.T_TIME, "")).upper().replace(" ", "")
            if tt in ("OT-N", "OT-D", "OTN", "OTD"):
                d = _to_date(t.get(schema.T_DATE))
                ot_keys.add((str(t.get(schema.T_USER, "")).strip(), d.isoformat() if d else ""))

    flagged = []
    for _, a in att_df.iterrows():
        wh = _f(a.get("# OF WORKING HRS"))
        ot_h = _f(a.get("# OF OT HRS"))
        d = _to_date(a.get("DATE"))
        sched = scheduled_hours(a.get("DATE"), holidays)
        did_ot = (ot_h > 0) or (wh > sched)
        if not did_ot:
            continue
        key = (str(a.get("USER ID", "")).strip(), d.isoformat() if d else "")
        if key not in ot_keys:
            row = a.to_dict()
            row["EXTRA HRS"] = round(max(wh - sched, ot_h), 2)
            row["ISSUE"] = "OT වැඩ කරලා OT-N/OT-D transaction නෑ"
            flagged.append(row)
    return pd.DataFrame(flagged)


def audit_weekly_ot(att_df: pd.DataFrame) -> pd.DataFrame:
    """සතියකට OT පැය 15+ ගිය user-week combinations."""
    if att_df.empty or "# OF OT HRS" not in att_df:
        return pd.DataFrame()
    recs = []
    for _, a in att_df.iterrows():
        d = _to_date(a.get("DATE"))
        if d is None:
            continue
        recs.append({
            "USER ID": str(a.get("USER ID", "")).strip(),
            "USER NAME": a.get("USER NAME", ""),
            "WEEK": _week_key(d),
            "OT": _f(a.get("# OF OT HRS")),
        })
    if not recs:
        return pd.DataFrame()
    g = pd.DataFrame(recs).groupby(
        ["USER ID", "USER NAME", "WEEK"], as_index=False)["OT"].sum()
    g = g.rename(columns={"OT": "WEEKLY OT HRS"})
    return g[g["WEEKLY OT HRS"] > schema.WEEKLY_OT_CAP].sort_values(
        "WEEKLY OT HRS", ascending=False)


def audit_missing_transactions(user_df: pd.DataFrame, txn_df: pd.DataFrame,
                               date_val) -> pd.DataFrame:
    """අදාළ දිනයට TRANSACTION එකක් add කරලා නැති active users."""
    if user_df.empty:
        return pd.DataFrame()
    d = _to_date(date_val)
    diso = d.isoformat() if d else str(date_val)

    submitted = set()
    if not txn_df.empty and {schema.T_USER, schema.T_DATE} <= set(txn_df.columns):
        for _, t in txn_df.iterrows():
            td = _to_date(t.get(schema.T_DATE))
            if td and td.isoformat() == diso:
                submitted.add(str(t.get(schema.T_USER, "")).strip())

    rows = []
    for _, u in user_df.iterrows():
        uid = str(u.get("USER ID", "")).strip()
        if not uid:
            continue
        active = str(u.get("ACTIVE", "Y")).strip().upper() in ("", "Y", "YES", "1", "TRUE")
        if active and uid not in submitted:
            rows.append({
                "DATE": diso, "USER ID": uid, "USER NAME": u.get("USER NAME", ""),
                "DEPARTMENT": u.get("DEPARTMENT", ""),
                "ISSUE": "මේ දිනයට TRANSACTION නෑ",
            })
    return pd.DataFrame(rows)


# ═══════════════════════ MONTHLY user-level summary ═══════════════════════
def _month_key(d: dt.date) -> str:
    return f"{d.year}-{d.month:02d}"


def add_month_col(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    """date_col එක අනුව 'MONTH' (YYYY-MM) column එකක් add කරනවා."""
    out = df.copy()
    out["MONTH"] = out[date_col].apply(lambda x: (_to_date(x) and _month_key(_to_date(x))) or "")
    return out


def monthly_user_summary(txn_df: pd.DataFrame, att_df: pd.DataFrame,
                         month: str | None = None) -> pd.DataFrame:
    """
    User × Month අනුව: Normal Rev, OT Rev, Total Revenue, Incentive, OT Hrs, Cost.
      Revenue  = REVANUE-NORMAL + REVANUE-OT-N + REVANUE-OT-D
      OT Rev   = REVANUE-OT-N + REVANUE-OT-D
      Incentive= In (revenue/10)
      OT Hrs   = ATTANDANCE.# OF OT HRS
      Cost     = Incentive payout (company එකට යන වියදම — note බලන්න)
    month දුන්නොත් ඒ මාසෙට filter වෙනවා.
    """
    recs = {}

    def slot(uid, name, mon):
        k = (mon, uid)
        if k not in recs:
            recs[k] = {"MONTH": mon, "USER ID": uid, "USER NAME": name,
                       "NORMAL REV": 0.0, "OT REV": 0.0, "TOTAL REV": 0.0,
                       "INCENTIVE": 0.0, "OT HRS": 0.0}
        return recs[k]

    if not txn_df.empty and schema.T_DATE in txn_df:
        for _, t in txn_df.iterrows():
            d = _to_date(t.get(schema.T_DATE))
            if d is None:
                continue
            mon = _month_key(d)
            uid = str(t.get(schema.T_USER, "")).strip()
            if not uid:
                continue
            s = slot(uid, t.get(schema.T_NAME, ""), mon)
            n = _f(t.get(schema.T_REV_N))
            otn = _f(t.get(schema.T_REV_OTN))
            otd = _f(t.get(schema.T_REV_OTD))
            s["NORMAL REV"] += n
            s["OT REV"] += otn + otd
            s["TOTAL REV"] += n + otn + otd
            s["INCENTIVE"] += _f(t.get(schema.T_INCENTIVE))

    if not att_df.empty and schema.A_DATE in att_df:
        for _, a in att_df.iterrows():
            d = _to_date(a.get(schema.A_DATE))
            if d is None:
                continue
            mon = _month_key(d)
            uid = str(a.get(schema.A_USER, "")).strip()
            if not uid:
                continue
            s = slot(uid, a.get("USER NAME", ""), mon)
            s["OT HRS"] += _f(a.get(schema.A_OT))

    df = pd.DataFrame(list(recs.values()))
    if df.empty:
        return df
    df["COST"] = df["INCENTIVE"]          # Cost = incentive payout (assumption)
    for c in ["NORMAL REV", "OT REV", "TOTAL REV", "INCENTIVE", "OT HRS", "COST"]:
        df[c] = df[c].round(2)
    if month:
        df = df[df["MONTH"] == month]
    return df.sort_values(["MONTH", "TOTAL REV"], ascending=[True, False])


def filter_by_range(df: pd.DataFrame, date_col: str, start, end,
                    user_id: str | None = None) -> pd.DataFrame:
    """date range + (optional) user අනුව filter — download/export වලට."""
    if df.empty or date_col not in df:
        return df
    out = df.copy()
    s, e = _to_date(start), _to_date(end)
    keep = out[date_col].apply(lambda x: (lambda d: d is not None and s <= d <= e)(_to_date(x)))
    out = out[keep]
    if user_id and user_id != "ALL" and "USER ID" in out:
        out = out[out["USER ID"].astype(str).str.strip() == user_id]
    return out
