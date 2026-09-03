# ASN / GRN Control System

Reconciles ASN documents against Korber One inventory and drives the
**Korber GRN → AX GRN → Fully Complete** pipeline. Built on Streamlit with
Google Sheets as the database.

---

## The flow

```
ASN Upload (Excel or PDF)
        │
        ▼
Inventory upload  ──────────────► everything below runs automatically
        │
        ├── merge inventory (replace by Invoice Number + Pallet, add the rest)
        ├── reconcile every open ASN
        ├── tallied lines            → Korber GRN Done → AX GRN Pending
        ├── lines that now tally     → open discrepancies auto-resolved
        ├── lines that still differ  → discrepancies raised
        └── mismatch email generated and stored in EMAIL_LOG
        │
        ▼
AX GRN page → download the ASN document → Mark AX GRN done → Fully Complete
```

Uploading the inventory is the only action required. No selecting, no
confirming, no button chasing.

---

## Pages

| Page | What it does |
|------|--------------|
| **Dashboard** | Pipeline strip, tally rate, open discrepancies, expected vs received quantity |
| **ASN Upload** | Excel or PDF → choose the sheet or table → confirm → summary and line details |
| **Inventory** | Upload the Korber inventory; the merge, reconciliation, AX push and email all follow automatically |
| **Reconciliation** | Manual re-run for a chosen set of ASNs |
| **ASN Register** | Summary and line details, Excel export, delete |
| **Search** | Find any HU, ASN, item, lot, PO, GRN or vendor across sheets |
| **Discrepancies** | Summary and line level, severity, Excel export, manual close |
| **Email** | Generate or reopen the Markdown discrepancy email |
| **AX GRN** | Pending queue, override push, then Mark AX GRN done |
| **Pending List** | Every GRN held at Korber or AX, with the reason, remark, priority and follow-up |
| **Setup** | Sheets, matching rules, automation, API and quota |
| **Data Manager** | Edit any sheet directly (admin) |
| **Maintenance** | Delete an ASN, clear a sheet, reset the database (admin) |

---

## Pending register

Any GRN that is held up gets a row in the `PENDING` sheet, one per ASN per
stage, so the hold-ups are a maintained list rather than something you
re-derive each time.

* Reconciliation keeps it current by itself: an ASN short of Korber GRN done
  gets a hold with an auto reason (`2 line(s) not received; 1 line(s)
  mismatched`), one that has passed gets its Korber hold cleared and an
  `Awaiting AX posting` hold opened instead.
* Operators add the **reason, remark, priority and follow-up** on the Pending
  List page. A remark you write is never overwritten by a later
  reconciliation — only the auto reason is refreshed.
* When a corrected inventory makes the ASN tally, the hold clears itself with
  `CLEARED BY = auto`. Posting the AX GRN clears the AX hold the same way.
* A hold that comes back re-opens the same row rather than adding a duplicate.

## Sending to AX with a discrepancy

Sometimes an ASN has to be posted even though it still carries a variance.
On the AX GRN page, **Send to AX despite a discrepancy** lists every ASN that
is not complete and not already queued, showing how many open discrepancy
lines each one has. A reason and a **remark are required**, plus an explicit
confirmation. The push records `OVERRIDE = Y`, the reason and the remark on
the `AX_GRN` row, and opens a high-priority AX hold in the pending register
noting how many discrepancy lines were outstanding at the time.

## Finalize summary report

One workbook covering the whole position, downloadable from the Dashboard,
the Pending List and the AX GRN page:

| Sheet | Contents |
|---|---|
| Overview | Counts, tally rate, quantity variance, overrides used |
| Pending | ASNs not complete, with the stage, hold reason and remark |
| Pending register | Every hold, open and cleared |
| Discrepancies | Open discrepancy lines |
| Completed | Fully complete ASNs with both GRN references |
| All ASN | The full summary |
| Line details | Every line with its match result |

---

## Matching logic

The primary key is the **HU ID** (ASN `HU_ID` ↔ inventory `Pallet`).

| Situation | Status | Korber GRN |
|---|---|---|
| HU not in inventory | `MISSING IN INVENTORY` | Pending |
| Quantity differs | `QTY MISMATCH` | Pending |
| Item number differs | `ITEM MISMATCH` | Pending |
| Lot number differs | `LOT MISMATCH` | Pending |
| Received under another ASN | `WRONG ASN` | Pending |
| In inventory but not on the ASN | `EXTRA IN INVENTORY` | Pending |
| Everything agrees | `MATCHED` | **Done** |

When every line of an ASN is `MATCHED` the ASN becomes **Korber GRN Done**
and moves straight into the **AX GRN Pending** queue.

Client prefixes such as `HIES-26AUG_UPPD_40659` are stripped before
comparison (Setup → Matching rules).

---

## Data flow fixes worth knowing

* **Inventory merge replaces by group.** All existing rows for an Invoice
  Number + Pallet in the upload are removed before the new rows are written,
  so a pallet carrying several items or lots keeps every line. A row-for-row
  swap used to drop the extras.
* **Re-uploading an ASN document no longer resets its GRN status.** Only ASNs
  the system has not seen before start at NEW; a reconciled or completed ASN
  keeps its Korber and AX state, and the page says how many were left alone.
* **A posted ASN is never re-queued.** ASNs already marked AX GRN done are
  skipped by the automatic push and by the pending register.

---

## Inventory merge

Uploaded rows are keyed on **Invoice Number + Pallet**:

* every existing row for an Invoice Number + Pallet in the upload is
  **removed**, then all the uploaded rows for it are written;
* pallets not mentioned in the upload are **left alone**.

Replacing by group rather than row for row matters when one pallet carries
several items or lots: a row-for-row swap would silently drop the extra
lines.

So a small correction file containing two pallets updates exactly those two
pallets and leaves the other few hundred rows intact. When a row has no
invoice number the pallet alone identifies it.

## Self-correcting discrepancies

Each discrepancy is identified by `ASN NO | HU ID` rather than a run number.
When a later inventory upload makes that line tally, the discrepancy is
marked `RESOLVED` automatically, with `ACTION BY = auto` and a note naming
the run. Re-running reconciliation updates existing discrepancy records
instead of piling up duplicates.

---

## PDF support

ASN documents can arrive as PDF.

* `pdfplumber` finds every table in the file and ranks them by how many ASN
  columns they contain, so the right one is offered first.
* The picker shows entries like `Page 2 · Table 1 · 34×9 · ASN columns 9` —
  choose one and confirm, exactly as with an Excel worksheet.
* Repeated header rows in multi-page tables are removed automatically.
* A scanned PDF with no text layer reports that clearly - load the data
  from Excel in that case.

---

## API management

The Sheets API allows roughly 60 requests per minute. Setup → **API & quota**
shows and controls:

* live calls-per-minute counter, mirrored in the sidebar;
* automatic throttling as the limit approaches, instead of an error;
* exponential backoff with jitter for `429 / 500 / 502 / 503 / 504`, up to
  five attempts — other errors surface immediately rather than being hidden;
* rate limit and cache lifetime tuning;
* retry, throttle and error counters plus the last error message.

---

## Sheets, created automatically

`ASN_SUMMARY` · `ASN_DETAIL` · `INVENTORY` · `DISCREPANCY` · `AX_GRN` ·
`PENDING` · `RECON_LOG` · `EMAIL_LOG` · `USER-M` · `SETTINGS`

Missing tabs are created at start-up and again on first write, so a missing
sheet never raises an error.

When a new version adds a column, existing sheets keep working: pages only
read the columns that are actually present, and **Setup → Sheets** flags
which sheets are behind and adds the new headers without touching the data.

If a new version adds a whole sheet, upload **every** `.py` file from the
package together. A mismatched set — say a new `app.py` with an old
`schema.py` — is detected at start-up: the sidebar names the missing sheets
and the affected page explains what to upload instead of failing.

---

## Setup

### 1. Google service account
1. Create a project at [console.cloud.google.com](https://console.cloud.google.com).
2. Enable the **Google Sheets API** and the **Google Drive API**.
3. Create a service account and download a **JSON key**.
4. Create a Google Sheet and share it with the JSON key's `client_email` as
   an **Editor**.

### 2. Secrets
Rename `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and
fill in the values. On Streamlit Cloud use App → Settings → Secrets.

### 3. Run
```bash
pip install -r requirements.txt
streamlit run app.py
```

### 4. First time through
1. **Setup → Sheets** → create all sheets.
2. **Setup → Matching rules** → set the client code, site and email addresses.
3. **Setup → Automation** → confirm the three automatic steps are enabled.

---

## Files

| File | Responsibility |
|------|----------------|
| `app.py` | Page behaviour and flow |
| `ui.py` | Custom HTML/CSS layer — stylesheet, cards, badges, steps, nav |
| `pipeline.py` | Inventory merge and the automatic reconciliation flow |
| `matching.py` | Reconciliation engine |
| `parsing.py` | Excel and PDF parsing, alias mapping, image extraction |
| `gsheets.py` | Sheets backend, auto-create, API manager |
| `reporting.py` | Excel reports and the Markdown email |
| `schema.py` | Sheet definitions, status constants, default settings |
