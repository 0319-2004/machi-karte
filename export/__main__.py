"""export の CLI。

例:
    uv run python -m export
    uv run python -m export --city-code 13104 --out-dir data/derived/export
"""
from __future__ import annotations

import argparse
import os
import sys

import duckdb
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
    try:
        run(args.db, args.city_code, args.version, args.out_dir)
    except duckdb.IOException as e:
        if "lock" in str(e).lower():
            print("⚠ DB がロックされています。API サーバー（uvicorn）が起動中は CLI で出力"
                  "できません。\n   → サーバーを止めてから再実行するか、画面の「更新候補を"
                  "ダウンロード」（/api/export.csv・/api/export.geojson）を使ってください。",
                  file=sys.stderr)
            sys.exit(1)
        raise


if __name__ == "__main__":
    main()
