# estimate

属性推定を行う場所。POI 結合による用途推定、階数推定、画像判定、建築年の区間推定。
結果は `candidates` / `evidence` テーブルに入れる。Claude に渡すプロンプトは `prompts/` に置く。

- `poi.py` … Overture Places / OSM POI / OSM 建物タグを建物に割り当て、用途を推定する
  （内包＝距離0 を含む 10m 以内の最近傍、距離で信頼度を減衰）。`uv run python -m estimate.poi`。
- `poi_mapping.yaml` … カテゴリ → 用途コードの対応表。判断に迷うカテゴリ（シェアオフィス・民泊・
  駐車場など）もここに記録し、`poi.py` 実行末尾で報告する。
- Phase 2 後半の判定（POI は用途推定の主軸にしない）は `docs/decisions.md` 2026-11-03 を参照。
