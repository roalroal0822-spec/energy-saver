"""국내 유가 자동 수집: 한국석유공사 오피넷 일반 API(무료, 하루 300회).

- 전국 주유소 평균가격(/api/avgAllPrice.do): 제품별(휘발유·경유·실내등유 등) 오늘 평균가격과 전일 대비
- 시도별 평균가격(/api/avgSidoPrice.do): 시도(OPINET_SIDO, 기본 01 서울) 제품별 평균가격
오피넷에는 지난 달 가격을 한꺼번에 주는 일반 API가 없어, 받을 때마다 그날 값을 data/opinet.json에 쌓아 달 평균을 만든다.
국제유가가 아니므로 가스 단가 연동(두바이유×환율)은 그대로 두고 참고값으로만 보여 준다.
키는 환경변수 OPINET_KEY로만 읽고 화면·파일·기록에 남기지 않는다.
"""
import json
import os
import time
import urllib.parse
import urllib.request

from kma import _clean

BASE = os.environ.get("OPINET_URL", "https://www.opinet.co.kr/api")  # 시험용으로만 바꿈
SIDO = os.environ.get("OPINET_SIDO", "01")
TIMEOUT = 10
REFRESH = 6 * 3600  # 하루 호출 한도(300회)를 아끼려고 6시간에 한 번만 묻는다
SRC = "한국석유공사 오피넷"
NAMES = {"B027": "휘발유", "D047": "경유", "C004": "실내등유", "B034": "고급휘발유", "K015": "자동차부탄"}
SHOW = ("B027", "D047", "C004")


def key():
    v = os.environ.get("OPINET_KEY", "")
    return _clean(_clean(v, "certkey"), "code")


def _get(path, **q):
    k = key()
    q.update(out="json", certkey=k, code=k)  # 안내서는 certkey, 예전 예시는 code라 둘 다 보낸다
    with urllib.request.urlopen(f"{BASE}/{path}?{urllib.parse.urlencode(q)}", timeout=TIMEOUT) as r:
        text = r.read().decode("utf-8", "replace")
    try:
        rows = json.loads(text)["RESULT"]["OIL"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError(f"{path} 응답 형식이 다릅니다: {text[:80]}")
    if not rows:
        raise RuntimeError(f"{path} 자료가 비어 있습니다(인증키를 확인하세요)")
    return rows


def _p(v):
    try:
        return round(float(str(v).replace(",", "")), 2)
    except (TypeError, ValueError):
        return None


def collect(store_data):
    """store_data: {"days": {YYYYMMDD: {제품코드: 값}}, "sido": {...}, "at": 시각}. 갱신하고 (자료, 오류)를 돌려준다."""
    d = store_data
    d.setdefault("days", {})
    d.setdefault("sido", {})
    if not key():
        return d, "오피넷 키(OPINET_KEY)가 없습니다"
    if time.time() - d.get("at", 0) < REFRESH and d["days"]:
        return d, d.get("error", "")
    err = ""
    try:
        rows = _get("avgAllPrice.do")
        for r in rows:
            day, pc, pr = str(r.get("TRADE_DT") or ""), r.get("PRODCD"), _p(r.get("PRICE"))
            if len(day) == 8 and pc and pr:
                d["days"].setdefault(day, {})[pc] = {"price": pr, "diff": _p(r.get("DIFF"))}
        try:
            srows = _get("avgSidoPrice.do", sido=SIDO)
            day = max(d["days"]) if d["days"] else time.strftime("%Y%m%d")
            for r in srows:
                pc, pr = r.get("PRODCD"), _p(r.get("PRICE"))
                if pc and pr:
                    d["sido"].setdefault(day, {})[pc] = {"price": pr, "diff": _p(r.get("DIFF")), "name": r.get("SIDONM") or ""}
        except Exception as e:
            err = f"시도별 가격을 받지 못했습니다({str(e)[:80]})"
    except Exception as e:
        err = f"오피넷 자료를 받지 못했습니다({str(e)[:100]})"
    d["at"], d["error"] = time.time(), err
    return d, err


def summary(d):
    """화면용: 최근 날 전국·시도 가격, 달별 평균(쌓인 날로 계산)."""
    days = d.get("days") or {}
    if not days:
        return None
    last = max(days)
    sd = (d.get("sido") or {})
    slast = max(sd) if sd else None
    months = {}
    for day, v in days.items():
        ym = f"{day[:4]}-{day[4:6]}"
        for pc, x in v.items():
            months.setdefault(ym, {}).setdefault(pc, []).append(x["price"])
    return {"date": f"{last[:4]}-{last[4:6]}-{last[6:]}", "days": len(days),
            "items": [{"code": pc, "name": NAMES[pc], "price": days[last][pc]["price"], "diff": days[last][pc]["diff"],
                       "sido": (sd[slast].get(pc) or {}).get("price") if slast else None} for pc in SHOW if pc in days[last]],
            "sido_name": next((x.get("name") for x in (sd[slast].values() if slast else []) if x.get("name")), ""),
            "monthly": [{"ym": ym, **{NAMES[pc]: round(sum(v) / len(v), 1) for pc, v in m.items() if pc in SHOW}, "n": max(len(v) for v in m.values())}
                        for ym, m in sorted(months.items())],
            "src": SRC}
