"""산출물 내보내기: 한글(.hwpx), 워드(.docx), 마크다운(.md).

앱은 아래 형식의 '블록 목록' 하나만 만들면 세 형식으로 모두 저장할 수 있다.
  ("title", "문서 제목")
  ("h1"|"h2"|"h3", "제목")
  ("p", "문단")            # __밑줄__, **굵게** 표기 지원
  ("bullet", "항목")
  ("table", header_list, rows_list[, col_widths])
  ("note", "※ 참고")
  ("summary", ["요약 줄", ...])  # 한글: 요약 상자, 그 밖: 글머리 목록
  ("image", base64_또는_bytes, "그림 설명")  # PNG·JPEG·GIF, 가운데 정렬, 아래에 설명
"""
import io
import re
import warnings

RICH = re.compile(r"(__.+?__|\*\*.+?\*\*)")


def _segments(text):
    """'__밑줄__', '**굵게**' 표기를 (텍스트, 굵게, 밑줄) 조각으로 나눈다."""
    out = []
    for part in RICH.split(text or ""):
        if not part:
            continue
        if part.startswith("__") and part.endswith("__"):
            out.append((part[2:-2], False, True))
        elif part.startswith("**") and part.endswith("**"):
            out.append((part[2:-2], True, False))
        else:
            out.append((part, False, False))
    return out


def image_info(data):
    """그림 바이트 → (형식, 가로, 세로). Pillow 없이 PNG·JPEG·GIF 머리만 읽는다. 모르는 형식이면 ValueError."""
    import base64
    import struct
    if isinstance(data, str):
        data = base64.b64decode(data.split(",", 1)[-1])
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return data, "png", w, h
    if data[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", data[6:10])
        return data, "gif", w, h
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return data, "jpg", w, h
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    raise ValueError("PNG·JPEG·GIF 그림만 넣을 수 있습니다")


def _img_mm(w, h, max_w=150, max_h=110):
    s = min(max_w / max(w, 1), max_h / max(h, 1))
    return w * s, h * s


def plain(text):
    return "".join(s for s, _, _ in _segments(text))


# ---------------------------------------------------------------- Markdown

def to_markdown(blocks):
    lines = []
    for b in blocks:
        kind = b[0]
        if kind == "title":
            lines += [f"# {b[1]}", ""]
        elif kind in ("h1", "h2", "h3"):
            lines += ["#" * (int(kind[1]) + 1) + " " + b[1], ""]
        elif kind == "p":
            lines += [b[1], ""]
        elif kind == "bullet":
            lines.append(f"- {b[1]}")
        elif kind == "note":
            lines += [f"> {b[1]}", ""]
        elif kind == "summary":
            lines += ["**핵심 요약**", ""] + [f"- {x}" for x in b[1]] + [""]
        elif kind == "image":
            import base64
            data, fmt, _, _ = image_info(b[1])
            lines += [f"![{b[2] if len(b) > 2 else ''}](data:image/{'jpeg' if fmt == 'jpg' else fmt};base64,{base64.b64encode(data).decode()})", ""]
            if len(b) > 2 and b[2]:
                lines += [f"<그림> {b[2]}", ""]
        elif kind == "table":
            header, rows = b[1], b[2]
            esc = lambda s: str(s).replace("|", "\\|").replace("\n", "<br>")
            lines.append("| " + " | ".join(esc(h) for h in header) + " |")
            lines.append("|" + "---|" * len(header))
            for r in rows:
                lines.append("| " + " | ".join(esc(c) for c in r) + " |")
            lines.append("")
    return "\n".join(lines).encode("utf-8")


# ---------------------------------------------------------------- HWPX
# 정부 보고서 양식: 제목 상자(위아래 굵은 선) · 오른쪽 작성 정보 · 요약 상자 ·
# Ⅰ/□/○/※ 단계 기호 · 머리행 음영 표 · 쪽 번호(- 1 -). 글꼴은 한글 기본 글꼴(HY헤드라인M·휴먼명조·맑은 고딕).

NAVY = "#1F3864"
ROMAN = "Ⅰ Ⅱ Ⅲ Ⅳ Ⅴ Ⅵ Ⅶ Ⅷ Ⅸ Ⅹ".split()
F_HEAD, F_BODY, F_TABLE = "HY헤드라인M", "휴먼명조", "맑은 고딕"


def _roman(text):
    m = re.match(r"^(\d+)\.\s*(.*)$", text)
    if m and 1 <= int(m.group(1)) <= len(ROMAN):
        return f"{ROMAN[int(m.group(1)) - 1]}. {m.group(2)}"
    return text


def to_hwpx(blocks):
    from hwpx import HwpxDocument
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    doc = HwpxDocument.new()
    try:
        doc.set_page_margins(left=5669, right=5669, top=4252, bottom=4252)  # 좌우 20mm, 위아래 15mm
        doc.set_page_number(prefix="- ", suffix=" -")
    except Exception:
        pass
    cstyle, pstyle = {}, {}

    def cs(bold=False, underline=False, size=11, font=F_BODY, color=None):
        key = (bold, underline, size, font, color)
        if key not in cstyle:
            cstyle[key] = doc.ensure_run_style(bold=bold, underline=underline, size=size, font=font, color=color)
        return cstyle[key]

    def para(text="", fmt=None, **run):
        p = doc.add_paragraph("", include_run=False)
        for seg, bold, und in _segments(text):
            p.add_run(seg, char_pr_id_ref=cs(bold=bold or run.get("bold", False), underline=und, size=run.get("size", 12),
                                             font=run.get("font", F_BODY), color=run.get("color")))
        if fmt:
            key = tuple(sorted(fmt.items()))
            if key in pstyle:
                p.para_pr_id_ref = pstyle[key]
            else:
                doc.set_paragraph_format(paragraph_index=len(doc.paragraphs) - 1, **fmt)
                pstyle[key] = doc.paragraphs[-1].para_pr_id_ref
        return p

    def ppr(**fmt):
        """표 안 문단에 쓸 문단 모양 id(본문에 임시 문단을 만들어 얻고 지운다)."""
        key = tuple(sorted(fmt.items()))
        if key not in pstyle:
            para("", fmt)
            doc.remove_paragraph(len(doc.paragraphs) - 1)
        return pstyle[key]

    def cell_text(cell, text, size=10, bold=False, font=F_TABLE, align=None, color=None):
        cell.set_text("")
        for li, line in enumerate(str(text).split("\n")):
            cp = cell.paragraphs[0] if li == 0 else cell.add_paragraph("")
            if align:
                cp.para_pr_id_ref = ppr(alignment=align, line_spacing_percent=130)
            for seg, b, und in _segments(line):
                cp.add_run(seg, char_pr_id_ref=cs(bold=b or bold, underline=und, size=size, font=font, color=color))

    def box(lines, fill, title=None):
        t = doc.add_table(1, 1)
        bf = doc.ensure_border_fill(border_color=NAVY, border_width="0.4 mm", fill_color=fill)
        t.set_cell_border_fill(0, 0, bf)
        c = t.cell(0, 0)
        c.set_text("")
        first = True
        if title:
            cp = c.paragraphs[0]
            cp.add_run(title, char_pr_id_ref=cs(bold=True, size=12, font=F_HEAD, color=NAVY))
            first = False
        for ln in lines:
            cp = c.paragraphs[0] if first else c.add_paragraph("")
            first = False
            cp.para_pr_id_ref = ppr(line_spacing_percent=160, indent_left_mm=2)
            for seg, b, und in _segments("○ " + ln):
                cp.add_run(seg, char_pr_id_ref=cs(bold=b, underline=und, size=11, font=F_BODY))
        para("", {"spacing_after_pt": 4})

    prev = None
    first = True
    for b in blocks:
        kind = b[0]
        if kind == "title":
            t = doc.add_table(1, 1)
            bf = doc.ensure_border_fill(border_color=NAVY, border_width="1.0 mm", active_borders=["top", "bottom"])
            t.set_cell_border_fill(0, 0, bf)
            cell_text(t.cell(0, 0), b[1], size=20, bold=False, font=F_HEAD, align="CENTER", color="#000000")
            para("", {"spacing_after_pt": 2}, size=6)
        elif kind == "p" and prev == "title":
            para(b[1], {"alignment": "RIGHT", "spacing_after_pt": 10}, size=10, font=F_TABLE, color="#555555")
        elif kind == "summary":
            box(b[1], "#F3F5F9", "□ 핵심 요약")
        elif kind == "h1":
            para(_roman(b[1]), {"spacing_before_pt": 14, "spacing_after_pt": 6, "bottom_border": True, "border_color": NAVY,
                                "border_width": "0.4 mm", "keep_with_next": True}, size=15, font=F_HEAD, color=NAVY)
        elif kind in ("h2", "h3"):
            para("□ " + b[1], {"spacing_before_pt": 8, "spacing_after_pt": 3, "keep_with_next": True}, size=13, bold=True)
        elif kind == "p":
            for line in str(b[1]).split("\n"):
                para(line, {"indent_left_mm": 3, "line_spacing_percent": 160, "spacing_after_pt": 3}, size=12)
        elif kind == "bullet":
            para("○ " + b[1], {"indent_left_mm": 6, "first_line_indent_mm": -4, "line_spacing_percent": 160, "spacing_after_pt": 2}, size=12)
        elif kind == "note":
            txt = re.sub(r"^※\s*", "", str(b[1]))
            para("※ " + txt, {"indent_left_mm": 6, "first_line_indent_mm": -4, "line_spacing_percent": 140, "spacing_after_pt": 4},
                 size=10, font=F_TABLE, color="#555555")
        elif kind == "table":
            header, rows = b[1], b[2]
            ncol = max([len(header)] + [len(r) for r in rows])
            t = doc.add_table(len(rows) + 1, ncol)
            hb = doc.ensure_border_fill(border_color="#7F8FA9", border_width="0.12 mm", fill_color="#DCE3EE")
            cb = doc.ensure_border_fill(border_color="#7F8FA9", border_width="0.12 mm")
            for c in range(ncol):
                t.set_cell_border_fill(0, c, hb)
                cell_text(t.cell(0, c), header[c] if c < len(header) else "", size=10, bold=True, align="CENTER")
            for ri, r in enumerate(rows, 1):
                for c in range(ncol):
                    val = str(r[c]) if c < len(r) and r[c] is not None else ""
                    t.set_cell_border_fill(ri, c, cb)
                    numeric = bool(re.fullmatch(r"[-+]?[\d,.]+(%|원|건|kWh|㎥)?( \(.*\))?", val.strip()))
                    cell_text(t.cell(ri, c), val, size=10, align="RIGHT" if numeric else None)
            if len(b) > 3 and b[3]:
                try:
                    total = sum(b[3])
                    width = 48188  # A4 폭 210mm - 좌우 여백 40mm (HWPUNIT)
                    t.set_column_widths([int(width * w / total) for w in b[3]])
                except Exception:
                    pass
            para("", {"spacing_after_pt": 4}, size=6)
        elif kind == "image":
            data, fmt, w, h = image_info(b[1])
            wm, hm = _img_mm(w, h)
            doc.add_picture(data, fmt, width_mm=wm, height_mm=hm, para_pr_id_ref=ppr(alignment="CENTER"))
            if len(b) > 2 and b[2]:
                para(f"<그림> {b[2]}", {"alignment": "CENTER", "spacing_after_pt": 6}, size=10, font=F_TABLE)
        prev = kind
        first = False
    return doc.to_bytes()


# ---------------------------------------------------------------- DOCX

def to_docx(blocks):
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt

    d = Document()
    st = d.styles["Normal"]
    st.font.name = "맑은 고딕"
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")
    st.font.size = Pt(10.5)

    def rich(par, text):
        for seg, bold, und in _segments(text):
            r = par.add_run(seg)
            r.bold = bold or None
            r.underline = und or None

    for b in blocks:
        kind = b[0]
        if kind == "title":
            d.add_heading(b[1], level=0)
        elif kind in ("h1", "h2", "h3"):
            d.add_heading(b[1], level=int(kind[1]))
        elif kind in ("p", "note"):
            for line in str(b[1]).split("\n"):
                rich(d.add_paragraph(), line)
        elif kind == "bullet":
            rich(d.add_paragraph(style="List Bullet"), b[1])
        elif kind == "summary":
            rich(d.add_paragraph(), "**핵심 요약**")
            for x in b[1]:
                rich(d.add_paragraph(style="List Bullet"), x)
        elif kind == "table":
            header, rows = b[1], b[2]
            ncol = max([len(header)] + [len(r) for r in rows])
            t = d.add_table(rows=1, cols=ncol)
            t.style = "Table Grid"
            for c in range(ncol):
                t.rows[0].cells[c].text = str(header[c]) if c < len(header) else ""
            for r in rows:
                cells = t.add_row().cells
                for c in range(ncol):
                    val = str(r[c]) if c < len(r) and r[c] is not None else ""
                    cells[c].text = ""
                    lines = val.split("\n")
                    par = cells[c].paragraphs[0]
                    for li, line in enumerate(lines):
                        if li:
                            par = cells[c].add_paragraph()
                        rich(par, line)
            d.add_paragraph()
        elif kind == "image":
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.shared import Mm
            data, fmt, w, h = image_info(b[1])
            wm, _ = _img_mm(w, h)
            d.add_picture(io.BytesIO(data), width=Mm(wm))
            d.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            if len(b) > 2 and b[2]:
                cp = d.add_paragraph(f"<그림> {b[2]}")
                cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


FORMATS = {
    "hwpx": (to_hwpx, "application/hwp+zip"),
    "docx": (to_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "md": (to_markdown, "text/markdown; charset=utf-8"),
}


def export(blocks, fmt):
    if fmt not in FORMATS:
        from .server import HttpError
        raise HttpError(400, f"지원하지 않는 파일 형식입니다: '{fmt}'. 한글(hwpx)·워드(docx)·마크다운(md) 중에서 골라 주세요.")
    fn, ctype = FORMATS[fmt]
    return fn(blocks), ctype
