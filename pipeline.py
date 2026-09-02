"""
pipeline.py
===========
Everything that happens automatically the moment an inventory file is
uploaded. No further user input is required.

    merge_inventory()  replace rows matching Invoice Number + Pallet,
                       append the rest
    auto_reconcile()   reconcile every open ASN, mark tallied lines as
                       Korber GRN Done, push those ASNs to AX GRN Pending,
                       auto-resolve discrepancies that now agree, raise new
                       ones, and generate the mismatch email

Kept out of app.py so the whole flow can be tested on its own.
"""
from __future__ import annotations

import uuid

import pandas as pd

import gsheets
import matching
import reporting
import schema
from matching import nkey, now_str, run_id
from parsing import clean, fmt_num, to_num

# Inventory rows are identified by invoice number + pallet. When the invoice
# number is blank the pallet alone identifies the row.
INV_KEY = ["INVOICE NUMBER", "PALLET"]
INV_KEY_FALLBACK = ["PALLET"]


# ═══════════════════════════════════════════════════════════════════
#  inventory
# ═══════════════════════════════════════════════════════════════════
def to_sheet_rows(inv: pd.DataFrame, snapshot: str = "") -> pd.DataFrame:
    """Canonical inventory DataFrame -> INVENTORY sheet columns."""
    ts = snapshot or now_str()
    return pd.DataFrame({
        "SNAPSHOT AT": ts,
        "WH ID": inv["WH_ID"], "CLIENT CODE": inv["CLIENT_CODE"],
        "PALLET": inv["PALLET"], "LOCATION ID": inv["LOCATION_ID"],
        "ITEM NUMBER": inv["ITEM_NUMBER"],
        "DISPLAY ITEM NUMBER": inv["DISPLAY_ITEM_NUMBER"],
        "DESCRIPTION": inv["DESCRIPTION"], "LOT NUMBER": inv["LOT_NUMBER"],
        "ACTUAL QTY": inv["ACTUAL_QTY"], "UNAVAILABLE QTY": inv["UNAVAILABLE_QTY"],
        "UOM": inv["UOM"], "STATUS": inv["STATUS"],
        "GRN NUMBER": inv["GRN_NUMBER"], "ASN NUMBER": inv["ASN_NUMBER"],
        "ASN LINE NUMBER": inv["ASN_LINE_NUMBER"], "SUPPLIER HU": inv["SUPPLIER_HU"],
        "PO NUMBER": inv["PO_NUMBER"], "INVOICE NUMBER": inv["INVOICE_NUMBER"],
        "VENDOR NAME": inv["VENDOR_NAME"], "INVENTORY TYPE": inv["INVENTORY_TYPE"],
        "SUPPLIER DESC": inv["SUPPLIER_DESC"], "S UOM": inv["S_UOM"],
        "S QTY": inv["S_QTY"],
    })


def from_sheet_rows(raw: pd.DataFrame) -> pd.DataFrame:
    """INVENTORY sheet columns -> canonical DataFrame the matcher expects."""
    if raw.empty:
        return pd.DataFrame(columns=[
            "WH_ID", "CLIENT_CODE", "PALLET", "LOCATION_ID", "ITEM_NUMBER",
            "DISPLAY_ITEM_NUMBER", "DESCRIPTION", "LOT_NUMBER", "ACTUAL_QTY",
            "UNAVAILABLE_QTY", "UOM", "STATUS", "GRN_NUMBER", "ASN_NUMBER",
            "ASN_LINE_NUMBER", "SUPPLIER_HU", "PO_NUMBER", "INVOICE_NUMBER",
            "VENDOR_NAME", "INVENTORY_TYPE", "SUPPLIER_DESC", "S_UOM", "S_QTY"])
    return pd.DataFrame({
        "WH_ID": raw["WH ID"], "CLIENT_CODE": raw["CLIENT CODE"],
        "PALLET": raw["PALLET"], "LOCATION_ID": raw["LOCATION ID"],
        "ITEM_NUMBER": raw["ITEM NUMBER"],
        "DISPLAY_ITEM_NUMBER": raw["DISPLAY ITEM NUMBER"],
        "DESCRIPTION": raw["DESCRIPTION"], "LOT_NUMBER": raw["LOT NUMBER"],
        "ACTUAL_QTY": raw["ACTUAL QTY"], "UNAVAILABLE_QTY": raw["UNAVAILABLE QTY"],
        "UOM": raw["UOM"], "STATUS": raw["STATUS"],
        "GRN_NUMBER": raw["GRN NUMBER"], "ASN_NUMBER": raw["ASN NUMBER"],
        "ASN_LINE_NUMBER": raw["ASN LINE NUMBER"], "SUPPLIER_HU": raw["SUPPLIER HU"],
        "PO_NUMBER": raw["PO NUMBER"], "INVOICE_NUMBER": raw["INVOICE NUMBER"],
        "VENDOR_NAME": raw["VENDOR NAME"], "INVENTORY_TYPE": raw["INVENTORY TYPE"],
        "SUPPLIER_DESC": raw["SUPPLIER DESC"], "S_UOM": raw["S UOM"],
        "S_QTY": raw["S QTY"],
    })


def merge_inventory(inv: pd.DataFrame, snapshot: str = "") -> dict:
    """
    Merge an uploaded inventory file into the INVENTORY sheet.

    Rows that already exist for the same Invoice Number + Pallet are
    replaced with the new values; everything else is appended. Nothing
    that is not in the uploaded file is touched.
    """
    rows = to_sheet_rows(inv, snapshot)
    added, replaced = gsheets.upsert_by(
        "INVENTORY", rows.to_dict("records"), INV_KEY, INV_KEY_FALLBACK)
    total = len(gsheets.get_df("INVENTORY"))
    return {"uploaded": len(rows), "added": added, "replaced": replaced,
            "total": total}


# ═══════════════════════════════════════════════════════════════════
#  reconciliation
# ═══════════════════════════════════════════════════════════════════
def open_asns(summary: pd.DataFrame) -> list[str]:
    """Every ASN that is not yet fully complete."""
    if summary.empty:
        return []
    m = summary["OVERALL"].astype(str) != schema.S_COMPLETE
    return sorted({clean(a) for a in summary.loc[m, "ASN NO"] if clean(a)})


def auto_reconcile(inv: pd.DataFrame, cfg: dict, user: str = "auto",
                   asns: list[str] | None = None, note: str = "",
                   push_ax: bool = True, make_email: bool = True,
                   settings: dict | None = None) -> dict:
    """
    Reconcile, write every result back, and return what happened.

    Steps
      1. reconcile the selected ASN lines against the inventory
      2. write ASN_DETAIL (including extra HU rows)
      3. write ASN_SUMMARY; Korber GRN Done -> AX GRN Pending
      4. push those ASNs into the AX_GRN queue
      5. auto-resolve open discrepancies whose lines now tally
      6. upsert the discrepancies still outstanding
      7. build the mismatch email
      8. append a RECON_LOG entry
    """
    rid = run_id()
    ts = now_str()
    settings = settings or gsheets.settings_dict()

    detail_all = gsheets.get_df("ASN_DETAIL")
    summary_all = gsheets.get_df("ASN_SUMMARY")

    if asns is None:
        asns = open_asns(summary_all) or sorted(
            {clean(a) for a in detail_all["ASN NO"] if clean(a)})

    result = {"run_id": rid, "run_at": ts, "asns": asns, "note": note,
              "stats": {}, "detail": pd.DataFrame(), "extra": pd.DataFrame(),
              "summary": pd.DataFrame(), "discrepancies": pd.DataFrame(),
              "resolved": [], "ax_pushed": [], "email": None,
              "skipped": not asns}

    if detail_all.empty or not asns:
        return result

    sub = detail_all[detail_all["ASN NO"].astype(str).isin(asns)].copy()
    if sub.empty:
        result["skipped"] = True
        return result

    # 1 ── reconcile
    upd, extra, stats = matching.reconcile(sub, inv, cfg, rid)
    stats["inventory_rows"] = len(inv)
    summ = matching.summarise_asn(upd, extra)

    # 2 ── ASN_DETAIL
    rows = upd.to_dict("records")
    if not extra.empty:
        rows += extra.to_dict("records")
    gsheets.upsert("ASN_DETAIL", rows)

    # 3 ── ASN_SUMMARY
    keep = ["ASN NO", "TOTAL LINES", "TOTAL HU", "TOTAL QTY", "ITEM COUNT",
            "MATCHED LINES", "MISSING LINES", "MISMATCH LINES", "EXTRA LINES",
            "MATCHED QTY", "RECEIVED QTY", "QTY DIFF", "STATUS",
            "KORBER GRN", "KORBER GRN NO", "LAST RECON"]
    sm = summ[keep].copy()
    done = sm["KORBER GRN"] == schema.K_DONE
    sm["KORBER GRN DATE"] = [ts if d else "" for d in done]
    if push_ax:
        sm["AX GRN"] = [schema.AX_PENDING if d else schema.AX_NA for d in done]
        sm["OVERALL"] = [schema.S_AX_PENDING if d else schema.S_GRN_PENDING
                         for d in done]
    else:
        sm["AX GRN"] = [schema.AX_NA for _ in done]
        sm["OVERALL"] = [schema.S_KORBER_DONE if d else schema.S_GRN_PENDING
                         for d in done]

    # never downgrade an ASN that is already fully complete
    if not summary_all.empty:
        complete = set(summary_all.loc[
            summary_all["OVERALL"] == schema.S_COMPLETE, "ASN NO"].astype(str))
        sm = sm[~sm["ASN NO"].astype(str).isin(complete)]
    if not sm.empty:
        gsheets.upsert("ASN_SUMMARY", sm.to_dict("records"))

    # 4 ── AX GRN queue
    ax_pushed = []
    if push_ax:
        ready = summ[summ["KORBER GRN"] == schema.K_DONE]
        ax_rows = [{
            "ASN NO": r["ASN NO"], "CLIENT CODE": r["CLIENT CODE"],
            "KORBER GRN NO": r["KORBER GRN NO"], "KORBER GRN DATE": ts,
            "TOTAL LINES": r["TOTAL LINES"], "TOTAL QTY": r["TOTAL QTY"],
            "PUSHED AT": ts, "PUSHED BY": user,
            "AX GRN": schema.AX_PENDING, "AX GRN NO": "", "AX GRN DATE": "",
            "AX GRN BY": "", "OVERALL": schema.S_AX_PENDING,
            "REMARK": "Auto-pushed on inventory upload",
        } for _, r in ready.iterrows()
            if str(r["ASN NO"]) not in _already_done_in_ax(r["ASN NO"])]
        if ax_rows:
            gsheets.upsert("AX_GRN", ax_rows)
            ax_pushed = [r["ASN NO"] for r in ax_rows]

    # 5 ── auto-resolve discrepancies that now tally
    resolved = _auto_resolve(upd, rid, ts)

    # 6 ── outstanding discrepancies
    disc = matching.discrepancy_rows(upd, extra, rid)
    if not disc.empty:
        gsheets.upsert("DISCREPANCY", disc.to_dict("records"))

    # 7 ── mismatch email
    email = None
    if make_email and not disc.empty:
        email = build_mismatch_email(summ, disc, settings, user, rid, note)

    # 8 ── log
    gsheets.append_rows("RECON_LOG", [[
        rid, ts, user, len(asns), ", ".join(asns[:20]),
        stats["inventory_rows"], stats["lines"], stats["matched"],
        stats["missing"], stats["mismatch"], stats["extra"],
        note or "Auto reconciliation on inventory upload",
    ]])

    result.update({"stats": stats, "detail": upd, "extra": extra,
                   "summary": summ, "discrepancies": disc,
                   "resolved": resolved, "ax_pushed": ax_pushed,
                   "email": email, "skipped": False})
    return result


def _already_done_in_ax(asn) -> set:
    """ASNs already marked AX GRN Done should not be re-queued."""
    ax = gsheets.get_df("AX_GRN")
    if ax.empty:
        return set()
    return set(ax.loc[ax["AX GRN"] == schema.AX_DONE, "ASN NO"].astype(str))


def _auto_resolve(detail: pd.DataFrame, rid: str, ts: str) -> list[str]:
    """
    Close any open discrepancy whose line now matches. This is what makes a
    previously mismatched HU clear itself once a corrected inventory file is
    uploaded.
    """
    disc = gsheets.get_df("DISCREPANCY")
    if disc.empty:
        return []

    good = {matching.disc_id(r["ASN NO"], r["HU ID"])
            for _, r in detail.iterrows()
            if str(r.get("MATCH STATUS")) == schema.M_MATCHED}
    if not good:
        return []

    open_mask = disc["STATUS"].astype(str).str.upper().isin(
        ["", schema.D_OPEN])
    hit = open_mask & disc["DISC ID"].astype(str).isin(good)
    ids = list(disc.loc[hit, "DISC ID"])
    if not ids:
        return []

    disc.loc[hit, "STATUS"] = schema.D_RESOLVED
    disc.loc[hit, "ACTION BY"] = "auto"
    disc.loc[hit, "CLOSED AT"] = ts
    disc.loc[hit, "NOTE"] = f"Resolved automatically on inventory upload ({rid})"
    gsheets.overwrite("DISCREPANCY", disc)
    return ids


# ═══════════════════════════════════════════════════════════════════
#  email
# ═══════════════════════════════════════════════════════════════════
def build_mismatch_email(summary: pd.DataFrame, disc: pd.DataFrame,
                         settings: dict, user: str, rid: str,
                         note: str = "") -> dict:
    """Build the mismatch email and store it in EMAIL_LOG."""
    asns = sorted({clean(a) for a in disc["ASN NO"] if clean(a)})
    sm = summary[summary["ASN NO"].astype(str).isin(asns)] \
        if not summary.empty else pd.DataFrame()

    subject, md = reporting.discrepancy_email(
        sm, disc,
        company=settings.get("COMPANY", "EFL"),
        site=settings.get("SITE", ""),
        client=settings.get("CLIENT_CODE", ""),
        prepared_by=user,
        to=settings.get("EMAIL_TO", ""),
        cc=settings.get("EMAIL_CC", ""),
        run_id=rid,
        inventory_note=note,
        auto=True)

    eid = uuid.uuid4().hex[:10].upper()
    gsheets.append_rows("EMAIL_LOG", [[
        eid, now_str(), user, ", ".join(asns[:20]), subject,
        settings.get("EMAIL_TO", ""), settings.get("EMAIL_CC", ""),
        md[:45000],
    ]])
    return {"id": eid, "subject": subject, "md": md, "asns": asns,
            "to": settings.get("EMAIL_TO", ""), "cc": settings.get("EMAIL_CC", "")}


# ═══════════════════════════════════════════════════════════════════
#  attachments
# ═══════════════════════════════════════════════════════════════════
def attachments_for(asn: str) -> pd.DataFrame:
    """Every image and PDF linked to one ASN."""
    meta = gsheets.get_df("ASN_IMAGES")
    if meta.empty:
        return meta
    return meta[meta["ASN NO"].astype(str).str.strip() == str(asn).strip()]


def attachment_counts() -> dict[str, int]:
    """ASN -> number of attachments, for badges in list views."""
    meta = gsheets.get_df("ASN_IMAGES")
    if meta.empty:
        return {}
    return meta.groupby(meta["ASN NO"].astype(str).str.strip()).size().to_dict()
