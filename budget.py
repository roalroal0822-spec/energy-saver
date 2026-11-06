"""예산 관리: 올해 공공요금 예산(본예산·추경) 집행 관리와 내년 예산 편성 근거.

모든 금액은 규칙과 계산으로 낸다(모델 없이 같은 결과).
- 집행액: 넣은 전기·가스 내역(고지서)의 청구금액을 월별로 더한다(고지 기준).
- 남은 달 예측: 시설별 기온 보정 모델 × 최근 사용 수준(기준선 대비 실제 비율)으로 사용량을 내고,
  요금 구조(기본요금·계절별 전력량 단가·부가세, 가스 ㎥당 단가)에 유가·환율 연동을 곱한다.
  기온은 그 달의 평년값(넣은 기온 자료에서 같은 달 평균)을 쓴다. 오차 범위는 모델 오차율(CV-RMSE).
- 판정: 예산현액(본예산+추경+전용) - (집행액 + 남은 달 예측) → 불용 예상 / 적정 / 부족 예상(소진 달).
- 내년 편성: 평년 기온 × 최근 사용 수준 × (1 - 추가 절감 목표) 사용량에 내년 단가 전망을 곱하고,
  이상기온(폭염·한파) 시나리오와의 차이를 '기후 위험 여유분'으로 따로 낸다.
가정값(유가 연동 비율·시차, 기후 시나리오 폭, 불용 판정 기준)은 config.json energy.budget에서 바꾼다.
"""
import math

from analysis import predict
from ingest import ym_add, ym_days

KIND = {"전기료": "elec", "가스료": "gas"}
UNIT = {"elec": "kWh", "gas": "㎥"}
DEFAULT = {"unused_pct": 5, "gas_oil_pass": 0.6, "oil_lag": 3, "elec_oil_pass": 0.1, "elec_fuel_cap": 5,
           "climate_summer": 1.5, "climate_winter": 2.0, "elec_up_pct": 0, "gas_up_pct": 0, "save_pct": 0}


def _season(m):
    return "summer" if m in (6, 7, 8) else "winter" if m in (11, 12, 1, 2) else "spring"


def ceil_k(x):
    """예산은 천원 단위 올림."""
    return int(math.ceil(x / 1000.0) * 1000) if x > 0 else 0


class Budget:
    def __init__(self, an, cfg, opts=None):
        self.an, self.ds = an, an.ds
        b = dict(DEFAULT)
        b.update((cfg["energy"].get("budget") or {}))
        for k, v in (opts or {}).items():
            if k in DEFAULT and v is not None:
                b[k] = float(v)
        self.p = b
        self.co2 = cfg["energy"]["co2_per_kwh"]
        self.latest = an.latest
        self.fy = int(self.latest[:4]) if self.latest else None
        self.notes = []
        self._perf, self._price = {}, {}
        self._oil_ref = self._oil_reference()

    # -------------------------------------------------- 기온·사용량
    def normal_temp(self, mo):
        vals = [w["t"] for ym, w in self.ds.weather.items() if int(ym[5:]) == mo]
        return round(sum(vals) / len(vals), 1) if vals else None

    def dd(self, ym, t):
        e = self.an.cfg["energy"]
        d = ym_days(ym)
        return d * max(0.0, t - e["cdd_base"]), d * max(0.0, e["hdd_base"] - t)

    def perf(self, fac, kind):
        """최근 12개월(휴관 달 제외) 실제 ÷ 기온만 감안한 예상. 조치 효과·초과 사용이 모두 반영된 '지금 사용 수준'."""
        k = (fac, kind)
        if k not in self._perf:
            rows = [r for r in self.an.series.get(k, []) if r["recent"] and r["expected"] and r["closed"] == 0]
            a, e = sum(r["actual"] for r in rows), sum(r["expected"] for r in rows)
            self._perf[k] = min(1.3, max(0.7, a / e)) if e else 1.0
        return self._perf[k]

    def usage(self, fac, kind, ym, t):
        """그 달 기온이 t일 때 예상 사용량과 오차율(%)."""
        m = self.an.models.get((fac, kind))
        if m and t is not None:
            return max(0.0, predict(m, self.dd(ym, t)) * self.perf(fac, kind)), (m.get("cv_rmse") or 0)
        table = self.ds.elec if kind == "elec" else self.ds.gas
        field = "kwh" if kind == "elec" else "m3"
        for back in (12, 24):
            r = table.get((fac, ym_add(ym, -back)))
            if r:
                return r[field], 10.0
        return None, None

    # -------------------------------------------------- 요금 구조·유가
    def price(self, fac, kind):
        """최근 고지서로 읽은 요금 구조. 전기: 월 고정(기본+역률), 계절별 kWh당 전력량 단가, 부가세 배율. 가스: ㎥당 단가."""
        k = (fac, kind)
        if k in self._price:
            return self._price[k]
        table = self.ds.elec if kind == "elec" else self.ds.gas
        rows = sorted(((ym, r) for (f, ym), r in table.items() if f == fac), key=lambda x: x[0])
        out = None
        if kind == "elec" and rows:
            ym0, last = rows[-1]
            var = {}
            for ym, r in reversed(rows):
                s = _season(int(ym[5:]))
                if s not in var and r.get("energy") and r.get("kwh"):
                    var[s] = r["energy"] / r["kwh"]
            sub = (last.get("basic") or 0) + (last.get("energy") or 0) + (last.get("pfc") or 0)
            if var and last.get("basic") is not None and last.get("total") and sub:
                out = {"fixed": (last.get("basic") or 0) + (last.get("pfc") or 0), "var": var, "tax": last["total"] / sub,
                       "from": ym0, "src": last.get("src"), "mode": "구조"}
            elif last.get("total") and last.get("kwh"):  # 청구금액만 있는 자료
                out = {"fixed": 0, "var": {}, "flat": last["total"] / last["kwh"], "tax": 1.0, "from": ym0, "src": last.get("src"), "mode": "평균"}
        elif kind == "gas" and rows:
            ym0, last = rows[-1]
            if last.get("total") and last.get("m3"):
                out = {"unit": last["total"] / last["m3"], "from": ym0, "src": last.get("src")}
        self._price[k] = out
        return out

    def _oilfx(self, ym):
        if not self.ds.oil:
            return None
        if ym not in self.ds.oil:
            keys = sorted(self.ds.oil)
            ym = keys[-1] if ym > keys[-1] else keys[0]
        o = self.ds.oil[ym]
        return o["oil"] * (o["fx"] or 1)

    def _oil_reference(self):
        """지금 단가에 이미 반영된 유가 수준: 최신 달까지 12개월(시차 반영) 평균."""
        if not self.ds.oil or not self.latest:
            return None
        lag = int(self.p["oil_lag"])
        vals = [self._oilfx(ym_add(self.latest, -lag - i)) for i in range(12)]
        vals = [v for v in vals if v]
        return sum(vals) / len(vals) if vals else None

    def oil_ratio(self, ym):
        if not self._oil_ref:
            return 1.0
        return self._oilfx(ym_add(ym, -int(self.p["oil_lag"]))) / self._oil_ref

    def cost(self, fac, kind, ym, qty, next_year=False):
        pr = self.price(fac, kind)
        if pr is None or qty is None:
            return None
        r = self.oil_ratio(ym)
        if kind == "gas":
            u = pr["unit"] * (1 + self.p["gas_oil_pass"] * (r - 1))
            if next_year:
                u *= 1 + self.p["gas_up_pct"] / 100
            return qty * u
        if pr["mode"] == "평균":
            v = pr["flat"]
            fixed = 0
        else:
            v = pr["var"].get(_season(int(ym[5:]))) or next(iter(pr["var"].values()))
            fixed = pr["fixed"]
        cap = self.p["elec_fuel_cap"]
        v += max(-cap, min(cap, v * self.p["elec_oil_pass"] * (r - 1)))  # 연료비 조정(상·하한)
        c = (fixed + qty * v) * pr["tax"]
        if next_year:
            c *= 1 + self.p["elec_up_pct"] / 100
        return c

    # -------------------------------------------------- 올해 예산 관리
    def _spent(self, fac, kind, ym):
        table = self.ds.elec if kind == "elec" else self.ds.gas
        r = table.get((fac, ym))
        return (r.get("total"), r.get("src")) if r else (None, None)

    def current(self):
        if not self.fy:
            return None
        rows = [b for b in self.ds.budget if b["fy"] in (None, self.fy)]
        if not rows:
            return {"fy": self.fy, "items": [], "note": "예산서가 없습니다. '자료 넣기'에 본예산·추경 엑셀을 넣으면 집행 관리를 시작합니다."}
        done = [f"{self.fy}-{m:02d}" for m in range(1, 13) if f"{self.fy}-{m:02d}" <= self.latest]
        todo = [f"{self.fy}-{m:02d}" for m in range(1, 13) if f"{self.fy}-{m:02d}" > self.latest]
        th = self.p["unused_pct"]
        items, monthly = [], {ym: {"spent": 0.0, "fc": 0.0, "lo": 0.0, "hi": 0.0} for ym in done + todo}
        for b in rows:
            kind = KIND[b["item"]]
            fac = b["fac"]
            cur = b["main"] + b["supp"] + b["transfer"]
            spent, srcs, miss, series = 0.0, [], [], []
            for ym in done:
                v, s = self._spent(fac, kind, ym)
                if v is None:
                    miss.append(ym)
                    continue
                spent += v
                srcs.append(s)
                monthly[ym]["spent"] += v
                series.append({"ym": ym, "spent": round(v)})
            fc = lo = hi = 0.0
            fc_rows = []
            for ym in miss + todo:  # 고지 자료가 아직 없는 지난달도 예측으로 채운다
                t = self.normal_temp(int(ym[5:]))
                q, err = self.usage(fac, kind, ym, t)
                c = self.cost(fac, kind, ym, q)
                if c is None:
                    continue
                e = (err or 0) / 100
                cl, ch = self.cost(fac, kind, ym, q * (1 - e)), self.cost(fac, kind, ym, q * (1 + e))
                fc, lo, hi = fc + c, lo + cl, hi + ch
                monthly[ym]["fc"] += c
                monthly[ym]["lo"] += cl
                monthly[ym]["hi"] += ch
                fc_rows.append({"ym": ym, "temp": t, "qty": round(q), "cost": round(c), "lo": round(cl), "hi": round(ch), "billed_missing": ym in miss})
            exp_total = spent + fc
            remain = cur - exp_total
            rp = remain / cur * 100 if cur else 0
            # 소진 달: 누적(집행 + 예측)이 현액을 넘는 첫 달
            run_out, acc = None, 0.0
            for x in series:
                acc += x["spent"]
                if acc > cur and not run_out:
                    run_out = x["ym"]
            for x in fc_rows:
                acc += x["cost"]
                if acc > cur and not run_out:
                    run_out = x["ym"]
            if remain < 0:
                state = "부족 예상"
            elif rp >= th:
                state = "불용 예상"
            else:
                state = "적정"
            rlo, rhi = cur - (spent + hi), cur - (spent + lo)
            items.append({
                "fac": fac, "item": b["item"], "kind": kind, "main": round(b["main"]), "supp": round(b["supp"]), "transfer": round(b["transfer"]),
                "supp_cols": b["supp_cols"], "current": round(cur), "spent": round(spent), "fc": round(fc), "expected": round(exp_total),
                "remain": round(remain), "remain_pct": round(rp, 1), "remain_range": [round(rlo), round(rhi)],
                "exec_pct": round(spent / cur * 100, 1) if cur else None, "state": state, "run_out": run_out,
                "uncertain": (rlo < 0 <= remain) or (remain < 0 <= rhi) or (rlo / cur * 100 < th <= rp if cur else False),
                "months_spent": series, "months_fc": fc_rows, "missing": miss, "note": b["note"], "src": b["src"],
            })
        cur_t = sum(i["current"] for i in items)
        spent_t = sum(i["spent"] for i in items)
        exp_t = sum(i["expected"] for i in items)
        # 전용 검토: 부족 항목을 불용 예상 항목의 잔액으로(기준선 th% 만큼은 남겨 둠)
        short = sorted([i for i in items if i["remain"] < 0], key=lambda i: i["remain"])
        spare = sorted([[i, i["remain"] - i["current"] * th / 100] for i in items if i["state"] == "불용 예상"], key=lambda x: -x[1])
        moves = []
        for s in short:
            need = ceil_k(-s["remain"])
            for sp in spare:
                if need <= 0:
                    break
                give = min(need, int(sp[1] // 1000 * 1000))
                if give < 1_000_000:  # 백만 원 미만은 전용 대상으로 보지 않음
                    continue
                moves.append({"from": f"{sp[0]['fac']} {sp[0]['item']}", "to": f"{s['fac']} {s['item']}", "amount": give})
                sp[1] -= give
                need -= give
            s["need"] = ceil_k(-s["remain"])
            s["need_after_moves"] = max(0, need)
        cum, acc = [], 0.0
        for ym in done + todo:
            m = monthly[ym]
            acc += m["spent"] + m["fc"]
            cum.append({"ym": ym, "spent": round(m["spent"]), "fc": round(m["fc"]), "lo": round(m["lo"]), "hi": round(m["hi"]), "cum": round(acc), "actual": ym in done})
        main_t = sum(i["main"] for i in items)
        return {
            "fy": self.fy, "latest": self.latest, "months_done": len(done), "elapsed_pct": round(len(done) / 12 * 100, 1),
            "items": sorted(items, key=lambda i: ({"부족 예상": 0, "불용 예상": 1}.get(i["state"], 2), i["remain_pct"])),
            "totals": {"main": main_t, "supp": sum(i["supp"] + i["transfer"] for i in items), "current": cur_t, "spent": spent_t,
                       "fc": sum(i["fc"] for i in items), "expected": exp_t, "remain": cur_t - exp_t,
                       "exec_pct": round(spent_t / cur_t * 100, 1) if cur_t else None,
                       "short": sum(1 for i in items if i["state"] == "부족 예상"), "unused": sum(1 for i in items if i["state"] == "불용 예상"),
                       "unused_amount": sum(i["remain"] for i in items if i["state"] == "불용 예상"),
                       "short_amount": -sum(i["remain"] for i in items if i["remain"] < 0)},
            "moves": moves, "cum": cum, "unused_pct": th,
            "note": f"집행액은 넣은 고지서·내역의 청구금액(고지 기준, {done[0] if done else ''}~{self.latest})입니다. 남은 달은 평년 기온과 최근 사용 수준으로 예측했고, "
                    f"범위는 시설별 기온 보정 모델의 오차율입니다. 잔액이 예산현액의 {th:g}% 이상이면 '불용 예상'으로 봅니다(설정값).",
        }

    # -------------------------------------------------- 다음 달 예측
    def next_month(self):
        if not self.latest:
            return None
        nm = ym_add(self.latest, 1)
        t = self.normal_temp(int(nm[5:]))
        rows = []
        for fac in self.ds.facilities:
            rec = {"fac": fac}
            for kind in ("elec", "gas"):
                if (fac, kind) not in self.an.series:
                    continue
                q, err = self.usage(fac, kind, nm, t)
                c = self.cost(fac, kind, nm, q, next_year=nm[:4] != self.latest[:4])
                if c is None:
                    continue
                e = (err or 0) / 100
                rec[kind] = {"qty": round(q), "cost": round(c), "lo": round(c * (1 - e)), "hi": round(c * (1 + e)), "err": err}
            if len(rec) > 1:
                rec["cost"] = sum(rec[k]["cost"] for k in ("elec", "gas") if k in rec)
                rows.append(rec)
        return {"ym": nm, "temp": t, "rows": rows, "total": sum(r["cost"] for r in rows),
                "oil_ratio": round(self.oil_ratio(nm), 3),
                "note": f"기온은 넣은 기온 자료의 {int(nm[5:])}월 평균(평년값, {t}℃)입니다. 사용량은 기온 보정 모델 × 최근 사용 수준, 요금은 최근 고지서의 요금 구조에 유가·환율 연동을 반영한 추정치입니다."}

    # -------------------------------------------------- 내년 예산 편성
    def next_year(self):
        if not self.fy:
            return None
        ny = self.fy + 1
        save = self.p["save_pct"] / 100
        cur = {(b["fac"], b["item"]): b["main"] + b["supp"] + b["transfer"] for b in self.ds.budget if b["fy"] in (None, self.fy)}
        this = {(i["fac"], i["item"]): i for i in (self.current() or {}).get("items", [])}
        items = []
        for fac in self.ds.facilities:
            for item, kind in KIND.items():
                if (fac, kind) not in self.an.series or self.price(fac, kind) is None:
                    continue
                q_t = c_t = risk = 0.0
                months = []
                for mo in range(1, 13):
                    ym = f"{ny}-{mo:02d}"
                    t = self.normal_temp(mo)
                    q, _ = self.usage(fac, kind, ym, t)
                    if q is None:
                        continue
                    q *= 1 - save
                    c = self.cost(fac, kind, ym, q, next_year=True)
                    # 이상기온 시나리오: 여름(6~9월) 더 덥게, 겨울(12~2월) 더 춥게
                    ts = t + self.p["climate_summer"] if mo in (6, 7, 8, 9) else t - self.p["climate_winter"] if mo in (12, 1, 2) else t
                    qs, _ = self.usage(fac, kind, ym, ts)
                    cs = self.cost(fac, kind, ym, qs * (1 - save), next_year=True) if qs is not None else c
                    q_t, c_t = q_t + q, c_t + c
                    risk += max(0.0, cs - c)
                    months.append({"ym": ym, "temp": t, "qty": round(q), "cost": round(c), "risk": round(max(0.0, cs - c))})
                if not months:
                    continue
                pr = self.price(fac, kind)
                req = ceil_k(c_t)
                prev = cur.get((fac, item))
                exp_now = this.get((fac, item), {}).get("expected")
                r_avg = sum(self.oil_ratio(m["ym"]) for m in months) / len(months)
                if kind == "elec":
                    basis = (f"사용량 {round(q_t):,}kWh(평년 기온·최근 사용 수준"
                             + (f", 추가 절감 {self.p['save_pct']:g}%" if save else "") + ") × 평균 "
                             + f"{c_t / q_t:,.1f}원/kWh(기본요금·계절별 전력량 단가·부가세"
                             + (f", 요금 인상 {self.p['elec_up_pct']:g}%" if self.p["elec_up_pct"] else "") + ")")
                else:
                    basis = (f"사용량 {round(q_t):,}㎥(평년 기온·최근 사용 수준" + (f", 추가 절감 {self.p['save_pct']:g}%" if save else "")
                             + f") × 평균 {c_t / q_t:,.1f}원/㎥(현재 {pr['unit']:,.1f}원/㎥, 유가·환율 연동 {(r_avg - 1) * self.p['gas_oil_pass'] * 100:+.1f}%"
                             + (f", 요금 인상 {self.p['gas_up_pct']:g}%" if self.p["gas_up_pct"] else "") + ")")
                items.append({"fac": fac, "item": item, "kind": kind, "qty": round(q_t), "unit": UNIT[kind], "cost": round(c_t), "request": req,
                              "risk": ceil_k(risk), "prev_budget": round(prev) if prev is not None else None,
                              "prev_expected": exp_now, "change_pct": round((req - prev) / prev * 100, 1) if prev else None,
                              "basis": basis, "months": months, "price_from": pr.get("from"), "price_src": pr.get("src"),
                              "co2_t": round(q_t * self.co2 / 1000, 1) if kind == "elec" else None})
        req_t = sum(i["request"] for i in items)
        risk_t = sum(i["risk"] for i in items)
        prev_t = sum(i["prev_budget"] or 0 for i in items)
        kwh = sum(i["qty"] for i in items if i["kind"] == "elec")
        # 감축 목표: 지침 목표만큼 더 줄이면
        tg = self.an.g["target_pct"]
        return {
            "fy": ny, "items": items,
            "totals": {"request": req_t, "risk": risk_t, "with_risk": req_t + risk_t, "prev_budget": prev_t,
                       "change_pct": round((req_t - prev_t) / prev_t * 100, 1) if prev_t else None,
                       "kwh": kwh, "co2_t": round(kwh * self.co2 / 1000, 1),
                       "target_pct": tg, "co2_target_t": round(kwh * (1 - (tg or 0) / 100) * self.co2 / 1000, 1) if tg else None},
            "assume": self.assumptions(ny),
            "oil": self.oil_summary(ny),
            "note": "요구액은 천원 단위 올림입니다. 기후 위험 여유분은 이상기온(여름 +{0:g}℃, 겨울 -{1:g}℃) 때 더 드는 금액으로, 요구액에 넣을지는 기관이 정합니다."
                    .format(self.p["climate_summer"], self.p["climate_winter"]),
        }

    def assumptions(self, ny):
        temps = {mo: self.normal_temp(mo) for mo in range(1, 13)}
        yrs = sorted({ym[:4] for ym in self.ds.weather})
        return [
            {"k": "기온", "v": "평년값(넣은 기온 자료의 같은 달 평균, " + (f"{yrs[0]}~{yrs[-1]}" if yrs else "자료 없음") + ")",
             "detail": ", ".join(f"{mo}월 {t}℃" for mo, t in temps.items() if t is not None)},
            {"k": "사용 수준", "v": "최근 12개월 실제 ÷ 기온만 감안한 예상(시설·에너지별)", "detail": "LED 교체 같은 조치 효과와 최근 초과 사용이 모두 반영됨"},
            {"k": "추가 절감 목표", "v": f"{self.p['save_pct']:g}%", "detail": "내년에 더 줄일 목표를 넣으면 사용량에서 뺍니다"},
            {"k": "전기요금 인상", "v": f"{self.p['elec_up_pct']:g}%", "detail": f"연료비 조정은 유가 연동 {self.p['elec_oil_pass'] * 100:g}%, kWh당 ±{self.p['elec_fuel_cap']:g}원 상·하한(가정)"},
            {"k": "가스 단가", "v": f"유가·환율 연동 {self.p['gas_oil_pass'] * 100:g}% · 시차 {int(self.p['oil_lag'])}개월"
                                   + (f" · 요금 인상 {self.p['gas_up_pct']:g}%" if self.p["gas_up_pct"] else ""),
             "detail": "도시가스 요금 중 원료비 비중을 연동 비율로 가정(설정값). 기준은 지금 단가에 반영된 최근 12개월 유가×환율 평균"},
            {"k": "기후 위험", "v": f"여름(6~9월) +{self.p['climate_summer']:g}℃, 겨울(12~2월) -{self.p['climate_winter']:g}℃",
             "detail": "이상기온 때 더 드는 금액을 여유분으로 따로 계산"},
        ]

    def oil_summary(self, ny):
        if not self.ds.oil:
            return {"has": False, "note": "국제유가 자료가 없어 유가 연동 없이(지금 단가 그대로) 계산했습니다."}
        keys = sorted(self.ds.oil)
        lag = int(self.p["oil_lag"])
        nxt = [self._oilfx(ym_add(f"{ny}-{m:02d}", -lag)) for m in range(1, 13)]
        avg_n = sum(nxt) / len(nxt)
        o_now = [self.ds.oil[k]["oil"] for k in keys if k <= (self.latest or "") ][-12:]
        o_next = [self.ds.oil.get(ym_add(f"{ny}-{m:02d}", -lag), self.ds.oil[keys[-1]])["oil"] for m in range(1, 13)]
        fc = [k for k in keys if "전망" in (self.ds.oil[k]["kind"] or "")]
        return {"has": True, "series": [{"ym": k, "oil": self.ds.oil[k]["oil"], "fx": self.ds.oil[k]["fx"], "kind": self.ds.oil[k]["kind"]} for k in keys],
                "now_avg": round(sum(o_now) / len(o_now), 1) if o_now else None, "next_avg": round(sum(o_next) / len(o_next), 1),
                "ratio": round(avg_n / self._oil_ref, 3) if self._oil_ref else None,
                "gas_change_pct": round((avg_n / self._oil_ref - 1) * self.p["gas_oil_pass"] * 100, 1) if self._oil_ref else None,
                "forecast_from": fc[0] if fc else None, "src": self.ds.oil[keys[-1]]["src"],
                "note": f"가스 단가는 {lag}개월 전 유가×환율을 따라간다고 가정합니다. 전망값은 넣은 자료의 '전망' 행이며, 자료가 끝난 뒤 달은 마지막 값을 씁니다."}

    def run(self):
        return {"current": self.current(), "next_month": self.next_month(), "next_year": self.next_year(), "params": self.p}
