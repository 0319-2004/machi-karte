.PHONY: ingest serve geojson eval export test

# CityGML を解析し DuckDB に取り込む（版は VERSION で指定、既定 2025）
VERSION ?= 2025
ingest:
	@echo "TODO: uv run python -m ingest --version $(VERSION)"

# 地図UI 用の建物ポリゴン GeoJSON を生成する（data/derived/buildings.geojson）
geojson:
	uv run python -m api.build_geojson

# FastAPI を起動する（http://localhost:8000/ 。初回は geojson を自動生成）
serve:
	uv run uvicorn api.main:app --reload --port 8000

# 既知棟での精度検証を実行する
eval:
	@echo "TODO: uv run python -m eval"

# 更新候補リスト（CSV / GeoJSON）を出力する
export:
	@echo "TODO: uv run python -m export"

# pytest を実行する
test:
	@echo "TODO: uv run pytest"
