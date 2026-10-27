"""분석: 기온 보정 기준선, 이상 사용·요금 점검, 절감 추정, 조치 효과.

모든 판단은 규칙과 계산으로 한다(모델 없이 같은 결과). 점검 기준값은 기관 지침 조문에서 읽고,
지침에 없으면 설정값을 쓴다. 결과마다 근거(지침 조문·원문 행)와 계산식을 붙인다.

기온 보정 기준선: 시설·에너지별로 기준 기간 12개월의 월 사용량을
  사용량 = 기저 + 냉방계수 × 냉방도일 + 난방계수 × 난방도일
로 맞춰 두고, 이후 달의 '기온만 감안했을 때 예상 사용량'과 실제를 비교한다.
도일(度日)은 월평균기온으로 근사한다: 냉방도일 = 일수 × max(0, 기온 - 기준온도), 난방도일 = 일수 × max(0, 기준온도 - 기온).
"""
import math
import re
from datetime import date

from ingest import ym_add, ym_days

# 지침에서 기준값을 읽는 방법: (키, 조문을 찾는 낱말, 숫자 패턴, 기본값, 설명)
RULES = [
    ("excess_pct", r"이상 사용|기온 영향", r"(\d+)\s*%\s*이상\s*많", 10, "기온 보정 후 초과 사용 기준(%)"),
    ("consecutive", r"이상 사용", r"(\d+)\s*개월\s*연속", 2, "연속 초과 시 합동 점검(개월)"),
    ("gas_excess_pct", r"연소|보일러", r"(\d+)\s*%\s*이상\s*많", 15, "가스 초과 사용 기준(%)"),
    ("peak_ratio_pct", r"계약전력", r"(\d+)\s*%\s*미만", 50, "최대수요/요금적용전력 비율 기준(%)"),
    ("peak_months", r"계약전력", r"(\d+)\s*개월\s*이상", 12, "계약전력 검토 기간(개월)"),
    ("pf_min", r"역률", r"(\d+)\s*%\s*미만", 90, "역률 기준(%)"),
    ("bill_diff_pct", r"요금 확인|고지서", r"(\d+)\s*%\s*이상", 1, "요금 재계산 차이 기준(%)"),
    ("effect_months", r"효과", r"(\d+)\s*개월\s*이상", 3, "조치 효과 측정 최소 기간(개월)"),
    ("cool_setpoint", r"냉방", r"(\d+)\s*℃\s*이상", 26, "냉방 설정온도(℃)"),
    ("target_pct", r"감축 목표", r"(\d+)\s*%\s*이상\s*줄", None, "연간 전기 감축 목표(기온 보정, %)"),
]
TOPIC = {  # 결과 종류 → 관련 지침 조문을 찾는 낱말
    "excess": r"이상 사용", "gas_excess": r"연소|보일러", "contract": r"계약전력", "pf": r"역률",
    "bill": r"요금 확인", "closure": r"휴관", "cooling": r"냉방", "heating": r"난방", "effect": r"효과", "lighting": r"조명",
}


def _season(m):
    return "summer" if m in (6, 7, 8) else "winter" if m in (11, 12, 1, 2) else "spring"


class Guide:
    """지침 조문 찾기와 기준값 읽기."""

    def __init__(self, ds, cfg):
        self.reg, self.doc = ds.guide if ds.guide else (None, None)
        self.cfg = cfg
        self.values, self.basis = {}, {}
        for key, topic, rx, default, label in RULES:
            v, b = default, None
            a = self.article(topic)
            if a:
                m = re.search(rx, a.plain)
                if m:
                    v, b = float(m.group(1)), self.cite(a)
            self.values[key] = v
            self.basis[key] = {"label": label, "value": v, "basis": b, "from": "지침" if b else "기본값"}

    def article(self, topic):
        if not self.reg:
            return None
        for a in self.reg.articles:
            if re.search(topic, a.title or ""):
                return a
        for a in self.reg.articles:
            if re.search(topic, a.plain):
                return a
        return None

    def cite(self, a):
        if not a:
            return None
        return {"reg": self.reg.title or (self.doc.name if self.doc else ""), "label": a.label, "title": a.title,
                "text": a.to_text(), "file": self.doc.relpath if self.doc else "", "loc": a.loc}

    def for_topic(self, kind):
        return self.cite(self.article(TOPIC.get(kind, kind)))

    def __getitem__(self, k):
        return self.values[k]


# ------------------------------------------------------------ 기준선(회귀)

def _solve(A, b):
    """작은 정규방정식 풀이(가우스 소거). 특이하면 None."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-9:
            return None
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [x - f * y for x, y in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def fit(points, use_cdd=True, use_hdd=True):
    """points: [(냉방도일, 난방도일, 사용량)] → 계수와 적합도. 음수 계수는 빼고 다시 맞춘다."""
    feats = [i for i, on in ((0, use_cdd), (1, use_hdd)) if on]
    while True:
        X = [[1.0] + [p[i] for i in feats] for p in points]
        y = [p[2] for p in points]
        k = len(X[0])
        if len(points) < k + 2:
            return None
        A = [[sum(r[i] * r[j] for r in X) for j in range(k)] for i in range(k)]
        b = [sum(r[i] * yy for r, yy in zip(X, y)) for i in range(k)]
        coef = _solve(A, b)
        if coef is None:
            feats = feats[:-1]
            continue
        neg = [f for f, c in zip(feats, coef[1:]) if c < 0]
        if neg:
            feats = [f for f in feats if f not in neg]
            continue
        break
    full = {"base": coef[0], "cdd": 0.0, "hdd": 0.0}
    for f, c in zip(feats, coef[1:]):
        full["cdd" if f == 0 else "hdd"] = c
    pred = [full["base"] + full["cdd"] * p[0] + full["hdd"] * p[1] for p in points]
    resid = [yy - pp for yy, pp in zip(y, pred)]
    n, mean = len(y), sum(y) / len(y)
    rmse = math.sqrt(sum(r * r for r in resid) / max(1, n - k))
    ss_tot = sum((yy - mean) ** 2 for yy in y) or 1
    full.update(n=n, cv_rmse=round(rmse / mean * 100, 1) if mean else None,
                r2=round(1 - sum(r * r for r in resid) / ss_tot, 3))
    return full


def predict(model, dd):
    return model["base"] + model["cdd"] * dd[0] + model["hdd"] * dd[1]


# ------------------------------------------------------------ 분석 본체

class Analysis:
    def __init__(self, ds, cfg, actions=None):
        self.ds, self.cfg = ds, cfg
        self.g = Guide(ds, cfg)
        self.actions = actions if actions is not None else ds.actions
        self.months = ds.months
        self.latest = self.months[-1] if self.months else None
        self.recent = [ym_add(self.latest, -i) for i in range(11, -1, -1)] if self.latest else []
        base_end = ym_add(self.recent[0], -1) if self.recent else None
        self.baseline = [m for m in self.months if base_end and ym_add(base_end, -11) <= m <= base_end]
        self.findings = []
        self.models = {}
        self.series = {}

    # 도일
    def dd(self, ym):
        w = self.ds.weather.get(ym)
        if not w:
            return None
        e = self.cfg["energy"]
        d = ym_days(ym)
        return d * max(0.0, w["t"] - e["cdd_base"]), d * max(0.0, e["hdd_base"] - w["t"])

    def closed(self, fac, ym):
        """그 달에 휴관한 날 비율(0~1)과 사유."""
        f = self.ds.facilities.get(fac) or {}
        y, m = map(int, ym.split("-"))
        first, last = date(y, m, 1), date(y, m, ym_days(ym))
        days, reason = 0, ""
        for c in f.get("closures", []):
            s, e = date.fromisoformat(c["start"]), date.fromisoformat(c["end"])
            lo, hi = max(s, first), min(e, last)
            if lo <= hi:
                days += (hi - lo).days + 1
                reason = c["reason"]
        return days / ym_days(ym), reason

    def action_before(self, fac, ym):
        """그 달 이전에 끝난(완료) 조치 목록."""
        return [a for a in self.actions if a["fac"] == fac and a.get("status") == "완료" and a["date"][:7] < ym]

    def run(self):
        if not self.latest:
            return self
        for fac in self.ds.facilities:
            for kind, table, field in (("elec", self.ds.elec, "kwh"), ("gas", self.ds.gas, "m3")):
                rows = {ym: r for (f, ym), r in table.items() if f == fac}
                if not rows:
                    continue
                self._series(fac, kind, rows, field)
        self._check_excess()
        self._check_closure()
        self._check_contract()
        self._check_pf()
        self._check_bill()
        self.findings.sort(key=lambda f: (-{"bad": 2, "warn": 1}.get(f["level"], 0), -(f.get("amount") or 0)))
        for i, f in enumerate(self.findings, 1):
            f["id"] = f"F{i}"
        return self

    def _series(self, fac, kind, rows, field):
        pts = []
        for ym in self.baseline:
            r, dd = rows.get(ym), self.dd(ym)
            if r is None or dd is None or self.closed(fac, ym)[0] > 0:
                continue
            pts.append((dd[0], dd[1], r[field]))
        model = fit(pts, use_cdd=(kind == "elec"), use_hdd=True) if len(pts) >= 6 else None
        self.models[(fac, kind)] = model
        out = []
        for ym in self.months:
            r = rows.get(ym)
            if r is None:
                continue
            dd = self.dd(ym)
            p = predict(model, dd) if model and dd else None
            if p is None:  # 기온 자료가 없으면 전년 같은 달
                prev = rows.get(ym_add(ym, -12))
                p = prev[field] if prev else None
            share, reason = self.closed(fac, ym)
            unit = None
            if kind == "elec" and r.get("energy") and r["kwh"]:
                unit = r["energy"] / r["kwh"]
            elif kind == "gas" and r.get("total") and r["m3"]:
                unit = r["total"] / r["m3"]
            out.append({"ym": ym, "actual": r[field], "expected": round(p) if p else None,
                        "diff_pct": round((r[field] - p) / p * 100, 1) if p else None,
                        "closed": round(share, 2), "closed_reason": reason, "unit": unit,
                        "cost": r.get("total"), "src": r.get("src"), "temp": (self.ds.weather.get(ym) or {}).get("t"),
                        "baseline": ym in self.baseline, "recent": ym in self.recent})
        self.series[(fac, kind)] = out

    def _fac_label(self, fac):
        f = self.ds.facilities.get(fac) or {}
        return f.get("manager") or "시설 담당"

    # -------------------------------------------------- 점검 1: 기온 보정 후 초과 사용
    def _check_excess(self):
        for (fac, kind), rows in self.series.items():
            th = self.g["excess_pct"] if kind == "elec" else self.g["gas_excess_pct"]
            hits = [r for r in rows if r["recent"] and r["diff_pct"] is not None and r["closed"] == 0 and r["diff_pct"] >= th]
            if not hits:
                continue
            # 연속 구간끼리 묶는다
            groups, cur = [], []
            for r in hits:
                if cur and ym_add(cur[-1]["ym"], 1) != r["ym"]:
                    groups.append(cur)
                    cur = []
                cur.append(r)
            groups.append(cur)
            for grp in groups:
                excess = sum(r["actual"] - r["expected"] for r in grp)
                amount = round(sum((r["actual"] - r["expected"]) * (r["unit"] or 0) for r in grp))
                unit = "kWh" if kind == "elec" else "㎥"
                months = [r["ym"] for r in grp]
                consec = len(grp) >= self.g["consecutive"]
                m_nums = {int(m[5:]) for m in months}
                topic = "cooling" if kind == "elec" and m_nums & {6, 7, 8, 9} else "heating" if m_nums & {11, 12, 1, 2, 3} else "excess"
                f = {
                    "type": "excess" if kind == "elec" else "gas_excess",
                    "fac": fac, "kind": kind, "months": months,
                    "level": "bad" if consec or max(r["diff_pct"] for r in grp) >= th * 1.5 else "warn",
                    "title": f"{fac} {'전기' if kind == 'elec' else '가스'} 사용이 기온을 감안해도 많음 ({_mrange(months)})",
                    "summary": f"기온만 감안한 예상치보다 " + ", ".join(f"{int(r['ym'][5:])}월 {r['diff_pct']:+.1f}%" for r in grp)
                               + f" 더 썼습니다. 초과 {excess:,.0f}{unit}.",
                    "evidence": [{"label": f"{r['ym']} 실제 {r['actual']:,.0f}{unit} / 예상 {r['expected']:,.0f}{unit} (기온 {r['temp']}℃)",
                                  "src": r["src"]} for r in grp],
                    "amount": amount, "amount_label": "초과 사용 요금(추정)",
                    "formula": f"Σ(실제 - 예상) × 그 달 고지서의 {unit}당 단가",
                    "excess": round(excess), "unit": unit,
                    "basis": [self.g.for_topic("excess" if kind == "elec" else "gas_excess")],
                    "owner": self._fac_label(fac),
                    "steps": _steps(topic if kind == "elec" else "gas", consec, self.g),
                }
                if topic in ("cooling", "heating"):
                    f["basis"].append(self.g.for_topic(topic))
                if consec:
                    f["note"] = f"{int(self.g['consecutive'])}개월 이상 연속이라 지침상 시설 관리자와 합동 점검 대상입니다."
                self.findings.append(f)

    # -------------------------------------------------- 점검 2: 휴관 중 설비 미정지
    def _check_closure(self):
        ratio = self.cfg["energy"]["closure_expected_ratio"]
        for (fac, kind), rows in self.series.items():
            for r in rows:
                if not r["recent"] or r["closed"] < 0.5 or not r["expected"]:
                    continue
                share = r["actual"] / r["expected"]
                # 휴관한 날은 평소의 ratio 만큼만 쓴다고 보고 그 달 예상치를 다시 잡는다
                exp_closed = r["expected"] * (1 - r["closed"] + r["closed"] * ratio)
                if r["actual"] <= exp_closed * 1.15:
                    continue
                unit = "kWh" if kind == "elec" else "㎥"
                over = r["actual"] - exp_closed
                self.findings.append({
                    "type": "closure", "fac": fac, "kind": kind, "months": [r["ym"]], "level": "bad",
                    "title": f"{fac} 휴관 기간에도 {'전기' if kind == 'elec' else '가스'}를 평소처럼 사용 ({int(r['ym'][5:])}월)",
                    "summary": f"휴관({r['closed_reason'] or '사유 미기재'})으로 그 달 {round(r['closed'] * 100)}%를 쉬었는데 사용량은 평소 예상치의 "
                               f"{share * 100:.0f}%였습니다. 필수 설비만 돌렸다면 약 {exp_closed:,.0f}{unit}였을 것입니다.",
                    "evidence": [{"label": f"{r['ym']} 실제 {r['actual']:,.0f}{unit} / 평소 예상 {r['expected']:,.0f}{unit}", "src": r["src"]},
                                 {"label": f"휴관 정보: {r['closed_reason']}", "src": (self.ds.facilities.get(fac) or {}).get("src")}],
                    "amount": round(over * (r["unit"] or 0)), "amount_label": "휴관 중 불필요 사용 요금(추정)",
                    "formula": f"(실제 - 휴관 반영 예상) × 단가. 휴관일에는 평소의 {round(ratio * 100)}%만 쓴다고 가정(설정값)",
                    "excess": round(over), "unit": unit,
                    "basis": [self.g.for_topic("closure")], "owner": self._fac_label(fac),
                    "steps": _steps("closure", False, self.g),
                })

    # -------------------------------------------------- 점검 3: 계약전력(요금적용전력) 과다
    def _check_contract(self):
        months = int(self.g["peak_months"])
        ratio = self.g["peak_ratio_pct"] / 100
        margin = self.cfg["energy"]["contract_margin"]
        for fac in self.ds.facilities:
            rows = [self.ds.elec.get((fac, m)) for m in self.recent[-months:]]
            rows = [r for r in rows if r and r.get("peak") and r.get("applied")]
            if len(rows) < months:
                continue
            if not all(r["peak"] / r["applied"] < ratio for r in rows):
                continue
            mx = max(r["peak"] for r in rows)
            applied = rows[-1]["applied"]
            proposal = math.ceil(mx * (1 + margin) / 10) * 10
            unit = rows[-1]["basic"] / applied if rows[-1].get("basic") else None
            amount = round((applied - proposal) * unit * 12) if unit else 0
            self.findings.append({
                "type": "contract", "fac": fac, "kind": "elec", "months": [self.recent[-months], self.latest], "level": "warn",
                "title": f"{fac} 요금적용전력이 실제 최대수요보다 크게 잡혀 있음",
                "summary": f"최근 {months}개월 최대수요전력은 가장 높을 때 {mx:,.0f}kW인데 요금적용전력은 {applied:,.0f}kW입니다"
                           f"(최고치 기준 {mx / applied * 100:.0f}%). 기본요금은 요금적용전력에 따라 매겨집니다.",
                "evidence": [{"label": f"{self.latest} 요금적용전력 {applied:,.0f}kW, 기본요금 {rows[-1]['basic']:,.0f}원", "src": rows[-1]["src"]},
                             {"label": f"최근 {months}개월 최대수요전력 {min(r['peak'] for r in rows):,.0f}~{mx:,.0f}kW", "src": None}],
                "amount": amount, "amount_label": "연간 기본요금 차이(추정)",
                "formula": f"(현재 {applied:,.0f}kW - 검토안 {proposal}kW) × 고지서 kW당 기본요금 {unit:,.0f}원 × 12개월. "
                           f"검토안 = 최고 최대수요 × (1 + 여유율 {round(margin * 100)}%)를 10kW 단위 올림",
                "proposal": proposal, "basis": [self.g.for_topic("contract")], "owner": "에너지 관리 담당",
                "note": "요금적용전력 산정 방식은 계약종별로 다르므로 한국전력공사와 협의해 확정해야 합니다.",
                "steps": _steps("contract", False, self.g),
            })

    # -------------------------------------------------- 점검 4: 역률
    def _check_pf(self):
        th = self.g["pf_min"]
        for fac in self.ds.facilities:
            rows = [(m, self.ds.elec.get((fac, m))) for m in self.recent]
            low = [(m, r) for m, r in rows if r and r.get("pf") and r["pf"] < th]
            if not low:
                continue
            paid = sum(r.get("pfc") or 0 for _, r in low)
            self.findings.append({
                "type": "pf", "fac": fac, "kind": "elec", "months": [m for m, _ in low], "level": "warn",
                "title": f"{fac} 평균역률이 {th:.0f}% 미만 ({len(low)}개월)",
                "summary": f"{_mrange([m for m, _ in low])} 평균역률이 {min(r['pf'] for _, r in low)}~{max(r['pf'] for _, r in low)}%로 기준 {th:.0f}%보다 낮아 "
                           f"역률요금 {paid:,.0f}원이 붙었습니다. 그전에는 기준을 넘었으므로 설비 이상을 의심할 수 있습니다.",
                "evidence": [{"label": f"{m} 평균역률 {r['pf']}%, 역률요금 {r.get('pfc') or 0:,.0f}원", "src": r["src"]} for m, r in low[-3:]],
                "amount": round(paid), "amount_label": "이미 낸 역률요금(고지서 합계)",
                "formula": "최근 12개월 고지서의 역률요금 합계",
                "basis": [self.g.for_topic("pf")], "owner": self._fac_label(fac),
                "steps": _steps("pf", False, self.g),
            })

    # -------------------------------------------------- 점검 5: 요금 재계산
    def _check_bill(self):
        tariff = self.cfg["energy"]["tariff"]
        th = self.g["bill_diff_pct"]
        checked, hits = 0, []
        for (fac, ym), r in self.ds.elec.items():
            if ym not in self.recent or None in (r.get("off"), r.get("mid"), r.get("on"), r.get("energy")):
                continue
            rate = tariff[_season(int(ym[5:]))]
            calc = round(r["off"] * rate[0] + r["mid"] * rate[1] + r["on"] * rate[2])
            checked += 1
            diff = r["energy"] - calc
            if calc and abs(diff) / calc * 100 >= th:
                hits.append((fac, ym, r, calc, diff))
        if not checked:
            return
        if len(hits) > checked / 2:
            self.findings.append({
                "type": "tariff", "fac": "", "kind": "elec", "months": [], "level": "gray",
                "title": "설정한 요금 단가가 고지서와 맞지 않음",
                "summary": f"점검한 고지서 {checked}건 중 {len(hits)}건이 단가 재계산과 달라, 고지서가 틀렸다기보다 설정한 단가표가 기관 계약종별과 다를 가능성이 큽니다. "
                           "설정 파일(config.json)의 energy.tariff를 기관 계약 단가로 고치면 요금 검증이 정확해집니다.",
                "evidence": [], "amount": 0, "amount_label": "", "formula": "", "basis": [self.g.for_topic("bill")], "owner": "에너지 관리 담당",
                "steps": [],
            })
            return
        for fac, ym, r, calc, diff in hits:
            self.findings.append({
                "type": "bill", "fac": fac, "kind": "elec", "months": [ym], "level": "bad",
                "title": f"{fac} {int(ym[5:])}월 고지 전력량요금이 단가 계산과 다름",
                "summary": f"고지서의 시간대별 사용량에 단가를 곱하면 {calc:,.0f}원인데 고지 금액은 {r['energy']:,.0f}원으로 "
                           f"{diff:+,.0f}원({diff / calc * 100:+.1f}%) 차이가 납니다.",
                "evidence": [{"label": f"{ym} 경부하 {r['off']:,.0f} · 중간 {r['mid']:,.0f} · 최대 {r['on']:,.0f}kWh, 전력량요금 {r['energy']:,.0f}원", "src": r["src"]}],
                "amount": round(diff), "amount_label": "확인 요청 금액",
                "formula": "고지 전력량요금 - Σ(시간대별 사용량 × 설정 단가). 단가는 config.json energy.tariff",
                "basis": [self.g.for_topic("bill")], "owner": "에너지 관리 담당",
                "steps": _steps("bill", False, self.g),
            })

    # -------------------------------------------------- 조치 효과
    def effects(self):
        out = []
        need = int(self.g["effect_months"])
        for a in self.actions:
            if a.get("status") != "완료":
                out.append({**a, "state": "시행 전", "months": 0})
                continue
            rows = self.series.get((a["fac"], "elec"), [])
            after = [r for r in rows if r["ym"] > a["date"][:7] and r["closed"] == 0 and r["expected"]]
            if len(after) < need:
                out.append({**a, "state": f"측정 중({len(after)}/{need}개월)", "months": len(after)})
                continue
            saved = sum(r["expected"] - r["actual"] for r in after)
            exp = sum(r["expected"] for r in after)
            won = sum((r["expected"] - r["actual"]) * (r["unit"] or 0) for r in after)
            out.append({**a, "state": "효과 확인", "months": len(after), "from": after[0]["ym"], "to": after[-1]["ym"],
                        "saved_kwh": round(saved), "saved_pct": round(saved / exp * 100, 1) if exp else None,
                        "saved_won": round(won), "co2_t": round(saved * self.cfg["energy"]["co2_per_kwh"] / 1000, 1),
                        "note": "휴관 달은 빼고, 시행 전 기준선(기온 보정)과 비교"})
        return out

    # -------------------------------------------------- 감축 목표 진척
    def target(self):
        """지침의 감축 목표 대비 최근 12개월 기온 보정 사용량 변화. 휴관한 달은 뺀다."""
        t = self.g["target_pct"]
        if t is None or not self.recent:
            return None
        facs, A, E = [], 0.0, 0.0
        for fac in self.ds.facilities:
            rows = [r for r in self.series.get((fac, "elec"), []) if r["recent"] and r["expected"] and r["closed"] == 0]
            if not rows:
                continue
            a, e = sum(r["actual"] for r in rows), sum(r["expected"] for r in rows)
            A, E = A + a, E + e
            facs.append({"name": fac, "actual": round(a), "expected": round(e), "change_pct": round((a - e) / e * 100, 1), "months": len(rows)})
        if not E:
            return None
        # 점검 결과(기온 보정 초과 사용)를 조치했다면: 초과분이 없었다고 보고 다시 계산
        fix = sum(f["excess"] for f in self.findings if f["type"] == "excess")
        goal = E * (1 - t / 100)
        return {"target_pct": t, "basis": self.g.basis["target_pct"]["basis"], "actual": round(A), "expected": round(E),
                "change_pct": round((A - E) / E * 100, 1), "goal_kwh": round(goal), "gap_kwh": round(max(0, A - goal)),
                "fix_kwh": round(fix), "after_fix_pct": round((A - fix - E) / E * 100, 1),
                "facilities": sorted(facs, key=lambda x: -x["change_pct"]),
                "note": "최근 12개월 중 휴관한 달을 뺀 달의 실제 사용량을 같은 달 기온만 감안한 예상치(기준선)와 비교"}

    # -------------------------------------------------- 다음 달 관리 목표
    def outlook(self):
        """다음 달 시설별 예상 사용량. 기온은 작년 같은 달 값을 쓴다(예보가 아님)."""
        if not self.latest:
            return None
        nm = ym_add(self.latest, 1)
        ref = ym_add(nm, -12)
        dd = self.dd(ref)
        if dd is None:
            return {"ym": nm, "rows": [], "note": f"{ref} 기온 자료가 없어 계산하지 못했습니다"}
        th = self.g["excess_pct"]
        rows = []
        for fac in self.ds.facilities:
            m = self.models.get((fac, "elec"))
            if not m:
                continue
            exp = predict(m, dd)
            units = [r["unit"] for r in self.series.get((fac, "elec"), []) if r["recent"] and r["unit"]]
            unit = units[-1] if units else None
            rows.append({"name": fac, "expected": round(exp), "limit": round(exp * (1 + th / 100)),
                         "cost": round(exp * unit) if unit else None, "unit": round(unit, 1) if unit else None})
        return {"ym": nm, "ref": ref, "temp": self.ds.weather[ref]["t"], "excess_pct": th, "rows": rows,
                "note": f"기온은 작년 같은 달({ref}) 월평균기온을 썼습니다(예보 아님). 점검선은 지침의 초과 기준 {th:g}%. 요금은 최근 고지서의 kWh당 전력량요금 단가로 계산한 추정치이며 기본요금은 빠져 있습니다."}

    # -------------------------------------------------- 요약
    def overview(self):
        facs = []
        for fac, f in self.ds.facilities.items():
            e = self.series.get((fac, "elec"), [])
            g = self.series.get((fac, "gas"), [])
            rec = [r for r in e if r["recent"]]
            by_ym = {r["ym"]: r for r in e}
            pairs = [(r, by_ym[ym_add(r["ym"], -12)]) for r in rec if ym_add(r["ym"], -12) in by_ym]
            kwh = sum(r["actual"] for r in rec)
            # 전년 대비는 전년 같은 달 자료가 있는 달끼리만 비교
            ckwh, pkwh = sum(a["actual"] for a, _ in pairs), sum(b["actual"] for _, b in pairs)
            norm = [r for r in rec if r["expected"]]
            model = self.models.get((fac, "elec"))
            facs.append({
                "name": fac, "use": f.get("use"), "area": f.get("area"), "manager": f.get("manager"),
                "kwh": round(kwh), "kwh_prev": round(pkwh) if pairs else None, "change_months": len(pairs),
                "change_pct": round((ckwh - pkwh) / pkwh * 100, 1) if pkwh else None,
                "norm_pct": round((sum(r["actual"] for r in norm) - sum(r["expected"] for r in norm)) / sum(r["expected"] for r in norm) * 100, 1) if norm else None,
                "cost": round(sum(r["cost"] or 0 for r in rec) + sum(r["cost"] or 0 for r in g if r["recent"])),
                "m3": round(sum(r["actual"] for r in g if r["recent"])),
                "kwh_per_m2": round(kwh / f["area"], 1) if f.get("area") else None,
                "co2_t": round(kwh * self.cfg["energy"]["co2_per_kwh"] / 1000, 1),
                "findings": sum(1 for x in self.findings if x["fac"] == fac),
                "fit": {"cv_rmse": model["cv_rmse"], "r2": model["r2"], "n": model["n"]} if model else None,
                "spark": [r["actual"] for r in e[-12:]],
            })
        total_amount = sum(max(0, f["amount"] or 0) for f in self.findings)
        return {
            "latest": self.latest, "recent": [self.recent[0], self.recent[-1]] if self.recent else [],
            "baseline": [self.baseline[0], self.baseline[-1]] if self.baseline else [],
            "facilities": facs,
            "totals": {"kwh": sum(f["kwh"] for f in facs), "cost": sum(f["cost"] for f in facs),
                       "co2_t": round(sum(f["co2_t"] for f in facs), 1), "findings": len(self.findings),
                       "amount": total_amount,
                       "excess_kwh": sum(f.get("excess") or 0 for f in self.findings if f.get("unit") == "kWh"),
                       "excess_co2_t": round(sum(f.get("excess") or 0 for f in self.findings if f.get("unit") == "kWh") * self.cfg["energy"]["co2_per_kwh"] / 1000, 1)},
            "rules": list(self.g.basis.values()),
            "co2_factor": self.cfg["energy"]["co2_per_kwh"],
            "target": self.target(), "outlook": self.outlook(),
        }


def _mrange(months):
    if not months:
        return ""
    a, b = months[0], months[-1]
    if a == b:
        return f"{a[:4]}년 {int(a[5:])}월"
    if a[:4] == b[:4]:
        return f"{a[:4]}년 {int(a[5:])}~{int(b[5:])}월"
    return f"{a[:4]}년 {int(a[5:])}월~{b[:4]}년 {int(b[5:])}월"


def _steps(kind, consec, g):
    sp = int(g["cool_setpoint"])
    S = {
        "cooling": ["냉방 설정온도가 지침 기준(" + f"{sp}℃ 이상)을 지키는지 현장 확인",
                    "운영시간 외(야간·휴관일) 냉방기·공조기 가동 기록 확인", "냉동기·냉각탑 등 냉방 설비 효율 점검(필터·냉매)"],
        "heating": ["난방 설정온도와 운영시간 외 가동 여부 확인", "보일러·난방기 가동 기록 확인"],
        "excess": ["그 달 행사·대관·이용객 증가 등 사용량이 늘 만한 일이 있었는지 확인", "운영시간 외 가동 설비 확인"],
        "gas": ["보일러 연소 효율·배기가스 점검", "급탕·수영장 수온 설정과 가동 시간 확인", "배관·밸브 누설 확인"],
        "closure": ["휴관 기간 설비 가동 기록(냉난방·공조·조명) 확인", "다음 휴관부터 설비 정지 계획을 미리 받아 두기"],
        "contract": ["최근 12개월 최대수요전력 기록과 증설 계획 확인", "한국전력공사에 계약전력(요금적용전력) 조정 가능 여부 문의"],
        "pf": ["진상용 콘덴서 고장·용량 부족 점검(전기안전관리자)", "역률 개선 후 다음 달 고지서로 효과 확인"],
        "bill": ["고지서 원본과 계약종별 단가 확인", "차이가 맞으면 공급사(한국전력공사)에 정정·환급 요청"],
    }
    steps = list(S.get(kind, []))
    if consec:
        steps.insert(0, "시설 관리자와 합동 현장 점검 일정 잡기")
    return steps
