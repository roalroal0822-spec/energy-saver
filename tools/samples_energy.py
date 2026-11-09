"""에너지 절감 비서 모의데이터: 가상의 공공기관 시설 5곳, 24개월(2024.10.~2026.09.) 전기·가스 사용 내역.

sample_data/
  시설 현황.xlsx                         시설별 연면적·계약전력·휴관 기간
  월평균기온.xlsx                         기온 보정용(모의값)
  전력 사용 내역(2024.10~2026.08).xlsx     한전 고지 항목을 옮긴 표(모의)
  도시가스 사용 내역(2024.10~2026.08).xlsx
  고지서/전기요금_<시설>_2026-09.pdf       최신 달은 고지서 PDF로 넣는 장면 시연용
  절감 조치 이력.xlsx                     이미 시행한 조치(효과 측정 시연용)
  사업장 설비 점검표.xlsx                 시설별 절감 설비·운영 이행 현황(놓친 것 추천용)
  지침/공공기관 에너지 절약 실천 지침(모의).hwpx  점검 기준값(10%, 50%, 90% 등)을 이 지침에서 읽음
  예산/2026년 공공요금 예산(본예산·추경).xlsx   시설별 전기료·가스료 본예산과 제1회 추경(예산 관리 시연용)
  예산/국제유가·환율(실적·전망).xlsx            두바이유·원달러 환율 월별 실적과 전망(가스 단가 연동 시연용)

일부러 넣은 이상 상황(시연에서 찾아내야 하는 것):
  - 종합체육센터: 2026년 7·8월 전기가 기온을 감안해도 많음, 2026년 1월 가스 급증
  - 문화예술회관: 2025.12. 조명 LED 교체(효과 측정), 2026.2. 전관 휴관인데 사용량이 거의 그대로
  - 공영주차장: 요금적용전력 300kW, 실제 최대수요전력은 100kW 안팎
  - 복지관: 2026년 3월부터 역률 90% 미만(역률요금 부과)
  - 본부 청사: 2026년 6월 고지 전력량요금이 단가로 다시 계산한 값과 다름
  - 예산: 종합체육센터 전기료는 더운 여름·초과 사용으로 연말 전에 부족, 문화예술회관 전기료는 LED·휴관으로 불용 예상
모든 기관명·시설·수치는 지어낸 것이다.
"""
import io
import random
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.export import to_hwpx  # noqa: E402

OUT = ROOT / "sample_data"
MONTHS = [(2024 + (9 + i) // 12, (9 + i) % 12 + 1) for i in range(24)]  # 2024-10 … 2026-09

# 모의 월평균기온(℃). 2026년 여름을 조금 더 덥게 해서 '기온 보정'이 필요한 상황을 만든다
TEMP = {
    2024: {10: 15.6, 11: 8.1, 12: 0.6},
    2025: {1: -1.8, 2: 0.4, 3: 6.9, 4: 13.1, 5: 18.0, 6: 22.9, 7: 26.1, 8: 26.4, 9: 22.3, 10: 15.0, 11: 7.5, 12: -0.2},
    2026: {1: -2.6, 2: 1.2, 3: 6.2, 4: 12.6, 5: 18.6, 6: 23.4, 7: 26.9, 8: 27.6, 9: 22.8},
}

# 모의 요금 단가(원). 실제 계약종별 단가와 다르며 설정 파일에서 바꿔 쓴다
BASIC = 8320
RATE = {"summer": (105.0, 160.0, 240.0), "spring": (100.0, 125.0, 155.0), "winter": (110.0, 155.0, 205.0)}
GAS_RATE = 1050  # 원/㎥ (모의)


def season(m):
    return "summer" if m in (6, 7, 8) else "winter" if m in (11, 12, 1, 2) else "spring"


def days(y, m):
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - date(y, m, 1)).days


def dd(y, m):
    t = TEMP[y][m]
    return days(y, m) * max(0.0, t - 18), days(y, m) * max(0.0, 18 - t)  # 냉방도일, 난방도일


FAC = [
    # 이름, 용도, 연면적, 계약전력, 기저(kWh), 냉방계수, 난방계수, 가스기저, 가스난방계수
    {"name": "본부 청사", "use": "업무시설", "area": 6200, "contract": 400, "base": 52000, "c": 95, "h": 40, "gbase": 900, "gh": 6},
    {"name": "종합체육센터", "use": "체육시설(실내수영장)", "area": 9800, "contract": 700, "base": 118000, "c": 160, "h": 60, "gbase": 18000, "gh": 55},
    {"name": "문화예술회관", "use": "문화시설(공연장)", "area": 7400, "contract": 500, "base": 61000, "c": 120, "h": 35, "gbase": 2400, "gh": 14},
    {"name": "공영주차장", "use": "주차장(지하 3층)", "area": 12000, "contract": 300, "base": 38000, "c": 10, "h": 5, "gbase": 0, "gh": 0},
    {"name": "복지관", "use": "노유자시설", "area": 4100, "contract": 250, "base": 24000, "c": 70, "h": 30, "gbase": 1500, "gh": 9},
]
CLOSE = {"문화예술회관": ("2026-02-02", "2026-02-27", "무대 리모델링 공사로 전관 휴관")}


def gen():
    rnd = random.Random(20261027)
    elec, gas = [], []
    for f in FAC:
        for (y, m) in MONTHS:
            cdd, hdd = dd(y, m)
            kwh = f["base"] + f["c"] * cdd + f["h"] * hdd
            ym = f"{y}-{m:02d}"
            n = f["name"]
            if n == "문화예술회관" and ym >= "2026-01":
                kwh *= 0.92  # LED 교체 효과
            if n == "문화예술회관" and ym == "2026-02":
                kwh *= 0.84  # 전관 휴관인데도 설비를 거의 그대로 돌림(정상이면 35% 안팎)
            if n == "종합체육센터" and ym in ("2026-07", "2026-08"):
                kwh *= {"2026-07": 1.17, "2026-08": 1.19}[ym]
            kwh = round(kwh * (1 + rnd.uniform(-0.018, 0.018)))
            peak = round(kwh / (days(y, m) * 24 * 0.42) * rnd.uniform(0.97, 1.03))
            pf = round(rnd.uniform(93.0, 96.0), 1)
            if n == "복지관" and ym >= "2026-03":
                pf = round(rnd.uniform(85.5, 87.8), 1)
            elec.append({"ym": ym, "fac": f, "kwh": kwh, "peak": peak, "pf": pf})
            if f["gbase"]:
                g = f["gbase"] + f["gh"] * hdd
                if n == "종합체육센터" and ym == "2026-01":
                    g *= 1.22
                if n == "문화예술회관" and ym == "2026-02":
                    g *= 0.9
                gas.append({"ym": ym, "fac": f, "m3": round(g * (1 + rnd.uniform(-0.02, 0.02)))})
    # 요금적용전력: 주차장은 계약전력 그대로, 나머지는 직전 12개월 최대수요전력(모의 단순화)
    peaks = {}
    for r in elec:
        n = r["fac"]["name"]
        hist = peaks.setdefault(n, [])
        hist.append(r["peak"])
        r["applied"] = r["fac"]["contract"] if n == "공영주차장" else max(hist[-12:])
    for r in elec:
        y, m = map(int, r["ym"].split("-"))
        k = r["kwh"]
        bands = (round(k * 0.35), round(k * 0.40))
        bands = bands + (k - bands[0] - bands[1],)
        r["bands"] = bands
        rate = RATE[season(m)]
        energy = sum(round(b * p) for b, p in zip(bands, rate))
        if r["fac"]["name"] == "본부 청사" and r["ym"] == "2026-06":
            energy = round(energy * 1.08)  # 고지 금액이 단가 계산과 다름(시연용)
        basic = r["applied"] * BASIC
        pfc = round(basic * 0.002 * (90 - r["pf"])) if r["pf"] < 90 else 0
        r.update(basic=basic, energy=energy, pfc=pfc, vat=round((basic + energy + pfc) * 0.1))
        r["total"] = basic + energy + pfc + r["vat"]
    return elec, gas


# 절감 조치 이력: 투자비·지원금을 적으면 '투자 효과' 탭에서 사후 평가(실측 회수 기간)를 한다.
# 본부 청사 LED는 사용량 자료에 절감이 거의 나타나지 않게 둔 '효과 미흡' 시연용
ACT_HEAD = ["시설명", "조치 내용", "구분", "시행일", "상태", "담당", "투자비(원)", "지원금(원)"]
ACT_ROWS = [["문화예술회관", "공연장·로비 조명 LED 교체", "조명", "2025-12-15", "완료", "문화사업팀", 48000000, 9600000],
            ["본부 청사", "사무실 조명 LED 교체(1차)", "조명", "2025-11-20", "완료", "시설운영팀", 62000000, 0]]


# 사업장 설비 점검표: 이미 하고 있는 것은 추천에서 빼고, 놓친 것만 '투자 효과' 탭에서 추천한다
CHK_HEAD = ["시설명", "점검 항목", "이행 현황", "이행률(%)", "비고"]
_CHK = {  # 항목: (본부 청사, 종합체육센터, 문화예술회관, 공영주차장, 복지관)
    "조명 LED 전환": (("일부", 40, "2025.11 사무실 1차 교체"), ("안 함", 0, ""), ("일부", 70, "공연장·로비 교체(2025.12)"), ("안 함", 0, ""), ("하고 있음", 100, "2024 전면 교체")),
    "펌프·공조기 인버터": (("일부", 30, "냉각수 펌프만"), ("안 함", 0, "수영장 순환펌프 정속"), ("안 함", 0, ""), ("안 함", 0, "환기팬 정속"), ("안 함", 0, "")),
    "고효율(콘덴싱) 보일러": (("안 함", 0, ""), ("안 함", 0, "2012년 설치"), ("안 함", 0, ""), (None,), ("하고 있음", 100, "2023 교체")),
    "전자결재·페이퍼리스": (("일부", 50, "결재만 전자, 회의자료 인쇄"), ("안 함", 0, ""), ("안 함", 0, ""), ("안 함", 0, ""), ("안 함", 0, "")),
    "대기전력 자동 차단": (("하고 있음", 100, ""), ("일부", 50, ""), ("안 함", 0, ""), ("하고 있음", 100, ""), ("안 함", 0, "")),
    "냉난방 설정온도(28℃/18℃) 관리": (("하고 있음", 100, ""), ("일부", 50, "수영장 제외"), ("안 함", 0, "공연 시 수동"), ("하고 있음", 100, ""), ("하고 있음", 100, "")),
    "야간·휴관 설비 정지 일정": (("하고 있음", 100, ""), ("하고 있음", 100, ""), ("안 함", 0, "휴관 중 공조 가동 확인"), ("일부", 50, ""), ("하고 있음", 100, "")),
    "계약전력 적정성 점검": (("하고 있음", 100, "2026.03"), ("안 함", 0, ""), ("하고 있음", 100, ""), ("안 함", 0, ""), ("하고 있음", 100, "")),
    "창호 단열·차양": (("하고 있음", 100, ""), ("안 함", 0, ""), ("일부", 50, ""), (None,), ("안 함", 0, "")),
}
_FACS = ["본부 청사", "종합체육센터", "문화예술회관", "공영주차장", "복지관"]
CHK_ROWS = [[f, item, v[i][0], v[i][1], v[i][2]] for f_i, f in enumerate(_FACS) for item, v in _CHK.items() for i in [f_i] if v[i][0]]


def xlsx(path, sheet, header, rows, widths=None, note=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    if note:
        ws.append([note])
        ws.append([])
    ws.append(header)
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths or [], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    p = OUT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    wb.save(p)


def bill_pdf(r):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    if "K" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("K", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", subfontIndex=0))
    f = r["fac"]
    y, m = map(int, r["ym"].split("-"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setFont("K", 16)
    c.drawString(60, 790, f"전기요금 청구서 (모의) - {y}년 {m}월분")
    c.setFont("K", 10.5)
    lines = [
        f"고객명 공공기관 {f['name']}",
        f"고객번호 01-{1000 + FAC.index(f) * 37:04d}-{5000 + FAC.index(f):04d}",
        f"계약종별 일반용(을) 고압A 선택II (모의)",
        f"사용기간 {y}.{m:02d}.01 ~ {y}.{m:02d}.{days(y, m):02d}",
        f"계약전력 {f['contract']} kW",
        f"요금적용전력 {r['applied']} kW",
        f"최대수요전력 {r['peak']} kW",
        f"경부하 사용량 {r['bands'][0]:,} kWh",
        f"중간부하 사용량 {r['bands'][1]:,} kWh",
        f"최대부하 사용량 {r['bands'][2]:,} kWh",
        f"사용전력량 합계 {r['kwh']:,} kWh",
        f"평균역률 {r['pf']} %",
        f"기본요금 {r['basic']:,} 원",
        f"전력량요금 {r['energy']:,} 원",
        f"역률요금 {r['pfc']:,} 원",
        f"부가가치세 {r['vat']:,} 원",
        f"청구금액 {r['total']:,} 원",
        "",
        "※ 시연용으로 지어낸 고지서입니다. 전력산업기반기금 등 일부 항목은 생략했습니다.",
    ]
    yy = 750
    for ln in lines:
        c.drawString(60, yy, ln)
        yy -= 20
    c.save()
    return buf.getvalue()


GUIDE = """공공기관 에너지 절약 실천 지침(모의)
제1조(목적) 이 지침은 공공기관이 운영하는 시설의 에너지 사용을 줄이고 요금을 바르게 관리하기 위하여 필요한 사항을 정함을 목적으로 한다.
제2조(적용 범위) 이 지침은 기관이 소유하거나 위탁받아 운영하는 모든 시설에 적용한다.
제3조(냉방 기준) ① 냉방 설비를 가동할 때에는 실내 평균 온도를 26℃ 이상으로 유지한다.
② 실내수영장 등 다중이용 체육시설은 이용자 안전을 해치지 않는 범위에서 운영시간 외에는 냉방을 정지한다.
제4조(난방 기준) 난방 설비를 가동할 때에는 실내 평균 온도를 20℃ 이하로 유지한다.
제5조(조명 관리) ① 점심시간과 퇴근 후에는 업무에 필요한 곳을 제외하고 조명을 끈다.
② 조명 설비를 교체할 때에는 고효율 조명(LED)으로 교체한다.
제6조(휴관·공사 기간 설비 관리) ① 휴관 또는 공사로 시설을 운영하지 않는 기간에는 냉난방·환기·조명 설비를 정지하고 보안·방재 등 필수 설비만 가동한다.
② 시설 관리자는 휴관 전에 설비 정지 계획을 세워 에너지 관리 담당자에게 알린다.
제7조(계약전력 관리) ① 에너지 관리 담당자는 매년 시설별 최대수요전력과 계약전력(요금적용전력)을 비교하여 적정성을 검토한다.
② 최대수요전력이 요금적용전력의 50% 미만인 상태가 12개월 이상 계속되면 계약전력 조정을 한국전력공사와 협의한다.
제8조(역률 관리) 평균 역률이 90% 미만인 달이 있으면 진상용 콘덴서 등 역률 개선 설비를 점검하고 보수한다.
제9조(요금 확인) 에너지 관리 담당자는 매월 전기·가스 요금 고지서를 확인하여 사용량과 요금이 맞는지 검증하고, 차이가 1% 이상이면 공급사에 확인을 요청한다.
제10조(이상 사용 점검) ① 기온 영향을 감안한 사용량이 기준보다 10% 이상 많은 달이 있으면 원인을 점검하고 결과를 기록한다.
② 같은 시설에서 2개월 연속으로 발생하면 시설 관리자와 합동 점검을 한다.
제11조(연소 설비 효율 관리) 보일러 등 연소 설비는 기온 영향을 감안한 가스 사용량이 기준보다 15% 이상 많으면 연소 효율을 점검한다.
제12조(절감 조치의 효과 확인) 절감 조치를 시행한 후에는 3개월 이상 사용량을 측정하여 효과를 확인하고 보고한다.
제13조(보고) 에너지 관리 담당자는 매월 에너지 사용 현황과 조치 결과를 기관장에게 보고한다.
제14조(감축 목표) 기관은 기온 영향을 감안한 시설 전체의 전기 사용량을 기준 기간보다 5% 이상 줄이는 것을 목표로 한다.
부칙
이 지침은 2026년 1월 1일부터 시행한다.
"""


# 2026년 공공요금 예산(원). (시설, 과목, 본예산, 제1회 추경, 비고). 모의값
BUDGET = [
    ("본부 청사", "전기료", 158_000_000, 0, ""),
    ("본부 청사", "가스료", 28_000_000, 0, ""),
    ("종합체육센터", "전기료", 330_000_000, 0, ""),
    ("종합체육센터", "가스료", 360_000_000, 35_000_000, "1월 가스 급증으로 제1회 추경 증액"),
    ("문화예술회관", "전기료", 186_000_000, 0, ""),
    ("문화예술회관", "가스료", 70_000_000, -2_000_000, "2월 휴관 반영 감액"),
    ("공영주차장", "전기료", 110_000_000, 0, ""),
    ("복지관", "전기료", 84_000_000, 0, ""),
    ("복지관", "가스료", 45_000_000, 0, ""),
]


def oil_rows():
    """두바이유(달러/배럴)·원달러 환율 월별 값. 2026.09까지 실적, 이후 2027.12까지 전망(모의)."""
    import math
    rows = []
    months = [(2024 + (9 + i) // 12, (9 + i) % 12 + 1) for i in range(39)]  # 2024-10 … 2027-12
    last = None
    for i, (y, m) in enumerate(months):
        ym = f"{y}-{m:02d}"
        if ym <= "2026-09":
            oil = 74.5 + 2.6 * math.sin(i / 2.3) + 0.5 * math.sin(i * 1.7)
            fx = 1385 + 14 * math.sin(i / 4.0)
            kind = "실적"
            last = (oil, fx)
        else:
            k = i - 23  # 전망: 겨울 수요와 감산 영향으로 완만히 오른다고 가정
            oil = last[0] + (80.5 - last[0]) * min(k, 9) / 9
            fx = last[1] + (1400 - last[1]) * min(k, 6) / 6
            kind = "전망"
        rows.append([ym, round(oil, 1), round(fx), kind])
    return rows


def main():
    import shutil
    if OUT.exists():
        shutil.rmtree(OUT)
    elec, gas = gen()
    note = "※ 시연용 모의자료입니다. 기관·시설·수치는 모두 지어낸 것입니다."
    xlsx("시설 현황.xlsx", "시설", ["시설명", "용도", "연면적(㎡)", "계약전력(kW)", "휴관 기간", "휴관 사유", "시설 관리자"],
         [[f["name"], f["use"], f["area"], f["contract"], "~".join(CLOSE[f["name"]][:2]) if f["name"] in CLOSE else "",
           CLOSE[f["name"]][2] if f["name"] in CLOSE else "", ["시설운영팀", "체육사업팀", "문화사업팀", "교통시설팀", "복지사업팀"][i]]
          for i, f in enumerate(FAC)], [16, 22, 12, 13, 26, 26, 14], note)
    xlsx("월평균기온.xlsx", "기온", ["연월", "월평균기온(℃)"],
         [[f"{y}-{m:02d}", TEMP[y][m]] for y, m in MONTHS], [10, 16], "※ 모의값입니다. 실제 사용 시 기상청 자료(관할 관측소)를 넣으세요.")
    head = ["연월", "시설명", "계약전력(kW)", "요금적용전력(kW)", "최대수요전력(kW)", "경부하(kWh)", "중간부하(kWh)", "최대부하(kWh)",
            "사용전력량(kWh)", "평균역률(%)", "기본요금(원)", "전력량요금(원)", "역률요금(원)", "부가가치세(원)", "청구금액(원)"]
    rows = [[r["ym"], r["fac"]["name"], r["fac"]["contract"], r["applied"], r["peak"], *r["bands"], r["kwh"], r["pf"],
             r["basic"], r["energy"], r["pfc"], r["vat"], r["total"]] for r in elec if r["ym"] <= "2026-08"]
    xlsx("전력 사용 내역(2024.10~2026.08).xlsx", "전력", head, rows, [9, 14] + [12] * 13, note)
    xlsx("도시가스 사용 내역(2024.10~2026.08).xlsx", "가스", ["연월", "시설명", "사용량(㎥)", "요금(원)"],
         [[g["ym"], g["fac"]["name"], g["m3"], g["m3"] * GAS_RATE] for g in gas if g["ym"] <= "2026-08"], [9, 14, 12, 14], note)
    for r in elec:
        if r["ym"] == "2026-09":
            p = OUT / "고지서" / f"전기요금_{r['fac']['name']}_2026-09.pdf"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(bill_pdf(r))
    xlsx("절감 조치 이력.xlsx", "조치", ACT_HEAD, ACT_ROWS, [14, 30, 8, 12, 8, 12, 14, 14], note)
    xlsx("사업장 설비 점검표.xlsx", "점검표", CHK_HEAD, CHK_ROWS, [14, 30, 12, 10, 30], note)
    xlsx("예산/2026년 공공요금 예산(본예산·추경).xlsx", "예산", ["회계연도", "시설명", "세출예산 과목", "본예산(원)", "제1회 추경(원)", "비고"],
         [[2026, f, f"공공요금 및 제세 > {k}", a, b, n] for f, k, a, b, n in BUDGET], [10, 14, 26, 16, 16, 30], note)
    xlsx("예산/국제유가·환율(실적·전망).xlsx", "유가", ["연월", "두바이유(달러/배럴)", "원달러 환율(원)", "구분"], oil_rows(), [10, 18, 16, 8],
         "※ 모의값입니다. 실제는 한국석유공사 페트로넷의 두바이유 가격과 한국은행 환율, 기관이 쓰는 전망치를 넣으세요.")
    p = OUT / "지침" / "공공기관 에너지 절약 실천 지침(모의).hwpx"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(to_hwpx([("p", l) for l in GUIDE.strip().splitlines() if l.strip()]))
    print("모의자료 생성:", OUT)


if __name__ == "__main__":
    main()
