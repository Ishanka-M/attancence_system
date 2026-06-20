# 📊 EFL CSS — KPI & Incentive System

මුල් **`KPI_CSS_-_EGF_OTH__2_.xlsb`** Excel file එක වෙනුවට හදපු
**Streamlit + Python + Google Sheets** system එක.
GitHub එකට push කරලා **Streamlit Cloud** එකේ free deploy කරන්න පුළුවන්.

---

## ✨ විශේෂාංග

### 🔐 Login & Roles
- **👤 User** — USER ID එකෙන් log වෙනවා. තමන්ගේ data විතරක්.
- **👔 Leader** — USER-M එකේ **SUPERVISOR ID** = ඒ leader ගේ USER ID විදිහට users
  assign කළාම, leader log වුණාම **තමන්ගේ team එකේ හැම user කෙනෙක්ගේම data**
  (recursive, multi-level) පේනවා + ඔවුන් වෙනුවෙන් entry/upload කරන්න පුළුවන්.
  *(Assign කරන්නේ Admin → Data Manager → USER-M → SUPERVISOR ID column එකෙන්.)*
- **🛡️ Admin** — `admin_pin` එකෙන්. සම්පූර්ණ data + reports + audit + upload + CRUD.

### 📤 Bulk Upload — system එකෙන් calculate වෙනවා
අවම columns දාලා upload කළාම ඉතුරු ඔක්කොම **auto-calculate** වෙනවා:
- **TRANSACTION** (`Date, USER ID, SITE, CUSTOMMER, T-CODE, TIME, # OF TRANSACTION`)
  → USER NAME, Description, UOM, SMV, UTILIZE HOURS, REVANUE, In compute.
  T-CODE/TIME/qty invalid rows **block**.
- **ATTANDANCE** (`UNIC CODE, DATE, USER ID, IN DATE & TIME, OUT DATE & TIME, WORCK LOCATION`)
  → **# OF WORKING HRS = (OUT − IN) − LUNCH & TEA(1)**, # OF OT HRS = WORKING − SCHEDULED,
  SCHEDULED HRS, UTILIZED HOURS (ඒ දවසේ transactions වලින්), UTILIZATION, USER NAME/DEPT compute.
  පැය 20+ හෝ නිවාඩු/ඉරිදා rows → **PENDING** (admin approval).

| Page | User | Admin |
|------|:----:|:-----:|
| 🏠 Dashboard (monthly OT/Revenue/Cost/Incentive) | තමන්ගේ | සියල්ල |
| 📝 Transaction · 🕐 Attendance | තමන්ට lock | සියල්ල |
| 💰 Incentive | තමන්ගේ row | recalc + save |
| ⚙️ Setup · ⏱️ OT · 📋 Complaint · ✅ KPI · 🔍 Audit | — | ✅ |
| 📥 Export · 📤 Upload · 🛡️ Admin · 🗂️ Data Manager | — | ✅ |

### 📤 Bulk Upload (admin)
ATTANDANCE / TRANSACTION — Excel (.xlsx) හෝ CSV එකකින් data එකපාර add කරන්න.
Template එකක් download කරගන්න පුළුවන්. **Upload කරද්දීත් audit rules check වෙනවා:**
- **TRANSACTION:** T-CODE/TIME/qty valid ද check කරලා, error rows **block** කරනවා.
  SMV/UTILIZE/REVANUE/In auto-recompute වෙනවා.
- **ATTANDANCE:** පැය 20+ හෝ නිවාඩු/ඉරිදා rows → **PENDING** (admin approve කරන තෙක්),
  සතියට OT 15+ → highlight. "Clean විතරක්" හෝ "ඔක්කොම (violations PENDING)" කියලා තෝරන්න පුළුවන්.

### 🗂️ Data Manager (admin) — Add / Update / Delete
`USER-M`, `CUSTOMMER-M`, `TCODE-M` (= Master sheet - Finalized),
`CUSTOMMER COMPLAINT` ඇතුළු ඕනෑම sheet එකක records **add / update / delete**
කරන්න පුළුවන් (table එකේ edit කරලා Save).

| Page | වැඩේ |
|------|------|
| ⚙️ **Setup** | Google Sheet එකේ **හැම tab එකක්ම auto-create** + masters seed |
| 📝 **Transaction** | Activity එකක් දාද්දී SMV / Revenue / Incentive **auto-calculate** |
| 🕐 **Attendance** | Working hrs, OT, Utilization auto |
| ⏱️ **OT Approval** | OT request / approval tracking |
| 📋 **Complaint** | Customer complaint log |
| ✅ **KPI Update** | On-time KPI update tracking |
| 💰 **Incentive** | User එක එකකට incentive එකතුව + complaint penalty + INSENTIVE sheet save |
| 🔍 **Audit** | Rule violations 5ක් highlight කරලා පෙන්නනවා (පහත බලන්න) |
| 📥 **Export** | ATTANDANCE / TRANSACTION — **date range + user level** filter කරලා Excel/CSV download |
| 🛡️ **Admin** | Attendance approvals + නිවාඩු දවස් (HOLIDAY-M) setup *(PIN protected)* |
| 👥 **Masters** | USER-M, TCODE-M, SITE-M ... live edit |
| 🏠 **Dashboard** | **Monthly user-level** OT / Revenue / Cost / Incentive + charts |

> මුල් Excel එකේ data ඔක්කොම (336 T-codes, 110 users, 128 customers, sites,
> locations, times) seed data විදිහට මේකට දාලා තියෙනවා — Setup එක run කළාම
> Google Sheet එක ම පිරිලා එනවා.

### 🗂️ Google Sheet data format
ATTANDANCE, TRANSACTION, OT APPROVAL, CUSTOMMER COMPLAINT — **මුල් Excel එකේ
column නම් + order එකම** තියාගෙන save වෙනවා (TRANSACTION 19 cols, ATTANDANCE
21 cols). Audit feature වලට ඕනේ system columns 3ක් විතරක් (`SCHEDULED HRS`,
`APPROVAL STATUS`, `APPROVAL NOTE`) ATTANDANCE එකේ **අගට** add වෙනවා — මුල්
format එක එලෙසම තියෙනවා. Export කරද්දී downloaded Excel එකත් මේ format එකෙන්මයි.

### 📊 Monthly dashboard (user level)
මාසය select කරලා, user එක එකකට **OT Hrs · Normal Rev · OT Rev · Total Revenue
· Incentive · Cost** බලන්න පුළුවන්.
> **Cost** = incentive payout කියලා assume කරලා තියෙනවා. වෙනත් cost basis එකක්
> (OT wage වගේ) ඕනේ නම් `calc.monthly_user_summary` එකේ `COST` line එක වෙනස් කරන්න.

---

## 🔍 Audit Rules (system එකෙන්ම audit වෙනවා)

| # | Rule | වැඩේ |
|---|------|------|
| 1 | **20hr cap** | `# OF WORKING HRS` පැය 20+ → **Admin approval** ඕනේ. Approve වෙනකම් PENDING. |
| 2 | **නිවාඩු/ඉරිදා** | ඉරිදා හෝ admin නිවාඩු දවසකට attendance → **Admin approval** ඕනේ. |
| 3 | **OT ↔ Transaction** | Scheduled time එකට වඩා වැඩ කළොත් ඒ දවසට TRANSACTION එකේ **OT-N/OT-D** තියෙන්න ඕනේ. නැත්නම් flag. |
| 4 | **Complaint penalty** | CUSTOMMER COMPLAINT එකක් user ට add වුණොත් incentive එකෙන් අඩු වෙනවා (default 1000/complaint). |
| 5 | **සතියට OT 15+** | සතියකට `# OF OT HRS` 15 ඉක්මෙව්වොත් highlight. |
| 6 | **Missing Txn** | දවසකට TRANSACTION දාලා නැති active users highlight. |

**වැඩ පැය schedule** (`schema.py` → `WORKDAY_HOURS`):
සතියේ දවස් **08:00–17:00 = 8h** · සෙනසුරාදා **08:00–13:00 = 5h** · ඉරිදා **නිවාඩු**.

**Admin login:** secrets එකේ `[app] admin_pin` දාලා, sidebar එකේ 🔑 Admin login එකෙන්
PIN දාන්න. එතකොට Approvals + Holiday setup unlock වෙනවා.

---

## 🧮 Calculation Logic (Excel formulas → Python)

මුල් Excel එකේ formulas reverse-engineer කරලා verify කරපු:

```
UTILIZE HOURS  = # OF TRANSACTION × SMV(M) ÷ 60
REVANUE-NORMAL = # OF TRANSACTION × NORMAL rate     (TIME = NORMAL නම්)
REVANUE-OT-N   = # OF TRANSACTION × OT-N rate        (TIME = OT -N නම්)
REVANUE-OT-D   = # OF TRANSACTION × OT-D rate        (TIME = OT -D නම්)
TXN INCENTIVE  = TOTAL REVANUE ÷ 10
UTILIZATION    = UTILIZED HOURS ÷ # OF WORKING HRS
```

> ✅ මේ formulas මුල් Excel එකේ row එකක් (CSSTR0201, NORMAL, qty 128 →
> revenue 793.06) එක්ක exact ගැළපුණා.

Incentive rules (`schema.py` එකේ වෙනස් කරන්න පුළුවන්):
`0-Complaint = 3000`, `On-time KPI = 4000`, `100% OT recovery = 3000`, `Target = 20000`.

---

## 🚀 Setup පියවරෙන් පියවර

### 1️⃣ Google Cloud — Service Account හදන්න

1. <https://console.cloud.google.com> → අලුත් **Project** එකක්.
2. **APIs & Services → Library** → මේ දෙක **Enable** කරන්න:
   - *Google Sheets API*
   - *Google Drive API*
3. **APIs & Services → Credentials → Create Credentials → Service Account**.
4. Service account හදලා → **Keys → Add Key → JSON** → key file එක download වෙනවා.
5. ඒ JSON එකේ තියෙන `client_email` එක copy කරගන්න
   (උදා: `kpi-bot@project.iam.gserviceaccount.com`).

### 2️⃣ Secrets දාන්න

`.streamlit/secrets.toml.example` එක `.streamlit/secrets.toml` විදිහට copy කරලා,
download කරපු JSON එකේ values දාන්න:

```toml
[app]
spreadsheet_id = ""                 # තියෙන Sheet එකක් නම් URL එකේ id එක දාන්න
spreadsheet_name = "EFL KPI System" # නැත්නම් මේ නමින් auto-create වෙයි
share_email = "ඔයාගේ@gmail.com"     # auto-create වුණොත් මේකට share වෙයි

[gcp_service_account]
# ... JSON file එකේ field ඔක්කොම ...
```

> **වැදගත්:** දැනටමත් Google Sheet එකක් පාවිච්චි කරනවා නම්, ඒකේ **Share** එකෙන්
> service account එකේ `client_email` එකට **Editor** permission දෙන්න. නැත්නම්
> `spreadsheet_id` හිස් තියලා app එකට අලුත් එකක් හදන්න දෙන්න.

### 3️⃣ Local run

```bash
pip install -r requirements.txt
streamlit run app.py
```

App එක open වුණාම → **⚙️ Setup → 🚀 Sheets Auto-Create / Sync** click කරන්න.
මේකෙන් Google Sheet එකේ tabs ඔක්කොම හැදිලා, masters seed වෙනවා.

---

## ☁️ GitHub + Streamlit Cloud Deploy

### GitHub එකට push

```bash
cd kpi_system
git init
git add .
git commit -m "EFL KPI system - initial"
git branch -M main
git remote add origin https://github.com/<ඔයාගේ-user>/<repo>.git
git push -u origin main
```

> `.streamlit/secrets.toml` එක `.gitignore` එකේ block කරලා තියෙන නිසා
> push වෙන්නේ නෑ — හරි.

### Streamlit Cloud

1. <https://share.streamlit.io> → **New app** → GitHub repo එක select කරන්න.
2. **Main file path** = `app.py`.
3. **Advanced settings → Secrets** එකට, ඔයාගේ `secrets.toml` content එකම paste කරන්න.
4. **Deploy** → done. 🎉

---

## 📁 File structure

```
kpi_system/
├── app.py            # Streamlit UI (pages 9යි)
├── gsheets.py        # Google Sheets connect + AUTO-CREATE + read/write
├── schema.py         # හැම sheet එකකම headers + incentive rules
├── calc.py           # SMV / Revenue / Utilization / Incentive engine
├── seeds.json        # මුල් Excel masters (T-codes, users, sites...)
├── requirements.txt
├── .gitignore
├── .streamlit/
│   └── secrets.toml.example
└── README.md
```

---

## 🔧 Customize කරන්න

- **අලුත් column / sheet:** `schema.py` එකේ `SHEETS` dict එකට දාන්න — Setup එක
  run කළාම auto-create වෙනවා.
- **Incentive rules වෙනස්:** `schema.py` උඩ තියෙන constants වෙනස් කරන්න.
- **T-code rates update:** 👥 Masters → `TCODE-M` → cell edit → Save.

---

## ⚠️ සටහන්

- OT revenue counted වෙන්නේ TIME එක හරියට select කළොත් විතරයි (NORMAL / OT -N / OT -D).
- මුල් Excel එකේ dates *serial number* (උදා 46174) විදිහට තිබුණා — මේ system එක
  සාමාන්‍ය `YYYY-MM-DD` dates පාවිච්චි කරනවා (clean).
- `OT RECOVERY %` දැනට manual/0 — recovery data source එකක් තිබ්බොත් `calc.compute_incentive`
  එකේ `ot_recovery` dict එකට pass කරන්න පුළුවන්.
