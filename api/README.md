# api

FastAPI のアプリケーションを置く場所。地図 UI・確認画面へ建物属性や推定結果を返し、人の確定を受け取る。

- `main.py` … エンドポイント：`/api/stats`（件数。DB から数える）／`/api/targets`（優先度順、今は高さ降順の仮）／
  `/api/buildings/{id}`（正規化値＋`raw_*`、候補・確定は Phase 4–5）／`/attribution`（`docs/attribution.md`）。
  `web/` を `/` に、`data/derived/` を `/data/` に配信。`GZipMiddleware` 有効。
- `db.py` … DuckDB 接続（読み取り専用、`DATA_DIR` から解決）。
- `build_geojson.py` … 地図用の `data/derived/buildings.geojson` を生成（起動時に無ければ自動）。

起動：`make serve`（= `uv run uvicorn api.main:app --reload --port 8000`）→ http://localhost:8000/
