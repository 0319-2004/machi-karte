"""新宿区の輪郭 GeoJSON を、PLATEAU の建物データから生成する。

デモの「新宿と入力すると輪郭を光がなぞる」演出のためだけに使う、表示専用の線。
外部の行政界データは使わず、`data/derived/buildings.geojson`（PLATEAU 由来）の
各建物の代表点から凹包（concave hull）を作り、簡略化して閉じた線にする。
出力は小さいので `web/shinjuku_outline.geojson` に置き、`/shinjuku_outline.geojson`
で配信する（git 管理に入れてよいサイズ）。

使い方:
    uv run python -m api.build_outline            # 既定の ratio/簡略化で生成
    uv run python -m api.build_outline 0.4 0.0006 # ratio と簡略化(度)を指定
"""
from __future__ import annotations

import json
import os
import sys

from shapely import concave_hull, simplify
from shapely.geometry import MultiPoint, shape

HERE = os.path.dirname(__file__)
IN_PATH = os.path.join(HERE, "..", "data", "derived", "buildings.geojson")
OUT_PATH = os.path.join(HERE, "..", "web", "shinjuku_outline.geojson")


def build(ratio: float = 0.4, tol: float = 0.0006,
          in_path: str = IN_PATH, out_path: str = OUT_PATH) -> str:
    with open(in_path, encoding="utf-8") as f:
        fc = json.load(f)

    pts = []
    for feat in fc["features"]:
        geom = feat.get("geometry")
        if not geom:
            continue
        pts.append(shape(geom).representative_point())

    hull = concave_hull(MultiPoint(pts), ratio=ratio)
    hull = simplify(hull, tol, preserve_topology=True)

    # 外周リングを閉じた LineString として書き出す（EPSG:4326）
    ring = list(hull.exterior.coords)
    line = {"type": "LineString", "coordinates": [[round(x, 6), round(y, 6)] for x, y in ring]}

    out = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"name": "新宿区（PLATEAU建物の凹包）"},
                      "geometry": line}],
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    xs = [c[0] for c in ring]
    ys = [c[1] for c in ring]
    print(f"書き出し: {out_path}")
    print(f"頂点数: {len(ring)} / bbox: [{min(xs):.5f},{min(ys):.5f},{max(xs):.5f},{max(ys):.5f}]")
    print(f"入力建物: {len(pts):,} 棟 / ratio={ratio} / 簡略化={tol}度")
    return out_path


if __name__ == "__main__":
    r = float(sys.argv[1]) if len(sys.argv) > 1 else 0.4
    t = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0006
    build(ratio=r, tol=t)
