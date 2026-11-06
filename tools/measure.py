"""실측: 이 PC에서 앱을 띄워 시연 자료로 실제 화면과 같은 API를 불러 처리 시간과 산출량을 잰다.

실행: python tools/measure.py   (모델·인터넷 불필요, 결과는 검증결과/실측.md·measure.json)
사람이 손으로 고지서를 옮겨 적고 분석·보고서를 쓰는 시간은 이 스크립트가 잴 수 없으므로 적지 않는다.
"""
import base64
import json
import os
import platform
import shutil
import statistics as st
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
from smoke_test import Client, free_port  # noqa: E402
from eval import QUESTIONS, HOLDOUT  # noqa: E402

N = 5  # 자료 불러오기·보고서 반복 횟수


def timed(fn):
    t = time.perf_counter()
    r = fn()
    return r, (time.perf_counter() - t) * 1000


def stat(v):
    return {"median": round(st.median(v), 1), "max": round(max(v), 1), "mean": round(st.mean(v), 1), "n": len(v)}


def main():
    tmp = tempfile.mkdtemp(prefix="measure-energy-")
    port = free_port()
    env = dict(os.environ, APP_DATA_DIR=tmp, LLM_PROVIDER="none")
    for k in ("KMA_APIHUB_KEY", "DATA_GO_KR_KEY", "KEPCO_API_KEY", "OPINET_KEY"):
        env.pop(k, None)
    proc = subprocess.Popen([sys.executable, "run.py", "--no-browser", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = {k: [] for k in ("load", "bill", "report_hwpx", "budget_hwpx", "xlsx", "ask")}
    try:
        c = Client(port)
        for _ in range(100):
            try:
                c.call("GET", "/api/info")
                break
            except Exception:
                time.sleep(0.2)
        for _ in range(N):
            s, ms = timed(lambda: c.call("POST", "/api/sample", {"with_bills": True, "reset": True}))
            t["load"].append(ms)
        data = s["data"]
        months = len(s["months"])
        bills_read = len([b for b in s["bills"] if not b["missing"]])
        for pdf in sorted((ROOT / "sample_data" / "고지서").glob("*.pdf")):
            b64 = base64.b64encode(pdf.read_bytes()).decode()
            _, ms = timed(lambda: c.call("POST", "/api/upload", {"files": [{"path": pdf.name, "b64": b64}], "fac": "측정용 시설"}))
            t["bill"].append(ms)
        c.call("POST", "/api/sample", {"with_bills": True, "reset": True})
        for _ in range(N):
            t["report_hwpx"].append(timed(lambda: c.call("POST", "/api/report?fmt=hwpx", {}, raw=True))[1])
            t["budget_hwpx"].append(timed(lambda: c.call("POST", "/api/budget/report?fmt=hwpx", {"save_pct": 0}, raw=True))[1])
            t["xlsx"].append(timed(lambda: c.call("POST", "/api/export-xlsx", {}, raw=True))[1])
        for q, _ in QUESTIONS + HOLDOUT:
            t["ask"].append(timed(lambda: c.call("POST", "/api/ask", {"q": q}))[1])
        pv = c.call("GET", "/api/report/preview")["blocks"]
    finally:
        proc.terminate()
        shutil.rmtree(tmp, ignore_errors=True)

    out = {"date": date.today().isoformat(), "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
           "months": months, "findings": len(s["findings"]), "bills_read": bills_read, "report_blocks": len(pv),
           "data": data, **{k: stat(v) for k, v in t.items()}}
    (ROOT / "검증결과").mkdir(exist_ok=True)
    (ROOT / "검증결과" / "measure.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    f = lambda k: f"{out[k]['median']:g}ms | {out[k]['max']:g}ms | {out[k]['n']}회"  # noqa: E731
    md = f"""# 한톨 실측 결과

- 측정일: {out['date']} · 재현: `python tools/measure.py` (모델·인터넷 불필요)
- 측정 환경: {out['machine']} (개발용 리눅스 컨테이너, 기관 PC에서 다시 재면 숫자가 달라질 수 있음)
- 대상: 시연용 모의자료(시설 5곳, {out['months']}개월 전기·가스 내역 + 9월 한전 고지서 PDF 5장)

| 항목 | 중앙값 | 최대 | 횟수 |
|---|---|---|---|
| 자료 전체 불러오기·분석(내역 읽기, 고지서 PDF {out['bills_read']}장 읽기, 기온 보정, 점검 {out['findings']}건, 예산) | {f('load')} |
| 고지서 PDF 1장 넣고 다시 분석 | {f('bill')} |
| 분석 보고서 한글(HWPX) 만들기 | {f('report_hwpx')} |
| 예산 집행 관리·편성 근거 한글(HWPX) 만들기 | {f('budget_hwpx')} |
| 점검 결과·월 내역·예산 엑셀 만들기 | {f('xlsx')} |
| 물어보기 1건(규칙 답, 성적표 질문 60개) | {f('ask')} |

- 자동으로 만든 것: 점검 결과 {out['findings']}건(근거 행·계산식 포함), 보고서 블록 {out['report_blocks']}개.
- 처리 시간은 서버 응답까지 잰 값(화면 그리기 제외)입니다. 모델(솔라)·외부 API(기상청·한전·오피넷)를 켜면 그 응답 시간만큼 더 걸립니다.
- **사람이 고지서를 옮겨 적고 분석·보고서를 쓰는 시간은 재지 않았습니다.** 비교하려면 기존 방식으로 걸린 시간을 기관에서 재야 합니다.
"""
    (ROOT / "검증결과" / "실측.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
