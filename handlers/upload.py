"""Attach a shared file to an event.

A document or photo that arrives outside any wizard gets offered a home: the
bot asks whether it belongs to an event, which one, and whether it's the
Proposal / Budget / Report — then files it under the same name the generators
use, so a signed scan lands beside the original it replaces.

This lives in its own ConversationHandler, registered *after* the main one, so
the uploads the report wizard is waiting for still reach the wizard.
"""
import os
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, MessageHandler, CallbackQueryHandler, filters, ConversationHandler
)

from event_store import list_events, get_event, save_event, event_folder, slugify
from handlers.ui import resolve_text

UP_ASK, UP_EVENT, UP_NEW_NAME, UP_KIND, UP_REPLACE = range(90, 95)

KIND_LABELS = {
    'proposal': '📄 Event Proposal',
    'budget': '💰 Budget Form',
    'report': '📋 Final Event Report',
}
KIND_PREFIX = {'proposal': 'Proposal', 'budget': 'Budget', 'report': 'Report'}


def kind_files(folder: str, kind: str) -> list[str]:
    """Files in this event folder belonging to one document type."""
    prefix = KIND_PREFIX[kind].lower() + '_'
    if not os.path.isdir(folder):
        return []
    return sorted(f for f in os.listdir(folder)
                  if f.lower().startswith(prefix)
                  and os.path.isfile(os.path.join(folder, f)))


def _file_stem(event_name: str) -> str:
    """The generators' filename convention, so uploads sit beside originals."""
    return (event_name or 'Event').replace(' ', '_').replace('/', '_')


async def upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: any document or photo sent outside a wizard."""
    msg = update.effective_message
    if msg.document:
        file_id = msg.document.file_id
        name = msg.document.file_name or 'file'
        ext = '.' + name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    elif msg.photo:
        file_id, name, ext = msg.photo[-1].file_id, 'photo.jpg', '.jpg'
    else:
        return ConversationHandler.END

    context.user_data['_upload'] = {'file_id': file_id, 'name': name, 'ext': ext}
    await msg.reply_text(
        f"📎 {name}\n\nDoes this file belong to one of your events?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes", callback_data="up:yes"),
            InlineKeyboardButton("🚫 No", callback_data="up:no"),
        ]])
    )
    return UP_ASK


async def _expired(update: Update):
    await update.effective_message.reply_text(
        "That upload is no longer pending — send the file again."
    )
    return ConversationHandler.END


async def upload_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (await resolve_text(update) or '').strip().lower()
    if not context.user_data.get('_upload'):
        return await _expired(update)

    if text in ('up:no', 'no', 'n'):
        context.user_data.pop('_upload', None)
        await update.effective_message.reply_text("👍 Ignored — nothing saved.")
        return ConversationHandler.END
    if text not in ('up:yes', 'yes', 'y'):
        await update.effective_message.reply_text("Please tap Yes or No.")
        return UP_ASK
    return await _ask_event(update, context)


async def _ask_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    events = list_events(update.effective_user.id)[:20]
    context.user_data['_upload']['slugs'] = [e.get('slug', '') for e in events]

    if not events:
        await update.effective_message.reply_text(
            "You have no saved events yet.\n\nWhat's the event called?"
        )
        return UP_NEW_NAME

    rows = [[InlineKeyboardButton(f"📂 {e.get('name', e.get('slug', '?'))}",
                                  callback_data=f"upev:{i}")]
            for i, e in enumerate(events)]
    rows.append([InlineKeyboardButton("➕ New event", callback_data="upev:new")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="upev:cancel")])
    await update.effective_message.reply_text("Which event does it belong to?",
                                              reply_markup=InlineKeyboardMarkup(rows))
    return UP_EVENT


async def upload_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (await resolve_text(update) or '').strip()
    up = context.user_data.get('_upload')
    if not up:
        return await _expired(update)

    if text == 'upev:cancel':
        context.user_data.pop('_upload', None)
        await update.effective_message.reply_text("❌ Cancelled — nothing saved.")
        return ConversationHandler.END

    if text == 'upev:new':
        await update.effective_message.reply_text("What's the event called?")
        return UP_NEW_NAME

    if text.startswith('upev:'):
        try:
            up['slug'] = up['slugs'][int(text.split(':', 1)[1])]
        except (ValueError, IndexError):
            await update.effective_message.reply_text("That event is gone — send the file again.")
            return ConversationHandler.END
        return await _ask_kind(update, context)

    await update.effective_message.reply_text("Please pick an event from the buttons.")
    return UP_EVENT


async def upload_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or '').strip()
    up = context.user_data.get('_upload')
    if not up:
        return await _expired(update)
    if not name:
        await update.message.reply_text("Please type the event name.")
        return UP_NEW_NAME

    user_id = update.effective_user.id
    slug = slugify(name)
    if get_event(user_id, slug) is None:
        save_event(user_id, {'name': name, 'slug': slug})
    up['slug'] = slug
    return await _ask_kind(update, context)


async def _ask_kind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    up = context.user_data['_upload']
    event = get_event(update.effective_user.id, up['slug']) or {}
    rows = [[InlineKeyboardButton(label, callback_data=f"upkind:{kind}")]
            for kind, label in KIND_LABELS.items()]
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="upkind:cancel")])
    await update.effective_message.reply_text(
        f"Filing under: {event.get('name', up['slug'])}\n\nWhich document is it?",
        reply_markup=InlineKeyboardMarkup(rows)
    )
    return UP_KIND


async def upload_kind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (await resolve_text(update) or '').strip()
    up = context.user_data.get('_upload')
    if not up:
        return await _expired(update)

    if text == 'upkind:cancel':
        context.user_data.pop('_upload', None)
        await update.effective_message.reply_text("❌ Cancelled — nothing saved.")
        return ConversationHandler.END

    kind = text.split(':', 1)[1] if text.startswith('upkind:') else ''
    if kind not in KIND_LABELS:
        await update.effective_message.reply_text("Please pick a document type from the buttons.")
        return UP_KIND

    user_id = update.effective_user.id
    event = get_event(user_id, up['slug']) or {}
    folder = event_folder(user_id, up['slug'])
    up['kind'] = kind
    up['target'] = f"{KIND_PREFIX[kind]}_{_file_stem(event.get('name', ''))}{up['ext']}"

    existing = kind_files(folder, kind)
    if not existing:
        return await _save(update, context)

    # Only the same name gets overwritten — the other formats of this document
    # (e.g. the editable .docx behind a signed .pdf) are left alone.
    keep = [f for f in existing if f != up['target']]
    lines = [f"This event already has {KIND_LABELS[kind]} files:"]
    lines += [f"• {f}" for f in existing]
    lines.append("")
    lines.append(f"↻ Replaces: {up['target']}" if up['target'] in existing
                 else f"➕ Adds: {up['target']}")
    if keep:
        lines.append("✅ Keeps: " + ", ".join(keep))

    action = "♻️ Replace" if up['target'] in existing else "➕ Add"
    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(action, callback_data="upsave:yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="upsave:no"),
        ]])
    )
    return UP_REPLACE


async def upload_replace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (await resolve_text(update) or '').strip().lower()
    if not context.user_data.get('_upload'):
        return await _expired(update)

    if text in ('upsave:yes', 'replace', 'add', 'yes'):
        return await _save(update, context)

    context.user_data.pop('_upload', None)
    await update.effective_message.reply_text("❌ Cancelled — the existing files are untouched.")
    return ConversationHandler.END


async def _save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    up = context.user_data['_upload']
    user_id = update.effective_user.id
    folder = event_folder(user_id, up['slug'])
    path = os.path.join(folder, up['target'])

    try:
        tg_file = await context.bot.get_file(up['file_id'])
        await tg_file.download_to_drive(path)
    except Exception as e:
        await update.effective_message.reply_text(f"⚠️ Could not save the file: {e}")
        context.user_data.pop('_upload', None)
        return ConversationHandler.END

    event = get_event(user_id, up['slug']) or {'slug': up['slug'], 'name': up['slug']}
    uploads = [u for u in event.get('uploads', []) if u.get('file') != up['target']]
    uploads.append({
        'kind': up['kind'],
        'file': up['target'],
        'original_name': up['name'],
        'at': datetime.now().isoformat(timespec='seconds'),
    })
    event['uploads'] = uploads
    save_event(user_id, event)

    await update.effective_message.reply_text(
        f"✅ Saved as {up['target']}\nunder {event.get('name', up['slug'])} "
        f"({KIND_LABELS[up['kind']]}).\n\n/events to see it."
    )
    context.user_data.pop('_upload', None)
    return ConversationHandler.END


async def upload_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('_upload', None)
    await update.effective_message.reply_text("❌ Cancelled — nothing saved.")
    return ConversationHandler.END


UPLOAD_STATES = {
    UP_ASK: [
        CallbackQueryHandler(upload_ask),
        MessageHandler(filters.TEXT & ~filters.COMMAND, upload_ask),
    ],
    UP_EVENT: [
        CallbackQueryHandler(upload_event),
        MessageHandler(filters.TEXT & ~filters.COMMAND, upload_event),
    ],
    UP_NEW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, upload_new_name)],
    UP_KIND: [
        CallbackQueryHandler(upload_kind),
        MessageHandler(filters.TEXT & ~filters.COMMAND, upload_kind),
    ],
    UP_REPLACE: [
        CallbackQueryHandler(upload_replace),
        MessageHandler(filters.TEXT & ~filters.COMMAND, upload_replace),
    ],
}
