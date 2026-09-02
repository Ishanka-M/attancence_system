"""
parsing.py
==========
Excel files කියවන කොටස.

  * list_sheets()          -> workbook එකේ sheet නම් (user ට තෝරන්න)
  * parse_asn()            -> ASN Excel එකක් -> canonical DataFrame
  * parse_inventory()      -> Korber Inventory Excel -> canonical DataFrame
  * extract_images()       -> xlsx එක ඇතුළේ embed වෙලා තියෙන images ඔක්කොම

Column නම් වෙනස් වුණත් වැඩ කරන්න ALIAS map එකක් තියෙනවා, header row එක
මුල් rows 25ක් ඇතුළේ auto-detect කරනවා.
"""
from __future__ import annotations

import io
import re
import zipfile

import pandas as pd

# ═══════════════════════════════════════════════════════════════════
#  helpers
# ═══════════════════════════════════════════════════════════════════
def norm_key(s) -> str:
    """Column header එකක් compare කරන්න පුළුවන් form එකකට."""
    s = str(s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def clean(v) -> str:
    """Cell value එකක් -> පිරිසිදු string (nan/None -> '')."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("nan", "nat", "none", "null", "#n/a"):
        return ""
    # 1.0 -> 1  (Excel float artefact)
    if re.fullmatch(r"-?\d+\.0", s):
        s = s[:-2]
    return s


def to_num(v, default=0.0) -> float:
    s = clean(v).replace(",", "")
    if s == "":
        return default
    try:
        return float(s)
    except Exception:
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group()) if m else default


def fmt_num(v) -> str:
    """3.0 -> '3',  3.25 -> '3.25'"""
    f = to_num(v)
    return str(int(f)) if float(f).is_integer() else f"{f:g}"


# ═══════════════════════════════════════════════════════════════════
#  ALIAS maps
# ═══════════════════════════════════════════════════════════════════
ASN_ALIASES: dict[str, list[str]] = {
    "ASN_NO": ["display_asn_number", "asn_number", "asn_no", "asn", "shipment_no",
               "asn_num", "advance_shipment_no"],
    "ASN_LINE": ["asn_line_number", "asn_line", "line_number", "line_no", "line", "seq"],
    "CLIENT_CODE": ["client_code", "client", "customer_code", "cust_code"],
    "ITEM_NUMBER": ["display_item_number", "item_number", "item_no", "item", "sku",
                    "material", "material_code", "item_code", "part_no"],
    "HU_ID": ["hu_id", "huid", "hu", "pallet", "pallet_id", "pallet_no", "lpn",
              "handling_unit", "carton_no"],
    "SUPPLIER_HU": ["supplier_hu", "vendor_hu", "supplier_hu_id", "supplier_carton"],
    "LOT_NUMBER": ["lot_number", "lot_no", "lot", "batch", "batch_no", "batch_number"],
    "QTY": ["quantity", "qty", "asn_qty", "expected_qty", "ship_qty", "pack_qty"],
    "UOM": ["uom", "unit", "unit_of_measure"],
    "S_UOM": ["s_uom", "suom", "secondary_uom", "sec_uom"],
    "S_QTY": ["s_qty", "sqty", "secondary_qty", "sec_qty"],
    "PO_NUMBER": ["display_po_number", "po_number", "po_no", "po", "purchase_order"],
    "PO_LINE": ["po_line_number", "po_line", "po_line_no"],
    "PACKAGE_TYPE": ["package_type", "pack_type", "packaging"],
    "VENDOR_CODE": ["vendor_code", "supplier_code", "vendor", "supplier_no"],
    "GROSS_WEIGHT": ["gross_weight", "gw", "gross_wt"],
    "NET_WEIGHT": ["net_weight", "nw", "net_wt"],
    "COLOR": ["color", "colour"],
    "TYPE_QC": ["type_qc", "qc_type", "qc"],
    "SUPPLIER_DESC": ["supplier_desc", "supplier_description", "description",
                      "item_description", "desc", "item_desc"],
}

INV_ALIASES: dict[str, list[str]] = {
    "WH_ID": ["wh_id", "warehouse_id", "warehouse", "wh"],
    "CLIENT_CODE": ["client_code", "client"],
    "PALLET": ["pallet", "pallet_id", "hu_id", "huid", "lpn", "handling_unit"],
    "LOCATION_ID": ["location_id", "location", "loc", "bin", "bin_location"],
    "ITEM_NUMBER": ["item_number", "item_no", "item_code"],
    "DISPLAY_ITEM_NUMBER": ["display_item_number", "disp_item_number", "display_item"],
    "DESCRIPTION": ["description", "item_description"],
    "LOT_NUMBER": ["lot_number", "lot_no", "lot", "batch", "batch_no"],
    "ACTUAL_QTY": ["actual_qty", "actual_quantity", "qty", "quantity", "on_hand_qty"],
    "UNAVAILABLE_QTY": ["unavailable_qty", "unavailable_quantity", "blocked_qty"],
    "UOM": ["uom", "unit"],
    "STATUS": ["status", "inventory_status"],
    "GRN_NUMBER": ["grn_number", "grn_no", "grn"],
    "ASN_NUMBER": ["asn_number", "asn_no", "asn", "display_asn_number"],
    "ASN_LINE_NUMBER": ["asn_line_number", "asn_line", "asn_line_no"],
    "SUPPLIER_HU": ["supplier_hu", "vendor_hu"],
    "PO_NUMBER": ["po_number", "po_no", "po"],
    "INVOICE_NUMBER": ["invoice_number", "invoice_no", "invoice"],
    "VENDOR_NAME": ["vendor_name", "supplier_name", "vendor"],
    "INVENTORY_TYPE": ["inventory_type", "inv_type"],
    "SUPPLIER_DESC": ["supplier_desc", "supplier_description"],
    "S_UOM": ["s_uom", "suom"],
    "S_QTY": ["s_qty", "sqty"],
}


def _lookup(aliases: dict[str, list[str]]) -> dict[str, str]:
    """alias -> canonical (exact normalized match)."""
    out = {}
    for canon, alist in aliases.items():
        out.setdefault(norm_key(canon), canon)
        for a in alist:
            out.setdefault(norm_key(a), canon)
    return out


ASN_LOOKUP = _lookup(ASN_ALIASES)
INV_LOOKUP = _lookup(INV_ALIASES)

# header row detect කරන්න අවශ්‍ය අවම tokens
ASN_MUST = {"ASN_NO", "HU_ID", "QTY"}
INV_MUST = {"PALLET", "ACTUAL_QTY"}


# ═══════════════════════════════════════════════════════════════════
#  workbook helpers
# ═══════════════════════════════════════════════════════════════════
def list_sheets(file_bytes: bytes) -> list[str]:
    """Workbook එකේ තියෙන sheet නම් — user ට 'මොන sheet එකද?' අහන්න."""
    try:
        return pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names
    except Exception:
        return []


def _detect_header(raw: pd.DataFrame, lookup: dict, must: set) -> tuple[int, dict]:
    """
    මුල් rows 25ක් ඇතුළේ header row එක හොයනවා.
    return: (header_row_index, {col_index: canonical_name})
    """
    best_i, best_map, best_score = -1, {}, 0
    limit = min(25, len(raw))
    for i in range(limit):
        row = raw.iloc[i].tolist()
        cmap = {}
        for j, cell in enumerate(row):
            canon = lookup.get(norm_key(cell))
            if canon and canon not in cmap.values():
                cmap[j] = canon
        score = len(cmap) + (5 if must.issubset(set(cmap.values())) else 0)
        if score > best_score:
            best_i, best_map, best_score = i, cmap, score
    return best_i, best_map


def _read_raw(file_bytes: bytes, sheet_name) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet_name,
                         header=None, dtype=object)


# ═══════════════════════════════════════════════════════════════════
#  ASN parsing
# ═══════════════════════════════════════════════════════════════════
def parse_asn(file_bytes: bytes, sheet_name) -> tuple[pd.DataFrame, dict]:
    """
    ASN Excel sheet එකක් -> canonical DataFrame.
    return: (df, meta)   meta = {header_row, mapped, unmapped, sheet}
    """
    raw = _read_raw(file_bytes, sheet_name)
    if raw.empty:
        return pd.DataFrame(columns=list(ASN_ALIASES)), {
            "header_row": None, "mapped": {}, "unmapped": [], "sheet": sheet_name,
            "error": "Sheet එක හිස්.",
        }

    hrow, cmap = _detect_header(raw, ASN_LOOKUP, ASN_MUST)
    if hrow < 0 or not cmap:
        return pd.DataFrame(columns=list(ASN_ALIASES)), {
            "header_row": None, "mapped": {}, "unmapped": list(raw.iloc[0].tolist()),
            "sheet": sheet_name,
            "error": "ASN columns හඳුනාගන්න බැරි උනා. (ASN number / HU / Qty columns ඕනේ)",
        }

    header_vals = raw.iloc[hrow].tolist()
    body = raw.iloc[hrow + 1:].reset_index(drop=True)

    data = {}
    for j, canon in cmap.items():
        data[canon] = body[j] if j in body.columns else ""
    df = pd.DataFrame(data)

    for c in ASN_ALIASES:
        if c not in df.columns:
            df[c] = ""
    df = df[list(ASN_ALIASES)]

    # string clean
    for c in df.columns:
        df[c] = df[c].map(clean)

    # numeric columns tidy
    for c in ("QTY", "S_QTY", "GROSS_WEIGHT", "NET_WEIGHT"):
        df[c] = df[c].map(lambda v: fmt_num(v) if clean(v) != "" else "")

    # සම්පූර්ණයෙන් හිස් rows අයින්
    df = df[(df["HU_ID"].str.strip() != "") | (df["ASN_NO"].str.strip() != "")]
    df = df.reset_index(drop=True)

    # ASN line number නැත්නම් auto
    if (df["ASN_LINE"].str.strip() == "").all():
        df["ASN_LINE"] = [str(i + 1) for i in range(len(df))]

    mapped = {str(header_vals[j]): canon for j, canon in cmap.items()}
    unmapped = [str(h) for j, h in enumerate(header_vals)
                if j not in cmap and clean(h) != ""]

    meta = {
        "header_row": hrow + 1,
        "mapped": mapped,
        "unmapped": unmapped,
        "sheet": sheet_name,
        "rows": len(df),
        "error": None,
    }
    return df, meta


# ═══════════════════════════════════════════════════════════════════
#  Inventory parsing
# ═══════════════════════════════════════════════════════════════════
def parse_inventory(file_bytes: bytes, sheet_name) -> tuple[pd.DataFrame, dict]:
    raw = _read_raw(file_bytes, sheet_name)
    if raw.empty:
        return pd.DataFrame(columns=list(INV_ALIASES)), {"error": "Sheet එක හිස්."}

    hrow, cmap = _detect_header(raw, INV_LOOKUP, INV_MUST)
    if hrow < 0 or not cmap:
        return pd.DataFrame(columns=list(INV_ALIASES)), {
            "error": "Inventory columns හඳුනාගන්න බැරි උනා. (Pallet / Actual Qty ඕනේ)"
        }

    header_vals = raw.iloc[hrow].tolist()
    body = raw.iloc[hrow + 1:].reset_index(drop=True)

    data = {}
    for j, canon in cmap.items():
        data[canon] = body[j] if j in body.columns else ""
    df = pd.DataFrame(data)
    for c in INV_ALIASES:
        if c not in df.columns:
            df[c] = ""
    df = df[list(INV_ALIASES)]
    for c in df.columns:
        df[c] = df[c].map(clean)
    for c in ("ACTUAL_QTY", "UNAVAILABLE_QTY", "S_QTY"):
        df[c] = df[c].map(lambda v: fmt_num(v) if clean(v) != "" else "")

    df = df[df["PALLET"].str.strip() != ""].reset_index(drop=True)

    meta = {
        "header_row": hrow + 1,
        "rows": len(df),
        "mapped": {str(header_vals[j]): c for j, c in cmap.items()},
        "error": None,
    }
    return df, meta


# ═══════════════════════════════════════════════════════════════════
#  Embedded images  (xlsx = zip; xl/media/* ඇතුළේ images)
# ═══════════════════════════════════════════════════════════════════
_IMG_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
            ".tif": "image/tiff", ".tiff": "image/tiff", ".emf": "image/emf",
            ".wmf": "image/wmf"}


def extract_images(file_bytes: bytes) -> list[dict]:
    """
    Excel (.xlsx/.xlsm) එකක් ඇතුළේ embed වෙච්ච හැම image එකක්ම ගන්නවා.
    Normal 'Insert > Picture' සහ 'Insert image in cell' දෙකම support.
    return: [{name, data(bytes), mime, size_kb}]
    """
    out = []
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            for name in z.namelist():
                low = name.lower()
                if not (low.startswith("xl/media/") or "/media/" in low):
                    continue
                ext = "." + low.rsplit(".", 1)[-1] if "." in low else ""
                if ext not in _IMG_EXT:
                    continue
                data = z.read(name)
                if not data:
                    continue
                out.append({
                    "name": name.split("/")[-1],
                    "data": data,
                    "mime": _IMG_EXT[ext],
                    "size_kb": round(len(data) / 1024, 1),
                })
    except zipfile.BadZipFile:
        pass                                   # .xls වගේ පරණ format
    except Exception:
        pass
    return out
