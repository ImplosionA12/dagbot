"""Read a people list out of a Google Forms .xlsx export.

Two sheet shapes show up in practice:

  flat  — one person per row:
            Name | Registration Number | ...

  team  — one *team* per row, each member in their own column group:
            Team Name | Player 1 — Full Name | Player 1 — Registration Number | ...

The old reader took the first header containing 'name' and the first containing
'reg', then emitted one entry per row. On a team export that silently collapsed
34 teams x 4 players into 34 rows of (team name, team lead's reg no) — the other
102 players were in the file and never read. This module finds *every* member
column pair, so a team sheet expands to one entry per player.
"""
import re

# 'Player 1 — Full Name', 'P2 Reg No', 'Member 3 Name' — the slot number is what
# groups one person's columns together. Gaming forms say Player, hackathon forms
# say Member or Teammate; the word also decides what the report column is called.
SLOT_RE = re.compile(
    r'\b(?P<role>player|participant|teammate|member|student|p)\s*[-–—.]?\s*(?P<num>\d+)',
    re.I,
)

# Matched word → the heading the report uses for that column, and the per-row
# label ('P1', 'M2'). Anything unrecognised reads as a Player.
ROLE_NAMES = {
    'player': 'Player', 'p': 'Player',
    'participant': 'Participant',
    'member': 'Member', 'teammate': 'Member',
    'student': 'Student',
}

# 'BGMI In-Game Name (IGN)' also contains 'name'; it is not the person's name.
NOT_A_REAL_NAME = ('in-game', 'ingame', 'ign', 'nick', 'username', 'user name', 'gamer')


def _is_name(h: str) -> bool:
    h = h.lower()
    if 'name' not in h:
        return False
    return not any(bad in h for bad in NOT_A_REAL_NAME)


def _is_reg(h: str) -> bool:
    h = h.lower()
    return 'registration' in h or bool(re.search(r'\breg\.?\s*(no|num|#)?\b', h))


def _pick(headers, idxs, test, prefer=None):
    """First column in idxs whose header passes test, preferring a header that
    contains `prefer` (so 'Full Name' wins over a bare 'Name')."""
    hits = [i for i in idxs if test(headers[i])]
    if not hits:
        return None
    if prefer:
        for i in hits:
            if prefer in headers[i].lower():
                return i
    return hits[0]


def find_columns(headers):
    """Map a header row to the columns worth reading.

    Returns {'team': idx|None, 'slots': [(label, name_idx, reg_idx), ...],
             'role': 'Player'|'Member'|..., 'name': idx|None, 'reg': idx|None} —
    'slots' drives team mode, 'name'/'reg' the flat fallback.
    """
    lower = [h.lower() for h in headers]

    # Group columns by slot number. A column with no slot number is shared
    # (Timestamp, Team Name, the confirmation checkbox...).
    by_slot, shared, words = {}, [], []
    for i, h in enumerate(lower):
        m = SLOT_RE.search(h)
        if m:
            by_slot.setdefault(int(m.group('num')), []).append(i)
            words.append(m.group('role').lower())
        else:
            shared.append(i)

    # A sheet mixing 'Player 1' and 'P2' should settle on one heading, so go with
    # the word that appears most often.
    role = 'Player'
    if words:
        winner = max(set(words), key=words.count)
        role = ROLE_NAMES.get(winner, 'Player')

    slots = []
    for n in sorted(by_slot):
        idxs = by_slot[n]
        name_i = _pick(headers, idxs, _is_name, prefer='full name')
        reg_i = _pick(headers, idxs, _is_reg)
        if name_i is not None and reg_i is not None:
            slots.append((f"{role[0]}{n}", name_i, reg_i))

    team_i = next((i for i in shared if 'team' in lower[i]), None)

    # Flat fallback: the shared columns only, so a team sheet never falls back
    # onto 'Team Name' as if it were a person.
    pool = shared or list(range(len(headers)))
    flat_name = _pick(headers, [i for i in pool if i != team_i], _is_name, prefer='full name')
    flat_reg = _pick(headers, [i for i in pool if i != team_i], _is_reg)

    return {'team': team_i, 'slots': slots, 'role': role,
            'name': flat_name, 'reg': flat_reg}


def _cell(row, idx):
    if idx is None or idx >= len(row) or row[idx] is None:
        return ''
    return str(row[idx]).strip()


def parse_people(path):
    """Read an .xlsx into (people, meta).

    people — [{'name', 'reg_no'}] plus 'team' and 'slot' when the sheet is
             team-shaped. meta — {'mode', 'teams', 'count'}.

    Raises ValueError if no usable Name/Registration columns are present.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    try:
        header_row = next(ws.iter_rows(max_row=1))
    except StopIteration:
        raise ValueError("The sheet is empty.")
    headers = [str(c.value).strip() if c.value is not None else '' for c in header_row]

    cols = find_columns(headers)
    people, teams = [], 0

    if cols['slots']:
        for row in ws.iter_rows(min_row=2, values_only=True):
            team = _cell(row, cols['team'])
            members = []
            for label, name_i, reg_i in cols['slots']:
                name, reg = _cell(row, name_i), _cell(row, reg_i)
                if not name and not reg:
                    continue
                entry = {'name': name, 'reg_no': reg, 'slot': label}
                if team:
                    entry['team'] = team
                    entry['role'] = cols['role']
                members.append(entry)
            if not members:
                continue  # blank row — these exports carry a few
            teams += 1
            if not team:
                # No team column: the slots are just a wide flat sheet.
                for m in members:
                    m.pop('slot', None)
            people.extend(members)
        # `is not None`, not truthiness — a team column at index 0 is real, and a
        # sheet with no Timestamp puts Team Name exactly there.
        mode = 'team' if cols['team'] is not None else 'flat'
        return people, {'mode': mode, 'teams': teams if mode == 'team' else 0,
                        'count': len(people), 'role': cols['role']}

    if cols['name'] is None or cols['reg'] is None:
        raise ValueError("Could not find Name and Registration Number columns.")

    for row in ws.iter_rows(min_row=2, values_only=True):
        name, reg = _cell(row, cols['name']), _cell(row, cols['reg'])
        if name and reg:
            people.append({'name': name, 'reg_no': reg})
    return people, {'mode': 'flat', 'teams': 0, 'count': len(people),
                    'role': cols['role']}


def summary(meta) -> str:
    """One-line description of what was read, for the wizard's confirmation."""
    if meta['mode'] == 'team' and meta['teams']:
        return f"{meta['teams']} teams · {meta['count']} participants"
    return f"{meta['count']} participants"
