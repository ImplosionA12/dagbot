"""Mail Prompt flow.

Lists saved events → user picks one (or "New") → bot asks for a few mail-specific
fields → outputs a copy-pasteable prompt for an HTML email generator
(ChatGPT / Claude / similar).
"""
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes, MessageHandler, CallbackQueryHandler, filters, ConversationHandler
)

from config import fixed_profile
from event_store import list_events, get_event, upsert_flow
from handlers.ui import NONE_KB, resolve_text, md_escape, md_wrap, parse_event_date, DATE_HELP

# States
M_PICK, M_NEW_NAME, M_NEW_DATE, M_NEW_VENUE, M_NEW_DESC, \
M_AUDIENCE, M_REGISTRATION, M_EXTRA = range(400, 408)


async def mail_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['type'] = 'mail'
    context.user_data['mail'] = {}

    user_id = update.effective_user.id
    events = list_events(user_id)

    if not events:
        # No saved events yet — go straight to new event flow
        await update.message.reply_text(
            "📧 *Mail Prompt*\n\n"
            "No saved events yet. Let's collect the basics for a new one.\n\n"
            "*Event name?*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return M_NEW_NAME

    # Show event picker
    keyboard = []
    for ev in events[:8]:  # cap at 8 visible
        name = ev.get('name', ev.get('slug', '?'))
        keyboard.append([f"📂 {name}"])
    keyboard.append(["➕ New event"])

    await update.message.reply_text(
        "📧 *Mail Prompt*\n\n"
        "Pick an existing event or start a new one:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return M_PICK


async def mail_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip()
    user_id = update.effective_user.id

    if text.startswith('➕') or 'new event' in text.lower():
        await update.message.reply_text(
            "*Event name?*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return M_NEW_NAME

    # Strip "📂 " prefix and look up
    name = text.lstrip('📂 ').strip()
    events = list_events(user_id)
    match = next((e for e in events if e.get('name') == name), None)
    if not match:
        await update.message.reply_text("Please pick one from the menu.")
        return M_PICK

    # Pre-fill from event
    context.user_data['mail'].update({
        'event_name': match.get('name', ''),
        'date': '',
        'venue': '',
        'description': '',
        'slug': match.get('slug', ''),
    })

    # Best-effort: pull date/venue/description from any saved flow
    for flow_key in ('report', 'proposal', 'budget'):
        flow = match.get(flow_key) or {}
        if not context.user_data['mail']['date']:
            context.user_data['mail']['date'] = flow.get('date', '')
        if not context.user_data['mail']['venue']:
            context.user_data['mail']['venue'] = flow.get('venue', '')
        if not context.user_data['mail']['description']:
            context.user_data['mail']['description'] = flow.get('description', '') or flow.get('purpose', '')

    await update.message.reply_text(
        f"✅ Using event: {md_wrap(match.get('name', ''))}\n\n"
        f"*Target audience?*\n_(e.g. all students, club members, BCE students)_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return M_AUDIENCE


# ── New-event branch ────────────────────────────────────────────────────────
async def mail_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['mail']['event_name'] = update.message.text.strip()
    await update.message.reply_text(
        "*Event date?*\n\nFormat: DD-MM-YYYY",
        parse_mode="Markdown"
    )
    return M_NEW_DATE


async def mail_new_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date = parse_event_date(update.message.text)
    if not date:
        await update.message.reply_text(DATE_HELP, parse_mode="Markdown")
        return M_NEW_DATE
    context.user_data['mail']['date'] = date
    await update.message.reply_text(
        "*Venue?*\n\n_(or 'Online' if virtual)_",
        parse_mode="Markdown"
    )
    return M_NEW_VENUE


async def mail_new_venue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['mail']['venue'] = update.message.text.strip()
    await update.message.reply_text(
        "*Brief description of the event?*\n\n_(1-3 sentences — what's it about, who's it for)_",
        parse_mode="Markdown"
    )
    return M_NEW_DESC


async def mail_new_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['mail']['description'] = update.message.text.strip()
    await update.message.reply_text(
        "*Target audience?*\n_(e.g. all students, club members, BCE students)_",
        parse_mode="Markdown"
    )
    return M_AUDIENCE


# ── Shared tail (both new and existing event branches) ──────────────────────
async def mail_audience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['mail']['audience'] = update.message.text.strip()
    await update.message.reply_text(
        "*Registration link or contact?*\n_(URL, email, phone — tap None if not applicable)_",
        parse_mode="Markdown", reply_markup=NONE_KB
    )
    return M_REGISTRATION


async def mail_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip()
    context.user_data['mail']['registration'] = '' if text.lower() == 'none' else text
    await update.effective_message.reply_text(
        "*Anything else to highlight?*\n_(prizes, speakers, food, certificates — tap None to skip)_",
        parse_mode="Markdown", reply_markup=NONE_KB
    )
    return M_EXTRA


async def mail_extra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ((await resolve_text(update)) or '').strip()
    context.user_data['mail']['extra'] = '' if text.lower() == 'none' else text

    user_id = update.effective_user.id
    profile = fixed_profile()
    m = context.user_data['mail']

    prompt_text = _build_prompt(profile, m)

    # Send the generated prompt in a code block so it's tap-to-copy on mobile
    await update.effective_message.reply_text(
        "📧 *Here's your HTML mail prompt — tap and hold to copy:*",
        parse_mode="Markdown"
    )
    # Telegram limits messages to ~4096 chars. Wrap in a code block (no markdown inside).
    chunked = _chunk(prompt_text, 3800)
    for chunk in chunked:
        await update.effective_message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")

    # Save the prompt under the event for future reference
    try:
        upsert_flow(user_id, m.get('event_name', 'Untitled'), 'mail_prompt', m)
    except Exception:
        pass

    await update.effective_message.reply_text(
        "✅ Done. Paste this into ChatGPT / Claude / any LLM to get the full HTML email.\n\n"
        "Send /start to generate another document.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


# ────────────────────────────────────────────────────────────────────────────
# Prompt builder
# ────────────────────────────────────────────────────────────────────────────
def _build_prompt(profile: dict, m: dict) -> str:
    lines = [
        "You are a designer + copywriter. Generate a complete, production-ready HTML email "
        "announcing the event below. Use inline CSS only (no <style> tags, no external CSS) so "
        "it renders cleanly in Gmail, Outlook, and Apple Mail. Target ~600px max width, "
        "responsive on mobile, professional but visually engaging.",
        "",
        "STYLE: Clean modern card layout. Bold accent color tied to a creative/animation club. "
        "Clear hierarchy: hero header → event details block → description → CTA button → footer.",
        "",
        "INCLUDE:",
        "- Header banner with club name + event title (placeholder image acceptable: https://placehold.co/600x200)",
        "- Event details (date, venue, mode) as a tidy info block with icons (use emoji or unicode)",
        "- Description / purpose paragraph",
        "- Highlights list (if provided)",
        "- Prominent CTA button linking to the registration target",
        "- Footer with club name + contact",
        "",
        "OUTPUT FORMAT: A single self-contained HTML snippet I can paste directly into an email "
        "client's HTML view. No surrounding markdown, no <html>/<body> wrappers — just the email "
        "body markup ready to send.",
        "",
        "EVENT DETAILS:",
        f"- Club: {profile.get('club', '—')}",
        f"- Coordinator: {profile.get('coordinator', '—')}",
        f"- Event name: {m.get('event_name', '—')}",
        f"- Date: {m.get('date', '—')}",
        f"- Venue: {m.get('venue', '—')}",
    ]
    if m.get('description'):
        lines.append(f"- Description: {m['description']}")
    if m.get('audience'):
        lines.append(f"- Target audience: {m['audience']}")
    if m.get('registration'):
        lines.append(f"- Registration / Contact: {m['registration']}")
    if m.get('extra'):
        lines.append(f"- Highlights: {m['extra']}")
    lines.append("")
    lines.append("Tone: warm, energetic, inclusive. Avoid jargon. Keep copy short.")
    return "\n".join(lines)


def _chunk(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or ['']


# ────────────────────────────────────────────────────────────────────────────
# State map
# ────────────────────────────────────────────────────────────────────────────
MAIL_STATES = {
    M_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, mail_pick)],
    M_NEW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, mail_new_name)],
    M_NEW_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, mail_new_date)],
    M_NEW_VENUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, mail_new_venue)],
    M_NEW_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, mail_new_desc)],
    M_AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, mail_audience)],
    M_REGISTRATION: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, mail_registration),
        CallbackQueryHandler(mail_registration),
    ],
    M_EXTRA: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, mail_extra),
        CallbackQueryHandler(mail_extra),
    ],
}
