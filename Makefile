.PHONY: ingest serve eval export test

# CityGML を解析し DuckDB に取り込む（版は VERSION で指定、既定 2025）
VERSION ?= 2025
ingest:
	@echo "TODO: uv run python -m ingest --version $(VERSION)"

# FastAPI を起動する
serve:
	@echo "TODO: uv run uvicorn api.main:app --reload"

# 既知棟での精度検証を実行する
eval:
	@echo "TODO: uv run python -m eval"

# 更新候補リスト（CSV / GeoJSON）を出力する
export:
	@echo "TODO: uv run python -m export"

# pytest を実行する
test:
	@echo "TODO: uv run pytest"
