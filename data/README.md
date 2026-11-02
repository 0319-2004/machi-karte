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
