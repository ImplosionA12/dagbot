"""Final Event Report generator — hybrid template-fill.

Opens templates/report_converted.docx (the Form3 template) and:
  1. Fills cover-page placeholders (club, year, coordinator)
  2. Fills REPORT-page event details (Event Name, Mode, Venue, Date)
  3. Removes the "Add these in your Report:" checklist (instructions to the human
     filler — the bot replaces those instructions with real content)
  4. Appends sections 1-11 with the wizard data:
       1. Brochure / poster
       2. Detailed schedule
       3. Registered participant list (Name + Reg No table)
       4. Attendance list (Excel → Name + Reg No table)
       5. Brief description of the event
       6. Sample certificate copies         (only if uploaded)
       7. Geotagged photographs / Event Screenshots
       8. Winners list                       (only if any)
       9. Guest Speaker's profile with Photo (only if any)
      10. Feedback forms sample              (only if any)
      11. Expenditure Statement              (only if has_budget AND uploaded)
  5. Inserts approval mail screenshot right after the REPORT page

The cover + REPORT page keep the institute's exact bordered layout because they
come straight from the template — no rebuild.
"""
import os
import shutil
import logging
from datetime import date
from copy import deepcopy

from docx import Document
from docx.shared import Pt, Inches, Twips
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from PIL import Image as PILImage

from generators.file_utils import (
    docx_to_pdf, file_to_images, stamp_docx_author, disable_soft_return_justify, modernize_docx,
    safe_filename,
)

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(BASE_DIR, 'templates', 'report_converted.docx')
TEMP_DIR = os.path.join(BASE_DIR, 'temp')
os.makedirs(TEMP_DIR, exist_ok=True)

# Fixed club logo (like the coordinator name) — drop any image named
# "club_logo.*" or "DAG logo.*" into templates/ and it's auto-inserted on the cover.
LOGO_CANDIDATES = [
    'DAG logo.png', 'DAG logo.jpg', 'DAG logo.jpeg',
    'club_logo.png', 'club_logo.jpg', 'club_logo.jpeg',
]
LOGO_WIDTH_IN = 2.2


def _resolve_logo_path():
    for name in LOGO_CANDIDATES:
        p = os.path.join(BASE_DIR, 'templates', name)
        if os.path.exists(p):
            return p
    return None

FONT = 'Times New Roman'
IMG_MAX_W_IN = 6.0
IMG_FULL_H_IN = 8.5
IMG_HALF_H_IN = 4.0


# ────────────────────────────────────────────────────────────────────────────
# Doc helpers
# ────────────────────────────────────────────────────────────────────────────
def _run(p, text, bold=False, size=12, underline=False):
    r = p.add_run(text)
    r.bold = bold
    r.underline = underline
    r.font.name = FONT
    r.font.size = Pt(size)
    rPr = r._element.get_or_add_rPr()
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = OxmlElement('w:rFonts')
        rPr.insert(0, rFonts)
    rFonts.set(qn('w:ascii'), FONT)
    rFonts.set(qn('w:hAnsi'), FONT)
    return r


def _para(doc, text='', bold=False, size=12, align=None, underline=False):
    """Add text as one paragraph per line. Newlines must NOT go through
    add_run (they'd become <w:br/> soft breaks, which justify to full page
    width and leave un-deletable gaps when the text is edited in Word).
    Blank lines in user text are dropped — paragraph spacing already
    separates paragraphs, and empty paragraphs waste page space."""
    lines = [ln.strip() for ln in str(text).replace('\r', '').split('\n')] if text else ['']
    lines = [ln for ln in lines if ln] or ['']
    first_p = None
    for line in lines:
        p = doc.add_paragraph()
        if align is not None:
            p.alignment = align
        if line:
            _run(p, line, bold=bold, size=size, underline=underline)
        if first_p is None:
            first_p = p
    return first_p


def _cell_text(cell, text, size=11, bold=False, align=None, justify_over=60):
    """Write user text into a table cell without ever creating a soft break.

    `run.add_run("a\\nb")` turns each newline into <w:br/>, which is how a pasted
    multi-line block ended up in a schedule cell as one ragged, un-justified
    lump. One paragraph per line instead — same rule the body text follows.

    Anything longer than `justify_over` characters is justified so the cell's
    right edge lines up like the prose sections; short values (times, names,
    reg numbers) keep their natural alignment, which reads better centred.
    """
    lines = [ln.strip() for ln in str(text or '').replace('\r', '').split('\n')]
    lines = [ln for ln in lines if ln] or ['']
    cell.text = ''
    first = cell.paragraphs[0]
    multiline = len(lines) > 1
    for i, line in enumerate(lines):
        p = first if i == 0 else cell.add_paragraph()
        if multiline:
            # Uniform spacing across the block — leaving the first paragraph on
            # the table's default opened a gap under line 1 that the rest of the
            # lines didn't have. Single-line cells keep whatever the table had,
            # so nothing shifts in the forms that were already fine.
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
        if align is not None:
            p.alignment = align
        elif len(line) > justify_over:
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if line:
            _run(p, line, size=size, bold=bold)
    return first


def _apply_page_borders(doc):
    """Add a single black box border around every page (matches the cover table look).
    Done at the section properties level so all pages — cover and body — get it."""
    sectPr = doc.sections[0]._sectPr
    # Remove any existing pgBorders so we don't duplicate
    for existing in sectPr.findall(qn('w:pgBorders')):
        sectPr.remove(existing)
    pgBorders = OxmlElement('w:pgBorders')
    pgBorders.set(qn('w:offsetFrom'), 'page')
    for side in ('top', 'left', 'bottom', 'right'):
        b = OxmlElement(f'w:{side}')
        b.set(qn('w:val'), 'single')
        b.set(qn('w:sz'), '18')      # 18 eighths-of-a-point ≈ 2.25pt
        b.set(qn('w:space'), '24')   # offset from page edge
        b.set(qn('w:color'), '000000')
        pgBorders.append(b)
    # w:pgBorders belongs right after w:pgMar in the schema; at the end Word may
    # ignore it.
    pgMar = sectPr.find(qn('w:pgMar'))
    if pgMar is not None:
        pgMar.addnext(pgBorders)
    else:
        sectPr.append(pgBorders)
    # The border sits 24pt in from the page edge. The template's 0.25" (18pt)
    # bottom margin lets table rows run past it, so the frame cuts through the
    # last row of a page. Keep the text clear of the line.
    section = doc.sections[0]
    if section.bottom_margin is None or section.bottom_margin < Twips(720):
        section.bottom_margin = Twips(720)


def _append_borderless_section(doc):
    """Start a fresh page whose section carries no page border.

    _apply_page_borders frames every page, which is right for the report's own
    pages but wrong for an attached form — the budget already has its own frame
    and ends up boxed twice. A section is the only level at which Word lets the
    page border be switched back off, and add_section inherits the previous
    section's properties, so the border has to be stripped from the copy."""
    from docx.enum.section import WD_SECTION

    section = doc.add_section(WD_SECTION.NEW_PAGE)
    for existing in section._sectPr.findall(qn('w:pgBorders')):
        section._sectPr.remove(existing)
    return section


SECTION_SPACER_PT = 14  # vertical breathing room between section blocks


def _section_block(doc):
    """Create a 1-row 1-cell sub-table marked cantSplit (invisible borders)
    and return its cell. Anything appended to this cell is guaranteed to stay
    together on the same page — heading + content can't be split — while
    multiple small section-blocks still pack onto the same page if they fit.

    A small spacer paragraph is appended to the body AFTER the sub-table so
    consecutive sections don't touch each other visually."""
    tbl = doc.add_table(rows=1, cols=1)
    tbl.autofit = True
    tbl.rows[0]._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
    # Make every border 'nil' so the wrapper is invisible
    tblPr = tbl._tbl.find(qn('w:tblPr'))
    if tblPr is None:
        tblPr = OxmlElement('w:tblPr')
        tbl._tbl.insert(0, tblPr)
    for existing in tblPr.findall(qn('w:tblBorders')):
        tblPr.remove(existing)
    tblBorders = OxmlElement('w:tblBorders')
    for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        b = OxmlElement(f'w:{side}')
        b.set(qn('w:val'), 'nil')
        tblBorders.append(b)
    tblPr.append(tblBorders)
    cell = tbl.rows[0].cells[0]
    # Cell starts with one empty paragraph by default — remove it so the first
    # content we add isn't preceded by blank space.
    for p in list(cell.paragraphs):
        if not p.text.strip() and 'blip' not in p._p.xml and 'imagedata' not in p._p.xml:
            p._element.getparent().remove(p._element)
            break
    # Spacer paragraph in body after the block — provides breathing room between sections.
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(SECTION_SPACER_PT)
    return cell


def _remove_outer_table_border(doc):
    """The cover table already has its own border from the template — once we
    apply page borders, the table border would double up. Strip the table border."""
    if not doc.tables:
        return
    tbl = doc.tables[0]
    tblPr = tbl._tbl.find(qn('w:tblPr'))
    if tblPr is None:
        return
    for existing in tblPr.findall(qn('w:tblBorders')):
        tblPr.remove(existing)
    tblBorders = OxmlElement('w:tblBorders')
    for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        b = OxmlElement(f'w:{side}')
        b.set(qn('w:val'), 'nil')
        tblBorders.append(b)
    tblPr.append(tblBorders)


def _page_break(doc):
    p = doc.add_paragraph()
    p.add_run().add_break(WD_BREAK.PAGE)


def _set_keep_with_next(paragraph):
    """Tell Word/LibreOffice not to separate this paragraph from the next."""
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.keep_together = True


def _section_heading(doc, number, title):
    """Section heading used in the document body (not inside a cell)."""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    label = f"{number}. {title}" if number else title
    _run(p, label, bold=True, size=13, underline=True)
    _set_keep_with_next(p)
    blank = doc.add_paragraph()
    _set_keep_with_next(blank)
    return p


def _academic_year() -> str:
    today = date.today()
    if today.month >= 7:
        return f"{today.year}-{today.year + 1}"
    return f"{today.year - 1}-{today.year}"


def _set_para_text(paragraph, text):
    """Replace paragraph text, keeping the first run's formatting.
    Strips newlines to spaces — template placeholders are single-line fields,
    and run.text would turn \\n into <w:br/> soft breaks (see _para)."""
    text = ' '.join(str(text or '').split('\n'))
    if paragraph.runs:
        paragraph.runs[0].text = text
        for r in paragraph.runs[1:]:
            r.text = ''
    else:
        paragraph.add_run(text)


# Roughly the most rows that still fit on one page; past this a table is going
# to break across pages no matter what we ask for.
_LONG_TABLE_ROWS = 25


def _keep_table_together(table):
    """Stop a table straddling a page break.

    keepNext on every row only makes sense while the table can actually fit a
    page. On a 137-row participant list it asks Word to hold all 136 rows with
    the next one, which just pushes the whole table onto a fresh page and leaves
    a gap behind it. Long tables keep cantSplit (no row split through its own
    height) and glue only the header to the first data row.
    """
    rows = list(table.rows)
    last_idx = len(rows) - 1
    glue_all = len(rows) <= _LONG_TABLE_ROWS
    for ri, row in enumerate(rows):
        trPr = row._tr.get_or_add_trPr()
        trPr.append(OxmlElement('w:cantSplit'))
        for cell in row.cells:
            for p in cell.paragraphs:
                # Via paragraph_format, not a raw append: w:pPr children have a
                # required order (keepNext before keepLines, both before jc) and
                # Word ignores the ones that arrive out of sequence.
                if ri < last_idx and (glue_all or ri == 0):
                    p.paragraph_format.keep_with_next = True
                p.paragraph_format.keep_together = True


# ── Image insertion ──────────────────────────────────────────────────────────
def _image_size_inches(img_path, max_w_in, max_h_in):
    try:
        with PILImage.open(img_path) as img:
            w_px, h_px = img.size
    except Exception:
        return max_w_in, max_h_in
    if w_px == 0 or h_px == 0:
        return max_w_in, max_h_in
    aspect = w_px / h_px
    new_w = max_w_in
    new_h = new_w / aspect
    if new_h > max_h_in:
        new_h = max_h_in
        new_w = new_h * aspect
    return new_w, new_h


def _insert_image(doc, img_path, max_w_in, max_h_in):
    if not img_path or not os.path.exists(img_path):
        return
    w_in, h_in = _image_size_inches(img_path, max_w_in, max_h_in)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    try:
        p.add_run().add_picture(img_path, width=Inches(w_in), height=Inches(h_in))
    except Exception as e:
        logger.warning(f"insert_image failed for {img_path}: {e}")


def _insert_full_page(doc, paths):
    paths = [p for p in paths if p and os.path.exists(p)]
    for i, path in enumerate(paths):
        _insert_image(doc, path, IMG_MAX_W_IN, IMG_FULL_H_IN)
        if i < len(paths) - 1:
            _page_break(doc)


def _insert_two_per_page(doc, paths):
    paths = [p for p in paths if p and os.path.exists(p)]
    for i, path in enumerate(paths):
        _insert_image(doc, path, IMG_MAX_W_IN, IMG_HALF_H_IN)
        if (i + 1) % 2 == 0 and (i + 1) < len(paths):
            _page_break(doc)


def _expand_uploads(file_paths):
    out = []
    for fp in file_paths:
        out.extend(file_to_images(fp, TEMP_DIR))
    return out


# ── Person-list table (used for participants, attendees, winners) ───────────
def _set_vmerge(cell, restart=False):
    """Vertically merge this cell with the one above (or start a new span)."""
    el = OxmlElement('w:vMerge')
    if restart:
        el.set(qn('w:val'), 'restart')
    cell._tc.get_or_add_tcPr().append(el)


def _team_groups(rows):
    """Consecutive rows sharing a team name, in sheet order."""
    groups = []
    for person in rows:
        team = person.get('team', '')
        if groups and groups[-1][0] == team:
            groups[-1][1].append(person)
        else:
            groups.append((team, [person]))
    return groups


def _add_team_person_table(doc, rows):
    """5-column S.No / Team / Player / Full Name / Reg No table, with the S.No
    and Team cells merged down across each team's members."""
    groups = _team_groups(rows)
    tbl = doc.add_table(rows=len(rows) + 1, cols=5)
    tbl.style = 'Table Grid'
    tbl.autofit = False
    # Both halves matter: tblGrid places the column, tcW holds it there under
    # the fixed layout. Setting only cell.width leaves an even tblGrid behind.
    # 9360 twips total (6.5" text width). S.No gets 800 rather than 600: at 600,
    # Word's 0.08" cell margins leave less room than bold "S.No" needs and the
    # header wraps to "S.N / o" on every page.
    widths = [Twips(800), Twips(2150), Twips(900), Twips(2750), Twips(2760)]
    for ci, col in enumerate(tbl.columns):
        col.width = widths[ci]
    for row in tbl.rows:
        for ci, cell in enumerate(row.cells):
            cell.width = widths[ci]

    # Gaming forms give Players, hackathon forms Members — the parser reports
    # which, so the heading matches the P1/M1 labels underneath it.
    role = next((r.get('role') for r in rows if r.get('role')), 'Player')
    for ci, h in enumerate(['S.No', 'Team Name', role, 'Full Name', 'Registration Number']):
        cell = tbl.rows[0].cells[ci]
        cell.text = ''
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(p, h, bold=True, size=11)

    ri = 1
    for gi, (team, members) in enumerate(groups, start=1):
        for mi, person in enumerate(members):
            row = tbl.rows[ri]
            for c in row.cells:
                c.text = ''
            first = mi == 0
            # The merged cells only carry text in the row that starts the span;
            # the continuation cells stay empty (Word renders one tall cell).
            if first:
                _set_vmerge(row.cells[0], restart=True)
                _set_vmerge(row.cells[1], restart=True)
                _cell_text(row.cells[0], str(gi), align=WD_ALIGN_PARAGRAPH.CENTER)
                _cell_text(row.cells[1], team, align=WD_ALIGN_PARAGRAPH.CENTER)
            else:
                _set_vmerge(row.cells[0])
                _set_vmerge(row.cells[1])
            # Centre the team block against its members instead of letting it
            # ride at the top of a tall span.
            row.cells[0].vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            row.cells[1].vertical_alignment = WD_ALIGN_VERTICAL.CENTER

            _cell_text(row.cells[2], person.get('slot', f"P{mi + 1}"),
                       align=WD_ALIGN_PARAGRAPH.CENTER)
            _cell_text(row.cells[3], person.get('name', ''), align=WD_ALIGN_PARAGRAPH.CENTER)
            _cell_text(row.cells[4], person.get('reg_no', ''), align=WD_ALIGN_PARAGRAPH.CENTER)
            ri += 1

    _keep_table_together(tbl)
    _repeat_header_row(tbl)
    _keep_groups_together(tbl, [len(m) for _, m in groups])


def _people_total(label, rows):
    """'Total registered: 34 teams · 136 participants' — the headcount line above
    a person table. Counting rows alone would report 34 under a 136-row table."""
    teams = len(_team_groups(rows)) if any(r.get('team') for r in rows) else 0
    if teams:
        return f"{label}: {teams} teams · {len(rows)} participants"
    return f"{label}: {len(rows)}"


def _person_section(doc, number, title, label, rows):
    """Emit a person-list section — heading, headcount, table.

    A long list is written at body level instead of inside a _section_block:
    Word only repeats a header row for a *top-level* table, so a participant
    list nested in the wrapper table would carry column labels on its first
    page and none of the five after it. Short lists keep the wrapper, which
    stops a small section being split from its heading."""
    if len(rows) + 1 > _LONG_TABLE_ROWS:
        # A long list gets its own page: it's the biggest block in the report, and
        # starting it mid-page left a tall blank gap above it in Word.
        _section_heading(doc, number, title).paragraph_format.page_break_before = True
        # The headcount line and the blank under it must ride with the table's
        # header row, or the heading strands at the foot of a page while the
        # table starts on the next one.
        _set_keep_with_next(_para(doc, _people_total(label, rows), size=12))
        _set_keep_with_next(doc.add_paragraph())
        _add_person_table(doc, rows)
        spacer = doc.add_paragraph()
        spacer.paragraph_format.space_after = Pt(SECTION_SPACER_PT)
        return
    c = _section_block(doc)
    _section_heading(c, number, title)
    _para(c, _people_total(label, rows), size=12)
    c.add_paragraph()
    _add_person_table(c, rows)


def _repeat_header_row(table):
    """Repeat row 0 on every page the table spans. A 137-row participant list
    runs to six pages, and without this only the first carries column labels."""
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))


def _keep_groups_together(table, group_sizes, first_row=1):
    """Glue each team's rows so a team is never split by a page break.

    The merged S.No/Team cell only prints on the page where its span starts, so
    a team straddling a break leaves rows at the top of the next page with no
    team name against them. Holding each group together avoids that entirely.
    It has to reach every <w:tc> in the row, including a vertical merge's empty
    continuation cells — row.cells hands back the anchor cell for those, so
    going through it would leave their own paragraphs unmarked and Word would
    break the row anyway."""
    ri = first_row
    for size in group_sizes:
        for k in range(size - 1):           # every row but the group's last
            if ri + k >= len(table.rows):
                break
            for tc in table.rows[ri + k]._tr.findall(qn('w:tc')):
                for p_el in tc.findall(qn('w:p')):
                    # get_or_add_keepNext keeps w:pPr's required child order.
                    p_el.get_or_add_pPr().get_or_add_keepNext()
        ri += size


def _add_person_table(doc, rows):
    """Render rows of {'name', 'reg_no'} as a 3-column S.No / Name / Reg No table.
    Rows carrying a 'team' get the wider team-grouped layout instead."""
    if not rows:
        return
    if any(r.get('team') for r in rows):
        _add_team_person_table(doc, rows)
        return
    tbl = doc.add_table(rows=len(rows) + 1, cols=3)
    tbl.style = 'Table Grid'
    tbl.autofit = False
    widths = [Inches(0.6), Inches(3.4), Inches(2.5)]
    for row in tbl.rows:
        for ci, cell in enumerate(row.cells):
            cell.width = widths[ci]
    for ci, h in enumerate(['S.No', 'Name', 'Registration Number']):
        cell = tbl.rows[0].cells[ci]
        cell.text = ''
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(p, h, bold=True, size=11)
    for j, person in enumerate(rows):
        row = tbl.rows[j + 1]
        for c in row.cells:
            c.text = ''
        _cell_text(row.cells[0], str(j + 1), align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell_text(row.cells[1], person.get('name', ''), align=WD_ALIGN_PARAGRAPH.LEFT)
        _cell_text(row.cells[2], person.get('reg_no', ''), align=WD_ALIGN_PARAGRAPH.CENTER)
    _keep_table_together(tbl)
    _repeat_header_row(tbl)


# ── Template-cell editing helpers ────────────────────────────────────────────
def _walk_cell_paragraphs(cell):
    """Yield every paragraph inside the cell (including nested tables)."""
    for p in cell.paragraphs:
        yield p


def _insert_logo_into_cover(main_cell):
    """Insert the DAG logo into an empty paragraph between the club name and the
    'Event report' text. Preserves P0 (which holds the VIT-AP VML logo)."""
    logo_path = _resolve_logo_path()
    if logo_path is None:
        logger.info("No club logo found in templates/ — skipping logo insertion.")
        return
    paragraphs = list(main_cell.paragraphs)
    # Find the 'club / chapter name' paragraph; the DAG logo goes in the first
    # empty paragraph after it (mirrors the sample layout: VIT-AP → club name → DAG logo → Event report).
    club_idx = None
    for i, p in enumerate(paragraphs):
        if p.text.strip().lower().startswith('(club/'):
            club_idx = i
            break
    if club_idx is None:
        logger.warning("Couldn't locate club-name paragraph; skipping DAG logo.")
        return
    target = None
    for p in paragraphs[club_idx + 1:]:
        if not p.text.strip() and 'imagedata' not in p._p.xml and 'blip' not in p._p.xml:
            target = p
            break
    if target is None:
        logger.warning("No empty paragraph after club name; skipping DAG logo.")
        return
    target.alignment = WD_ALIGN_PARAGRAPH.CENTER
    try:
        target.add_run().add_picture(logo_path, width=Inches(LOGO_WIDTH_IN))
    except Exception as e:
        logger.warning(f"logo insert failed: {e}")


def _insert_page_break_before_paragraph(paragraph):
    """Insert an explicit <w:br type='page'/> run at the start of this paragraph.
    Works inside table cells, where the pageBreakBefore property is silently ignored."""
    new_r = OxmlElement('w:r')
    new_br = OxmlElement('w:br')
    new_br.set(qn('w:type'), 'page')
    new_r.append(new_br)
    pPr = paragraph._p.find(qn('w:pPr'))
    if pPr is not None:
        pPr.addnext(new_r)
    else:
        paragraph._p.insert(0, new_r)


def _fill_template_placeholders(doc, data):
    """Replace cover + REPORT page placeholders inside the template's main cell."""
    mode = data.get('mode', 'Offline')
    venue_display = data.get('venue', '') if mode == 'Offline' else 'Online'

    # Most templates put all the content in tables[0].rows[0].cells[0]
    if not doc.tables:
        return
    main_cell = doc.tables[0].rows[0].cells[0]

    _insert_logo_into_cover(main_cell)

    # Map "starts-with" matches to the new full text. Order matters — first match wins
    # per paragraph. Use distinctive prefixes so we don't accidentally overwrite
    # the 11-item checklist (which we delete separately).
    replacements = [
        ('(club/ chapter name)', data.get('club', '')),
        ('(2025-2026)', f"({_academic_year()})"),
        ('coordinator name', data.get('coordinator', '')),
        ('event name :', f"Event Name :  {data.get('event_name', '')}"),
        ('mode of conduct', f"Mode of Conduct  :  {mode}"),
        ('venue:', f"Venue:  {venue_display}"),
        ('date :', f"Date :  {data.get('date', '')}"),
    ]

    report_header_paragraph = None
    for paragraph in list(main_cell.paragraphs):
        ptext = paragraph.text.strip()
        ptext_lc = ptext.lower()
        if report_header_paragraph is None and ptext == 'REPORT':
            report_header_paragraph = paragraph
        for prefix, new_text in replacements:
            if ptext_lc.startswith(prefix):
                _set_para_text(paragraph, new_text)
                break

    # Push the REPORT details block onto a fresh page so the cover stands alone.
    if report_header_paragraph is not None:
        _insert_page_break_before_paragraph(report_header_paragraph)


def _delete_checklist(doc):
    """Strip trailing template content from the cover/REPORT cell + extra empty
    rows of the outer table — otherwise the cover table spans into blank pages
    after the REPORT details.

    The cell is trimmed from the 'Approval mail screenshot:' placeholder onwards
    (we re-add those in the body) and any trailing empty paragraphs are removed.
    Whitespace is collapsed when matching since the template has odd double-spaces.
    """
    import re
    if not doc.tables:
        return
    tbl = doc.tables[0]
    main_cell = tbl.rows[0].cells[0]
    to_remove = []
    triggered = False
    for paragraph in main_cell.paragraphs:
        t = re.sub(r'\s+', ' ', paragraph.text.strip().lower())
        if not triggered and (
            t.startswith('approval mail screenshot')
            or t.startswith('add these in your report')
        ):
            triggered = True
        if triggered:
            to_remove.append(paragraph)
    for p in to_remove:
        p._element.getparent().remove(p._element)

    # Also strip trailing empty paragraphs (Word/LO leave one final paragraph
    # marker, so keep at least one).
    paragraphs = list(main_cell.paragraphs)
    while len(paragraphs) > 1 and not paragraphs[-1].text.strip() \
            and 'blip' not in paragraphs[-1]._p.xml \
            and 'imagedata' not in paragraphs[-1]._p.xml:
        last = paragraphs.pop()
        last._element.getparent().remove(last._element)

    # Drop the template's empty trailing rows (rows 1 and 2 are blank placeholders).
    for extra_row in list(tbl.rows)[1:]:
        tbl._tbl.remove(extra_row._tr)

    # Strip the row's fixed height — the template forces ~9.86" so the cover
    # row spans a whole page, leaving blank space after our trimmed content.
    for row in tbl.rows:
        trPr = row._tr.find(qn('w:trPr'))
        if trPr is None:
            continue
        for h in trPr.findall(qn('w:trHeight')):
            trPr.remove(h)


# ────────────────────────────────────────────────────────────────────────────
# Generator
# ────────────────────────────────────────────────────────────────────────────
def generate_report(data: dict, output_dir: str | None = None):
    event_name = safe_filename(data.get('event_name', 'Event'))
    out_dir = output_dir or TEMP_DIR
    os.makedirs(out_dir, exist_ok=True)
    docx_path = os.path.join(out_dir, f"Report_{event_name}.docx")
    shutil.copy(TEMPLATE_PATH, docx_path)

    doc = Document(docx_path)

    mode = data.get('mode', 'Offline')
    has_budget = bool(data.get('has_budget', False))
    photo_section_title = "Geotagged photographs" if mode == "Offline" else "Event Screenshots"

    # ── 1. Fill cover + REPORT-page placeholders ───────────────────────────
    _fill_template_placeholders(doc, data)

    # ── 2. Strip instructions checklist ────────────────────────────────────
    _delete_checklist(doc)

    # Apply page borders and strip the cover-table border so every page —
    # cover/REPORT included — has the same single black frame.
    _apply_page_borders(doc)
    _remove_outer_table_border(doc)

    # Each section goes into its own invisible cantSplit sub-table (a "block").
    # Word/LO will keep the block on one page if it fits, otherwise move the
    # whole block to the next page — never splitting heading from content.
    # Multiple small blocks naturally share a page.

    # ── Approval mail screenshot ───────────────────────────────────────────
    c = _section_block(doc)
    _section_heading(c, '', 'Approval mail screenshot:')
    _insert_full_page(c, _expand_uploads(data.get('approval_images', [])))

    # The Signed Budget Form is no longer rendered here — it is appended to the
    # end of the PDF as its own pages after conversion (see the Save section).

    # ── Signed Event Proposal (only if has_budget) ─────────────────────────
    if has_budget and data.get('signed_proposal_files'):
        c = _section_block(doc)
        _section_heading(c, '', 'Signed Event Proposal')
        _insert_full_page(c, _expand_uploads(data['signed_proposal_files']))

    # ── 1. Brochure / poster ───────────────────────────────────────────────
    c = _section_block(doc)
    _section_heading(c, 1, 'Brochure / poster')
    if data.get('brochure'):
        _insert_full_page(c, _expand_uploads([data['brochure']]))

    # ── 2. Detailed schedule ───────────────────────────────────────────────
    schedule = data.get('schedule', [])
    if schedule:
        c = _section_block(doc)
        _section_heading(c, 2, 'Detailed schedule')
        st = c.add_table(rows=len(schedule) + 1, cols=2)
        st.style = 'Table Grid'
        st.autofit = False
        for row in st.rows:
            row.cells[0].width = Inches(2.5)
            row.cells[1].width = Inches(4.0)
        for ci, h in enumerate(['Time', 'Activity']):
            cell = st.rows[0].cells[ci]
            cell.text = ''
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _run(p, h, bold=True, size=11)
        for j, entry in enumerate(schedule):
            row = st.rows[j + 1]
            _cell_text(row.cells[0], entry.get('time', ''), size=11)
            _cell_text(row.cells[1], entry.get('activity', ''), size=11)
        _keep_table_together(st)

    # ── 3. Registered participant list ─────────────────────────────────────
    participants = data.get('participants', [])
    if participants:
        _person_section(doc, 3, 'Registered participant list',
                        'Total registered', participants)

    # ── 4. Attendance list (Excel → table) ─────────────────────────────────
    attendees = data.get('attendees', [])
    if attendees:
        _person_section(doc, 4, 'Attendance list', 'Total attendees', attendees)

    # ── 5. Brief description of the event ──────────────────────────────────
    # Prose the user may later edit in Word must NOT sit inside a cantSplit
    # block: the moment the text outgrows the page, Word moves the whole
    # block to the next page, leaving an empty gap backspace can't remove.
    # Plain body paragraphs split across pages naturally; the headings keep
    # with their first text line via keep_with_next.
    _section_heading(doc, 5, 'Brief description of the event')
    p = _para(doc, 'Purpose of the Event:', bold=True, size=12)
    _set_keep_with_next(p)
    _para(doc, data.get('purpose', ''), align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    doc.add_paragraph()
    p = _para(doc, 'Brief Description:', bold=True, size=12)
    _set_keep_with_next(p)
    _para(doc, data.get('description', ''), align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(SECTION_SPACER_PT)

    # ── 6. Sample Certificate copies (optional) ────────────────────────────
    certs = _expand_uploads(data.get('certificates', []))
    if certs:
        c = _section_block(doc)
        _section_heading(c, 6, 'Sample Certificate copies')
        _insert_two_per_page(c, certs)

    # ── 7. Geotagged photographs / Event Screenshots ───────────────────────
    photos = _expand_uploads(data.get('photos', []))
    if photos:
        c = _section_block(doc)
        _section_heading(c, 7, photo_section_title)
        _insert_two_per_page(c, photos)

    # ── 8. Winners list ────────────────────────────────────────────────────
    winners = data.get('winners', [])
    if winners:
        c = _section_block(doc)
        _section_heading(c, 8, 'Winners list')
        _add_person_table(c, winners)

    # ── 9. Guest Speaker's profile with Photo (conditional) ────────────────
    speaker_files = _expand_uploads(data.get('speaker_files', []))
    speaker_bio = data.get('speaker_bio', '')
    if speaker_files or speaker_bio:
        # Bio is editable prose → body paragraphs (see section 5); only the
        # photos stay in a cantSplit block.
        _section_heading(doc, 9, "Guest Speaker's profile with Photo")
        if speaker_bio:
            _para(doc, speaker_bio, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
            spacer = doc.add_paragraph()
            spacer.paragraph_format.space_after = Pt(SECTION_SPACER_PT)
        if speaker_files:
            c = _section_block(doc)
            _insert_two_per_page(c, speaker_files)

    # ── 10. Feedback forms sample (link, not pic) ──────────────────────────
    feedback_link = (data.get('feedback_link', '') or '').strip()
    if feedback_link:
        c = _section_block(doc)
        _section_heading(c, 10, 'Feedback forms sample')
        _para(c, feedback_link, size=12)

    # ── 11. Expenditure Statement (only if has_budget) ─────────────────────
    expenditure_files = _expand_uploads(data.get('expenditure_files', []))
    if has_budget and expenditure_files:
        c = _section_block(doc)
        _section_heading(c, 11, 'Expenditure Statement')
        _insert_full_page(c, expenditure_files)

    # ── Signed Budget Form — the last pages, unframed ───────────────────────
    # Attached whole at the end rather than sitting in a bordered section near
    # the front: one page per sheet, no report frame around it. Word can't embed
    # PDF pages, so a PDF upload is rendered a page at a time like every other
    # scan in the report — this way the DOCX and the PDF hold the same thing.
    if has_budget and data.get('signed_budget_files'):
        budget_pages = _expand_uploads(data['signed_budget_files'])
        if budget_pages:
            _append_borderless_section(doc)
            _insert_full_page(doc, budget_pages)

    # No signature block — the institute sample has one inside the expenditure
    # section, but DAG club doesn't use it here.

    # ── Save ────────────────────────────────────────────────────────────────
    stamp_docx_author(doc)
    disable_soft_return_justify(doc)
    modernize_docx(doc)
    doc.save(docx_path)
    pdf_path = docx_to_pdf(docx_path)
    return docx_path, pdf_path
