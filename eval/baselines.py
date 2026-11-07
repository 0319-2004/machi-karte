"""用途推定のベースライン（POI結合なし。設計書「計測と検証」）。

3段階を、既知棟のホールドアウトで実測する：
    (a) 最頻値            … train の最頻用途を一律予測
    (b) 近傍の用途        … 近傍 train 棟の多数決
    (c) 近傍 ＋ 高さ±3m   … 近傍のうち高さが ±3m の棟だけで多数決

近傍の定義（半径メートル / k近傍）は複数試し、設計書の 43.1 / 49.5 / 56.8%
に一番近いものを採用する。距離は EPSG:6677（メートル）の重心間距離。
"""
from __future__ import annotations

from collections import Counter

import numpy as np
from scipy.spatial import cKDTree

from .holdout import KNOWN_PREDICATE, VERSION, connect

RADII = [25, 50, 100, 150, 200]      # メートル
KS = [1, 3, 5, 10, 20]               # k近傍
HEIGHT_TOL = 3.0                     # 高さ許容（m）
TARGET_B = 49.5
TARGET_C = 56.8


def _load(con, version=VERSION):
    """既知棟の usage・height・重心(6677)・split を numpy で返す。"""
    rows = con.execute(
        f"""
        SELECT b.building_id, b.usage, b.height_m,
               ST_X(ST_Centroid(b.geom)), ST_Y(ST_Centroid(b.geom)), s.split
        FROM buildings b
        JOIN eval_split s ON b.building_id = s.building_id AND b.version = s.version
        WHERE b.version = ? AND {KNOWN_PREDICATE} AND b.geom IS NOT NULL
        """,
        [version],
    ).fetchall()
    usage = np.array([r[1] for r in rows])
    height = np.array([r[2] for r in rows], dtype=float)
    xy = np.array([[r[3], r[4]] for r in rows], dtype=float)
    split = np.array([r[5] for r in rows])
    return usage, height, xy, split


def _majority(codes, order=None):
    """多数決。同数は order（距離の近い順）での先着を優先。"""
    if len(codes) == 0:
        return None
    if order is not None:
        codes = [codes[i] for i in order]
    return Counter(codes).most_common(1)[0][0]


def _neighbors_radius(tree, pt, r):
    idx = tree.query_ball_point(pt, r)
    if not idx:
        return np.array([], dtype=int)
    # 距離の近い順に並べる（同数の多数決を安定させる）
    d = np.linalg.norm(tree.data[idx] - pt, axis=1)
    return np.array(idx)[np.argsort(d)]


def run() -> None:
    con = connect()
    usage, height, xy, split = _load(con)
    tr = split == "train"
    ho = split == "holdout"
    tr_usage, tr_height, tr_xy = usage[tr], height[tr], xy[tr]
    ho_usage, ho_height, ho_xy = usage[ho], height[ho], xy[ho]
    tree = cKDTree(tr_xy)
    global_mode = Counter(tr_usage).most_common(1)[0][0]
    n = len(ho_usage)

    print("=== ベースライン（既知棟 holdout=%d, train=%d）===" % (n, tr.sum()))

    # (a) 最頻値
    acc_a = float(np.mean(ho_usage == global_mode)) * 100
    print(f"\n(a) 最頻値 [{global_mode}]  正解率 = {acc_a:.1f}%  （設計書 43.1%）")

    # (b) 近傍の用途：複数定義を試す
    print("\n(b) 近傍の用途（設計書 49.5%）")
    print(f"  {'定義':<14}{'正解率':>8}{'近傍あり率':>10}")
    results_b = []
    pred_cache = {}  # 定義 -> 各holdoutの近傍idx（(c)で再利用）
    for r in RADII:
        preds = np.empty(n, dtype=object)
        cov = 0
        neigh_idx = []
        for i in range(n):
            idx = _neighbors_radius(tree, ho_xy[i], r)
            neigh_idx.append(idx)
            if len(idx):
                cov += 1
                preds[i] = _majority(list(tr_usage[idx]))
            else:
                preds[i] = global_mode
        acc = float(np.mean(preds == ho_usage)) * 100
        results_b.append((f"半径{r}m", acc))
        pred_cache[f"半径{r}m"] = neigh_idx
        print(f"  {'半径%dm'%r:<14}{acc:>7.1f}%{cov/n*100:>9.1f}%")
    for k in KS:
        _, idxs = tree.query(ho_xy, k=k)
        idxs = np.atleast_2d(idxs.T).T if k == 1 else idxs
        if k == 1:
            idxs = idxs.reshape(n, 1)
        preds = np.array([_majority(list(tr_usage[row])) for row in idxs])
        acc = float(np.mean(preds == ho_usage)) * 100
        results_b.append((f"k={k}", acc))
        pred_cache[f"k={k}"] = [np.array(row).reshape(-1) for row in idxs]
        print(f"  {'k=%d'%k:<14}{acc:>7.1f}%{'(常にあり)':>12}")
    best_b = min(results_b, key=lambda t: abs(t[1] - TARGET_B))
    print(f"  → 採用: {best_b[0]}（{best_b[1]:.1f}%、設計書49.5%に最接近）")

    # (c) 近傍 ＋ 高さ±3m：同じ近傍定義に高さフィルタを足す
    print("\n(c) 近傍 ＋ 高さ±%gm（設計書 56.8%%）" % HEIGHT_TOL)
    print(f"  {'定義':<14}{'正解率':>8}")
    results_c = []
    for name, neigh in pred_cache.items():
        preds = np.empty(n, dtype=object)
        for i in range(n):
            idx = np.asarray(neigh[i])
            if len(idx):
                mask = np.abs(tr_height[idx] - ho_height[i]) <= HEIGHT_TOL
                sel = idx[mask]
                if len(sel):
                    preds[i] = _majority(list(tr_usage[sel]))
                else:
                    preds[i] = _majority(list(tr_usage[idx]))
            else:
                preds[i] = global_mode
        acc = float(np.mean(preds == ho_usage)) * 100
        results_c.append((name, acc))
        print(f"  {name:<14}{acc:>7.1f}%")
    best_c = min(results_c, key=lambda t: abs(t[1] - TARGET_C))
    print(f"  → 採用: {best_c[0]}（{best_c[1]:.1f}%、設計書56.8%に最接近）")

    # 不明群の近傍にある既知棟の数の分布（近傍法が不明群でどれだけ使えるか）
    print("\n=== 不明群(is_target)の近傍にある既知棟の数の分布 ===")
    known_tree = cKDTree(xy)  # 既知棟全体
    tgt = con.execute(
        "SELECT ST_X(ST_Centroid(geom)), ST_Y(ST_Centroid(geom)) "
        "FROM buildings WHERE version=? AND is_target AND geom IS NOT NULL",
        [VERSION],
    ).fetchall()
    tgt_xy = np.array(tgt, dtype=float)
    for r in (50, 100):
        counts = np.array([len(known_tree.query_ball_point(p, r)) for p in tgt_xy])
        zero = int(np.sum(counts == 0))
        print(f"  半径{r}m: 中央値={np.median(counts):.0f} 平均={counts.mean():.1f} "
              f"最大={counts.max()} / 既知棟0件の不明群={zero}（{zero/len(counts)*100:.1f}%）")

    con.close()
    return {"a": acc_a, "b": results_b, "best_b": best_b,
            "c": results_c, "best_c": best_c}


# 高さ帯別ベースラインで使う近傍定義（k近傍。常に候補があり帯で空にならない）
BAND_NEIGHBOR_K = 5


def run_bands(k: int = BAND_NEIGHBOR_K, con=None) -> dict:
    """ベースライン (a)(b)(c) を高さ帯別に出す。

    - (a) 最頻値 / (b) 近傍の用途（k近傍）/ (c) 近傍＋高さ±3m
    - 帯ごとに holdout 棟数・不明群(is_target)棟数・正解率を並べる
    - 不明群の高さ分布で重み付けした正解率（「不明群に近い条件での実力」）を1行

    con を渡すとその接続を使う（レポート生成で1接続に束ねるため。既定は自前接続）。
    """
    from .common import (BAND_LABELS, NeighborModel, band_of, load_known,
                         target_band_weights, weighted_accuracy)

    owns_con = con is None            # 自分で開いた接続だけを閉じる（渡された con は閉じない）
    con = con or connect()
    d = load_known(con)
    tr = d["split"] == "train"
    ho = d["split"] == "holdout"
    model = NeighborModel(d["xy"][tr], d["usage"][tr], d["height"][tr],
                          train_id=d["building_id"][tr])

    ho_usage = d["usage"][ho]
    ho_xy = d["xy"][ho]
    ho_height = d["height"][ho]
    ho_band = d["band"][ho]

    pred_a = np.array([model.global_mode] * len(ho_usage), dtype=object)
    pred_b = model.predict_many_knn(ho_xy, k)
    pred_c = model.predict_many_knn(ho_xy, k, heights=ho_height, height_tol=HEIGHT_TOL)

    weights = target_band_weights(con)
    # 不明群の帯別棟数（height 非NULL）
    tgt_counts = {b: 0 for b in BAND_LABELS}
    tgt_rows = con.execute(
        "SELECT height_m FROM buildings WHERE version=? AND is_target "
        "AND height_m IS NOT NULL", [VERSION]
    ).fetchall()
    for r in tgt_rows:
        b = band_of(r[0])
        if b:
            tgt_counts[b] += 1

    print("\n=== ベースライン 高さ帯別（既知棟 holdout, 近傍 k=%d）===" % k)
    print(f"  {'帯':<9}{'holdout':>8}{'不明群':>8}"
          f"{'(a)最頻':>9}{'(b)近傍':>9}{'(c)+高さ':>9}")
    band_acc = {"a": {}, "b": {}, "c": {}}
    for label in BAND_LABELS:
        m = ho_band == label
        n = int(m.sum())
        if n == 0:
            continue
        aa = float(np.mean(pred_a[m] == ho_usage[m])) * 100
        ab = float(np.mean(pred_b[m] == ho_usage[m])) * 100
        ac = float(np.mean(pred_c[m] == ho_usage[m])) * 100
        band_acc["a"][label] = aa
        band_acc["b"][label] = ab
        band_acc["c"][label] = ac
        print(f"  {label:<9}{n:>8}{tgt_counts[label]:>8}"
              f"{aa:>8.1f}%{ab:>8.1f}%{ac:>8.1f}%")

    # 全体（参考）
    for key, pred in (("a", pred_a), ("b", pred_b), ("c", pred_c)):
        band_acc[key]["_all"] = float(np.mean(pred == ho_usage)) * 100
    print(f"  {'全体':<9}{len(ho_usage):>8}{sum(tgt_counts.values()):>8}"
          f"{band_acc['a']['_all']:>8.1f}%{band_acc['b']['_all']:>8.1f}%"
          f"{band_acc['c']['_all']:>8.1f}%")

    # 不明群の高さ分布で重み付け（_all は除いて帯だけで加重）
    def _wacc(key):
        bands_only = {k2: v for k2, v in band_acc[key].items() if k2 != "_all"}
        return weighted_accuracy(bands_only, weights)
    wa, wb, wc = _wacc("a"), _wacc("b"), _wacc("c")
    print(f"  {'不明群重み':<9}{'':>8}{'':>8}{wa:>8.1f}%{wb:>8.1f}%{wc:>8.1f}%")
    print("  （重み＝不明群の高さ帯構成比：" +
          " ".join(f"{b}={weights[b]*100:.0f}%" for b in BAND_LABELS) + "）")

    if owns_con:
        con.close()
    return {"band_acc": band_acc, "weights": weights,
            "weighted": {"a": wa, "b": wb, "c": wc}, "tgt_counts": tgt_counts}


if __name__ == "__main__":
    run()
    run_bands()
