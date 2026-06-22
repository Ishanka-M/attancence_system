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
    if isinstance(x, dt.date) and not isinstance(x, dt.datetime):
        return x
    if isinstance(x, dt.datetime):
        return x.date()
    s = str(x).strip()
    if not s:
        return None
    # Excel serial number?
    try:
        if s.replace(".", "", 1).isdigit() and float(s) > 30000:
            return (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(s))))
    except (ValueError, TypeError):
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _to_datetime(x):
    """datetime string -> datetime (බැරිනම් None). 'YYYY-MM-DD HH:MM:SS' වැනි."""
    if isinstance(x, dt.datetime):
        return x
    s = str(x).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                "%H:%M:%S", "%H:%M"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def hours_between(in_str, out_str) -> float:
    """IN / OUT අතර පැය ගණන. රෑ පහුවෙනවා නම් (out < in) පැය 24ක් එකතු කරනවා."""
    a, b = _to_datetime(in_str), _to_datetime(out_str)
    if a is None or b is None:
        return 0.0
    diff = (b - a).total_seconds() / 3600.0
    if diff < 0:
        diff += 24
    return round(diff, 4)


def compute_work_hours(in_str, out_str, lunch=1.0) -> float:
    """# OF WORKING HRS = (OUT − IN) − LUNCH & TEA. (lunch default 1)"""
    gross = hours_between(in_str, out_str)
    return round(max(gross - _f(lunch, 1.0), 0.0), 4)


def compute_attendance(date_val, in_str, out_str, lunch, utilized_hours, holidays):
    """IN/OUT + lunch + utilized වලින් working/ot/scheduled/utilization ගණනය."""
    wh = compute_work_hours(in_str, out_str, lunch)
    sched = scheduled_hours(date_val, holidays)
    ot = round(max(wh - sched, 0.0), 4)
    util = calc_attendance_utilization(utilized_hours, wh)
    return {"working": wh, "ot": ot, "sched": round(sched, 2), "utilization": util}


# ───────── Excel-format helpers (ATTANDANCE save format) ─────────
def excel_serial(date_val):
    """date -> Excel serial number (1899-12-30 epoch). උදා 2026-06-30 -> 46203."""
    d = _to_date(date_val)
    return (d - dt.date(1899, 12, 30)).days if d else ""


def fmt_date(date_val):
    """date -> 'M/D/YYYY' (උදා 6/30/2026)."""
    d = _to_date(date_val)
    return f"{d.month}/{d.day}/{d.year}" if d else str(date_val or "")


def fmt_datetime(x):
    """datetime -> 'M/D/YYYY H:MM' (උදා 6/23/2026 8:00)."""
    t = _to_datetime(x)
    if t is None:
        d = _to_date(x)
        return fmt_date(d) if d else str(x or "")
    return f"{t.month}/{t.day}/{t.year} {t.hour}:{t.minute:02d}"


def unic_serial(date_val, uid):
    """UNIC CODE = Excel serial + USER ID (උදා 46203CSSUN157)."""
    s = excel_serial(date_val)
    uid = str(uid).strip()
    return f"{s}{uid}" if s != "" else uid


def team_user_ids(user_df: pd.DataFrame, leader_uid: str) -> set:
    """
    Leader කෙනෙක්ට පේන USER ID set එක:
      තමන් + SUPERVISOR ID = leader වෙච්ච හැම user කෙනෙක්ම (recursive, multi-level).
    """
    leader_uid = str(leader_uid).strip()
    allowed = {leader_uid}
    if user_df is None or user_df.empty or \
       "SUPERVISOR ID" not in user_df or "USER ID" not in user_df:
        return allowed
    reports = {}
    for _, r in user_df.iterrows():
        sup = str(r.get("SUPERVISOR ID", "")).strip()
        uid = str(r.get("USER ID", "")).strip()
        if sup and uid:
            reports.setdefault(sup, []).append(uid)
    queue = [leader_uid]
    while queue:
        cur = queue.pop()
        for child in reports.get(cur, []):
            if child not in allowed:
                allowed.add(child)
                queue.append(child)
    return allowed


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


# WORCK LOCATION values that are NOT working days (leave/off) — excluded from
# worked-days, working-hours and OT calculations everywhere.
NON_WORK_LOCATIONS = ("LEAVE", "OFF")


def is_non_work_location(loc) -> bool:
    return str(loc).strip().upper() in NON_WORK_LOCATIONS


def row_ot_split(working, date_val, location=None, holidays: set | None = None):
    """
    එක attendance row එකක OT split — system එක පුරාම SAME logic (ot_report එකට ගැළපෙනවා):
      • LEAVE/OFF      -> (0, 0)
      • rest day (Sun/holiday, scheduled<=0) -> OT-D = working
      • normal day     -> OT-N = max(working − scheduled, 0)
    return: (otn, otd)
    """
    if is_non_work_location(location):
        return 0.0, 0.0
    w = _f(working)
    sched = scheduled_hours(date_val, holidays)
    if sched <= 0:
        return 0.0, max(w, 0.0)
    return max(w - sched, 0.0), 0.0


def attendance_ot_total(att_df: pd.DataFrame, holidays: set | None = None) -> float:
    """Total OT (OT-N + OT-D) — ot_report එකට ගැළපෙන විදිහට (LEAVE/OFF skip)."""
    if att_df is None or att_df.empty or schema.A_DATE not in att_df:
        return 0.0
    tot = 0.0
    for _, a in att_df.iterrows():
        otn, otd = row_ot_split(a.get(schema.A_WH), a.get(schema.A_DATE),
                                a.get("WORCK LOCATION"), holidays)
        tot += otn + otd
    return round(tot, 2)


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
        reasons.append("Attendance on holiday/Sunday")
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
    status = df.get("APPROVAL STATUS", pd.Series([""] * len(df))).astype(str).str.upper()
    loc = df.get("WORCK LOCATION", pd.Series([""] * len(df))).astype(str).str.upper()
    # APPROVED/OFF status හෝ LEAVE/OFF location -> clear (working day නෙවෙයි)
    cleared = status.isin([schema.APPR_APPROVED, schema.APPR_OFF]) | \
        loc.isin([s.upper() for s in NON_WORK_LOCATIONS])
    mask = df["_rest"] & (~cleared)
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
        ot_h = _f(a.get("# OF OT HRS"))
        # # OF OT HRS = 0 නම් OT නෑ -> flag කරන්නේ නෑ (column එක authoritative)
        if ot_h <= 0:
            continue
        # admin remark එක්ක clear කරපු rows -> skip
        if str(a.get("APPROVAL STATUS", "")).strip().upper() == schema.APPR_OT_CLEARED:
            continue
        d = _to_date(a.get("DATE"))
        key = (str(a.get("USER ID", "")).strip(), d.isoformat() if d else "")
        if key not in ot_keys:
            row = a.to_dict()
            row["EXTRA HRS"] = round(ot_h, 2)
            row["ISSUE"] = "OT worked but no OT-N/OT-D transaction"
            flagged.append(row)
    return pd.DataFrame(flagged)


def audit_weekly_ot(att_df: pd.DataFrame, holidays: set | None = None) -> pd.DataFrame:
    """සතියකට OT පැය 15+ ගිය user-week combinations (LEAVE/OFF skip, recomputed OT)."""
    if att_df.empty or "# OF OT HRS" not in att_df:
        return pd.DataFrame()
    recs = []
    for _, a in att_df.iterrows():
        d = _to_date(a.get("DATE"))
        if d is None:
            continue
        otn, otd = row_ot_split(a.get(schema.A_WH), d, a.get("WORCK LOCATION"), holidays)
        recs.append({
            "USER ID": str(a.get("USER ID", "")).strip(),
            "USER NAME": a.get("USER NAME", ""),
            "WEEK": _week_key(d),
            "OT": otn + otd,
        })
    if not recs:
        return pd.DataFrame()
    g = pd.DataFrame(recs).groupby(
        ["USER ID", "USER NAME", "WEEK"], as_index=False)["OT"].sum()
    g = g.rename(columns={"OT": "WEEKLY OT HRS"})
    return g[g["WEEKLY OT HRS"] > schema.WEEKLY_OT_CAP].sort_values(
        "WEEKLY OT HRS", ascending=False)


def audit_monthly_ot(att_df: pd.DataFrame, holidays: set | None = None) -> pd.DataFrame:
    """මාසෙකට OT පැය 60+ ගිය user-month combinations (LEAVE/OFF skip, recomputed OT)."""
    if att_df.empty or "# OF OT HRS" not in att_df:
        return pd.DataFrame()
    recs = []
    for _, a in att_df.iterrows():
        d = _to_date(a.get("DATE"))
        if d is None:
            continue
        otn, otd = row_ot_split(a.get(schema.A_WH), d, a.get("WORCK LOCATION"), holidays)
        recs.append({
            "USER ID": str(a.get("USER ID", "")).strip(),
            "USER NAME": a.get("USER NAME", ""),
            "MONTH": _month_key(d),
            "OT": otn + otd,
        })
    if not recs:
        return pd.DataFrame()
    g = pd.DataFrame(recs).groupby(
        ["USER ID", "USER NAME", "MONTH"], as_index=False)["OT"].sum()
    g = g.rename(columns={"OT": "MONTHLY OT HRS"})
    return g[g["MONTHLY OT HRS"] > schema.MONTHLY_OT_CAP].sort_values(
        "MONTHLY OT HRS", ascending=False)


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
                "ISSUE": "No transaction on this date",
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
                         month: str | None = None,
                         holidays: set | None = None) -> pd.DataFrame:
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
            otn_r, otd_r = row_ot_split(a.get(schema.A_WH), d,
                                        a.get("WORCK LOCATION"), holidays)
            s["OT HRS"] += otn_r + otd_r

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


# ═══════════════════ UPLOAD validation (rules apply on bulk add) ═══════════════════
def validate_attendance_upload(df: pd.DataFrame, existing_att: pd.DataFrame,
                               holidays: set, txn_df: pd.DataFrame = None,
                               user_df: pd.DataFrame = None):
    """
    Upload කරන ATTANDANCE rows වල **ඉතුරු columns calculate කරනවා** + rules check:
      • # OF WORKING HRS = (OUT − IN) − LUNCH & TEA(1)
      • # OF OT HRS      = max(WORKING − SCHEDULED, 0)
      • SCHEDULED HRS    = schedule (8/5/0)
      • UTILIZED HOURS   = ඒ user+date එකට TRANSACTION වල UTILIZE HOURS එකතුව
      • UTILIZATION      = UTILIZED ÷ WORKING
      • USER NAME / DEPARTMENT / SUB DEPARTMENT  -> USER-M වලින්
      • Day, UNIC CODE
    Rules: WORKING > 20 හෝ නිවාඩු/ඉරිදා -> APPROVAL STATUS = PENDING.
    return: (save_df, display_df)  display එකේ '⚠ VIOLATION' column.
    """
    H = schema.SHEETS["ATTANDANCE"]["headers"]
    out = pd.DataFrame({h: (df[h] if h in df.columns else "") for h in H}).astype(object)

    # lookups
    user_lut = {}
    if user_df is not None and not user_df.empty and "USER ID" in user_df:
        for _, u in user_df.iterrows():
            user_lut[str(u.get("USER ID", "")).strip()] = (
                u.get("USER NAME", ""), u.get("DEPARTMENT", ""),
                u.get("SUB DEPARTMENT", ""))
    util_lut = {}
    if txn_df is not None and not txn_df.empty and \
       {schema.T_USER, schema.T_DATE, schema.T_UTIL} <= set(txn_df.columns):
        for _, t in txn_df.iterrows():
            d = _to_date(t.get(schema.T_DATE))
            if d is None:
                continue
            key = (str(t.get(schema.T_USER, "")).strip(), d.isoformat())
            util_lut[key] = util_lut.get(key, 0.0) + _f(t.get(schema.T_UTIL))

    for i in out.index:
        uid = str(out.at[i, "USER ID"]).strip()
        d = _to_date(out.at[i, "DATE"])
        diso = d.isoformat() if d else ""
        lunch = _f(out.at[i, "LUNCH & TEA"], 1.0) or 1.0
        res = compute_attendance(out.at[i, "DATE"], out.at[i, "IN DATE & TIME"],
                                 out.at[i, "OUT DATE & TIME"], lunch,
                                 util_lut.get((uid, diso), 0.0), holidays)
        # LEAVE / OFF -> working day නෙවෙයි
        if is_non_work_location(out.at[i, "WORCK LOCATION"]):
            res = {"working": 0, "ot": 0, "sched": res["sched"], "utilization": 0}
        out.at[i, "LUNCH & TEA"] = lunch
        out.at[i, "# OF WORKING HRS"] = res["working"]
        out.at[i, "# OF OT HRS"] = res["ot"]
        out.at[i, "SCHEDULED HRS"] = res["sched"]
        out.at[i, "UTILIZED HOURS"] = round(util_lut.get((uid, diso), 0.0), 4)
        out.at[i, "UTILIZATION"] = res["utilization"]
        if d:
            out.at[i, "Day"] = d.strftime("%a").upper()
        name, dept, sub = user_lut.get(uid, ("", "", ""))
        out.at[i, "USER NAME"] = out.at[i, "USER NAME"] or name
        out.at[i, "DEPARTMENT"] = out.at[i, "DEPARTMENT"] or dept
        out.at[i, "SUB DEPARTMENT"] = out.at[i, "SUB DEPARTMENT"] or sub
        # approval
        needs, reason = attendance_needs_approval(res["working"], out.at[i, "DATE"], holidays)
        out.at[i, "APPROVAL STATUS"] = schema.APPR_PENDING if needs else schema.APPR_OK
        out.at[i, "APPROVAL NOTE"] = reason
        # ── Excel save format: UNIC=serial+uid, DATE=M/D/YYYY, IN/OUT=M/D/YYYY H:MM ──
        if d and uid:
            out.at[i, "UNIC CODE"] = unic_serial(d, uid)
        in_fmt = fmt_datetime(out.at[i, "IN DATE & TIME"])
        out_fmt = fmt_datetime(out.at[i, "OUT DATE & TIME"])
        if str(out.at[i, "IN DATE & TIME"]).strip():
            out.at[i, "IN DATE & TIME"] = in_fmt
        if str(out.at[i, "OUT DATE & TIME"]).strip():
            out.at[i, "OUT DATE & TIME"] = out_fmt
        if d:
            out.at[i, "DATE"] = fmt_date(d)

    # weekly OT highlight (existing + new)
    combined = pd.concat([existing_att, out], ignore_index=True) \
        if (existing_att is not None and not existing_att.empty) else out
    wk = audit_weekly_ot(combined)
    over = set(zip(wk["USER ID"].astype(str), wk["WEEK"])) if not wk.empty else set()

    display = out.copy()
    viol = []
    for _, r in out.iterrows():
        d = _to_date(r["DATE"])
        wkey = (str(r["USER ID"]).strip(), _week_key(d)) if d else None
        parts = []
        if r["APPROVAL NOTE"]:
            parts.append(r["APPROVAL NOTE"])
        if wkey in over:
            parts.append("Weekly OT > 15")
        viol.append(" / ".join(parts))
    display["⚠ VIOLATION"] = viol
    return out, display


def validate_transaction_upload(df: pd.DataFrame, tcode_lut: dict):
    """
    TRANSACTION rows වලට data-quality check:
      • T-CODE එක TCODE-M එකේ තියෙනවද
      • TIME එක NORMAL / OT -N / OT -D ද
      • # OF TRANSACTION > 0 ද
    return: (df_aligned, display_df, error_mask)  — error_mask True = block.
    """
    H = schema.SHEETS["TRANSACTION"]["headers"]
    out = pd.DataFrame({h: (df[h] if h in df.columns else "") for h in H})
    valid_times = {"NORMAL", "OT-N", "OT-D"}
    errs, mask = [], []
    for _, r in out.iterrows():
        e = []
        if str(r["T-CODE"]).strip() not in tcode_lut:
            e.append("T-CODE invalid")
        if str(r["TIME"]).upper().replace(" ", "") not in valid_times:
            e.append("TIME invalid")
        if _f(r["# OF TRANSACTION"]) <= 0:
            e.append("qty ≤ 0")
        errs.append(" / ".join(e))
        mask.append(len(e) > 0)
    display = out.copy()
    display["⚠ ERROR"] = errs
    return out, display, pd.Series(mask, index=out.index)


# ═══════════════════════ COST & REVENUE report (payroll) ═══════════════════════
def _ot_split_and_days(att_df: pd.DataFrame, holidays: set, month: str):
    """
    Attendance වලින් per-user (selected month): worked_days, OT-N hrs, OT-D hrs.
      • normal දවස් වල OT (working − scheduled)  -> OT-N
      • rest day (ඉරිදා/නිවාඩු) වැඩ              -> OT-D
    """
    acc = {}
    if att_df is None or att_df.empty or schema.A_DATE not in att_df:
        return acc
    for _, a in att_df.iterrows():
        d = _to_date(a.get(schema.A_DATE))
        if d is None or _month_key(d) != month:
            continue
        uid = str(a.get(schema.A_USER, "")).strip()
        if not uid:
            continue
        wh = _f(a.get(schema.A_WH))
        loc = str(a.get("WORCK LOCATION", "")).strip().upper()
        s = acc.setdefault(uid, {"days": set(), "otn": 0.0, "otd": 0.0})
        # LEAVE / OFF -> working day එකක් නෙවෙයි, OT එකක් නෙවෙයි
        if loc in NON_WORK_LOCATIONS:
            continue
        if wh > 0:
            s["days"].add(d.isoformat())
        otn_r, otd_r = row_ot_split(wh, d, loc, holidays)
        s["otn"] += otn_r
        s["otd"] += otd_r
    for uid in acc:
        acc[uid]["days"] = len(acc[uid]["days"])   # set -> දවස් ගණන
    return acc


def cost_revenue_report(att_df, txn_df, salary_df, user_df, holidays, month):
    """
    Admin Cost & Revenue report (user-wise, monthly) — Book1 format එක.
      Cost  = Basic + OT-N Amt + OT-D Amt + Fixed Incentive + EPF + ETF + Contractor Fee
      Revenue = Σ REVANUE-NORMAL/OT-N/OT-D (transactions)
      Margin  = Revenue − Cost
    """
    ot = _ot_split_and_days(att_df, holidays, month)

    # revenue per user (month)
    rev = {}
    if txn_df is not None and not txn_df.empty and schema.T_DATE in txn_df:
        for _, t in txn_df.iterrows():
            d = _to_date(t.get(schema.T_DATE))
            if d is None or _month_key(d) != month:
                continue
            uid = str(t.get(schema.T_USER, "")).strip()
            if not uid:
                continue
            r = rev.setdefault(uid, {"n": 0.0, "otn": 0.0, "otd": 0.0})
            r["n"] += _f(t.get(schema.T_REV_N))
            r["otn"] += _f(t.get(schema.T_REV_OTN))
            r["otd"] += _f(t.get(schema.T_REV_OTD))

    # salary lookup
    sal = {}
    if salary_df is not None and not salary_df.empty and "USER ID" in salary_df:
        for _, s in salary_df.iterrows():
            sal[str(s.get("USER ID", "")).strip()] = s

    # name lookup
    name = {}
    if user_df is not None and not user_df.empty and "USER ID" in user_df:
        name = {str(r["USER ID"]).strip(): r.get("USER NAME", "") for _, r in user_df.iterrows()}

    uids = set(ot) | set(rev) | set(sal)
    rows = []
    for uid in sorted(uids):
        o = ot.get(uid, {"days": 0, "otn": 0.0, "otd": 0.0})
        rv = rev.get(uid, {"n": 0.0, "otn": 0.0, "otd": 0.0})
        s = sal.get(uid, {})
        basic = _f(s.get("BASIC SALARY")) if len(s) else 0.0
        hourly = basic / schema.OT_HOURLY_DIVISOR if basic else 0.0
        otn_rate = _f(s.get("OT-N RATE")) or round(hourly * schema.OT_N_MULTIPLIER, 4)
        otd_rate = _f(s.get("OT-D RATE")) or round(hourly * schema.OT_D_MULTIPLIER, 4)
        fixed_inc = _f(s.get("FIXED INCENTIVE")) if len(s) else 0.0
        epf_pct = _f(s.get("EPF %"), schema.EPF_PCT) or schema.EPF_PCT
        etf_pct = _f(s.get("ETF %"), schema.ETF_PCT) or schema.ETF_PCT
        contractor = _f(s.get("CONTRACTOR FEE")) if len(s) else 0.0

        otn_amt = round(o["otn"] * otn_rate, 3)
        otd_amt = round(o["otd"] * otd_rate, 3)
        gross = round(basic + otn_amt + otd_amt + fixed_inc, 3)
        epf = round(basic * epf_pct / 100, 3)
        etf = round(basic * etf_pct / 100, 3)
        cost = round(gross + epf + etf + contractor, 3)
        total_rev = round(rv["n"] + rv["otn"] + rv["otd"], 3)
        rows.append({
            "USER ID": uid,
            "CSS USER NAME": (s.get("CSS USER NAME") if len(s) and str(s.get("CSS USER NAME", "")).strip() else name.get(uid, "")),
            "JOB TITLE": s.get("JOB TITLE", "") if len(s) else "",
            "WORKED DAYS": o["days"],
            "OT-N HRS": round(o["otn"], 3), "OT-D HRS": round(o["otd"], 3),
            "BASIC SALARY": round(basic, 3),
            "OT-N AMOUNT": otn_amt, "OT-D AMOUNT": otd_amt,
            "FIXED INCENTIVE": round(fixed_inc, 3),
            "TOTAL GROSS": gross, "EPF": epf, "ETF": etf,
            "CONTRACTOR FEE": round(contractor, 3),
            "COST TO COMPANY": cost,
            "REVENUE NORMAL": round(rv["n"], 3),
            "REVENUE OT-N": round(rv["otn"], 3),
            "REVENUE OT-D": round(rv["otd"], 3),
            "OT-N VARIANCE": round(rv["otn"] - otn_amt, 3),
            "OT-D VARIANCE": round(rv["otd"] - otd_amt, 3),
            "TOTAL REVENUE": total_rev,
            "MARGIN": round(total_rev - cost, 3),
        })
    cols = ["USER ID", "CSS USER NAME", "JOB TITLE", "WORKED DAYS", "OT-N HRS",
            "OT-D HRS", "BASIC SALARY", "OT-N AMOUNT", "OT-D AMOUNT",
            "FIXED INCENTIVE", "TOTAL GROSS", "EPF", "ETF", "CONTRACTOR FEE",
            "COST TO COMPANY", "REVENUE NORMAL", "REVENUE OT-N", "REVENUE OT-D",
            "OT-N VARIANCE", "OT-D VARIANCE", "TOTAL REVENUE", "MARGIN"]
    return pd.DataFrame(rows, columns=cols)


def site_volume_month(txn_df: pd.DataFrame, month: str) -> pd.DataFrame:
    """SITE අනුව current-month transaction volume (# OF TRANSACTION එකතුව)."""
    if txn_df is None or txn_df.empty or schema.T_DATE not in txn_df:
        return pd.DataFrame()
    rows = {}
    for _, t in txn_df.iterrows():
        d = _to_date(t.get(schema.T_DATE))
        if d is None or _month_key(d) != month:
            continue
        site = str(t.get("SITE", "")).strip() or "—"
        rows[site] = rows.get(site, 0.0) + _f(t.get(schema.T_QTY))
    return pd.DataFrame(sorted(rows.items(), key=lambda x: -x[1]),
                        columns=["SITE", "VOLUME"])


def top_users_volume(txn_df: pd.DataFrame, month: str, n: int = 5) -> pd.DataFrame:
    """Current-month වැඩිම transaction කරපු top-N users."""
    if txn_df is None or txn_df.empty or schema.T_DATE not in txn_df:
        return pd.DataFrame()
    rows = {}
    for _, t in txn_df.iterrows():
        d = _to_date(t.get(schema.T_DATE))
        if d is None or _month_key(d) != month:
            continue
        key = (str(t.get(schema.T_USER, "")).strip(), str(t.get(schema.T_NAME, "")).strip())
        if not key[0]:
            continue
        rows[key] = rows.get(key, 0.0) + _f(t.get(schema.T_QTY))
    data = [{"USER": (nm or uid), "VOLUME": v} for (uid, nm), v in rows.items()]
    df = pd.DataFrame(data)
    if df.empty:
        return df
    return df.sort_values("VOLUME", ascending=False).head(n).reset_index(drop=True)


def top_users_revenue(txn_df: pd.DataFrame, month: str, n: int = 5) -> pd.DataFrame:
    """Current-month වැඩිම Revenue (Normal+OT-N+OT-D) කරපු top-N users."""
    if txn_df is None or txn_df.empty or schema.T_DATE not in txn_df:
        return pd.DataFrame()
    rows = {}
    for _, t in txn_df.iterrows():
        d = _to_date(t.get(schema.T_DATE))
        if d is None or _month_key(d) != month:
            continue
        key = (str(t.get(schema.T_USER, "")).strip(), str(t.get(schema.T_NAME, "")).strip())
        if not key[0]:
            continue
        rev = _f(t.get(schema.T_REV_N)) + _f(t.get(schema.T_REV_OTN)) + _f(t.get(schema.T_REV_OTD))
        rows[key] = rows.get(key, 0.0) + rev
    data = [{"USER": (nm or uid), "REVENUE": round(v, 2)} for (uid, nm), v in rows.items()]
    df = pd.DataFrame(data)
    if df.empty:
        return df
    return df.sort_values("REVENUE", ascending=False).head(n).reset_index(drop=True)


def ot_report(att_df: pd.DataFrame, holidays: set, start=None, end=None):
    """
    User-wise total OT report. start/end දුන්නොත් ඒ range එකට filter වෙනවා.
      OT-N = normal දවස් වල OT (working − scheduled)
      OT-D = rest day (ඉරිදා/නිවාඩු) වැඩ
      TOTAL OT = OT-N + OT-D
    """
    if att_df is None or att_df.empty or schema.A_DATE not in att_df:
        return pd.DataFrame(columns=["USER ID", "USER NAME", "WORKED DAYS",
                                     "OT-N HRS", "OT-D HRS", "TOTAL OT HRS"])
    s = _to_date(start) if start else None
    e = _to_date(end) if end else None
    acc = {}
    for _, a in att_df.iterrows():
        d = _to_date(a.get(schema.A_DATE))
        if d is None:
            continue
        if (s and d < s) or (e and d > e):
            continue
        uid = str(a.get(schema.A_USER, "")).strip()
        if not uid:
            continue
        if is_non_work_location(a.get("WORCK LOCATION", "")):
            continue   # LEAVE / OFF -> working day නෙවෙයි
        wh = _f(a.get(schema.A_WH))
        otn_r, otd_r = row_ot_split(wh, d, a.get("WORCK LOCATION"), holidays)
        rec = acc.setdefault(uid, {"name": a.get("USER NAME", ""), "days": 0,
                                   "otn": 0.0, "otd": 0.0})
        if not rec["name"]:
            rec["name"] = a.get("USER NAME", "")
        if wh > 0:
            rec["days"] += 1
        rec["otn"] += otn_r
        rec["otd"] += otd_r
    rows = []
    for uid, r in acc.items():
        otn, otd = round(r["otn"], 2), round(r["otd"], 2)
        rows.append({"USER ID": uid, "USER NAME": r["name"], "WORKED DAYS": r["days"],
                     "OT-N HRS": otn, "OT-D HRS": otd,
                     "TOTAL OT HRS": round(otn + otd, 2)})
    df = pd.DataFrame(rows, columns=["USER ID", "USER NAME", "WORKED DAYS",
                                     "OT-N HRS", "OT-D HRS", "TOTAL OT HRS"])
    return df.sort_values("TOTAL OT HRS", ascending=False).reset_index(drop=True) if not df.empty else df


def recompute_attendance_df(df: pd.DataFrame, holidays: set,
                            txn_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    ATTANDANCE df එකක working/OT/scheduled/utilization, IN/OUT − LUNCH වලින්
    නැවත calculate කරනවා (Data Manager edit වලට). approval/remark වැනි ඒවා තියාගන්නවා.
    LEAVE rows (working=0) අත නෑ.
    """
    if df is None or df.empty:
        return df
    out = df.copy().astype(object)
    util_lut = {}
    if txn_df is not None and not txn_df.empty and \
       {schema.T_USER, schema.T_DATE, schema.T_UTIL} <= set(txn_df.columns):
        for _, t in txn_df.iterrows():
            d = _to_date(t.get(schema.T_DATE))
            if d is None:
                continue
            k = (str(t.get(schema.T_USER, "")).strip(), d.isoformat())
            util_lut[k] = util_lut.get(k, 0.0) + _f(t.get(schema.T_UTIL))
    for i in out.index:
        loc = str(out.at[i, "WORCK LOCATION"]).strip().upper() if "WORCK LOCATION" in out else ""
        in_s = str(out.at[i, "IN DATE & TIME"]).strip() if "IN DATE & TIME" in out else ""
        out_s = str(out.at[i, "OUT DATE & TIME"]).strip() if "OUT DATE & TIME" in out else ""
        # LEAVE / OFF -> working day නෙවෙයි: working/OT = 0
        if loc in NON_WORK_LOCATIONS:
            out.at[i, "# OF WORKING HRS"] = 0
            out.at[i, "# OF OT HRS"] = 0
            out.at[i, "UTILIZED HOURS"] = 0
            out.at[i, "UTILIZATION"] = 0
            continue
        if not in_s or not out_s:
            continue   # IN-OUT නැති rows අත නෑ
        d = _to_date(out.at[i, "DATE"]) if "DATE" in out else None
        uid = str(out.at[i, "USER ID"]).strip() if "USER ID" in out else ""
        lunch = _f(out.at[i, "LUNCH & TEA"], 1.0) or 1.0
        utilized = util_lut.get((uid, d.isoformat()) if d else ("", ""), 0.0)
        res = compute_attendance(out.at[i, "DATE"], in_s, out_s, lunch, utilized, holidays)
        out.at[i, "# OF WORKING HRS"] = res["working"]
        out.at[i, "# OF OT HRS"] = res["ot"]
        out.at[i, "SCHEDULED HRS"] = res["sched"]
        if utilized:
            out.at[i, "UTILIZED HOURS"] = round(utilized, 2)
            out.at[i, "UTILIZATION"] = res["utilization"]
    return out


def recompute_transaction_df(df: pd.DataFrame, tcode_lut: dict) -> pd.DataFrame:
    """
    TRANSACTION df එකක SMV/UTILIZE/REVANUE/In, T-CODE/TIME/qty වලින් නැවත calculate.
    """
    if df is None or df.empty:
        return df
    out = df.copy().astype(object)
    for i in out.index:
        code = str(out.at[i, "T-CODE"]).strip() if "T-CODE" in out else ""
        info = tcode_lut.get(code, {})
        r = calc_transaction(info, out.at[i, schema.T_TIME] if schema.T_TIME in out else "",
                             out.at[i, schema.T_QTY] if schema.T_QTY in out else 0)
        if "CSSTR00" in out and not str(out.at[i, "CSSTR00"]).strip():
            out.at[i, "CSSTR00"] = info.get("desc", "")
        if "UOM" in out and not str(out.at[i, "UOM"]).strip():
            out.at[i, "UOM"] = info.get("uom", "")
        out.at[i, "SMV"] = r["smv"]
        out.at[i, "UTILIZE HOURS"] = r["utilize_hours"]
        out.at[i, schema.T_REV_N] = r["rev_normal"]
        out.at[i, schema.T_REV_OTN] = r["rev_otn"]
        out.at[i, schema.T_REV_OTD] = r["rev_otd"]
        out.at[i, schema.T_INCENTIVE] = r["txn_incentive"]
    return out


# ───────── Bulk attendance generation (all active users) ─────────
# Default shift times by weekday: (IN hour, OUT hour). Mon-Fri 8-17, Sat/Sun 8-13.
SHIFT_TIMES = {0: (8, 17), 1: (8, 17), 2: (8, 17), 3: (8, 17), 4: (8, 17),
               5: (8, 13), 6: (8, 13)}


def bulk_attendance_rows(user_df, dates, holidays, txn_df=None,
                         weekday_lunch=1.0, weekend_lunch=0.0, location="EGF",
                         only_uids=None, admin=False):
    """
    Generate ATTANDANCE rows for every ACTIVE user in USER-M, for each date.
      Mon-Fri 08:00-17:00, Sat/Sun 08:00-13:00. Working/OT auto-computed.
      Rest-day (Sun/holiday) rows -> PENDING (admin approval). admin=True -> APPROVED.
    Returns a list of rows (ATTANDANCE header order) ready for upsert.
    """
    H = schema.SHEETS["ATTANDANCE"]["headers"]
    if user_df is None or user_df.empty:
        return []
    # utilized-hours lookup from transactions
    util_lut = {}
    if txn_df is not None and not txn_df.empty and \
       {schema.T_USER, schema.T_DATE, schema.T_UTIL} <= set(txn_df.columns):
        for _, t in txn_df.iterrows():
            d = _to_date(t.get(schema.T_DATE))
            if d is None:
                continue
            k = (str(t.get(schema.T_USER, "")).strip(), d.isoformat())
            util_lut[k] = util_lut.get(k, 0.0) + _f(t.get(schema.T_UTIL))

    rows = []
    for _, u in user_df.iterrows():
        if str(u.get("ACTIVE", "")).strip().upper() in ("N", "NO", "0", "INACTIVE"):
            continue
        uid = str(u.get("USER ID", "")).strip()
        if not uid or (only_uids is not None and uid not in only_uids):
            continue
        name = u.get("USER NAME", "")
        dept = u.get("DEPARTMENT", "")
        sub = u.get("SUB DEPARTMENT", "")
        for d in dates:
            wd = d.weekday()
            in_h, out_h = SHIFT_TIMES.get(wd, (8, 17))
            lunch = weekend_lunch if wd >= 5 else weekday_lunch
            in_dt = dt.datetime(d.year, d.month, d.day, in_h, 0)
            out_dt = dt.datetime(d.year, d.month, d.day, out_h, 0)
            utilized = util_lut.get((uid, d.isoformat()), 0.0)
            res = compute_attendance(d, in_dt.isoformat(" "), out_dt.isoformat(" "),
                                     lunch, utilized, holidays)
            needs, reason = attendance_needs_approval(res["working"], d, holidays)
            if not needs:
                status, note = schema.APPR_OK, ""
            elif admin:
                status, note = schema.APPR_APPROVED, f"Bulk admin approved: {reason}"
            else:
                status, note = schema.APPR_PENDING, reason
            row = {h: "" for h in H}
            row.update({
                "UNIC CODE": unic_serial(d, uid), "DATE": fmt_date(d),
                "USER ID": uid, "USER NAME": name, "DEPARTMENT": dept,
                "SUB DEPARTMENT": sub, "IN DATE & TIME": fmt_datetime(in_dt),
                "OUT DATE & TIME": fmt_datetime(out_dt), "LUNCH & TEA": lunch,
                "WORCK LOCATION": location, "# OF WORKING HRS": res["working"],
                "# OF OT HRS": res["ot"], "UTILIZED HOURS": round(utilized, 2),
                "UTILIZATION": res["utilization"], "Day": d.strftime("%a").upper(),
                "SCHEDULED HRS": res["sched"], "APPROVAL STATUS": status,
                "APPROVAL NOTE": note,
            })
            rows.append([row[h] for h in H])
    return rows


def date_range_list(start, end):
    """start..end (inclusive) date list."""
    s, e = _to_date(start), _to_date(end)
    if s is None or e is None or e < s:
        return []
    out, cur = [], s
    while cur <= e:
        out.append(cur)
        cur += dt.timedelta(days=1)
    return out


# ═══════════════════════ DATA AUDIT ENGINE (integrity) ═══════════════════════
def data_audit_attendance(att_df: pd.DataFrame, holidays: set, txn_df=None):
    """
    ATTANDANCE data integrity check (rule-violations නෙවෙයි — data errors):
      • LEAVE/OFF rows වල IN/OUT time හෝ OT/working hrs තියෙනවද (තිබිය යුතු නෑ)
      • working hrs = (OUT−IN)−lunch ද? OT = working−scheduled ද? scheduled හරිද?
      • UNIC CODE = serial+uid ද? DATE format හරිද?
    return: issues DataFrame [UNIC CODE, DATE, USER ID, USER NAME, FIELD, CURRENT, EXPECTED, ISSUE]
    """
    cols = ["UNIC CODE", "DATE", "USER ID", "USER NAME", "FIELD", "CURRENT", "EXPECTED", "ISSUE"]
    if att_df is None or att_df.empty:
        return pd.DataFrame(columns=cols)
    issues = []
    for _, a in att_df.iterrows():
        d = _to_date(a.get("DATE"))
        uid = str(a.get("USER ID", "")).strip()
        loc = str(a.get("WORCK LOCATION", "")).strip()
        in_s = str(a.get("IN DATE & TIME", "")).strip()
        out_s = str(a.get("OUT DATE & TIME", "")).strip()
        wh = _f(a.get("# OF WORKING HRS"))
        ot = _f(a.get("# OF OT HRS"))
        sched_stored = a.get("SCHEDULED HRS", "")
        unic = str(a.get("UNIC CODE", "")).strip()
        base = {"UNIC CODE": unic, "DATE": a.get("DATE", ""), "USER ID": uid,
                "USER NAME": a.get("USER NAME", "")}

        def add(field, cur, exp, issue):
            issues.append({**base, "FIELD": field, "CURRENT": cur, "EXPECTED": exp, "ISSUE": issue})

        # UNIC CODE
        if d and uid:
            exp_u = unic_serial(d, uid)
            if unic != exp_u:
                add("UNIC CODE", unic, exp_u, "UNIC CODE should be Excel-serial + USER ID")

        if is_non_work_location(loc):
            # LEAVE / OFF -> IN/OUT/OT/working තිබිය යුතු නෑ
            if in_s:
                add("IN DATE & TIME", in_s, "(empty)", f"{loc}: IN time should be empty")
            if out_s:
                add("OUT DATE & TIME", out_s, "(empty)", f"{loc}: OUT time should be empty")
            if ot != 0:
                add("# OF OT HRS", ot, 0, f"{loc}: OT should be 0")
            if wh != 0:
                add("# OF WORKING HRS", wh, 0, f"{loc}: working hrs should be 0")
        elif in_s and out_s:
            lunch = _f(a.get("LUNCH & TEA"), 1.0) or 1.0
            exp_wh = compute_work_hours(in_s, out_s, lunch)
            exp_sched = scheduled_hours(d, holidays) if d else 0
            exp_ot = round(max(exp_wh - exp_sched, 0.0), 2)
            if abs(wh - exp_wh) > 0.02:
                add("# OF WORKING HRS", wh, exp_wh, "Working ≠ (OUT−IN) − LUNCH")
            if abs(ot - exp_ot) > 0.02:
                add("# OF OT HRS", ot, exp_ot, "OT ≠ working − scheduled")
            if str(sched_stored).strip() and abs(_f(sched_stored) - exp_sched) > 0.02:
                add("SCHEDULED HRS", sched_stored, round(exp_sched, 2), "Scheduled hrs mismatch")
    return pd.DataFrame(issues, columns=cols)


def fix_attendance_df(att_df: pd.DataFrame, holidays: set, txn_df=None):
    """data_audit_attendance හමුවුණ වැරදි හදනවා — corrected ATTANDANCE df එකක් return."""
    if att_df is None or att_df.empty:
        return att_df
    out = att_df.copy().astype(object)
    util_lut = {}
    if txn_df is not None and not txn_df.empty and \
       {schema.T_USER, schema.T_DATE, schema.T_UTIL} <= set(txn_df.columns):
        for _, t in txn_df.iterrows():
            d = _to_date(t.get(schema.T_DATE))
            if d is None:
                continue
            k = (str(t.get(schema.T_USER, "")).strip(), d.isoformat())
            util_lut[k] = util_lut.get(k, 0.0) + _f(t.get(schema.T_UTIL))
    for i in out.index:
        d = _to_date(out.at[i, "DATE"])
        uid = str(out.at[i, "USER ID"]).strip()
        loc = str(out.at[i, "WORCK LOCATION"]).strip()
        if d and uid:
            out.at[i, "UNIC CODE"] = unic_serial(d, uid)
            out.at[i, "DATE"] = fmt_date(d)
            out.at[i, "Day"] = d.strftime("%a").upper()
        if is_non_work_location(loc):
            out.at[i, "IN DATE & TIME"] = ""
            out.at[i, "OUT DATE & TIME"] = ""
            out.at[i, "# OF WORKING HRS"] = 0
            out.at[i, "# OF OT HRS"] = 0
            out.at[i, "UTILIZED HOURS"] = 0
            out.at[i, "UTILIZATION"] = 0
            out.at[i, "SCHEDULED HRS"] = round(scheduled_hours(d, holidays), 2) if d else ""
            continue
        in_s = str(out.at[i, "IN DATE & TIME"]).strip()
        out_s = str(out.at[i, "OUT DATE & TIME"]).strip()
        if in_s and out_s:
            lunch = _f(out.at[i, "LUNCH & TEA"], 1.0) or 1.0
            utilized = util_lut.get((uid, d.isoformat()) if d else ("", ""), 0.0)
            res = compute_attendance(out.at[i, "DATE"], in_s, out_s, lunch, utilized, holidays)
            out.at[i, "IN DATE & TIME"] = fmt_datetime(in_s)
            out.at[i, "OUT DATE & TIME"] = fmt_datetime(out_s)
            out.at[i, "# OF WORKING HRS"] = res["working"]
            out.at[i, "# OF OT HRS"] = res["ot"]
            out.at[i, "SCHEDULED HRS"] = res["sched"]
            if utilized:
                out.at[i, "UTILIZED HOURS"] = round(utilized, 2)
                out.at[i, "UTILIZATION"] = res["utilization"]
    return out


def data_audit_transaction(txn_df: pd.DataFrame, tcode_lut: dict):
    """TRANSACTION calculation integrity — SMV/UTILIZE/REVANUE/In vs recompute."""
    cols = ["UNIC CODE", "DATE", "USER ID", "T-CODE", "FIELD", "CURRENT", "EXPECTED", "ISSUE"]
    if txn_df is None or txn_df.empty:
        return pd.DataFrame(columns=cols)
    issues = []
    for _, t in txn_df.iterrows():
        code = str(t.get("T-CODE", "")).strip()
        info = tcode_lut.get(code, {})
        r = calc_transaction(info, t.get(schema.T_TIME, ""), t.get(schema.T_QTY, 0))
        base = {"UNIC CODE": t.get("UNIC CODE", ""), "DATE": t.get(schema.T_DATE, ""),
                "USER ID": t.get(schema.T_USER, ""), "T-CODE": code}

        def add(field, cur, exp, issue):
            issues.append({**base, "FIELD": field, "CURRENT": cur, "EXPECTED": exp, "ISSUE": issue})

        checks = [
            ("SMV", _f(t.get(schema.T_SMV)), r["smv"]),
            ("UTILIZE HOURS", _f(t.get(schema.T_UTIL)), r["utilize_hours"]),
            (schema.T_REV_N, _f(t.get(schema.T_REV_N)), r["rev_normal"]),
            (schema.T_REV_OTN, _f(t.get(schema.T_REV_OTN)), r["rev_otn"]),
            (schema.T_REV_OTD, _f(t.get(schema.T_REV_OTD)), r["rev_otd"]),
            (schema.T_INCENTIVE, _f(t.get(schema.T_INCENTIVE)), r["txn_incentive"]),
        ]
        for field, cur, exp in checks:
            if abs(cur - exp) > 0.02:
                add(field, round(cur, 4), round(exp, 4), f"{field} ≠ recomputed")
        if not info and code:
            add("T-CODE", code, "", "T-CODE not found in TCODE-M")
    return pd.DataFrame(issues, columns=cols)
