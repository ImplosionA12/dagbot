import asyncio, os
import _drive_bot as D
async def main():
    client = D.Client(name=str(D.BASE/'dag_user'), api_id=D.TG_API_ID, api_hash=D.TG_API_HASH, no_updates=True)
    async with client:
        chat = D.BotChat(client); await chat.init()
        mode = os.environ.get('PHASE')
        if mode == '1':
            await chat.send('/cancel', timeout=10); await asyncio.sleep(1)
            await chat.send('/start', timeout=10)
            r = await chat.send('📋 Final Event Report', timeout=10)
            await D._skip_event_picker_if_shown(chat, r)
            await chat.send('Persist Event', timeout=10)
            await chat.send('Online', timeout=10)          # now at Venue
        else:
            await chat.send('⬅️ Back', label='Back after service restart (expect Mode)', timeout=12)
            await chat.send('Offline', timeout=10)
            await chat.send('/cancel', timeout=10)
asyncio.run(main())
