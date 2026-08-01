"""Let the user fix one answer from a wizard's summary screen.

Every wizard ends on a summary with Confirm / Cancel. Until now a single wrong
date meant cancelling and retyping every answer — for the report that includes
re-uploading every screenshot, certificate and Excel sheet. The answers are all
sitting in `context.user_data[flow]` by then, so editing one is just: show the
fields, take a new value, redraw the same summary.

Two shapes of field:

  plain  — a single value (event name, date, purpose). Ask, store, redraw.
  list   — the item collections (budget items, schedule, winners). These already
           have a wizard state that loops "send another / tap Done", so editing
           clears the list and drops the user back into that state. Their Done
           branch calls `resume_after_edit()` to come straight back to the
           summary instead of walking the rest of the wizard again.
"""
from dataclasses import dataclass, field as _field

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from handlers.ui import resolve_text, md_escape

# user_data key holding the summary to return to once a list section is re-entered
_RESUME = '_edit_resume'
_EDITING = '_edit_key'


@dataclass(frozen=True)
class Field:
    """One editable answer.

    key            where it lives in context.user_data[flow]
    label          button text
    prompt         what to ask for when it's picked
    choices        accepted values, case-insensitive (e.g. Online/Offline)
    restart_state  list fields only: the wizard state that collects the items
    restart_prompt list fields only: the message that state expects to follow
    clears         extra keys emptied alongside `key` before restarting
    """
    key: str
    label: str
    prompt: str
    choices: tuple = ()
    restart_state: int | None = None
    restart_prompt: str = ''
    clears: tuple = _field(default_factory=tuple)


def edit_keyboard(fields: list[Field]) -> InlineKeyboardMarkup:
    """Two buttons a row — field labels are short enough to pair up."""
    buttons = [InlineKeyboardButton(f.label, callback_data=f"edit:{f.key}") for f in fields]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⬅️ Back to summary", callback_data="edit:back")])
    return InlineKeyboardMarkup(rows)


def _strip_echo(text: str) -> str:
    """Drop a header the user copied back along with the value.

    Showing the current value inline invites copy-the-whole-message-and-edit,
    and the header then lands in the document — a real report went out with
    "Current Brief Description (2721 characters):" as its opening line.
    """
    lines = text.split('\n')
    while lines and (
        lines[0].strip().lower().startswith('current ')
        and lines[0].rstrip().endswith(':')
    ):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return '\n'.join(lines).strip()


def resume_after_edit(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True when a list section was re-entered from the summary, so its Done
    branch should return to the summary rather than continue the wizard."""
    return bool(context.user_data.pop(_RESUME, False))


def make_edit_handlers(flow: str, fields: list[Field], summary_fn,
                       pick_state: int, value_state: int):
    """Build the (pick, value) handlers for one wizard's edit menu."""
    by_key = {f.key: f for f in fields}

    async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.effective_message.reply_text(
            "✏️ *Which field?*\n\nPick one — everything else stays as it is.",
            parse_mode="Markdown", reply_markup=edit_keyboard(fields)
        )
        return pick_state

    async def pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (await resolve_text(update) or '').strip()
        data = context.user_data.get(flow)
        if data is None:                       # persistence lost the flow
            await update.effective_message.reply_text(
                "That form is no longer in progress. Send /start to begin again.")
            from telegram.ext import ConversationHandler
            return ConversationHandler.END

        if text in ('edit:back', 'back'):
            return await summary_fn(update, context)

        fld = by_key.get(text.split(':', 1)[1]) if text.startswith('edit:') else None
        if fld is None:
            await update.effective_message.reply_text("Please pick a field from the buttons.")
            return pick_state

        if fld.restart_state is not None:
            for key in (fld.key,) + tuple(fld.clears):
                if isinstance(data.get(key), list):
                    data[key] = []
                else:
                    data[key] = ''
            context.user_data[_RESUME] = True
            await update.effective_message.reply_text(fld.restart_prompt, parse_mode="Markdown")
            return fld.restart_state

        context.user_data[_EDITING] = fld.key
        current = str(data.get(fld.key, '') or '')
        # The value goes in its own code block: tap-to-copy grabs just the text,
        # not the sentence introducing it.
        shown = f"\n\n```\n{current}\n```" if current else "\n\n_(currently empty)_"
        await update.effective_message.reply_text(
            f"✏️ *{md_escape(fld.label)}*{shown}\n\n{fld.prompt}",
            parse_mode="Markdown"
        )
        return value_state

    async def value(update: Update, context: ContextTypes.DEFAULT_TYPE):
        key = context.user_data.get(_EDITING)
        data = context.user_data.get(flow)
        if not key or data is None:
            return await show_menu(update, context)

        fld = by_key[key]
        new = _strip_echo((await resolve_text(update) or '').strip())
        if not new:
            await update.effective_message.reply_text(
                "That came through empty — send the new value, or /cancel to stop.")
            return value_state
        if fld.choices and new.lower() not in [c.lower() for c in fld.choices]:
            await update.effective_message.reply_text(
                f"Please send one of: {', '.join(fld.choices)}")
            return value_state
        if fld.choices:                        # store the canonical casing
            new = next(c for c in fld.choices if c.lower() == new.lower())

        data[key] = new
        context.user_data.pop(_EDITING, None)
        await update.effective_message.reply_text(f"✅ *{md_escape(fld.label)}* updated.",
                                                  parse_mode="Markdown")
        return await summary_fn(update, context)

    return show_menu, pick, value
