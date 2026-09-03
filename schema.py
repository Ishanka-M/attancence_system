"""
schema.py
=========
Defines every worksheet (tab) in the Google Sheet and its columns.
gsheets.ensure_all() reads this schema and AUTO-CREATES any missing tab.

Each sheet entry has:
  - title   : the Google Sheet tab name
  - headers : column header list
  - kind    : "master" | "data" | "log"
  - key     : unique column used for upserts (if any)
  - seed    : default rows for master sheets
"""
from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════
#  STATUS CONSTANTS - used everywhere instead of raw strings
# ═══════════════════════════════════════════════════════════════════

# Line level match status
M_MATCHED = "MATCHED"
M_MISSING = "MISSING IN INVENTORY"
M_QTY = "QTY MISMATCH"
M_ITEM = "ITEM MISMATCH"
M_LOT = "LOT MISMATCH"
M_ASN = "WRONG ASN"
M_EXTRA = "EXTRA IN INVENTORY"
M_PENDING = "NOT CHECKED"

MISMATCH_STATUSES = {M_QTY, M_ITEM, M_LOT, M_ASN}

# Korber / AX GRN status
K_PENDING = "PENDING"
K_DONE = "DONE"
AX_NA = "-"
AX_PENDING = "PENDING"
AX_DONE = "DONE"

P_OPEN = "OPEN"
P_CLEARED = "CLEARED"

STAGE_KORBER = "KORBER GRN"
STAGE_AX = "AX GRN"

PENDING_REASONS = {
    STAGE_KORBER: [
        "Goods not yet received",
        "Short shipment",
        "Excess received",
        "Damaged / QC hold",
        "Wrong item or lot",
        "Awaiting supplier confirmation",
        "Documentation pending",
        "WMS / system issue",
        "Not yet reconciled",
        "Other",
    ],
    STAGE_AX: [
        "Interface error",
        "Awaiting finance approval",
        "Invoice not received",
        "Discrepancy accepted - posting with variance",
        "Master data missing",
        "Awaiting management approval",
        "Other",
    ],
}

PRIORITIES = ["Normal", "High", "Critical"]

D_OPEN = "OPEN"
D_RESOLVED = "RESOLVED"
D_CLOSED = "CLOSED"

# Pending register - a GRN held up at either stage, with the reason
STAGE_KORBER = "KORBER GRN"
STAGE_AX = "AX GRN"
P_OPEN = "OPEN"
P_CLEARED = "CLEARED"

# ASN level status
S_NEW = "NEW"
S_GRN_PENDING = "KORBER GRN PENDING"
S_PARTIAL = "PARTIAL"
S_DISCREPANCY = "DISCREPANCY"
S_KORBER_DONE = "KORBER GRN DONE"
S_AX_PENDING = "AX GRN PENDING"
S_COMPLETE = "FULLY COMPLETE"

STATUS_COLORS = {
    S_NEW: "#94a3b8",
    S_GRN_PENDING: "#a5670c",
    S_PARTIAL: "#b78103",
    S_DISCREPANCY: "#b3261e",
    S_KORBER_DONE: "#0d6e63",
    S_AX_PENDING: "#2d5f9a",
    S_COMPLETE: "#17794a",
}


# ═══════════════════════════════════════════════════════════════════
#  SHEETS
# ═══════════════════════════════════════════════════════════════════

ASN_SUMMARY_HEADERS = [
    "ASN NO", "CLIENT CODE", "PO NUMBER", "VENDOR CODE", "SUPPLIER DESC",
    "UPLOAD DATE", "UPLOADED BY", "SOURCE FILE", "SOURCE SHEET",
    "TOTAL LINES", "TOTAL HU", "TOTAL QTY", "ITEM COUNT",
    "MATCHED LINES", "MISSING LINES", "MISMATCH LINES", "EXTRA LINES",
    "MATCHED QTY", "RECEIVED QTY", "QTY DIFF",
    "STATUS", "KORBER GRN", "KORBER GRN NO", "KORBER GRN DATE",
    "AX GRN", "AX GRN NO", "AX GRN DATE", "AX GRN BY",
    "OVERALL", "LAST RECON", "IMAGES", "REMARK",
]

ASN_DETAIL_HEADERS = [
    "LINE UID", "ASN NO", "ASN LINE", "CLIENT CODE",
    "ITEM NUMBER", "HU ID", "SUPPLIER HU", "LOT NUMBER",
    "QTY", "UOM", "S UOM", "S QTY",
    "PO NUMBER", "PO LINE", "PACKAGE TYPE", "VENDOR CODE",
    "GROSS WEIGHT", "NET WEIGHT", "COLOR", "TYPE QC", "SUPPLIER DESC",
    "UPLOAD DATE", "UPLOADED BY", "SOURCE FILE", "SOURCE SHEET",
    "MATCH STATUS", "INV QTY", "QTY DIFF", "INV ITEM", "INV LOT",
    "INV LOCATION", "INV ASN NO", "INV GRN NO",
    "DISCREPANCY", "KORBER GRN", "AX GRN", "LAST RECON", "REMARK",
]

INVENTORY_HEADERS = [
    "SNAPSHOT AT", "WH ID", "CLIENT CODE", "PALLET", "LOCATION ID",
    "ITEM NUMBER", "DISPLAY ITEM NUMBER", "DESCRIPTION", "LOT NUMBER",
    "ACTUAL QTY", "UNAVAILABLE QTY", "UOM", "STATUS",
    "GRN NUMBER", "ASN NUMBER", "ASN LINE NUMBER", "SUPPLIER HU",
    "PO NUMBER", "INVOICE NUMBER", "VENDOR NAME", "INVENTORY TYPE",
    "SUPPLIER DESC", "S UOM", "S QTY",
]

DISCREPANCY_HEADERS = [
    "DISC ID", "RUN ID", "GENERATED AT", "ASN NO", "ASN LINE", "HU ID",
    "ITEM NUMBER", "LOT NUMBER", "ASN QTY", "INV QTY", "QTY DIFF",
    "DISCREPANCY TYPE", "DETAIL", "SEVERITY",
    "STATUS", "ACTION BY", "CLOSED AT", "NOTE",
]

AX_GRN_HEADERS = [
    "ASN NO", "CLIENT CODE", "KORBER GRN NO", "KORBER GRN DATE",
    "TOTAL LINES", "TOTAL QTY", "PUSHED AT", "PUSHED BY",
    "AX GRN", "AX GRN NO", "AX GRN DATE", "AX GRN BY",
    "OVERALL", "OVERRIDE", "OVERRIDE REASON", "REMARK",
]

PENDING_HEADERS = [
    "PENDING ID", "ASN NO", "STAGE", "REASON", "REMARK", "PRIORITY",
    "RAISED AT", "RAISED BY", "FOLLOW UP", "STATUS",
    "CLEARED AT", "CLEARED BY", "NOTE",
]

ASN_IMAGES_HEADERS = [
    "IMAGE ID", "ASN NO", "FILE NAME", "KIND", "SOURCE", "MIME", "SIZE KB",
    "QUALITY", "STORAGE", "DRIVE FILE ID", "LINK", "UPLOADED AT",
    "UPLOADED BY", "NOTE",
]

IMAGE_DATA_HEADERS = ["IMAGE ID", "SEQ", "CHUNK"]

RECON_LOG_HEADERS = [
    "RUN ID", "RUN AT", "RUN BY", "ASN COUNT", "ASN LIST",
    "INVENTORY ROWS", "LINES CHECKED", "MATCHED", "MISSING",
    "MISMATCH", "EXTRA", "NOTE",
]

EMAIL_LOG_HEADERS = [
    "EMAIL ID", "GENERATED AT", "GENERATED BY", "ASN LIST",
    "SUBJECT", "TO", "CC", "BODY MD",
]

USER_HEADERS = ["USER ID", "USER NAME", "ROLE", "EMAIL", "PIN", "ACTIVE"]

SETTINGS_HEADERS = ["KEY", "VALUE", "DESCRIPTION"]


DEFAULT_SETTINGS = [
    ["CLIENT_CODE", "HIES", "Client code used as the Korber inventory prefix"],
    ["STRIP_CLIENT_PREFIX", "Y", "Strip the 'HIES-' prefix from inventory ASN/item before comparing (Y/N)"],
    ["QTY_TOLERANCE", "0", "Allowed absolute quantity difference before a line is flagged"],
    ["CHECK_ITEM", "Y", "Compare item numbers (Y/N)"],
    ["CHECK_LOT", "Y", "Compare lot numbers (Y/N)"],
    ["CHECK_ASN_NO", "Y", "Compare the inventory ASN number (Y/N)"],
    ["FLAG_EXTRA", "Y", "Flag HUs present in inventory but missing from the ASN document (Y/N)"],
    ["AUTO_RECON", "Y", "Reconcile automatically as soon as an inventory file is uploaded (Y/N)"],
    ["AUTO_PUSH_AX", "Y", "Move Korber GRN Done ASNs straight to AX GRN Pending (Y/N)"],
    ["AUTO_EMAIL", "Y", "Generate the mismatch email automatically after auto reconciliation (Y/N)"],
    ["IMAGE_STORAGE", "DRIVE", "Where attachments are stored: DRIVE (falls back to SHEET) | SHEET"],
    ["KEEP_ORIGINAL", "Y", "Upload images to Drive at original quality with no resizing (Y/N)"],
    ["IMAGE_MAX_PX", "2200", "Maximum image edge in pixels, used only when an image must be compressed"],
    ["IMAGE_QUALITY", "92", "JPEG quality used when an image must be compressed (40-95)"],
    ["DRIVE_FOLDER_ID", "https://drive.google.com/drive/u/2/folders/14t0faZpeMAZIxMAV9q7fQB1ryN1Ps7mP",
     "Drive folder for images and PDFs - paste a folder link or an ID"],
    ["API_RATE_LIMIT", "55", "Maximum Google API calls per minute (Google allows 60)"],
    ["CACHE_TTL", "90", "Sheet data cache lifetime in seconds"],
    ["EMAIL_TO", "", "Default To address for discrepancy emails"],
    ["EMAIL_CC", "", "Default Cc address for discrepancy emails"],
    ["COMPANY", "EFL", "Company name shown on reports and emails"],
    ["SITE", "EGDC", "Warehouse / site code"],
    ["ADMIN_PIN", "1234", "PIN required for admin actions (change it in Setup)"],
]

DEFAULT_USERS = [
    ["ADMIN", "Administrator", "admin", "", "1234", "Y"],
]


SHEETS: dict[str, dict] = {
    "ASN_SUMMARY": {
        "title": "ASN_SUMMARY",
        "kind": "data",
        "key": "ASN NO",
        "headers": ASN_SUMMARY_HEADERS,
    },
    "ASN_DETAIL": {
        "title": "ASN_DETAIL",
        "kind": "data",
        "key": "LINE UID",
        "headers": ASN_DETAIL_HEADERS,
    },
    "INVENTORY": {
        "title": "INVENTORY",
        "kind": "data",
        "key": None,
        "headers": INVENTORY_HEADERS,
    },
    "DISCREPANCY": {
        "title": "DISCREPANCY",
        "kind": "data",
        "key": "DISC ID",
        "headers": DISCREPANCY_HEADERS,
    },
    "AX_GRN": {
        "title": "AX_GRN",
        "kind": "data",
        "key": "ASN NO",
        "headers": AX_GRN_HEADERS,
    },
    "PENDING": {
        "title": "PENDING",
        "kind": "data",
        "key": "PENDING ID",
        "headers": PENDING_HEADERS,
    },
    "ASN_IMAGES": {
        "title": "ASN_IMAGES",
        "kind": "data",
        "key": "IMAGE ID",
        "headers": ASN_IMAGES_HEADERS,
    },
    "IMAGE_DATA": {
        "title": "IMAGE_DATA",
        "kind": "data",
        "key": None,
        "headers": IMAGE_DATA_HEADERS,
    },
    "RECON_LOG": {
        "title": "RECON_LOG",
        "kind": "log",
        "key": "RUN ID",
        "headers": RECON_LOG_HEADERS,
    },
    "EMAIL_LOG": {
        "title": "EMAIL_LOG",
        "kind": "log",
        "key": "EMAIL ID",
        "headers": EMAIL_LOG_HEADERS,
    },
    "USER-M": {
        "title": "USER-M",
        "kind": "master",
        "key": "USER ID",
        "headers": USER_HEADERS,
        "seed": DEFAULT_USERS,
    },
    "SETTINGS": {
        "title": "SETTINGS",
        "kind": "master",
        "key": "KEY",
        "headers": SETTINGS_HEADERS,
        "seed": DEFAULT_SETTINGS,
    },
}
