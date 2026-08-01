"""Header-shape matrix for handlers.xlsx_people.

Google Forms exports vary a lot between events — gaming forms say 'Player 1',
hackathon forms say 'Member 1' or 'Team Member 1', and some drop the Timestamp
column so Team Name lands at index 0. This pins the shapes we handle.

    python _test_parser.py
"""
import os
import sys

import openpyxl

from handlers.xlsx_people import parse_people, summary

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp')
os.makedirs(TMP, exist_ok=True)
SHEET = os.path.join(TMP, '_test_parser.xlsx')

passed = failed = 0


def _write(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(SHEET)
    return SHEET


def check(label, headers, rows, want_count, want_teams, want_role=None, want_slots=None):
    global passed, failed
    try:
        people, meta = parse_people(_write(headers, rows))
    except Exception as e:
        print(f'  FAIL {label}: raised {e!r}')
        failed += 1
        return

    errs = []
    if meta['count'] != want_count:
        errs.append(f"count {meta['count']} != {want_count}")
    if meta['teams'] != want_teams:
        errs.append(f"teams {meta['teams']} != {want_teams}")
    if want_role and meta.get('role') != want_role:
        errs.append(f"role {meta.get('role')!r} != {want_role!r}")
    if want_slots is not None:
        got = [p.get('slot') for p in people[:len(want_slots)]]
        if got != want_slots:
            errs.append(f"slots {got} != {want_slots}")

    if errs:
        print(f'  FAIL {label}: ' + '; '.join(errs))
        failed += 1
    else:
        role = f"  [{meta.get('role')}]" if want_teams else ''
        print(f'  ok   {label:44} -> {summary(meta)}{role}')
        passed += 1


def expect_error(label, headers, rows):
    global passed, failed
    try:
        parse_people(_write(headers, rows))
    except ValueError:
        print(f'  ok   {label:44} -> ValueError as expected')
        passed += 1
        return
    except Exception as e:
        print(f'  FAIL {label}: wrong exception {e!r}')
        failed += 1
        return
    print(f'  FAIL {label}: expected ValueError, got none')
    failed += 1


print('\n-- team sheets, varying the slot word --')
for word, role, initial in [('Player', 'Player', 'P'), ('Member', 'Member', 'M'),
                            ('Team Member', 'Member', 'M'), ('Teammate', 'Member', 'M'),
                            ('Participant', 'Participant', 'P'), ('Student', 'Student', 'S'),
                            ('P', 'Player', 'P')]:
    hdrs = ['Timestamp', 'Team Name']
    for n in (1, 2, 3):
        hdrs += [f'{word} {n} — Full Name', f'{word} {n} — Registration Number']
    check(f'{word} N — 2 teams x 3', hdrs,
          [['t', 'Alpha', 'A1', '23A1', 'A2', '23A2', 'A3', '23A3'],
           ['t', 'Beta', 'B1', '23B1', 'B2', '23B2', 'B3', '23B3']],
          6, 2, role, [f'{initial}1', f'{initial}2', f'{initial}3'])

print('\n-- separator and casing variants --')
check('Member1 (no space)', ['Team Name', 'Member1 Name', 'Member1 Reg No'],
      [['A', 'x', '23x']], 1, 1, 'Member', ['M1'])
check('MEMBER - 1 (dash, caps)',
      ['Team Name', 'MEMBER - 1 FULL NAME', 'MEMBER - 1 REGISTRATION NUMBER'],
      [['A', 'x', '23x']], 1, 1, 'Member', ['M1'])
check('P1/P2 short form', ['Team Name', 'P1 Name', 'P1 Reg No', 'P2 Name', 'P2 Reg No'],
      [['A', 'x', '23x', 'y', '23y']], 2, 1, 'Player', ['P1', 'P2'])
check('member.1 (dot)', ['Team Name', 'Member.1 Full Name', 'Member.1 Reg. No'],
      [['A', 'x', '23x']], 1, 1, 'Member', ['M1'])

print('\n-- distractor columns --')
check('IGN column must not beat Full Name',
      ['Team Name', 'Player 1 — Full Name', 'Player 1 — Registration Number',
       'Player 1 — BGMI In-Game Name (IGN)', 'Player 1 — Email Address'],
      [['A', 'Real Name', '23x', 'ProGamer', 'a@b.c']], 1, 1, 'Player')
check('Member email/phone ignored',
      ['Team Name', 'Member 1 Name', 'Member 1 Email', 'Member 1 Phone', 'Member 1 Reg No'],
      [['A', 'x', 'a@b.c', '999', '23x']], 1, 1, 'Member')

print('\n-- ragged / partial rows --')
check('blank leading rows skipped',
      ['Timestamp', 'Team Name', 'Member 1 Name', 'Member 1 Reg No'],
      [[None, None, None, None], [None, None, None, None],
       ['t', 'A', 'x', '23x'], ['t', 'B', 'y', '23y']], 2, 2, 'Member')
check('team with only 2 of 4 slots filled',
      ['Team Name'] + [h for n in (1, 2, 3, 4)
                       for h in (f'Member {n} Name', f'Member {n} Reg No')],
      [['A', 'x', '23x', 'y', '23y', None, None, None, None]], 2, 1, 'Member')

print('\n-- flat sheets (no slots) --')
check('flat Name + Registration Number', ['Name', 'Registration Number', 'Email'],
      [['x', '23x', 'a@b'], ['y', '23y', 'c@d']], 2, 0)
check('flat Full Name + Reg No', ['Full Name', 'Reg No'], [['x', '23x']], 1, 0)
check('slots but no team column -> flat',
      ['Member 1 Name', 'Member 1 Reg No', 'Member 2 Name', 'Member 2 Reg No'],
      [['x', '23x', 'y', '23y']], 2, 0)

print('\n-- rejections --')
expect_error('no name/reg columns at all', ['Foo', 'Bar'], [[1, 2]])
expect_error('name but no reg column', ['Full Name', 'Email'], [['x', 'a@b']])

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
