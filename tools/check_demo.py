"""시연영상 값 검증: 새 작업 폴더에서 시연용 모의자료로 앱을 띄워, 영상에 나오는 숫자·판정이 그대로 나오는지 확인한다.

실행: python tools/check_demo.py   (모델·인터넷·외부 키 없이, 결과는 검증결과/시연값_검증.md)
두 번 새로 띄워 두 번 모두 같은 값이 나오는지도 본다. 처리 시간은 PC마다 달라 여기서 보지 않는다(tools/measure.py).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from smoke_test import Client, free_port  # noqa: E402


def won(v):
    return f"{v / 1e8:.2f}억" if v >= 1e8 else f"{round(v / 1e4):,}만"


def run_once():
    tmp = tempfile.mkdtemp(prefix="demo-check-")
    port = free_port()
    env = dict(os.environ, APP_DATA_DIR=tmp, LLM_PROVIDER="none")
    for k in ("KMA_APIHUB_KEY", "DATA_GO_KR_KEY", "KEPCO_API_KEY", "OPINET_KEY"):
        env.pop(k, None)
    proc = subprocess.Popen([sys.executable, "run.py", "--no-browser", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    got = []
    try:
        c = Client(port)
        for _ in range(100):
            try:
                c.call("GET", "/api/info")
                break
            except Exception:
                time.sleep(0.2)
        s = c.call("POST", "/api/sample", {"with_bills": True, "reset": True})
        got.append(("1. 한눈에 보기", "넣은 내역 기간", "24개월", f"{len(s['months'])}개월"))
        got.append(("1. 한눈에 보기", "읽은 9월 고지서 PDF", "5장", f"{len([b for b in s['bills'] if not b['missing']])}장"))
        got.append(("1. 한눈에 보기 · 2. 점검 결과", "점검 결과", "7건", f"{len(s['findings'])}건"))
        ov = s["overview"]["totals"]
        kinds = {}
        for f in s["findings"]:
            kinds[f["type"]] = kinds.get(f["type"], 0) + (f.get("amount") or 0)
        got.append(("첫 화면 카드·도표", "찾은 금액(추정)·점검 건수", "4,492만원 · 7건", f"{won(ov['amount'])}원 · {ov['findings']}건"))
        got.append(("1. 한눈에 보기 · 도표", "유형별 찾은 금액(가스 초과·계약전력·전기 초과·휴관)", "1,291만 · 1,198만 · 994만 · 917만",
                    " · ".join(won(kinds.get(k, 0)) for k in ("gas_excess", "contract", "excess", "closure"))))
        got.append(("1. 한눈에 보기 · 도표", "시설별 전기 사용 비중 합계·종합체육센터", "4,356MWh · 40.7%",
                    f"{round(ov['kwh'] / 1000):,}MWh · {next(x['kwh'] for x in s['overview']['facilities'] if x['name'] == '종합체육센터') / ov['kwh'] * 100:.1f}%"))
        f1 = s["findings"][0]
        got.append(("2. 점검 결과", "첫 점검 결과 금액(종합체육센터 가스 1월)", "12,907,650원", f"{f1['amount']:,}원"))
        e = next(x for x in s["effects"] if "LED" in x["title"])
        got.append(("추천 절감 조치", "LED 교체 실측 효과(기온 보정)", "-8.2% · 52,134kWh · 751만 원 · 24tCO2eq",
                    f"-{e['saved_pct']:g}% · {e['saved_kwh']:,}kWh · {won(e['saved_won'])} 원 · {e['co2_t']:g}tCO2eq"))
        rc = c.call("GET", "/api/recommend")["totals"]
        got.append(("추천 절감 조치", "추천 조치 수·예상 절감액·목표까지 남은 양", "10건 · 4,492만 원 · 218,595kWh",
                    f"{rc['count']}건 · {won(rc['save_won'])} 원 · {rc['target_gap_kwh']:,}kWh"))
        ins = c.call("GET", "/api/insight")
        got.append(("외부 자료 종합 해석", "냉방도일·난방도일(최근 12개월)", "898.1 · 2609", f"{ins['weather']['cdd']:g} · {ins['weather']['hdd']:g}"))
        fx = {x.split(":")[0]: x for x in ins["facts"]}
        got.append(("외부 자료 종합 해석", "종합체육센터 전년 대비·기온 보정 후·날씨 몫", "+4.7% · +3.2% · +1.5%",
                    " · ".join(fx["종합체육센터"].split(": ")[1].split(" → ")[0].replace("전년 대비 ", "").replace("기온 보정 후 ", "").replace("날씨로 설명되는 몫 ", "").split(", "))))
        got.append(("외부 자료 종합 해석", "외부 키 없을 때 기상청·한전·오피넷", "없음(넣은 기온 자료 사용)",
                    "없음(넣은 기온 자료 사용)" if not any(ins["src"].values()) and ins["weather"]["src"] == "넣은 기온 자료" else str(ins["src"])))
        q = c.call("POST", "/api/ask", {"q": "체육센터 8월 전기 왜 늘었어?"})
        got.append(("이어 묻기", "8월 전기(올해·전년)", "198,178kWh · 160,007kWh",
                    " · ".join(x for x in ("198,178kWh", "160,007kWh") if x in q["answer"]) or "답에 없음"))
        q2 = c.call("POST", "/api/ask", {"q": "그럼 9월은?", "history": [{"q": "체육센터 8월 전기 왜 늘었어?", "a": q["answer"]}]})
        got.append(("이어 묻기", "'그럼 9월은?'이 앞 질문의 시설을 이어받음", "종합체육센터", "종합체육센터" if "종합체육센터" in q2["carried"] + q2["answer"] else q2["carried"] or "-"))
        bt = s["budget"]["totals"]
        got.append(("4. 예산 관리", "현 예산·집행률·연말 예상·남는 돈", "14.04억 · 71.3% · 13.92억 · 1,210만",
                    f"{won(bt['current'])} · {bt['exec_pct']:g}% · {won(bt['expected'])} · {won(bt['remain'])}"))
        got.append(("4. 예산 관리", "부족·불용 예상 항목", "부족 1 · 불용 1", f"부족 {bt['short']} · 불용 {bt['unused']}"))
        pv = c.call("GET", "/api/report/preview")["blocks"]
        got.append(("보고서 미리보기", "보고서 제목", "에너지 사용 점검 보고서 (2025-10~2026-09)", next(b[1] for b in pv if b[0] == "title")))
        sc = json.loads((ROOT / "검증결과" / "scorecard.json").read_text(encoding="utf-8"))
        b = next(x for x in sc["areas"] if x["name"].startswith("고지서 읽기"))
        got.append(("끝 화면", "고지서 정확히 읽음", "40/40장", f"{b['passed']}/{b['total']}장"))
    finally:
        proc.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    return got


def main():
    subprocess.run([sys.executable, "tools/eval.py"], cwd=ROOT, stdout=subprocess.DEVNULL, check=True)
    r1, r2 = run_once(), run_once()
    ok = sum(1 for g in r1 if g[2] == g[3])
    same = all(a[3] == b[3] for a, b in zip(r1, r2))
    rows = "\n".join(f"| {s} | {n} | {e} | {g} | {'일치' if e == g else '**다름**'} |" for s, n, e, g in r1)
    md = f"""# 한톨 시연영상 값 검증

- 측정일: {date.today().isoformat()} · 재현: `python tools/check_demo.py` (모델·인터넷·외부 키 불필요)
- 방법: 빈 작업 폴더에서 시연용 모의자료(시설 5곳 24개월 + 9월 한전 고지서 PDF 5장)를 넣고, 화면과 같은 API를 불러 영상에 나오는 값과 비교. 두 번 새로 띄워 같은 값이 나오는지도 확인.
- 결과: **{ok}/{len(r1)} 일치**, 두 번 실행 결과 {'같음' if same else '**다름**'}

| 영상 장면 | 항목 | 영상에 나온 값 | 다시 돌린 값 | 비교 |
|---|---|---|---|---|
{rows}

- 이 영상은 외부 키 없이 찍어 기상청·한전·오피넷 값이 '없음'이고, 날씨 몫은 모의자료에 든 월평균기온으로 계산합니다. 키를 넣으면 실시간 공공데이터로 바뀌어 이 표의 값과 달라질 수 있습니다.
- 처리 시간은 PC마다 다르므로 `python tools/measure.py`(검증결과/실측.md)로 따로 잽니다.
"""
    (ROOT / "검증결과" / "시연값_검증.md").write_text(md, encoding="utf-8")
    print(md)
    sys.exit(0 if ok == len(r1) and same else 1)


if __name__ == "__main__":
    main()
