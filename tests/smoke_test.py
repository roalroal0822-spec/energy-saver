"""구동 점검: 서버를 임시 작업 폴더로 띄워 주요 기능을 차례로 호출한다(모델 없이 규칙 기반).

실행: python tests/smoke_test.py
원본(sample_data)은 읽기만 하며, 작업 데이터는 임시 폴더에 쓰고 끝나면 지운다.
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def call(self, method, path, body=None, raw=False, headers=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        h = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        with urllib.request.urlopen(req, timeout=120) as r:
            b = r.read()
            return b if raw else json.loads(b)

    def status(self, method, path, body=None, headers=None):
        try:
            self.call(method, path, body, raw=True, headers=headers)
            return 200
        except urllib.error.HTTPError as e:
            return e.code


def checks(c):
    yield "화면 열기", c.call("GET", "/", raw=True)[:15].lower().startswith(b"<!doctype")
    s = c.call("GET", "/api/state")
    yield f"처음 실행: 모의자료(8월까지) 자동 불러오기 · 최신 {s['overview']['latest']}", s["overview"]["latest"] == "2026-08"
    s = c.call("POST", "/api/sample", {"with_bills": True})
    bills = [b for b in s["bills"] if not b["missing"]]
    yield f"고지서 PDF 읽기 {len(bills)}/5장 · 최신 {s['overview']['latest']}", len(bills) == 5 and s["overview"]["latest"] == "2026-09"
    types = sorted({f["type"] for f in s["findings"]})
    yield f"점검 결과 {len(s['findings'])}건 ({', '.join(types)})", {"excess", "gas_excess", "closure", "contract", "pf", "bill"} <= set(types)
    rules = [r for r in s["overview"]["rules"] if r["from"] == "지침"]
    yield f"점검 기준을 지침 조문에서 읽음 {len(rules)}/{len(s['overview']['rules'])}", len(rules) == len(s["overview"]["rules"])
    eff = [e for e in s["effects"] if e.get("saved_pct")]
    yield f"조치 효과 측정({eff[0]['title'] if eff else '없음'} {eff[0]['saved_pct'] if eff else ''}%)", bool(eff) and 5 < eff[0]["saved_pct"] < 12
    a = c.call("POST", "/api/ask", {"q": "체육센터 8월 전기 왜 늘었어?"})
    yield "질문: 기온 보정 설명", "19.3" in a["answer"] and a["sources"]
    a = c.call("POST", "/api/ask", {"q": "주차장 기본요금 줄일 수 있어?"})
    yield "질문: 계약전력", "요금적용전력" in a["answer"]
    sm = c.call("POST", "/api/summary", {})
    yield "총평(규칙)", "점검 기준" in sm["text"]
    for fmt in ("hwpx", "docx", "md"):
        d = c.call("POST", f"/api/report?fmt={fmt}", {}, raw=True)
        yield f"보고서 내보내기({fmt})", len(d) > 500
    tg = s["overview"]["target"]
    yield "감축 목표를 지침 조문에서 읽고 진척 계산", tg and tg["target_pct"] == 5 and tg["basis"]["label"] == "제14조" and tg["gap_kwh"] > 0
    ol = s["overview"]["outlook"]
    yield "다음 달 시설별 관리 목표(작년 같은 달 기온)", ol["rows"] and all(r["limit"] > r["expected"] for r in ol["rows"])
    q = c.call("POST", "/api/ask", {"q": "감축 목표 달성할 수 있어?"})
    yield "목표 질문 답변", "목표" in q["answer"] and "제14조" not in q["answer"] and q["sources"]
    d = c.call("POST", "/api/export-xlsx", {}, raw=True)
    yield "엑셀 내보내기(시트 3개)", d[:2] == b"PK" and _sheets(d) == ["점검 결과", "시설별 월 내역", "조치 효과"]
    fid = s["findings"][0]["id"]
    d = c.call("POST", f"/api/findings/{fid}/request?fmt=hwpx", {}, raw=True)
    yield "점검 요청서", d[:2] == b"PK"
    f = next(x for x in s["findings"] if x["type"] == "pf")
    act = c.call("POST", "/api/actions", {"finding": f["id"], "title": "진상용 콘덴서 교체", "date": "2026-10-10", "status": "예정"})
    s2 = c.call("GET", "/api/state")
    yield "조치 등록", any(e["id"] == act["id"] and e["state"] == "시행 전" for e in s2["effects"])
    c.call("POST", f"/api/actions/{act['id']}", {"status": "완료"})
    s3 = c.call("GET", "/api/state")
    yield "조치 완료 처리(측정 대기)", any(e["id"] == act["id"] and e["state"].startswith("측정 중") for e in s3["effects"])
    c.call("DELETE", f"/api/actions/{act['id']}")
    up = c.call("POST", "/api/upload", {"files": [{"path": "추가/메모.txt", "b64": base64.b64encode("메모".encode()).decode()}]})
    yield "자료 올리기(읽을 표가 없는 파일은 경고 없이 보관)", up["overview"]["latest"] == "2026-09"
    yield "다른 사이트 요청 차단", c.status("POST", "/api/reset", {}, {"Origin": "http://evil.example"}) == 403
    yield "올리기 경로 제한", c.status("POST", "/api/upload", {"files": [{"path": "../../x.exe", "b64": "AA=="}]}) == 400
    r = c.call("POST", "/api/reset", {})
    yield "자료 비우기 후에도 동작", r["overview"]["latest"] is None and r["findings"] == []
    yield "원본 보호(sample_data 파일 수 그대로)", sum(1 for p in (ROOT / "sample_data").rglob("*") if p.is_file()) == 11


def _sheets(data):
    import io
    from openpyxl import load_workbook
    return load_workbook(io.BytesIO(data)).sheetnames


def main():
    tmp = tempfile.mkdtemp(prefix="smoke-energy-")
    port = free_port()
    env = dict(os.environ, APP_DATA_DIR=tmp, LLM_PROVIDER="none")
    proc = subprocess.Popen([sys.executable, "run.py", "--no-browser", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    total = failed = 0
    try:
        c = Client(port)
        for _ in range(60):
            try:
                c.call("GET", "/api/info")
                break
            except Exception:
                time.sleep(0.3)
        print(f"[에너지 절감 비서] http://127.0.0.1:{port}")
        for label, ok in checks(c):
            total += 1
            failed += 0 if ok else 1
            print(f"  {'통과' if ok else '실패'}  {label}")
    except Exception as e:
        total += 1
        failed += 1
        print(f"  실패  {type(e).__name__}: {e}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n결과: {total - failed}/{total} 통과")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
