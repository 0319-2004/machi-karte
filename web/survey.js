"use strict";

// まちカルテ 現地調査（スマホのブラウザ前提）。
// - 優先度順リストから今日回る棟を選び、現地で1棟ずつ入力する。
// - 位置情報（Geolocation）は使わない（許可ダイアログで現地作業が止まるのを避ける）。
// - 入力は都度 localStorage に保存し、通信が切れても消えないようにする。
// - 送信は POST /api/survey（写真は端末側で縮小して data URL で送る）。

const LS = {
  selected: "mk.survey.selected",
  operator: "mk.survey.operator",
  draft: (id) => `mk.survey.draft.${id}`,
};
const PHOTO_MAX = 1280;   // 長辺の上限px（localStorage とアップロードを軽くする）

const lsGet = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };

let usageCodes = [];
let targets = [];
let guidanceById = {};       // building_id -> 現地で確認することの文章
let selected = lsGet(LS.selected, []);
let order = [];              // 調査対象の building_id 配列（selected の並び）
let idx = 0;                 // 調査中の位置
let map = null, marker = null;

// --- データ読み込み ---------------------------------------------------------

async function loadAll() {
  [usageCodes, targets] = await Promise.all([
    fetch("/api/usage_codes").then((r) => r.json()),
    fetch("/api/targets?limit=100").then((r) => r.json()),
  ]);
  // 判断不能の「現地で確認すること」を building_id に展開（最初に当たった区分を使う）
  try {
    const un = await fetch("/api/unresolved").then((r) => r.json());
    for (const g of un.groups || []) {
      if (!g.guidance) continue;
      for (const bid of g.building_ids || []) if (!(bid in guidanceById)) guidanceById[bid] = g.guidance;
    }
  } catch {}
  document.getElementById("operator").value = lsGet(LS.operator, "") || "";
  renderPick();
}

// --- 棟を選ぶ画面 -----------------------------------------------------------

function renderPick() {
  const ul = document.getElementById("pick-list");
  ul.innerHTML = "";
  for (const t of targets) {
    const li = document.createElement("li");
    const on = selected.includes(t.id);
    if (on) li.classList.add("on");
    const h = t.height_m != null ? t.height_m.toFixed(0) + "m" : "高さ不明";
    const draft = lsGet(LS.draft(t.id), null);
    const doneMark = draft && draft.done ? '<span class="check">✓調査済</span>' : "";
    const newTag = t.is_new ? '<span class="tag">新規</span>' : "";
    li.innerHTML =
      `<input type="checkbox" ${on ? "checked" : ""} />` +
      `<div><div class="bid">${t.id}${newTag}</div>` +
      `<div class="meta">${h}・用途 ${t.usage_label || "不明"}</div></div>${doneMark}`;
    const cb = li.querySelector("input");
    cb.addEventListener("change", () => toggleSelect(t.id, cb.checked, li));
    ul.appendChild(li);
  }
  updateStartBtn();
}

function toggleSelect(id, on, li) {
  if (on && !selected.includes(id)) selected.push(id);
  if (!on) selected = selected.filter((x) => x !== id);
  li.classList.toggle("on", on);
  lsSet(LS.selected, selected);
  updateStartBtn();
}

function updateStartBtn() {
  const btn = document.getElementById("start-btn");
  btn.textContent = `選んだ棟を調査（${selected.length}）`;
  btn.disabled = selected.length === 0;
}

document.getElementById("operator").addEventListener("input", (e) =>
  lsSet(LS.operator, e.target.value.trim()));

document.getElementById("start-btn").addEventListener("click", () => {
  // targets の並び（優先度順）に沿って調査順を決める
  order = targets.map((t) => t.id).filter((id) => selected.includes(id));
  if (!order.length) return;
  document.getElementById("view-pick").classList.add("hidden");
  document.getElementById("view-survey").classList.remove("hidden");
  ensureMap();
  // 未調査の先頭から始める
  const firstUndone = order.findIndex((id) => !(lsGet(LS.draft(id), {}).done));
  idx = firstUndone >= 0 ? firstUndone : 0;
  loadBuilding(idx);
});

document.getElementById("survey-back").addEventListener("click", () => {
  document.getElementById("view-survey").classList.add("hidden");
  document.getElementById("view-pick").classList.remove("hidden");
  renderPick();   // 調査済マークを反映
});

// --- 地図（位置の代わり。位置情報は使わない） ------------------------------

function ensureMap() {
  if (map) { map.resize(); return; }
  const style = {
    version: 8,
    sources: {
      gsi: { type: "raster",
             tiles: ["https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"],
             tileSize: 256,
             attribution: "国土地理院（地理院タイル）" },
    },
    layers: [{ id: "gsi", type: "raster", source: "gsi" }],
  };
  map = new maplibregl.Map({
    container: "mini-map", style, center: [139.6917, 35.6896], zoom: 17,
    attributionControl: false,
  });
  marker = new maplibregl.Marker({ color: "#e64a19" });
}

function placeMarker(lon, lat) {
  if (lon == null || lat == null) return;
  marker.setLngLat([lon, lat]).addTo(map);
  map.jumpTo({ center: [lon, lat], zoom: 17 });
  setTimeout(() => map.resize(), 50);
}

// --- 1棟の調査画面 ----------------------------------------------------------

function fillUsageOptions(selectedCode) {
  const sel = document.getElementById("f-usage");
  sel.innerHTML = '<option value="">（未選択）</option>' +
    usageCodes.map((u) => `<option value="${u.code}">${u.code}：${u.label}</option>`).join("");
  sel.value = selectedCode || "";
}

async function loadBuilding(i) {
  idx = i;
  const id = order[idx];
  document.getElementById("survey-bid").textContent = id;
  document.getElementById("survey-prog").textContent = `${idx + 1} / ${order.length}`;
  document.getElementById("survey-err").classList.add("hidden");

  const t = targets.find((x) => x.id === id);
  placeMarker(t && t.lon, t && t.lat);

  // 推定値・判断不能ガイドを取りに行く
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

  // 下書き（localStorage）があれば優先、無ければ推定を初期値にする
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
  const id = order[idx];
  const st = document.getElementById("f-storeys").value;
  return {
    building_id: id,
    usage: document.getElementById("f-usage").value || null,
    storeys: st === "" ? null : Math.max(1, parseInt(st, 10) || 0) || null,
    note: document.getElementById("f-note").value.trim() || null,
    photo: currentPhoto,
  };
}

function saveDraft() {
  const id = order[idx];
  const prev = lsGet(LS.draft(id), {});
  const f = readForm();
  lsSet(LS.draft(id), { ...f, done: prev.done || false });
  document.getElementById("saved-flag").classList.remove("hidden");
}

// 入力のたびに下書き保存（通信が切れても消えないように）
for (const elid of ["f-usage", "f-storeys", "f-note"]) {
  document.getElementById(elid).addEventListener("input", saveDraft);
}

// --- 写真（カメラ起動 → 端末側で縮小） --------------------------------------

let currentPhoto = null;   // data URL or null

function setPhoto(dataUrl) {
  currentPhoto = dataUrl || null;
  const thumb = document.getElementById("photo-thumb");
  const del = document.getElementById("photo-del");
  if (currentPhoto) { thumb.src = currentPhoto; thumb.style.display = "inline";
    del.classList.remove("hidden"); }
  else { thumb.removeAttribute("src"); thumb.style.display = "none";
    del.classList.add("hidden"); }
}

function downscale(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, PHOTO_MAX / Math.max(img.width, img.height));
      const w = Math.round(img.width * scale), h = Math.round(img.height * scale);
      const c = document.createElement("canvas");
      c.width = w; c.height = h;
      c.getContext("2d").drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/jpeg", 0.7));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("画像を読めません")); };
    img.src = url;
  });
}

document.getElementById("f-photo").addEventListener("change", async (e) => {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  try { setPhoto(await downscale(file)); saveDraft(); }
  catch { showErr("写真を読み込めませんでした"); }
  e.target.value = "";   // 同じ写真でも再選択できるように
});
document.getElementById("photo-del").addEventListener("click", () => { setPhoto(null); saveDraft(); });

// --- 送信（POST /api/survey） ----------------------------------------------

function showErr(msg) {
  const el = document.getElementById("survey-err");
  el.textContent = "⚠ " + msg; el.classList.remove("hidden");
}

function gotoNextUndone() {
  for (let j = idx + 1; j < order.length; j++)
    if (!lsGet(LS.draft(order[j]), {}).done) return loadBuilding(j);
  // 後ろが無ければ、未調査を先頭から探す
  const k = order.findIndex((id) => !lsGet(LS.draft(id), {}).done);
  if (k >= 0) loadBuilding(k);
  else { alert("選んだ棟をすべて調査しました。お疲れさまでした。"); document.getElementById("survey-back").click(); }
}

document.getElementById("nav-save").addEventListener("click", async () => {
  saveDraft();   // まず端末に確実に残す
  const f = readForm();
  const operator = lsGet(LS.operator, "") || null;
  if (!f.usage && f.storeys == null && !f.note && !f.photo) {
    return showErr("用途・階数・写真・メモのいずれかを入力してください");
  }
  const btn = document.getElementById("nav-save");
  btn.disabled = true; btn.textContent = "送信中…";
  try {
    const res = await fetch("/api/survey", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...f, operator }),
    });
    if (!res.ok) throw new Error("サーバーエラー " + res.status);
    const id = order[idx];
    lsSet(LS.draft(id), { ...lsGet(LS.draft(id), {}), done: true });
    btn.disabled = false;
    gotoNextUndone();
  } catch (err) {
    btn.disabled = false; btn.textContent = "保存して次へ";
    showErr("送信できませんでした（通信を確認）。入力は端末に保存済みです。");
  }
});

document.getElementById("nav-prev").addEventListener("click", () => { if (idx > 0) loadBuilding(idx - 1); });
document.getElementById("nav-next").addEventListener("click", () => { if (idx < order.length - 1) loadBuilding(idx + 1); });

// --- 起動 -------------------------------------------------------------------
loadAll();
