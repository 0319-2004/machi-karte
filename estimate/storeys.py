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
from eval.common import (BAND_LABELS, NeighborModel, band_of, connect,
                         load_known, target_band_weights, weighted_accuracy)
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
    """階数（高さ÷階高）の信頼度：<20m=高 / 20〜45m=中 / 45m超=低。

    帯別の精度（decisions.md 2026-11-04）に合わせる。±1階率は <20m で 94〜97%、
    20〜45m で 67%、45m超で 40% まで落ちるため、20m以上は候補扱いに下げる。
    画面ラベル（api.confidence_label）は 0.8→高 / 0.5→中 / 0.3→低 に対応。
    """
    if height is None or np.isnan(height):
        return 0.0
    if height > 45.0:
        return 0.3      # 低（±1階でも約40%）
    if height >= 20.0:
        return 0.5      # 中（20〜45m：完全一致27%・±1階67%）
    return 0.8          # 高（<20m：±1階94〜97%）


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
                s_reason += "高層（45m超）は階高のばらつきが大きく、階数は候補（±1階でも外れやすい）。"
            elif h >= 20.0:
                s_reason += "高さ20m以上は階高のばらつきが大きく、階数は目安（±1階程度）。"
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

def _pct(num: int, den: int):
    return (num / den * 100) if den else None


def _band_score(rows, floor_fn) -> dict:
    """行ごとに floor_fn(i, usage, height)→階高 で予測し、帯別と全体の
    完全一致率・±1階率を返す。floor_fn が返す階高で height÷階高を丸める。"""
    agg = {label: [0, 0, 0] for label in BAND_LABELS}    # [exact, within1, n]
    tot = [0, 0, 0]
    for i, (usage, h, st, *_rest) in enumerate(rows):
        pred = predict_storeys(h, floor_fn(i, usage, h))
        e = 1 if pred == st else 0
        w = 1 if abs(pred - st) <= 1 else 0
        band = band_of(h)
        if band in agg:
            agg[band][0] += e; agg[band][1] += w; agg[band][2] += 1
        tot[0] += e; tot[1] += w; tot[2] += 1
    band_exact = {b: _pct(agg[b][0], agg[b][2]) for b in BAND_LABELS}
    band_w1 = {b: _pct(agg[b][1], agg[b][2]) for b in BAND_LABELS}
    band_n = {b: agg[b][2] for b in BAND_LABELS}
    return {
        "band_exact": band_exact, "band_w1": band_w1, "band_n": band_n,
        "overall": (_pct(tot[0], tot[2]), _pct(tot[1], tot[2])),
    }


def evaluate_storeys(con, version: str = VERSION) -> dict:
    """ホールドアウトで階数の精度を**高さ帯別**に出す（設計書 62.3%/92.1% と照合）。

    階高は train から学習し、holdout 全体で評価する（リーク防止）。仮推定（高さ÷階高）を
    3つの階高の当て方で比較する:
      - flat3.8      : 一律 3.8m（設計書の 62.3/92.1 はこれ）
      - 用途別・真   : holdout 棟の真の用途の階高（上限の目安。不明群では使えない）
      - 不明群相当   : 近傍(train)で予測した用途の階高（= 本番 run_estimate と同じ当て方）
    各方式で帯別と、不明群の高さ分布で重み付けした値を出す。
    """
    fh = learn_floor_heights(con, version, split="train")
    rows = con.execute(
        """
        SELECT b.usage, b.height_m, b.storeys_above,
               ST_X(ST_Centroid(b.geom)), ST_Y(ST_Centroid(b.geom))
        FROM buildings b
        JOIN eval_split s ON b.building_id=s.building_id AND b.version=s.version
        WHERE b.version=? AND s.split='holdout' AND b.geom IS NOT NULL
          AND b.height_m IS NOT NULL AND b.storeys_above IS NOT NULL
          AND b.storeys_above >= 1
        """,
        [version],
    ).fetchall()

    # 近傍（train のみ）で holdout 棟の用途を予測 → 本番と同じ「不明群相当」の当て方
    known = load_known(con, version)
    tr = known["split"] == "train"
    model = NeighborModel(known["xy"][tr], known["usage"][tr], known["height"][tr],
                          known["building_id"][tr])
    xy = np.array([[r[3], r[4]] for r in rows], dtype=float)
    heights = np.array([r[1] for r in rows], dtype=float)
    pred_usage = model.predict_many_knn(xy, NEIGHBOR_K, heights=heights,
                                         height_tol=HEIGHT_TOL)

    weights = target_band_weights(con, version)

    methods = {
        "flat3.8": _band_score(rows, lambda i, u, h: 3.8),
        "用途別・真": _band_score(rows, lambda i, u, h: floor_height_for(u, fh)[0]),
        "不明群相当": _band_score(rows, lambda i, u, h: floor_height_for(pred_usage[i], fh)[0]),
    }
    for m in methods.values():
        m["w_exact"] = weighted_accuracy(m["band_exact"], weights)
        m["w_w1"] = weighted_accuracy(m["band_w1"], weights)

    # --- 原因分析 ---------------------------------------------------------
    # (a) 実際の階高（height/storeys, クリップ無し）の帯別中央値と、適用した階高の差
    by_band_actual = {b: [] for b in BAND_LABELS}
    for usage, h, st, *_ in rows:
        band = band_of(h)
        if band in by_band_actual:
            by_band_actual[band].append(h / st)
    actual_fh = {
        b: {"median": float(np.median(v)) if v else None, "n": len(v)}
        for b, v in by_band_actual.items()
    }
    # (b) 「不明群相当」で、近傍予測用途の階高が学習済みに無く全体中央値へ落ちた割合（帯別）
    fb = {b: [0, 0] for b in BAND_LABELS}     # [fallback, n]
    for i, (usage, h, st, *_) in enumerate(rows):
        band = band_of(h)
        pu = pred_usage[i]
        used_fallback = not (pu is not None and pu in fh["by_usage"])
        if band in fb:
            fb[band][1] += 1
            if used_fallback:
                fb[band][0] += 1
    fallback = {b: _pct(fb[b][0], fb[b][1]) for b in BAND_LABELS}

    return {
        "n": len(rows), "weights": weights, "methods": methods,
        "actual_fh": actual_fh, "fallback": fallback, "floor_heights": fh,
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


def _p(x) -> str:
    return "  --  " if x is None else f"{x:5.1f}%"


def _print_method(label: str, m: dict, note: str = "") -> None:
    print(f"\n  [{label}]{note}")
    print(f"    {'帯':<8}{'完全一致':>8}{'±1階':>8}{'n':>8}")
    for b in BAND_LABELS:
        print(f"    {b:<8}{_p(m['band_exact'][b]):>8}{_p(m['band_w1'][b]):>8}"
              f"{m['band_n'][b]:>8}")
    oe, ow = m["overall"]
    print(f"    {'全体':<8}{_p(oe):>8}{_p(ow):>8}")
    print(f"    {'不明群重み':<7}{_p(m['w_exact']):>8}{_p(m['w_w1']):>8}")


def _print_eval(res: dict) -> None:
    print("=== 階数の精度（ホールドアウト n=%d, 高さ帯別）===" % res["n"])
    _print_floor_heights(res["floor_heights"])
    w = res["weights"]
    print("\n  不明群の高さ分布（重み）: "
          + " / ".join(f"{b} {w.get(b, 0)*100:.1f}%" for b in BAND_LABELS))
    _print_method("flat3.8（設計書 62.3/92.1 はこの方式）", res["methods"]["flat3.8"])
    _print_method("用途別階高・真の用途（上限の目安／不明群では使えない）",
                  res["methods"]["用途別・真"])
    _print_method("不明群相当（近傍予測用途の階高＝本番 run_estimate と同じ）",
                  res["methods"]["不明群相当"])

    print("\n  === 20m以上で落ちる原因 ===")
    print("  実際の階高（中央値 height/storeys, クリップ無し）と一律3.8mの差:")
    print(f"    {'帯':<8}{'実測階高':>9}{'3.8との差':>10}{'n':>8}")
    for b in BAND_LABELS:
        a = res["actual_fh"][b]
        if a["median"] is None:
            print(f"    {b:<8}{'--':>9}{'--':>10}{0:>8}")
        else:
            print(f"    {b:<8}{a['median']:8.2f}m{a['median']-3.8:+9.2f}m{a['n']:>8}")
    print("  近傍予測用途の階高が全体中央値へフォールバックした割合（帯別）:")
    for b in BAND_LABELS:
        print(f"    {b:<8}{_p(res['fallback'][b])}")


def run(do_eval: bool) -> None:
    con = connect()
    if do_eval:
        res = evaluate_storeys(con)
        _print_eval(res)
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
