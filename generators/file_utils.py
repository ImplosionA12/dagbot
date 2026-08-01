"""Shared file conversion helpers.

PDF generation strategy (in order):
  1. docx2pdf (Windows + MS Word installed) — best fidelity
  2. soffice (LibreOffice headless) — fallback
  3. None — DOCX-only delivery, caller decides what to do

PDF → image rendering: PyMuPDF (no external binaries).
"""
import os
import re
import shutil
import subprocess
import logging

logger = logging.getLogger(__name__)


def stamp_docx_author(doc):
    """Overwrite the template's original creator with our configured author.
    Templates are copied byte-for-byte, so without this the generated file
    keeps whoever authored the original sample document."""
    from datetime import datetime, timezone
    from config import DOC_AUTHOR
    cp = doc.core_properties
    cp.author = DOC_AUTHOR
    cp.last_modified_by = DOC_AUTHOR
    cp.revision = 1
    now = datetime.now(timezone.utc)
    cp.created = now
    cp.modified = now


def stamp_xlsx_author(wb):
    """openpyxl equivalent of stamp_docx_author for the budget workbook."""
    from datetime import datetime, timezone
    from config import DOC_AUTHOR
    props = wb.properties
    props.creator = DOC_AUTHOR
    props.lastModifiedBy = DOC_AUTHOR
    now = datetime.now(timezone.utc)
    props.created = now
    props.modified = now


def disable_soft_return_justify(doc):
    """Word stretches any justified line that ends in a soft return (Shift+Enter)
    to the full page width, which shows up as un-deletable gaps between words.
    The doNotExpandShiftReturn compat flag turns that behaviour off."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    settings_el = doc.settings.element
    compat = settings_el.find(qn('w:compat'))
    if compat is None:
        compat = OxmlElement('w:compat')
        settings_el.append(compat)
    if compat.find(qn('w:doNotExpandShiftReturn')) is None:
        compat.insert(0, OxmlElement('w:doNotExpandShiftReturn'))


def modernize_docx(doc):
    """Make the saved DOCX open cleanly editable in Word.

    - Drops documentProtection / readOnlyRecommended if the template carried them.
    - Stamps compatibilityMode=15: the report template was converted from a
      legacy .doc and carries mode 11, so every generated report opened with a
      '[Compatibility Mode]' banner and old-Word behaviour.
    - Replaces non-breaking spaces with plain spaces and trims trailing
      whitespace from each paragraph's last run (invisible junk inherited
      from the template text).
    """
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    settings_el = doc.settings.element
    for tag in ('w:documentProtection', 'w:readOnlyRecommended'):
        el = settings_el.find(qn(tag))
        if el is not None:
            settings_el.remove(el)

    compat = settings_el.find(qn('w:compat'))
    if compat is None:
        compat = OxmlElement('w:compat')
        settings_el.append(compat)
    for cs in compat.findall(qn('w:compatSetting')):
        if cs.get(qn('w:name')) == 'compatibilityMode':
            compat.remove(cs)
    cs = OxmlElement('w:compatSetting')
    cs.set(qn('w:name'), 'compatibilityMode')
    cs.set(qn('w:uri'), 'http://schemas.microsoft.com/office/word')
    cs.set(qn('w:val'), '15')
    compat.append(cs)

    for t in doc.element.body.iter(qn('w:t')):
        if t.text and ' ' in t.text:
            t.text = t.text.replace(' ', ' ')
    for p in doc.element.body.iter(qn('w:p')):
        texts = [t for t in p.iter(qn('w:t')) if t.text]
        if texts and texts[-1].text != texts[-1].text.rstrip():
            texts[-1].text = texts[-1].text.rstrip()


_SOFFICE_KNOWN_PATHS = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def _find_soffice() -> str | None:
    found = shutil.which('soffice') or shutil.which('soffice.exe')
    if found:
        return found
    for p in _SOFFICE_KNOWN_PATHS:
        if os.path.exists(p):
            return p
    return None


def docx_to_pdf(docx_path: str) -> str | None:
    """Convert docx to pdf. Returns pdf path or None on failure."""
    pdf_path = docx_path[:-5] + '.pdf' if docx_path.lower().endswith('.docx') else docx_path + '.pdf'

    # Try 1: docx2pdf (uses MS Word via COM on Windows)
    try:
        from docx2pdf import convert
        convert(docx_path, pdf_path)
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            return pdf_path
    except Exception as e:
        logger.warning(f"docx2pdf failed: {e}")

    # Try 2: soffice (LibreOffice)
    soffice = _find_soffice()
    if soffice:
        try:
            result = subprocess.run(
                [soffice, '--headless', '--convert-to', 'pdf',
                 '--outdir', os.path.dirname(docx_path), docx_path],
                capture_output=True, timeout=120
            )
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                return pdf_path
            logger.warning(f"soffice ran but produced no pdf: {result.stderr.decode(errors='ignore')}")
        except Exception as e:
            logger.warning(f"soffice failed: {e}")

    return None


def xlsx_to_pdf(xlsx_path: str) -> str | None:
    """Convert xlsx → pdf via Excel COM (Windows). Falls back to soffice."""
    pdf_path = xlsx_path[:-5] + '.pdf' if xlsx_path.lower().endswith('.xlsx') else xlsx_path + '.pdf'

    # Try 1: Excel COM (best fidelity for spreadsheets)
    try:
        import win32com.client
        excel = win32com.client.DispatchEx('Excel.Application')
        excel.Visible = False
        excel.DisplayAlerts = False
        wb = excel.Workbooks.Open(os.path.abspath(xlsx_path))
        try:
            # Fit to a single page — the budget form is a one-pager by design;
            # wrapped long values must shrink to fit rather than spill to page 2.
            for sheet in wb.Worksheets:
                sheet.PageSetup.Zoom = False
                sheet.PageSetup.FitToPagesWide = 1
                sheet.PageSetup.FitToPagesTall = 1
                sheet.PageSetup.Orientation = 1  # xlPortrait
            wb.ExportAsFixedFormat(0, os.path.abspath(pdf_path))  # 0 = xlTypePDF
        finally:
            wb.Close(SaveChanges=False)
            excel.Quit()
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            return pdf_path
    except Exception as e:
        logger.warning(f"Excel COM xlsx→pdf failed: {e}")

    # Try 2: soffice
    soffice = _find_soffice()
    if soffice:
        try:
            subprocess.run(
                [soffice, '--headless', '--convert-to', 'pdf',
                 '--outdir', os.path.dirname(xlsx_path), xlsx_path],
                capture_output=True, timeout=120
            )
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                return pdf_path
        except Exception as e:
            logger.warning(f"soffice xlsx→pdf failed: {e}")

    return None


_SUM_RE = re.compile(r'^=SUM\(([A-Z]+\d+):([A-Z]+\d+)\)$', re.I)


def _eval_sum(ws, formula: str):
    """Evaluate the simple =SUM(range) formulas the budget template uses.

    openpyxl writes formulas without cached results, so a workbook we generated
    has no computed totals to read — Excel/LibreOffice only fill those in when
    they open it. The template only ever uses SUM over one range."""
    m = _SUM_RE.match(formula.replace(' ', ''))
    if not m:
        return None
    total = 0
    for row in ws[f'{m.group(1)}:{m.group(2)}']:
        for c in row:
            if isinstance(c.value, (int, float)):
                total += c.value
    return total


def _cell_display(ws, cell) -> str:
    """The text Excel would show for a cell, including accounting formatting."""
    from datetime import date, datetime

    value = cell.value
    if isinstance(value, str) and value.startswith('='):
        value = _eval_sum(ws, value)
    if value is None:
        return ''
    if isinstance(value, (datetime, date)):
        return value.strftime('%d-%m-%Y')
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        if '#,##0' in (cell.number_format or ''):
            return '-' if value == 0 else f"{value:,.0f}"
        return str(int(value)) if float(value).is_integer() else str(value)
    return str(value)


def _col_width_inches(width_chars: float | None) -> float:
    """Excel column width (in '0'-glyph units) → inches, via Excel's px formula."""
    return (round((width_chars or 8.43) * 7) + 5) / 96.0


def _row_height_points(ws, r: int) -> float:
    """Row height in points, falling back to the sheet default."""
    return ws.row_dimensions[r].height or ws.sheet_format.defaultRowHeight or 15.0


def _anchor_size_emu(ws, image):
    """How big a floating picture is actually *drawn*, in EMU.

    image.width/height are the picture file's native pixel dimensions. If it was
    resized after being dropped into the sheet — the budget template's VIT-AP
    logo is — those are the wrong numbers: scaling by them renders the logo at
    3.6" instead of 1.75", overflowing the form's top border and punching a gap
    through it. The anchor carries the drawn size, so read it from there.
    """
    from openpyxl.utils import get_column_letter

    anchor = image.anchor
    ext = getattr(anchor, 'ext', None)
    if ext is not None:                       # OneCellAnchor / AbsoluteAnchor
        return ext.cx, ext.cy

    frm, to = getattr(anchor, '_from', None), getattr(anchor, 'to', None)
    if frm is None or to is None:             # nothing better to go on
        return None, None

    def col_emu(idx):
        return sum(int(_col_width_inches(
            ws.column_dimensions[get_column_letter(c)].width) * 914400)
            for c in range(1, idx + 1))

    def row_emu(idx):
        return sum(int(_row_height_points(ws, r) / 72 * 914400)
                   for r in range(1, idx + 1))

    cx = (col_emu(to.col) + (to.colOff or 0)) - (col_emu(frm.col) + (frm.colOff or 0))
    cy = (row_emu(to.row) + (to.rowOff or 0)) - (row_emu(frm.row) + (frm.rowOff or 0))
    return (cx if cx > 0 else None), (cy if cy > 0 else None)


def _row_is_padding(ws, r: int, n_cols: int) -> bool:
    """A row with neither text nor borders. Excel leaves these out of the printed
    form; as Word table rows they still take height and open a gap under it."""
    for c in range(1, n_cols + 1):
        cell = ws.cell(row=r, column=c)
        if cell.value not in (None, ''):
            return False
        b = cell.border
        if b.top.style or b.bottom.style or b.left.style or b.right.style:
            return False
    return True


def _h_align(xl):
    """Resolve Excel's horizontal alignment for a cell.

    'General' (horizontal=None) is not left — it right-aligns numbers and
    left-aligns text. Treating None as LEFT left-aligned every Qty, Rate and
    Amount in the budget form."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    explicit = {
        'center': WD_ALIGN_PARAGRAPH.CENTER,
        'right': WD_ALIGN_PARAGRAPH.RIGHT,
        'left': WD_ALIGN_PARAGRAPH.LEFT,
    }.get(xl.alignment.horizontal)
    if explicit is not None:
        return explicit

    value = xl.value
    if isinstance(value, str) and value.startswith('='):
        return WD_ALIGN_PARAGRAPH.RIGHT     # the SUM totals display as numbers
    if isinstance(value, bool):
        return WD_ALIGN_PARAGRAPH.LEFT
    if isinstance(value, (int, float)):
        return WD_ALIGN_PARAGRAPH.RIGHT
    return WD_ALIGN_PARAGRAPH.LEFT


def _shrink_empty_paragraph(para, pt=1):
    """Shrink an empty cell's paragraph mark so the row can honour the height
    Excel specified. Left at 11pt, every blank row is forced to ~0.19" and the
    accumulated overshoot pushes the signature block away from the table."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    pPr = para._p.get_or_add_pPr()
    rPr = pPr.find(qn('w:rPr'))
    if rPr is None:
        rPr = OxmlElement('w:rPr')
        # w:rPr is second-to-last in w:pPr's required child order — inserting it
        # at the front produces [rPr, spacing, jc], which Word discards.
        pPr.insert_element_before(rPr, 'w:sectPr', 'w:pPrChange')
    for tag in ('w:sz', 'w:szCs'):
        el = OxmlElement(tag)
        el.set(qn('w:val'), str(int(pt * 2)))
        rPr.append(el)


_BORDER_SZ = {'thin': 4, 'hair': 2, 'medium': 8, 'thick': 12, 'double': 8}


def _row_tc_at(table, row_idx: int, col_idx: int):
    """The <w:tc> sitting at this grid column of this row, honouring gridSpan.

    table.cell(r, c) on a merged region hands back the anchor cell whatever row
    you ask for, so it can't reach a vertical merge's continuation cells."""
    from docx.oxml.ns import qn

    tr = table.rows[row_idx]._tr
    pos = 0
    for tc in tr.findall(qn('w:tc')):
        span = 1
        tcPr = tc.find(qn('w:tcPr'))
        if tcPr is not None:
            gs = tcPr.find(qn('w:gridSpan'))
            if gs is not None:
                span = int(gs.get(qn('w:val')) or 1)
        if pos <= col_idx < pos + span:
            return tc
        pos += span
    return None


def _apply_borders_tc(tc, edges: dict):
    """Draw only the edges the spreadsheet had; explicitly nil the rest, so the
    Word table doesn't fall back to a uniform grid."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    tcPr = tc.get_or_add_tcPr()
    for existing in tcPr.findall(qn('w:tcBorders')):
        tcPr.remove(existing)
    borders = OxmlElement('w:tcBorders')
    for edge in ('top', 'left', 'bottom', 'right'):
        el = OxmlElement(f'w:{edge}')
        style = edges.get(edge)
        if style:
            el.set(qn('w:val'), 'double' if style == 'double' else 'single')
            el.set(qn('w:sz'), str(_BORDER_SZ.get(style, 4)))
            el.set(qn('w:color'), '000000')
        else:
            el.set(qn('w:val'), 'nil')
        borders.append(el)
    tcPr.append(borders)


def xlsx_to_docx(xlsx_path: str, docx_path: str | None = None) -> str | None:
    """Render a filled budget workbook as an editable Word document.

    LibreOffice refuses Calc → Writer ("Please verify input parameters"), and
    Word can't open an .xlsx, so the sheet's grid is rebuilt as a Word table
    carrying the same cells, merges, borders, column widths and logo."""
    if docx_path is None:
        docx_path = (xlsx_path[:-5] if xlsx_path.lower().endswith('.xlsx') else xlsx_path) + '.docx'

    try:
        import openpyxl
        from io import BytesIO
        from docx import Document
        from docx.shared import Inches, Pt, Emu
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        wb = openpyxl.load_workbook(xlsx_path)
        ws = wb.active
        n_rows, n_cols = ws.max_row, ws.max_column
        while n_rows > 1 and _row_is_padding(ws, n_rows, n_cols):
            n_rows -= 1

        doc = Document()
        section = doc.sections[0]
        section.page_width, section.page_height = Inches(8.27), Inches(11.69)  # A4
        for margin in ('left_margin', 'right_margin'):
            setattr(section, margin, Inches(0.5))
        section.top_margin = section.bottom_margin = Inches(0.5)
        normal = doc.styles['Normal']
        normal.font.name = 'Calibri'
        normal.font.size = Pt(11)

        # Anchor of each merged range, and the cells it swallows.
        anchors = {}
        covered = set()
        merges = []
        for rng in ws.merged_cells.ranges:
            if rng.min_row > n_rows:
                continue                       # entirely inside trimmed padding
            max_row = min(rng.max_row, n_rows)  # clamp one that straddles the cut
            merges.append((rng.min_row, rng.min_col, max_row, rng.max_col))
            anchors[(rng.min_row, rng.min_col)] = rng
            for r in range(rng.min_row, max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    if (r, c) != (rng.min_row, rng.min_col):
                        covered.add((r, c))

        table = doc.add_table(rows=n_rows, cols=n_cols)
        table.autofit = False
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        tblPr = table._tbl.tblPr
        layout = OxmlElement('w:tblLayout')
        layout.set(qn('w:type'), 'fixed')
        tblPr.append(layout)

        # Word's default cell padding (0.08" a side) is room Excel doesn't spend,
        # so text that fit on one line in the sheet wrapped here and pushed the
        # one-page form onto a second page. Trim it back.
        cell_mar = OxmlElement('w:tblCellMar')
        for edge, twips in (('top', 0), ('left', 40), ('bottom', 0), ('right', 40)):
            el = OxmlElement(f'w:{edge}')
            el.set(qn('w:w'), str(twips))
            el.set(qn('w:type'), 'dxa')
            cell_mar.append(el)
        tblPr.append(cell_mar)

        # Widths must be set before merging — a merged cell inherits the sum.
        widths = [
            Inches(_col_width_inches(
                ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width))
            for c in range(1, n_cols + 1)
        ]
        for row in table.rows:
            for i, cell in enumerate(row.cells):
                cell.width = widths[i]

        # Every row gets an explicit height, not just the ones Excel stored one
        # for — otherwise Word auto-sizes the rest to its own default and the
        # form drifts taller than the sheet.
        for r in range(1, n_rows + 1):
            trPr = table.rows[r - 1]._tr.get_or_add_trPr()
            trH = OxmlElement('w:trHeight')
            trH.set(qn('w:val'), str(int(_row_height_points(ws, r) * 20)))  # pt → twips
            trH.set(qn('w:hRule'), 'atLeast')
            trPr.append(trH)

        for min_row, min_col, max_row, max_col in merges:
            table.cell(min_row - 1, min_col - 1).merge(
                table.cell(max_row - 1, max_col - 1))

        for r in range(1, n_rows + 1):
            for c in range(1, n_cols + 1):
                if (r, c) in covered:
                    continue
                xl = ws.cell(row=r, column=c)
                cell = table.cell(r - 1, c - 1)

                # A merged cell's outer edges come from opposite corners of the
                # range, and a vertical merge needs them written to every row it
                # spans — the continuation cells carry the left/right edges that
                # close the box down its height.
                rng = anchors.get((r, c))
                far = ws.cell(row=rng.max_row, column=rng.max_col) if rng else xl
                lo, hi = (rng.min_row, min(rng.max_row, n_rows)) if rng else (r, r)
                for rr in range(lo, hi + 1):
                    tc = _row_tc_at(table, rr - 1, c - 1)
                    if tc is None:
                        continue
                    _apply_borders_tc(tc, {
                        'top': xl.border.top.style if rr == lo else None,
                        'bottom': far.border.bottom.style if rr == hi else None,
                        'left': xl.border.left.style,
                        'right': far.border.right.style,
                    })

                tcPr = cell._tc.get_or_add_tcPr()
                vAlign = OxmlElement('w:vAlign')
                vAlign.set(qn('w:val'), 'center')
                tcPr.append(vAlign)

                para = cell.paragraphs[0]
                para.paragraph_format.space_before = Pt(0)
                para.paragraph_format.space_after = Pt(0)
                para.alignment = _h_align(xl)

                text = _cell_display(ws, xl)
                if text:
                    run = para.add_run(text)
                    run.bold = bool(xl.font.bold)
                    run.font.name = xl.font.name or 'Calibri'
                    run.font.size = Pt(xl.font.size or 11)
                else:
                    _shrink_empty_paragraph(para)

        # Logo (and any other floating picture) goes into the cell it sits over.
        for image in getattr(ws, '_images', []):
            try:
                row = image.anchor._from.row
                if row >= n_rows:
                    continue
                cell = table.cell(row, image.anchor._from.col)
                para = cell.paragraphs[0]
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                cx, cy = _anchor_size_emu(ws, image)
                if cx is None:
                    cx = int(image.width / 96 * 914400)
                para.add_run().add_picture(BytesIO(image._data()), width=Emu(cx))
            except Exception as e:
                logger.warning(f"could not place image in docx: {e}")

        # A document body must end in a paragraph, and at default size that
        # paragraph doesn't fit under the form — it spilled a blank second page.
        tail = doc.add_paragraph()
        tail.paragraph_format.space_before = Pt(0)
        tail.paragraph_format.space_after = Pt(0)
        tail.paragraph_format.line_spacing = Pt(1)
        tail.add_run().font.size = Pt(1)

        stamp_docx_author(doc)
        modernize_docx(doc)
        doc.save(docx_path)
        return docx_path if os.path.exists(docx_path) else None
    except Exception as e:
        logger.warning(f"xlsx→docx failed: {e}")
        return None


def pdf_to_images(pdf_path: str, out_dir: str, dpi: int = 180) -> list[str]:
    """Render every page of a PDF to PNG. Returns list of png paths in page order."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF not installed — cannot render PDF pages.")
        return []

    paths = []
    try:
        doc = fitz.open(pdf_path)
        zoom = dpi / 72.0  # default DPI is 72
        mat = fitz.Matrix(zoom, zoom)
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat, alpha=False)
            out_path = os.path.join(out_dir, f"{base}_p{i+1}.png")
            pix.save(out_path)
            paths.append(out_path)
        doc.close()
    except Exception as e:
        logger.error(f"PyMuPDF rendering failed: {e}")
    return paths


IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'bmp', 'gif', 'webp', 'tiff'}


def file_to_images(file_path: str, out_dir: str) -> list[str]:
    """Convert any uploaded file (image, pdf, docx) to a list of PNG paths.

    - image → returns [file_path]
    - pdf → renders each page → list of pngs
    - docx → converts to pdf first → renders pages
    """
    if not file_path or not os.path.exists(file_path):
        return []

    ext = file_path.rsplit('.', 1)[-1].lower() if '.' in file_path else ''

    if ext in IMAGE_EXTS:
        return [file_path]

    if ext == 'pdf':
        return pdf_to_images(file_path, out_dir)

    if ext in ('docx', 'doc'):
        pdf_path = docx_to_pdf(file_path)
        if pdf_path:
            return pdf_to_images(pdf_path, out_dir)
        return []

    return []
