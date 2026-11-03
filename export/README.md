# export

自治体向けの更新候補リスト（CSV / GeoJSON）を出力する場所。
人が確定した推定値のみを出す（未確定・判断不能は出さない）。

---

以下は、出力物（更新候補リスト）に同梱する **README テンプレート**（Phase 9 で使う）。
出力を配布・公開するときは、このテンプレートをコピーして出力ディレクトリに添える。

<!-- ここから出力同梱用テンプレート -->

# まちカルテ 更新候補リスト

PLATEAU 新宿区（13104）の建物属性について、人が地図で確認・確定した更新候補です。
未確定・判断不能の属性は含みません。

## 帰属表示

| データ源 | 表記 |
| --- | --- |
| PLATEAU | 出典明示（国土交通省 Project PLATEAU） |
| OpenStreetMap | © OpenStreetMap contributors（ODbL） |
| Overture Maps | CDLA Permissive 2.0。Foursquare 由来の項目は NOTICE を添える |
| Mapillary | ロゴとリンクを表示。画像ごとに「by *username*, CC BY-SA」 |
| 東京都デジタルツイン | CC BY 4.0 |
| 国土地理院 | 出典明示（地理院タイル） |

## ライセンスの注記

- この更新候補リストには、使ったデータ源のライセンスが及びます。
- **`source` が `poi` / `osm_building` の行は OpenStreetMap 由来の情報を含むため、ODbL（Open Database License）の対象になり得ます。** 配布・公開する場合は ODbL の帰属・継承義務に従ってください。
- **Mapillary の画像はこの出力に含まれません。** 参照は画像 URL のみです。画面に表示する場合は帰属表示（ロゴ・リンク、画像ごとに「by *username*, CC BY-SA」）を付けてください。

<!-- ここまで出力同梱用テンプレート -->
