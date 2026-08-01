# DAG-Bot — Club Documentation Automation Bot

Telegram bot for Drushya Animation and Gaming Club.
Generates Event Proposals, Budget Forms, and Final Event Reports as `.docx` + `.pdf`.

---

## Setup

### 1. Bot Token
Already configured in `.env`. To regenerate:
1. Open Telegram → `@BotFather`
2. `/mybots` → select `@DAGClubDocBot` → API Token → Revoke + Generate
3. Paste the new token into `.env`

### 2. Dependencies
```powershell
pip install -r requirements.txt
```

**PDF conversion requires either:**
- Microsoft Word installed (recommended on Windows — used via `docx2pdf`)
- OR LibreOffice (`soffice` on PATH) as fallback

If neither is available, the bot will still deliver the DOCX file.

### 3. Run
```powershell
python bot.py
```

### 4. Run as a Windows Service (NSSM)
1. Install NSSM from https://nssm.cc/download
2. Open an **Administrator** PowerShell in this directory
3. Run `install_service.bat`

Service name: `DAGBot`. Stop with `nssm stop DAGBot`, restart with `nssm restart DAGBot`.

---

## Documents Generated

### 📄 Event Proposal
Club info, coordinator, event details, purpose, description, budget breakdown table, signature placeholder.

### 💰 Budget Form
Revenue table + Expenditure table (Budget vs Actual), auto-calculated surplus/deficit, 3-signatory block.

### 📋 Final Event Report
Step-by-step wizard. The compiled report contains:

1. **Cover page** — club, event title, date, submitted-by
2. **Signed Budget Form** *(only if event had a budget)*
3. **Signed Event Proposal** *(only if event had a budget)*
4. **Event Details** table
5. Approval Mail Screenshot
6. Brochure / Poster
7. Detailed Schedule (table)
8. Registered Participant List (auto-extracted from Excel)
9. Attendance (Morning + Evening photos)
10. Brief Description (Purpose + Description)
11. Sample Certificate Copies
12. Geotagged Photographs (Offline) / Event Screenshots (Online)
13. Winners List
14. Signature block

**Zero-budget events:** The bot asks "Did this event have a budget?". If **No**, the signed-attachment steps are skipped entirely — you get just the event report.

---

## Upload Formats Accepted

| Section | Accepts |
|--|--|
| Signed Budget / Proposal | Image, PDF, DOCX (multi-page PDFs rendered page-by-page) |
| Approval mail | Image, PDF, DOCX |
| Brochure | Image |
| Attendance / certificates / photos | Image |
| Participant list | `.xlsx` (must contain `Name` + `Registration Number` columns) |

---

## Output Format

- Times New Roman, 12pt body, justified
- Every major section starts on a new page
- Images centered with proper aspect-ratio scaling
- Tables with full borders
- DOCX + PDF both delivered in Telegram

---

## Logs
When run as a service: `logs/dagbot.log` and `logs/dagbot_error.log`.
