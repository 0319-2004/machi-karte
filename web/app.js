"use strict";

// 背景：国土地理院 淡色地図（出典表示を必ず出す）
const style = {
  version: 8,
  sources: {
    gsi: {
      type: "raster",
      tiles: ["https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution:
        '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">国土地理院（地理院タイル）</a>',
    },
  },
  layers: [{ id: "gsi", type: "raster", source: "gsi" }],
};

// 初期表示：西新宿・都庁周辺を斜め俯瞰（デモの開始位置）
const map = new maplibregl.Map({
  container: "map",
  style,
  center: [139.6917, 35.6896],
  zoom: 15,
  pitch: 55,
  bearing: -20,
  attributionControl: { compact: false },
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-right");

const COLOR_NON = "#b0bec5";
const COLOR_TGT = "#e64a19";
const COLOR_SEL = "#ffd600";
const HEIGHT_EXPR = ["coalesce", ["get", "h"], 3];

let stats = null;
let targetsOnly = false;
let usageCodes = [];
let targetList = [];        // 優先度順
let current = null;         // 選択中の building_id
const done = new Set();     // このセッションで確定した building_id

// --- 件数カウンタ -----------------------------------------------------------
const elTotal = document.getElementById("count-total");
const elTarget = document.getElementById("count-target");
const elReduction = document.getElementById("count-reduction");

function animateCount(el, from, to, dur = 1500) {
  const t0 = performance.now();
  function step(now) {
    const p = Math.min(1, (now - t0) / dur);
    const e = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(from + (to - from) * e).toLocaleString();
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

async function loadStats() {
  stats = await (await fetch("/api/stats")).json();
  elTotal.textContent = stats.total.toLocaleString();
  elTarget.textContent = stats.target.toLocaleString();
  elReduction.textContent = "−" + stats.reduction_pct + "%";
}

// --- 計測（常時表示） -------------------------------------------------------
async function loadMetrics() {
  const m = await (await fetch("/api/metrics")).json();
  document.getElementById("m-processed").textContent = m.processed;
  document.getElementById("m-total").textContent = m.total_minutes.toFixed(1);
  document.getElementById("m-median").textContent =
    m.median_sec != null ? m.median_sec.toFixed(1) : "–";
  const fmt = (x) => (x.median_sec != null ? x.median_sec.toFixed(1) + "s" : "–") + `（${x.count}）`;
  document.getElementById("m-keep").textContent = fmt(m.keep);
  document.getElementById("m-edit").textContent = fmt(m.edit);
}

// --- 建物レイヤ -------------------------------------------------------------
function addBuildingLayers() {
  map.addSource("buildings", { type: "geojson", data: "/data/buildings.geojson" });
  map.addLayer({
    id: "b-non", type: "fill-extrusion", source: "buildings",
    filter: ["==", ["get", "t"], 0],
    paint: {
      "fill-extrusion-height": HEIGHT_EXPR, "fill-extrusion-base": 0,
      "fill-extrusion-color": COLOR_NON, "fill-extrusion-opacity": 0.85,
      "fill-extrusion-opacity-transition": { duration: 1500 },
    },
  });
  map.addLayer({
    id: "b-tgt", type: "fill-extrusion", source: "buildings",
    filter: ["==", ["get", "t"], 1],
    paint: {
      "fill-extrusion-height": HEIGHT_EXPR, "fill-extrusion-base": 0,
      "fill-extrusion-color": COLOR_TGT, "fill-extrusion-opacity": 0.9,
    },
  });
  map.addLayer({
    id: "b-sel", type: "fill-extrusion", source: "buildings",
    filter: ["==", ["get", "id"], "__none__"],
    paint: {
      "fill-extrusion-height": HEIGHT_EXPR, "fill-extrusion-base": 0,
      "fill-extrusion-color": COLOR_SEL, "fill-extrusion-opacity": 1,
    },
  });
  for (const id of ["b-non", "b-tgt", "b-sel"]) {
    map.on("click", id, (e) => selectBuilding(e.features[0].properties.id));
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
  }
}

// --- 全棟 ⇄ 調査対象のみ ----------------------------------------------------
const toggleBtn = document.getElementById("toggle");
toggleBtn.addEventListener("click", () => {
  targetsOnly = !targetsOnly;
  map.setPaintProperty("b-non", "fill-extrusion-opacity", targetsOnly ? 0 : 0.85);
  const from = targetsOnly ? stats.total : stats.target;
  const to = targetsOnly ? stats.target : stats.total;
  animateCount(elTotal, from, to);
  toggleBtn.textContent = targetsOnly ? "全棟を表示" : "調査対象のみ";
});

// --- 用途コードの選択肢 -----------------------------------------------------
async function loadUsageCodes() {
  usageCodes = await (await fetch("/api/usage_codes")).json();
}

// --- 優先度順リスト ---------------------------------------------------------
async function loadTargets() {
  targetList = await (await fetch("/api/targets?limit=100")).json();
  for (const t of targetList) if (t.confirmed) done.add(t.id);
  renderList();
}

function renderList() {
  const ol = document.getElementById("target-list");
  ol.innerHTML = "";
  for (const t of targetList) {
    const li = document.createElement("li");
    li.dataset.id = t.id;
    if (done.has(t.id)) li.classList.add("done");
    const h = t.height_m != null ? t.height_m.toFixed(1) + "m" : "高さ不明";
    const tags =
      (t.is_new ? `<span class="tag new">新規</span>` : "") +
      (t.has_image
        ? `<span class="tag img" title="LOD2テクスチャあり（3D都市モデルの面テクスチャ。実写真ではない）">LOD2</span>`
        : "");
    li.innerHTML =
      `<div><div class="bid">${t.id}</div>` +
      `<div class="meta">${h}・用途 ${t.usage_label || "不明"}</div></div>` +
      `<div class="tags">${tags}${done.has(t.id) ? '<span class="check">✓</span>' : ""}</div>`;
    li.addEventListener("click", () => selectBuilding(t.id));
    ol.appendChild(li);
  }
}

function markActive(id) {
  document.querySelectorAll("#target-list li").forEach((x) =>
    x.classList.toggle("active", x.dataset.id === id));
}

// --- 確認・確定カード -------------------------------------------------------
const confirmPanel = document.getElementById("confirm");
const confirmScroll = document.getElementById("confirm-scroll");
const confirmFoot = document.getElementById("confirm-foot");
document.getElementById("confirm-close").addEventListener("click", closeConfirm);

function closeConfirm() {
  confirmPanel.classList.add("hidden");
  current = null;
  map.setFilter("b-sel", ["==", ["get", "id"], "__none__"]);
  document.querySelectorAll("#target-list li.active").forEach((x) => x.classList.remove("active"));
}

async function selectBuilding(id) {
  current = id;
  map.setFilter("b-sel", ["==", ["get", "id"], id]);
  markActive(id);
  const t = targetList.find((x) => x.id === id);
  if (t && t.lon != null) map.flyTo({ center: [t.lon, t.lat], zoom: 17, pitch: 60, duration: 1200 });

  // 建物を開いた時刻をサーバー側で記録（計測の起点）
  fetch(`/api/open/${id}`, { method: "POST" });
  const d = await (await fetch(`/api/buildings/${id}`)).json();
  if (d.detail) return;
  renderConfirm(d);
}

const ATTR_META = {
  usage: { name: "用途", cur: (a) => `不明（${a.usage.raw}）` },
  storeys: { name: "階数（地上）", cur: (a) => `不明（${a.storeys_above.raw}）` },
  year_built: { name: "建築年", cur: () => "未記録（空）" },
};

function attrRowHtml(key, cand, a) {
  const meta = ATTR_META[key];
  if (!meta) return "";
  if (cand.unresolvable) {
    return (
      `<div class="attr-row unres"><div class="top">` +
      `<span class="name">${meta.name}</span>` +
      `<span class="conf conf-—">判断不能</span></div>` +
      `<div class="reason">⚠ ${cand.reasoning}</div></div>`
    );
  }
  const ev = (cand.evidence || [])
    .map((e) => `<span class="ev">${e.label}</span>`).join("");
  return (
    `<div class="attr-row"><div class="top">` +
    `<span class="name">${meta.name}</span>` +
    `<span class="conf conf-${cand.confidence_label}">信頼度 ${cand.confidence_label}</span></div>` +
    `<div class="vals"><span class="cur">現在 <b>${meta.cur(a)}</b></span>` +
    `<span class="to">→</span><span class="est">${cand.display ?? "—"}</span></div>` +
    `<div class="reason">${cand.reasoning || ""}</div>` +
    (ev ? `<div class="evidence">${ev}</div>` : "") +
    `</div>`
  );
}

function renderConfirm(d) {
  const a = d.attributes;
  const c = d.candidates;
  const order = ["usage", "storeys", "year_built"];
  const rows = order.filter((k) => c[k]).map((k) => attrRowHtml(k, c[k], a)).join("");

  // 用途の選択肢（推定値を既定で選択）
  const estUsage = c.usage && !c.usage.unresolvable ? c.usage.value : null;
  const opts = usageCodes
    .map((u) => `<option value="${u.code}"${u.code === estUsage ? " selected" : ""}>` +
                `${u.code}：${u.label}</option>`).join("");

  const badges =
    (a.is_target ? `<span class="badge target">調査対象（不明群）</span>` : "") +
    (d.is_new ? `<span class="badge new">新規出現（版間差分）</span>` : "");

  const hasTex = a.lod_type === "LOD2";
  const imgText = hasTex ? "LOD2テクスチャあり（実写真は未取得）" : "画像なし";
  confirmScroll.innerHTML =
    `<div class="c-head"><h3>${a.id}</h3><div class="c-badges">${badges}</div></div>` +
    `<div class="c-image">${imgText}` +
    `<span class="src">供給源：実写真（Mapillary/オルソ）は Phase 7 で取得</span></div>` +
    `<div class="c-attrs">${rows}</div>` +
    `<div class="c-usage"><label>用途コード（修正する場合に選ぶ）</label>` +
    `<select id="usage-select">${opts}</select></div>`;
  confirmFoot.innerHTML =
    `<div class="c-actions">` +
    `<button id="btn-keep">そのまま確定</button>` +
    `<button id="btn-edit">修正して確定</button></div>` +
    `<div class="c-hint"><kbd>Enter</kbd> でそのまま確定 → 次の優先棟へ</div>`;

  document.getElementById("btn-keep").addEventListener("click", () => doConfirm(true));
  document.getElementById("btn-edit").addEventListener("click", () => doConfirm(false));
  confirmScroll.scrollTop = 0;
  confirmPanel.classList.remove("hidden");
}

async function doConfirm(accepted) {
  if (!current) return;
  const id = current;
  const sel = document.getElementById("usage-select");
  const body = { accepted, usage: sel ? sel.value : null };
  await fetch(`/api/confirm/${id}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  done.add(id);
  const t = targetList.find((x) => x.id === id);
  if (t) t.confirmed = true;
  renderList();
  loadMetrics();
  openNext(id);   // 確定後は次の優先棟を自動で開く（連続処理を見せる）
}

function openNext(afterId) {
  const idx = targetList.findIndex((x) => x.id === afterId);
  for (let i = idx + 1; i < targetList.length; i++) {
    if (!done.has(targetList[i].id)) return selectBuilding(targetList[i].id);
  }
  // 後ろに無ければ先頭から未確定を探す
  const next = targetList.find((x) => !done.has(x.id));
  if (next) selectBuilding(next.id);
  else closeConfirm();   // 全部確定
}

// --- キー操作：Enter でそのまま確定 ----------------------------------------
document.addEventListener("keydown", (e) => {
  if (confirmPanel.classList.contains("hidden")) return;
  if (e.key === "Enter") { e.preventDefault(); doConfirm(true); }
  else if (e.key === "Escape") closeConfirm();
});

// --- 起動 -------------------------------------------------------------------
loadStats();
loadUsageCodes();
loadMetrics();
loadTargets();
map.on("load", addBuildingLayers);
