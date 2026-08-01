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


def _kb(*rows):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=data) for label, data in row]
        for row in rows
    ])


DONE_KB = _kb([("✅ Done", "done")])
DONE_NONE_KB = _kb([("✅ Done", "done"), ("🚫 None", "none")])
NONE_KB = _kb([("🚫 None", "None")])
CONFIRM_KB = _kb([("✅ Confirm", "confirm"), ("❌ Cancel", "cancel")])
SKIP_KB = _kb([("⏭ Skip", "skip")])
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
