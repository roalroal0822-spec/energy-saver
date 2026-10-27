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
        self.guide = None  # (Regulation, 문서)
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

    def put(self, table, key, rec):
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


def read_tables(ds, docs):
    pending = []
    for doc in docs:
        for t in doc.tables:
            cols = _map_header(t["header"])
            kind = _kind_of(cols, doc.name + t.get("name", ""))
            if kind:
                pending.append((kind, doc, t))
    # 시설 현황을 먼저 읽어야 다른 표의 시설 이름을 맞출 수 있다
    order = {"facility": 0, "weather": 1, "elec": 2, "gas": 3, "action": 4}
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
            elif kind == "action":
                fac = ds.fac_name(get("fac"))
                d = re.search(r"20\d{2}-\d{2}-\d{2}", str(get("date") or ""))
                if fac and d:
                    ds.actions.append({"id": f"A{len(ds.actions) + 1}", "fac": fac, "title": str(get("title") or "").strip(),
                                       "kind": str(get("kind") or "").strip(), "date": d.group(),
                                       "status": str(get("status") or "완료").strip(), "owner": str(get("owner") or "").strip(),
                                       "src": src, "origin": "file"})
            n += 1
        ds.files.append({"file": doc.relpath, "kind": {"facility": "시설 현황", "weather": "기온", "elec": "전력 내역",
                                                       "gas": "가스 내역", "action": "조치 이력"}[kind], "rows": n})


# ------------------------------------------------------------ 고지서 PDF

BILL = {
    "ym": r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*분|사용기간\s*(20\d{2})[.\-](\d{2})",
    "fac": r"고객명\s*(.+)",
    "contract": r"계약전력\s*([\d,]+)\s*kW",
    "applied": r"요금적용전력\s*([\d,]+)\s*kW",
    "peak": r"최대수요전력\s*([\d,]+)\s*kW",
    "off": r"경부하[^\d]*([\d,]+)\s*kWh",
    "mid": r"중간부하[^\d]*([\d,]+)\s*kWh",
    "on": r"최대부하[^\d]*([\d,]+)\s*kWh",
    "kwh": r"사용전력량[^\d]*([\d,]+)\s*kWh",
    "pf": r"역률\s*([\d.]+)\s*%",
    "basic": r"기본요금\s*([\d,]+)\s*원",
    "energy": r"전력량요금\s*([\d,]+)\s*원",
    "pfc": r"역률요금\s*([\d,]+)\s*원",
    "vat": r"부가가치세\s*([\d,]+)\s*원",
    "total": r"청구금액\s*([\d,]+)\s*원",
}
NEED = ("ym", "fac", "kwh", "total")


def _is_bill(doc):
    t = doc.text
    return doc.ext == ".pdf" and ("kWh" in t or "kwh" in t.lower()) and re.search(r"청구|고지|요금", t)


def parse_bill(doc, ds, llm=None):
    """고지서 한 장 → 전력 기록 1건. (기록, 못 읽은 항목, 모델 사용 여부)"""
    blocks = doc.blocks
    rec, srcs = {}, {}
    for key, rx in BILL.items():
        for b in blocks:
            m = re.search(rx, b.text)
            if not m:
                continue
            if key == "ym":
                g = [x for x in m.groups() if x]
                rec["ym"] = f"{int(g[0])}-{int(g[1]):02d}"
            elif key == "fac":
                rec["fac"] = m.group(1).strip()
            else:
                rec[key] = _num(m.group(1))
            srcs[key] = b.cite()
            break
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


def read_bills(ds, docs, llm=None):
    out = []
    for doc in docs:
        if not _is_bill(doc):
            continue
        rec, srcs, missing, by_model = parse_bill(doc, ds, llm)
        item = {"file": doc.relpath, "fields": {k: v for k, v in rec.items()}, "missing": missing, "by_model": by_model}
        if missing:
            ds.warnings.append(f"{doc.name}: 고지서에서 {', '.join(missing)} 항목을 찾지 못해 넣지 않았습니다")
        else:
            fac = ds.fac_name(rec["fac"].replace("공공기관", "").strip())
            item["fac"] = fac
            data = {k: rec.get(k) for k in ("contract", "applied", "peak", "off", "mid", "on", "kwh", "pf",
                                            "basic", "energy", "pfc", "vat", "total")}
            data["src"] = srcs.get("kwh") or {"file": doc.relpath, "loc": "", "quote": ""}
            data["srcs"] = srcs
            data["from_bill"] = True
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
