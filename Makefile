.PHONY: ingest ingest-2023 schema estimate version-diff geojson serve eval export test \
        reset-confirmations pipeline claude-infer claude-eval

# CityGML を解析し DuckDB に取り込む（版は VERSION で指定、既定 2025）
VERSION ?= 2025
ingest:
	uv run python -m ingest --version $(VERSION)

# 2023年度版を別テーブル（buildings_2023）に取り込む（版間差分用。buildings は触らない）
ingest-2023:
	uv run python -m ingest.raw_version --version 2023

# 推定・確定・根拠・計測のテーブルを用意する（buildings は触らない）
schema:
	uv run python -m api.schema

# 仮の推定（階数＝高さ÷用途別階高、用途＝近傍＋高さ±3m）を candidates に投入
estimate:
	uv run python -m estimate.storeys

# 版間差分で新規棟を検出し、year_built 候補（source='version_diff'）を投入
version-diff:
	uv run python -m estimate.version_diff

# 地図UI 用の建物ポリゴン GeoJSON を生成する（data/derived/buildings.geojson）
geojson:
	uv run python -m api.build_geojson

# FastAPI を起動する（http://localhost:8000/ 。初回は geojson を自動生成）
serve:
	uv run uvicorn api.main:app --reload --port 8000

# 既知棟での精度検証（階数の完全一致/±1階、用途ベースライン）
eval:
	uv run python -m eval.holdout
	uv run python -m estimate.storeys --eval
	uv run python -m eval.baselines

# Claude API で不明群の用途・階数を推定し candidates(source='image') に投入
# 費用注意：既定20棟。全件は LIMIT=--all、モデルは CLAUDE_MODEL（.env）
CLAUDE_LIMIT ?= 20
claude-infer:
	uv run python -m estimate.claude_infer --limit $(CLAUDE_LIMIT)

# Claude 推定の既知棟検証（先にこれ）。用途 vs 近傍(c) は高さ20m以上で測る
# 2モデル比較は make claude-eval MODELS=claude-opus-5-5,claude-haiku-5-5
CLAUDE_EVAL_N ?= 30
claude-eval:
	uv run python -m eval.claude_eval --n $(CLAUDE_EVAL_N) --min-height 20 \
		$(if $(MODELS),--models $(MODELS),)

# 更新候補リスト（CSV / GeoJSON / README）を出力する（確定済みのみ）
# 他自治体は CITY_CODE を変える：make export CITY_CODE=13104
CITY_CODE ?= 13104
export:
	uv run python -m export --city-code $(CITY_CODE)

# pytest を実行する
test:
	uv run pytest

# 確定履歴と計測だけを消す（candidates/buildings は残す。デモの撮り直し用）
reset-confirmations:
	uv run python -m api.schema --reset-confirmations

# 取り込み〜推定を一気に通す（2025 ingest 済み前提。schema→estimate→版間差分→geojson）
pipeline: schema estimate ingest-2023 version-diff geojson
	@echo "pipeline 完了：candidates / year_built / buildings.geojson を更新した"
