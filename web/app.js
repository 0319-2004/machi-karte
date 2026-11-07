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
  // kepler.gl 調の暗い背景。地理院タイル（淡色）を暗く沈めて使う（出典表示は維持）。
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#0e1116" } },
    {
      id: "gsi", type: "raster", source: "gsi",
      paint: {
        "raster-opacity": 0.8,
        "raster-brightness-max": 0.45,
        "raster-saturation": -0.3,
        "raster-contrast": 0.1,
      },
    },
  ],
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

// kepler.gl 調：暗い地図の上でも建物の塊が見える青灰色。調査対象は橙で浮かせる。
const COLOR_NON = "#6b7687";
const COLOR_TGT = "#ff6a2b";
const COLOR_SEL = "#ffd54a";
const HEIGHT_EXPR = ["coalesce", ["get", "h"], 3];

// デモ演出モード（map.html は body.demo で開始）。demo.js が段階を進める。
// このモードでは件数と建物の色を即時に出さず、「新宿」入力→抽出の流れで公開する。
const DEMO = document.body.classList.contains("demo");
// デモで抽出まで隠すハイライト系レイヤ（計画=紫・確定=緑・現地確認=濃い紫・選択=黄・写真=シアン）
const DEMO_OVERLAY_LAYERS = ["b-plan", "b-onsite", "b-done-onsite", "b-sel", "b-photo"];
window.MK = {};   // demo.js へ渡すフック（map / 色の切り替え / カウント）

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
const COLOR_PLAN = "#b06bff";     // 調査計画に入れた棟のハイライト（紫＝選択中）
const COLOR_ONSITE = "#8e24aa";   // 現地確認が必要な棟のハイライト（濃い紫）
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
  if (DEMO) return;   // デモでは demo.js が段階に合わせて数字を公開する
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
      `<div class="og-chips">${chips(g.bands)}${chips(g.areas)}</div>` + guide +
      `<button type="button" class="og-plan">＋ この区分を調査計画に追加</button>`;
    el.addEventListener("click", () => focusGroup(g));
    el.querySelector(".og-plan").addEventListener("click", (e) => {
      e.stopPropagation();   // グループの絞り込み（focusGroup）は走らせない
      addManyToPlan(g.building_ids || []);
    });
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
      "fill-extrusion-color": COLOR_NON,
      "fill-extrusion-opacity": DEMO ? 0 : 0.85,
      "fill-extrusion-opacity-transition": { duration: 1500 },
    },
  });
  map.addLayer({
    id: "b-tgt", type: "fill-extrusion", source: "buildings",
    filter: ["==", ["get", "t"], 1],
    paint: {
      "fill-extrusion-height": HEIGHT_EXPR, "fill-extrusion-base": 0,
      // デモでは最初は全棟と同じ灰色・非表示。抽出の瞬間に橙へ変える。
      "fill-extrusion-color": DEMO ? COLOR_NON : COLOR_TGT,
      "fill-extrusion-color-transition": { duration: 900 },
      "fill-extrusion-opacity": DEMO ? 0 : 0.9,
      "fill-extrusion-opacity-transition": { duration: 900 },
    },
  });
  // 調査計画に入れた棟を紫でハイライト（選択中だと一目でわかるように）。
  map.addLayer({
    id: "b-plan", type: "fill-extrusion", source: "buildings",
    filter: ["in", ["get", "id"], ["literal", planSel]],
    paint: {
      "fill-extrusion-height": HEIGHT_EXPR, "fill-extrusion-base": 0,
      "fill-extrusion-color": COLOR_PLAN, "fill-extrusion-opacity": 0.95,
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
  for (const id of ["b-non", "b-tgt", "b-plan", "b-onsite", "b-done-onsite", "b-sel"]) {
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

  // 新宿区の輪郭（表示専用・PLATEAU建物の凹包）。「新宿」入力時に光がなぞる。
  // line-gradient を1フレームずつ書き換えて走らせるため lineMetrics を有効にする。
  const clearGrad = ["interpolate", ["linear"], ["line-progress"],
    0, "rgba(42,212,232,0)", 1, "rgba(42,212,232,0)"];
  map.addSource("ward", { type: "geojson", data: "/shinjuku_outline.geojson", lineMetrics: true });
  map.addLayer({
    id: "ward-glow", type: "line", source: "ward",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-width": 10, "line-blur": 9, "line-opacity": 0,
      "line-opacity-transition": { duration: 700 }, "line-gradient": clearGrad },
  });
  map.addLayer({
    id: "ward-line", type: "line", source: "ward",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-width": 2.5, "line-opacity": 0,
      "line-opacity-transition": { duration: 700 }, "line-gradient": clearGrad },
  });

  // デモ演出のフック：灰色で全棟を見せる → 抽出で調査対象を橙に浮かび上がらせる
  window.MK.showAllGray = () => {
    map.setPaintProperty("b-non", "fill-extrusion-opacity", 0.85);
    map.setPaintProperty("b-tgt", "fill-extrusion-color", COLOR_NON);
    map.setPaintProperty("b-tgt", "fill-extrusion-opacity", 0.85);
  };
  window.MK.revealTargets = () => {
    map.setPaintProperty("b-tgt", "fill-extrusion-color", COLOR_TGT);
    map.setPaintProperty("b-tgt", "fill-extrusion-opacity", 0.9);
    // 抽出まで隠していたハイライト（計画=紫・確定=緑・写真=シアン等）をここで出す
    for (const id of DEMO_OVERLAY_LAYERS) map.setLayoutProperty(id, "visibility", "visible");
  };

  // デモでは抽出前（段階0/1）に計画の紫・確定の緑・写真の点などが見えないよう隠す。
  // これらはロード時にフィルタが入る（loadConfirmed/loadPhotos/planSel）ため、
  // 全棟を灰色で見せる演出が崩れる。revealTargets（抽出）で visible に戻す。
  if (DEMO) {
    for (const id of DEMO_OVERLAY_LAYERS) map.setLayoutProperty(id, "visibility", "none");
  }
  document.dispatchEvent(new Event("mk:layers-ready"));
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
  const inPlan = planSel.includes(a.id);
  confirmFoot.innerHTML =
    `<div class="c-operator"><label for="operator-input">確定者</label>` +
    `<input id="operator-input" type="text" placeholder="調査担当者" /></div>` +
    `<button id="btn-plan" class="c-plan${inPlan ? " on" : ""}">` +
    `${inPlan ? "✓ 調査計画に追加済み（外す）" : "＋ 調査計画に追加（現地で確認）"}</button>` +
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
  document.getElementById("btn-plan").addEventListener("click", () => {
    togglePlan(a.id);
    const on = planSel.includes(a.id);
    const b = document.getElementById("btn-plan");
    b.classList.toggle("on", on);
    b.textContent = on ? "✓ 調査計画に追加済み（外す）" : "＋ 調査計画に追加（現地で確認）";
  });
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

// 確定せず、調査計画（現地調査モードの選択リスト）に追加する（現地確認に回す）
function queueForSurvey(id) {
  if (!id) return;
  if (!planSel.includes(id)) { planSel.push(id); savePlanSel(); renderPlanTray(); }
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

// --- 調査計画（PC で選び、QR で現地スマホへ渡す） --------------------------
// 選択は現地調査モードと同じ localStorage（mk.survey.selected）に持つので、
// 低確度ダイアログの「現地確認リストに追加」ともそのまま合流する。
let planSel = [];
try { planSel = JSON.parse(localStorage.getItem(SURVEY_SELECTED_LS)) || []; } catch {}

function savePlanSel() { localStorage.setItem(SURVEY_SELECTED_LS, JSON.stringify(planSel)); }
function togglePlan(id) {
  const i = planSel.indexOf(id);
  if (i >= 0) planSel.splice(i, 1); else planSel.push(id);
  savePlanSel(); renderPlanTray();
}
function addManyToPlan(ids) {
  let added = 0;
  for (const id of ids) if (!planSel.includes(id)) { planSel.push(id); added++; }
  savePlanSel(); renderPlanTray();
  const tray = document.getElementById("plan-tray");
  tray.classList.add("flash");
  setTimeout(() => tray.classList.remove("flash"), 600);
}

// 地図の b-plan レイヤ（紫ハイライト）を現在の計画選択に合わせる
function updatePlanLayer() {
  if (!map.getLayer || !map.getLayer("b-plan")) return;   // 地図読み込み前はスキップ
  map.setFilter("b-plan", ["in", ["get", "id"], ["literal", planSel]]);
}

const planTray = document.getElementById("plan-tray");
function renderPlanTray() {
  document.getElementById("pt-count").textContent = planSel.length;
  planTray.classList.toggle("empty", planSel.length === 0);
  document.getElementById("pt-save").disabled = planSel.length === 0;
  updatePlanLayer();   // 紫ハイライトを地図に反映
  const ul = document.getElementById("pt-list");
  ul.innerHTML = "";
  for (const id of planSel) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${id}</span><button class="pt-rm" title="計画から外す">外す</button>`;
    li.querySelector(".pt-rm").addEventListener("click", () => {
      togglePlan(id);
      // いま開いている確認カードのボタン表示も合わせる
      if (current === id) { const b = document.getElementById("btn-plan");
        if (b) { b.classList.remove("on"); b.textContent = "＋ 調査計画に追加（現地で確認）"; } }
    });
    ul.appendChild(li);
  }
}
document.getElementById("pt-head").addEventListener("click", () => {
  const hidden = document.getElementById("pt-list").classList.toggle("hidden");
  document.getElementById("pt-caret").textContent = hidden ? "▸" : "▾";
});

// 「QRで渡す」：選んだ棟をサーバーに保存 → 計画ID を受け取り、受け渡しURL と QR を出す
const planModal = document.getElementById("plan-modal");
document.getElementById("pt-save").addEventListener("click", async () => {
  if (!planSel.length) return;
  const btn = document.getElementById("pt-save");
  const orig = btn.textContent; btn.disabled = true; btn.textContent = "保存中…";
  try {
    const res = await fetch("/api/plans", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ building_ids: planSel, created_by: getDeskOperator() }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const d = await res.json();
    // 受け渡しURL は PC のいまのオリジンで作る（LAN/トンネル越しでもスマホが辿れる）
    const url = location.origin + "/survey.html?plan=" + encodeURIComponent(d.plan_id);
    showPlanModal(d, url);
  } catch (e) {
    alert("調査計画の保存に失敗しました：" + e.message);
  } finally {
    btn.disabled = planSel.length === 0; btn.textContent = orig;
  }
});

function showPlanModal(plan, url) {
  // 既定タイトルは「調査計画（N棟）」で棟数を含む。カスタム名のときだけ棟数を添える。
  document.getElementById("pm-title").textContent =
    /（\d+棟）\s*$/.test(plan.title) ? plan.title : `${plan.title}（${plan.count}棟）`;
  document.getElementById("pm-url").value = url;
  const box = document.getElementById("pm-qr");
  box.innerHTML = "";
  try {
    const qr = qrcode(0, "M");   // 0=型番自動、M=誤り訂正中
    qr.addData(url);
    qr.make();
    box.innerHTML = qr.createSvgTag({ cellSize: 6, margin: 2, scalable: true });
  } catch {
    box.textContent = "QR を生成できませんでした（URL をコピーして渡してください）";
  }
  planModal.classList.remove("hidden");
}
document.getElementById("pm-close").addEventListener("click", () =>
  planModal.classList.add("hidden"));
document.getElementById("pm-copy").addEventListener("click", async () => {
  const input = document.getElementById("pm-url");
  try { await navigator.clipboard.writeText(input.value); }
  catch { input.select(); document.execCommand && document.execCommand("copy"); }
  const b = document.getElementById("pm-copy"); const o = b.textContent;
  b.textContent = "コピーしました"; setTimeout(() => (b.textContent = o), 1500);
});

// 「進捗」：保存済みの計画を一覧し、何棟中何棟終わったかを出す
const plansModal = document.getElementById("plans-modal");
document.getElementById("pt-plans").addEventListener("click", loadPlans);
document.getElementById("pl-close").addEventListener("click", () =>
  plansModal.classList.add("hidden"));
async function loadPlans() {
  const box = document.getElementById("pl-list");
  box.innerHTML = "<p class='pl-empty'>読み込み中…</p>";
  plansModal.classList.remove("hidden");
  try {
    const list = await (await fetch("/api/plans")).json();
    if (!list.length) { box.innerHTML = "<p class='pl-empty'>保存済みの調査計画はありません。</p>"; return; }
    box.innerHTML = "";
    for (const p of list) {
      const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
      const by = p.created_by ? `・${p.created_by}` : "";
      const row = document.createElement("div");
      row.className = "pl-row";
      row.innerHTML =
        `<div class="pl-top"><span class="pl-title">${p.title}</span>` +
        `<span class="pl-frac">${p.done} / ${p.total} 棟</span></div>` +
        `<div class="pl-bar"><span style="width:${pct}%"></span></div>` +
        `<div class="pl-meta">${p.created_at}${by}　<a href="/survey.html?plan=${p.plan_id}" ` +
        `target="_blank" rel="noopener">現地調査で開く ↗</a></div>`;
      box.appendChild(row);
    }
  } catch (e) { box.innerHTML = "<p class='pl-empty'>進捗を取得できませんでした。</p>"; }
}

// --- 地図を囲んで調査計画にまとめて追加（3Dの建物をドラッグで囲む） --------
// queryRenderedFeatures で矩形内に描画されている調査対象（b-tgt）を拾い、計画に入れる。
// 3D（pitch あり）でも「見た目で囲んだ範囲」をそのまま拾えるのでピッチ補正は不要。
(function setupMapRegionSelect() {
  const mapEl = document.getElementById("map");
  const box = document.getElementById("map-box");
  const btn = document.getElementById("region-toggle");
  const hint = document.getElementById("region-hint");
  let on = false, start = null;
  const pt = (ev) => {
    const r = mapEl.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  };
  function setMode(v) {
    on = v;
    btn.classList.toggle("on", on);
    btn.textContent = on ? "✓ 囲みモード中（解除）" : "⬚ 囲んで計画に追加";
    hint.classList.toggle("hidden", !on);
    hint.textContent = "ドラッグで囲んだ調査対象を計画に追加します";
    mapEl.classList.toggle("region-on", on);
    // ドラッグを矩形選択に使うため、地図のパン/ボックスズームを止める
    if (on) { map.dragPan.disable(); map.boxZoom.disable(); }
    else { map.dragPan.enable(); map.boxZoom.enable(); }
    start = null; box.style.display = "none";
  }
  btn.addEventListener("click", () => setMode(!on));

  mapEl.addEventListener("pointerdown", (ev) => {
    if (!on) return;
    ev.preventDefault();
    try { mapEl.setPointerCapture(ev.pointerId); } catch {}
    start = pt(ev);
    box.style.display = "block";
    box.style.left = start.x + "px"; box.style.top = start.y + "px";
    box.style.width = "0px"; box.style.height = "0px";
  });
  mapEl.addEventListener("pointermove", (ev) => {
    if (!on || !start) return;
    const p = pt(ev);
    box.style.left = Math.min(start.x, p.x) + "px";
    box.style.top = Math.min(start.y, p.y) + "px";
    box.style.width = Math.abs(p.x - start.x) + "px";
    box.style.height = Math.abs(p.y - start.y) + "px";
  });
  mapEl.addEventListener("pointerup", (ev) => {
    if (!on || !start) return;
    const p = pt(ev), s = start; start = null; box.style.display = "none";
    const x0 = Math.min(s.x, p.x), y0 = Math.min(s.y, p.y);
    const x1 = Math.max(s.x, p.x), y1 = Math.max(s.y, p.y);
    if (x1 - x0 < 5 && y1 - y0 < 5) return;   // タップ扱い（誤爆回避）
    const feats = map.queryRenderedFeatures([[x0, y0], [x1, y1]], { layers: ["b-tgt"] });
    const ids = [...new Set(feats.map((f) => f.properties.id))];
    if (!ids.length) { hint.textContent = "囲んだ範囲に調査対象はありませんでした"; return; }
    addManyToPlan(ids);
    hint.textContent = `${ids.length} 棟を調査計画に追加しました（左下トレイ →「QRで渡す」）`;
  });
  mapEl.addEventListener("pointercancel", () => { start = null; box.style.display = "none"; });
})();

// --- 共有（更新候補 CSV/GeoJSON を Web Share API でファイル共有） -----------
// 対応端末（navigator.canShare でファイル共有が可能）でだけボタンを出す。
function canShareFiles() {
  try {
    return !!(navigator.canShare && navigator.canShare(
      { files: [new File(["x"], "t.txt", { type: "text/plain" })] }));
  } catch { return false; }
}
async function shareUpdateCandidates(btn) {
  const orig = btn.textContent; btn.disabled = true; btn.textContent = "準備中…";
  try {
    const [csv, geo] = await Promise.all([
      fetch("/api/export.csv").then((r) => r.blob()),
      fetch("/api/export.geojson").then((r) => r.blob()),
    ]);
    const files = [
      new File([csv], "update_candidates_13104.csv", { type: "text/csv" }),
      new File([geo], "update_candidates_13104.geojson", { type: "application/geo+json" }),
    ];
    const payload = { title: "まちカルテ 更新候補",
      text: "まちカルテで確定した更新候補（CSV / GeoJSON）です。" };
    if (navigator.canShare && navigator.canShare({ ...payload, files })) {
      await navigator.share({ ...payload, files });
    } else {
      await navigator.share({ ...payload, files: [files[0]] });  // 複数不可なら CSV のみ
    }
  } catch (e) {
    if (e && e.name === "AbortError") { /* ユーザーが共有を閉じた：何もしない */ }
    else alert("共有できませんでした：" + (e && e.message || e));
  } finally { btn.disabled = false; btn.textContent = orig; }
}
const dlShare = document.getElementById("dl-share");
if (canShareFiles()) {
  dlShare.classList.remove("hidden");
  dlShare.addEventListener("click", () => shareUpdateCandidates(dlShare));
}

// --- 起動 -------------------------------------------------------------------
// デモ演出（demo.js）へ map と数字アニメーションを渡す
window.MK.map = map;
window.MK.animateCount = animateCount;
renderPlanTray();

loadStats();
loadUsageCodes();
loadMetrics();
loadExportSummary();
loadTargets();
loadUnresolved();
map.on("load", addBuildingLayers);
