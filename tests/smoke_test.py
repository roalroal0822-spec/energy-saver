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
        if fmt == "hwpx":
            import io, zipfile
            sec = zipfile.ZipFile(io.BytesIO(d)).read("Contents/section0.xml").decode("utf-8")
            yield "한글 보고서 양식(핵심 요약 상자·Ⅰ 단계 제목·□/○ 기호)", all(x in sec for x in ("□ 핵심 요약", "Ⅰ. 총평", "○ "))
    pv = c.call("GET", "/api/report/preview")["blocks"]
    import base64 as _b, struct as _s, zlib as _z
    def _png(w, h):
        ch = lambda t, d: _s.pack(">I", len(d)) + t + d + _s.pack(">I", _z.crc32(t + d) & 0xffffffff)
        return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", _s.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", _z.compress(b"".join(b"\x00" + b"\x80\x80\x80" * w for _ in range(h)))) + ch(b"IEND", b"")
    pv[0][1] = "고친 제목: 10월 에너지 점검"
    pv.insert(3, ["image", "data:image/png;base64," + _b.b64encode(_png(60, 30)).decode(), "현장 사진 설명"])
    d = c.call("POST", "/api/report?fmt=hwpx", {"blocks": pv}, raw=True)
    import io as _io, zipfile as _zf
    zz = _zf.ZipFile(_io.BytesIO(d))
    sec = zz.read("Contents/section0.xml").decode("utf-8")
    yield "보고서 미리보기에서 고친 제목·넣은 사진이 한글 파일에 들어감", "고친 제목: 10월 에너지 점검" in sec and "&lt;그림&gt; 현장 사진 설명" in sec \
        and any(n.startswith("BinData/") for n in zz.namelist())
    dx = c.call("POST", "/api/report?fmt=docx", {"blocks": pv}, raw=True)
    yield "고친 보고서 워드로 받기(사진 포함)", any(n.startswith("word/media/") for n in _zf.ZipFile(_io.BytesIO(dx)).namelist())
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
    pdf = (ROOT / "sample_data" / "고지서" / "전기요금_복지관_2026-09.pdf").read_bytes()
    fu = c.call("POST", "/api/upload", {"fac": "새 별관", "files": [{"path": "별관_9월.pdf", "b64": base64.b64encode(pdf).decode()}]})
    nb = next((b for b in fu["bills"] if "새 별관" in b["file"]), None)
    cov = c.call("GET", "/api/coverage")
    yield "시설별 자료: 고른 시설로 고지서 넣기·달별 채움 표", nb and nb["fac"] == "새 별관" and not nb["missing"] and any(r["fac"] == "새 별관" and "bill" in r["elec"] for r in cov["rows"])
    import ingest as I

    class _D:
        relpath, name, ext = "kepco.pdf", "kepco.pdf", ".pdf"
        text = ("○○공단 고객님의 2026년 10\n월\n행정센터\n2026년 11월 05일까지\n 09.05 ~ 10.04 일까지\n기본\u3000요금 1,000,000\n전력량요금 2,000,000\n"
                "역률\u3000요금 -10,000\n부가가치세 300,000\n당월요금계 3,300,000\nＴＶ수신료 2,500\n3,302,500\n계약종별 일반용(갑)저압\n역률 100\n"
                "계약전력 220 kW\n최대수요전력 64 kW\n1304.81\n12345 kWh\n23456 kWh")
        blocks = []
    rec, _, miss, _ = I.parse_bill(_D(), I.Dataset())
    yield "한국전력 청구서 형식 읽기(연월·사용량·청구금액·역률요금)", (rec.get("ym"), rec.get("kwh"), rec.get("total"), rec.get("pfc"), miss) == ("2026-10", 12345, 3302500, -10000, ["fac"])
    yield "올리기 경로 제한", c.status("POST", "/api/upload", {"files": [{"path": "../../x.exe", "b64": "AA=="}]}) == 400
    s0 = c.call("GET", "/api/state")
    k = c.call("POST", "/api/kma", {"on": True})
    w = [f for f in k["data"]["files"] if "기상청" in f["kind"]]
    yield (f"기온 자동 수집 켜기(가짜 기상청 서버, {k['kma']['months']}개월)",
           k["kma"]["on"] and k["kma"]["months"] == 24 and not k["kma"]["error"] and w and w[0]["rows"] == 24)
    sr = c.call("GET", "/api/series?fac=" + urllib.parse.quote("본부 청사") + "&kind=elec")
    yield "받은 일별 기온으로 도일 계산(기온 20℃ 고정 → 냉방·난방 0)", all(x["temp"] == 20.0 for x in sr["rows"]) and (sr["model"] is None or sr["model"]["cdd"] == 0)
    wx = c.call("GET", "/api/weather")
    yield "기상 분석 자료(일별 기온·도일·사용량)", len(wx["months"]) == 24 and all(m["api"] and len(m["days"]) >= 28 for m in wx["months"]) and wx["months"][0]["kwh"] > 0
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
    bi = c.call("GET", "/api/budget/input")
    yield "예산 입력: 엑셀 예산서 불러오기(9과목)", bi["fy"] == 2026 and len(bi["rows"]) == 9 and bi["source"].startswith("엑셀")
    rows = [{k: v for k, v in x.items() if k != "manual"} for x in bi["rows"]]
    for x in rows:
        if x["fac"] == "종합체육센터" and x["item"] == "전기료":
            x["supp2"] = 20000000
    c.call("POST", "/api/budget/input", {"fy": 2026, "rows": rows})
    bg = c.call("GET", "/api/budget")
    gym = next(i for i in bg["current"]["items"] if i["fac"] == "종합체육센터" and i["item"] == "전기료")
    yield "예산 입력: 제2회 추경 저장하면 판정이 바뀜(부족 → 적정)", gym["current"] == 350000000 and gym["state"] != "부족 예상"
    n27 = c.call("GET", "/api/budget/input?fy=2027")
    c.call("POST", "/api/budget/input", {"fy": 2027, "rows": [{k: v for k, v in x.items() if k != "manual"} for x in n27["rows"]]})
    yield "내년 예산 입력을 앱 편성안과 비교", c.call("GET", "/api/budget")["next_year"]["totals"]["entered"] is not None
    back = c.call("POST", "/api/budget/input", {"fy": 2026, "reset": True})
    tpl = c.call("POST", "/api/budget/template", {"fy": 2026}, raw=True)
    yield "엑셀 예산서로 되돌리기·예산서 양식 내려받기", back["source"].startswith("엑셀") and len(tpl) > 1000
    rc = c.call("GET", "/api/recommend")
    keys = {x["key"] for x in rc["items"]}
    yield f"절감 조치 추천 {len(rc['items'])}건 ({', '.join(sorted(keys))})", {"냉방", "보일러", "휴관", "계약전력", "역률", "LED"} <= keys and all(x["basis"] for x in rc["items"] if x["key"] != "진단")
    off = [x for x in rc["items"] if x["official"]]
    yield "외부 공식 기준은 '원문 확인 필요' 표시", off and all(x["official"]["verified"] is False for x in off)
    cool = next(x for x in rc["items"] if x["key"] == "냉방")
    c.call("POST", "/api/actions", {"fac": cool["fac"], "title": cool["title"], "kind": "냉방", "finding": cool["finding"]})
    rc2 = c.call("GET", "/api/recommend")
    yield "추천을 조치로 등록하면 '등록됨'으로 바뀌고 합계에서 빠짐", next(x for x in rc2["items"] if x["key"] == "냉방" and x["fac"] == cool["fac"])["registered"] and rc2["totals"]["save_won"] < rc["totals"]["save_won"]
    ai = c.call("POST", "/api/recommend/ai", {})
    yield "추천 순서 요약(규칙 기반)", ai["text"].startswith("먼저 할 일") and ai["mode"]
    iv = c.call("GET", "/api/invest")
    yield "투자 효과: 사업별 회수 기간·수명 순이익·판정", len(iv["plans"]) > 5 and all(p["verdict"] for p in iv["plans"]) and iv["roadmap"]["steps"]
    yield "점검표에서 이미 하고 있는 것(복지관 LED)은 놓친 것에서 빠짐", iv["gaps"]["has"] and not any(g["fac"] == "복지관" and "LED" in g["item"] for g in iv["gaps"]["items"])
    rv = {v["fac"]: v for v in iv["reviews"]}
    yield "사후 평가: 효과 있는 LED와 효과 미흡 LED를 가름", rv["문화예술회관"]["verdict"] == "효과 있음" and rv["본부 청사"]["verdict"] == "효과 미흡"
    yield "효과 미흡 사업의 같은 종류 다음 단계는 '사후 확인 먼저'", any(p["fac"] == "본부 청사" and p["key"] == "led" and p["verdict"] == "사후 확인 먼저" for p in iv["plans"])
    ai = c.call("POST", "/api/invest/ai", {})
    yield "투자 효과 종합 분석(규칙 기반)", ai["text"].startswith("먼저 할 사업")
    try:
        c.call("POST", "/api/invest/assume", {"set": {"led": {"rate": 5}}})
        bad = False
    except Exception:
        bad = True
    iv2 = c.call("POST", "/api/invest/assume", {"set": {"led": {"subsidy": 0.6}}})
    led = lambda d: sorted(p["net"] for p in d["plans"] if p["key"] == "led")
    yield "가정값 범위 검사·지원율 바꾸면 순투자 줄어듦", bad and led(iv2) < led(iv)
    c.call("POST", "/api/invest/assume", {"reset": True})
    r = c.call("POST", "/api/reset", {})
    yield "자료 비우기 후에도 동작", r["overview"]["latest"] is None and r["findings"] == []
    yield "원본 보호(sample_data 파일 수 그대로)", sum(1 for p in (ROOT / "sample_data").rglob("*") if p.is_file()) == 14


def kepco_checks(c, fk):
    c.call("POST", "/api/sample", {"with_bills": True})
    k = c.call("GET", "/api/kepco")
    cmp_ = k["cmp"] or {}
    yield f"한전 지역 비교: 키(주소째 넣어도)·서울 전체에서 도봉구 고르기·{cmp_.get('months')}개월 받기", k["info"]["key"] and not k["info"]["error"] and cmp_.get("range", [None, None])[1] == "2026-07"
    gym = next((f for f in cmp_.get("facilities", []) if f["fac"] == "종합체육센터"), None)
    yield "시설 단가를 지역 같은 계약종별 평균과 비교", gym and gym["cntr"] == "일반용" and gym["region_unit"] == 120.0 and gym["unit_gap_pct"] > 10
    yield "전년 대비 증감을 지역 추세(+3.0%)와 비교", gym and gym["region_yoy"] == 3.0 and gym["our_yoy"] is not None
    kr = c.call("GET", "/api/kepco?from=2025-01&to=2025-06")["cmp"]
    g2 = next(f for f in kr["facilities"] if f["fac"] == "종합체육센터")
    yield "분석 기간(2025-01~06)을 고르면 한전 비교도 그 기간으로", kr["range"] == ["2025-01", "2025-06"] and g2["from"] == "2025-01" and g2["to"] == "2025-06" and g2["months"] == 6
    yield "태양광 계약현황(도봉구 21개)", (k["solar"] or {}).get("cnt") == 21
    rc = c.call("GET", "/api/recommend")
    ext = [x for x in rc["items"] if x.get("ext")]
    yield f"절감 조치 추천에 한전 지역 통계 근거 {len(ext)}건", any(x["key"] == "요금구조" for x in ext)
    import json as _j
    sys.path.insert(0, str(ROOT))
    import kepco
    real = kepco.parse_contract(_j.loads((ROOT / "tests/fixtures/kepco_contract_sample.json").read_text(encoding="utf-8")), "도봉구")
    yield "실제 한전 응답 형식(숫자가 글자, '5호미만제거' 행) 읽기 · 도봉구 일반용 175.4원/kWh", \
        set(real) == {"교육용", "심 야", "일반용", "주택용"} and real["일반용"]["unit"] == 175.4 and real["일반용"]["cust"] == 12888
    op = c.call("GET", "/api/opinet")
    it = {x["name"]: x for x in (op["data"] or {}).get("items", [])}
    yield "국내 유가(오피넷) 자동 수집: 전국·서울 휘발유·경유·등유", op["key"] and not op["error"] and it.get("휘발유", {}).get("price") == 1667.33 \
        and it["경유"]["sido"] == 1602.22 and op["data"]["sido_name"] == "서울"
    m = len([s for s in fk.seen if s[0].endswith("avgAllPrice.do")])
    c.call("GET", "/api/opinet")
    yield "오피넷은 6시간에 한 번만 물음(하루 300회 한도)", len([s for s in fk.seen if s[0].endswith("avgAllPrice.do")]) == m
    n = len(fk.seen)
    c.call("GET", "/api/kepco")
    yield "받은 달은 다시 묻지 않음", len(fk.seen) == n
    ins = c.call("GET", "/api/insight")
    gi = next((x for x in ins["rows"] if x["fac"] == "종합체육센터"), {})
    yield f"외부 자료 종합 해석: 한전·오피넷 반영, 체육센터 판정 '{gi.get('verdict')}'", ins["src"]["kepco"] and ins["src"]["opinet"] \
        and gi.get("region_yoy") == 3.0 and gi.get("verdict") and len(ins["rows"]) == 5 and "오피넷" in "".join(ins["facts"])


def _sheets(data):
    import io
    from openpyxl import load_workbook
    return load_workbook(io.BytesIO(data)).sheetnames


def fake_kepco():
    """한전 전력데이터 개방포털 흉내(포털 API 가이드의 응답 형식). 2026-07까지만 공개된 것처럼 답한다."""
    import json as _j
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            H.seen.append((u.path, q))
            if u.path.endswith(("avgAllPrice.do", "avgSidoPrice.do")):  # 오피넷 흉내(안내서 응답 형식)
                ok = q.get("certkey") == "opi-test" and q.get("out") == "json"
                rows = [{"TRADE_DT": "20261105", "PRODCD": "B027", "PRODNM": "휘발유", "PRICE": "1667.33", "DIFF": "-0.23"},
                        {"TRADE_DT": "20261105", "PRODCD": "D047", "PRODNM": "자동차용경유", "PRICE": "1532.22", "DIFF": "+0.39"},
                        {"TRADE_DT": "20261105", "PRODCD": "C004", "PRODNM": "실내등유", "PRICE": "1295.23", "DIFF": "-0.72"}]
                if u.path.endswith("avgSidoPrice.do"):
                    rows = [{"SIDOCD": "01", "SIDONM": "서울", "PRODCD": r["PRODCD"], "PRICE": str(float(r["PRICE"]) + 70), "DIFF": "0"} for r in rows]
                body = {"RESULT": {"OIL": rows if ok else []}}
            elif q.get("apiKey") != "kepco-test":
                body = {"errMsg": "인증키 오류"}
            elif u.path.endswith("commonCode.do"):
                body = {"data": [{"uppoCd": "11", "uppoCdNm": "서울특별시", "codeTy": "cityCd", "code": "320", "codeNm": "도봉구"},
                                 {"uppoCd": "41", "uppoCdNm": "경기도", "codeTy": "cityCd", "code": "999", "codeNm": "도봉구"}]}
            elif u.path.endswith("contractType.do"):
                ym = f"{q['year']}-{q['month']}"
                grow = 1.03 if q["year"] >= "2025" and ym >= "2025-10" else 1.0
                if ym > "2026-07":  # 실제 포털: 아직 공개 안 된 달은 HTTP 404 {"errCd":"404","errMsg":"NotFound"}
                    b = _j.dumps({"errCd": "404", "errMsg": "NotFound"}).encode()
                    self.send_response(404)
                    self.send_header("Content-Length", str(len(b)))
                    self.end_headers()
                    self.wfile.write(b)
                    return
                body = {"data": [] if "cityCd" in q else [
                    {"year": q["year"], "month": q["month"], "metro": "서울특별시", "city": "도봉구", "cntr": c, "custCnt": n,
                     "powerUsage": round(kwh * grow), "bill": round(kwh * grow * unit), "unitCost": unit, "cntrPwr": 1000}
                    for c, n, kwh, unit in (("일반용", 9000, 40000000, 120.0), ("교육용", 150, 6000000, 110.0), ("주택용", 140000, 33000000, 130.0))]}
            elif u.path.endswith("renewEnergy.do"):
                body = {"data": [{"genSrc": "태양광", "metro": "서울", "city": "도봉구", "cnt": 21, "capacity": 685.66, "areaCnt": 411, "areaCapacity": 22247.64},
                                 {"genSrc": "태양광", "metro": "서울", "city": "강남구", "cnt": 18, "capacity": 1563.96, "areaCnt": 411, "areaCapacity": 22247.64}]}
            else:
                body = {"errMsg": "없는 주소"}
            b = _j.dumps(body, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    H.seen = []
    srv = HTTPServer(("127.0.0.1", 0), H)
    srv.seen = H.seen
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


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


def fake_llm():
    """OpenAI 호환 모델 서버 흉내(업스테이지 솔라 등). 받은 사실 첫 줄로 답하고, 질문에 '거짓'이 있으면 사실에 없는 숫자를 지어낸다."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            H.seen.append((self.headers.get("Authorization"), body))
            msgs = body["messages"]
            last = msgs[-1]["content"]
            facts = [x[2:] for x in last.splitlines() if x.startswith("- ")]
            sysmsg = msgs[0]["content"] if msgs[0]["role"] == "system" else ""
            if "거짓" in last:
                text = "추정 절감액은 987,654원입니다."
            elif "결론 및 건의" in sysmsg:
                text = "\n".join(f"- {f}" for f in facts[:3])
            elif "다듬어" in sysmsg:
                text = facts[0] if facts else ""
            else:
                text = f"<think>생각</think>정리하면 {facts[0] if facts else ''}"
            out = {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}
            b = json.dumps(out, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    H.seen = []
    s = HTTPServer(("127.0.0.1", 0), H)
    s.seen = H.seen
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def ai_checks(c, fl):
    info = c.call("GET", "/api/info")
    yield f"모델 연결 표시 {info['llm']}", info["llm"] == "openai:solar-pro4"
    r1 = c.call("POST", "/api/ask", {"q": "체육센터 8월 전기 왜 늘었어?"})
    yield "물어보기: 모델 답(생각 과정 태그 지움, 사실 대조 통과)", "모델" in r1["mode"] and "<think>" not in r1["answer"] and r1["answer"].startswith("정리하면")
    yield "모델 서버에 키를 Authorization 으로 보냄", fl.seen[-1][0] == "Bearer test-llm-key" and fl.seen[-1][1]["model"] == "solar-pro4"
    r2 = c.call("POST", "/api/ask", {"q": "그럼 가스는?", "history": [{"q": "체육센터 8월 전기 왜 늘었어?", "a": r1["answer"]}]})
    sent = fl.seen[-1][1]["messages"]
    yield f"이어 묻기: '그럼 가스는?'에 앞 질문의 '{r2['carried']}'를 이어받고 앞 대화를 모델에 전달", \
        r2["carried"] == "종합체육센터 8월" and any("가스" in f for f in r2["facts"]) and len(sent) == 4 and sent[1]["content"].startswith("체육센터")
    r3 = c.call("POST", "/api/ask", {"q": "우리 기관 에너지 상황 전반적으로 어때?"})
    yield "정해진 주제가 아닌 질문도 기관 전체 현황 사실로 모델이 답함", r3["general"] and "모델" in r3["mode"] and len(r3["facts"]) > 3
    r4 = c.call("POST", "/api/ask", {"q": "거짓 숫자 섞어서 말해봐"})
    yield "모델이 사실에 없는 숫자(987,654)를 쓰면 버리고 규칙 답", r4["mode"] == "규칙 기반" and "987654" in r4["dropped"].replace(",", "")
    fid = c.call("GET", "/api/state")["findings"][0]["id"]
    ex = c.call("POST", f"/api/findings/{fid}/explain")
    yield f"점검 결과 {fid} AI 해설", "모델" in ex["mode"] and ex["text"]
    po = c.call("POST", "/api/report/polish", {"text": "체육센터 8월 전기 사용량은 12,345kWh로 늘었음."})
    yield "보고서 문단 AI 다듬기(숫자 유지)", "모델" in po["mode"] and "12,345" in po["text"]
    co = c.call("POST", "/api/report/conclusion")
    yield f"AI 결론 및 건의 {len(co['lines'])}개 항목", "모델" in co["mode"] and len(co["lines"]) == 3 and not co["lines"][0].startswith("-")
    ia = c.call("POST", "/api/insight/ai")
    yield "AI 외부 자료 종합 해석(사실 대조 통과)", "모델" in ia["mode"] and c.call("GET", "/api/insight")["ai"]["text"] == ia["text"]
    sm = c.call("POST", "/api/summary")
    yield "AI 총평(사실 대조 통과)", "모델" in sm["mode"]


def start(env, port):
    proc = subprocess.Popen([sys.executable, "run.py", "--no-browser", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    c = Client(port)
    for _ in range(60):
        try:
            c.call("GET", "/api/info")
            break
        except Exception:
            time.sleep(0.3)
    return proc, c


def main():
    tmp = tempfile.mkdtemp(prefix="smoke-energy-")
    port = free_port()
    fake = fake_kma()
    fk = fake_kepco()
    env = dict(os.environ, KEPCO_API_KEY="https://bigdata.kepco.co.kr/openapi/v1/commonCode.do?codeTy=metroCd&apiKey=kepco-test",
               KEPCO_API_URL=f"http://127.0.0.1:{fk.server_port}/openapi/v1", OPINET_KEY="opi-test", OPINET_URL=f"http://127.0.0.1:{fk.server_port}/api", KEPCO_METRO="11", KEPCO_CITY="도봉구", APP_DATA_DIR=tmp, LLM_PROVIDER="none", KMA_APIHUB_KEY="test-key",
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
        for label, ok in list(checks(c)) + list(kepco_checks(c, fk)):
            total += 1
            failed += 0 if ok else 1
            print(f"  {'통과' if ok else '실패'}  {label}")
        fl = fake_llm()
        tmp2 = tempfile.mkdtemp(prefix="smoke-energy-ai-")
        proc2, c2 = start(dict(env, APP_DATA_DIR=tmp2, LLM_PROVIDER="openai", LLM_BASE_URL=f"http://127.0.0.1:{fl.server_port}/v1",
                               LLM_MODEL="solar-pro4", LLM_API_KEY="test-llm-key"), free_port())
        try:
            for label, ok in ai_checks(c2, fl):
                total += 1
                failed += 0 if ok else 1
                print(f"  {'통과' if ok else '실패'}  {label}")
        finally:
            proc2.terminate()
            shutil.rmtree(tmp2, ignore_errors=True)
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
