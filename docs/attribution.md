# 帰属表示

このファイルと、画面の帰属ページ、出力ファイルにそろえて載せる。

| データ源 | 表記 |
| --- | --- |
| PLATEAU | 出典明示（国土交通省 Project PLATEAU） |
| OpenStreetMap | © OpenStreetMap contributors（ODbL） |
| Overture Maps | CDLA Permissive 2.0。Foursquare 由来の項目は NOTICE を添える |
| Mapillary | ロゴとリンクを表示。画像ごとに「by 'username', CC BY-SA」 |
| 東京都デジタルツイン | CC BY 4.0 |
| 国土地理院 | 出典明示（地理院タイル） |
| MediaPipe Face Detector | Apache-2.0（© The MediaPipe Authors）。現地写真の顔の自動ぼかしに使用。モデルは BlazeFace。推論は端末内で行い、画像はサーバーに送らない |

## ライセンス方針

- **このリポジトリのコードは MIT ライセンス**（[`LICENSE`](../LICENSE)）。
- **`data/` はリポジトリに含めない。** 生データ・派生データ・キャッシュは各自で取得し、各データ源のライセンスにそれぞれ従う（配置は [`data/README.md`](../data/README.md) を参照）。
- **出力ファイル（更新候補リスト）には、使ったデータ源のライセンスが及ぶ。** 特に `source` が `poi` / `osm_building` の行は OpenStreetMap 由来の情報を含むため、**ODbL（Open Database License）の対象になり得る**。出力を配布・公開する場合は ODbL の帰属・継承義務に従うこと。
  - なお `source` が `neighbor` の行は PLATEAU の既知棟（近傍）由来であり、OSM の対象ではない。`height` / `version_diff` も PLATEAU 由来。
- **Mapillary の画像はリポジトリにも出力にも含めない。** 参照する場合は画像 URL のみを持ち、画面での表示には帰属表示（ロゴ・リンク、画像ごとに「by *username*, CC BY-SA」）を付ける。
- **顔の自動ぼかしは MediaPipe Face Detector（Apache-2.0）** を使う。モデル・WASM は CDN から端末に読み込むが、**写真そのものはサーバーに送らず端末内で推論**する。Apache-2.0 は本リポジトリの MIT とも、ODbL・CC BY-SA のデータ/画像との併用とも整合する（コピーレフト・非商用の制約は無い）。
