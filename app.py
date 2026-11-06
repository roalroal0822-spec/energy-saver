"""에너지 절감 비서 웹 서버."""
import base64
import shutil
import time
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
from core.pii import mask
from core.store import Store
from core.xlsx import CTYPE as XLSX, workbook

import analysis
import budget
import ingest
import kepco
import opinet
import kma
import qa
import recommend

VERSION = "1.1.0"
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
    # 예산 관리 가정값. 불용 판정 기준(%), 가스 단가의 유가·환율 연동 비율과 시차(개월), 전기 연료비 조정(연동 비율, kWh당 상·하한 원),
    # 내년 이상기온 시나리오 폭(여름 +℃, 겨울 -℃), 내년 요금 인상률(%). 모두 기관 사정에 맞게 바꿔 쓰세요
    "budget": {"unused_pct": 5, "gas_oil_pass": 0.6, "oil_lag": 3, "elec_oil_pass": 0.1, "elec_fuel_cap": 5,
               "climate_summer": 1.5, "climate_winter": 2.0, "elec_up_pct": 0, "gas_up_pct": 0},
}

cfg = Config(HERE, {"app_title": "한톨(에너지 절감 비서)", "port": 8104, "energy": ENERGY})
llm = LLM(cfg["llm"])
store = Store(cfg.data_dir)
SRC = store.path("source")
app = App(HERE / "static")
webcommon.register(app, cfg, llm, "한톨(에너지 절감 비서)", VERSION)
ABOUT = {
    "lead": "모든 판단은 규칙과 계산으로 하고, 모델은 고지서 읽기와 문장 다듬기만 돕습니다. 모델이 없어도 같은 결과가 나옵니다.",
    "pipeline": [("자료 읽기", "엑셀은 열 이름으로, 고지서 PDF는 항목 이름으로 읽고 모든 숫자에 파일·행을 붙임"),
                 ("기온 보정 기준선", "기준 기간 12개월로 '기저+냉방도일+난방도일' 모델을 맞추고 설명력·오차율 공개"),
                 ("기준값을 지침에서", "초과 10%·역률 90%·감축 목표 5% 같은 기준을 기관 지침 조문에서 읽음"),
                 ("점검 6종", "초과 사용·가스·휴관 미정지·계약전력·역률·요금 재계산, 결과마다 근거와 계산식"),
                 ("예산 관리", "예산서(본예산·추경)와 고지 금액으로 집행률, 남은 달은 기온 보정 모델로 예측해 불용·부족 판정"),
                 ("내년 편성 근거", "평년 기온 × 최근 사용 수준 × 단가 전망(유가·환율 연동·요금 인상), 이상기온 여유분은 따로"),
                 ("모델 보조(선택)", "못 읽은 고지서 칸, 답 문장 다듬기. 결과는 원문·사실과 대조"),
                 ("근거 카드", "답·보고서에 원본 행과 지침 조문 원문을 그대로 붙임")],
    "guards": ["판단(초과·미달·금액)은 모델이 하지 않고 계산으로만 합니다.",
               "모델이 고지서에서 찾은 숫자는 고지서 원문에 그대로 있어야 받습니다.",
               "모델 답·총평에 사실 목록에 없는 숫자가 하나라도 있으면 버리고 규칙 답을 씁니다.",
               "점검 기준값은 지침 조문에서 읽고, 지침에 없는 값은 '기본값'으로 따로 표시합니다.",
               "금액은 모두 '추정'으로 표시하고 계산식과 단가 출처(고지서·설정값)를 붙입니다.",
               "휴관한 달은 초과 사용·조치 효과 계산에서 빼고, 휴관 기준으로 따로 봅니다.",
               "예산 예측과 편성액은 계산식과 가정값(유가 연동 비율·기후 시나리오 등)을 모두 화면과 문서에 함께 적습니다.",
               "질문에 적은 개인정보(전화·주민번호·계좌 등)는 저장·모델 전달 전에 ○○○로 가립니다.",
               "원본 파일은 읽기만 하고, 작업 기록을 모두 남깁니다."],
    "data": ["시연 자료는 모두 지어낸 모의자료입니다(시설 5곳, 2024.10~2026.09). 실제 기관 자료로 바꾸면 그대로 동작합니다.",
             "월평균기온은 모의값입니다. 실제 사용 때는 '기온 자동 수집'을 켜면 기상청 지상관측(ASOS) 일자료로 바꿔 날마다 도일을 계산합니다(키 필요).",
             "요금 단가·배출계수는 설정값(config.json)이며 기관 계약종별 단가로 바꿔 씁니다.",
             "예산서(본예산·추경)와 국제유가·환율(실적·전망)도 모의값입니다. 기관 예산서와 한국석유공사 페트로넷·한국은행 자료로 바꿔 넣습니다.",
             "자료는 이 PC의 작업 폴더(data)에만 저장되고, 화면은 이 PC에서만 열립니다."],
}
webcommon.register_about(app, HERE, ABOUT)

STATE = {"an": None, "bills": [], "ds": None}


def log(action, detail=""):
    store.append_log("history.jsonl", {"action": action, "detail": detail})


def _copy_sample(with_bills):
    for p in SAMPLE.rglob("*"):
        if p.is_file() and (with_bills or "고지서" not in p.parts):
            dst = SRC / "모의자료" / p.relative_to(SAMPLE)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)


def apply_kma(ds):
    """기온 자동 수집이 켜져 있으면 내역이 있는 달의 기온을 기상청 일자료로 바꾼다(없는 달은 넣은 파일 값 유지)."""
    st = store.read("kma.json", {"on": False})
    info = {"on": bool(st.get("on")), "provider": kma.provider(), "label": kma.LABEL.get(kma.provider() or "", ""), "stn": kma.STN,
            "months": 0, "partial": [], "error": "", "range": []}
    if info["on"] and info["provider"] and ds.months:
        first, last = ds.months[0], ds.months[-1]
        months, ym = [], first
        while ym <= last:
            months.append(ym)
            ym = ingest.ym_add(ym, 1)
        cache = store.read("kma_cache.json", {})
        last = STATE.get("kma_fail")
        if last and time.time() - last[0] < 600:  # 실패 뒤 10분은 다시 묻지 않고 받아 둔 값만 씀(화면이 느려지지 않게)
            got, err = {ym: cache[ym] for ym in months if ym in cache}, last[1]
        else:
            got, err = kma.collect(months, cache)
            store.write("kma_cache.json", cache)
            STATE["kma_fail"] = (time.time(), err) if err else None
        for ym, w in got.items():
            ds.weather[ym] = {"t": w["t"], "days": w["days"], "full": w["full"], "from_api": True,
                              "src": {"file": w["src"], "loc": f"지점 {w['stn']} · {ym} 일자료 {w['n']}일", "quote": f"월평균기온 {w['t']}℃"}}
        info.update(months=len(got), partial=[ym for ym, w in got.items() if not w["full"]], error=err,
                    range=[min(got), max(got)] if got else [])
        if got:
            ds.files.append({"file": info["label"], "kind": "기온(기상청 자동 수집)", "rows": len(got)})
        if err:
            ds.warnings.append(err + " 넣은 기온 파일 값을 씁니다.")
    elif info["on"] and not info["provider"]:
        info["error"] = "키가 없어 자동 수집을 하지 못했습니다. 환경변수 KMA_APIHUB_KEY(기상청 API허브) 또는 DATA_GO_KR_KEY(공공데이터포털)를 넣어 주세요."
    STATE["kma"] = info


BUDGET_ITEMS = ["전기료", "가스료"]


def apply_budget_input(ds):
    """화면에서 입력한 연도 예산이 있으면 그 연도의 엑셀 예산서 대신 쓴다."""
    st = store.read("budget_input.json", {"years": {}})
    for fy, info in (st.get("years") or {}).items():
        fy = int(fy)
        ds.budget = [b for b in ds.budget if b["fy"] != fy]
        for r in info.get("rows", []):
            s1, s2 = r.get("supp1") or 0, r.get("supp2") or 0
            ds.budget.append({"fy": fy, "fac": r["fac"], "item": r["item"], "main": r.get("main") or 0, "supp": s1 + s2,
                              "transfer": r.get("transfer") or 0, "supp1": s1, "supp2": s2,
                              "supp_cols": [c for c, v in (("제1회 추경", s1), ("제2회 추경", s2)) if v],
                              "note": r.get("note", ""), "manual": True,
                              "src": {"file": "화면 입력(예산 관리)", "loc": f"{fy}년 예산 입력 · {info.get('saved', '')}",
                                      "quote": f"{r['fac']} {r['item']} 본예산 {r.get('main') or 0:,}원"}})


def user_actions():
    return store.read("actions.json", {"actions": []})["actions"]


def rebuild():
    SRC.mkdir(parents=True, exist_ok=True)
    docs, skipped = load_folder(SRC)
    ds, bills = ingest.build(docs, llm if llm.enabled else None)
    for s in skipped:
        ds.warnings.append(f"{s['file']}: {s['reason']}")
    apply_kma(ds)
    apply_budget_input(ds)
    acts = ds.actions + user_actions()
    an = analysis.Analysis(ds, cfg, acts).run()
    STATE.update(an=an, bills=bills, ds=ds, rec_ai=None)
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
    cur = budget.Budget(a, cfg).current()
    return {"overview": a.overview(), "findings": a.findings, "effects": a.effects(), "months": a.months, "recent": a.recent,
            "data": a.ds.summary(), "bills": STATE["bills"], "llm": llm.enabled, "kma": STATE.get("kma"),
            "budget": {"fy": cur["fy"], "totals": cur.get("totals"), "items": [{k: i[k] for k in ("fac", "item", "state", "remain", "run_out")} for i in cur["items"]]} if cur else None}


@app.get("/api/kma")
def kma_status(req):
    an()
    return STATE.get("kma") or {}


@app.get("/api/weather")
def weather(req):
    """달별 기온·도일과 전체 시설 사용량(기상 분석 도표용). 일별 기온은 기상청에서 받은 달만 있다."""
    a = an()
    use = {}
    for (fac, kind), rows in a.series.items():
        for r in rows:
            u = use.setdefault(r["ym"], {"kwh": 0, "m3": 0})
            u["kwh" if kind == "elec" else "m3"] += r["actual"] or 0
    out = []
    for ym in a.months:
        w = a.ds.weather.get(ym)
        if not w:
            continue
        dd = a.dd(ym)
        out.append({"ym": ym, "t": w["t"], "api": bool(w.get("from_api")), "full": bool(w.get("full")),
                    "days": w.get("days") or {}, "cdd": round(dd[0], 1), "hdd": round(dd[1], 1),
                    "kwh": round(use.get(ym, {}).get("kwh", 0)), "m3": round(use.get(ym, {}).get("m3", 0)),
                    "src": w.get("src")})
    e = cfg["energy"]
    return {"months": out, "kma": STATE.get("kma") or {}, "cdd_base": e["cdd_base"], "hdd_base": e["hdd_base"],
            "recent": a.recent, "baseline": a.baseline}


@app.post("/api/kma")
def kma_set(req):
    b = req.json or {}
    st = store.read("kma.json", {"on": False})
    if "on" in b:
        st["on"] = bool(b["on"])
        store.write("kma.json", st)
        STATE["kma_fail"] = None
        log("기온 자동 수집", "켬" if st["on"] else "끔")
    if b.get("refresh"):
        STATE["kma_fail"] = None
        cache = store.read("kma_cache.json", {})
        store.write("kma_cache.json", {k: v for k, v in cache.items() if v.get("full")})
        log("기온 자동 수집", "덜 찬 달 다시 받기")
    rebuild()
    return get_state(req)


# ------------------------------------------------------------ 예산

def _bopts(src):
    out = {}
    for k in ("save_pct", "elec_up_pct", "gas_up_pct", "climate_summer", "climate_winter"):
        v = src.get(k)
        if v not in (None, ""):
            try:
                out[k] = max(-50.0, min(50.0, float(v)))
            except (TypeError, ValueError):
                raise HttpError(400, f"{k} 값은 숫자로 입력하세요")
    return out


@app.get("/api/budget")
def get_budget(req):
    return budget.Budget(an(), cfg, _bopts(req.query)).run()


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
        store.write("budget_input.json", {"years": {}})  # 처음 상태로: 화면에서 입력한 예산도 지움
    _copy_sample(bool(b.get("with_bills")))
    log("시연용 모의자료 불러오기", "고지서 포함" if b.get("with_bills") else "엑셀 내역")
    rebuild()
    return get_state(req)


@app.post("/api/upload")
def upload(req):
    files = (req.json or {}).get("files") or []
    if not files:
        raise HttpError(400, "올릴 파일이 없습니다")
    fac = str((req.json or {}).get("fac") or "").strip()
    if fac:
        try:
            safe_parts(fac)
        except ValueError:
            raise HttpError(400, "시설 이름에 쓸 수 없는 글자가 있습니다")
        if "/" in fac or "\\" in fac:
            raise HttpError(400, "시설 이름에 쓸 수 없는 글자가 있습니다")
    saved = []
    for f in files:
        try:
            parts = safe_parts(f.get("path") or "")
        except ValueError:
            continue
        if Path(parts[-1]).suffix.lower() not in SUPPORTED:
            raise HttpError(400, f"읽을 수 없는 형식입니다: {parts[-1]} (엑셀·PDF·한글·워드·CSV)")
        dst = SRC.joinpath(ingest.FAC_DIR, fac, parts[-1]) if fac else SRC.joinpath("올린 자료", *parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(base64.b64decode(f["b64"]))
        saved.append("/".join(parts))
    log("자료 올리기", (f"[{fac}] " if fac else "") + ", ".join(saved))
    rebuild()
    return get_state(req)


@app.get("/api/coverage")
def coverage(req):
    """시설별로 달마다 전기·가스 자료가 어디서 왔는지(고지서·글자 인식·엑셀·없음)."""
    a = an()
    ds = a.ds
    months = []
    if a.months:
        ym = ingest.ym_add(a.months[-1], -23)
        while ym <= a.months[-1]:
            months.append(ym)
            ym = ingest.ym_add(ym, 1)
    rows = []
    for fac in ds.facilities:
        r = {"fac": fac, "elec": [], "gas": []}
        for kind, table in (("elec", ds.elec), ("gas", ds.gas)):
            for ym in months:
                x = table.get((fac, ym))
                r[kind].append(None if not x else "ocr" if x.get("ocr") else "bill" if x.get("from_bill") else "table")
        r["files"] = sorted({d["file"] for d in ds.files if ingest.folder_fac(d["file"]) == fac})
        rows.append(r)
    from core import ocr
    return {"months": months, "rows": rows, "ocr": ocr.status()}


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
    q, hidden = mask(q)
    r = qa.answer(an(), q, llm)
    r["masked"] = hidden
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
    tg = ov["target"]
    if tg:
        lines.append(f"지침의 감축 목표({tg['target_pct']:g}%) 대비 기온 보정 사용량은 {_pct(tg['change_pct'])}로, "
                     + ("목표를 달성했습니다." if tg["gap_kwh"] == 0 else f"{tg['gap_kwh']:,}kWh를 더 줄여야 합니다."))
    for e in a.effects():
        if e.get("saved_pct") is not None:
            lines.append(f"{e['fac']} '{e['title']}'은 시행 후 {e['months']}개월 동안 기온을 감안해 {e['saved_pct']}% 줄었습니다.")
    cur = budget.Budget(a, cfg).current()
    if cur and cur["items"]:
        bt = cur["totals"]
        lines.append(f"{cur['fy']}년 공공요금 예산은 연말까지 {bt['expected']:,}원 집행이 예상되어 잔액은 {bt['remain']:,}원입니다"
                     + "".join(f"; {i['fac']} {i['item']} {i['state']}({i['remain']:,}원)" for i in cur["items"] if i["state"] != "적정") + ".")
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

def kepco_cmp(a, rng=None):
    """한전 전력데이터 개방포털 지역 비교(키가 있을 때만). 실패 뒤 10분은 받아 둔 값만 쓴다."""
    info = {"key": bool(kepco.key()), "metro": kepco.METRO, "city": kepco.CITY, "src": kepco.SRC, "error": ""}
    if not info["key"]:
        return info, None, None
    cache = store.read("kepco_cache.json", {})
    fail = STATE.get("kepco_fail")
    if fail and time.time() - fail[0] < 600:
        region = {ym: v["rows"] for ym, v in cache.get("months", {}).items() if ym in a.months and v.get("rows")}
        err = fail[1]
    else:
        region, err = kepco.collect(list(a.months), cache)
        store.write("kepco_cache.json", cache)
        STATE["kepco_fail"] = (time.time(), err) if err else None
    solar = None
    if not err:
        try:
            for y in (date.today().year - 1, date.today().year - 2):
                solar = kepco.renew(y, cache)
                if solar:
                    break
            store.write("kepco_cache.json", cache)
        except Exception as e:
            info["solar_error"] = f"신재생 계약현황을 받지 못했습니다({str(e)[:80]})"
    info["error"] = err
    tariffs = {}
    for (fac, ym), d in sorted(a.ds.elec.items(), key=lambda x: x[0][1]):
        if d.get("tariff"):
            tariffs[fac] = d["tariff"]
    return info, kepco.compare(a, region, tariffs, rng), solar


@app.get("/api/kepco")
def get_kepco(req):
    q = req.query
    rng = (q.get("from"), q.get("to")) if q.get("from") and q.get("to") else None
    info, cmp, solar = kepco_cmp(an(), rng)
    return {"info": info, "cmp": cmp, "solar": solar}


@app.post("/api/kepco")
def kepco_refresh(req):
    STATE["kepco_fail"] = None
    cache = store.read("kepco_cache.json", {})
    for v in cache.get("months", {}).values():
        if not v.get("rows"):
            v["at"] = 0
    store.write("kepco_cache.json", cache)
    log("한전 자료", "다시 받기")
    return get_kepco(req)


@app.get("/api/opinet")
def get_opinet(req):
    """국내 유가(오피넷). 국제유가 연동 계산에는 쓰지 않는 참고값."""
    d, err = opinet.collect(store.read("opinet.json", {}))
    if opinet.key():
        store.write("opinet.json", d)
    return {"key": bool(opinet.key()), "error": err, "data": opinet.summary(d), "sido": opinet.SIDO}


@app.get("/api/recommend")
def get_recommend(req):
    a = an()
    r = recommend.build(a, kp=kepco_cmp(a)[1])
    r["ai"] = STATE.get("rec_ai")
    return r


@app.post("/api/recommend/ai")
def recommend_ai(req):
    """모델이 연결되어 있으면 추천 목록을 바탕으로 우선순위 설명을 쓴다. 사실에 없는 숫자가 있으면 버린다."""
    a = an()
    r = recommend.build(a, kp=kepco_cmp(a)[1])
    facts = [f"{x['fac']} - {x['title']}: {x['why']}" + (f" 예상 절감 {x['save_won']:,}원" if x["save_won"] else "")
             + (f" (근거 {x['basis']['reg']} {x['basis']['label']})" if x["basis"] else "") for x in r["items"] if not x["registered"]]
    if not facts:
        return {"text": "새로 추천할 조치가 없습니다.", "mode": "규칙 기반"}
    rule = "먼저 할 일: " + "; ".join(f"{x['fac']} {x['title']}" for x in r["items"][:3] if not x["registered"]) + "."
    res = {"text": rule, "mode": "규칙 기반", "dropped": ""}
    if llm.enabled:
        try:
            out = llm.chat([{"role": "system", "content": "공공기관 시설 에너지 담당자에게 절감 조치 추천을 한국어 4~6문장으로 설명하세요. 아래 사실에 있는 숫자·시설·근거만 쓰고, 효과가 크고 비용이 적은 일부터 순서를 정해 이유를 말하세요."},
                            {"role": "user", "content": "\n".join(f"- {x}" for x in facts)}], max_tokens=700)
            allowed = qa._nums("\n".join(facts)) | {float(i) for i in range(0, 13)}
            extra = [n for n in qa._nums(out) if n not in allowed and round(n) not in allowed]
            if extra:
                res["dropped"] = f"모델 설명에 사실에 없는 숫자({', '.join(str(x) for x in extra[:3])})가 있어 규칙 설명을 씁니다"
            else:
                res.update(text=out.strip(), mode="모델(사실 대조 통과)")
        except LLMError as e:
            res["dropped"] = f"모델 응답 실패({e})"
    STATE["rec_ai"] = res
    log("절감 조치 추천 설명", res["mode"])
    return res


@app.post("/api/actions")
def add_action(req):
    b = req.json or {}
    a = an()
    f = next((x for x in a.findings if x["id"] == b.get("finding")), None)
    fac = b.get("fac") or (f["fac"] if f else "")
    title = mask((b.get("title") or "").strip())[0]
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
    blocks += [("p", "점검 결과와 조치 계획(시행일 포함)을 회신해 주시면 한톨(에너지 절감 비서)에 등록하여 효과를 측정하겠습니다.")]
    fmt = req.query.get("fmt", "hwpx")
    data, ctype = export(blocks, fmt)
    log("점검 요청서 만들기", f["title"])
    return file_response(data, f"점검요청_{f['fac'] or '전체'}_{f['id']}.{fmt}", ctype)


def _report_summary(a, ov):
    t = ov["totals"]
    lv = [f["level"] for f in a.findings]
    out = [f"최근 12개월({ov['recent'][0]}~{ov['recent'][1]}) 전기 {t['kwh']:,}kWh, 전기·가스 요금 {t['cost']:,}원, 전기 온실가스 {t['co2_t']:,}tCO2eq",
           f"점검 기준에 걸린 항목 **{t['findings']}건**(조치 필요 {lv.count('bad')}건, 검토 {lv.count('warn')}건), 찾은 금액 **{t['amount']:,}원**(추정)"]
    tg = ov.get("target")
    if tg:
        out.append(f"감축 목표 {tg['target_pct']:g}% 대비 기온 보정 변화 {_pct(tg['change_pct'])}, 목표까지 {tg['gap_kwh']:,}kWh")
    try:
        cur = budget.Budget(a, cfg).current()
        st = [i["state"] for i in cur["items"]]
        if st:
            out.append(f"{cur['fy']}년 공공요금 예산: 부족 예상 {st.count('부족 예상')}개 과목, 불용 예상 {st.count('불용 예상')}개 과목(집행률 {cur['totals']['exec_pct']}%)")
    except Exception:
        pass
    k = STATE.get("kma") or {}
    if k.get("on") and k.get("months"):
        out.append(f"기온: 기상청 지상관측(ASOS) 일자료(지점 {k['stn']}, {k['months']}개월)로 날마다 도일을 계산해 보정")
    return out


@app.post("/api/report")
def report(req):
    a = an()
    ov = a.overview()
    s = STATE.get("summary") or {"text": rule_summary(a), "mode": "규칙 기반"}
    t = ov["totals"]
    blocks = [("title", f"에너지 사용 점검 보고서 ({ov['recent'][0]}~{ov['recent'][1]})"),
              ("p", f"작성일 {date.today().isoformat()} · 기준선 {ov['baseline'][0]}~{ov['baseline'][1]}(기온 보정) · 작성: 에너지 절감 비서({s['mode']})"),
              ("summary", _report_summary(a, ov)),
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
    tg = ov["target"]
    if tg:
        blocks += [("h1", "4. 감축 목표 진척"),
                   ("p", f"목표: 기온 보정 전기 사용량 {tg['target_pct']:g}% 감축({_basis_text(tg['basis'])}). 최근 12개월(휴관 달 제외) 실제 {tg['actual']:,}kWh, "
                         f"기준선 {tg['expected']:,}kWh, 변화 {_pct(tg['change_pct'])}. 목표까지 {tg['gap_kwh']:,}kWh 남음. "
                         f"점검 결과의 초과 사용 {tg['fix_kwh']:,}kWh를 조치했다면 {_pct(tg['after_fix_pct'])}."),
                   ("table", ["시설", "실제(kWh)", "기준선(kWh)", "기온 보정 변화"],
                    [[x["name"], f"{x['actual']:,}", f"{x['expected']:,}", _pct(x["change_pct"])] for x in tg["facilities"]], [3, 2, 2, 2])]
    ol = ov["outlook"]
    if ol and ol["rows"]:
        blocks += [("h2", f"{ol['ym']} 시설별 관리 목표"),
                   ("table", ["시설", "예상 사용량(kWh)", "점검선(kWh)", "예상 전력량요금(원)"],
                    [[x["name"], f"{x['expected']:,}", f"{x['limit']:,}", f"{x['cost']:,}" if x["cost"] else ""] for x in ol["rows"]], [3, 2, 2, 2]),
                   ("note", "※ " + ol["note"])]
    eff = a.effects()
    if eff:
        blocks += [("h1", "5. 절감 조치 이행·효과"),
                   ("table", ["시설", "조치", "시행일", "상태", "효과(기온 보정)"],
                    [[e["fac"], e["title"], e["date"], e["state"],
                      f"{e['saved_kwh']:,}kWh ({e['saved_pct']}%), {e['saved_won']:,}원, {e['co2_t']}tCO2eq ({e['from']}~{e['to']})" if e.get("saved_kwh") is not None else ""]
                     for e in eff], [2, 4, 2, 2, 5])]
    blocks += [("h1", "6. 점검 기준과 계산 방법"),
               ("table", ["기준", "값", "근거"], [[r["label"], "" if r["value"] is None else f"{r['value']:g}", _basis_text(r["basis"]) if r["basis"] else "기본값(설정)"] for r in ov["rules"]], [4, 1, 4]),
               ("p", "기온 보정: 기준 기간 12개월의 월 사용량을 '기저 + 냉방계수×냉방도일 + 난방계수×난방도일'로 맞추고, 이후 달의 기온만 감안한 예상치와 실제를 비교합니다(도일은 월평균기온으로 근사)."),
               ("note", "※ 금액은 고지서 단가와 설정값으로 계산한 추정치이며, 계약전력·요금 정정은 공급사 확인 후 확정됩니다.")]
    fmt = req.query.get("fmt", "hwpx")
    data, ctype = export(blocks, fmt)
    log("보고서 만들기", fmt)
    return file_response(data, f"에너지사용점검보고서_{ov['recent'][1]}.{fmt}", ctype)


def _won(v):
    return "" if v is None else f"{v:,.0f}"


def _int(v):
    try:
        return int(round(float(str(v).replace(",", "").strip() or 0)))
    except ValueError:
        raise HttpError(400, f"숫자가 아닌 값이 있습니다: '{v}'")


def _budget_rows(a, fy):
    rows = [b for b in a.ds.budget if b["fy"] == fy or (b["fy"] is None and fy == (int(a.latest[:4]) if a.latest else None))]
    return [{"fac": b["fac"], "item": b["item"], "main": round(b["main"]),
             "supp1": round(b.get("supp1", b["supp"])), "supp2": round(b.get("supp2", 0)), "transfer": round(b["transfer"]),
             "note": b.get("note", ""), "manual": bool(b.get("manual"))} for b in rows]


def _cur_fy(a):
    return int(a.latest[:4]) if a.latest else date.today().year


@app.get("/api/budget/input")
def budget_input_get(req):
    return _budget_input(_int(req.query.get("fy") or _cur_fy(an())))


def _budget_input(fy):
    a = an()
    cur = _cur_fy(a)
    rows = _budget_rows(a, fy)
    st = store.read("budget_input.json", {"years": {}})
    saved = (st.get("years") or {}).get(str(fy))
    files = sorted({b["src"]["file"] for b in a.ds.budget if b["fy"] in (fy, None) and not b.get("manual") and b.get("src")})
    suggest = {}
    if fy == cur + 1:
        ny = budget.Budget(a, cfg).next_year() or {}
        suggest = {f"{i['fac']}|{i['item']}": i["request"] for i in ny.get("items", [])}
        if not rows:  # 내년 예산이 아직 없으면 앱 편성안을 본예산 칸에 미리 채워 둔다(저장 전까지는 반영 안 됨)
            rows = [{"fac": i["fac"], "item": i["item"], "main": i["request"], "supp1": 0, "supp2": 0, "transfer": 0, "note": "앱 편성안", "manual": False}
                    for i in ny.get("items", [])]
    if not rows:
        rows = [{"fac": f, "item": it, "main": 0, "supp1": 0, "supp2": 0, "transfer": 0, "note": "", "manual": False}
                for f in a.ds.facilities for it, k in budget.KIND.items() if (f, k) in a.series]
    return {"fy": fy, "years": [cur, cur + 1], "rows": rows, "suggest": suggest,
            "source": "화면 입력" if saved else ("엑셀: " + ", ".join(files) if files else "없음"),
            "saved": saved.get("saved") if saved else None, "facilities": list(a.ds.facilities), "items": BUDGET_ITEMS}


@app.post("/api/budget/input")
def budget_input_set(req):
    b = req.json or {}
    fy = _int(b.get("fy"))
    if not 2000 <= fy <= 2100:
        raise HttpError(400, "회계연도를 확인해 주세요")
    st = store.read("budget_input.json", {"years": {}})
    st.setdefault("years", {})
    if b.get("reset"):
        st["years"].pop(str(fy), None)
        store.write("budget_input.json", st)
        log("예산 입력", f"{fy}년 화면 입력 지우고 엑셀 예산서로 되돌림")
    else:
        rows, seen = [], set()
        for r in b.get("rows") or []:
            fac, item = str(r.get("fac") or "").strip(), str(r.get("item") or "").strip()
            if not fac or item not in BUDGET_ITEMS:
                raise HttpError(400, "시설과 과목(전기료·가스료)을 골라 주세요")
            if (fac, item) in seen:
                raise HttpError(400, f"{fac} {item}이(가) 두 번 들어 있습니다")
            seen.add((fac, item))
            rows.append({"fac": fac, "item": item, "main": _int(r.get("main")), "supp1": _int(r.get("supp1")), "supp2": _int(r.get("supp2")),
                         "transfer": _int(r.get("transfer")), "note": str(r.get("note") or "")[:100]})
        st["years"][str(fy)] = {"rows": rows, "saved": time.strftime("%Y-%m-%d %H:%M")}
        store.write("budget_input.json", st)
        log("예산 입력", f"{fy}년 {len(rows)}과목, 합계 {sum(x['main'] + x['supp1'] + x['supp2'] + x['transfer'] for x in rows):,}원")
    rebuild()
    return _budget_input(fy)


@app.post("/api/budget/template")
def budget_template(req):
    fy = _int((req.json or {}).get("fy") or _cur_fy(an()))
    rows = _budget_input(fy)["rows"]
    data = workbook([(f"{fy}년 공공요금 예산", ["회계연도", "시설명", "세출예산 과목", "본예산(원)", "제1회 추경(원)", "제2회 추경(원)", "전용(원)", "비고"],
                      [[fy, r["fac"], "공공요금 및 제세 > " + r["item"], r["main"], r["supp1"], r["supp2"], r["transfer"], r["note"]] for r in rows],
                      [10, 16, 26, 16, 16, 16, 14, 20])],
                    note="한톨 예산서 양식: 시설명은 전기·가스 내역과 같은 이름으로, 과목은 전기료·가스료가 들어가게 적고 '자료 넣기'에 올리세요. 금액은 원 단위.")
    log("예산서 양식 내려받기", f"{fy}년")
    return file_response(data, f"한톨_예산서양식_{fy}.xlsx", XLSX)


@app.post("/api/budget/report")
def budget_report(req):
    a = an()
    b = budget.Budget(a, cfg, _bopts({**req.query, **(req.json or {})})).run()
    cur, nm, ny = b["current"], b["next_month"], b["next_year"]
    blocks = [("title", f"{cur['fy']}년 공공요금 예산 집행 관리 및 {ny['fy']}년도 편성 근거(안)"),
              ("p", f"작성일 {date.today().isoformat()} · 자료 기준 {cur['latest']}(고지 기준) · 작성: 한톨(에너지 절감 비서), 규칙·계산 기반")]
    if cur["items"]:
        tt, st = cur["totals"], [i["state"] for i in cur["items"]]
        sm = [f"예산현액 {_won(tt['current'])}원 중 {_won(tt['spent'])}원 집행(집행률 {tt['exec_pct']}%, 경과율 {cur['elapsed_pct']}%), 연말 예상 잔액 {_won(tt['remain'])}원",
              f"부족 예상 **{st.count('부족 예상')}개 과목**, 불용 예상 **{st.count('불용 예상')}개 과목**" + (f", 전용 검토 {len(cur['moves'])}건" if cur["moves"] else "")]
        if ny and ny.get("totals"):
            sm.append(f"{ny['fy']}년 요구액 {_won(ny['totals']['request'])}원" + (f"(올해 대비 {ny['totals']['change_pct']:+.1f}%)" if ny["totals"].get("change_pct") is not None else ""))
        blocks.append(("summary", sm))
    if cur["items"]:
        t = cur["totals"]
        blocks += [("h1", f"1. {cur['fy']}년 예산 집행 현황"),
                   ("p", f"예산현액 {_won(t['current'])}원(본예산 {_won(t['main'])}원, 추경·전용 {_won(t['supp'])}원) 중 {_won(t['spent'])}원을 집행해 집행률 {t['exec_pct']}%"
                         f"(경과율 {cur['elapsed_pct']}%)입니다. 남은 달 예측 {_won(t['fc'])}원을 더하면 연말 예상 집행액은 {_won(t['expected'])}원, 예상 잔액은 {_won(t['remain'])}원입니다."),
                   ("table", ["시설", "과목", "본예산", "추경·전용", "예산현액", "집행액", "남은 달 예측", "예상 잔액", "판정"],
                    [[i["fac"], i["item"], _won(i["main"]), _won(i["supp"] + i["transfer"]), _won(i["current"]), _won(i["spent"]), _won(i["fc"]),
                      f"{_won(i['remain'])} ({i['remain_pct']:+.1f}%)", i["state"] + (f"({int(i['run_out'][5:])}월분부터)" if i["run_out"] else "")] for i in cur["items"]],
                    [2, 1, 2, 2, 2, 2, 2, 3, 2]),
                   ("note", "※ " + cur["note"])]
        short = [i for i in cur["items"] if i["state"] == "부족 예상"]
        unused = [i for i in cur["items"] if i["state"] == "불용 예상"]
        blocks.append(("h2", "조치 의견"))
        for i in short:
            blocks.append(("bullet", f"{i['fac']} {i['item']}: {int(i['run_out'][5:]) if i['run_out'] else 12}월분 고지부터 부족, 부족 예상액 {_won(i.get('need'))}원. "
                                     + (f"전용 후에도 {_won(i['need_after_moves'])}원은 추경 증액이 필요합니다." if i.get("need_after_moves") else "아래 전용으로 메울 수 있습니다.")))
        for m in cur["moves"]:
            blocks.append(("bullet", f"전용 검토: {m['from']} 잔액에서 {m['to']}로 {_won(m['amount'])}원 (기관 예산 집행 규정·승인 절차 확인)"))
        for i in unused:
            blocks.append(("bullet", f"{i['fac']} {i['item']}: 불용 예상 {_won(i['remain'])}원({i['remain_pct']:.1f}%). 전용 뒤 남는 금액은 감액 추경 또는 다른 사업 재배정을 검토하고, 내년 편성액에 반영합니다."))
        if not short and not unused:
            blocks.append(("bullet", "모든 과목이 적정 범위입니다."))
    if nm and nm["rows"]:
        blocks += [("h1", f"2. 다음 달({nm['ym']}) 요금 예측"),
                   ("table", ["시설", "전기(kWh)", "전기요금", "가스(㎥)", "가스요금", "합계"],
                    [[r["fac"], _won(r.get("elec", {}).get("qty")), _won(r.get("elec", {}).get("cost")), _won(r.get("gas", {}).get("qty")),
                      _won(r.get("gas", {}).get("cost")), _won(r["cost"])] for r in nm["rows"]], [3, 2, 2, 2, 2, 2]),
                   ("p", f"합계 {_won(nm['total'])}원(추정)."), ("note", "※ " + nm["note"])]
    t = ny["totals"]
    blocks += [("h1", f"3. {ny['fy']}년도 공공요금 예산 편성 근거(안)"),
               ("p", f"요구액 합계 {_won(t['request'])}원" + (f"(올해 예산현액 대비 {t['change_pct']:+.1f}%)" if t["change_pct"] is not None else "")
                     + f", 기후 위험 여유분 {_won(t['risk'])}원(포함 시 {_won(t['with_risk'])}원)."),
               ("table", ["시설", "과목", "요구액(원)", "올해 예산현액", "증감", "기후 위험 여유분", "산출 근거"],
                [[i["fac"], i["item"], _won(i["request"]), _won(i["prev_budget"]), f"{i['change_pct']:+.1f}%" if i["change_pct"] is not None else "",
                  _won(i["risk"]), i["basis"]] for i in ny["items"]], [2, 1, 2, 2, 1, 2, 6]),
               ("h2", "편성 가정"),
               ("table", ["항목", "값", "설명"], [[x["k"], x["v"], x["detail"]] for x in ny["assume"]], [2, 4, 6])]
    oil = ny["oil"]
    if oil.get("has"):
        blocks.append(("p", f"국제유가(두바이유): 최근 12개월 평균 {oil['now_avg']}달러/배럴, 내년 단가에 반영될 시기 평균 {oil['next_avg']}달러/배럴(전망). "
                            f"유가×환율 변화 {(oil['ratio'] - 1) * 100:+.1f}% → 가스 단가 {oil['gas_change_pct']:+.1f}%."))
    else:
        blocks.append(("p", oil["note"]))
    blocks += [("h2", "기후변화 대응"),
               ("p", f"내년 예상 전기 사용량 {_won(t['kwh'])}kWh, 온실가스 {t['co2_t']:,}tCO2eq(배출계수 {cfg['energy']['co2_per_kwh']} kgCO2eq/kWh, 설정값)."
                     + (f" 지침의 감축 목표 {t['target_pct']:g}%를 달성하면 {t['co2_target_t']:,}tCO2eq입니다." if t.get("target_pct") else "")),
               ("note", "※ " + ny["note"] + " 금액은 모두 추정이며, 확정 단가·요금 조정은 공급사 고지에 따릅니다.")]
    fmt = req.query.get("fmt", "hwpx")
    data, ctype = export(blocks, fmt)
    log("예산 문서 만들기", f"{cur['fy']} 집행 관리·{ny['fy']} 편성 근거")
    return file_response(data, f"공공요금예산_{cur['fy']}집행관리_{ny['fy']}편성근거.{fmt}", ctype)


@app.post("/api/export-xlsx")
def export_xlsx(req):
    """점검 결과·시설별 월 내역·조치 효과를 엑셀 한 파일로."""
    a = an()
    ov = a.overview()
    lv = {"bad": "조치 필요", "warn": "검토", "gray": "설정 확인"}
    sheets = [("점검 결과", ["번호", "구분", "시설", "내용", "요약", "금액(원, 추정)", "계산식", "담당", "근거 조문", "근거 자료"],
               [[f["id"], lv.get(f["level"], ""), f["fac"], f["title"], f["summary"], f.get("amount") or None, f.get("formula", ""), f["owner"],
                 ", ".join(_basis_text(b) for b in f["basis"] if b), "; ".join(_cite_text(e.get("src")) for e in f["evidence"] if e.get("src"))]
                for f in a.findings], [6, 10, 12, 40, 60, 14, 40, 14, 30, 50])]
    rows = []
    for (fac, kind), ser in sorted(a.series.items()):
        for r in ser:
            rows.append([fac, "전기" if kind == "elec" else "가스", r["ym"], r["temp"], r["actual"], r["expected"], r["diff_pct"],
                         round(r["closed"] * 100) or None, r["cost"], "기준 기간" if r["baseline"] else "최근 12개월" if r["recent"] else "", _cite_text(r["src"])])
    sheets.append(("시설별 월 내역", ["시설", "에너지", "연월", "월평균기온(℃)", "실제", "기온 보정 예상", "차이(%)", "휴관(%)", "요금(원)", "구간", "출처"],
                   rows, [12, 8, 9, 10, 12, 12, 9, 8, 14, 11, 50]))
    sheets.append(("조치 효과", ["시설", "조치", "시행일", "상태", "판정", "측정 기간", "줄인 전기(kWh)", "변화(%)", "줄인 요금(원, 추정)", "온실가스(tCO2eq)"],
                   [[e["fac"], e["title"], e["date"], e["status"], e["state"], f"{e['from']}~{e['to']}" if e.get("from") else "",
                     e.get("saved_kwh"), e.get("saved_pct"), e.get("saved_won"), e.get("co2_t")] for e in a.effects()],
                   [12, 30, 11, 8, 14, 16, 14, 9, 16, 14]))
    bd = budget.Budget(a, cfg).run()
    cur, ny = bd["current"], bd["next_year"]
    if cur["items"]:
        sheets.append((f"{cur['fy']} 예산 집행", ["시설", "과목", "본예산", "추경·전용", "예산현액", "집행액", "남은 달 예측", "연말 예상 집행", "예상 잔액", "잔액률(%)", "판정", "소진 달", "예산서 출처"],
                       [[i["fac"], i["item"], i["main"], i["supp"] + i["transfer"], i["current"], i["spent"], i["fc"], i["expected"], i["remain"], i["remain_pct"],
                         i["state"], i["run_out"] or "", _cite_text(i["src"])] for i in cur["items"]], [12, 8, 14, 12, 14, 14, 14, 14, 14, 9, 10, 9, 40]))
    sheets.append((f"{ny['fy']} 편성 근거", ["시설", "과목", "예상 사용량", "단위", "요구액(원)", "올해 예산현액", "증감(%)", "기후 위험 여유분", "산출 근거"],
                   [[i["fac"], i["item"], i["qty"], i["unit"], i["request"], i["prev_budget"], i["change_pct"], i["risk"], i["basis"]] for i in ny["items"]],
                   [12, 8, 14, 6, 14, 14, 9, 14, 80]))
    data = workbook(sheets, note=f"한톨(에너지 절감 비서) {VERSION} · 작성일 {date.today().isoformat()} · 금액은 고지서 단가와 설정값으로 계산한 추정치")
    log("엑셀 내보내기", ov["recent"][1] if ov["recent"] else "")
    return file_response(data, f"에너지점검_{ov['recent'][1] if ov['recent'] else ''}.xlsx", XLSX)


def _pct(v):
    return "" if v is None else f"{v:+.1f}%"


@app.get("/api/log")
def get_log(req):
    return {"items": list(reversed(store.read_log("history.jsonl")))[:200]}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="한톨(에너지 절감 비서) 실행")
    ap.add_argument("--port", type=int, default=cfg["port"])
    ap.add_argument("--host", default=cfg["host"])
    ap.add_argument("--no-browser", action="store_true")
    ar = ap.parse_args()
    an()
    app.run(ar.host, ar.port, not ar.no_browser, "한톨(에너지 절감 비서)")


if __name__ == "__main__":
    main()
