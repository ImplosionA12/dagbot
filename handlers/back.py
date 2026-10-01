"""Step back through a wizard without starting over.

Every wizard moves forward one handler at a time, and each handler both stores
the answer and sends the next prompt. Rather than teach forty handlers about
"previous", `track()` wraps them: whenever one moves the conversation to a new
state it records a checkpoint — the state, a snapshot of user_data *as it was on
arrival*, and the prompt that was shown. `/back` (or the ⬅️ Back button under
each prompt) pops the current checkpoint, restores the one before it and re-sends
its prompt, so the answer given at that step is undone and can be given again.

Steps that loop in one state (upload N photos, "send another or Done") don't add
checkpoints, so Back from inside one returns to the step before the loop.
"""
import contextvars
import copy
import functools
import logging

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from handlers.ui import MAIN_MENU_ROWS
from telegram.ext import (
    ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters,
)

# Last text message the bot sent per chat, written by MarkdownSafeBot.send_message
# (PTB bot objects are frozen, so it can't live on the bot itself).
LAST_SENT: dict = {}
# chat_id -> True while the reply keyboard on screen carries a Back key (kept in
# step with what send_message actually sent, so /cancel and menus clear it).
KB_HAS_BACK: dict = {}

logger = logging.getLogger(__name__)
_HIST = '_back_hist'
_MAX = 60
BACK_DATA = 'nav:back'
BACK_TEXT = '⬅️ Back'
# True while a wizard-step handler runs, so send_message knows its reply keyboards
# belong to a wizard and should carry a Back key.
_IN_STEP = contextvars.ContextVar('dag_in_wizard_step', default=False)
_BACK_ROW = [InlineKeyboardButton("⬅️ Back", callback_data=BACK_DATA)]


def _with_back(markup):
    """Inline markup carrying a Back button. Under an existing inline keyboard
    it is one more row; anything else (no keyboard, a reply keyboard, a
    keyboard-remove) gets just the button — editing an inline markup onto a
    message leaves an already-shown reply keyboard exactly as it was."""
    if isinstance(markup, InlineKeyboardMarkup):
        rows = [list(r) for r in markup.inline_keyboard]
        if any(b.callback_data == BACK_DATA for r in rows for b in r):
            return markup
        return InlineKeyboardMarkup(rows + [_BACK_ROW])
    return InlineKeyboardMarkup([_BACK_ROW])


def decorate_markup(markup, chat_id=None):
    """Give a wizard's reply keyboard a Back key, and keep KB_HAS_BACK truthful.

    Telegram refuses to edit an inline button onto a message that carries a
    reply keyboard, so Back has to ride in the keyboard itself. A keyboard-remove
    (which wizards send after a choice) becomes a keyboard holding only Back, so
    that step has a tap-able Back too; it stays on screen until the wizard ends."""
    in_step = _IN_STEP.get()
    result = markup
    if in_step and isinstance(markup, ReplyKeyboardRemove):
        result = ReplyKeyboardMarkup([[KeyboardButton(BACK_TEXT)]], resize_keyboard=True)
    elif in_step and isinstance(markup, ReplyKeyboardMarkup):
        rows = [list(r) for r in markup.keyboard]
        if not any((b.text if hasattr(b, 'text') else b) == BACK_TEXT for r in rows for b in r):
            result = ReplyKeyboardMarkup(
                rows + [[KeyboardButton(BACK_TEXT)]],
                resize_keyboard=markup.resize_keyboard,
                one_time_keyboard=markup.one_time_keyboard,
                selective=markup.selective,
                input_field_placeholder=markup.input_field_placeholder,
            )
    if chat_id is not None and isinstance(result, (ReplyKeyboardMarkup, ReplyKeyboardRemove)):
        KB_HAS_BACK[chat_id] = isinstance(result, ReplyKeyboardMarkup) and any(
            (b.text if hasattr(b, 'text') else b) == BACK_TEXT
            for r in result.keyboard for b in r)
    return result


def _snapshot(user_data) -> dict:
    return copy.deepcopy({k: v for k, v in user_data.items() if k != _HIST})


def track(handler, reset: bool = False):
    """Wrap a conversation handler so its state transitions become checkpoints.
    reset=True for entry points: a new flow starts with an empty history."""
    @functools.wraps(handler)
    async def wrapper(update, context):
        bot = context.bot
        chat_id = update.effective_chat.id if update.effective_chat else None
        if reset:
            context.user_data.pop(_HIST, None)
        if chat_id is not None:
            LAST_SENT.pop(chat_id, None)

        token = _IN_STEP.set(not reset)
        try:
            new_state = await handler(update, context)
        finally:
            _IN_STEP.reset(token)

        hist = context.user_data.setdefault(_HIST, [])
        if new_state is None:                       # stayed where it was
            return new_state
        if new_state == ConversationHandler.END:
            context.user_data.pop(_HIST, None)
            # The Back key would otherwise outlive the wizard; put the main menu
            # buttons back so the next thing is a tap, not a typed command.
            if chat_id is not None and KB_HAS_BACK.get(chat_id):
                await update.effective_message.reply_text(
                    "🏠 What next?",
                    reply_markup=ReplyKeyboardMarkup(MAIN_MENU_ROWS, resize_keyboard=True))
            return new_state
        if hist and hist[-1]['state'] == new_state:  # looped in the same state
            return new_state

        sent = LAST_SENT.get(chat_id) if chat_id is not None else None
        hist.append({
            'state': new_state,
            'snap': _snapshot(context.user_data),
            'prompt': ({k: sent[k] for k in ('text', 'parse_mode', 'reply_markup')}
                       if sent else None),
        })
        del hist[:-_MAX]
        logger.info(f"back-track: state={new_state} depth={len(hist)} prompt={'yes' if sent else 'NO'}")
        editable = sent and not KB_HAS_BACK.get(chat_id) and (sent.get('reply_markup') is None
                             or isinstance(sent.get('reply_markup'), InlineKeyboardMarkup))
        if editable and len(hist) > 1:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=sent['message_id'],
                    reply_markup=_with_back(sent.get('reply_markup')))
            except Exception as e:
                logger.warning(f"could not attach Back button: {e}")
        return new_state
    wrapper._tracked = True
    return wrapper


async def go_back(update, context):
    """/back or the ⬅️ Back button: undo the current step, re-ask the last one."""
    q = update.callback_query
    if q is not None:
        try:
            await q.answer()            # may already be answered (queued tap)
        except Exception:
            pass
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    msg = update.effective_message
    hist = context.user_data.get(_HIST) or []
    if len(hist) < 2:
        await msg.reply_text("Nothing to go back to — you're at the first step. "
                             "Send /cancel to stop.")
        return None                                 # stay in the current state

    popped = hist.pop()
    prev = hist[-1]
    # Keep everything answered up to the step being returned to (popped['snap'] is
    # the data as it stood on arrival at the step being left) — so going back into
    # a list step keeps its items and the user just adds or taps Done. Only the
    # answer to the step being left is discarded. Control flags (_edit_*, ...)
    # come from the returned-to step's own arrival, matching where we now are.
    data = copy.deepcopy(popped['snap'])
    for k in [k for k in data if k.startswith('_')]:
        del data[k]
    data.update({k: v for k, v in copy.deepcopy(prev['snap']).items() if k.startswith('_')})
    data[_HIST] = hist
    context.user_data.clear()
    context.user_data.update(data)

    token = _IN_STEP.set(True)
    try:
        await _resend(msg, context, hist, prev)
    finally:
        _IN_STEP.reset(token)
    return prev['state']


async def _resend(msg, context, hist, prev):
    prompt = prev['prompt']
    if prompt is None:
        await msg.reply_text("↩️ Went back a step.")
    else:
        markup = prompt.get('reply_markup')
        inline = isinstance(markup, InlineKeyboardMarkup)
        want_back = len(hist) > 1
        sent = await msg.reply_text(
            prompt['text'], parse_mode=prompt.get('parse_mode'),
            reply_markup=_with_back(markup) if (want_back and inline) else markup)
        if want_back and markup is None and not KB_HAS_BACK.get(sent.chat_id):
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=sent.chat_id, message_id=sent.message_id,
                    reply_markup=_with_back(None))
            except Exception as e:
                logger.warning(f"could not attach Back button: {e}")


def back_handlers(auth_filter):
    """Handlers to put first in every wizard state, ahead of the catch-alls
    (several states take any callback or any text)."""
    return [
        CommandHandler("back", go_back, filters=auth_filter),
        CallbackQueryHandler(go_back, pattern=f"^{BACK_DATA}$"),
        MessageHandler(filters.Regex(f"^{BACK_TEXT}$") & auth_filter, go_back),
    ]
