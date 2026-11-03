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

## ライセンス方針

- **このリポジトリのコードは MIT ライセンス**（[`LICENSE`](../LICENSE)）。
- **`data/` はリポジトリに含めない。** 生データ・派生データ・キャッシュは各自で取得し、各データ源のライセンスにそれぞれ従う（配置は [`data/README.md`](../data/README.md) を参照）。
- **出力ファイル（更新候補リスト）には、使ったデータ源のライセンスが及ぶ。** 特に `source` が `poi` / `osm_building` の行は OpenStreetMap 由来の情報を含むため、**ODbL（Open Database License）の対象になり得る**。出力を配布・公開する場合は ODbL の帰属・継承義務に従うこと。
- **Mapillary の画像はリポジトリにも出力にも含めない。** 参照する場合は画像 URL のみを持ち、画面での表示には帰属表示（ロゴ・リンク、画像ごとに「by *username*, CC BY-SA」）を付ける。
