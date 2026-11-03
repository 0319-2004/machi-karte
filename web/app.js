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

// 初期表示：西新宿・都庁周辺を斜め俯瞰
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

const COLOR_NON = "#b0bec5";   // 対象外（灰）
const COLOR_TGT = "#e64a19";   // 調査対象（強調・橙赤）
const COLOR_SEL = "#ffd600";   // 選択中（黄）
const HEIGHT_EXPR = ["coalesce", ["get", "h"], 3];

let stats = null;
let targetsOnly = false;

// --- 件数カウンタ -----------------------------------------------------------
const elTotal = document.getElementById("count-total");
const elTarget = document.getElementById("count-target");
const elReduction = document.getElementById("count-reduction");

function animateCount(el, from, to, dur = 1500) {
  const t0 = performance.now();
  function step(now) {
    const p = Math.min(1, (now - t0) / dur);
    const e = 1 - Math.pow(1 - p, 3); // ease-out cubic
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
  // 選択中の棟を上に重ねて強調
  map.addLayer({
    id: "b-sel", type: "fill-extrusion", source: "buildings",
    filter: ["==", ["get", "id"], "__none__"],
    paint: {
      "fill-extrusion-height": HEIGHT_EXPR, "fill-extrusion-base": 0,
      "fill-extrusion-color": COLOR_SEL, "fill-extrusion-opacity": 1,
    },
  });

  // ポリゴンのクリックでも属性を開く
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

// --- 優先度順リスト ---------------------------------------------------------
async function loadTargets() {
  const list = await (await fetch("/api/targets?limit=100")).json();
  const ol = document.getElementById("target-list");
  ol.innerHTML = "";
  for (const t of list) {
    const li = document.createElement("li");
    li.dataset.id = t.id;
    li.dataset.lon = t.lon;
    li.dataset.lat = t.lat;
    const h = t.height_m != null ? t.height_m.toFixed(1) + "m" : "高さ不明";
    li.innerHTML =
      `<div class="bid">${t.id}</div>` +
      `<div class="meta">${h}・用途 ${t.usage_label || "不明"}</div>`;
    li.addEventListener("click", () => {
      map.flyTo({ center: [t.lon, t.lat], zoom: 17, pitch: 60, duration: 1500 });
      selectBuilding(t.id, li);
    });
    ol.appendChild(li);
  }
}

// --- 属性パネル -------------------------------------------------------------
const panel = document.getElementById("panel");
const panelBody = document.getElementById("panel-body");
document.getElementById("panel-close").addEventListener("click", () => {
  panel.classList.add("hidden");
  map.setFilter("b-sel", ["==", ["get", "id"], "__none__"]);
  document.querySelectorAll("#target-list li.active").forEach((x) => x.classList.remove("active"));
});

function row(label, value, raw) {
  const rawHtml = raw != null && String(raw) !== String(value ?? "")
    ? `<div class="raw">raw: ${raw}</div>` : "";
  return `<tr><th>${label}</th><td>${value ?? "—"}${rawHtml}</td></tr>`;
}

async function selectBuilding(id, li) {
  map.setFilter("b-sel", ["==", ["get", "id"], id]);
  document.querySelectorAll("#target-list li.active").forEach((x) => x.classList.remove("active"));
  if (li) li.classList.add("active");

  const d = await (await fetch(`/api/buildings/${id}`)).json();
  if (d.detail) return;
  const a = d.attributes;
  const usageText = a.usage.label
    ? `${a.usage.label}（${a.usage.value}）`
    : (a.usage.raw === "461" ? "不明（461）" : a.usage.value ?? "—");

  panelBody.innerHTML =
    `<h3>${a.id}</h3>` +
    (a.is_target ? `<span class="badge">調査対象（不明群）</span>` : "") +
    `<table>` +
    row("用途", usageText, a.usage.raw) +
    row("詳細用途", a.detailed_usage.value, a.detailed_usage.raw) +
    row("階数（地上）", a.storeys_above.value, a.storeys_above.raw) +
    row("調査年", a.survey_year.value, a.survey_year.raw) +
    row("高さ", a.height_m.value != null ? a.height_m.value + " m" : null, a.height_m.raw) +
    row("屋根面積", a.roof_edge_area.value != null ? a.roof_edge_area.value + " ㎡" : null, a.roof_edge_area.raw) +
    row("作成日", a.creation_date) +
    row("LOD", a.lod_type) +
    `</table>` +
    `<div class="note">推定候補・人の確定は Phase 4–5 で表示（候補 ${d.candidates.length} 件 / 確定 ${d.confirmations.length} 件）。</div>`;
  panel.classList.remove("hidden");
}

// --- 起動 -------------------------------------------------------------------
loadStats();
loadTargets();
map.on("load", addBuildingLayers);
