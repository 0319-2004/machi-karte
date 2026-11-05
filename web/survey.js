"use strict";

// まちカルテ 現地調査（スマホのブラウザ前提）。
// 1) 地図で調査対象をタップ/範囲選択 → 2) 近い順/優先度順でグループ化した調査プラン
// → 3) 1棟ずつ入力（既存流用）。
// 位置情報は「近い順」を押したときだけ求め、表示と並び替えにだけ使う（サーバーに送らない）。
// 位置が取れなくても全機能が使える。入力の下書きは localStorage に保存する。

const LS = {
  selected: "mk.survey.selected",
  operator: "mk.survey.operator",
  draft: (id) => `mk.survey.draft.${id}`,
};
const PHOTO_MAX = 1280;
const GSI = {
  version: 8,
  sources: { gsi: { type: "raster",
    tiles: ["https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"],
    tileSize: 256, attribution: "国土地理院（地理院タイル）" } },
  layers: [{ id: "gsi", type: "raster", source: "gsi" }],
};
const COLOR_TGT = "#e64a19", COLOR_SEL = "#2e7d32";
const GROUP_COLORS = ["#1565c0", "#6a1b9a", "#ef6c00", "#00838f", "#c2185b",
                      "#558b0f", "#4527a0", "#00695c"];
const GROUP_RADIUS_M = 200;

const lsGet = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };

let usageCodes = [];
let targets = [];                 // 全調査対象（優先度順、coords つき）
let targetsById = {};
let guidanceById = {};
let selected = lsGet(LS.selected, []);
let displayLimit = 100;           // 地図に出す上位N（「もっと表示」で増やす）
let targetTotal = 0;

let pickMap = null, planMap = null;
let boxMode = false;

// プラン/入力
let mode = "priority";            // priority | near
let userPos = null;               // {lon,lat} | null（近い順を押したときだけ）
let planGroups = [];              // [[id,...], ...]
let planOrder = [];               // グループを平らにした訪問順
let idx = 0;

// --- 距離 -------------------------------------------------------------------
function distM(a, b) {
  const R = 6378137, rad = Math.PI / 180;
  const dx = (b.lon - a.lon) * rad * Math.cos((a.lat + b.lat) / 2 * rad) * R;
  const dy = (b.lat - a.lat) * rad * R;
  return Math.hypot(dx, dy);
}
function fmtDist(m) {
  if (m == null) return "";
  return m < 1000 ? `約${Math.round(m / 10) * 10}m` : `約${(m / 1000).toFixed(1)}km`;
}

// --- データ読み込み ---------------------------------------------------------
async function loadAll() {
  const stats = await fetch("/api/stats").then((r) => r.json()).catch(() => null);
  targetTotal = stats ? stats.target : 0;
  // 対象の座標は /api/targets からしか取れないので、全件を一度だけ読む（点なので軽い）
  [usageCodes, targets] = await Promise.all([
    fetch("/api/usage_codes").then((r) => r.json()),
    fetch("/api/targets?limit=5000").then((r) => r.json()),
  ]);
  if (!targetTotal) targetTotal = targets.length;
  for (const t of targets) targetsById[t.id] = t;
  try {
    const un = await fetch("/api/unresolved").then((r) => r.json());
    for (const g of un.groups || []) {
      if (!g.guidance) continue;
      for (const bid of g.building_ids || []) if (!(bid in guidanceById)) guidanceById[bid] = g.guidance;
    }
  } catch {}
  document.getElementById("operator").value = lsGet(LS.operator, "") || "";
  initPickMap();
  renderPickList();
  updateSelbar();
}

// --- 1. 選択（地図が主役） --------------------------------------------------
function shownTargets() { return targets.slice(0, displayLimit); }

function pointFC(list) {
  return { type: "FeatureCollection", features: list.map((t) => ({
    type: "Feature", id: t.id,
    properties: { id: t.id },
    geometry: { type: "Point", coordinates: [t.lon, t.lat] },
  })) };
}

function initPickMap() {
  pickMap = new maplibregl.Map({
    container: "pick-map", style: GSI, center: [139.6917, 35.6896], zoom: 14,
    attributionControl: { compact: true },
  });
  pickMap.on("load", () => {
    pickMap.addSource("tg", { type: "geojson", data: pointFC(shownTargets()) });
    // 選択は feature-state ではなく paint 式で塗る（建物IDは数値化できず feature-state 不可のため）。
    // タップ判定を確保するため半径は大きめ（小さい建物でも指で押せる）。
    pickMap.addLayer({ id: "tg-dot", type: "circle", source: "tg", paint: {
      "circle-radius": selRadiusExpr(),
      "circle-color": selColorExpr(),
      "circle-stroke-width": 1.6, "circle-stroke-color": "#fff", "circle-opacity": 0.9,
    } });
    pickMap.on("click", "tg-dot", (e) => {
      const id = e.features[0].properties.id;
      toggleSelect(id); showPickInfo(id);
    });
    pickMap.on("mouseenter", "tg-dot", () => pickMap.getCanvas().style.cursor = "pointer");
    pickMap.on("mouseleave", "tg-dot", () => pickMap.getCanvas().style.cursor = "");
    updateShownCount();
  });
  setupBoxSelect();
}

function selColorExpr() {
  return ["case", ["in", ["get", "id"], ["literal", selected]], COLOR_SEL, COLOR_TGT];
}
function selRadiusExpr() {
  return ["case", ["in", ["get", "id"], ["literal", selected]], 11, 8];
}
function paintSel() {
  if (!pickMap || !pickMap.getLayer("tg-dot")) return;
  pickMap.setPaintProperty("tg-dot", "circle-color", selColorExpr());
  pickMap.setPaintProperty("tg-dot", "circle-radius", selRadiusExpr());
}

function refreshPickSource() {
  const src = pickMap && pickMap.getSource("tg");
  if (src) { src.setData(pointFC(shownTargets())); paintSel(); }
  updateShownCount();
}
function updateShownCount() {
  document.getElementById("shown-count").textContent =
    `表示 ${Math.min(displayLimit, targets.length)} / ${targetTotal}`;
  document.getElementById("more-btn").classList.toggle("hidden", displayLimit >= targets.length);
}

function toggleSelect(id) {
  const i = selected.indexOf(id);
  if (i >= 0) selected.splice(i, 1); else selected.push(id);
  lsSet(LS.selected, selected);
  paintSel();
  updateSelbar(); renderPickList();
}

function showPickInfo(id) {
  const t = targetsById[id]; if (!t) return;
  const h = t.height_m != null ? `${t.height_m.toFixed(0)}m` : "高さ不明";
  const on = selected.includes(id);
  const guide = guidanceById[id]
    ? `<div class="pi-guide">現地で確認：${guidanceById[id]}</div>` : "";
  const el = document.getElementById("pick-info");
  el.innerHTML =
    `<div class="pi-id">${id}</div>` +
    `<div class="pi-meta">${h}・推定用途 ${t.usage_label || "不明"}${t.is_new ? "・新規" : ""}</div>` +
    guide +
    `<button class="pi-toggle ${on ? "on" : ""}">${on ? "選択を解除" : "この棟を選ぶ"}</button>`;
  el.classList.remove("hidden");
  el.querySelector(".pi-toggle").addEventListener("click", () => { toggleSelect(id); showPickInfo(id); });
}

// 範囲で選択（指でドラッグ）：ドラッグ中はパンを止め、矩形内の表示中の棟を選ぶ
function setupBoxSelect() {
  const wrap = document.querySelector("#view-pick .mapwrap");
  const rect = document.getElementById("box-rect");
  let start = null;
  const pt = (ev) => { const r = wrap.getBoundingClientRect(); return { x: ev.clientX - r.left, y: ev.clientY - r.top }; };

  document.getElementById("box-toggle").addEventListener("click", (e) => {
    boxMode = !boxMode;
    e.currentTarget.classList.toggle("on", boxMode);
    if (boxMode) pickMap.dragPan.disable(); else pickMap.dragPan.enable();
  });

  wrap.addEventListener("pointerdown", (ev) => {
    if (!boxMode) return;
    ev.preventDefault(); start = pt(ev);
    rect.style.display = "block";
    rect.style.left = start.x + "px"; rect.style.top = start.y + "px";
    rect.style.width = "0px"; rect.style.height = "0px";
  });
  wrap.addEventListener("pointermove", (ev) => {
    if (!boxMode || !start) return;
    const p = pt(ev);
    rect.style.left = Math.min(start.x, p.x) + "px";
    rect.style.top = Math.min(start.y, p.y) + "px";
    rect.style.width = Math.abs(p.x - start.x) + "px";
    rect.style.height = Math.abs(p.y - start.y) + "px";
  });
  const finish = (ev) => {
    if (!boxMode || !start) return;
    const p = pt(ev);
    const x0 = Math.min(start.x, p.x), x1 = Math.max(start.x, p.x);
    const y0 = Math.min(start.y, p.y), y1 = Math.max(start.y, p.y);
    start = null; rect.style.display = "none";
    if (x1 - x0 < 6 && y1 - y0 < 6) return;   // タップ扱い（誤爆回避）
    let added = 0;
    for (const t of shownTargets()) {
      const s = pickMap.project([t.lon, t.lat]);
      if (s.x >= x0 && s.x <= x1 && s.y >= y0 && s.y <= y1 && !selected.includes(t.id)) {
        selected.push(t.id); added++;
      }
    }
    if (added) { lsSet(LS.selected, selected); paintSel(); updateSelbar(); renderPickList(); }
  };
  wrap.addEventListener("pointerup", finish);
  wrap.addEventListener("pointercancel", () => { start = null; rect.style.display = "none"; });
}

// リストからの選択（地図が重い端末向け）
function renderPickList() {
  const ul = document.getElementById("pick-list");
  ul.innerHTML = "";
  for (const t of shownTargets()) {
    const li = document.createElement("li");
    const on = selected.includes(t.id);
    if (on) li.classList.add("on");
    const h = t.height_m != null ? t.height_m.toFixed(0) + "m" : "高さ不明";
    li.innerHTML =
      `<input type="checkbox" ${on ? "checked" : ""} />` +
      `<div><div class="bid">${t.id}</div>` +
      `<div class="meta">${h}・用途 ${t.usage_label || "不明"}${t.is_new ? "・新規" : ""}</div></div>`;
    li.querySelector("input").addEventListener("change", () => toggleSelect(t.id));
    ul.appendChild(li);
  }
}
document.getElementById("toggle-list").addEventListener("click", (e) => {
  const list = document.getElementById("pick-list");
  const hidden = list.classList.toggle("hidden");
  e.currentTarget.classList.toggle("on", !hidden);
});
document.getElementById("more-btn").addEventListener("click", () => {
  displayLimit = displayLimit >= 2000 ? targets.length
    : displayLimit >= 500 ? 2000 : displayLimit >= 100 ? 500 : 100;
  refreshPickSource(); renderPickList();
});

// 下部：選択中 N棟
function updateSelbar() {
  document.getElementById("sel-n").textContent = selected.length;
  const btn = document.getElementById("start-btn");
  btn.disabled = selected.length === 0;
  document.getElementById("sel-clear").classList.toggle("hidden", selected.length === 0);
  const ul = document.getElementById("sel-list");
  ul.innerHTML = "";
  for (const id of selected) {
    const t = targetsById[id] || { id };
    const li = document.createElement("li");
    li.innerHTML = `<span>${id}</span><button class="rm">外す</button>`;
    li.querySelector(".rm").addEventListener("click", () => toggleSelect(id));
    ul.appendChild(li);
  }
}
document.getElementById("sel-head").addEventListener("click", () => {
  const list = document.getElementById("sel-list");
  const hidden = list.classList.toggle("hidden");
  document.getElementById("sel-caret").textContent = hidden ? "▸" : "▾";
});
// 選択した地点をすべて解除（一覧の開閉とは別操作なので伝播を止める）
document.getElementById("sel-clear").addEventListener("click", (e) => {
  e.stopPropagation();
  if (!selected.length) return;
  selected.length = 0;
  lsSet(LS.selected, selected);
  if (typeof paintSel === "function") paintSel();
  renderPickList();
  updateSelbar();
});
document.getElementById("operator").addEventListener("input", (e) =>
  lsSet(LS.operator, e.target.value.trim()));

document.getElementById("start-btn").addEventListener("click", () => {
  if (!selected.length) return;
  document.getElementById("view-pick").classList.add("hidden");
  document.getElementById("view-plan").classList.remove("hidden");
  initPlanMap();
  mode = "priority"; setModeButtons();
  buildPlan();
});
document.getElementById("plan-back").addEventListener("click", () => {
  document.getElementById("view-plan").classList.add("hidden");
  document.getElementById("view-pick").classList.remove("hidden");
  setTimeout(() => pickMap && pickMap.resize(), 50);
});

// --- 2. 調査プラン（距離から訪問順を提案。経路の線は引かない） --------------
// 近似TSP（最近傍 → 2-opt）。経路案内ではなく「回る順番」の提案に用いる。
function pathLength(start, pts) {
  let d = pts.length ? distM(start, pts[0]) : 0;
  for (let i = 0; i < pts.length - 1; i++) d += distM(pts[i], pts[i + 1]);
  return d;
}
function nearestNeighbor(start, pts) {
  const pool = pts.slice(), out = []; let cur = start;
  while (pool.length) {
    let bi = 0, bd = Infinity;
    pool.forEach((p, i) => { const d = distM(cur, p); if (d < bd) { bd = d; bi = i; } });
    cur = pool.splice(bi, 1)[0]; out.push(cur);
  }
  return out;
}
function twoOpt(start, pts) {
  if (pts.length < 4) return pts;
  let best = pts.slice(), improved = true, guard = 0;
  while (improved && guard++ < 40) {
    improved = false;
    for (let i = 0; i < best.length - 1; i++) {
      for (let k = i + 1; k < best.length; k++) {
        const cand = best.slice(0, i).concat(best.slice(i, k + 1).reverse(), best.slice(k + 1));
        if (pathLength(start, cand) + 1e-6 < pathLength(start, best)) { best = cand; improved = true; }
      }
    }
  }
  return best;
}
function optimizePath(start, pts) { return twoOpt(start, nearestNeighbor(start, pts)); }

// 半径200m のグループ（membership のみ。順番は後で最適化する）
function groupMembership(ids) {
  const rest = ids.slice(), groups = [];
  while (rest.length) {
    const anchor = rest.shift(); const grp = [anchor];
    for (let i = rest.length - 1; i >= 0; i--)
      if (distM(targetsById[anchor], targetsById[rest[i]]) <= GROUP_RADIUS_M)
        grp.push(rest.splice(i, 1)[0]);
    groups.push(grp);
  }
  return groups;
}
function centroid(ids) {
  let lon = 0, lat = 0, n = 0;
  ids.forEach((id) => { const t = targetsById[id]; if (t) { lon += t.lon; lat += t.lat; n++; } });
  return { lon: lon / n, lat: lat / n };
}

function buildPlan() {
  const ids = selected.filter((id) => targetsById[id]);
  if (!ids.length) { planGroups = []; planOrder = []; renderPlanMap(); renderPlanGroups(); updatePlanSummary(0); return; }

  const rank = {}; targets.forEach((t, i) => rank[t.id] = i);
  const byPriority = ids.slice().sort((a, b) => rank[a] - rank[b]);

  // 起点：近い順＝現在地 / それ以外＝優先度1位の棟
  const start = (mode === "near" && userPos)
    ? { lon: userPos.lon, lat: userPos.lat }
    : { lon: targetsById[byPriority[0]].lon, lat: targetsById[byPriority[0]].lat };

  // 200m グループを作り、グループ順を最適化（重心で NN+2-opt、起点から）
  const groups = groupMembership(byPriority);
  const gpts = groups.map((g) => ({ members: g, ...centroid(g) }));
  const gorder = optimizePath(start, gpts);

  // 各グループ内も最適化（入口＝直前グループの出口、最初は起点）
  let entry = start; planGroups = [];
  for (const gp of gorder) {
    const pts = gp.members.map((id) => ({ id, lon: targetsById[id].lon, lat: targetsById[id].lat }));
    const ord = optimizePath(entry, pts);
    planGroups.push(ord.map((p) => p.id));
    entry = ord[ord.length - 1];
  }
  planOrder = planGroups.flat();

  // 総移動距離（起点→各棟の直線距離の合計）
  const seq = [start, ...planOrder.map((id) => targetsById[id])];
  let total = 0; for (let i = 0; i < seq.length - 1; i++) total += distM(seq[i], seq[i + 1]);

  renderPlanMap(); renderPlanGroups(); updatePlanSummary(total);
}

function updatePlanSummary(total) {
  const el = document.getElementById("plan-summary");
  const dtxt = total < 1000 ? `約${Math.round(total / 10) * 10}m` : `約${(total / 1000).toFixed(1)}km`;
  const from = (mode === "near" && userPos) ? "現在地" : "優先度1位の棟";
  el.textContent = planOrder.length
    ? `この順で回ると、移動距離は ${from}起点で ${dtxt}（直線距離の合計／${planOrder.length}棟）`
    : "棟が選ばれていません";
}

function initPlanMap() {
  if (planMap) { planMap.resize(); return; }
  planMap = new maplibregl.Map({
    container: "plan-map", style: GSI, center: [139.6917, 35.6896], zoom: 13,
    attributionControl: false,
  });
}
let planMarkers = [];
function clearPlanMarkers() { planMarkers.forEach((m) => m.remove()); planMarkers = []; }

function renderPlanMap() {
  if (!planMap) return;
  const run = () => {
    clearPlanMarkers();
    const bounds = new maplibregl.LngLatBounds();
    let n = 0;
    planGroups.forEach((grp, gi) => {
      const color = GROUP_COLORS[gi % GROUP_COLORS.length];
      grp.forEach((id) => {
        const t = targetsById[id]; if (!t) return;
        n++;
        const el = document.createElement("div");
        el.className = "pin"; el.style.background = color;
        el.innerHTML = `<span>${n}</span>`;
        planMarkers.push(new maplibregl.Marker({ element: el, anchor: "bottom" })
          .setLngLat([t.lon, t.lat]).addTo(planMap));
        bounds.extend([t.lon, t.lat]);
      });
    });
    if (userPos) {
      const me = document.createElement("div"); me.className = "me-dot";
      planMarkers.push(new maplibregl.Marker({ element: me })
        .setLngLat([userPos.lon, userPos.lat]).addTo(planMap));
      bounds.extend([userPos.lon, userPos.lat]);
    }
    if (!bounds.isEmpty()) planMap.fitBounds(bounds, { padding: 46, maxZoom: 16, duration: 600 });
  };
  if (planMap.loaded()) run(); else planMap.once("load", run);
}

function renderPlanGroups() {
  const box = document.getElementById("plan-groups");
  box.innerHTML = "";
  let n = 0;
  planGroups.forEach((grp, gi) => {
    const color = GROUP_COLORS[gi % GROUP_COLORS.length];
    const wrap = document.createElement("div"); wrap.className = "group";
    const head = document.createElement("div"); head.className = "group-head";
    head.innerHTML = `<span class="gdot" style="background:${color}"></span>` +
      `グループ${gi + 1}（${grp.length}棟）`;
    wrap.appendChild(head);
    grp.forEach((id) => {
      n++;
      const t = targetsById[id] || { id };
      const draft = lsGet(LS.draft(id), {});
      const h = t.height_m != null ? `${t.height_m.toFixed(0)}m` : "高さ不明";
      const dist = userPos ? fmtDist(distM(userPos, t)) : "";
      const item = document.createElement("div");
      item.className = "grp-item" + (draft.done ? " done" : "");
      item.innerHTML =
        `<div class="num" style="background:${color}">${n}</div>` +
        `<div class="gi-main"><div class="gi-id">${id}</div>` +
        `<div class="gi-meta">${h}・${t.usage_label || "用途不明"}` +
        `${draft.done ? ' <span class="gi-done">✓調査済</span>' : ""}</div></div>` +
        (dist ? `<div class="gi-dist">${dist}</div>` : "");
      item.addEventListener("click", () => openInput(planOrder.indexOf(id)));
      wrap.appendChild(item);
    });
    box.appendChild(wrap);
  });
}

function setModeButtons() {
  document.getElementById("mode-priority").classList.toggle("on", mode === "priority");
  document.getElementById("mode-near").classList.toggle("on", mode === "near");
}
document.getElementById("mode-priority").addEventListener("click", () => {
  mode = "priority"; setModeButtons();
  document.getElementById("geo-msg").textContent = "";
  buildPlan();
});
document.getElementById("mode-near").addEventListener("click", () => {
  const msg = document.getElementById("geo-msg");
  if (userPos) { mode = "near"; setModeButtons(); buildPlan(); return; }
  if (!navigator.geolocation) { msg.textContent = "この端末は位置情報に対応していません"; return; }
  msg.textContent = "位置を取得中…";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      userPos = { lon: pos.coords.longitude, lat: pos.coords.latitude };
      mode = "near"; setModeButtons(); msg.textContent = ""; buildPlan();
    },
    () => { msg.textContent = "位置を取得できませんでした（優先度順のままです）"; },
    { enableHighAccuracy: true, timeout: 8000, maximumAge: 30000 });
});

// --- 3. 入力（既存流用＋グループ/距離） -------------------------------------
let miniMap = null, miniMarker = null;
function ensureMiniMap() {
  if (miniMap) { miniMap.resize(); return; }
  miniMap = new maplibregl.Map({ container: "mini-map", style: GSI,
    center: [139.6917, 35.6896], zoom: 17, attributionControl: false });
  miniMarker = new maplibregl.Marker({ color: COLOR_TGT });
}
function placeMini(lon, lat) {
  if (lon == null) return;
  miniMarker.setLngLat([lon, lat]).addTo(miniMap);
  miniMap.jumpTo({ center: [lon, lat], zoom: 17 });
  setTimeout(() => miniMap.resize(), 50);
}

function openInput(i) {
  document.getElementById("view-plan").classList.add("hidden");
  document.getElementById("view-input").classList.remove("hidden");
  ensureMiniMap();
  loadBuilding(i < 0 ? 0 : i);
}
document.getElementById("input-back").addEventListener("click", () => {
  document.getElementById("view-input").classList.add("hidden");
  document.getElementById("view-plan").classList.remove("hidden");
  renderPlanGroups();                       // 調査済マークを反映
  setTimeout(() => planMap && planMap.resize(), 50);
});

function groupOf(id) {
  for (let gi = 0; gi < planGroups.length; gi++)
    if (planGroups[gi].includes(id)) return gi + 1;
  return null;
}
function fillUsageOptions(code) {
  const sel = document.getElementById("f-usage");
  sel.innerHTML = '<option value="">（未選択）</option>' +
    usageCodes.map((u) => `<option value="${u.code}">${u.code}：${u.label}</option>`).join("");
  sel.value = code || "";
}

async function loadBuilding(i) {
  stopVoice(true);                 // 棟を移動したら音声は止める（振り分けない）
  vStatus("");
  idx = i;
  const id = planOrder[idx];
  document.getElementById("survey-bid").textContent = id;
  document.getElementById("survey-prog").textContent = `${idx + 1} / ${planOrder.length}`;
  document.getElementById("survey-err").classList.add("hidden");

  const t = targetsById[id] || {};
  placeMini(t.lon, t.lat);

  // グループ・距離の表示（入力画面にも足す）
  const g = groupOf(id);
  const dist = userPos && t.lon != null ? fmtDist(distM(userPos, t)) : null;
  document.getElementById("input-group").innerHTML =
    (g ? `<b>グループ${g}</b>` : "") + (dist ? `・現在地から ${dist}` : "") +
    (t.height_m != null ? `・高さ ${t.height_m.toFixed(0)}m` : "");

  let estUsage = null, estStoreys = null, estText = "推定なし";
  try {
    const d = await fetch(`/api/buildings/${id}`).then((r) => r.json());
    const cu = d.candidates.usage, cs = d.candidates.storeys;
    if (cu && !cu.unresolvable) estUsage = cu.value;
    if (cs && !cs.unresolvable) estStoreys = cs.value;
    const uTxt = cu ? (cu.unresolvable ? "判断不能" : (cu.display || "—")) : "—";
    const sTxt = cs ? (cs.unresolvable ? "判断不能" : (cs.display || "—")) : "—";
    estText = `推定：用途 <b>${uTxt}</b> ／ 階数 <b>${sTxt}</b>`;
  } catch { estText = "推定の取得に失敗（オフラインでも入力は続けられます）"; }
  document.getElementById("survey-est").innerHTML = estText;

  const guideEl = document.getElementById("survey-guide");
  if (guidanceById[id]) {
    guideEl.innerHTML = `<span class="lead">現地で確認すること</span>${guidanceById[id]}`;
    guideEl.classList.remove("hidden");
  } else guideEl.classList.add("hidden");

  const draft = lsGet(LS.draft(id), null);
  fillUsageOptions(draft ? draft.usage : estUsage);
  document.getElementById("f-storeys").value =
    draft && draft.storeys != null && draft.storeys !== "" ? draft.storeys
      : (estStoreys != null ? estStoreys : "");
  document.getElementById("f-note").value = (draft && draft.note) || "";
  setPhoto(draft && draft.photo);
  document.getElementById("saved-flag").classList.toggle("hidden", !draft);

  const saveBtn = document.getElementById("nav-save");
  const isDone = !!(draft && draft.done);
  saveBtn.textContent = isDone ? "保存済み（上書き保存）" : "保存して次へ";
  saveBtn.classList.toggle("done", isDone);
}

function readForm() {
  const st = document.getElementById("f-storeys").value;
  return {
    building_id: planOrder[idx],
    usage: document.getElementById("f-usage").value || null,
    storeys: st === "" ? null : Math.max(1, parseInt(st, 10) || 0) || null,
    note: document.getElementById("f-note").value.trim() || null,
    photo: currentPhoto,
  };
}
function saveDraft() {
  const id = planOrder[idx];
  const prev = lsGet(LS.draft(id), {});
  lsSet(LS.draft(id), { ...readForm(), done: prev.done || false });
  document.getElementById("saved-flag").classList.remove("hidden");
}
for (const elid of ["f-usage", "f-storeys", "f-note"])
  document.getElementById(elid).addEventListener("input", saveDraft);

// 写真（カメラ→端末側で縮小）
let currentPhoto = null;
function setPhoto(dataUrl) {
  currentPhoto = dataUrl || null;
  const thumb = document.getElementById("photo-thumb");
  const del = document.getElementById("photo-del");
  const note = document.getElementById("photo-note");
  if (currentPhoto) { thumb.src = currentPhoto; thumb.style.display = "inline"; del.classList.remove("hidden"); }
  else { thumb.removeAttribute("src"); thumb.style.display = "none"; del.classList.add("hidden"); }
  // 写真を付けたら注意書きを強調（人・ナンバー・表札の写り込み確認）
  if (note) note.classList.toggle("alert", !!currentPhoto);
}
function downscale(file) {
  return new Promise((resolve, reject) => {
    const img = new Image(); const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, PHOTO_MAX / Math.max(img.width, img.height));
      const w = Math.round(img.width * scale), h = Math.round(img.height * scale);
      const c = document.createElement("canvas"); c.width = w; c.height = h;
      c.getContext("2d").drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url); resolve(c.toDataURL("image/jpeg", 0.7));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("画像を読めません")); };
    img.src = url;
  });
}
document.getElementById("f-photo").addEventListener("change", async (e) => {
  const file = e.target.files && e.target.files[0]; if (!file) return;
  try { setPhoto(await downscale(file)); saveDraft(); } catch { showErr("写真を読み込めませんでした"); }
  e.target.value = "";
});
document.getElementById("photo-del").addEventListener("click", () => { setPhoto(null); saveDraft(); });

// 送信
function showErr(msg) {
  const el = document.getElementById("survey-err");
  el.textContent = "⚠ " + msg; el.classList.remove("hidden");
}
function gotoNextUndone() {
  for (let j = idx + 1; j < planOrder.length; j++)
    if (!lsGet(LS.draft(planOrder[j]), {}).done) return loadBuilding(j);
  const k = planOrder.findIndex((id) => !lsGet(LS.draft(id), {}).done);
  if (k >= 0) loadBuilding(k);
  else { alert("選んだ棟をすべて調査しました。お疲れさまでした。");
         document.getElementById("input-back").click(); }
}
document.getElementById("nav-save").addEventListener("click", async () => {
  saveDraft();
  const f = readForm();
  const operator = lsGet(LS.operator, "") || null;
  if (!f.usage && f.storeys == null && !f.note && !f.photo)
    return showErr("用途・階数・写真・メモのいずれかを入力してください");
  const btn = document.getElementById("nav-save");
  btn.disabled = true; btn.textContent = "送信中…";
  try {
    const res = await fetch("/api/survey", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...f, operator }) });
    if (!res.ok) throw new Error("サーバーエラー " + res.status);
    lsSet(LS.draft(f.building_id), { ...lsGet(LS.draft(f.building_id), {}), done: true });
    btn.disabled = false; gotoNextUndone();
  } catch {
    btn.disabled = false; btn.textContent = "保存して次へ";
    showErr("送信できませんでした（通信を確認）。入力は端末に保存済みです。");
  }
});
document.getElementById("nav-prev").addEventListener("click", () => { if (idx > 0) loadBuilding(idx - 1); });
document.getElementById("nav-next").addEventListener("click", () => { if (idx < planOrder.length - 1) loadBuilding(idx + 1); });

// --- 音声で入力（試験的） ---------------------------------------------------
// Web Speech API（SpeechRecognition）。音声はブラウザ提供元（Google/Apple）の
// サーバーで文字に変換される＝端末内処理ではない。初回に一度だけ同意を取り、
// 音声は保存せず認識後テキストだけを扱う。非対応・拒否・失敗でも手入力は壊さない。
const VLS = { consent: "mk.survey.voiceConsent" };
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const voiceSupported = !!SR && window.isSecureContext;

let recog = null;            // SpeechRecognition インスタンス（使い回す）
let voiceActive = false;     // 認識中
let voiceStopping = false;   // 停止ボタン押下（onend で振り分けへ）
let voiceSilent = false;     // 棟移動で止めた（振り分けず、メモも触らない）
let voiceBase = "";          // 音声開始前のメモ（手入力を消さないため）
let voiceFinal = "";         // 確定した認識テキストの蓄積（再開をまたいで保持）

function vStatus(msg, isErr) {
  const el = document.getElementById("voice-status");
  el.textContent = msg || "";
  el.classList.toggle("err", !!isErr);
}
function vBtn(rec) {
  const b = document.getElementById("voice-btn");
  b.classList.toggle("rec", rec);
  b.textContent = rec ? "■ 停止" : "🎤 音声で入力";
}
function applyTranscript(interim) {
  // メモ欄 = 開始前メモ + 認識テキスト（確定 + 暫定）。手入力は保持。
  const spoken = (voiceFinal + (interim || "")).trim();
  const sep = voiceBase && spoken ? "\n" : "";
  document.getElementById("f-note").value = voiceBase + sep + spoken;
}

function startVoice() {
  if (!voiceSupported || voiceActive) return;
  recog = new SR();
  recog.lang = "ja-JP";
  recog.continuous = true;
  recog.interimResults = true;
  voiceBase = document.getElementById("f-note").value.trim();
  voiceFinal = "";
  voiceStopping = false;
  voiceSilent = false;

  recog.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) voiceFinal += r[0].transcript;
      else interim += r[0].transcript;
    }
    applyTranscript(interim);
    vStatus("聞いています…");
  };
  recog.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      voiceActive = false; voiceStopping = true;   // 再開しない
      vStatus("マイクが使えません（手入力は続けられます）", true);
    } else if (e.error === "no-speech") {
      vStatus("音声が聞き取れませんでした");
    } else {
      vStatus("認識でエラー（手入力は続けられます）", true);
    }
  };
  recog.onend = () => {
    if (voiceActive && !voiceStopping) {
      try { recog.start(); return; } catch {}   // iOS 対策：止めるまで自動再開
    }
    finishVoice();
  };

  try {
    recog.start();
    voiceActive = true;
    vBtn(true);
    vStatus("聞いています…（話し終わったら「停止」）");
  } catch {
    voiceActive = false;
    vStatus("音声を開始できませんでした（手入力をどうぞ）", true);
  }
}

function stopVoice(silent) {
  if (!voiceActive) return;
  voiceStopping = true;
  voiceActive = false;
  voiceSilent = !!silent;        // 棟移動で止めたときは振り分けずメモも触らない
  try { recog && recog.stop(); } catch {}
}

async function finishVoice() {
  voiceActive = false;
  vBtn(false);
  if (voiceSilent) { voiceSilent = false; return; }  // 棟移動：メモ・振り分けに触れない
  applyTranscript("");     // 暫定分を確定表示に
  saveDraft();             // ここまでの文字は必ず端末に残す
  const text = voiceFinal.trim();
  if (!text) { vStatus(""); return; }

  vStatus("内容を振り分け中…");
  try {
    const res = await fetch("/api/voice_parse", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) throw new Error(String(res.status));
    const d = await res.json();
    const filled = [];
    if (d.storeys != null) {
      document.getElementById("f-storeys").value = d.storeys;
      filled.push(`階数 ${d.storeys}`);
    }
    if (d.usage) {
      const sel = document.getElementById("f-usage");
      if ([...sel.options].some((o) => o.value === String(d.usage))) {
        sel.value = String(d.usage);
        const lbl = usageCodes.find((u) => String(u.code) === String(d.usage));
        filled.push(`用途 ${lbl ? lbl.label : d.usage}`);
      }
    }
    saveDraft();
    vStatus(filled.length
      ? `${filled.join("・")} を入力しました（ご確認を）`
      : "読み取れる用途・階数はありませんでした（メモに残しました）");
  } catch {
    // 振り分け失敗でもメモは残っている。手入力で続けられる。
    vStatus("振り分けに失敗。話した内容はメモに残っています", true);
  }
}

if (voiceSupported) {
  document.getElementById("voice-tools").classList.remove("hidden");
  document.getElementById("voice-btn").addEventListener("click", () => {
    if (voiceActive) { stopVoice(false); return; }
    if (lsGet(VLS.consent, false)) startVoice();
    else document.getElementById("voice-consent").classList.remove("hidden");
  });
  document.getElementById("voice-ok").addEventListener("click", () => {
    lsSet(VLS.consent, true);
    document.getElementById("voice-consent").classList.add("hidden");
    startVoice();
  });
  document.getElementById("voice-cancel").addEventListener("click", () =>
    document.getElementById("voice-consent").classList.add("hidden"));
}

// --- 起動 -------------------------------------------------------------------
loadAll();
