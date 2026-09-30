# DAGBot Project Log

Running log of changes made to the DAGBot codebase. Newest entries on top.

---

## 2026-09-30 — Page-border overflow, stranded headings, schedule parsing, timeout retry, Back button, Edit & regenerate

Started from three screenshots of a real report (`Report_Wipe_Out_Arena_Reloaded.docx`, 846 participants) and a Telegram "Timed out" error, and grew into a step-back feature and an edit-after-generation path. Every fix below was checked in the live bot (`@DAGClubDocBot`, driven through `dag_user.session`); the layout fixes were also checked inside real Word via COM.

### 1. Page border cut through table rows and text (report)

**Symptom:** the black page frame ran through the last row on a page ("772 J. ROHIT SHREE SAI") and through a line of body text ("Purpose of the Event").

**Root cause:** `_apply_page_borders` draws the frame 24 pt in from the page edge (`offsetFrom="page"`, `space=24`), but the template's bottom margin is 360 twips = 18 pt. Text could run 6 pt *past* the frame. Top/left/right margins (45/45/36 pt) were fine; only the bottom was short.

**Fix (`generators/report_gen.py` → `_apply_page_borders`):** raise the section's bottom margin to at least 720 twips (0.5"). Also moved `<w:pgBorders>` to sit right after `<w:pgMar>` (schema order) instead of the end of `<w:sectPr>`.

**Verified:** rendered PDF, lowest text on a full page is y=734 pt against a frame at about 768 pt.

### 2. Heading stranded at the foot of a page, table on the next

**Symptom:** "3. Registered participant list" + "Total registered: 846" sat alone at the bottom of a page; pressing space pushed them onto the next page *with* the table.

**Root cause:** in `_person_section`'s long-list branch (more than 25 rows, written at body level so the header row can repeat), the heading and the blank under it carried `keepNext`, but the **"Total registered" line and the blank paragraph after it did not**. The chain heading, blank, total, blank, table was broken in the middle, so Word was free to break there.

**Fix:** `_set_keep_with_next` on the total line and the trailing blank paragraph.

**Verified in real Word (COM):** heading, total line, header row and first data row all land on the same page as generated, and stay together after inserting 1 / 3 / 10 / 25 / 40 spaces or 1 / 5 / 15 / 30 blank paragraphs in front of the heading (the whole block just moves to the next page as one unit). No non-breaking spaces or stray `<w:br>` in the document.

### 3. Schedule table came out as garbage

**Symptom:** rows like `Time = "Day 1 (Saturday"`, `Activity = "19 Sep): Round 1"`.

**Root cause:** the handler split *every* line at its first comma, so any prose line pasted into the schedule step became a row split mid-sentence.

**Fix (`handlers/report.py`):** `_split_schedule_line`. A comma only splits a line when the left side is a clock time (`9:00 PM`, `9:00 AM – 9:30 AM`, `9-10 AM`, `14:00`). Lines that aren't `Time, Activity` are rejected and listed back to the user with the expected format; the good lines in the same message are still added.

**Verified live:** a pasted block of 2 prose lines + 3 timed lines added the 3 timed rows and reported the 2 prose lines.

### 4. "Error generating report: Timed out" now offers a retry button

**Root cause of the timeout:** Telegram's default upload timeout (5 s write) on a 1.6 MB DOCX. The file actually arrived, but the exception aborted the handler and told the user to `/start` over.

**Fix:**
- `reply_document(..., write_timeout=180, read_timeout=180)` on every generated-file send in `report.py`, `proposal.py`, `budget.py`.
- New `RETRY_KB` (`handlers/ui.py`): 🔄 Generate again, ✏️ Edit a field, ❌ Cancel. On any generation/send failure the report, proposal and budget confirm handlers now return to their **confirm state** (`R_CONFIRM` / `P_CONFIRM` / `B_CONFIRM`) with that keyboard, so the answers are kept and one tap retries. The exception is logged with a traceback (`logger.exception`).
- Bug caught while testing this: I had added `logger.exception(...)` to `report.py` without defining `logger`, so the retry message would never have been sent. Added `import logging` + `logger`. (Retry path checked with a mocked `TimedOut`: state stays `R_CONFIRM`, buttons present, second attempt succeeds and ends the conversation.)

### 5. ⬅️ Back: step back through a wizard without starting over

New module **`handlers/back.py`**; no wizard handler was edited to support it.

- `track(handler)` wraps every wizard state handler. When a handler moves the conversation to a new state it pushes a checkpoint: state, deep snapshot of `user_data` on arrival, and the prompt that was shown. Entry points wrap with `reset=True` (empty history per flow).
- Back **keeps everything answered up to the step you return to** and discards only the answer to the step you leave, so going back into a list step (schedule, winners, budget items) keeps its items and you just add more or tap Done. Control flags (`_edit_*`) come from the returned-to step's arrival snapshot.
- **Buttons, not commands:** every prompt gets a Back button.
  - Plain / inline-keyboard prompts: an inline `⬅️ Back` (added by editing the message after the transition).
  - Reply-keyboard prompts (Online/Offline, Yes/No, event picker): Telegram refuses to edit an inline button onto a message carrying a reply keyboard ("Message can't be edited"), so `decorate_markup()` (called from `MarkdownSafeBot.send_message`) adds a `⬅️ Back` key row at send time. A wizard's `ReplyKeyboardRemove` becomes a keyboard holding only Back so *that* step has a tap too; `KB_HAS_BACK` tracks whether the Back key is on screen so inline duplicates are skipped.
  - When a wizard ends with the Back key still showing, the bot sends "🏠 What next?" with the main-menu buttons (`MAIN_MENU_ROWS`, now shared with `/start`) so the next move is a tap, not `/start`.
  - `/back` still works, `/back` when idle answers politely, and it is in the command menu.
- Back handlers are inserted **first** in every wizard state (several states take any text or any callback, which would otherwise swallow them).
- State lives in `user_data['_back_hist']` (at most 60 checkpoints), so it survives restarts via PicklePersistence.
- `MarkdownSafeBot` can't take new attributes (PTB freezes bot objects; my first attempt crashed the service at startup with `AttributeError: Attribute 'last_sent' ... can't be set`), so the "last message sent per chat" record is the module-level `LAST_SENT` in `handlers/back.py`.

**Verified live:** Back on reply-keyboard steps, inline steps and plain steps; Back after a **service restart** mid-wizard; `/back` when idle; and a fuzz driver (`_drive_bot_back_fuzz.py`) that runs the whole suite tapping Back and repeating the answer on every 3rd one-answer step. The resulting documents match a normal run in size (`Report_…docx` 78,012–78,013 bytes, `.pdf` 191,187).

### 6. ✏️ Edit & regenerate: fix a saved document without touching Word

**Complaint:** a typo in one document meant downloading it, editing the Word file by hand and re-exporting a PDF.

**Change (`bot.py`):** My Events → event → **✏️ Edit & regenerate** (only shown when the event has a saved proposal/budget/report) → pick the document → its saved answers load into `user_data[flow]` and open on the normal **summary screen**. The existing *Edit a field* menu and *Confirm* then regenerate the DOCX/PDF (new state `EVENTS_REGEN = 84`; each flow's own `*_summary` and confirm state are reused). Uploaded image/Excel paths in the saved data still exist under `temp/`, so nothing needs re-uploading.

**Verified live (buttons only):** open event → Edit & regenerate → Event Proposal → Edit a field → Venue → new value → Confirm → new DOCX + PDF delivered.

### 7. Housekeeping and gotchas learned

- `/events` is now an entry with `reset=True`, so leftover Back history from an abandoned wizard can't leak into it.
- **Telegram flood limit** hits the live drivers hard: back-to-back suite runs produced stale replies and false failures (Budget / Mail / `/events`). The same code passed after a ~4-minute cooldown. Don't read a red suite right after another run as a bot bug.
- `_drive_bot.py` `test_events_listing` now drains straggler replies after `/cancel`: a finished wizard ends with the extra "🏠 What next?" message, which otherwise shifts the driver's reply counting by one. Final state: plain suite 5/5 and the Back-fuzz suite 5/5 (after cooldown).
- Test events created by the drivers (`LiveTest*`) must be removed from `events/5422521708/` after a run (`rm -rf events/5422521708/livetest_*`); the real events (`battle_for_glory`, `explore_the_world`, `ignis_s1`, `pixelhack_2_0`, `wipe_out_arena_reloaded`) were not touched.
- The budget date step accepts any text (a pasted line item became the date in one fuzz run). Pre-existing and not validated; worth a follow-up.
- One layout detail I could not settle from the screenshots: a large blank area above "5. Brief description" on the page after a long participant table. The document XML contains only a normal spacer paragraph there; re-check on a fresh report.

### Files touched

- `generators/report_gen.py`: bottom margin + pgBorders order (§1), keep-with-next on total line/blank (§2)
- `handlers/report.py`: schedule line parsing (§3), retry + write timeouts + logger (§4)
- `handlers/proposal.py`, `handlers/budget.py`: retry + write timeouts (§4)
- `handlers/ui.py`: `RETRY_KB`, `MAIN_MENU_ROWS`
- `handlers/back.py`: **new** (§5)
- `bot.py`: `LAST_SENT` capture + reply-keyboard Back injection in `MarkdownSafeBot.send_message`, tracked entry points and states, `/back` command + idle handler, main-menu "What next?", Edit & regenerate (§6)
- `_drive_bot_fixes.py`, `_drive_bot_back.py`, `_drive_bot_back2.py`, `_drive_bot_back_fuzz.py`, `_drive_bot_back_persist.py`, `_drive_bot_regen.py`: **new** live drivers for the above

---

## 2026-08-01/02 — Repo created; the missing-PDF bug and a full audit; Edit step; report justification

Started from one user report ("only doc?" — a proposal arrived as DOCX with no PDF) and ran to a full pass over the codebase. Everything below was reproduced before being fixed and verified against the live bot after.

### 0. The project is now a private git repo

`https://github.com/ImplosionA12/dagbot` (private). `.gitignore` extended to keep `*.session` (the Pyrogram user session is a live credential), `dagbot_state.pickle`, `logs/`, `events/`, `temp/` and `.claude/settings.local.json` out, with `!templates/**` so the blanket `*.docx`/`*.pdf` rule doesn't swallow the source templates. Two secrets were moved out of tracked code: `_drive_bot.py` had the Telegram API id/hash hardcoded (now `TG_API_ID` / `TG_API_HASH` / `BOT_USERNAME` from `.env`, documented in `.env.example`), and the coordinator's phone number was redacted from this log.

### 1. A ':' in the event name silently cost every PDF

Event `Wipe Out Arena: Reloaded` produced the path `Proposal_Wipe_Out_Arena:_Reloaded.docx`.

**Root cause — NTFS alternate data streams.** Windows reads that colon as a stream separator, so the DOCX content landed in a *stream* on a 0-byte file:

```
Proposal_Wipe_Out_Arena     0 bytes   (:$DATA)
   └─ :_Reloaded.docx      63,260 bytes   ← the whole document
```

python-docx and Telegram follow that path happily, which is why the DOCX still sent at 61.7 KB. Word and LibreOffice both refuse it, so both PDF paths died:

```
14:14:02  docx2pdf failed: 'NoneType' object has no attribute 'SaveAs'
14:14:08  soffice ran but produced no pdf: Error: source file could not be loaded
```

Reproduced exactly by running soffice against the stream path. The generators only ever replaced spaces and `/`, so `< > : " \ | ? *` all passed through.

**Fix** — `safe_filename()` in `generators/file_utils.py`: strips every reserved character, collapses underscore runs, caps length at 80, guards reserved device names (`CON`, `COM1`…). Used by all three generators and `handlers/upload.py` (so signed re-uploads still land beside their originals). Documents are now sent under `os.path.basename(path)` rather than a re-derived name — the mismatch between the two is what kept this invisible.

Note the colon is only a problem in the *filename*; the event name itself still reads `LiveTest_Proposal: 2026` inside the document, as it should.

### 2. Word/Excel COM never works under the service — it was just burning time

`docx2pdf` fails on *every* service-run conversion. NSSM runs the bot as LocalSystem in **session 0**, which has no interactive desktop, so `Documents.Open()` returns None. LibreOffice was doing all the real work while ~5 s per document went to a doomed COM attempt.

**Fix** — `_in_session_zero()` checks `ProcessIdToSessionId` up front and skips COM entirely there; any COM failure anywhere also disables it for the rest of the process. Run from a normal console, Word still gets first refusal. Log now shows one `running as a service (session 0) — using LibreOffice for PDFs` line and no COM attempts. Measured: 7.3 s → 3.3 s per proposal.

### 3. Excel COM leaked a process on every failure

`wb.Close()` / `excel.Quit()` sat in a `finally` that only ran if `Workbooks.Open()` had already succeeded. When Open raised — it did, on `Budget_IGNIS ‘S1.xlsx`, 30-07 — an invisible `EXCEL.EXE` was left running with no handle to it. One orphan per failed conversion. Now both are closed defensively regardless of where it failed.

### 4. A failed conversion shipped the *previous* run's PDF

`docx_to_pdf`/`xlsx_to_pdf` checked `os.path.exists(pdf_path)` without clearing stale output first, so a conversion that failed outright still found last generation's file sitting there and returned it. An edited document would have gone out with the old PDF attached. `_clear_stale()` now removes it before converting; verified that a broken source returns `None` and leaves nothing behind.

Also folded both soffice invocations into one `_soffice_convert()`: absolute outdir, timeout handled separately from other errors, and the produced file moved into place when its name doesn't match the requested path.

### 5. One stray underscore could kill a wizard step

113 replies use `parse_mode="Markdown"` and interpolate text the user typed; only 4 sites escaped anything. A single unmatched `_` — `first_prize` is enough — makes Telegram reject the whole message with `Can't parse entities`. The exception then aborted the handler *mid-step*: no summary, no keyboard, and the conversation stuck in a state the user couldn't see.

**Fix** — `MarkdownSafeBot(ExtBot)` in `bot.py` overrides `send_message`, catches that specific `BadRequest` and resends with `parse_mode=None`. One subclass covers every existing and future call site, since `reply_text` funnels through `Bot.send_message`. Verified against the live API: plain `ExtBot` raises, the subclass delivers. `md_escape` also moved to `handlers/ui.py` and applied to the values in all three summaries and their echo messages, so formatting survives too rather than degrading to plain text.

### 6. Stale inline buttons crashed the upload steps

`_download_upload` read `update.message.photo` directly. Every upload step *also* carries inline buttons, and a stale one from an earlier step (callback_data that isn't the `done` the caller checked for) arrives with `update.message = None`. Guarded.

### 7. Document-only states told users their session had expired

`R_PARTICIPANTS`, `R_ATTENDANCE_FILE` and `R_BROCHURE` registered only `filters.Document.ALL` / `PHOTO`. Typing anything there left the update unclaimed by the conversation, so it fell through to the global catch-all: *"That menu is from an earlier session… send /start"* — mid-report, with every upload already done. Each state now also routes TEXT to its own handler, which already had the right "please send an Excel/image" re-prompt.

### 8. No error handler was registered at all

PTB logged `No error handlers are registered, logging exception`. Any unexpected exception in a handler was logged and swallowed, leaving the user with silence — the same failure mode as #1, one layer up. `on_error` (module-level in `bot.py`, so it's testable) logs with traceback and tells the user the step wasn't saved. Network-level errors arrive with no update and stay silent.

### 9. `save_event` could truncate an event

Direct write to `event.json`. A crash or restart partway through left unparseable JSON, and every reader treats that as "no such event" — the whole event, form data included, silently gone. Now writes to `event.json.tmp`, fsyncs, and `os.replace()`s. `_event_artifacts` skips `event.json*` so a leftover temp is never offered as a document.

### 10. Budget C5 was missing the phone its own label asks for

The cell is labelled *"Name and phone number of the Coordinator"* and only ever received the name — `COORDINATOR_PHONE` became a separate config value and nothing joined it back. `Budget_IGNIS ‘S1.xlsx` has `C5 = "Dr. Shaik Kareemulla"`.

Worth recording *how this was caught twice*: the generator fix passed a direct test (which supplies `fixed_profile()`) but the live budget PDF still showed the name alone — `budget_start` never put `coordinator_phone` into `user_data`. Only the end-to-end run exposed it. C5 now reads `Dr. Shaik Kareemulla, 99084 56052`; the signature block still shows the name alone via the existing `.split(',')[0]`, and the form is still one page.

### 11. Smaller ones

- Upload extensions came from the phone's filename straight into a path; now scrubbed to alphanumerics, max 8 chars (same class of bug as #1).
- The proposal prints the typed Total Budget *and* the sum of the items, in two places, with nothing keeping them in agreement. The summary now flags a mismatch before generation.
- `httpx` logs every `getUpdates` at INFO — that is ~99% of the 213 MB in `logs/` and what buried the PDF warnings above. Noted, not yet changed.

### 12. New: Edit a field from any summary

Every wizard ended on Confirm / Cancel, so one wrong date cost the entire form — for the report, that meant re-uploading every screenshot, certificate and Excel sheet. The answers are already in `user_data` at that point.

`handlers/edit_fields.py` adds a generic editor, wired into all three summaries via `CONFIRM_EDIT_KB`:

- **Plain fields** are asked for and stored, then the same summary is redrawn.
- **List fields** (budget items, revenue, expenditure, schedule, winners) reuse the wizard state that already collects them; that state's Done branch calls `resume_after_edit()` and returns to the summary instead of walking the rest of the wizard. Editing revenue no longer re-asks expenditure.
- The current value is shown in its own code block. Showing it inline invited copy-the-whole-message-and-edit — which is how `ignis_s1`'s proposal ended up with `description` starting *"Current Brief Description (2721 characters):"* and `purpose` set to the literal string `"None"`. `_strip_echo()` drops such a header if it comes back.
- `mode` is validated against Online/Offline instead of accepting free text.

Editable: proposal — name, venue, date, duration, total budget, extra, purpose, description, items. Budget — name, date, club, coordinator, emp ID, revenue, expenditure. Report — name, mode, venue, date, purpose, description, speaker bio, feedback link, schedule, winners.

### 13. The "uneven sentences" in the report were the schedule, not the prose

Purpose and description were already justified. The ragged block on page 8 of `Report_IGNIS ‘S1.pdf` came from two compounding bugs:

1. `report_schedule` split the **whole message** on its first comma, so a pasted multi-line schedule became a single row whose "Time" was the entire paste (and it was pasted twice, giving two identical rows).
2. That text went into the cell through `_run()`, and python-docx turns every `\n` in a run into `<w:br/>` — the documented soft-break trap, still live for table cells. Result: one cell holding a wall of soft-broken, unjustified text.

**Fix** — `_cell_text()` writes one paragraph per line (same rule the body text follows) and justifies anything over 60 characters so cell edges line up with the prose sections; single-line cells keep the table's existing spacing, so tables that were already fine don't shift. `report_schedule` now takes one entry per line like the winners list, skips exact duplicates, and names unparseable lines individually.

Measured on the IGNIS report: soft breaks **50 → 5** (the 5 are the template's own cover), paragraphs containing newlines **5 → 0**, long unjustified paragraphs **5 → 0**.

### Verification

Everything above was driven through Telegram against the service running the committed code, not just unit-tested:

- Full driver (`_drive_bot.py`): proposal, budget, mail prompt, report, `/events` — 5/5, three separate runs. Its proposal now deliberately uses `LiveTest_Proposal: 2026` (colon *and* underscore) so both #1 and #5 stay covered.
- **New** `_drive_bot_budget_yes.py`: the existing driver always answers `has_budget=No`, so the signed Budget Form, signed Event Proposal and the conditional Expenditure Statement had never been exercised end to end. That branch now has its own driver, and it also takes the Online path so the "Event Screenshots" heading gets used.
- Edit flow: checked the *documents*, not the summaries — proposal carried the new date and `Trophies`/`1500` with no trace of `Posters`; budget carried `Sponsorship` with `Prizes` intact; report carried the rewritten purpose.
- Formatting: re-measured on a report the **bot** produced (three long schedule lines pasted as one message) — 3 rows, 0 paragraphs with newlines, 5/5 long paragraphs justified, 2 soft breaks (template cover only).
- Section order confirmed against the Form3 spec, including that the Signed Budget Form is appended last and unframed rather than sitting near the front.

### Still open

- No backup of `events/` anywhere — it holds every generated document *and* the data to regenerate them, on one machine, gitignored.
- No way to edit a saved event after generation (the Edit step above only covers a form still in progress).
- Dates are never validated; `24-05-20226` would reach a submitted document.
- `temp/` grows forever; `logs/` is 213 MB, mostly httpx INFO.
- Budget revenue copies budgeted qty/rate into actual, so actual revenue can't differ from planned. Expenditure asks for both.
- `ALLOWED_USER_ID` hard-locks the bot to one account; storage is already per-user, only the filter blocks a handover.

---

## 2026-07-27 — Budget date overflow, Budget as Word doc, stale keyboards, grouped delete, share-a-file

Five things in one session, all verified against the live bot.

### 1. "Event Date:" overflowed its cell in every Budget PDF

User report: the event date looked broken in `Budget_IGNIS ‘S1.pdf`. Rendered the PDF with PyMuPDF and read the source XLSX with openpyxl to measure it.

**Root cause — the label, not the value.** `Event Date:` renders 51.8 pt wide but column G is 48.3 pt (width `8.71`, Calibri 11). H7 is empty, so Excel/LibreOffice spill the text into H — straight through the thin border drawn between them. The gridline visibly cut through the word "Date". The value `25-07-2026` (x 484.9→536.1) did fit inside column I (481.4→541.6), with ~5 pt to spare. It lives in `templates/Final_Budget_Form.xlsx`, so **every** budget PDF had it.

**Fix** (`generators/budget_gen.py`, next to the existing `C5:I5` / `C6:I6` repairs — the template itself stays read-only):
- `_ensure_merge(ws, 'G7:H7')` — ~90 pt for the label, and merging removes the gridline it was crossing.
- Wrap on `I7`, folded into row 7's height as `max(name_lines, date_lines)`. Column I's right edge *is* the table's outer border, so a multi-day value like `25-07-2026 to 27-07-2026` had nothing to spill into and would have hung outside the form. Now it wraps inside the cell. `fitToWidth/fitToHeight` keep it one page.

Verified: both short and long dates render at 1 page, label clear of the boundary.

### 2. Budget is now delivered as .docx too

LibreOffice **refuses Calc → Writer** (`--convert-to docx` on an .xlsx dies with `Error Area:Io Class:Write`), and Word can't open an .xlsx, so `xlsx_to_docx()` in `generators/file_utils.py` rebuilds the sheet as a Word table: same cells, merges, borders, column widths, fonts and the VIT-AP logo.

Two things that needed handling:
- **openpyxl writes formulas with no cached result**, so the four `=SUM()` totals would have come out blank — only Excel/LO fill those in on open. `_eval_sum()` evaluates them, `_cell_display()` applies the accounting format (`5,000`, `-` for zero).
- **Word's default cell padding** (0.08" a side) is room Excel doesn't spend, so text that fit on one line wrapped here and pushed the form to a second page. Trimmed via `tblCellMar`.

`generate_budget()` now returns `(xlsx, docx, pdf)`; `handlers/budget.py` sends all three.

**Known cosmetic caveat:** Word renders it as 1 page (verified via `docx_to_pdf` → Word COM). **LibreOffice** shows a trailing blank page — its handling of the mandatory paragraph after a table. Content is identical and page 1 is complete. Only bites if you open the .docx in LO rather than Word.

### 3. Stale reply keyboards did nothing at all

User report: tapped `📂 IGNIS ‘S1`, got no response; `/start` a minute later worked.

**Root cause.** The bot had restarted at 00:24:47. `Application.builder()` had no persistence, so conversation state was memory-only and died with the process. Telegram reply keyboards persist on the *client* across restarts, so the tap sent plain text matching no state — and the only handler outside the conversation was `deny_unauthorized`, filtered to `~auth_filter`. An **authorized** unmatched message hit nothing and the bot stayed silent. Log confirms: `getUpdates` at 00:49:21 returned an update with no following `sendMessage`; the `/start` at 00:50:10 was followed by `sendMessage` at 00:50:11 (it's an entry point, so it needs no prior state).

**Fix** (`bot.py`):
- `PicklePersistence` (`dagbot_state.pickle`, gitignored) with `persistent=True` on both conversations. **`update_interval=10`** on purpose: NSSM stops the service by killing the process, so PTB never flushes on shutdown, and the 60 s default would throw away a minute of state on every restart — exactly what this is here to prevent.
- Exact main-menu labels as entry points, so a stale main-menu tap re-enters.
- A catch-all `MessageHandler(auth_filter, …)` + `CallbackQueryHandler` registered last in group 0: an unmatched message now says the menu is from an earlier session instead of vanishing.

Verified end-to-end: opened `/events`, restarted the service, tapped the button drawn *before* the restart → detail view opened.

**Gotcha worth remembering** (also in `memory/conversation_entry_point_gotcha.md`): with `allow_reentry=True`, PTB matches `entry_points` **before** the active state's handlers. A `^📂 ` regex entry point was written first and then removed — it hijacked the budget/proposal wizards' own "📂 <event>" pick keyboards. Only exact `$`-anchored main-menu labels are safe.

**Regression found and fixed during testing:** `/cancel` with nothing running used to be silent (the fallback only fires inside a conversation), so it fell to the new catch-all and got the wrong message. Added an `idle_cancel` `CommandHandler` before the catch-all. `_drive_bot.py` assumed that silence in 5 places (`expect_reply=False`) and shifted a message behind, which cascaded into false Budget/Report/events failures — updated to consume the reply.

### 4. Delete menu groups by document, not by format

User report: deleting should show Proposal / Budget, not every Word/PDF/Excel variant. `_group_artifacts()` in `bot.py` buckets an event's files by generator prefix; `_files_keyboard()` renders one row per document (`🗑 💰 Budget Form · 3 file(s)`), and `delkind:<kind>` deletes the whole group. Unmatched files fall under "📎 Other files". Saved form data still stays, so documents remain regenerable.

### 5. Share a file → file it under an event (new `handlers/upload.py`)

Send any document or photo outside a flow: *"Does this belong to one of your events?"* → Yes → existing events + ➕ New event → Proposal / Budget / Report → if that document already exists, the prompt spells out what gets replaced and what's kept, with Replace/Cancel. Saved under the generators' naming (`Budget_IGNIS_‘S1.pdf`) so a signed scan lands beside its original, and recorded in `event.json` under `uploads` (surfaced in the detail view as "📤 Attached by you").

Report is offered alongside Proposal and Budget even though the request said "budget or proposal" — the bot generates all three, so leaving Report out would make report scans unfileable.

**Registered as its own `ConversationHandler`, after the main one**, precisely so the uploads the report wizard is waiting for still reach the wizard (see the entry-point gotcha above). Confirmed by the full suite's report test passing.

### Verification

- New behaviour: 10/10 checks via a live Pyrogram driver (share → new event → kind → save; same kind again → replace prompt → cancel keeps files; grouped delete menu; group delete removes its files). Throwaway `UploadTest 2026` event used throughout.
- Existing suite: 5/5 (`Proposal`, `Budget`, `Mail Prompt`, `Report (uploads)`, `/events listing`) after the `/cancel` fix.
- Test events (`livetest_*`, `uploadtest_2026`) cleaned up afterwards.

### Notes on existing files

- `events/…/ignis_s1/` Budget files were **regenerated** with the date fix (+ new .docx) at the user's request. `event.json` and `Proposal_*` untouched by that step.
- That PDF grew 80 KB → 214 KB: this run went through **Excel COM** (Word/Excel available in the user session), which embeds the logo at higher fidelity than the LibreOffice fallback. Under the service it'll likely fall back to LO again and land near 80 KB.
- `Proposal_IGNIS_‘S1.docx/.pdf` are gone — **the user deleted them at 01:29** while exploring the old per-file delete menu (bot log: 5 × `sendDocument` at 01:28, then two `answerCallbackQuery` + `editMessageReplyMarkup` at 01:29:13 and 01:29:18, matching the folder mtime). Not a bug. The proposal form data is intact, so `/proposal` → pick IGNIS regenerates them.

### Files touched

- `generators/budget_gen.py` — `G7:H7` merge, `I7` wrap + shared row-7 height, returns `(xlsx, docx, pdf)`
- `generators/file_utils.py` — new `xlsx_to_docx()` + `_eval_sum` / `_cell_display` / `_col_width_inches` / `_apply_borders` helpers; `re` import
- `handlers/upload.py` — **new**, the whole share-a-file flow
- `handlers/budget.py` — sends the .docx alongside xlsx + pdf
- `bot.py` — persistence, upload conversation, catch-alls, `idle_cancel`, `_group_artifacts` + grouped `_files_keyboard` + `delkind:` handling, uploads line in the detail view
- `_drive_bot.py` — consumes the `/cancel` reply (5 places)
- `.gitignore` — `dagbot_state.pickle`

## 2026-07-19 (later still) — Editable prose no longer trapped in cantSplit blocks

User report: opened a generated report in Word, pasted a longer description, and the whole section jumped to the next page leaving an empty gap that backspace couldn't remove.

### Root cause
`report_gen.py` wraps every section in an invisible 1-cell `cantSplit` sub-table (`_section_block`) so headings never separate from content. That's right for images/tables, but wrong for **prose the user edits later**: a cantSplit block that outgrows the remaining page space moves to the next page *as a whole*, and the gap it leaves is pagination, not characters — backspace can't touch it.

### Fix
- **Section 5 (Purpose + Brief Description)** and **section 9's speaker bio** now go into plain body paragraphs that split across pages naturally. Headings/labels keep with their first text line via `keep_with_next`; a spacer paragraph preserves the inter-section breathing room. Speaker *photos* still go in a cantSplit block.
- Verified: markers audit shows all prose paragraphs are body-level (`inside-table-cell=False`, no keepNext/keepLines/pageBreakBefore) in both report and proposal; rendered PDF layout unchanged. Service restarted.

### Note for old files
Artifacts generated before the 2026-05 soft-break fix (e.g. `Proposal_Battle_For_Glory.docx`, 4 `<w:br/>` soft breaks) still contain un-deletable gaps when edited. The wizard data is saved — re-running the flow for that event regenerates a clean file.

## 2026-07-19 (later) — DOCX outputs open cleanly in Word (no compat banner, no invisible junk)

User report: generated docs should open in Word immediately editable, with no invisible characters or "restrictions". Audited templates + outputs by unzipping the DOCX XML.

### Findings
- **No `documentProtection` / `readOnlyRecommended` / track-changes anywhere** — templates and outputs were never actually locked. What the user most likely sees on top of the fixes below is Word's *Protected View* ("Enable Editing" yellow bar), which Windows applies to any file downloaded from Telegram (Mark-of-the-Web) — not fixable from the generator side.
- **`templates/report_converted.docx` is stuck at `compatibilityMode=11`** (it was converted from a legacy .doc) → every generated report opened with the "[Compatibility Mode]" title-bar banner and old-Word behaviour. Proposal template was already 15.
- Report template text also carried 1 NBSP and many runs with trailing whitespace.
- Old artifacts generated before the 2026-05 soft-break fix (e.g. Battle For Glory) still contain `<w:br/>` soft breaks; freshly generated files are clean.

### Fix
- New `modernize_docx(doc)` in `generators/file_utils.py`, called at save time in `proposal_gen.py` + `report_gen.py` (next to `stamp_docx_author` / `disable_soft_return_justify`):
  - removes `w:documentProtection` / `w:readOnlyRecommended` if a template ever carries them;
  - stamps `compatibilityMode=15` (native Word, no banner);
  - NBSP → plain space in body text; trims trailing whitespace from each paragraph's last run.
- Deliberately NOT touched: leading-space indentation of template lines like "Sub: Event Proposal…" (intentional layout), and the `<w:br/>` inside the logo drawing (template artwork).
- Verified: generated fresh proposal + report; XML scan shows compat 15 / no NBSP / no protection; rendered PDFs page-by-page — layout unchanged (cover page, info block, schedule table all intact). Service restarted.

## 2026-07-19 — Per-file delete in /events + delete confirmation + events UX fixes

User report: no way to delete a generated file from a saved event ("no delete action button"), plus assorted small glitches. All changes in `bot.py`; verified live (service restarted, 10/10 checks in a focused Pyrogram driver: file list → delete one file → back → Keep-it → confirmed event delete → folder gone from disk).

### New
- **🗑 Delete a file** in the event detail view (new `EVENTS_FILES` state, inline buttons `delfile:<idx>` via the `resolve_text()` pattern; filenames >40 chars truncated in labels; callback_data stays index-based so long names never hit the 64-byte limit). Deleting a file keeps `event.json`, so the doc can be regenerated — the button prompt says so.
- **Confirmation before "Delete this event"** (new `EVENTS_DELETE` state, `delev:yes/no` inline buttons). Previously one tap silently nuked the whole folder. The warning states how many generated files will go with it.

### Fixed (minute glitches)
- **Markdown breakage**: event names/venues with `_ * ` [` broke `parse_mode="Markdown"` rendering in the events list and detail view → new `md_escape()` applied to user-provided strings there.
- **Download files kicked you out**: after sending the files the conversation `END`ed; now it returns to the event detail view. Detail view also shows a `📎 Generated files: N` count now.
- **`_show_main_menu`/events handlers used `update.message`**, which is `None` on callback-button paths → switched to `update.effective_message` (matches the inline-buttons convention in `handlers/ui.py`).

### Refactor
- Detail view extracted into `_show_event_detail()` (re-showable after downloads/deletes); artifact listing into `_event_artifacts()`.

### Files touched
- `bot.py` — everything above; new states `EVENTS_FILES, EVENTS_DELETE = 82, 83` registered with `MessageHandler + CallbackQueryHandler`.

## 2026-07-16 (evening) — Same-class fixes audited/applied to Report + Budget formats

Audited the other two document formats for the issue classes fixed in the proposal earlier today.

### Report (report_gen.py) — mostly already fine
- Purpose/description/speaker-bio were already justified; no blank pages; no leftover-template spill (checked a fully-loaded 13-page report).
- Fixed: blank lines in user text created empty paragraphs — `_para` now drops them (paragraph spacing already separates paragraphs). `_para(doc)`/`_para(doc, '')` spacer behaviour unchanged.

### Budget (budget_gen.py + file_utils.xlsx_to_pdf) — real clipping bugs found (rendered the PDF to PNG to see them)
- **Long event name clipped**: the B7:E7 merge is ~45 chars wide and Excel never auto-fits merged cells → wrap_text + explicit `row_dimensions[7].height = 16 * ceil(len/45)` when >1 line.
- **Long particulars clipped by the Qty column** (both tables): column B is ~22 chars wide → wrap_text + explicit row heights via `_fit_particulars_row` (`15 * ceil(len/20)`), because Excel does NOT auto-fit wrapped rows at COM export either (verified: wrap alone left the 2nd line hidden).
- **7th expenditure row unnumbered**: template pre-fills Sl only for rows 19–24 → Sl now written explicitly for used expenditure rows.
- **Page spill**: taller wrapped rows pushed the form to 2 pages → `fitToPage/fitToWidth=1/fitToHeight=1` in sheet page-setup (honored by LibreOffice too) and `FitToPagesTall = 1` in the Excel COM export (was `False`). The form is a one-pager by design.
- Verified: stress case (59-char event name, 5×long revenue + 7×long expenditure) renders 1 page, everything visible; normal case unchanged.

### Live testing note (driver, not bot)
After many runs in one day Telegram flood-limits `messages.GetHistory` (18s waits) — the driver's 0.3s polling then reads stale replies and desyncs (symptom: every reply lags one step, or 'Please pick one from the menu' loops). Two runs "failed" this way with zero bot defects. Remedy: wait a few minutes, poll every ~3s with longer timeouts — then Budget and Report both PASS live.

---

## 2026-07-16 (later) — Justified text, blank-page fix, page-overflow guard in proposal

### 1. Purpose / Brief Description now justified

`generators/proposal_gen.py` — new `_set_body_text()` wraps `_set_para_text` (which now returns the written paragraphs) and sets `WD_ALIGN_PARAGRAPH.JUSTIFY` on each. Safe because soft breaks were eliminated earlier today and `doNotExpandShiftReturn` is set. Blank lines in user text are dropped (default paragraph spacing separates paragraphs visually) to save page-1 space.

### 2. Blank page between page 1 and the Budget page

**Root cause:** the old code *blanked* the template's sample-description paragraphs instead of deleting them, and the template ships extra empty spacer paragraphs — 6 empty paragraphs sat between the description and the `pageBreakBefore`'d Budget Breakdown heading. With longer user text those spilled over and rendered as an empty page. Fix: new `_delete_following()` removes leftover sample text and blank spacers after the purpose block (empties only, stop at 'Brief Description') and after the description block (everything up to 'Budget Breakdown' / 'Digital signature').

### 3. Page-overflow guard in the proposal wizard

The proposal should be exactly 2 pages. After generation, `_proposal_overflow()` (handlers/proposal.py) opens the produced PDF with PyMuPDF and finds which page 'Budget Breakdown' starts on. If it's page 3+, the user text spilled off page 1: the bot reports how many characters spilled and offers inline buttons — 📤 Send anyway / ✏️ Shorten purpose / ✏️ Shorten description (`OVERFLOW_KB` in handlers/ui.py). Shorten shows the current text + char count, takes a replacement, and regenerates (loops until it fits or the user sends anyway). New states `P_OVERFLOW`, `P_EDIT_PURPOSE`, `P_EDIT_DESC` (state range extended to `range(100, 116)`); `proposal_confirm` split into `_generate_proposal_docs` + `_send_proposal_files`. If PDF conversion failed, the guard is skipped and the DOCX is sent as before.

### Testing

Live via user session: long description → warning "about 1949 characters spilled onto page 2 … 3 pages" with the 3 buttons → tapped Shorten description → sent short text → 2-page doc delivered. Downloaded DOCX verified: all user paragraphs justified (`w:jc val="both"`), zero empty paragraphs before Budget Breakdown. Short-text run confirmed no false-positive warning. Test events cleaned up. Service restarted (note: one `nssm restart` left the service STOPPED — had to `nssm start` separately; check `sc query DAGBot` after restarting).

---

## 2026-07-16 — Fix un-deletable spacing in DOCX, fix author metadata, inline buttons, command menu

Three user-reported issues fixed, all verified live against the running bot.

### 1. "Invisible spacing" when editing generated Word files

**Root cause:** newlines in wizard text (purpose/description) were written through
`run.text`, which python-docx converts to `<w:br/>` *soft line breaks*. In Word a
soft break freezes the line wrap — editing the line leaves gaps you can't delete
without removing whole words, and in justified paragraphs (report) Word stretches
those lines to full page width.

- **`generators/proposal_gen.py`** — `_set_para_text` now splits input on `\n` and emits one real `<w:p>` per line (cloning the anchor paragraph to keep formatting).
- **`generators/report_gen.py`** — `_para` same treatment; `_set_para_text` flattens newlines to spaces (placeholders are single-line fields).
- **`generators/file_utils.py`** — new `disable_soft_return_justify()` adds the `w:doNotExpandShiftReturn` compat flag as belt-and-braces so any remaining soft break can't be stretch-justified.

### 2. Wrong document author ("K.KARTHIKEYA" from the template)

Template files are copied byte-for-byte, so `docProps/core.xml` kept the original
sample's creator.

- **`config.py`** — new `DOC_AUTHOR` (env-overridable, defaults to `CLUB_NAME`).
- **`generators/file_utils.py`** — `stamp_docx_author(doc)` / `stamp_xlsx_author(wb)` set creator + lastModifiedBy + fresh created/modified timestamps. Called before save in proposal, report, and budget generators.

### 3. Inline buttons instead of typing "done"

- **`handlers/ui.py`** (new) — `DONE_KB`, `DONE_NONE_KB`, `NONE_KB`, `CONFIRM_KB`, `SKIP_KB` inline keyboards + `resolve_text(update)` which returns typed text OR the pressed button's callback_data (and strips the buttons after the press). Typing "done"/"confirm" still works everywhere.
- **`handlers/proposal.py` / `budget.py` / `report.py` / `mail_prompt.py`** — every "type done/none/confirm/skip" prompt now carries the matching inline keyboard; handlers read input via `resolve_text` and reply via `update.effective_message` (needed for button-press updates); `CallbackQueryHandler`s added to the affected states. Reply-keyboard menus (Yes/No, event picker, main menu) are untouched.
- **`bot.py`** — `post_init` registers the command menu via `set_my_commands`; new direct entry points `/proposal`, `/budget`, `/report`, `/mail` (plus existing `/start`, `/events`, `/cancel`).

### Testing

- `_drive_bot.py` full regression: 5/5 pass (after fixing the mail-prompt check — Telegram converts ``` to a PRE entity, so backticks never appear in `message.text`).
- New scratchpad button test: drove the proposal flow by *tapping* None/Done/Confirm via Pyrogram `message.click()`, multi-line purpose/description, then verified the downloaded DOCX: creator = club, one `<w:p>` per line, zero soft breaks, compat flag present.
- Test events cleaned from `events/5422521708/` (only `battle_for_glory` kept).
- Service restarted twice via elevated `nssm restart DAGBot` (11:32 and 11:39).

---

## 2026-05-25 — Lock bot to a single Telegram user

The bot had no access control — anyone who found `@DAGClubDocBot` could use it, and every document they generated would carry the DAG Club's coordinator name + emp ID (since `config.fixed_profile()` is global). Locked it down to the owner's Telegram ID.

### What changed
- **`config.py`** — added `ALLOWED_USER_ID = int(os.getenv('ALLOWED_USER_ID', '5422521708'))`. Defaults to the owner's ID; overridable via env.
- **`.env`** — added `ALLOWED_USER_ID=5422521708`.
- **`bot.py`** —
  - Imported `ALLOWED_USER_ID` from config.
  - Built `auth_filter = filters.User(user_id=ALLOWED_USER_ID)` inside `main()`.
  - Passed `filters=auth_filter` to both `CommandHandler` entry points (`/start`, `/events`) on the `ConversationHandler`. The conversation now never starts for anyone else.
  - Added a catch-all `MessageHandler(~auth_filter, deny_unauthorized)` registered *after* the conv handler. Unauthorized users get a single reply: `🔒 This bot is private. Access is restricted to its owner.`
  - Startup log now reports `DAG Bot started (locked to user_id=…)`.

### Why this approach
- `filters.User` is the native PTB v20 gate — cleaner than per-handler `if update.effective_user.id != …` checks scattered through every handler module.
- Gating only the entry points means the per-state handlers (`PROPOSAL_STATES`, `BUDGET_STATES`, etc.) don't need touching — a non-owner can't enter the conversation in the first place, so its state handlers are unreachable for them.
- The catch-all reject lives outside the conv handler at a default group, so it doesn't interfere with the owner's flows. It only fires for messages whose user fails `auth_filter`.

### Verification
- `python -c "import ast; ast.parse(...)"` on `bot.py` + `config.py` → OK.
- `nssm restart DAGBot` (via UAC) → `SERVICE_RUNNING`. Lockdown is live in the deployed service.

### To grant access to additional users later
`filters.User(user_id=…)` accepts an int or a sequence. Change `.env` to `ALLOWED_USER_ID=5422521708,<other_id>` and parse it into a tuple in `config.py` (e.g. `tuple(int(x) for x in os.getenv('ALLOWED_USER_ID').split(','))`). Today's code only handles a single int.

---

## 2026-05-25 — Layout overhaul, event-wise storage, file-pull via Telegram

Long session. Started broken (service running stale code, no PDFs), ended with the bot fully polished and event-grouped.

### What changed

**PDF generation under the LocalSystem service**
- `docx2pdf` (Word COM) fails under LocalSystem with `'NoneType' object has no attribute 'SaveAs'`. The user didn't want to switch the service to run as `mrrut` (would require typing the Windows password into NSSM).
- Installed **LibreOffice 26.2.3** via winget. `soffice.exe` lives at `C:\Program Files\LibreOffice\program\soffice.exe` and is not on PATH, so `generators/file_utils.py` got a `_find_soffice()` helper with a hardcoded fallback list. Service stays on LocalSystem; PDFs now generate via the soffice subprocess fallback.

**Report layout (`generators/report_gen.py`) — big rewrite**
1. **Logo**: `LOGO_CANDIDATES` list finds `DAG logo.jpg` (or `club_logo.{png,jpg,jpeg}`) in `templates/`. Inserted into the first empty paragraph *after* the club name (P3 area) so the existing VIT-AP VML logo at P0 is **preserved**, not overwritten.
2. **REPORT page break**: explicit `<w:br w:type="page"/>` run prepended inside the REPORT heading paragraph. `pageBreakBefore` paragraph property is silently dropped by LibreOffice inside nested table cells; an inline break run in a non-empty paragraph is the only reliable mechanism.
3. **Template trim**: `_delete_checklist` now starts deleting from "Approval mail screenshot:" onwards (was "Add these in your Report"). Whitespace collapsed before prefix match (template had `"Add these  in your Report"` with a double space, so the old check was a no-op).
4. **Empty trailing rows + fixed row height**: the template's outer table has 3 rows; rows 1–2 are blank and row 0 has `trHeight=14199` (~9.86" — forces row to span a full page). Both get stripped, otherwise the cover row trails 2–3 blank pages before sections start.
5. **Sections in document body, NOT inside the cover cell.** Iterated many times: putting sections inside the cover cell with `<w:br w:type="page"/>` standalone paragraphs → LibreOffice drops most of them (12 page breaks in XML → 9 pages rendered). Moved every section to `doc.add_paragraph()` / `doc.add_table()` in the body, where page breaks fire reliably.
6. **Page borders** added at section properties (`<w:pgBorders>`) so every page — cover, REPORT, and body sections — gets a single black box border. The cover table's own border is stripped (`_remove_outer_table_border`) so we don't double-border.
7. **Each section wrapped in an invisible `cantSplit` sub-table** (`_section_block`). 1 row × 1 cell, borders=nil, `cantSplit` on the row. This keeps heading + content welded together when paginating: small sections share a page if they fit, large ones move whole to the next page. No orphan headings.
8. **14pt spacer paragraph** appended after each block for visual breathing room between sections.
9. **No signature block** at the bottom of report — institute sample has one inside the expenditure section but DAG club doesn't use it; removed.

**Report wizard (`handlers/report.py`)**
- Section 10 (Feedback) flipped from file upload to **link/URL**. New state `R_FEEDBACK_LINK` (was `R_FEEDBACK_FILES`). Data key changed from `feedback_files` to `feedback_link`. Generator renders the URL as text in the report.
- Mode → Venue → Date chain now **skips** any field already prefilled from a picker (`_ask_has_budget` helper extracted).

**Budget (`generators/budget_gen.py` + the XLSX)**
- Coordinator value cell (C5) was too narrow — text "Dr. Shaik Kareemulla, &lt;phone&gt;" got truncated to "ik Kareemulla". Now merges **C5:I5** before writing, similar for C6:I6 (Emp ID).
- Surplus row merge extended **E28:I28** (was E28:H28) so the longer "Surplus: Budget Rs. X | Actual Rs. Y" string fits.
- Signature block: A31 keeps the literal label `"Event Co-ordinator"`, prof name goes in **B31 adjacent** (not appended to A31 with `\n`). Matches the rest of the sig block's `label-in-A / value-in-B` pattern.
- New `_ensure_merge()` helper — merges a range if it isn't already merged (idempotent).

**Proposal (`generators/proposal_gen.py`)**
- `_force_page_break_before(paragraph)` added — sets `<w:pageBreakBefore/>` on a body paragraph. Used on the "Budget Breakdown:" heading so it can never be on a different page from the budget table that follows it. (`keepNext` alone isn't reliable when followed by a table.)

**Per-event storage (`event_store.py`) — folder-per-event refactor**
- Layout moved from `events/<user>/<slug>.json` (flat) to `events/<user>/<slug>/event.json` + all generated artifacts in the same folder.
- `event_folder(user_id, slug)` helper returns the path. Generators (`generate_proposal`, `generate_budget`, `generate_report`) now accept `output_dir=`. Handlers pass `event_folder(user_id, slugify(event_name))` so artifacts land next to the JSON.
- **Auto-migration**: `_migrate_flat_jsons()` runs on every `list_events()` / `get_event()` — moves any legacy `<slug>.json` into `<slug>/event.json`. Idempotent.

**Event picker at start of each wizard**
- Proposal, Budget, Report each got a new initial picker state (`P_PICK`, `B_PICK`, `R_PICK`). Mirrors the existing mail-prompt picker.
- If the user has saved events, the picker shows up to 8 with `📂 ` prefix plus a `➕ New event` option.
- Picking existing: prefills `event_name`, `venue`, `date` (best-effort from any prior flow on that event), then jumps the wizard past those steps. Picking "New event" continues to the event-name prompt as before.
- All three flows now upsert into the same `event.json` if the same name is reused, so one event ends up with all four artifacts (proposal + budget + report + mail prompt) in one folder.

**`/events` got a Download Files button (`bot.py`)**
- Event detail view now has three buttons: `📥 Download files`, `🗑 Delete this event`, `⬅️ Back to events`.
- Tapping Download iterates the event folder, sends every file (skipping `event.json`) back as Telegram documents.
- `delete_event` updated to remove the whole folder (`shutil.rmtree`), not just the JSON.

**Drive test (`_drive_bot.py`)**
- `_skip_event_picker_if_shown()` added — after sending a flow trigger ("📄 Event Proposal" etc.), checks the bot reply for "Pick an existing event" and sends `➕ New event` if needed. Otherwise the test would fail because old events from previous runs make the picker appear.
- Feedback step changed from sending `feedback_img` photo to sending a link string.

**Cleanup**
- Cleared the four test events (`events/5422521708/livetest_*/`) and ~105 stale artifacts from `temp/`. Only `temp/live_test_assets/` (test fixtures) remains. Bot is in fresh-install state.

### Service ops headaches worth remembering

- The harness's PowerShell tool runs `Start-Process -Verb RunAs` in background mode, which **silently dismisses the UAC dialog** instead of showing it. Service restarts required foreground invocation with `-Wait` and explicit "click Yes" prompts to the user.
- After a few failed cycles, the service ended up `Stopped` and needed a manual `nssm start DAGBot` (also elevated).

### Mail Prompt test still fails

Live test reports `❌ Mail Prompt` because `_drive_bot.py` asserts `'```' in r.text`. Pyrogram strips markdown code-block backticks from `.text` (they live in `entities`), so the assertion is always false. The bot itself sends the prompt correctly — confirmed by inspecting the message. Test bug, not a bot bug.

### Things worth improving

1. **Mail Prompt test assertion** — fix `_drive_bot.py` to check `r.entities` for a `MessageEntityCode` (or just match on "EVENT DETAILS" text content). Five-minute fix; haven't done it because it's only the test driver.
2. **Service restart automation** — UAC keeps blocking. Options: (a) configure NSSM to auto-restart on file change via `AppRotateOnline`, (b) install a `restart-dagbot.bat` shortcut elevated-by-default on the desktop, (c) bite the bullet and store the `mrrut` password in the NSSM service config (would also let `docx2pdf` work, making LibreOffice the fallback instead of primary).
3. **Old Telegram-uploaded files** still pile up in `temp/` (e.g., `approval_<file_id>.jpg` from each report). Could either (a) route uploads into the event folder once `event_name` is known, or (b) add a daily cron that wipes `temp/*.jpg` older than 7 days. We removed 105 leftover files today during cleanup.
4. **Cover row prefilled fields** in the picker are best-effort. If the user starts Budget → picks an existing event that only has a Proposal so far, we prefill `date` from the proposal but not `event_name → mode → venue → date` chain (Budget doesn't have those). Works fine today; just flagging that the prefill logic is per-flow specific and any new field added later needs the same treatment.
5. **No date validation** anywhere. Bot accepts "next Friday" or "abc" as a date. Should probably regex-check `\d{2}-\d{2}-\d{4}` and reject otherwise.
6. **Event picker caps at 8** entries. Fine for a club doing maybe 12 events a year, but as the list grows we'd want either pagination or a "search by name" mode.
7. **Backup story is nil**. `events/<user>/` lives on D:\ only. If the disk dies, every report goes with it. Could add a nightly `robocopy events/ <somewhere>` task, or push to Google Drive via the existing `client_secret`/Google auth if you want to wire that up.
8. **Service still runs as LocalSystem.** If you ever want native Word fidelity for PDFs (docx2pdf produces nicer results than LibreOffice in some edge cases), switching the service account to `mrrut` would unlock it. Optional — current LibreOffice output is good enough.

### Files touched

- `generators/report_gen.py` — full pagination + border + section-block rewrite
- `generators/budget_gen.py` — cell merges, surplus widening, sig block format
- `generators/proposal_gen.py` — page break before Budget Breakdown
- `generators/file_utils.py` — `_find_soffice()` fallback
- `handlers/report.py` — `R_PICK` picker state + `_ask_has_budget` extraction + feedback link instead of files
- `handlers/proposal.py` — `P_PICK` picker state
- `handlers/budget.py` — `B_PICK` picker state
- `event_store.py` — folder-per-event layout + auto-migration
- `bot.py` — Download Files button + `event_folder` import
- `_drive_bot.py` — `_skip_event_picker_if_shown` helper + feedback link in test data

---

## 2026-05-24 — Wire the report rebuild + fix broken imports

**Context:** The Final Event Report wizard and generator were rebuilt earlier today using the template-fill approach (see `memory/template_fill_approach.md`). New wizard sections were added: Guest Speaker, Feedback Forms, Expenditure Statement (the last gated on `has_budget=Yes`). Attendance was reshaped from "morning + evening photo" to "Yes/No → optional Excel". Certificates were reshaped from "always upload" to "Yes/No → optional images". This session continues from there.

### Changes

- **`bot.py`** — Replaced the long named-handler import block from `handlers.report` with `from handlers.report import report_start, REPORT_STATES`. The old import listed `report_attend_morning` and `report_attend_evening`, which no longer exist after the wizard rewrite — so `bot.py` was failing at import time and the service couldn't start. All the other named handlers in that block were also unused (everything routes through `REPORT_STATES`).
- **`_drive_bot.py`** — Updated `test_report` to match the new wizard:
  - Attendance: was `send_photo(morning) → done → send_photo(evening) → done`. Now `send('Yes') → send_document(attendance.xlsx)`.
  - Certificates: was `send_photo(cert) → done`. Now `send('Yes') → send_photo(cert) → done` (Yes/No gate added in the handler).
  - Removed unused `morning_img` / `evening_img` asset builds.

### Pending — pick up here next session

1. **Restart the DAGBot service** so the running Python picks up today's code.
   - The python processes were started 2026-05-24 ~00:29. Today's `handlers/report.py` rewrite + the `bot.py` fix landed after that, so the service is running stale in-memory code.
   - Run (in an elevated PowerShell): `Restart-Service DAGBot -Force`
   - Or `services.msc` → DAGBot → Restart.
   - UAC was cancelled twice during this session, so the restart never happened.
2. **Run the live test:** `python _drive_bot.py` from `D:\dagbot`. Expect 5/5 pass.
3. **Watch for these test gotchas:**
   - The driver now sends `Yes` then an attendance .xlsx (not morning/evening photos).
   - The driver now sends `Yes` before the certificate image.
   - `has_budget=No` skips signed-budget + signed-proposal uploads and skips the Expenditure section.
4. **Cosmetic cleanup (optional):** `bot.py` still has long named-handler import blocks for proposal and budget. Not broken — those names still exist — but they're unused. Could be slimmed the same way report's was.

### Files touched

- `bot.py` — slimmed `handlers.report` import to just `report_start, REPORT_STATES`. Removed dead references to `report_attend_morning, report_attend_evening` which no longer exist.
- `_drive_bot.py` — `test_report` now matches the new wizard (Yes/No gate for attendance + certificates; Excel upload for attendance).
- `PROJECT_LOG.md` — created.
