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
  renderHome(); renderFind(); renderFacSel(); renderAct(); renderReport(); renderData();
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
    svg += `<rect x="${x0}" y="${y + 4}" width="${w}" height="18" rx="4" fill="${v > 0 ? "#ff3b30" : v <= -t.target_pct ? "#30d158" : "#6e9be0"}"/>`;
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
  const rows = r.rows;
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
    el("span", {}, el("i", { class: "dot", style: { background: "#6e9be0", borderRadius: "3px" } }), "실제(최근 12개월)"),
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
const EX = ["체육센터 8월 전기 왜 늘었어?", "감축 목표 달성할 수 있어?", "얼마나 아낄 수 있어?", "역률 문제 있는 곳?", "면적당 가장 많이 쓰는 시설은?", "주차장 기본요금 줄일 수 있어?", "문화예술회관 2월 왜 이렇게 많이 썼어?"];
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
function renderData() {
  const box = $("#tab-data"), d = S.data;
  box.innerHTML = "";
  const drop = el("div", { class: "drop" }, "엑셀(전력·가스 내역, 시설 현황, 기온, 조치 이력), 고지서 PDF, 지침(한글)을 여기에 끌어다 놓거나 ",
    el("label", { style: { color: "var(--brand)", cursor: "pointer" } }, "파일 선택", el("input", { type: "file", multiple: true, hidden: true, onchange: e => up([...e.target.files]) })));
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", async e => { e.preventDefault(); drop.classList.remove("over"); up(await filesFromDrop(e)); });
  async function up(files) {
    if (!files.length) return;
    toast("읽는 중…");
    await load(await api("POST", "/api/upload", { files: await readFiles(files) }));
    toast("반영했습니다");
  }
  const bills = el("button", { class: "primary", onclick: async () => {
    busy(bills, true, "고지서 읽는 중");
    try { await load(await api("POST", "/api/sample", { with_bills: true })); toast("9월 고지서 5장을 읽어 반영했습니다"); }
    finally { busy(bills, false); }
  } }, "시연: 9월 고지서 PDF 5장 넣기");
  box.append(el("div", { class: "panel" }, el("h2", {}, "자료 넣기"),
    el("p", { class: "small muted" }, "표는 열 이름으로 알아봅니다(열 순서가 달라도 됨). 원본 파일은 읽기만 하고, 이 PC의 작업 폴더(data)에 복사본을 둡니다."),
    drop,
    el("div", { class: "row", style: { marginTop: "12px" } }, bills,
      el("button", { onclick: async () => { await load(await api("POST", "/api/sample", { reset: true })); toast("모의자료(8월까지)로 되돌렸습니다"); } }, "모의자료 처음 상태로"),
      el("button", { class: "danger", onclick: async () => { await load(await api("POST", "/api/reset")); } }, "자료 모두 비우기"))));
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
        el("td", {}, b.missing.length ? el("span", { class: "tag bad" }, "못 읽음: " + b.missing.join(", ")) : el("span", { class: "tag ok" }, b.by_model.length ? "규칙+모델" : "규칙")))))));
  box.append(el("div", { class: "panel" }, el("h3", {}, "점검 기준"),
    el("p", { class: "small muted" }, "기준값은 넣은 지침의 조문에서 읽습니다. 지침을 고쳐 다시 넣으면 기준이 바뀝니다. 지침에 없는 값은 기본값을 씁니다."),
    el("table", { class: "t" }, el("tr", {}, ["기준", "값", "근거"].map(h => el("th", {}, h))),
      S.overview.rules.map(r => el("tr", {}, el("td", {}, r.label), el("td", {}, r.value == null ? "-" : r.value), el("td", {}, r.basis ? basisChip(r.basis) : el("span", { class: "tag gray" }, "기본값"))))),
    el("p", { class: "small muted" }, `전력 온실가스 배출계수 ${S.overview.co2_factor} kgCO2eq/kWh, 요금 단가·휴관 사용 비율·계약전력 여유율은 config.json의 energy 항목에서 바꿉니다.`)));
}

async function renderLog() {
  const r = await api("GET", "/api/log");
  $("#tab-log").replaceChildren(el("div", { class: "panel" }, el("h2", {}, "작업 기록"),
    r.items.length ? el("table", { class: "t" }, el("tr", {}, ["시각", "작업", "내용"].map(h => el("th", {}, h))),
      r.items.map(i => el("tr", {}, el("td", {}, i.at), el("td", {}, i.action), el("td", {}, i.detail || "")))) : el("div", { class: "empty" }, "기록이 없습니다.")));
}

document.addEventListener("DOMContentLoaded", async () => {
  GO = setupTabs(id => { if (id === "log") renderLog(); if (id === "fac" && S) renderFac(); });
  $("#fac-sel").addEventListener("change", renderFac);
  $("#kind-sel").addEventListener("change", renderFac);
  $("#ask-btn").addEventListener("click", () => ask());
  $("#q").addEventListener("keydown", e => { if (e.key === "Enter") ask(); });
  $("#ask-ex").append(...EX.map(x => el("span", { class: "chip", onclick: () => ask(x) }, x)));
  await load();
  if (!$("#tab-fac").hidden) renderFac();
});
