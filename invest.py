"""투자 효과 분석: 절감 사업을 '할까 말까'(사전 판단), '해 보니 어땠나'(사후 평가), '언제 할까'(추진 일정)로 본다.

- 절감량·금액은 이 기관 자료(최근 12개월 사용량·단가)와 사업별 가정값(절감률·단가·지원율·수명)으로 계산한다.
- 사후 평가는 시행 전 기준선(기온 보정 모델)과 시행 뒤 실제 사용량을 비교한 실측 효과(analysis.effects)만 쓴다.
- 가정값은 모두 화면·보고서에 '가정'으로 적고, 기관 값으로 바꿀 수 있다(설정 energy.invest 또는 화면 입력).
- 판정(추진·조건부·보류)은 계산 규칙으로만 하고, 모델은 설명 문장만 쓴다.
"""
from datetime import date

from ingest import ym_add

# 사업 종류별 가정값. share = 그 에너지 중 사업이 줄일 수 있는 몫(용도별), rate = 그 몫에서 줄어드는 비율,
# cost = 단가(basis: area=연면적 ㎡당 원, contract=계약전력 kW당 원, fac=시설당 원), life = 수명(년),
# subsidy = 지원금 비율(0~1), months = 공사하기 좋은 달
MEASURES = {
    "led": {"name": "조명 LED 교체", "energy": "elec", "kind": "조명",
            "share": {"업무": 0.25, "체육": 0.12, "문화": 0.18, "주차": 0.45, "노유자": 0.20, "_": 0.20},
            "rate": 0.5, "cost": 12000, "basis": "area", "life": 10, "subsidy": 0.2,
            "subsidy_name": "고효율 조명 보급 지원·지자체 탄소중립 보조(공고마다 다름)", "months": list(range(1, 13)),
            "note": "형광등 대비 LED 소비전력 절반 가정"},
    "inverter": {"name": "펌프·공조기 인버터 설치(동력비)", "energy": "elec", "kind": "동력",
                 "share": {"업무": 0.25, "체육": 0.35, "문화": 0.25, "주차": 0.30, "노유자": 0.20, "_": 0.25},
                 "rate": 0.25, "cost": 90000, "basis": "contract", "life": 12, "subsidy": 0.3,
                 "subsidy_name": "에너지절약시설 설치 지원·ESCO 융자(공고마다 다름)", "months": [3, 4, 10, 11],
                 "note": "회전수 제어로 동력 25% 절감 가정, 냉난방 성수기 전 공사"},
    "boiler": {"name": "고효율(콘덴싱) 보일러 교체", "energy": "gas", "kind": "보일러",
               "share": {"_": 1.0}, "rate": 0.12, "cost": 15000, "basis": "area", "life": 15, "subsidy": 0.2,
               "subsidy_name": "가정·건물 친환경 보일러 지원(공고마다 다름)", "months": [5, 6, 7, 8, 9],
               "note": "일반 보일러 대비 효율 12%p 개선 가정, 난방기 전 공사"},
    "paperless": {"name": "페이퍼리스(전자결재·태블릿 회의)", "energy": "paper", "kind": "페이퍼리스",
                  "share": {"_": 1.0}, "rate": 0.6, "cost": 6000000, "basis": "fac", "life": 5, "subsidy": 0.0,
                  "subsidy_name": "", "months": list(range(1, 13)),
                  "note": "인쇄 60% 감소 가정. 종이 사용량은 용도별 연면적 ㎡당 연간 장수로 가정(기관 실적으로 바꿔 쓰세요)"},
}
# 사업장 설비 점검표 항목 → 투자 사업(MEASURES) 또는 운영 개선(가정 절감률: 전기·가스 사용량 대비)
CHECK = [
    (r"LED|조명", "led", None),
    (r"인버터|동력", "inverter", None),
    (r"보일러", "boiler", None),
    (r"전자결재|페이퍼|종이", "paperless", None),
    (r"대기전력", None, {"title": "대기전력 자동 차단 콘센트·멀티탭 설치", "elec": 0.03, "gas": 0, "cost": "적음(콘센트 교체)"}),
    (r"온도|28|18", None, {"title": "냉난방 설정온도(냉방 28℃·난방 18℃) 지키기", "elec": 0.02, "gas": 0.03, "cost": "없음(운영)"}),
    (r"야간|휴관|정지|스케줄|일정", None, {"title": "야간·휴관 설비 정지 일정 만들기", "elec": 0.02, "gas": 0.02, "cost": "없음(운영)"}),
    (r"계약전력", None, {"title": "계약전력 적정성 점검(연 1회)", "elec": 0, "gas": 0, "cost": "없음(신청)", "finding": "contract"}),
    (r"단열|차양|창호", None, {"title": "창호 단열·차양 보강", "elec": 0.01, "gas": 0.05, "cost": "큼(설계 필요)"}),
    (r"태양광|재생", None, {"title": "태양광 발전 설비 검토", "elec": 0, "gas": 0, "cost": "큼(지붕·구조 검토 필요)"}),
]
PAPER_PER_M2 = {"업무": 40, "문화": 15, "체육": 10, "노유자": 20, "주차": 2, "_": 15}   # 연면적 ㎡당 연간 인쇄 장수(가정)
DEFAULT = {"discount": 4.5, "co2_per_m3": 2.23, "won_per_sheet": 25, "co2_per_sheet": 0.0029,
           "carbon_price": 10000, "year_budget": 300000000, "per_month": 2}
NOTE = ("가정값(절감률·단가·지원율·수명·배출계수)은 시연용 일반 수준이며 원문 확인 전입니다. "
        "지원사업은 해마다 공고·조건이 달라 실제 지원 여부와 비율은 공고문으로 확인하세요.")


def _use(f):
    u = f.get("use") or ""
    for k in ("업무", "체육", "문화", "주차", "노유자"):
        if k in u:
            return k
    return "_"


class Invest:
    def __init__(self, a, cfg, over=None):
        self.a, self.cfg = a, cfg
        e = cfg["energy"]
        self.p = dict(DEFAULT, **(e.get("invest") or {}))
        self.m = {k: dict(v) for k, v in MEASURES.items()}
        for k, v in (over or {}).items():   # 화면에서 바꾼 가정값
            if k in self.m:
                self.m[k].update({kk: vv for kk, vv in v.items() if kk in ("rate", "cost", "subsidy", "life")})
            elif k == "_":
                self.p.update({kk: vv for kk, vv in v.items() if kk in DEFAULT})
        self.co2_kwh = e["co2_per_kwh"]

    # ---------------------------------------------- 시설별 기준 사용량·단가(최근 12개월)
    def base(self, fac, energy):
        if energy == "paper":
            f = self.a.ds.facilities.get(fac) or {}
            sheets = (f.get("area") or 0) * PAPER_PER_M2[_use(f)]
            return {"qty": sheets, "unit": self.p["won_per_sheet"], "co2": self.p["co2_per_sheet"], "u": "장"}
        rows = [r for r in self.a.series.get((fac, energy), []) if r["recent"]]
        if not rows:
            return None
        q = sum(r["actual"] for r in rows)
        won = sum(r["actual"] * (r["unit"] or 0) for r in rows)
        if not q or not won:
            return None
        return {"qty": q, "unit": won / q, "co2": (self.co2_kwh if energy == "elec" else self.p["co2_per_m3"]),
                "u": "kWh" if energy == "elec" else "㎥", "months": len(rows)}

    def _cost(self, m, f):
        q = {"area": f.get("area") or 0, "contract": f.get("contract") or 0, "fac": 1}[m["basis"]]
        return m["cost"] * q, {"area": f"연면적 {q:,.0f}㎡ × {m['cost']:,}원", "contract": f"계약전력 {q:,.0f}kW × {m['cost']:,}원",
                              "fac": f"시설당 {m['cost']:,}원"}[m["basis"]]

    def _npv(self, annual, life, net):
        r = self.p["discount"] / 100
        ann = (1 - (1 + r) ** -life) / r if r else life
        return annual * ann - net

    def _verdict(self, payback, life, npv, co2):
        if npv > 0 and payback <= life * 0.5:
            return "추진", "good", f"수명({life}년)의 절반 안에 회수되고 순이익이 남습니다."
        if npv > 0:
            return "조건부", "warn", f"수명 안에 회수되지만 {payback:.1f}년이 걸립니다. 지원금을 더 받거나 다른 공사와 함께 하면 유리합니다."
        if co2 >= 10:
            return "ESG 검토", "warn", f"돈으로는 수명 안에 회수되지 않지만 온실가스를 연 {co2:,.1f}t 줄입니다. 감축 목표·ESG 목적이면 검토하세요."
        return "보류", "bad", "수명 안에 투자비를 회수하지 못하고 감축 효과도 작습니다."

    # ---------------------------------------------- 점검표
    def check_of(self, fac, key):
        import re
        for c in getattr(self.a.ds, "checklist", []):
            if c["fac"] != fac:
                continue
            for rx, k, _ in CHECK:
                if k == key and re.search(rx, c["item"]):
                    return c
        return None

    # ---------------------------------------------- 사전 판단: 할까 말까
    def plan(self, fac, key, done=False, remain=1.0):
        m, f = self.m[key], self.a.ds.facilities.get(fac) or {}
        b = self.base(fac, m["energy"])
        if not b:
            return None
        share = m["share"].get(_use(f), m["share"]["_"]) * remain
        save_q = b["qty"] * share * m["rate"]
        save_won = save_q * b["unit"]
        co2 = save_q * b["co2"] / 1000
        cost, cost_f = self._cost(m, f)
        if remain < 1:
            cost, cost_f = cost * remain, cost_f + f" × 남은 몫 {remain:.0%}"
        sub = cost * m["subsidy"]
        net = cost - sub
        payback = net / save_won if save_won else 99
        npv = self._npv(save_won, m["life"], net)
        v, lv, why = self._verdict(payback, m["life"], npv, co2)
        return {"fac": fac, "key": key, "name": m["name"], "energy": m["energy"], "done": done,
                "base_qty": round(b["qty"]), "unit": round(b["unit"], 1), "u": b["u"], "share": share, "rate": m["rate"],
                "save_qty": round(save_q), "save_won": round(save_won), "co2_t": round(co2, 1),
                "social_won": round(co2 * self.p["carbon_price"]),
                "cost": round(cost), "cost_formula": cost_f, "subsidy": round(sub), "subsidy_rate": m["subsidy"],
                "subsidy_name": m["subsidy_name"], "net": round(net), "life": m["life"],
                "payback_y": round(payback, 1), "payback_m": round(payback * 12), "npv": round(npv), "verdict": v, "level": lv, "why": why,
                "formula": (f"최근 12개월 {b['qty']:,.0f}{b['u']} × 대상 몫 {share:.0%} × 절감률 {m['rate']:.0%} = {save_q:,.0f}{b['u']}/년, "
                            f"× 단가 {b['unit']:,.1f}원 = {save_won:,.0f}원/년"),
                "note": m["note"]}

    def plans(self):
        done = {(x["fac"], x.get("kind")) for x in self.a.actions if x.get("status") == "완료"}
        out = []
        for fac in self.a.ds.facilities:
            for key, m in self.m.items():
                c = self.check_of(fac, key)
                if c and c.get("pct") is not None:   # 점검표가 있으면 점검표의 이행률로 남은 몫만 계산
                    remain = max(0.0, 1 - c["pct"] / 100)
                    is_done, chk = remain <= 0.05, f"점검표: {c['state']}({c['pct']:g}%)"
                else:
                    remain, is_done, chk = 1.0, (fac, m["kind"]) in done, ("조치 이력: 완료" if (fac, m["kind"]) in done else "점검표 없음")
                p = self.plan(fac, key, done=is_done, remain=remain if remain > 0.05 else 1.0)
                if p and p["save_won"] > 0:
                    p["check"], p["check_src"] = chk, (c or {}).get("src")
                    out.append(p)
        order = {"추진": 0, "조건부": 1, "ESG 검토": 2, "사후 확인 먼저": 3, "보류": 4}
        out.sort(key=lambda x: (x["done"], order[x["verdict"]], x["payback_y"]))
        return out

    # ---------------------------------------------- 놓치고 있는 것(점검표 기준)
    def gaps(self, plans):
        import re
        cl = getattr(self.a.ds, "checklist", [])
        if not cl:
            return {"has": False, "items": [], "matrix": [],
                    "note": "사업장 설비 점검표(시설명·점검 항목·이행 현황·이행률)를 넣으면, 이미 하고 있는 것은 빼고 놓친 것만 추천합니다."}
        pmap = {(p["fac"], p["key"]): p for p in plans}
        items = []
        for c in cl:
            if c["state"] == "하고 있음" and (c.get("pct") or 100) >= 95:
                continue
            rule = next(((k, r) for rx, k, r in CHECK if re.search(rx, c["item"])), (None, None))
            key, r = rule
            remain = 1 - (c.get("pct") or 0) / 100
            g = {"fac": c["fac"], "item": c["item"], "state": c["state"], "pct": c.get("pct"), "note": c.get("note"), "src": c["src"]}
            if key:
                p = pmap.get((c["fac"], key))
                if not p:
                    continue
                g.update(title=p["name"], save_won=p["save_won"], co2_t=p["co2_t"], cost=f"순투자 {p['net']:,}원", payback_y=p["payback_y"],
                         verdict=p["verdict"], level=p["level"], how="투자 사업: 1번 표의 근거에서 계산식 확인")
            elif r:
                eb, gb = self.base(c["fac"], "elec"), self.base(c["fac"], "gas")
                won = remain * ((eb["qty"] * eb["unit"] * r["elec"]) if eb else 0) + remain * ((gb["qty"] * gb["unit"] * r["gas"]) if gb else 0)
                co2 = remain * (((eb["qty"] * r["elec"] * eb["co2"]) if eb else 0) + ((gb["qty"] * r["gas"] * gb["co2"]) if gb else 0)) / 1000
                fd = [f for f in self.a.findings if f["fac"] == c["fac"] and f["type"] == r.get("finding")] if r.get("finding") else []
                if fd:
                    won = fd[0].get("amount") or won
                g.update(title=r["title"], save_won=round(won) or None, co2_t=round(co2, 1), cost=r["cost"], payback_y=None,
                         verdict="바로 하기" if r["cost"].startswith(("없음", "적음")) else "검토", level="good" if r["cost"].startswith(("없음", "적음")) else "warn",
                         how=("점검 결과 '" + fd[0]["title"] + "'에서 계산" if fd else
                              (f"최근 12개월 사용량 × 가정 절감률(전기 {r['elec']:.0%}·가스 {r['gas']:.0%}) × 남은 몫 {remain:.0%}" if (r["elec"] or r["gas"]) else "효과는 현장 설계·검토 뒤 계산")))
            else:
                g.update(title=c["item"], save_won=None, co2_t=None, cost="-", payback_y=None, verdict="확인", level="gray", how="이 항목은 계산 규칙이 없어 목록에만 표시")
            items.append(g)
        items.sort(key=lambda g: (-(g["save_won"] or 0)))
        facs = list(dict.fromkeys(c["fac"] for c in cl))
        names = list(dict.fromkeys(c["item"] for c in cl))
        matrix = [{"fac": f, "cells": [next(({"state": c["state"], "pct": c.get("pct")} for c in cl if c["fac"] == f and c["item"] == n), None) for n in names]} for f in facs]
        return {"has": True, "items": items, "names": names, "matrix": matrix,
                "note": "운영 개선 항목의 절감률(대기전력 3%, 설정온도 전기 2%·가스 3%, 설비 정지 2%, 단열·차양 가스 5%)은 가정값입니다."}

    # ---------------------------------------------- 사후 평가: 해 보니 어땠나
    def reviews(self):
        out = []
        keymap = {m["kind"]: k for k, m in self.m.items()}
        for e in self.a.effects():
            if e.get("status") != "완료":
                continue
            cost = e.get("cost") or 0
            sub = e.get("subsidy") or 0
            r = {"id": e["id"], "fac": e["fac"], "title": e["title"], "date": e["date"], "state": e["state"], "cost": cost,
                 "subsidy": sub, "net": cost - sub, "src": e.get("src")}
            key = keymap.get(e.get("kind"))
            pre = self.plan(e["fac"], key) if key else None
            if pre:
                tot = self.base(e["fac"], self.m[key]["energy"])
                r["pre_pct"] = round(pre["save_qty"] / tot["qty"] * 100, 1) if tot else None
                r["pre_won"] = pre["save_won"]
            if e["state"] != "효과 확인":
                r.update(verdict="측정 중", level="gray", why=f"시행 뒤 {e.get('months', 0)}개월 자료뿐이라 아직 판단하지 않습니다.")
                out.append(r)
                continue
            mon = e["months"]
            annual = e["saved_won"] / mon * 12
            r.update(months=mon, period=f"{e['from']}~{e['to']}", saved_kwh=e["saved_kwh"], saved_pct=e["saved_pct"],
                     saved_won=e["saved_won"], annual_won=round(annual), co2_t=e["co2_t"])
            life = self.m[key]["life"] if key else 10
            if not cost:
                r.update(verdict="투자비 없음", level="gray", why="투자비를 적지 않아 회수 기간을 계산하지 않았습니다. 조치 이력에 투자비·지원금을 넣으세요.")
            else:
                net = cost - sub
                pay = net / annual if annual > 0 else None
                r["payback_y"] = round(pay, 1) if pay else None
                r["recovered_pct"] = round(e["saved_won"] / net * 100, 1) if net else None
                if pay:
                    r["breakeven"] = ym_add(e["date"][:7], round(pay * 12))
                r["npv"] = round(self._npv(max(annual, 0), life, net))
                ratio = (e["saved_pct"] / r["pre_pct"]) if r.get("pre_pct") else None
                r["vs_pre"] = round(ratio * 100) if ratio is not None else None
                if pay is None or pay > life:
                    r.update(verdict="효과 미흡", level="bad",
                             why=f"실측 절감이 {e['saved_pct']}%에 그쳐 수명({life}년) 안에 투자비를 회수하지 못합니다"
                                 + (f"(예상의 {r['vs_pre']}%)" if r["vs_pre"] is not None else "") + ". 설치 범위·점등 시간·기존 조명 상태를 점검하세요.")
                elif ratio is not None and ratio < 0.6:
                    r.update(verdict="예상보다 낮음", level="warn",
                             why=f"회수는 되지만({pay:.1f}년) 절감이 예상의 {r['vs_pre']}%입니다.")
                else:
                    r.update(verdict="효과 있음", level="good",
                             why=f"실측 절감 {e['saved_pct']}%, {pay:.1f}년이면 투자비를 회수합니다(손익분기 {r['breakeven']}).")
            out.append(r)
        return out

    # ---------------------------------------------- 추진 일정: 몇 개월에 걸쳐 바꿀까
    def roadmap(self, plans, start=None):
        today = date.today()
        start = start or ym_add(f"{today.year}-{today.month:02d}", 1)
        cand = [p for p in plans if not p["done"] and p["verdict"] in ("추진", "조건부")]
        cand.sort(key=lambda p: p["payback_y"])
        slots, spent = {}, {}
        steps = []
        for p in cand:
            m = self.m[p["key"]]
            ym = start
            for _ in range(36):
                y = int(ym[:4])
                if int(ym[5:]) in m["months"] and slots.get(ym, 0) < self.p["per_month"] \
                        and spent.get(y, 0) + p["net"] <= self.p["year_budget"]:
                    break
                ym = ym_add(ym, 1)
            slots[ym] = slots.get(ym, 0) + 1
            spent[int(ym[:4])] = spent.get(int(ym[:4]), 0) + p["net"]
            steps.append({"ym": ym, "fac": p["fac"], "name": p["name"], "net": p["net"], "save_won": p["save_won"],
                          "co2_t": p["co2_t"], "payback_y": p["payback_y"], "verdict": p["verdict"],
                          "why_month": "공사하기 좋은 달" if len(m["months"]) < 12 else "바로 가능"})
        steps.sort(key=lambda s: s["ym"])
        if not steps:
            return {"steps": [], "curve": [], "start": start}
        end = ym_add(steps[-1]["ym"], 48)
        curve, inv, sav, be = [], 0, 0, None
        ym = start
        while ym <= end:
            for s in steps:
                if s["ym"] == ym:
                    inv += s["net"]
            sav += sum(s["save_won"] / 12 for s in steps if s["ym"] < ym)
            curve.append({"ym": ym, "invest": round(inv), "saved": round(sav)})
            if be is None and inv and sav >= inv and ym > steps[-1]["ym"]:
                be = ym
            ym = ym_add(ym, 1)
        return {"steps": steps, "curve": curve, "start": start, "breakeven": be,
                "total_net": sum(s["net"] for s in steps), "total_save": sum(s["save_won"] for s in steps),
                "total_co2": round(sum(s["co2_t"] for s in steps), 1),
                "rule": (f"회수 빠른 사업부터, 사업별 공사하기 좋은 달에, 한 달 {self.p['per_month']}건 이내, "
                         f"한 해 순투자 {self.p['year_budget']:,}원 이내(가정)로 배치")}

    def run(self):
        ps = self.plans()
        rv = self.reviews()
        # 사후 평가가 '효과 미흡'인 같은 시설·같은 종류 사업은 원인을 확인하기 전까지 다음 단계를 미룬다
        kinds = {m["kind"]: k for k, m in self.m.items()}
        poor = {}
        for v in rv:
            if v.get("verdict") == "효과 미흡":
                act = next((x for x in self.a.actions if x["id"] == v["id"]), {})
                if act.get("kind") in kinds:
                    poor[(v["fac"], kinds[act["kind"]])] = v
        for p in ps:
            v = poor.get((p["fac"], p["key"]))
            if v and not p["done"]:
                p["calc_verdict"] = p["verdict"]
                p.update(verdict="사후 확인 먼저", level="warn",
                         why=f"계산상으로는 {p['verdict']}이지만, 같은 시설의 '{v['title']}'({v['date']}) 사후 평가가 효과 미흡(실측 절감 {v['saved_pct']}%)입니다. "
                             "원인(설치 범위·점등 시간·기존 설비 상태)을 확인하기 전에는 다음 단계를 미루세요.")
        gp = self.gaps(ps)
        rm = self.roadmap(ps)
        cnt = {}
        for p in ps:
            if not p["done"]:
                cnt[p["verdict"]] = cnt.get(p["verdict"], 0) + 1
        return {"plans": ps, "gaps": gp, "reviews": rv, "roadmap": rm, "counts": cnt, "params": self.p,
                "measures": {k: {kk: m[kk] for kk in ("name", "rate", "cost", "basis", "life", "subsidy", "subsidy_name", "note")}
                             for k, m in self.m.items()},
                "note": NOTE}

    def facts(self, r):
        f = []
        for p in r["plans"]:
            if p["done"]:
                continue
            f.append(f"{p['fac']} {p['name']}: 연 {p['save_won']:,}원 절감, 순투자 {p['net']:,}원(지원금 {p['subsidy']:,}원), "
                     f"회수 {p['payback_y']}년, 수명 {p['life']}년 순이익 {p['npv']:,}원, 온실가스 연 {p['co2_t']}t, 판정 {p['verdict']}")
        for v in r["reviews"]:
            if v.get("annual_won") is not None:
                f.append(f"사후 평가 {v['fac']} {v['title']}({v['date']} 시행): 실측 절감 {v['saved_pct']}%, 연 {v['annual_won']:,}원, "
                         f"투자비 {v['cost']:,}원, 회수 {v.get('payback_y')}년, 판정 {v['verdict']}")
        for g in r["gaps"]["items"][:10]:
            f.append(f"점검표에서 놓친 것 {g['fac']} {g['item']}({g['state']}): {g['title']}" + (f", 연 {g['save_won']:,}원 절감 추정" if g.get("save_won") else ""))
        rm = r["roadmap"]
        if rm["steps"]:
            f.append(f"추진 일정: {len(rm['steps'])}건, {rm['steps'][0]['ym']}부터 {rm['steps'][-1]['ym']}까지, 순투자 {rm['total_net']:,}원, "
                     f"연 절감 {rm['total_save']:,}원, 온실가스 연 {rm['total_co2']}t, 전체 손익분기 {rm.get('breakeven') or '계산 기간 밖'}")
        return f
