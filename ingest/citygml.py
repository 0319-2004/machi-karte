"""PLATEAU 建築物 CityGML の逐次パーサ。

`bldg:Building` を `lxml.iterparse` で1棟ずつ流し、全部をメモリに載せない。
各棟から設計書 buildings の素の値（raw）と外形（LOD0 RoofEdge）を取り出す。
座標系は EPSG:6697（緯度・経度・標高）で入っているので、平面直角第9系
EPSG:6677 と表示用 EPSG:4326 の両方へ変換して返す。
"""
from __future__ import annotations

from dataclasses import dataclass

from lxml import etree
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon

# --- 名前空間（検証で確認した実データの定義） ---
# bldg/core/gml は版をまたいで同じ。uro だけ版で異なる
# （2025年度版=IUR 3.2 / 2023年度版=IUR 3.1）ため、localname で判定する。
BLDG = "{http://www.opengis.net/citygml/building/2.0}"
CORE = "{http://www.opengis.net/citygml/2.0}"
GML = "{http://www.opengis.net/gml}"


def _localname(tag) -> str:
    """名前空間付きタグから localname を返す（uro の版差を吸収）。"""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

# EPSG:6697 の水平成分は JGD2011 地理座標（EPSG:6668）と同じ。
# 緯度経度 → 平面直角第9系（メートル）。always_xy=True なので入力は (lon, lat)。
_TO_6677 = Transformer.from_crs("EPSG:6668", "EPSG:6677", always_xy=True)


@dataclass
class BuildingRow:
    building_id: str
    city_code: str
    raw_usage: str | None
    raw_detailed_usage: str | None
    raw_storeys_above: str | None
    raw_survey_year: str | None
    raw_height: str | None
    raw_roof_edge_area: str | None
    creation_date: str | None
    lod_type: str
    wkt_6677: str | None
    wkt_4326: str | None


def _text(el) -> str | None:
    if el is None:
        return None
    t = (el.text or "").strip()
    return t or None


def _rings_from_polygon(poly_el):
    """gml:Polygon から外周・内周のリング（(lon, lat) 列）を返す。

    posList は "緯度 経度 標高 緯度 経度 標高 ..." の並び（EPSG:6697）。
    """
    def ring(linear_ring_el):
        pos = linear_ring_el.find(f"{GML}posList")
        if pos is None or not pos.text:
            return None
        nums = pos.text.split()
        # 3 次元（lat lon h）。(lon, lat) に並べ替える。
        return [(float(nums[i + 1]), float(nums[i])) for i in range(0, len(nums), 3)]

    ext_el = poly_el.find(f"{GML}exterior/{GML}LinearRing")
    exterior = ring(ext_el) if ext_el is not None else None
    if not exterior:
        return None
    interiors = []
    for int_el in poly_el.findall(f"{GML}interior/{GML}LinearRing"):
        r = ring(int_el)
        if r:
            interiors.append(r)
    return exterior, interiors


def _geometry(building):
    """LOD0 RoofEdge から shapely ジオメトリ（EPSG:4326, lon/lat）を組む。"""
    roof = building.find(f"{BLDG}lod0RoofEdge")
    if roof is None:
        return None
    polys = []
    for poly_el in roof.findall(f".//{GML}Polygon"):
        parsed = _rings_from_polygon(poly_el)
        if parsed is None:
            continue
        exterior, interiors = parsed
        polys.append(Polygon(exterior, interiors))
    if not polys:
        return None
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def _to_6677(geom_4326):
    """shapely ジオメトリ(4326) を EPSG:6677 の WKT に変換する。"""
    def conv(coords):
        lons, lats = zip(*coords)
        xs, ys = _TO_6677.transform(lons, lats)
        return list(zip(xs, ys))

    def conv_poly(p: Polygon) -> Polygon:
        return Polygon(conv(p.exterior.coords), [conv(r.coords) for r in p.interiors])

    if geom_4326.geom_type == "Polygon":
        return conv_poly(geom_4326).wkt
    return MultiPolygon([conv_poly(p) for p in geom_4326.geoms]).wkt


def _lod_type(building) -> str:
    """幾何として入っている最上位 LOD を返す（テクスチャ有無判定の手掛かり）。"""
    if (
        building.find(f"{BLDG}lod2Solid") is not None
        or building.find(f"{BLDG}lod2MultiSurface") is not None
    ):
        return "LOD2"
    if building.find(f"{BLDG}lod1Solid") is not None:
        return "LOD1"
    return "LOD0"


def iter_buildings(gml_path: str, city_code: str | None = None):
    """GML を1棟ずつ走査して BuildingRow を yield する。

    city_code を与えると、buildingID の接頭コードが一致する棟だけ返す
    （PLATEAU のメッシュタイルには隣接区の建物も含まれるため）。
    """
    context = etree.iterparse(gml_path, events=("end",), tag=f"{BLDG}Building")
    for _, b in context:
        bid = None
        raw = {
            "usage": None,
            "detailed_usage": None,
            "storeys_above": None,
            "survey_year": None,
            "height": None,
            "roof_edge_area": None,
        }
        creation_date = None
        for c in b:
            tag = c.tag
            local = _localname(tag)
            if tag == f"{BLDG}usage" and raw["usage"] is None:
                raw["usage"] = _text(c)
            elif tag == f"{BLDG}storeysAboveGround":
                raw["storeys_above"] = _text(c)
            elif tag == f"{BLDG}measuredHeight":
                raw["height"] = _text(c)
            elif tag == f"{CORE}creationDate":
                creation_date = _text(c)
            elif local == "buildingIDAttribute":  # uro（版非依存）
                bia = c.find("{*}BuildingIDAttribute")
                if bia is not None:
                    bid = _text(bia.find("{*}buildingID"))
            elif local == "buildingDetailAttribute":  # uro（版非依存）
                bda = c.find("{*}BuildingDetailAttribute")
                if bda is not None:
                    raw["detailed_usage"] = _text(bda.find("{*}detailedUsage"))
                    raw["survey_year"] = _text(bda.find("{*}surveyYear"))
                    raw["roof_edge_area"] = _text(bda.find("{*}buildingRoofEdgeArea"))

        prefix = (bid or "").split("-")[0]
        if city_code is None or prefix == city_code:
            geom = _geometry(b)
            yield BuildingRow(
                building_id=bid,
                city_code=prefix or None,
                raw_usage=raw["usage"],
                raw_detailed_usage=raw["detailed_usage"],
                raw_storeys_above=raw["storeys_above"],
                raw_survey_year=raw["survey_year"],
                raw_height=raw["height"],
                raw_roof_edge_area=raw["roof_edge_area"],
                creation_date=creation_date,
                lod_type=_lod_type(b),
                wkt_4326=geom.wkt if geom is not None else None,
                wkt_6677=_to_6677(geom) if geom is not None else None,
            )

        # メモリ解放（処理済みの棟と前方の兄弟を捨てる）
        b.clear()
        while b.getprevious() is not None:
            del b.getparent()[0]
