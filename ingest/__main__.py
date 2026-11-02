"""ingest の CLI。

例:
    uv run python -m ingest --version 2025
    uv run python -m ingest --version 2023   # Phase 5（版間差分）用
"""
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from .pipeline import run


def main() -> None:
    load_dotenv()
    data_dir = os.environ.get("DATA_DIR", "./data")

    p = argparse.ArgumentParser(description="PLATEAU CityGML を DuckDB に取り込む")
    p.add_argument("--version", default="2025", help="年度版（既定 2025）")
    p.add_argument("--data-dir", default=data_dir, help="DATA_DIR（既定は環境変数）")
    p.add_argument("--db", default=None, help="DuckDB パス（既定 <DATA_DIR>/machikarte.duckdb）")
    p.add_argument("--targets-out", default=None,
                   help="不明群 GeoJSON の出力先（既定 <DATA_DIR>/derived/targets.geojson）")
    args = p.parse_args()

    db_path = args.db or os.path.join(args.data_dir, "machikarte.duckdb")
    targets_out = args.targets_out or os.path.join(args.data_dir, "derived", "targets.geojson")

    summary = run(args.data_dir, args.version, db_path, targets_out)
    if not summary["all_ok"]:
        print("\n※ 設計書値と一部相違あり。docs/decisions.md に記録して確認すること。")


if __name__ == "__main__":
    main()
