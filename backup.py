"""Zip the saved events (answers, generated files and uploads) to another drive.

events/ is git-ignored and lived only on D:, so one bad disk or a stray delete
lost every saved event. A zip goes to BACKUP_DIR (default C:\\DAGBot_backups,
a different drive) at startup and every 24 hours; the newest KEEP are kept.
"""
import logging
import os
import zipfile
from datetime import datetime

from event_store import EVENTS_DIR

logger = logging.getLogger(__name__)

DEFAULT_BACKUP_DIR = r'C:\DAGBot_backups'
KEEP = 14


def make_backup() -> str:
    """Write one backup zip and prune old ones. Returns the zip's path."""
    # Read at call time: bot.py loads .env after its imports.
    BACKUP_DIR = os.getenv('BACKUP_DIR') or DEFAULT_BACKUP_DIR
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    final = os.path.join(BACKUP_DIR, f'events_{stamp}.zip')
    tmp = final + '.part'
    root = os.path.dirname(os.path.abspath(EVENTS_DIR))
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for dirpath, _, files in os.walk(EVENTS_DIR):
            for fn in files:
                if fn.endswith('.tmp'):
                    continue                    # a save in flight
                full = os.path.join(dirpath, fn)
                z.write(full, os.path.relpath(full, root))
    os.replace(tmp, final)                      # never leave a half zip as "latest"
    zips = sorted(f for f in os.listdir(BACKUP_DIR)
                  if f.startswith('events_') and f.endswith('.zip'))
    for old in zips[:-KEEP]:
        os.remove(os.path.join(BACKUP_DIR, old))
    return final


async def backup_job(context):
    import asyncio
    try:
        path = await asyncio.to_thread(make_backup)
        logger.info("events backed up to %s (%.1f MB)", path, os.path.getsize(path) / 1e6)
    except Exception:
        logger.exception("events backup failed")
