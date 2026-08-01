"""Event Proposal generator.

Opens the original template (templates/Event Proposal - Wipe_Out_Arena_unsigned.docx),
replaces the sample values in place, and saves a copy. The output is byte-for-byte
the same layout, fonts and table styling as the institute template — no rebuild.
"""
import os
import shutil
from copy import deepcopy

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph

from generators.file_utils import (
    docx_to_pdf, stamp_docx_author, disable_soft_return_justify, modernize_docx,
    safe_filename,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(BASE_DIR, 'templates',
                             'Event Proposal - Wipe_Out_Arena_unsigned.docx')
TEMP_DIR = os.path.join(BASE_DIR, 'temp')
os.makedirs(TEMP_DIR, exist_ok=True)


# ── Run/paragraph helpers (preserve formatting) ─────────────────────────────
def _fill_first_run(paragraph, text):
    if paragraph.runs:
        paragraph.runs[0].text = text
        for r in paragraph.runs[1:]:
            r.text = ''
    else:
        paragraph.add_run(text)


def _set_para_text(paragraph, text):
    """Replace paragraph text, keeping the first run's font/size/bold.

    Newlines become real paragraphs, not <w:br/> soft breaks — soft breaks
    freeze the line wrap, so editing the text later leaves gaps that can't be
    deleted without removing whole words.

    Returns the list of paragraphs that now hold the text."""
    lines = str(text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    _fill_first_run(paragraph, lines[0])
    written = [paragraph]
    anchor = paragraph._p
    for line in lines[1:]:
        new_p = deepcopy(paragraph._p)
        anchor.addnext(new_p)
        anchor = new_p
        new_para = Paragraph(new_p, paragraph._parent)
        _fill_first_run(new_para, line)
        written.append(new_para)
    return written


def _set_body_text(paragraph, text):
    """Insert a user-written block (purpose/description): blank lines are
    dropped to save page-1 space (default paragraph spacing already separates
    them visually) and every paragraph is justified."""
    lines = [ln.strip() for ln in str(text or '').replace('\r', '').split('\n')]
    lines = [ln for ln in lines if ln] or ['']
    written = _set_para_text(paragraph, '\n'.join(lines))
    for p in written:
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    return written


def _set_cell(cell, text):
    """Set cell text, preserving first paragraph's first-run formatting."""
    paras = cell.paragraphs
    if paras:
        _set_para_text(paras[0], text)
        for extra in list(paras[1:]):
            extra._element.getparent().remove(extra._element)
    else:
        cell.text = text


def _find_para_idx(paragraphs, prefix):
    for i, p in enumerate(paragraphs):
        if p.text.strip().lower().startswith(prefix.lower()):
            return i
    return -1


def _keep_paragraph_with_next(paragraph):
    pPr = paragraph._p.get_or_add_pPr()
    pPr.append(OxmlElement('w:keepNext'))
    pPr.append(OxmlElement('w:keepLines'))


def _force_page_break_before(paragraph):
    """Make this paragraph start on a fresh page. Reliable in document body
    (not nested table cells, which LibreOffice drops)."""
    pPr = paragraph._p.get_or_add_pPr()
    pPr.append(OxmlElement('w:pageBreakBefore'))


def _keep_table_together(table):
    """Stop Word from breaking the table across pages — whole block stays on one page."""
    rows = list(table.rows)
    last_idx = len(rows) - 1
    for ri, row in enumerate(rows):
        trPr = row._tr.get_or_add_trPr()
        trPr.append(OxmlElement('w:cantSplit'))
        for cell in row.cells:
            for p in cell.paragraphs:
                pPr = p._p.get_or_add_pPr()
                if ri < last_idx:
                    pPr.append(OxmlElement('w:keepNext'))
                pPr.append(OxmlElement('w:keepLines'))


# ── Generator ───────────────────────────────────────────────────────────────
def generate_proposal(data: dict, output_dir: str | None = None):
    event_name = safe_filename(data.get('event_name', 'Event'))
    out_dir = output_dir or TEMP_DIR
    os.makedirs(out_dir, exist_ok=True)
    docx_path = os.path.join(out_dir, f"Proposal_{event_name}.docx")
    shutil.copy(TEMPLATE_PATH, docx_path)

    doc = Document(docx_path)

    # ── Info table (table 0) — column 1 holds the values ───────────────────
    total_budget_raw = data.get('total_budget', '') or 'Nil'
    try:
        int(str(total_budget_raw).replace(',', '').strip())
        total_budget_str = f"Rs. {total_budget_raw}/-"
    except (ValueError, TypeError):
        total_budget_str = str(total_budget_raw)

    coord_name = data.get('coordinator', '')
    coord_phone = data.get('coordinator_phone', '')
    coord_line = f"{coord_name}, {coord_phone}" if coord_phone else coord_name

    info_values = [
        data.get('club', ''),
        coord_line,
        data.get('event_name', ''),
        data.get('venue', ''),
        data.get('date', ''),
        data.get('duration', ''),
        total_budget_str,
        data.get('extra', '') or 'NA',
    ]
    info = doc.tables[0]
    for i, v in enumerate(info_values):
        if i < len(info.rows):
            _set_cell(info.rows[i].cells[1], v)

    # ── Body paragraphs ─────────────────────────────────────────────────────
    from docx.oxml.ns import qn

    def _delete_following(last_written, stop_prefixes, empty_only=False):
        """Remove sibling paragraphs after the freshly written block: leftover
        sample text and blank spacers. Blanking them (the old approach) left
        empty paragraphs that spilled onto a blank page when the user's text
        ran long."""
        el = last_written._p.getnext()
        while el is not None and el.tag == qn('w:p'):
            t = Paragraph(el, last_written._parent).text.strip().lower()
            if any(t.startswith(s) for s in stop_prefixes) or (empty_only and t):
                break
            nxt = el.getnext()
            el.getparent().remove(el)
            el = nxt

    paras = doc.paragraphs
    pp_i = _find_para_idx(paras, 'Purpose of the Event and Brief Description')
    if pp_i >= 0 and pp_i + 1 < len(paras):
        written = _set_body_text(paras[pp_i + 1], data.get('purpose', ''))
        _delete_following(written[-1], ('brief description',), empty_only=True)

    paras = doc.paragraphs  # refresh — purpose may have inserted paragraphs
    bd_i = _find_para_idx(paras, 'Brief Description')
    if bd_i >= 0 and bd_i + 1 < len(paras):
        written = _set_body_text(paras[bd_i + 1], data.get('description', ''))
        _delete_following(written[-1], ('budget breakdown', 'digital signature'))

    # ── Budget table (table 1) ──────────────────────────────────────────────
    # Template structure:
    #   row 0: merged header "Description costing of the expenditure"
    #   row 1: column headers (Item description / Rate / Total)
    #   row 2..N-2: item rows (template ships with placeholders)
    #   row N-2: "Total cost" row
    #   row N-1: merged "Financial calculation..." row
    budget = doc.tables[1]
    items = data.get('budget_items', [])

    total_row_idx = -1
    for ri, row in enumerate(budget.rows):
        if row.cells[0].text.strip().lower().startswith('total cost'):
            total_row_idx = ri
            break

    item_start = 2
    if total_row_idx < 0:
        total_row_idx = item_start + 1
    existing_item_rows = total_row_idx - item_start

    if existing_item_rows < 1:
        existing_item_rows = 1

    # Use the first item row as the clone template before mutating it
    template_tr = deepcopy(budget.rows[item_start]._tr)

    n_items = max(len(items), 1)

    # Add rows if needed
    while existing_item_rows < n_items:
        new_tr = deepcopy(template_tr)
        budget.rows[total_row_idx]._tr.addprevious(new_tr)
        existing_item_rows += 1
        total_row_idx += 1

    # Remove extra rows if fewer items
    while existing_item_rows > n_items:
        budget.rows[total_row_idx - 1]._tr.getparent().remove(
            budget.rows[total_row_idx - 1]._tr)
        existing_item_rows -= 1
        total_row_idx -= 1

    grand_total = 0
    for i in range(n_items):
        row = budget.rows[item_start + i]
        if i < len(items):
            item = items[i]
            qty = item.get('qty', '')
            rate = item.get('rate', '')
            line_total = item.get('total', 0) or 0
            try:
                grand_total += int(line_total)
            except (ValueError, TypeError):
                pass
            _set_cell(row.cells[0], str(item.get('item', '')))
            _set_cell(row.cells[1], f"{qty} x {rate}")
            _set_cell(row.cells[2], f"Rs. {line_total}/-")
        else:
            _set_cell(row.cells[0], '')
            _set_cell(row.cells[1], '')
            _set_cell(row.cells[2], '')

    # Total cost row
    total_row = budget.rows[total_row_idx]
    _set_cell(total_row.cells[0], 'Total cost')
    _set_cell(total_row.cells[1], '')
    _set_cell(total_row.cells[2], f"Rs. {grand_total}/-" if grand_total > 0 else 'Nil')

    # Force "Budget Breakdown:" onto a fresh page so the heading + table never
    # split across pages (keepNext alone isn't reliable with a following table).
    bb_i = _find_para_idx(doc.paragraphs, 'Budget Breakdown')
    if bb_i >= 0:
        _force_page_break_before(doc.paragraphs[bb_i])
        _keep_paragraph_with_next(doc.paragraphs[bb_i])
    _keep_table_together(budget)

    # Save and convert
    stamp_docx_author(doc)
    disable_soft_return_justify(doc)
    modernize_docx(doc)
    doc.save(docx_path)
    pdf_path = docx_to_pdf(docx_path)
    return docx_path, pdf_path
