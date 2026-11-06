"use strict";
let S = null;
let GO = null;
const won = v => (v == null ? "-" : Math.round(v).toLocaleString("ko-KR") + "원");
const num = (v, u = "") => (v == null ? "-" : Math.round(v).toLocaleString("ko-KR") + u);
const pct = v => (v == null ? "-" : (v > 0 ? "+" : "") + v.toFixed(1) + "%");
const ymLabel = ym => `${ym.slice(2, 4)}.${ym.slice(5)}`;
const LV = { bad: "조치 필요", warn: "검토", gray: "설정 확인" };

async function load(data) {
  S = data || await api("GET", "/api/state");
  renderHome(); renderFind(); renderFacSel(); renderPeriod(); renderAct(); renderReport(); renderData();
  BUD = null;
  if (!$("#tab-budget").hidden) renderBudget();
  if (!$("#tab-fac").hidden) { renderWeather(); renderKepco(); }
}

function basisChip(b) {
  if (!b) return null;
  return el("span", { class: "chip", title: b.text, onclick: () => modal(`${b.reg} ${b.label}`, el("pre", { class: "doc" }, b.text),
    el("p", { class: "small muted" }, `파일: ${b.file} · ${b.loc}`)) }, `${shortName(b.reg)} ${b.label}`);
}
function srcChip(s) { return s ? citeChips([s]) : null; }

function spark(values) {
  if (!values.length) return el("span");
  const w = 200, h = 38, mx = Math.max(...values), mn = Math.min(...values);
  const pts = values.map((v, i) => `${(i / (values.length - 1 || 1)) * w},${h - 4 - ((v - mn) / ((mx - mn) || 1)) * (h - 8)}`).join(" ");
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", `0 0 ${w} ${h}`); s.setAttribute("preserveAspectRatio", "none");
  s.innerHTML = `<polyline points="${pts}" fill="none" stroke="#0071e3" stroke-width="2"/>`;
  return s;
}

// ------------------------------------------------------------ 한눈에 보기
function renderHome() {
  const o = S.overview, t = o.totals, box = $("#tab-home");
  box.innerHTML = "";
  if (!o.latest) {
    box.append(el("div", { class: "panel empty" }, "아직 자료가 없습니다. '자료 넣기'에서 엑셀 내역이나 고지서를 넣어 주세요."));
    return;
  }
  box.append(el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, `최근 12개월 (${o.recent[0]} ~ ${o.recent[1]})`),
      el("span", { class: "small muted" }, `기준선 ${o.baseline[0]} ~ ${o.baseline[1]} · 자료 ${S.data.files.length}개 파일`)),
    el("div", { class: "kpis", style: { marginTop: "14px" } },
      el("div", { class: "stat" }, el("b", {}, num(t.kwh / 1000)), el("span", { class: "lbl" }, "전기 사용량(MWh)")),
      el("div", { class: "stat" }, el("b", {}, num(t.cost / 1e6)), el("span", { class: "lbl" }, "전기·가스 요금(백만원)")),
      el("div", { class: "stat" }, el("b", {}, num(t.co2_t)), el("span", { class: "lbl" }, "전기 온실가스(tCO2eq)")),
      el("div", { class: "stat hl" }, el("b", {}, t.findings + "건"), el("span", { class: "lbl" }, "점검 기준에 걸린 항목")),
      el("div", { class: "stat hl" }, el("b", {}, num(t.amount / 1e4) + "만"), el("span", { class: "lbl" }, "찾은 금액(원, 추정)"))),
    el("div", { class: "flow" }, "흐름:", el("b", {}, "고지서·내역 넣기"), "→", el("b", {}, "기온 보정 진단"), "→", el("b", {}, "점검 요청서"), "→", el("b", {}, "조치 등록"), "→", el("b", {}, "효과 측정·보고"))));

  if (o.target) box.append(targetPanel(o.target));
  if (S.budget && S.budget.totals) box.append(budgetBrief(S.budget));
  if (o.outlook && o.outlook.rows.length) box.append(outlookPanel(o.outlook));

  const sum = el("div", { class: "ans", id: "summary-text" }, "");
  const btn = el("button", { class: "primary", onclick: async () => {
    busy(btn, true, "총평 쓰는 중");
    try { const r = await api("POST", "/api/summary"); sum.textContent = r.text; mode.textContent = r.mode + (r.dropped ? " · " + r.dropped : ""); }
    finally { busy(btn, false); }
  } }, "AI 총평 쓰기");
  const mode = el("span", { class: "small muted" });
  box.append(el("div", { class: "panel" }, el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "총평"), mode, btn), sum));

  const facs = el("div", { class: "facs" });
  for (const f of o.facilities) {
    const d = f.norm_pct;
    facs.append(el("div", { class: "fac", onclick: () => { $("#fac-sel").value = f.name; GO("fac"); renderFac(); } },
      el("h4", {}, f.name), el("div", { class: "use" }, `${f.use || ""} · ${num(f.area, "㎡")}`),
      el("div", { class: "num" }, num(f.kwh, " kWh")),
      el("div", { class: "small" }, "기온 보정 후 ", el("b", { class: "delta " + (d > 0 ? "up" : "down") }, pct(d)),
        " · 전년 ", pct(f.change_pct)),
      spark(f.spark),
      el("div", { class: "small", style: { marginTop: "4px" } }, f.findings ? el("span", { class: "tag bad" }, `점검 ${f.findings}건`) : el("span", { class: "tag ok" }, "이상 없음"))));
  }
  box.append(el("div", { class: "panel" }, el("h2", {}, "시설"), facs));

  const top = el("div", {});
  for (const f of S.findings.slice(0, 4)) top.append(el("div", { class: "finding" }, el("i", { class: "dot " + f.level }),
    el("div", {}, el("div", { class: "ttl" }, f.title), el("div", { class: "small muted" }, f.summary),
      f.amount ? el("div", { class: "fix" }, el("b", {}, f.amount_label + " "), won(f.amount)) : null)));
  box.append(el("div", { class: "panel" }, el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "먼저 볼 것"),
    el("button", { onclick: () => GO("find") }, "점검 결과 전체")), top));
}

// 감축 목표: 시설별 기온 보정 변화(가로 막대, 0 기준 좌우)와 목표선
function targetPanel(t) {
  const W = 1100, rowH = 40, L = 140, R = 90, H = t.facilities.length * rowH + 30;
  const span = Math.max(t.target_pct + 2, ...t.facilities.map(x => Math.abs(x.change_pct))) * 1.1;
  const cx = L + (W - L - R) / 2, sx = v => cx + (v / span) * (W - L - R) / 2;
  let svg = `<line x1="${cx}" x2="${cx}" y1="4" y2="${H - 20}" stroke="#8e8e93"/>`;
  svg += `<line x1="${sx(-t.target_pct)}" x2="${sx(-t.target_pct)}" y1="4" y2="${H - 20}" stroke="#30d158" stroke-width="2" stroke-dasharray="5 4"/>`;
  svg += `<text x="${sx(-t.target_pct)}" y="${H - 4}" text-anchor="middle" fill="#248a3d">목표 -${t.target_pct}%</text><text x="${cx}" y="${H - 4}" text-anchor="middle">기준선</text>`;
  t.facilities.forEach((f, i) => {
    const y = 8 + i * rowH, v = f.change_pct, x0 = Math.min(cx, sx(v)), w = Math.max(2, Math.abs(sx(v) - cx));
    svg += `<text x="${L - 10}" y="${y + 17}" text-anchor="end" fill="#1d1d1f">${esc(f.name)}</text>`;
    svg += `<rect x="${x0}" y="${y + 4}" width="${w}" height="18" rx="4" fill="${v > 0 ? "#ff3b30" : v <= -t.target_pct ? "#30d158" : "var(--bar-recent, #6e9be0)"}"/>`;
    svg += `<text x="${v > 0 ? sx(v) + 6 : x0 - 6}" y="${y + 17}" text-anchor="${v > 0 ? "start" : "end"}" fill="#1d1d1f">${pct(v)}</text>`;
  });
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", `0 0 ${W} ${H}`); s.setAttribute("class", "chart"); s.innerHTML = svg;
  const done = t.gap_kwh === 0;
  return el("div", { class: "panel", id: "target-panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "감축 목표 진척"), basisChip(t.basis)),
    el("div", { class: "kpis", style: { marginTop: "12px", gridTemplateColumns: "repeat(4,1fr)" } },
      el("div", { class: "stat" }, el("b", {}, "-" + t.target_pct + "%"), el("span", { class: "lbl" }, "지침 목표(기온 보정)")),
      el("div", { class: "stat " + (done ? "hl" : "") }, el("b", { style: { color: t.change_pct > 0 ? "var(--bad)" : "" } }, pct(t.change_pct)), el("span", { class: "lbl" }, "최근 12개월 현재")),
      el("div", { class: "stat" }, el("b", {}, done ? "달성" : num(t.gap_kwh / 1000)), el("span", { class: "lbl" }, done ? "목표 달성" : "목표까지 더 줄일 양(MWh)")),
      el("div", { class: "stat hl" }, el("b", {}, pct(t.after_fix_pct)), el("span", { class: "lbl" }, "점검 결과를 조치했다면"))),
    el("div", { style: { marginTop: "12px" } }, s),
    el("p", { class: "small muted" }, t.note + ". 빨간 막대는 기준선보다 더 쓴 시설, 초록 점선은 목표선입니다. '조치했다면'은 점검 결과의 기온 보정 초과 사용분이 없었다고 보고 다시 계산한 값입니다."));
}

function budgetBrief(b) {
  const t = b.totals, warn = b.items.filter(i => i.state !== "적정");
  return el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, `${b.fy}년 공공요금 예산`),
      el("button", { onclick: () => GO("budget") }, "예산 관리 열기")),
    el("div", { class: "kpis", style: { marginTop: "12px", gridTemplateColumns: "repeat(4,1fr)" } },
      el("div", { class: "stat" }, el("b", {}, man(t.current)), el("span", { class: "lbl" }, "예산현액(원)")),
      el("div", { class: "stat" }, el("b", {}, t.exec_pct + "%"), el("span", { class: "lbl" }, "집행률")),
      el("div", { class: "stat" }, el("b", {}, man(t.expected)), el("span", { class: "lbl" }, "연말 예상 집행")),
      el("div", { class: "stat hl" }, el("b", { style: { color: t.remain < 0 ? "var(--bad)" : "" } }, man(t.remain)), el("span", { class: "lbl" }, "연말 예상 잔액"))),
    warn.length ? el("div", { style: { marginTop: "10px" } }, warn.map(i => el("div", { class: "small" },
      el("span", { class: "tag " + STATE_TAG[i.state] }, i.state), ` ${i.fac} ${i.item} `, i.state === "부족 예상" ? `${+i.run_out.slice(5)}월분부터 ${won(-i.remain)} 부족` : `${won(i.remain)} 남을 전망`))) : null);
}

function outlookPanel(o) {
  return el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, `${+o.ym.slice(5)}월 시설별 관리 목표`),
      el("span", { class: "small muted" }, `작년 ${+o.ref.slice(5)}월 기온 ${o.temp}℃ 기준`)),
    el("table", { class: "t", style: { marginTop: "10px" } }, el("tr", {}, ["시설", "예상 사용량", `점검선(+${o.excess_pct}%)`, "예상 전력량요금"].map(h => el("th", {}, h))),
      o.rows.map(x => el("tr", {}, el("td", {}, x.name), el("td", {}, num(x.expected, " kWh")), el("td", {}, el("b", {}, num(x.limit, " kWh"))), el("td", {}, won(x.cost))))),
    el("p", { class: "small muted" }, "시설 담당에게 이번 달 점검선을 미리 알려 두면, 고지서가 오기 전에 계량기로 확인할 수 있습니다. " + o.note));
}

// ------------------------------------------------------------ 점검 결과
function findingBox(f) {
  const steps = el("ol", { class: "steps" }, f.steps.map(s => el("li", {}, s)));
  return el("div", { class: "fbox " + f.level },
    el("div", { class: "row" }, el("span", { class: "tag " + (f.level === "gray" ? "gray" : f.level) }, LV[f.level] || ""),
      el("span", { class: "small muted" }, f.id), el("span", { style: { flex: 1 } }),
      f.amount ? el("span", { class: "amount" }, won(f.amount)) : null),
    el("h3", {}, f.title),
    el("p", { style: { margin: "4px 0 8px" } }, f.summary),
    f.note ? el("p", { class: "small", style: { color: "var(--warn)" } }, "※ " + f.note) : null,
    f.amount ? el("div", { class: "small muted" }, `${f.amount_label} 계산: ${f.formula}`) : null,
    f.evidence.length ? el("div", { style: { marginTop: "8px" } }, el("div", { class: "small muted" }, "근거 자료"),
      f.evidence.map(e => el("div", { class: "ev" }, e.label, " ", srcChip(e.src)))) : null,
    f.steps.length ? el("div", { style: { marginTop: "8px" } }, el("div", { class: "small muted" }, "확인할 일"), steps) : null,
    el("div", { class: "row", style: { marginTop: "10px" } },
      el("span", { class: "small muted" }, "관련 기준:"), (f.basis || []).filter(Boolean).map(basisChip),
      el("span", { style: { flex: 1 } }),
      f.fac ? el("button", { onclick: () => download(`/api/findings/${f.id}/request?fmt=hwpx`, {}) }, "점검 요청서(한글)") : null,
      f.fac ? el("button", { class: "primary", onclick: () => actionForm(f) }, "조치 등록") : null));
}

function renderFind() {
  const box = $("#tab-find");
  box.innerHTML = "";
  const c = { bad: 0, warn: 0, gray: 0 };
  S.findings.forEach(f => c[f.level]++);
  box.append(el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, `점검 결과 ${S.findings.length}건`),
      el("div", { class: "legend" }, el("span", {}, el("i", { class: "dot bad" }), `조치 필요 ${c.bad}`), el("span", {}, el("i", { class: "dot warn" }), `검토 ${c.warn}`))),
    el("p", { class: "small muted" }, "기관 지침의 기준값으로 점검했습니다. 기준값과 근거 조문은 '자료 넣기 > 점검 기준'에서 볼 수 있습니다. 금액은 고지서 단가와 설정값으로 계산한 추정치입니다.")));
  if (!S.findings.length) box.append(el("div", { class: "panel empty" }, "점검 기준에 걸린 항목이 없습니다."));
  S.findings.forEach(f => box.append(findingBox(f)));
}

// ------------------------------------------------------------ 시설별 분석
function renderFacSel() {
  const sel = $("#fac-sel");
  const cur = sel.value;
  sel.replaceChildren(...S.overview.facilities.map(f => el("option", { value: f.name, selected: f.name === cur }, f.name)));
  if ($("#tab-fac") && !$("#tab-fac").hidden) renderFac();
}

async function renderFac() {
  const fac = $("#fac-sel").value, kind = $("#kind-sel").value;
  if (!fac) return;
  let r;
  try { r = await api("GET", `/api/series?fac=${encodeURIComponent(fac)}&kind=${kind}`); }
  catch (e) { $("#fac-chart").replaceChildren(el("div", { class: "empty" }, "이 시설은 해당 에너지 자료가 없습니다.")); $("#fac-detail").innerHTML = ""; return; }
  const unit = kind === "gas" ? "㎥" : "kWh";
  const hitMonths = new Set(S.findings.filter(f => f.fac === fac && f.kind === kind && ["excess", "gas_excess", "closure"].includes(f.type)).flatMap(f => f.months));
  const rows = r.rows.filter(x => inPer(x.ym));
  if (!rows.length) { $("#fac-chart").replaceChildren(el("div", { class: "empty" }, "선택한 기간에 이 시설 자료가 없습니다.")); $("#fac-detail").innerHTML = ""; return; }
  const W = 1100, H = 300, L = 64, B = 34, T = 14;
  const raw = Math.max(...rows.map(x => Math.max(x.actual, x.expected || 0))) * 1.05;
  const mag = Math.pow(10, Math.floor(Math.log10(raw / 4)));
  const step = [1, 2, 2.5, 5, 10].map(k => k * mag).find(v => v * 4 >= raw);
  const mx = step * 4;
  const bw = (W - L - 10) / rows.length;
  const y = v => T + (H - T - B) * (1 - v / mx);
  let svg = "";
  const bIdx = rows.findIndex(x => x.baseline), bEnd = rows.map(x => x.baseline).lastIndexOf(true);
  if (bIdx >= 0) svg += `<rect class="base-bg" x="${L + bIdx * bw}" y="${T}" width="${(bEnd - bIdx + 1) * bw}" height="${H - T - B}"/><text x="${L + bIdx * bw + 6}" y="${T + 14}">기준 기간</text>`;
  for (let i = 0; i <= 4; i++) {
    const v = mx * i / 4;
    svg += `<line x1="${L}" x2="${W - 10}" y1="${y(v)}" y2="${y(v)}" stroke="#e5e5ea"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${Math.round(v).toLocaleString("ko-KR")}</text>`;
  }
  rows.forEach((x, i) => {
    const cls = x.closed > 0.4 ? "closed" : hitMonths.has(x.ym) ? "hit" : x.recent ? "recent" : "";
    svg += `<rect class="bar ${cls}" x="${L + i * bw + bw * 0.15}" y="${y(x.actual)}" width="${bw * 0.7}" height="${H - B - y(x.actual)}"><title>${x.ym} 실제 ${Math.round(x.actual).toLocaleString("ko-KR")}${unit}${x.expected ? " / 예상 " + x.expected.toLocaleString("ko-KR") + unit + " (" + (x.diff_pct > 0 ? "+" : "") + x.diff_pct + "%)" : ""}${x.temp != null ? " · 기온 " + x.temp + "℃" : ""}${x.closed ? " · 휴관 " + Math.round(x.closed * 100) + "%" : ""}</title></rect>`;
    if (i % 2 === 0 || rows.length < 14) svg += `<text x="${L + i * bw + bw / 2}" y="${H - B + 16}" text-anchor="middle">${ymLabel(x.ym)}</text>`;
  });
  const pts = rows.map((x, i) => x.expected ? `${L + i * bw + bw / 2},${y(x.expected)}` : null).filter(Boolean).join(" ");
  svg += `<polyline class="exp" points="${pts}"/>`;
  for (const a of r.actions.filter(a => a.status === "완료")) {
    const i = rows.findIndex(x => x.ym >= a.date.slice(0, 7));
    if (i < 0) continue;
    const xx = L + i * bw + bw / 2;
    svg += `<line class="act" x1="${xx}" x2="${xx}" y1="${T}" y2="${H - B}"/><text class="act-t" x="${xx + 4}" y="${T + 28}">${esc(a.title.slice(0, 14))}</text>`;
  }
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", `0 0 ${W} ${H}`); s.setAttribute("class", "chart"); s.innerHTML = svg;
  $("#fac-chart").replaceChildren(s, el("div", { class: "legend", style: { marginTop: "6px" } },
    el("span", {}, el("i", { class: "dot", style: { background: "var(--bar-recent, #6e9be0)", borderRadius: "3px" } }), "실제(최근 12개월)"),
    el("span", {}, el("i", { class: "dot", style: { background: "#ff3b30", borderRadius: "3px" } }), "기준 초과"),
    el("span", {}, el("i", { class: "dot", style: { background: "#d1d1d6", borderRadius: "3px" } }), "휴관"),
    el("span", {}, "┅ 기온만 감안한 예상")));
  const m = r.model;
  const det = $("#fac-detail");
  det.innerHTML = "";
  det.append(el("div", { class: "panel" },
    el("h3", {}, "기준선(기온 보정 모델)"),
    m ? el("p", { class: "small" }, `월 사용량 = ${num(m.base)} + ${m.cdd.toFixed(1)} × 냉방도일 + ${m.hdd.toFixed(1)} × 난방도일  ·  기준 기간 ${m.n}개월, 설명력(R²) ${m.r2}, 오차율(CV-RMSE) ${m.cv_rmse}%`)
      : el("p", { class: "small muted" }, "기준 기간 자료가 부족하거나 기온 자료가 없어 전년 같은 달과 비교했습니다."),
    el("table", { class: "t" }, el("tr", {}, ["연월", "기온", "실제", "예상", "차이", "휴관", "출처"].map(h => el("th", {}, h))),
      rows.slice().reverse().map(x => el("tr", {}, el("td", {}, x.ym), el("td", {}, x.temp != null ? x.temp + "℃" : ""), el("td", {}, num(x.actual, unit)),
        el("td", {}, num(x.expected, unit)), el("td", { style: { color: x.diff_pct >= 10 ? "var(--bad)" : "" } }, pct(x.diff_pct)),
        el("td", {}, x.closed ? Math.round(x.closed * 100) + "%" : ""), el("td", {}, srcChip(x.src)))))));
}

// ------------------------------------------------------------ 조치
function actionForm(f) {
  const title = el("input", { class: "wide", value: f && f.steps[0] ? f.steps[0].replace(/ 일정 잡기$/, "") : "" });
  const fac = el("select", {}, S.overview.facilities.map(x => el("option", { value: x.name, selected: f && x.name === f.fac }, x.name)));
  const d = el("input", { type: "date", value: new Date().toISOString().slice(0, 10) });
  const st = el("select", {}, ["예정", "진행", "완료"].map(x => el("option", {}, x)));
  const bg = modal("절감 조치 등록",
    f ? el("p", { class: "small muted" }, "점검 결과: " + f.title) : null,
    el("label", { class: "f" }, "시설"), fac, el("label", { class: "f" }, "조치 내용"), title,
    el("div", { class: "grid2" }, el("div", {}, el("label", { class: "f" }, "시행일"), d), el("div", {}, el("label", { class: "f" }, "상태"), st)),
    el("p", { class: "small muted" }, "완료로 바꾸면 시행일 다음 달부터 기온 보정 기준선과 비교해 효과를 자동으로 측정합니다."),
    el("button", { class: "primary", onclick: async () => {
      await api("POST", "/api/actions", { finding: f && f.id, fac: fac.value, title: title.value, date: d.value, status: st.value });
      bg.remove(); toast("등록했습니다"); await load(); GO("act");
    } }, "등록"));
}

function renderAct() {
  const box = $("#tab-act");
  box.innerHTML = "";
  box.append(el("div", { class: "panel" }, el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "절감 조치 이행·효과"),
    el("button", { class: "primary", onclick: () => actionForm(null) }, "조치 직접 등록")),
    el("p", { class: "small muted" }, "효과는 시행 전 기준선(기온 보정)과 시행 후 실제를 비교해 계산합니다. 휴관한 달은 빼고, 지침에 정한 기간 이상 쌓이면 '효과 확인'으로 바뀝니다.")));
  const rec = el("div", { id: "rec" });
  box.append(rec);
  renderRecommend(rec);
  if (!S.effects.length) { box.append(el("div", { class: "panel empty" }, "등록된 조치가 없습니다. 점검 결과에서 '조치 등록'을 누르세요.")); return; }
  for (const e of S.effects) {
    const user = e.origin === "user";
    box.append(el("div", { class: "panel" },
      el("div", { class: "row" }, el("h3", { style: { flex: 1, margin: 0 } }, `${e.fac} · ${e.title}`),
        el("span", { class: "tag " + (e.state === "효과 확인" ? "ok" : "info") }, e.state)),
      el("div", { class: "small muted" }, `시행일 ${e.date} · 상태 ${e.status} · 담당 ${e.owner || "-"}`, e.finding ? ` · 점검 결과: ${e.finding}` : "", e.src ? " · " : "", e.src ? srcChip(e.src) : null),
      e.saved_kwh != null ? el("div", { class: "kpis", style: { marginTop: "12px", gridTemplateColumns: "repeat(4,1fr)" } },
        el("div", { class: "stat hl" }, el("b", {}, (e.saved_pct > 0 ? "-" : "+") + Math.abs(e.saved_pct) + "%"), el("span", { class: "lbl" }, "기온 보정 사용량 변화")),
        el("div", { class: "stat" }, el("b", {}, num(e.saved_kwh)), el("span", { class: "lbl" }, "줄인 전기(kWh)")),
        el("div", { class: "stat" }, el("b", {}, num(e.saved_won / 1e4) + "만"), el("span", { class: "lbl" }, "줄인 요금(원, 추정)")),
        el("div", { class: "stat" }, el("b", {}, e.co2_t), el("span", { class: "lbl" }, "줄인 온실가스(tCO2eq)"))) : null,
      e.saved_kwh != null ? el("p", { class: "small muted" }, `측정 기간 ${e.from} ~ ${e.to} (${e.months}개월) · ${e.note}`) : null,
      user ? el("div", { class: "row", style: { marginTop: "8px" } },
        ["예정", "진행", "완료"].map(s => el("button", { class: s === e.status ? "primary small" : "small", onclick: async () => { await api("POST", `/api/actions/${e.id}`, { status: s }); await load(); } }, s)),
        el("button", { class: "small danger", onclick: async () => { await api("DELETE", `/api/actions/${e.id}`); await load(); } }, "삭제")) : null));
  }
}

// ------------------------------------------------------------ 물어보기
const EX = ["체육센터 8월 전기 왜 늘었어?", "올해 예산 모자라는 곳 있어?", "내년 예산 얼마 잡아야 해?", "감축 목표 달성할 수 있어?", "얼마나 아낄 수 있어?", "역률 문제 있는 곳?", "면적당 가장 많이 쓰는 시설은?", "주차장 기본요금 줄일 수 있어?", "문화예술회관 2월 왜 이렇게 많이 썼어?"];
async function ask(q) {
  q = (q || $("#q").value).trim();
  if (!q) return;
  $("#q").value = q;
  const out = $("#ask-out");
  out.replaceChildren(el("div", { class: "panel" }, el("span", { class: "spinner" }), " 계산 결과에서 찾는 중"));
  const r = await api("POST", "/api/ask", { q });
  out.replaceChildren(el("div", { class: "panel" },
    el("div", { class: "row" }, el("h3", { style: { flex: 1, margin: 0 } }, q), el("span", { class: "small muted" }, r.mode)),
    el("div", { class: "ans", style: { marginTop: "8px" } }, r.answer),
    r.dropped ? el("p", { class: "small", style: { color: "var(--warn)" } }, "※ " + r.dropped) : null,
    r.masked && r.masked.length ? el("p", { class: "small muted" }, `※ 질문의 개인정보(${r.masked.join(", ")})를 ○○○로 가린 뒤 처리했습니다.`) : null,
    r.sources.length ? el("div", { class: "small muted", style: { marginTop: "8px" } }, "근거: ", citeChips(r.sources)) : null,
    r.facts.length ? el("details", { style: { marginTop: "8px" } }, el("summary", { class: "small muted" }, `답에 쓴 사실 ${r.facts.length}개`),
      el("ul", { class: "small" }, r.facts.map(f => el("li", {}, f)))) : null));
}

// ------------------------------------------------------------ 보고서
function renderReport() {
  const box = $("#tab-report");
  box.innerHTML = "";
  box.append(el("div", { class: "panel" }, el("h2", {}, "에너지 사용 점검 보고서"),
    el("p", {}, "총평, 시설별 현황, 점검 결과(근거 조문·계산식), 절감 조치 이행·효과, 점검 기준을 한 문서로 만듭니다. '한눈에 보기'에서 AI 총평을 쓴 경우 그 총평이 들어갑니다."),
    el("div", { class: "row" }, el("span", { class: "small muted" }, "내려받기:"), exportButtons("/api/report"))));
  box.append(el("div", { class: "panel" }, el("h3", {}, "엑셀로 받기"),
    el("p", { class: "small muted" }, "점검 결과(근거 조문·계산식·출처), 시설별 월 내역(실제·기온 보정 예상·차이), 조치 효과를 시트별로 담습니다. 기관 양식에 옮겨 붙이거나 다시 계산할 때 쓰세요."),
    el("button", { onclick: () => download("/api/export-xlsx", {}) }, "엑셀(XLSX) 내려받기")));
  box.append(el("div", { class: "panel" }, el("h3", {}, "점검 요청서"),
    el("p", { class: "small muted" }, "점검 결과마다 담당 부서에 보낼 요청서(근거 자료·확인할 일·관련 기준)를 만들 수 있습니다."),
    el("table", { class: "t" }, el("tr", {}, ["번호", "점검 결과", "수신", ""].map(h => el("th", {}, h))),
      S.findings.filter(f => f.fac).map(f => el("tr", {}, el("td", {}, f.id), el("td", {}, f.title), el("td", {}, f.owner),
        el("td", {}, el("button", { class: "small", onclick: () => download(`/api/findings/${f.id}/request?fmt=hwpx`, {}) }, "한글")))))));
}

// ------------------------------------------------------------ 자료
function dropZone(text, onFiles) {
  const drop = el("div", { class: "drop" }, text, " ",
    el("label", { style: { color: "var(--brand)", cursor: "pointer" } }, "파일 선택", el("input", { type: "file", multiple: true, hidden: true, onchange: e => onFiles([...e.target.files]) })));
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", async e => { e.preventDefault(); drop.classList.remove("over"); onFiles(await filesFromDrop(e)); });
  return drop;
}

let FAC_UP = "";
function renderData() {
  const box = $("#tab-data"), d = S.data;
  box.innerHTML = "";
  async function up(files, fac) {
    if (!files.length) return;
    toast(fac ? `${fac} 자료 읽는 중…` : "읽는 중…");
    await load(await api("POST", "/api/upload", { files: await readFiles(files), fac: fac || "" }));
    toast("반영했습니다");
  }
  const bills = el("button", { class: "primary", onclick: async () => {
    busy(bills, true, "고지서 읽는 중");
    try { await load(await api("POST", "/api/sample", { with_bills: true })); toast("9월 고지서 5장을 읽어 반영했습니다"); }
    finally { busy(bills, false); }
  } }, "시연: 9월 고지서 PDF 5장 넣기");
  box.append(el("div", { class: "panel" }, el("h2", {}, "자료 넣기"),
    el("p", { class: "small muted" }, "원본 파일은 읽기만 하고, 이 PC의 작업 폴더(data)에 복사본을 둡니다. 기관 전체 자료는 ① 통합 자료에, 시설마다 오는 고지서는 ② 시설별 자료에 넣으세요."),
    el("h3", { style: { marginTop: "18px" } }, "① 통합 자료"),
    el("p", { class: "small muted" }, "여러 시설이 한 표에 들어 있는 자료: 전력·가스 사용 내역(엑셀), 시설 현황, 기온, 조치 이력, 공공요금 예산서, 국제유가, 에너지 절약 지침(한글). 표는 열 이름으로 알아봅니다(열 순서가 달라도 됨)."),
    dropZone("통합 자료를 여기에 끌어다 놓거나", f => up(f, "")),
    el("div", { class: "row", style: { marginTop: "12px" } },
      el("button", { onclick: async () => { await load(await api("POST", "/api/sample", { reset: true })); toast("모의자료(8월까지)로 되돌렸습니다"); } }, "모의자료 처음 상태로"),
      el("button", { class: "danger", onclick: async () => { await load(await api("POST", "/api/reset")); } }, "자료 모두 비우기"))));
  const facPanel = el("div", { class: "panel", id: "fac-data" }, el("span", { class: "spinner" }));
  box.append(facPanel);
  renderFacData(facPanel, up, bills);
  box.append(kmaPanel(S.kma || {}));
  box.append(el("div", { class: "panel" }, el("h3", {}, `읽은 자료 (${d.months.length ? d.months[0] + " ~ " + d.months[1] : "없음"})`),
    el("p", { class: "small" }, `시설 ${d.facilities}곳 · 전력 ${d.elec}건 · 가스 ${d.gas}건 · 기온 ${d.weather}개월 · 조치 ${d.actions}건`),
    el("table", { class: "t" }, el("tr", {}, ["파일", "종류", "건수"].map(h => el("th", {}, h))),
      d.files.map(f => el("tr", {}, el("td", {}, f.file), el("td", {}, f.kind), el("td", {}, f.rows)))),
    d.warnings.length ? el("div", { style: { marginTop: "10px" } }, d.warnings.map(w => el("div", { class: "small", style: { color: "var(--warn)" } }, "※ " + w))) : null,
    d.conflicts.length ? el("div", { style: { marginTop: "10px" } }, el("b", { class: "small" }, "같은 달 값이 서로 다른 자료"),
      d.conflicts.map(c => el("div", { class: "small" }, `${c.key.join(" ")} ${c.fields.join(", ")}: `, srcChip(c.old), " → ", srcChip(c.new)))) : null));
  if (S.bills.length) box.append(el("div", { class: "panel" }, el("h3", {}, "고지서 PDF 읽기 결과"),
    el("table", { class: "t" }, el("tr", {}, ["파일", "시설", "연월", "사용전력량", "청구금액", "역률", "읽기"].map(h => el("th", {}, h))),
      S.bills.map(b => el("tr", {}, el("td", {}, shortName(b.file)), el("td", {}, b.fac || b.fields.fac || ""), el("td", {}, b.fields.ym || ""),
        el("td", {}, num(b.fields.kwh, "kWh")), el("td", {}, won(b.fields.total)), el("td", {}, b.fields.pf ? b.fields.pf + "%" : ""),
        el("td", {}, b.missing.length ? el("span", { class: "tag bad" }, "못 읽음: " + b.missing.join(", ")) : b.ocr ? el("span", { class: "tag warn", title: "스캔본을 글자 인식으로 읽었습니다. 숫자를 원본과 대조하세요" }, "글자 인식 · 원본 대조") : el("span", { class: "tag ok" }, b.by_model.length ? "규칙+모델" : "규칙")))))));
  box.append(el("div", { class: "panel" }, el("h3", {}, "점검 기준"),
    el("p", { class: "small muted" }, "기준값은 넣은 지침의 조문에서 읽습니다. 지침을 고쳐 다시 넣으면 기준이 바뀝니다. 지침에 없는 값은 기본값을 씁니다."),
    el("table", { class: "t" }, el("tr", {}, ["기준", "값", "근거"].map(h => el("th", {}, h))),
      S.overview.rules.map(r => el("tr", {}, el("td", {}, r.label), el("td", {}, r.value == null ? "-" : r.value), el("td", {}, r.basis ? basisChip(r.basis) : el("span", { class: "tag gray" }, "기본값"))))),
    el("p", { class: "small muted" }, `전력 온실가스 배출계수 ${S.overview.co2_factor} kgCO2eq/kWh, 요금 단가·휴관 사용 비율·계약전력 여유율은 config.json의 energy 항목에서 바꿉니다.`)));
}

function kmaPanel(k) {
  const on = !!k.on;
  const set = async body => { toast(body.on === false ? "끄는 중…" : "기상청 기온 받는 중…"); await load(await api("POST", "/api/kma", body)); toast("반영했습니다"); };
  let status;
  if (!on) status = el("span", { class: "tag gray" }, "꺼짐 · 넣은 기온 파일 사용");
  else if (k.error) status = el("span", { class: "tag bad" }, k.error);
  else status = el("span", { class: "tag ok" }, `${k.months}개월 받음 (${(k.range || []).join(" ~ ")})` + (k.partial && k.partial.length ? ` · 덜 찬 달 ${k.partial.join(", ")}` : ""));
  return el("div", { class: "panel", id: "kma-panel" },
    el("div", { class: "row" }, el("h3", { style: { flex: 1, margin: 0 } }, "기온 자동 수집(기상청)"),
      on ? el("button", { onclick: () => set({ refresh: true }) }, "다시 받기") : null,
      el("button", { class: on ? "" : "primary", onclick: () => set({ on: !on }) }, on ? "끄기" : "켜기")),
    el("p", { class: "small", style: { marginTop: "8px" } }, "상태: ", status),
    el("p", { class: "small muted" }, k.provider ? `창구: ${k.label} · 지점 ${k.stn}` : "키가 없습니다. 기상청 API허브 키는 환경변수 KMA_APIHUB_KEY, 공공데이터포털 키는 DATA_GO_KR_KEY에 넣으세요(화면·파일에 적지 않음). 지점은 WEATHER_STN(기본 108 서울)."),
    el("p", { class: "small muted" }, "켜면 내역이 있는 달의 기온을 기상청 지상관측(ASOS) 일자료로 바꾸고, 도일을 날마다 더해 계산합니다. 받은 달은 저장해 두고 다시 받지 않습니다. 시연용 모의자료는 모의 기온으로 만든 숫자라, 실제 기온으로 바꾸면 점검 결과가 달라집니다(실제 기관 자료를 넣을 때 켜세요)."));
}

async function renderLog() {
  const r = await api("GET", "/api/log");
  $("#tab-log").replaceChildren(el("div", { class: "panel" }, el("h2", {}, "작업 기록"),
    r.items.length ? el("table", { class: "t" }, el("tr", {}, ["시각", "작업", "내용"].map(h => el("th", {}, h))),
      r.items.map(i => el("tr", {}, el("td", {}, i.at), el("td", {}, i.action), el("td", {}, i.detail || "")))) : el("div", { class: "empty" }, "기록이 없습니다.")));
}

const ADMIN_TABS = ["log", "about"];
function setupAdmin() {
  let on = false;
  try {
    const q = new URLSearchParams(location.search).get("admin");
    if (q != null) localStorage.setItem("hantol:admin", q === "1" ? "1" : "0");
    on = localStorage.getItem("hantol:admin") === "1";
    if (!on && ADMIN_TABS.includes(localStorage.getItem(location.pathname + ":tab"))) localStorage.removeItem(location.pathname + ":tab");
  } catch (e) {}
  const btn = $("#admin-toggle");
  const apply = () => {
    document.body.classList.toggle("admin", on);
    if (btn) btn.textContent = on ? "관리자 모드 끄기" : "관리자 모드";
    window.dispatchEvent(new Event("resize"));
  };
  if (btn) btn.addEventListener("click", () => {
    on = !on;
    try { localStorage.setItem("hantol:admin", on ? "1" : "0"); } catch (e) {}
    apply();
    if (!on && GO && ADMIN_TABS.some(t => !$("#tab-" + t).hidden)) GO("home");
    toast(on ? "관리자 모드: 작업 기록·서비스 구조·검증 메뉴가 보입니다" : "관리자 모드를 껐습니다");
    if (on) window.scrollTo({ top: 0, behavior: "smooth" });
  });
  apply();
}

document.addEventListener("DOMContentLoaded", async () => {
  setupAdmin();
  GO = setupTabs(id => { if (id === "budget") renderBudget(); if (id === "log") renderLog(); if (id === "about") renderAbout(); if (id === "fac" && S) { renderFac(); renderWeather(); renderKepco(); } });
  $("#fac-sel").addEventListener("change", renderFac);
  setupPeriod();
  $("#kind-sel").addEventListener("change", renderFac);
  $("#ask-btn").addEventListener("click", () => ask());
  $("#q").addEventListener("keydown", e => { if (e.key === "Enter") ask(); });
  $("#ask-ex").append(...EX.map(x => el("span", { class: "chip", onclick: () => ask(x) }, x)));
  await load();
  if (!$("#tab-fac").hidden) renderFac();
});

// ------------------------------------------------------------ 예산 관리
let BUD = null;
const man = v => (v == null ? "-" : Math.abs(v) >= 1e8 ? (v / 1e8).toFixed(2) + "억" : Math.round(v / 1e4).toLocaleString("ko-KR") + "만");
let BSEL = "";
const STATE_TAG = { "부족 예상": "bad", "불용 예상": "warn", "적정": "ok" };
const BOPT = { save_pct: 0, elec_up_pct: 0, gas_up_pct: 0, climate_summer: 1.5 };

async function renderBudget() {
  const box = $("#tab-budget");
  if (!BUD) box.replaceChildren(el("div", { class: "panel" }, el("span", { class: "spinner" }), " 예산 계산 중"));
  const q = Object.entries(BOPT).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  BUD = await api("GET", "/api/budget?" + q);
  box.replaceChildren(...[budgetCurrent(BUD.current), el("div", { id: "bud-input" }), budgetNextMonth(BUD.next_month), budgetNextYear(BUD.next_year), budgetDocs()].filter(Boolean));
  renderBudgetInput();
}

function itemCum(i) {
  const m = {};
  i.months_spent.forEach(x => { m[x.ym] = { ym: x.ym, spent: x.spent, fc: 0, lo: 0, hi: 0, actual: true }; });
  i.months_fc.forEach(x => { m[x.ym] = { ym: x.ym, spent: 0, fc: x.cost, lo: x.lo, hi: x.hi, actual: false }; });
  let acc = 0;
  return Object.keys(m).sort().map(k => { acc += m[k].spent + m[k].fc; return { ...m[k], cum: acc }; });
}

function cumChart(c) {
  const it = BSEL ? c.items.find(i => `${i.fac}|${i.item}` === BSEL) : null;
  const rows = it ? itemCum(it) : c.cum, t = it ? { main: it.main, current: it.current } : c.totals;
  const W = 1100, H = 280, L = 70, B = 30, T = 16, R = 150;
  const mx = Math.max(t.current, rows[rows.length - 1].cum + rows.reduce((s, r) => s + (r.actual ? 0 : r.hi - r.fc), 0)) * 1.08;
  const step = (W - L - R) / (rows.length - 1 || 1), x = i => L + i * step, y = v => T + (H - T - B) * (1 - v / mx);
  let svg = "";
  for (let i = 0; i <= 4; i++) { const v = mx * i / 4; svg += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="#ece6dc"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${Math.round(v / 1e8 * 10) / 10}억</text>`; }
  if (t.main !== t.current) svg += `<line x1="${L}" x2="${W - R}" y1="${y(t.main)}" y2="${y(t.main)}" stroke="#a89a88" stroke-dasharray="3 4"/><text x="${L + 8}" y="${y(t.main) + (t.main < t.current ? 16 : -6)}" style="fill:#7a6c5d">본예산 ${man(t.main)}</text>`;
  svg += `<line x1="${L}" x2="${W - R}" y1="${y(t.current)}" y2="${y(t.current)}" stroke="#2f241b" stroke-width="2"/><text x="${L + 8}" y="${y(t.current) + (t.main < t.current ? -6 : 16)}" style="fill:#2f241b;font-weight:700">예산현액 ${man(t.current)}</text>`;
  let acc = 0, lo = 0, hi = 0, act = [], fc = [], band = [];
  rows.forEach((r, i) => {
    acc += r.spent + r.fc; lo += r.spent + r.lo; hi += r.spent + r.hi;
    if (r.actual && !r.fc) act.push(`${x(i)},${y(acc)}`);
    else { if (!fc.length && act.length) fc.push(act[act.length - 1]); fc.push(`${x(i)},${y(acc)}`); band.push([x(i), y(lo), y(hi)]); }
    svg += `<text x="${x(i)}" y="${H - B + 18}" text-anchor="middle">${+r.ym.slice(5)}월</text>`;
  });
  if (band.length) {
    const p0 = fc[0].split(",");
    const up = [p0.join(","), ...band.map(b => `${b[0]},${b[2]}`)], dn = [...band.map(b => `${b[0]},${b[1]}`).reverse(), p0.join(",")];
    svg += `<polygon points="${up.concat(dn).join(" ")}" fill="var(--accent)" opacity=".13"/>`;
    svg += `<polyline points="${fc.join(" ")}" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-dasharray="7 5"/>`;
  }
  svg += `<polyline points="${act.join(" ")}" fill="none" stroke="var(--accent-strong)" stroke-width="3"/>`;
  act.forEach(p => { const [a, b] = p.split(","); svg += `<circle cx="${a}" cy="${b}" r="3.5" fill="var(--accent-strong)"/>`; });
  const end = rows[rows.length - 1].cum, over = end > t.current;
  svg += `<text x="${x(rows.length - 1) + 10}" y="${y(end) + 5}" style="fill:${over ? "#d7263d" : "var(--accent-strong)"};font-weight:700">연말 ${man(end)}</text>`;
  svg += `<text x="${x(rows.length - 1) + 10}" y="${y(end) + 22}" style="fill:${over ? "#d7263d" : "#7a6c5d"}">${over ? man(end - t.current) + " 부족" : man(t.current - end) + " 남음"}</text>`;
  if (over) { const k = rows.findIndex(r => r.cum > t.current); if (k >= 0) svg += `<circle cx="${x(k)}" cy="${y(rows[k].cum)}" r="7" fill="none" stroke="#d7263d" stroke-width="2.5"/><text x="${x(k)}" y="${y(rows[k].cum) - 14}" text-anchor="middle" style="fill:#d7263d;font-weight:700">${+rows[k].ym.slice(5)}월 소진</text>`; }
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", `0 0 ${W} ${H}`); s.setAttribute("class", "chart"); s.innerHTML = svg;
  return s;
}

function budgetCurrent(c) {
  if (!c || !c.items.length) return el("div", { class: "panel empty" }, (c && c.note) || "예산서가 없습니다. '자료 넣기'에 본예산·추경 엑셀을 넣어 주세요.");
  const t = c.totals;
  const short = c.items.filter(i => i.state === "부족 예상"), unused = c.items.filter(i => i.state === "불용 예상");
  const opinions = el("div", {},
    short.map(i => el("div", { class: "finding" }, el("i", { class: "dot bad" }), el("div", {},
      el("div", { class: "ttl" }, `${i.fac} ${i.item}: ${+i.run_out.slice(5)}월분 고지부터 부족 · ${won(i.need)} 모자람`),
      el("div", { class: "small muted" }, i.need_after_moves ? `아래 전용을 하고도 ${won(i.need_after_moves)}은 추경 증액이 필요합니다.` : "아래 전용으로 메울 수 있습니다.")))),
    c.moves.map(m => el("div", { class: "finding" }, el("i", { class: "dot warn" }), el("div", {},
      el("div", { class: "ttl" }, `전용 검토: ${m.from} → ${m.to} ${won(m.amount)}`),
      el("div", { class: "small muted" }, `불용 예상 잔액에서 예산현액의 ${c.unused_pct}%는 남기고 옮기는 안입니다. 기관 예산 집행 규정과 승인 절차를 확인하세요.`)))),
    unused.map(i => el("div", { class: "finding" }, el("i", { class: "dot warn" }), el("div", {},
      el("div", { class: "ttl" }, `${i.fac} ${i.item}: 불용 예상 ${won(i.remain)} (${i.remain_pct}%)`),
      el("div", { class: "small muted" }, "전용 뒤 남는 금액은 감액 추경이나 다른 사업 재배정을 검토하고, 내년 편성액에 반영하세요.")))),
    !short.length && !unused.length ? el("p", { class: "small" }, "모든 과목이 적정 범위입니다.") : null);
  return el("div", {},
    el("div", { class: "panel" },
      el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, `${c.fy}년 공공요금 예산 관리`),
        el("span", { class: "small muted" }, `${c.latest}분 고지까지 · 경과율 ${c.elapsed_pct}%`)),
      el("div", { class: "kpis", style: { marginTop: "14px" } },
        el("div", { class: "stat" }, el("b", {}, man(t.current)), el("span", { class: "lbl" }, `예산현액(본예산 ${man(t.main)} + 추경·전용 ${man(t.supp)})`)),
        el("div", { class: "stat" }, el("b", {}, t.exec_pct + "%"), el("span", { class: "lbl" }, `집행 ${man(t.spent)} · 경과율 ${c.elapsed_pct}%`)),
        el("div", { class: "stat" }, el("b", {}, man(t.expected)), el("span", { class: "lbl" }, `연말 예상 집행(남은 달 ${man(t.fc)})`)),
        el("div", { class: "stat hl" }, el("b", { style: { color: t.remain < 0 ? "var(--bad)" : "" } }, man(t.remain)), el("span", { class: "lbl" }, "연말 예상 잔액(원)")),
        el("div", { class: "stat hl" }, el("b", {}, `부족 ${t.short}`, el("small", { style: { fontSize: ".6em", color: "var(--muted)" } }, ` · 불용 ${t.unused}`)), el("span", { class: "lbl" }, `과목 ${c.items.length}개 판정`))),
      el("div", { class: "row", style: { marginTop: "16px" } }, el("b", { style: { flex: 1 } }, "누적 집행과 연말 예측"),
        el("select", { id: "bud-sel", onchange: e => { BSEL = e.target.value; $("#bud-chart").replaceChildren(cumChart(c)); } },
          el("option", { value: "" }, "전체 과목"), c.items.map(i => el("option", { value: `${i.fac}|${i.item}`, selected: BSEL === `${i.fac}|${i.item}` }, `${i.fac} ${i.item} (${i.state})`)))),
      el("div", { id: "bud-chart", style: { marginTop: "6px" } }, cumChart(c)),
      el("div", { class: "legend", style: { marginTop: "4px" } },
        el("span", {}, "━ 누적 집행(고지)"), el("span", {}, "┅ 남은 달 예측(색 띠는 오차 범위)"), el("span", {}, "━ 예산현액 · ┄ 본예산")),
      el("p", { class: "small muted" }, c.note)),
    el("div", { class: "panel" }, el("h3", {}, "과목별 판정"),
      el("table", { class: "t" }, el("tr", {}, ["시설", "과목", "본예산", "추경·전용", "예산현액", "집행액", "남은 달 예측", "예상 잔액", "판정", "예산서"].map(h => el("th", {}, h))),
        c.items.map(i => el("tr", {}, el("td", {}, i.fac), el("td", {}, i.item), el("td", {}, man(i.main)),
          el("td", { title: i.note || "" }, i.supp + i.transfer ? (i.supp + i.transfer > 0 ? "+" : "") + man(i.supp + i.transfer) : "-"),
          el("td", {}, el("b", {}, man(i.current))), el("td", {}, `${man(i.spent)} (${i.exec_pct}%)`), el("td", { title: i.months_fc.map(m => `${m.ym} ${won(m.cost)}${m.billed_missing ? "(고지 전)" : ""}`).join("\n") }, man(i.fc)),
          el("td", { style: { color: i.remain < 0 ? "var(--bad)" : "" } }, `${man(i.remain)} (${i.remain_pct > 0 ? "+" : ""}${i.remain_pct}%)`),
          el("td", {}, el("span", { class: "tag " + STATE_TAG[i.state] }, i.state + (i.run_out ? ` · ${+i.run_out.slice(5)}월` : ""))),
          el("td", {}, srcChip(i.src)))))),
    el("div", { class: "panel", id: "bud-op" }, el("h3", {}, "조치 의견"), opinions));
}

function budgetNextMonth(n) {
  if (!n || !n.rows.length) return null;
  return el("div", { class: "panel", id: "bud-next" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, `다음 달(${+n.ym.slice(5)}월) 요금 예측`),
      el("span", { class: "amount" }, won(n.total))),
    el("table", { class: "t", style: { marginTop: "10px" } }, el("tr", {}, ["시설", "전기 사용량", "전기요금(범위)", "가스 사용량", "가스요금(범위)", "합계"].map(h => el("th", {}, h))),
      n.rows.map(r => el("tr", {}, el("td", {}, r.fac),
        el("td", {}, r.elec ? num(r.elec.qty, " kWh") : "-"), el("td", {}, r.elec ? `${won(r.elec.cost)} (${man(r.elec.lo)}~${man(r.elec.hi)})` : "-"),
        el("td", {}, r.gas ? num(r.gas.qty, " ㎥") : "-"), el("td", {}, r.gas ? `${won(r.gas.cost)} (${man(r.gas.lo)}~${man(r.gas.hi)})` : "-"),
        el("td", {}, el("b", {}, won(r.cost)))))),
    el("p", { class: "small muted" }, n.note));
}

function oilChart(o) {
  const W = 520, H = 150, L = 34, B = 22, T = 10, RP = 22;
  const v = o.series.map(s => s.oil), mn = Math.min(...v) - 2, mx = Math.max(...v) + 2;
  const x = i => L + i * (W - L - RP) / (v.length - 1), y = a => T + (H - T - B) * (1 - (a - mn) / (mx - mn));
  const fi = o.series.findIndex(s => (s.kind || "").includes("전망"));
  let svg = `<text x="${L - 6}" y="${y(mx) + 8}" text-anchor="end">${Math.round(mx)}</text><text x="${L - 6}" y="${y(mn)}" text-anchor="end">${Math.round(mn)}</text>`;
  if (fi > 0) svg += `<rect x="${x(fi)}" y="${T}" width="${W - RP - x(fi)}" height="${H - T - B}" fill="var(--accent-soft)"/><text x="${x(fi) + 6}" y="${T + 14}" style="fill:var(--accent-strong)">전망</text>`;
  svg += `<polyline points="${v.map((a, i) => `${x(i)},${y(a)}`).join(" ")}" fill="none" stroke="var(--accent-strong)" stroke-width="2"/>`;
  [0, Math.floor(v.length / 2), v.length - 1].forEach(i => { svg += `<text x="${x(i)}" y="${H - 4}" text-anchor="middle">${o.series[i].ym.slice(2).replace("-", ".")}</text>`; });
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", `0 0 ${W} ${H}`); s.setAttribute("class", "chart"); s.innerHTML = svg;
  return s;
}

function budgetNextYear(n) {
  if (!n) return null;
  const t = n.totals;
  const inp = (k, label, unit) => el("label", { class: "small", style: { display: "inline-flex", alignItems: "center", gap: "6px" } }, label,
    el("input", { type: "number", step: "0.5", value: BOPT[k], id: "bopt-" + k, style: { width: "70px" }, oninput: e => { BOPT[k] = e.target.value || 0; } }), unit);
  const btn = el("button", { class: "primary", onclick: async () => { busy(btn, true, "다시 계산 중"); try { await renderBudget(); } finally { busy(btn, false); } } }, "다시 계산");
  const o = n.oil;
  return el("div", { id: "bud-ny" },
    el("div", { class: "panel" },
      el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, `${n.fy}년도 예산 편성 근거`), el("span", { class: "small muted" }, "평년 기온 · 최근 사용 수준 · 단가 전망")),
      el("div", { class: "row", style: { marginTop: "10px", gap: "14px", flexWrap: "wrap" } },
        inp("save_pct", "추가 절감 목표", "%"), inp("elec_up_pct", "전기요금 인상", "%"), inp("gas_up_pct", "가스요금 인상", "%"), inp("climate_summer", "여름 이상고온", "℃"), btn),
      el("div", { class: "kpis", style: { marginTop: "14px" } },
        el("div", { class: "stat hl" }, el("b", {}, man(t.request)), el("span", { class: "lbl" }, `${n.fy}년 요구액(원)`)),
        el("div", { class: "stat" }, el("b", { style: { color: t.change_pct > 0 ? "var(--bad)" : "var(--ok)" } }, t.change_pct == null ? "-" : pct(t.change_pct)), el("span", { class: "lbl" }, "올해 예산현액 대비")),
        el("div", { class: "stat" }, el("b", {}, man(t.risk)), el("span", { class: "lbl" }, "기후 위험 여유분(폭염·한파)")),
        el("div", { class: "stat" }, el("b", {}, o.has ? pct(o.gas_change_pct) : "-"), el("span", { class: "lbl" }, o.has ? `가스 단가(유가 ${o.now_avg}→${o.next_avg}달러)` : "유가 자료 없음")),
        el("div", { class: "stat" }, el("b", {}, num(t.co2_t)), el("span", { class: "lbl" }, t.target_pct ? `전기 온실가스 tCO2eq(목표 달성 시 ${num(t.co2_target_t)})` : "전기 온실가스 tCO2eq"))),
      el("table", { class: "t", style: { marginTop: "14px" } }, el("tr", {}, ["시설", "과목", "요구액", t.entered != null ? "입력한 예산(차이)" : null, "올해 예산현액", "증감", "기후 위험 여유분", "산출 근거"].filter(Boolean).map(h => el("th", {}, h))),
        n.items.map(i => el("tr", {}, el("td", {}, i.fac), el("td", {}, i.item), el("td", {}, el("b", {}, won(i.request))),
          t.entered != null ? el("td", {}, i.entered == null ? "-" : man(i.entered), i.entered == null ? null : el("div", { class: "small", style: { color: i.entered < i.request ? "var(--bad)" : "var(--muted)" } }, (i.entered - i.request >= 0 ? "+" : "") + man(i.entered - i.request))) : null,
          el("td", {}, man(i.prev_budget)),
          el("td", { style: { color: i.change_pct > 0 ? "var(--bad)" : "var(--ok)" } }, i.change_pct == null ? "-" : pct(i.change_pct)), el("td", {}, man(i.risk)),
          el("td", { class: "small" }, i.basis)))),
      el("p", { class: "small muted" }, n.note)),
    el("div", { class: "grid2" },
      el("div", { class: "panel" }, el("h3", {}, "편성 가정"),
        el("table", { class: "t" }, n.assume.map(a => el("tr", {}, el("td", {}, el("b", {}, a.k)), el("td", {}, a.v, el("div", { class: "small muted" }, a.detail)))))),
      el("div", { class: "panel" }, el("h3", {}, "국제유가(두바이유, 달러/배럴)"),
        o.has ? oilChart(o) : null,
        el("p", { class: "small muted" }, o.note, " ", o.src ? srcChip(o.src) : null),
        domesticOil())));
}

function domesticOil() {
  const box = el("div", { class: "dom-oil" }, el("h3", { style: { marginTop: "18px" } }, "국내 유가(오피넷, 원/리터)"), el("div", { class: "small muted" }, "받는 중…"));
  api("GET", "/api/opinet").then(r => {
    const head = box.firstChild;
    if (!r.key) { box.replaceChildren(head, el("p", { class: "small muted" }, "오피넷 인증키가 없어 표시하지 않습니다. 오피넷 '유가 API' 신청(무료, 바로 승인)으로 받은 키를 환경변수 OPINET_KEY에 넣으면 날마다 전국·시도 평균가격을 자동으로 받습니다.")); return; }
    const d = r.data;
    const err = r.error ? el("p", { class: "small", style: { color: "var(--warn)" } }, r.error) : null;
    if (!d) { box.replaceChildren(head, err || el("p", { class: "small muted" }, "아직 받은 값이 없습니다.")); return; }
    box.replaceChildren(...[head, err,
      el("table", { class: "t" }, el("tr", {}, ["제품", "전국 평균", "전일 대비", d.sido_name ? d.sido_name + " 평균" : "시도 평균"].map(h => el("th", {}, h))),
        d.items.map(x => el("tr", {}, el("td", {}, x.name), el("td", {}, x.price.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) + "원"),
          el("td", { style: { color: x.diff > 0 ? "var(--bad)" : x.diff < 0 ? "var(--ok)" : "" } }, x.diff == null ? "-" : (x.diff > 0 ? "+" : "") + x.diff),
          el("td", {}, x.sido == null ? "-" : x.sido.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) + "원")))),
      d.monthly.length > 1 ? el("table", { class: "t", style: { marginTop: "8px" } }, el("tr", {}, ["달", "휘발유", "경유", "실내등유", "받은 날"].map(h => el("th", {}, h))),
        d.monthly.slice(-6).reverse().map(m => el("tr", {}, el("td", {}, m.ym), ...["휘발유", "경유", "실내등유"].map(k => el("td", {}, m[k] == null ? "-" : num(m[k]))), el("td", {}, m.n + "일")))) : null,
      el("p", { class: "small muted" }, `${d.date} 기준 · 출처 ${d.src}. 국내 주유소 판매가격이라 참고용이며, 가스 단가 전망은 위 국제유가(두바이유)×환율 연동으로 계산합니다. 지난 달 가격을 한꺼번에 주는 API가 없어 받은 날이 쌓이면 달 평균이 나옵니다.`)].filter(Boolean));
  }).catch(e => box.replaceChildren(box.firstChild, el("p", { class: "small", style: { color: "var(--warn)" } }, "국내 유가를 불러오지 못했습니다: " + (e.message || e))));
  return box;
}

function budgetDocs() {
  return el("div", { class: "panel", id: "bud-docs" }, el("h3", {}, "예산 문서"),
    el("p", { class: "small muted" }, "올해 집행 관리 현황(판정·조치 의견·전용 검토), 다음 달 예측, 내년 편성 근거(산출 근거·가정·유가·온실가스)를 한 문서로 만듭니다. 위에서 바꾼 가정값이 그대로 들어갑니다."),
    el("div", { class: "row" }, el("span", { class: "small muted" }, "내려받기:"), exportButtons("/api/budget/report", () => ({ ...BOPT })),
      el("button", { onclick: () => download("/api/export-xlsx", {}) }, "엑셀(XLSX)")));
}

// ------------------------------------------------------------ 기상 분석(기상청 기온)
const WCOOL = [47, 109, 181], WWARM = [201, 102, 26], WMID = [240, 239, 236];
function tempColor(v, mid) {
  const k = Math.max(-1, Math.min(1, (v - mid) / 14)), p = k < 0 ? WCOOL : WWARM, a = Math.abs(k);
  return `rgb(${WMID.map((m, i) => Math.round(m + (p[i] - m) * a)).join(",")})`;
}
function svgBox(W, H, inner) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", `0 0 ${W} ${H}`); s.setAttribute("class", "chart wx"); s.innerHTML = inner;
  return s;
}
const ymAdd = (ym, n) => { const d = new Date(+ym.slice(0, 4), +ym.slice(5) - 1 + n, 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; };

// ---------------------------------------------------------------- 분석 기간(시설별 분석 탭 전체에 적용)
let PER = null;
const inPer = ym => !PER || (ym >= PER.from && ym <= PER.to);
function renderPeriod() {
  const ms = S.months || [];
  const f = $("#per-from"), t = $("#per-to");
  if (!f || !ms.length) return;
  if (!PER || !ms.includes(PER.from) || !ms.includes(PER.to)) {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem("hantol:period") || "null"); } catch (e) { /* 저장 안 됨 */ }
    PER = saved && ms.includes(saved.from) && ms.includes(saved.to) && saved.from <= saved.to ? saved : { from: ms[0], to: ms[ms.length - 1] };
  }
  const opts = sel => ms.map(m => el("option", { value: m, selected: m === sel }, ymLabel(m)));
  f.replaceChildren(...opts(PER.from)); t.replaceChildren(...opts(PER.to));
  const n = ms.filter(inPer).length;
  $("#per-note").textContent = `${PER.from} ~ ${PER.to} (${n}개월) · 아래 기상 분석, 한전 지역 비교, 시설별 그래프·표가 이 기간으로 바뀝니다. 기준선(기온 보정 모델)과 점검 결과는 기간과 관계없이 같은 기준으로 계산합니다.`;
}
function setPeriod(from, to) {
  if (from > to) [from, to] = [to, from];
  PER = { from, to };
  try { localStorage.setItem("hantol:period", JSON.stringify(PER)); } catch (e) { /* 저장 안 됨 */ }
  renderPeriod(); renderFac(); renderWeather(); renderKepco();
}
function setupPeriod() {
  $("#per-from").addEventListener("change", e => setPeriod(e.target.value, PER.to));
  $("#per-to").addEventListener("change", e => setPeriod(PER.from, e.target.value));
  $("#per-12").addEventListener("click", () => { const r = S.recent && S.recent.length ? S.recent : S.months.slice(-12); setPeriod(r[0], r[r.length - 1]); });
  $("#per-all").addEventListener("click", () => setPeriod(S.months[0], S.months[S.months.length - 1]));
}

async function renderWeather() {
  const box = $("#wx");
  if (!box) return;
  let r;
  try { r = await api("GET", "/api/weather"); } catch (e) { box.replaceChildren(); return; }
  const M = r.months;
  if (!M.length) { box.replaceChildren(); return; }
  const api_ = M.filter(m => m.api && inPer(m.ym)), mid = r.cdd_base;
  const byYm = Object.fromEntries(M.map(m => [m.ym, m]));
  const rec = M.filter(m => inPer(m.ym));
  const recP = rec.filter(m => byYm[ymAdd(m.ym, -12)]), base = recP.map(m => byYm[ymAdd(m.ym, -12)]);  // 전년 같은 달이 있는 달끼리만 비교
  const sum = (a, k) => a.reduce((s, m) => s + m[k], 0);
  const chg = (a, b) => (b ? (a - b) / b * 100 : null);
  const avgT = a => a.length ? a.reduce((s, m) => s + m.t, 0) / a.length : null;
  const days = rec.flatMap(m => Object.entries(m.days));
  const hot = days.filter(([, v]) => v >= 25).length, cold = days.filter(([, v]) => v <= 0).length;
  const mx = days.length ? days.reduce((a, b) => (b[1] > a[1] ? b : a)) : null, mn = days.length ? days.reduce((a, b) => (b[1] < a[1] ? b : a)) : null;
  const tR = avgT(rec), tB = base.length === rec.length ? avgT(base) : null;
  const src = api_.length ? `기상청 지상관측(ASOS) 일자료 · 지점 ${r.kma.stn || ""} · ${api_.length}개월` : "넣은 기온 파일(월평균) · 기온 자동 수집을 켜면 일별 분석이 나옵니다";
  const stat = (b, l, color) => el("div", { class: "stat" }, el("b", { style: color ? { color } : {} }, b), el("span", { class: "lbl" }, l));
  const dpct = v => (v == null ? "-" : pct(v));
  const kpis = el("div", { class: "kpis", style: { marginTop: "14px" } },
    stat(tR == null ? "-" : tR.toFixed(1) + "℃", tB == null ? "선택 기간 평균기온" : `선택 기간 평균기온 (전년 같은 달 ${tB.toFixed(1)}℃)`),
    stat(dpct(chg(sum(recP, "cdd"), sum(base, "cdd"))), `냉방도일 전년 대비 (${num(sum(rec, "cdd"))})`, chg(sum(recP, "cdd"), sum(base, "cdd")) > 0 ? "var(--bad)" : null),
    stat(dpct(chg(sum(recP, "hdd"), sum(base, "hdd"))), `난방도일 전년 대비 (${num(sum(rec, "hdd"))})`, chg(sum(recP, "hdd"), sum(base, "hdd")) > 0 ? "var(--bad)" : null),
    days.length ? stat(`${hot}일 · ${cold}일`, "일평균 25℃ 이상 · 0℃ 이하 (선택 기간)") : null,
    mx ? stat(`${mx[1]} · ${mn[1]}℃`, `가장 더운 날(${+mx[0].slice(5, 7)}.${+mx[0].slice(8)}.) · 추운 날(${+mn[0].slice(5, 7)}.${+mn[0].slice(8)}.) 일평균`) : null);

  const panels = [];
  if (api_.length) panels.push(el("div", { class: "panel" }, el("h3", {}, "날마다의 기온"),
    el("p", { class: "small muted" }, `칸 하나가 하루입니다. ${mid}℃(냉난방이 필요 없는 기준 온도)에서 멀수록 진하게, 더우면 주황, 추우면 파랑입니다. 칸에 마우스를 올리면 날짜와 기온이 보입니다.`),
    heatmap(api_, mid)));
  panels.push(el("div", { class: "grid2" },
    el("div", { class: "panel" }, el("h3", {}, "달별 기온, 전년 같은 달과의 차이"),
      el("p", { class: "small muted" }, "위로 솟으면 전년보다 더웠던 달, 아래로 내려가면 추웠던 달입니다(선택 기간)."), anomalyChart(rec, byYm)),
    el("div", { class: "panel" }, el("h3", {}, "기온과 전기 사용량"),
      el("p", { class: "small muted" }, `점 하나가 한 달(전체 시설 합계)입니다. ${mid}℃에서 멀어질수록 냉난방 때문에 사용량이 늘어납니다.`), scatterChart(M.filter(m => inPer(m.ym)), r, mid))));
  box.replaceChildren(el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "기상 분석"), el("span", { class: "small muted" }, src)), kpis), ...panels);
}

function heatmap(ms, mid) {
  const cw = 30, ch = 18, L = 52, T = 22, W = L + 31 * cw + 10, H = T + ms.length * ch + 40;
  let g = "";
  for (let d = 1; d <= 31; d += (d === 1 ? 4 : 5)) g += `<text x="${L + (d - 1) * cw + cw / 2}" y="${T - 7}" text-anchor="middle">${d}일</text>`;
  ms.forEach((m, i) => {
    const y = T + i * ch;
    g += `<text x="${L - 8}" y="${y + ch - 5}" text-anchor="end">${ymLabel(m.ym)}</text>`;
    Object.entries(m.days).forEach(([d, v]) => {
      const x = L + (+d.slice(8) - 1) * cw;
      g += `<rect x="${x + 1}" y="${y + 1}" width="${cw - 2}" height="${ch - 2}" rx="3" fill="${tempColor(v, mid)}"><title>${d} 일평균 ${v}℃</title></rect>`;
    });
  });
  const ly = T + ms.length * ch + 16, lw = 240, lx = W - lw - 10;
  g += `<defs><linearGradient id="wxg"><stop offset="0" stop-color="${tempColor(mid - 14, mid)}"/><stop offset=".5" stop-color="${tempColor(mid, mid)}"/><stop offset="1" stop-color="${tempColor(mid + 14, mid)}"/></linearGradient></defs>`;
  g += `<rect x="${lx}" y="${ly}" width="${lw}" height="10" rx="5" fill="url(#wxg)"/><text x="${lx - 8}" y="${ly + 9}" text-anchor="end">${mid - 14}℃ 이하</text><text x="${lx + lw / 2}" y="${ly + 24}" text-anchor="middle">${mid}℃</text><text x="${lx + lw}" y="${ly + 24}" text-anchor="end">${mid + 14}℃ 이상</text>`;
  return el("div", { class: "wx-scroll" }, svgBox(W, H, g));
}

function anomalyChart(rec, byYm) {
  const rows = rec.map(m => ({ ym: m.ym, t: m.t, p: byYm[ymAdd(m.ym, -12)] })).filter(x => x.p).map(x => ({ ...x, d: +(x.t - x.p.t).toFixed(1) }));
  if (!rows.length) return el("div", { class: "empty small" }, "전년 같은 달 기온이 없어 비교할 수 없습니다.");
  const W = 520, H = 200, L = 34, T = 12, B = 24, R = 8, lim = Math.max(1, ...rows.map(x => Math.abs(x.d))) * 1.15;
  const y = v => T + (H - T - B) * (1 - (v + lim) / (2 * lim)), bw = (W - L - R) / rows.length;
  let g = `<line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line-strong)"/><text x="${L - 6}" y="${y(lim) + 8}" text-anchor="end">+${lim.toFixed(0)}</text><text x="${L - 6}" y="${y(-lim)}" text-anchor="end">-${lim.toFixed(0)}</text><text x="${L - 6}" y="${y(0) + 4}" text-anchor="end">0</text>`;
  rows.forEach((x, i) => {
    const bx = L + i * bw + bw * 0.18, w = bw * 0.64, y0 = y(0), y1 = y(x.d);
    g += `<rect x="${bx}" y="${Math.min(y0, y1)}" width="${w}" height="${Math.max(2, Math.abs(y1 - y0))}" rx="3" fill="${x.d >= 0 ? "rgb(" + WWARM + ")" : "rgb(" + WCOOL + ")"}"><title>${x.ym} ${x.t}℃ (전년 ${x.p.t}℃, ${x.d >= 0 ? "+" : ""}${x.d}℃)</title></rect>`;
    if (i % 2 === 0 || rows.length <= 6) g += `<text x="${bx + w / 2}" y="${H - 6}" text-anchor="middle">${ymLabel(x.ym)}</text>`;
  });
  const big = rows.reduce((a, b) => (Math.abs(b.d) > Math.abs(a.d) ? b : a));
  g += `<text x="${L + rows.indexOf(big) * bw + bw / 2}" y="${big.d >= 0 ? y(big.d) - 5 : y(big.d) + 13}" text-anchor="middle" style="font-weight:600;fill:var(--ink)">${big.d >= 0 ? "+" : ""}${big.d}℃</text>`;
  return svgBox(W, H, g);
}

function scatterChart(M, r, mid) {
  const pts = M.filter(m => m.kwh > 0);
  if (pts.length < 3) return el("div", { class: "empty small" }, "전기 사용량 자료가 부족합니다.");
  const W = 520, H = 200, L = 50, T = 12, B = 26, R = 10;
  const tx = pts.map(p => p.t), ky = pts.map(p => p.kwh);
  const x0 = Math.floor(Math.min(...tx) - 1), x1 = Math.ceil(Math.max(...tx) + 1), y0 = Math.min(...ky) * 0.9, y1 = Math.max(...ky) * 1.05;
  const x = v => L + (W - L - R) * (v - x0) / (x1 - x0), y = v => T + (H - T - B) * (1 - (v - y0) / (y1 - y0));
  let g = `<line x1="${x(mid)}" x2="${x(mid)}" y1="${T}" y2="${H - B}" stroke="var(--line-strong)" stroke-dasharray="3 3"/><text x="${x(mid) + 4}" y="${T + 10}">${mid}℃</text>`;
  g += `<text x="${L}" y="${H - 6}">${x0}℃</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${x1}℃</text><text x="${L - 6}" y="${y(y1) + 8}" text-anchor="end">${Math.round(y1 / 1000)}</text><text x="${L - 6}" y="${y(y0)}" text-anchor="end">${Math.round(y0 / 1000)}</text><text x="${L - 6}" y="${T + 26}" text-anchor="end" style="font-size:10px">MWh</text>`;
  pts.forEach(p => {
    const recent = r.recent.includes(p.ym);
    g += `<circle cx="${x(p.t)}" cy="${y(p.kwh)}" r="5.5" fill="${recent ? "var(--accent-strong)" : "#a9a39b"}" stroke="#fff" stroke-width="2"><title>${p.ym} · ${p.t}℃ · ${num(p.kwh)} kWh${p.api ? " (기상청)" : ""}</title></circle>`;
  });
  const s = svgBox(W, H, g);
  return el("div", {}, s, el("div", { class: "small muted wx-legend" },
    el("span", {}, el("i", { style: { background: "var(--accent-strong)" } }), "최근 12개월"), el("span", {}, el("i", { style: { background: "#a9a39b" } }), "그 전")));
}

// ------------------------------------------------------------ 예산 입력(연도별 본예산·추경·전용)
let BIN_FY = null;
const nfmt = v => (v == null || v === "" ? "" : Number(v).toLocaleString("ko-KR"));
const nparse = v => Number(String(v).replace(/[^\d-]/g, "")) || 0;

async function renderBudgetInput() {
  const box = $("#bud-input");
  if (!box) return;
  const d = await api("GET", "/api/budget/input" + (BIN_FY ? "?fy=" + BIN_FY : ""));
  BIN_FY = d.fy;
  const rows = d.rows.map(r => ({ ...r }));
  const tbody = el("tbody");
  const totalCell = el("td", { colspan: 2 }), totals = [0, 1, 2, 3, 4].map(() => el("td", { class: "num" }));
  const recalc = () => {
    const k = ["main", "supp1", "supp2", "transfer"];
    k.forEach((f, i) => { totals[i].textContent = nfmt(rows.reduce((s, r) => s + (r[f] || 0), 0)); });
    totals[4].replaceChildren(el("b", {}, nfmt(rows.reduce((s, r) => s + r.main + r.supp1 + r.supp2 + r.transfer, 0))));
    rows.forEach(r => r._cur && (r._cur.textContent = nfmt(r.main + r.supp1 + r.supp2 + r.transfer)));
  };
  const money = (r, f) => el("input", { class: "num-in", inputmode: "numeric", value: nfmt(r[f]), "aria-label": f,
    oninput: e => { r[f] = nparse(e.target.value); recalc(); }, onblur: e => { e.target.value = nfmt(r[f]); } });
  const draw = () => {
    tbody.replaceChildren(...rows.map((r, i) => {
      r._cur = el("td", { class: "num" });
      const sugg = d.suggest[`${r.fac}|${r.item}`];
      return el("tr", {},
        el("td", {}, r.fac), el("td", {}, r.item),
        el("td", {}, money(r, "main"), sugg != null ? el("div", { class: "small muted" }, "앱 편성안 " + man(sugg)) : null),
        el("td", {}, money(r, "supp1")), el("td", {}, money(r, "supp2")), el("td", {}, money(r, "transfer")), r._cur,
        el("td", {}, el("input", { value: r.note || "", style: { width: "110px" }, oninput: e => { r.note = e.target.value; } })),
        el("td", {}, el("button", { class: "icon-btn", title: "이 줄 빼기", onclick: () => { rows.splice(i, 1); draw(); } }, "×")));
    }));
    recalc();
  };
  const facSel = el("select", {}, ...d.facilities.map(f => el("option", { value: f }, f)));
  const itSel = el("select", {}, ...d.items.map(f => el("option", { value: f }, f)));
  const add = el("button", { onclick: () => {
    if (rows.some(r => r.fac === facSel.value && r.item === itSel.value)) { toast("이미 있는 시설·과목입니다", true); return; }
    rows.push({ fac: facSel.value, item: itSel.value, main: 0, supp1: 0, supp2: 0, transfer: 0, note: "" }); draw();
  } }, "줄 추가");
  const save = el("button", { class: "primary", onclick: async () => {
    busy(save, true, "저장 중");
    try { await api("POST", "/api/budget/input", { fy: d.fy, rows: rows.map(({ _cur, manual, ...r }) => r) }); toast(`${d.fy}년 예산을 저장하고 다시 계산했습니다`); await load(); }
    finally { busy(save, false); }
  } }, "저장하고 다시 계산");
  const reset = d.source === "화면 입력" ? el("button", { onclick: async () => {
    await api("POST", "/api/budget/input", { fy: d.fy, reset: true }); toast("화면 입력을 지우고 엑셀 예산서로 되돌렸습니다"); await load();
  } }, "엑셀 예산서로 되돌리기") : null;
  const yearBtn = y => el("button", { class: "seg" + (y === d.fy ? " on" : ""), onclick: () => { BIN_FY = y; renderBudgetInput(); } }, y === d.years[0] ? `${y}년(올해)` : `${y}년(내년)`);
  box.replaceChildren(el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "예산 입력"), el("div", { class: "segs" }, ...d.years.map(yearBtn))),
    el("p", { class: "small muted", style: { marginTop: "8px" } },
      `지금 쓰는 ${d.fy}년 예산: `, el("b", {}, d.source), d.saved ? ` (${d.saved} 저장)` : "",
      d.fy === d.years[1] ? " · 내년 예산을 넣으면 '예산 편성 근거'에서 앱 편성안과 나란히 비교합니다. 비어 있으면 앱 편성안을 미리 채워 둡니다(저장 전에는 반영 안 됨)." : " · 추경이 잡히면 해당 칸만 고쳐 저장하세요. 저장한 연도는 엑셀 예산서보다 화면 입력이 먼저입니다."),
    el("div", { class: "tbl-scroll" }, el("table", { class: "t bud-in" },
      el("thead", {}, el("tr", {}, ["시설", "과목", "본예산(원)", "제1회 추경", "제2회 추경", "전용(±)", "예산현액", "비고", ""].map(h => el("th", {}, h)))),
      tbody, el("tfoot", {}, el("tr", {}, totalCell, ...totals, el("td", { colspan: 2 }))))),
    el("div", { class: "row", style: { marginTop: "12px", gap: "8px", flexWrap: "wrap" } }, facSel, itSel, add, el("span", { style: { flex: 1 } }),
      el("button", { onclick: () => download("/api/budget/template", { fy: d.fy }) }, "예산서 양식(엑셀)"), reset, save)));
  totalCell.textContent = "합계";
  draw();
}

// ------------------------------------------------------------ 시설별 자료(시설을 골라 고지서 넣기 + 달별 채움 표)
const COV = { bill: ["고지서", "var(--accent-strong)"], ocr: ["고지서(글자 인식)", "#d9a441"], table: ["엑셀 내역", "#a9a39b"] };
async function renderFacData(box, up, bills) {
  let c;
  try { c = await api("GET", "/api/coverage"); } catch (e) { box.replaceChildren(); return; }
  const facs = c.rows.map(r => r.fac);
  if (!FAC_UP || !facs.includes(FAC_UP)) FAC_UP = facs[0] || "";
  const sel = el("select", { onchange: e => { FAC_UP = e.target.value; renderFacData(box, up, bills); } }, ...facs.map(f => el("option", { value: f, selected: f === FAC_UP }, f)));
  const newFac = el("input", { placeholder: "새 시설 이름", style: { width: "150px" } });
  const addFac = el("button", { onclick: () => { const v = newFac.value.trim(); if (!v) return; FAC_UP = v; if (!facs.includes(v)) { sel.append(el("option", { value: v, selected: true }, v)); } sel.value = v; toast(`'${v}'을(를) 골랐습니다. 이 시설의 고지서를 넣으면 시설이 추가됩니다`); } }, "추가");
  const ocrLine = c.ocr.ok ? el("span", { class: "tag ok" }, "스캔본 글자 인식 사용 가능(Tesseract OCR)") : el("span", { class: "tag gray", title: c.ocr.note }, "스캔본 글자 인식 꺼짐: " + c.ocr.note);
  const cell = (st, ym, fac, kind) => el("span", { class: "cov" + (st ? "" : " none"), style: st ? { background: COV[st][1] } : {}, title: `${fac} ${kind === "elec" ? "전기" : "가스"} ${ym}: ${st ? COV[st][0] : "자료 없음"}` });
  const grid = el("div", { class: "cov-grid" },
    el("div", { class: "cov-row cov-head" }, el("span", { class: "cov-name" }), el("span", { class: "cov-k" }),
      ...c.months.map((m, i) => el("span", { class: "cov-m" }, i % 3 === 0 ? ymLabel(m) : ""))),
    ...c.rows.flatMap(r => ["elec", "gas"].map((k, j) => el("div", { class: "cov-row" + (r.fac === FAC_UP ? " on" : ""), onclick: () => { FAC_UP = r.fac; renderFacData(box, up, bills); } },
      el("span", { class: "cov-name" }, j === 0 ? r.fac : ""), el("span", { class: "cov-k" }, k === "elec" ? "전기" : "가스"),
      ...r[k].map((st, i) => cell(st, c.months[i], r.fac, k))))));
  const mine = c.rows.find(r => r.fac === FAC_UP);
  box.replaceChildren(
    el("h3", {}, "② 시설별 자료"),
    el("p", { class: "small muted" }, "시설을 고르고 그 시설의 고지서 PDF(스캔본 포함)를 넣으세요. 고지서의 상호가 시설 이름과 달라도 고른 시설로 들어갑니다. 아래 표에서 달마다 자료가 채워졌는지 확인합니다."),
    el("div", { class: "row", style: { gap: "8px", flexWrap: "wrap", margin: "8px 0 12px" } }, el("b", {}, "시설"), sel, el("span", { class: "small muted" }, "또는"), newFac, addFac, el("span", { style: { flex: 1 } }), ocrLine),
    dropZone(`${FAC_UP || "시설"}의 고지서 PDF·엑셀을 여기에 끌어다 놓거나`, f => up(f, FAC_UP)),
    mine && mine.files.length ? el("p", { class: "small muted", style: { marginTop: "8px" } }, `${FAC_UP}에 넣은 파일: ${mine.files.map(shortName).join(", ")}`) : null,
    el("div", { class: "row", style: { marginTop: "12px" } }, bills),
    el("h3", { style: { marginTop: "22px" } }, "달별 자료 채움"),
    el("div", { class: "cov-legend small muted" }, ...Object.values(COV).map(([t, col]) => el("span", {}, el("i", { style: { background: col } }), t)), el("span", {}, el("i", { class: "none" }), "자료 없음")),
    el("div", { class: "tbl-scroll" }, grid));
}

// ------------------------------------------------------------ 절감 조치 추천
const REC_LV = { bad: ["먼저", "bad"], warn: ["검토", "warn"], gray: ["참고", "gray"] };
async function renderRecommend(box) {
  let r;
  try { r = await api("GET", "/api/recommend"); } catch (e) { box.replaceChildren(); return; }
  const aiBox = el("div", { class: "ans", style: { marginTop: "12px" } }, r.ai ? r.ai.text : "");
  const aiMode = el("div", { class: "small muted" }, r.ai ? r.ai.mode + (r.ai.dropped ? " · " + r.ai.dropped : "") : "");
  const aiBtn = el("button", { class: "primary", onclick: async () => {
    busy(aiBtn, true, "추천 이유 쓰는 중");
    try { const x = await api("POST", "/api/recommend/ai"); aiBox.textContent = x.text; aiMode.textContent = x.mode + (x.dropped ? " · " + x.dropped : ""); }
    finally { busy(aiBtn, false); }
  } }, S.llm ? "AI 추천 이유 쓰기" : "추천 순서 요약");
  const card = x => el("div", { class: "rec-card" + (x.registered ? " done" : "") },
    el("div", { class: "row", style: { gap: "8px", alignItems: "flex-start" } },
      el("span", { class: "tag " + REC_LV[x.level][1] }, x.registered ? "등록함" : REC_LV[x.level][0]),
      el("div", { style: { flex: 1 } }, el("b", {}, `${x.fac} · ${x.title}`), el("div", { class: "small", style: { marginTop: "4px" } }, x.why)),
      x.save_won ? el("div", { class: "rec-save" }, el("b", {}, man(x.save_won)), el("span", { class: "small muted" }, x.key === "LED" ? "연간(추정)" : "줄일 수 있는 금액(추정)")) : null),
    el("div", { class: "small muted", style: { marginTop: "6px" } }, x.formula ? "계산: " + x.formula : ""),
    el("div", { class: "row", style: { marginTop: "8px", gap: "6px", flexWrap: "wrap" } },
      x.basis ? basisChip(x.basis) : null,
      x.official ? el("span", { class: "chip", title: x.official.text + " · " + x.official.note, onclick: () => modal(x.official.name, el("p", {}, x.official.text), el("p", { class: "small muted" }, x.official.note)) },
        x.official.name.split("(")[0] + (x.official.verified ? "" : " · 원문 확인 필요")) : null,
      x.ext ? el("span", { class: "chip ext" }, x.ext.label) : null,
      el("span", { style: { flex: 1 } }),
      x.registered ? null : el("button", { class: "small", onclick: async () => {
        await api("POST", "/api/actions", { fac: x.fac, title: x.title, kind: x.key, finding: x.finding });
        toast("조치로 등록했습니다"); await load();
      } }, "조치 등록")));
  box.replaceChildren(el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "추천 절감 조치"), aiBtn),
    el("p", { class: "small muted" }, "점검 결과, 이미 해 본 조치의 실측 효과, 지침 조문을 묶어 시설별로 할 일을 골랐습니다. 효과가 크고 바로 할 수 있는 것부터 보여 줍니다."),
    el("div", { class: "kpis", style: { marginTop: "10px", gridTemplateColumns: "repeat(3,1fr)" } },
      el("div", { class: "stat hl" }, el("b", {}, man(r.totals.save_won)), el("span", { class: "lbl" }, "추천대로 하면 줄일 수 있는 금액(원, 추정·LED 제외)")),
      el("div", { class: "stat" }, el("b", {}, r.items.filter(x => !x.registered).length + "건"), el("span", { class: "lbl" }, "아직 등록하지 않은 추천")),
      el("div", { class: "stat" }, el("b", {}, r.totals.target_gap_kwh != null ? num(r.totals.target_gap_kwh) : "-"), el("span", { class: "lbl" }, "감축 목표까지 남은 전기(kWh)"))),
    aiBox, aiMode,
    el("div", { class: "rec-list" }, ...r.items.map(card)),
    el("p", { class: "small muted" }, r.note)));
}

// ---------------------------------------------------------------- 한전 지역 비교(전력데이터 개방포털)
async function renderKepco() {
  const box = $("#kepco");
  if (!box) return;
  let r;
  if (!box.firstChild) box.replaceChildren(el("div", { class: "panel" }, el("h2", {}, "한전 지역 비교"), el("div", { class: "empty small" }, "한전 전력데이터 개방포털에서 자료를 받는 중입니다(처음에는 30초쯤 걸릴 수 있음)…")));
  try { r = await api("GET", "/api/kepco" + (PER ? `?from=${PER.from}&to=${PER.to}` : "")); } catch (e) {
    box.replaceChildren(el("div", { class: "panel" }, el("h2", {}, "한전 지역 비교"), el("div", { class: "small", style: { color: "var(--warn)" } }, "한전 자료를 불러오지 못했습니다: " + (e.message || e))));
    return;
  }
  const i = r.info, c = r.cmp;
  const head = el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "한전 지역 비교"),
    i.key ? el("button", { class: "small", onclick: async ev => { busy(ev.target, true, "받는 중"); try { await api("POST", "/api/kepco"); } finally { renderKepco(); } } }, "다시 받기") : null);
  const lead = el("p", { class: "small muted" }, "한국전력공사 전력데이터 개방포털의 계약종별 전력사용량(지역 집계 통계)과 견줍니다. 시설 단위 자료는 포털에 없어 같은 지역·같은 계약종별 평균을 기준으로 씁니다.");
  if (!i.key) {
    box.replaceChildren(el("div", { class: "panel" }, head, lead,
      el("div", { class: "empty small" }, "한전 전력데이터 개방포털 API 인증키가 없어 표시하지 않습니다. 포털에서 'API 인증키 신청'으로 받은 키를 환경변수 KEPCO_API_KEY에 넣으세요(지역: KEPCO_METRO 시도 코드, 기본 11 서울 · KEPCO_CITY 시군구, 기본 도봉구).")));
    return;
  }
  const err = i.error ? el("div", { class: "small", style: { color: "var(--warn)", marginTop: "6px" } }, i.error + (c ? " 받아 둔 달만 씁니다." : "")) : null;
  if (!c) { box.replaceChildren(el("div", { class: "panel" }, head, lead, err || el("div", { class: "empty small" }, "자료가 있는 달의 한전 통계가 아직 없습니다."))); return; }
  const gapColor = v => (v == null ? null : v >= 10 ? "var(--bad)" : v <= -10 ? "var(--ok)" : null);
  const tbl = el("table", { class: "t" },
    el("thead", {}, el("tr", {}, ...["시설", "계약종별", "비교 기간", "우리 단가", "지역 평균 단가", "단가 차이", "우리 전년 대비", "지역 전년 대비", "차이"].map(h => el("th", {}, h)))),
    el("tbody", {}, ...c.facilities.map(f => el("tr", {},
      el("td", {}, f.fac), el("td", { title: f.tariff ? "고지서 계약종별: " + f.tariff : "고지서에 계약종별이 없어 일반용으로 봄" }, f.cntr + (f.tariff ? "" : "*")),
      el("td", {}, `${f.from}~${f.to} (${f.months}개월)`),
      el("td", {}, f.our_unit == null ? "-" : f.our_unit + "원/kWh"), el("td", {}, f.region_unit == null ? "-" : f.region_unit + "원/kWh"),
      el("td", { style: { color: gapColor(f.unit_gap_pct) } }, f.unit_gap_pct == null ? "-" : pct(f.unit_gap_pct)),
      el("td", {}, f.our_yoy == null ? "-" : pct(f.our_yoy)), el("td", {}, f.region_yoy == null ? "-" : pct(f.region_yoy)),
      el("td", { style: { color: gapColor(f.yoy_gap) } }, f.yoy_gap == null ? "-" : (f.yoy_gap > 0 ? "+" : "") + f.yoy_gap.toFixed(1) + "%p")))));
  const L = c.latest;
  const lt = el("table", { class: "t" },
    el("thead", {}, el("tr", {}, ...["계약종별", "고객호수", "사용량(kWh)", "전기요금", "평균판매단가"].map(h => el("th", {}, h)))),
    el("tbody", {}, ...L.rows.map(x => el("tr", {}, el("td", {}, x.cntr), el("td", {}, num(x.cust, "호")), el("td", {}, num(x.kwh)), el("td", {}, won(x.won)), el("td", {}, x.unit == null ? "-" : x.unit + "원/kWh")))));
  const s = r.solar;
  box.replaceChildren(el("div", { class: "panel" }, head, lead, err,
    el("div", { class: "small muted", style: { margin: "6px 0 10px" } }, `${c.area} · ${c.range[0]}~${c.range[1]} ${c.months}개월 · 출처 ${i.src}`),
    el("h3", {}, "전년 같은 달 대비 전기 사용 증감: 우리 시설 전체와 " + c.area + " 일반용"),
    yoyChart(c.monthly.filter(m => m.region_yoy != null && m.our_yoy != null)),
    el("h3", { style: { marginTop: "16px" } }, "시설별 단가·증감 비교"),
    el("div", { class: "tbl-scroll" }, tbl),
    el("p", { class: "small muted" }, "단가 = 전기요금 ÷ 사용량(우리는 고지서, 지역은 한전 평균판매단가). 고압·저압과 계약 규모에 따라 단가가 다르므로 차이는 점검 실마리로만 씁니다. * 고지서에 계약종별이 없어 일반용으로 봄."),
    el("h3", { style: { marginTop: "16px" } }, `${c.area} 계약종별 현황 (${L.ym})`),
    el("div", { class: "tbl-scroll" }, lt),
    s ? el("p", { class: "small", style: { marginTop: "12px" } }, `태양광 한전 전력거래계약(${s.year}년): ${s.city} ${s.cnt == null ? "-" : num(s.cnt, "개")}·${s.kw == null ? "-" : num(s.kw, " kW")} / ${s.metro} 전체 ${num(s.area_cnt, "개")}·${num(s.area_kw, " kW")}`) : null));
}

function yoyChart(rows) {
  if (!rows.length) return el("div", { class: "empty small" }, "전년 같은 달 자료가 함께 있는 달이 없어 비교할 수 없습니다.");
  const W = 1000, H = 220, L = 44, T = 14, B = 26, R = 8;
  const lim = Math.max(5, ...rows.flatMap(x => [Math.abs(x.our_yoy), Math.abs(x.region_yoy)])) * 1.15;
  const y = v => T + (H - T - B) * (1 - (v + lim) / (2 * lim)), bw = (W - L - R) / rows.length;
  let g = `<line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line-strong)"/><text x="${L - 6}" y="${y(lim) + 8}" text-anchor="end">+${lim.toFixed(0)}%</text><text x="${L - 6}" y="${y(-lim)}" text-anchor="end">-${lim.toFixed(0)}%</text>`;
  rows.forEach((x, i) => {
    const w = bw * 0.32, y0 = y(0);
    [[x.our_yoy, "var(--accent-strong)", "우리 시설 전체", 0.16], [x.region_yoy, "#7d8ea8", "지역 일반용", 0.5]].forEach(([v, col, nm, off]) => {
      const y1 = y(v);
      g += `<rect x="${L + i * bw + bw * off}" y="${Math.min(y0, y1)}" width="${w}" height="${Math.max(2, Math.abs(y1 - y0))}" rx="2" fill="${col}"><title>${x.ym} ${nm} 전년 대비 ${v > 0 ? "+" : ""}${v}%</title></rect>`;
    });
    if (i % 2 === 0 || rows.length <= 6) g += `<text x="${L + i * bw + bw / 2}" y="${H - 6}" text-anchor="middle">${ymLabel(x.ym)}</text>`;
  });
  return el("div", {}, svgBox(W, H, g), el("div", { class: "small muted wx-legend" },
    el("span", {}, el("i", { style: { background: "var(--accent-strong)" } }), "우리 시설 전체"), el("span", {}, el("i", { style: { background: "#7d8ea8" } }), "지역 일반용(한전 통계)")));
}
