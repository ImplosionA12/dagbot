"""Build documents off the event loop, and keep answering while they build.

Word/Excel conversion takes 10-60 s. It used to run on the bot's event loop,
so for that whole time the bot answered nothing — not /cancel, not Back, not
"is it working?". Now the generator runs in a worker thread, and any message
that arrives meanwhile is acknowledged at once and handled after the document
is sent.

Updates from one user are still handled strictly one after another (the wizard
states depend on it).
"""
import asyncio
import threading
from collections import defaultdict

from telegram import Update
from telegram.ext import BaseUpdateProcessor

BUSY: set[int] = set()
# Word and Excel are single COM servers — never drive them from two threads.
_OFFICE_LOCK = threading.Lock()


def _run_with_com(fn, args, kwargs):
    import pythoncom
    pythoncom.CoInitialize()          # every thread that touches COM needs this
    try:
        with _OFFICE_LOCK:
            return fn(*args, **kwargs)
    finally:
        pythoncom.CoUninitialize()


async def run_generator(update: Update, fn, *args, **kwargs):
    """await fn(*args, **kwargs) in a worker thread; the user is marked busy."""
    uid = update.effective_user.id if update.effective_user else 0
    BUSY.add(uid)
    try:
        return await asyncio.to_thread(_run_with_com, fn, args, kwargs)
    finally:
        BUSY.discard(uid)


async def _answer_busy(update: Update):
    q = update.callback_query
    if q is not None:
        # resolve_text() answers again later; it tolerates the second answer.
        await q.answer("⏳ Your document is still being made — I'll do this right after.")
        return
    if update.effective_message is not None:
        await update.effective_message.reply_text(
            "⏳ Your document is still being made — I'll handle this message "
            "right after it's sent.")


class PerUserUpdateProcessor(BaseUpdateProcessor):
    """Different users run in parallel; one user's updates run in order.

    A message that arrives while that user's document is building is
    acknowledged at once, then waits its turn like any other — it used to be
    answered and dropped, so the user had to send it again."""

    def __init__(self):
        super().__init__(max_concurrent_updates=64)
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def do_process_update(self, update, coroutine):
        uid = (update.effective_user.id
               if isinstance(update, Update) and update.effective_user else 0)
        if uid in BUSY:
            try:
                await _answer_busy(update)
            except Exception:
                pass
        async with self._locks[uid]:
            await coroutine

    async def initialize(self):
        pass

    async def shutdown(self):
        pass
