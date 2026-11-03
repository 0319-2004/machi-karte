"""Mapillary のカバレッジ測定（Phase 7 の前段。取得・割り当て本体はまだ作らない）。

設計書「画像判定」の供給源1＝Mapillary が新宿区でどれだけ使えるかを、
**公開 API（Graph API v4, images の bbox 検索）だけ**で測る。スクレイピングはしない。

やること：
  - 不明群 4,982 棟の重心（EPSG:6677）から半径 20m / 40m 以内に Mapillary 画像が
    あるかを数える（高さ帯別・デモ範囲別にも）。
  - images の bbox 検索で**タイル単位にまとめて取得**（1棟1リクエストにしない）。
    不明群を含むセルだけを、40m 分だけ外側に広げて取得する。
  - fields は id, computed_geometry, computed_compass_angle, camera_type, captured_at のみ。

測らないこと（Phase 7 でやる）：
  - 画像のダウンロード・保存、視錐台レイキャストでの建物割り当て。
  - **向きは考慮せず距離だけ**で見る。実際に建物が写っている保証はないことを明記して報告する。

実行:
    uv run python -m estimate.mapillary_coverage              # 全不明群
    uv run python -m estimate.mapillary_coverage --top 300    # 優先度上位300棟のみ
    uv run python -m estimate.mapillary_coverage --dry-run    # タイル数だけ見る（取得しない）
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone

import duckdb
import numpy as np
from dotenv import load_dotenv
from pyproj import Transformer
from scipy.spatial import cKDTree

DB_PATH = "data/machikarte.duckdb"
VERSION = "2025"
GRAPH = "https://graph.mapillary.com/images"
FIELDS = "id,computed_geometry,computed_compass_angle,camera_type,captured_at"

# タイル：経度0.005° × 緯度0.004°（≈450m）。40m 相当のバッファで外側に広げる。
CELL_LON, CELL_LAT = 0.005, 0.004
BUF_LON, BUF_LAT = 0.0006, 0.0005   # ≈ 50m 強（取りこぼし防止）
PAGE_LIMIT = 2000
MAX_PAGES_PER_TILE = 8
MAX_REQUESTS = 800                  # 安全上限（超えたら中断して実測を報告）

RADII = (20.0, 40.0)
_TO_6677 = Transformer.from_crs("EPSG:4326", "EPSG:6677", always_xy=True)

HEIGHT_BANDS = [("0-10m", 0, 10), ("10-20m", 10, 20),
                ("20-45m", 20, 45), ("45m+", 45, 1e9)]


def band_of(h):
    if h is None:
        return "高さ無"
    for label, lo, hi in HEIGHT_BANDS:
        if lo <= h < hi:
            return label
    return "高さ無"


def token() -> str:
    load_dotenv(os.path.join(os.getcwd(), ".env"))
    return os.environ.get("MAPILLARY_ACCESS_TOKEN", "").strip()


# --- 不明群の読み込み -------------------------------------------------------

def load_targets(con, top: int | None):
    if top:
        from api.priority import TARGETS_SQL
        rows = con.execute(TARGETS_SQL, [VERSION, VERSION, top]).fetchall()
        # (id, h, st, usage, lon, lat, area, is_new, has_image, score, confirmed)
        recs = [(r[0], r[1], r[4], r[5]) for r in rows if r[4] is not None]
    else:
        rows = con.execute(
            """SELECT building_id, height_m,
                      ST_X(ST_Centroid(geom_4326)), ST_Y(ST_Centroid(geom_4326))
               FROM buildings WHERE version=? AND is_target AND geom_4326 IS NOT NULL""",
            [VERSION],
        ).fetchall()
        recs = [(r[0], r[1], r[2], r[3]) for r in rows]
    ids = [r[0] for r in recs]
    heights = [r[1] for r in recs]
    lon = np.array([r[2] for r in recs], float)
    lat = np.array([r[3] for r in recs], float)
    x, y = _TO_6677.transform(lon, lat)
    return {"id": ids, "height": heights, "lon": lon, "lat": lat,
            "xy": np.c_[x, y], "band": [band_of(h) for h in heights]}


def occupied_tiles(lon, lat):
    cells = {(int(np.floor(a / CELL_LON)), int(np.floor(b / CELL_LAT)))
             for a, b in zip(lon, lat)}
    tiles = []
    for (ix, iy) in cells:
        w = ix * CELL_LON - BUF_LON
        e = (ix + 1) * CELL_LON + BUF_LON
        s = iy * CELL_LAT - BUF_LAT
        n = (iy + 1) * CELL_LAT + BUF_LAT
        tiles.append((w, s, e, n))
    return tiles


# --- Mapillary 取得（bbox 一括・ページング） --------------------------------

def _get(url: str, tok: str, retries: int = 2):
    req = urllib.request.Request(url, headers={"Authorization": "OAuth " + tok})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise
        except Exception:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise


def fetch_images(tiles, tok: str):
    """占有タイルごとに images を bbox 取得。id で重複排除して返す。"""
    seen: dict[str, dict] = {}
    req_count = 0
    for i, (w, s, e, n) in enumerate(tiles, 1):
        params = {"fields": FIELDS, "bbox": f"{w},{s},{e},{n}", "limit": PAGE_LIMIT}
        url = GRAPH + "?" + urllib.parse.urlencode(params)
        for _page in range(MAX_PAGES_PER_TILE):
            if req_count >= MAX_REQUESTS:
                print(f"  ※ リクエスト上限 {MAX_REQUESTS} に到達。ここまでで集計する。")
                return seen, req_count, True
            d = _get(url, tok)
            req_count += 1
            for rec in d.get("data", []):
                g = rec.get("computed_geometry")
                if g and g.get("coordinates"):
                    seen[rec["id"]] = rec
            nxt = (d.get("paging") or {}).get("next")
            if not nxt:
                break
            url = nxt
        if i % 20 == 0:
            print(f"  タイル {i}/{len(tiles)}  画像 {len(seen)}  リクエスト {req_count}")
    return seen, req_count, False


# --- 集計 -------------------------------------------------------------------

def summarize(tgt, images: dict):
    recs = list(images.values())
    coords = np.array([r["computed_geometry"]["coordinates"][:2] for r in recs], float)
    ix, iy = _TO_6677.transform(coords[:, 0], coords[:, 1])
    img_xy = np.c_[ix, iy]
    tree = cKDTree(img_xy)

    # 各棟の最近傍画像の距離と index
    dist, idx = tree.query(tgt["xy"], k=1)
    bands = np.array(tgt["band"])

    def cover(mask_radius):
        return dist <= mask_radius

    out = {"n_targets": len(tgt["id"]), "n_images": len(recs), "by_radius": {}}
    for r in RADII:
        cov = cover(r)
        band_rates = {}
        for label, _, _ in HEIGHT_BANDS + [("高さ無", 0, 0)]:
            m = bands == label
            if m.sum():
                band_rates[label] = (int(cov[m].sum()), int(m.sum()))
        out["by_radius"][r] = {
            "covered": int(cov.sum()),
            "rate": float(cov.mean()),
            "by_band": band_rates,
        }

    # 40m 被覆棟に割り当たった最近傍画像の撮影年・camera_type 分布
    cov40 = dist <= 40.0
    assigned = idx[cov40]
    years = Counter()
    cams = Counter()
    for j in assigned:
        rec = recs[j]
        cap = rec.get("captured_at")
        if cap:
            years[datetime.fromtimestamp(cap / 1000, tz=timezone.utc).year] += 1
        cams[rec.get("camera_type") or "unknown"] += 1
    # 取得画像全体の camera_type も
    cams_all = Counter(r.get("camera_type") or "unknown" for r in recs)
    out["assigned_years"] = dict(sorted(years.items()))
    out["assigned_camera_type"] = dict(cams)
    out["all_camera_type"] = dict(cams_all)
    out["dist"] = dist
    return out


def demo_coverage(con, tgt, dist):
    """デモ範囲（高さ20m以上=196 / 西新宿1km圏=567）での被覆率。"""
    h = np.array([x if x is not None else -1 for x in tgt["height"]], float)
    # 西新宿1km圏の building_id
    nishi = {r[0] for r in con.execute(
        """SELECT building_id FROM buildings
           WHERE version=? AND is_target AND geom IS NOT NULL
             AND ST_Distance(ST_Centroid(geom),
                 ST_Transform(ST_Point(139.6917,35.6896),'EPSG:4326','EPSG:6677',true))<=1000""",
        [VERSION]).fetchall()}
    ids = np.array(tgt["id"])
    h20 = h >= 20
    nishi_mask = np.array([i in nishi for i in ids])
    res = {}
    for label, mask in [("高さ20m以上", h20), ("西新宿1km圏", nishi_mask),
                        ("西新宿1km圏かつ20m以上", nishi_mask & h20)]:
        if mask.sum() == 0:
            continue
        res[label] = {
            "n": int(mask.sum()),
            "within20": int((dist[mask] <= 20).sum()),
            "within40": int((dist[mask] <= 40).sum()),
        }
    return res


# --- 実行 -------------------------------------------------------------------

def run(top: int | None, dry: bool) -> dict:
    con = duckdb.connect(DB_PATH, read_only=True)
    con.execute("LOAD spatial;")
    tgt = load_targets(con, top)
    tiles = occupied_tiles(tgt["lon"], tgt["lat"])
    scope = f"優先度上位{top}棟" if top else "全不明群"
    print(f"=== Mapillary カバレッジ測定（{scope}）===")
    print(f"  対象棟 {len(tgt['id'])} / 取得タイル {len(tiles)}（≈450m, 40mバッファ付）")
    if dry:
        con.close()
        return {"tiles": len(tiles), "targets": len(tgt["id"])}

    tok = token()
    if not tok:
        print("  MAPILLARY_ACCESS_TOKEN が読み込めない。測定を中止する。")
        con.close()
        return {"error": "no_token"}

    t0 = time.time()
    images, reqs, capped = fetch_images(tiles, tok)
    print(f"  取得完了：画像 {len(images)} 件 / リクエスト {reqs} / {time.time()-t0:.0f}s"
          + ("（上限で打ち切り）" if capped else ""))

    s = summarize(tgt, images)
    demo = demo_coverage(con, tgt, s["dist"])
    con.close()

    print("\n--- 半径別の被覆率（距離のみ。向きは未考慮）---")
    for r in RADII:
        br = s["by_radius"][r]
        print(f"  {int(r)}m 以内に画像: {br['covered']}/{s['n_targets']} = {br['rate']*100:.1f}%")
        for label, _, _ in HEIGHT_BANDS + [("高さ無", 0, 0)]:
            if label in br["by_band"]:
                c, nn = br["by_band"][label]
                print(f"      {label:<7}: {c}/{nn} = {c/nn*100:.1f}%")
    print("\n--- 撮影年の分布（40m被覆棟の最近傍画像）---")
    print("  " + ", ".join(f"{y}:{c}" for y, c in s["assigned_years"].items()))
    print("--- camera_type（40m被覆棟の最近傍 / 取得画像全体）---")
    print("  最近傍:", s["assigned_camera_type"])
    print("  全体  :", s["all_camera_type"])
    print("\n--- デモ範囲の被覆率 ---")
    for label, v in demo.items():
        print(f"  {label}（n={v['n']}）: 20m {v['within20']}({v['within20']/v['n']*100:.1f}%) / "
              f"40m {v['within40']}({v['within40']/v['n']*100:.1f}%)")
    print("\n※ 距離のみの被覆率。向きを考慮しておらず、実際にその建物が写っている保証はない。")

    # JSON を保存（再現・記録用）
    s.pop("dist", None)
    out = {"scope": scope, "targets": len(tgt["id"]), "tiles": len(tiles),
           "requests": reqs, "capped": capped, "images": len(images),
           "coverage": {str(r): s["by_radius"][r] for r in RADII},
           "assigned_years": s["assigned_years"],
           "assigned_camera_type": s["assigned_camera_type"],
           "all_camera_type": s["all_camera_type"], "demo": demo}
    os.makedirs("data/derived", exist_ok=True)
    with open("data/derived/mapillary_coverage.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("  → data/derived/mapillary_coverage.json に保存")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Mapillary カバレッジ測定（公開APIのみ）")
    p.add_argument("--top", type=int, default=None, help="優先度上位N棟に絞る")
    p.add_argument("--dry-run", action="store_true", help="タイル数だけ出す（取得しない）")
    args = p.parse_args()
    run(args.top, args.dry_run)


if __name__ == "__main__":
    main()
