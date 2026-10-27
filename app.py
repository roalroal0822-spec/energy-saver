"""에너지 절감 비서 웹 서버."""
import base64
import shutil
import uuid
from datetime import date
from pathlib import Path

from core import webcommon
from core.config import Config
from core.export import export
from core.llm import LLM, LLMError
from core.loaders import SUPPORTED, load_folder
from core.server import App, HttpError, file_response
from core.sources import safe_parts
from core.store import Store

import analysis
import ingest
import qa

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_data"

ENERGY = {
    "cdd_base": 18.0,  # 냉방도일 기준온도(℃)
    "hdd_base": 18.0,  # 난방도일 기준온도(℃)
    "closure_expected_ratio": 0.35,  # 휴관일에 평소 대비 쓰는 비율(필수 설비만 가동 가정)
    "contract_margin": 0.2,  # 계약전력 검토안 여유율
    # 전력 온실가스 배출계수(tCO2eq/MWh = kgCO2eq/kWh). 기관이 쓰는 고시값으로 바꿔 쓰세요
    "co2_per_kwh": 0.4594,
    # 시간대별 전력량 단가(원/kWh: 경부하, 중간부하, 최대부하). 모의값이므로 기관 계약종별 단가로 바꿔 쓰세요
    "tariff": {"summer": [105, 160, 240], "spring": [100, 125, 155], "winter": [110, 155, 205]},
}

cfg = Config(HERE, {"app_title": "에너지 절감 비서", "port": 8104, "energy": ENERGY})
llm = LLM(cfg["llm"])
store = Store(cfg.data_dir)
SRC = store.path("source")
app = App(HERE / "static")
webcommon.register(app, cfg, llm, "에너지 절감 비서", VERSION)

STATE = {"an": None, "bills": [], "ds": None}


def log(action, detail=""):
    store.append_log("history.jsonl", {"action": action, "detail": detail})


def _copy_sample(with_bills):
    for p in SAMPLE.rglob("*"):
        if p.is_file() and (with_bills or "고지서" not in p.parts):
            dst = SRC / "모의자료" / p.relative_to(SAMPLE)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)


def user_actions():
    return store.read("actions.json", {"actions": []})["actions"]


def rebuild():
    SRC.mkdir(parents=True, exist_ok=True)
    docs, skipped = load_folder(SRC)
    ds, bills = ingest.build(docs, llm if llm.enabled else None)
    for s in skipped:
        ds.warnings.append(f"{s['file']}: {s['reason']}")
    acts = ds.actions + user_actions()
    an = analysis.Analysis(ds, cfg, acts).run()
    STATE.update(an=an, bills=bills, ds=ds)
    return an


def an():
    if STATE["an"] is None:
        if not any(SRC.rglob("*")) if SRC.exists() else True:
            _copy_sample(with_bills=False)
        rebuild()
    return STATE["an"]


# ------------------------------------------------------------ 자료

@app.get("/api/state")
def get_state(req):
    a = an()
    return {"overview": a.overview(), "findings": a.findings, "effects": a.effects(),
            "data": a.ds.summary(), "bills": STATE["bills"], "llm": llm.enabled}


@app.get("/api/series")
def get_series(req):
    a = an()
    fac, kind = req.query.get("fac"), req.query.get("kind", "elec")
    rows = a.series.get((fac, kind))
    if rows is None:
        raise HttpError(404, "그 시설의 자료가 없습니다")
    m = a.models.get((fac, kind))
    return {"rows": rows, "model": m, "baseline": a.baseline, "recent": a.recent,
            "actions": [x for x in a.actions if x["fac"] == fac]}


@app.post("/api/sample")
def load_sample(req):
    b = req.json or {}
    if b.get("reset"):
        store.remove_tree("source")
    _copy_sample(bool(b.get("with_bills")))
    log("시연용 모의자료 불러오기", "고지서 포함" if b.get("with_bills") else "엑셀 내역")
    rebuild()
    return get_state(req)


@app.post("/api/upload")
def upload(req):
    files = (req.json or {}).get("files") or []
    if not files:
        raise HttpError(400, "올릴 파일이 없습니다")
    saved = []
    for f in files:
        try:
            parts = safe_parts(f.get("path") or "")
        except ValueError:
            continue
        if Path(parts[-1]).suffix.lower() not in SUPPORTED:
            raise HttpError(400, f"읽을 수 없는 형식입니다: {parts[-1]} (엑셀·PDF·한글·워드·CSV)")
        dst = SRC.joinpath("올린 자료", *parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(base64.b64decode(f["b64"]))
        saved.append("/".join(parts))
    log("자료 올리기", ", ".join(saved))
    rebuild()
    return get_state(req)


@app.post("/api/reset")
def reset(req):
    store.remove_tree("source")
    SRC.mkdir(parents=True, exist_ok=True)
    log("자료 비우기")
    rebuild()
    return get_state(req)


# ------------------------------------------------------------ 질문·요약

@app.post("/api/ask")
def ask(req):
    q = ((req.json or {}).get("q") or "").strip()
    if not q:
        raise HttpError(400, "질문을 입력하세요")
    r = qa.answer(an(), q, llm)
    log("질문", q)
    return r


def rule_summary(a):
    ov = a.overview()
    t = ov["totals"]
    lines = [f"최근 12개월({ov['recent'][0]}~{ov['recent'][1]}) 시설 {len(ov['facilities'])}곳의 전기 사용량은 {t['kwh']:,}kWh, 전기·가스 요금은 {t['cost']:,}원입니다."]
    if a.findings:
        lines.append(f"점검 기준에 걸린 항목은 {t['findings']}건이며, 항목별로 계산한 금액은 모두 {t['amount']:,}원(추정)입니다.")
        top = [f for f in a.findings if f["level"] == "bad"][:3]
        if top:
            lines.append("먼저 볼 것: " + "; ".join(f["title"] for f in top) + ".")
    else:
        lines.append("점검 기준에 걸린 항목이 없습니다.")
    for e in a.effects():
        if e.get("saved_pct") is not None:
            lines.append(f"{e['fac']} '{e['title']}'은 시행 후 {e['months']}개월 동안 기온을 감안해 {e['saved_pct']}% 줄었습니다.")
    return " ".join(lines)


@app.post("/api/summary")
def summary(req):
    a = an()
    text = rule_summary(a)
    res = {"text": text, "mode": "규칙 기반", "dropped": ""}
    if llm.enabled:
        facts = [text] + [f"{f['title']}: {f['summary']}" for f in a.findings]
        try:
            out = llm.chat([{"role": "system", "content": "공공기관 에너지 관리 보고서의 '총평'을 한국어 4~6문장으로 쓰세요. 아래 사실에 있는 숫자와 내용만 쓰고, 우선순위와 다음 할 일을 말하세요."},
                            {"role": "user", "content": "\n".join(f"- {x}" for x in facts)}], max_tokens=700)
            allowed = qa._nums("\n".join(facts)) | {float(i) for i in range(0, 13)}
            extra = [n for n in qa._nums(out) if n not in allowed and round(n) not in allowed]
            if extra:
                res["dropped"] = f"모델 총평에 사실에 없는 숫자({', '.join(str(x) for x in extra[:3])})가 있어 규칙 총평을 씁니다"
            else:
                res.update(text=out.strip(), mode="모델(사실 대조 통과)")
        except LLMError as e:
            res["dropped"] = f"모델 응답 실패({e})"
    STATE["summary"] = res
    return res


# ------------------------------------------------------------ 조치

@app.post("/api/actions")
def add_action(req):
    b = req.json or {}
    a = an()
    f = next((x for x in a.findings if x["id"] == b.get("finding")), None)
    fac = b.get("fac") or (f["fac"] if f else "")
    title = (b.get("title") or "").strip()
    if not fac or not title:
        raise HttpError(400, "시설과 조치 내용을 입력하세요")
    d = b.get("date") or date.today().isoformat()
    try:
        date.fromisoformat(d)
    except ValueError:
        raise HttpError(400, "시행일은 2026-10-27 형식으로 입력하세요")
    acts = user_actions()
    item = {"id": "U" + uuid.uuid4().hex[:6], "fac": fac, "title": title, "kind": b.get("kind") or (f["type"] if f else ""),
            "date": d, "status": b.get("status") or "예정", "owner": b.get("owner") or (f["owner"] if f else ""),
            "finding": f["title"] if f else "", "origin": "user", "src": None}
    acts.append(item)
    store.write("actions.json", {"actions": acts})
    log("조치 등록", f"{fac} {title}")
    rebuild()
    return item


@app.post("/api/actions/<aid>")
def update_action(req):
    b = req.json or {}
    acts = user_actions()
    for x in acts:
        if x["id"] == req.params["aid"]:
            if b.get("status") in ("예정", "진행", "완료"):
                x["status"] = b["status"]
            if b.get("date"):
                date.fromisoformat(b["date"])
                x["date"] = b["date"]
            store.write("actions.json", {"actions": acts})
            log("조치 상태 변경", f"{x['fac']} {x['title']} → {x['status']}")
            rebuild()
            return x
    raise HttpError(404, "파일에서 읽은 조치는 원본 파일에서 고쳐 주세요")


@app.delete("/api/actions/<aid>")
def del_action(req):
    acts = [x for x in user_actions() if x["id"] != req.params["aid"]]
    store.write("actions.json", {"actions": acts})
    rebuild()
    return {"ok": True}


# ------------------------------------------------------------ 문서

def _cite_text(s):
    return f"{s['file']} {s['loc']}" if s else ""


def _basis_text(b):
    return f"{b['reg']} {b['label']}({b['title']})" if b else ""


@app.post("/api/findings/<fid>/request")
def request_doc(req):
    a = an()
    f = next((x for x in a.findings if x["id"] == req.params["fid"]), None)
    if not f:
        raise HttpError(404, "점검 결과를 찾지 못했습니다")
    today = date.today().isoformat()
    blocks = [("title", "에너지 사용 점검 요청"),
              ("table", ["수신", "발신", "요청일"], [[f["owner"] or "시설 담당", "에너지 관리 담당", today]], [3, 3, 2]),
              ("h2", "1. 점검 대상"), ("p", f"**{f['title']}**"), ("p", f["summary"])]
    if f.get("note"):
        blocks.append(("note", "※ " + f["note"]))
    if f["evidence"]:
        blocks += [("h2", "2. 근거 자료"), ("table", ["내용", "출처"], [[e["label"], _cite_text(e.get("src"))] for e in f["evidence"]], [5, 3])]
    if f.get("amount"):
        blocks += [("p", f"{f['amount_label']}: **{f['amount']:,}원** (계산: {f['formula']})")]
    blocks += [("h2", "3. 확인·조치 요청 사항")] + [("bullet", s) for s in f["steps"]]
    bs = [b for b in f["basis"] if b]
    if bs:
        blocks += [("h2", "4. 관련 기준")] + [("p", f"{_basis_text(b)}: {b['text']}") for b in bs]
    blocks += [("p", "점검 결과와 조치 계획(시행일 포함)을 회신해 주시면 에너지 절감 비서에 등록하여 효과를 측정하겠습니다.")]
    fmt = req.query.get("fmt", "hwpx")
    data, ctype = export(blocks, fmt)
    log("점검 요청서 만들기", f["title"])
    return file_response(data, f"점검요청_{f['fac'] or '전체'}_{f['id']}.{fmt}", ctype)


@app.post("/api/report")
def report(req):
    a = an()
    ov = a.overview()
    s = STATE.get("summary") or {"text": rule_summary(a), "mode": "규칙 기반"}
    t = ov["totals"]
    blocks = [("title", f"에너지 사용 점검 보고서 ({ov['recent'][0]}~{ov['recent'][1]})"),
              ("p", f"작성일 {date.today().isoformat()} · 기준선 {ov['baseline'][0]}~{ov['baseline'][1]}(기온 보정) · 작성: 에너지 절감 비서({s['mode']})"),
              ("h1", "1. 총평"), ("p", s["text"]),
              ("h1", "2. 시설별 현황(최근 12개월)"),
              ("table", ["시설", "전기(kWh)", "전년 대비", "기온 보정 후", "면적당(kWh/㎡)", "전기·가스 요금(원)", "점검 결과"],
               [[f["name"], f"{f['kwh']:,}", _pct(f["change_pct"]), _pct(f["norm_pct"]), f["kwh_per_m2"] or "",
                 f"{f['cost']:,}", f"{f['findings']}건"] for f in ov["facilities"]], [3, 2, 2, 2, 2, 3, 1]),
              ("p", f"합계: 전기 {t['kwh']:,}kWh, 요금 {t['cost']:,}원, 전기 사용 온실가스 {t['co2_t']:,}tCO2eq(배출계수 {ov['co2_factor']} kgCO2eq/kWh, 설정값)"),
              ("h1", "3. 점검 결과"),
              ("table", ["번호", "구분", "내용", "금액(원)", "담당"],
               [[f["id"], {"bad": "조치 필요", "warn": "검토", "gray": "설정 확인"}.get(f["level"], ""), f["title"],
                 f"{f['amount']:,}" if f.get("amount") else "", f["owner"]] for f in a.findings], [1, 1, 6, 2, 2])]
    for f in a.findings:
        blocks += [("h3", f"{f['id']}. {f['title']}"), ("p", f["summary"])]
        if f.get("amount"):
            blocks.append(("p", f"{f['amount_label']} {f['amount']:,}원 = {f['formula']}"))
        blocks += [("bullet", st) for st in f["steps"]]
        bs = [b for b in f["basis"] if b]
        if bs:
            blocks.append(("note", "근거: " + ", ".join(_basis_text(b) for b in bs)))
    eff = a.effects()
    if eff:
        blocks += [("h1", "4. 절감 조치 이행·효과"),
                   ("table", ["시설", "조치", "시행일", "상태", "효과(기온 보정)"],
                    [[e["fac"], e["title"], e["date"], e["state"],
                      f"{e['saved_kwh']:,}kWh ({e['saved_pct']}%), {e['saved_won']:,}원, {e['co2_t']}tCO2eq ({e['from']}~{e['to']})" if e.get("saved_kwh") is not None else ""]
                     for e in eff], [2, 4, 2, 2, 5])]
    blocks += [("h1", "5. 점검 기준과 계산 방법"),
               ("table", ["기준", "값", "근거"], [[r["label"], f"{r['value']:g}", _basis_text(r["basis"]) if r["basis"] else "기본값(설정)"] for r in ov["rules"]], [4, 1, 4]),
               ("p", "기온 보정: 기준 기간 12개월의 월 사용량을 '기저 + 냉방계수×냉방도일 + 난방계수×난방도일'로 맞추고, 이후 달의 기온만 감안한 예상치와 실제를 비교합니다(도일은 월평균기온으로 근사)."),
               ("note", "※ 금액은 고지서 단가와 설정값으로 계산한 추정치이며, 계약전력·요금 정정은 공급사 확인 후 확정됩니다.")]
    fmt = req.query.get("fmt", "hwpx")
    data, ctype = export(blocks, fmt)
    log("보고서 만들기", fmt)
    return file_response(data, f"에너지사용점검보고서_{ov['recent'][1]}.{fmt}", ctype)


def _pct(v):
    return "" if v is None else f"{v:+.1f}%"


@app.get("/api/log")
def get_log(req):
    return {"items": list(reversed(store.read_log("history.jsonl")))[:200]}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="에너지 절감 비서 실행")
    ap.add_argument("--port", type=int, default=cfg["port"])
    ap.add_argument("--host", default=cfg["host"])
    ap.add_argument("--no-browser", action="store_true")
    ar = ap.parse_args()
    an()
    app.run(ar.host, ar.port, not ar.no_browser, "에너지 절감 비서")


if __name__ == "__main__":
    main()
