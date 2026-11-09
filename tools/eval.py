"""정확도 성적표: 모델·인터넷 없이 같은 결과가 나오는 시험을 돌려 검증결과/에 남긴다.

  python tools/eval.py            → 검증결과/scorecard.json, 검증결과/정확도_성적표.md

시험 다섯 가지
  1. 이상 찾기: 무작위로 만든 기관 자료 30세트(시드 고정)에 이상을 몰래 심고, 찾아내는지(놓침)와
     심지 않은 것을 잘못 잡는지(오탐)를 센다. 날씨만 더운 여름, 기준보다 작은 증가(4%), 제대로 설비를 끈 휴관은
     '잡으면 안 되는 것'으로 함께 심는다.
  2. 고지서 읽기: 값을 무작위로 바꾼 고지서 PDF 40장을 만들어 15개 항목을 읽고 원래 값과 대조한다.
  3. 질문 이해: 개발자가 미리 적어 둔 질문 30개에 대해 답에 들어가야 할 시설·주제 낱말이 있는지 본다.
  4. 지어낸 숫자 차단: 가짜 모델이 답에 사실에 없는 숫자를 섞으면 버리는지(200회), 사실 안의 숫자만 쓴 답은
     버리지 않는지(30회) 본다.
  5. 개인정보 가림: 개인정보가 든 문장 30개는 가리고, 앱이 실제로 만든 문장(답·점검 결과) 전부는 건드리지 않는지 본다.
문항과 정답은 이 파일에 있고, 틀린 문항은 고치지 않고 성적표에 그대로 적는다.
"""
import json
import random
import re
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import analysis  # noqa: E402
import ingest  # noqa: E402
import qa  # noqa: E402
import samples_energy as S  # noqa: E402
from core.export import to_hwpx  # noqa: E402
from core.loaders import Block, Document, load_folder  # noqa: E402
from core.pii import mask  # noqa: E402

CFG = {"energy": {"cdd_base": 18.0, "hdd_base": 18.0, "closure_expected_ratio": 0.35, "contract_margin": 0.2, "co2_per_kwh": 0.4594,
                  "tariff": {k: list(v) for k, v in S.RATE.items()}}}
RECENT = [m for m in (f"{y}-{mm:02d}" for y, mm in S.MONTHS) if "2025-09" <= m <= "2026-08"]
NAMES = ["가 청사", "나 체육관", "다 회관", "라 주차장", "마 복지관"]


# ------------------------------------------------------------ 1. 이상 찾기

def make_world(seed, out):
    """무작위 기관 자료 한 세트를 out 폴더에 쓰고, 심은 이상 목록을 돌려준다."""
    rnd = random.Random(seed)
    temp = {y: {m: t + rnd.uniform(-1.2, 1.2) for m, t in ms.items()} for y, ms in S.TEMP.items()}
    hot = rnd.random() < 0.5  # 최근 여름을 더 덥게(날씨 탓 증가는 잡으면 안 됨)
    if hot:
        for m in (7, 8):
            temp[2026][m] += 2.5

    def dd(y, m):
        t = temp[y][m]
        d = S.days(y, m)
        return d * max(0.0, t - 18), d * max(0.0, 18 - t)

    facs = []
    for n in NAMES:
        facs.append({"name": n, "area": rnd.randint(3000, 12000), "contract": rnd.choice([250, 300, 400, 500, 700]),
                     "base": rnd.randint(20000, 120000), "c": rnd.uniform(10, 160), "h": rnd.uniform(5, 60),
                     "gbase": rnd.choice([0, rnd.randint(800, 18000)]), "gh": rnd.uniform(5, 50)})
    plant, decoy = [], []
    pick = lambda: rnd.choice(facs)  # noqa: E731
    mult, gmult, pfs, billx, closures = {}, {}, {}, {}, {}
    used = set()
    kinds = rnd.sample(["excess", "excess2", "gas", "closure", "contract", "pf", "bill"], 4)
    for k in kinds:
        f = pick()
        while f["name"] in used:
            f = pick()
        used.add(f["name"])
        if k in ("excess", "excess2"):
            i = rnd.randrange(0, 11 if k == "excess2" else 12)
            ms = RECENT[i:i + (2 if k == "excess2" else 1)]
            for m in ms:
                mult[(f["name"], m)] = rnd.uniform(1.12, 1.30)  # 기준 10%를 조금 넘는 것부터
            plant.append(("excess", f["name"], ms))
        elif k == "gas":
            if not f["gbase"]:
                f["gbase"] = rnd.randint(800, 18000)
            m = rnd.choice(["2025-12", "2026-01", "2026-02"])
            gmult[(f["name"], m)] = rnd.uniform(1.17, 1.40)  # 가스 기준 15%를 조금 넘는 것부터
            plant.append(("gas_excess", f["name"], [m]))
        elif k == "closure":
            m = rnd.choice(RECENT[2:10])
            y, mm = map(int, m.split("-"))
            closures[f["name"]] = (f"{m}-01", f"{m}-{S.days(y, mm):02d}")
            mult[(f["name"], m)] = rnd.uniform(0.80, 0.95)
            plant.append(("closure", f["name"], [m]))
        elif k == "contract":
            typical = (f["base"] + f["c"] * 300 + f["h"] * 500) / (30 * 24 * 0.42)
            f["fixed_applied"] = int(round(typical * rnd.uniform(2.4, 3.2), -1))
            f["contract"] = max(f["contract"], f["fixed_applied"])
            plant.append(("contract", f["name"], []))
        elif k == "pf":
            start = rnd.choice(RECENT[3:11])
            pfs[f["name"]] = start
            plant.append(("pf", f["name"], [start]))
        elif k == "bill":
            m = rnd.choice(RECENT)
            billx[(f["name"], m)] = rnd.uniform(1.03, 1.10) if rnd.random() < 0.5 else rnd.uniform(0.90, 0.97)
            plant.append(("bill", f["name"], [m]))
    # 잡으면 안 되는 것: 기준보다 작은 증가, 설비를 제대로 끈 휴관
    rest = [f for f in facs if f["name"] not in used]
    if rest:
        f = rnd.choice(rest)
        m = rnd.choice(RECENT)
        mult[(f["name"], m)] = 1.04
        decoy.append(("작은 증가(4%)", f["name"], m))
        rest = [x for x in rest if x is not f]
    if rest:
        f = rnd.choice(rest)
        m = rnd.choice(RECENT[2:10])
        y, mm = map(int, m.split("-"))
        closures[f["name"]] = (f"{m}-01", f"{m}-{S.days(y, mm):02d}")
        mult[(f["name"], m)] = 0.38
        decoy.append(("설비를 끈 휴관", f["name"], m))
    if hot:
        decoy.append(("날씨만 더운 여름", "", "2026-07~08"))

    elec, gas = [], []
    for f in facs:
        hist = []
        for (y, m) in S.MONTHS:
            ym = f"{y}-{m:02d}"
            if ym > "2026-08":
                continue
            cdd, hdd = dd(y, m)
            kwh = (f["base"] + f["c"] * cdd + f["h"] * hdd) * mult.get((f["name"], ym), 1.0)
            kwh = round(kwh * (1 + rnd.uniform(-0.018, 0.018)))
            peak = round(kwh / (S.days(y, m) * 24 * 0.42) * rnd.uniform(0.97, 1.03))
            hist.append(peak)
            # 계약전력 과다: 요금적용전력을 최대수요가 절반에도 못 미치게 크게 고정
            applied = f["fixed_applied"] if f.get("fixed_applied") else max(hist[-12:])
            pf = round(rnd.uniform(93.0, 96.0), 1)
            if f["name"] in pfs and ym >= pfs[f["name"]]:
                pf = round(rnd.uniform(84.0, 88.5), 1)
            bands = (round(kwh * 0.35), round(kwh * 0.40))
            bands = bands + (kwh - bands[0] - bands[1],)
            rate = S.RATE[S.season(m)]
            energy = round(sum(round(b * p) for b, p in zip(bands, rate)) * billx.get((f["name"], ym), 1.0))
            basic = applied * S.BASIC
            pfc = round(basic * 0.002 * (90 - pf)) if pf < 90 else 0
            vat = round((basic + energy + pfc) * 0.1)
            elec.append([ym, f["name"], f["contract"], applied, peak, *bands, kwh, pf, basic, energy, pfc, vat, basic + energy + pfc + vat])
            if f["gbase"]:
                g = (f["gbase"] + f["gh"] * hdd) * gmult.get((f["name"], ym), 1.0) * (mult.get((f["name"], ym), 1.0) if f["name"] in closures else 1.0)
                g = round(g * (1 + rnd.uniform(-0.02, 0.02)))
                gas.append([ym, f["name"], g, g * S.GAS_RATE])
    S.OUT = out
    head = ["연월", "시설명", "계약전력(kW)", "요금적용전력(kW)", "최대수요전력(kW)", "경부하(kWh)", "중간부하(kWh)", "최대부하(kWh)",
            "사용전력량(kWh)", "평균역률(%)", "기본요금(원)", "전력량요금(원)", "역률요금(원)", "부가가치세(원)", "청구금액(원)"]
    S.xlsx("전력.xlsx", "전력", head, elec)
    S.xlsx("가스.xlsx", "가스", ["연월", "시설명", "사용량(㎥)", "요금(원)"], gas)
    S.xlsx("시설 현황.xlsx", "시설", ["시설명", "용도", "연면적(㎡)", "계약전력(kW)", "휴관 기간", "휴관 사유", "시설 관리자"],
           [[f["name"], "시설", f["area"], f["contract"], "~".join(closures[f["name"]]) if f["name"] in closures else "",
             "공사" if f["name"] in closures else "", "담당"] for f in facs])
    S.xlsx("기온.xlsx", "기온", ["연월", "월평균기온(℃)"], [[f"{y}-{m:02d}", round(temp[y][m], 1)] for y, m in S.MONTHS if f"{y}-{m:02d}" <= "2026-08"])
    (out / "지침.hwpx").write_bytes(to_hwpx([("p", ln) for ln in S.GUIDE.strip().splitlines() if ln.strip()]))
    return plant, decoy


def eval_detect(n=30):
    hit = total = fa = 0
    misses, falses, decoys = [], [], 0
    for seed in range(1, n + 1):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            plant, decoy = make_world(seed, out)
            docs, _ = load_folder(out)
            ds, _ = ingest.build(docs)
            an = analysis.Analysis(ds, CFG, []).run()
        decoys += len(decoy)
        matched = set()
        for typ, fac, months in plant:
            total += 1
            ok = [i for i, f in enumerate(an.findings) if f["fac"] == fac and f["type"] == typ
                  and (typ not in ("excess", "gas_excess", "closure", "bill") or set(months) & set(f["months"]))]
            if not ok:
                misses.append(f"세트 {seed}: {fac} {typ} {','.join(months)} 놓침")
            else:
                hit += 1
                matched.update(ok)
        for i, f in enumerate(an.findings):
            if i not in matched:
                fa += 1
                falses.append(f"세트 {seed}: 심지 않은 결과 '{f['title']}'")
    return [
        {"name": "이상 찾기(놓치지 않음)", "passed": hit, "total": total, "failures": misses,
         "desc": f"무작위 자료 {n}세트에 심은 이상(초과 사용·가스·휴관 미정지·계약전력·역률·요금 차이)을 찾아낸 비율"},
        {"name": "이상 찾기(오탐 없음)", "passed": n - len({x.split(':')[0] for x in falses}), "total": n, "failures": falses,
         "desc": f"심지 않은 것을 잘못 잡지 않은 세트 수. 잡으면 안 되는 것 {decoys}개(날씨만 더운 여름·4% 증가·설비를 끈 휴관)를 함께 심음. 오탐 {fa}건"},
    ]


# ------------------------------------------------------------ 2. 고지서 읽기

def eval_bills(n=40):
    from pypdf import PdfReader
    import io
    rnd = random.Random(7)
    fields = ["contract", "applied", "peak", "off", "mid", "on", "kwh", "pf", "basic", "energy", "pfc", "vat", "total", "ym", "fac"]
    ok_bills = ok_fields = 0
    fails = []
    # 고지서 PDF는 reportlab으로 만든다(개발용). 없으면 저장해 둔 PDF 추출 글자(tools/eval_bills_text.json)로 같은 시험을 한다.
    cache_file = ROOT / "tools" / "eval_bills_text.json"
    try:
        import reportlab  # noqa: F401
        have_rl = True
    except ImportError:
        have_rl = False
    cache = {} if have_rl else json.loads(cache_file.read_text(encoding="utf-8"))
    for i in range(n):
        f = dict(rnd.choice(S.FAC))
        y, m = rnd.choice(S.MONTHS)
        kwh = rnd.randint(8000, 260000)
        bands = (round(kwh * 0.35), round(kwh * 0.40))
        bands += (kwh - sum(bands),)
        peak = rnd.randint(40, 700)
        applied = rnd.randint(peak, 800)
        pf = round(rnd.uniform(82, 99), 1)
        basic = applied * S.BASIC
        energy = rnd.randint(1_000_000, 40_000_000)
        pfc = round(basic * 0.002 * (90 - pf)) if pf < 90 else 0
        vat = round((basic + energy + pfc) * 0.1)
        r = {"fac": f, "ym": f"{y}-{m:02d}", "kwh": kwh, "bands": bands, "peak": peak, "applied": applied, "pf": pf,
             "basic": basic, "energy": energy, "pfc": pfc, "vat": vat, "total": basic + energy + pfc + vat}
        key = f"{i}:{r['fac']['name']}:{r['total']}"
        if have_rl:
            pdf = S.bill_pdf(r)
            lines = [ln for p in PdfReader(io.BytesIO(pdf)).pages for ln in (p.extract_text() or "").splitlines() if ln.strip()]
            cache[key] = lines
        else:   # reportlab이 없는 PC: 같은 시드로 만든 고지서 PDF에서 미리 읽어 둔 글자를 쓴다
            lines = cache[key]
        doc = Document(id=f"b{i}", name=f"b{i}.pdf", relpath=f"b{i}.pdf", ext=".pdf",
                       blocks=[Block(f"b{i}-{k}", f"b{i}", f"b{i}.pdf", f"p.1 {k + 1}줄", t) for k, t in enumerate(lines)])
        ds = ingest.Dataset()
        ds.facilities[f["name"]] = {"name": f["name"]}
        rec, _, missing, _ = ingest.parse_bill(doc, ds, None)
        truth = {"contract": f["contract"], "applied": applied, "peak": peak, "off": bands[0], "mid": bands[1], "on": bands[2], "kwh": kwh,
                 "pf": pf, "basic": basic, "energy": energy, "pfc": pfc, "vat": vat, "total": r["total"], "ym": r["ym"]}
        good = 0
        bad = []
        for k in fields:
            if k == "fac":
                v_ok = f["name"] in str(rec.get("fac") or "")
            else:
                v_ok = rec.get(k) == truth[k] or (isinstance(truth[k], float) and rec.get(k) is not None and abs(rec[k] - truth[k]) < 1e-6)
            good += v_ok
            if not v_ok:
                bad.append(f"{k}: 읽음 {rec.get(k)} / 정답 {truth.get(k, f['name'])}")
        ok_fields += good
        if good == len(fields):
            ok_bills += 1
        else:
            fails.append(f"고지서 {i + 1}: " + "; ".join(bad))
    if have_rl:
        cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    return [{"name": "고지서 읽기(장 단위)", "passed": ok_bills, "total": n, "failures": fails,
             "desc": f"값을 무작위로 바꾼 고지서 PDF {n}장에서 15개 항목을 모두 맞게 읽은 장 수(항목 단위 {ok_fields}/{n * len(fields)})"
                     + ("" if have_rl else ". 이 PC에 reportlab이 없어 같은 시드로 만들어 둔 PDF의 추출 글자로 잼")}]


# ------------------------------------------------------------ 3. 질문 이해

# (질문, 답에 있어야 할 낱말들) — 낱말은 질문을 쓸 때 함께 정했다
QUESTIONS = [
    ("체육센터 8월 전기 왜 늘었어?", ["종합체육센터", "8월"]),
    ("종합체육센터 7월 전기 사용량 알려줘", ["종합체육센터", "7월"]),
    ("문화예술회관 2월 왜 이렇게 많이 썼어?", ["문화예술회관", "휴관"]),
    ("회관 2월 가스 사용 이상해?", ["문화예술회관", "가스"]),
    ("복지관 역률 문제 있어?", ["복지관", "역률"]),
    ("역률 문제 있는 곳?", ["복지관", "역률"]),
    ("주차장 기본요금 줄일 수 있어?", ["공영주차장", "요금적용전력"]),
    ("계약전력 줄일 수 있는 시설은?", ["공영주차장"]),
    ("요금적용전력 너무 높은 곳 있어?", ["공영주차장"]),
    ("휴관 때 전기 낭비한 곳?", ["문화예술회관", "휴관"]),
    ("고지서 요금이 틀린 곳 있어?", ["본부 청사"]),
    ("청사 6월 요금 맞아?", ["본부 청사", "6월"]),
    ("얼마나 아낄 수 있어?", ["원"]),
    ("절감할 수 있는 금액이 얼마야?", ["원"]),
    ("감축 목표 달성할 수 있어?", ["목표", "%"]),
    ("올해 목표 달성했어?", ["목표"]),
    ("면적당 가장 많이 쓰는 시설은?", ["종합체육센터"]),
    ("가장 전기를 많이 쓰는 시설은?", ["종합체육센터"]),
    ("LED 교체 효과 있었어?", ["LED", "%"]),
    ("조명 바꾼 효과 얼마야?", ["LED"]),
    ("체육센터 1월 가스 왜 많아?", ["종합체육센터", "가스", "1월"]),
    ("복지관 5월 전기 정상이야?", ["복지관", "5월"]),
    ("본부 청사 8월 전기 사용량은?", ["본부 청사", "8월"]),
    ("주차장 3월 전기 얼마나 썼어?", ["공영주차장", "3월"]),
    ("2026년 8월 체육센터 전기", ["종합체육센터", "8월"]),
    ("문화예술회관 전기 사용 어때?", ["문화예술회관"]),
    ("복지관은 문제 없어?", ["복지관"]),
    ("수영장 있는 시설 여름 전기 왜 늘었어?", ["종합체육센터"]),
    ("보일러 효율 점검이 필요한 곳은?", ["종합체육센터", "가스"]),
    ("지난달 전기 많이 쓴 곳 있어?", ["8월"]),
]


# 고치는 데 쓰지 않은 두 번째 문항(2026-10-29 작성, 3차 개선 뒤 처음 측정). 틀려도 앱을 이 문항에 맞춰 고치지 않는다
HOLDOUT = [
    ("체육관 쪽 7월 전기 사용 이상 없어?", ["종합체육센터", "7월"]),
    ("공연장 2월 전기 얼마야?", ["문화예술회관", "2월"]),
    ("노유자시설 역률 어때?", ["복지관", "역률"]),
    ("지하주차장 계약전력 적정해?", ["공영주차장"]),
    ("업무시설 6월 고지서 확인해줘", ["본부 청사", "6월"]),
    ("청사 전기요금 고지 금액 오류 있어?", ["본부 청사"]),
    ("어느 시설이 기본요금을 너무 많이 내?", ["공영주차장"]),
    ("역률요금 내고 있는 시설?", ["복지관"]),
    ("공사로 문 닫은 동안 에너지 썼어?", ["문화예술회관"]),
    ("LED 바꾼 회관 효과는?", ["LED"]),
    ("가스 많이 쓴 달 있어?", ["종합체육센터", "가스"]),
    ("체육센터 가스 1월", ["종합체육센터", "1월"]),
    ("복지관 3월 전기", ["복지관", "3월"]),
    ("작년보다 전기 많이 쓴 곳?", ["종합체육센터"]),
    ("체육센터 여름 냉방 점검할 거 있어?", ["종합체육센터"]),
    ("요금 아낄 방법 알려줘", ["원"]),
    ("총 절감 가능 금액?", ["원"]),
    ("탄소 배출 줄이는 목표 얼마나 남았어?", ["목표"]),
    ("5% 감축 가능해?", ["목표"]),
    ("주차장 전기 문제 없어?", ["공영주차장"]),
    ("회관 휴관 때 전기", ["문화예술회관", "휴관"]),
    ("청사 8월 전기 정상이야?", ["본부 청사", "8월"]),
    ("복지관 콘덴서 점검 필요해?", ["복지관", "역률"]),
    ("냉방 때문에 늘어난 거야 아니면 설비 문제야? 체육센터 8월", ["종합체육센터", "8월"]),
    ("면적 대비 전기 제일 적게 쓰는 곳?", ["공영주차장"]),
    ("전기 사용 순위 알려줘", ["종합체육센터"]),
    ("2026년 2월 문화예술회관 가스", ["문화예술회관", "가스"]),
    ("청구 금액 틀린 고지서?", ["본부 청사"]),
    ("체육센터 수영장 보일러 점검?", ["종합체육센터", "가스"]),
    ("복지관 1월 가스 사용량", ["복지관", "1월"]),
]


def eval_questions():
    an = sample_analysis()
    ok, fails = 0, []
    for q, need in QUESTIONS:
        a = qa.answer(an, q, None)["answer"]
        miss = [w for w in need if w not in a]
        if miss:
            fails.append(f"'{q}' → 답에 {', '.join(miss)} 없음 (답: {a[:60]}…)")
        else:
            ok += 1
    ok2, fails2 = 0, []
    for q, need in HOLDOUT:
        a = qa.answer(an, q, None)["answer"]
        miss = [w for w in need if w not in a]
        if miss:
            fails2.append(f"'{q}' → 답에 {', '.join(miss)} 없음 (답: {a[:60]}…)")
        else:
            ok2 += 1
    return [{"name": "질문 이해(개선에 쓴 문항)", "passed": ok, "total": len(QUESTIONS), "failures": fails,
             "desc": "개발자가 미리 적은 질문 30개(시설 줄임말·주제·달 섞음). 1차에서 틀린 2건을 보고 앱을 고쳤으므로 참고용"},
            {"name": "질문 이해(개선에 쓰지 않은 문항)", "passed": ok2, "total": len(HOLDOUT), "failures": fails2,
             "desc": "3차 개선 뒤 새로 적어 한 번만 잰 질문 30개(용도·다른 표현으로 시설을 부름). 틀린 것을 보고 앱을 고치지 않음"}]


_SAMPLE = {}


def sample_analysis():
    if "an" not in _SAMPLE:
        docs, _ = load_folder(ROOT / "sample_data")
        ds, _ = ingest.build(docs)
        _SAMPLE["an"] = analysis.Analysis(ds, CFG).run()
    return _SAMPLE["an"]


# ------------------------------------------------------------ 4. 지어낸 숫자 차단

class FakeLLM:
    enabled = True

    def __init__(self, reply):
        self.reply = reply
        self.cfg = {"model": "fake"}

    def chat(self, messages, **kw):
        return self.reply(messages)


def eval_fabrication(n=200):
    qa.RATE_MAX = 10**9  # 공개 시연판용 1분 호출 제한은 가짜 모델 시험에는 끈다(제한에 걸리면 대조 자체를 안 하므로)
    an = sample_analysis()
    rnd = random.Random(11)
    qs = [q for q, _ in QUESTIONS]
    blocked, fails, tried = 0, [], 0
    asks = [q for q in qs if qa.facts_for(an, q)[0]]  # 사실이 없는 질문은 모델을 부르지 않으므로 뺀다
    while tried < n:
        q = rnd.choice(asks)
        tried += 1
        facts, _, rule = qa.facts_for(an, q)
        allowed = qa._nums("\n".join(facts) + " " + q)
        while True:
            v = rnd.choice([rnd.randint(13, 999), rnd.randint(1000, 9_999_999), round(rnd.uniform(13, 99), 1)])
            if v not in allowed and round(v) not in allowed:
                break
        tmpl = rnd.choice(["또한 약 {v:,}kWh를 더 줄일 수 있습니다.", "전년보다 {v}% 늘었습니다.", "요금은 {v:,}원 더 나왔습니다.", "{v}℃ 기준으로 보면 정상입니다."])
        fake = FakeLLM(lambda m, t=rule + " " + tmpl.format(v=v): t)
        r = qa.answer(an, q, fake)
        if r["mode"] == "규칙 기반" and "사실에 없는 숫자" in r["dropped"]:
            blocked += 1
        else:
            fails.append(f"'{q}'에 섞은 숫자 {v}를 걸러 내지 못함")
    ok_clean, clean_fail = 0, []
    for q in asks:
        facts, _, rule = qa.facts_for(an, q)
        r = qa.answer(an, q, FakeLLM(lambda m, t=rule: t))
        if r["mode"].startswith("모델"):
            ok_clean += 1
        else:
            clean_fail.append(f"'{q}': 사실 안의 숫자만 쓴 답을 버림({r['dropped']})")
    return [{"name": "지어낸 숫자 차단", "passed": blocked, "total": n, "failures": fails,
             "desc": f"가짜 모델이 답에 사실에 없는 숫자를 섞었을 때 그 답을 버리고 규칙 답을 쓴 횟수"},
            {"name": "바른 답은 살림", "passed": ok_clean, "total": len(asks), "failures": clean_fail,
             "desc": "사실 목록 안의 숫자만 쓴 모델 답을 잘못 버리지 않은 비율"}]


# ------------------------------------------------------------ 5. 개인정보 가림

PII_POS = ["담당 김○○ 010-2345-6789로 연락 바람", "주민번호 850315-2345678 확인", "계좌 110-234-567890 입금", "메일 manager@agency.go.kr",
           "카드 4321-8765-1234-5678 결제", "사무실 02-345-6789", "지역번호 031-456-7890", "외국인등록번호 900101-5123456",
           "여권번호 M12345678 사본", "휴대폰 01098765432", "010.1111.2222 문자", "국민은행 123456-12-345678", "신한 110-12-345678",
           "정산 담당 042-123-4567", "개인 메일 abc.def@example.com 회신", "주민 9001011234567", "농협 301-1234-5678-91",
           "번호 010 3333 4444", "팩스 02-777-8888", "주민등록 760505 1234567", "a_b@test.co.kr로 보내 줘", "070-1234-5678 인터넷전화",
           "카드번호 1111 2222 3333 4444", "우리 1002-345-678901", "051-234-5678 부산", "연락 011-234-5678", "여권 S98765432",
           "메일주소: hong.gildong@agency.kr", "공단 담당 053-230-1234", "계좌 3333-01-1234567"]


def eval_pii():
    an = sample_analysis()
    ok_pos, pos_fail = 0, []
    for t in PII_POS:
        out, kinds = mask(t)
        if kinds and "@" not in out and not re.search(r"\d{3,}", out.replace(" ", "")):
            ok_pos += 1
        else:
            pos_fail.append(f"'{t}' → '{out}'")
    neg = set()
    for q, _ in QUESTIONS:
        neg.add(qa.answer(an, q, None)["answer"])
        neg.add(q)
    for f in an.findings:
        neg.add(f["title"])
        neg.add(f["summary"])
        for e in f["evidence"]:
            neg.add(e["label"])
    neg = sorted(neg)
    ok_neg, neg_fail = 0, []
    for t in neg:
        out, kinds = mask(t)
        if not kinds:
            ok_neg += 1
        else:
            neg_fail.append(f"잘못 가림({', '.join(kinds)}): {t[:60]}")
    return [{"name": "개인정보 가림", "passed": ok_pos, "total": len(PII_POS), "failures": pos_fail,
             "desc": "주민번호·전화·계좌·카드·이메일·여권이 든 문장 30개를 가린 비율"},
            {"name": "개인정보 오탐 없음", "passed": ok_neg, "total": len(neg), "failures": neg_fail,
             "desc": "앱이 실제로 만든 답·점검 결과·근거 문장(날짜·kWh·원·% 투성이)을 잘못 가리지 않은 비율"}]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="정확도 성적표 만들기")
    ap.add_argument("--note", default="", help="측정 이력에 남길 한 줄(무엇을 고친 뒤 쟀는지)")
    note = ap.parse_args().note
    t0 = time.time()
    areas = eval_detect() + eval_bills() + eval_questions() + eval_fabrication() + eval_pii()
    out = ROOT / "검증결과"
    out.mkdir(exist_ok=True)
    prev = None
    try:
        prev = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    summary = ", ".join(f"{a['name']} {a['passed']}/{a['total']}" for a in areas)
    hist = (prev or {}).get("history", [])
    if not hist or hist[-1]["summary"] != summary or note:
        hist.append({"date": date.today().isoformat(), "summary": summary, "note": note})
    sc = {"app": "에너지 절감 비서", "date": date.today().isoformat(), "command": "python tools/eval.py",
          "author": "개발자 작성(블라인드 아님) · 무작위 자료는 시드 고정으로 누구나 같은 결과", "seconds": round(time.time() - t0, 1),
          "areas": areas, "history": hist[-10:]}
    (out / "scorecard.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# 정확도 성적표 (에너지 절감 비서)", "", f"- 측정일: {sc['date']} · 재현: `{sc['command']}` (모델·인터넷 불필요, {sc['seconds']}초)",
          f"- 문항: {sc['author']}", "", "| 시험 | 결과 | 무엇을 쟀나 |", "|---|---|---|"]
    for a in areas:
        pct = f" ({a['passed'] / a['total'] * 100:.1f}%)" if a["total"] else ""
        md.append(f"| {a['name']} | {a['passed']}/{a['total']}{pct} | {a['desc']} |")
    md += ["", "## 틀린 문항 (고치지 않고 공개)", ""]
    fails = [(a["name"], f) for a in areas for f in a["failures"]]
    md += [f"- [{n}] {f}" for n, f in fails] or ["- 없음"]
    md += ["", "## 측정 이력", ""] + [f"- {i}차 {h['date']}" + (f" ({h['note']})" if h.get("note") else "") + f": {h['summary']}" for i, h in enumerate(hist[-10:], 1)]
    (out / "정확도_성적표.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:8 + len(areas)]))
    print(f"\n틀린 문항 {len(fails)}건 · 결과: {out / '정확도_성적표.md'}")


if __name__ == "__main__":
    main()
