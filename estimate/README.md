# estimate

属性推定を行う場所。POI 結合による用途推定、階数推定、画像判定、建築年の区間推定。
結果は `candidates` / `evidence` テーブルに入れる。Claude に渡すプロンプトは `prompts/` に置く。

- `poi.py` … Overture Places / OSM POI / OSM 建物タグを建物に割り当て、用途を推定する
  （内包＝距離0 を含む 10m 以内の最近傍、距離で信頼度を減衰）。`uv run python -m estimate.poi`。
- `poi_mapping.yaml` … カテゴリ → 用途コードの対応表。判断に迷うカテゴリ（シェアオフィス・民泊・
  駐車場など）もここに記録し、`poi.py` 実行末尾で報告する。
- `storeys.py` … 仮の推定（階数＝高さ÷用途別階高、用途＝近傍＋高さ±3m）を candidates に投入。
- `claude_infer.py` … Claude API で不明群の用途・階数を推定する（画像が無い棟でも動く）。
  事実（高さ・外形面積・周辺の用途分布・POI・仮の推定）と、あれば画像を渡し、構造化出力
  （usage / storeys / unresolvable）で返させる。判断不能も candidates(source='image') に
  1行として残す。入力ハッシュで `data/cache/claude_infer/` にキャッシュし二重課金を防ぐ。
  トークン・費用は `data/derived/claude_infer_log.jsonl` に追記。モデルは `CLAUDE_MODEL`。
  `uv run python -m estimate.claude_infer --limit 20`（`--dry-run` で入力だけ確認）。
  検証は `uv run python -m eval.claude_eval`（既知棟で正解率・判断不能率・断定誤を出す）。
- `prompts/usage_storeys_v1.md` … Claude に渡すプロンプト（版番号つき）。
  「外観だけで断定しない／決められない属性は判断不能／高層の複合用途では地上POIは1階のみ」。
- Phase 2 後半の判定（POI は用途推定の主軸にしない）は `docs/decisions.md` 2026-11-03 を参照。
