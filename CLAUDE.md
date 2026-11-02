# まちカルテ — Claude Code 作業ガイド

このリポジトリは「Built with Claude: Japan 2026」（開発期間 2026-11-02 12:00 〜 11-08 20:00 JST）の提出物。
設計の正本は `docs/design.md`（まちカルテ（仮）設計書）。迷ったら設計書に戻る。

## プロダクト

PLATEAU 新宿区（13104）の建物属性のうち、調査が及んでいない建物（不明群）を自動で絞り込み、
属性を推定し、人が地図で確認・確定して、自治体向けの更新候補リスト（CSV / GeoJSON）を出力する。

骨格：抽出（自動）→ 推定（Claude）→ 確認・確定（人）→ 更新候補の出力（自動）

## 絶対に守ること（ハッカソンのルール由来）

- すべてのコードはこのリポジトリで新規に書く。既存のスクリプトや他プロジェクトからコピーしない
- 権利を確認できないコード・データ・素材を使わない
  - 使わない：Google Street View / Google Places / Google Maps、ゼンリン住宅地図、不動産ポータル
- Mapillary
  - 公開API（Graph API v4）以外の手段で取得しない（スクレイピング禁止）
  - 顔やナンバープレートのぼかし解除・再識別をしない
  - ナビゲーション、経路案内、車両の自動・自律制御の機能を作らない
- APIキーは環境変数で渡す。`.env` はコミットしない。`.env.example` のみコミットする
- 公式データは上書きしない。元の属性は `buildings` に残し、推定は `candidates`、人の確定は `confirmations` に入れる
- UI、出力ファイル、README、コミットメッセージは日本語

## 技術スタック

- Python 3.12 + uv、DuckDB（spatial 拡張）、FastAPI + uvicorn
- フロント：MapLibre GL JS。まずはビルド不要の素の HTML/JS で始める
- 背景地図：国土地理院タイル（出典表示を必ず出す）
- 推定：Claude API（Anthropic Python SDK）。モデル名は環境変数 `CLAUDE_MODEL` で切り替える
- DB：`data/machikarte.duckdb`（git 管理外）。テーブルは buildings / candidates / confirmations / evidence

## ディレクトリ

```
/ingest      CityGMLの解析、DuckDBへの投入
/estimate    POI結合、階数推定、画像判定、建築年
/api         FastAPI
/web         MapLibre の地図と確認画面
/export      更新候補リストの出力
/eval        既知棟での精度検証、数字のレポート
/docs        design.md、decisions.md、attribution.md
data/        生データ・派生データ・キャッシュ（git 管理外）
```

## データの約束

- 主キーは `uro:buildingID`（例：`13104-bldg-2923`）。外部情報はすべて建物IDに寄せる
- 座標系：幾何演算は EPSG:6677、地図表示と GeoJSON 出力は EPSG:4326。CityGML は EPSG:6697 で入っている
- センチネル値は読み込み時に NULL へ正規化する
  - 階数 9999、高さ・屋根面積 -9999、調査年 0001、用途 461、1011（どの列かは実データの値分布で特定する）
  - **生の値は `raw_*` 列で必ず残す。** 出力の `current_value` に 461 や 9999 をそのまま出すため
- 不明群：`usage = '461'` を主条件にし、`detailed_usage = '0'`・`storeys_above = 9999`・`survey_year = '0001'` の各件数と一致するか検証する。ずれたら止めて報告する
- 件数（57,780 / 4,982 など）をコードに直書きしない。毎回データから数え、設計書の値と違えば報告する
- 「判断不能」も行として残す（`value` NULL、`confidence` 0、`reasoning` に理由）。行が無いことと区別する
- 建築年は点推定しない。区間（`value_min`、`value_max`）で持つ
- 人が確定していない推定値と、判断不能の属性は、更新候補リストに出さない

## 優先順位

UIを前に、推定を後ろに。どの時点で止まっても「抽出 → 地図 → 確定 → 出力」が一本通る状態を保つ。
推定が未完成なら仮の推定（高さ ÷ 階高）で埋めて先に進む。

デモで見せたいのは2つ：**57,780棟が4,982棟に減る瞬間**と、**1棟あたりの処理時間**。

## 進め方

- 1フェーズは小さな完了状態で終える。最後に動作確認の手順を書く
- コミットは小さく、日本語で「何を・なぜ」。履歴が期間中の貢献の証拠になる
- 実データで分かった事実（件数、精度、想定外）と設計判断は、`docs/decisions.md` に日付付きで追記する
- 精度などの数字は `eval/` のスクリプトで再現できる形でだけ報告する。推測の数字を書かない
- 大きいデータ（CityGML、Parquet、画像）はリポジトリに入れない
- 前提が崩れたら（設計書「前提が崩れたら止める点」）、勝手に回避せず止めて相談する
- Claude API の呼び出しは入力ハッシュでキャッシュし、トークン数と費用をログに残す

## 帰属表示

`docs/attribution.md` と画面の帰属ページ、出力ファイルにそろえて載せる。

| データ源 | 表記 |
| --- | --- |
| PLATEAU | 出典明示（国土交通省 Project PLATEAU） |
| OpenStreetMap | © OpenStreetMap contributors（ODbL） |
| Overture Maps | CDLA Permissive 2.0。Foursquare 由来の項目は NOTICE を添える |
| Mapillary | ロゴとリンクを表示。画像ごとに「by 'username', CC BY-SA」 |
| 東京都デジタルツイン | CC BY 4.0 |
| 国土地理院 | 出典明示（地理院タイル） |
