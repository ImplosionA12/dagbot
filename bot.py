import os
import re
import logging
from telegram import (
    Update, BotCommand, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    CallbackQueryHandler, PicklePersistence, filters, ContextTypes, ExtBot
)
from dotenv import load_dotenv

from config import fixed_profile, ALLOWED_USER_ID
from event_store import list_events, get_event, delete_event, event_status_label, event_folder
from handlers.ui import resolve_text, md_escape, md_wrap, MAIN_MENU_ROWS
from handlers.busy import PerUserUpdateProcessor
from handlers.back import track, back_handlers, decorate_markup, LAST_SENT


class MarkdownSafeBot(ExtBot):
    """Never let a stray Markdown character swallow a message.

    Nearly every reply here is sent with parse_mode="Markdown" and interpolates
    text the user typed. One unmatched '_' or '*' in an event name or a budget
    line item — "first_prize" is enough — makes Telegram reject the whole
    message with "can't parse entities". The wizard step then dies mid-handler:
    no summary, no keyboard, and the conversation sits in a state the user can't
    see. Resending unformatted is worse-looking than the intent but always
    arrives, so a stray underscore costs a bit of bold, not the flow.
    """

    async def send_message(self, *args, **kwargs):
        if kwargs.get('reply_markup') is not None:
            kwargs['reply_markup'] = decorate_markup(
                kwargs['reply_markup'], kwargs.get('chat_id', args[0] if args else None))
        try:
            msg = await super().send_message(*args, **kwargs)
        except BadRequest as e:
            if kwargs.get('parse_mode') is None or "can't parse entities" not in str(e).lower():
                raise
            logger.warning(f"markdown rejected, resending as plain text: {e}")
            msg = await super().send_message(*args, **{**kwargs, 'parse_mode': None})
        try:
            text = kwargs.get('text', args[1] if len(args) > 1 else None)
            # handlers.back.track() reads this to learn which prompt a step ended on.
            LAST_SENT[msg.chat_id] = {
                'text': text, 'parse_mode': kwargs.get('parse_mode'),
                'reply_markup': kwargs.get('reply_markup'), 'message_id': msg.message_id,
            }
        except Exception:
            pass
        return msg

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO, and Telegram URLs carry the bot token —
# the logs held it in plain text thousands of times over.
logging.getLogger('httpx').setLevel(logging.WARNING)

# ── States ──────────────────────────────────────────────────────────────────

MAIN_MENU = 0

# Events viewer
EVENTS_MENU, EVENTS_DETAIL, EVENTS_FILES, EVENTS_DELETE = range(80, 84)
EVENTS_REGEN = 84

# Proposal states
P_CLUB, P_COORD, P_EVENT, P_VENUE, P_DATE, P_DURATION, P_BUDGET, P_EXTRA, \
P_PURPOSE, P_DESCRIPTION, P_BUDGET_ITEMS, P_CONFIRM = range(100, 112)

# Budget form states
B_CLUB, B_COORD, B_EMPID, B_EVENT, B_DATE, \
B_REV_ITEMS, B_EXP_ITEMS, B_CONFIRM = range(200, 208)

# Final report states
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
R_HAS_FEEDBACK, R_FEEDBACK_FILES, \
R_HAS_EXPENDITURE, R_EXPENDITURE_FILES, \
R_CONFIRM = range(300, 330)

from handlers.proposal import _proposal_summary, P_CONFIRM as P_CONFIRM_STATE
from handlers.budget import _budget_summary, B_CONFIRM as B_CONFIRM_STATE
from handlers.report import _report_summary, R_CONFIRM as R_CONFIRM_STATE
from handlers.proposal import (
    proposal_start,
    proposal_club, proposal_coord, proposal_event, proposal_venue,
    proposal_date, proposal_duration, proposal_budget, proposal_extra,
    proposal_purpose, proposal_description,
    proposal_budget_item, proposal_confirm,
    PROPOSAL_STATES
)
from handlers.budget import (
    budget_start,
    budget_club, budget_coord, budget_empid, budget_event, budget_date,
    budget_rev_item, budget_exp_item, budget_confirm,
    BUDGET_STATES
)
from handlers.report import report_start, REPORT_STATES
from handlers.mail_prompt import mail_start, MAIL_STATES
from handlers.upload import (
    upload_start, upload_cancel, UPLOAD_STATES, KIND_LABELS, KIND_PREFIX
)


# ────────────────────────────────────────────────────────────────────────────
# /start — straight to menu (no setup needed, config is fixed)
# ────────────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    return await _show_main_menu(update)


async def _show_main_menu(update: Update):
    profile = fixed_profile()
    keyboard = MAIN_MENU_ROWS
    await update.effective_message.reply_text(
        f"👋 *DAG Club Documentation Bot*\n\n"
        f"🏛 *Club:* {profile['club']}\n"
        f"👤 *Coordinator:* {profile['coordinator']}\n"
        f"🆔 *Emp ID:* {profile['emp_id']}\n\n"
        f"What do you want to do?\n"
        f"_(/events to see past events, /cancel to abort any flow)_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return MAIN_MENU


def _event_artifacts(user_id: int, slug: str) -> tuple[list[str], str]:
    """Generated files (everything except event.json) in this event's folder."""
    folder = event_folder(user_id, slug)
    files = sorted(
        f for f in os.listdir(folder)
        # 'event.json.tmp' is save_event's in-flight write; a crash can leave one
        # behind and it must never be offered as a generated document.
        if not f.startswith('event.json') and os.path.isfile(os.path.join(folder, f))
    )
    return files, folder


# ────────────────────────────────────────────────────────────────────────────
# Main menu router
# ────────────────────────────────────────────────────────────────────────────
async def main_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if "Proposal" in text and "Mail" not in text:
        return await proposal_start(update, context)
    if "Budget" in text:
        return await budget_start(update, context)
    if "Report" in text:
        return await report_start(update, context)
    if "Mail" in text:
        return await mail_start(update, context)
    if "Events" in text or "📂" in text:
        return await events_cmd(update, context)
    await update.message.reply_text("Please choose an option from the menu.")
    return MAIN_MENU


# ────────────────────────────────────────────────────────────────────────────
# /events — list, view, delete
# ────────────────────────────────────────────────────────────────────────────
async def events_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    events = list_events(user_id)

    if not events:
        await update.effective_message.reply_text(
            "No saved events yet.\n\nGenerate a proposal, budget, or report — they'll auto-save here.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    lines = ["📂 *Your Events*\n"]
    keyboard = []
    for ev in events[:20]:
        status = event_status_label(ev)
        name = ev.get('name', ev.get('slug', '?'))
        updated = ev.get('updated_at', '')[:10]
        lines.append(f"• {md_wrap(name)} — `{status}` _(updated {updated})_")
        keyboard.append([f"📂 {name}"])

    lines.append("\n_P = Proposal · B = Budget · R = Report_\n")
    lines.append("Tap an event to view, or /start to go back.")

    keyboard.append(["⬅️ Back to menu"])

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return EVENTS_MENU


async def _show_event_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Render the detail view for the event slug stored in user_data."""
    user_id = update.effective_user.id
    slug = context.user_data.get('_event_slug', '')
    match = get_event(user_id, slug)
    if not match:
        return await events_cmd(update, context)

    has_proposal = bool(match.get('proposal'))
    has_budget = bool(match.get('budget'))
    has_report = bool(match.get('report'))
    has_mail = bool(match.get('mail_prompt'))
    artifacts, _ = _event_artifacts(user_id, slug)

    lines = [
        f"{md_wrap(match.get('name', '?'))}\n",
        f"📅 Created: {match.get('created_at', '?')[:10]}",
        f"🔄 Updated: {match.get('updated_at', '?')[:10]}",
        "",
        f"{'✅' if has_proposal else '⬜'} Event Proposal",
        f"{'✅' if has_budget else '⬜'} Budget Form",
        f"{'✅' if has_report else '⬜'} Final Event Report",
        f"{'✅' if has_mail else '⬜'} Mail Prompt",
        "",
        f"📎 Generated files: {len(artifacts)}",
    ]
    uploads = match.get('uploads') or []
    if uploads:
        lines.append(f"📤 Attached by you: {len(uploads)}")
    flow = match.get('report') or match.get('proposal') or match.get('budget') or {}
    if flow.get('venue'):
        lines.append(f"\n📍 Venue: {md_escape(flow['venue'])}")
    if flow.get('date'):
        lines.append(f"📅 Date: {md_escape(flow['date'])}")

    keyboard = [
        ["📥 Download files"],
        *([["✏️ Edit & regenerate"]] if (has_proposal or has_budget or has_report) else []),
        ["🗑 Delete a file", "🗑 Delete this event"],
        ["⬅️ Back to events"],
    ]
    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return EVENTS_DETAIL


async def events_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip()
    user_id = update.effective_user.id

    if "Back" in text or "menu" in text.lower():
        return await _show_main_menu(update)

    name = text.lstrip('📂 ').strip()
    events = list_events(user_id)
    match = next((e for e in events if e.get('name') == name), None)
    if not match:
        await update.message.reply_text("Please pick one from the menu.")
        return EVENTS_MENU

    context.user_data['_event_slug'] = match.get('slug', '')
    return await _show_event_detail(update, context)


def _group_artifacts(artifacts: list[str]) -> list[tuple[str, str, list[str]]]:
    """Group an event's files by the document they belong to.

    Each document exists in several formats (docx/xlsx/pdf), and listing every
    one made the delete menu a wall of near-identical names. Returns
    [(kind, label, filenames)] for the kinds that actually have files."""
    groups = []
    for kind, label in KIND_LABELS.items():
        prefix = KIND_PREFIX[kind].lower() + '_'
        files = [f for f in artifacts if f.lower().startswith(prefix)]
        if files:
            groups.append((kind, label, files))
    known = {f for _, _, files in groups for f in files}
    other = [f for f in artifacts if f not in known]
    if other:
        groups.append(('other', '📎 Other files', other))
    return groups


def _files_keyboard(groups: list[tuple[str, str, list[str]]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"🗑 {label} · {len(files)} file(s)",
                              callback_data=f"delkind:{kind}")]
        for kind, label, files in groups
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="delkind:back")])
    return InlineKeyboardMarkup(rows)


async def events_detail_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip()
    user_id = update.effective_user.id
    slug = context.user_data.get('_event_slug', '')

    if "Download" in text:
        artifacts, folder = _event_artifacts(user_id, slug)
        if not artifacts:
            await update.message.reply_text(
                "📭 No generated files yet for this event.\n"
                "Run a Proposal / Budget / Report and pick this event to attach docs."
            )
            return EVENTS_DETAIL
        await update.message.reply_text(f"📥 Sending {len(artifacts)} file(s)…")
        for fn in artifacts:
            try:
                with open(os.path.join(folder, fn), 'rb') as f:
                    await update.message.reply_document(f, filename=fn)
            except Exception as e:
                await update.message.reply_text(f"⚠️ Could not send {fn}: {e}")
        return await _show_event_detail(update, context)

    if "Edit & regenerate" in text:
        return await _regen_menu(update, context)

    if "Delete a file" in text:
        artifacts, _ = _event_artifacts(user_id, slug)
        if not artifacts:
            await update.message.reply_text("📭 No generated files to delete for this event.")
            return EVENTS_DETAIL
        await update.message.reply_text(
            "Tap a document to delete it — all its formats (Word/Excel/PDF) go "
            "together. The saved form data stays, so you can regenerate anytime:",
            reply_markup=_files_keyboard(_group_artifacts(artifacts))
        )
        return EVENTS_FILES

    if "Delete this event" in text:
        artifacts, _ = _event_artifacts(user_id, slug)
        await update.message.reply_text(
            f"⚠️ This permanently deletes the event, its saved form data and "
            f"{len(artifacts)} generated file(s).\n\nAre you sure?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑 Yes, delete", callback_data="delev:yes"),
                InlineKeyboardButton("❌ Keep it", callback_data="delev:no"),
            ]])
        )
        return EVENTS_DELETE

    if "Back" in text:
        return await events_cmd(update, context)

    await update.message.reply_text("Please pick an option.")
    return EVENTS_DETAIL


_REGEN_FLOWS = (
    ('proposal', '📄 Event Proposal'),
    ('budget', '💰 Budget Form'),
    ('report', '📋 Final Event Report'),
)


async def _regen_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pick which saved document of this event to edit and regenerate."""
    match = get_event(update.effective_user.id, context.user_data.get('_event_slug', ''))
    have = [(k, label) for k, label in _REGEN_FLOWS if match and match.get(k)]
    if not have:
        await update.effective_message.reply_text("Nothing saved to edit for this event yet.")
        return EVENTS_DETAIL
    rows = [[InlineKeyboardButton(label, callback_data=f"regen:{k}")] for k, label in have]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="regen:back")])
    await update.effective_message.reply_text(
        "✏️ *Which document?*" + chr(10)*2 + "Its saved answers open on the summary screen — fix "
        "whatever is wrong with *Edit a field*, then *Confirm* to regenerate the Word "
        "and PDF. No need to edit the file by hand.",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    return EVENTS_REGEN


async def events_regen_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import copy
    text = (await resolve_text(update) or '').strip()
    if not text.startswith('regen:') or text == 'regen:back':
        return await _show_event_detail(update, context)

    flow = text.split(':', 1)[1]
    match = get_event(update.effective_user.id, context.user_data.get('_event_slug', ''))
    data = (match or {}).get(flow)
    if flow not in dict(_REGEN_FLOWS) or not data:
        await update.effective_message.reply_text("⚠️ That document isn't saved any more.")
        return await _show_event_detail(update, context)

    # Same shape the wizard keeps while running, so its summary, Edit menu and
    # Confirm work unchanged.
    context.user_data['type'] = flow
    context.user_data[flow] = copy.deepcopy(data)
    summary_fn, confirm_state = {
        'proposal': (_proposal_summary, P_CONFIRM_STATE),
        'budget': (_budget_summary, B_CONFIRM_STATE),
        'report': (_report_summary, R_CONFIRM_STATE),
    }[flow]
    return await summary_fn(update, context)


async def events_file_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (await resolve_text(update) or '').strip()
    user_id = update.effective_user.id
    slug = context.user_data.get('_event_slug', '')

    if text == 'delkind:back' or 'back' in text.lower():
        return await _show_event_detail(update, context)

    if text.startswith('delkind:'):
        kind = text.split(':', 1)[1]
        artifacts, folder = _event_artifacts(user_id, slug)
        files = next((f for k, _, f in _group_artifacts(artifacts) if k == kind), [])
        if not files:
            await update.effective_message.reply_text("⚠️ Those files no longer exist.")
        else:
            deleted, failed = [], []
            for fn in files:
                try:
                    os.remove(os.path.join(folder, fn))
                    deleted.append(fn)
                except OSError as e:
                    failed.append(f"{fn} ({e})")
            if deleted:
                await update.effective_message.reply_text(
                    "🗑 Deleted:\n" + "\n".join(f"• {f}" for f in deleted))
            if failed:
                await update.effective_message.reply_text(
                    "⚠️ Could not delete:\n" + "\n".join(f"• {f}" for f in failed))

        remaining, _ = _event_artifacts(user_id, slug)
        if not remaining:
            await update.effective_message.reply_text("📭 No generated files left.")
            return await _show_event_detail(update, context)
        await update.effective_message.reply_text(
            "Anything else to delete?",
            reply_markup=_files_keyboard(_group_artifacts(remaining))
        )
        return EVENTS_FILES

    await update.effective_message.reply_text("Please use the buttons above.")
    return EVENTS_FILES


async def events_delete_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (await resolve_text(update) or '').strip().lower()
    user_id = update.effective_user.id
    slug = context.user_data.get('_event_slug', '')

    if text in ('delev:yes', 'yes'):
        ok = delete_event(user_id, slug)
        await update.effective_message.reply_text(
            "🗑 Event and its files deleted." if ok else "⚠️ Could not delete event.",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.pop('_event_slug', None)
        return await events_cmd(update, context)

    return await _show_event_detail(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Cancelled. Send /start to begin again.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


async def _post_init(app: Application):
    """Register the command menu (the blue '/' button in Telegram)."""
    await app.bot.set_my_commands([
        BotCommand("start", "Main menu"),
        BotCommand("proposal", "New Event Proposal"),
        BotCommand("budget", "New Budget Form"),
        BotCommand("report", "New Final Event Report"),
        BotCommand("mail", "Generate a mail prompt"),
        BotCommand("events", "View saved events"),
        BotCommand("back", "Go back one step"),
        BotCommand("cancel", "Cancel the current flow"),
    ])


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Without this, PTB logs "No error handlers are registered" and stops: an
    exception anywhere in a wizard step leaves the user with no reply at all and
    no idea the step failed. Say so, and leave them in the state they were in."""
    logger.error("unhandled exception while processing an update", exc_info=context.error)
    msg = getattr(update, 'effective_message', None) if isinstance(update, Update) else None
    if msg is None:
        return                          # polling/network hiccup — nobody to tell
    try:
        await msg.reply_text(
            "⚠️ Something went wrong handling that. The step wasn't saved — "
            "try it again, or send /cancel to start over."
        )
    except Exception:
        logger.exception("could not deliver the error notice")


def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN not set in .env")

    # Conversation state lived only in memory, so every restart left the reply
    # keyboards on the user's screen pointing at states the bot had forgotten —
    # tapping one did nothing at all. Persisting it makes restarts invisible.
    # NSSM stops the service by killing the process, so PTB never gets to flush
    # on shutdown — the default 60s interval would throw away a minute of state
    # on every restart, which is exactly what this is here to prevent.
    persistence = PicklePersistence(
        filepath=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dagbot_state.pickle'),
        update_interval=10,
    )
    app = (Application.builder().bot(MarkdownSafeBot(token))
           .persistence(persistence).post_init(_post_init)
           .concurrent_updates(PerUserUpdateProcessor()).build())

    auth_filter = filters.User(user_id=ALLOWED_USER_ID)

    # Belt and braces for the main menu: a tap on a stale main-menu button
    # re-enters the conversation instead of falling through to nothing. Only
    # exact main-menu labels — the wizards' own "📂 <event>" pick keyboards must
    # keep reaching the wizard, and entry points are matched before states.
    menu_labels = "|".join(re.escape(lbl) for lbl in (
        "📄 Event Proposal", "💰 Budget Form", "📋 Final Event Report",
        "📧 Mail Prompt", "📂 My Events",
    ))

    # /back support: wizard steps become checkpoints; entry points reset them.
    wizard_states = {**PROPOSAL_STATES, **BUDGET_STATES, **REPORT_STATES, **MAIL_STATES}
    for handlers_ in wizard_states.values():
        for h in handlers_:
            if not getattr(h.callback, '_tracked', False):
                h.callback = track(h.callback)
    wizard_states = {st: back_handlers(auth_filter) + list(hs)
                     for st, hs in wizard_states.items()}

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", track(start, reset=True), filters=auth_filter),
            CommandHandler("events", track(events_cmd, reset=True), filters=auth_filter),
            CommandHandler("proposal", track(proposal_start, reset=True), filters=auth_filter),
            CommandHandler("budget", track(budget_start, reset=True), filters=auth_filter),
            CommandHandler("report", track(report_start, reset=True), filters=auth_filter),
            CommandHandler("mail", track(mail_start, reset=True), filters=auth_filter),
            MessageHandler(filters.Regex(rf"^({menu_labels})$") & auth_filter,
                           track(main_menu_choice, reset=True)),
        ],
        states={
            MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, track(main_menu_choice))],
            EVENTS_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, events_menu_choice)],
            EVENTS_DETAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, events_detail_choice)],
            EVENTS_FILES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, events_file_delete),
                CallbackQueryHandler(events_file_delete),
            ],
            EVENTS_DELETE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, events_delete_event),
                CallbackQueryHandler(events_delete_event),
            ],
            EVENTS_REGEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, track(events_regen_pick)),
                CallbackQueryHandler(track(events_regen_pick)),
            ],
            **wizard_states,
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        persistent=True,
        name="dag_main",
    )

    # Registered after the main conversation on purpose: while the report wizard
    # is waiting for a signed scan, that upload belongs to the wizard, not here.
    upload_conv = ConversationHandler(
        entry_points=[
            MessageHandler((filters.Document.ALL | filters.PHOTO) & auth_filter,
                           upload_start),
        ],
        states=UPLOAD_STATES,
        fallbacks=[CommandHandler("cancel", upload_cancel)],
        allow_reentry=True,
        persistent=True,
        name="dag_upload",
    )

    app.add_handler(conv_handler)
    app.add_handler(upload_conv)

    async def deny_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message:
            await update.message.reply_text(
                "🔒 This bot is private. Access is restricted to its owner."
            )

    app.add_handler(
        MessageHandler(~auth_filter, deny_unauthorized),
    )

    # Last resort, same group so it only runs when nothing above claimed the
    # update: an authorized message that matches no state used to vanish
    # silently, which is what made a stale button look like a dead bot.
    async def stale_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.effective_message.reply_text(
            "That menu is from an earlier session, so I've lost track of where "
            "you were.\n\nSend /start to begin again.",
            reply_markup=ReplyKeyboardRemove()
        )

    async def stale_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer(
            "This button has expired — send /start.", show_alert=True
        )

    # /cancel is a conversation fallback, so with nothing in progress it reaches
    # neither conversation. Answer it here rather than let the nudge below claim it.
    async def idle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await update.effective_message.reply_text(
            "Nothing in progress. Send /start to begin.",
            reply_markup=ReplyKeyboardRemove()
        )

    async def idle_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.effective_message.reply_text(
            "Nothing to go back to. Send /start to begin.")

    app.add_handler(CommandHandler("cancel", idle_cancel, filters=auth_filter))
    app.add_handler(CommandHandler("back", idle_back, filters=auth_filter))
    app.add_handler(MessageHandler(auth_filter, stale_message))
    app.add_handler(CallbackQueryHandler(stale_button))

    app.add_error_handler(on_error)

    logger.info("DAG Bot started (locked to user_id=%s)...", ALLOWED_USER_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
