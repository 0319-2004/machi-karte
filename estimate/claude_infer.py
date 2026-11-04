"""Claude API による属性推定（設計書「Claudeに渡すもの、返させるもの」）。

画像がまだ無い棟でも動く。1棟ごとに「事実（高さ・外形面積・周辺の用途分布・POI・
仮の推定）」と（あれば）画像を渡し、用途(usage)と階数(storeys)を構造化 JSON で返させる。

要点（設計書・プロンプト集の指示に対応）:
- 出力は tool/構造化出力のスキーマで強制する（usage / storeys / unresolvable）。
  ここでは Messages API の構造化出力（output_config.format の json_schema）を使う。
- パースに失敗したら1回だけリトライし、だめならその属性を判断不能(unresolvable)にする。
- 結果は candidates（source='image'＝Claude の多モーダル推定チャネル）に。判断不能も
  value=NULL / confidence=0 / unresolvable=TRUE の1行として残す。画像は evidence(kind='image')に。
- モデルは環境変数 CLAUDE_MODEL。入力ハッシュでキャッシュして二重課金しない。
- 並列数を絞り、429 は SDK のリトライ＋バックオフに任せる。1棟あたりのトークンと費用をログに残す。

プロンプトは estimate/prompts/usage_storeys_v1.md（版番号つき）。

実行:
    uv run python -m estimate.claude_infer --limit 20          # 不明群の先頭20棟に投入
    uv run python -m estimate.claude_infer --limit 5 --dry-run # API を呼ばず入力だけ確認
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import numpy as np
from lxml import etree
from scipy.spatial import cKDTree
from dotenv import load_dotenv

from api.schema import ensure_tables
from eval.common import load_known
from eval.holdout import VERSION, connect
from estimate.storeys import (HEIGHT_TOL, NEIGHBOR_K, floor_height_for,
                              learn_floor_heights, predict_storeys)

# --- 定数 -------------------------------------------------------------------

DB_PATH = "data/machikarte.duckdb"
PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")
PROMPT_FILE = "usage_storeys_v1.md"
USAGE_XML = "data/raw/plateau/2025/codelists/Building_usage.xml"
CACHE_DIR = "data/cache/claude_infer"
LOG_PATH = "data/derived/claude_infer_log.jsonl"

DEFAULT_MODEL = "claude-opus-5-5"   # CLAUDE_MODEL 未設定時のフォールバック
MAX_OUTPUT_TOKENS = 2048            # 返すのは小さな JSON のみ
MAX_API_RETRIES = 5                 # SDK の自動リトライ回数（429/5xx をバックオフ）
NEIGHBOR_DESC_K = 8                 # 周辺の用途分布に使う近傍棟数
HIGHRISE_M = 20.0                   # 「高層の複合用途」注意を促す高さ

# モデル別の価格（USD / 1M tokens, (入力, 出力)）。費用ログ用。未知モデルは None。
# 値は platform.claude.com の料金ページ（2026-11 時点）より。推測しない。
# 注: haiku-5-5 は 10万トークン超で (0.50, 2.50) に上がる階層価格。本件は1棟あたり
#     数千トークンのため ≤10万時の (0.10, 0.50) を採る。compute_cost は単一レート前提。
PRICING = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# 用途コード（Building_usage.xml の18コード）のうち、併用・複合系（高層注意の対象）
MIXED_USE_CODES = {"404", "413", "414", "415"}


class ParseError(Exception):
    """構造化出力のパース・検証に失敗。"""


# --- 設定（モデル・プロンプト・スキーマ） -----------------------------------

def get_model() -> str:
    return os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL


def load_prompt(name: str = PROMPT_FILE) -> tuple[str, str]:
    """プロンプト本文と版番号（ファイル名から）を返す。"""
    path = os.path.join(PROMPTS_DIR, name)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"_v(\d+)\.md$", name)
    version = f"v{m.group(1)}" if m else name
    return text, version


def load_usage_codes(path: str = USAGE_XML) -> dict[str, str]:
    """Building_usage.xml を {コード: 名称} で返す（18コード）。"""
    ns = {"gml": "http://www.opengis.net/gml"}
    tree = etree.parse(path)
    codes: dict[str, str] = {}
    for d in tree.iterfind(".//gml:Definition", ns):
        code = (d.findtext("gml:name", namespaces=ns) or "").strip()
        label = (d.findtext("gml:description", namespaces=ns) or "").strip()
        if code:
            codes[code] = label
    return codes


def format_usage_codes(usage_codes: dict[str, str]) -> str:
    return "\n".join(f"- {c} {label}" for c, label in usage_codes.items())


def build_schema(usage_codes: dict[str, str]) -> dict:
    """構造化出力の json_schema（usage / storeys / unresolvable）。"""
    code_enum = list(usage_codes.keys())

    def attr(value_schema):
        return {
            "type": "object",
            "properties": {
                "value": value_schema,
                "confidence": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["value", "confidence", "reasoning"],
            "additionalProperties": False,
        }

    return {
        "type": "object",
        "properties": {
            "usage": attr({"anyOf": [{"type": "string", "enum": code_enum},
                                     {"type": "null"}]}),
            "storeys": attr({"anyOf": [{"type": "integer"}, {"type": "null"}]}),
            "unresolvable": {
                "type": "array",
                "items": {"type": "string", "enum": ["usage", "storeys"]},
            },
        },
        "required": ["usage", "storeys", "unresolvable"],
        "additionalProperties": False,
    }


# --- 周辺の用途分布（近傍の既知棟から） -------------------------------------

class NeighborContext:
    """近傍の既知棟から「周辺の用途分布」を作る。

    train だけ（評価）/ 全既知棟（本番）のどちらでも、同じ使い方で距離の近い
    既知棟の用途分布を返す。評価では exclude_id で自棟・リークを避ける。
    """

    def __init__(self, xy, usage, height, ids):
        self.xy = np.asarray(xy, dtype=float)
        self.usage = np.asarray(usage)
        self.height = np.asarray(height, dtype=float)
        self.ids = np.asarray(ids)
        self.tree = cKDTree(self.xy)

    def describe(self, pt, usage_codes, k=NEIGHBOR_DESC_K, exclude_id=None):
        """近傍 k 棟の用途分布（コード・名称・件数、近い順）を返す。"""
        # exclude 用に少し多めに取り、自棟を落としてから k に絞る
        kq = min(k + 2, len(self.ids))
        if kq == 0:
            return []
        _, idx = self.tree.query(pt, k=kq)
        idx = np.atleast_1d(idx)
        if exclude_id is not None:
            idx = np.array([i for i in idx if self.ids[i] != exclude_id])
        idx = idx[:k]
        codes = [self.usage[i] for i in idx]
        dist = []
        for code, cnt in Counter(codes).most_common():
            dist.append({"code": code, "label": usage_codes.get(code, "?"),
                         "count": cnt})
        return dist


def neighbor_context_known(con, version=VERSION, split=None) -> NeighborContext:
    """既知棟から近傍モデルを作る。split='train' で評価用（リーク防止）。"""
    d = load_known(con, version)
    if split is not None:
        m = d["split"] == split
        return NeighborContext(d["xy"][m], d["usage"][m], d["height"][m],
                               d["building_id"][m])
    return NeighborContext(d["xy"], d["usage"], d["height"], d["building_id"])


# --- POI（カテゴリ・名称・距離） --------------------------------------------

def _load_pois_named(poi_dir="data/raw/poi"):
    """3ソースの POI を名称つきで (source, category, name, lon, lat) のリストで返す。

    overture の名称列はスキーマ差があるため best-effort（取れなければ name=None）。
    """
    import duckdb
    from estimate.poi import OSM_POI_KEYS

    recs = []
    # Overture Places（名称は names.primary を試す。失敗したらカテゴリのみ）
    con = duckdb.connect()
    for name_expr in ("names.primary", "name"):
        try:
            rows = con.execute(
                f"""
                SELECT coalesce(basic_category, taxonomy.primary) AS cat,
                       {name_expr} AS nm, ST_X(geometry), ST_Y(geometry)
                FROM read_parquet('{poi_dir}/overture_places.parquet')
                WHERE geometry IS NOT NULL
                """
            ).fetchall()
            for cat, nm, lon, lat in rows:
                recs.append(("overture", cat, nm, lon, lat))
            break
        except Exception:
            continue
    else:
        # 名称列が無い場合はカテゴリのみ
        try:
            rows = con.execute(
                f"SELECT coalesce(basic_category, taxonomy.primary), "
                f"ST_X(geometry), ST_Y(geometry) "
                f"FROM read_parquet('{poi_dir}/overture_places.parquet') "
                f"WHERE geometry IS NOT NULL"
            ).fetchall()
            for cat, lon, lat in rows:
                recs.append(("overture", cat, None, lon, lat))
        except Exception:
            pass
    con.close()

    # OSM POI
    with open(f"{poi_dir}/osm_poi.geojson", encoding="utf-8") as f:
        for feat in json.load(f)["features"]:
            p = feat["properties"]
            cat = None
            for key in OSM_POI_KEYS:
                if p.get(key):
                    cat = f"{key}={p[key]}"
                    break
            lon, lat = feat["geometry"]["coordinates"][:2]
            recs.append(("osm_poi", cat, p.get("name"), lon, lat))

    # OSM 建物タグ（名称があれば拾う）
    with open(f"{poi_dir}/osm_buildings.geojson", encoding="utf-8") as f:
        for feat in json.load(f)["features"]:
            p = feat["properties"]
            lon, lat = feat["geometry"]["coordinates"][:2]
            recs.append(("osm_building", p.get("building"), p.get("name"), lon, lat))
    return recs


def poi_evidence_by_building(con, version=VERSION, top_n=6) -> dict[str, list[dict]]:
    """各建物の近傍 POI（カテゴリ・名称・距離）を上位 top_n 件返す。

    estimate.poi の割り当てを名称つきの POI に適用する。失敗しても推定は続けられる
    よう、例外時は空 dict を返す（Claude には「POI なし」と伝わる）。
    """
    try:
        from estimate.poi import assign, load_buildings
        building_ids, geoms = load_buildings(con, version)
        pois = _load_pois_named()
        lonlat = [(s, c, lon, lat) for (s, c, nm, lon, lat) in pois]
        bld_idx, poi_idx, dist, conf = assign(geoms, lonlat)
    except Exception:
        return {}

    out: dict[str, list[dict]] = {}
    for bi, pi, d in zip(bld_idx, poi_idx, dist):
        source, cat, nm, _lon, _lat = pois[pi]
        if cat is None and nm is None:
            continue
        bid = building_ids[bi]
        out.setdefault(bid, []).append(
            {"source": source, "category": cat, "name": nm,
             "distance_m": round(float(d), 1)})
    for bid in out:
        out[bid].sort(key=lambda r: r["distance_m"])
        out[bid] = out[bid][:top_n]
    return out


# --- 1棟ぶんの入力（事実）を組み立てる -------------------------------------

@dataclass
class Building:
    building_id: str
    height: float | None
    area: float | None      # roof_edge_area（外形面積の近似）
    xy: tuple[float, float]


def load_buildings_rows(con, predicate: str, version=VERSION) -> list[Building]:
    rows = con.execute(
        f"""
        SELECT building_id, height_m, roof_edge_area,
               ST_X(ST_Centroid(geom)), ST_Y(ST_Centroid(geom))
        FROM buildings
        WHERE version=? AND geom IS NOT NULL AND ({predicate})
        """,
        [version],
    ).fetchall()
    out = []
    for bid, h, area, cx, cy in rows:
        out.append(Building(bid, h, area, (cx, cy)))
    return out


def build_context(b: Building, neigh: NeighborContext, poi_feat: dict,
                  poi_named: list[dict], fh: dict, usage_codes: dict,
                  exclude_id: str | None = None) -> dict:
    """1棟ぶんの「事実」を辞書にまとめる（ハッシュ・描画の両方に使う）。"""
    has_h = b.height is not None and not (isinstance(b.height, float) and np.isnan(b.height))

    neigh_dist = neigh.describe(b.xy, usage_codes, exclude_id=exclude_id)

    # 仮の推定：階数（高さ÷用途別階高）と POI 結合の用途候補
    tentative_usage = (poi_feat or {}).get("poi_pred")
    tentative_storeys = None
    floor_h_used = None
    if has_h:
        floor_h, _label = floor_height_for(tentative_usage, fh)
        floor_h_used = round(floor_h, 2)
        tentative_storeys = predict_storeys(b.height, floor_h)

    # POI カテゴリ分布（名称つきが取れない時のフォールバック）
    cat_dist = (poi_feat or {}).get("cat_dist", {})
    cat_top = [c for c, _ in Counter(cat_dist).most_common(6)]

    return {
        "building_id": b.building_id,
        "height_m": round(b.height, 1) if has_h else None,
        "roof_edge_area_m2": round(b.area, 1) if b.area is not None else None,
        "is_highrise": bool(has_h and b.height >= HIGHRISE_M),
        "neighbor_usage_distribution": neigh_dist,
        "poi_nearby": poi_named,                      # カテゴリ・名称・距離
        "poi_categories": cat_top,                    # 名称が無い場合の補助
        "tentative": {
            "storeys_from_height": tentative_storeys,
            "floor_height_m": floor_h_used,
            "usage_from_poi": tentative_usage,
        },
    }


def render_context(ctx: dict, usage_codes: dict) -> str:
    """context 辞書を、Claude に渡す読みやすい日本語テキストにする。"""
    lines = [f"建物ID: {ctx['building_id']}"]
    h = ctx["height_m"]
    lines.append(f"高さ: {h} m" if h is not None else "高さ: 不明（欠損）")
    a = ctx["roof_edge_area_m2"]
    lines.append(f"外形面積: {a} m^2" if a is not None else "外形面積: 不明")
    if ctx["is_highrise"]:
        lines.append("※ 高さ20m以上の高層棟。地上POIは1階部分しか表さない点に注意。")

    lines.append("")
    lines.append("周辺の既知建物の用途分布（近い順）:")
    if ctx["neighbor_usage_distribution"]:
        for d in ctx["neighbor_usage_distribution"]:
            lines.append(f"  - {d['code']} {d['label']}: {d['count']}棟")
    else:
        lines.append("  （近傍の既知棟なし）")

    lines.append("")
    lines.append("内包・近傍のPOI（カテゴリ / 名称 / 距離）:")
    if ctx["poi_nearby"]:
        for p in ctx["poi_nearby"]:
            nm = p.get("name") or "（名称なし）"
            cat = p.get("category") or "（分類なし）"
            lines.append(f"  - {cat} / {nm} / {p['distance_m']}m [{p['source']}]")
    elif ctx["poi_categories"]:
        lines.append("  （名称は取得できず。カテゴリのみ）: "
                     + ", ".join(ctx["poi_categories"]))
    else:
        lines.append("  （近傍POIなし）")

    lines.append("")
    t = ctx["tentative"]
    lines.append("仮の推定（参考）:")
    if t["storeys_from_height"] is not None:
        lines.append(f"  - 階数（高さ÷階高{t['floor_height_m']}m）≒ "
                     f"{t['storeys_from_height']}階")
    else:
        lines.append("  - 階数: 高さ欠損のため計算不可")
    if t["usage_from_poi"]:
        code = t["usage_from_poi"]
        lines.append(f"  - POI結合の用途候補: {code} {usage_codes.get(code, '?')}")
    else:
        lines.append("  - POI結合の用途候補: なし")

    lines.append("")
    lines.append("上記の事実と（あれば）画像から、usage と storeys を推定してください。"
                 "判断材料が足りない属性は unresolvable にしてください。")
    return "\n".join(lines)


# --- 画像 -------------------------------------------------------------------

@dataclass
class Image:
    """Claude に渡す1枚の画像。data は base64 文字列。"""
    media_type: str
    data: str
    source: str = ""     # Mapillary / ortho / lod2texture など
    url: str = ""


def no_images(building_id: str) -> list[Image]:
    """既定の画像プロバイダ：画像なし（Mapillary等は後回し）。"""
    return []


# --- API 呼び出し（構造化出力・リトライ・判断不能フォールバック） -----------

@dataclass
class InferConfig:
    model: str
    prompt_text: str
    prompt_version: str
    schema: dict
    thinking: bool = False          # 既定は思考オフ（費用の予測性のため）


def _message_content(ctx_text: str, images: list[Image]) -> list[dict]:
    content: list[dict] = []
    for img in images:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": img.media_type, "data": img.data}})
    content.append({"type": "text", "text": ctx_text})
    return content


def _fallback_result(reason: str) -> dict:
    """両属性を判断不能にした結果（パース失敗時）。"""
    cell = {"value": None, "confidence": 0.0, "reasoning": reason}
    return {"usage": dict(cell), "storeys": dict(cell),
            "unresolvable": ["usage", "storeys"]}


def _normalize_result(raw: dict, usage_codes: dict) -> dict:
    """構造化出力を検証・正規化する。判断不能の整合（null↔confidence0↔unresolvable）。"""
    if not isinstance(raw, dict):
        raise ParseError("JSON が object でない")
    out = {}
    unresolvable = set(raw.get("unresolvable") or [])
    for attr in ("usage", "storeys"):
        cell = raw.get(attr)
        if not isinstance(cell, dict) or "value" not in cell:
            raise ParseError(f"{attr} の形が不正")
        value = cell.get("value")
        conf = cell.get("confidence", 0.0)
        reasoning = cell.get("reasoning", "")
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = 0.0
        conf = min(max(conf, 0.0), 1.0)

        if attr == "usage" and value is not None:
            value = str(value)
            if value not in usage_codes:
                raise ParseError(f"未知の用途コード: {value}")
        if attr == "storeys" and value is not None:
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ParseError("storeys が整数でない")
            if value < 1:
                value = None

        # 判断不能の整合を取る（value が null → confidence 0 / unresolvable に入れる）
        if value is None:
            conf = 0.0
            unresolvable.add(attr)
        elif attr in unresolvable:
            # unresolvable なのに値がある → 値を採らず判断不能に揃える
            value = None
            conf = 0.0

        out[attr] = {"value": value, "confidence": conf,
                     "reasoning": str(reasoning)}
    out["unresolvable"] = sorted(unresolvable)
    return out


def _usage_dict(usage) -> dict:
    g = lambda k: getattr(usage, k, 0) or 0  # noqa: E731
    return {
        "input_tokens": g("input_tokens"),
        "output_tokens": g("output_tokens"),
        "cache_read_input_tokens": g("cache_read_input_tokens"),
        "cache_creation_input_tokens": g("cache_creation_input_tokens"),
    }


def _add_usage(a: dict, b: dict | None) -> dict:
    if b is None:
        return a
    return {k: a.get(k, 0) + b.get(k, 0) for k in set(a) | set(b)}


def compute_cost(model: str, usage: dict) -> float | None:
    price = PRICING.get(model)
    if price is None or not usage:
        return None
    in_m, out_m = price
    cost = (usage.get("input_tokens", 0) * in_m
            + usage.get("cache_read_input_tokens", 0) * in_m * 0.1
            + usage.get("cache_creation_input_tokens", 0) * in_m * 1.25
            + usage.get("output_tokens", 0) * out_m) / 1_000_000
    return round(cost, 6)


def call_claude(client, cfg: InferConfig, ctx_text: str,
                images: list[Image], usage_codes: dict) -> tuple[dict, dict, int]:
    """構造化出力で1棟を推定。パース失敗は1回だけリトライ、だめなら判断不能。

    返り値: (result, usage_total, attempts)
    """
    system = [{"type": "text", "text": cfg.prompt_text,
               "cache_control": {"type": "ephemeral"}}]
    kwargs = dict(
        model=cfg.model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        messages=[{"role": "user", "content": _message_content(ctx_text, images)}],
        output_config={"format": {"type": "json_schema", "schema": cfg.schema}},
    )
    if cfg.thinking:
        kwargs["thinking"] = {"type": "adaptive"}

    usage_total: dict = {}
    last_err = ""
    for attempt in (1, 2):   # 本番1回＋リトライ1回
        try:
            msg = client.messages.create(**kwargs)
            usage_total = _add_usage(usage_total, _usage_dict(msg.usage))
            if getattr(msg, "stop_reason", None) == "refusal":
                raise ParseError("refusal")
            text = next((b.text for b in msg.content if getattr(b, "type", None) == "text"), None)
            if not text:
                raise ParseError("テキストブロックなし")
            result = _normalize_result(json.loads(text), usage_codes)
            return result, usage_total, attempt
        except (ParseError, json.JSONDecodeError, ValueError) as e:
            last_err = str(e)
            continue
    return (_fallback_result(f"構造化出力のパースに失敗（{last_err}）のため判断不能"),
            usage_total, 2)


# --- キャッシュ -------------------------------------------------------------

def input_hash(cfg: InferConfig, ctx: dict, images: list[Image]) -> str:
    img_fp = [hashlib.sha256(i.data.encode()).hexdigest()[:16] for i in images]
    payload = {
        "model": cfg.model,
        "prompt_version": cfg.prompt_version,
        "thinking": cfg.thinking,
        "schema": cfg.schema,
        "context": ctx,
        "images": img_fp,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cache_path(h: str) -> str:
    return os.path.join(CACHE_DIR, f"{h}.json")


def read_cache(h: str) -> dict | None:
    path = _cache_path(h)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def write_cache(h: str, rec: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(h), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)


def infer_building(client, cfg: InferConfig, b: Building, ctx: dict,
                   images: list[Image], usage_codes: dict) -> dict:
    """キャッシュを見てから推定。二重課金を避ける。"""
    h = input_hash(cfg, ctx, images)
    cached = read_cache(h)
    if cached is not None:
        cached["cached"] = True
        return cached

    result, usage, attempts = call_claude(client, cfg, render_context(ctx, usage_codes),
                                           images, usage_codes)
    rec = {
        "building_id": b.building_id,
        "model": cfg.model,
        "prompt_version": cfg.prompt_version,
        "result": result,
        "usage": usage,
        "cost_usd": compute_cost(cfg.model, usage),
        "attempts": attempts,
        "n_images": len(images),
        "cached": False,
        "hash": h,
    }
    write_cache(h, rec)
    return rec


# --- candidates / evidence への投入 -----------------------------------------

def _existing_evidence_ids(con, bid, version) -> list[str]:
    rows = con.execute(
        "SELECT evidence_id FROM evidence WHERE building_id=? AND version=? "
        "AND kind IN ('poi','osm_building')", [bid, version]).fetchall()
    return [r[0] for r in rows]


def write_candidates(con, records: list[dict], images_by_bid: dict[str, list[Image]],
                     version=VERSION) -> dict:
    """Claude の結果を candidates(source='image') と evidence(kind='image') に入れる。"""
    ensure_tables(con)
    con.execute("DELETE FROM candidates WHERE version=? AND source='image'", [version])
    con.execute("DELETE FROM evidence WHERE version=? AND kind='image'", [version])

    cand_rows, ev_rows = [], []
    n_unres = 0
    for rec in records:
        bid = rec["building_id"]
        res = rec["result"]

        # 画像を evidence に、既存の POI/OSM evidence も紐づける
        ev_ids = list(_existing_evidence_ids(con, bid, version))
        for i, img in enumerate(images_by_bid.get(bid, [])):
            eid = f"{bid}-img{i}"
            ev_rows.append([eid, bid, version, "image",
                            f"画像（{img.source or '供給源不明'}）",
                            json.dumps({"source": img.source, "url": img.url},
                                       ensure_ascii=False)])
            ev_ids.append(eid)

        for attr in ("usage", "storeys"):
            cell = res[attr]
            unres = attr in res["unresolvable"] or cell["value"] is None
            if unres:
                n_unres += 1
            value = None if cell["value"] is None else str(cell["value"])
            cand_rows.append([bid, version, attr, value, None, None,
                              float(cell["confidence"]), "image",
                              cell["reasoning"], ev_ids, bool(unres)])

    if ev_rows:
        con.executemany(
            "INSERT OR REPLACE INTO evidence "
            "(evidence_id, building_id, version, kind, label, detail) "
            "VALUES (?,?,?,?,?,?)", ev_rows)
    con.executemany(
        "INSERT OR REPLACE INTO candidates "
        "(building_id, version, attribute, value, value_min, value_max, "
        " confidence, source, reasoning, evidence_ids, unresolvable) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", cand_rows)
    return {"candidates": len(cand_rows), "evidence": len(ev_rows),
            "unresolvable": n_unres}


def append_log(records: list[dict]) -> None:
    """1棟あたりのトークン・費用を JSONL に追記する（証跡）。"""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "building_id": rec["building_id"],
                "model": rec["model"],
                "prompt_version": rec["prompt_version"],
                "usage": rec["usage"],
                "cost_usd": rec["cost_usd"],
                "cached": rec["cached"],
                "unresolvable": rec["result"]["unresolvable"],
            }, ensure_ascii=False) + "\n")


# --- 本番パイプライン -------------------------------------------------------

def prepare_contexts(con, buildings: list[Building], usage_codes: dict,
                     neigh: NeighborContext, version=VERSION):
    """各棟の context を作る（POI・近傍・仮推定をまとめて1回だけ読み込む）。"""
    from estimate.poi import compute as poi_compute
    try:
        poi_feats = poi_compute(con, version)["feats"]
    except Exception:
        poi_feats = {}
    poi_named = poi_evidence_by_building(con, version)
    fh = learn_floor_heights(con, version)
    ctxs = {}
    for b in buildings:
        ctxs[b.building_id] = build_context(
            b, neigh, poi_feats.get(b.building_id), poi_named.get(b.building_id, []),
            fh, usage_codes)
    return ctxs


def run(limit: int | None, workers: int, model: str | None, dry_run: bool,
        images_provider=no_images, version=VERSION) -> dict:
    con = connect()
    usage_codes = load_usage_codes()
    prompt_text_raw, prompt_version = load_prompt()
    prompt_text = prompt_text_raw.replace("{USAGE_CODES}", format_usage_codes(usage_codes))
    schema = build_schema(usage_codes)
    cfg = InferConfig(model=model or get_model(), prompt_text=prompt_text,
                      prompt_version=prompt_version, schema=schema,
                      thinking=os.environ.get("CLAUDE_THINKING") == "1")

    buildings = load_buildings_rows(con, "is_target", version)
    if limit is not None:
        buildings = buildings[:limit]
    neigh = neighbor_context_known(con, version)
    ctxs = prepare_contexts(con, buildings, usage_codes, neigh, version)
    images_by_bid = {b.building_id: images_provider(b.building_id) for b in buildings}

    print(f"=== Claude 属性推定（model={cfg.model}, prompt={prompt_version}, "
          f"thinking={cfg.thinking}）===")
    print(f"  対象: {len(buildings)} 棟")

    if dry_run:
        for b in buildings[:3]:
            print("\n--- 入力プレビュー ---")
            print(render_context(ctxs[b.building_id], usage_codes))
        print(f"\n（dry-run：API は呼ばなかった。プレビュー上位3棟）")
        con.close()
        return {"targets": len(buildings), "dry_run": True}

    import anthropic
    client = anthropic.Anthropic(max_retries=MAX_API_RETRIES)

    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {
            ex.submit(infer_building, client, cfg, b, ctxs[b.building_id],
                      images_by_bid[b.building_id], usage_codes): b
            for b in buildings
        }
        for fut in as_completed(futs):
            rec = fut.result()
            records.append(rec)
            u = rec["result"]["usage"]["value"]
            s = rec["result"]["storeys"]["value"]
            tag = "cache" if rec["cached"] else f"${rec['cost_usd']}"
            print(f"  {rec['building_id']}: usage={u} storeys={s} "
                  f"unres={rec['result']['unresolvable']} [{tag}]")

    append_log(records)
    stats = write_candidates(con, records, images_by_bid, version)

    live = [r for r in records if not r["cached"]]
    total_cost = sum(r["cost_usd"] or 0 for r in live)
    total_tok = sum((r["usage"].get("input_tokens", 0)
                     + r["usage"].get("output_tokens", 0)) for r in live)
    print("\n=== サマリ ===")
    print(f"  candidates 投入 : {stats['candidates']} 行（source='image'）")
    print(f"  evidence 投入   : {stats['evidence']} 行（kind='image'）")
    print(f"  判断不能       : {stats['unresolvable']} 属性")
    print(f"  API 呼び出し    : {len(live)} 棟（キャッシュ {len(records)-len(live)} 棟）")
    print(f"  トークン合計    : {total_tok}（1棟平均 "
          f"{total_tok/max(1,len(live)):.0f}）")
    print(f"  費用合計        : ${total_cost:.4f}（1棟平均 "
          f"${total_cost/max(1,len(live)):.5f}）")
    con.close()
    return {"targets": len(buildings), **stats, "cost_usd": total_cost}


def main() -> None:
    load_dotenv()   # .env の ANTHROPIC_API_KEY / CLAUDE_MODEL を読む
    p = argparse.ArgumentParser(description="Claude API で不明群の用途・階数を推定する")
    p.add_argument("--limit", type=int, default=20,
                   help="処理する棟数（既定20。--all で全件）")
    p.add_argument("--all", action="store_true", help="不明群すべてを処理（費用注意）")
    p.add_argument("--workers", type=int, default=3, help="並列数（既定3）")
    p.add_argument("--model", default=None, help="CLAUDE_MODEL を上書き")
    p.add_argument("--dry-run", action="store_true",
                   help="API を呼ばず入力プレビューだけ出す")
    args = p.parse_args()
    run(limit=None if args.all else args.limit, workers=args.workers,
        model=args.model, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
