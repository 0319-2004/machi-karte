"""DuckDB 接続とパス解決（API 共通）。

API は確認・確定を書き込むため、プロセスで1本の read-write 接続を共有する
（DuckDB は同一プロセス内で read_only と read_write を混在できないため）。
確認画面の同時操作はデモ規模なので、ロックで直列化すれば十分。

データ格納先は環境変数 DATA_DIR（既定 ./data）から解決する。
"""
from __future__ import annotations

import os
import threading

import duckdb

DATA_DIR = os.environ.get("DATA_DIR", "./data")
DB_PATH = os.path.join(DATA_DIR, "machikarte.duckdb")
DERIVED_DIR = os.path.join(DATA_DIR, "derived")

VERSION = "2025"

# プロセス共有の read-write 接続（遅延生成）とロック
_con: duckdb.DuckDBPyConnection | None = None
db_lock = threading.Lock()


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """スクリプト用：都度の接続（既定 read_only）。API は get_con() を使う。"""
    con = duckdb.connect(DB_PATH, read_only=read_only)
    con.execute("LOAD spatial;")
    return con


def get_con() -> duckdb.DuckDBPyConnection:
    """API 用：プロセス共有の read-write 接続。db_lock で保護して使う。"""
    global _con
    if _con is None:
        _con = duckdb.connect(DB_PATH, read_only=False)
        _con.execute("LOAD spatial;")
    return _con


def table_exists(con, name: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).fetchone()[0] > 0
