"""判断不能の集約と「現地で何を確認すべきか」の要約（設計書「判断不能も残す」）。

不明群(is_target)の candidates のうち **判断不能（unresolvable=TRUE）** の棟を、
属性ごと・理由ごとに数え、高さ帯別・エリア別（西新宿1km圏）に内訳を出す。
判断不能は value=NULL のマーカーだが、year_built は区間（value_min/max）で value=NULL の
正常推定があるため、ここでは value=NULL ではなく **unresolvable フラグ** で判定する。

要約は Claude に **区分ごとに1回だけ**投げる（棟ごとには呼ばない＝費用と時間のため）。
入力ハッシュでキャッシュし、トークン・費用をログに残す（CLAUDE.md）。現地確認でも
埋まらない区分には「別データが必要」と正直に言わせる。生成は `--generate`、API は
生成済み JSON（data/derived/onsite_guidance.json）を読むだけ（リクエスト経路で Claude を呼ばない）。

実行:
    uv run python -m api.onsite              # 集約だけ表示（Claude 呼ばない）
    uv run python -m api.onsite --generate   # 区分ごとに Claude 要約を生成し JSON 保存
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time

from .db import DERIVED_DIR, VERSION, connect

# 高さ帯（eval に依存しないよう api 内に持つ。定義は eval.common と同じ）
HEIGHT_BANDS = [("0-10m", 0.0, 10.0), ("10-20m", 10.0, 20.0),
                ("20-45m", 20.0, 45.0), ("45m+", 45.0, float("inf"))]
BAND_ORDER = [b[0] for b in HEIGHT_BANDS] + ["高さ無"]

# 西新宿1km圏（都庁。priority.demo_counts と同じ定義）
TOCHO_LON, TOCHO_LAT, NISHI_R = 139.6917, 35.6896, 1000.0
AREA_NISHI, AREA_OTHER = "西新宿1km圏", "その他"

GUIDANCE_PATH = os.path.join(DERIVED_DIR, "onsite_guidance.json")
CACHE_DIR = "data/cache/onsite_guidance"
LOG_PATH = os.path.join(DERIVED_DIR, "onsite_guidance_log.jsonl")

GUIDANCE_SYSTEM = (
    "あなたは建物属性調査の補助です。ある属性が自動推定で『判断不能』だった建物群について、"
    "担当者が現地（または合法的に入手できる資料）で何を確認すればその属性を埋められるかを、"
    "具体的な行動として日本語で1〜2行で述べてください。"
    "確認手段が無い、または現地に行っても分からない区分では、埋め方をでっち上げず"
    "『現地確認では埋まらない（別データが必要）』と正直に述べます。"
    "出力は要約文そのものだけ（前置き・箇条書き・記号の装飾は不要）。"
)


def band_of(height) -> str | None:
    if height is None:
        return None
    for label, lo, hi in HEIGHT_BANDS:
        if lo <= height < hi:
            return label
    return None


def group_key(attribute: str, reason: str) -> str:
    """属性＋理由から安定した短いキー（キャッシュ・区分の同定に使う）。"""
    return hashlib.sha1(f"{attribute}|{reason}".encode("utf-8")).hexdigest()[:10]


# --- 集約（純 SQL。Claude を呼ばない） --------------------------------------

def aggregate_unresolved(con, version: str = VERSION) -> dict:
    """判断不能（unresolvable=TRUE）の不明群を属性×理由で集約する。"""
    total_targets = con.execute(
        "SELECT count(*) FROM buildings WHERE version=? AND is_target", [version]
    ).fetchone()[0]

    rows = con.execute(
        """
        SELECT c.attribute, c.reasoning, c.building_id, b.height_m,
               ST_Distance(
                 ST_Centroid(b.geom),
                 ST_Transform(ST_Point(?, ?), 'EPSG:4326', 'EPSG:6677', true)
               ) AS dist_m,
               ST_X(ST_Centroid(b.geom_4326)) AS lon,
               ST_Y(ST_Centroid(b.geom_4326)) AS lat
        FROM candidates c
        JOIN buildings b
          ON c.building_id = b.building_id AND c.version = b.version
        WHERE c.version = ? AND c.unresolvable AND b.is_target AND b.geom IS NOT NULL
        """,
        [TOCHO_LON, TOCHO_LAT, version],
    ).fetchall()

    groups: dict[str, dict] = {}
    distinct: set[str] = set()
    for attribute, reason, bid, h, dist, lon, lat in rows:
        distinct.add(bid)
        key = group_key(attribute, reason)
        g = groups.setdefault(key, {
            "key": key, "attribute": attribute, "reason": reason, "count": 0,
            "bands": {}, "areas": {AREA_NISHI: 0, AREA_OTHER: 0}, "building_ids": [],
            "bbox": None,
        })
        g["count"] += 1
        g["building_ids"].append(bid)
        band = band_of(h) or "高さ無"
        g["bands"][band] = g["bands"].get(band, 0) + 1
        area = AREA_NISHI if (dist is not None and dist <= NISHI_R) else AREA_OTHER
        g["areas"][area] += 1
        if lon is not None and lat is not None:
            bb = g["bbox"]
            g["bbox"] = ([lon, lat, lon, lat] if bb is None else
                         [min(bb[0], lon), min(bb[1], lat),
                          max(bb[2], lon), max(bb[3], lat)])

    # 帯の並びを固定（0件の帯は落とす）
    for g in groups.values():
        g["bands"] = {b: g["bands"][b] for b in BAND_ORDER if b in g["bands"]}

    group_list = sorted(groups.values(), key=lambda x: -x["count"])
    return {
        "total_targets": total_targets,
        "unresolved_buildings": len(distinct),
        "groups": group_list,
    }


# --- Claude 要約（区分ごとに1回。キャッシュ＋費用ログ） --------------------

def _guidance_payload(g: dict) -> dict:
    """Claude に渡す1区分ぶんの事実（棟数・帯・エリアの内訳）。"""
    return {
        "attribute": g["attribute"],
        "unresolved_reason": g["reason"],
        "count": g["count"],
        "height_bands": g["bands"],
        "areas": g["areas"],
    }


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


def generate_guidance(con, version: str = VERSION, model: str | None = None) -> dict:
    """区分ごとに Claude 要約を生成し、data/derived/onsite_guidance.json に保存する。"""
    import anthropic
    from dotenv import load_dotenv

    load_dotenv()
    from estimate.claude_infer import _usage_dict, compute_cost, get_model

    model = model or get_model()
    agg = aggregate_unresolved(con, version)
    client = anthropic.Anthropic()

    guidance: dict[str, dict] = {}
    records: list[dict] = []
    live = 0
    for g in agg["groups"]:
        payload = _guidance_payload(g)
        blob = json.dumps({"model": model, "v": 1, "payload": payload},
                          sort_keys=True, ensure_ascii=False)
        h = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        cached = _cache_read(h)
        if cached is not None:
            guidance[g["key"]] = cached["guidance"]
            continue

        user = ("次の『判断不能だった建物群』について、現地で何を確認すればこの属性を"
                "埋められるかを1〜2行で。埋まらないならそう述べてください。\n"
                + json.dumps(payload, ensure_ascii=False, indent=2))
        msg = client.messages.create(
            model=model, max_tokens=1024,
            system=[{"type": "text", "text": GUIDANCE_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in msg.content
                       if getattr(b, "type", None) == "text").strip()
        if not text:
            text = "（要約を取得できませんでした）"
        usage = _usage_dict(msg.usage)
        cost = compute_cost(model, usage)
        gobj = {"key": g["key"], "attribute": g["attribute"], "reason": g["reason"],
                "count": g["count"], "summary": text, "model": model}
        _cache_write(h, {"hash": h, "guidance": gobj, "usage": usage, "cost_usd": cost})
        guidance[g["key"]] = gobj
        records.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "key": g["key"],
                        "attribute": g["attribute"], "model": model,
                        "usage": usage, "cost_usd": cost})
        live += 1

    os.makedirs(DERIVED_DIR, exist_ok=True)
    with open(GUIDANCE_PATH, "w", encoding="utf-8") as f:
        json.dump({"model": model, "groups": guidance}, f, ensure_ascii=False, indent=2)
    if records:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    total_cost = sum(r["cost_usd"] or 0 for r in records)
    return {"groups": len(agg["groups"]), "api_calls": live,
            "cost_usd": total_cost, "path": GUIDANCE_PATH}


def load_guidance() -> dict[str, dict]:
    """生成済みの要約を key→{summary,...} で返す。無ければ空。"""
    if not os.path.exists(GUIDANCE_PATH):
        return {}
    with open(GUIDANCE_PATH, encoding="utf-8") as f:
        return json.load(f).get("groups", {})


# --- CLI --------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="判断不能の集約と現地確認ガイドの生成")
    p.add_argument("--generate", action="store_true",
                   help="区分ごとに Claude 要約を生成し JSON 保存（API を呼ぶ）")
    args = p.parse_args()
    con = connect(read_only=True)
    agg = aggregate_unresolved(con)
    print(f"=== 判断不能の集約（不明群 {agg['total_targets']} / "
          f"現地確認が必要 {agg['unresolved_buildings']} 棟）===")
    for g in agg["groups"]:
        print(f"\n  区分[{g['key']}] {g['attribute']} / {g['count']}棟")
        print(f"    理由: {g['reason']}")
        print(f"    高さ帯: " + " / ".join(f"{b} {n}" for b, n in g["bands"].items()))
        print(f"    エリア: " + " / ".join(f"{a} {n}" for a, n in g["areas"].items()))
    if args.generate:
        print("\n--- Claude 要約を生成中（区分ごとに1回）---")
        res = generate_guidance(con)
        print(f"  区分 {res['groups']} / API {res['api_calls']} 回 / "
              f"${res['cost_usd']:.4f} → {res['path']}")
    con.close()


if __name__ == "__main__":
    main()
