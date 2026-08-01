"""Per-user, per-event storage.

Directory layout: events/<user_id>/<slug>/event.json + generated artifacts (PDFs/DOCX/XLSX)

Each event JSON shape:
{
  "slug":       "tech_fest_2026",
  "name":       "Tech Fest 2026",
  "created_at": "2026-05-23T20:00:00",
  "updated_at": "2026-05-23T20:30:00",
  "proposal":   {...} | null,
  "budget":     {...} | null,
  "report":     {...} | null
}

Old layout was events/<user_id>/<slug>.json (flat). Existing flat JSONs are
auto-migrated into per-event folders on first access.
"""
import json
import os
import re
from datetime import datetime

EVENTS_DIR = os.path.join(os.path.dirname(__file__), 'events')


def slugify(name: str) -> str:
    s = re.sub(r'[^a-zA-Z0-9_-]+', '_', (name or '').strip())
    s = s.strip('_').lower()
    return s or 'untitled'


def _user_dir(user_id: int) -> str:
    p = os.path.join(EVENTS_DIR, str(user_id))
    os.makedirs(p, exist_ok=True)
    return p


def event_folder(user_id: int, slug: str) -> str:
    """Folder that holds an event's JSON + generated artifacts. Created on demand."""
    p = os.path.join(_user_dir(user_id), slug)
    os.makedirs(p, exist_ok=True)
    return p


def _event_json_path(user_id: int, slug: str) -> str:
    return os.path.join(event_folder(user_id, slug), 'event.json')


def _migrate_flat_jsons(user_id: int):
    """Move any legacy events/<user>/<slug>.json into events/<user>/<slug>/event.json."""
    d = _user_dir(user_id)
    for fn in os.listdir(d):
        if not fn.endswith('.json'):
            continue
        flat_path = os.path.join(d, fn)
        if not os.path.isfile(flat_path):
            continue
        slug = fn[:-5]
        new_path = os.path.join(d, slug, 'event.json')
        if os.path.exists(new_path):
            os.remove(flat_path)  # already migrated; drop the stale flat copy
            continue
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        os.replace(flat_path, new_path)


def list_events(user_id: int) -> list[dict]:
    """Newest-first list of all events for this user."""
    _migrate_flat_jsons(user_id)
    d = _user_dir(user_id)
    folders = []
    for name in os.listdir(d):
        sub = os.path.join(d, name)
        if os.path.isdir(sub) and os.path.exists(os.path.join(sub, 'event.json')):
            folders.append(name)
    folders.sort(key=lambda s: os.path.getmtime(os.path.join(d, s, 'event.json')),
                 reverse=True)
    out = []
    for slug in folders:
        try:
            with open(_event_json_path(user_id, slug), 'r', encoding='utf-8') as f:
                out.append(json.load(f))
        except Exception:
            continue
    return out


def get_event(user_id: int, slug: str) -> dict | None:
    _migrate_flat_jsons(user_id)
    path = _event_json_path(user_id, slug)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_event(user_id: int, event: dict) -> str:
    """Saves the full event record. Returns the slug."""
    slug = event.get('slug') or slugify(event.get('name', 'untitled'))
    event['slug'] = slug
    now = datetime.now().isoformat(timespec='seconds')
    event['updated_at'] = now
    event.setdefault('created_at', now)
    # Write through a temp file: a crash (or a restart) partway through a direct
    # write leaves a truncated event.json, and every reader treats unparseable
    # JSON as "no such event" — the whole event silently disappears.
    path = _event_json_path(user_id, slug)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(event, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return slug


def upsert_flow(user_id: int, event_name: str, flow_type: str, data: dict) -> str:
    """flow_type ∈ {'proposal', 'budget', 'report'}.

    Loads (or creates) the event and merges this flow's data under that key.
    """
    slug = slugify(event_name)
    existing = get_event(user_id, slug) or {'name': event_name, 'slug': slug}
    existing[flow_type] = data
    return save_event(user_id, existing)


def delete_event(user_id: int, slug: str) -> bool:
    """Delete the full event folder (JSON + artifacts)."""
    folder = os.path.join(_user_dir(user_id), slug)
    if not os.path.isdir(folder):
        # fall back to old flat layout if migration hasn't run yet
        legacy = os.path.join(_user_dir(user_id), f"{slug}.json")
        if os.path.exists(legacy):
            os.remove(legacy)
            return True
        return False
    import shutil
    shutil.rmtree(folder)
    return True


def event_status_label(event: dict) -> str:
    """Returns 'P/B/R' style label showing which docs exist."""
    flags = []
    if event.get('proposal'): flags.append('P')
    if event.get('budget'): flags.append('B')
    if event.get('report'): flags.append('R')
    return '+'.join(flags) if flags else '—'
