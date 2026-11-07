"""既知棟の定義とホールドアウト分割（設計書「計測と検証」）。

既知棟の定義：
    usage IS NOT NULL（＝461以外の既知用途）かつ height_m IS NOT NULL
これは設計書の「既知 52,161 棟」と一致する。usage だけなら 52,798 だが、
そのうち 637 棟は height=-9999（高さ欠損）で、高さベースラインに使えない。

ホールドアウトは seed 固定で 20%。後のフェーズでも同じ分割を使い回すため、
DuckDB の eval_split テーブルに保存する。
"""
from __future__ import annotations

import os
import random

import duckdb

# DB のパスは DATA_DIR から解決する（api/db.py と同じ約束。既定 ./data）。
DATA_DIR = os.environ.get("DATA_DIR", "./data")
DB_PATH = os.path.join(DATA_DIR, "machikarte.duckdb")
VERSION = "2025"
SEED = 42
HOLDOUT_FRAC = 0.20

# 既知棟の述語（buildings に対して）
KNOWN_PREDICATE = "usage IS NOT NULL AND height_m IS NOT NULL"


def connect(db_path: str = DB_PATH) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path)
    con.execute("LOAD spatial;")
    return con


def known_count(con, version: str = VERSION) -> int:
    return con.execute(
        f"SELECT count(*) FROM buildings WHERE version=? AND {KNOWN_PREDICATE}",
        [version],
    ).fetchone()[0]


def report_definition(con, version: str = VERSION) -> None:
    def c(pred):
        return con.execute(
            f"SELECT count(*) FROM buildings WHERE version=? AND {pred}", [version]
        ).fetchone()[0]

    usage_known = c("usage IS NOT NULL")
    both_known = c(KNOWN_PREDICATE)
    height_missing = c("usage IS NOT NULL AND height_m IS NULL")
    print("=== 既知棟の定義と件数（version=%s）===" % version)
    print(f"  usage 既知(461以外)              : {usage_known}")
    print(f"  usage 既知 かつ height 既知       : {both_known}  ← 採用（既知棟）")
    print(f"  usage 既知 かつ height 欠損(-9999): {height_missing}")
    print(f"  設計書 52,161 との一致           : {'一致' if both_known == 52161 else '相違'}")
    print(f"  57,780 - 4,982 = 52,798 との差    : {usage_known - both_known} 棟 = 高さ欠損の既知棟")


def build_split(con, version: str = VERSION, frac: float = HOLDOUT_FRAC,
                seed: int = SEED) -> tuple[int, int]:
    """既知棟を train/holdout に分けて eval_split に保存する。"""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS eval_split (
            building_id VARCHAR,
            version     VARCHAR,
            split       VARCHAR,   -- 'train' | 'holdout'
            PRIMARY KEY (building_id, version)
        )
        """
    )
    con.execute("DELETE FROM eval_split WHERE version=?", [version])

    ids = [r[0] for r in con.execute(
        f"SELECT building_id FROM buildings WHERE version=? AND {KNOWN_PREDICATE} "
        f"ORDER BY building_id", [version]
    ).fetchall()]
    rng = random.Random(seed)
    rng.shuffle(ids)
    n_holdout = int(round(len(ids) * frac))
    holdout = set(ids[:n_holdout])

    rows = [[bid, version, "holdout" if bid in holdout else "train"] for bid in ids]
    con.executemany("INSERT INTO eval_split VALUES (?,?,?)", rows)
    return len(ids) - n_holdout, n_holdout


def run() -> None:
    con = connect()
    report_definition(con)
    n_train, n_holdout = build_split(con)
    print("\n=== ホールドアウト分割（seed=%d, %.0f%%）===" % (SEED, HOLDOUT_FRAC * 100))
    print(f"  train  : {n_train}")
    print(f"  holdout: {n_holdout}")
    print(f"  eval_split テーブルに保存（version={VERSION}）")
    con.close()


if __name__ == "__main__":
    run()
