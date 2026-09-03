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

    Every existing row for an Invoice Number + Pallet that appears in the
    upload is removed, then all the uploaded rows are written. Replacing by
    group rather than row matters when one pallet carries several items or
    lots - a row-for-row swap would silently drop the extra lines. Anything
    not present in the upload is left untouched.
    """
    rows = to_sheet_rows(inv, snapshot)
    if rows.empty:
        return {"uploaded": 0, "added": 0, "replaced": 0, "groups": 0,
                "new_groups": 0, "total": len(gsheets.get_df("INVENTORY"))}

    def key_of(invoice, pallet) -> str:
        i, p = str(invoice or "").strip().upper(), str(pallet or "").strip().upper()
        return f"{i}|{p}" if i else p

    keys = {key_of(r["INVOICE NUMBER"], r["PALLET"]) for _, r in rows.iterrows()}
    keys.discard("")

    cur = gsheets.get_df("INVENTORY")
    replaced, prev_keys = 0, set()
    if not cur.empty:
        cur_keys = cur.apply(
            lambda r: key_of(r.get("INVOICE NUMBER"), r.get("PALLET")), axis=1)
        prev_keys = set(cur_keys)
        hit = cur_keys.isin(keys)
        replaced = int(hit.sum())
        cur = cur[~hit]

    up_keys = rows.apply(
        lambda r: key_of(r["INVOICE NUMBER"], r["PALLET"]), axis=1)
    added = int((~up_keys.isin(prev_keys)).sum())
    new_groups = len(keys - prev_keys)

    out = pd.concat([cur, rows.reindex(columns=schema.INVENTORY_HEADERS)],
                    ignore_index=True)
    gsheets.overwrite("INVENTORY", out)

    return {"uploaded": len(rows), "added": added, "replaced": replaced,
            "groups": len(keys), "new_groups": new_groups, "total": len(out)}


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
              "resolved": [], "ax_pushed": [],
              "pending": {"opened": 0, "cleared": 0}, "email": None,
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
        posted = _ax_completed()          # read once, not once per row
        ax_rows = [{
            "ASN NO": r["ASN NO"], "CLIENT CODE": r["CLIENT CODE"],
            "KORBER GRN NO": r["KORBER GRN NO"], "KORBER GRN DATE": ts,
            "TOTAL LINES": r["TOTAL LINES"], "TOTAL QTY": r["TOTAL QTY"],
            "PUSHED AT": ts, "PUSHED BY": user,
            "AX GRN": schema.AX_PENDING, "AX GRN NO": "", "AX GRN DATE": "",
            "AX GRN BY": "", "OVERALL": schema.S_AX_PENDING,
            "REMARK": "Auto-pushed on inventory upload",
        } for _, r in ready.iterrows() if str(r["ASN NO"]) not in posted]
        if ax_rows:
            gsheets.upsert("AX_GRN", ax_rows)
            ax_pushed = [r["ASN NO"] for r in ax_rows]

    # 5 ── auto-resolve discrepancies that now tally
    resolved = _auto_resolve(upd, rid, ts)

    # 5b ── keep the pending register in step with the result
    opens, clears = [], []
    if not pending_enabled():
        sm_iter = []
    else:
        sm_iter = list(sm.iterrows())
    posted_ax = _ax_completed()
    for _, r in sm_iter:
        a = clean(r["ASN NO"])
        if not a or a in posted_ax:
            continue
        if str(r["KORBER GRN"]) == schema.K_DONE:
            clears.append(pending_id(a, schema.STAGE_KORBER))
            if push_ax:
                opens.append({
                    "PENDING ID": pending_id(a, schema.STAGE_AX),
                    "ASN NO": a, "STAGE": schema.STAGE_AX,
                    "REASON": "Awaiting AX posting",
                    "PRIORITY": "Normal",
                    "RAISED AT": ts, "RAISED BY": user,
                })
        else:
            opens.append({
                "PENDING ID": pending_id(a, schema.STAGE_KORBER),
                "ASN NO": a, "STAGE": schema.STAGE_KORBER,
                "REASON": auto_reason(r),
                "PRIORITY": "Normal",
                "RAISED AT": ts, "RAISED BY": user,
            })
    pending_change = sync_pending(opens, clears, user)

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
                   "pending": pending_change, "email": email,
                   "skipped": False})
    return result


def _ax_completed() -> set:
    """ASNs already posted in AX - they must not be re-queued as pending."""
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
#  pending register
#  Any GRN held up at either stage gets a row here with the reason and a
#  remark, so the hold-ups are a maintained list rather than something you
#  have to re-derive from the reconciliation every time.
# ═══════════════════════════════════════════════════════════════════
def pending_enabled() -> bool:
    """False when the deployed schema.py has no PENDING sheet yet."""
    return gsheets.has_sheet("PENDING") and hasattr(schema, "PENDING_HEADERS")


def pending_id(asn: str, stage: str) -> str:
    """One row per ASN per stage, so updates land on the same record."""
    return f"{nkey(asn)}|{nkey(stage)}"


def auto_reason(row) -> str:
    """Plain description of why an ASN is still short of Korber GRN done."""
    missing = int(to_num(row.get("MISSING LINES")))
    mismatch = int(to_num(row.get("MISMATCH LINES")))
    extra = int(to_num(row.get("EXTRA LINES")))
    matched = int(to_num(row.get("MATCHED LINES")))
    bits = []
    if missing:
        bits.append(f"{missing} line(s) not received")
    if mismatch:
        bits.append(f"{mismatch} line(s) mismatched")
    if extra:
        bits.append(f"{extra} extra HU in inventory")
    if not bits:
        return "Not yet reconciled" if not matched else "Partially received"
    return "; ".join(bits)


def sync_pending(opens: list[dict], clears: list[str], user: str = "auto") -> dict:
    """
    Apply a batch of pending changes in a single write.

    `opens` are full rows keyed by PENDING ID - blank values leave whatever
    is already recorded alone, so an operator's remark survives every
    later reconciliation. `clears` are ids to mark CLEARED.
    """
    if not opens and not clears or not pending_enabled():
        return {"opened": 0, "cleared": 0}

    ts = now_str()
    df = gsheets.get_df("PENDING")
    cur: dict[str, dict] = {}
    if not df.empty:
        for _, r in df.iterrows():
            key = str(r["PENDING ID"]).strip()
            if key:
                cur[key] = {c: clean(r.get(c)) for c in schema.PENDING_HEADERS}

    opened = 0
    for e in opens:
        pid = e["PENDING ID"]
        old = cur.get(pid, {})
        merged = dict(old)
        for k, v in e.items():
            if str(v).strip() != "":
                merged[k] = v
        merged["PENDING ID"] = pid
        if not merged.get("RAISED AT"):
            merged["RAISED AT"] = ts
            merged["RAISED BY"] = merged.get("RAISED BY") or user
        if old.get("STATUS") == schema.P_CLEARED:      # the issue came back
            merged["RAISED AT"] = ts
            merged["CLEARED AT"] = ""
            merged["CLEARED BY"] = ""
        merged["STATUS"] = schema.P_OPEN
        cur[pid] = merged
        opened += 1

    cleared = 0
    for pid in clears:
        row = cur.get(str(pid).strip())
        if row and row.get("STATUS") != schema.P_CLEARED:
            row["STATUS"] = schema.P_CLEARED
            row["CLEARED AT"] = ts
            row["CLEARED BY"] = user
            cleared += 1

    gsheets.overwrite("PENDING", pd.DataFrame(list(cur.values())))
    return {"opened": opened, "cleared": cleared}


def raise_pending(asn: str, stage: str, reason: str, remark: str = "",
                  priority: str = "Normal", user: str = "",
                  follow_up: str = "", note: str = "") -> dict:
    """Put one ASN on hold at a stage, or update the existing hold."""
    return sync_pending([{
        "PENDING ID": pending_id(asn, stage),
        "ASN NO": clean(asn), "STAGE": stage, "REASON": reason,
        "REMARK": remark, "PRIORITY": priority,
        "RAISED AT": now_str(), "RAISED BY": user or "unknown",
        "FOLLOW UP": follow_up, "NOTE": note,
    }], [], user or "unknown")


def clear_pending(ids: list[str], user: str = "", note: str = "") -> dict:
    res = sync_pending([], list(ids), user or "unknown")
    if note:
        df = gsheets.get_df("PENDING")
        if not df.empty:
            m = df["PENDING ID"].astype(str).isin([str(i) for i in ids])
            df.loc[m, "NOTE"] = note
            gsheets.overwrite("PENDING", df)
    return res


def clear_stage(asns: list[str], stage: str, user: str = "") -> dict:
    """Clear a stage hold for several ASNs at once - used when AX is posted."""
    return sync_pending([], [pending_id(a, stage) for a in asns], user or "auto")


def open_pending(stage: str | None = None) -> pd.DataFrame:
    if not pending_enabled():
        return pd.DataFrame()
    df = gsheets.get_df("PENDING")
    if df.empty:
        return df
    out = df[df["STATUS"].astype(str).str.upper() != schema.P_CLEARED]
    if stage:
        out = out[out["STAGE"] == stage]
    return out


def pending_remarks() -> dict[str, str]:
    """ASN -> 'stage: reason - remark' for the open holds, for reports."""
    df = open_pending()
    if df.empty:
        return {}
    out: dict[str, str] = {}
    for _, r in df.iterrows():
        asn = clean(r["ASN NO"])
        txt = f"{clean(r['STAGE'])}: {clean(r['REASON'])}"
        if clean(r["REMARK"]):
            txt += f" - {clean(r['REMARK'])}"
        out[asn] = (out[asn] + " | " + txt) if asn in out else txt
    return out


# ═══════════════════════════════════════════════════════════════════
#  send to AX despite a discrepancy
# ═══════════════════════════════════════════════════════════════════
def push_to_ax(asns: list[str], user: str, reason: str, remark: str,
               override: bool = True) -> dict:
    """
    Move ASNs into the AX GRN queue even though they still carry a
    discrepancy. The override, the reason and the remark are all recorded,
    and the ASN is added to the pending register at the AX stage.
    """
    asns = [clean(a) for a in asns if clean(a)]
    if not asns:
        return {"pushed": 0}

    ts = now_str()
    summ = gsheets.get_df("ASN_SUMMARY")
    disc = gsheets.get_df("DISCREPANCY")
    open_by_asn = {}
    if not disc.empty:
        o = disc[disc["STATUS"].astype(str).str.upper() == schema.D_OPEN]
        open_by_asn = o.groupby(o["ASN NO"].astype(str)).size().to_dict()

    ax_rows, opens = [], []
    for a in asns:
        row = summ[summ["ASN NO"].astype(str) == a]
        r = row.iloc[0] if not row.empty else {}
        ax_rows.append({
            "ASN NO": a,
            "CLIENT CODE": clean(r.get("CLIENT CODE", "")) if len(row) else "",
            "KORBER GRN NO": clean(r.get("KORBER GRN NO", "")) if len(row) else "",
            "KORBER GRN DATE": clean(r.get("KORBER GRN DATE", "")) if len(row) else "",
            "TOTAL LINES": clean(r.get("TOTAL LINES", "")) if len(row) else "",
            "TOTAL QTY": clean(r.get("TOTAL QTY", "")) if len(row) else "",
            "PUSHED AT": ts, "PUSHED BY": user or "unknown",
            "AX GRN": schema.AX_PENDING, "AX GRN NO": "", "AX GRN DATE": "",
            "AX GRN BY": "", "OVERALL": schema.S_AX_PENDING,
            "OVERRIDE": "Y" if override else "N",
            "OVERRIDE REASON": reason,
            "REMARK": remark,
        })
        opens.append({
            "PENDING ID": pending_id(a, schema.STAGE_AX),
            "ASN NO": a, "STAGE": schema.STAGE_AX,
            "REASON": reason or "Discrepancy accepted - posting with variance",
            "REMARK": remark, "PRIORITY": "High" if override else "Normal",
            "RAISED AT": ts, "RAISED BY": user or "unknown",
            "NOTE": (f"Sent to AX with {open_by_asn.get(a, 0)} open discrepancy "
                     f"line(s)") if override else "",
        })

    gsheets.upsert("AX_GRN", ax_rows)

    if not summ.empty:
        m = summ["ASN NO"].astype(str).isin(asns)
        summ.loc[m, "AX GRN"] = schema.AX_PENDING
        summ.loc[m, "OVERALL"] = schema.S_AX_PENDING
        summ.loc[m, "REMARK"] = remark
        gsheets.overwrite("ASN_SUMMARY", summ)

    sync_pending(opens, [], user or "unknown")
    return {"pushed": len(ax_rows), "asns": asns}
