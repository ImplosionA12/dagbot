"""Drive the report wizard down the has_budget=Yes branch.

_drive_bot.py only ever answers 'No', so the signed Budget / signed Proposal
uploads and the conditional Expenditure Statement section (11) have never been
exercised end to end.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import _drive_bot as d
from pyrogram import Client

SESSION = os.getenv('DAG_SESSION') or str(Path(__file__).parent / 'dag_user')


async def run(chat):
    print("=" * 60)
    print("TEST: Final Event Report — has_budget = YES")
    print("=" * 60)
    await chat.send('/cancel', timeout=8)
    await asyncio.sleep(0.5)
    await chat.send('/start', timeout=8)
    r = await chat.send('📋 Final Event Report', timeout=8)
    await d._skip_event_picker_if_shown(chat, r)

    await chat.send('LiveTest BudgetYes 2026', label='Event name', timeout=8)
    await chat.send('Online', label='Mode = Online (Event Screenshots branch)', timeout=8)
    # Venue is still asked for Online events — the generator maps it to "Online"
    # at render time, the wizard does not skip the question.
    await chat.send('Online', label='Venue', timeout=8)
    await chat.send('26-05-2026', label='Date', timeout=8)
    await chat.send('Yes', label='Has budget = YES', timeout=8)

    signed_budget = d.make_image('signed_budget', (30, 90, 160))
    signed_proposal = d.make_image('signed_proposal', (160, 90, 30))
    approval = d.make_image('approval2', (50, 200, 50))
    brochure = d.make_image('brochure2', (200, 50, 50))
    cert = d.make_image('cert2', (50, 200, 200))
    photo = d.make_image('photo2', (200, 50, 200))
    expenditure = d.make_image('expenditure', (90, 90, 90))
    xlsx = d.make_participants_xlsx()

    # Signed budget + signed proposal — only reachable on this branch
    await chat.send_photo(signed_budget, label='Signed budget scan')
    await chat.send('done', label='Done signed budget', timeout=8)
    await chat.send_photo(signed_proposal, label='Signed proposal scan')
    await chat.send('done', label='Done signed proposal', timeout=8)

    await chat.send_photo(approval, label='Approval mail')
    await chat.send('done', label='Done approval', timeout=8)
    await chat.send_photo(brochure, label='Brochure', timeout=8)

    await chat.send('9:00 AM - 10:00 AM, Keynote', label='Schedule 1', timeout=8)
    await chat.send('done', label='Done schedule', timeout=8)

    await chat.send_document(xlsx, label='Participants xlsx', timeout=15)
    await chat.send('No', label='Has attendance = No', timeout=8)

    await chat.send('Verify the budget-yes branch of the report wizard.', label='Purpose', timeout=8)
    await chat.send('Signed budget, signed proposal and expenditure sections were exercised.',
                    label='Brief', timeout=8)

    await chat.send('Yes', label='Has certificates = Yes', timeout=8)
    await chat.send_photo(cert, label='Cert 1')
    await chat.send('done', label='Done certs', timeout=8)

    await chat.send_photo(photo, label='Screenshot 1')
    await chat.send('done', label='Done photos', timeout=8)

    await chat.send('Bob TestUser, 23BCE0002', label='Winner 1', timeout=8)
    await chat.send('done', label='Done winners', timeout=8)

    await chat.send('No', label='Has speaker = No', timeout=8)
    await chat.send('No', label='Has feedback = No', timeout=8)

    # Section 11 — only offered because has_budget was Yes
    await chat.send('Yes', label='Has expenditure = Yes', timeout=8)
    await chat.send_photo(expenditure, label='Expenditure statement')
    await chat.send('done', label='Done expenditure', timeout=10)

    print("ME  > confirm")
    await chat.client.send_message(d.BOT_USERNAME, 'confirm')
    docs = await chat.collect_bot_replies(expected=3, timeout=180)
    files = [m.document for m in docs if m.document]
    for f in files:
        print(f"   • {f.file_name} ({f.file_size:,} bytes)")
    return len(files) >= 2


async def main():
    client = Client(name=SESSION, api_id=d.TG_API_ID, api_hash=d.TG_API_HASH, no_updates=True)
    async with client:
        chat = d.BotChat(client)
        await chat.init()
        ok = await run(chat)
        print("\nRESULT:", "PASS" if ok else "FAIL")


asyncio.run(main())
