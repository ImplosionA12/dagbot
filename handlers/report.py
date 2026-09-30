import logging
import os
import re
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes, MessageHandler, CallbackQueryHandler, filters, ConversationHandler
)

from config import fixed_profile
from event_store import list_events
from handlers.ui import (
    DONE_KB, CONFIRM_KB, CONFIRM_EDIT_KB, RETRY_KB, SKIP_KB, resolve_text, md_escape
)
from handlers.edit_fields import Field, make_edit_handlers, resume_after_edit
from handlers.xlsx_people import parse_people, summary

logger = logging.getLogger(__name__)

# State IDs — must match bot.py
R_CLUB, R_COORD, R_EMPID, R_EVENT, R_MODE, R_VENUE, R_DATE, \
R_HAS_BUDGET, R_SIGNED_BUDGET, R_SIGNED_PROPOSAL, \
R_APPROVAL, R_BROCHURE, \
R_SCHEDULE, \
R_PARTICIPANTS, \
R_HAS_ATTENDANCE, R_ATTENDANCE_FILE, \
R_DESCRIPTION_PURPOSE, R_DESCRIPTION_BRIEF, \
R_HAS_CERTIFICATES, R_CERTIFICATES, \
R_PHOTOS, \
R_WINNERS, \
R_HAS_SPEAKER, R_SPEAKER_FILES, R_SPEAKER_BIO, \
R_HAS_FEEDBACK, R_FEEDBACK_LINK, \
R_HAS_EXPENDITURE, R_EXPENDITURE_FILES, \
R_CONFIRM, R_PICK, \
R_EDIT_PICK, R_EDIT_VALUE = range(300, 333)

_SCHEDULE_PROMPT = (
    "*Detailed Schedule* — send the entries again:\n`Time, Activity`\n\n"
    "E.g. `9:00 AM – 9:30 AM, Registration & Setup`\n\n"
    "One per line (you can paste the whole schedule at once). Tap *Done* when finished."
)
_WINNERS_PROMPT = (
    "*Winners List* — send them again:\n`Name, Registration Number`\n\n"
    "E.g. `Dumpa Vikhil Sai, 23BCE9428`\n\nTap *Done* when finished."
)

REPORT_EDIT_FIELDS = [
    Field('event_name', '📛 Event name', 'Send the new event name:'),
    Field('mode', '🌐 Mode', 'Send the new mode:', choices=('Online', 'Offline')),
    Field('venue', '📍 Venue', 'Send the new venue:'),
    Field('date', '📅 Date', 'Send the new date (DD-MM-YYYY):'),
    Field('purpose', '📝 Purpose', 'Send the new Purpose text:'),
    Field('description', '📄 Brief description', 'Send the new Brief Description text:'),
    Field('speaker_bio', '🎤 Speaker bio', 'Send the new speaker bio:'),
    Field('feedback_link', '🔗 Feedback link', 'Send the new feedback link:'),
    Field('schedule', '🗓 Schedule', '', restart_state=R_SCHEDULE,
          restart_prompt=_SCHEDULE_PROMPT),
    Field('winners', '🏆 Winners', '', restart_state=R_WINNERS,
          restart_prompt=_WINNERS_PROMPT),
]

TEMP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'temp')
os.makedirs(TEMP_DIR, exist_ok=True)


# ────────────────────────────────────────────────────────────────────────────
# Upload helper — accepts photo OR document (image / PDF / Word)
# ────────────────────────────────────────────────────────────────────────────
async def _download_upload(update: Update, prefix: str) -> str | None:
    """Save a photo or document upload to TEMP_DIR. Returns absolute path or None.

    Every upload step also carries inline buttons, so this can be reached by a
    button press — including a stale one from an earlier step whose callback_data
    isn't the 'done' the caller checked for. update.message is None for those,
    and reading .photo off it took the whole wizard step down.
    """
    msg = update.message
    if msg is None:
        return None
    if msg.photo:
        tg_file = await msg.photo[-1].get_file()
        ext = 'jpg'
    elif msg.document:
        tg_file = await msg.document.get_file()
        name = msg.document.file_name or 'file'
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else 'bin'
    else:
        return None

    path = os.path.join(TEMP_DIR, f"{prefix}_{tg_file.file_id}.{ext}")
    await tg_file.download_to_drive(path)
    return path


# ────────────────────────────────────────────────────────────────────────────
# Conversation entry
# ────────────────────────────────────────────────────────────────────────────
async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['type'] = 'report'
    profile = fixed_profile()

    context.user_data['report'] = {
        'club': profile['club'],
        'coordinator': profile['coordinator'],
        'emp_id': profile['emp_id'],
        'has_budget': False,
        'signed_budget_files': [],
        'signed_proposal_files': [],
        'approval_images': [],
        'certificates': [],
        'photos': [],
        'winners': [],
        'schedule': [],
        'participants': [],
        'attendees': [],
        'speaker_files': [],
        'speaker_bio': '',
        'feedback_link': '',
        'expenditure_files': [],
    }

    user_id = update.effective_user.id
    events = list_events(user_id)
    if events:
        keyboard = [[f"📂 {ev.get('name', ev.get('slug', '?'))}"] for ev in events[:8]]
        keyboard.append(["➕ New event"])
        await update.effective_message.reply_text(
            "📋 *Final Event Report*\n\n"
            "Pick an existing event or start a new one:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )
        return R_PICK

    await update.effective_message.reply_text(
        f"📋 *Final Event Report*\n\n*Event name?*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return R_EVENT


async def report_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip()
    user_id = update.effective_user.id

    if text.startswith('➕') or 'new event' in text.lower():
        await update.effective_message.reply_text(
            "*Event name?*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return R_EVENT

    name = text.lstrip('📂 ').strip()
    events = list_events(user_id)
    match = next((e for e in events if e.get('name') == name), None)
    if not match:
        await update.effective_message.reply_text("Please pick one from the menu.")
        return R_PICK

    r = context.user_data['report']
    r['event_name'] = match.get('name', '')
    for flow_key in ('report', 'proposal', 'budget'):
        flow = match.get(flow_key) or {}
        if not r.get('venue'):
            r['venue'] = flow.get('venue', '')
        if not r.get('date'):
            r['date'] = flow.get('date', '')

    summary_parts = []
    if r.get('venue'): summary_parts.append(f"Venue: {md_escape(r['venue'])}")
    if r.get('date'): summary_parts.append(f"Date: {md_escape(r['date'])}")
    sub = f"\n_({' · '.join(summary_parts)})_" if summary_parts else ""

    keyboard = [["Online", "Offline"]]
    await update.effective_message.reply_text(
        f"✅ Using event: *{md_escape(r['event_name'])}*{sub}\n\n*Mode of Conduct?*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return R_MODE


async def report_club(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['report']['club'] = update.message.text
    await update.effective_message.reply_text(
        "2️⃣ *Coordinator name?*\n\nE.g. Dr. Shaik Kareemulla",
        parse_mode="Markdown"
    )
    return R_COORD


async def report_coord(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['report']['coordinator'] = update.message.text
    await update.effective_message.reply_text("3️⃣ *Employee ID?*", parse_mode="Markdown")
    return R_EMPID


async def report_empid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['report']['emp_id'] = update.message.text
    await update.effective_message.reply_text("4️⃣ *Event name?*", parse_mode="Markdown")
    return R_EVENT


async def report_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['report']['event_name'] = update.message.text
    keyboard = [["Online", "Offline"]]
    await update.effective_message.reply_text(
        "5️⃣ *Mode of Conduct?*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return R_MODE


async def _ask_has_budget(update: Update):
    keyboard = [["Yes", "No"]]
    await update.effective_message.reply_text(
        "8️⃣ *Did this event have a budget?*\n\n"
        "_If yes, you'll be asked to upload the signed Budget Form and signed Event Proposal._\n"
        "_If no, we'll skip straight to the report sections._",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return R_HAS_BUDGET


async def report_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = update.message.text.strip()
    if mode not in ["Online", "Offline"]:
        await update.effective_message.reply_text("Please choose *Online* or *Offline*.", parse_mode="Markdown")
        return R_MODE
    r = context.user_data['report']
    r['mode'] = mode
    # Skip venue/date if prefilled from picker
    if r.get('venue') and r.get('date'):
        return await _ask_has_budget(update)
    if r.get('venue'):
        await update.effective_message.reply_text(
            "7️⃣ *Event date?*\n\nFormat: DD-MM-YYYY",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return R_DATE
    await update.effective_message.reply_text(
        "6️⃣ *Venue?*\n\nE.g. Einstein Hall, AB-1 or Online",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return R_VENUE


async def report_venue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = context.user_data['report']
    r['venue'] = update.message.text
    if r.get('date'):
        return await _ask_has_budget(update)
    await update.effective_message.reply_text(
        "7️⃣ *Event date?*\n\nFormat: DD-MM-YYYY",
        parse_mode="Markdown"
    )
    return R_DATE


async def report_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['report']['date'] = update.message.text
    return await _ask_has_budget(update)


# ────────────────────────────────────────────────────────────────────────────
# Branch: signed attachments (only if budget)
# ────────────────────────────────────────────────────────────────────────────
async def report_has_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip().lower()
    if text not in ('yes', 'no'):
        await update.effective_message.reply_text("Please tap *Yes* or *No*.", parse_mode="Markdown")
        return R_HAS_BUDGET

    if text == 'no':
        context.user_data['report']['has_budget'] = False
        await update.effective_message.reply_text(
            "✅ Skipping signed attachments.\n\n"
            "📌 *Section 1 — Approval Mail Screenshot*\n\n"
            "Send the approval mail screenshot (image / PDF / Word).\n"
            "You can send up to 2 files. Tap *Done* when finished.",
            parse_mode="Markdown",
            reply_markup=DONE_KB
        )
        return R_APPROVAL

    context.user_data['report']['has_budget'] = True
    await update.effective_message.reply_text(
        "📎 *Signed Budget Form*\n\n"
        "Upload the signed Budget Form (image / PDF / Word). Multi-page PDFs are fine.\n"
        "Send one or more files, then tap *Done* to continue.",
        parse_mode="Markdown",
        reply_markup=DONE_KB
    )
    return R_SIGNED_BUDGET


async def report_signed_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip().lower() or None

    if text == 'done':
        if not context.user_data['report']['signed_budget_files']:
            await update.effective_message.reply_text("⚠️ Please upload at least one file or send the budget form.")
            return R_SIGNED_BUDGET
        await update.effective_message.reply_text(
            "✅ Got the signed budget.\n\n"
            "📎 *Signed Event Proposal*\n\n"
            "Upload the signed Event Proposal (image / PDF / Word).\n"
            "Send one or more files, then tap *Done* to continue.",
            parse_mode="Markdown",
            reply_markup=DONE_KB
        )
        return R_SIGNED_PROPOSAL

    path = await _download_upload(update, 'signed_budget')
    if path:
        context.user_data['report']['signed_budget_files'].append(path)
        count = len(context.user_data['report']['signed_budget_files'])
        await update.effective_message.reply_text(
            f"✅ Got file {count}. Send more, or tap *Done* to continue.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
    else:
        await update.effective_message.reply_text("⚠️ Please send an image / PDF / Word file, or tap *Done*.", parse_mode="Markdown", reply_markup=DONE_KB)
    return R_SIGNED_BUDGET


async def report_signed_proposal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip().lower() or None

    if text == 'done':
        if not context.user_data['report']['signed_proposal_files']:
            await update.effective_message.reply_text("⚠️ Please upload at least one file or send the proposal.")
            return R_SIGNED_PROPOSAL
        await update.effective_message.reply_text(
            "✅ Got the signed proposal.\n\n"
            "📌 *Section 1 — Approval Mail Screenshot*\n\n"
            "Send the approval mail screenshot (image / PDF / Word).\n"
            "You can send up to 2 files. Tap *Done* when finished.",
            parse_mode="Markdown",
            reply_markup=DONE_KB
        )
        return R_APPROVAL

    path = await _download_upload(update, 'signed_proposal')
    if path:
        context.user_data['report']['signed_proposal_files'].append(path)
        count = len(context.user_data['report']['signed_proposal_files'])
        await update.effective_message.reply_text(
            f"✅ Got file {count}. Send more, or tap *Done* to continue.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
    else:
        await update.effective_message.reply_text("⚠️ Please send an image / PDF / Word file, or tap *Done*.", parse_mode="Markdown", reply_markup=DONE_KB)
    return R_SIGNED_PROPOSAL


# ────────────────────────────────────────────────────────────────────────────
# Section 1 — Approval Mail
# ────────────────────────────────────────────────────────────────────────────
async def report_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip().lower() or None

    if text == 'done':
        if not context.user_data['report']['approval_images']:
            await update.effective_message.reply_text("⚠️ Please send at least one approval file.")
            return R_APPROVAL
        await update.effective_message.reply_text(
            "📌 *Section 2 — Brochure / Poster*\n\nSend the event poster/brochure image.",
            parse_mode="Markdown"
        )
        return R_BROCHURE

    path = await _download_upload(update, 'approval')
    if path:
        context.user_data['report']['approval_images'].append(path)
        count = len(context.user_data['report']['approval_images'])
        if count >= 2:
            await update.effective_message.reply_text(
                "✅ Got 2 approval files (max).\n\n"
                "📌 *Section 2 — Brochure / Poster*\n\nSend the event poster/brochure image.",
                parse_mode="Markdown"
            )
            return R_BROCHURE
        await update.effective_message.reply_text(
            f"✅ Got {count} file. Send one more or tap *Done*.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
    else:
        await update.effective_message.reply_text("⚠️ Please send an image / PDF / Word file.")
    return R_APPROVAL


# ────────────────────────────────────────────────────────────────────────────
# Section 2 — Brochure
# ────────────────────────────────────────────────────────────────────────────
async def report_brochure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    path = await _download_upload(update, 'brochure')
    if path:
        context.user_data['report']['brochure'] = path
        await update.effective_message.reply_text(
            "✅ Brochure saved.\n\n"
            "📌 *Section 3 — Detailed Schedule*\n\n"
            "Send schedule entries one by one in this format:\n"
            "`Time, Activity`\n\n"
            "E.g. `9:00 AM – 9:30 AM, Registration & Setup`\n\n"
            "Tap *Done* when finished.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
        return R_SCHEDULE
    await update.effective_message.reply_text("⚠️ Please send an image.")
    return R_BROCHURE


# ────────────────────────────────────────────────────────────────────────────
# Section 3 — Schedule
# ────────────────────────────────────────────────────────────────────────────
# A schedule "Time" cell holds a clock time ("9:00 PM", "9-10 AM", "14:00").
# Without this check any comma splits a line, so prose like "Day 1 (Saturday,
# 19 Sep): Round 1" became a row with Time="Day 1 (Saturday".
_TIME_RE = re.compile(r'\d{1,2}\s*:\s*\d{2}|\d{1,2}(?:\s*[-–—]\s*\d{1,2})?\s*[ap]\.?\s*m(?![a-z])', re.I)


def _split_schedule_line(line: str):
    """(time, activity) for a 'Time, Activity' line, or None if it isn't one."""
    parts = line.split(',', 1)
    if len(parts) < 2:
        return None
    time_slot, activity = parts[0].strip(), parts[1].strip()
    if not activity or not _TIME_RE.search(time_slot):
        return None
    return time_slot, activity


async def report_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip()
    if text.lower() == 'done':
        if not context.user_data['report']['schedule']:
            await update.effective_message.reply_text("⚠️ Add at least one schedule entry.")
            return R_SCHEDULE
        if resume_after_edit(context):
            return await _report_summary(update, context)
        await update.effective_message.reply_text(
            "✅ Schedule saved.\n\n"
            "📌 *Section 4 — Participant List*\n\n"
            "Send the Excel file (.xlsx) exported from Google Forms.\n"
            "It must contain *Name* and *Registration Number* columns.",
            parse_mode="Markdown"
        )
        return R_PARTICIPANTS
    # One entry per line, like the winners list. Splitting the whole message on
    # its first comma turned a pasted multi-line schedule into a single row whose
    # "Time" was the entire block — which then rendered as an unjustified wall of
    # soft-broken text in the report table.
    schedule = context.user_data['report']['schedule']
    added, bad, dupes = [], [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        split = _split_schedule_line(line)
        if split is None:
            bad.append(line)
            continue
        time_slot, activity = split
        entry = {'time': time_slot, 'activity': activity}
        # Sending the same block twice is a slip, not a schedule — the report it
        # produces has the row printed twice with no way to tell them apart.
        if entry in schedule:
            dupes += 1
            continue
        schedule.append(entry)
        added.append((time_slot, activity))

    if added:
        lines = "\n".join(f"• `{md_escape(t)}` — {md_escape(a)}" for t, a in added)
        await update.effective_message.reply_text(
            f"✅ Added {len(added)} entr{'y' if len(added) == 1 else 'ies'}:\n{lines}\n\n"
            f"Send more or tap *Done*.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
    if dupes:
        await update.effective_message.reply_text(
            f"↩️ Skipped {dupes} entr{'y' if dupes == 1 else 'ies'} already in the schedule.")
    if bad:
        await update.effective_message.reply_text(
            "⚠️ Couldn't read these — each line must start with a time, then a comma, "
            "then the activity:\n" + "\n".join(f"• {md_escape(b[:60])}" for b in bad) +
            "\n\nUse:\n`Time, Activity`\nE.g. `9:00 AM – 9:30 AM, Registration & Setup`",
            parse_mode="Markdown"
        )
    return R_SCHEDULE


# ────────────────────────────────────────────────────────────────────────────
# Section 4 — Participants Excel
# ────────────────────────────────────────────────────────────────────────────
async def report_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.effective_message.reply_text("⚠️ Please send an Excel (.xlsx) file.")
        return R_PARTICIPANTS

    tg_file = await update.message.document.get_file()
    path = os.path.join(TEMP_DIR, f"participants_{tg_file.file_id}.xlsx")
    await tg_file.download_to_drive(path)

    try:
        participants, meta = parse_people(path)
    except ValueError:
        await update.effective_message.reply_text(
            "⚠️ Could not find *Name* and *Registration Number* columns.\n"
            "Make sure your Excel has these column headers.",
            parse_mode="Markdown"
        )
        return R_PARTICIPANTS
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error reading Excel: {str(e)}\n\nPlease send a valid .xlsx file.")
        return R_PARTICIPANTS

    if not participants:
        await update.effective_message.reply_text(
            "⚠️ Found the columns but no filled-in rows. Check the sheet and send it again."
        )
        return R_PARTICIPANTS

    context.user_data['report']['participants'] = participants
    # Team sheets expand to several rows per response, so show the count back —
    # a mis-detected header is much cheaper to catch here than at PDF time.
    await update.effective_message.reply_text(
        f"✅ Read *{summary(meta)}*.\n\n_First entry:_ {md_escape(participants[0]['name'])} "
        f"({md_escape(participants[0]['reg_no'])})",
        parse_mode="Markdown"
    )
    return await _ask_has_attendance(update)


# ────────────────────────────────────────────────────────────────────────────
# Section 5 — Attendance (Excel, optional)
# ────────────────────────────────────────────────────────────────────────────
async def _ask_has_attendance(update: Update):
    keyboard = [["Yes", "No"]]
    await update.effective_message.reply_text(
        "📌 *Section 5 — Attendance*\n\n"
        "Do you have an attendance Excel sheet to attach?\n"
        "_(Same format as participants — Name + Registration Number columns.)_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return R_HAS_ATTENDANCE


async def report_has_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip().lower()
    if text not in ('yes', 'no'):
        await update.effective_message.reply_text("Please tap *Yes* or *No*.", parse_mode="Markdown")
        return R_HAS_ATTENDANCE
    if text == 'no':
        await update.effective_message.reply_text(
            "✅ Skipping attendance.\n\n"
            "📌 *Section 6 — Brief Description*\n\n"
            "Write the *Purpose of the Event* paragraph.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return R_DESCRIPTION_PURPOSE
    await update.effective_message.reply_text(
        "📎 Send the *attendance Excel* (.xlsx) file.\n"
        "_It must have Name and Registration Number columns._",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return R_ATTENDANCE_FILE


async def report_attendance_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.effective_message.reply_text("⚠️ Please send an Excel (.xlsx) file.")
        return R_ATTENDANCE_FILE

    tg_file = await update.message.document.get_file()
    path = os.path.join(TEMP_DIR, f"attendance_{tg_file.file_id}.xlsx")
    await tg_file.download_to_drive(path)

    try:
        attendees, meta = parse_people(path)
    except ValueError:
        await update.effective_message.reply_text(
            "⚠️ Could not find *Name* and *Registration Number* columns.\n"
            "Make sure your Excel has these column headers.",
            parse_mode="Markdown"
        )
        return R_ATTENDANCE_FILE
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error reading Excel: {str(e)}\n\nPlease send a valid .xlsx file.")
        return R_ATTENDANCE_FILE

    context.user_data['report']['attendees'] = attendees
    await update.effective_message.reply_text(
        f"✅ Loaded *{summary(meta)}* from Excel.\n\n"
        "📌 *Section 6 — Brief Description*\n\n"
        "Write the *Purpose of the Event* paragraph.",
        parse_mode="Markdown"
    )
    return R_DESCRIPTION_PURPOSE


# ────────────────────────────────────────────────────────────────────────────
# Section 6 — Description
# ────────────────────────────────────────────────────────────────────────────
async def report_desc_purpose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['report']['purpose'] = update.message.text
    await update.effective_message.reply_text(
        "Now write the *Brief Description* paragraph.\n_(What happened during the event)_",
        parse_mode="Markdown"
    )
    return R_DESCRIPTION_BRIEF


async def report_desc_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['report']['description'] = update.message.text
    return await _ask_has_certificates(update)


# ────────────────────────────────────────────────────────────────────────────
# Section 7 — Certificates (optional)
# ────────────────────────────────────────────────────────────────────────────
async def _ask_has_certificates(update: Update):
    keyboard = [["Yes", "No"]]
    await update.effective_message.reply_text(
        "✅ Description saved.\n\n"
        "📌 *Section 7 — Sample Certificate Copies*\n\n"
        "Do you have sample certificates to attach?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return R_HAS_CERTIFICATES


async def _photos_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data['report'].get('mode', 'Offline')
    section_label = "Geotagged Photographs" if mode == "Offline" else "Event Screenshots"
    await update.effective_message.reply_text(
        f"📌 *Section 8 — {section_label}*\n\n"
        f"Send photos one by one. Tap *Done* when finished.",
        parse_mode="Markdown",
        reply_markup=DONE_KB
    )
    return R_PHOTOS


async def report_has_certificates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip().lower()
    if text not in ('yes', 'no'):
        await update.effective_message.reply_text("Please tap *Yes* or *No*.", parse_mode="Markdown")
        return R_HAS_CERTIFICATES
    if text == 'no':
        return await _photos_prompt(update, context)
    await update.effective_message.reply_text(
        "Send certificate images one by one. Tap *Done* when finished.",
        parse_mode="Markdown",
        reply_markup=DONE_KB
    )
    return R_CERTIFICATES


async def report_certificates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip().lower() or None
    if text == 'done':
        if not context.user_data['report']['certificates']:
            await update.effective_message.reply_text("⚠️ Please send at least one certificate image, or restart this section.")
            return R_CERTIFICATES
        return await _photos_prompt(update, context)

    path = await _download_upload(update, 'certificate')
    if path:
        context.user_data['report']['certificates'].append(path)
        await update.effective_message.reply_text(
            f"✅ Got {len(context.user_data['report']['certificates'])} certificate(s). Send more or tap *Done*.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
    else:
        await update.effective_message.reply_text("⚠️ Please send an image.")
    return R_CERTIFICATES


# ────────────────────────────────────────────────────────────────────────────
# Section 8 — Photos / Screenshots
# ────────────────────────────────────────────────────────────────────────────
async def report_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip().lower() or None
    if text == 'done':
        if not context.user_data['report']['photos']:
            await update.effective_message.reply_text("⚠️ Please send at least one photo.")
            return R_PHOTOS
        await update.effective_message.reply_text(
            "✅ Photos saved.\n\n"
            "📌 *Section 9 — Winners List*\n\n"
            "Send each winner in this format:\n"
            "`Name, Registration Number`\n\n"
            "E.g. `Dumpa Vikhil Sai, 23BCE9428`\n\n"
            "Tap *Done* when finished.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
        return R_WINNERS

    path = await _download_upload(update, 'photo')
    if path:
        context.user_data['report']['photos'].append(path)
        await update.effective_message.reply_text(
            f"✅ Got {len(context.user_data['report']['photos'])} photo(s). Send more or tap *Done*.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
    else:
        await update.effective_message.reply_text("⚠️ Please send an image.")
    return R_PHOTOS


# ────────────────────────────────────────────────────────────────────────────
# Section 9 — Winners
# ────────────────────────────────────────────────────────────────────────────
async def report_winners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip()
    if text.lower() == 'done':
        if not context.user_data['report']['winners']:
            await update.effective_message.reply_text("⚠️ Add at least one winner.")
            return R_WINNERS
        if resume_after_edit(context):
            return await _report_summary(update, context)
        return await _ask_has_speaker(update)

    # One winner per line, so pasting a whole list at once still yields one table
    # row each — splitting only on the first comma turned "A, B, C, D" into a
    # single winner whose reg no read "B, C, D".
    added, bad = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(',', 1)
        name = parts[0].strip()
        reg = parts[1].strip() if len(parts) > 1 else ''
        if not name or not reg:
            bad.append(line)
            continue
        context.user_data['report']['winners'].append({'name': name, 'reg_no': reg})
        added.append(f"*{name}* ({reg})")

    if not added:
        await update.effective_message.reply_text(
            "⚠️ Wrong format. Use one winner per line:\n"
            "`Name, Registration Number`\nE.g. `Dumpa Vikhil Sai, 23BCE9428`",
            parse_mode="Markdown"
        )
        return R_WINNERS

    msg = "✅ Added " + ("winner: " if len(added) == 1 else f"{len(added)} winners:\n")
    msg += "\n".join(added) if len(added) > 1 else added[0]
    if bad:
        msg += "\n\n⚠️ Skipped (need `Name, Reg No`):\n" + "\n".join(f"• {b}" for b in bad)
    await update.effective_message.reply_text(
        msg + "\n\nSend next winner or tap *Done*.",
        parse_mode="Markdown", reply_markup=DONE_KB
    )
    return R_WINNERS


# ────────────────────────────────────────────────────────────────────────────
# Section 9 — Guest Speaker (optional)
# ────────────────────────────────────────────────────────────────────────────
async def _ask_has_speaker(update: Update):
    keyboard = [["Yes", "No"]]
    await update.effective_message.reply_text(
        "📌 *Section 9 — Guest Speaker*\n\n"
        "Did this event have a guest speaker?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return R_HAS_SPEAKER


async def report_has_speaker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip().lower()
    if text not in ('yes', 'no'):
        await update.effective_message.reply_text("Please tap *Yes* or *No*.", parse_mode="Markdown")
        return R_HAS_SPEAKER
    if text == 'no':
        return await _ask_has_feedback(update)
    await update.effective_message.reply_text(
        "📸 Send the *speaker's photo(s)* (image / PDF / Word).\n"
        "Send one or more files, then tap *Done* to continue.",
        parse_mode="Markdown",
        reply_markup=DONE_KB
    )
    return R_SPEAKER_FILES


async def report_speaker_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip().lower() or None
    if text == 'done':
        if not context.user_data['report']['speaker_files']:
            await update.effective_message.reply_text("⚠️ Please send at least one speaker photo, or restart this section.")
            return R_SPEAKER_FILES
        await update.effective_message.reply_text(
            "✏️ Now write a short *bio for the speaker*.\n"
            "_(Or tap Skip to leave it blank.)_",
            parse_mode="Markdown", reply_markup=SKIP_KB
        )
        return R_SPEAKER_BIO

    path = await _download_upload(update, 'speaker')
    if path:
        context.user_data['report']['speaker_files'].append(path)
        count = len(context.user_data['report']['speaker_files'])
        await update.effective_message.reply_text(
            f"✅ Got file {count}. Send more, or tap *Done*.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
    else:
        await update.effective_message.reply_text("⚠️ Please send an image / PDF / Word file, or tap *Done*.", parse_mode="Markdown", reply_markup=DONE_KB)
    return R_SPEAKER_FILES


async def report_speaker_bio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip()
    if text.lower() != 'skip':
        context.user_data['report']['speaker_bio'] = text
    return await _ask_has_feedback(update)


# ────────────────────────────────────────────────────────────────────────────
# Section 10 — Feedback (optional)
# ────────────────────────────────────────────────────────────────────────────
async def _ask_has_feedback(update: Update):
    keyboard = [["Yes", "No"]]
    await update.effective_message.reply_text(
        "📌 *Section 10 — Feedback Forms*\n\n"
        "Do you have a feedback form link to share?\n"
        "_(Optional — tap No to skip.)_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return R_HAS_FEEDBACK


async def report_has_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip().lower()
    if text not in ('yes', 'no'):
        await update.effective_message.reply_text("Please tap *Yes* or *No*.", parse_mode="Markdown")
        return R_HAS_FEEDBACK
    if text == 'no':
        return await _ask_has_expenditure(update, context)
    await update.effective_message.reply_text(
        "🔗 Send the *feedback form link*.\n"
        "_(e.g. a Google Forms URL or Google Drive link to the responses.)_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return R_FEEDBACK_LINK


async def report_feedback_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = (update.message.text or '').strip()
    if not link:
        await update.effective_message.reply_text("⚠️ Please send the feedback form link as text.")
        return R_FEEDBACK_LINK
    context.user_data['report']['feedback_link'] = link
    return await _ask_has_expenditure(update, context)


# ────────────────────────────────────────────────────────────────────────────
# Section 11 — Expenditure Statement (only if has_budget)
# ────────────────────────────────────────────────────────────────────────────
async def _ask_has_expenditure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data['report'].get('has_budget'):
        return await _report_summary(update, context)
    keyboard = [["Yes", "No"]]
    await update.effective_message.reply_text(
        "📌 *Section 11 — Expenditure Statement*\n\n"
        "Do you have the signed expenditure statement to attach?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return R_HAS_EXPENDITURE


async def report_has_expenditure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip().lower()
    if text not in ('yes', 'no'):
        await update.effective_message.reply_text("Please tap *Yes* or *No*.", parse_mode="Markdown")
        return R_HAS_EXPENDITURE
    if text == 'no':
        return await _report_summary(update, context)
    await update.effective_message.reply_text(
        "📎 Send the *expenditure statement* (image / PDF / Word).\n"
        "Send one or more files, then tap *Done* to continue.",
        parse_mode="Markdown",
        reply_markup=DONE_KB
    )
    return R_EXPENDITURE_FILES


async def report_expenditure_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip().lower() or None
    if text == 'done':
        if not context.user_data['report']['expenditure_files']:
            await update.effective_message.reply_text("⚠️ Please send at least one expenditure file.")
            return R_EXPENDITURE_FILES
        return await _report_summary(update, context)

    path = await _download_upload(update, 'expenditure')
    if path:
        context.user_data['report']['expenditure_files'].append(path)
        count = len(context.user_data['report']['expenditure_files'])
        await update.effective_message.reply_text(
            f"✅ Got file {count}. Send more, or tap *Done*.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
    else:
        await update.effective_message.reply_text("⚠️ Please send an image / PDF / Word file, or tap *Done*.", parse_mode="Markdown", reply_markup=DONE_KB)
    return R_EXPENDITURE_FILES


# ────────────────────────────────────────────────────────────────────────────
# Summary + confirm
# ────────────────────────────────────────────────────────────────────────────
async def _report_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = context.user_data['report']
    mode = r['mode']
    photo_label = "Geotagged Photos" if mode == "Offline" else "Event Screenshots"

    budget_line = ""
    if r['has_budget']:
        budget_line = (
            f"*Signed Budget files:* {len(r['signed_budget_files'])}\n"
            f"*Signed Proposal files:* {len(r['signed_proposal_files'])}\n"
        )
    else:
        budget_line = "*Budget event:* No (signed attachments skipped)\n"

    # Free-text fields the user typed — a lone '_' or '*' here would make
    # Telegram reject the entire summary.
    e = md_escape
    summary = (
        f"📋 *Final Report Summary*\n\n"
        f"*Club:* {e(r['club'])}\n"
        f"*Coordinator:* {e(r['coordinator'])} (Emp: {e(str(r['emp_id']))})\n"
        f"*Event:* {e(r['event_name'])}\n"
        f"*Mode:* {e(r['mode'])}\n"
        f"*Venue:* {e(r['venue'])}\n"
        f"*Date:* {e(r['date'])}\n\n"
        f"{budget_line}\n"
        f"*Approval files:* {len(r['approval_images'])}\n"
        f"*Schedule entries:* {len(r['schedule'])}\n"
        f"*Participants:* {len(r['participants'])}\n"
        f"*Attendees (Excel):* {len(r.get('attendees', []))}\n"
        f"*Certificates:* {len(r['certificates'])} image(s)\n"
        f"*{photo_label}:* {len(r['photos'])} image(s)\n"
        f"*Winners:* {len(r['winners'])}\n"
        f"*Guest Speaker:* {len(r['speaker_files'])} file(s)"
        f"{' + bio' if r.get('speaker_bio') else ''}\n"
        f"*Feedback link:* {e(r['feedback_link']) or '(none)'}\n"
        f"*Expenditure:* {len(r['expenditure_files'])} file(s)\n\n"
        f"Tap *Confirm* to generate the report or *Cancel* to restart."
    )
    await update.effective_message.reply_text(summary, parse_mode="Markdown",
                                              reply_markup=CONFIRM_EDIT_KB)
    return R_CONFIRM


report_edit_menu, report_edit_pick, report_edit_value = make_edit_handlers(
    'report', REPORT_EDIT_FIELDS, _report_summary, R_EDIT_PICK, R_EDIT_VALUE)


async def report_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip().lower()
    if text == 'edit':
        return await report_edit_menu(update, context)
    if text != 'confirm':
        await update.effective_message.reply_text("Cancelled. Send /start to begin again.")
        return ConversationHandler.END

    await update.effective_message.reply_text("⏳ Generating final report... this may take a minute.")
    from generators.report_gen import generate_report
    from event_store import upsert_flow, event_folder, slugify
    try:
        event = context.user_data['report']['event_name']
        out_dir = event_folder(update.effective_user.id, slugify(event))
        docx_path, pdf_path = generate_report(context.user_data['report'], output_dir=out_dir)
        upsert_flow(update.effective_user.id, event, 'report', context.user_data['report'])

        # Big reports (1.6 MB+) can outlast the default 5s upload timeout.
        with open(docx_path, 'rb') as f:
            await update.effective_message.reply_document(
                f, filename=os.path.basename(docx_path), write_timeout=180, read_timeout=180)

        if pdf_path and os.path.exists(pdf_path):
            with open(pdf_path, 'rb') as f:
                await update.effective_message.reply_document(
                    f, filename=os.path.basename(pdf_path), write_timeout=180, read_timeout=180)
            await update.effective_message.reply_text(
                "✅ *Final Report generated!*\n\n"
                "Please print, get it signed, and upload the signed version for records.\n\n"
                "Send /start to generate another document.",
                parse_mode="Markdown"
            )
        else:
            await update.effective_message.reply_text(
                "✅ *DOCX generated*, but PDF conversion failed.\n"
                "Make sure Microsoft Word or LibreOffice is installed.\n"
                "You can open the DOCX and export to PDF manually.\n\n"
                "Send /start to generate another document.",
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.exception("report generation failed")
        # Stay in R_CONFIRM so the button retries without re-entering the wizard.
        await update.effective_message.reply_text(
            f"❌ Error generating report: {str(e) or type(e).__name__}\n\n"
            "Your answers are saved — tap *Generate again* to retry.",
            parse_mode="Markdown", reply_markup=RETRY_KB)
        return R_CONFIRM
    return ConversationHandler.END


# ────────────────────────────────────────────────────────────────────────────
# State map
# ────────────────────────────────────────────────────────────────────────────
REPORT_STATES = {
    R_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_pick)],
    R_CLUB: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_club)],
    R_COORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_coord)],
    R_EMPID: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_empid)],
    R_EVENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_event)],
    R_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_mode)],
    R_VENUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_venue)],
    R_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_date)],
    R_HAS_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_has_budget)],
    R_SIGNED_BUDGET: [
        MessageHandler(filters.PHOTO | filters.Document.ALL, report_signed_budget),
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_signed_budget),
        CallbackQueryHandler(report_signed_budget),
    ],
    R_SIGNED_PROPOSAL: [
        MessageHandler(filters.PHOTO | filters.Document.ALL, report_signed_proposal),
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_signed_proposal),
        CallbackQueryHandler(report_signed_proposal),
    ],
    R_APPROVAL: [
        MessageHandler(filters.PHOTO | filters.Document.ALL, report_approval),
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_approval),
        CallbackQueryHandler(report_approval),
    ],
    R_BROCHURE: [
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, report_brochure),
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_brochure),
    ],
    R_SCHEDULE: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_schedule),
        CallbackQueryHandler(report_schedule),
    ],
    # The TEXT handlers here aren't optional politeness: a state that only
    # accepts documents leaves a typed message unclaimed by the conversation,
    # so it falls through to the global catch-all and tells the user their
    # session expired — mid-wizard, with everything still in progress.
    R_PARTICIPANTS: [
        MessageHandler(filters.Document.ALL, report_participants),
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_participants),
    ],
    R_HAS_ATTENDANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_has_attendance)],
    R_ATTENDANCE_FILE: [
        MessageHandler(filters.Document.ALL, report_attendance_file),
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_attendance_file),
    ],
    R_DESCRIPTION_PURPOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_desc_purpose)],
    R_DESCRIPTION_BRIEF: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_desc_brief)],
    R_HAS_CERTIFICATES: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_has_certificates)],
    R_CERTIFICATES: [
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, report_certificates),
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_certificates),
        CallbackQueryHandler(report_certificates),
    ],
    R_PHOTOS: [
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, report_photos),
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_photos),
        CallbackQueryHandler(report_photos),
    ],
    R_WINNERS: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_winners),
        CallbackQueryHandler(report_winners),
    ],
    R_HAS_SPEAKER: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_has_speaker)],
    R_SPEAKER_FILES: [
        MessageHandler(filters.PHOTO | filters.Document.ALL, report_speaker_files),
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_speaker_files),
        CallbackQueryHandler(report_speaker_files),
    ],
    R_SPEAKER_BIO: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_speaker_bio),
        CallbackQueryHandler(report_speaker_bio),
    ],
    R_HAS_FEEDBACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_has_feedback)],
    R_FEEDBACK_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_feedback_link)],
    R_HAS_EXPENDITURE: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_has_expenditure)],
    R_EXPENDITURE_FILES: [
        MessageHandler(filters.PHOTO | filters.Document.ALL, report_expenditure_files),
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_expenditure_files),
        CallbackQueryHandler(report_expenditure_files),
    ],
    R_CONFIRM: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_confirm),
        CallbackQueryHandler(report_confirm),
    ],
    R_EDIT_PICK: [
        CallbackQueryHandler(report_edit_pick),
        MessageHandler(filters.TEXT & ~filters.COMMAND, report_edit_pick),
    ],
    R_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_edit_value)],
}
