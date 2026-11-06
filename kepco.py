"""한전 전력데이터 개방포털(bigdata.kepco.co.kr) Open API에서 지역 집계 통계를 받는다.

- 계약종별 전력사용량(/openapi/v1/powerUsage/contractType.do): 시도·시군구·계약종별 달마다 고객호수, 사용량(kWh), 전기요금(원), 평균판매단가(원/kWh)
- 신재생에너지 계약현황(/openapi/v1/renewEnergy.do): 연도·시도별 발전원(태양광 등) 한전 전력거래계약 개수·용량
- 공통코드정보(/openapi/v1/commonCode.do): 시군구 이름을 코드로 바꿀 때
시설(고객번호) 단위 자료는 포털에 없으므로, 우리 시설 고지서와 '같은 지역·같은 계약종별 평균'을 견주는 데만 쓴다.
키는 환경변수 KEPCO_API_KEY로만 읽고 화면·파일·기록에 남기지 않는다. 지역은 KEPCO_METRO(시도 코드, 기본 11 서울특별시),
KEPCO_CITY(시군구 이름 또는 코드, 기본 도봉구). 받은 값은 data/kepco_cache.json에 달 단위로 보관한다.
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date

from kma import _clean

BASE = os.environ.get("KEPCO_API_URL", "https://bigdata.kepco.co.kr/openapi/v1")  # 시험용으로만 바꿈
TIMEOUT = 10
METRO = os.environ.get("KEPCO_METRO", "11")
CITY = os.environ.get("KEPCO_CITY", "도봉구")
SRC = "한전 전력데이터 개방포털"
RETRY_EMPTY = 86400  # 아직 공개 전(전전월 통계를 매월 16일경 갱신)인 달은 하루 뒤 다시 묻는다


def key():
    return _clean(os.environ.get("KEPCO_API_KEY", ""), "apiKey")


def _get(path, **q):
    q.update(apiKey=key(), returnType="json")
    url = f"{BASE}/{path}?{urllib.parse.urlencode(q)}"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        text = r.read().decode("utf-8", "replace")
    try:
        d = json.loads(text)
    except ValueError:
        m = re.search(r"(인증|apiKey|error|오류|에러)[^<\n]{0,80}", text, re.I)
        raise RuntimeError(m.group(0) if m else "JSON이 아닌 응답")
    if not isinstance(d, dict) or not ("data" in d or "totData" in d):
        raise RuntimeError(str(d)[:100])
    return d


def _n(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def city_code(cache):
    """KEPCO_CITY가 이름이면 공통코드에서 코드로 바꾼다(시도 코드가 같은 것)."""
    if not CITY or CITY.isdigit():
        return CITY
    c = cache.get("city")
    if c and c.get("name") == CITY and c.get("metro") == METRO:
        return c["code"]
    rows = _get("commonCode.do", codeTy="cityCd").get("data") or []
    hit = [r for r in rows if r.get("codeNm") == CITY and str(r.get("uppoCd", METRO)) == METRO]
    if not hit:
        raise RuntimeError(f"공통코드에서 시도 {METRO}의 '{CITY}'를 찾지 못했습니다")
    cache["city"] = {"name": CITY, "metro": METRO, "code": str(hit[0]["code"])}
    return cache["city"]["code"]


def parse_contract(d):
    """계약종별 응답 → {계약종별: {cust, kwh, won, unit}}. 시군구 여러 개가 오면 더한다."""
    out = {}
    for r in d.get("data") or d.get("totData") or []:
        c = (r.get("cntr") or "").strip()
        kwh, won, cust = _n(r.get("powerUsage")), _n(r.get("bill")), _n(r.get("custCnt"))
        if not c or kwh is None:
            continue
        o = out.setdefault(c, {"cust": 0, "kwh": 0, "won": 0, "area": ""})
        o["cust"] += cust or 0
        o["kwh"] += kwh
        o["won"] += won or 0
        o["area"] = " ".join(x for x in (r.get("metro"), r.get("city")) if x)
    for o in out.values():
        o["unit"] = round(o["won"] / o["kwh"], 1) if o["kwh"] else None
    return out


def collect(months, cache):
    """months(YYYY-MM)의 지역 계약종별 통계. cache 갱신. (결과 {ym: {계약종별: ...}}, 오류 문장)"""
    if not key():
        return {}, "한전 전력데이터 개방포털 키(KEPCO_API_KEY)가 없습니다"
    mc = cache.setdefault("months", {})
    if cache.get("region") != f"{METRO}/{CITY}":
        mc.clear()
        cache.pop("city", None)
        cache["region"] = f"{METRO}/{CITY}"
    today = date.today().strftime("%Y-%m")
    err = ""
    try:
        cc = city_code(cache)
        for ym in months:
            if ym >= today:
                continue
            got = mc.get(ym)
            if got and (got.get("rows") or time.time() - got.get("at", 0) < RETRY_EMPTY):
                continue
            q = {"year": ym[:4], "month": ym[5:], "metroCd": METRO}
            if cc:
                q["cityCd"] = cc
            rows = parse_contract(_get("powerUsage/contractType.do", **q))
            mc[ym] = {"rows": rows, "at": time.time()}
    except Exception as e:  # 키 오류·승인 전·연결 실패
        err = f"한전 자료를 받지 못했습니다({str(e)[:100]})"
    return {ym: mc[ym]["rows"] for ym in months if mc.get(ym, {}).get("rows")}, err


def renew(year, cache):
    """year의 신재생 계약현황 중 태양광: 우리 시군구와 시도 합계. 없으면 None."""
    rc = cache.setdefault("renew", {})
    k = f"{year}/{METRO}/{CITY}"
    if k in rc and (rc[k] or time.time() - rc.get(k + "@", 0) < RETRY_EMPTY):
        return rc[k]
    rows = _get("renewEnergy.do", year=str(year), metroCd=METRO).get("data") or []
    sun = [r for r in rows if "태양광" in (r.get("genSrc") or "")]
    mine = next((r for r in sun if (r.get("city") or "").strip() == CITY), None)
    out = None
    if sun:
        out = {"year": year, "city": CITY, "metro": sun[0].get("metro"), "cnt": _n(mine.get("cnt")) if mine else None,
               "kw": _n(mine.get("capacity")) if mine else None, "area_cnt": _n(sun[0].get("areaCnt")), "area_kw": _n(sun[0].get("areaCapacity"))}
    rc[k], rc[k + "@"] = out, time.time()
    return out


def contract_of(tariff, use=""):
    """고지서 계약종별(예: 일반용(을)고압A)·시설 용도 → 포털 계약종별 이름."""
    t = f"{tariff or ''} {use or ''}"
    for c in ("교육용", "산업용", "농사용", "주택용"):
        if c[:2] in t:
            return c
    return "일반용"


def _yoy(cur, prev):
    return round((cur / prev - 1) * 100, 1) if cur and prev else None


def compare(a, region, tariffs):
    """우리 시설(최근 12개월) 전기 단가·전년 대비 증감을 같은 지역·같은 계약종별 평균과 견준다.
    a: analysis.Analysis, region: collect 결과, tariffs: {시설: 고지서 계약종별}."""
    if not region:
        return None
    use = {f["name"]: f.get("use") or "" for f in a.overview()["facilities"]}
    facs, tot = [], {}
    for (fac, kind), rows in a.series.items():
        if kind != "elec":
            continue
        cntr = contract_of(tariffs.get(fac), use.get(fac))
        byym = {r["ym"]: r for r in rows}
        rec = [r for r in rows if r["recent"] and r["ym"] in region and cntr in region[r["ym"]] and r["actual"]]
        for r in rows:
            t = tot.setdefault(r["ym"], 0)
            tot[r["ym"]] = t + (r["actual"] or 0)
        if not rec:
            continue
        kwh = sum(r["actual"] for r in rec)
        won = sum(r["actual"] * (r["unit"] or 0) for r in rec)
        rk = sum(region[r["ym"]][cntr]["kwh"] for r in rec)
        rw = sum(region[r["ym"]][cntr]["won"] for r in rec)
        our_u = won / kwh if kwh and won else None
        reg_u = rw / rk if rk else None
        pairs = [(r, byym.get(_ym(r["ym"], -12))) for r in rec]
        pairs = [(r, p) for r, p in pairs if p and p["actual"] and _ym(r["ym"], -12) in region and cntr in region[_ym(r["ym"], -12)]]
        oy = _yoy(sum(r["actual"] for r, _ in pairs), sum(p["actual"] for _, p in pairs)) if pairs else None
        ry = _yoy(sum(region[r["ym"]][cntr]["kwh"] for r, _ in pairs), sum(region[p["ym"]][cntr]["kwh"] for _, p in pairs)) if pairs else None
        facs.append({"fac": fac, "cntr": cntr, "tariff": tariffs.get(fac) or "", "months": len(rec), "from": rec[0]["ym"], "to": rec[-1]["ym"],
                     "kwh": round(kwh), "our_unit": round(our_u, 1) if our_u else None, "region_unit": round(reg_u, 1) if reg_u else None,
                     "unit_gap_pct": round((our_u / reg_u - 1) * 100, 1) if our_u and reg_u else None,
                     "gap_won": round((our_u - reg_u) * kwh) if our_u and reg_u else None,
                     "yoy_months": len(pairs), "our_yoy": oy, "region_yoy": ry,
                     "yoy_gap": round(oy - ry, 1) if oy is not None and ry is not None else None})
    monthly = []
    for ym in sorted(region):
        p = _ym(ym, -12)
        g, gp = region[ym].get("일반용"), region.get(p, {}).get("일반용")
        monthly.append({"ym": ym, "region_kwh": round(g["kwh"]) if g else None, "region_unit": g["unit"] if g else None,
                        "region_yoy": _yoy(g["kwh"], gp["kwh"]) if g and gp else None,
                        "our_kwh": round(tot.get(ym, 0)), "our_yoy": _yoy(tot.get(ym), tot.get(p))})
    latest = max(region)
    area = next((v.get("area") for v in region[latest].values() if v.get("area")), CITY)
    return {"area": area, "months": len(region), "range": [min(region), latest], "facilities": facs, "monthly": monthly,
            "latest": {"ym": latest, "rows": [dict(cntr=c, **{k: (round(v[k]) if k != "unit" else v[k]) for k in ("cust", "kwh", "won", "unit")})
                                                for c, v in sorted(region[latest].items(), key=lambda x: -x[1]["kwh"])]}}


def _ym(ym, k):
    y, m = int(ym[:4]), int(ym[5:]) - 1 + k
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"
