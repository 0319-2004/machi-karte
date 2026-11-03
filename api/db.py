"""DuckDB 接続とパス解決（API 共通）。

DB は読み取り専用で開く（API は確認・確定の書き込みを Phase 4 で別経路にする）。
データ格納先は環境変数 DATA_DIR（既定 ./data）から解決する。
"""
from __future__ import annotations

import os

import duckdb

DATA_DIR = os.environ.get("DATA_DIR", "./data")
DB_PATH = os.path.join(DATA_DIR, "machikarte.duckdb")
DERIVED_DIR = os.path.join(DATA_DIR, "derived")

VERSION = "2025"


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(DB_PATH, read_only=read_only)
    con.execute("LOAD spatial;")
    return con


def table_exists(con, name: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).fetchone()[0] > 0
