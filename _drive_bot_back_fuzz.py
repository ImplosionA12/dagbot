"""Regression fuzz: run the whole suite, but after every 3rd typed answer tap Back and
give the same answer again. Output documents must be unchanged and every test must pass."""
import asyncio
import _drive_bot as D

_orig = D.BotChat.send
_n = {'i': 0}
SKIP = {'/cancel', '/start', '/events', '📄 Event Proposal', '💰 Budget Form', '📧 Mail Prompt',
        '📋 Final Event Report', '➕ New event', 'confirm', 'done', 'Done'}

async def send(self, text, label=None, expect_reply=True, timeout=10.0):
    r = await _orig(self, text, label=label, expect_reply=expect_reply, timeout=timeout)
    if text in SKIP or not expect_reply:
        return r
    rt = ((r.text or '') if r else '')
    if any(k in rt for k in ('Send next', 'Send more', 'tap Done', 'Tap Done', 'Send one more', 'What next', 'Here', 'Generating', 'Summary')):
        return r                     # inside a list loop: Back leaves the loop by design
    _n['i'] += 1
    if _n['i'] % 3 == 0:
        print('   ~~ fuzz: Back + repeat answer')
        await _orig(self, '⬅️ Back', label='⬅️ Back (fuzz)', timeout=12)
        r = await _orig(self, text, label=f'{label or text} (again)', timeout=timeout)
    return r

D.BotChat.send = send
asyncio.run(D.main())
