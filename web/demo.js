"use strict";

// 配布デモの進行役。app.js が用意した window.MK のフックを使う。
//   段階0：街の名前を入力（「新宿」）
//   段階1：新宿区の全棟を灰色で表示（まだ分類していない）
//   段階2：抽出 → エレクトリックな演出 → 調査対象が橙に浮かび上がる＋作業UI
//
// 数字（総数・対象・削減率）はコードに直書きせず /api/stats から取る。

const body = document.body;
const hint = document.getElementById("demo-hint");

let statsP = fetch("/api/stats").then((r) => r.json());   // 件数は毎回データから
let outlineP = fetch("/shinjuku_outline.geojson").then((r) => r.json());  // 新宿区の輪郭
let layersReady = false;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
document.addEventListener("mk:layers-ready", () => { layersReady = true; });

function whenLayers(fn) {
  if (layersReady) return fn();
  document.addEventListener("mk:layers-ready", fn, { once: true });
}

// 段階0の背景：新宿区を少し引いて俯瞰（検索画面の奥に街を見せる）
function setupStage0View() {
  const m = window.MK.map;
  if (!m) return;
  const apply = () => m.jumpTo({ center: [139.70, 35.690], zoom: 12.6, pitch: 25, bearing: -15 });
  if (m.loaded()) apply(); else m.once("load", apply);
}
setupStage0View();

// --- 段階0 → 段階1：「新宿」を受け付けて全棟（灰色）を表示 -------------------
const SHINJUKU = /新宿|しんじゅく|シンジュク|shinjuku/i;

document.getElementById("demo-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = document.getElementById("demo-input").value.trim();
  if (!v) { hint.textContent = "街の名前を入力してください。"; return; }
  if (!SHINJUKU.test(v)) {
    hint.textContent = `「${v}」のデータはこのデモにはありません。『新宿』でお試しください。`;
    return;
  }
  hint.textContent = "";
  enterStage1();
});

async function enterStage1() {
  body.classList.remove("stage-0");
  body.classList.add("stage-1", "tracing");   // なぞる間は件数/抽出UIを隠す
  const m = window.MK.map;

  // レイヤと輪郭データが揃うのを待つ
  await new Promise((res) => whenLayers(res));
  const fc = await outlineP;
  const bbox = outlineBbox(fc);

  // まず新宿区の全体を真上から収める（輪郭が読める向き・建物はまだ非表示）
  if (m) m.fitBounds(bbox, { padding: 70, pitch: 0, bearing: 0, duration: 1000, maxZoom: 14.5 });
  await wait(1050);

  // エレクトリックな光で輪郭をなぞる
  await traceOutline(1900);

  // 街データの登場：都庁周辺へ寄り、全棟（灰色）を出し、総数を数え上げる
  body.classList.remove("tracing");
  if (m) {
    m.flyTo({ center: [139.6917, 35.6896], zoom: 15, pitch: 55, bearing: -20, duration: 2000 });
    m.setPaintProperty("ward-glow", "line-opacity", 0);
    m.setPaintProperty("ward-line", "line-opacity", 0);
  }
  window.MK.showAllGray && window.MK.showAllGray();
  const s = await statsP;
  window.MK.animateCount(document.getElementById("count-total"), 0, s.total, 1400);
}

// 輪郭(LineString 群)の外接矩形 [[minX,minY],[maxX,maxY]]
function outlineBbox(fc) {
  let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
  for (const f of fc.features) for (const [x, y] of f.geometry.coordinates) {
    if (x < a) a = x; if (y < b) b = y; if (x > c) c = x; if (y > d) d = y;
  }
  return [[a, b], [c, d]];
}

// 走る光のグラデーション。描いた部分は点灯したまま、先端が白く輝く（なぞる見立て）。
// line-gradient の stop は厳密増加が必須なので、昇順・重複除去して組み立てる。
function traceGradient(p, glow) {
  const trail = glow ? "rgba(42,212,232,0.30)" : "rgba(53,224,196,0.55)";
  const head  = glow ? "rgba(140,235,255,0.95)" : "rgba(235,255,255,1)";
  const clear = "rgba(42,212,232,0)";
  const hb = Math.max(0.001, Math.min(0.999, p));
  const raw = [
    [0, trail],
    [Math.max(0.0005, hb - 0.05), trail],
    [hb, head],
    [Math.min(1, hb + 0.004), clear],
    [1, clear],
  ];
  const stops = []; let last = -1;
  for (const [pos, col] of raw) { if (pos > last) { stops.push(pos, col); last = pos; } }
  if (stops[0] !== 0) stops.unshift(0, trail);
  return ["interpolate", ["linear"], ["line-progress"], ...stops];
}

// 輪郭を 0→1 でなぞる（毎フレーム line-gradient を更新）
function traceOutline(durationMs) {
  return new Promise((resolve) => {
    const m = window.MK.map;
    if (!m) return resolve();
    m.setPaintProperty("ward-glow", "line-opacity", 1);
    m.setPaintProperty("ward-line", "line-opacity", 1);
    const t0 = performance.now();
    function frame(now) {
      const p = Math.min(1, (now - t0) / durationMs);
      m.setPaintProperty("ward-glow", "line-gradient", traceGradient(p, true));
      m.setPaintProperty("ward-line", "line-gradient", traceGradient(p, false));
      if (p < 1) requestAnimationFrame(frame); else resolve();
    }
    requestAnimationFrame(frame);
  });
}

// --- 段階1 → 段階2：抽出（エレクトリック演出） ------------------------------
document.getElementById("demo-extract-btn").addEventListener("click", runExtract);

async function runExtract() {
  const btn = document.getElementById("demo-extract-btn");
  if (btn.disabled) return;
  btn.disabled = true;
  body.classList.add("extracting");

  const s = await statsP;
  // ネオンの光が通り過ぎる頃に調査対象を橙へ浮かび上がらせる
  playNeon(2200, 0.4, () => {
    window.MK.revealTargets && window.MK.revealTargets();
    body.classList.add("revealing");   // 件数カウンタの「対象・削減率」を表に出す
    const elTarget = document.getElementById("count-target");
    const elReduction = document.getElementById("count-reduction");
    window.MK.animateCount(elTarget, 0, s.target, 1300);
    elReduction.textContent = "−" + s.reduction_pct + "%";
  }, () => {
    // 演出おわり：作業UIを公開
    body.classList.remove("extracting", "revealing", "stage-1");
    body.classList.add("stage-2");
  });
}

// --- ネオンの演出（自前の Canvas・外部ライブラリなし） -----------------------
// 地図はうっすら見せたまま、ネオンの光の筋が走り抜け、下りのスキャン光が街を帯電させる。
// 稲妻のようなギザギザではなく、なめらかな光跡（コメット）で仕上げる。
// kepler.gl 調：シアン／ティールを主に、紫（現地）と橙（調査対象）を混ぜる
const NEON_PALETTE = [
  [42, 212, 232],   // シアン
  [53, 224, 196],   // ティール
  [120, 230, 255],  // 淡いシアン
  [176, 107, 255],  // 紫
  [255, 106, 43],   // 橙（調査対象の色に揃える）
];

function playNeon(durationMs, revealAt, onReveal, onDone) {
  const canvas = document.getElementById("demo-bolt");
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  let W = 0, H = 0;
  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  resize();
  window.addEventListener("resize", resize);
  canvas.style.display = "block";

  // 走る光の筋（コメット）。向き・高さ・開始時刻・長さ・色をばらけさせる。
  const streaks = [];
  for (let i = 0; i < 9; i++) {
    const dir = Math.random() < 0.5 ? 1 : -1;
    streaks.push({
      y: 0.12 + 0.76 * Math.random(),      // 画面高に対する基準位置
      tilt: (Math.random() - 0.5) * 0.4,   // ゆるい傾き
      dir,
      start: Math.random() * 0.6,          // 走り出すタイミング（0〜0.6）
      life: 0.3 + Math.random() * 0.28,    // 横断にかける割合
      tail: 220 + Math.random() * 300,     // 光跡の長さ(px)
      width: 2 + Math.random() * 3,
      rgb: NEON_PALETTE[i % NEON_PALETTE.length],
    });
  }
  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const easeInOut = (x) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);

  const t0 = performance.now();
  let revealed = false;

  function step(now) {
    const p = Math.min(1, (now - t0) / durationMs);

    // 毎フレーム完全に消す → 地図が常に見える（残像で暗くしない）
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, W, H);
    // 地図を「うっすら」に沈める薄いベール（毎フレーム一定＝暗くはならない）
    ctx.fillStyle = "rgba(6,12,22,0.22)";
    ctx.fillRect(0, 0, W, H);

    // ここから光は加算合成でにじませる
    ctx.globalCompositeOperation = "lighter";

    // 立ち上がりのやわらかい発光
    if (p < 0.25) {
      const a = (0.25 - p) / 0.25;
      const g = ctx.createRadialGradient(W / 2, H * 0.5, 0, W / 2, H * 0.5, Math.max(W, H) * 0.75);
      g.addColorStop(0, `rgba(42,212,232,${0.22 * a})`);
      g.addColorStop(1, "rgba(42,212,232,0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    }

    // ネオンの光の筋（コメット：先端が明るく、尾に向かって消える）
    ctx.lineCap = "round";
    for (const s of streaks) {
      const local = (p - s.start) / s.life;
      if (local < 0 || local > 1) continue;
      const e = easeInOut(local);
      const span = W + 2 * s.tail;
      const headX = s.dir > 0 ? -s.tail + e * span : W + s.tail - e * span;
      const headY = s.y * H + (headX - W / 2) * s.tilt;
      const tailX = headX - s.dir * s.tail;
      const tailY = s.y * H + (tailX - W / 2) * s.tilt;
      // 出入りのフェード（端で急に現れない）
      const fade = Math.sin(Math.min(1, local) * Math.PI);
      const grad = ctx.createLinearGradient(headX, headY, tailX, tailY);
      grad.addColorStop(0, rgba(s.rgb, 0.0));
      grad.addColorStop(0.12, rgba(s.rgb, 0.85 * fade));
      grad.addColorStop(1, rgba(s.rgb, 0.0));
      ctx.save();
      ctx.shadowColor = rgba(s.rgb, 0.9); ctx.shadowBlur = 22;
      ctx.strokeStyle = grad; ctx.lineWidth = s.width;
      ctx.beginPath(); ctx.moveTo(headX, headY); ctx.lineTo(tailX, tailY); ctx.stroke();
      ctx.restore();
      // 先端の白いコア（点）
      ctx.save();
      ctx.shadowColor = "rgba(255,255,255,0.95)"; ctx.shadowBlur = 16;
      ctx.fillStyle = `rgba(255,255,255,${0.9 * fade})`;
      ctx.beginPath(); ctx.arc(headX, headY, s.width * 0.9, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    }

    // 上から下へ走るスキャン光（街を帯電させる見立て・やわらかめ）
    const sy = p * (H + 160) - 80;
    const band = ctx.createLinearGradient(0, sy - 120, 0, sy + 16);
    band.addColorStop(0, "rgba(0,200,255,0)");
    band.addColorStop(1, "rgba(80,220,255,0.18)");
    ctx.fillStyle = band;
    ctx.fillRect(0, sy - 120, W, 136);
    ctx.save();
    ctx.shadowColor = "rgba(150,235,255,0.9)"; ctx.shadowBlur = 26;
    ctx.strokeStyle = "rgba(210,248,255,0.9)"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(W, sy); ctx.stroke();
    ctx.restore();

    // スキャンが規定位置を越えたら調査対象を公開（＋通過部に橙のにじみ）
    if (!revealed && p >= revealAt) { revealed = true; onReveal && onReveal(); }
    if (revealed) {
      const warm = ctx.createLinearGradient(0, sy - 60, 0, sy + 10);
      warm.addColorStop(0, "rgba(255,106,43,0)");
      warm.addColorStop(1, "rgba(255,106,43,0.16)");
      ctx.fillStyle = warm; ctx.fillRect(0, sy - 60, W, 70);
    }

    if (p < 1) {
      requestAnimationFrame(step);
    } else {
      fadeOut(canvas, () => {
        window.removeEventListener("resize", resize);
        onDone && onDone();
      });
    }
  }
  requestAnimationFrame(step);
}

function fadeOut(canvas, done) {
  canvas.style.transition = "opacity .5s ease";
  canvas.style.opacity = "0";
  setTimeout(() => {
    canvas.style.display = "none";
    canvas.style.opacity = "1";
    canvas.style.transition = "";
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    done && done();
  }, 500);
}
