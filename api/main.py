"""まちカルテ API（FastAPI）。

地図UIへ件数・優先度リスト・建物属性・推定候補を返し、人の確認・確定を
受け取って計測（opened_at / confirmed_at）を記録する。件数は毎回 DB から
数える（直書きしない）。確定の書き込みは db.get_con()（read-write）で行う。
"""
from __future__ import annotations

import html
import os
import statistics
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .build_geojson import build_if_missing
from .codelists import load_usage_codes, usage_label_map
from .db import DERIVED_DIR, VERSION, db_lock, get_con
from .priority import TARGETS_SQL, demo_counts
from .schema import ensure_tables

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs")

USAGE_LABELS = usage_label_map()


def usage_label(code) -> str | None:
    if code is None:
        return None
    return USAGE_LABELS.get(str(code), str(code))


def confidence_label(conf, unresolvable: bool = False) -> str:
    """信頼度の数値を画面用の 高・中・低 に畳む（生値は出さない）。"""
    if unresolvable or conf is None:
        return "—"
    if conf >= 0.65:
        return "高"
    if conf >= 0.45:
        return "中"
    return "低"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建物ポリゴン GeoJSON が無ければ生成（read_only 接続。RW を開く前に閉じる）
    build_if_missing()
    # 推定・確定・計測テーブルを用意（冪等）
    with db_lock:
        ensure_tables(get_con())
    yield


app = FastAPI(title="まちカルテ API", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)


# --- 件数（絞り込みの瞬間） -------------------------------------------------

@app.get("/api/stats")
def stats() -> dict:
    with db_lock:
        con = get_con()
        total = con.execute(
            "SELECT count(*) FROM buildings WHERE version=?", [VERSION]
        ).fetchone()[0]
        target = con.execute(
            "SELECT count(*) FROM buildings WHERE version=? AND is_target", [VERSION]
        ).fetchone()[0]
    non_target = total - target
    reduction_pct = round((1 - target / total) * 100, 1) if total else 0.0
    return {"total": total, "target": target, "non_target": non_target,
            "reduction_pct": reduction_pct}


@app.get("/api/demo")
def demo() -> dict:
    """デモ範囲の棟数（高さ20m以上・西新宿周辺）。"""
    with db_lock:
        return demo_counts(get_con(), VERSION)


@app.get("/api/usage_codes")
def usage_codes() -> list[dict]:
    """用途コードの選択肢（Building_usage.xml 由来、461=不明は除く）。"""
    return load_usage_codes()


# --- 優先度順リスト（高さ→新規→面積→画像） --------------------------------

@app.get("/api/targets")
def targets(limit: int = 100) -> list[dict]:
    with db_lock:
        rows = get_con().execute(TARGETS_SQL, [VERSION, VERSION, limit]).fetchall()
    out = []
    for (bid, h, st, u, lon, lat, area, is_new, has_img, score, confirmed) in rows:
        out.append({
            "id": bid, "height_m": h, "storeys_above": st,
            "usage": u, "usage_label": usage_label(u),
            "lon": lon, "lat": lat, "area_m2": area,
            "is_new": bool(is_new), "has_image": bool(has_img),
            "score": round(score, 4), "confirmed": bool(confirmed),
        })
    return out


# --- 1棟の属性・推定候補・根拠・確定状況 ------------------------------------

def _candidates_for(con, bid: str) -> dict:
    """attribute ごとに1件（信頼度が最大）の候補を、画面用の形で返す。"""
    rows = con.execute(
        """
        SELECT attribute, value, value_min, value_max, confidence, source,
               reasoning, evidence_ids, unresolvable
        FROM candidates WHERE version=? AND building_id=?
        ORDER BY attribute, confidence DESC
        """,
        [VERSION, bid],
    ).fetchall()
    # 根拠（evidence）をまとめて引く
    ev = {
        r[0]: {"kind": r[1], "label": r[2]}
        for r in con.execute(
            "SELECT evidence_id, kind, label FROM evidence WHERE version=? AND building_id=?",
            [VERSION, bid],
        ).fetchall()
    }

    best: dict[str, dict] = {}
    for (attr, value, vmin, vmax, conf, source, reasoning, ev_ids, unres) in rows:
        if attr in best:  # 既に信頼度が高い候補を採用済み（ORDER BY で先頭が最大）
            continue
        if attr == "usage":
            disp = usage_label(value)
        elif attr == "storeys":
            disp = f"{value}階" if value is not None else None
        elif attr == "year_built":
            disp = f"{vmin}〜{vmax}年" if vmin is not None else value
        else:
            disp = value
        best[attr] = {
            "attribute": attr,
            "value": value, "value_min": vmin, "value_max": vmax,
            "display": disp,
            "confidence_label": confidence_label(conf, unres),
            "source": source,
            "reasoning": reasoning,
            "unresolvable": bool(unres),
            "evidence": [ev[e] for e in (ev_ids or []) if e in ev],
        }
    return best


@app.get("/api/buildings/{building_id}")
def building(building_id: str) -> dict:
    with db_lock:
        con = get_con()
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
            raise HTTPException(status_code=404, detail="建物が見つかりません")

        (bid, is_target, usage, raw_usage, du, raw_du, st, raw_st, sy, raw_sy,
         h, raw_h, rea, raw_rea, cdate, lod) = row

        candidates = _candidates_for(con, bid)
        confirmations = [
            dict(zip([d[0] for d in con.description], r))
            for r in con.execute(
                "SELECT * FROM confirmations WHERE version=? AND building_id=?",
                [VERSION, bid],
            ).fetchall()
        ]
        is_new = con.execute(
            "SELECT count(*) FROM candidates WHERE version=? AND building_id=? "
            "AND source='version_diff'", [VERSION, bid],
        ).fetchone()[0] > 0

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
    return {"attributes": attributes, "candidates": candidates,
            "confirmations": confirmations, "is_new": is_new,
            "confirmed": len(confirmations) > 0}


# --- 計測：建物を開いた時刻を記録 -------------------------------------------

@app.post("/api/open/{building_id}")
def open_building(building_id: str) -> dict:
    """建物を開いた時刻を opened_at に記録（サーバー側の時計）。"""
    with db_lock:
        con = get_con()
        exists = con.execute(
            "SELECT 1 FROM buildings WHERE version=? AND building_id=?",
            [VERSION, building_id],
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="建物が見つかりません")
        con.execute(
            "INSERT OR REPLACE INTO open_events (building_id, version, opened_at) "
            "VALUES (?, ?, now())",
            [building_id, VERSION],
        )
        opened_at = con.execute(
            "SELECT opened_at FROM open_events WHERE version=? AND building_id=?",
            [VERSION, building_id],
        ).fetchone()[0]
    return {"building_id": building_id, "opened_at": str(opened_at)}


# --- 確認・確定 -------------------------------------------------------------

class ConfirmBody(BaseModel):
    accepted: bool = True            # そのまま確定=True / 修正して確定=False
    usage: str | None = None         # 修正時に選んだ用途コード（未指定なら候補値）
    note: str | None = None
    operator: str | None = None


@app.post("/api/confirm/{building_id}")
def confirm(building_id: str, body: ConfirmBody) -> dict:
    """1棟を確定し、confirmations に書く。opened_at はサーバー保持分を使う。"""
    with db_lock:
        con = get_con()
        cands = _candidates_for(con, building_id)
        if not cands:
            raise HTTPException(status_code=400, detail="推定候補がありません")

        # opened_at はサーバー保持分を使う。無ければ now()（計測は0秒扱い）
        opened_sql = "COALESCE((SELECT opened_at FROM open_events " \
                     "WHERE version=? AND building_id=?), now())"

        rows = []
        # usage：そのまま=候補値、修正=選んだ値。accepted は候補一致か
        # そのまま確定=候補値を採用(accepted)、修正して確定=選んだ値(accepted=False)。
        # 計測の「そのまま／修正」はこの accepted（＝押したボタン）で分ける。
        cu = cands.get("usage")
        if cu and not cu["unresolvable"]:
            if body.accepted:
                rows.append(("usage", cu["value"], True))
            else:
                rows.append(("usage", body.usage or cu["value"], False))
        # storeys：自動で埋める属性。判断不能でなければそのまま確定
        cs = cands.get("storeys")
        if cs and not cs["unresolvable"] and cs["value"] is not None:
            rows.append(("storeys", cs["value"], True))
        # year_built：区間で確定（新規棟のみ）
        cy = cands.get("year_built")
        if cy and not cy["unresolvable"]:
            rows.append(("year_built", f"{cy['value_min']}-{cy['value_max']}", True))

        for attr, val, accepted in rows:
            con.execute(
                f"""
                INSERT OR REPLACE INTO confirmations
                  (building_id, version, attribute, confirmed_value,
                   accepted_candidate, opened_at, confirmed_at, confirmed_by, operator_note)
                VALUES (?, ?, ?, ?, ?, {opened_sql}, now(), ?, ?)
                """,
                [building_id, VERSION, attr, val, accepted,
                 VERSION, building_id, body.operator, body.note],
            )
    return {"building_id": building_id, "confirmed": [r[0] for r in rows],
            "accepted": body.accepted}


# --- 計測の集計（処理済みN棟 / 合計M分 / 中央値S秒） ------------------------

@app.get("/api/metrics")
def metrics() -> dict:
    """確定済みを1棟ごとに集計。平均でなく中央値。そのまま／修正を分ける。"""
    with db_lock:
        rows = get_con().execute(
            """
            SELECT building_id,
                   (epoch_ms(max(confirmed_at)) - epoch_ms(min(opened_at))) / 1000.0 AS sec,
                   bool_and(accepted_candidate) AS all_accepted
            FROM confirmations WHERE version=?
            GROUP BY building_id
            """,
            [VERSION],
        ).fetchall()

    secs_all = [max(0.0, r[1]) for r in rows]
    secs_keep = [max(0.0, r[1]) for r in rows if r[2]]       # そのまま確定
    secs_edit = [max(0.0, r[1]) for r in rows if not r[2]]   # 修正して確定

    def med(xs):
        return round(statistics.median(xs), 1) if xs else None

    return {
        "processed": len(rows),
        "total_minutes": round(sum(secs_all) / 60.0, 2) if secs_all else 0.0,
        "median_sec": med(secs_all),
        "keep": {"count": len(secs_keep), "median_sec": med(secs_keep)},
        "edit": {"count": len(secs_edit), "median_sec": med(secs_edit)},
    }


@app.get("/attribution", response_class=HTMLResponse)
def attribution() -> str:
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
