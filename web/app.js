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
let currentData = null;     // 選択中の建物の詳細（/api/buildings の応答）
const done = new Set();     // このセッションで確定した building_id
const surveyQueued = new Set();  // 現地確認リストに回した building_id（低確度）
const DESK_OPERATOR_LS = "mk.desk.operator";     // 確定者名（机上）
const SURVEY_SELECTED_LS = "mk.survey.selected"; // 現地調査モードが読む選択リスト

function getDeskOperator() {
  return localStorage.getItem(DESK_OPERATOR_LS) || "調査担当者";
}
let onsiteData = null;      // 判断不能の集約（/api/unresolved）
let onsiteActiveKey = null; // 地図で絞り込み中の区分キー
const COLOR_ONSITE = "#8e24aa";   // 現地確認が必要な棟のハイライト（紫）
const COLOR_DONE_ONSITE = "#2e7d32"; // 現地調査で確定した棟（緑）
const COLOR_PHOTO = "#00bcd4";    // 現地写真がある棟のマーカー（シアン）
const ATTR_LABEL = { usage: "用途", storeys: "階数（地上）", year_built: "建築年" };

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

// --- 更新候補のダウンロード（確定済み件数で有効/無効） --------------------
async function loadExportSummary() {
  const s = await (await fetch("/api/export/summary")).json();
  const el = document.getElementById("download");
  document.getElementById("dl-count").textContent =
    `更新候補 ${s.buildings} 棟・${s.rows} 件`;
  el.classList.toggle("empty", s.rows === 0);
}

// --- レポート生成（POST /api/report → Markdown をダウンロード） ------------
const dlReport = document.getElementById("dl-report");
dlReport.addEventListener("click", async () => {
  const orig = dlReport.textContent;
  dlReport.disabled = true;
  dlReport.textContent = "生成中…";
  try {
    const res = await fetch("/api/report?city_code=13104", { method: "POST" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "report_13104.md";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert("レポート生成に失敗しました：" + e.message);
  } finally {
    dlReport.disabled = false;
    dlReport.textContent = orig;
  }
});

// --- 現地確認が必要な棟（判断不能の集約） ----------------------------------
const onsiteBar = document.getElementById("onsite-bar");
const onsitePanel = document.getElementById("onsite-panel");
const onsiteCaret = document.getElementById("onsite-caret");

async function loadUnresolved() {
  onsiteData = await (await fetch("/api/unresolved")).json();
  document.getElementById("onsite-summary").innerHTML =
    `${onsiteData.total_targets.toLocaleString()}棟のうち、現地確認が必要な ` +
    `<b>${onsiteData.unresolved_buildings.toLocaleString()}</b>棟`;
  renderOnsiteGroups();
}

function renderOnsiteGroups() {
  if (!onsiteData) return;
  document.getElementById("op-sub").textContent = `（${onsiteData.groups.length} 区分）`;
  const box = document.getElementById("onsite-groups");
  box.innerHTML = "";
  for (const g of onsiteData.groups) {
    const el = document.createElement("div");
    el.className = "og" + (g.key === onsiteActiveKey ? " active" : "");
    const chips = (obj) =>
      Object.entries(obj).filter(([, n]) => n > 0)
        .map(([k, n]) => `<span class="chip">${k}: ${n}</span>`).join("");
    const guide = g.guidance
      ? `<div class="og-guide"><span class="lead">現地で確認すること</span>${g.guidance}</div>`
      : `<div class="og-guide none">要約は未生成（make onsite で生成）</div>`;
    el.innerHTML =
      `<div class="og-top"><span class="og-title">${ATTR_LABEL[g.attribute] || g.attribute}</span>` +
      `<span class="og-count">${g.count.toLocaleString()}棟</span></div>` +
      `<div class="og-reason">理由：${g.reason}</div>` +
      `<div class="og-chips">${chips(g.bands)}${chips(g.areas)}</div>` + guide;
    el.addEventListener("click", () => focusGroup(g));
    box.appendChild(el);
  }
}

function focusGroup(g) {
  onsiteActiveKey = g.key;
  renderOnsiteGroups();
  // その区分の棟だけを紫で残し、他は消す（絞り込み）
  map.setFilter("b-onsite", ["in", ["get", "id"], ["literal", g.building_ids]]);
  map.setPaintProperty("b-non", "fill-extrusion-opacity", 0);
  map.setPaintProperty("b-tgt", "fill-extrusion-opacity", 0);
  document.getElementById("onsite-clear").classList.remove("hidden");
  if (g.bbox) {
    map.fitBounds([[g.bbox[0], g.bbox[1]], [g.bbox[2], g.bbox[3]]],
      { padding: 90, pitch: 30, duration: 1200, maxZoom: 16 });
  }
}

function clearOnsiteFocus() {
  onsiteActiveKey = null;
  map.setFilter("b-onsite", ["in", ["get", "id"], ["literal", []]]);
  map.setPaintProperty("b-non", "fill-extrusion-opacity", targetsOnly ? 0 : 0.85);
  map.setPaintProperty("b-tgt", "fill-extrusion-opacity", 0.9);
  document.getElementById("onsite-clear").classList.add("hidden");
  renderOnsiteGroups();
}

onsiteBar.addEventListener("click", () => {
  const hidden = onsitePanel.classList.toggle("hidden");
  onsiteCaret.textContent = hidden ? "▸" : "▾";
});
document.getElementById("onsite-close").addEventListener("click", () => {
  onsitePanel.classList.add("hidden");
  onsiteCaret.textContent = "▸";
});
document.getElementById("onsite-clear").addEventListener("click", clearOnsiteFocus);

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
  // 現地確認が必要な棟（判断不能の区分）のハイライト。既定は非表示（空フィルタ）。
  map.addLayer({
    id: "b-onsite", type: "fill-extrusion", source: "buildings",
    filter: ["in", ["get", "id"], ["literal", []]],
    paint: {
      "fill-extrusion-height": HEIGHT_EXPR, "fill-extrusion-base": 0,
      "fill-extrusion-color": COLOR_ONSITE, "fill-extrusion-opacity": 0.95,
    },
  });
  // 現地調査（スマホ）で確定した棟を緑で示す。既定は非表示（空フィルタ）。
  map.addLayer({
    id: "b-done-onsite", type: "fill-extrusion", source: "buildings",
    filter: ["in", ["get", "id"], ["literal", []]],
    paint: {
      "fill-extrusion-height": HEIGHT_EXPR, "fill-extrusion-base": 0,
      "fill-extrusion-color": COLOR_DONE_ONSITE, "fill-extrusion-opacity": 0.95,
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
  for (const id of ["b-non", "b-tgt", "b-onsite", "b-done-onsite", "b-sel"]) {
    map.on("click", id, (e) => selectBuilding(e.features[0].properties.id));
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
  }

  // 現地写真がある棟のマーカー（カメラ色の点）。クリックでその棟を開く。
  map.addSource("photos", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "b-photo", type: "circle", source: "photos",
    paint: {
      "circle-radius": 6, "circle-color": COLOR_PHOTO,
      "circle-stroke-color": "#ffffff", "circle-stroke-width": 2,
    },
  });
  map.on("click", "b-photo", (e) => selectBuilding(e.features[0].properties.id));
  map.on("mouseenter", "b-photo", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "b-photo", () => (map.getCanvas().style.cursor = ""));

  loadConfirmed();
  loadPhotos();
}

// 現地写真がある棟を地図にマーカー表示（/api/photos の重心点）
async function loadPhotos() {
  try {
    const list = await (await fetch("/api/photos")).json();
    const feats = list
      .filter((p) => p.lon != null && p.lat != null)
      .map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
        properties: { id: p.building_id, count: p.count, latest: p.latest },
      }));
    const src = map.getSource("photos");
    if (src) src.setData({ type: "FeatureCollection", features: feats });
  } catch {}
}

// 現地調査で確定した棟（source='onsite'）を緑でハイライトする
async function loadConfirmed() {
  try {
    const c = await (await fetch("/api/confirmed")).json();
    if (map.getLayer("b-done-onsite")) {
      map.setFilter("b-done-onsite", ["in", ["get", "id"], ["literal", c.onsite || []]]);
    }
  } catch {}
}

// --- 全棟 ⇄ 調査対象のみ ----------------------------------------------------
const toggleBtn = document.getElementById("toggle");
toggleBtn.addEventListener("click", () => {
  if (onsiteActiveKey) clearOnsiteFocus();   // 現地確認の絞り込み中なら解除してから
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
      (surveyQueued.has(t.id) ? `<span class="tag survey">現地予定</span>` : "") +
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

// 画像枠：現地写真があればそれを出す（複数なら切り替え）。無ければ従来の文言。
function imageBlockHtml(d) {
  const photos = d.photos || [];
  if (photos.length > 0) {
    const p0 = photos[0];
    const nav = photos.length > 1
      ? `<button class="ph-nav prev" title="前の写真">‹</button>` +
        `<button class="ph-nav next" title="次の写真">›</button>` +
        `<span class="ph-count"><span id="ph-idx">1</span>/${photos.length}</span>`
      : "";
    return (
      `<div class="c-image has-photo">` +
      `<img id="ph-img" src="${p0.url}" alt="現地写真" />${nav}` +
      `<span class="src" id="ph-src">${photoSrcLabel(p0)}</span></div>`
    );
  }
  const hasTex = d.attributes.lod_type === "LOD2";
  const imgText = hasTex ? "LOD2テクスチャあり（実写真は未取得）" : "画像なし";
  return (
    `<div class="c-image">${imgText}` +
    `<span class="src">供給源：実写真は現地調査で取得（未取得）</span></div>`
  );
}

function photoSrcLabel(p) {
  const date = p.captured_on ? `${p.captured_on} 撮影` : "撮影日不明";
  const who = p.operator ? `・${p.operator}` : "";
  return `現地調査（${date}）${who}`;
}

// 複数写真の切り替え（前/次）。枠内の img と供給源ラベルを差し替える。
function wirePhotoNav(photos) {
  if (!photos || photos.length <= 1) return;
  let i = 0;
  const img = document.getElementById("ph-img");
  const idxEl = document.getElementById("ph-idx");
  const srcEl = document.getElementById("ph-src");
  const show = () => {
    const p = photos[i];
    img.src = p.url;
    if (idxEl) idxEl.textContent = i + 1;
    if (srcEl) srcEl.textContent = photoSrcLabel(p);
  };
  const prev = confirmScroll.querySelector(".ph-nav.prev");
  const next = confirmScroll.querySelector(".ph-nav.next");
  if (prev) prev.addEventListener("click", () => { i = (i - 1 + photos.length) % photos.length; show(); });
  if (next) next.addEventListener("click", () => { i = (i + 1) % photos.length; show(); });
}

function renderConfirm(d) {
  currentData = d;
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

  confirmScroll.innerHTML =
    `<div class="c-head"><h3>${a.id}</h3><div class="c-badges">${badges}</div></div>` +
    imageBlockHtml(d) +
    `<div class="c-attrs">${rows}</div>` +
    `<div class="c-usage"><label>用途コード（修正する場合に選ぶ）</label>` +
    `<select id="usage-select">${opts}</select></div>`;
  confirmFoot.innerHTML =
    `<div class="c-operator"><label for="operator-input">確定者</label>` +
    `<input id="operator-input" type="text" placeholder="調査担当者" /></div>` +
    `<div class="c-actions">` +
    `<button id="btn-keep">そのまま確定</button>` +
    `<button id="btn-edit">修正して確定</button></div>` +
    `<div class="c-hint"><kbd>Enter</kbd> でそのまま確定 → 次の優先棟へ</div>`;

  const opEl = document.getElementById("operator-input");
  opEl.value = getDeskOperator();
  opEl.addEventListener("input", () =>
    localStorage.setItem(DESK_OPERATOR_LS, opEl.value.trim() || "調査担当者"));

  document.getElementById("btn-keep").addEventListener("click", attemptKeep);
  document.getElementById("btn-edit").addEventListener("click", () => doConfirm(false));
  wirePhotoNav(d.photos);
  confirmScroll.scrollTop = 0;
  confirmPanel.classList.remove("hidden");
}

// 低確度の属性名を集める（usage/storeys/year_built のうち confidence_label が「低」）
function lowConfidenceAttrs(d) {
  const names = [];
  for (const k of ["usage", "storeys", "year_built"]) {
    const cand = d.candidates[k];
    if (cand && !cand.unresolvable && cand.confidence_label === "低")
      names.push(ATTR_META[k] ? ATTR_META[k].name : k);
  }
  return names;
}

// 「そのまま確定」の入口。低確度があれば一度確認を挟む（高さ119.5m×供給処理施設の誤確定を防ぐ）
function attemptKeep() {
  if (!current || !currentData) return;
  const low = lowConfidenceAttrs(currentData);
  if (low.length === 0) return doConfirm(true);
  openLowConfModal(low);
}

// --- 低確度の確認ダイアログ -------------------------------------------------
const lowModal = document.getElementById("lowconf-modal");

function openLowConfModal(lowNames) {
  document.getElementById("lc-attrs").textContent =
    "確度が低い属性：" + lowNames.join("・");
  lowModal.classList.remove("hidden");
}
function closeLowConfModal() { lowModal.classList.add("hidden"); }

// 確定せず、現地調査モードの選択リストに追加する（現地確認に回す）
function queueForSurvey(id) {
  if (!id) return;
  let sel = [];
  try { sel = JSON.parse(localStorage.getItem(SURVEY_SELECTED_LS)) || []; } catch {}
  if (!sel.includes(id)) sel.push(id);
  localStorage.setItem(SURVEY_SELECTED_LS, JSON.stringify(sel));
  surveyQueued.add(id);
  renderList();
  openNext(id);   // 確定はせず、次の優先棟へ送る
}

document.getElementById("lc-proceed").addEventListener("click", () => {
  closeLowConfModal(); doConfirm(true);
});
document.getElementById("lc-cancel").addEventListener("click", closeLowConfModal);
document.getElementById("lc-survey").addEventListener("click", () => {
  const id = current; closeLowConfModal(); queueForSurvey(id);
});

async function doConfirm(accepted) {
  if (!current) return;
  const id = current;
  const sel = document.getElementById("usage-select");
  const opEl = document.getElementById("operator-input");
  const operator = (opEl && opEl.value.trim()) || getDeskOperator();
  const body = { accepted, usage: sel ? sel.value : null, operator };
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
  loadExportSummary();
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

// --- キー操作：Enter でそのまま確定（低確度なら確認を挟む） ----------------
document.addEventListener("keydown", (e) => {
  // 低確度ダイアログが開いているときは Esc で閉じるだけ（誤確定を防ぐ）
  if (!lowModal.classList.contains("hidden")) {
    if (e.key === "Escape") closeLowConfModal();
    return;
  }
  if (confirmPanel.classList.contains("hidden")) return;
  if (e.key === "Enter") { e.preventDefault(); attemptKeep(); }
  else if (e.key === "Escape") closeConfirm();
});

// --- 起動 -------------------------------------------------------------------
loadStats();
loadUsageCodes();
loadMetrics();
loadExportSummary();
loadTargets();
loadUnresolved();
map.on("load", addBuildingLayers);
