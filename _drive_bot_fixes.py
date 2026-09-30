"""Live check of: schedule comma parsing, long participant list layout, page-border margin.
Drives the Report flow (has_budget=No) with an 80-row participant sheet, downloads the docx."""
import asyncio, io, os, sys
from pathlib import Path
import _drive_bot as D
import openpyxl

OUT = D.BASE / 'temp' / 'fixes_out'
OUT.mkdir(parents=True, exist_ok=True)

SCHEDULE = """Total 192 teams, 3 rounds, played over 3 days.
Day 1 (Saturday, 19 Sep): Round 1
9:00 PM, Sat 19 Sep - Round 1 Slot 1: 3 matches per group (Random, 6 maps each). Top 3 per group qualify
10:00 PM, Sat 19 Sep - Round 1 Slot 2: 3 matches per group
3:00 PM, Sun 20 Sep - Buffer Round: only Round 1 teams placed 4th-6th"""


def big_xlsx(n=80):
    p = D.TEST_DIR / 'participants_big.xlsx'
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(['Name', 'Registration Number', 'Email'])
    for i in range(1, n + 1):
        ws.append([f'Participant Number {i}', f'23BCE{i:04d}', f'p{i}@vit.edu'])
    wb.save(p); return p


async def main():
    session = os.getenv('DAG_SESSION') or str(D.BASE / 'dag_user')
    client = D.Client(name=session, api_id=D.TG_API_ID, api_hash=D.TG_API_HASH, no_updates=True)
    async with client:
        chat = D.BotChat(client); await chat.init()
        await chat.send('/cancel', timeout=8); await asyncio.sleep(0.5)
        await chat.send('/start', timeout=8)
        r = await chat.send('📋 Final Event Report', timeout=8)
        await D._skip_event_picker_if_shown(chat, r)
        await chat.send('LiveTest Fixes 2026', timeout=8)
        await chat.send('Offline', timeout=8)
        await chat.send('Einstein Hall, AB-1', timeout=8)
        await chat.send('25-05-2026', timeout=8)
        await chat.send('No', timeout=8)
        await chat.send_photo(D.make_image('approval', (50, 200, 50)))
        await chat.send('done', timeout=8)
        await chat.send_photo(D.make_image('brochure', (200, 50, 50)), timeout=8)
        sched_reply = await chat.send(SCHEDULE, label='Schedule (mixed block)', timeout=10)
        # bot may send several replies (added / rejected)
        extra = await chat.collect_bot_replies(expected=2, timeout=4)
        for m in extra: chat._print_reply(m)
        await chat.send('done', timeout=8)
        await chat.send_document(big_xlsx(), label='80 participants', timeout=20)
        await chat.send('No', label='no attendance', timeout=8)
        long = 'This is a long purpose paragraph to fill the page. ' * 12
        await chat.send(long, label='Purpose', timeout=8)
        await chat.send(long * 2, label='Brief', timeout=8)
        await chat.send('No', label='no certs', timeout=8)
        await chat.send_photo(D.make_image('photo', (200, 50, 200)))
        await chat.send('done', timeout=8)
        await chat.send('Alice TestUser, 23BCE0001', label='Winner', timeout=8)
        await chat.send('done', label='done winners', timeout=8)
        await chat.send('No', label='no speaker', timeout=8)
        await chat.send('No', label='no feedback', timeout=8)
        print('ME  > confirm')
        await client.send_message(D.BOT_USERNAME, 'confirm')
        docs = await chat.collect_bot_replies(expected=4, timeout=150)
        for m in docs:
            chat._print_reply(m)
            if m.document:
                path = await client.download_media(m, file_name=str(OUT) + os.sep + m.document.file_name)
                print('saved', path)

asyncio.run(main())
