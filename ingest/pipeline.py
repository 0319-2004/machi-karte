"""CityGML → DuckDB の取り込みパイプライン（設計書 第1段：絞り込み）。

手順：
1. 1ファイルずつ逐次パースして buildings に追記（全部メモリに載せない）
2. raw_* と正規化列を同時に持つ
3. センチネルを NULL 正規化（基準は normalize.py）
4. 外形は LOD0 RoofEdge を 6677 と 4326 で保持
5. 不明群の件数照合（usage=461 と各条件・AND が一致するか）→ ずれたら停止
6. 一致したら is_target を立て、targets.geojson を書き出す
7. サマリーを出し、設計書値と照合して返す
"""
from __future__ import annotations

import glob
import json
import os

import duckdb

from .citygml import iter_buildings
from .normalize import (
    DETAILED_USAGE_SENTINEL,
    NUMERIC_SENTINEL,
    STOREYS_SENTINEL,
    SURVEY_YEAR_SENTINEL,
    USAGE_SENTINEL,
    normalize,
)

CITY_CODE = "13104"  # 新宿区
INSERT_BATCH = 2000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS buildings (
    building_id        VARCHAR,
    version            VARCHAR,
    city_code          VARCHAR,
    raw_usage          VARCHAR,  usage          VARCHAR,
    raw_detailed_usage VARCHAR,  detailed_usage VARCHAR,
    raw_storeys_above  VARCHAR,  storeys_above  INTEGER,
    raw_survey_year    VARCHAR,  survey_year    VARCHAR,
    raw_height         VARCHAR,  height_m       DOUBLE,
    raw_roof_edge_area VARCHAR,  roof_edge_area DOUBLE,
    creation_date      VARCHAR,
    lod_type           VARCHAR,
    is_target          BOOLEAN DEFAULT FALSE,
    geom               GEOMETRY,   -- EPSG:6677（幾何演算用）
    geom_4326          GEOMETRY,   -- EPSG:4326（表示・出力用）
    PRIMARY KEY (building_id, version)
);
"""

_INSERT = """
INSERT INTO buildings (
    building_id, version, city_code,
    raw_usage, usage, raw_detailed_usage, detailed_usage,
    raw_storeys_above, storeys_above, raw_survey_year, survey_year,
    raw_height, height_m, raw_roof_edge_area, roof_edge_area,
    creation_date, lod_type, geom, geom_4326
) VALUES (
    ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ST_GeomFromText(?), ST_GeomFromText(?)
)
"""


def connect(db_path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path)
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(_SCHEMA)
    return con


def load_version(con, data_dir: str, version: str) -> int:
    """指定版の bldg GML を全ファイル読み込み、buildings に入れ直す。"""
    bldg_dir = os.path.join(data_dir, "raw", "plateau", version, "udx", "bldg")
    files = sorted(glob.glob(os.path.join(bldg_dir, "*.gml")))
    if not files:
        raise FileNotFoundError(f"GML が見つからない: {bldg_dir}")

    con.execute("DELETE FROM buildings WHERE version = ?", [version])
    total = 0
    no_geom = 0
    batch = []
    for f in files:
        for row in iter_buildings(f, city_code=CITY_CODE):
            n = normalize(row)
            if row.wkt_6677 is None:
                no_geom += 1
            batch.append([
                row.building_id, version, row.city_code,
                row.raw_usage, n["usage"],
                row.raw_detailed_usage, n["detailed_usage"],
                row.raw_storeys_above, n["storeys_above"],
                row.raw_survey_year, n["survey_year"],
                row.raw_height, n["height_m"],
                row.raw_roof_edge_area, n["roof_edge_area"],
                row.creation_date, row.lod_type,
                row.wkt_6677, row.wkt_4326,
            ])
            total += 1
            if len(batch) >= INSERT_BATCH:
                con.executemany(_INSERT, batch)
                batch.clear()
        print(f"  読み込み: {os.path.basename(f)}  累計 {total} 棟")
    if batch:
        con.executemany(_INSERT, batch)
    if no_geom:
        print(f"  ※ 外形(LOD0)が取れなかった棟: {no_geom}")
    return total


def value_distribution(con, version: str) -> None:
    """ステップ3：正規化を決める前に素の値の分布を出す。"""
    print("\n=== 素の値の分布（raw, version=%s）===" % version)
    for col in ("raw_usage", "raw_detailed_usage", "raw_storeys_above",
                "raw_survey_year", "raw_height", "raw_roof_edge_area"):
        rows = con.execute(
            f"SELECT {col}, count(*) c FROM buildings WHERE version=? "
            f"GROUP BY {col} ORDER BY c DESC LIMIT 6", [version]
        ).fetchall()
        shown = ", ".join(f"{v}:{c}" for v, c in rows)
        print(f"  {col}: {shown}")


def check_targets(con, version: str) -> int:
    """ステップ5：不明群の件数を照合する。一致しなければ ValueError で停止。"""
    q = """
    SELECT
      count(*) FILTER (WHERE raw_usage = ?)          AS c_usage,
      count(*) FILTER (WHERE raw_detailed_usage = ?) AS c_detail,
      count(*) FILTER (WHERE raw_storeys_above = ?)  AS c_storeys,
      count(*) FILTER (WHERE raw_survey_year = ?)    AS c_survey,
      count(*) FILTER (WHERE raw_usage = ? AND raw_detailed_usage = ?
                         AND raw_storeys_above = ? AND raw_survey_year = ?) AS c_and
    FROM buildings WHERE version = ?
    """
    c_usage, c_detail, c_storeys, c_survey, c_and = con.execute(
        q,
        [USAGE_SENTINEL, DETAILED_USAGE_SENTINEL, STOREYS_SENTINEL, SURVEY_YEAR_SENTINEL,
         USAGE_SENTINEL, DETAILED_USAGE_SENTINEL, STOREYS_SENTINEL, SURVEY_YEAR_SENTINEL,
         version],
    ).fetchone()

    print("\n=== 不明群の件数照合（version=%s）===" % version)
    print(f"  usage=461          : {c_usage}")
    print(f"  detailed_usage=0   : {c_detail}")
    print(f"  storeys_above=9999 : {c_storeys}")
    print(f"  survey_year=0001   : {c_survey}")
    print(f"  全条件 AND         : {c_and}")

    counts = {c_usage, c_detail, c_storeys, c_survey, c_and}
    if len(counts) != 1:
        raise ValueError(
            "不明群の件数が一致しない（設計書『前提が崩れたら止める点』に該当）。"
            f" usage={c_usage} detail={c_detail} storeys={c_storeys} "
            f"survey={c_survey} AND={c_and}。is_target は立てずに停止する。"
        )
    con.execute(
        "UPDATE buildings SET is_target = (raw_usage = ?) WHERE version = ?",
        [USAGE_SENTINEL, version],
    )
    return c_and


def export_targets(con, version: str, out_path: str) -> int:
    """ステップ6：不明群を EPSG:4326 の GeoJSON で書き出す。"""
    rows = con.execute(
        """
        SELECT building_id, raw_usage, usage, height_m, storeys_above,
               survey_year, creation_date, lod_type, ST_AsGeoJSON(geom_4326)
        FROM buildings
        WHERE version = ? AND is_target AND geom_4326 IS NOT NULL
        """,
        [version],
    ).fetchall()
    features = []
    for (bid, raw_usage, usage, height, storeys, survey, cdate, lod, gj) in rows:
        features.append({
            "type": "Feature",
            "geometry": json.loads(gj),
            "properties": {
                "building_id": bid,
                "current_value": raw_usage,  # 461 をそのまま（出力用）
                "usage": usage,
                "height_m": height,
                "storeys_above": storeys,
                "survey_year": survey,
                "creation_date": cdate,
                "lod_type": lod,
            },
        })
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features},
                  fh, ensure_ascii=False)
    return len(features)


def summarize(con, version: str) -> dict:
    """ステップ7：サマリーを作って表示し、設計書値と照合する。"""
    total = con.execute(
        "SELECT count(*) FROM buildings WHERE version=?", [version]
    ).fetchone()[0]
    unknown = con.execute(
        "SELECT count(*) FROM buildings WHERE version=? AND is_target", [version]
    ).fetchone()[0]

    sent = {}
    for label, col, val in (
        ("usage=461", "raw_usage", USAGE_SENTINEL),
        ("detailed_usage=0", "raw_detailed_usage", DETAILED_USAGE_SENTINEL),
        ("storeys_above=9999", "raw_storeys_above", STOREYS_SENTINEL),
        ("survey_year=0001", "raw_survey_year", SURVEY_YEAR_SENTINEL),
        ("height=-9999", "raw_height", NUMERIC_SENTINEL),
        ("roof_edge_area=-9999", "raw_roof_edge_area", NUMERIC_SENTINEL),
    ):
        sent[label] = con.execute(
            f"SELECT count(*) FROM buildings WHERE version=? AND {col}=?",
            [version, val],
        ).fetchone()[0]

    bands = {}
    for m in (10, 20, 45, 100):
        bands[m] = con.execute(
            "SELECT count(*) FROM buildings "
            "WHERE version=? AND is_target AND height_m >= ?",
            [version, m],
        ).fetchone()[0]

    reduction = (1 - unknown / total) * 100 if total else 0.0

    print("\n========== サマリー（version=%s）==========" % version)
    print(f"  全棟数       : {total}")
    print(f"  不明群(is_target): {unknown}")
    print(f"  削減率       : {reduction:.1f}%")
    print("  センチネル件数:")
    for k, v in sent.items():
        print(f"    {k:<22}: {v}")
    print("  不明群の高さ帯:")
    for m, v in bands.items():
        print(f"    {m:>3}m 以上          : {v}")

    # 設計書の期待値と照合（2025年度版・13104 前提）
    expect = {"total": 57780, "unknown": 4982, 10: 895, 20: 196, 45: 21, 100: 6}
    checks = {
        "全棟数": (total, expect["total"]),
        "不明群": (unknown, expect["unknown"]),
        "高さ10m以上": (bands[10], expect[10]),
        "高さ20m以上": (bands[20], expect[20]),
        "高さ45m以上": (bands[45], expect[45]),
        "高さ100m以上": (bands[100], expect[100]),
    }
    print("  設計書値との照合:")
    all_ok = True
    for name, (got, exp) in checks.items():
        ok = got == exp
        all_ok &= ok
        print(f"    {name:<12}: 実測 {got} / 設計 {exp}  {'一致' if ok else '★相違'}")

    return {
        "total": total, "unknown": unknown, "reduction": reduction,
        "sentinels": sent, "bands": bands, "checks": checks, "all_ok": all_ok,
    }


def run(data_dir: str, version: str, db_path: str, targets_out: str) -> dict:
    con = connect(db_path)
    print(f"=== ingest version={version} / city_code={CITY_CODE} ===")
    loaded = load_version(con, data_dir, version)
    print(f"読み込み完了: {loaded} 棟")
    value_distribution(con, version)
    check_targets(con, version)
    n = export_targets(con, version, targets_out)
    print(f"\ntargets.geojson 書き出し: {n} 件 → {targets_out}")
    summary = summarize(con, version)
    con.close()
    return summary
