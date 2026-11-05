"""export（更新候補リスト）の不変条件を固定する。

設計書「出力しないもの」：人が確定していない推定値・判断不能の属性は出さない。
ここを pytest で保証する。あわせて CSV の BOM、建築年の区間、現在値の生値、
city_code 可変（他自治体でも通る）を確認する。
"""
from __future__ import annotations

import duckdb

from api.schema import ensure_tables
from export.update_candidates import csv_bytes, fetch_rows, geojson_obj
from ingest.pipeline import schema_sql

GEOM = ("POLYGON((139.690 35.680,139.691 35.680,139.691 35.681,"
        "139.690 35.681,139.690 35.680))")


def _setup(db_path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path)
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(schema_sql("buildings"))
    ensure_tables(con)

    # buildings：B1/B2/B3/B4 は新宿(13104)、B5 は別区(13101)
    bld = [
        ("B1", "461", "9999"), ("B2", "461", "9999"),
        ("B3", "461", "9999"), ("B4", "461", "9999"),
    ]
    for bid, ru, rs in bld:
        con.execute(
            "INSERT INTO buildings (building_id, version, city_code, raw_usage, "
            "raw_storeys_above, is_target, geom_4326) "
            "VALUES (?, '2025', '13104', ?, ?, TRUE, ST_GeomFromText(?))",
            [bid, ru, rs, GEOM],
        )
    con.execute(
        "INSERT INTO buildings (building_id, version, city_code, raw_usage, "
        "raw_storeys_above, is_target, geom_4326) "
        "VALUES ('B5', '2025', '13101', '461', '9999', TRUE, ST_GeomFromText(?))",
        [GEOM],
    )

    # candidates（B4 には判断不能の storeys も入れる＝確定が無ければ出ない）
    cand = [
        ("B1", "usage", "412", None, None, 0.5, "neighbor", "r-usage-b1", ["e1"], False),
        ("B1", "storeys", "10", None, None, 0.8, "height", "r-storeys-b1", [], False),
        ("B2", "usage", "412", None, None, 0.5, "neighbor", "r-usage-b2", [], False),
        ("B3", "year_built", None, 2022, 2024, 0.7, "version_diff", "r-year-b3", [], False),
        ("B4", "usage", "412", None, None, 0.5, "neighbor", "r-usage-b4", [], False),
        ("B4", "storeys", None, None, None, 0.0, "height", "高さ欠損", [], True),
        ("B5", "usage", "401", None, None, 0.5, "neighbor", "r-usage-b5", [], False),
    ]
    con.executemany(
        "INSERT INTO candidates (building_id, version, attribute, value, value_min, "
        "value_max, confidence, source, reasoning, evidence_ids, unresolvable) "
        "VALUES (?, '2025', ?, ?, ?, ?, ?, ?, ?, ?, ?)", cand,
    )
    con.execute("INSERT INTO evidence (evidence_id, building_id, version, kind, label) "
                "VALUES ('e1', 'B1', '2025', 'poi', 'POI: cafe')")

    # confirmations：B1(採用×2)・B2(修正)・B3(year採用)・B5(別区)。B4 は未確定。
    conf = [
        ("B1", "usage", "412", True, "tester"),
        ("B1", "storeys", "10", True, "tester"),
        ("B2", "usage", "401", False, None),   # 修正して確定
        ("B3", "year_built", "2022-2024", True, "tester"),
        ("B5", "usage", "401", True, "tester"),
    ]
    for bid, attr, val, acc, by in conf:
        con.execute(
            "INSERT INTO confirmations (building_id, version, attribute, confirmed_value, "
            "accepted_candidate, opened_at, confirmed_at, confirmed_by) "
            "VALUES (?, '2025', ?, ?, ?, now(), now(), ?)",
            [bid, attr, val, acc, by],
        )
    return con


def test_only_confirmed_no_unresolved(tmp_path):
    con = _setup(str(tmp_path / "t.duckdb"))
    rows = fetch_rows(con, city_code="13104")
    ids = {r["building_id"] for r in rows}

    # 未確定の B4 は出ない（判断不能 storeys も含め構造的に除外）
    assert "B4" not in ids
    # 別区 B5 は city_code=13104 では出ない
    assert "B5" not in ids
    assert ids == {"B1", "B2", "B3"}
    # 判断不能の行は1つも無い
    assert all(r["reasoning"] != "高さ欠損" for r in rows)


def test_row_values(tmp_path):
    con = _setup(str(tmp_path / "t.duckdb"))
    rows = {(r["building_id"], r["attribute"]): r for r in fetch_rows(con, "13104")}

    u = rows[("B1", "usage")]
    assert u["current_value"] == "461" and u["current_label"] == "不明"
    assert u["proposed_value"] == "412" and u["proposed_label"] == "共同住宅"
    assert u["source"] == "neighbor" and u["reasoning"] == "r-usage-b1"
    assert u["evidence_url"] == "POI: cafe"

    s = rows[("B1", "storeys_above")]
    assert s["current_value"] == "9999" and s["proposed_value"] == "10"
    assert s["proposed_label"] == "10階" and s["source"] == "height"
    assert s["confirmed_by"] == "tester"

    # 修正した行は source=manual、confidence/reasoning 空
    m = rows[("B2", "usage")]
    assert m["source"] == "manual" and m["confidence"] == "" and m["reasoning"] == ""
    assert m["confirmed_by"] == "調査担当者"   # confirmed_by 未入力の既定

    # 建築年は区間。proposed は '2022-2024'、value_min/max 別列
    y = rows[("B3", "year_built")]
    assert y["proposed_value"] == "2022-2024"
    assert y["value_min"] == 2022 and y["value_max"] == 2024
    assert y["proposed_label"] == "2022〜2024年" and y["current_label"] == "未記録"


def test_csv_has_bom(tmp_path):
    con = _setup(str(tmp_path / "t.duckdb"))
    data = csv_bytes(fetch_rows(con, "13104"))
    assert data.startswith(b"\xef\xbb\xbf")        # UTF-8 BOM（Excel 対応）
    assert "共同住宅".encode("utf-8") in data


def test_city_code_variable(tmp_path):
    con = _setup(str(tmp_path / "t.duckdb"))
    rows = fetch_rows(con, city_code="13101")       # 別区でも同じ処理が通る
    ids = {r["building_id"] for r in rows}
    assert ids == {"B5"}


def test_geojson_features_match_rows(tmp_path):
    con = _setup(str(tmp_path / "t.duckdb"))
    rows = fetch_rows(con, "13104")
    gj = geojson_obj(con, rows)
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) == len(rows)
    assert all(f["geometry"] is not None for f in gj["features"])
    assert "attribution" in gj and "license_note" in gj
