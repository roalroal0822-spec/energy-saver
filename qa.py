"""질문 답변: 분석 결과(계산된 사실)에서만 답한다.

모델을 연결하면 같은 사실 목록을 주고 문장만 다듬게 하되, 모델 답에 사실 목록에 없는 숫자가 있으면
그 답을 버리고 규칙 답을 쓴다(지어낸 수치 차단).
"""
import re
import time
from collections import deque

from core.llm import LLMError

# 모델 호출 한도(1분에 40번). 공개 시연판에서 키가 빨리 소진되지 않게 하고, 넘으면 규칙 문장을 쓴다
RATE = deque()
RATE_MAX = 40
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

    if re.search(r"예산|불용|추경|집행|편성|내년|유가|전용|모자라|부족", q):
        import budget
        b = budget.Budget(an, an.cfg)
        if re.search(r"편성|내년|유가", q):
            ny = b.next_year()
            t = ny["totals"]
            items = [i for i in ny["items"] if not fac or i["fac"] == fac]
            facts.append(f"{ny['fy']}년도 공공요금 요구액 합계 {_fmt(t['request'], '원')}" + (f", 올해 예산현액 대비 {t['change_pct']:+.1f}%" if t["change_pct"] is not None else "")
                         + f", 기후 위험 여유분 {_fmt(t['risk'], '원')}")
            for i in items:
                facts.append(f"{i['fac']} {i['item']} 요구액 {_fmt(i['request'], '원')}: {i['basis']}")
            oil = ny["oil"]
            if oil.get("has"):
                facts.append(f"두바이유 최근 12개월 평균 {oil['now_avg']}달러, 내년 반영 시기 평균 {oil['next_avg']}달러(전망), 가스 단가 {oil['gas_change_pct']:+.1f}%")
            lines = [f"{ny['fy']}년도 공공요금 예산은 {_fmt(t['request'], '원')}이 필요합니다"
                     + (f"(올해 예산현액 대비 {t['change_pct']:+.1f}%)" if t["change_pct"] is not None else "") + "."]
            if oil.get("has"):
                lines.append(f"국제유가(두바이유)가 최근 평균 {oil['now_avg']}달러에서 {oil['next_avg']}달러로 오를 전망이라 가스 단가를 {oil['gas_change_pct']:+.1f}% 반영했습니다.")
            lines.append(f"이상기온(폭염·한파)에 대비하려면 기후 위험 여유분 {_fmt(t['risk'], '원')}을 더 검토하세요.")
            lines += [f"- {i['fac']} {i['item']}: {_fmt(i['request'], '원')} ({i['basis']})" for i in items[:6]]
            return facts, srcs, "\n".join(lines)
        cur = b.current()
        if not cur or not cur["items"]:
            return facts, srcs, "예산서가 없습니다. '자료 넣기'에 본예산·추경 엑셀을 넣어 주세요."
        t = cur["totals"]
        items = [i for i in cur["items"] if not fac or i["fac"] == fac]
        facts.append(f"{cur['fy']}년 공공요금 예산현액 {_fmt(t['current'], '원')}, 집행 {_fmt(t['spent'], '원')}(집행률 {t['exec_pct']}%, 경과율 {cur['elapsed_pct']}%), "
                     f"연말 예상 집행 {_fmt(t['expected'], '원')}, 예상 잔액 {_fmt(t['remain'], '원')}")
        for i in items:
            facts.append(f"{i['fac']} {i['item']} 예산현액 {_fmt(i['current'], '원')}, 집행 {_fmt(i['spent'], '원')}, 남은 달 예측 {_fmt(i['fc'], '원')}, "
                         f"예상 잔액 {_fmt(i['remain'], '원')}({i['remain_pct']:+.1f}%), {i['state']}" + (f", {int(i['run_out'][5:])}월분부터 부족" if i["run_out"] else ""))
            srcs.append(i["src"])
        lines = [f"{cur['fy']}년 공공요금 예산 {_fmt(t['current'], '원')} 중 {cur['latest'][5:].lstrip('0')}월분까지 {_fmt(t['spent'], '원')}을 썼고(집행률 {t['exec_pct']}%), "
                 f"남은 달 예측을 더하면 연말 잔액은 {_fmt(t['remain'], '원')}로 예상됩니다."]
        for i in items:
            if i["state"] == "부족 예상":
                lines.append(f"- {i['fac']} {i['item']}: {int(i['run_out'][5:])}월분부터 부족, {_fmt(-i['remain'], '원')} 모자랄 전망입니다.")
            elif i["state"] == "불용 예상":
                lines.append(f"- {i['fac']} {i['item']}: {_fmt(i['remain'], '원')}({i['remain_pct']:.1f}%) 남을 전망(불용 예상)입니다.")
            elif fac:
                lines.append(f"- {i['fac']} {i['item']}: 예상 잔액 {_fmt(i['remain'], '원')}로 적정합니다.")
        for m in cur["moves"]:
            facts.append(f"전용 검토: {m['from']} → {m['to']} {_fmt(m['amount'], '원')}")
            lines.append(f"전용 검토: {m['from']} 잔액에서 {m['to']}로 {_fmt(m['amount'], '원')}을 옮기면 부족분을 줄일 수 있습니다(기관 규정 확인).")
        return facts, srcs, "\n".join(lines)

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


def guard(llm, system, facts, rule, user=None, extra="", history=None, max_tokens=700):
    """모델에게 사실 목록만 주고 문장을 쓰게 한 뒤, 사실에 없는 숫자가 있으면 버리고 규칙 문장을 쓴다.

    돌려주는 값: {"text", "mode", "dropped"}. 모델이 없거나 실패하면 규칙 문장(mode='규칙 기반').
    extra 는 숫자 대조 때 허용할 추가 문장(질문, 앞선 대화 등).
    """
    res = {"text": rule, "mode": "규칙 기반", "dropped": ""}
    if not (llm and llm.enabled and facts):
        return res
    now = time.time()
    while RATE and now - RATE[0] > 60:
        RATE.popleft()
    if len(RATE) >= RATE_MAX:
        res["dropped"] = "요청이 많아 잠시 규칙 문장을 씁니다(1분 뒤 다시 시도)"
        return res
    RATE.append(now)
    msgs = [{"role": "system", "content": system + " 아래 '사실'에 있는 숫자·시설·근거만 쓰고, 사실에 없는 숫자·원인·추측은 쓰지 마세요."}]
    for h in history or []:
        msgs += [{"role": "user", "content": h["q"]}, {"role": "assistant", "content": h["a"]}]
    msgs.append({"role": "user", "content": "사실:\n" + "\n".join(f"- {f}" for f in facts) + (f"\n\n{user}" if user else "")})
    try:
        text = llm.chat(msgs, max_tokens=max_tokens).strip()
    except LLMError as e:
        res["dropped"] = f"모델 응답 실패로 규칙 문장을 씁니다({e})"
        return res
    if not text:
        res["dropped"] = "모델이 빈 답을 돌려줘 규칙 문장을 씁니다"
        return res
    hist = " ".join(h["q"] + " " + h["a"] for h in history or [])
    allowed = _nums("\n".join(facts) + " " + (user or "") + " " + rule + " " + extra + " " + hist) | {float(i) for i in range(0, 13)}
    bad = sorted(n for n in _nums(text) if n not in allowed and round(n) not in allowed)
    if bad:
        res["dropped"] = f"모델 문장에 사실에 없는 숫자({', '.join(f'{x:g}' for x in bad[:3])})가 있어 규칙 문장을 씁니다"
        return res
    res.update(text=text, mode="모델(사실 대조 통과)")
    return res


RX_FOLLOW = re.compile(r"^\s*(그럼|그러면|그리고|그건|그거|거기|그곳|그 시설|그 달|그달|그때|작년|전년|가스는|전기는|왜\??$)")


def carry(an, q, history):
    """이어 묻는 질문('그럼 가스는?', '작년은?')이면 앞 질문의 시설·달을 붙인다. (새 질문, 붙인 말)"""
    if not history:
        return q, ""
    short = len(q.replace(" ", "")) <= 10
    if not (RX_FOLLOW.search(q) or short):
        return q, ""
    add = []
    for h in reversed(history):
        pq = h["q"]
        if not _facility(an, q) and _facility(an, pq) and not any(_facility(an, x) for x in add):
            add.append(_facility(an, pq))
        m = re.search(r"(20\d{2}\s*년\s*)?\d{1,2}\s*월", pq)
        if not _month(an, q) and m and not any(re.search(r"\d월", x) for x in add):
            add.append(m.group(0))
        if len(add) == 2:
            break
    if not add:
        return q, ""
    return " ".join(add) + " " + q, " ".join(add)


def overview_facts(an):
    """주제를 못 찾은 질문에 모델이 쓸 수 있는 기관 전체 사실."""
    ov = an.overview()
    t = ov["totals"]
    facts = [f"최근 12개월({ov['recent'][0]}~{ov['recent'][1]}) 시설 {len(ov['facilities'])}곳 전기 {_fmt(t['kwh'], 'kWh')}, 전기·가스 요금 {_fmt(t['cost'], '원')}",
             f"점검 결과 {t['findings']}건, 찾은 금액 합계(추정) {_fmt(t['amount'], '원')}"]
    for f in ov["facilities"]:
        facts.append(f"{f['name']}: 전기 {_fmt(f['kwh'], 'kWh')}, 면적당 {f['kwh_per_m2']}kWh/㎡"
                     + (f", 전년 대비 {_p(f['change_pct'])}" if f["change_pct"] is not None else "")
                     + (f", 기온 보정 후 {_p(f['norm_pct'])}" if f["norm_pct"] is not None else ""))
    for x in an.findings[:8]:
        facts.append(f"점검 결과 {x['id']}: {x['title']}" + (f" ({x['amount_label']} {_fmt(x['amount'], '원')})" if x.get("amount") else ""))
    return facts


def answer(an, q, llm=None, history=None):
    history = [h for h in (history or []) if h.get("q") and h.get("a")][-4:]
    q2, carried = carry(an, q, history)
    facts, srcs, rule = facts_for(an, q2)
    general = False
    if not facts and llm and llm.enabled:
        facts, general = overview_facts(an), True
    res = {"answer": rule, "mode": "규칙 기반", "facts": facts, "sources": [s for s in srcs if s][:6], "dropped": "",
           "carried": carried, "general": general}
    g = guard(llm, "당신은 공공기관 에너지 관리 담당자를 돕습니다. 질문에 한국어로 3~5문장으로 답하세요. 원인을 단정하지 말고 확인할 일을 말하세요."
              + (" 사실만으로 답할 수 없으면 그렇다고 말하고, 한톨에서 볼 수 있는 화면을 알려 주세요." if general else ""),
              facts, rule, user=f"질문: {q2}", extra=q, history=history, max_tokens=700)
    res.update(answer=g["text"], mode=g["mode"], dropped=g["dropped"].replace("규칙 문장", "규칙 답"))
    return res


def _p(v):
    return "변화 없음(0.0%)" if abs(v) < 0.05 else f"{v:+.1f}%"
