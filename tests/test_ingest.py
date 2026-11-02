"""ingest のパーサ・正規化のテスト（小さなサンプル GML を使う）。

検証する不変条件：
- センチネル値が NULL（None）に正規化される
- 素の値が raw_* に保持される
- 外形が EPSG:4326 と EPSG:6677 に正しく変換される
- buildingID の接頭コードで city_code フィルタが効く
"""
from __future__ import annotations

import os

from shapely import wkt

from ingest.citygml import iter_buildings
from ingest.normalize import normalize

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample_bldg.gml")


def _rows(city_code="13104"):
    return {r.building_id: r for r in iter_buildings(FIXTURE, city_code=city_code)}


def test_city_code_filter():
    # 13104 指定なら隣接区(13113)は除外され、2棟だけ
    rows = _rows("13104")
    assert set(rows) == {"13104-bldg-1", "13104-bldg-2"}
    # フィルタなしなら 3 棟すべて
    all_rows = _rows(None)
    assert len(all_rows) == 3
    assert "13113-bldg-9" in all_rows


def test_raw_values_preserved():
    # 不明群の棟は素の値をそのまま持つ
    r = _rows()["13104-bldg-2"]
    assert r.raw_usage == "461"
    assert r.raw_detailed_usage == "0"
    assert r.raw_storeys_above == "9999"
    assert r.raw_survey_year == "0001"
    assert r.raw_height == "-9999"
    assert r.raw_roof_edge_area == "-9999"


def test_sentinel_normalized_to_none():
    r = _rows()["13104-bldg-2"]
    n = normalize(r)
    assert n["usage"] is None
    assert n["detailed_usage"] is None
    assert n["storeys_above"] is None
    assert n["survey_year"] is None
    assert n["height_m"] is None
    assert n["roof_edge_area"] is None


def test_normal_values_kept():
    r = _rows()["13104-bldg-1"]
    n = normalize(r)
    assert n["usage"] == "411"
    assert n["detailed_usage"] == "1310"
    assert n["storeys_above"] == 3
    assert n["survey_year"] == "2021"
    assert n["height_m"] == 9.3
    assert n["roof_edge_area"] == 123.4


def test_lod_type_detection():
    rows = _rows()
    assert rows["13104-bldg-1"].lod_type == "LOD1"
    assert rows["13104-bldg-2"].lod_type == "LOD2"


def test_geometry_4326():
    r = _rows()["13104-bldg-1"]
    g = wkt.loads(r.wkt_4326)
    assert g.geom_type == "Polygon"
    minx, miny, maxx, maxy = g.bounds
    # サンプルの矩形（lon≈139.700, lat≈35.690）
    assert 139.6999 <= minx <= 139.7006
    assert 35.6899 <= miny <= 35.6906


def test_geometry_6677_is_projected_meters():
    r = _rows()["13104-bldg-1"]
    g6677 = wkt.loads(r.wkt_6677)
    g4326 = wkt.loads(r.wkt_4326)
    # 平面直角第9系はメートル座標。緯度経度とは桁が違う（投影されている）。
    x, y = list(g6677.exterior.coords)[0]
    assert abs(x) > 1000 and abs(y) > 1000
    # 面積も 1 平方度のような極小値ではなく、m^2 オーダー（この矩形は数千 m^2）
    assert g6677.area > 100
    assert g4326.area < 1e-3
