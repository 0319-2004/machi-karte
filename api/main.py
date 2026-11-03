"""まちカルテ API（FastAPI）。

地図UIへ件数・優先度リスト・建物属性を返す。件数は毎回 DB から数える（直書きしない）。
確認・確定の書き込みは Phase 4 で追加する（今は読み取りのみ）。
"""
from __future__ import annotations

import html
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .build_geojson import build_if_missing
from .db import DERIVED_DIR, VERSION, connect, table_exists

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs")

# PLATEAU Building_usage コードの日本語ラベル（codelist は git 管理外のため内蔵）
USAGE_LABELS = {
    "401": "業務施設", "402": "商業施設", "403": "宿泊施設", "404": "商業系複合施設",
    "411": "住宅", "412": "共同住宅", "413": "店舗等併用住宅", "414": "店舗等併用共同住宅",
    "415": "作業所併用住宅", "421": "官公庁施設", "422": "文教厚生施設",
    "431": "運輸倉庫施設", "441": "工場", "451": "農林漁業用施設", "452": "供給処理施設",
    "453": "防衛施設", "454": "その他", "461": "不明",
}


def usage_label(code) -> str | None:
    if code is None:
        return None
    return USAGE_LABELS.get(str(code), str(code))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建物ポリゴン GeoJSON が無ければ生成（初回起動）
    build_if_missing()
    yield


app = FastAPI(title="まちカルテ API", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.get("/api/stats")
def stats() -> dict:
    con = connect()
    total = con.execute(
        "SELECT count(*) FROM buildings WHERE version=?", [VERSION]
    ).fetchone()[0]
    target = con.execute(
        "SELECT count(*) FROM buildings WHERE version=? AND is_target", [VERSION]
    ).fetchone()[0]
    con.close()
    non_target = total - target
    reduction_pct = round((1 - target / total) * 100, 1) if total else 0.0
    return {
        "total": total,
        "target": target,
        "non_target": non_target,
        "reduction_pct": reduction_pct,
    }


@app.get("/api/targets")
def targets(limit: int = 100) -> list[dict]:
    # 優先度順＝今は height_m 降順の仮置き（Phase 5 でスコアに差し替える）
    con = connect()
    rows = con.execute(
        """
        SELECT building_id, height_m, storeys_above, usage,
               ST_X(ST_Centroid(geom_4326)), ST_Y(ST_Centroid(geom_4326))
        FROM buildings
        WHERE version=? AND is_target AND geom_4326 IS NOT NULL
        ORDER BY height_m DESC NULLS LAST
        LIMIT ?
        """,
        [VERSION, limit],
    ).fetchall()
    con.close()
    return [
        {
            "id": bid, "height_m": h, "storeys_above": st,
            "usage": u, "usage_label": usage_label(u),
            "lon": lon, "lat": lat,
        }
        for bid, h, st, u, lon, lat in rows
    ]


@app.get("/api/buildings/{building_id}")
def building(building_id: str) -> dict:
    con = connect()
    row = con.execute(
        """
        SELECT building_id, is_target,
               usage, raw_usage, detailed_usage, raw_detailed_usage,
               storeys_above, raw_storeys_above, survey_year, raw_survey_year,
               height_m, raw_height, roof_edge_area, raw_roof_edge_area,
               creation_date, lod_type
        FROM buildings WHERE version=? AND building_id=?
        """,
        [VERSION, building_id],
    ).fetchone()
    if row is None:
        con.close()
        raise HTTPException(status_code=404, detail="建物が見つかりません")

    (bid, is_target, usage, raw_usage, du, raw_du, st, raw_st, sy, raw_sy,
     h, raw_h, rea, raw_rea, cdate, lod) = row

    attributes = {
        "id": bid,
        "is_target": bool(is_target),
        "usage": {"value": usage, "label": usage_label(usage), "raw": raw_usage},
        "detailed_usage": {"value": du, "raw": raw_du},
        "storeys_above": {"value": st, "raw": raw_st},
        "survey_year": {"value": sy, "raw": raw_sy},
        "height_m": {"value": h, "raw": raw_h},
        "roof_edge_area": {"value": rea, "raw": raw_rea},
        "creation_date": cdate,
        "lod_type": lod,
    }

    # candidates / confirmations は Phase 4–5 でテーブルができたら中身を返す
    candidates: list[dict] = []
    confirmations: list[dict] = []
    if table_exists(con, "candidates"):
        candidates = [
            dict(zip([d[0] for d in con.description], r))
            for r in con.execute(
                "SELECT * FROM candidates WHERE building_id=?", [bid]
            ).fetchall()
        ]
    if table_exists(con, "confirmations"):
        confirmations = [
            dict(zip([d[0] for d in con.description], r))
            for r in con.execute(
                "SELECT * FROM confirmations WHERE building_id=?", [bid]
            ).fetchall()
        ]
    con.close()
    return {"attributes": attributes, "candidates": candidates,
            "confirmations": confirmations}


@app.get("/attribution", response_class=HTMLResponse)
def attribution() -> str:
    # docs/attribution.md をそのまま読んで簡易表示（依存を増やさない）
    path = os.path.join(DOCS_DIR, "attribution.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = "attribution.md が見つかりません。"
    return (
        "<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>帰属表示 — まちカルテ</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;"
        "padding:0 16px;line-height:1.7;color:#222}pre{white-space:pre-wrap;"
        "background:#f6f6f6;padding:16px;border-radius:8px}a{color:#1565c0}</style>"
        "</head><body><p><a href='/'>← 地図に戻る</a></p>"
        f"<pre>{html.escape(text)}</pre></body></html>"
    )


# /data/buildings.geojson を配信（ファイルが無ければ startup で生成済み）
if os.path.isdir(DERIVED_DIR):
    app.mount("/data", StaticFiles(directory=DERIVED_DIR), name="data")

# web/ を / にマウント（index.html を返す）。最後に置く（API ルートを優先）。
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
