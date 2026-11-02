# data

生データと派生データを置く場所。**`data/README.md` を除き git 管理外**（`.gitignore` で除外）。
ここでは「どこに何を置くか」だけを示す。実データはコミットしない。

## ディレクトリ構成

```
data/
├── raw/                    生データ
│   ├── plateau/
│   │   ├── 2025/           PLATEAU 新宿区（13104）2025年度版 CityGML
│   │   └── 2023/           PLATEAU 新宿区（13104）2023年度版 CityGML（版間差分用）
│   ├── poi/                Overture Places / OpenStreetMap の新宿区範囲
│   ├── ortho/              東京都区部オルソ（東京都デジタルツイン）の新宿区分
├── derived/                派生データ（抽出・推定の中間生成物、GeoJSON など）
└── cache/                  Claude API 等のキャッシュ
```

## 置くもの

- `raw/plateau/2025` … PLATEAU 新宿区 2025年度版の CityGML（EPSG:6697）
- `raw/plateau/2023` … PLATEAU 新宿区 2023年度版の CityGML（版間差分に使用）
- `raw/poi` … Overture Maps Places、OpenStreetMap の新宿区範囲データ
- `raw/ortho` … 東京都区部オルソ画像の新宿区分
- `derived` … 正規化後の抽出結果や GeoJSON などの派生物
- `cache` … API 呼び出しのキャッシュ

DuckDB（`data/machikarte.duckdb`）もこの配下に置き、git 管理外とする。

## 取得記録（2026-11-02）

| データ | 格納先 | 版 / リリース | 取得元 | ライセンス |
| --- | --- | --- | --- | --- |
| PLATEAU 新宿区 CityGML 2025年度版 | `raw/plateau/2025/` | CityGML v5（`13104_shinjuku-ku_pref_2025_citygml_1_op`） | [G空間情報センター `plateau-13104-shinjuku-ku-2025`](https://www.geospatial.jp/ckan/dataset/plateau-13104-shinjuku-ku-2025) | PLATEAU サイトポリシー |
| PLATEAU 新宿区 CityGML 2023年度版 | `raw/plateau/2023/` | CityGML v4（`13104_shinjuku-ku_pref_2023_citygml_2_op`） | [G空間情報センター `plateau-13104-shinjuku-ku-2023`](https://www.geospatial.jp/ckan/dataset/plateau-13104-shinjuku-ku-2023) | PLATEAU サイトポリシー |
| Overture Maps Places | `raw/poi/overture_places.parquet` | リリース `2026-10-21.0` | Overture Maps（`overturemaps download --type=place`） | CDLA Permissive 2.0（Foursquare 由来項目は NOTICE を添える） |
| OpenStreetMap POI | `raw/poi/osm_poi.geojson` | Overpass base `2026-11-02T14:23:19Z` | Overpass API（amenity/shop/office/tourism/leisure/healthcare/building の node/way） | © OpenStreetMap contributors（ODbL） |

- 取得範囲 bbox（2025年度 CityGML の範囲 +100m 余白）：`139.661321,35.665555,139.751553,35.734288`（西,南,東,北）
- PLATEAU zip は `udx/bldg/*.gml`・`codelists/`（`Building_usage.xml` を含む）・`metadata/` のみ展開。**テクスチャ（`*_appearance/*.jpg`）は未展開。**
- 件数（概数）：2025 bldg GML 31ファイル、2023 bldg GML 33ファイル、Overture Places 82,431件、OSM POI 212,504件（うち building タグ 184,214件）
- 東京都区部オルソは Phase 8 用のため未取得。
