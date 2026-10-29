"""질문 답변: 분석 결과(계산된 사실)에서만 답한다.

모델을 연결하면 같은 사실 목록을 주고 문장만 다듬게 하되, 모델 답에 사실 목록에 없는 숫자가 있으면
그 답을 버리고 규칙 답을 쓴다(지어낸 수치 차단).
"""
import re

from core.llm import LLMError
from ingest import ym_add

RX_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def _facility(an, q):
    qs = q.replace(" ", "")
    for name in an.ds.facilities:
        n = name.replace(" ", "")
        if n in qs:
            return name
    for name, f in an.ds.facilities.items():  # '수영장 있는 시설'처럼 용도로 말한 경우(시설 현황의 용도 열)
        for w in re.findall(r"[가-힣]{2,}", f.get("use") or ""):
            if w not in ("시설", "지하") and (w in qs or (len(w) > 3 and w[-3:] in qs)):
                return name
    for name in an.ds.facilities:  # '체육센터', '주차장'처럼 일부만 말한 경우
        for k in range(len(name), 1, -1):
            for i in range(0, len(name) - k + 1):
                part = name[i:i + k].replace(" ", "")
                if len(part) >= 2 and part in qs and part not in ("공공", "시설", "센터"):
                    return name
    return None


def _month(an, q):
    m = re.search(r"(20\d{2})\s*년\s*(\d{1,2})\s*월", q)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r"(\d{1,2})\s*월", q)
    if m and 1 <= int(m.group(1)) <= 12:
        # 가장 최근의 그 달
        for ym in reversed(an.months):
            if int(ym[5:]) == int(m.group(1)):
                return ym
    if re.search(r"지난달|최근 달|이번 달|저번 달", q):
        return an.latest
    return None


def _fmt(v, unit=""):
    return f"{v:,.0f}{unit}" if isinstance(v, (int, float)) else str(v)


def facts_for(an, q):
    """질문에 맞는 사실 목록과 출처. (사실 문장 목록, 출처 목록, 규칙 답)"""
    fac = _facility(an, q)
    ym = _month(an, q)
    kind = "gas" if re.search(r"가스|보일러|난방비", q) else "elec"
    unit = "㎥" if kind == "gas" else "kWh"
    facts, srcs = [], []
    ov = an.overview()

    if fac and ym:
        rows = {r["ym"]: r for r in an.series.get((fac, kind), [])}
        r = rows.get(ym)
        if r:
            prev = rows.get(ym_add(ym, -12))
            facts.append(f"{fac} {ym} {'가스' if kind == 'gas' else '전기'} 실제 사용량 {_fmt(r['actual'], unit)}")
            if r["expected"]:
                facts.append(f"기온만 감안한 예상 사용량 {_fmt(r['expected'], unit)} (차이 {r['diff_pct']:+.1f}%)")
            if r["temp"] is not None:
                facts.append(f"그 달 월평균기온 {r['temp']}℃")
            if prev:
                ch = (r["actual"] - prev["actual"]) / prev["actual"] * 100
                facts.append(f"전년 같은 달 {_fmt(prev['actual'], unit)} (전년 대비 {ch:+.1f}%)"
                             + (f", 그때 기온 {prev['temp']}℃" if prev["temp"] is not None else ""))
            if r["closed"]:
                facts.append(f"그 달 휴관 비율 {round(r['closed'] * 100)}% ({r['closed_reason']})")
            srcs.append(r["src"])
            hits = [f for f in an.findings if f["fac"] == fac and ym in f["months"] and f["kind"] == kind]
            for f in hits:
                facts.append(f"점검 결과: {f['title']}. {f['summary']}")
                srcs += [e["src"] for e in f["evidence"] if e.get("src")]
            over = [f for f in hits if f["type"] in ("excess", "gas_excess")]
            closed = [f for f in hits if f["type"] == "closure"]
            if not over and not closed and r["diff_pct"] is not None:
                th = an.g["excess_pct"] if kind == "elec" else an.g["gas_excess_pct"]
                facts.append(f"기온 보정 후 차이가 지침 기준({th:.0f}%) 안이라 이상 사용으로 보지 않음")
            # 규칙 답
            lines = [f"{fac}의 {int(ym[5:])}월 {'가스' if kind == 'gas' else '전기'} 사용량은 {_fmt(r['actual'], unit)}입니다."]
            if prev:
                lines.append(f"전년 같은 달({_fmt(prev['actual'], unit)})보다 {ch:+.1f}% {'늘었' if ch > 0 else '줄었'}습니다.")
            if closed:
                lines.append(closed[0]["summary"] + " 휴관을 감안하면 많이 쓴 달입니다.")
            elif r["expected"] and over:
                lines.append(f"그 달 기온({r['temp']}℃)만 감안하면 {_fmt(r['expected'], unit)}가 예상되는데 실제가 {r['diff_pct']:+.1f}% 많아, 날씨만으로는 설명되지 않습니다.")
            elif r["expected"]:
                lines.append(f"기온({r['temp']}℃)을 감안한 예상치({_fmt(r['expected'], unit)})와 {r['diff_pct']:+.1f}% 차이로, 지침 기준 안이라 이상 사용으로 보지 않습니다.")
            for f in hits:
                lines.append(f"점검 결과: {f['title']}. 확인할 일: " + "; ".join(f["steps"][:3]))
            return facts, srcs, " ".join(lines)

    topic = [("보일러|연소", "gas_excess"), ("역률", "pf"), ("계약전력|기본요금|요금적용", "contract"), ("휴관|공사", "closure"),
             ("고지서|요금.*(틀|맞|다르|오류)|청구", "bill")]
    for rx, t in topic:
        if re.search(rx, q):
            hits = [f for f in an.findings if f["type"] == t and (not fac or f["fac"] == fac)]
            if hits:
                for f in hits:
                    facts.append(f"{f['title']}. {f['summary']} {f['amount_label']} {_fmt(f['amount'], '원')}")
                    srcs += [e["src"] for e in f["evidence"] if e.get("src")]
                return facts, srcs, "\n".join(f"{f['title']}: {f['summary']} 할 일: " + "; ".join(f["steps"]) for f in hits)
            return facts, srcs, "해당하는 점검 결과가 없습니다."

    if re.search(r"목표|달성", q):
        tg = ov.get("target")
        if not tg:
            return facts, srcs, "넣은 지침에서 감축 목표 조문을 찾지 못했습니다."
        facts += [f"지침 감축 목표: 기온 보정 전기 사용량 {tg['target_pct']:g}% 감축",
                  f"최근 12개월(휴관 달 제외) 실제 {_fmt(tg['actual'], 'kWh')}, 기온만 감안한 기준선 {_fmt(tg['expected'], 'kWh')}, 변화 {_p(tg['change_pct'])}",
                  f"목표까지 더 줄여야 할 양 {_fmt(tg['gap_kwh'], 'kWh')}",
                  f"점검 결과의 초과 사용 {_fmt(tg['fix_kwh'], 'kWh')}를 없앴다면 변화 {_p(tg['after_fix_pct'])}"]
        facts += [f"{x['name']} 기온 보정 변화 {_p(x['change_pct'])}" for x in tg["facilities"]]
        if tg["basis"]:
            srcs.append({"file": tg["basis"]["file"], "loc": tg["basis"]["loc"], "quote": tg["basis"]["text"][:160]})
        worst = tg["facilities"][0]
        ans = (f"지침 목표는 기온을 감안한 전기 사용량 {tg['target_pct']:g}% 감축인데, 최근 12개월(휴관 달 제외)은 기준선 대비 {_p(tg['change_pct'])}로 "
               f"{'목표를 달성했습니다' if tg['gap_kwh'] == 0 else '아직 목표에 못 미칩니다. ' + _fmt(tg['gap_kwh'], 'kWh') + '를 더 줄여야 합니다'}. "
               f"점검 결과의 초과 사용을 조치했다면 {_p(tg['after_fix_pct'])}였습니다. 기준선보다 가장 많이 늘어난 곳은 {worst['name']}({_p(worst['change_pct'])})입니다.")
        return facts, srcs, ans

    if re.search(r"절감|아낄|줄일|얼마|효과", q):
        t = ov["totals"]
        facts.append(f"점검 결과 {t['findings']}건, 찾은 금액 합계(추정) {_fmt(t['amount'], '원')}")
        lines = [f"이번 점검에서 {t['findings']}건을 찾았고, 항목별로 계산한 금액은 모두 {_fmt(t['amount'], '원')}입니다(추정, 항목마다 기준이 다름)."]
        for f in an.findings[:5]:
            if f.get("amount"):
                facts.append(f"{f['title']}: {f['amount_label']} {_fmt(f['amount'], '원')}")
                lines.append(f"- {f['title']}: {f['amount_label']} {_fmt(f['amount'], '원')}")
        for e in an.effects():
            if e.get("saved_kwh") is not None:
                facts.append(f"조치 효과: {e['fac']} {e['title']} 시행 후 {e['months']}개월 {_fmt(e['saved_kwh'], 'kWh')} 절감({e['saved_pct']}%), {_fmt(e['saved_won'], '원')}")
                lines.append(f"이미 시행한 조치 중 {e['fac']} '{e['title']}'은 {e['months']}개월 동안 {_fmt(e['saved_kwh'], 'kWh')}({e['saved_pct']}%)를 줄였습니다.")
        return facts, srcs, "\n".join(lines)

    if re.search(r"가장|제일|순위|많이 쓰", q):
        facs = sorted(ov["facilities"], key=lambda f: -(f["kwh_per_m2"] or 0))
        lines = ["최근 12개월 면적당 전기 사용량 순위입니다."]
        for i, f in enumerate(facs, 1):
            facts.append(f"{f['name']} 연간 {_fmt(f['kwh'], 'kWh')}, 면적당 {f['kwh_per_m2']}kWh/㎡")
            lines.append(f"{i}. {f['name']}: {f['kwh_per_m2']}kWh/㎡ (연간 {_fmt(f['kwh'], 'kWh')})")
        lines.append("용도가 다르면(수영장·공연장 등) 면적당 사용량도 차이가 크므로 같은 시설의 전년·기준선과 비교한 결과를 함께 보세요.")
        return facts, srcs, "\n".join(lines)

    if fac:
        f = next(x for x in ov["facilities"] if x["name"] == fac)
        facts.append(f"{fac} 최근 12개월 전기 {_fmt(f['kwh'], 'kWh')}, 전년 대비 {f['change_pct']}%, 기온 보정 후 {f['norm_pct']}%")
        hits = [x for x in an.findings if x["fac"] == fac]
        lines = [f"{fac}의 최근 12개월 전기 사용량은 {_fmt(f['kwh'], 'kWh')}로 전년보다 {_p(f['change_pct'])}, 기온을 감안하면 {_p(f['norm_pct'])}입니다."
                 if f["change_pct"] is not None and f["norm_pct"] is not None else f"{fac}의 최근 12개월 전기 사용량은 {_fmt(f['kwh'], 'kWh')}입니다."]
        for x in hits:
            facts.append(f"점검 결과: {x['title']}")
            lines.append(f"- {x['title']}")
        return facts, srcs, "\n".join(lines)

    lines = ["이렇게 물어보세요: \"체육센터 8월 전기 왜 늘었어?\", \"얼마나 아낄 수 있어?\", \"역률 문제 있는 곳?\", \"면적당 가장 많이 쓰는 시설은?\""]
    return facts, srcs, "\n".join(lines)


def _nums(text):
    out = set()
    for m in RX_NUM.findall(text or ""):
        v = m.replace(",", "")
        try:
            out.add(round(float(v), 1))
        except ValueError:
            pass
    return out


def answer(an, q, llm=None):
    facts, srcs, rule = facts_for(an, q)
    res = {"answer": rule, "mode": "규칙 기반", "facts": facts, "sources": [s for s in srcs if s][:6], "dropped": ""}
    if not (llm and llm.enabled and facts):
        return res
    try:
        text = llm.chat([
            {"role": "system", "content": "당신은 공공기관 에너지 관리 담당자를 돕습니다. 아래 '사실'에 있는 내용과 숫자만 써서 질문에 한국어로 3~5문장으로 답하세요. "
                                          "사실에 없는 숫자·원인·추측은 쓰지 마세요. 원인을 단정하지 말고 확인할 일을 말하세요."},
            {"role": "user", "content": "사실:\n" + "\n".join(f"- {f}" for f in facts) + f"\n\n질문: {q}"}], max_tokens=600)
    except LLMError as e:
        res["dropped"] = f"모델 응답 실패로 규칙 답을 보여 줍니다({e})"
        return res
    allowed = _nums("\n".join(facts) + " " + q + " " + rule) | {float(i) for i in range(0, 13)}
    extra = sorted(n for n in _nums(text) if n not in allowed and round(n) not in allowed)
    if extra:
        res["dropped"] = f"모델 답에 사실에 없는 숫자({', '.join(str(x) for x in extra[:3])})가 있어 규칙 답을 보여 줍니다"
        return res
    res.update(answer=text.strip(), mode="모델(사실 대조 통과)")
    return res


def _p(v):
    return "변화 없음(0.0%)" if abs(v) < 0.05 else f"{v:+.1f}%"
