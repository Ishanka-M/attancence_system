"""
parsing.py
==========
Reads ASN and inventory files - Excel and PDF.

  * list_sheets()      -> sheet names in a workbook, for the user to pick
  * parse_asn()        -> ASN Excel sheet   -> canonical DataFrame
  * parse_inventory()  -> Korber inventory  -> canonical DataFrame
  * extract_images()   -> images embedded in an xlsx
  * list_pdf_tables()  -> tables found in a PDF, best ASN match first
  * parse_asn_pdf()    -> selected PDF table -> canonical DataFrame

An alias map keeps this working when column names differ, and the header
row is auto-detected within the first 25 rows.
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
    """Normalise a column header so it can be compared."""
    s = str(s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def clean(v) -> str:
    """Cell value -> clean string (nan/None become '')."""
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

# minimum canonical columns needed to confirm a header row
ASN_MUST = {"ASN_NO", "HU_ID", "QTY"}
INV_MUST = {"PALLET", "ACTUAL_QTY"}


# ═══════════════════════════════════════════════════════════════════
#  workbook helpers
# ═══════════════════════════════════════════════════════════════════
def list_sheets(file_bytes: bytes) -> list[str]:
    """Sheet names in the workbook, so the user can choose one."""
    try:
        return pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names
    except Exception:
        return []


def _detect_header(raw: pd.DataFrame, lookup: dict, must: set) -> tuple[int, dict]:
    """
    Find the header row within the first 25 rows.
    Returns (header_row_index, {col_index: canonical_name}).
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
    ASN Excel sheet -> canonical DataFrame.
    Returns (df, meta) where meta = {header_row, mapped, unmapped, sheet}.
    """
    raw = _read_raw(file_bytes, sheet_name)
    if raw.empty:
        return pd.DataFrame(columns=list(ASN_ALIASES)), {
            "header_row": None, "mapped": {}, "unmapped": [], "sheet": sheet_name,
            "error": "The sheet is empty.",
        }

    hrow, cmap = _detect_header(raw, ASN_LOOKUP, ASN_MUST)
    if hrow < 0 or not cmap:
        return pd.DataFrame(columns=list(ASN_ALIASES)), {
            "header_row": None, "mapped": {}, "unmapped": list(raw.iloc[0].tolist()),
            "sheet": sheet_name,
            "error": ("Could not identify the ASN columns - an ASN number, "
                      "HU and quantity column are required."),
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

    # tidy strings
    for c in df.columns:
        df[c] = df[c].map(clean)

    # tidy numeric columns
    for c in ("QTY", "S_QTY", "GROSS_WEIGHT", "NET_WEIGHT"):
        df[c] = df[c].map(lambda v: fmt_num(v) if clean(v) != "" else "")

    # drop completely blank rows
    df = df[(df["HU_ID"].str.strip() != "") | (df["ASN_NO"].str.strip() != "")]
    df = df.reset_index(drop=True)

    # generate line numbers when the document has none
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
        return pd.DataFrame(columns=list(INV_ALIASES)), {"error": "The sheet is empty."}

    hrow, cmap = _detect_header(raw, INV_LOOKUP, INV_MUST)
    if hrow < 0 or not cmap:
        return pd.DataFrame(columns=list(INV_ALIASES)), {
            "error": ("Could not identify the inventory columns - a pallet "
                      "and actual quantity column are required.")
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
#  Embedded images - an xlsx is a zip, images live under xl/media/
# ═══════════════════════════════════════════════════════════════════
_IMG_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
            ".tif": "image/tiff", ".tiff": "image/tiff", ".emf": "image/emf",
            ".wmf": "image/wmf"}


def extract_images(file_bytes: bytes) -> list[dict]:
    """
    Every image embedded in an .xlsx/.xlsm file. Handles both
    'Insert > Picture' and 'Insert image in cell'.
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
        pass                                   # older formats such as .xls
    except Exception:
        pass
    return out


# ═══════════════════════════════════════════════════════════════════
#  PDF - for ASN documents that arrive as PDF
# ═══════════════════════════════════════════════════════════════════
def pdf_available() -> bool:
    try:
        import pdfplumber  # noqa: F401
        return True
    except Exception:
        return False


def is_pdf(name: str, data: bytes = b"") -> bool:
    return str(name).lower().endswith(".pdf") or data[:5] == b"%PDF-"


def _table_to_raw(table: list[list]) -> pd.DataFrame:
    """pdfplumber table (list of rows) -> DataFrame without a header."""
    width = max((len(r) for r in table), default=0)
    rows = [list(r) + [""] * (width - len(r)) for r in table]
    return pd.DataFrame(rows, dtype=object)


def list_pdf_tables(file_bytes: bytes) -> list[dict]:
    """
    Find every table in the PDF and rank the ones that look like ASN data
    first, so the user can pick the right one.
    """
    if not pdf_available():
        return []
    import pdfplumber

    out = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for pi, page in enumerate(pdf.pages, start=1):
                try:
                    tables = page.extract_tables()
                except Exception:
                    tables = []
                for ti, tb in enumerate(tables):
                    if not tb or len(tb) < 2:
                        continue
                    raw = _table_to_raw(tb)
                    _, cmap = _detect_header(raw, ASN_LOOKUP, ASN_MUST)
                    score = len(cmap) + (10 if ASN_MUST.issubset(set(cmap.values())) else 0)
                    out.append({
                        "key": f"p{pi}t{ti}",
                        "label": (f"Page {pi} · Table {ti + 1} · "
                                  f"{len(raw)}×{raw.shape[1]} · "
                                  f"ASN columns {len(cmap)}"),
                        "page": pi,
                        "index": ti,
                        "rows": len(raw),
                        "cols": raw.shape[1],
                        "score": score,
                        "raw": raw,
                    })
    except Exception:
        return []

    out.sort(key=lambda d: (-d["score"], d["page"], d["index"]))
    return out


def parse_asn_pdf(file_bytes: bytes, table_keys: list[str] | None = None
                  ) -> tuple[pd.DataFrame, dict]:
    """
    Build a canonical ASN DataFrame from the selected PDF table(s).
    With no table_keys the best scoring table is used.
    """
    if not pdf_available():
        return pd.DataFrame(columns=list(ASN_ALIASES)), {
            "header_row": None, "mapped": {}, "unmapped": [], "sheet": "PDF",
            "error": "pdfplumber is not installed. Run `pip install pdfplumber`.",
        }

    tables = list_pdf_tables(file_bytes)
    if not tables:
        return pd.DataFrame(columns=list(ASN_ALIASES)), {
            "header_row": None, "mapped": {}, "unmapped": [], "sheet": "PDF",
            "error": ("No table was found in this PDF. A scanned PDF has no "
                      "text layer - upload an Excel file instead, or attach "
                      "this PDF to the ASN as a document."),
        }

    picked = [t for t in tables if t["key"] in (table_keys or [])] or [tables[0]]

    frames, mapped, unmapped, hdr = [], {}, [], None
    for t in picked:
        raw = t["raw"]
        hrow, cmap = _detect_header(raw, ASN_LOOKUP, ASN_MUST)
        if hrow < 0 or not cmap:
            continue
        hdr = hdr or f"{t['page']}/{hrow + 1}"
        header_vals = raw.iloc[hrow].tolist()
        body = raw.iloc[hrow + 1:].reset_index(drop=True)
        data = {canon: (body[j] if j in body.columns else "")
                for j, canon in cmap.items()}
        frames.append(pd.DataFrame(data))
        mapped.update({str(header_vals[j]): c for j, c in cmap.items()})
        unmapped += [str(h) for j, h in enumerate(header_vals)
                     if j not in cmap and clean(h) != ""]

    if not frames:
        return pd.DataFrame(columns=list(ASN_ALIASES)), {
            "header_row": None, "mapped": {}, "unmapped": [], "sheet": "PDF",
            "error": ("Could not identify ASN columns (ASN no / HU / quantity) "
                      "in the selected table."),
        }

    df = pd.concat(frames, ignore_index=True)
    for c in ASN_ALIASES:
        if c not in df.columns:
            df[c] = ""
    df = df[list(ASN_ALIASES)]
    for c in df.columns:
        df[c] = df[c].map(clean)
    for c in ("QTY", "S_QTY", "GROSS_WEIGHT", "NET_WEIGHT"):
        df[c] = df[c].map(lambda v: fmt_num(v) if clean(v) != "" else "")

    df = df[(df["HU_ID"].str.strip() != "") | (df["ASN_NO"].str.strip() != "")]
    df = df.reset_index(drop=True)

    # drop repeated header rows that appear in multi-page tables
    df = df[df["HU_ID"].str.upper().str.strip() != "HU_ID"].reset_index(drop=True)

    if (df["ASN_LINE"].str.strip() == "").all():
        df["ASN_LINE"] = [str(i + 1) for i in range(len(df))]

    meta = {
        "header_row": hdr,
        "mapped": mapped,
        "unmapped": sorted(set(unmapped)),
        "sheet": " + ".join(t["label"].split(" · ")[0] + "/" + str(t["index"] + 1)
                            for t in picked),
        "rows": len(df),
        "error": None,
    }
    return df, meta


def extract_pdf_images(file_bytes: bytes, max_images: int = 20) -> list[dict]:
    """Images embedded in the PDF, or an empty list."""
    out = []
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        for pi, page in enumerate(reader.pages, start=1):
            for im in getattr(page, "images", []):
                data = im.data
                if not data or len(data) < 4096:      # skip icons and logos
                    continue
                nm = getattr(im, "name", f"p{pi}_img")
                ext = "." + nm.rsplit(".", 1)[-1].lower() if "." in nm else ".png"
                out.append({
                    "name": f"p{pi}_{nm}",
                    "data": data,
                    "mime": _IMG_EXT.get(ext, "image/png"),
                    "size_kb": round(len(data) / 1024, 1),
                })
                if len(out) >= max_images:
                    return out
    except Exception:
        pass
    return out


def pdf_page_count(file_bytes: bytes) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(file_bytes)).pages)
    except Exception:
        return 0
