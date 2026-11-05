"""まちカルテ API（FastAPI）。

地図UIへ件数・優先度リスト・建物属性・推定候補を返し、人の確認・確定を
受け取って計測（opened_at / confirmed_at）を記録する。件数は毎回 DB から
数える（直書きしない）。確定の書き込みは db.get_con()（read-write）で行う。
"""
from __future__ import annotations

import base64
import binascii
import html
import json
import os
import re
import statistics
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from export.report import build_markdown, compute_facts
from export.report import _append_log as report_append_log
from export.update_candidates import csv_bytes, fetch_rows, geojson_obj
from .build_geojson import build_if_missing
from .codelists import load_usage_codes, usage_label_map
from .db import DERIVED_DIR, VERSION, db_lock, get_con
from .onsite import aggregate_unresolved, load_guidance
from .priority import TARGETS_SQL, demo_counts
from .schema import ensure_tables

load_dotenv()   # .env の ANTHROPIC_API_KEY / CLAUDE_MODEL（レポートの Claude 生成で使う）

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs")
DATA_DIR = os.environ.get("DATA_DIR", "./data")
SURVEY_DIR = os.path.join(DATA_DIR, "survey")   # 現地写真の保存先（git 管理外）

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


@app.get("/api/unresolved")
def unresolved() -> dict:
    """判断不能（現地確認が必要）の棟を属性×理由で集約し、Claude 要約を添える。

    要約は事前生成（make onsite）の JSON を読むだけ。未生成なら guidance=None。
    """
    with db_lock:
        agg = aggregate_unresolved(get_con(), VERSION)
    guidance = load_guidance()
    for g in agg["groups"]:
        gd = guidance.get(g["key"])
        g["guidance"] = gd["summary"] if gd else None
    return agg


@app.get("/api/usage_codes")
def usage_codes() -> list[dict]:
    """用途コードの選択肢（Building_usage.xml 由来、461=不明は除く）。"""
    return load_usage_codes()


class VoiceBody(BaseModel):
    text: str                         # 端末の音声認識が返した「認識後テキスト」だけ
    building_id: str | None = None    # ログ用（音声は扱わない）
    status: dict | None = None        # {usage:{filled,value,est}, storeys:{...}} 現在の埋まり具合
    pending: str | None = None        # 直前の質問が尋ねた項目（usage/storeys）
    givenup: list[str] | None = None  # 既に「分からない」と答えた項目（もう聞かない）
    asked: int = 0                    # これまでに尋ねた質問数（3で打ち切り）


@app.post("/api/voice_parse")
def voice_parse(body: VoiceBody) -> dict:
    """音声入力（試験的）：認識後テキストを Claude で振り分け、足りなければ質問を1つ返す。

    受け取るのは認識後の **テキストだけ**（音声は扱わない・保存しない）。読み取れない
    項目は null（推測で埋めない）。埋まっていない項目があれば、その建物の状況に合わせた
    質問を1つ返す（全部埋まれば null）。質問は1度に1つ、3往復で打ち切る。入力ハッシュで
    キャッシュしログに残す。API 未設定など失敗時は 503 を返し、UI は手入力へ戻れる。
    """
    from .voice import parse_voice

    try:
        return parse_voice(
            body.text, load_usage_codes(),
            status=body.status, pending=body.pending,
            givenup=body.givenup, asked=body.asked, building_id=body.building_id)
    except Exception as e:  # noqa: BLE001 UI は手入力へフォールバックする
        raise HTTPException(status_code=503, detail=f"音声の振り分けに失敗: {e}")


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
        photos = _photos_for(con, bid)

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
            "confirmed": len(confirmations) > 0, "photos": photos}


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
                   accepted_candidate, source, opened_at, confirmed_at,
                   confirmed_by, operator_note)
                VALUES (?, ?, ?, ?, ?, 'desk', {opened_sql}, now(), ?, ?)
                """,
                [building_id, VERSION, attr, val, accepted,
                 VERSION, building_id, body.operator, body.note],
            )
    return {"building_id": building_id, "confirmed": [r[0] for r in rows],
            "accepted": body.accepted}


# --- 現地調査（スマホ）：入力と写真を受け取り source='onsite' で確定 --------

class SurveyBody(BaseModel):
    building_id: str                  # 対象の建物ID
    usage: str | None = None          # 現地で選んだ用途コード
    storeys: int | None = None        # 現地で数えた地上階数
    note: str | None = None           # 自由記述メモ
    operator: str | None = None       # 調査担当者
    photo: str | None = None          # data URL（base64, 端末側で縮小済み）。無くてよい


def _save_survey_photo(con, building_id: str, photo: str,
                       operator: str | None = None) -> dict | None:
    """data URL の写真を data/survey/ に保存し、evidence(kind='photo') に紐づける。

    撮影日は evidence.created_at（サーバー側の時計）を正本にし、撮影者（operator）は
    detail に残す（写真のみの登録で confirmations が無くても撮影者を辿れるように）。
    """
    m = re.match(r"^data:(image/(?:jpeg|png|webp));base64,(.+)$", photo, re.DOTALL)
    if not m:
        return None
    media_type, b64 = m.group(1), m.group(2)
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[media_type]
    try:
        raw = base64.b64decode(b64, validate=True)
    except (ValueError, binascii.Error):
        return None

    os.makedirs(SURVEY_DIR, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", building_id)
    seq = len([f for f in os.listdir(SURVEY_DIR) if f.startswith(safe + "_")]) + 1
    fname = f"{safe}_{seq}.{ext}"
    with open(os.path.join(SURVEY_DIR, fname), "wb") as f:
        f.write(raw)

    eid = f"{building_id}-onsite-photo{seq}"
    url = f"/survey/{fname}"
    con.execute(
        "INSERT OR REPLACE INTO evidence "
        "(evidence_id, building_id, version, kind, label, detail) "
        "VALUES (?, ?, ?, 'photo', ?, ?)",
        [eid, building_id, VERSION, "現地写真", json.dumps(
            {"source": "onsite", "filename": fname, "url": url,
             "operator": (operator or "").strip() or None}, ensure_ascii=False)],
    )
    return {"evidence_id": eid, "url": url}


def _photos_for(con, bid: str) -> list[dict]:
    """建物の現地写真を撮影順（created_at 昇順）で返す。画面の画像枠が読む。"""
    rows = con.execute(
        "SELECT detail, created_at FROM evidence "
        "WHERE version=? AND building_id=? AND kind='photo' ORDER BY created_at",
        [VERSION, bid],
    ).fetchall()
    out = []
    for detail, created in rows:
        try:
            d = json.loads(detail) if detail else {}
        except (TypeError, ValueError):
            d = {}
        if not d.get("url"):
            continue
        out.append({
            "url": d["url"],
            "operator": d.get("operator"),
            "captured_on": str(created)[:10] if created is not None else None,
            "source": d.get("source", "onsite"),
        })
    return out


@app.post("/api/survey")
def survey(body: SurveyBody) -> dict:
    """現地調査の入力（用途・階数・写真・メモ）を受け取り、source='onsite' で確定する。

    - 写真は data/survey/（git 管理外）に保存し evidence に紐づける。無くても受け付ける。
    - usage / storeys は与えられた分だけ confirmations に書く。accepted_candidate は
      推定値と一致すれば TRUE（そのまま）、違えば FALSE（現地で修正）。
    - opened_at はサーバー保持分があれば使う（無ければ now()）。計測は desk と分ける。
    """
    building_id = body.building_id
    with db_lock:
        con = get_con()
        if not con.execute(
            "SELECT 1 FROM buildings WHERE version=? AND building_id=?",
            [VERSION, building_id],
        ).fetchone():
            raise HTTPException(status_code=404, detail="建物が見つかりません")

        cands = _candidates_for(con, building_id)
        photo_info = None
        if body.photo:
            photo_info = _save_survey_photo(con, building_id, body.photo, body.operator)

        opened_sql = ("COALESCE((SELECT opened_at FROM open_events "
                      "WHERE version=? AND building_id=?), now())")

        written = []
        pairs = [("usage", None if body.usage in (None, "") else str(body.usage)),
                 ("storeys", None if body.storeys is None else str(body.storeys))]
        for attr, val in pairs:
            if val is None:
                continue
            cand = cands.get(attr)
            est = str(cand["value"]) if (cand and cand["value"] is not None) else None
            accepted = (est is not None and val == est)
            con.execute(
                f"""
                INSERT OR REPLACE INTO confirmations
                  (building_id, version, attribute, confirmed_value,
                   accepted_candidate, source, opened_at, confirmed_at,
                   confirmed_by, operator_note)
                VALUES (?, ?, ?, ?, ?, 'onsite', {opened_sql}, now(), ?, ?)
                """,
                [building_id, VERSION, attr, val, accepted,
                 VERSION, building_id, body.operator, body.note],
            )
            written.append(attr)

    if not written and not photo_info:
        raise HTTPException(status_code=400,
                            detail="用途・階数・写真のいずれも入力がありません")
    return {"building_id": building_id, "confirmed": written,
            "photo": photo_info, "source": "onsite"}


@app.get("/api/confirmed")
def confirmed() -> dict:
    """確定済みの建物IDを source 別（desk / onsite）で返す。地図の色分けに使う。"""
    with db_lock:
        rows = get_con().execute(
            """
            SELECT building_id, bool_or(source='onsite') AS any_onsite
            FROM confirmations WHERE version=?
            GROUP BY building_id
            """,
            [VERSION],
        ).fetchall()
    onsite = [r[0] for r in rows if r[1]]
    desk = [r[0] for r in rows if not r[1]]
    return {"onsite": onsite, "desk": desk}


@app.get("/api/photos")
def photos() -> list[dict]:
    """現地写真がある棟を、重心座標・枚数・最新の撮影日つきで返す（地図のマーカー用）。"""
    with db_lock:
        rows = get_con().execute(
            """
            SELECT e.building_id, count(*) AS n, max(e.created_at) AS latest,
                   ST_X(ST_Centroid(b.geom_4326)) AS lon,
                   ST_Y(ST_Centroid(b.geom_4326)) AS lat
            FROM evidence e
            JOIN buildings b ON b.building_id=e.building_id AND b.version=e.version
            WHERE e.version=? AND e.kind='photo'
            GROUP BY e.building_id, b.geom_4326
            """,
            [VERSION],
        ).fetchall()
    return [{"building_id": bid, "count": n, "latest": str(latest)[:10],
             "lon": lon, "lat": lat} for (bid, n, latest, lon, lat) in rows]


# --- 計測の集計（処理済みN棟 / 合計M分 / 中央値S秒） ------------------------

@app.get("/api/metrics")
def metrics() -> dict:
    """確定済みを1棟ごとに集計。平均でなく中央値。そのまま／修正を分ける。

    処理時間の指標は机上（desk）のトリアージが対象。現地調査（onsite）は
    opened_at≒confirmed_at で0秒寄りになり中央値を歪めるため、ここでは除く。
    """
    with db_lock:
        rows = get_con().execute(
            """
            SELECT building_id,
                   (epoch_ms(max(confirmed_at)) - epoch_ms(min(opened_at))) / 1000.0 AS sec,
                   bool_and(accepted_candidate) AS all_accepted
            FROM confirmations WHERE version=? AND source='desk'
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


# --- 更新候補の出力（確定済みだけ。未確定・判断不能は出さない） ------------

@app.get("/api/export/summary")
def export_summary(city_code: str = "13104") -> dict:
    with db_lock:
        rows = fetch_rows(get_con(), city_code, VERSION)
    return {"buildings": len({r["building_id"] for r in rows}), "rows": len(rows)}


@app.get("/api/export.csv")
def export_csv(city_code: str = "13104") -> Response:
    with db_lock:
        data = csv_bytes(fetch_rows(get_con(), city_code, VERSION))
    return Response(
        content=data, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="update_candidates_{city_code}.csv"'},
    )


@app.get("/api/export.geojson")
def export_geojson(city_code: str = "13104") -> Response:
    with db_lock:
        con = get_con()
        obj = geojson_obj(con, fetch_rows(con, city_code, VERSION), VERSION)
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return Response(
        content=data, media_type="application/geo+json",
        headers={"Content-Disposition":
                 f'attachment; filename="update_candidates_{city_code}.geojson"'},
    )


@app.post("/api/report")
def report_md(city_code: str = "13104") -> Response:
    """調査レポート（Markdown）をサーバー側で生成して返す（CLI 不要・画面から生成）。

    数字の集計（compute_facts）は db_lock 内で行い、時間のかかる Claude 生成
    （build_markdown）はロックを解いてから実行する（他の操作を止めない）。
    確定0件なら Claude を呼ばず『確定された棟がありません』を返す。CSV/GeoJSON と
    同じ場所にも保存する。
    """
    with db_lock:
        facts = compute_facts(get_con(), city_code, VERSION)
    md, meta = build_markdown(facts, city_code)   # Claude はロック外

    out_dir = os.path.join(DERIVED_DIR, "export")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"report_{city_code}.md"), "w", encoding="utf-8") as f:
        f.write(md)
    report_append_log(facts, meta.get("model") or "(skipped)", meta)

    return Response(
        content=md.encode("utf-8"), media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="report_{city_code}.md"'},
    )


def _render_markdown(md: str) -> str:
    """attribution.md 用の最小 Markdown → HTML（見出し・表・太字・リンク・リスト）。"""
    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                   r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return s

    html_parts: list[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            html_parts.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            html_parts.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("|"):  # 表ブロック
            tbl = []
            while i < len(lines) and lines[i].startswith("|"):
                tbl.append(lines[i])
                i += 1
            cells = [[c.strip() for c in r.strip("|").split("|")] for r in tbl]
            body = [r for r in cells if not all(set(c) <= set("-: ") for c in r)]
            out = ["<table>"]
            for n, r in enumerate(body):
                tag = "th" if n == 0 else "td"
                out.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in r) + "</tr>")
            out.append("</table>")
            html_parts.append("".join(out))
            continue
        elif line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{inline(lines[i][2:])}</li>")
                i += 1
            html_parts.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif line.strip():
            html_parts.append(f"<p>{inline(line)}</p>")
        i += 1
    return "\n".join(html_parts)


@app.get("/attribution", response_class=HTMLResponse)
def attribution() -> str:
    path = os.path.join(DOCS_DIR, "attribution.md")
    try:
        with open(path, encoding="utf-8") as f:
            body = _render_markdown(f.read())
    except FileNotFoundError:
        body = "<p>attribution.md が見つかりません。</p>"
    return (
        "<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>帰属表示 — まちカルテ</title>"
        "<style>body{font-family:system-ui,'Hiragino Sans',sans-serif;max-width:820px;"
        "margin:40px auto;padding:0 20px;line-height:1.75;color:#263238}"
        "h1{font-size:24px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid #eee;"
        "padding-bottom:4px}table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}"
        "th,td{border:1px solid #e0e0e0;padding:7px 10px;text-align:left;vertical-align:top}"
        "th{background:#f5f5f5}code{background:#f0f0f0;padding:1px 5px;border-radius:4px;font-size:13px}"
        "a{color:#1565c0}.back{display:inline-block;margin-bottom:8px}"
        ".dl{margin:18px 0;padding:12px 16px;background:#fff8e1;border-radius:8px;font-size:14px}</style>"
        "</head><body><a class='back' href='/'>← 地図に戻る</a>"
        f"{body}"
        "<div class='dl'>更新候補リストのダウンロード："
        "<a href='/api/export.csv'>CSV</a> / <a href='/api/export.geojson'>GeoJSON</a>"
        "（人が確定した属性のみ。未確定・判断不能は含みません）</div>"
        "</body></html>"
    )


# /data/buildings.geojson を配信（ファイルが無ければ startup で生成済み）
if os.path.isdir(DERIVED_DIR):
    app.mount("/data", StaticFiles(directory=DERIVED_DIR), name="data")

# 現地写真を /survey で配信（保存先が無ければ作ってからマウント）
os.makedirs(SURVEY_DIR, exist_ok=True)
app.mount("/survey", StaticFiles(directory=SURVEY_DIR), name="survey")

# web/ を / にマウント（index.html を返す）。最後に置く（API ルートを優先）。
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
