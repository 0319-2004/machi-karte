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


if __name__ == "__main__":
    run()
