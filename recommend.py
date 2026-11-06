"""절감 조치 추천: 점검 결과·실측 효과·지침 조문을 묶어 시설별로 할 일을 고른다.

- 추천의 '왜'는 이 기관 자료에서 나온 숫자(점검 결과 금액, 조치 실측 효과)만 쓴다.
- 근거는 넣은 지침의 조문(원문 인용)과, 외부 공식 기준은 원문 확인 여부를 함께 적는다.
- 예상 절감액은 계산식과 가정을 같이 보여 준다(추정).
"""

# 외부 공식 기준: 원문을 직접 확인하지 못한 것은 verified=False로 두고 화면에 '원문 확인 필요'를 붙인다.
OFFICIAL = {
    "cool": {"name": "공공기관 에너지이용 합리화 추진에 관한 규정(산업통상자원부 고시)",
             "text": "냉방설비 가동 시 실내 평균 28℃ 이상, 난방설비 가동 시 평균 18℃ 이하 유지(제14조로 보도됨). 학교·도서관·민원실·의료기관 등은 기관이 따로 정할 수 있음",
             "verified": False, "note": "언론 보도(서울신문 2024-08-08)와 산업통상자원부 설명자료(2017-06-01)로 확인, 고시 원문은 법제처에서 확인 필요"},
}
OFFICIAL["heat"] = OFFICIAL["cool"]

SUMMER, WINTER = (6, 7, 8, 9), (12, 1, 2)


def _months_of(f):
    return {int(m[5:]) for m in f.get("months") or []}


def build(a, acts=None, kp=None):
    """a: analysis.Analysis(실행 후). 돌려줌: {"items": [...], "totals": {...}}"""
    g = a.g
    eff = a.effects()
    done_kinds = {(x["fac"], x.get("kind") or "") for x in (acts if acts is not None else a.actions)}
    led_done = {x["fac"] for x in a.actions if "LED" in (x.get("title") or "") or x.get("kind") == "조명"}
    led_eff = next((e for e in eff if e.get("saved_pct") and ("LED" in e.get("title", "") or e.get("kind") == "조명")), None)
    items = []

    def add(fac, key, title, why, save_won, save_kwh, formula, topic, official=None, finding=None, kind="elec", level="warn", ext=None):
        items.append({"ext": ext, "fac": fac, "key": key, "title": title, "why": why, "save_won": round(save_won) if save_won else None,
                      "save_kwh": round(save_kwh) if save_kwh else None, "formula": formula,
                      "basis": g.cite(g.article(topic)) if topic else None, "official": OFFICIAL.get(official) if official else None,
                      "finding": finding, "kind": kind, "level": level,
                      "registered": (fac, key) in done_kinds})

    for f in a.findings:
        fac, mo, amt = f["fac"], _months_of(f), f.get("amount") or 0
        mtxt = ", ".join(f["months"][:3]) + (" 등" if len(f["months"]) > 3 else "")
        if f["type"] == "excess":
            if mo & set(SUMMER):
                add(fac, "냉방", "냉방 설정온도 지키기와 운영시간 외 냉방 정지",
                    f"{mtxt} 전기 사용이 기온을 감안해도 많았습니다(초과분 {amt:,}원 추정).", amt, f.get("excess"),
                    "같은 기간 초과 사용분을 기준선 수준으로 되돌릴 때(점검 결과 금액)", "냉방", "cool", f["id"], level="bad")
            elif mo & set(WINTER):
                add(fac, "난방", "전기 난방기 사용 점검과 난방 설정온도 지키기",
                    f"{mtxt} 겨울철 전기 사용이 기온을 감안해도 많았습니다(초과분 {amt:,}원 추정).", amt, f.get("excess"),
                    "초과 사용분을 기준선 수준으로 되돌릴 때", "난방", "heat", f["id"], level="bad")
            else:
                add(fac, "조명", "점심·퇴근 후 소등과 대기전력 차단",
                    f"{mtxt} 냉난방 영향이 적은 달인데도 전기 사용이 많았습니다(초과분 {amt:,}원 추정).", amt, f.get("excess"),
                    "초과 사용분을 기준선 수준으로 되돌릴 때", "조명", None, f["id"])
        elif f["type"] == "gas_excess":
            add(fac, "보일러", "보일러 연소 효율 점검과 난방 설정온도 지키기",
                f"{mtxt} 가스 사용이 기온을 감안해도 많았습니다(초과분 {amt:,}원 추정).", amt, None,
                "초과 사용분을 기준선 수준으로 되돌릴 때", "연소", "heat", f["id"], kind="gas", level="bad")
        elif f["type"] == "closure":
            add(fac, "휴관", "휴관 전 설비 정지 계획 세우기(" + ("보일러·급탕" if f["kind"] == "gas" else "냉난방·환기·조명") + ")",
                f"{mtxt} 휴관 기간에도 {'가스' if f['kind'] == 'gas' else '전기'}를 평소처럼 썼습니다(불필요 사용 {amt:,}원 추정).", amt, f.get("excess"),
                "다음 휴관 때 같은 일이 없으면 줄일 수 있는 금액(점검 결과 금액)", "휴관", None, f["id"], kind=f["kind"], level="bad")
        elif f["type"] == "contract":
            add(fac, "계약전력", "계약전력 조정을 한국전력공사와 협의",
                f["summary"], amt, None, f.get("formula", ""), "계약전력", None, f["id"])
        elif f["type"] == "pf":
            add(fac, "역률", "진상용 콘덴서 등 역률 개선 설비 점검",
                f"최근 역률이 기준보다 낮은 달이 있어 역률요금 {amt:,}원을 냈습니다.", amt, None,
                "이미 낸 역률요금(고지서 합계)만큼 줄일 수 있음", "역률", None, f["id"])
        elif f["type"] == "bill":
            add(fac, "요금확인", "공급사에 고지 금액 확인 요청",
                f["summary"], amt, None, f.get("formula", ""), "요금 확인", None, f["id"])

    # 실측한 LED 교체 효과를 아직 안 바꾼 시설에 견주어 보기(추정)
    if led_eff:
        for fac in a.ds.facilities:
            if fac in led_done or (fac, "elec") not in a.series:
                continue
            rows = [r for r in a.series[(fac, "elec")] if r["recent"]]
            kwh = sum(r["actual"] for r in rows)
            unit = sum(r["unit"] or 0 for r in rows) / max(1, sum(1 for r in rows if r["unit"]))
            if kwh <= 0 or not unit:
                continue
            s_kwh = kwh * led_eff["saved_pct"] / 100
            add(fac, "LED", "고효율 조명(LED) 교체 검토",
                f"같은 기관 {led_eff['fac']}의 '{led_eff['title']}'이 기온을 감안해 {led_eff['saved_pct']}% 줄었습니다({led_eff['from']}~{led_eff['to']} 실측).",
                s_kwh * unit, s_kwh,
                f"최근 12개월 전기 {kwh:,.0f}kWh × 실측 절감률 {led_eff['saved_pct']}% × 평균 {unit:,.1f}원/kWh (조명 비중이 시설마다 달라 실제는 다를 수 있음)",
                "조명", None, None, level="gray")

    # 같은 용도 시설 사이 면적당 사용량 비교
    ov = a.overview()
    by_use = {}
    for f in ov["facilities"]:
        if f.get("kwh_per_m2") and f.get("use"):
            by_use.setdefault(f["use"], []).append(f)
    for use, fs in by_use.items():
        if len(fs) < 2:
            continue
        avg = sum(x["kwh_per_m2"] for x in fs) / len(fs)
        for x in fs:
            if x["kwh_per_m2"] > avg * 1.2:
                add(x["name"], "진단", "에너지 진단(설비 운영 실태 점검) 받기",
                    f"같은 용도({use}) 시설 평균보다 면적당 전기 사용량이 {x['kwh_per_m2'] / avg * 100 - 100:.0f}% 많습니다({x['kwh_per_m2']} vs 평균 {avg:.1f} kWh/㎡).",
                    None, None, "", None, None, None, level="gray")

    # 한전 전력데이터 개방포털: 같은 지역·같은 계약종별 평균과 견주기(지역 집계 통계라 참고 근거)
    if kp:
        area = kp["area"]
        for k in kp["facilities"]:
            ext = {"label": f"한전 전력데이터 개방포털 · 계약종별 전력사용량({area} {k['cntr']}, {k['from']}~{k['to']})"}
            if k["unit_gap_pct"] is not None and k["unit_gap_pct"] >= 10:
                add(k["fac"], "요금구조", "요금 구조 점검(계약전력·역률·시간대별 사용)",
                    f"최근 {k['months']}개월 전기 단가가 {k['our_unit']}원/kWh로 {area} {k['cntr']} 평균 {k['region_unit']}원/kWh보다 {k['unit_gap_pct']}% 높습니다.",
                    None, None,
                    f"참고: (우리 단가 {k['our_unit']} - 지역 평균 {k['region_unit']}원/kWh) × {k['kwh']:,}kWh = {k['gap_won']:,}원. 고압·저압과 계약 규모에 따라 단가가 달라 전부 줄일 수 있는 금액은 아닙니다",
                    "계약전력", None, None, level="gray", ext=ext)
            if k["yoy_gap"] is not None and k["yoy_gap"] >= 10:
                note = f" 같은 기간 {area} {k['cntr']} 전체 사용량은 전년보다 {k['region_yoy']:+.1f}%, 이 시설은 {k['our_yoy']:+.1f}%였습니다(한전 지역 통계)."
                mine = [x for x in items if x["fac"] == k["fac"] and x["key"] in ("냉방", "난방", "조명")]
                for x in mine:
                    x["why"] += note
                    x["ext"] = ext
                if not mine:
                    add(k["fac"], "지역추세", "지역 추세보다 많이 늘어난 원인 확인(운영시간·설비 추가)",
                        note.strip(), None, None, "", None, None, None, level="gray", ext=ext)

    order = {"bad": 0, "warn": 1, "gray": 2}
    items.sort(key=lambda x: (x["registered"], order.get(x["level"], 3), -(x["save_won"] or 0)))
    for i, x in enumerate(items, 1):
        x["id"] = f"R{i}"
    tot = sum(x["save_won"] or 0 for x in items if not x["registered"] and x["key"] != "LED")
    tg = a.target()
    return {"items": items, "totals": {"save_won": tot, "count": len(items),
                                         "target_gap_kwh": tg["gap_kwh"] if tg else None},
            "note": "예상 절감액은 점검 결과 금액(기준선과의 차이)과 실측 조치 효과로 계산한 추정입니다. 근거 조문은 넣은 지침 원문이며, 외부 공식 기준은 원문 확인 여부를 함께 적었습니다."}
