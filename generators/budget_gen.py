"""Budget Form generator — clones the official DAG club XLSX template
and fills it with the user's data. Also produces a PDF via Excel COM.

Reference template: templates/Final_Budget_Form.xlsx
"""
import math
import os
import shutil
import openpyxl
from copy import copy
from openpyxl.styles import Font, Alignment

from generators.file_utils import xlsx_to_pdf, xlsx_to_docx, stamp_xlsx_author, safe_filename

TEMP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'temp')
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        'templates', 'Final_Budget_Form.xlsx')
os.makedirs(TEMP_DIR, exist_ok=True)

# Cell map matching the template structure
CELL_CLUB        = 'C4'
CELL_COORDINATOR = 'C5'
CELL_EMP_ID      = 'C6'
CELL_EVENT_NAME  = 'B7'
CELL_EVENT_DATE  = 'I7'
CELL_EMP_ID_SIG  = 'B32'  # under signature block
CELL_COORD_SIG   = 'A31'  # signature-block column header — append prof name here

REV_FIRST_ROW = 10
REV_LAST_ROW  = 14  # 5 rows for revenue items
EXP_FIRST_ROW = 19
EXP_LAST_ROW  = 25  # 7 rows for expenditure items
SURPLUS_LABEL_CELL = 'E28'  # was "Deficit" default


def _write_cell(ws, coord: str, value, bold: bool = False, align: str | None = None):
    """Write a value into a cell. If cell is part of a merge, write to the
    top-left anchor (openpyxl writes are silently ignored on non-anchor cells)."""
    cell = ws[coord]
    # Walk merged ranges to find anchor
    for merged_range in ws.merged_cells.ranges:
        if coord in merged_range:
            cell = ws.cell(row=merged_range.min_row, column=merged_range.min_col)
            break
    cell.value = value
    if bold or align:
        existing = cell.font
        if bold:
            cell.font = Font(name=existing.name or 'Times New Roman',
                             size=existing.size or 11,
                             bold=True)
        if align:
            cell.alignment = Alignment(horizontal=align, vertical='center', wrap_text=True)


def _ensure_merge(ws, range_str: str):
    """Merge `range_str` if it isn't already merged. No-op if it is."""
    target = openpyxl.utils.cell.range_boundaries(range_str)
    for r in list(ws.merged_cells.ranges):
        if (r.min_col, r.min_row, r.max_col, r.max_row) == target:
            return
    ws.merge_cells(range_str)


def generate_budget(data: dict, output_dir: str | None = None):
    if not os.path.exists(TEMPLATE):
        raise FileNotFoundError(f"Budget template missing: {TEMPLATE}")

    wb = openpyxl.load_workbook(TEMPLATE)
    ws = wb.active

    # ── Widen narrow cells so long values (Dr.+name+phone) aren't truncated ─
    _ensure_merge(ws, 'C5:I5')   # Coordinator name+phone
    _ensure_merge(ws, 'C6:I6')   # Employee ID
    # "Event Date:" is ~52pt but column G is only ~48pt, so the label spilled
    # into the empty H7 and crossed the gridline drawn between them. Merging
    # G:H gives it room and removes the line it was crossing.
    _ensure_merge(ws, 'G7:H7')
    # Surplus row: extend H → I and enable wrap
    for r in list(ws.merged_cells.ranges):
        if (r.min_col, r.min_row, r.max_col, r.max_row) == (5, 28, 8, 28):
            ws.unmerge_cells(str(r))
            break
    _ensure_merge(ws, 'E28:I28')
    # Signature block: keep A31 ("Event Co-ordinator" label) as its own cell,
    # write prof name in B31 (adjacent), just like the rest of the template uses
    # A=label / B=value (A32 "Emp ID" + B32 value, etc.).
    for r in list(ws.merged_cells.ranges):
        if (r.min_col, r.min_row, r.max_col, r.max_row) == (1, 31, 2, 31):
            ws.unmerge_cells(str(r))
            break

    # ── Header info ─────────────────────────────────────────────────────────
    _write_cell(ws, CELL_CLUB,        data.get('club', ''))
    # C5's label is "Name and phone number of the Coordinator" — the phone is a
    # separate config value, so join it back on here.
    coordinator = (data.get('coordinator', '') or '').strip()
    phone = (data.get('coordinator_phone', '') or '').strip()
    if phone and phone not in coordinator:
        coordinator = f"{coordinator}, {phone}" if coordinator else phone
    _write_cell(ws, CELL_COORDINATOR, coordinator, align='left')
    ws['C5'].alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
    _write_cell(ws, CELL_EMP_ID,      data.get('emp_id', ''))
    _write_cell(ws, CELL_EVENT_NAME,  data.get('event_name', ''))
    _write_cell(ws, CELL_EVENT_DATE,  data.get('date', ''))

    # Long event names clip: the B7:E7 merge is ~45 chars wide and Excel never
    # auto-fits merged cells, so wrap + give the row an explicit height.
    # Column I has no neighbour to spill into — its right edge is the table's
    # outer border — so a multi-day date ("25-07-2026 to 27-07-2026") has to
    # wrap rather than hang outside the form.
    EVENT_NAME_CHARS = 45
    DATE_CHARS = 10
    ws['B7'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws['I7'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    name_lines = math.ceil(max(len(str(data.get('event_name', ''))), 1) / EVENT_NAME_CHARS)
    date_lines = math.ceil(max(len(str(data.get('date', ''))), 1) / DATE_CHARS)
    row7_lines = max(name_lines, date_lines)
    if row7_lines > 1:
        ws.row_dimensions[7].height = 16 * row7_lines

    # Excel doesn't reliably auto-fit wrapped rows at export either, so item
    # rows with long particulars also get explicit heights (see loops below).
    # The form is a one-pager by design — fit-to-page keeps it that way even
    # when wrapped rows make it taller (honored by Excel COM and LibreOffice).
    PARTICULARS_CHARS = 20  # column B is ~22 chars wide; leave slack for word wrap
    def _fit_particulars_row(row, text):
        lines = math.ceil(max(len(str(text)), 1) / PARTICULARS_CHARS)
        if lines > 1:
            ws.row_dimensions[row].height = 15 * lines

    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1

    # ── Revenue items (rows 10–14) ──────────────────────────────────────────
    rev_items = data.get('revenue', [])[:5]  # cap to template rows
    for i in range(REV_FIRST_ROW, REV_LAST_ROW + 1):
        idx = i - REV_FIRST_ROW
        if idx < len(rev_items):
            it = rev_items[idx]
            ws.cell(row=i, column=1).value  = idx + 1                    # A: Sl
            b_cell = ws.cell(row=i, column=2)                            # B
            b_cell.value = it.get('particulars', '')
            b_cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
            _fit_particulars_row(i, b_cell.value)
            ws.cell(row=i, column=3).value  = it.get('b_qty', '')        # C
            ws.cell(row=i, column=4).value  = it.get('b_rate', '')       # D
            ws.cell(row=i, column=5).value  = it.get('b_amount', '')     # E (will be overwritten if formula)
            # Actual side
            ws.cell(row=i, column=7).value  = it.get('a_qty', '')        # G
            ws.cell(row=i, column=8).value  = it.get('a_rate', '')       # H
            ws.cell(row=i, column=9).value  = it.get('a_amount', '')     # I
        else:
            # Blank out unused rows so SUM works correctly
            for col in (1, 2, 3, 4, 5, 7, 8, 9):
                ws.cell(row=i, column=col).value = None

    # ── Expenditure items (rows 19–25) ─────────────────────────────────────
    exp_items = data.get('expenditure', [])[:7]
    for i in range(EXP_FIRST_ROW, EXP_LAST_ROW + 1):
        idx = i - EXP_FIRST_ROW
        if idx < len(exp_items):
            it = exp_items[idx]
            # Template pre-fills Sl only for rows 19-24 — write it explicitly
            # so the 7th row isn't left unnumbered.
            ws.cell(row=i, column=1).value = idx + 1
            b_cell = ws.cell(row=i, column=2)
            b_cell.value = it.get('particulars', '')
            b_cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
            _fit_particulars_row(i, b_cell.value)
            ws.cell(row=i, column=3).value = it.get('b_qty', '')
            ws.cell(row=i, column=4).value = it.get('b_rate', '')
            ws.cell(row=i, column=5).value = it.get('b_amount', '')
            ws.cell(row=i, column=7).value = it.get('a_qty', '')
            ws.cell(row=i, column=8).value = it.get('a_rate', '')
            ws.cell(row=i, column=9).value = it.get('a_amount', '')
        else:
            for col in (2, 3, 4, 5, 7, 8, 9):
                ws.cell(row=i, column=col).value = None

    # ── Surplus / Deficit label (template defaults to "Deficit") ────────────
    rev_b_total = sum(int(it.get('b_amount', 0) or 0) for it in rev_items)
    exp_b_total = sum(int(it.get('b_amount', 0) or 0) for it in exp_items)
    surplus_b = rev_b_total - exp_b_total
    rev_a_total = sum(int(it.get('a_amount', 0) or 0) for it in rev_items)
    exp_a_total = sum(int(it.get('a_amount', 0) or 0) for it in exp_items)
    surplus_a = rev_a_total - exp_a_total

    label = "Surplus" if surplus_b >= 0 else "Deficit"
    detail = f"{label}: Budget Rs. {abs(surplus_b)} | Actual Rs. {abs(surplus_a)}"
    _write_cell(ws, SURPLUS_LABEL_CELL, detail, bold=True, align='center')

    # ── Signature block: label "Event Co-ordinator" stays in A31, prof name
    #     goes in B31 (adjacent cell), and Emp ID value in B32 next to its label.
    _write_cell(ws, CELL_COORD_SIG, 'Event Co-ordinator')
    coord_name = (data.get('coordinator', '') or '').split(',')[0].strip()
    if coord_name:
        _write_cell(ws, 'B31', coord_name)
        ws['B31'].alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
    _write_cell(ws, CELL_EMP_ID_SIG, data.get('emp_id', ''))

    # ── Save ───────────────────────────────────────────────────────────────
    event_name = safe_filename(data.get('event_name', 'Event'))
    out_dir = output_dir or TEMP_DIR
    os.makedirs(out_dir, exist_ok=True)
    xlsx_path = os.path.join(out_dir, f"Budget_{event_name}.xlsx")
    stamp_xlsx_author(wb)
    wb.save(xlsx_path)

    docx_path = xlsx_to_docx(xlsx_path)
    pdf_path = xlsx_to_pdf(xlsx_path)
    return xlsx_path, docx_path, pdf_path
