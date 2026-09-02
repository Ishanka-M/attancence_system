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
| ⚙️ **Setup** | Sheets auto-create · matching rules · attachments · **API & quota panel** |
| 📤 **ASN Upload** | **Excel හෝ PDF** upload → sheet/table එක තෝරලා confirm → Summary + Details save → file එකේ **images auto-extract** + PDF එකම attach |
| 📦 **Inventory** | Korber inventory report upload + snapshot save |
| 🔄 **Reconciliation** | ASN vs Inventory match → tally ඒවාට **Korber GRN Done** remark → tally නැති ඒවා discrepancy |
| 🧾 **ASN Register** | Summary + Details browse / filter / Excel download |
| ⚠️ **Discrepancy** | විෂමතා **Summary සහ Details** report + Excel download + close කිරීම |
| ✉️ **Email** | විස්තර සහිත **Markdown email** එකක් auto-generate (copy කරගන්න code block එකක්) |
| ✅ **AX GRN** | AX GRN Pending list → **AX GRN Done** button → Fully Complete |
| 🔍 **Search** | ඕනෑම data එකක් — HU, ASN, item, lot, PO, GRN, vendor — sheets කිහිපයක් හරහා |
| 🖼️ **Attachments** | Photos සහ PDF documents — preview, download, delete |
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

## 📕 PDF support

ASN document එක PDF එකකින් ආවත් වැඩ කරනවා.

* `pdfplumber` එකෙන් PDF එකේ **හැම table එකක්ම** හොයලා, ASN columns
  කීයක් තියෙනවද කියලා score කරලා ලකුණු කරනවා. හොඳම එක ඉස්සරහට එනවා.
* Upload කරද්දී "Page 2 · Table 1 · 34×9 · ASN columns 9" වගේ options
  එනවා — **තෝරලා confirm කරන්න** ඕනේ, Excel එකේ sheet එක වගේම.
* Multi-page tables වල repeat වෙන header rows automatic ව අයින් වෙනවා.
* PDF එකේ embed වුණ images ත් auto-extract වෙනවා, සහ **PDF එකම** ASN එකට
  document එකක් විදිහට attach වෙනවා (Upload page එකේ *PDF attach* checkbox).
* Scan කරපු (text layer නැති) PDF එකකදී පැහැදිලි message එකක් එනවා —
  ඒ වගේ එකක් attachment එකක් විදිහට තියාගෙන Excel එකෙන් data දාන්න.

---

## 🖼️ Attachments (images + PDF)

Default විදිහට **Drive folder** එකට යනවා. Drive එක fail වුණොත් automatic ව
**Google Sheet එකට** fallback වෙනවා — attachment එකක් කවදාවත් නැති වෙන්නේ නෑ.

* Excel/PDF එක ඇතුළේ embed වුණ images upload කරද්දීම auto-extract වෙනවා
  (`Insert → Picture` සහ `Insert image in cell` දෙකම).
* Pillow එකෙන් resize (default max 1400px) + JPEG compress. 10 MB photo එකක්
  ~500 KB දක්වා පොඩි වෙනවා. PDF compress කරන්නේ නෑ.
* SHEET mode එකේදී base64 chunks විදිහට `IMAGE_DATA` sheet එකට,
  metadata `ASN_IMAGES` sheet එකේ.

### Drive folder එක හදාගන්නේ කොහොමද

1. Setup → **Attachments** tab එකේ folder **link එකම** paste කරන්න පුළුවන් —
   ID එක automatic ව extract වෙනවා.
2. ඒ folder එක service account email එකට **Editor** විදිහට share කරන්න.
   Email එක Setup page එකේ පෙන්නනවා.
3. **Drive connection test** button එකෙන් හරියටම වැඩද කියලා බලන්න.

> Google service account එකකට තමන්ගේ Drive storage quota නෑ. My Drive folder
> එකකට upload කරද්දී `storageQuotaExceeded` ආවොත්, folder එක **Shared Drive**
> එකක් යටතට ගන්න, නැත්නම් storage `SHEET` කරන්න. දෙකෙන් එකක්වත් නැතත්
> fallback එක නිසා data නැති වෙන්නේ නෑ.

---

## 🔌 API management

Google Sheets API එකේ සීමාව විනාඩියකට request **60**ක්. Setup → **API & quota**
tab එකේ මේවා තියෙනවා:

* විනාඩියේ calls ගණන live counter එකක් (sidebar එකෙත් පෙන්නනවා)
* සීමාවට ළං වුණාම **automatic throttle** — error එකක් වෙනුවට තත්පර ටිකක් රැඳෙනවා
* `429 / 500 / 502 / 503 / 504` වලට **exponential backoff + jitter** retry
  (උපරිම 5 වතාවක්). අනිත් errors කෙළින්ම එනවා, හංගන්නේ නෑ.
* Rate limit සහ cache TTL tune කරන්න පුළුවන්
* Retry / error / throttle counters + last error message

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
| `gsheets.py` | Google Sheets backend · auto-create · API manager |
| `parsing.py` | Excel + PDF parsing (alias mapping, header detect, image extract) |
| `matching.py` | Reconciliation engine |
| `reporting.py` | Excel reports + Markdown email |
| `images.py` | Image/PDF compress · Sheet storage · load · delete |
| `drive.py` | Drive image upload (optional) |
