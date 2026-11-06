"""기온 자동 수집: 기상청 지상관측(ASOS) 일자료로 월평균기온과 일별 기온을 받는다.

두 가지 창구 중 키가 있는 쪽을 쓴다(둘 다 있으면 API허브 먼저).
- 기상청 API허브(apihub.kma.go.kr): 환경변수 KMA_APIHUB_KEY, 지상관측 > 종관기상관측(ASOS) 일자료 기간 조회(kma_sfcdd3.php)
- 공공데이터포털(data.go.kr): 환경변수 DATA_GO_KR_KEY, '기상청_지상(종관, ASOS) 일자료 조회서비스'(AsosDalyInfoService)
지점은 WEATHER_STN(기본 108 서울). 받은 값은 data/kma_cache.json에 달 단위로 보관해 같은 달을 다시 받지 않는다.
키는 환경변수로만 읽고 화면·파일·기록에 남기지 않는다.
"""
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import date, timedelta

HUB = os.environ.get("KMA_HUB_URL", "https://apihub.kma.go.kr/api/typ01/url/kma_sfcdd3.php")  # 시험용으로만 바꿈
DGK = os.environ.get("KMA_DGK_URL", "http://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList")
TIMEOUT = 8
STN = os.environ.get("WEATHER_STN", "108")


def keys():
    return {"hub": os.environ.get("KMA_APIHUB_KEY", "").strip(), "dgk": os.environ.get("DATA_GO_KR_KEY", "").strip()}


def provider():
    k = keys()
    return "hub" if k["hub"] else "dgk" if k["dgk"] else None


LABEL = {"hub": "기상청 API허브 · 종관기상관측(ASOS) 일자료", "dgk": "공공데이터포털 · 기상청 지상(종관, ASOS) 일자료 조회서비스"}


def _open(url):
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _f(v):
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return None if x <= -50 else x  # 결측(-99 등)


def parse_hub(text):
    """API허브 응답 → {YYYY-MM-DD: 일평균기온}. 글자 표(주석 머리줄에 열 이름)와 JSON 둘 다 받는다."""
    t = text.strip()
    if t.startswith("{") or t.startswith("["):
        data = json.loads(t)
        items = data
        for k in ("response", "body", "items", "item"):
            if isinstance(items, dict) and k in items:
                items = items[k]
        out = {}
        for it in items if isinstance(items, list) else []:
            d = re.sub(r"\D", "", str(it.get("TM") or it.get("tm") or ""))[:8]
            v = _f(it.get("TA_AVG", it.get("avgTa")))
            if len(d) == 8 and v is not None:
                out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = v
        return out
    cols = None
    out = {}
    for line in t.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            toks = s.lstrip("#").split()
            if len(toks) >= 5 and ":" not in toks and "TA_AVG" in [x.upper() for x in toks] or ("TA" in [x.upper() for x in toks] and ("TM" in [x.upper() for x in toks] or "YYMMDD" in [x.upper() for x in toks])):
                cols = [x.upper() for x in toks]
            continue
        if not cols:
            continue
        parts = re.split(r"[,\s]+", s)
        ta_i = cols.index("TA_AVG") if "TA_AVG" in cols else cols.index("TA")
        tm_i = next((i for i, c in enumerate(cols) if c in ("TM", "YYMMDD")), 0)
        if len(parts) <= ta_i:
            continue
        d = re.sub(r"\D", "", parts[tm_i])[:8]
        v = _f(parts[ta_i])
        if len(d) == 8 and v is not None:
            out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = v
    if not out:
        m = re.search(r"(인증|auth|error|오류)[^\n]{0,80}", t, re.I)
        raise RuntimeError(m.group(0) if m else "기온 열(TA_AVG)을 찾지 못했습니다")
    return out


def parse_dgk(text):
    """공공데이터포털 ASOS 일자료 JSON → {YYYY-MM-DD: 일평균기온}."""
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"<returnAuthMsg>(.*?)</returnAuthMsg>|<errMsg>(.*?)</errMsg>", text)
        raise RuntimeError((m.group(1) or m.group(2)) if m else "JSON이 아닌 응답")
    hdr = data.get("response", {}).get("header", {})
    if hdr.get("resultCode") not in (None, "00"):
        raise RuntimeError(f"{hdr.get('resultCode')} {hdr.get('resultMsg')}")
    items = data.get("response", {}).get("body", {}).get("items", {})
    items = items.get("item", []) if isinstance(items, dict) else items
    out = {}
    for it in items or []:
        v = _f(it.get("avgTa"))
        d = str(it.get("tm") or "")[:10]
        if v is not None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            out[d] = v
    return out


def fetch_days(start, end):
    """start~end(date) 일평균기온. 창구별로 나눠 받는다."""
    k, p = keys(), provider()
    out = {}
    cur = start
    while cur <= end:
        stop = min(end, cur + timedelta(days=30 if p == "hub" else 364))
        if p == "hub":
            q = urllib.parse.urlencode({"tm1": cur.strftime("%Y%m%d"), "tm2": stop.strftime("%Y%m%d"), "stn": STN, "help": "0", "authKey": k["hub"]})
            out.update(parse_hub(_open(f"{HUB}?{q}")))
        else:
            q = urllib.parse.urlencode({"serviceKey": k["dgk"], "pageNo": 1, "numOfRows": 400, "dataType": "JSON", "dataCd": "ASOS", "dateCd": "DAY",
                                        "startDt": cur.strftime("%Y%m%d"), "endDt": stop.strftime("%Y%m%d"), "stnIds": STN}, quote_via=urllib.parse.quote)
            out.update(parse_dgk(_open(f"{DGK}?{q}")))
        cur = stop + timedelta(days=1)
    return out


def _month_days(ym):
    y, m = map(int, ym.split("-"))
    first = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def collect(months, cache):
    """months(YYYY-MM 목록)의 월평균기온과 일별 기온. cache는 {ym: {...}}로 갱신된다. (결과, 오류 문장)"""
    p = provider()
    if not p:
        return {}, "기온 자동 수집 키(KMA_APIHUB_KEY 또는 DATA_GO_KR_KEY)가 없습니다"
    today = date.today()
    need = [ym for ym in months if ym not in cache or (not cache[ym].get("full") and _month_days(ym)[0] <= today)]
    err = ""
    if need:
        start, end = _month_days(min(need))[0], min(_month_days(max(need))[1], today - timedelta(days=1))
        try:
            days = fetch_days(start, end) if start <= end else {}
        except Exception as e:  # 키 오류·승인 전·연결 실패
            days, err = {}, f"기상청 자료를 받지 못했습니다({str(e)[:100]})"
        for ym in need:
            a, b = _month_days(ym)
            vals = {d: v for d, v in days.items() if d.startswith(ym)}
            if not vals:
                continue
            n = (b - a).days + 1
            cache[ym] = {"t": round(sum(vals.values()) / len(vals), 1), "days": vals, "n": len(vals), "full": len(vals) >= n,
                         "src": LABEL[p], "stn": STN}
    return {ym: cache[ym] for ym in months if ym in cache}, err
