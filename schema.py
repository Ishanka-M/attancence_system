"""
schema.py
=========
Google Sheet එකේ හැම worksheet (tab) එකකම නම + columns මෙතන define කරනවා.
gsheets.ensure_all() මේ schema එක කියවලා නැති sheets auto-create කරනවා.

සෑම sheet එකකම:
  - title  : Google Sheet tab එකේ නම
  - headers: column header list
  - kind   : "master" (seed වෙන reference data) | "txn" (daily data entry)
  - seed   : seeds.json එකේ key එක (master sheets වලට පමණයි)
"""

# ── TIME tokens (original Excel වල තිබුණු ආකාරයටම තබාගෙන) ──────────────
TIME_NORMAL = "NORMAL"
TIME_OT_N = "OT -N"
TIME_OT_D = "OT -D"

SHEETS = {
    # ===================== MASTERS (reference data) =====================
    "USER-M": {
        "title": "USER-M",
        "kind": "master",
        "seed": "USER",
        "headers": [
            "USER ID", "COMPANY", "DEPARTMENT", "SUB DEPARTMENT",
            "USER NAME", "SUPERVISOR ID", "SUPERVISOR", "ACTIVE", "PASSWORD",
        ],
    },
    "TCODE-M": {
        "title": "TCODE-M",
        "kind": "master",
        "seed": "TCODE",
        # Master sheet - Finalized එකේ core SMV/rate columns
        "headers": [
            "System", "T-CODE", "Description", "UOM", "Volume",
            "CSS SMV", "SMV (M)", "NORMAL rate", "OT-N rate", "OT-D rate",
        ],
    },
    "SITE-M": {
        "title": "SITE-M",
        "kind": "master",
        "seed": "SITE",
        "headers": ["SITE", "DESCRIPTION", "HJ CODE", "ADDRESS", "SITE HEAD"],
    },
    "CUSTOMMER-M": {
        "title": "CUSTOMMER-M",
        "kind": "master",
        "seed": "CUSTOMMER",
        "headers": [
            "CUSTOMMER", "SITE", "HJ SITE", "DISCRIPTION", "ADDRESS",
            "CUSTOMMER CORDINATOR", "SITE MANAGER", "CUSTOMMER COORDINATOR 2",
        ],
    },
    "TIME-M": {
        "title": "TIME-M",
        "kind": "master",
        "seed": "TIME",
        "headers": ["TIME"],
    },
    "LOCATION-M": {
        "title": "LOCATION-M",
        "kind": "master",
        "seed": "LOCATION",
        "headers": ["LOCATION"],
    },
    "SUPPLIER-M": {
        "title": "SUPPLIER-M",
        "kind": "master",
        "seed": None,
        "headers": ["SUPPLIER NAME", "ADDRESS", "MOBILE #", "WATS APP",
                    "MOBILE ALLOUNCE", "E-MAIL"],
    },
    "HOLIDAY-M": {
        "title": "HOLIDAY-M",
        "kind": "master",
        "seed": None,
        # Admin මෙතනට නිවාඩු දවස් දානවා. TYPE = Public / Special / Mercantile…
        "headers": ["DATE", "DESCRIPTION", "TYPE"],
    },

    # ===================== TRANSACTIONAL (daily entry) ==================
    # headers මුල් Excel එකේ විදිහටම (order + names). "CSSTR00" = Description,
    # "In" = transaction incentive (revenue/10). Column18 / OT -N = legacy empty.
    "TRANSACTION": {
        "title": "TRANSACTION",
        "kind": "txn",
        "seed": None,
        "headers": [
            "UNIC CODE", "Date", "USER ID", "USER NAME", "SITE", "CUSTOMMER",
            "T-CODE", "CSSTR00", "TIME", "UOM", "# OF TRANSACTION",
            "SMV", "UTILIZE HOURS", "REVANUE-NORMAL", "REVANUE-OT -N",
            "REVANUE-OT -D", "In", "Column18", "OT -N",
        ],
    },
    "ATTANDANCE": {
        "title": "ATTANDANCE",
        "kind": "txn",
        "seed": None,
        # මුල් Excel columns 21 + system (audit) columns 3ක් අගට.
        "headers": [
            "UNIC CODE", "DATE", "USER ID", "USER NAME", "DEPARTMENT",
            "SUB DEPARTMENT", "IN DATE & TIME", "OUT DATE & TIME", "LUNCH & TEA",
            "WORCK LOCATION", "IDLE TIME", "# OF WORKING HRS", "# OF OT HRS",
            "# APPROVED PRE OT HRS", "# APPROVED POST OT HRS",
            "# APPROVED CLIENT OT HRS", "UTILIZED HOURS", "UTILIZATION",
            "Day", "Remark", "Insentive",
            "SCHEDULED HRS", "APPROVAL STATUS", "APPROVAL NOTE",
        ],
    },
    "OT APPROVAL": {
        "title": "OT APPROVAL",
        "kind": "txn",
        "seed": None,
        "headers": [
            "UNIC", "REQUEST DATE", "OT PLANNED DATE", "SITE", "CLIENT",
            "OPERATION", "USER ID", "CSS USER NAME", "PRE REQUEST OT HOURS",
            "REVESTED PERSON", "REQUEST OT HOURS", "PRE  OT APPROVED HOURS -SITE",
            "PRE APPROVAL SITE PERSON", "PRE APPROVAL CLIENT",
            "PRE APPROVED PERSON CLIENT", "REASON FOR OT", "MANAGEMENT APPROVAL",
            "POST OT APPROVED SITE", "POST OT APPROVED SITE PERSON",
            "POST OT APPROVAL CLIENT", "POST OT APPROVED CLIENT NAME",
            "ACTUAL OT HRS.", "VARIATION",
        ],
    },
    "CUSTOMMER COMPLAINT": {
        "title": "CUSTOMMER COMPLAINT",
        "kind": "txn",
        "seed": None,
        "headers": [
            "DATE", "USER ID", "USER NAME", "TEAM LEADER ID",
            "TEAM LEADER NAME", "CUSTOMMER", "COMPLAINT", "CA", "PA",
            "IMPLIMENT DATE",
        ],
    },
    "KPI UPDATE": {
        "title": "KPI UPDATE",
        "kind": "txn",
        "seed": None,
        "headers": [
            "DATE", "USER ID", "USER NAME", "ON TIME UPDATE",
            "DESCRIPTION", "SCORE",
        ],
    },

    # ===================== OUTPUT (computed, stored) ====================
    "INSENTIVE": {
        "title": "INSENTIVE",
        "kind": "txn",
        "seed": None,
        "headers": [
            "PERIOD", "USER ID", "USER NAME", "TXN INCENTIVE",
            "# OF COMPLAINTS", "COMPLAINT PENALTY", "ZERO-COMPLAINT BONUS",
            "ON-TIME KPI BONUS", "OT RECOVERY %", "100% OT RECOVERY BONUS",
            "TOTAL INSENTIVE", "TARGET", "BALANCE", "REMARKS",
        ],
    },
}

# Incentive rule constants (INSENTIVE sheet headers එකෙන් ආ values)
ZERO_COMPLAINT_BONUS = 3000      # complaints නැත්නම්
ONTIME_KPI_BONUS = 4000          # KPI on-time update bonus
FULL_OT_RECOVERY_BONUS = 3000    # 100% OT recovery bonus
TXN_INCENTIVE_DIVISOR = 10       # Transaction incentive = revenue / 10
DEFAULT_TARGET = 20000           # Sheet2 එකේ Target = 20000
COMPLAINT_PENALTY = 1000         # complaint එකකට incentive එකෙන් අඩු කරන මුදල

# ───────────────── AUDIT / SCHEDULE rules ─────────────────
WORKING_HRS_CAP = 20             # දවසකට පැය 20 ට වඩා -> Admin approval ඕනේ
WEEKLY_OT_CAP = 15               # සතියකට OT පැය 15 ට වඩා -> highlight

# සතියේ දවස් අනුව scheduled working hours (Python weekday(): 0=Mon … 6=Sun)
#   සතියේ දවස් : 08:00–17:00, lunch 1h  -> 8h
#   සෙනසුරාදා  : 08:00–13:00            -> 5h
#   ඉරිදා      : නිවාඩු                 -> 0h
WORKDAY_HOURS = {0: 8, 1: 8, 2: 8, 3: 8, 4: 8, 5: 5, 6: 0}

# Approval status tokens (ATTANDANCE.APPROVAL STATUS)
APPR_OK = "OK"               # rule violation එකක් නෑ
APPR_PENDING = "PENDING"     # admin approval බලාගෙන
APPR_APPROVED = "APPROVED"
APPR_REJECTED = "REJECTED"

MASTER_SHEETS = [k for k, v in SHEETS.items() if v["kind"] == "master"]
TXN_SHEETS = [k for k, v in SHEETS.items() if v["kind"] == "txn"]

# ───────────── Column-name constants (logic මේවා පාවිච්චි කරනවා) ─────────────
# මුල් Excel headers වෙනස් වුණොත් මෙතන විතරක් වෙනස් කරන්න.
# TRANSACTION
T_DATE, T_USER, T_NAME, T_TIME, T_QTY = "Date", "USER ID", "USER NAME", "TIME", "# OF TRANSACTION"
T_DESC, T_SMV, T_UTIL = "CSSTR00", "SMV", "UTILIZE HOURS"
T_REV_N, T_REV_OTN, T_REV_OTD, T_INCENTIVE = "REVANUE-NORMAL", "REVANUE-OT -N", "REVANUE-OT -D", "In"
# ATTANDANCE
A_DATE, A_USER, A_WH, A_OT = "DATE", "USER ID", "# OF WORKING HRS", "# OF OT HRS"
A_UTILIZED, A_UTILIZATION, A_STATUS = "UTILIZED HOURS", "UTILIZATION", "APPROVAL STATUS"
