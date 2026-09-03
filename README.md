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
| **ASN Upload** | Excel or PDF → choose the sheet or table → confirm → summary, details and attachments |
| **Inventory** | Upload the Korber inventory; the merge, reconciliation, AX push and email all follow automatically |
| **Reconciliation** | Manual re-run for a chosen set of ASNs |
| **ASN Register** | Summary and line details, per-ASN attachments, Excel export, delete |
| **Search** | Find any HU, ASN, item, lot, PO, GRN or vendor across sheets |
| **Discrepancies** | Summary and line level, severity, Excel export, manual close |
| **Email** | Generate or reopen the Markdown discrepancy email |
| **AX GRN** | Pending queue with attachment downloads, then Mark AX GRN done |
| **Attachments** | Every photo and PDF, with preview, download and delete |
| **Setup** | Sheets, matching rules, automation, attachments, API and quota |
| **Data Manager** | Edit any sheet directly (admin) |
| **Maintenance** | Delete an ASN, clear a sheet, reset the database (admin) |

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

## Inventory merge

Uploaded rows are keyed on **Invoice Number + Pallet**:

* a row with a key that already exists is **replaced** with the new values;
* a row with a new key is **added**;
* rows not present in the uploaded file are **left alone**.

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
* Images embedded in the PDF are extracted, and the PDF itself is attached
  to the ASN.
* A scanned PDF with no text layer reports that clearly; attach it as a
  document and load the data from Excel.

---

## Attachments

Images and PDFs are stored in the configured **Google Drive folder**.

* Paste the folder **link** into Setup → Attachments; the id is extracted
  for you. Use **Test the Drive connection** to confirm access.
* Share the folder with the service account address shown on that page as an
  **Editor**.
* **Drive keeps images at original quality.** Nothing is resized or
  re-encoded, so a download returns the exact bytes that were uploaded. The
  `QUALITY` column in `ASN_IMAGES` records this for every file.
* Compression only happens when a file cannot be kept whole — the sheet
  fallback, where a cell has a hard size limit. Those settings live under
  Setup → Attachments → Compression (2200 px, quality 92 by default).
* If Drive is unavailable the file falls back to the sheet itself — base64
  chunks in `IMAGE_DATA`, metadata in `ASN_IMAGES` — so nothing is lost.
* PDFs are never compressed, in either mode.

> A Google service account has no Drive storage quota of its own. If uploads
> return a quota error, move the folder into a **Shared Drive** or set
> Storage to `SHEET`.

Attachments can be downloaded from the ASN Register, the Attachments page,
and directly from the AX GRN queue before posting into AX. **Download
original** fetches the file back from Drive rather than serving a preview,
so the AX GRN download is the full-resolution original.

Photos *and* PDFs can be attached anywhere files are accepted, including the
"Photos or PDFs for this ASN" uploader on the ASN Upload page — useful for
GRN sheets, damage evidence, seal shots and supplier documents.

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
`ASN_IMAGES` · `IMAGE_DATA` · `RECON_LOG` · `EMAIL_LOG` · `USER-M` ·
`SETTINGS`

Missing tabs are created at start-up and again on first write, so a missing
sheet never raises an error. New schema columns are appended to existing
sheets without losing data.

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
3. **Setup → Attachments** → paste the Drive folder link, share the folder
   with the service account, then run the connection test.
4. **Setup → Automation** → confirm the three automatic steps are enabled.

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
| `images.py` | Attachment compression, Drive and sheet storage |
| `drive.py` | Drive upload and folder checks |
| `reporting.py` | Excel reports and the Markdown email |
| `schema.py` | Sheet definitions, status constants, default settings |
