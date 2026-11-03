"""export の CLI。

例:
    uv run python -m export
    uv run python -m export --city-code 13104 --out-dir data/derived/export
"""
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from .update_candidates import CITY_CODE, VERSION, run


def main() -> None:
    load_dotenv()
    data_dir = os.environ.get("DATA_DIR", "./data")
    p = argparse.ArgumentParser(description="更新候補リスト（CSV/GeoJSON）を出力する")
    p.add_argument("--city-code", default=CITY_CODE, help="市区町村コード（既定 13104）")
    p.add_argument("--version", default=VERSION, help="年度版（既定 2025）")
    p.add_argument("--db", default=os.path.join(data_dir, "machikarte.duckdb"))
    p.add_argument("--out-dir", default=os.path.join(data_dir, "derived", "export"))
    args = p.parse_args()
    run(args.db, args.city_code, args.version, args.out_dir)


if __name__ == "__main__":
    main()
