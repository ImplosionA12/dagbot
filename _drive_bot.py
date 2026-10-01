"""Comprehensive end-to-end test of all bot flows.

Tests:
  1. Event Proposal (text-only, generates docx+pdf)
  2. Budget Form (text-only, generates docx+pdf)
  3. Mail Prompt (new event branch, returns prompt text)
  4. Final Event Report (uploads images + Excel, has_budget=No to keep short)
  5. /events listing
"""
import asyncio
import io
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pyrogram import Client
from PIL import Image
from dotenv import load_dotenv
import openpyxl

BASE = Path('D:/dagbot')
TEST_DIR = BASE / 'temp' / 'live_test_assets'
TEST_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE / '.env')

TG_API_ID = int(os.environ['TG_API_ID'])
TG_API_HASH = os.environ['TG_API_HASH']
BOT_USERNAME = os.getenv('BOT_USERNAME', 'DAGClubDocBot')


# ── Test asset generators ──────────────────────────────────────────────────
def make_image(name: str, color):
    p = TEST_DIR / f"{name}.jpg"
    Image.new('RGB', (800, 600), color).save(p)
    return p


def make_participants_xlsx():
    p = TEST_DIR / 'participants.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Name', 'Registration Number', 'Email'])
    ws.append(['Alice TestUser', '23BCE0001', 'alice@vit.edu'])
    ws.append(['Bob TestUser', '23BCE0002', 'bob@vit.edu'])
    ws.append(['Carol TestUser', '23BCE0003', 'carol@vit.edu'])
    wb.save(p)
    return p


# ── Bot conversation helpers ───────────────────────────────────────────────
class BotChat:
    def __init__(self, client):
        self.client = client
        self.last_id = 0

    async def init(self):
        async for m in self.client.get_chat_history(BOT_USERNAME, limit=1):
            self.last_id = m.id
            break

    async def wait_bot_reply(self, timeout=10.0):
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.3)
            async for m in self.client.get_chat_history(BOT_USERNAME, limit=10):
                if (m.id > self.last_id
                        and m.from_user
                        and m.from_user.username == BOT_USERNAME):
                    self.last_id = m.id
                    return m
        return None

    async def collect_bot_replies(self, expected=2, timeout=60.0):
        """Collect N bot replies (or wait until timeout)."""
        got = []
        deadline = asyncio.get_event_loop().time() + timeout
        seen = set()
        while asyncio.get_event_loop().time() < deadline and len(got) < expected:
            await asyncio.sleep(0.7)
            async for m in self.client.get_chat_history(BOT_USERNAME, limit=20):
                if (m.id > self.last_id
                        and m.id not in seen
                        and m.from_user
                        and m.from_user.username == BOT_USERNAME):
                    got.append(m)
                    seen.add(m.id)
        if got:
            self.last_id = max(m.id for m in got)
        return got

    async def send(self, text, label=None, expect_reply=True, timeout=10.0):
        print(f"ME  > {label or text}")
        await self.client.send_message(BOT_USERNAME, text)
        if expect_reply:
            r = await self.wait_bot_reply(timeout=timeout)
            self._print_reply(r)
            return r

    async def send_photo(self, path, label=None, timeout=15.0):
        print(f"ME  > [PHOTO: {Path(path).name}] {label or ''}")
        await self.client.send_photo(BOT_USERNAME, str(path))
        r = await self.wait_bot_reply(timeout=timeout)
        self._print_reply(r)
        return r

    async def send_document(self, path, label=None, timeout=15.0):
        print(f"ME  > [DOC: {Path(path).name}] {label or ''}")
        await self.client.send_document(BOT_USERNAME, str(path))
        r = await self.wait_bot_reply(timeout=timeout)
        self._print_reply(r)
        return r

    @staticmethod
    def _print_reply(r):
        if r is None:
            print("BOT > (no reply)")
            return
        if r.document:
            print(f"BOT > 📎 [{r.document.file_name}, {r.document.file_size:,} bytes]")
        elif r.photo:
            print("BOT > 🖼  [photo]")
        else:
            t = (r.text or r.caption or '').replace('\n', ' | ')[:200]
            print(f"BOT > {t}")


# ── Test cases ─────────────────────────────────────────────────────────────
async def _skip_event_picker_if_shown(chat, reply, timeout=8):
    """If the bot shows the 'Pick an existing event' picker, send '➕ New event' so
    the wizard falls through to the normal first prompt. Returns the next bot reply."""
    if reply and reply.text and 'Pick an existing event' in reply.text:
        return await chat.send('➕ New event', label='Force new event', timeout=timeout)
    return reply


async def test_proposal(chat):
    print("\n" + "=" * 60)
    print("TEST 1: Event Proposal")
    print("=" * 60)
    # /cancel always answers now (a conversation fallback when one is running,
    # an "idle" reply otherwise) — consume it so replies don't shift by one.
    await chat.send('/cancel', timeout=8)
    await asyncio.sleep(0.5)
    await chat.send('/start', timeout=8)
    r = await chat.send('📄 Event Proposal', timeout=8)
    await _skip_event_picker_if_shown(chat, r)

    steps = [
        # Deliberately nasty: the ':' used to become an NTFS alternate data
        # stream (DOCX only, no PDF) and the '_' used to break the summary's
        # Markdown. Both must survive a run.
        ('LiveTest_Proposal: 2026', 'Event name'),
        ('Einstein Hall', 'Venue'),
        ('24-05-2026', 'Date'),
        ('2 hours', 'Duration'),
        ('5000', 'Total Budget'),
        ('Projector, Mic', 'Extra'),
        ('Showcase animation & gaming projects from members.', 'Purpose'),
        ('A 2-hour event featuring animation showcase and Q&A.', 'Description'),
        ('Posters, 10 x 20', 'Budget item 1'),
        ('Snacks, 50 x 30', 'Budget item 2'),
        ('done', 'Done with budget items'),
    ]
    for value, label in steps:
        await chat.send(value, label=label, timeout=8)

    print("ME  > confirm")
    await chat.client.send_message(BOT_USERNAME, 'confirm')
    docs = await chat.collect_bot_replies(expected=3, timeout=60)
    doc_files = [m.document for m in docs if m.document]
    print(f"→ Received {len(doc_files)} document(s)")
    return len(doc_files) >= 2


async def test_budget(chat):
    print("\n" + "=" * 60)
    print("TEST 2: Budget Form")
    print("=" * 60)
    # /cancel always answers now (a conversation fallback when one is running,
    # an "idle" reply otherwise) — consume it so replies don't shift by one.
    await chat.send('/cancel', timeout=8)
    await asyncio.sleep(0.5)
    await chat.send('/start', timeout=8)
    r = await chat.send('💰 Budget Form', timeout=8)
    await _skip_event_picker_if_shown(chat, r)

    steps = [
        ('LiveTest Budget 2026', 'Event name'),
        ('24-05-2026', 'Date'),
        ('Registration Fee, 50, 100', 'Revenue item 1'),
        ('done', 'Done revenue'),
        ('Prize Money, 1, 2000, 1, 2000', 'Expenditure item 1'),
        ('Snacks, 50, 30, 45, 35', 'Expenditure item 2'),
        ('done', 'Done expenditure'),
    ]
    for value, label in steps:
        await chat.send(value, label=label, timeout=8)

    print("ME  > confirm")
    await chat.client.send_message(BOT_USERNAME, 'confirm')
    docs = await chat.collect_bot_replies(expected=3, timeout=60)
    doc_files = [m.document for m in docs if m.document]
    print(f"→ Received {len(doc_files)} document(s)")
    return len(doc_files) >= 2


async def test_mail_prompt(chat):
    print("\n" + "=" * 60)
    print("TEST 3: Mail Prompt")
    print("=" * 60)
    # /cancel always answers now (a conversation fallback when one is running,
    # an "idle" reply otherwise) — consume it so replies don't shift by one.
    await chat.send('/cancel', timeout=8)
    await asyncio.sleep(0.5)
    await chat.send('/start', timeout=8)
    await chat.send('📧 Mail Prompt', timeout=8)

    # Should show event picker (we have some from prior tests) OR new flow
    # Pick "New event" to test that branch
    await chat.send('➕ New event', label='Force new event', timeout=8)
    await chat.send('LiveTest Mail 2026', label='Event name', timeout=8)
    await chat.send('25-05-2026', label='Date', timeout=8)
    await chat.send('Einstein Hall', label='Venue', timeout=8)
    await chat.send('A live test of the mail prompt feature.', label='Description', timeout=8)
    await chat.send('All VIT-AP students', label='Audience', timeout=8)
    await chat.send('https://forms.gle/test', label='Registration', timeout=8)
    await chat.send('Snacks + Certificates', label='Extra', timeout=8)

    # Now bot sends prompt — gather all messages. NOTE: Telegram consumes the
    # ``` markdown into a PRE entity, so the backticks never appear in .text —
    # detect the prompt by its content instead.
    replies = await chat.collect_bot_replies(expected=3, timeout=15)
    prompt_text = ''
    for r in replies:
        if r.text and 'EVENT DETAILS' in r.text:
            prompt_text = r.text
    print(f"→ Prompt block received: {len(prompt_text)} chars (contains 'EVENT DETAILS': {'EVENT DETAILS' in prompt_text})")
    return 'EVENT DETAILS' in prompt_text


async def test_report(chat):
    print("\n" + "=" * 60)
    print("TEST 4: Final Event Report (with uploads)")
    print("=" * 60)
    # /cancel always answers now (a conversation fallback when one is running,
    # an "idle" reply otherwise) — consume it so replies don't shift by one.
    await chat.send('/cancel', timeout=8)
    await asyncio.sleep(0.5)
    await chat.send('/start', timeout=8)
    r = await chat.send('📋 Final Event Report', timeout=8)
    await _skip_event_picker_if_shown(chat, r)

    # Text fields
    await chat.send('LiveTest Report 2026', label='Event name', timeout=8)
    await chat.send('Offline', label='Mode', timeout=8)
    await chat.send('Einstein Hall, AB-1', label='Venue', timeout=8)
    await chat.send('25-05-2026', label='Date', timeout=8)
    await chat.send('No', label='Has budget = No (skip signed uploads)', timeout=8)

    # Build assets
    approval_img = make_image('approval', (50, 200, 50))
    brochure_img = make_image('brochure', (200, 50, 50))
    cert_img = make_image('cert', (50, 200, 200))
    photo_img = make_image('photo', (200, 50, 200))
    xlsx = make_participants_xlsx()

    # Section 1: Approval
    await chat.send_photo(approval_img, label='Approval mail')
    await chat.send('done', label='Done approval', timeout=8)

    # Section 2: Brochure
    await chat.send_photo(brochure_img, label='Brochure', timeout=8)

    # Section 3: Schedule
    await chat.send('9:00 AM - 10:00 AM, Registration', label='Schedule 1', timeout=8)
    await chat.send('10:00 AM - 12:00 PM, Workshop', label='Schedule 2', timeout=8)
    await chat.send('done', label='Done schedule', timeout=8)

    # Section 4: Participants Excel
    await chat.send_document(xlsx, label='Participants xlsx', timeout=15)

    # Section 5: Attendance — new flow: Yes/No, then xlsx if Yes
    attendance_xlsx = make_participants_xlsx()  # reuse same shape
    await chat.send('Yes', label='Has attendance = Yes', timeout=8)
    await chat.send_document(attendance_xlsx, label='Attendance xlsx', timeout=15)

    # Section 6: Description
    await chat.send('To test the live report generation pipeline end-to-end.', label='Purpose', timeout=8)
    await chat.send('We exercised every code path: uploads, Excel parsing, attendance grids, certificates, and winners.', label='Brief', timeout=8)

    # Section 7: Certificates — new flow: Yes/No, then images if Yes
    await chat.send('Yes', label='Has certificates = Yes', timeout=8)
    await chat.send_photo(cert_img, label='Cert 1')
    await chat.send('done', label='Done certs', timeout=8)

    # Section 8: Photos
    await chat.send_photo(photo_img, label='Photo 1')
    await chat.send('done', label='Done photos', timeout=8)

    # Section 9: Winners
    await chat.send('Alice TestUser, 23BCE0001', label='Winner 1', timeout=8)
    await chat.send('done', label='Done winners', timeout=8)

    # Section 10: Guest Speaker (NEW) — Yes path
    speaker_img = make_image('speaker', (120, 80, 40))
    await chat.send('Yes', label='Has speaker = Yes', timeout=8)
    await chat.send_photo(speaker_img, label='Speaker photo')
    await chat.send('done', label='Done speaker files', timeout=8)
    await chat.send('Dr. Test Speaker is a guest lecturer at VIT-AP specializing in animation pipelines.',
                    label='Speaker bio', timeout=8)

    # Section 11: Feedback (NEW) — Yes path with link (not file)
    await chat.send('Yes', label='Has feedback = Yes', timeout=8)
    await chat.send('https://forms.gle/test-feedback', label='Feedback link', timeout=8)

    # Expenditure (NEW) — skipped because has_budget=No, jumps to summary

    # Summary + confirm
    print("ME  > confirm")
    await chat.client.send_message(BOT_USERNAME, 'confirm')
    docs = await chat.collect_bot_replies(expected=3, timeout=120)
    doc_files = [m.document for m in docs if m.document]
    print(f"→ Received {len(doc_files)} document(s)")
    for d in doc_files:
        print(f"   • {d.file_name} ({d.file_size:,} bytes)")
    return len(doc_files) >= 2


async def test_date_mismatch(chat):
    """Report for the event TEST 2 budgeted on 24-05-2026, but dated 26-05-2026.
    Confirm must stop and ask which date to use; 'everywhere' must regenerate
    the Budget with the report date and re-date the Word budget attached."""
    print("\n" + "=" * 60)
    print("TEST 6: Report date ≠ Budget date")
    print("=" * 60)
    from docx import Document
    budget_docx = TEST_DIR / 'old_budget.docx'
    d = Document()
    d.add_paragraph('Event Date:')
    d.add_paragraph('24-05-2026')
    d.save(budget_docx)
    img = make_image('mismatch', (90, 90, 160))
    xlsx = make_participants_xlsx()

    await chat.send('/cancel', timeout=8)
    await asyncio.sleep(0.5)
    await chat.send('/start', timeout=8)
    r = await chat.send('📋 Final Event Report', timeout=8)
    await _skip_event_picker_if_shown(chat, r)
    for value, label in [('LiveTest Budget 2026', 'Event name (same as TEST 2)'),
                         ('Offline', 'Mode'), ('Einstein Hall', 'Venue'),
                         ('26-05-2026', 'Date (differs from budget)'),
                         ('Yes', 'Has budget')]:
        await chat.send(value, label=label, timeout=8)
    await chat.send_document(budget_docx, label='Signed budget (Word, old date)')
    await chat.send('done', label='Done budget', timeout=8)
    await chat.send_photo(img, label='Signed proposal')
    await chat.send('done', label='Done proposal', timeout=8)
    await chat.send_photo(img, label='Approval')
    await chat.send('done', label='Done approval', timeout=8)
    await chat.send_photo(img, label='Brochure')
    await chat.send('9:00 AM - 10:00 AM, Registration', label='Schedule', timeout=8)
    await chat.send('done', label='Done schedule', timeout=8)
    await chat.send_document(xlsx, label='Participants', timeout=15)
    for value, label in [('No', 'Attendance'), ('Purpose text.', 'Purpose'),
                         ('Brief text.', 'Brief'), ('No', 'Certificates')]:
        await chat.send(value, label=label, timeout=8)
    await chat.send_photo(img, label='Photo')
    await chat.send('done', label='Done photos', timeout=8)
    for value, label in [('Bob Test, 23BCE0002', 'Winner'), ('done', 'Done winners'),
                         ('No', 'Speaker'), ('No', 'Feedback'), ('Yes', 'Expenditure')]:
        await chat.send(value, label=label, timeout=8)
    # Same old-dated budget as the Expenditure Statement — it must be re-dated too.
    await chat.send_document(budget_docx, label='Expenditure (same Word budget, old date)')
    await chat.send('done', label='Done expenditure', timeout=8)

    prompt = await chat.send('confirm', label='confirm (expect date question)', timeout=15)
    if not (prompt and prompt.text and "dates don't match" in prompt.text):
        print("→ No date question shown")
        return False
    print("ME  > [tap] Use report date everywhere")
    try:
        await chat.client.request_callback_answer(BOT_USERNAME, prompt.id, b'datefix:report', timeout=5)
    except Exception:
        pass  # the bot answers the query only after its slow work; the tap still lands
    docs = await chat.collect_bot_replies(expected=10, timeout=180)
    names = [m.document.file_name for m in docs if m.document]
    for n in names:
        print(f"   • {n}")

    import json, zipfile
    ev = json.loads((BASE / 'events' / '5422521708' / 'livetest_budget_2026' / 'event.json')
                    .read_text(encoding='utf-8'))
    budget_date = ev['budget']['date']
    # The attached Word budget is embedded as page images, so check the
    # attachment the report was built from, not the report's own XML.
    attached = (ev.get('report') or {}).get('signed_budget_files') or ['']
    xml = ''
    if attached[0].endswith('.docx') and Path(attached[0]).exists():
        xml = zipfile.ZipFile(attached[0]).read('word/document.xml').decode('utf-8')
    exp = (ev.get('report') or {}).get('expenditure_files') or ['']
    exml = ''
    if exp[0].endswith('.docx') and Path(exp[0]).exists():
        exml = zipfile.ZipFile(exp[0]).read('word/document.xml').decode('utf-8')
    print(f"→ saved budget date now: {budget_date}")
    print(f"→ attached budget {Path(attached[0]).name}: old date={'24-05-2026' in xml}, new date={'26-05-2026' in xml}")
    print(f"→ attached expenditure {Path(exp[0]).name}: old date={'24-05-2026' in exml}, new date={'26-05-2026' in exml}")
    # Uploads must be kept with the event, not left in temp/.
    upload_paths = [p for k in ('signed_budget_files', 'signed_proposal_files', 'expenditure_files',
                                'approval_images', 'photos')
                    for p in (ev.get('report') or {}).get(k) or []]
    in_event = all(f"{os.sep}livetest_budget_2026{os.sep}uploads{os.sep}" in p and Path(p).exists()
                   for p in upload_paths)
    print(f"→ {len(upload_paths)} uploads kept in the event's uploads folder: {in_event}")
    return (any(n.startswith('Budget_') for n in names)
            and any(n.startswith('Report_') for n in names)
            and budget_date == '26-05-2026'
            and in_event and len(upload_paths) >= 5
            and '26-05-2026' in xml and '24-05-2026' not in xml
            and '26-05-2026' in exml and '24-05-2026' not in exml)


async def test_guards(chat):
    """A bad date is re-asked, a stray word at the summary doesn't cancel, and
    a message sent while the document builds gets a 'still generating' reply."""
    print("\n" + "=" * 60)
    print("TEST 7: Date check, summary typo, busy reply")
    print("=" * 60)
    ok = True
    await chat.send('/cancel', timeout=8)
    await asyncio.sleep(0.5)
    await chat.send('/start', timeout=8)
    r = await chat.send('💰 Budget Form', timeout=8)
    await _skip_event_picker_if_shown(chat, r)
    await chat.send('LiveTest Guards 2026', label='Event name', timeout=8)
    r = await chat.send('tomorrow', label='Bad date', timeout=8)
    if not (r and r.text and "couldn't read that date" in r.text):
        print("→ FAIL: bad date was accepted"); ok = False
    r = await chat.send('1-9-26 to 3-9-26', label='Range date, loose format', timeout=8)
    if not (r and r.text and 'Revenue' in r.text):
        print("→ FAIL: valid range rejected"); ok = False
    for value, label in [('Registration Fee, 50, 100', 'Revenue'), ('done', 'Done revenue'),
                         ('Snacks, 50, 30, 45, 35', 'Expenditure'), ('done', 'Done expenditure')]:
        r = await chat.send(value, label=label, timeout=8)
    if not (r and r.text and '01-09-2026 to 03-09-2026' in r.text):
        print("→ FAIL: summary doesn't show the tidied date"); ok = False
    r = await chat.send('hmm', label='Stray word at summary', timeout=8)
    if not (r and r.text and "didn't catch" in r.text):
        print("→ FAIL: stray word cancelled the form"); ok = False
    r = await chat.send('ok', label="'ok' = confirm", timeout=8)
    if not (r and r.text and 'Generating' in r.text):
        print("→ FAIL: 'ok' didn't start generation"); ok = False
    await asyncio.sleep(1.0)
    r = await chat.send('/events', label='/events while generating (must be queued, not dropped)', timeout=8)
    if not (r and r.text and 'still being made' in r.text):
        print("→ FAIL: no 'queued' acknowledgement"); ok = False
    later = await chat.collect_bot_replies(expected=6, timeout=90)
    later.sort(key=lambda m: m.id)
    names = [m.document.file_name for m in later if m.document]
    print(f"→ Received {len(names)} document(s): {names}")
    events_idx = next((i for i, m in enumerate(later) if m.text and 'Your Events' in m.text), None)
    last_doc_idx = max((i for i, m in enumerate(later) if m.document), default=-1)
    if events_idx is None:
        print("→ FAIL: the queued /events was never handled"); ok = False
    elif events_idx < last_doc_idx:
        print("→ FAIL: /events was handled before the documents were sent"); ok = False
    else:
        print("→ queued /events answered after the documents ✔")
    return ok and len(names) >= 2


async def test_events_listing(chat):
    print("\n" + "=" * 60)
    print("TEST 5: /events listing")
    print("=" * 60)
    # /cancel always answers now (a conversation fallback when one is running,
    # an "idle" reply otherwise) — consume it so replies don't shift by one.
    await chat.send('/cancel', timeout=8)
    # A finished wizard now ends with a "What next?" menu message that can land
    # after the last document — drain stragglers so /events reads its own reply.
    await chat.collect_bot_replies(expected=5, timeout=3)
    await asyncio.sleep(0.5)
    r = await chat.send('/events', timeout=8)
    if r and r.text:
        # Count event entries
        count = r.text.count('•')
        print(f"→ /events listed {count} event(s)")
        return count >= 1
    return False


async def main():
    # Other bots on this machine share the same Telegram account, and two
    # processes on one .session file fight over it — point DAG_SESSION at a copy.
    session = os.getenv('DAG_SESSION') or str(BASE / 'dag_user')
    client = Client(
        name=session,
        api_id=TG_API_ID,
        api_hash=TG_API_HASH,
        no_updates=True,
    )
    async with client:
        me = await client.get_me()
        print(f"# Logged in as: {me.first_name} (id={me.id})")
        print(f"# Driving @{BOT_USERNAME}")

        chat = BotChat(client)
        await chat.init()

        results = {}
        results['Proposal'] = await test_proposal(chat)
        results['Budget'] = await test_budget(chat)
        results['Mail Prompt'] = await test_mail_prompt(chat)
        results['Report (uploads)'] = await test_report(chat)
        results['/events listing'] = await test_events_listing(chat)
        results['Date mismatch prompt'] = await test_date_mismatch(chat)
        results['Guards (date/typo/busy)'] = await test_guards(chat)

        print("\n" + "=" * 60)
        print("FINAL RESULTS")
        print("=" * 60)
        for name, ok in results.items():
            mark = "✅" if ok else "❌"
            print(f"  {mark}  {name}")
        all_ok = all(results.values())
        print("\n" + ("🎉 All tests passed!" if all_ok else "⚠️  Some tests failed."))


if __name__ == '__main__':
    asyncio.run(main())
