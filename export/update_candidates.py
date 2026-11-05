"""更新候補リストの出力（設計書「第4段：更新候補の出力」）。

人が確定した推定値（confirmations）だけを、自治体がそのまま使える差分として
CSV / GeoJSON で出す。**未確定・判断不能は出さない**（confirmations にある属性だけが
対象なので構造的に出ない。tests/test_export.py で保証）。

- CSV は UTF-8 BOM 付き（Excel で文字化けしない）。current_value は raw_*（461/9999）の生値。
- 建築年は proposed_value を '2022-2024' 形式にし、value_min / value_max を別列で持つ。
- GeoJSON（EPSG:4326）は同じ内容を建物外形付きで出す。
- city_code は引数（既定 13104）。新宿区以外でも同じ処理が通る（実行は新宿区のみでよい）。
- 公式データへの書き込みは作らない（差分の提案までが責任範囲）。

実行:
    uv run python -m export                      # 新宿区(13104)、data/derived/export/ に出力
    uv run python -m export --city-code 13104 --out-dir data/derived/export
"""
from __future__ import annotations

import csv
import io
import json
import os

import duckdb

from api.codelists import usage_label_map

DEFAULT_DB = os.path.join(os.environ.get("DATA_DIR", "./data"), "machikarte.duckdb")
DEFAULT_OUT = os.path.join(os.environ.get("DATA_DIR", "./data"), "derived", "export")
VERSION = "2025"
CITY_CODE = "13104"

# 内部 attribute 名 → 出力の attribute 名（設計書の列）
ATTR_OUT = {"usage": "usage", "storeys": "storeys_above", "year_built": "year_built"}

CSV_COLUMNS = [
    "building_id", "attribute", "current_value", "current_label",
    "proposed_value", "proposed_label", "source", "confidence", "reasoning",
    "confirmed_by", "confirmed_via", "confirmed_at", "evidence_url",
    "value_min", "value_max",
]

ATTRIBUTION = "出典：国土交通省 Project PLATEAU（新宿区）。近傍推定は PLATEAU 既知棟由来。"
LICENSE_NOTE = ("source が poi / osm_building の行は OpenStreetMap 由来を含み ODbL の対象に"
                "なり得る。Mapillary の画像は含めない（参照名のみ）。公式データへの書き込みではない。")

_USAGE_LABELS = usage_label_map()


def connect(db_path: str = DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path, read_only=True)
    con.execute("LOAD spatial;")
    return con


# --- 行の組み立て -----------------------------------------------------------

def _candidate_map(con, version: str) -> dict:
    """(building_id, attribute) → 最大信頼度の candidate（source/conf/reasoning/区間/evidence）。"""
    rows = con.execute(
        """
        SELECT building_id, attribute, source, confidence, reasoning,
               value_min, value_max, evidence_ids
        FROM candidates WHERE version=?
        ORDER BY building_id, attribute, confidence DESC NULLS LAST
        """,
        [version],
    ).fetchall()
    best: dict[tuple, dict] = {}
    for (bid, attr, source, conf, reason, vmin, vmax, ev_ids) in rows:
        key = (bid, attr)
        if key in best:
            continue
        best[key] = {"source": source, "confidence": conf, "reasoning": reason,
                     "value_min": vmin, "value_max": vmax, "evidence_ids": ev_ids or []}
    return best


def _evidence_map(con, version: str) -> dict:
    out: dict[str, str] = {}
    for eid, label in con.execute(
        "SELECT evidence_id, label FROM evidence WHERE version=?", [version]
    ).fetchall():
        out[eid] = label
    return out


def _proposed_label(attr: str, value, vmin, vmax) -> str:
    if attr == "usage":
        return _USAGE_LABELS.get(str(value), str(value) if value is not None else "")
    if attr == "storeys":
        return f"{value}階" if value is not None else ""
    if attr == "year_built":
        return f"{vmin}〜{vmax}年" if vmin is not None else (value or "")
    return str(value) if value is not None else ""


def fetch_rows(con, city_code: str = CITY_CODE, version: str = VERSION) -> list[dict]:
    """確定済み（confirmations）だけを、設計書の列の dict リストで返す。"""
    conf_rows = con.execute(
        """
        SELECT c.building_id, c.attribute, c.confirmed_value, c.accepted_candidate,
               c.confirmed_at, c.confirmed_by, b.raw_usage, b.raw_storeys_above,
               COALESCE(c.source, 'desk') AS confirmed_via
        FROM confirmations c
        JOIN buildings b ON b.building_id=c.building_id AND b.version=c.version
        WHERE c.version=? AND b.city_code=?
        ORDER BY c.building_id, c.attribute
        """,
        [version, city_code],
    ).fetchall()
    cand = _candidate_map(con, version)
    ev = _evidence_map(con, version)

    out = []
    for (bid, attr, cval, accepted, cat, cby, raw_usage, raw_storeys,
         confirmed_via) in conf_rows:
        c = cand.get((bid, attr), {})
        vmin, vmax = c.get("value_min"), c.get("value_max")

        if attr == "usage":
            current_value, current_label = raw_usage, "不明"
        elif attr == "storeys":
            current_value, current_label = raw_storeys, "不明"
        else:  # year_built
            current_value, current_label = "", "未記録"

        # 人が修正した行は候補の値ではないので source=manual・confidence/reasoning 空
        if accepted:
            source = c.get("source", "")
            confidence = c.get("confidence")
            reasoning = c.get("reasoning", "")
        else:
            source, confidence, reasoning = "manual", None, ""

        ev_labels = ";".join(ev[e] for e in c.get("evidence_ids", []) if e in ev)

        out.append({
            "building_id": bid,
            "attribute": ATTR_OUT.get(attr, attr),
            "current_value": current_value if current_value is not None else "",
            "current_label": current_label,
            "proposed_value": cval if cval is not None else "",
            "proposed_label": _proposed_label(attr, cval, vmin, vmax),
            "source": source or "",
            "confidence": "" if confidence is None else round(float(confidence), 2),
            "reasoning": reasoning or "",
            "confirmed_by": cby or "調査担当者",
            "confirmed_via": confirmed_via or "desk",
            "confirmed_at": str(cat) if cat is not None else "",
            "evidence_url": ev_labels,
            "value_min": "" if vmin is None else vmin,
            "value_max": "" if vmax is None else vmax,
        })
    return out


# --- シリアライズ -----------------------------------------------------------

def csv_bytes(rows: list[dict]) -> bytes:
    """UTF-8 BOM 付き CSV のバイト列（Excel 対応）。"""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8-sig")


def geojson_obj(con, rows: list[dict], version: str = VERSION) -> dict:
    """CSV と同じ行を建物外形付き Feature にした FeatureCollection。"""
    ids = sorted({r["building_id"] for r in rows})
    geoms: dict[str, dict] = {}
    if ids:
        placeholders = ",".join("?" * len(ids))
        for bid, gj in con.execute(
            f"SELECT building_id, ST_AsGeoJSON(geom_4326) FROM buildings "
            f"WHERE version=? AND building_id IN ({placeholders})",
            [version, *ids],
        ).fetchall():
            if gj:
                geoms[bid] = json.loads(gj)
    feats = [
        {"type": "Feature", "properties": r, "geometry": geoms.get(r["building_id"])}
        for r in rows
    ]
    return {
        "type": "FeatureCollection",
        "attribution": ATTRIBUTION,
        "license_note": LICENSE_NOTE,
        "features": feats,
    }


def readme_text() -> str:
    """出力同梱の README（export/README.md のテンプレート部分を使う）。"""
    path = os.path.join(os.path.dirname(__file__), "README.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    start = text.find("<!-- ここから出力同梱用テンプレート -->")
    end = text.find("<!-- ここまで出力同梱用テンプレート -->")
    if start != -1 and end != -1:
        return text[start + len("<!-- ここから出力同梱用テンプレート -->"):end].strip() + "\n"
    return text


# --- CLI --------------------------------------------------------------------

def run(db_path: str = DEFAULT_DB, city_code: str = CITY_CODE,
        version: str = VERSION, out_dir: str = DEFAULT_OUT) -> dict:
    con = connect(db_path)
    rows = fetch_rows(con, city_code, version)
    os.makedirs(out_dir, exist_ok=True)

    csv_path = os.path.join(out_dir, f"update_candidates_{city_code}.csv")
    geojson_path = os.path.join(out_dir, f"update_candidates_{city_code}.geojson")
    readme_path = os.path.join(out_dir, "README.md")

    with open(csv_path, "wb") as f:
        f.write(csv_bytes(rows))
    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(geojson_obj(con, rows, version), f, ensure_ascii=False, indent=1)
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_text())
    con.close()

    buildings = len({r["building_id"] for r in rows})
    print("=== 更新候補リストの出力 ===")
    print(f"  city_code={city_code} version={version}")
    print(f"  確定済み: {buildings} 棟 / {len(rows)} 行（属性）")
    print(f"  CSV    : {csv_path}（UTF-8 BOM）")
    print(f"  GeoJSON: {geojson_path}（EPSG:4326）")
    print(f"  README : {readme_path}")
    return {"buildings": buildings, "rows": len(rows),
            "csv": csv_path, "geojson": geojson_path, "readme": readme_path}
