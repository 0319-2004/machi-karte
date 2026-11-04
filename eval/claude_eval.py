"""Claude 属性推定の検証（設計書「計測と検証」／「前提が崩れたら止める点」）。

既知棟のホールドアウトで、Claude の推定を正解と突き合わせる。**検証を先にやる。**

出すもの:
1. 用途・階数の正解率、判断不能率、「根拠なく断定して外した」件数（既知棟 n 棟）
2. 高さ20m以上で、Claude の用途が近傍(c)＝56.8%ベースラインを超えるか
   （超えなければ用途は近傍の候補提示のまま、が設計書の分岐）
3. 屋上と道路しか写っていない画像を渡し、階数を判断不能と返すか（--roof-image）
4. 2モデルで精度と費用を比べ、採用の判断材料を出す（--models）

**リークを避ける:** 近傍の用途分布・近傍ベースライン・階高はすべて train だけから作り、
対象棟自身は exclude する。Claude には正解を一切渡さない。

実行:
    uv run python -m eval.claude_eval --n 30
    uv run python -m eval.claude_eval --n 30 --models claude-opus-5-5,claude-haiku-5-5
    uv run python -m eval.claude_eval --roof-image path/to/roof.jpg
"""
from __future__ import annotations

import argparse
import base64
import os

import duckdb
import numpy as np
from dotenv import load_dotenv

from eval.common import NeighborModel, load_known
from eval.holdout import DB_PATH, VERSION
from estimate.claude_infer import (DEFAULT_MODEL, HIGHRISE_M, Building, Image, InferConfig,
                                   build_context, build_schema,
                                   compute_cost, format_usage_codes,
                                   infer_building, load_prompt, load_usage_codes,
                                   load_buildings_rows, neighbor_context_known,
                                   no_images, poi_evidence_by_building)
from estimate.storeys import HEIGHT_TOL, NEIGHBOR_K, learn_floor_heights

def connect_ro(db_path: str = DB_PATH) -> duckdb.DuckDBPyConnection:
    """読み取り専用で開く（評価は読むだけ。稼働中の server と同居できる）。"""
    con = duckdb.connect(db_path, read_only=True)
    con.execute("LOAD spatial;")
    return con


RECKLESS_CONF = 0.6     # これ以上の confidence で断定して外したら「根拠なく断定」
BASELINE_C = 56.8       # 設計書：近傍＋高さ±3m の用途正解率
STOREYS_TOL = 1         # 階数は±1階許容も見る


# --- 評価対象の既知棟を選ぶ -------------------------------------------------

def select_known(con, n: int, min_height: float | None, version=VERSION):
    """holdout の既知棟から n 棟を返す（正解 usage/storeys つき）。"""
    pred = "usage IS NOT NULL AND height_m IS NOT NULL"
    if min_height is not None:
        pred += f" AND height_m >= {min_height}"
    rows = con.execute(
        f"""
        SELECT b.building_id, b.height_m, b.roof_edge_area,
               ST_X(ST_Centroid(b.geom)), ST_Y(ST_Centroid(b.geom)),
               b.usage, b.storeys_above
        FROM buildings b
        JOIN eval_split s ON b.building_id=s.building_id AND b.version=s.version
        WHERE b.version=? AND s.split='holdout' AND b.geom IS NOT NULL AND ({pred})
        ORDER BY b.building_id
        """,
        [version],
    ).fetchall()
    out = []
    for bid, h, area, cx, cy, usage, storeys in rows[:n]:
        out.append({
            "b": Building(bid, h, area, (cx, cy)),
            "true_usage": usage,
            "true_storeys": storeys,
        })
    return out


# --- 近傍(c) ベースライン（train のみ、同じ棟を評価） -----------------------

def neighbor_c_predictions(con, eval_rows, version=VERSION):
    """評価棟に対する近傍＋高さ±3m の用途予測（train のみ）。"""
    d = load_known(con, version)
    tr = d["split"] == "train"
    model = NeighborModel(d["xy"][tr], d["usage"][tr], d["height"][tr],
                          train_id=d["building_id"][tr])
    xy = np.array([r["b"].xy for r in eval_rows], dtype=float)
    heights = np.array([r["b"].height for r in eval_rows], dtype=float)
    return model.predict_many_knn(xy, NEIGHBOR_K, heights=heights,
                                  height_tol=HEIGHT_TOL)


# --- context 作成（train 近傍・exclude・train 階高でリーク防止） -------------

def build_eval_contexts(con, eval_rows, usage_codes, version=VERSION):
    from estimate.poi import compute as poi_compute
    try:
        poi_feats = poi_compute(con, version)["feats"]
    except Exception:
        poi_feats = {}
    poi_named = poi_evidence_by_building(con, version)
    fh = learn_floor_heights(con, version, split="train")       # train のみ
    neigh = neighbor_context_known(con, version, split="train")  # train のみ
    ctxs = {}
    for r in eval_rows:
        b = r["b"]
        ctxs[b.building_id] = build_context(
            b, neigh, poi_feats.get(b.building_id),
            poi_named.get(b.building_id, []), fh, usage_codes,
            exclude_id=b.building_id)   # 自棟を近傍から除く
    return ctxs


# --- 1モデルの評価 ----------------------------------------------------------

def make_config(model: str, usage_codes: dict) -> InferConfig:
    text_raw, pv = load_prompt()
    text = text_raw.replace("{USAGE_CODES}", format_usage_codes(usage_codes))
    return InferConfig(model=model, prompt_text=text, prompt_version=pv,
                       schema=build_schema(usage_codes),
                       thinking=os.environ.get("CLAUDE_THINKING") == "1")


def evaluate_model(client, cfg, eval_rows, ctxs, usage_codes,
                   neigh_c_preds) -> dict:
    """1モデルを評価し、指標と per-building を返す。"""
    recs = []
    for r in eval_rows:
        b = r["b"]
        rec = infer_building(client, cfg, b, ctxs[b.building_id], no_images(b.building_id),
                             usage_codes)
        recs.append((r, rec))

    n = len(recs)
    # --- usage ---
    u_correct = u_resolved = u_reckless_wrong = u_unres = 0
    hi_correct = hi_resolved = hi_total = 0     # 高さ20m以上
    c_hi_correct = 0                            # (c) ベースライン 高さ20m以上
    for i, (r, rec) in enumerate(recs):
        cell = rec["result"]["usage"]
        pred, conf = cell["value"], cell["confidence"]
        true = r["true_usage"]
        unres = "usage" in rec["result"]["unresolvable"] or pred is None
        highrise = r["b"].height is not None and r["b"].height >= HIGHRISE_M
        if unres:
            u_unres += 1
        else:
            u_resolved += 1
            if pred == true:
                u_correct += 1
            elif conf >= RECKLESS_CONF:
                u_reckless_wrong += 1
        if highrise:
            hi_total += 1
            if not unres:
                hi_resolved += 1
                if pred == true:
                    hi_correct += 1
            if neigh_c_preds[i] == true:
                c_hi_correct += 1

    # --- storeys ---
    s_exact = s_within1 = s_resolved = s_unres = 0
    for r, rec in recs:
        cell = rec["result"]["storeys"]
        pred = cell["value"]
        true = r["true_storeys"]
        if "storeys" in rec["result"]["unresolvable"] or pred is None:
            s_unres += 1
            continue
        s_resolved += 1
        if true is not None:
            if pred == true:
                s_exact += 1
            if abs(pred - true) <= STOREYS_TOL:
                s_within1 += 1

    live = [rec for _, rec in recs if not rec["cached"]]
    cost = sum(rec["cost_usd"] or 0 for rec in live)
    tok = sum(rec["usage"].get("input_tokens", 0) + rec["usage"].get("output_tokens", 0)
              for rec in live)

    def pct(a, b):
        return (a / b * 100) if b else float("nan")

    return {
        "model": cfg.model, "n": n, "records": recs,
        "usage": {
            "acc_all": pct(u_correct, n),
            "acc_resolved": pct(u_correct, u_resolved),
            "unresolvable_rate": pct(u_unres, n),
            "reckless_wrong": u_reckless_wrong,
            "resolved": u_resolved,
        },
        "highrise": {
            "n": hi_total,
            "claude_acc_resolved": pct(hi_correct, hi_resolved),
            "claude_acc_all": pct(hi_correct, hi_total),
            "neighbor_c_acc": pct(c_hi_correct, hi_total),
            "resolved": hi_resolved,
        },
        "storeys": {
            "exact": pct(s_exact, s_resolved),
            "within1": pct(s_within1, s_resolved),
            "unresolvable_rate": pct(s_unres, n),
            "resolved": s_resolved,
        },
        "cost_usd": cost, "tokens": tok, "live": len(live),
    }


def print_model_report(m: dict) -> None:
    print(f"\n=== {m['model']}（既知棟 n={m['n']}）===")
    u = m["usage"]
    print("[用途]")
    print(f"  正解率（全体）        : {u['acc_all']:.1f}%")
    print(f"  正解率（判断できた分） : {u['acc_resolved']:.1f}%（{u['resolved']}棟）")
    print(f"  判断不能率            : {u['unresolvable_rate']:.1f}%")
    print(f"  根拠なく断定して外した : {u['reckless_wrong']} 件"
          f"（conf≥{RECKLESS_CONF}で誤答）")
    hi = m["highrise"]
    print(f"[用途・高さ{HIGHRISE_M:.0f}m以上 n={hi['n']}]")
    print(f"  Claude（判断できた分） : {hi['claude_acc_resolved']:.1f}%"
          f"（{hi['resolved']}棟）")
    print(f"  Claude（全体）         : {hi['claude_acc_all']:.1f}%")
    print(f"  近傍(c)ベースライン    : {hi['neighbor_c_acc']:.1f}%（設計書 {BASELINE_C}%）")
    beat = hi["claude_acc_all"] - hi["neighbor_c_acc"]
    verdict = "超えた → 用途推定の上積みあり" if beat > 0 else \
              "超えず → 用途は近傍の候補提示のまま（設計書の分岐）"
    print(f"  判定: Claude − 近傍(c) = {beat:+.1f}pt  {verdict}")
    s = m["storeys"]
    print("[階数]")
    print(f"  完全一致/±1階（判断分）: {s['exact']:.1f}% / {s['within1']:.1f}%"
          f"（{s['resolved']}棟）")
    print(f"  判断不能率            : {s['unresolvable_rate']:.1f}%")
    print(f"[費用] API {m['live']}棟 / トークン {m['tokens']} / ${m['cost_usd']:.4f}"
          f"（1棟 ${m['cost_usd']/max(1,m['live']):.5f}）")


# --- 屋上・道路だけの画像 → 階数を判断不能と返すか --------------------------

def roof_only_check(con, cfg, usage_codes, image_path: str,
                    building_id: str | None, version=VERSION) -> None:
    """屋上と道路しか写っていない画像を渡し、storeys が判断不能になるか確かめる。"""
    rows = load_buildings_rows(con, "is_target", version)
    b = next((x for x in rows if x.building_id == building_id), None) if building_id \
        else (rows[0] if rows else None)
    if b is None:
        print("対象棟が見つからない（is_target が空？）。--roof-building で指定を。")
        return

    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    ext = os.path.splitext(image_path)[1].lower()
    media = {".png": "image/png", ".jpg": "image/jpeg",
             ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/jpeg")
    img = Image(media_type=media, data=data, source="屋上・道路のみ（検証用）",
                url=image_path)

    neigh = neighbor_context_known(con, version)
    from estimate.poi import compute as poi_compute
    try:
        feats = poi_compute(con, version)["feats"].get(b.building_id)
    except Exception:
        feats = None
    fh = learn_floor_heights(con, version)
    ctx = build_context(b, neigh, feats, [], fh, usage_codes)

    import anthropic
    client = anthropic.Anthropic()
    rec = infer_building(client, cfg, b, ctx, [img], usage_codes)
    res = rec["result"]
    ok = "storeys" in res["unresolvable"] or res["storeys"]["value"] is None
    print(f"\n=== 屋上・道路のみ画像の階数判定（{b.building_id}）===")
    print(f"  storeys.value   : {res['storeys']['value']}")
    print(f"  storeys.reason  : {res['storeys']['reasoning']}")
    print(f"  判断不能か       : {'はい（期待どおり）' if ok else 'いいえ（断定した）'}")
    if not ok:
        print("  ⚠ 屋上と道路だけの画像から階数を断定した。設計書「前提が崩れたら止める点」"
              "（Claudeが判断不能を安定して返さない）に該当。プロンプトを見直す。")


# --- 2モデル比較と採用判断 --------------------------------------------------

def compare_and_recommend(results: list[dict]) -> None:
    print("\n=== モデル比較 ===")
    print(f"  {'model':<20}{'用途正解':>9}{'判断不能':>9}{'断定誤':>7}"
          f"{'20m超vs(c)':>12}{'費用$':>9}")
    for m in results:
        hi = m["highrise"]
        beat = hi["claude_acc_all"] - hi["neighbor_c_acc"]
        print(f"  {m['model']:<20}{m['usage']['acc_all']:>8.1f}%"
              f"{m['usage']['unresolvable_rate']:>8.1f}%"
              f"{m['usage']['reckless_wrong']:>7}"
              f"{beat:>+11.1f}{m['cost_usd']:>9.4f}")

    # 採用：20m超で(c)を最も上回り、断定誤が少なく、費用が低い方を優先
    def key(m):
        hi = m["highrise"]
        beat = hi["claude_acc_all"] - hi["neighbor_c_acc"]
        return (round(beat, 1), -m["usage"]["reckless_wrong"], -m["cost_usd"])
    best = max(results, key=key)
    hi = best["highrise"]
    beat = hi["claude_acc_all"] - hi["neighbor_c_acc"]
    print(f"\n  採用候補: {best['model']}")
    print(f"  理由: 高さ20m以上で近傍(c)を {beat:+.1f}pt、断定誤 "
          f"{best['usage']['reckless_wrong']}件、費用 ${best['cost_usd']:.4f}。")
    print("  ※ この数字と採用理由を docs/decisions.md に日付付きで追記すること"
          "（推測の数字は書かない）。")


# --- CLI --------------------------------------------------------------------

def run(n: int, min_height: float | None, models: list[str] | None,
        roof_image: str | None, roof_building: str | None,
        version=VERSION) -> None:
    con = connect_ro()
    usage_codes = load_usage_codes()

    if roof_image:
        cfg = make_config(models[0] if models else (os.environ.get("CLAUDE_MODEL")
                          or DEFAULT_MODEL), usage_codes)
        roof_only_check(con, cfg, usage_codes, roof_image, roof_building, version)
        con.close()
        return

    eval_rows = select_known(con, n, min_height, version)
    if not eval_rows:
        print("評価対象の既知棟が無い。先に `make eval`（holdout 分割）を実行する。")
        con.close()
        return
    print(f"評価対象: holdout 既知棟 {len(eval_rows)} 棟"
          + (f"（高さ{min_height:.0f}m以上）" if min_height else ""))

    ctxs = build_eval_contexts(con, eval_rows, usage_codes, version)
    neigh_c = neighbor_c_predictions(con, eval_rows, version)

    import anthropic
    client = anthropic.Anthropic()
    model_list = models or [os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL]

    results = []
    for model in model_list:
        cfg = make_config(model, usage_codes)
        m = evaluate_model(client, cfg, eval_rows, ctxs, usage_codes, neigh_c)
        print_model_report(m)
        results.append(m)

    if len(results) > 1:
        compare_and_recommend(results)

    # 判断不能を「安定して返す」かの点検（0%なら要注意）
    for m in results:
        if m["usage"]["unresolvable_rate"] == 0 and m["storeys"]["unresolvable_rate"] == 0:
            print(f"\n⚠ {m['model']}: 判断不能を一度も返していない。"
                  "設計書「前提が崩れたら止める点」。屋上・道路のみ画像（--roof-image）で"
                  "挙動を確かめ、返さないならプロンプトを見直す。")
    con.close()


def main() -> None:
    load_dotenv()   # .env の ANTHROPIC_API_KEY / CLAUDE_MODEL を読む
    p = argparse.ArgumentParser(description="Claude 属性推定の既知棟検証")
    p.add_argument("--n", type=int, default=30, help="評価棟数（既定30）")
    p.add_argument("--min-height", type=float, default=None,
                   help="高さ下限（m）。用途 vs 近傍(c) は 20 を推奨")
    p.add_argument("--models", default=None,
                   help="カンマ区切りで2モデル比較（例: claude-opus-4-8,claude-sonnet-4-6）")
    p.add_argument("--roof-image", default=None,
                   help="屋上・道路のみ画像のパス（階数が判断不能になるか検証）")
    p.add_argument("--roof-building", default=None, help="検証に使う不明群の建物ID")
    args = p.parse_args()
    models = [s.strip() for s in args.models.split(",")] if args.models else None
    run(n=args.n, min_height=args.min_height, models=models,
        roof_image=args.roof_image, roof_building=args.roof_building)


if __name__ == "__main__":
    main()
