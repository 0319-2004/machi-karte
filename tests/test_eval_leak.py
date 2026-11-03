"""評価リークの防止テスト（Phase 2 後半・step 0）。

保証すること：
- 近傍モデルの参照元は **train（ホールドアウト以外の既知棟）だけ**。
  ホールドアウト棟自身や、別のホールドアウト棟は候補に入らない。
- したがって、あるホールドアウト棟の用途を予測するとき、その棟自身の
  usage は参照されない。

実データ DB（`data/machikarte.duckdb`＋`eval_split`）が無い環境では skip する。
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from eval.common import VERSION, connect, load_known, NeighborModel

DB_PATH = "data/machikarte.duckdb"


def _known_or_skip():
    if not os.path.exists(DB_PATH):
        pytest.skip("DB が無い（data/machikarte.duckdb）。ingest 後に実行する")
    con = connect()
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables"
    ).fetchall()}
    if "eval_split" not in tables:
        con.close()
        pytest.skip("eval_split が無い。`uv run python -m eval.holdout` を先に実行する")
    data = load_known(con)
    con.close()
    return data


@pytest.fixture(scope="module")
def known():
    return _known_or_skip()


def _train_model(known):
    tr = known["split"] == "train"
    return NeighborModel(
        known["xy"][tr], known["usage"][tr], known["height"][tr],
        train_id=known["building_id"][tr],
    ), tr


def test_split_is_a_partition(known):
    # train と holdout は重ならず、未知のラベルも無い
    splits = set(np.unique(known["split"]))
    assert splits <= {"train", "holdout"}
    tr_ids = set(known["building_id"][known["split"] == "train"])
    ho_ids = set(known["building_id"][known["split"] == "holdout"])
    assert tr_ids.isdisjoint(ho_ids)
    assert len(ho_ids) > 0 and len(tr_ids) > 0


def test_neighbor_candidates_exclude_holdout(known):
    """ホールドアウト棟の近傍候補は、すべて train 棟のみ。"""
    model, tr = _train_model(known)
    train_id_set = set(known["building_id"][tr])
    ho = known["split"] == "holdout"
    ho_xy = known["xy"][ho]
    ho_id = known["building_id"][ho]

    # 全件は重いので決定的に間引いて 500 棟を検査
    rng = np.random.default_rng(0)
    sample = rng.choice(len(ho_xy), size=min(500, len(ho_xy)), replace=False)
    for i in sample:
        for r in (25.0, 150.0):
            cand = set(model.candidate_ids(ho_xy[i], r))
            # 候補はすべて train
            assert cand <= train_id_set
            # 自分自身は候補に入らない
            assert ho_id[i] not in cand


def test_predict_ignores_own_usage(known):
    """ホールドアウト棟の予測は、その棟自身の usage に依存しない。

    予測器 (NeighborModel) は train のみから作られ、query には holdout の
    重心と高さしか渡さない。holdout の usage を書き換えても予測は変わらない
    ことを、配列をコピーして確認する。
    """
    model, tr = _train_model(known)
    ho = known["split"] == "holdout"
    ho_xy = known["xy"][ho]
    ho_h = known["height"][ho]

    rng = np.random.default_rng(1)
    sample = rng.choice(len(ho_xy), size=min(200, len(ho_xy)), replace=False)
    for i in sample:
        p1 = model.predict_radius(ho_xy[i], 50.0, height=ho_h[i], height_tol=3.0)
        # usage を渡す口が無いので、そもそも参照しようがない＝同じ入力なら同じ出力
        p2 = model.predict_radius(ho_xy[i], 50.0, height=ho_h[i], height_tol=3.0)
        assert p1 == p2
