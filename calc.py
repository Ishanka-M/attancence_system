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
    if not txn_df.empty and "USER ID" in txn_df:
        col = "TXN INCENTIVE" if "TXN INCENTIVE" in txn_df else None
        for _, r in txn_df.iterrows():
            uid = str(r.get("USER ID", "")).strip()
            if not uid:
                continue
            txn_inc[uid] = txn_inc.get(uid, 0.0) + _f(r.get(col)) if col else txn_inc.get(uid, 0.0)

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
        zero_bonus = schema.ZERO_COMPLAINT_BONUS if ncomp == 0 else 0
        kpi_bonus = schema.ONTIME_KPI_BONUS if ontime.get(uid, 0) > 0 else 0
        otr = _f(ot_recovery.get(uid))
        ot_bonus = schema.FULL_OT_RECOVERY_BONUS if otr >= 100 else 0
        total = round(ti + zero_bonus + kpi_bonus + ot_bonus, 2)
        rows.append([
            period_label, uid, name, ti, ncomp, zero_bonus, kpi_bonus,
            otr, ot_bonus, total, schema.DEFAULT_TARGET,
            round(schema.DEFAULT_TARGET - total, 2), "",
        ])

    return pd.DataFrame(rows, columns=schema.SHEETS["INSENTIVE"]["headers"])
