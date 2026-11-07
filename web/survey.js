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
  // kepler.gl 調：地理院タイル（淡色）を暗く沈める（出典表示は維持）
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#0e1116" } },
    { id: "gsi", type: "raster", source: "gsi",
      paint: { "raster-opacity": 0.8, "raster-brightness-max": 0.45,
               "raster-saturation": -0.3, "raster-contrast": 0.1 } },
  ],
};
const COLOR_TGT = "#ff6a2b", COLOR_SEL = "#43d19e";
const GROUP_COLORS = ["#2ad4e8", "#ff6a2b", "#b06bff", "#43d19e", "#ffca3a",
                      "#5ea0ff", "#ff6b9d", "#7ee787"];
const GROUP_RADIUS_M = 200;

const lsGet = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };

// --- 「ここへ行く」：外部の地図アプリで徒歩ルートを開く -----------------------
// アプリ内に経路は描かない。渡すのは目的地（建物の重心）だけ。出発地は渡さず、
// 現在地は外部アプリ側（Apple/Google マップ）が扱う。こちらでは位置情報を保存しない。
function navDirectionsUrl(lat, lon) {
  // iOS は Apple マップ、それ以外は Google マップ。どちらも徒歩（walking）指定。
  // 端末判定は既存の isIOS（iPhone/iPad/iPod・iPadOS 対応）を使う。
  if (isIOS) return `https://maps.apple.com/?daddr=${lat},${lon}&dirflg=w`;
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}&travelmode=walking`;
}
function goTo(id) {
  const t = targetsById[id];
  if (!t || t.lon == null || t.lat == null) return;
  // 新しいタブ/外部アプリで開く（この画面の入力は消えない）
  window.open(navDirectionsUrl(t.lat, t.lon), "_blank", "noopener");
}

// --- アプリ内の徒歩経路（OSRM。線はこちらで描く。ターンバイターンの案内はしない） ----
// 現在地→目的の建物の徒歩経路を、入力画面のミニ地図と俯瞰地図に描く。
// 距離と線形は OSRM の経路、所要時間は「徒歩＝80m/分」で算出する
//（公開デモサーバーは profile を無視して車速の duration を返すため、時間は距離から出す）。
// 取得に失敗したら直線距離＋方向だけ出す（止まらない）。
// 位置情報は経路取得のためだけに使い、保存しない。OSRM に送るのは座標のみ。
const OSRM = "https://router.project-osrm.org";   // 本番は自前サーバーに差し替える（README 参照）
let miniMeMarker = null, planMeMarker = null;

function getCurrentPos() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(userPos);
    navigator.geolocation.getCurrentPosition(
      (p) => { userPos = { lon: p.coords.longitude, lat: p.coords.latitude }; resolve(userPos); },
      () => resolve(userPos),             // 取れなければ既知の userPos（無ければ null）。保存はしない
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 30000 });
  });
}
async function fetchFootRoute(from, to) {
  const u = `${OSRM}/route/v1/foot/${from.lon},${from.lat};${to.lon},${to.lat}?overview=full&geometries=geojson`;
  const res = await fetch(u);
  if (!res.ok) throw new Error("osrm " + res.status);
  const j = await res.json();
  const r = j.routes && j.routes[0];
  if (!r || !r.geometry) throw new Error("no route");
  return r;                              // {geometry(LineString), distance(m), duration(s)}
}
// 徒歩の所要時間は距離から（80m/分＝不動産表示の慣行）。OSRM の duration は使わない。
function walkMinutes(m) { return Math.max(1, Math.round(m / 80)); }
function fmtWalk(m) { return `徒歩約${walkMinutes(m)}分・${fmtDist(m)}`; }
function bearingDeg(a, b) {
  const rad = Math.PI / 180;
  const y = Math.sin((b.lon - a.lon) * rad) * Math.cos(b.lat * rad);
  const x = Math.cos(a.lat * rad) * Math.sin(b.lat * rad)
          - Math.sin(a.lat * rad) * Math.cos(b.lat * rad) * Math.cos((b.lon - a.lon) * rad);
  return (Math.atan2(y, x) / rad + 360) % 360;
}
function compass(deg) {
  return ["北", "北東", "東", "南東", "南", "南西", "西", "北西"][Math.round(deg / 45) % 8] + "の方向";
}
function routeFC(coords) {
  return { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } };
}
function drawRouteLine(map, feature, approx) {
  const ID = "walk-route";
  const run = () => {
    const data = feature || routeFC([]);
    if (map.getSource(ID)) map.getSource(ID).setData(data);
    else {
      map.addSource(ID, { type: "geojson", data });
      map.addLayer({ id: ID, type: "line", source: ID,
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-width": 5, "line-opacity": 0.9, "line-color": "#2ad4e8" } });
    }
    // 実経路＝シアン、直線フォールバック＝橙で区別する
    map.setPaintProperty(ID, "line-color", approx ? COLOR_TGT : "#2ad4e8");
  };
  // スタイルが読めていれば即描く。map.loaded() はタイル保留中に false を返し、
  // そのとき once("load") が再発火せずレイヤが付かないため isStyleLoaded() で判定する。
  if (map.isStyleLoaded()) run(); else map.once("load", run);
}
function setRouteInfo(el, text, approx) {
  if (!el) return;
  el.textContent = "🚶 " + text;
  el.classList.remove("hidden");
  el.classList.toggle("approx", !!approx);
}
function fitRoute(map, coords) {
  if (!coords || !coords.length) return;
  const b = new maplibregl.LngLatBounds();
  coords.forEach((c) => b.extend(c));
  if (!b.isEmpty()) map.fitBounds(b, { padding: 50, maxZoom: 17, duration: 500 });
}
function meEl() { const e = document.createElement("div"); e.className = "me-dot"; return e; }

// 現在地→目的地の経路を map に描く。fit=true なら経路全体に合わせてズーム（ミニ地図用）。
// 俯瞰地図は全ピン表示を保つため fit=false（線とピンだけ重ねる）。
async function renderWalkRoute(map, meMarker, targetId, infoEl, fit) {
  const t = targetsById[targetId];
  if (!map || !t || t.lon == null) return;
  const pos = await getCurrentPos();
  if (!pos) {                             // 現在地が取れない：経路も直線も描けない
    drawRouteLine(map, routeFC([]));
    if (meMarker) meMarker.remove();
    setRouteInfo(infoEl, "現在地が取れません（「ここへ行く」で地図アプリを使えます）", true);
    return;
  }
  if (meMarker) meMarker.setLngLat([pos.lon, pos.lat]).addTo(map);
  try {
    const r = await fetchFootRoute(pos, t);
    drawRouteLine(map, routeFC(r.geometry.coordinates), false);
    setRouteInfo(infoEl, fmtWalk(r.distance), false);
    if (fit) fitRoute(map, r.geometry.coordinates);
  } catch {
    // フォールバック：直線距離＋方向（止まらない）
    const d = distM(pos, t);
    drawRouteLine(map, routeFC([[pos.lon, pos.lat], [t.lon, t.lat]]), true);
    setRouteInfo(infoEl, `直線 ${fmtDist(d)}・${compass(bearingDeg(pos, t))}（経路を取得できませんでした）`, true);
    if (fit) fitRoute(map, [[pos.lon, pos.lat], [t.lon, t.lat]]);
  }
}
// 入力画面：いま開いている棟への経路（棟を移動すると自動で更新＝次の棟に切り替わる）
async function updateMiniRoute() {
  const id = planOrder[idx]; if (!id || !miniMap) return;
  if (!miniMeMarker) miniMeMarker = new maplibregl.Marker({ element: meEl() });
  await renderWalkRoute(miniMap, miniMeMarker, id, document.getElementById("route-info"), true);
}
// 俯瞰地図：訪問順の「次に行く棟」（未調査の先頭）への経路
async function updatePlanRoute() {
  if (!planMap || !planOrder.length) return;
  const id = planOrder.find((x) => !lsGet(LS.draft(x), {}).done) || planOrder[0];
  if (!planMeMarker) planMeMarker = new maplibregl.Marker({ element: meEl() });
  await renderWalkRoute(planMap, planMeMarker, id, document.getElementById("plan-route"), false);
}

let usageCodes = [];
let targets = [];                 // 全調査対象（優先度順、coords つき）
let targetsById = {};
let guidanceById = {};
let selected = lsGet(LS.selected, []);
let displayLimit = 100;           // 地図に出す上位N（「もっと表示」で増やす）
let targetTotal = 0;

let pickMap = null, planMap = null;
let selMode = null;   // 範囲選択モード："box"（四角）| "lasso"（なぞる）| null

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
  setupRegionSelect();
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

// 範囲で選択（四角＝範囲 / 指でなぞる＝鉛筆）。モード中は地図のジェスチャを止める。
// 囲んだ中の棟を選ぶが、**全部すでに選択済みなら解除**（同じ所を2度囲むと消える）。
function pointInPoly(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i].x, yi = poly[i].y, xj = poly[j].x, yj = poly[j].y;
    if (((yi > y) !== (yj > y)) && (x < ((xj - xi) * (y - yi)) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}
function applyRegionSelection(ids) {
  if (!ids.length) return;
  // 囲んだ中の半数以上がすでに選択済みなら「まとめて解除」（同じ所を2度囲むと消える）。
  // 半数以上を基準にするのは、2度目に少しズレて囲んでも確実に解除できるようにするため。
  const sel = ids.filter((id) => selected.includes(id)).length;
  if (sel * 2 >= ids.length) {
    selected = selected.filter((id) => !ids.includes(id));
  } else {                                             // それ以外は未選択を追加
    for (const id of ids) if (!selected.includes(id)) selected.push(id);
  }
  lsSet(LS.selected, selected); paintSel(); updateSelbar(); renderPickList();
}
function setupRegionSelect() {
  const wrap = document.querySelector("#view-pick .mapwrap");
  const rect = document.getElementById("box-rect");
  const cv = document.getElementById("lasso-canvas");
  let start = null, path = null;
  const pt = (ev) => { const r = wrap.getBoundingClientRect(); return { x: ev.clientX - r.left, y: ev.clientY - r.top }; };

  function setMode(mode) {
    selMode = selMode === mode ? null : mode;          // 同じボタンで切替、排他
    document.getElementById("box-toggle").classList.toggle("on", selMode === "box");
    document.getElementById("lasso-toggle").classList.toggle("on", selMode === "lasso");
    wrap.classList.toggle("box-on", selMode !== null); // touch-action:none
    const on = selMode !== null;
    // 指のドラッグを選択に使うため、地図側のジェスチャを全部止める（スマホ対策）
    [pickMap.dragPan, pickMap.touchZoomRotate, pickMap.touchPitch, pickMap.doubleClickZoom]
      .forEach((h) => h && (on ? h.disable() : h.enable()));
    start = null; path = null; rect.style.display = "none"; cv.style.display = "none";
  }
  document.getElementById("box-toggle").addEventListener("click", () => setMode("box"));
  document.getElementById("lasso-toggle").addEventListener("click", () => setMode("lasso"));

  const idsInBox = (x0, y0, x1, y1) => shownTargets()
    .filter((t) => { const s = pickMap.project([t.lon, t.lat]); return s.x >= x0 && s.x <= x1 && s.y >= y0 && s.y <= y1; })
    .map((t) => t.id);
  const idsInPoly = (poly) => shownTargets()
    .filter((t) => { const s = pickMap.project([t.lon, t.lat]); return pointInPoly(s.x, s.y, poly); })
    .map((t) => t.id);
  function drawLasso() {
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, cv.width, cv.height);
    if (!path || path.length < 2) return;
    ctx.fillStyle = "rgba(142,36,170,.14)"; ctx.strokeStyle = "#8e24aa";
    ctx.lineWidth = 2.5; ctx.lineJoin = ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(path[0].x, path[0].y);
    for (let i = 1; i < path.length; i++) ctx.lineTo(path[i].x, path[i].y);
    ctx.closePath(); ctx.fill(); ctx.stroke();
  }

  wrap.addEventListener("pointerdown", (ev) => {
    if (!selMode) return;
    // ツールバー/情報カードのボタンは素通し（押しても選択を始めない＝モード切替が効く）
    if (ev.target.closest && ev.target.closest(".pick-tools, #pick-info")) return;
    ev.preventDefault();
    try { wrap.setPointerCapture(ev.pointerId); } catch {}
    const p = pt(ev);
    if (selMode === "box") {
      start = p; rect.style.display = "block";
      rect.style.left = p.x + "px"; rect.style.top = p.y + "px"; rect.style.width = "0px"; rect.style.height = "0px";
    } else {
      path = [p]; cv.width = wrap.clientWidth; cv.height = wrap.clientHeight; cv.style.display = "block";
    }
  });
  wrap.addEventListener("pointermove", (ev) => {
    if (!selMode) return;
    if (selMode === "box" && start) {
      ev.preventDefault(); const p = pt(ev);
      rect.style.left = Math.min(start.x, p.x) + "px"; rect.style.top = Math.min(start.y, p.y) + "px";
      rect.style.width = Math.abs(p.x - start.x) + "px"; rect.style.height = Math.abs(p.y - start.y) + "px";
    } else if (selMode === "lasso" && path) {
      ev.preventDefault(); path.push(pt(ev)); drawLasso();
    }
  });
  const finish = (ev) => {
    if (!selMode) return;
    if (selMode === "box" && start) {
      const p = pt(ev);
      const x0 = Math.min(start.x, p.x), x1 = Math.max(start.x, p.x);
      const y0 = Math.min(start.y, p.y), y1 = Math.max(start.y, p.y);
      start = null; rect.style.display = "none";
      if (x1 - x0 < 6 && y1 - y0 < 6) return;        // タップ扱い（誤爆回避）
      applyRegionSelection(idsInBox(x0, y0, x1, y1));
    } else if (selMode === "lasso" && path) {
      const poly = path; path = null; cv.style.display = "none";
      if (poly.length >= 3) applyRegionSelection(idsInPoly(poly));
    }
  };
  wrap.addEventListener("pointerup", finish);
  wrap.addEventListener("pointercancel", () => { start = null; path = null; rect.style.display = "none"; cv.style.display = "none"; });
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
  const nowHidden = list.classList.toggle("hidden");      // true=地図表示に戻った
  e.currentTarget.classList.toggle("on", !nowHidden);
  e.currentTarget.textContent = nowHidden ? "リスト" : "地図";  // 戻り先を明示
  if (nowHidden && pickMap) setTimeout(() => pickMap.resize(), 50);  // 地図を描き直す
});
document.getElementById("more-btn").addEventListener("click", () => {
  displayLimit = displayLimit >= 2000 ? targets.length
    : displayLimit >= 500 ? 2000 : displayLimit >= 100 ? 500 : 100;
  refreshPickSource(); renderPickList();
});

// --- 更新候補の出力（スマホから直接ダウンロード／生成） ---------------------
const dlPanel = document.getElementById("dl-panel");
document.getElementById("dl-open").addEventListener("click", async () => {
  dlPanel.classList.remove("hidden");
  try {
    const s = await (await fetch("/api/export/summary")).json();
    const n = s.buildings || 0;
    document.getElementById("dl-count").textContent = n > 0
      ? `確定済み ${n} 棟から出力します（CSV / GeoJSON / レポート）。`
      : "まだ確定した棟がありません。確定すると出力できます。";
    const dis = n === 0;
    document.getElementById("dl-csv").classList.toggle("disabled", dis);
    document.getElementById("dl-geojson").classList.toggle("disabled", dis);
    document.getElementById("dl-report").disabled = dis;
  } catch { document.getElementById("dl-count").textContent = "件数を取得できませんでした。"; }
});
document.getElementById("dl-close").addEventListener("click", () => dlPanel.classList.add("hidden"));
document.getElementById("dl-report").addEventListener("click", async (e) => {
  const btn = e.currentTarget, old = btn.textContent;
  btn.disabled = true; btn.textContent = "生成中…（Claudeで要約・30秒ほど）";
  try {
    const res = await fetch("/api/report?city_code=13104", { method: "POST" });
    if (!res.ok) throw new Error(String(res.status));
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "report_13104.md";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    btn.textContent = "生成しました";
  } catch { btn.textContent = "生成に失敗しました"; }
  setTimeout(() => { btn.disabled = false; btn.textContent = old; }, 2500);
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
  updatePlanRoute();                    // 「次に行く棟」への徒歩経路を俯瞰地図に描く
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
let planPopup = null;
function clearPlanMarkers() { planMarkers.forEach((m) => m.remove()); planMarkers = []; }

// 俯瞰地図のピンをタップ → 「ここへ行く」ボタン付きの小ポップアップ
function openPlanPopup(id, lon, lat) {
  if (planPopup) planPopup.remove();
  const html = `<div class="nav-pop"><div class="np-id">${id}</div>` +
    `<button type="button" class="np-go">🧭 ここへ行く</button></div>`;
  planPopup = new maplibregl.Popup({ offset: 26, closeButton: true, closeOnClick: true })
    .setLngLat([lon, lat]).setHTML(html).addTo(planMap);
  planPopup.getElement().querySelector(".np-go")
    .addEventListener("click", () => goTo(id));
}

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
        el.className = "pin"; el.style.background = color; el.style.cursor = "pointer";
        el.innerHTML = `<span>${n}</span>`;
        el.addEventListener("click", (e) => { e.stopPropagation(); openPlanPopup(id, t.lon, t.lat); });
        planMarkers.push(new maplibregl.Marker({ element: el, anchor: "bottom" })
          .setLngLat([t.lon, t.lat]).addTo(planMap));
        bounds.extend([t.lon, t.lat]);
      });
    });
    // 現在地の点は updatePlanRoute 側の planMeMarker が担当（経路取得で得た位置を使う）
    if (userPos) bounds.extend([userPos.lon, userPos.lat]);
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
      `グループ${gi + 1}（${grp.length}棟）` +
      `<button type="button" class="grp-go">🧭 このグループへ行く</button>`;
    // グループの先頭（訪問順の最初）の棟へ徒歩ルートを出す
    head.querySelector(".grp-go").addEventListener("click", (e) => {
      e.stopPropagation(); goTo(grp[0]);
    });
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
  updatePlanRoute();                        // 次に行く棟が変わっていれば経路も更新
  setTimeout(() => planMap && planMap.resize(), 50);
});
// 入力画面の「ここへ行く」：いま開いている棟へ徒歩ルートを出す
document.getElementById("go-here").addEventListener("click", () => {
  const id = planOrder[idx]; if (id) goTo(id);
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
  vAsked = 0; vGivenUp = new Set(); vEst = { usage: "none", storeys: "none" };
  vProposal = null; hideQuestion(); hideProposal();   // 棟ごとに聞き返し・確認をリセット
  idx = i;
  const id = planOrder[idx];
  document.getElementById("survey-bid").textContent = id;
  document.getElementById("survey-prog").textContent = `${idx + 1} / ${planOrder.length}`;
  document.getElementById("survey-err").classList.add("hidden");

  const t = targetsById[id] || {};
  placeMini(t.lon, t.lat);
  updateMiniRoute();                 // この棟への徒歩経路（棟を移動すると自動で更新）

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
    vEst = { usage: estState(cu), storeys: estState(cs) };  // 聞き返しの出し分け用
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
  setPhoto(draft && draft.photo, draft ? draft.photo_faces : null);
  document.getElementById("saved-flag").classList.toggle("hidden", !draft);

  const saveBtn = document.getElementById("nav-save");
  const isDone = !!(draft && draft.done);
  saveBtn.textContent = isDone ? "保存済み（上書き保存）" : "保存して次へ";
  saveBtn.classList.toggle("done", isDone);

  refreshVoiceFill();              // いまの埋まり具合を小さく表示
}

function readForm() {
  const st = document.getElementById("f-storeys").value;
  return {
    building_id: planOrder[idx],
    usage: document.getElementById("f-usage").value || null,
    storeys: st === "" ? null : Math.max(1, parseInt(st, 10) || 0) || null,
    note: document.getElementById("f-note").value.trim() || null,
    photo: currentPhoto,
    photo_faces: currentPhotoFaces,
  };
}
function saveDraft() {
  const id = planOrder[idx];
  const prev = lsGet(LS.draft(id), {});
  lsSet(LS.draft(id), { ...readForm(), done: prev.done || false });
  document.getElementById("saved-flag").classList.remove("hidden");
}
function onFieldInput() {
  saveDraft();
  if (!voiceSupported) return;
  // 手入力でも質問に答えられる：保留中の質問の項目が埋まったら、その質問を消す
  if (vPending) {
    const v = vPending === "usage"
      ? document.getElementById("f-usage").value
      : document.getElementById("f-storeys").value;
    if ((vPending === "usage" && v) || (vPending === "storeys" && v !== "")) hideQuestion();
  }
  refreshVoiceFill();
}
for (const elid of ["f-usage", "f-storeys", "f-note"])
  document.getElementById(elid).addEventListener("input", onFieldInput);

// 写真（カメラ→端末内で縮小→顔を自動ぼかし→手塗り確認→加工後だけ保存）
// 画像はサーバーに送らず端末内で処理する。元画像は端末にも残さない（currentPhoto は加工後のみ）。
let currentPhoto = null;
let currentPhotoFaces = null;     // 自動ぼかしした顔の件数（未対応なら null）
function setPhoto(dataUrl, faces) {
  currentPhoto = dataUrl || null;
  currentPhotoFaces = currentPhoto ? (faces == null ? null : faces) : null;
  const thumb = document.getElementById("photo-thumb");
  const del = document.getElementById("photo-del");
  const note = document.getElementById("photo-note");
  if (currentPhoto) { thumb.src = currentPhoto; thumb.style.display = "inline"; del.classList.remove("hidden"); }
  else { thumb.removeAttribute("src"); thumb.style.display = "none"; del.classList.add("hidden"); }
  if (note) note.classList.toggle("alert", !!currentPhoto);
}

// ファイル → 端末内で縮小したキャンバス（元画像はここから外に出さない）
function fileToCanvas(file) {
  return new Promise((resolve, reject) => {
    const img = new Image(); const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, PHOTO_MAX / Math.max(img.width, img.height));
      const w = Math.round(img.width * scale), h = Math.round(img.height * scale);
      const c = document.createElement("canvas"); c.width = w; c.height = h;
      c.getContext("2d").drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url); resolve(c);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("画像を読めません")); };
    img.src = url;
  });
}

// --- 顔の自動ぼかし（MediaPipe Face Detector, Apache-2.0） -------------------
// 端末内で推論。ローダ/WASM/モデルは CDN ではなく自サーバー（/vendor/mediapipe/）から
// 読み込む＝デモ中に通信が不安定でも動く。WASM 未取得・非対応・失敗は null を返して
// 手塗りに進む（止まらない）。画像はどこにも送らない。
const MP_DIR = "/vendor/mediapipe";   // 配置は web/vendor/mediapipe/（make fetch-mediapipe）
let faceDetectorPromise = null;
function getFaceDetector() {
  if (faceDetectorPromise) return faceDetectorPromise;
  faceDetectorPromise = (async () => {
    const vision = await import(`${MP_DIR}/vision_bundle.mjs`);
    const resolver = await vision.FilesetResolver.forVisionTasks(`${MP_DIR}/wasm`);
    return vision.FaceDetector.createFromOptions(resolver, {
      baseOptions: { modelAssetPath: `${MP_DIR}/blaze_face_short_range.tflite` },
      runningMode: "IMAGE",
    });
  })().catch(() => { faceDetectorPromise = null; return null; });  // 失敗は null（手塗りへ）
  return faceDetectorPromise;
}
// 領域をモザイク化（ぼかし。canvas filter 非対応端末でも効く＝縮小→拡大）
function pixelate(ctx, src, x, y, w, h) {
  x = Math.round(x); y = Math.round(y); w = Math.round(w); h = Math.round(h);
  if (w <= 0 || h <= 0) return;
  const blocks = 9;
  const tmp = document.createElement("canvas"); tmp.width = blocks; tmp.height = blocks;
  tmp.getContext("2d").drawImage(src, x, y, w, h, 0, 0, blocks, blocks);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(tmp, 0, 0, blocks, blocks, x, y, w, h);
  ctx.imageSmoothingEnabled = true;
}
// 検出した顔（少し広げて）をぼかす。検出数を返す。-1 は自動ぼかし未実施。
async function autoBlurFaces(canvas) {
  const detector = await getFaceDetector();
  if (!detector) return -1;
  let dets;
  try { dets = (detector.detect(canvas).detections) || []; }
  catch { return -1; }
  const ctx = canvas.getContext("2d");
  for (const d of dets) {
    const b = d.boundingBox; if (!b) continue;
    const ex = b.width * 0.3, ey = b.height * 0.35;
    const x = Math.max(0, b.originX - ex), y = Math.max(0, b.originY - ey);
    const w = Math.min(canvas.width - x, b.width + 2 * ex);
    const h = Math.min(canvas.height - y, b.height + 2 * ey);
    pixelate(ctx, canvas, x, y, w, h);
  }
  return dets.length;
}

// --- 手塗り（黒塗り）エディタ -----------------------------------------------
let edBase = null;        // 自動ぼかし済みの土台キャンバス
let edStrokes = [];       // [{w, pts:[{x,y}...]}, ...]
let edWidth = 18, edFaces = null, edDrawing = false;
function edRender() {
  const cv = document.getElementById("redact-canvas");
  const ctx = cv.getContext("2d");
  ctx.drawImage(edBase, 0, 0);
  ctx.strokeStyle = "#000"; ctx.fillStyle = "#000";
  ctx.lineJoin = ctx.lineCap = "round";
  for (const s of edStrokes) {
    if (s.pts.length === 1) { ctx.beginPath();
      ctx.arc(s.pts[0].x, s.pts[0].y, s.w / 2, 0, Math.PI * 2); ctx.fill(); continue; }
    ctx.lineWidth = s.w; ctx.beginPath();
    ctx.moveTo(s.pts[0].x, s.pts[0].y);
    for (let i = 1; i < s.pts.length; i++) ctx.lineTo(s.pts[i].x, s.pts[i].y);
    ctx.stroke();
  }
}
function edPoint(ev) {
  const cv = document.getElementById("redact-canvas");
  const r = cv.getBoundingClientRect();
  return { x: (ev.clientX - r.left) * (cv.width / r.width),
           y: (ev.clientY - r.top) * (cv.height / r.height) };
}
function openRedactEditor(baseCanvas, faces) {
  edBase = baseCanvas; edStrokes = []; edFaces = faces;
  const cv = document.getElementById("redact-canvas");
  cv.width = baseCanvas.width; cv.height = baseCanvas.height;
  edRender();
  document.getElementById("redact-msg").textContent = faces >= 0
    ? `顔は自動で隠しました（${faces}件）。ナンバープレート・表札・人が写っていないか確認し、必要なら指で塗ってください。`
    : "この端末では顔の自動ぼかしが使えません。顔・ナンバープレート・表札・人が写っていないか確認し、指で塗ってください。";
  document.getElementById("redact").classList.remove("hidden");
}
function closeRedactEditor() {
  document.getElementById("redact").classList.add("hidden");
  edBase = null; edStrokes = [];   // 元画像を含め端末に残さない
}

document.getElementById("f-photo").addEventListener("change", async (e) => {
  const file = e.target.files && e.target.files[0]; e.target.value = "";
  if (!file) return;
  try {
    const canvas = await fileToCanvas(file);     // 端末内で縮小
    const faces = await autoBlurFaces(canvas);    // 端末内で顔ぼかし（非対応は -1）
    openRedactEditor(canvas, faces);              // 確認＋手塗り
  } catch { showErr("写真を読み込めませんでした"); }
});
document.getElementById("photo-del").addEventListener("click", () => { setPhoto(null); saveDraft(); });

// 手塗りの操作
(function wireRedact() {
  const cv = document.getElementById("redact-canvas");
  const start = (ev) => { edDrawing = true; edStrokes.push({ w: edWidth, pts: [edPoint(ev)] }); edRender(); ev.preventDefault(); };
  const move = (ev) => { if (!edDrawing) return; edStrokes[edStrokes.length - 1].pts.push(edPoint(ev)); edRender(); ev.preventDefault(); };
  const end = () => { edDrawing = false; };
  cv.addEventListener("pointerdown", start);
  cv.addEventListener("pointermove", move);
  cv.addEventListener("pointerup", end);
  cv.addEventListener("pointercancel", end);
  cv.addEventListener("pointerleave", end);
  for (const b of document.querySelectorAll(".rt-w"))
    b.addEventListener("click", () => {
      edWidth = Number(b.dataset.w);
      document.querySelectorAll(".rt-w").forEach((x) => x.classList.toggle("on", x === b));
    });
  document.getElementById("redact-undo").addEventListener("click", () => { edStrokes.pop(); edRender(); });
  document.getElementById("redact-cancel").addEventListener("click", closeRedactEditor);
  document.getElementById("redact-done").addEventListener("click", () => {
    edRender();
    const dataUrl = document.getElementById("redact-canvas").toDataURL("image/jpeg", 0.7);
    const faces = edFaces;
    closeRedactEditor();
    setPhoto(dataUrl, faces >= 0 ? faces : null);
    saveDraft();
  });
})();

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
// iOS は Web Speech の連続利用が不安定（2回目以降 onresult が来ない WebKit 既知バグ）。
// そのため iOS では Web Speech をやめ、OS 標準の音声入力（キーボードの🎤）で
// メモ欄に話してもらい、「振り分け」で Claude に渡す。Android/PC は Web Speech のまま。
const isIOS = /iP(hone|ad|od)/.test(navigator.userAgent) ||
  (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const voiceWebSpeech = !!SR && window.isSecureContext && !isIOS;  // iOS 以外：Web Speech
const voiceDictation = isIOS;                                     // iOS：OS標準の音声入力
const voiceSupported = voiceWebSpeech || voiceDictation;          // どちらかで音声ツールを出す

let recog = null;            // SpeechRecognition インスタンス（1個を使い回す）
let voiceErrored = false;    // 認識エラー（finishVoice でメッセージを消さないため）
let voiceActive = false;     // 認識中
let voiceStopping = false;   // 停止ボタン押下（onend で振り分けへ）
let voiceSilent = false;     // 棟移動で止めた（振り分けず、メモも触らない）
let voiceBase = "";          // 音声開始前のメモ（手入力を消さないため）
let voiceFinal = "";         // 確定した認識テキストの蓄積（再開をまたいで保持）
let voiceUsedOnce = false;   // 一度でも起動したか（iOS：2回目以降はマイクを作り直す）
let voiceStarting = false;   // 起動処理中（プライミングの await 中の二重起動を防ぐ）
let voiceNoteSnapshot = "";  // iOS 口述：開始時のメモ（新規に話したぶんの差分検出用）

// 聞き返しのセッション状態（棟ごとにリセット）
let vAsked = 0;              // これまで尋ねた質問数（3往復で打ち切り）
let vPending = null;         // いま表示中の質問が尋ねている項目（usage/storeys）
let vGivenUp = new Set();    // 「分からない」と答えた項目（判断不能として記録・もう聞かない）
let vEst = { usage: "none", storeys: "none" };  // 自動推定の状態（質問の出し分け用）
let vProposal = null;        // 反映前の確認内容（「反映」を押すまで各欄に入れない）

function vStatus(msg, isErr) {
  const el = document.getElementById("voice-status");
  el.textContent = msg || "";
  el.classList.toggle("err", !!isErr);
}
function vBtn(rec) {
  const b = document.getElementById("voice-btn");
  b.classList.toggle("rec", rec);
  if (voiceDictation) b.textContent = rec ? "✓ 話し終えたら振り分け" : "🎤 音声で入力";
  else b.textContent = rec ? "■ 停止" : "🎤 音声で入力";
}
function applyTranscript(interim) {
  // メモ欄 = 開始前メモ + 認識テキスト（確定 + 暫定）。手入力は保持。
  const spoken = (voiceFinal + (interim || "")).trim();
  const sep = voiceBase && spoken ? "\n" : "";
  document.getElementById("f-note").value = voiceBase + sep + spoken;
}

// SpeechRecognition は **1インスタンスを使い回す**（iOS Safari は毎回 new すると
// 2回目以降 onresult が来ない既知不具合のため）。continuous=false で1発話ごと、
// 自動再開はしない（iOS の continuous＋自動再開が不安定なため）。
// 認識の状況・エラーは画面にそのまま出して、端末ごとの不調を見えるようにする。
function ensureRecog() {
  if (recog) return recog;
  const r = new SR();
  r.lang = "ja-JP";
  r.continuous = false;        // iOS 対策：1発話ごと（話し終わると自動で止まる）
  r.interimResults = true;
  r.onstart = () => { voiceErrored = false; vStatus("🎤 聞き取り中…話してください（話し終わると自動で止まります）"); };
  r.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const res = e.results[i];
      if (res.isFinal) voiceFinal += res[0].transcript;
      else interim += res[0].transcript;
    }
    applyTranscript(interim);
    const t = (voiceFinal + interim).trim();
    vStatus(t ? `聞き取り：${t}` : "聞いています…");
  };
  r.onerror = (e) => {
    voiceActive = false; voiceErrored = true; vBtn(false);
    const m = {
      "service-not-allowed": "音声入力がオフのようです。iPhone の『設定＞一般＞キーボード＞音声入力』をオンにしてください（手入力は使えます）",
      "not-allowed": "マイクが許可されていません。アドレスバーの「ぁA」→Webサイトの設定でマイクを許可してください（手入力は使えます）",
      "no-speech": "音声が聞き取れませんでした。もう一度どうぞ",
      "audio-capture": "マイクが見つかりません（手入力は使えます）",
      "network": "音声認識サーバーに接続できませんでした（通信を確認。手入力は使えます）",
      "aborted": "",
    };
    const msg = e.error in m ? m[e.error] : `認識エラー：${e.error}（手入力は使えます）`;
    if (msg) vStatus(msg, true);
  };
  r.onend = () => { finishVoice(); };   // continuous=false：再開せず確定へ
  recog = r;
  return recog;
}

// iOS 対策：2回目以降は getUserMedia でマイクを一瞬だけ掴んで即解放し、
// 録音セッションを作り直してから認識を開始する。これをしないと iOS Safari は
// onstart は来るのに onresult が一度も来ず、2回目が全く録れない。
async function primeMic() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  stream.getTracks().forEach((t) => t.stop());   // すぐ解放（録音はしない）
  await new Promise((r) => setTimeout(r, 250));   // iOS が解放しきるのを待つ
}

async function startVoice() {
  if (!voiceSupported || voiceActive || voiceStarting) return;
  voiceStarting = true;
  try {
    // 確認待ちのまま話し直したら、その未反映ぶんは取り消してメモを戻す
    if (vProposal) { document.getElementById("f-note").value = vProposal.base; vProposal = null; hideProposal(); }
    const r = ensureRecog();     // 使い回し（iOS で2回目以降が録れなくなるのを防ぐ）
    voiceBase = document.getElementById("f-note").value.trim();
    voiceFinal = "";
    voiceStopping = false;
    voiceSilent = false;
    voiceErrored = false;
    if (voiceUsedOnce) {          // 初回は権限プロンプトのためジェスチャ内でそのまま start
      vStatus("🎤 マイク準備中…");
      await primeMic();
    }
    r.start();
    voiceUsedOnce = true;
    voiceActive = true;
    vBtn(true);
    vStatus("🎤 起動中…");
  } catch (err) {
    // 直前のセッションが残っていれば abort して次回に備える
    try { recog && recog.abort(); } catch {}
    voiceActive = false;
    vStatus(`音声を開始できませんでした：${(err && err.name) || err}`, true);
  } finally {
    voiceStarting = false;
  }
}

function stopVoice(silent) {
  if (!voiceActive) return;
  if (voiceDictation) { voiceActive = false; vBtn(false); return; }  // 口述：振り分けず閉じるだけ
  voiceStopping = true;
  voiceActive = false;
  voiceSilent = !!silent;        // 棟移動で止めたときは振り分けずメモも触らない
  try { recog && recog.stop(); } catch {}
}

// 自動推定の状態を質問の出し分け用ラベルに畳む
function estState(c) {
  if (!c) return "none";
  if (c.unresolvable) return "unresolvable";
  return c.confidence_label === "低" ? "low" : "ok";
}
// いまの埋まり具合（サーバーへ送る状態 ＝ 画面の小表示のもと）
function voiceStatusItems() {
  const uVal = document.getElementById("f-usage").value;
  const sVal = document.getElementById("f-storeys").value;
  const uLbl = uVal ? (usageCodes.find((u) => String(u.code) === String(uVal)) || {}).label || uVal : null;
  return {
    usage: { filled: !!uVal, value: uLbl, est: vEst.usage },
    storeys: { filled: sVal !== "", value: sVal === "" ? null : Number(sVal), est: vEst.storeys },
  };
}
function refreshVoiceFill() {
  if (!voiceSupported) return;
  const st = voiceStatusItems();
  const chip = (name, s, item) => {
    if (vGivenUp.has(item)) return `<span class="chip">${name} <span class="gu">判断不能</span></span>`;
    if (s.filled) return `<span class="chip">${name} <span class="ok">✓</span></span>`;
    return `<span class="chip">${name} <span class="empty">空</span></span>`;
  };
  const rounds = vAsked > 0 ? `<span class="chip">音声 ${vAsked}往復</span>` : "";
  const el = document.getElementById("voice-fill");
  el.innerHTML = chip("用途", st.usage, "usage") + chip("階数", st.storeys, "storeys") + rounds;
  el.classList.remove("hidden");
}
function showQuestion(text, target) {
  vPending = target;
  const el = document.getElementById("voice-question");
  el.innerHTML = `❓ ${text}<span class="q-hint">🎤「音声で入力」で答える／手入力でもOK</span>`;
  el.classList.remove("hidden");
}
function hideQuestion() {
  vPending = null;
  document.getElementById("voice-question").classList.add("hidden");
}
function usageOptionExists(code) {
  return code && [...document.getElementById("f-usage").options]
    .some((o) => o.value === String(code));
}
function doneStatus() {
  const gu = [...vGivenUp].map((i) => (i === "usage" ? "用途" : "階数"));
  return (vAsked > 0 ? `音声 ${vAsked}往復で確認完了` : "確認しました")
    + (gu.length ? `（${gu.join("・")}は判断不能）` : "");
}

function showProposal(p) {
  const parts = [];
  if (p.usage) parts.push(`用途：${p.usageLabel}`);
  if (p.storeys != null) parts.push(`階数：${p.storeys}階`);
  document.getElementById("vp-text").textContent = `${parts.join(" ／ ")} を入力します`;
  document.getElementById("voice-question").classList.add("hidden");  // 確認中は質問を隠す
  document.getElementById("voice-proposal").classList.remove("hidden");
}
function hideProposal() {
  document.getElementById("voice-proposal").classList.add("hidden");
}
// 「反映」：確認した内容だけを各欄へ入れ、メモから反映済みを消す（特徴は残す）
function applyProposal() {
  const p = vProposal; if (!p) return;
  const applied = [];
  if (p.storeys != null) {
    document.getElementById("f-storeys").value = p.storeys;
    applied.push(`階数 ${p.storeys}階`);
  }
  if (usageOptionExists(p.usage)) {
    document.getElementById("f-usage").value = String(p.usage);
    applied.push(`用途 ${p.usageLabel}`);
  }
  // メモ整理：発話前メモ ＋ 反映しなかった特徴だけ残す（反映済みの文言は消す）
  const kept = (p.features || "").trim();
  document.getElementById("f-note").value = p.base + (p.base && kept ? "\n" : "") + kept;
  saveDraft();
  hideProposal();
  vProposal = null;

  let msg = `反映：${applied.join("・")}`;
  msg += kept ? `／メモに残した：「${kept}」` : "／メモを整理しました";
  if (p.question) { vAsked += 1; showQuestion(p.question, p.questionTarget); msg += "。次の質問にどうぞ"; }
  else { hideQuestion(); }
  vStatus(msg);
  refreshVoiceFill();
}
// 「やり直す」：各欄は変えず、この発話ぶんをメモから取り消す
function redoProposal() {
  const p = vProposal; if (!p) return;
  document.getElementById("f-note").value = p.base;
  saveDraft();
  hideProposal();
  vProposal = null;
  vStatus("やり直しました。もう一度どうぞ");
  refreshVoiceFill();
}

async function finishVoice() {
  voiceActive = false;
  vBtn(false);
  if (voiceErrored) { voiceErrored = false; return; }  // エラー文言を消さない
  if (voiceSilent) { voiceSilent = false; return; }  // 棟移動：メモ・振り分けに触れない
  applyTranscript("");     // 暫定分を確定表示に
  saveDraft();             // ここまでの文字は必ず端末に残す
  const text = voiceFinal.trim();
  if (!text) { vStatus("聞き取れませんでした。もう一度『音声で入力』を押して話してください", true); return; }
  await dispatchSpoken(text, voiceBase);   // この発話前のメモ（voiceBase）を反映/やり直しの基準に
}

// iOS 口述：メモ欄にキーボードの🎤で話してもらう。開始時にメモを控え、
// 「振り分け」を押したら新規に話したぶんだけを取り出して Claude に渡す。
function startDictation() {
  if (voiceActive) return;
  if (vProposal) { document.getElementById("f-note").value = vProposal.base; vProposal = null; hideProposal(); }
  const ta = document.getElementById("f-note");
  voiceNoteSnapshot = ta.value;            // 新規ぶんの差分検出用（生の値）
  voiceBase = voiceNoteSnapshot.trim();    // 反映/やり直しで戻す基準
  voiceActive = true;
  vBtn(true);
  vStatus("メモ欄で、キーボードの🎤を押して話してください。終わったら「振り分け」を押します");
  ta.focus();
  try { const n = ta.value.length; ta.setSelectionRange(n, n); } catch {}  // 末尾に追記されるように
}
async function finishDictation() {
  voiceActive = false;
  vBtn(false);
  saveDraft();
  const full = document.getElementById("f-note").value;
  // 開始時のメモを前置きとして取り除き、新規に話したぶんだけを振り分ける
  let text = full.startsWith(voiceNoteSnapshot) ? full.slice(voiceNoteSnapshot.length) : full;
  text = text.trim();
  if (!text) { vStatus("メモに文字がありません。キーボードの🎤で話してから「振り分け」を押してください", true); return; }
  await dispatchSpoken(text, voiceBase);
}

// 認識/口述で得たテキストを Claude に振り分け、確認（反映前）まで進める。
async function dispatchSpoken(text, base) {
  vStatus("内容を振り分け中…");
  const askedPending = vPending;   // この発話が答えている質問の項目
  try {
    const res = await fetch("/api/voice_parse", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text, building_id: planOrder[idx],
        status: voiceStatusItems(), pending: askedPending,
        givenup: [...vGivenUp], asked: vAsked,
      }),
    });
    if (!res.ok) throw new Error(String(res.status));
    const d = await res.json();

    // 「分からない」は欄を変えないので確認不要。即時に判断不能として記録。
    (d.dont_know || []).forEach((it) => vGivenUp.add(it));

    const usageLabel = d.usage ? ((usageCodes.find((u) => String(u.code) === String(d.usage)) || {}).label || d.usage) : null;
    const hasField = (d.storeys != null) || usageOptionExists(d.usage);
    if (hasField) {
      // 反映前に一度確認を挟む（自動では各欄に入れない）
      vProposal = { storeys: d.storeys, usage: d.usage, usageLabel, features: d.features,
                    question: d.question, questionTarget: d.question_target, base };
      showProposal(vProposal);
      vStatus("内容を確認して「反映」を押してください");
    } else if (d.question) {           // 入れる欄は無いが、まだ足りない → 質問
      vAsked += 1;
      showQuestion(d.question, d.question_target);
      vStatus("質問にお答えください（🎤 か手入力）");
    } else if (d.features) {           // 用途・階数には当てはまらなかった（メモに残っている）
      hideQuestion();
      vStatus("用途・階数には反映できませんでした。話した内容はメモに残しています");
    } else {                           // 入れる欄も質問も無い＝確認完了
      hideQuestion();
      vStatus(doneStatus());
    }
    saveDraft();
    refreshVoiceFill();
  } catch {
    // 振り分け失敗でもメモは残っている。手入力で続けられる。
    vStatus("振り分けに失敗。話した内容はメモに残っています", true);
  }
}

// 端末に応じて開始する：iOS は OS 標準の口述、それ以外は Web Speech
function startEntry() { if (voiceDictation) startDictation(); else startVoice(); }

if (voiceSupported) {
  document.getElementById("voice-tools").classList.remove("hidden");
  document.getElementById("voice-btn").addEventListener("click", () => {
    if (voiceActive) { voiceDictation ? finishDictation() : stopVoice(false); return; }
    if (lsGet(VLS.consent, false)) startEntry();
    else document.getElementById("voice-consent").classList.remove("hidden");
  });
  document.getElementById("voice-ok").addEventListener("click", () => {
    lsSet(VLS.consent, true);
    document.getElementById("voice-consent").classList.add("hidden");
    startEntry();
  });
  document.getElementById("voice-cancel").addEventListener("click", () =>
    document.getElementById("voice-consent").classList.add("hidden"));
  document.getElementById("vp-apply").addEventListener("click", applyProposal);
  document.getElementById("vp-redo").addEventListener("click", redoProposal);
}

// --- 起動 -------------------------------------------------------------------
loadAll();
