"""Shared inline-button helpers for the wizard flows.

Inline buttons sit under the bot's message — they don't replace the reply
keyboard menus. Every button's callback_data is the exact text the user could
have typed instead ("done", "none", "confirm", ...), so resolve_text() lets
each handler treat a button press and a typed word identically.
"""
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update


def md_escape(s: str) -> str:
    """Escape Telegram legacy-Markdown specials in user-provided text.

    Lives here rather than in bot.py so the wizards can reach it without
    importing bot.py, which imports them.
    """
    return re.sub(r'([_*`\[])', r'\\\1', s or '')


def md_wrap(s: str, mark: str = '*') -> str:
    """User text wrapped in bold (*), italic (_) or code (`).

    md_escape() only works *outside* an entity — inside *...* Telegram shows
    the backslash, so "Live_Test" came out as "Live\\_Test". Inside, only the
    entity's own mark is special, and the documented way to include it is to
    close the entity, escape the mark, and reopen: *2*\\**2=4*."""
    s = str(s or '')
    return mark + s.replace(mark, f"{mark}\\{mark}{mark}") + mark


_DATE_RE = re.compile(r'(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{2}|\d{4})(?!\d)')
# What may sit between dates: commas, '&', 'and', 'to', dashes, spaces.
_DATE_GLUE = re.compile(r'^(?:\s|,|&|and|to|–|—|-)*$', re.IGNORECASE)

DATE_HELP = (
    "⚠️ I couldn't read that date. Please send it as *DD-MM-YYYY*, e.g. `24-05-2026`.\n\n"
    "Several days: `19-09-2026, 20-09-2026 & 21-09-2026`\n"
    "A range: `19-09-2026 to 21-09-2026`"
)


def parse_event_date(text: str) -> str | None:
    """One date, a list of dates, or a range → one tidy spelling; None if it
    isn't one. Free text used to go straight into the documents, which is how a
    single event ended up as "19-09-2026 , 20-09-2026 & 21-09-2026" in one form
    and "08-08-2026 & 09-08-2026" in another."""
    from datetime import date
    text = (text or '').strip()
    matches = list(_DATE_RE.finditer(text))
    if not matches or len(matches) > 10:
        return None
    dates, pos, glue = [], 0, []
    for m in matches:
        between = text[pos:m.start()]
        if not _DATE_GLUE.match(between):
            return None
        glue.append(between.strip().lower())
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            dt = date(y, mo, d)
        except ValueError:
            return None
        if not 2000 <= y <= 2100:
            return None
        dates.append(dt.strftime('%d-%m-%Y'))
        pos = m.end()
    if not _DATE_GLUE.match(text[pos:]) or text[pos:].strip():
        return None
    if len(dates) == 1:
        return dates[0]
    if len(dates) == 2 and glue[1] in ('to', '-', '–', '—'):
        return f"{dates[0]} to {dates[1]}"
    return ", ".join(dates[:-1]) + " & " + dates[-1]


# Typed words that mean the same as the summary's buttons. Anything else used
# to count as Cancel — one stray "ok" threw away every answer and upload.
_CONFIRM_WORDS = {'confirm', 'yes', 'y', 'ok', 'okay', 'generate', 'generate again', 'go'}


def confirm_intent(text: str) -> str:
    """'confirm' | 'edit' | 'cancel' | '' (not understood) for a summary reply."""
    t = (text or '').strip().lower().lstrip('✅✏️❌🔄 ').strip()
    if t in _CONFIRM_WORDS:
        return 'confirm'
    if t in ('edit', 'edit a field'):
        return 'edit'
    if t == 'cancel':
        return 'cancel'
    return ''


async def confirm_not_understood(update: Update):
    await update.effective_message.reply_text(
        "I didn't catch that — nothing is lost. Tap *Confirm* to generate, "
        "*Edit a field* to change something, or *Cancel* to discard this form.",
        parse_mode="Markdown", reply_markup=CONFIRM_EDIT_KB)


# Main-menu reply keyboard, shared by /start and the end of every wizard.
MAIN_MENU_ROWS = [
    ["📄 Event Proposal", "💰 Budget Form"],
    ["📋 Final Event Report"],
    ["📧 Mail Prompt", "📂 My Events"],
]


def _kb(*rows):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=data) for label, data in row]
        for row in rows
    ])


DONE_KB = _kb([("✅ Done", "done")])
DONE_NONE_KB = _kb([("✅ Done", "done"), ("🚫 None", "none")])
NONE_KB = _kb([("🚫 None", "None")])
CONFIRM_KB = _kb([("✅ Confirm", "confirm"), ("❌ Cancel", "cancel")])
# Summaries use this one: fixing a single wrong answer shouldn't cost the whole form.
CONFIRM_EDIT_KB = _kb(
    [("✅ Confirm", "confirm")],
    [("✏️ Edit a field", "edit"), ("❌ Cancel", "cancel")],
)
# Shown when generation/upload fails: "confirm" re-enters the confirm step.
RETRY_KB = _kb(
    [("🔄 Generate again", "confirm")],
    [("✏️ Edit a field", "edit"), ("❌ Cancel", "cancel")],
)
SKIP_KB =_kb([("⏭ Skip", "skip")])
OVERFLOW_KB = _kb(
    [("📤 Send anyway", "send")],
    [("✏️ Shorten purpose", "edit_purpose"), ("✏️ Shorten description", "edit_desc")],
)


async def resolve_text(update: Update) -> str | None:
    """Return the effective text of this update.

    - typed message  → its text (None for media-only messages)
    - inline button  → its callback_data; the query is answered and the
      buttons are removed from the pressed message so it can't fire twice.

    Handlers using this must reply via update.effective_message (for button
    presses update.message is None).
    """
    q = update.callback_query
    if q is not None:
        await q.answer()
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return q.data
    return update.message.text if update.message else None
