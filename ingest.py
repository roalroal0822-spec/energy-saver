"""자료 읽기: 엑셀 내역·고지서 PDF·시설 현황·기온·조치 이력·지침을 하나의 자료집으로 모은다.

표는 열 이름으로 알아본다(열 순서·이름이 조금 달라도 됨). 모든 값에는 원문 위치(파일·행)를 붙여 둔다.
고지서 PDF는 항목 이름을 찾아 읽고, 못 읽은 항목이 있으면(모델 연결 시) 모델에 묻되
모델이 준 값은 고지서 원문에 실제로 있는 숫자일 때만 쓴다.
"""
import re
from datetime import date

from core import regparse
from core.llm import LLMError

# 열 이름 → 내부 이름 (앞의 것이 우선)
COLS = {
    "ym": [r"연월|사용년월|청구년월|사용월|년월"],
    "fac": [r"시설명|시설|고객명|건물"],
    "contract": [r"계약전력"],
    "applied": [r"요금적용전력"],
    "peak": [r"최대수요"],
    "off": [r"경부하"],
    "mid": [r"중간부하"],
    "on": [r"최대부하"],
    "kwh": [r"사용전력량|전력사용량|사용량\(kwh\)|kwh"],
    "pf": [r"역률\(%\)|평균역률"],
    "basic": [r"기본요금"],
    "energy": [r"전력량요금"],
    "pfc": [r"역률요금"],
    "vat": [r"부가가치세|부가세"],
    "total": [r"청구금액|합계금액|요금\(원\)|요금"],
    "m3": [r"사용량\(㎥\)|사용량\(m3\)|㎥|m3"],
    "temp": [r"기온"],
    "use": [r"용도"],
    "area": [r"연면적|면적"],
    "closure": [r"휴관\s*기간|휴관"],
    "closure_reason": [r"휴관\s*사유"],
    "manager": [r"관리자|담당\s*부서|관리부서"],
    "title": [r"조치\s*내용|조치명|내용"],
    "kind": [r"구분"],
    "date": [r"시행일|완료일|일자"],
    "status": [r"상태"],
    "owner": [r"^담당$|담당자"],
    "cost": [r"투자비|사업비"],
    "subsidy": [r"지원금|보조금"],
    "check_item": [r"점검항목|^항목$"],
    "check_state": [r"이행현황|이행여부|^현황$"],
    "check_pct": [r"이행률|적용률|전환율"],
    "check_note": [r"^비고$|확인내용"],
}


def _num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def norm_ym(s):
    s = str(s or "").strip()
    m = re.search(r"(20\d{2})\s*[-./년]\s*(\d{1,2})", s)
    if not m:
        m = re.fullmatch(r"(20\d{2})(\d{2})", s)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    return f"{y}-{mo:02d}" if 1 <= mo <= 12 else None


def ym_add(ym, n):
    y, m = map(int, ym.split("-"))
    k = y * 12 + (m - 1) + n
    return f"{k // 12}-{k % 12 + 1:02d}"


def ym_days(ym):
    y, m = map(int, ym.split("-"))
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - date(y, m, 1)).days


def _map_header(header):
    """열 이름 → 내부 이름. 한 열은 한 이름에만, 이름 하나에는 처음 맞는 열만 쓴다."""
    out, used = {}, set()
    for key, pats in COLS.items():
        for i, h in enumerate(header):
            if i in used:
                continue
            hl = str(h).lower().replace(" ", "")
            if any(re.search(p, hl) for p in pats):
                out[key] = i
                used.add(i)
                break
    return out


def _cite(doc, loc, quote):
    q = str(quote)
    return {"file": doc.relpath, "loc": loc, "quote": q[:160]}


def _kind_of(cols, name):
    n = name.replace(" ", "")
    if "temp" in cols and "ym" in cols:
        return "weather"
    if "ym" in cols and "fac" in cols and ("kwh" in cols or {"off", "mid", "on"} <= cols.keys()):
        return "elec"
    if "ym" in cols and "fac" in cols and "m3" in cols:
        return "gas"
    if "fac" in cols and "check_item" in cols and "check_state" in cols:
        return "checklist"
    if "fac" in cols and ("area" in cols or "contract" in cols) and "ym" not in cols:
        return "facility"
    if "fac" in cols and "title" in cols and "date" in cols:
        return "action"
    if "가스" in n and "ym" in cols and "fac" in cols:
        return "gas"
    return None


class Dataset:
    def __init__(self):
        self.facilities = {}  # 이름 → {...}
        self.weather = {}  # ym → {"t", "src"}
        self.elec = {}  # (시설, ym) → {...}
        self.gas = {}
        self.actions = []
        self.checklist = []  # 사업장 설비 점검표 [{"fac","item","state","pct","note","src"}]
        self.guide = None  # (Regulation, 문서)
        self.budget = []  # [{"fy","fac","item","main","supp","transfer","note","src"}] 예산서
        self.oil = {}  # ym → {"oil","fx","kind","src"} 국제유가·환율
        self.files = []  # 읽은 파일 요약
        self.warnings = []
        self.conflicts = []

    # ------------------------------------------------------------ 시설 이름 맞추기
    def fac_name(self, raw):
        raw = str(raw or "").strip()
        if not raw:
            return None
        if raw in self.facilities:
            return raw
        key = raw.replace(" ", "")
        for n in self.facilities:
            nk = n.replace(" ", "")
            if nk in key or key in nk:
                return n
        return raw

    LIMITS = {"kwh": 50_000_000, "m3": 5_000_000, "total": 10_000_000_000}  # 한 시설 한 달 값으로 믿기 어려운 크기

    def put(self, table, key, rec):
        """사용량·요금 한 달치를 넣는다. 아직 오지 않은 달, 음수, 믿기 어려운 크기는 넣지 않고 경고로 남긴다
        (잘못된 한 줄이 '최근 12개월'과 전체 합계를 바꾸지 않게)."""
        fac, ym = key
        where = (rec.get("src") or {}).get("file") or ""
        today = date.today().strftime("%Y-%m")
        bad = None
        if ym > today:
            bad = f"아직 오지 않은 달({ym})"
        elif ym < "2000-01":
            bad = f"너무 이른 달({ym})"
        else:
            for k, lim in self.LIMITS.items():
                v = rec.get(k)
                if v is not None and v < 0:
                    bad = f"{ {'kwh': '사용량', 'm3': '사용량', 'total': '요금'}[k]}이 음수({v:,.0f})"
                    break
                if v is not None and v > lim:
                    bad = f"{ {'kwh': '사용량', 'm3': '사용량', 'total': '요금'}[k]}이 한 달 값으로 너무 큼({v:,.0f})"
                    break
        if bad:
            self.warnings.append(f"{where}: {fac} {ym} 줄은 넣지 않았습니다(이유: {bad}). 원본을 확인해 주세요")
            return
        old = table.get(key)
        if old:
            diff = [k for k in ("kwh", "m3", "total") if k in rec and k in old and old[k] != rec[k]]
            if diff:
                self.conflicts.append({"key": list(key), "fields": diff, "old": old["src"], "new": rec["src"]})
        table[key] = rec

    @property
    def months(self):
        return sorted({ym for _, ym in self.elec} | {ym for _, ym in self.gas})

    def summary(self):
        return {"facilities": len(self.facilities), "elec": len(self.elec), "gas": len(self.gas),
                "weather": len(self.weather), "actions": len(self.actions),
                "months": [self.months[0], self.months[-1]] if self.months else [],
                "guide": self.guide[1].relpath if self.guide else "", "files": self.files,
                "warnings": self.warnings, "conflicts": self.conflicts}


# ------------------------------------------------------------ 표 읽기

def _rows(doc, t):
    cols = _map_header(t["header"])
    locs = t.get("row_locs") or [f"{t.get('loc_prefix', '')}{i + 2}행" for i in range(len(t["rows"]))]
    for row, loc in zip(t["rows"], locs):
        get = lambda k: row[cols[k]] if k in cols and cols[k] < len(row) else None  # noqa: E731
        yield cols, get, loc, " | ".join(str(c) for c in row if str(c).strip())


ITEM = {"전기료": r"전기", "가스료": r"가스|연료|난방"}


def _item_of(s):
    s = str(s or "")
    for k, rx in ITEM.items():
        if re.search(rx, s):
            return k
    return None


def read_budget_oil(ds, doc, t):
    """예산서(본예산·추경)와 국제유가 표. 다른 표가 아니면 False."""
    hdr = [str(h).replace(" ", "") for h in t["header"]]
    locs = t.get("row_locs") or [f"{t.get('loc_prefix', '')}{i + 2}행" for i in range(len(t["rows"]))]
    find = lambda rx: [i for i, h in enumerate(hdr) if re.search(rx, h)]  # noqa: E731
    main = find(r"본예산|당초예산")
    if main:
        fac, item, fy = (find(r"시설|부서|사업") or [None])[0], (find(r"과목|세목|구분") or [None])[0], (find(r"회계연도|연도") or [None])[0]
        supp, trans, note = find(r"추경|추가경정"), find(r"전용|이용|이체"), (find(r"비고") or [None])[0]
        n = 0
        for row, loc in zip(t["rows"], locs):
            g = lambda i: row[i] if i is not None and i < len(row) else None  # noqa: E731
            it = _item_of(g(item))
            mv = _num(g(main[0]))
            if not it or mv is None:
                continue
            ds.budget.append({"fy": int(_num(g(fy)) or 0) or None, "fac": ds.fac_name(g(fac)) if fac is not None else "전체", "item": it,
                              "main": mv, "supp": sum(_num(g(i)) or 0 for i in supp), "transfer": sum(_num(g(i)) or 0 for i in trans),
                              "supp_cols": [t["header"][i] for i in supp], "note": str(g(note) or "").strip(),
                              "src": _cite(doc, loc, " | ".join(str(c) for c in row if str(c).strip()))})
            n += 1
        ds.files.append({"file": doc.relpath, "kind": "예산서(본예산·추경)", "rows": n})
        return True
    oil = find(r"두바이|유가|원유")
    ymc = find(r"연월|년월")
    if oil and ymc:
        fx, kind = (find(r"환율") or [None])[0], (find(r"구분") or [None])[0]
        n = 0
        for row, loc in zip(t["rows"], locs):
            ym = norm_ym(row[ymc[0]] if ymc[0] < len(row) else None)
            o = _num(row[oil[0]] if oil[0] < len(row) else None)
            if not ym or o is None:
                continue
            ds.oil[ym] = {"oil": o, "fx": _num(row[fx]) if fx is not None and fx < len(row) else None,
                          "kind": str(row[kind]).strip() if kind is not None and kind < len(row) else "",
                          "src": _cite(doc, loc, " | ".join(str(c) for c in row if str(c).strip()))}
            n += 1
        ds.files.append({"file": doc.relpath, "kind": "국제유가·환율", "rows": n})
        return True
    return False


def read_tables(ds, docs):
    pending, later = [], []
    for doc in docs:
        for t in doc.tables:
            hdr = "".join(str(h) for h in t["header"])
            if re.search(r"본예산|당초예산|두바이|유가|원유", hdr):
                later.append((doc, t))
                continue
            cols = _map_header(t["header"])
            kind = _kind_of(cols, doc.name + t.get("name", ""))
            if kind:
                pending.append((kind, doc, t))
    # 시설 현황을 먼저 읽어야 다른 표의 시설 이름을 맞출 수 있다
    order = {"facility": 0, "weather": 1, "elec": 2, "gas": 3, "action": 4, "checklist": 5}
    for kind, doc, t in sorted(pending, key=lambda x: order[x[0]]):
        n = 0
        for cols, get, loc, quote in _rows(doc, t):
            src = _cite(doc, loc, quote)
            if kind == "facility":
                name = str(get("fac") or "").strip()
                if not name:
                    continue
                closures = []
                for part in re.split(r"[,;/]", str(get("closure") or "")):
                    d = re.findall(r"20\d{2}-\d{2}-\d{2}", part)
                    if len(d) == 2:
                        closures.append({"start": d[0], "end": d[1], "reason": str(get("closure_reason") or "").strip()})
                ds.facilities[name] = {"name": name, "use": str(get("use") or ""), "area": _num(get("area")),
                                       "contract": _num(get("contract")), "closures": closures,
                                       "manager": str(get("manager") or "").strip(), "src": src}
            elif kind == "weather":
                ym = norm_ym(get("ym"))
                t_ = _num(get("temp"))
                if ym and t_ is not None:
                    ds.weather[ym] = {"t": t_, "src": src}
            elif kind in ("elec", "gas"):
                ym = norm_ym(get("ym"))
                fac = ds.fac_name(get("fac"))
                if not ym or not fac:
                    continue
                if kind == "elec":
                    rec = {k: _num(get(k)) for k in ("contract", "applied", "peak", "off", "mid", "on", "kwh", "pf",
                                                     "basic", "energy", "pfc", "vat", "total")}
                    if rec["kwh"] is None and None not in (rec["off"], rec["mid"], rec["on"]):
                        rec["kwh"] = rec["off"] + rec["mid"] + rec["on"]
                    if rec["kwh"] is None:
                        continue
                    rec["src"] = src
                    ds.put(ds.elec, (fac, ym), rec)
                else:
                    m3 = _num(get("m3"))
                    if m3 is None:
                        continue
                    ds.put(ds.gas, (fac, ym), {"m3": m3, "total": _num(get("total")), "src": src})
            elif kind == "checklist":
                fac = ds.fac_name(get("fac"))
                item = str(get("check_item") or "").strip()
                st = str(get("check_state") or "").strip()
                if fac and item:
                    pct = _num(get("check_pct"))
                    state = "일부" if re.search(r"일부|부분", st) else "안 함" if re.search(r"안\s*함|미|없음|^x$|아니", st, re.I) else \
                        "하고 있음" if re.search(r"하고\s*있|완료|적용|^o$|^예$|^있음$", st, re.I) else (st or "모름")
                    if pct is None:
                        pct = {"하고 있음": 100, "일부": 50, "안 함": 0}.get(state)
                    ds.checklist.append({"fac": fac, "item": item, "state": state, "pct": pct, "note": str(get("check_note") or "").strip(), "src": src})
            elif kind == "action":
                fac = ds.fac_name(get("fac"))
                d = re.search(r"20\d{2}-\d{2}-\d{2}", str(get("date") or ""))
                if fac and d:
                    ds.actions.append({"id": f"A{len(ds.actions) + 1}", "fac": fac, "title": str(get("title") or "").strip(),
                                       "kind": str(get("kind") or "").strip(), "date": d.group(),
                                       "status": str(get("status") or "완료").strip(), "owner": str(get("owner") or "").strip(),
                                       "cost": _num(get("cost")) or 0, "subsidy": _num(get("subsidy")) or 0,
                                       "src": src, "origin": "file"})
            n += 1
        ds.files.append({"file": doc.relpath, "kind": {"facility": "시설 현황", "weather": "기온", "elec": "전력 내역",
                                                       "gas": "가스 내역", "action": "조치 이력", "checklist": "설비 점검표"}[kind], "rows": n})
    for doc, t in later:
        read_budget_oil(ds, doc, t)


# ------------------------------------------------------------ 고지서 PDF

BILL = {  # 항목마다 (모의 고지서 형식, 한국전력 전기요금 청구서 형식) 순서로 찾는다
    "ym": [r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*분|사용기간\s*(20\d{2})[.\-](\d{2})",
           r"고객님의\s*(20\d{2})\s*년\s*(\d{1,2})(?!\d)"],
    "fac": [r"고객명\s*(.+)", r"상\s*호\s+(\S+)"],
    "tariff": [r"계약종별\s*(\S+)"],
    "contract": [r"계약전력\s*([\d,]+)\s*kW"],
    "applied": [r"요금적용전력\s*([\d,]+)\s*kW"],
    "peak": [r"최대수요전력\s*([\d,]+)\s*kW"],
    "off": [r"경부하[^\d]*([\d,]+)\s*kWh"],
    "mid": [r"중간부하[^\d]*([\d,]+)\s*kWh"],
    "on": [r"최대부하[^\d]*([\d,]+)\s*kWh"],
    "kwh": [r"사용전력량[^\d]*([\d,]+)\s*kWh", r"최대수요전력[\s\S]*?(\d[\d,]*)\s*kWh", r"(?i)(?<![\d.,])(\d[\d,]{2,})\s*kwh"],
    "pf": [r"역률\s*([\d.]+)\s*%", r"(?m)^\s*역률\s+(\d{2,3}(?:\.\d+)?)\s*$"],
    "basic": [r"기본요금\s*([\d,]+)\s*원", r"기본\s*요금\s*([\d,]+)"],
    "energy": [r"전력량요금\s*([\d,]+)\s*원", r"전력량요금\s*([\d,]+)"],
    "climate": [r"기후환경요금\s*(-?[\d,]+)"],
    "fuel": [r"연료비조정액\s*(-?[\d,]+)"],
    "pfc": [r"역률요금\s*([\d,]+)\s*원", r"역률\s*요금\s*(-?[\d,]+)"],
    "vat": [r"부가가치세\s*([\d,]+)\s*원", r"부가가치세\s*([\d,]+)"],
    "fund": [r"전력\s*기금\s*([\d,]+)"],
    "total": [r"청구금액\s*([\d,]+)\s*원", r"수신료\s*[\d,]+\s+([\d,]+)", r"당월요금계\s*([\d,]+)"],
}
NEED = ("ym", "fac", "kwh", "total")


def _is_bill(doc):
    t = doc.text
    return doc.ext == ".pdf" and ("kWh" in t or "kwh" in t.lower()) and re.search(r"청구|고지|요금", t)


def _kepco_fac(text):
    """한전 청구서는 '상호' 글자가 본문에 안 남아, 주소 다음 줄의 상호(시설 이름)를 꺼낸다."""
    lines = [x.strip() for x in text.splitlines()]
    for i, ln in enumerate(lines):
        if re.match(r"\S+(특별시|광역시|특별자치시|특별자치도|도)\s", ln) and i + 1 < len(lines):
            j = i
            if "(" in ln and ")" not in ln:  # 주소가 다음 줄로 이어짐
                while j + 1 < len(lines) and ")" not in lines[j]:
                    j += 1
            for k in range(j + 1, min(j + 4, len(lines))):
                if lines[k] and len(lines[k]) <= 30 and not re.search(r"\d{4}\s*년|까지|~", lines[k]):
                    return lines[k]
            break
    return None


def parse_bill(doc, ds, llm=None):
    """고지서 한 장 → 전력 기록 1건. (기록, 못 읽은 항목, 모델 사용 여부)"""
    blocks = doc.blocks
    flat = re.sub(r"[\u3000\xa0]", " ", doc.text)
    rec, srcs = {}, {}

    def put(key, m):
        if key == "ym":
            g = [x for x in m.groups() if x]
            rec["ym"] = f"{int(g[0])}-{int(g[1]):02d}"
        elif key in ("fac", "tariff"):
            rec[key] = m.group(1).strip()
        else:
            rec[key] = _num(m.group(1))

    for key, rxs in BILL.items():
        for rx in rxs:
            hit = next(((b, m) for b in blocks for m in [re.search(rx, re.sub(r"[\u3000\xa0]", " ", b.text))] if m), None)
            if hit:
                put(key, hit[1])
                srcs[key] = hit[0].cite()
                break
            m = re.search(rx, flat)  # 줄이 나뉜 항목(예: '2026년 10\n월')은 전체 글에서
            if m:
                put(key, m)
                srcs[key] = {"file": doc.relpath, "loc": "1쪽", "quote": re.sub(r"\s+", " ", m.group(0))[:160]}
                break
    if "fac" not in rec and "고객님의" in flat:  # 한국전력 청구서
        f = _kepco_fac(doc.text)
        if f:
            rec["fac"] = f
            srcs["fac"] = {"file": doc.relpath, "loc": "1쪽 상호", "quote": f}
    if "고객님의" in flat and "ym" in rec:
        per = re.search(r"(\d{2})\.(\d{2})\s*~\s*(\d{2})\.(\d{2})", flat)
        if per:
            rec["period"] = f"{per.group(1)}.{per.group(2)}~{per.group(3)}.{per.group(4)}"
    missing = [k for k in NEED if k not in rec]
    by_model = []
    if missing and llm is not None and llm.enabled:
        try:
            got = llm.json("전기요금 고지서 글에서 항목 값을 찾아 JSON으로 돌려주세요. 글에 없는 값은 비워 두세요.",
                           "찾을 항목: " + ", ".join(missing) + " (ym은 2026-09 형식, fac은 고객명·시설명, 숫자는 숫자만)\n\n" + doc.text[:4000])
            for k in missing:
                v = str(got.get(k) or "").strip()
                if not v:
                    continue
                # 모델이 준 값은 고지서 원문에 실제로 있어야 쓴다
                if k == "ym":
                    ymv = norm_ym(v)
                    if ymv and re.search(rf"{ymv[:4]}\D{{0,3}}0?{int(ymv[5:])}\D", doc.text):
                        rec["ym"], by_model = ymv, by_model + [k]
                elif k == "fac":
                    if v in doc.text:
                        rec["fac"], by_model = v, by_model + [k]
                else:
                    num = _num(v)
                    if num is not None and re.search(rf"(?<![\d,]){int(num):,}(?![\d])|(?<!\d){int(num)}(?!\d)", doc.text):
                        rec[k], by_model = num, by_model + [k]
        except LLMError as e:
            ds.warnings.append(f"{doc.name}: 모델로 항목을 읽지 못했습니다({e})")
    missing = [k for k in NEED if k not in rec]
    return rec, srcs, missing, by_model


FAC_DIR = "시설별 자료"


def folder_fac(relpath):
    m = re.search(rf"(?:^|/){FAC_DIR}/([^/]+)/", relpath.replace("\\", "/"))
    return m.group(1) if m else None


def read_bills(ds, docs, llm=None):
    out = []
    for doc in docs:
        if not _is_bill(doc):
            continue
        rec, srcs, missing, by_model = parse_bill(doc, ds, llm)
        folder = folder_fac(doc.relpath)
        if folder:  # '시설별 자료'에서 시설을 골라 넣은 고지서는 그 시설로(고지서의 상호와 달라도)
            if rec.get("fac") and rec["fac"] != folder:
                srcs["fac_bill"] = srcs.get("fac")
            rec["fac"] = folder
            srcs["fac"] = {"file": doc.relpath, "loc": "시설별 자료", "quote": f"넣을 때 고른 시설: {folder}"}
            missing = [k for k in missing if k != "fac"]
        ocr = any("글자 인식" in (v or {}).get("loc", "") for v in srcs.values())
        if ocr and not missing:
            ds.warnings.append(f"{doc.name}: 스캔본을 글자 인식(OCR)으로 읽었습니다. 사용량·금액을 원본과 대조해 주세요")
        item = {"file": doc.relpath, "fields": {k: v for k, v in rec.items()}, "missing": missing, "by_model": by_model, "ocr": ocr}
        if missing:
            ds.warnings.append(f"{doc.name}: 고지서에서 {', '.join(missing)} 항목을 찾지 못해 넣지 않았습니다")
        else:
            fac = folder or ds.fac_name(rec["fac"].replace("공공기관", "").strip())
            item["fac"] = fac
            data = {k: rec.get(k) for k in ("contract", "applied", "peak", "off", "mid", "on", "kwh", "pf",
                                            "basic", "energy", "pfc", "vat", "total", "climate", "fuel", "fund", "tariff", "period")}
            data["src"] = srcs.get("kwh") or {"file": doc.relpath, "loc": "", "quote": ""}
            data["srcs"] = srcs
            data["from_bill"] = True
            data["ocr"] = ocr
            ds.put(ds.elec, (fac, rec["ym"]), data)
        out.append(item)
        ds.files.append({"file": doc.relpath, "kind": "고지서(PDF)", "rows": 0 if missing else 1})
    return out


# ------------------------------------------------------------ 지침

def read_guide(ds, docs):
    best = None
    for doc in docs:
        if doc.ext not in (".hwpx", ".hwp", ".docx", ".txt", ".pdf", ".md"):
            continue
        if not re.search(r"에너지|전력|냉방|난방", doc.text) or not re.search(r"제\s*\d+\s*조", doc.text):
            continue
        reg = regparse.parse([(b.loc, b.text) for b in doc.blocks], source=doc.relpath)
        if reg.articles and (best is None or len(reg.articles) > len(best[0].articles)):
            best = (reg, doc)
    ds.guide = best
    if best:
        ds.files.append({"file": best[1].relpath, "kind": "점검 기준(지침)", "rows": len(best[0].articles)})


def build(docs, llm=None):
    ds = Dataset()
    read_tables(ds, docs)
    bills = read_bills(ds, docs, llm)
    read_guide(ds, docs)
    for fac, _ in list(ds.elec) + list(ds.gas):
        if fac not in ds.facilities:
            ds.facilities[fac] = {"name": fac, "use": "", "area": None, "contract": None, "closures": [], "manager": "", "src": None}
            ds.warnings.append(f"시설 현황에 없는 시설 '{fac}'의 내역이 있습니다(연면적·휴관 정보 없이 분석)")
    if ds.elec and not ds.weather:
        ds.warnings.append("기온 자료가 없어 기온 보정 없이(전년 같은 달과 단순 비교) 분석합니다")
    return ds, bills
