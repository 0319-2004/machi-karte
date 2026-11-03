"""地図UI 用の建物ポリゴン GeoJSON を生成する。

全 57,780 棟を最小プロパティ（id / t=is_target / h=height_m / u=usage）で
`data/derived/buildings.geojson` に書き出す。配信フォーマットの判定は
docs/decisions.md 2026-11-03 を参照（実測：生 28.1MB / gzip 6.7MB）。

使い方:
    uv run python -m api.build_geojson
API 起動時に出力が無ければ自動生成される（api.main）。
"""
from __future__ import annotations

import json
import os

import duckdb

from .db import DB_PATH, DERIVED_DIR

OUT_PATH = os.path.join(DERIVED_DIR, "buildings.geojson")
VERSION = "2025"


def build(out_path: str = OUT_PATH, db_path: str = DB_PATH) -> str:
    con = duckdb.connect(db_path, read_only=True)
    con.execute("LOAD spatial;")
    rows = con.execute(
        """
        SELECT building_id, is_target, height_m, usage, ST_AsGeoJSON(geom_4326)
        FROM buildings
        WHERE version = ? AND geom_4326 IS NOT NULL
        """,
        [VERSION],
    ).fetchall()
    con.close()

    feats = []
    for bid, is_target, height_m, usage, geom in rows:
        feats.append({
            "type": "Feature",
            "id": bid,
            "properties": {
                "id": bid,
                "t": 1 if is_target else 0,
                "h": height_m,
                "u": usage,
            },
            "geometry": json.loads(geom),
        })
    fc = {"type": "FeatureCollection", "features": feats}

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, separators=(",", ":"))
    return out_path


def build_if_missing(out_path: str = OUT_PATH) -> None:
    if not os.path.exists(out_path):
        build(out_path)


if __name__ == "__main__":
    path = build()
    size = os.path.getsize(path) / 1e6
    print(f"書き出し: {path}（{size:.1f} MB, 57,780 棟）")
