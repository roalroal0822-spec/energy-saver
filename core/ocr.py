"""스캔(이미지) PDF 글자 인식.

- 쪽 그림 만들기: pypdfium2(구글 PDFium, Apache-2.0/BSD-3) — pip 설치, 별도 프로그램 불필요.
- 글자 인식: Tesseract OCR(Apache-2.0, github.com/tesseract-ocr/tesseract) + 한국어 학습 자료(kor).
  Tesseract는 별도 설치 프로그램이다. PATH에 있거나, 환경변수 TESSERACT_CMD, 또는 Windows 기본 설치 위치에서 찾는다.
없으면 None을 돌려주고, 부르는 쪽이 '글자 인식 프로그램이 없어 못 읽음'을 알린다.
인식 결과는 숫자를 잘못 읽을 수 있으므로 출처에 '(글자 인식)'을 붙여 원본 대조를 안내한다.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

WIN_PATHS = [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]


def tesseract_cmd():
    for c in [os.environ.get("TESSERACT_CMD", ""), shutil.which("tesseract") or ""] + WIN_PATHS:
        if c and Path(c).exists():
            return c
    return None


def status():
    """화면 표시용: 쓸 수 있는지, 한국어 자료가 있는지."""
    cmd = tesseract_cmd()
    if not cmd:
        return {"ok": False, "engine": "Tesseract OCR", "note": "글자 인식 프로그램(Tesseract OCR)이 설치되어 있지 않습니다"}
    try:
        langs = subprocess.run([cmd, "--list-langs"], capture_output=True, text=True, timeout=20).stdout
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "engine": "Tesseract OCR", "note": f"Tesseract 실행 실패: {e}"}
    kor = "kor" in langs.split()
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        return {"ok": False, "engine": "Tesseract OCR", "note": "pypdfium2가 설치되어 있지 않습니다(pip install -r requirements.txt)"}
    return {"ok": kor, "engine": "Tesseract OCR", "note": "" if kor else "Tesseract에 한국어(kor) 학습 자료가 없습니다"}


def _fix_numbers(line):
    # 1.355.200 → 1,355,200 (글자 인식이 쉼표를 점으로 읽는 경우). 1304.81 같은 소수는 그대로.
    return re.sub(r"(?<=\d)\.(?=\d{3}(?:[.,]\d{3})*(?!\d))", ",", line)


def pdf_lines(data, max_pages=5, dpi=300):
    """이미지 PDF → [(위치, 줄 글자)]. 인식 프로그램이 없으면 None."""
    cmd = tesseract_cmd()
    if not cmd:
        return None
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    out = []
    pdf = pdfium.PdfDocument(data)
    with tempfile.TemporaryDirectory() as td:
        for pno in range(min(len(pdf), max_pages)):
            bm = pdf[pno].render(scale=dpi / 72, grayscale=True)
            w, h, stride, buf = bm.width, bm.height, bm.stride, bytes(bm.buffer)
            p = Path(td) / f"p{pno}.pgm"  # Pillow 없이 회색조 PGM으로 저장(Tesseract가 바로 읽음)
            with open(p, "wb") as f:
                f.write(f"P5 {w} {h} 255\n".encode())
                if stride == w:
                    f.write(buf[: w * h])
                else:
                    for y in range(h):
                        f.write(buf[y * stride: y * stride + w])
            r = subprocess.run([cmd, str(p), "stdout", "-l", "kor+eng", "--psm", "6"], capture_output=True, timeout=120)
            text = r.stdout.decode("utf-8", "replace")
            for li, line in enumerate(text.splitlines(), 1):
                t = _fix_numbers(re.sub(r"\s+", " ", line).strip())
                if t:
                    out.append((f"p.{pno + 1} {li}줄(글자 인식)", t))
    return out
