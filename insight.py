"""외부 자료 종합 해석: 우리 시설 사용량 변화를 날씨(기상청)·지역 추세(한전)·연료 가격(오피넷)과 견줘
'날씨 탓인지, 지역 공통인지, 우리 시설 요인인지'를 나눈다. 판정은 규칙으로 하고, 모델은 사실 목록으로 문장만 쓴다."""

TH = 5.0  # 의미 있게 본 변화 폭(%p)


def _p(v):
    return "-" if v is None else f"{v:+.1f}%"


def _dd_sum(a, months):
    c = h = 0.0
    n = 0
    for ym in months:
        d = a.dd(ym)
        if d:
            c, h, n = c + d[0], h + d[1], n + 1
    return (round(c, 1), round(h, 1)) if n else None


def build(a, kp=None, oil=None, kma=None):
    ov = a.overview()
    # 전년 같은 달 기온이 있는 달끼리만 견준다
    pairs = [(ym, f"{int(ym[:4]) - 1}{ym[4:]}") for ym in a.recent]
    pairs = [(x, y) for x, y in pairs if x in a.ds.weather and y in a.ds.weather]
    now_dd, prev_dd = _dd_sum(a, [x for x, _ in pairs]), _dd_sum(a, [y for _, y in pairs])
    region = {f["fac"]: f for f in (kp or {}).get("facilities", [])}
    rows, facts = [], []
    weather = None
    if now_dd and prev_dd:
        weather = {"cdd": now_dd[0], "cdd_prev": prev_dd[0], "hdd": now_dd[1], "hdd_prev": prev_dd[1],
                   "months": len(pairs), "src": "기상청 지상관측(ASOS) 일자료" if (kma or {}).get("on") else "넣은 기온 자료"}
        facts.append(f"최근 {len(pairs)}개월({pairs[0][0]}~{pairs[-1][0]}) 냉방도일 {now_dd[0]:g}(전년 {prev_dd[0]:g}), 난방도일 {now_dd[1]:g}(전년 {prev_dd[1]:g}), 출처 {weather['src']}")
    for f in ov["facilities"]:
        c, n = f["change_pct"], f["norm_pct"]
        k = region.get(f["name"]) or {}
        r = k.get("region_yoy")
        w = round(c - n, 1) if c is not None and n is not None else None
        if n is None:
            verdict, lv, why = "판단 자료 부족", "gray", "기온 보정 기준선이 없어 날씨 영향을 나눌 수 없음"
        elif n >= TH and (r is None or c - r >= TH):
            verdict, lv = "시설 요인 가능성 큼", "bad"
            why = f"기온을 감안해도 {_p(n)} 늘었고" + (f", 지역 같은 계약종별({_p(r)})보다 증가 폭이 큼" if r is not None else " 지역 통계는 없음")
        elif n >= TH:
            verdict, lv, why = "지역 공통 추세", "warn", f"기온 보정 후 {_p(n)}이지만 지역 같은 계약종별도 {_p(r)}로 비슷하게 늘어 지역 공통 요인 가능성"
        elif c is not None and c >= TH and w is not None and w >= c / 2:
            verdict, lv, why = "날씨 영향이 큼", "ok", f"전년보다 {_p(c)} 늘었고 그중 {_p(w)}p는 기온으로 설명되어 기온 보정 후 {_p(n)}임"
        elif c is not None and c >= TH:
            verdict, lv, why = "날씨+운영", "warn", f"전년보다 {_p(c)} 늘었는데 기온으로 설명되는 몫은 {_p(w)}p뿐이고 기온 보정 후에도 {_p(n)}임"
        elif n <= -TH:
            verdict, lv, why = "절감 진행 중", "ok", f"기온을 감안해 {_p(n)} 줄었음"
        else:
            verdict, lv, why = "변화 작음", "gray", f"기온 보정 후 {_p(n)}로 기준 폭({TH:g}%) 안"
        exc = [x for x in a.findings if x["fac"] == f["name"] and x["type"] in ("excess", "gas_excess")]
        if exc:
            why += ". 다만 점검 결과(" + "; ".join(x["title"] for x in exc[:2]) + ")를 보면 그 달은 시설 요인을 확인해야 함"
            if lv in ("ok", "gray"):
                verdict, lv = verdict + " · 특정 달 초과", "warn"
        gap = k.get("unit_gap_pct")
        if gap is not None and gap >= 10:
            why += f". 전기 단가가 지역 같은 계약종별 평균보다 {gap:+.1f}% 높아 계약종별·요금제 확인 필요"
        rows.append({"fac": f["name"], "change_pct": c, "norm_pct": n, "weather_pct": w, "region_yoy": r,
                     "cntr": k.get("cntr") or "", "unit_gap_pct": gap, "verdict": verdict, "level": lv, "why": why})
        facts.append(f"{f['name']}: 전년 대비 {_p(c)}, 기온 보정 후 {_p(n)}, 날씨로 설명되는 몫 {_p(w)}"
                     + (f", 도봉구 {k.get('cntr')} 전년 대비 {_p(r)}" if r is not None else "")
                     + (f", 단가 지역 평균 대비 {gap:+.1f}%" if gap is not None else "") + f" → 판정: {verdict}")
    for x in a.findings[:6]:
        facts.append(f"점검 결과: {x['fac']} {x['title']}")
    oil_items = (oil or {}).get("items") or []
    for it in oil_items:
        facts.append(f"오피넷 {oil['date']} 전국 평균 {it['name']} {it['price']:,.2f}원/L (전일 대비 {it['diff']:+.2f})")
    order = {"bad": 0, "warn": 1, "ok": 2, "gray": 3}
    top = sorted(rows, key=lambda x: (order[x["level"]], -(x["norm_pct"] or 0)))
    rule = []
    bad = [x for x in top if x["level"] == "bad"]
    if bad:
        rule.append("우선 볼 곳: " + ", ".join(f"{x['fac']}({x['why'].split('.')[0]})" for x in bad[:3]) + ". 날씨·지역 추세로 설명되지 않으므로 설비 운전 시간·설정 온도부터 확인함.")
    mix = [x for x in top if x["level"] == "warn"]
    if mix:
        rule.append("검토할 곳: " + ", ".join(f"{x['fac']}({x['verdict']})" for x in mix[:3]) + ". 늘어난 몫 중 일부만 날씨·지역 추세로 설명되어 운영 요인이 섞여 있을 수 있음.")
    wx = [x for x in rows if x["verdict"] == "날씨 영향이 큼"]
    if wx:
        rule.append("날씨 영향: " + ", ".join(x["fac"] for x in wx) + "은(는) 늘었지만 기온으로 대부분 설명되어 운영 문제로 보지 않음.")
    reg = [x for x in rows if x["verdict"] == "지역 공통 추세"]
    if reg:
        rule.append("지역 공통: " + ", ".join(x["fac"] for x in reg) + "은(는) 도봉구 같은 계약종별도 비슷하게 늘어 지역 공통 요인 가능성이 있음.")
    if weather:
        rule.append(f"기온: 최근 {weather['months']}개월 냉방도일 {weather['cdd']:g}(전년 {weather['cdd_prev']:g}), 난방도일 {weather['hdd']:g}(전년 {weather['hdd_prev']:g}).")
    if not rule:
        rule.append("외부 자료와 견줘 눈에 띄는 변화가 없음.")
    src = {"kma": bool((kma or {}).get("on")), "kepco": bool(region), "opinet": bool(oil_items)}
    return {"rows": top, "weather": weather, "oil": oil if oil_items else None, "facts": facts, "rule": "\n".join(rule), "src": src,
            "region_area": (kp or {}).get("area"), "th": TH}
