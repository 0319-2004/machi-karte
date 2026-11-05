"""調査レポートの生成（自治体の担当者が上司に出せる報告書）。

確定済み（confirmations）の棟を対象に、Markdown の報告書を1枚つくる。CSV / GeoJSON と
同じ場所（data/derived/export/）に置く。

**数字と文章の責任を分ける。**
- 数字はすべて SQL 集計（export.update_candidates.fetch_rows と api.onsite.aggregate_unresolved）
  から Python が決め、表として確定的に描く。Claude には数字を作らせない。
- Claude は「解釈と文章化」だけ担当する（要約・所見・むすび）。構造化出力で文章欄だけ返させ、
  本文の数値表は Python が差し込む。だから Claude が数字を取り違えても表の数字は正しいまま。
- API は **1回の生成で1回だけ** 呼ぶ（リトライもしない＝費用と約束のため）。入力ハッシュで
  キャッシュし、トークン・費用を data/derived/report_log.jsonl に残す。

モデルは環境変数 CLAUDE_MODEL（estimate.claude_infer.get_model を共用）。

実行:
    uv run python -m export.report                 # 新宿区(13104)、Claude 要約つきで生成
    uv run python -m export.report --dry-run        # API を呼ばず、数字（facts）だけ確認
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter

from dotenv import load_dotenv

from api.codelists import usage_label_map
from api.onsite import aggregate_unresolved
from export.update_candidates import (ATTRIBUTION, CITY_CODE, DEFAULT_OUT,
                                      VERSION, connect, fetch_rows)

PROMPT_VERSION = "report_v1"
CACHE_DIR = "data/cache/report"
LOG_PATH = os.path.join(os.environ.get("DATA_DIR", "./data"), "derived",
                        "report_log.jsonl")
MAX_OUTPUT_TOKENS = 2048

# 出力の attribute 名 → 日本語の見出し
ATTR_LABEL = {"usage": "用途（bldg:usage）",
              "storeys_above": "地上階数（bldg:storeysAboveGround）",
              "year_built": "建築年（uro:yearOfConstruction）"}

# 判断不能の集約（api.onsite）が返す内部 attribute 名 → 日本語
ONSITE_ATTR_LABEL = {"usage": "用途", "storeys": "階数",
                     "year_built": "建築年", "detailed_usage": "詳細用途"}

# 推定の由来（source）→ 日本語の説明
SOURCE_LABEL = {
    "poi": "POI 結合（OSM / Overture）", "osm_building": "OSM 建物タグ",
    "image": "Claude 多モーダル推定", "neighbor": "近傍の既知棟",
    "height": "高さ÷階高", "version_diff": "版間差分（新築検出）",
    "manual": "担当者が修正して確定",
}

# 帰属表示（CLAUDE.md・docs/attribution.md とそろえる）。レポートに常に全量を載せる。
ATTRIBUTION_TABLE = [
    ("PLATEAU", "出典明示（国土交通省 Project PLATEAU）"),
    ("OpenStreetMap", "© OpenStreetMap contributors（ODbL）"),
    ("Overture Maps", "CDLA Permissive 2.0。Foursquare 由来の項目は NOTICE を添える"),
    ("Mapillary", "ロゴとリンクを表示。画像ごとに「by *username*, CC BY-SA」"),
    ("東京都デジタルツイン", "CC BY 4.0"),
    ("国土地理院", "出典明示（地理院タイル）"),
]

GUIDANCE_SYSTEM = (
    "あなたは自治体の建物属性調査を補助する担当者です。以下に渡す『確定済みの数値』をもとに、"
    "上司に提出できる調査レポートの文章を日本語で書いてください。"
    "守ること：(1) 渡された数値だけを使い、新しい数値を作らない・推測で増やさない。"
    "数値そのものは別途こちらが表で載せるので、文章では主要な数字に軽く触れる程度でよい。"
    "(2) 事実と評価を分け、できたこと／まだ確認が要ることを率直に書く。"
    "(3) 現地でも埋まらない区分は『別データが必要』と正直に述べる。"
    "(4) 誇張しない。断定できないことは断定しない。丁寧だが簡潔な日本語。"
)

# 構造化出力：文章欄だけを返させる（数値表は Python が差し込む）
PROSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string",
                    "description": "レポート冒頭の要約。3〜5文。何を対象に、何棟を確認・確定し、"
                                   "どこまで言えるか。"},
        "confirmed_commentary": {"type": "string",
                                 "description": "確定結果の所見。1〜3文。属性の内訳や推定の由来から"
                                                "読み取れることを述べる。"},
        "onsite_commentary": {"type": "string",
                              "description": "現地確認が必要な棟についての所見。1〜3文。主要な理由と、"
                                             "現地で埋まるもの／別データが要るものの区別に触れる。"},
        "closing": {"type": "string",
                    "description": "むすび。1〜2文。この更新候補が公式データの上書きではなく差分提案で"
                                   "ある点を含め、次に何をすべきかを簡潔に。"},
    },
    "required": ["summary", "confirmed_commentary", "onsite_commentary", "closing"],
    "additionalProperties": False,
}


# --- 数字（SQL 集計。Claude には作らせない） --------------------------------

def compute_facts(con, city_code: str = CITY_CODE, version: str = VERSION) -> dict:
    """確定済みの棟から、レポートに出す数値を確定的に集計する。"""
    rows = fetch_rows(con, city_code, version)
    usage_labels = usage_label_map()

    buildings = sorted({r["building_id"] for r in rows})

    by_attr: dict[str, dict] = {}
    for r in rows:
        d = by_attr.setdefault(r["attribute"], {"count": 0, "accepted": 0, "corrected": 0})
        d["count"] += 1
        if r["source"] == "manual":
            d["corrected"] += 1
        else:
            d["accepted"] += 1

    sources = Counter(r["source"] for r in rows if r["source"])
    usage_dist = Counter(
        (r["proposed_label"] or r["proposed_value"] or "（未分類）")
        for r in rows if r["attribute"] == "usage")

    times = sorted(str(r["confirmed_at"]) for r in rows if r["confirmed_at"])
    survey_from = times[0][:10] if times else None
    survey_to = times[-1][:10] if times else None

    # 建築年は区間で確定している。確定した区間の最小〜最大を参考に載せる。
    year_mins = [r["value_min"] for r in rows
                 if r["attribute"] == "year_built" and r["value_min"] != ""]
    year_maxs = [r["value_max"] for r in rows
                 if r["attribute"] == "year_built" and r["value_max"] != ""]
    year_span = None
    if year_mins and year_maxs:
        year_span = {"min": min(int(y) for y in year_mins),
                     "max": max(int(y) for y in year_maxs)}

    onsite = aggregate_unresolved(con, version)
    onsite_groups = [
        {"attribute": g["attribute"], "reason": g["reason"], "count": g["count"],
         "bands": g["bands"], "areas": g["areas"]}
        for g in onsite["groups"]
    ]

    # 現地調査（source='onsite'）で確定した棟（机上 desk と分けて数える）
    field_rows = con.execute(
        """
        SELECT c.attribute, count(*), count(DISTINCT c.building_id)
        FROM confirmations c
        JOIN buildings b ON b.building_id=c.building_id AND b.version=c.version
        WHERE c.version=? AND b.city_code=? AND c.source='onsite'
        GROUP BY c.attribute
        """,
        [version, city_code],
    ).fetchall()
    field_buildings = con.execute(
        """
        SELECT count(DISTINCT c.building_id)
        FROM confirmations c
        JOIN buildings b ON b.building_id=c.building_id AND b.version=c.version
        WHERE c.version=? AND b.city_code=? AND c.source='onsite'
        """,
        [version, city_code],
    ).fetchone()[0]
    field_photos = con.execute(
        """
        SELECT count(*)
        FROM evidence e
        JOIN buildings b ON b.building_id=e.building_id AND b.version=e.version
        WHERE e.version=? AND b.city_code=? AND e.kind='photo'
        """,
        [version, city_code],
    ).fetchone()[0]
    field_survey = {
        "buildings": field_buildings,
        "rows": sum(r[1] for r in field_rows),
        "photos": field_photos,
        "by_attribute": {ONSITE_ATTR_LABEL.get(a, a): n for a, n, _ in field_rows},
    }

    return {
        "city_code": city_code,
        "version": version,
        "survey_from": survey_from,
        "survey_to": survey_to,
        "confirmed_buildings": len(buildings),
        "confirmed_rows": len(rows),
        "by_attribute": {ATTR_LABEL.get(a, a): v for a, v in by_attr.items()},
        "source_breakdown": {SOURCE_LABEL.get(s, s): n for s, n in sources.most_common()},
        "usage_breakdown": dict(usage_dist.most_common()),
        "year_built_span": year_span,
        "unknown_total": onsite["total_targets"],
        "onsite_needed_buildings": onsite["unresolved_buildings"],
        "onsite_groups": onsite_groups,
        "field_survey": field_survey,
    }


# --- Claude 要約（1回だけ。キャッシュ＋費用ログ） --------------------------

def _facts_for_prompt(facts: dict) -> dict:
    """Claude に渡す facts（判断に要らない building_id 配列などは入れない）。"""
    slim = dict(facts)
    # onsite_groups は理由・棟数・帯だけにして軽くする
    slim["onsite_groups"] = [
        {"attribute": g["attribute"], "reason": g["reason"], "count": g["count"]}
        for g in facts["onsite_groups"][:8]
    ]
    return slim


def _input_hash(model: str, facts: dict) -> str:
    blob = json.dumps({"model": model, "prompt_version": PROMPT_VERSION,
                       "facts": _facts_for_prompt(facts)},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cache_read(h: str) -> dict | None:
    p = os.path.join(CACHE_DIR, f"{h}.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def _cache_write(h: str, rec: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, f"{h}.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)


def _fallback_prose(note: str) -> dict:
    """構造化出力が取れないときの素の文章（数字は表にあるので空でも破綻しない）。"""
    return {"summary": note, "confirmed_commentary": "", "onsite_commentary": "",
            "closing": "本レポートの数値は SQL 集計による。"}


def generate_prose(facts: dict, model: str) -> dict:
    """Claude を1回だけ呼び、文章欄（summary/所見/むすび）を返す。

    返り値: {"prose": {...}, "usage": {...}, "cost_usd": float|None, "cached": bool}
    """
    from estimate.claude_infer import _usage_dict, compute_cost

    h = _input_hash(model, facts)
    cached = _cache_read(h)
    if cached is not None:
        return {"prose": cached["prose"], "usage": cached["usage"],
                "cost_usd": cached.get("cost_usd"), "cached": True}

    import anthropic

    user = ("次の『確定済みの数値』をもとに、上司へ提出する調査レポートの文章を書いてください。"
            "数値は作らず、渡された範囲だけで述べてください。\n\n"
            + json.dumps(_facts_for_prompt(facts), ensure_ascii=False, indent=2))

    client = anthropic.Anthropic()
    msg = client.messages.create(   # ★ 生成1回につき、この1回だけ API を呼ぶ
        model=model, max_tokens=MAX_OUTPUT_TOKENS,
        system=[{"type": "text", "text": GUIDANCE_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": PROSE_SCHEMA}},
    )
    usage = _usage_dict(msg.usage)
    cost = compute_cost(model, usage)

    text = next((b.text for b in msg.content if getattr(b, "type", None) == "text"), None)
    try:
        prose = json.loads(text) if text else None
        if not isinstance(prose, dict) or "summary" not in prose:
            raise ValueError("要約の形が不正")
    except (TypeError, ValueError, json.JSONDecodeError) as e:
        # リトライはしない（1回だけの約束）。素のテキスト or 失敗注記にフォールバック。
        prose = _fallback_prose(text.strip() if text else f"（要約を取得できませんでした：{e}）")

    _cache_write(h, {"hash": h, "prose": prose, "usage": usage, "cost_usd": cost})
    return {"prose": prose, "usage": usage, "cost_usd": cost, "cached": False}


# --- Markdown の組み立て（数値表は Python が描く） --------------------------

def _table(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join("---" for _ in header) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def render_markdown(facts: dict, prose: dict, model: str, meta: dict) -> str:
    f = facts
    today = time.strftime("%Y-%m-%d")
    if f["survey_from"] and f["survey_to"]:
        survey = (f["survey_from"] if f["survey_from"] == f["survey_to"]
                  else f"{f['survey_from']} 〜 {f['survey_to']}")
    else:
        survey = "（確定記録なし）"

    L: list[str] = []
    L.append(f"# まちカルテ 調査レポート — 新宿区（{f['city_code']}）")
    L.append("")
    L.append(_table(["項目", "内容"], [
        ["調査日（確定の記録日）", survey],
        ["対象地区", f"新宿区（{f['city_code']}）"],
        ["元データの版", f"PLATEAU {f['version']}年度版"],
        ["確認・確定した棟数", f"{f['confirmed_buildings']} 棟（属性 {f['confirmed_rows']} 件）"],
        ["レポート作成日", today],
    ]))
    L.append("")

    L.append("## 要約")
    L.append("")
    L.append(prose.get("summary", "").strip() or "（要約なし）")
    L.append("")

    L.append("## 確認・確定の結果")
    L.append("")
    if f["by_attribute"]:
        L.append(_table(["属性", "確定件数", "そのまま確定", "修正して確定"],
                        [[k, v["count"], v["accepted"], v["corrected"]]
                         for k, v in f["by_attribute"].items()]))
    else:
        L.append("まだ確定された属性はありません。")
    L.append("")
    if f["usage_breakdown"]:
        L.append("**確定した用途の内訳**")
        L.append("")
        L.append(_table(["用途", "件数"],
                        [[k, v] for k, v in f["usage_breakdown"].items()]))
        L.append("")
    if f["year_built_span"]:
        s = f["year_built_span"]
        L.append(f"確定した建築年は区間で提案し、{s['min']}〜{s['max']} 年の範囲に収まります。")
        L.append("")
    if f["source_breakdown"]:
        L.append("**推定の由来（source）**")
        L.append("")
        L.append(_table(["由来", "件数"],
                        [[k, v] for k, v in f["source_breakdown"].items()]))
        L.append("")
    commentary = prose.get("confirmed_commentary", "").strip()
    if commentary:
        L.append(commentary)
        L.append("")

    fs = f.get("field_survey") or {}
    L.append("## 現地調査で確定した棟")
    L.append("")
    if fs.get("buildings"):
        L.append(f"スマホの現地調査（source=onsite）で確定したのは **{fs['buildings']} 棟**"
                 f"（属性 {fs['rows']} 件、写真 {fs['photos']} 枚）です。"
                 "机上での確定とは分けて記録しています。")
        L.append("")
        if fs.get("by_attribute"):
            L.append(_table(["属性", "現地で確定した件数"],
                            [[k, v] for k, v in fs["by_attribute"].items()]))
            L.append("")
    else:
        L.append("現地調査で確定した棟はまだありません。")
        L.append("")

    L.append("## 現地確認が必要な棟")
    L.append("")
    L.append(f"不明群 {f['unknown_total']} 棟のうち、自動推定で判断不能だったのは "
             f"**{f['onsite_needed_buildings']} 棟** です。主な理由は次のとおりです。")
    L.append("")
    if f["onsite_groups"]:
        L.append(_table(["属性", "棟数", "判断不能の理由"],
                        [[ONSITE_ATTR_LABEL.get(g["attribute"], g["attribute"]),
                          g["count"], g["reason"]]
                         for g in f["onsite_groups"][:10]]))
    else:
        L.append("判断不能として残った棟はありません。")
    L.append("")
    oc = prose.get("onsite_commentary", "").strip()
    if oc:
        L.append(oc)
        L.append("")

    L.append("## データ源と出典")
    L.append("")
    L.append(_table(["データ源", "表記"],
                    [[src, note] for src, note in ATTRIBUTION_TABLE]))
    L.append("")
    L.append(ATTRIBUTION)
    L.append("")

    L.append("## 注記")
    L.append("")
    L.append("- この更新候補は **公式データを上書きするものではなく、差分の提案** です。"
             "自治体側で内容を確認のうえ反映してください。")
    L.append("- 人が確定した属性だけを対象にしています。**未確定・判断不能の属性は含みません**"
             "（同梱の CSV / GeoJSON も同じ）。")
    L.append(f"- 本レポートの**数値は SQL 集計**、**文章は Claude（{model}）による要約**です。"
             "文章は数値を作らず、集計結果の範囲で書かせています。")
    L.append("")
    tok = (meta["usage"].get("input_tokens", 0) + meta["usage"].get("output_tokens", 0)
           if meta.get("usage") else 0)
    cost = meta.get("cost_usd")
    cost_s = f"${cost:.5f}" if cost is not None else "不明"
    cache_s = "（キャッシュ利用・今回の課金なし）" if meta.get("cached") else ""
    L.append(f"<!-- 生成: model={model}, prompt={PROMPT_VERSION}, "
             f"tokens={tok}, cost={cost_s} {cache_s} -->")
    L.append("")
    return "\n".join(L)


def _empty_report(facts: dict, city_code: str) -> str:
    """確定0件のときの出力。Claude は呼ばず『確定された棟がありません』を明示する。"""
    today = time.strftime("%Y-%m-%d")
    L: list[str] = []
    L.append(f"# まちカルテ 調査レポート — 新宿区（{city_code}）")
    L.append("")
    L.append(_table(["項目", "内容"], [
        ["対象地区", f"新宿区（{city_code}）"],
        ["元データの版", f"PLATEAU {facts['version']}年度版"],
        ["確認・確定した棟数", "0 棟"],
        ["レポート作成日", today],
    ]))
    L.append("")
    L.append("## 確定された棟がありません")
    L.append("")
    L.append("この期間に人が確定した棟（机上 desk・現地 onsite とも）はありません。"
             "確定が0件で数値の根拠がないため、文章要約（Claude）は生成していません"
             "（API 呼び出しなし）。地図で確認・確定するか、現地調査モードで入力したうえで、"
             "再度このコマンドを実行してください。")
    L.append("")
    L.append("## 現地確認が必要な棟（参考）")
    L.append("")
    L.append(f"確定作業とは別に、不明群 {facts['unknown_total']} 棟のうち自動推定で判断不能"
             f"だったのは **{facts['onsite_needed_buildings']} 棟** です（確定0件でも集計できる実数）。")
    L.append("")
    L.append("## データ源と出典")
    L.append("")
    L.append(_table(["データ源", "表記"],
                    [[src, note] for src, note in ATTRIBUTION_TABLE]))
    L.append("")
    L.append("<!-- 生成: 確定0件のため Claude 生成スキップ（API 0 回） -->")
    L.append("")
    return "\n".join(L)


def _append_log(facts: dict, model: str, meta: dict) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fp:
        fp.write(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "city_code": facts["city_code"], "version": facts["version"],
            "confirmed_buildings": facts["confirmed_buildings"],
            "model": model, "usage": meta.get("usage"),
            "cost_usd": meta.get("cost_usd"), "cached": meta.get("cached"),
        }, ensure_ascii=False) + "\n")


# --- 生成（接続に依存しない。CLI と API で共用） ----------------------------

def build_markdown(facts: dict, city_code: str = CITY_CODE,
                   model: str | None = None) -> tuple[str, dict]:
    """facts（SQL 集計済み）から Markdown と生成メタを返す。接続もファイルも触らない。

    確定0件なら Claude を呼ばず『確定された棟がありません』を返す（API 0回）。
    DB アクセス（compute_facts）とは分けてあるので、呼び出し側でロックを解いてから
    この関数（＝Claude 呼び出し）を実行できる。
    """
    from estimate.claude_infer import get_model

    if facts["confirmed_buildings"] == 0:
        return _empty_report(facts, city_code), {
            "skipped": True, "api_calls": 0, "model": None,
            "usage": None, "cost_usd": None, "cached": False}

    model = model or get_model()
    res = generate_prose(facts, model)
    meta = {"usage": res["usage"], "cost_usd": res["cost_usd"],
            "cached": res["cached"], "model": model, "skipped": False,
            "api_calls": 0 if res["cached"] else 1}
    return render_markdown(facts, res["prose"], model, meta), meta


# --- CLI --------------------------------------------------------------------

class ServerRunningError(RuntimeError):
    """API サーバー起動中で DB がロックされているときに投げる（CLI 用）。"""


def _is_lock_error(exc: Exception) -> bool:
    s = str(exc)
    return "lock" in s.lower() or "Conflicting lock" in s


def run(db_path: str | None = None, city_code: str = CITY_CODE,
        version: str = VERSION, out_dir: str = DEFAULT_OUT,
        model: str | None = None, dry_run: bool = False) -> dict:
    import duckdb

    try:
        con = connect(db_path) if db_path else connect()
    except duckdb.IOException as e:
        if _is_lock_error(e):
            raise ServerRunningError from e
        raise
    try:
        facts = compute_facts(con, city_code, version)
    finally:
        con.close()

    print("=== 調査レポートの生成 ===")
    print(f"  city_code={city_code} version={version}")
    print(f"  確定済み: {facts['confirmed_buildings']} 棟 / {facts['confirmed_rows']} 件")
    print(f"  現地確認が必要: {facts['onsite_needed_buildings']} 棟"
          f"（不明群 {facts['unknown_total']} 棟中）")

    if dry_run:
        print("\n--- facts（dry-run：API は呼ばない）---")
        print(json.dumps(_facts_for_prompt(facts), ensure_ascii=False, indent=2))
        return {"dry_run": True, **facts}

    md, meta = build_markdown(facts, city_code, model)

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"report_{city_code}.md")
    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write(md)
    _append_log(facts, meta.get("model") or "(skipped)", meta)

    if meta["skipped"]:
        print("  確定0件：Claude 生成をスキップ（API 0 回）。『確定された棟がありません』を出力。")
    else:
        tag = "キャッシュ" if meta["cached"] else (
            f"${meta['cost_usd']:.5f}" if meta["cost_usd"] is not None else "費用不明")
        print(f"  API 呼び出し: {meta['api_calls']} 回（{tag}）model={meta['model']}")
    print(f"  Markdown    : {out_path}")
    return {"report": out_path, "confirmed_buildings": facts["confirmed_buildings"],
            **meta}


def main() -> None:
    import sys

    load_dotenv()   # .env の ANTHROPIC_API_KEY / CLAUDE_MODEL
    data_dir = os.environ.get("DATA_DIR", "./data")
    p = argparse.ArgumentParser(description="確定済みの棟から調査レポート（Markdown）を生成する")
    p.add_argument("--city-code", default=CITY_CODE, help="市区町村コード（既定 13104）")
    p.add_argument("--version", default=VERSION, help="年度版（既定 2025）")
    p.add_argument("--db", default=os.path.join(data_dir, "machikarte.duckdb"))
    p.add_argument("--out-dir", default=os.path.join(data_dir, "derived", "export"))
    p.add_argument("--model", default=None, help="CLAUDE_MODEL を上書き")
    p.add_argument("--dry-run", action="store_true",
                   help="API を呼ばず、数字（facts）だけ出す")
    args = p.parse_args()
    try:
        run(args.db, args.city_code, args.version, args.out_dir, args.model, args.dry_run)
    except ServerRunningError:
        print("⚠ DB がロックされています。API サーバー（uvicorn）が起動中は CLI でレポート生成"
              "できません。\n   → サーバーを止めてから再実行するか、画面の「レポート生成」"
              "（POST /api/report）を使ってください。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
