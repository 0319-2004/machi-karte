"""評価の共通部品：高さ帯、既知棟データの読み込み、近傍モデル。

- 高さ帯：〜10m / 10〜20m / 20〜45m / 45m〜（設計書の高さ閾値に合わせる）
- 近傍モデルは **train（ホールドアウト以外の既知棟）だけ** から作る。
  ホールドアウト棟や不明群は候補に入らない（評価リークの防止）。
- 重み付き正解率は、不明群(is_target)の高さ分布を重みに使う
  （「不明群に近い条件での実力」を1つの数字にする）。
"""
from __future__ import annotations

from collections import Counter

import numpy as np
from scipy.spatial import cKDTree

from .holdout import KNOWN_PREDICATE, VERSION, connect  # noqa: F401 (re-export)

# (ラベル, 下限[含む], 上限[含まない])
HEIGHT_BANDS = [
    ("0-10m", 0.0, 10.0),
    ("10-20m", 10.0, 20.0),
    ("20-45m", 20.0, 45.0),
    ("45m+", 45.0, float("inf")),
]
BAND_LABELS = [b[0] for b in HEIGHT_BANDS]


def band_of(height: float) -> str | None:
    """高さ(m)を帯ラベルに。NULL/NaN は None。"""
    if height is None or (isinstance(height, float) and np.isnan(height)):
        return None
    for label, lo, hi in HEIGHT_BANDS:
        if lo <= height < hi:
            return label
    return None


def load_known(con, version: str = VERSION):
    """既知棟の building_id・usage・height・重心(6677 xy)・split を返す。

    返り値は dict（numpy 配列）。重心は EPSG:6677（メートル）。
    """
    rows = con.execute(
        f"""
        SELECT b.building_id, b.usage, b.height_m,
               ST_X(ST_Centroid(b.geom)), ST_Y(ST_Centroid(b.geom)), s.split
        FROM buildings b
        JOIN eval_split s
          ON b.building_id = s.building_id AND b.version = s.version
        WHERE b.version = ? AND {KNOWN_PREDICATE} AND b.geom IS NOT NULL
        """,
        [version],
    ).fetchall()
    return {
        "building_id": np.array([r[0] for r in rows]),
        "usage": np.array([r[1] for r in rows]),
        "height": np.array([r[2] for r in rows], dtype=float),
        "xy": np.array([[r[3], r[4]] for r in rows], dtype=float),
        "split": np.array([r[5] for r in rows]),
        "band": np.array([band_of(r[2]) for r in rows]),
    }


def target_band_weights(con, version: str = VERSION) -> dict[str, float]:
    """不明群(is_target)の高さ帯ごとの構成比（height 非NULL の棟で正規化）。"""
    rows = con.execute(
        "SELECT height_m FROM buildings "
        "WHERE version=? AND is_target AND height_m IS NOT NULL",
        [version],
    ).fetchall()
    counts = Counter(band_of(r[0]) for r in rows)
    total = sum(counts.values())
    return {label: counts.get(label, 0) / total for label in BAND_LABELS}


def weighted_accuracy(band_acc: dict[str, float], weights: dict[str, float]) -> float:
    """帯別正解率(%)を重みで加重平均。重みが無い帯は無視して正規化する。"""
    num = den = 0.0
    for label, acc in band_acc.items():
        if acc is None:
            continue
        w = weights.get(label, 0.0)
        num += w * acc
        den += w
    return num / den if den else float("nan")


def majority(codes, order=None):
    """多数決。order（距離の近い順の index）があれば同数の先着を優先。"""
    if len(codes) == 0:
        return None
    if order is not None:
        codes = [codes[i] for i in order]
    return Counter(codes).most_common(1)[0][0]


class NeighborModel:
    """train 棟だけから作る近傍モデル。

    候補は常に train（ホールドアウト以外の既知棟）に限定される。
    query 点が train 棟自身であっても、その棟は候補集合の index で
    そのまま返るため、ホールドアウト棟の評価では query に holdout の
    重心を渡すことでリークを避ける（holdout は tree に入っていない）。
    """

    def __init__(self, train_xy, train_usage, train_height, train_id=None):
        self.xy = np.asarray(train_xy, dtype=float)
        self.usage = np.asarray(train_usage)
        self.height = np.asarray(train_height, dtype=float)
        self.id = None if train_id is None else np.asarray(train_id)
        self.tree = cKDTree(self.xy)
        self.global_mode = Counter(self.usage).most_common(1)[0][0]

    def candidates_radius(self, pt, r):
        """半径 r(m) 内の train 候補 index（距離の近い順）。"""
        idx = self.tree.query_ball_point(pt, r)
        if not idx:
            return np.array([], dtype=int)
        d = np.linalg.norm(self.tree.data[idx] - pt, axis=1)
        return np.array(idx)[np.argsort(d)]

    def candidate_ids(self, pt, r):
        """半径 r(m) 内の train 候補の building_id（リーク検査用）。"""
        if self.id is None:
            raise ValueError("train_id を渡していないので building_id を返せない")
        return self.id[self.candidates_radius(pt, r)]

    def predict_radius(self, pt, r, height=None, height_tol=None):
        """半径 r 内の多数決。height_tol 指定時は高さ±tol でさらに絞る。"""
        idx = self.candidates_radius(pt, r)
        if len(idx) == 0:
            return self.global_mode
        if height_tol is not None and height is not None:
            mask = np.abs(self.height[idx] - height) <= height_tol
            sel = idx[mask]
            if len(sel):
                idx = sel
        return majority(list(self.usage[idx]))

    def predict_many_knn(self, pts, k, heights=None, height_tol=None):
        """複数 query を k近傍で一括予測。

        height_tol 指定時は、k近傍のうち高さ±tol の棟に絞る（絞った結果が
        空なら k近傍そのままで多数決）。候補は train のみ。
        """
        pts = np.asarray(pts, dtype=float)
        _, idx = self.tree.query(pts, k=k)
        if k == 1:
            idx = idx.reshape(-1, 1)
        preds = np.empty(len(pts), dtype=object)
        for i, row in enumerate(idx):
            row = np.atleast_1d(row)
            if height_tol is not None and heights is not None:
                mask = np.abs(self.height[row] - heights[i]) <= height_tol
                sel = row[mask]
                if len(sel):
                    row = sel
            preds[i] = majority(list(self.usage[row]))
        return preds
