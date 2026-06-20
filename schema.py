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
            "USER NAME", "SUPERVISOR ID", "SUPERVISOR", "ACTIVE",
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
    "TRANSACTION": {
        "title": "TRANSACTION",
        "kind": "txn",
        "seed": None,
        "headers": [
            "UNIC CODE", "DATE", "USER ID", "USER NAME", "SITE", "CUSTOMMER",
            "T-CODE", "DESCRIPTION", "TIME", "UOM", "# OF TRANSACTION",
            "SMV", "UTILIZE HOURS",
            "REVANUE-NORMAL", "REVANUE-OT-N", "REVANUE-OT-D",
            "TOTAL REVANUE", "TXN INCENTIVE",
        ],
    },
    "ATTANDANCE": {
        "title": "ATTANDANCE",
        "kind": "txn",
        "seed": None,
        "headers": [
            "UNIC CODE", "DATE", "USER ID", "USER NAME", "DEPARTMENT",
            "SUB DEPARTMENT", "IN TIME", "OUT TIME", "LUNCH & TEA",
            "WORK LOCATION", "IDLE TIME", "# OF WORKING HRS", "# OF OT HRS",
            "SCHEDULED HRS", "UTILIZED HOURS", "UTILIZATION", "DAY", "REMARK",
            "APPROVAL STATUS", "APPROVAL NOTE",
        ],
    },
    "OT APPROVAL": {
        "title": "OT APPROVAL",
        "kind": "txn",
        "seed": None,
        "headers": [
            "UNIC", "REQUEST DATE", "OT PLANNED DATE", "SITE", "CLIENT",
            "OPERATION", "USER ID", "USER NAME", "REQUEST OT HOURS",
            "APPROVED OT HOURS", "APPROVED PERSON", "REASON FOR OT", "STATUS",
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
