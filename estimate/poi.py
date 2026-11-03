"""POI結合（設計書「POI結合（用途推定の本命）」）。

手順（設計書どおり）:
    1. Overture Places と OSM POI を、まずポリゴン内包で建物に割り当てる
    2. 内包から外れた POI は 5〜10m 以内の最近傍ポリゴンに割り当て、
       距離に応じて信頼度を下げる
    3. 1棟に複数 POI があれば、カテゴリ分布を特徴量にする（雑居ビル対策）
    4. OSM 建物タグ（building=*）は別ソース（source='osm_building'）として付ける

座標系は EPSG:6677（メートル）。建物外形は lod0RoofEdge なので足元より
一回り大きい（内包がやや緩めに当たる、decisions.md 2026-11-02）。

カテゴリ → 用途コードの対応は estimate/poi_mapping.yaml に外出し。
判断に迷うカテゴリ（シェアオフィス・民泊・駐車場など）は run() の末尾に報告する。
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

import duckdb
import numpy as np
import yaml
from pyproj import Transformer
from shapely import STRtree, points as shapely_points
from shapely import wkb as shapely_wkb

DB_PATH = "data/machikarte.duckdb"
VERSION = "2025"
POI_DIR = "data/raw/poi"
MAPPING_PATH = os.path.join(os.path.dirname(__file__), "poi_mapping.yaml")

NEAR_MAX_M = 10.0          # 最近傍で拾う最大距離（m）
# 緯度経度(WGS84) → 平面直角第9系（m）。ingest と同じ always_xy=True。
_TO_6677 = Transformer.from_crs("EPSG:4326", "EPSG:6677", always_xy=True)

# OSM POI でカテゴリに使うタグ（この優先順で最初に見つかったもの）
OSM_POI_KEYS = ["amenity", "shop", "office", "tourism", "leisure", "healthcare"]


def load_mapping(path: str = MAPPING_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def connect(db_path: str = DB_PATH) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path)
    con.execute("LOAD spatial;")
    return con


# --- 読み込み ---------------------------------------------------------------

def load_buildings(con, version: str = VERSION):
    """建物の building_id と外形(6677 shapely polygon)を返す。"""
    rows = con.execute(
        "SELECT building_id, ST_AsWKB(geom) FROM buildings "
        "WHERE version=? AND geom IS NOT NULL",
        [version],
    ).fetchall()
    ids = [r[0] for r in rows]
    geoms = [shapely_wkb.loads(bytes(r[1])) for r in rows]
    return ids, geoms


def load_pois(con):
    """3ソースの POI を (source, category, lon, lat) のリストで返す。

    source は 'overture' / 'osm_poi' / 'osm_building'（用途ソースの区別は
    map_code で 'poi' / 'osm_building' に畳む）。category は生の文字列。
    """
    recs = []  # (source, category, lon, lat)

    # Overture Places（geometry は CRS84 = lon/lat）
    ov = con.execute(
        f"""
        SELECT coalesce(basic_category, taxonomy.primary) AS cat,
               ST_X(geometry), ST_Y(geometry)
        FROM read_parquet('{POI_DIR}/overture_places.parquet')
        WHERE geometry IS NOT NULL
        """
    ).fetchall()
    for cat, lon, lat in ov:
        recs.append(("overture", cat, lon, lat))

    # OSM POI
    with open(f"{POI_DIR}/osm_poi.geojson", encoding="utf-8") as f:
        for feat in json.load(f)["features"]:
            p = feat["properties"]
            cat = None
            for k in OSM_POI_KEYS:
                if p.get(k):
                    cat = f"{k}={p[k]}"
                    break
            lon, lat = feat["geometry"]["coordinates"][:2]
            recs.append(("osm_poi", cat, lon, lat))

    # OSM 建物タグ
    with open(f"{POI_DIR}/osm_buildings.geojson", encoding="utf-8") as f:
        for feat in json.load(f)["features"]:
            val = feat["properties"].get("building")
            lon, lat = feat["geometry"]["coordinates"][:2]
            recs.append(("osm_building", val, lon, lat))

    return recs


# --- カテゴリ → 用途コード --------------------------------------------------

def map_code(source: str, category, mapping: dict):
    """(source, category) を (usage_source, usage_code) に。使わないものは (_, None)。

    usage_source は 'poi'（overture/osm_poi）か 'osm_building'。
    """
    drop = mapping.get("drop", {})
    if source == "overture":
        if category is None or category in drop.get("overture", []):
            return "poi", None
        code = mapping["overture"].get(category)
        if code is None:
            for kw, c in mapping.get("overture_keywords", {}).items():
                if kw in category:
                    code = c
                    break
        return "poi", code
    if source == "osm_poi":
        if category is None or category in drop.get("osm_poi_tag", []):
            return "poi", None
        tbl = mapping["osm_poi"]
        code = tbl["by_tag"].get(category)
        if code is None:
            key = category.split("=", 1)[0]
            code = tbl["by_key"].get(key)
        return "poi", code
    # osm_building
    if category is None or category in drop.get("osm_building_value", []):
        return "osm_building", None
    return "osm_building", mapping["osm_building"].get(category)


# --- 割り当て ---------------------------------------------------------------

def assign(building_geoms, poi_lonlat, near_max_m: float = NEAR_MAX_M):
    """各 POI を最近傍建物（内包＝距離0 を含む）に割り当てる。

    返り値: (bld_idx, poi_idx, dist, conf)
      - bld_idx[i] = poi_idx[i] の割り当て先の建物 index
      - conf = 内包(dist=0) で 1.0、距離に応じて線形に減衰（near_max_m で 0.1）
    near_max_m を超える POI は捨てる。
    """
    lon = np.array([r[2] for r in poi_lonlat], dtype=float)
    lat = np.array([r[3] for r in poi_lonlat], dtype=float)
    x, y = _TO_6677.transform(lon, lat)
    pts = shapely_points(np.c_[x, y])

    tree = STRtree(building_geoms)
    # 内包は距離0の最近傍として拾える（lod0RoofEdge は足元より大きめ）
    res = tree.query_nearest(
        pts, max_distance=near_max_m, return_distance=True, all_matches=False
    )
    poi_idx = res[0][0]
    bld_idx = res[0][1]
    dist = res[1]
    # 距離に応じた信頼度：0m→1.0、near_max_m→0.1（線形）
    conf = np.clip(1.0 - 0.9 * (dist / near_max_m), 0.1, 1.0)
    return bld_idx, poi_idx, dist, conf


# --- 特徴量と予測 -----------------------------------------------------------

def build_features(building_ids, pois, bld_idx, poi_idx, conf, mapping):
    """建物ごとに POI 特徴量と用途予測を作る。

    返り値: dict building_id -> {
        poi_pred, poi_conf, poi_n,        # source='poi' 由来
        osmb_pred, osmb_n,                # source='osm_building' 由来
        cat_dist,                         # 生カテゴリの分布(Counter, poi由来)
    }
    予測は「用途コードごとに信頼度を足し込んで最大」。
    """
    # building_id ごとに (usage_source, code, conf, raw_category) を集める
    poi_votes = defaultdict(lambda: defaultdict(float))   # bid -> code -> conf和
    osmb_votes = defaultdict(lambda: defaultdict(float))
    poi_n = Counter()
    osmb_n = Counter()
    cat_dist = defaultdict(Counter)

    for bi, pi, c in zip(bld_idx, poi_idx, conf):
        source, category, _lon, _lat = pois[pi]
        bid = building_ids[bi]
        usage_source, code = map_code(source, category, mapping)
        if usage_source == "osm_building":
            osmb_n[bid] += 1
            if code is not None:
                osmb_votes[bid][code] += float(c)
        else:
            poi_n[bid] += 1
            if category is not None:
                cat_dist[bid][category] += 1
            if code is not None:
                poi_votes[bid][code] += float(c)

    def _argmax(votes):
        if not votes:
            return None, 0.0
        code, w = max(votes.items(), key=lambda kv: kv[1])
        return code, w

    feats = {}
    bids = set(poi_n) | set(osmb_n)
    for bid in bids:
        poi_pred, poi_w = _argmax(poi_votes.get(bid, {}))
        osmb_pred, _ = _argmax(osmb_votes.get(bid, {}))
        feats[bid] = {
            "poi_pred": poi_pred,
            "poi_conf": poi_w,
            "poi_n": poi_n.get(bid, 0),
            "osmb_pred": osmb_pred,
            "osmb_n": osmb_n.get(bid, 0),
            "cat_dist": dict(cat_dist.get(bid, {})),
        }
    return feats


def compute(con=None, version: str = VERSION):
    """読み込み → 割り当て → 特徴量まで一括。feats と中間物を返す。"""
    own = con is None
    if own:
        con = connect()
    mapping = load_mapping()
    building_ids, geoms = load_buildings(con, version)
    pois = load_pois(con)
    bld_idx, poi_idx, dist, conf = assign(geoms, pois)
    feats = build_features(building_ids, pois, bld_idx, poi_idx, conf, mapping)
    if own:
        con.close()
    return {
        "feats": feats,
        "building_ids": building_ids,
        "pois": pois,
        "assign": (bld_idx, poi_idx, dist, conf),
        "mapping": mapping,
    }


def run() -> None:
    con = connect()
    out = compute(con)
    feats, pois = out["feats"], out["pois"]
    bld_idx, poi_idx, dist, conf = out["assign"]
    n_bld = len(out["building_ids"])

    assigned = len(poi_idx)
    print("=== POI 結合サマリ ===")
    print(f"  POI 総数（3ソース）     : {len(pois)}")
    print(f"  建物に割り当て（≤{NEAR_MAX_M:.0f}m）: {assigned}"
          f"（内包=距離0: {int(np.sum(dist == 0))}）")
    src_assigned = Counter(pois[pi][0] for pi in poi_idx)
    for s in ("overture", "osm_poi", "osm_building"):
        print(f"    {s:<13}: {src_assigned.get(s, 0)}")

    with_poi = sum(1 for v in feats.values() if v["poi_pred"] is not None)
    with_osmb = sum(1 for v in feats.values() if v["osmb_pred"] is not None)
    print(f"\n  用途予測が付いた建物（poi）        : {with_poi} / {n_bld}"
          f"（{with_poi/n_bld*100:.1f}%）")
    print(f"  用途予測が付いた建物（osm_building）: {with_osmb} / {n_bld}"
          f"（{with_osmb/n_bld*100:.1f}%）")

    # 判断に迷うカテゴリの報告
    print("\n=== 判断に迷うカテゴリ（poi_mapping.yaml: ambiguous）===")
    for a in out["mapping"].get("ambiguous", []):
        print(f"  - {a['category']}\n      → {a['note']}")

    con.close()


if __name__ == "__main__":
    run()
