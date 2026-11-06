"""구동 점검: 서버를 임시 작업 폴더로 띄워 주요 기능을 차례로 호출한다(모델 없이 규칙 기반).

실행: python tests/smoke_test.py
원본(sample_data)은 읽기만 하며, 작업 데이터는 임시 폴더에 쓰고 끝나면 지운다.
"""
import base64
import json
import urllib.parse
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
sys.path.insert(0, str(ROOT))


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
    yield "엑셀 내보내기(시트 5개: 점검·월 내역·조치 효과·예산 집행·내년 편성)", d[:2] == b"PK" and _sheets(d) == ["점검 결과", "시설별 월 내역", "조치 효과", "2026 예산 집행", "2027 편성 근거"]
    b = c.call("GET", "/api/budget")
    cur, ny, nm = b["current"], b["next_year"], b["next_month"]
    st = {(i["fac"], i["item"]): i for i in cur["items"]}
    yield f"예산서 읽기(본예산·추경 {len(cur['items'])}과목, 예산현액 {cur['totals']['current']:,}원)", len(cur["items"]) == 9 and cur["totals"]["current"] == 1_404_000_000
    yield (f"예산 판정: 체육센터 전기료 {st[('종합체육센터', '전기료')]['state']}·문화예술회관 전기료 {st[('문화예술회관', '전기료')]['state']}",
           st[("종합체육센터", "전기료")]["state"] == "부족 예상" and st[("종합체육센터", "전기료")]["run_out"] and st[("문화예술회관", "전기료")]["state"] == "불용 예상")
    yield "가스 고지 전 달은 예측으로 채움(9월 가스)", any(m["billed_missing"] for m in st[("종합체육센터", "가스료")]["months_fc"]) and st[("종합체육센터", "가스료")]["state"] == "적정"
    yield f"전용 검토안 {len(cur['moves'])}건", cur["moves"] and cur["moves"][0]["to"] == "종합체육센터 전기료" and cur["moves"][0]["amount"] >= 1_000_000
    yield f"다음 달({nm['ym']}) 요금 예측", nm["ym"] == "2026-10" and len(nm["rows"]) == 5 and all(r["cost"] > 0 for r in nm["rows"])
    yield (f"내년 편성 {ny['totals']['request']:,}원(천원 단위), 유가 연동 가스 {ny['oil']['gas_change_pct']}%",
           ny["fy"] == 2027 and all(i["request"] % 1000 == 0 for i in ny["items"]) and ny["oil"]["has"] and ny["oil"]["gas_change_pct"] > 0 and ny["totals"]["risk"] > 0)
    b2 = c.call("GET", "/api/budget?save_pct=5&elec_up_pct=3")
    yield "편성 가정 바꾸기(절감 5%·전기요금 3%)", b2["next_year"]["totals"]["request"] != ny["totals"]["request"] and b2["params"]["save_pct"] == 5
    yield "예산 질문(부족)", "부족" in c.call("POST", "/api/ask", {"q": "올해 예산 모자라는 곳 있어?"})["answer"]
    yield "예산 질문(내년 편성)", "2027" in c.call("POST", "/api/ask", {"q": "내년 예산 얼마 잡아야 해?"})["answer"]
    d = c.call("POST", "/api/budget/report?fmt=hwpx", {"save_pct": 0}, raw=True)
    yield "예산 문서(집행 관리·편성 근거, hwpx)", d[:2] == b"PK" and len(d) > 2000
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
    ab = c.call("GET", "/api/about")
    yield "서비스 구조·검증(처리 단계·안전장치·성적표)", len(ab["pipeline"]) >= 5 and len(ab["guards"]) >= 5 and ab["scorecard"] and ab["scorecard"]["areas"]
    pq = c.call("POST", "/api/ask", {"q": "담당 010-1234-5678 체육센터 8월 전기 왜 늘었어?"})
    yield "질문의 개인정보 가림", pq["masked"] == ["휴대전화"] and "010-1234" not in str(c.call("GET", "/api/log"))
    yield "다른 사이트 요청 차단", c.status("POST", "/api/reset", {}, {"Origin": "http://evil.example"}) == 403
    yield "올리기 경로 제한", c.status("POST", "/api/upload", {"files": [{"path": "../../x.exe", "b64": "AA=="}]}) == 400
    s0 = c.call("GET", "/api/state")
    k = c.call("POST", "/api/kma", {"on": True})
    w = [f for f in k["data"]["files"] if "기상청" in f["kind"]]
    yield (f"기온 자동 수집 켜기(가짜 기상청 서버, {k['kma']['months']}개월)",
           k["kma"]["on"] and k["kma"]["months"] == 24 and not k["kma"]["error"] and w and w[0]["rows"] == 24)
    sr = c.call("GET", "/api/series?fac=" + urllib.parse.quote("본부 청사") + "&kind=elec")
    yield "받은 일별 기온으로 도일 계산(기온 20℃ 고정 → 냉방·난방 0)", all(x["temp"] == 20.0 for x in sr["rows"]) and (sr["model"] is None or sr["model"]["cdd"] == 0)
    k2 = c.call("POST", "/api/kma", {"on": False})
    yield "기온 자동 수집 끄면 넣은 파일 기온으로 돌아감", not k2["kma"]["on"] and len(k2["findings"]) == len(s0["findings"])
    import kma as K
    yield "응답 읽기: API허브 글자 표·JSON, 공공데이터포털 JSON", (
        K.parse_hub("#START7777\n# TM STN WS_AVG WR_DAY TA_AVG TA_MAX\n20260101 108 2.1 100 -3.4 1.2\n20260102 108 1.0 90 -99.0 0\n#7777END") == {"2026-01-01": -3.4}
        and K.parse_hub('{"response":{"body":{"items":{"item":[{"TM":"20260105","TA_AVG":"1.5"}]}}}}') == {"2026-01-05": 1.5}
        and K.parse_dgk('{"response":{"header":{"resultCode":"00"},"body":{"items":{"item":[{"tm":"2026-01-03","avgTa":"-1.0"},{"tm":"2026-01-04","avgTa":""}]}}}}') == {"2026-01-03": -1.0})
    real = (ROOT / "tests" / "fixtures" / "kma_sfcdd3_sample.txt").read_text(encoding="utf-8")
    yield "실제 API허브 응답(2015-12-11~14 서울) 읽기", K.parse_hub(real) == {"2015-12-11": 6.4, "2015-12-12": 5.2, "2015-12-13": 5.2, "2015-12-14": 5.0}
    os.environ["KMA_APIHUB_KEY"], old_key = "https://apihub.kma.go.kr/api/typ01/url/kma_sfcdd3.php?tm1=1&authKey=ABC&help=1", os.environ.get("KMA_APIHUB_KEY", "")
    yield "키 칸에 주소 전체를 넣어도 키만 씀", K.keys()["hub"] == "ABC"
    os.environ["KMA_APIHUB_KEY"] = old_key
    r = c.call("POST", "/api/reset", {})
    yield "자료 비우기 후에도 동작", r["overview"]["latest"] is None and r["findings"] == []
    yield "원본 보호(sample_data 파일 수 그대로)", sum(1 for p in (ROOT / "sample_data").rglob("*") if p.is_file()) == 13


def _sheets(data):
    import io
    from openpyxl import load_workbook
    return load_workbook(io.BytesIO(data)).sheetnames


def fake_kma():
    """기상청 API허브 흉내: 요청한 기간의 날마다 일평균기온 20.0℃를 글자 표로 돌려준다."""
    import threading
    from datetime import date, timedelta
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            ok = q.get("authKey") == ["test-key"]
            a, b = (date(int(x[:4]), int(x[4:6]), int(x[6:8])) for x in (q["tm1"][0], q["tm2"][0]))
            lines = ["#START7777", "# TM STN WS_AVG WR_DAY TA_AVG TA_MAX TA_MIN"]
            while ok and a <= b:
                lines.append(f"{a:%Y%m%d} 108 2.0 100 20.0 25.0 15.0")
                a += timedelta(days=1)
            body = ("\n".join(lines + ["#7777END"]) if ok else "인증 실패").encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    s = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def main():
    tmp = tempfile.mkdtemp(prefix="smoke-energy-")
    port = free_port()
    fake = fake_kma()
    env = dict(os.environ, APP_DATA_DIR=tmp, LLM_PROVIDER="none", KMA_APIHUB_KEY="test-key",
               KMA_HUB_URL=f"http://127.0.0.1:{fake.server_port}/kma_sfcdd3.php")
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
