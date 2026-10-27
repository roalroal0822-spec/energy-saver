"""모델 교체 검증: 같은 기능을 여러 모델로 돌려 정상 동작하는지 확인하고 보고서를 남긴다.

예)
  python tools/verify_models.py --base-url http://localhost:11434/v1 --models gemma3:4b exaone3.5:2.4b
  python tools/verify_models.py --base-url https://.../v1 --models 모델A 모델B   (키는 환경변수 LLM_API_KEY)
  python tools/verify_models.py --mock      (가짜 모델 서버로 연결 경로만 시험)

결과: verify_report.md
'통과'는 오류 없이 응답했다는 뜻만이 아니라 결과가 모의자료의 정답과 맞는다는 뜻이다.
  - 고지서 읽기: 항목 이름이 다른 고지서에서 모델이 사용량·청구금액을 찾아, 원문 숫자와 맞아야 통과
  - 질문 답변: 모델 답이 숫자 대조를 통과하고, 핵심 수치(+19.3%)를 담아야 통과
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ODD_BILL = """전력 사용 요금 안내 (모의)
사용장소 공공기관 복지관
청구 대상 2026년 9월
당월 사용량 합계 33,715kWh
이번 달 납부하실 금액 5,896,591원
"""

CFG = {"energy": {"cdd_base": 18.0, "hdd_base": 18.0, "closure_expected_ratio": 0.35, "contract_margin": 0.2, "co2_per_kwh": 0.4594,
                  "tariff": {"summer": [105, 160, 240], "spring": [100, 125, 155], "winter": [110, 155, 205]}}}


def make_llm(provider, base, model):
    from core.llm import LLM
    return LLM({"provider": provider, "base_url": base, "model": model, "api_key_env": "LLM_API_KEY",
                "temperature": 0.1, "timeout": 300})


def analysis_once():
    import analysis
    import ingest
    from core.loaders import load_folder
    docs, _ = load_folder(ROOT / "sample_data")
    ds, _ = ingest.build(docs)
    return analysis.Analysis(ds, CFG).run()


def checks(llm, an):
    import ingest
    import qa
    from core.loaders import Block, Document
    res = []
    t = time.time()
    doc = Document(id="odd", name="odd.pdf", relpath="odd.pdf", ext=".pdf",
                   blocks=[Block(f"odd-{i}", "odd", "odd.pdf", f"p.1 {i + 1}줄", l) for i, l in enumerate(ODD_BILL.strip().splitlines())])
    ds = ingest.Dataset()
    ds.facilities["복지관"] = {"name": "복지관"}
    rec, _, missing, by_model = ingest.parse_bill(doc, ds, llm)
    ok = rec.get("kwh") == 33715 and rec.get("total") == 5896591 and not missing
    res.append(("고지서 읽기: 항목 이름이 다른 고지서(모델 + 원문 숫자 대조)", ok, time.time() - t,
                f"모델이 찾은 항목 {by_model}, 사용량 {rec.get('kwh')}, 청구금액 {rec.get('total')}, 못 찾음 {missing}"))
    t = time.time()
    a = qa.answer(an, "체육센터 8월 전기 왜 늘었어?", llm)
    if llm.cfg.get("model") == "mock-b":  # 지어낸 숫자를 넣는 가짜 모델: 앱이 걸러 내면 통과
        ok, name = a["mode"] == "규칙 기반" and "사실에 없는 숫자" in a["dropped"], "질문 답변: 지어낸 숫자 차단"
    else:
        ok, name = a["mode"].startswith("모델") and "19.3" in a["answer"], "질문 답변: 사실 목록에서만 답하기(숫자 대조)"
    res.append((name, ok, time.time() - t,
                (a["dropped"] or a["answer"][:70]).replace("\n", " ")))
    return res


def main():
    ap = argparse.ArgumentParser(description="여러 모델로 주요 기능을 돌려 보는 검증 도구")
    ap.add_argument("--provider", default="openai", choices=["openai", "ollama"])
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--models", nargs="+", default=[])
    ap.add_argument("--mock", action="store_true", help="가짜 모델 서버로 연결 경로만 시험")
    ap.add_argument("--out", default=str(ROOT / "verify_report.md"))
    a = ap.parse_args()
    proc = None
    if a.mock:
        port = 18080
        proc = subprocess.Popen([sys.executable, str(ROOT / "tools" / "mock_llm.py"), "--port", str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        a.base_url, a.models = f"http://127.0.0.1:{port}/v1", ["mock-a", "mock-b"]
    if not a.models:
        ap.error("--models 로 시험할 모델 이름을 2개 이상 주세요 (또는 --mock)")
    an = analysis_once()
    rows = []
    try:
        for m in a.models:
            llm = make_llm(a.provider, a.base_url, m)
            h = llm.health()
            rows.append((m, "연결 확인" + (f" [{h['family']}]" if h.get("family") else ""), h["ok"], h.get("seconds", 0), h.get("detail", "")))
            if not h["ok"]:
                continue
            try:
                for name, ok, sec, note in checks(llm, an):
                    rows.append((m, name, ok, sec, note))
            except Exception as e:
                rows.append((m, "기능 시험", False, 0, f"{type(e).__name__}: {e}"))
    finally:
        if proc:
            proc.terminate()
    lines = ["# 모델 교체 검증 보고서 (에너지 절감 비서)", "",
             f"- 실행 시각: {time.strftime('%Y-%m-%d %H:%M')}",
             f"- 연결 방식: {a.provider} / 주소: {a.base_url}",
             f"- 모델: {', '.join(a.models)}" + (" (가짜 모델 서버: 연결 경로 시험용이며 실제 모델 성능과 무관. mock-b는 답에 지어낸 숫자를 일부러 넣으며, 앱이 그 답을 버리고 규칙 답을 쓰면 통과)" if a.mock else ""), "",
             "| 모델 | 기능 | 결과 | 시간(초) | 비고 |", "|---|---|---|---|---|"]
    for m, name, ok, sec, note in rows:
        lines.append(f"| {m} | {name} | {'통과' if ok else '실패'} | {sec:.1f} | {str(note).replace('|', '/')} |")
    passed = sum(1 for r in rows if r[2])
    lines += ["", f"통과 {passed} / {len(rows)}"]
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n보고서: {a.out}")
    sys.exit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()
