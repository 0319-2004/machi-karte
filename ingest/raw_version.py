"""版別の生 ingest（buildings を壊さない別テーブル版）。

設計書「新しく現れた建物の検出」のために、2023年度版を 2025 の `buildings`
とは別のテーブル（既定 `buildings_2023`）へ取り込む。センチネル正規化・外形
（LOD0 RoofEdge, 6677/4326）は 2025 と同じ処理（pipeline.load_version を再利用）。

不明群判定・targets 出力・設計書値の照合（57,780/4,982 など 2025 前提）は行わない。
版間差分の分析と候補投入は estimate/version_diff.py が担う。

使い方:
    uv run python -m ingest.raw_version --version 2023
    uv run python -m ingest.raw_version --version 2023 --table buildings_2023
"""
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from .pipeline import connect, load_version


def run(data_dir: str, version: str, db_path: str, table: str) -> int:
    con = connect(db_path, table)
    print(f"=== raw ingest version={version} → table={table} ===")
    n = load_version(con, data_dir, version, table)
    geom_ok = con.execute(
        f"SELECT count(*) FROM {table} WHERE version=? AND geom IS NOT NULL",
        [version],
    ).fetchone()[0]
    cdates = con.execute(
        f"SELECT creation_date, count(*) c FROM {table} WHERE version=? "
        f"GROUP BY creation_date ORDER BY c DESC LIMIT 6",
        [version],
    ).fetchall()
    con.close()
    print(f"\n読み込み完了: {n} 棟（外形あり {geom_ok}） → {table}")
    print("  creationDate 分布:")
    for d, c in cdates:
        print(f"    {d}: {c}")
    return n


def main() -> None:
    load_dotenv()
    data_dir = os.environ.get("DATA_DIR", "./data")
    p = argparse.ArgumentParser(description="PLATEAU CityGML を版別テーブルに取り込む")
    p.add_argument("--version", default="2023", help="年度版（既定 2023）")
    p.add_argument("--data-dir", default=data_dir)
    p.add_argument("--db", default=None)
    p.add_argument("--table", default=None, help="取り込み先テーブル（既定 buildings_<version>）")
    args = p.parse_args()

    db_path = args.db or os.path.join(args.data_dir, "machikarte.duckdb")
    table = args.table or f"buildings_{args.version}"
    run(args.data_dir, args.version, db_path, table)


if __name__ == "__main__":
    main()
