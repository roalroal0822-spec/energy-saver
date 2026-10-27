"""엑셀(.xlsx) 내보내기: 시트마다 (이름, 머리글, 행, 열 너비) 하나."""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

CTYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEAD = PatternFill("solid", fgColor="E8EEF7")


def workbook(sheets, note=""):
    """sheets: [(시트 이름, 머리글 목록, 행 목록, 열 너비 목록 또는 None)] → xlsx 바이트."""
    wb = Workbook()
    wb.remove(wb.active)
    for name, header, rows, widths in sheets:
        ws = wb.create_sheet(name[:31])
        r0 = 1
        if note:
            ws.cell(1, 1, note).font = Font(size=9, color="666666")
            r0 = 3
        for c, h in enumerate(header, 1):
            cell = ws.cell(r0, c, h)
            cell.font, cell.fill = Font(bold=True), HEAD
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for i, row in enumerate(rows, r0 + 1):
            for c, v in enumerate(row, 1):
                ws.cell(i, c, v).alignment = Alignment(vertical="top", wrap_text=isinstance(v, str) and len(v) > 20)
        for c, w in enumerate(widths or [14] * len(header), 1):
            ws.column_dimensions[get_column_letter(c)].width = w
        ws.freeze_panes = ws.cell(r0 + 1, 1)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
