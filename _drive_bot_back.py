"""Live check of /back and the Back button in the Report wizard."""
import asyncio, os
import _drive_bot as D

def buttons(m):
    return [b.callback_data for row in (m.reply_markup.inline_keyboard if m and m.reply_markup and hasattr(m.reply_markup,'inline_keyboard') else []) for b in row]

async def main():
    session = os.getenv('DAG_SESSION') or str(D.BASE / 'dag_user')
    client = D.Client(name=session, api_id=D.TG_API_ID, api_hash=D.TG_API_HASH, no_updates=True)
    async with client:
        chat = D.BotChat(client); await chat.init()
        await chat.send('/cancel', timeout=10); await asyncio.sleep(1)
        await chat.send('/back', label='/back while idle', timeout=10)
        await chat.send('/start', timeout=10)
        r = await chat.send('📋 Final Event Report', timeout=10)
        await D._skip_event_picker_if_shown(chat, r)
        r = await chat.send('Back Test Event', label='event name', timeout=10)
        r = await chat.send('Offline', label='mode', timeout=10)      # -> venue prompt
        await asyncio.sleep(2)
        # re-read the venue prompt message to see the Back button
        async for m in client.get_chat_history(D.BOT_USERNAME, limit=3):
            if m.from_user and m.from_user.username == D.BOT_USERNAME:
                print('   venue prompt buttons:', buttons(m)); venue_msg = m; break
        r = await chat.send('/back', label='/back (expect Mode re-asked)', timeout=10)
        r = await chat.send('Online', label='mode again = Online', timeout=10)
        print('   expect Venue prompt above')
        r = await chat.send('Virtual Room', label='venue', timeout=10)   # -> date
        await asyncio.sleep(2)
        async for m in client.get_chat_history(D.BOT_USERNAME, limit=3):
            if m.from_user and m.from_user.username == D.BOT_USERNAME:
                print('   date prompt buttons:', buttons(m)); date_msg = m; break
        # press the Back button
        await client.request_callback_answer(D.BOT_USERNAME, date_msg.id, 'nav:back')
        r = await chat.wait_bot_reply(timeout=10); chat._print_reply(r)
        await chat.send('Hall X', label='venue again', timeout=10)
        await chat.send('25-05-2026', label='date', timeout=10)
        await chat.send('No', label='has budget No', timeout=10)
        await chat.send('/back', label='/back from approval step', timeout=10)
        await chat.send('/back', label='/back again', timeout=10)
        await chat.send('/cancel', timeout=10)
asyncio.run(main())
