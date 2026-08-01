import os
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes, MessageHandler, CallbackQueryHandler, filters, ConversationHandler
)

from config import fixed_profile
from event_store import list_events
from handlers.ui import (
    DONE_KB, DONE_NONE_KB, CONFIRM_KB, CONFIRM_EDIT_KB, resolve_text, md_escape
)
from handlers.edit_fields import Field, make_edit_handlers, resume_after_edit

B_CLUB, B_COORD, B_EMPID, B_EVENT, B_DATE, \
B_REV_ITEMS, B_EXP_ITEMS, B_CONFIRM, B_PICK, \
B_EDIT_PICK, B_EDIT_VALUE = range(200, 211)

_REV_PROMPT = (
    "*Revenue Items* — send them again, one per message:\n"
    "`Particulars, Qty, Rate`\n\nE.g. `Registration Fee, 50, 100`\n\n"
    "Tap *None* if there's no revenue, *Done* when finished."
)
_EXP_PROMPT = (
    "*Expenditure Items* — send them again, one per message:\n"
    "`Particulars, Budget Qty, Budget Rate, Actual Qty, Actual Rate`\n\n"
    "E.g. `Winner Cash Prize, 1, 2000, 1, 2000`\n\nTap *Done* when finished."
)

BUDGET_EDIT_FIELDS = [
    Field('event_name', '📛 Event name', 'Send the new event name:'),
    Field('date', '📅 Date', 'Send the new date (DD-MM-YYYY):'),
    Field('club', '🏛 Club', 'Send the new club name:'),
    Field('coordinator', '👤 Coordinator', 'Send the new coordinator name:'),
    Field('emp_id', '🆔 Emp ID', 'Send the new employee ID:'),
    Field('revenue', '💵 Revenue items', '', restart_state=B_REV_ITEMS,
          restart_prompt=_REV_PROMPT),
    Field('expenditure', '🧾 Expenditure items', '', restart_state=B_EXP_ITEMS,
          restart_prompt=_EXP_PROMPT),
]


async def budget_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['type'] = 'budget'
    profile = fixed_profile()

    context.user_data['budget'] = {
        'club': profile['club'],
        'coordinator': profile['coordinator'],
        # C5 is labelled "Name and phone number of the Coordinator"; the
        # generator joins these two, and without this key it only ever got half.
        'coordinator_phone': profile.get('coordinator_phone', ''),
        'emp_id': profile['emp_id'],
        'revenue': [],
        'expenditure': [],
    }

    user_id = update.effective_user.id
    events = list_events(user_id)
    if events:
        keyboard = [[f"📂 {ev.get('name', ev.get('slug', '?'))}"] for ev in events[:8]]
        keyboard.append(["➕ New event"])
        await update.message.reply_text(
            "💰 *Budget Form*\n\n"
            "Pick an existing event or start a new one:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )
        return B_PICK

    await update.message.reply_text(
        f"💰 *Budget Form*\n\n*Event name?*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return B_EVENT


async def budget_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip()
    user_id = update.effective_user.id

    if text.startswith('➕') or 'new event' in text.lower():
        await update.message.reply_text(
            "*Event name?*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return B_EVENT

    name = text.lstrip('📂 ').strip()
    events = list_events(user_id)
    match = next((e for e in events if e.get('name') == name), None)
    if not match:
        await update.message.reply_text("Please pick one from the menu.")
        return B_PICK

    b = context.user_data['budget']
    b['event_name'] = match.get('name', '')
    for flow_key in ('budget', 'proposal', 'report'):
        flow = match.get(flow_key) or {}
        if not b.get('date'):
            b['date'] = flow.get('date', '')

    if b.get('date'):
        msg = (f"✅ Using event: *{md_escape(b['event_name'])}*\n_(Date: {md_escape(b['date'])})_\n\n"
               f"*Revenue Items (Budget)*\n\n"
               f"Send each revenue item in this format:\n`Particulars, Qty, Rate`\n\n"
               f"E.g. `Registration Fee, 50, 100`\n\nTap *None* if no revenue. Tap *Done* when finished.")
        markup = DONE_NONE_KB
        next_state = B_REV_ITEMS
    else:
        msg = (f"✅ Using event: *{md_escape(b['event_name'])}*\n\n*Event date?*\n\nFormat: DD-MM-YYYY")
        markup = ReplyKeyboardRemove()
        next_state = B_DATE
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=markup)
    return next_state


async def budget_club(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['budget']['club'] = update.message.text
    await update.message.reply_text("2️⃣ *Coordinator name and phone number?*\n\nFormat: Dr. Name, 9XXXXXXXXX", parse_mode="Markdown")
    return B_COORD


async def budget_coord(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['budget']['coordinator'] = update.message.text
    await update.message.reply_text("3️⃣ *Employee ID?*", parse_mode="Markdown")
    return B_EMPID


async def budget_empid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['budget']['emp_id'] = update.message.text
    await update.message.reply_text("4️⃣ *Event name?*", parse_mode="Markdown")
    return B_EVENT


async def budget_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['budget']['event_name'] = update.message.text
    await update.message.reply_text("5️⃣ *Event date?*\n\nFormat: DD-MM-YYYY", parse_mode="Markdown")
    return B_DATE


async def budget_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['budget']['date'] = update.message.text
    await update.message.reply_text(
        "6️⃣ *Revenue Items (Budget)*\n\n"
        "Send each revenue item in this format:\n"
        "`Particulars, Qty, Rate`\n\n"
        "E.g. `Registration Fee, 50, 100`\n\n"
        "Tap *None* if no revenue. Tap *Done* when finished.",
        parse_mode="Markdown", reply_markup=DONE_NONE_KB
    )
    return B_REV_ITEMS


async def _exp_items_prompt(update: Update):
    await update.effective_message.reply_text(
        "7️⃣ *Expenditure Items*\n\n"
        "Send each expenditure item in this format:\n"
        "`Particulars, Budget Qty, Budget Rate, Actual Qty, Actual Rate`\n\n"
        "E.g. `Winner Cash Prize, 1, 2000, 1, 2000`\n\n"
        "Tap *Done* when finished.",
        parse_mode="Markdown", reply_markup=DONE_KB
    )
    return B_EXP_ITEMS


async def budget_rev_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (await resolve_text(update) or '').strip()

    if text.lower() in ('none', 'done'):
        # Re-entered from the summary to redo just this list — go straight back
        # rather than marching through expenditure again.
        if resume_after_edit(context):
            return await _budget_summary(update, context)
        return await _exp_items_prompt(update)

    try:
        parts = [p.strip() for p in text.split(',')]
        particulars = parts[0]
        qty = int(parts[1])
        rate = int(parts[2])
        amount = qty * rate
        context.user_data['budget']['revenue'].append({
            'particulars': particulars,
            'b_qty': qty, 'b_rate': rate, 'b_amount': amount,
            'a_qty': qty, 'a_rate': rate, 'a_amount': amount
        })
        await update.effective_message.reply_text(
            f"✅ Added: *{md_escape(particulars)}* — {qty} × ₹{rate} = ₹{amount}\n\nSend next item or tap *Done*.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
    except Exception:
        await update.effective_message.reply_text(
            "⚠️ Wrong format. Use:\n`Particulars, Qty, Rate`\nE.g. `Registration Fee, 50, 100`",
            parse_mode="Markdown"
        )
    return B_REV_ITEMS


async def budget_exp_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (await resolve_text(update) or '').strip()
    if text.lower() == 'done':
        if not context.user_data['budget']['expenditure']:
            await update.effective_message.reply_text("⚠️ Add at least one expenditure item.")
            return B_EXP_ITEMS
        resume_after_edit(context)      # clear the flag; this step ends at the summary anyway
        return await _budget_summary(update, context)

    try:
        parts = [p.strip() for p in text.split(',')]
        particulars = parts[0]
        b_qty = int(parts[1])
        b_rate = int(parts[2])
        a_qty = int(parts[3])
        a_rate = int(parts[4])
        context.user_data['budget']['expenditure'].append({
            'particulars': particulars,
            'b_qty': b_qty, 'b_rate': b_rate, 'b_amount': b_qty * b_rate,
            'a_qty': a_qty, 'a_rate': a_rate, 'a_amount': a_qty * a_rate
        })
        await update.effective_message.reply_text(
            f"✅ Added: *{md_escape(particulars)}*\n"
            f"  Budget: {b_qty} × ₹{b_rate} = ₹{b_qty * b_rate}\n"
            f"  Actual: {a_qty} × ₹{a_rate} = ₹{a_qty * a_rate}\n\n"
            f"Send next item or tap *Done*.",
            parse_mode="Markdown", reply_markup=DONE_KB
        )
    except Exception:
        await update.effective_message.reply_text(
            "⚠️ Wrong format. Use:\n`Particulars, Budget Qty, Budget Rate, Actual Qty, Actual Rate`\n"
            "E.g. `Winner Cash Prize, 1, 2000, 1, 2000`",
            parse_mode="Markdown"
        )
    return B_EXP_ITEMS


async def _budget_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    b = context.user_data['budget']
    rev_total = sum(i['b_amount'] for i in b['revenue'])
    exp_b_total = sum(i['b_amount'] for i in b['expenditure'])
    exp_a_total = sum(i['a_amount'] for i in b['expenditure'])
    surplus_b = rev_total - exp_b_total
    surplus_a = rev_total - exp_a_total

    # Particulars and the header fields are all typed by the user — escape them
    # or one '_' takes the whole summary down.
    e = md_escape
    exp_lines = "\n".join([
        f"  • {e(i['particulars'])}: Budget ₹{i['b_amount']} | Actual ₹{i['a_amount']}"
        for i in b['expenditure']
    ])
    rev_lines = "\n".join([f"  • {e(i['particulars'])}: ₹{i['b_amount']}" for i in b['revenue']]) if b['revenue'] else "  None"

    summary = (
        f"💰 *Budget Summary*\n\n"
        f"*Club:* {e(b['club'])}\n"
        f"*Coordinator:* {e(b['coordinator'])}\n"
        f"*Emp ID:* {e(str(b['emp_id']))}\n"
        f"*Event:* {e(b['event_name'])}\n"
        f"*Date:* {e(b['date'])}\n\n"
        f"*Revenue:*\n{rev_lines}\n"
        f"*Revenue Total:* ₹{rev_total}\n\n"
        f"*Expenditure:*\n{exp_lines}\n"
        f"*Exp Budget Total:* ₹{exp_b_total} | *Actual Total:* ₹{exp_a_total}\n\n"
        f"*Surplus/Deficit (Budget):* ₹{surplus_b}\n"
        f"*Surplus/Deficit (Actual):* ₹{surplus_a}\n\n"
        f"Tap *Confirm* to generate or *Cancel* to restart."
    )
    await update.effective_message.reply_text(summary, parse_mode="Markdown",
                                              reply_markup=CONFIRM_EDIT_KB)
    return B_CONFIRM


budget_edit_menu, budget_edit_pick, budget_edit_value = make_edit_handlers(
    'budget', BUDGET_EDIT_FIELDS, _budget_summary, B_EDIT_PICK, B_EDIT_VALUE)


async def budget_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (await resolve_text(update) or '').strip().lower()
    if text == 'edit':
        return await budget_edit_menu(update, context)
    if text != 'confirm':
        await update.effective_message.reply_text("Cancelled. Send /start to begin again.")
        return ConversationHandler.END

    await update.effective_message.reply_text("⏳ Generating budget form... please wait.")
    from generators.budget_gen import generate_budget
    from event_store import upsert_flow, event_folder, slugify
    b = context.user_data['budget']
    event_name = b.get('event_name', 'Event')
    try:
        out_dir = event_folder(update.effective_user.id, slugify(event_name))
        xlsx_path, docx_path, pdf_path = generate_budget(b, output_dir=out_dir)
        upsert_flow(update.effective_user.id, event_name, 'budget', b)

        # Filenames come from the paths themselves: the event name is sanitised
        # before it reaches disk, so re-deriving it here would drift.
        with open(xlsx_path, 'rb') as f:
            await update.effective_message.reply_document(f, filename=os.path.basename(xlsx_path))
        if docx_path and os.path.exists(docx_path):
            with open(docx_path, 'rb') as f:
                await update.effective_message.reply_document(f, filename=os.path.basename(docx_path))
        pdf_ok = bool(pdf_path) and os.path.exists(pdf_path)
        if pdf_ok:
            with open(pdf_path, 'rb') as f:
                await update.effective_message.reply_document(f, filename=os.path.basename(pdf_path))
        await update.effective_message.reply_text(
            (f"✅ *Budget form generated!*\n\n" if pdf_ok else
             f"⚠️ *Budget form generated, but PDF conversion failed* — XLSX/DOCX only.\n\n") +
            f"Event saved under: *{event_name}*\n"
            f"Send /start to generate another document.",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error: {str(e)}\n\nSend /start to try again.")
    return ConversationHandler.END


BUDGET_STATES = {
    B_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, budget_pick)],
    B_CLUB: [MessageHandler(filters.TEXT & ~filters.COMMAND, budget_club)],
    B_COORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, budget_coord)],
    B_EMPID: [MessageHandler(filters.TEXT & ~filters.COMMAND, budget_empid)],
    B_EVENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, budget_event)],
    B_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, budget_date)],
    B_REV_ITEMS: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, budget_rev_item),
        CallbackQueryHandler(budget_rev_item),
    ],
    B_EXP_ITEMS: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, budget_exp_item),
        CallbackQueryHandler(budget_exp_item),
    ],
    B_CONFIRM: [
        MessageHandler(filters.TEXT & ~filters.COMMAND, budget_confirm),
        CallbackQueryHandler(budget_confirm),
    ],
    B_EDIT_PICK: [
        CallbackQueryHandler(budget_edit_pick),
        MessageHandler(filters.TEXT & ~filters.COMMAND, budget_edit_pick),
    ],
    B_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, budget_edit_value)],
}
