"""階数の仮推定（高さ÷用途別階高）と、用途の候補提示（近傍＋高さ±3m）。

設計書「階数推定」「判定の採用（用途は候補提示）」に対応する、推定の主軸。

- 階数 = 高さ ÷ 用途別の階高。階高は既知棟から学習する（median(height/storeys)）。
  用途が分からない棟（＝不明群）は、近傍で推した用途の階高を使い、それも無ければ
  全体の中央値を使う。学習値は設計書の 3.80 / 3.70 / 3.96 / 4.47m と照合する。
- 高さ 45m 超は reasoning に「高層のため誤差が大きい」と注記（信頼度も下げる）。
  source='height' で candidates に入れる。高さ欠損の棟は storeys を判断不能で残す。
- 用途は近傍＋高さ±3m の多数決（source='neighbor'）。信頼度は低〜中で、高さ20m以上は「低」。
  POI と OSM 建物タグは evidence に入れ、主用途が住宅系で1階に店舗/事務所POIがある棟は
  「併用住宅の可能性」フラグを立てる（decisions.md 2026-11-03）。

ホールドアウトで階数の完全一致率と ±1階の率を出す（設計書 62.3% / 92.1% と照合）。

実行:
    uv run python -m estimate.storeys            # 不明群に候補を投入
    uv run python -m estimate.storeys --eval     # ホールドアウトで階数精度を出す（投入しない）
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

import numpy as np

from api.schema import ensure_tables
from eval.common import NeighborModel, band_of, connect, load_known
from eval.holdout import VERSION

# 設計書で示された用途別階高の実測値（照合用）
DESIGN_FLOOR_HEIGHTS = {"411": 3.80, "412": 3.70, "401": 3.96, "422": 4.47}
RESIDENTIAL_USAGES = {"411", "412", "413", "414", "415"}  # 住宅系（主用途）

NEIGHBOR_K = 5          # 近傍の棟数（decisions.md: k=5 で最良 58.2%）
HEIGHT_TOL = 3.0        # 高さ±3m で近傍を絞る（設計書）
MIN_FLOOR_H = 2.5       # 階高の下限クリップ（外れ値対策）
MAX_FLOOR_H = 6.0       # 階高の上限クリップ


# --- 階高の学習 -------------------------------------------------------------

def learn_floor_heights(con, version: str = VERSION, split: str | None = None) -> dict:
    """既知棟から用途別の階高（median height/storeys）と全体中央値を学習する。

    split を与えると eval_split の該当分割だけで学習する（評価リーク防止）。
    返り値: {"by_usage": {usage: fh}, "overall": fh, "n_by_usage": {...}}
    """
    join = ""
    params = [version]
    if split is not None:
        join = "JOIN eval_split s ON b.building_id=s.building_id AND b.version=s.version"
        where_split = "AND s.split = ?"
        params = [version, split]
    else:
        where_split = ""
    rows = con.execute(
        f"""
        SELECT b.usage, b.height_m, b.storeys_above
        FROM buildings b {join}
        WHERE b.version = ? AND b.usage IS NOT NULL
          AND b.height_m IS NOT NULL AND b.storeys_above IS NOT NULL
          AND b.storeys_above >= 1 {where_split}
        """,
        params,
    ).fetchall()

    per = {}
    all_fh = []
    for usage, h, st in rows:
        fh = h / st
        if not (MIN_FLOOR_H <= fh <= MAX_FLOOR_H):
            continue
        per.setdefault(usage, []).append(fh)
        all_fh.append(fh)

    by_usage = {u: float(np.median(v)) for u, v in per.items()}
    n_by_usage = {u: len(v) for u, v in per.items()}
    overall = float(np.median(all_fh)) if all_fh else 3.8
    return {"by_usage": by_usage, "overall": overall, "n_by_usage": n_by_usage}


def floor_height_for(usage, fh: dict) -> tuple[float, str]:
    """用途に対応する階高（無ければ全体中央値）と、根拠文の素を返す。"""
    if usage is not None and usage in fh["by_usage"]:
        return fh["by_usage"][usage], f"{usage}の階高"
    return fh["overall"], "全体中央値の階高"


def predict_storeys(height: float, floor_h: float) -> int:
    return max(1, int(round(height / floor_h)))


# --- 近傍の用途モデル（本番は全既知棟を候補にする） -------------------------

def build_neighbor_model(known) -> NeighborModel:
    return NeighborModel(known["xy"], known["usage"], known["height"],
                         train_id=known["building_id"])


def load_targets(con, version: str = VERSION):
    """不明群（is_target）の id・高さ・重心(6677)を返す。"""
    rows = con.execute(
        """
        SELECT building_id, height_m,
               ST_X(ST_Centroid(geom)), ST_Y(ST_Centroid(geom))
        FROM buildings
        WHERE version=? AND is_target AND geom IS NOT NULL
        """,
        [version],
    ).fetchall()
    return {
        "building_id": np.array([r[0] for r in rows]),
        "height": np.array([r[1] if r[1] is not None else np.nan for r in rows], dtype=float),
        "xy": np.array([[r[2], r[3]] for r in rows], dtype=float),
    }


# --- 信頼度 → 低〜中（画面では高・中・低に畳む。ここでは数値のみ） ----------

def usage_confidence(height) -> float:
    """用途（近傍）の信頼度：低〜中。高さ20m以上は「低」。"""
    if height is None or np.isnan(height):
        return 0.3          # 高さ不明 → 低
    return 0.3 if height >= 20.0 else 0.5   # 20m以上=低 / 未満=中


def storeys_confidence(height) -> float:
    """階数（高さ÷階高）の信頼度：基本は高。45m超は下げる（誤差大）。"""
    if height is None or np.isnan(height):
        return 0.0
    return 0.35 if height > 45.0 else 0.8


# --- POI/OSM を evidence に、併用住宅フラグ ----------------------------------

def _poi_feats_for_targets(con, target_ids):
    """不明群に付く POI/OSM 特徴量を返す（estimate.poi を再利用）。無ければ空。"""
    try:
        from estimate.poi import compute as poi_compute
    except Exception:
        return {}
    out = poi_compute(con)
    feats = out["feats"]
    idset = set(target_ids.tolist())
    return {bid: v for bid, v in feats.items() if bid in idset}


def _top_categories(cat_dist: dict, n: int = 4) -> list[str]:
    return [c for c, _ in Counter(cat_dist).most_common(n)]


# --- 候補と根拠の投入 -------------------------------------------------------

def run_estimate(con, version: str = VERSION) -> dict:
    """不明群に storeys / usage 候補と evidence を投入する（本番）。"""
    ensure_tables(con)
    known = load_known(con, version)
    fh = learn_floor_heights(con, version)          # 本番は全既知棟で学習
    model = build_neighbor_model(known)
    tgt = load_targets(con, version)
    poi_feats = _poi_feats_for_targets(con, tgt["building_id"])

    # 近傍＋高さ±3m で用途を一括予測
    usage_pred = model.predict_many_knn(
        tgt["xy"], k=NEIGHBOR_K, heights=tgt["height"], height_tol=HEIGHT_TOL
    )

    cand_rows = []   # candidates
    ev_rows = []     # evidence
    # 既存の推定を入れ直す（冪等）
    con.execute("DELETE FROM candidates WHERE version=? AND source IN ('height','neighbor')",
                [version])
    con.execute("DELETE FROM evidence WHERE version=? AND kind IN ('poi','osm_building')",
                [version])

    n_mixed = 0
    for i, bid in enumerate(tgt["building_id"]):
        h = tgt["height"][i]
        has_h = not np.isnan(h)
        pred_usage = usage_pred[i]
        band = band_of(h) if has_h else "高さ無"

        # --- evidence（POI / OSM 建物タグ）と併用住宅フラグ ---
        ev_ids: list[str] = []
        pf = poi_feats.get(bid)
        mixed_use = False
        if pf:
            if pf.get("poi_n"):
                cats = _top_categories(pf.get("cat_dist", {}))
                eid = f"{bid}-poi"
                ev_rows.append([eid, bid, version, "poi",
                                "POI: " + ", ".join(cats) if cats else "POI あり",
                                json.dumps({"n": pf["poi_n"], "categories": cats,
                                            "poi_pred_usage": pf.get("poi_pred")},
                                           ensure_ascii=False)])
                ev_ids.append(eid)
                # 併用住宅の可能性：主用途が住宅系で、1階に店舗/事務所POIがある
                if pred_usage in RESIDENTIAL_USAGES and pf.get("poi_pred") is not None:
                    mixed_use = True
            if pf.get("osmb_n"):
                eid = f"{bid}-osmb"
                ev_rows.append([eid, bid, version, "osm_building",
                                f"OSM建物タグ → {pf.get('osmb_pred') or '用途外'}",
                                json.dumps({"n": pf["osmb_n"],
                                            "osm_pred_usage": pf.get("osmb_pred")},
                                           ensure_ascii=False)])
                ev_ids.append(eid)
        if mixed_use:
            n_mixed += 1
            eid = f"{bid}-mixeduse"
            ev_rows.append([eid, bid, version, "poi", "併用住宅の可能性（1階に店舗/事務所POI）",
                            json.dumps({"note": "PLATEAUの用途コードは建物の主用途。"
                                        "POIは1階の使われ方を示す"}, ensure_ascii=False)])
            ev_ids.append(eid)

        # --- usage 候補（source='neighbor'） ---
        u_conf = usage_confidence(h)
        reason = (f"周辺の既知棟 {NEIGHBOR_K} 棟を高さ±{HEIGHT_TOL:.0f}mで絞った多数決。"
                  f"最も近い主用途は推定 {pred_usage}。"
                  + ("近隣に同用途が多く確度はやや高い。" if (has_h and h < 20) else
                     "高層で地上の手掛かりが届きにくく確度は低い。" if has_h else
                     "高さが欠損で帯別の確からしさを測れない。"))
        if mixed_use:
            reason += "POIから1階に店舗/事務所があり、併用住宅（413/414）の可能性。"
        cand_rows.append([bid, version, "usage", pred_usage, None, None,
                          u_conf, "neighbor", reason, ev_ids, False])

        # --- storeys 候補（source='height'） ---
        if has_h:
            floor_h, fh_label = floor_height_for(pred_usage, fh)
            n = predict_storeys(h, floor_h)
            s_conf = storeys_confidence(h)
            s_reason = (f"高さ {h:.1f}m ÷ 推定階高 {floor_h:.2f}m（{fh_label}）≒ {n}階。")
            if h > 45.0:
                s_reason += "高層のため誤差が大きい。"
            cand_rows.append([bid, version, "storeys", str(n), None, None,
                              s_conf, "height", s_reason, [], False])
        else:
            # 高さ欠損 → 判断不能（行は残す：行が無いのとは別物）
            cand_rows.append([bid, version, "storeys", None, None, None,
                              0.0, "height", "高さが欠損のため階数を推定できない。",
                              [], True])

    if ev_rows:
        con.executemany(
            "INSERT INTO evidence (evidence_id, building_id, version, kind, label, detail) "
            "VALUES (?,?,?,?,?,?)", ev_rows)
    con.executemany(
        "INSERT OR REPLACE INTO candidates "
        "(building_id, version, attribute, value, value_min, value_max, "
        " confidence, source, reasoning, evidence_ids, unresolvable) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", cand_rows)

    return {"targets": len(tgt["building_id"]), "candidates": len(cand_rows),
            "evidence": len(ev_rows), "mixed_use": n_mixed, "floor_heights": fh}


# --- ホールドアウトでの階数精度 ---------------------------------------------

def evaluate_storeys(con, version: str = VERSION) -> dict:
    """ホールドアウトで階数の完全一致率と ±1階の率を出す（設計書 62.3%/92.1%）。

    階高は train から学習し、holdout で評価する。比較のため「用途別階高」と
    「一律3.8m」の両方を出す（設計書の 62.3/92.1 は一律3.8の数字）。
    """
    fh = learn_floor_heights(con, version, split="train")
    rows = con.execute(
        """
        SELECT b.usage, b.height_m, b.storeys_above
        FROM buildings b
        JOIN eval_split s ON b.building_id=s.building_id AND b.version=s.version
        WHERE b.version=? AND s.split='holdout'
          AND b.height_m IS NOT NULL AND b.storeys_above IS NOT NULL
          AND b.storeys_above >= 1
        """,
        [version],
    ).fetchall()

    def score(use_usage: bool, flat: float | None):
        exact = within1 = 0
        for usage, h, st in rows:
            if flat is not None:
                floor_h = flat
            else:
                floor_h, _ = floor_height_for(usage if use_usage else None, fh)
            pred = predict_storeys(h, floor_h)
            if pred == st:
                exact += 1
            if abs(pred - st) <= 1:
                within1 += 1
        n = len(rows)
        return exact / n * 100, within1 / n * 100

    flat_exact, flat_w1 = score(False, 3.8)
    byu_exact, byu_w1 = score(True, None)
    return {
        "n": len(rows),
        "flat_3.8": (flat_exact, flat_w1),
        "by_usage": (byu_exact, byu_w1),
        "floor_heights": fh,
    }


def _print_floor_heights(fh: dict) -> None:
    print("  学習した階高（median height/storeys）と設計書値の照合:")
    for u, design in DESIGN_FLOOR_HEIGHTS.items():
        got = fh["by_usage"].get(u)
        n = fh["n_by_usage"].get(u, 0)
        if got is None:
            print(f"    用途{u}: 学習なし / 設計 {design}m")
        else:
            print(f"    用途{u}: 実測 {got:.2f}m（n={n}）/ 設計 {design:.2f}m  差 {got-design:+.2f}")
    print(f"    全体中央値: {fh['overall']:.2f}m")


def run(do_eval: bool) -> None:
    con = connect()
    if do_eval:
        res = evaluate_storeys(con)
        print("=== 階数の精度（ホールドアウト n=%d）===" % res["n"])
        _print_floor_heights(res["floor_heights"])
        fe, fw = res["flat_3.8"]
        be, bw = res["by_usage"]
        print(f"\n  一律3.8m     : 完全一致 {fe:.1f}% / ±1階 {fw:.1f}%（設計書 62.3% / 92.1%）")
        print(f"  用途別階高   : 完全一致 {be:.1f}% / ±1階 {bw:.1f}%")
    else:
        res = run_estimate(con)
        print("=== 不明群への推定投入 ===")
        print(f"  不明群         : {res['targets']} 棟")
        print(f"  candidates 投入 : {res['candidates']} 行（usage=neighbor / storeys=height）")
        print(f"  evidence 投入   : {res['evidence']} 行（POI / OSM建物タグ）")
        print(f"  併用住宅フラグ  : {res['mixed_use']} 棟")
        _print_floor_heights(res["floor_heights"])
    con.close()


def main() -> None:
    p = argparse.ArgumentParser(description="階数・用途の仮推定を candidates に投入／評価する")
    p.add_argument("--eval", action="store_true", help="ホールドアウトで階数精度を出す（投入しない）")
    args = p.parse_args()
    run(args.eval)


if __name__ == "__main__":
    main()
