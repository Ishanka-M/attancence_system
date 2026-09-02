"""
matching.py
===========
ASN to Korber inventory reconciliation engine.

Logic:
  * primary key = HU ID  (ASN: HU_ID  |  Inventory: Pallet)
  * HU not in inventory          -> MISSING IN INVENTORY (GRN not done yet)
  * HU found                     -> compare Qty / Item / Lot / ASN No
      - everything agrees        -> MATCHED -> KORBER GRN = DONE
      - a difference             -> QTY / ITEM / LOT / WRONG ASN mismatch
  * HU in inventory under the same ASN but absent from the ASN document
                                 -> EXTRA IN INVENTORY

When every line of an ASN is MATCHED the ASN becomes KORBER GRN DONE.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

import schema
from parsing import clean, to_num, fmt_num


# ───────────────────────── normalisers ─────────────────────────
def nkey(v) -> str:
    """Normalise a value for comparison: trim, uppercase, squash inner spaces."""
    return " ".join(str(v or "").strip().upper().split())


def strip_prefix(v, client: str, enabled: bool = True) -> str:
    """'HIES-26AUG_UPPD_40659' -> '26AUG_UPPD_40659'"""
    s = nkey(v)
    if not enabled or not client:
        return s
    p = nkey(client) + "-"
    return s[len(p):] if s.startswith(p) else s


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_id() -> str:
    return "RUN-" + datetime.now().strftime("%y%m%d-%H%M%S")


# ───────────────────────── inventory index ─────────────────────────
def build_inventory_index(inv: pd.DataFrame, cfg: dict) -> dict:
    """
    Index the inventory by pallet (HU). When one pallet has several rows the
    quantities are summed and the metadata is merged.
    """
    client = cfg.get("client", "")
    strip = cfg.get("strip_prefix", True)
    idx: dict[str, dict] = {}

    if inv is None or inv.empty:
        return idx

    for _, r in inv.iterrows():
        hu = nkey(r.get("PALLET"))
        if not hu:
            continue
        item = nkey(r.get("DISPLAY_ITEM_NUMBER")) or strip_prefix(r.get("ITEM_NUMBER"), client, strip)
        rec = idx.get(hu)
        if rec is None:
            idx[hu] = {
                "hu": hu,
                "qty": to_num(r.get("ACTUAL_QTY")),
                "items": {item} if item else set(),
                "lots": {nkey(r.get("LOT_NUMBER"))} if nkey(r.get("LOT_NUMBER")) else set(),
                "locations": {clean(r.get("LOCATION_ID"))} if clean(r.get("LOCATION_ID")) else set(),
                "grns": {clean(r.get("GRN_NUMBER"))} if clean(r.get("GRN_NUMBER")) else set(),
                "asn": strip_prefix(r.get("ASN_NUMBER"), client, strip),
                "asn_raw": clean(r.get("ASN_NUMBER")),
                "asn_line": clean(r.get("ASN_LINE_NUMBER")),
                "uom": clean(r.get("UOM")),
                "status": clean(r.get("STATUS")),
                "rows": 1,
            }
        else:
            rec["qty"] += to_num(r.get("ACTUAL_QTY"))
            if item:
                rec["items"].add(item)
            if nkey(r.get("LOT_NUMBER")):
                rec["lots"].add(nkey(r.get("LOT_NUMBER")))
            if clean(r.get("LOCATION_ID")):
                rec["locations"].add(clean(r.get("LOCATION_ID")))
            if clean(r.get("GRN_NUMBER")):
                rec["grns"].add(clean(r.get("GRN_NUMBER")))
            rec["rows"] += 1
    return idx


def inventory_by_asn(inv: pd.DataFrame, cfg: dict) -> dict[str, set]:
    """Normalised ASN number -> set of HUs, used to detect extra HUs."""
    client = cfg.get("client", "")
    strip = cfg.get("strip_prefix", True)
    out: dict[str, set] = {}
    if inv is None or inv.empty:
        return out
    for _, r in inv.iterrows():
        a = strip_prefix(r.get("ASN_NUMBER"), client, strip)
        hu = nkey(r.get("PALLET"))
        if a and hu:
            out.setdefault(a, set()).add(hu)
    return out


# ───────────────────────── line level ─────────────────────────
def _compare_line(line: dict, inv_rec: dict | None, cfg: dict) -> dict:
    """Compare one ASN line against its inventory record."""
    tol = cfg.get("qty_tolerance", 0.0)
    client = cfg.get("client", "")
    strip = cfg.get("strip_prefix", True)

    asn_qty = to_num(line.get("QTY"))
    res = {
        "MATCH STATUS": schema.M_MISSING,
        "INV QTY": "",
        "QTY DIFF": "",
        "INV ITEM": "",
        "INV LOT": "",
        "INV LOCATION": "",
        "INV ASN NO": "",
        "INV GRN NO": "",
        "DISCREPANCY": "",
        "KORBER GRN": schema.K_PENDING,
    }

    if inv_rec is None:
        res["DISCREPANCY"] = "HU not found in Korber inventory — GRN not yet done"
        res["QTY DIFF"] = fmt_num(-asn_qty)
        return res

    inv_qty = inv_rec["qty"]
    res["INV QTY"] = fmt_num(inv_qty)
    res["QTY DIFF"] = fmt_num(inv_qty - asn_qty)
    res["INV ITEM"] = " | ".join(sorted(inv_rec["items"]))
    res["INV LOT"] = " | ".join(sorted(inv_rec["lots"]))
    res["INV LOCATION"] = " | ".join(sorted(inv_rec["locations"]))
    res["INV ASN NO"] = inv_rec["asn_raw"]
    res["INV GRN NO"] = " | ".join(sorted(inv_rec["grns"]))

    issues, kinds = [], []

    if abs(inv_qty - asn_qty) > tol:
        kinds.append(schema.M_QTY)
        issues.append(f"Qty: ASN {fmt_num(asn_qty)} ≠ INV {fmt_num(inv_qty)} "
                      f"(diff {fmt_num(inv_qty - asn_qty)})")

    if cfg.get("check_item", True):
        a_item = strip_prefix(line.get("ITEM_NUMBER"), client, strip)
        if a_item and inv_rec["items"] and a_item not in inv_rec["items"]:
            kinds.append(schema.M_ITEM)
            issues.append(f"Item: ASN {a_item} ≠ INV {'/'.join(sorted(inv_rec['items']))}")

    if cfg.get("check_lot", True):
        a_lot = nkey(line.get("LOT_NUMBER"))
        if a_lot and inv_rec["lots"] and a_lot not in inv_rec["lots"]:
            kinds.append(schema.M_LOT)
            issues.append(f"Lot: ASN {a_lot} ≠ INV {'/'.join(sorted(inv_rec['lots']))}")

    if cfg.get("check_asn", True):
        a_asn = strip_prefix(line.get("ASN_NO"), client, strip)
        if a_asn and inv_rec["asn"] and a_asn != inv_rec["asn"]:
            kinds.append(schema.M_ASN)
            issues.append(f"ASN: doc {a_asn} ≠ INV {inv_rec['asn_raw']}")

    if kinds:
        res["MATCH STATUS"] = kinds[0] if len(kinds) == 1 else " + ".join(kinds)
        res["DISCREPANCY"] = " ; ".join(issues)
        res["KORBER GRN"] = schema.K_PENDING
    else:
        res["MATCH STATUS"] = schema.M_MATCHED
        res["DISCREPANCY"] = ""
        res["KORBER GRN"] = schema.K_DONE           # tallied -> Korber GRN done

    return res


# ───────────────────────── main reconcile ─────────────────────────
def reconcile(detail: pd.DataFrame, inv: pd.DataFrame, cfg: dict,
              rid: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    detail : ASN lines in ASN_DETAIL format
    inv    : canonical inventory DataFrame
    returns: (updated_detail, extra_rows_df, stats)
    """
    rid = rid or run_id()
    ts = now_str()
    idx = build_inventory_index(inv, cfg)
    by_asn = inventory_by_asn(inv, cfg)

    out = detail.copy().reset_index(drop=True)
    for col in ("MATCH STATUS", "INV QTY", "QTY DIFF", "INV ITEM", "INV LOT",
                "INV LOCATION", "INV ASN NO", "INV GRN NO", "DISCREPANCY",
                "KORBER GRN", "LAST RECON"):
        if col not in out.columns:
            out[col] = ""

    seen_hu: dict[str, set] = {}
    asn_display: dict[str, str] = {}       # normalised ASN -> name used in the document

    for i, row in out.iterrows():
        line = {
            "QTY": row.get("QTY"),
            "ITEM_NUMBER": row.get("ITEM NUMBER"),
            "LOT_NUMBER": row.get("LOT NUMBER"),
            "ASN_NO": row.get("ASN NO"),
        }
        hu = nkey(row.get("HU ID"))
        res = _compare_line(line, idx.get(hu), cfg)
        for k, v in res.items():
            out.at[i, k] = v
        out.at[i, "LAST RECON"] = ts

        a = strip_prefix(row.get("ASN NO"), cfg.get("client", ""), cfg.get("strip_prefix", True))
        seen_hu.setdefault(a, set()).add(hu)
        asn_display.setdefault(a, clean(row.get("ASN NO")) or a)

    # ── EXTRA: HUs in inventory that the ASN document does not list ──
    extra_rows = []
    if cfg.get("flag_extra", True):
        for a, hus in seen_hu.items():
            disp = asn_display.get(a, a)
            for hu in sorted(by_asn.get(a, set()) - hus):
                rec = idx.get(hu, {})
                extra_rows.append({
                    "LINE UID": f"{nkey(disp)}|{hu}",
                    "ASN NO": disp,
                    "ASN LINE": rec.get("asn_line", ""),
                    "HU ID": hu,
                    "ITEM NUMBER": " | ".join(sorted(rec.get("items", []))),
                    "LOT NUMBER": " | ".join(sorted(rec.get("lots", []))),
                    "QTY": "0",
                    "UOM": rec.get("uom", ""),
                    "MATCH STATUS": schema.M_EXTRA,
                    "INV QTY": fmt_num(rec.get("qty", 0)),
                    "QTY DIFF": fmt_num(rec.get("qty", 0)),
                    "INV ITEM": " | ".join(sorted(rec.get("items", []))),
                    "INV LOT": " | ".join(sorted(rec.get("lots", []))),
                    "INV LOCATION": " | ".join(sorted(rec.get("locations", []))),
                    "INV ASN NO": rec.get("asn_raw", ""),
                    "INV GRN NO": " | ".join(sorted(rec.get("grns", []))),
                    "DISCREPANCY": "HU exists in inventory but is not on the ASN document",
                    "KORBER GRN": schema.K_PENDING,
                    "AX GRN": schema.AX_NA,
                    "LAST RECON": ts,
                })
    extra_df = pd.DataFrame(extra_rows, columns=schema.ASN_DETAIL_HEADERS) \
        if extra_rows else pd.DataFrame(columns=schema.ASN_DETAIL_HEADERS)

    st = out["MATCH STATUS"].astype(str)
    stats = {
        "run_id": rid,
        "run_at": ts,
        "lines": len(out),
        "matched": int((st == schema.M_MATCHED).sum()),
        "missing": int((st == schema.M_MISSING).sum()),
        "mismatch": int((~st.isin([schema.M_MATCHED, schema.M_MISSING])).sum()),
        "extra": len(extra_df),
        "inventory_rows": 0 if inv is None else len(inv),
    }
    return out, extra_df, stats


# ───────────────────────── ASN level summary ─────────────────────────
def asn_status(matched: int, missing: int, mismatch: int, extra: int,
               total: int) -> str:
    if total == 0:
        return schema.S_NEW
    if mismatch > 0 or extra > 0:
        return schema.S_DISCREPANCY
    if missing == 0 and matched == total:
        return schema.S_KORBER_DONE
    if matched > 0:
        return schema.S_PARTIAL
    return schema.S_GRN_PENDING


def summarise_asn(detail: pd.DataFrame, extra: pd.DataFrame | None = None) -> pd.DataFrame:
    """Roll ASN_DETAIL rows up into one summary row per ASN."""
    if detail is None or detail.empty:
        return pd.DataFrame(columns=schema.ASN_SUMMARY_HEADERS)

    ex_count = {}
    if extra is not None and not extra.empty:
        ex_count = extra.groupby(extra["ASN NO"].astype(str)).size().to_dict()

    rows = []
    for asn, g in detail.groupby(detail["ASN NO"].astype(str), sort=True):
        st = g["MATCH STATUS"].astype(str)
        matched = int((st == schema.M_MATCHED).sum())
        missing = int((st == schema.M_MISSING).sum())
        checked = int((st.str.strip() != "").sum())
        mismatch = checked - matched - missing
        extra_n = int(ex_count.get(asn, 0))
        total = len(g)

        asn_qty = g["QTY"].map(to_num).sum()
        inv_qty = g["INV QTY"].map(lambda v: to_num(v) if clean(v) != "" else 0).sum()
        matched_qty = g.loc[st == schema.M_MATCHED, "QTY"].map(to_num).sum()

        status = schema.S_NEW if checked == 0 else asn_status(matched, missing, mismatch, extra_n, total)

        rows.append({
            "ASN NO": asn,
            "CLIENT CODE": clean(g["CLIENT CODE"].iloc[0]) if "CLIENT CODE" in g else "",
            "PO NUMBER": " | ".join(sorted({clean(x) for x in g.get("PO NUMBER", []) if clean(x)})[:3]),
            "VENDOR CODE": " | ".join(sorted({clean(x) for x in g.get("VENDOR CODE", []) if clean(x)})[:3]),
            "SUPPLIER DESC": clean(g["SUPPLIER DESC"].iloc[0]) if "SUPPLIER DESC" in g else "",
            "UPLOAD DATE": clean(g["UPLOAD DATE"].iloc[0]) if "UPLOAD DATE" in g else "",
            "UPLOADED BY": clean(g["UPLOADED BY"].iloc[0]) if "UPLOADED BY" in g else "",
            "SOURCE FILE": clean(g["SOURCE FILE"].iloc[0]) if "SOURCE FILE" in g else "",
            "SOURCE SHEET": clean(g["SOURCE SHEET"].iloc[0]) if "SOURCE SHEET" in g else "",
            "TOTAL LINES": total,
            "TOTAL HU": g["HU ID"].astype(str).str.strip().nunique(),
            "TOTAL QTY": fmt_num(asn_qty),
            "ITEM COUNT": g["ITEM NUMBER"].astype(str).str.strip().nunique(),
            "MATCHED LINES": matched,
            "MISSING LINES": missing,
            "MISMATCH LINES": mismatch,
            "EXTRA LINES": extra_n,
            "MATCHED QTY": fmt_num(matched_qty),
            "RECEIVED QTY": fmt_num(inv_qty),
            "QTY DIFF": fmt_num(inv_qty - asn_qty),
            "STATUS": status,
            "KORBER GRN": schema.K_DONE if status == schema.S_KORBER_DONE else schema.K_PENDING,
            "KORBER GRN NO": " | ".join(sorted({clean(x) for x in g.get("INV GRN NO", []) if clean(x)})[:3]),
            "LAST RECON": clean(g["LAST RECON"].iloc[0]) if "LAST RECON" in g else "",
        })
    return pd.DataFrame(rows)


# ───────────────────────── discrepancy extraction ─────────────────────────
def disc_id(asn, hu, seq=0) -> str:
    """Stable discrepancy id built from ASN number and HU id."""
    a, h = nkey(asn), nkey(hu)
    return f"{a}|{h}" if (a or h) else f"LINE-{seq + 1:04d}"


SEVERITY = {
    schema.M_MISSING: "HIGH",
    schema.M_QTY: "HIGH",
    schema.M_ITEM: "HIGH",
    schema.M_ASN: "HIGH",
    schema.M_EXTRA: "MEDIUM",
    schema.M_LOT: "MEDIUM",
}


def discrepancy_rows(detail: pd.DataFrame, extra: pd.DataFrame | None,
                     rid: str) -> pd.DataFrame:
    """Every line that did not tally, in DISCREPANCY sheet format."""
    frames = []
    if detail is not None and not detail.empty:
        bad = detail[~detail["MATCH STATUS"].astype(str).isin([schema.M_MATCHED, ""])]
        frames.append(bad)
    if extra is not None and not extra.empty:
        frames.append(extra)
    if not frames:
        return pd.DataFrame(columns=schema.DISCREPANCY_HEADERS)

    allbad = pd.concat(frames, ignore_index=True)
    ts = now_str()
    rows = []
    for i, r in allbad.iterrows():
        stt = str(r.get("MATCH STATUS", ""))
        sev = SEVERITY.get(stt, "HIGH" if "MISMATCH" in stt else "MEDIUM")
        # Deterministic id: the same ASN + HU always maps to the same record,
        # so re-running reconciliation updates it instead of adding a duplicate.
        rows.append({
            "DISC ID": disc_id(r.get("ASN NO"), r.get("HU ID"), i),
            "RUN ID": rid,
            "GENERATED AT": ts,
            "ASN NO": clean(r.get("ASN NO")),
            "ASN LINE": clean(r.get("ASN LINE")),
            "HU ID": clean(r.get("HU ID")),
            "ITEM NUMBER": clean(r.get("ITEM NUMBER")),
            "LOT NUMBER": clean(r.get("LOT NUMBER")),
            "ASN QTY": clean(r.get("QTY")),
            "INV QTY": clean(r.get("INV QTY")),
            "QTY DIFF": clean(r.get("QTY DIFF")),
            "DISCREPANCY TYPE": stt,
            "DETAIL": clean(r.get("DISCREPANCY")),
            "SEVERITY": sev,
            "STATUS": "OPEN",
            "ACTION BY": "",
            "CLOSED AT": "",
            "NOTE": "",
        })
    return pd.DataFrame(rows, columns=schema.DISCREPANCY_HEADERS)
