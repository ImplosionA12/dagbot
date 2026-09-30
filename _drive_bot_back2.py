"""Back as a tap on every kind of prompt (no /back command)."""
import asyncio, os
import _drive_bot as D

def desc(m):
    mk = m.reply_markup
    ib = getattr(mk, 'inline_keyboard', None)
    kb = getattr(mk, 'keyboard', None)
    return {'inline': [b.callback_data for r in ib for b in r] if ib else [],
            'reply': [[b if isinstance(b, str) else b.text for b in r] for r in kb] if kb else []}

async def last_bot(client):
    async for m in client.get_chat_history(D.BOT_USERNAME, limit=5):
        if m.from_user and m.from_user.username == D.BOT_USERNAME: return m

async def main():
    session = os.getenv('DAG_SESSION') or str(D.BASE / 'dag_user')
    client = D.Client(name=session, api_id=D.TG_API_ID, api_hash=D.TG_API_HASH, no_updates=True)
    async with client:
        chat = D.BotChat(client); await chat.init()
        await chat.send('/cancel', timeout=10); await asyncio.sleep(1)
        await chat.send('/start', timeout=10)
        r = await chat.send('📋 Final Event Report', timeout=10)
        await D._skip_event_picker_if_shown(chat, r)
        await chat.send('BackBtn Event', timeout=10)
        await asyncio.sleep(2); print('   MODE  :', desc(await last_bot(client)))
        await chat.send('Offline', timeout=10)
        await asyncio.sleep(2); print('   VENUE :', desc(await last_bot(client)))
        await chat.send('⬅️ Back', label='tap Back on VENUE step (expect Mode re-asked)', timeout=10)
        await chat.send('Offline', timeout=10)
        await chat.send('Hall X', timeout=10)
        await asyncio.sleep(2); m = await last_bot(client); print('   DATE  :', desc(m))
        await chat.send('⬅️ Back', label='tap Back on DATE step (expect Venue re-asked)', timeout=10)
        await chat.send('Hall Y', timeout=10)
        await chat.send('25-05-2026', timeout=10)                          # -> has budget (reply kb?)
        await asyncio.sleep(2); m = await last_bot(client); print('   BUDGET:', desc(m))
        await chat.send('⬅️ Back', label='tap reply-keyboard Back (expect Date re-asked)', timeout=10)
        await chat.send('/cancel', timeout=10)
asyncio.run(main())
