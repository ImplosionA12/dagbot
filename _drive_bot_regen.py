"""Edit & regenerate a saved document from My Events (via buttons only)."""
import asyncio, os
import _drive_bot as D

def btn_texts(m):
    mk=m.reply_markup
    ib=getattr(mk,'inline_keyboard',None); kb=getattr(mk,'keyboard',None)
    return ([b.text for r in ib for b in r] if ib else []) + ([b if isinstance(b,str) else b.text for r in kb for b in r] if kb else [])

async def click(client, m, text):
    for r in m.reply_markup.inline_keyboard:
        for b in r:
            if b.text == text:
                await client.request_callback_answer(D.BOT_USERNAME, m.id, b.callback_data); return
    raise SystemExit(f'button {text!r} not found in {btn_texts(m)}')

async def main():
    client = D.Client(name=str(D.BASE/'dag_user'), api_id=D.TG_API_ID, api_hash=D.TG_API_HASH, no_updates=True)
    async with client:
        chat = D.BotChat(client); await chat.init()
        ok = await D.test_proposal(chat)                       # creates the 'LiveTest' proposal event
        print('proposal created:', ok)
        await chat.send('/cancel', timeout=10); await asyncio.sleep(1)
        r = await chat.send('/events', timeout=10)
        print('   events menu buttons:', btn_texts(r)[:5])
        ev = next(t for t in btn_texts(r) if 'LiveTest' in t and 'Proposal' in t)
        r = await chat.send(ev, label=f'open {ev}', timeout=10)
        print('   detail buttons:', btn_texts(r))
        r = await chat.send('✏️ Edit & regenerate', timeout=10)
        print('   regen menu:', btn_texts(r))
        await click(client, r, '📄 Event Proposal')
        r = await chat.wait_bot_reply(timeout=10); chat._print_reply(r)
        print('   summary buttons:', btn_texts(r))
        await click(client, r, '✏️ Edit a field')
        r = await chat.wait_bot_reply(timeout=10); chat._print_reply(r)
        print('   fields:', btn_texts(r))
        venue = next(t for t in btn_texts(r) if 'Venue' in t)
        await click(client, r, venue)
        r = await chat.wait_bot_reply(timeout=10); chat._print_reply(r)
        await chat.send('Corrected Venue Hall', label='new venue', timeout=10)
        r = await chat.wait_bot_reply(timeout=6)
        await asyncio.sleep(1)
        async for m in client.get_chat_history(D.BOT_USERNAME, limit=3):
            if m.from_user and m.from_user.username == D.BOT_USERNAME and m.reply_markup and 'Confirm' in ' '.join(btn_texts(m)):
                summ = m; break
        print('   summary now shows corrected venue:', 'Corrected Venue Hall' in (summ.text or ''))
        await click(client, summ, '✅ Confirm')
        docs = await chat.collect_bot_replies(expected=4, timeout=90)
        for m in docs: chat._print_reply(m)
asyncio.run(main())
