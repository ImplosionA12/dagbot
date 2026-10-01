import os
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes, MessageHandler, CallbackQueryHandler, filters, ConversationHandler
)

from config import fixed_profile
from event_store import list_events, event_status_label
from handlers.ui import (
    DONE_KB, NONE_KB, CONFIRM_KB, CONFIRM_EDIT_KB, OVERFLOW_KB, RETRY_KB, resolve_text, md_escape, md_wrap
)
from handlers.edit_fields import Field, make_edit_handlers, resume_after_edit

# States
P_CLUB, P_COORD, P_EVENT, P_VENUE, P_DATE, P_DURATION, P_BUDGET, P_EXTRA, \
P_PURPOSE, P_DESCRIPTION, P_BUDGET_ITEMS, P_CONFIRM, P_PICK, \
P_OVERFLOW, P_EDIT_PURPOSE, P_EDIT_DESC, \
P_EDIT_PICK, P_EDIT_VALUE = range(100, 118)

_ITEMS_PROMPT = (
    "*Budget Items* — send them again, one per message:\n"
    "`Item Name, Qty x Rate`\n\nE.g. `Posters, 10 x 20`\n\nTap *Done* when finished."
)

PROPOSAL_EDIT_FIELDS = [
    Field('event_name', '📛 Event name', 'Send the new event name:'),
    Field('venue', '📍 Venue', 'Send the new venue:'),
    Field('date', '📅 Date', 'Send the new date (DD-MM-YYYY):'),
    Field('duration', '⏱ Duration', 'Send the new duration:'),
    Field('total_budget', '💰 Total budget', 'Send the new total budget:'),
    Field('extra', '🧰 Extra requirements', 'Send the new extra requirements:'),
    Field('purpose', '📝 Purpose', 'Send the new Purpose text:'),
    Field('description', '📄 Brief description', 'Send the new Brief Description text:'),
    Field('budget_items', '📊 Budget items', '', restart_state=P_BUDGET_ITEMS,
          restart_prompt=_ITEMS_PROMPT),
]


async def proposal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['type'] = 'proposal'
    profile = fixed_profile()

    context.user_data['proposal'] = {
        'club': profile['club'],
        'coordinator': profile['coordinator'],
        'coordinator_phone': profile.get('coordinator_phone', ''),
    }

    user_id = update.effective_user.id
    events = list_events(user_id)
    if events:
        keyboard = [[f"📂 {ev.get('name', ev.get('slug', '?'))}"] for ev in events[:8]]
        keyboard.append(["➕ New event"])
        await update.message.reply_text(
            "📄 *Event Proposal*\n\n"
            "Pick an existing event or start a new one:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )
        return P_PICK

    await update.message.reply_text(
        f"📄 *Event Proposal*\n\n*Event name?*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return P_EVENT


async def proposal_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip()
    user_id = update.effective_user.id

    if text.startswith('➕') or 'new event' in text.lower():
        await update.message.reply_text(
            "*Event name?*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return P_EVENT

    name = text.lstrip('📂 ').strip()
    events = list_events(user_id)
    match = next((e for e in events if e.get('name') == name), None)
    if not match:
        await update.message.reply_text("Please pick one from the menu.")
        return P_PICK

    p = context.user_data['proposal']
    p['event_name'] = match.get('name', '')
    # Best-effort prefill from any prior flow on this event
    for flow_key in ('proposal', 'report', 'budget'):
        flow = match.get(flow_key) or {}
        if not p.get('venue'):
            p['venue'] = flow.get('venue', '')
        if not p.get('date'):
            p['date'] = flow.get('date', '')

    msg = f"✅ Using event: {md_wrap(p['event_name'])}\n\n"
    if p.get('venue') and p.get('date'):
        msg += f"_(Venue: {p['venue']} · Date: {p['date']})_\n\n*Duration?*\nE.g. 1.5 hours (9:00 PM – 10:30 PM)"
        next_state = P_DURATION
    elif p.get('venue'):
        msg += f"_(Venue: {p['venue']})_\n\n*Date (Proposed)?*\nFormat: DD-MM-YYYY"
        next_state = P_DATE
    else:
        msg += "*Venue (Proposed)?*\nE.g. Einstein Hall, AB-1 or Online"
        next_state = P_VENUE
    await update.message.reply_text(msg, parse_mode="Markdown",
                                    reply_markup=ReplyKeyboardRemove())
    return next_state


async def proposal_club(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['proposal']['club'] = update.message.text
    await update.message.reply_text("2️⃣ *Coordinator name and phone number?*\n\nFormat: Dr. Name, 9XXXXXXXXX", parse_mode="Markdown")
    return P_COORD


async def proposal_coord(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['proposal']['coordinator'] = update.message.text
    await update.message.reply_text("3️⃣ *Event name?*", parse_mode="Markdown")
    return P_EVENT


async def proposal_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['proposal']['event_name'] = update.message.text
    await update.message.reply_text("4️⃣ *Venue (Proposed)?*\n\nE.g. Einstein Hall, AB-1 or Online", parse_mode="Markdown")
    return P_VENUE


async def proposal_venue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['proposal']['venue'] = update.message.text
    await update.message.reply_text("5️⃣ *Date (Proposed)?*\n\nFormat: DD-MM-YYYY", parse_mode="Markdown")
    return P_DATE


async def proposal_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['proposal']['date'] = update.message.text
    await update.message.reply_text("6️⃣ *Duration?*\n\nE.g. 1.5 hours (9:00 PM – 10:30 PM)", parse_mode="Markdown")
    return P_DURATION


async def proposal_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['proposal']['duration'] = update.message.text
    await update.message.reply_text("7️⃣ *Total Budget (Rs.)?*\n\nE.g. 2000", parse_mode="Markdown")
    return P_BUDGET


async def proposal_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['proposal']['total_budget'] = update.message.text
    await update.message.reply_text(
        "8️⃣ *Extra Requirements?*\n\nE.g. Event Boards, Projector\nTap *None* if not applicable.",
        parse_mode="Markdown", reply_markup=NONE_KB
    )
    return P_EXTRA


async def proposal_extra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['proposal']['extra'] = await resolve_text(update)
    await update.effective_message.reply_text(
        "9️⃣ *Purpose of the Event*\n\nWrite the purpose paragraph.\n_(Explain why this event is being conducted)_",
        parse_mode="Markdown"
    )
    return P_PURPOSE


async def proposal_purpose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['proposal']['purpose'] = update.message.text
    await update.message.reply_text(
        "🔟 *Brief Description of the Event*\n\nWrite the description paragraph.\n_(What will happen during the event)_",
        parse_mode="Markdown"
    )
    return P_DESCRIPTION


async def proposal_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['proposal']['description'] = update.message.text
    context.user_data['proposal']['budget_items'] = []
    await update.message.reply_text(
        "1️⃣1️⃣ *Budget Breakdown*\n\nSend each budget item in this format:\n"
        "`Item Name, Qty x Rate`\n\nE.g. `Posters, 10 x 20`\n\n"
        "Send one item per message. Tap *Done* when finished.",
        parse_mode="Markdown", reply_markup=DONE_KB
    )
    return P_BUDGET_ITEMS


async def proposal_budget_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (await resolve_text(update) or '').strip()
    if text.lower() == 'done':
        if not context.user_data['proposal']['budget_items']:
            await update.effective_message.reply_text("⚠️ Add at least one budget item before proceeding.")
            return P_BUDGET_ITEMS
        resume_after_edit(context)      # clear the flag; this step ends at the summary anyway
        return await _proposal_summary(update, context)

    try:
        parts = text.split(',')
        item_name = parts[0].strip()
        qty_rate = parts[1].strip()
        qty, rate = [x.strip() for x in qty_rate.split('x')]
        total = int(qty) * int(rate)
        context.user_data['proposal']['budget_items'].append({
            'item': item_name,
            'qty': qty,
            'rate': rate,
            'total': total
        })
        await update.effective_message.reply_text(
            f"✅ Added: {md_wrap(item_name)} — {qty} × ₹{rate} = ₹{total}\n\nSend next item or tap *Done*.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
    except Exception:
        await update.effective_message.reply_text(
            "⚠️ Wrong format. Use:\n`Item Name, Qty x Rate`\nE.g. `Posters, 10 x 20`",
            parse_mode="Markdown"
        )
    return P_BUDGET_ITEMS


def _budget_mismatch_note(total_budget, grand_total: int) -> str:
    """The proposal prints the Total Budget the user typed *and* the sum of the
    budget items, in two different places. Nothing kept them in agreement, so a
    typo in either sailed through to a document that contradicts itself."""
    try:
        stated = int(str(total_budget).replace(',', '').replace('₹', '').strip())
    except (TypeError, ValueError):
        return ''                       # 'Nil'/free text — nothing to compare
    if stated == grand_total:
        return ''
    return (f"⚠️ *Total Budget says ₹{stated} but the items add up to "
            f"₹{grand_total}.* Both appear in the document — cancel and redo "
            f"if that's not deliberate.\n\n")


async def _proposal_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p = context.user_data['proposal']
    # Everything below is text the user typed; a bare '_' or '*' would otherwise
    # break the whole summary's Markdown.
    e = md_escape
    items_text = "\n".join([f"  • {e(i['item'])}: {i['qty']} × ₹{i['rate']} = ₹{i['total']}" for i in p['budget_items']])
    grand_total = sum(i['total'] for i in p['budget_items'])

    summary = (
        f"📋 *Proposal Summary*\n\n"
        f"*Club:* {e(p['club'])}\n"
        f"*Coordinator:* {e(p['coordinator'])}\n"
        f"*Event:* {e(p['event_name'])}\n"
        f"*Venue:* {e(p['venue'])}\n"
        f"*Date:* {e(p['date'])}\n"
        f"*Duration:* {e(p['duration'])}\n"
        f"*Total Budget:* ₹{e(str(p['total_budget']))}\n"
        f"*Extra Requirements:* {e(p['extra'])}\n\n"
        f"*Budget Items:*\n{items_text}\n"
        f"*Grand Total:* ₹{grand_total}\n\n"
        f"{_budget_mismatch_note(p.get('total_budget'), grand_total)}"
        f"Tap *Confirm* to generate documents or *Cancel* to restart."
    )
    await update.effective_message.reply_text(summary, parse_mode="Markdown",
                                              reply_markup=CONFIRM_EDIT_KB)
    return P_CONFIRM


proposal_edit_menu, proposal_edit_pick, proposal_edit_value = make_edit_handlers(
    'proposal', PROPOSAL_EDIT_FIELDS, _proposal_summary, P_EDIT_PICK, P_EDIT_VALUE)


async def proposal_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (await resolve_text(update) or '').strip().lower()
    if text == 'edit':
        return await proposal_edit_menu(update, context)
    if text != 'confirm':
        await update.effective_message.reply_text("Cancelled. Send /start to begin again.")
        return ConversationHandler.END
    return await _generate_proposal_docs(update, context)


def _proposal_overflow(pdf_path):
    """The proposal should be exactly 2 pages: info + purpose/description on
    page 1, Budget Breakdown on page 2. Returns (spilled_chars, budget_page,
    total_pages) — spilled_chars > 0 means the text overflowed page 1."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        total = len(doc)
        budget_page = next(
            (i + 1 for i, pg in enumerate(doc) if 'Budget Breakdown' in pg.get_text()),
            total)
        spill = sum(len(doc[i].get_text().strip()) for i in range(1, budget_page - 1))
        return (spill if budget_page > 2 else 0), budget_page, total
    except Exception:
        return 0, -1, -1


async def _generate_proposal_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("⏳ Generating proposal... please wait.")
    from generators.proposal_gen import generate_proposal
    from event_store import upsert_flow, event_folder, slugify
    p = context.user_data['proposal']
    event_name = p.get('event_name', 'Event')
    try:
        out_dir = event_folder(update.effective_user.id, slugify(event_name))
        docx_path, pdf_path = generate_proposal(p, output_dir=out_dir)
        upsert_flow(update.effective_user.id, event_name, 'proposal', p)
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error generating proposal: {str(e) or type(e).__name__}\n\n"
            "Your answers are saved — tap *Generate again* to retry.",
            parse_mode="Markdown", reply_markup=RETRY_KB)
        return P_CONFIRM

    if pdf_path and os.path.exists(pdf_path):
        spill, budget_page, total = _proposal_overflow(pdf_path)
        if spill:
            context.user_data['_proposal_out'] = (docx_path, pdf_path, event_name)
            await update.effective_message.reply_text(
                f"⚠️ Your Purpose / Brief Description doesn't fit on page 1 — "
                f"about {spill} characters spilled onto page 2, so the document "
                f"is now {total} pages (Budget Breakdown starts on page {budget_page}).\n\n"
                f"Send it as is, or shorten the text and I'll regenerate.",
                reply_markup=OVERFLOW_KB
            )
            return P_OVERFLOW

    return await _send_proposal_files(update, docx_path, pdf_path, event_name)


async def _send_proposal_files(update: Update, docx_path, pdf_path, event_name):
    try:
        # Send under the name the file actually has on disk — event names get
        # sanitised on the way to the filesystem, and a mismatch here is how a
        # broken path stayed invisible.
        with open(docx_path, 'rb') as f:
            await update.effective_message.reply_document(f, filename=os.path.basename(docx_path), write_timeout=180, read_timeout=180)
        pdf_ok = bool(pdf_path) and os.path.exists(pdf_path)
        if pdf_ok:
            with open(pdf_path, 'rb') as f:
                await update.effective_message.reply_document(f, filename=os.path.basename(pdf_path), write_timeout=180, read_timeout=180)
        await update.effective_message.reply_text(
            (f"✅ *Proposal generated successfully!*\n\n" if pdf_ok else
             f"⚠️ *Proposal generated, but PDF conversion failed* — DOCX only.\n\n") +
            f"Event saved under: *{event_name}*\n"
            f"Send /start to generate another document.",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error sending the proposal: {str(e) or type(e).__name__}\n\n"
            "Your answers are saved — tap *Generate again* to retry.",
            parse_mode="Markdown", reply_markup=RETRY_KB)
        return P_CONFIRM
    return ConversationHandler.END


async def proposal_overflow_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (await resolve_text(update) or '').strip().lower()
    if text in ('send', 'send anyway'):
        out = context.user_data.pop('_proposal_out', None)
        if not out:
            await update.effective_message.reply_text("Nothing pending. Send /start to begin again.")
            return ConversationHandler.END
        return await _send_proposal_files(update, *out)
    if text == 'edit_purpose':
        cur = context.user_data['proposal'].get('purpose', '')
        await update.effective_message.reply_text(
            f"Current Purpose ({len(cur)} characters):\n\n{cur}\n\n"
            f"✏️ Send the shorter Purpose text:"
        )
        return P_EDIT_PURPOSE
    if text == 'edit_desc':
        cur = context.user_data['proposal'].get('description', '')
        await update.effective_message.reply_text(
            f"Current Brief Description ({len(cur)} characters):\n\n{cur}\n\n"
            f"✏️ Send the shorter Brief Description text:"
        )
        return P_EDIT_DESC
    await update.effective_message.reply_text("Please pick one of the buttons above.")
    return P_OVERFLOW


async def proposal_edit_purpose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['proposal']['purpose'] = update.message.text
    return await _generate_proposal_docs(update, context)


async def proposal_edit_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['proposal']['description'] = update.message.text
    return await _generate_proposal_docs(update, context)


PROPOSAL_STATES = {
    P_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_pick)],
    P_CLUB: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_club)],
    P_COORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_coord)],
    P_EVENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_event)],
    P_VENUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_venue)],
    P_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_date)],
    P_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_duration)],
    P_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_budget)],
    P_EXTRA: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_extra),
        CallbackQueryHandler(proposal_extra),
    ],
    P_PURPOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_purpose)],
    P_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_description)],
    P_BUDGET_ITEMS: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_budget_item),
        CallbackQueryHandler(proposal_budget_item),
    ],
    P_CONFIRM: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_confirm),
        CallbackQueryHandler(proposal_confirm),
    ],
    P_OVERFLOW: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_overflow_choice),
        CallbackQueryHandler(proposal_overflow_choice),
    ],
    P_EDIT_PURPOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_edit_purpose)],
    P_EDIT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_edit_desc)],
    P_EDIT_PICK: [
        CallbackQueryHandler(proposal_edit_pick),
        MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_edit_pick),
    ],
    P_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, proposal_edit_value)],
}
