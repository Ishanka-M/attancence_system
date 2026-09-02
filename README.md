# 📦 ASN ↔ GRN Control System

ASN document එකක් upload කරලා, Korber One inventory එකට match කරලා,
**Korber GRN → AX GRN → Fully Complete** කියන pipeline එක manage කරන
**Streamlit + Google Sheets** system එක.

පරණ `attancence_system` එකේ Google Sheets backend එකේ ව්‍යුහය තියාගෙන,
ඇතුළත සම්පූර්ණයෙන්ම ASN/GRN වැඩේට අලුතෙන් ලියලා තියෙනවා.

---

## 🔄 Flow එක

```
📤 ASN Upload  ──►  📦 Inventory  ──►  🔄 Reconciliation
                                          │
                        ┌─────────────────┴──────────────────┐
                        │                                    │
                  tally වෙනවා                          tally නෑ
                        │                                    │
                 KORBER GRN DONE                     ⚠️ DISCREPANCY
                        │                              │        │
                 ✅ AX GRN PENDING                Summary+    ✉️ Markdown
                        │                         Details      email
                 [AX GRN Done] button              report
                        │
                 🎉 FULLY COMPLETE
```

---

## ✨ මොනවද කරන්නේ

| Page | වැඩේ |
|------|------|
| ⚙️ **Setup** | Google Sheet එකේ **හැම tab එකක්ම auto-create** + matching settings |
| 📤 **ASN Upload** | Excel upload → **sheet එක තෝරලා confirm** → Summary + Details save → Excel එකේ තියෙන **images auto-extract** කරලා Drive එකට |
| 📦 **Inventory** | Korber inventory report upload + snapshot save |
| 🔄 **Reconciliation** | ASN vs Inventory match → tally ඒවාට **Korber GRN Done** remark → tally නැති ඒවා discrepancy |
| 🧾 **ASN Register** | Summary + Details browse / filter / Excel download |
| ⚠️ **Discrepancy** | විෂමතා **Summary සහ Details** report + Excel download + close කිරීම |
| ✉️ **Email** | විස්තර සහිත **Markdown email** එකක් auto-generate (copy කරගන්න code block එකක්) |
| ✅ **AX GRN** | AX GRN Pending list → **AX GRN Done** button → Fully Complete |
| 🔍 **Search** | ඕනෑම data එකක් — HU, ASN, item, lot, PO, GRN, vendor — sheets කිහිපයක් හරහා |
| 🖼️ **ASN Images** | ASN එකට අදාළ photos + preview + download + delete |
| 📊 **Dashboard** | Pipeline funnel, status donut, discrepancy charts, ASN vs Received qty |
| 🗂️ **Data Manager** | ඕනෑම sheet එකක් edit කරන්න (admin PIN) |
| 🧹 **Maintenance** | **ASN delete** · sheet clear · **Database reset** (backup + confirm සමග) |

---

## 🧠 Matching logic

ප්‍රධාන key එක **HU ID** (ASN `HU_ID` ↔ Inventory `Pallet`).

| තත්ත්වය | Status | Korber GRN |
|---|---|---|
| HU inventory එකේ නෑ | `MISSING IN INVENTORY` | PENDING |
| Qty වෙනස් | `QTY MISMATCH` | PENDING |
| Item number වෙනස් | `ITEM MISMATCH` | PENDING |
| Lot number වෙනස් | `LOT MISMATCH` | PENDING |
| වෙන ASN එකකට receive වෙලා | `WRONG ASN` | PENDING |
| ASN එකේ නැති HU inventory එකේ | `EXTRA IN INVENTORY` | PENDING |
| ඔක්කොම හරි ✅ | `MATCHED` | **DONE** |

ASN එකේ **හැම line එකක්ම** MATCHED නම් → ASN status = `KORBER GRN DONE`
→ automatic ව `AX_GRN` sheet එකට `AX GRN PENDING` විදිහට යනවා.

`HIES-26AUG_UPPD_40659` වගේ inventory ASN numbers වල client prefix එක
auto-strip වෙනවා (Setup → *Client prefix ඉවත් කරන්න*).

---

## 📑 Auto-create වෙන sheets

`ASN_SUMMARY` · `ASN_DETAIL` · `INVENTORY` · `DISCREPANCY` · `AX_GRN` ·
`ASN_IMAGES` · `IMAGE_DATA` · `RECON_LOG` · `EMAIL_LOG` · `USER-M` · `SETTINGS`

Setup page එකේ **🏗️ හැම Sheet එකක්ම හදන්න** ඔබන්න. පස්සේ schema එකට
column එකක් එකතු කළත් ඒකත් auto-add වෙනවා (තියෙන data නැති නොවී).

---

## 🚀 Setup

### 1. Google service account
1. [console.cloud.google.com](https://console.cloud.google.com) → project එකක් හදන්න
2. **Google Sheets API** සහ **Google Drive API** enable කරන්න
3. IAM → Service Accounts → account එකක් හදලා **JSON key** එකක් download කරන්න
4. Google Sheet එකක් හදලා ඒ JSON එකේ `client_email` එකට **Editor** විදිහට share කරන්න

### 2. Secrets
`.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` කියලා rename කරලා
values දාන්න. (Streamlit Cloud එකේදී App → Settings → Secrets.)

### 3. Run
```bash
pip install -r requirements.txt
streamlit run app.py
```

### 4. පළමු වතාවේ
1. **⚙️ Setup** → 🏗️ හැම Sheet එකක්ම හදන්න
2. Settings වල `CLIENT_CODE` (උදා: `HIES`), site, email addresses දාන්න
3. Images ඕන නම් Drive folder එකක් හදලා service account එකට Editor විදිහට
   share කරලා, folder ID එක `DRIVE_FOLDER_ID` එකට දාන්න

---

## 🖼️ Images ගැන

Default විදිහට images **Google Sheet එකේම** save වෙනවා — Drive එක ඕන නෑ.

* ASN Excel එක ඇතුළේ **embed වෙච්ච images** upload කරද්දීම auto-extract වෙනවා
  (`Insert → Picture` සහ `Insert image in cell` දෙකම).
* Pillow එකෙන් resize (default max 1400px) + JPEG compress කරලා,
  base64 chunks විදිහට `IMAGE_DATA` sheet එකට. Metadata `ASN_IMAGES` sheet එකේ.
* 10 MB photo එකක් ~500 KB දක්වා පොඩි වෙනවා. `🖼️ ASN Images` page එකේ
  preview, download, delete කරන්න පුළුවන්.

**Drive එකට ඕන නම්:** Setup → Image storage → `DRIVE`, ඊට පස්සේ Drive folder
එකක් හදලා service account එකට **Editor** විදිහට share කරලා folder ID එක දාන්න.
Service account එකකට තමන්ගේ storage quota නෑ — folder එක share කරලා නැත්නම්
Drive upload එක fail වෙනවා. ඒ වුණත් fail වුණාම automatic ව Sheet එකට
fallback වෙනවා, ඒ නිසා image එක නැති වෙන්නේ නෑ.

---

## 🧹 Maintenance (admin)

| Tab | වැඩේ |
|---|---|
| 🗑️ **ASN Delete** | ASN එකක් තෝරලා Summary + Details + Discrepancy + AX GRN + Images ඔක්කොම අයින්. කලින් backup Excel එකක් download කරගන්න පුළුවන්. තහවුරු කරන්න `DELETE` type කරන්න ඕනේ. |
| 🧽 **Sheet Clear** | Sheet එකක data විතරක් clear (headers ඉතුරු). Sheet එකේ නම type කරලා තහවුරු. |
| 💥 **Database Reset** | Transaction data විතරක් / custom sheets / සම්පූර්ණයෙන්ම. Full backup Excel + Admin PIN + `RESET` type කිරීම + checkbox — හතරම ඕනේ. |

ASN එකක් වේගයෙන් delete කරන්න `🧾 ASN Register` page එකෙත් shortcut එකක් තියෙනවා.

---

## 🗂️ Files

| File | වැඩේ |
|------|------|
| `app.py` | Streamlit UI — හැම page එකම |
| `schema.py` | Sheet definitions + status constants |
| `gsheets.py` | Google Sheets backend + auto-create |
| `parsing.py` | Excel parsing (alias mapping, header detect, image extract) |
| `matching.py` | Reconciliation engine |
| `reporting.py` | Excel reports + Markdown email |
| `images.py` | Image compress / Sheet storage / load / delete |
| `drive.py` | Drive image upload (optional) |
