# 判断ログ

実データで分かった事実（件数、精度、想定外）と設計判断を、日付付きで追記する。
新しいものを上に積む。

## 書き方

```
## YYYY-MM-DD タイトル

- 背景 / きっかけ：
- 判断：
- 根拠（データ・数字は再現方法も）：
- 影響範囲：
```

---

## 2026-11-02 ingest 実行（2025年度版）：不明群 4,982 を抽出し設計書値と全一致

- 背景 / きっかけ：`uv run python -m ingest --version 2025` を実行し、DuckDB `data/machikarte.duckdb` に buildings を構築。
- 判断：センチネル正規化・不明群判定・targets.geojson 出力まで一本通した。is_target は `raw_usage='461'`（4条件が全て 4,982 で一致したうえで立てた）。
- 根拠（実測。コードで再現可能）：

  | 指標 | 実測 | 設計書 | 判定 |
  | --- | --- | --- | --- |
  | 全棟数（13104） | 57,780 | 57,780 | 一致 |
  | 不明群（is_target） | 4,982 | 4,982 | 一致 |
  | 削減率 | 91.4% | 91.4% | 一致 |
  | 高さ10m以上 | 895 | 895 | 一致 |
  | 高さ20m以上 | 196 | 196 | 一致 |
  | 高さ45m以上 | 21 | 21 | 一致 |
  | 高さ100m以上 | 6 | 6 | 一致 |

  - センチネル件数：usage=461 / detailed_usage=0 / storeys=9999 / survey_year=0001 / roof_edge_area=-9999 は**いずれも 4,982**（roof_edge_area の欠損が不明群と完全一致）。height=-9999 は 1,158。
  - `roof_edge_area` の XPath は `uro:buildingDetailAttribute/uro:BuildingDetailAttribute/uro:buildingRoofEdgeArea`。
  - センチネル `1011` は `uro:fireproofStructureType`（耐火構造不明）の値で、保持列（usage/detailed_usage）のセンチネルではないと特定（保持列のセンチネルは 461 と 0 のみ）。
  - 外形：EPSG:4326（表示用, lon/lat）と EPSG:6677（幾何演算用, メートル）の両方を保持。不明群 4,982 を `data/derived/targets.geojson`（EPSG:4326）に出力。
- 影響範囲：第1段「絞り込み」が設計書どおり再現できた。地図UI（Day1–2）の入力は targets.geojson。pytest（`tests/test_ingest.py`）で正規化・raw保持・座標変換・city_code フィルタを固定。

## 2026-11-02 ingest 着手前の CityGML 検証（全31ファイル・2025年度版）

- 背景 / きっかけ：1タイルの XPath だけで ingest を書かず、全ファイルで属性の実在とセンチネル、版の正しさを先に確認した。
- 判断：XPath は IUR 3.2 系（`uro:buildingDetailAttribute/uro:BuildingDetailAttribute/...`）で確定。建物抽出は buildingID 接頭コード `13104` で絞る。
- 根拠（全31ファイルを `lxml.iterparse` で走査、`data/raw/plateau/2025/udx/bldg/`）：
  - タイル内の総棟数は 100,663 だが、メッシュに隣接区が混在（13114:14,580／13116:9,344／13105:7,904／13113:7,242／13101:2,032／13103:1,469／13120:312）。**13104 のみで 57,780 棟＝設計書と一致。**
  - 13104 で各属性の「見つからない棟」は全て **0**（usage/detailedUsage/storeysAboveGround/surveyYear/measuredHeight/creationDate/buildingID）。停止条件に該当せず。
  - センチネル件数（13104）：usage=461 → 4,982／detailedUsage=0 → 4,982／storeysAboveGround=9999 → 4,982／surveyYear=0001 → 4,982／measuredHeight=-9999 → 1,158。**4条件の AND も 4,982**（設計書「6条件が同じ 4,982 を指す」と一致）。
  - creationDate 分布（13104）：2024-03-15 → 51,984、**2026-03-13 → 5,796**（設計書の想定と完全一致 → 落とした版は正しい）。XPath 確認サンプル `53394517_bldg_6697_op.gml` が 2024-03-15 だったのは、2025年度版内に旧棟の作成日が引き継がれているため。
  - `buildingIDAttribute` / `buildingDetailAttribute` を1棟に複数持つ建物は **0**（最大1）。`bldg:usage` の複数保持も 0。
- 影響範囲：ingest は 13104 フィルタ必須。センチネル正規化と不明群判定の基準値が実データで裏取りできた。

## 2026-11-02 外形は lod0RoofEdge を採用（lod0FootPrint は存在せず）

- 判断：建物外形は `bldg:lod0RoofEdge` を使う。
- 根拠：全31ファイルで `bldg:lod0FootPrint` は **1棟も存在しない**（0 出現）。LOD1 は全棟 `bldg:lod1Solid`、LOD2 は一部のみ（`lod2Solid`/`lod2MultiSurface`）。
- 注記：**lod0RoofEdge は「屋根の縁」なので、建物の足元（footprint）より一回り大きめに出る。** 面積・内包判定で過大評価方向の誤差があり得る点に留意（POI の内包判定が緩めに当たる方向）。

## 2026-11-02 OSM を POI と建物に分離

- 判断：Overpass 取得結果を、用途推定に効く POI と、材料として残す建物に分けて保存。
- 根拠 / 内訳：amenity/shop/office/tourism/leisure/healthcare のいずれかを持つ 29,277 件 → `osm_poi.geojson`。`building` タグのみの 183,227 件 → `osm_buildings.geojson`。building 値の上位は yes:115,246／house:37,855／apartments:18,826／residential:5,577／commercial:1,414／public:1,093／retail:666／office:310／hotel:65 等。
- 影響範囲：POI 結合は osm_poi を主に使い、building 値（apartments/commercial/office 等）は補助特徴量にできる。

## 2026-11-02 リポジトリ構成を作成

- 背景 / きっかけ：設計書「リポジトリ構成」に沿って初期ディレクトリを用意した。
- 判断：処理コードは書かず、ディレクトリと雛形（README・`__init__.py`・`.gitignore` 等）のみ作成。
- 根拠：実装順序 Day 1 の「リポジトリ作成」に相当。
- 影響範囲：全体の土台。設計書正本は `docs/design.md`。
